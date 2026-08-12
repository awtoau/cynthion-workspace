#!/usr/bin/env python3
"""Run a design through Lattice Diamond, for comparison against the open flow.

Diamond as an oracle, not as a replacement. The open flow is

    yosys -> nextpnr-ecp5 -> ecppack

and Diamond is an independent implementation of all three stages. Where it does
better, the gap names a specific missing capability in the open tools, which is
the thing worth fixing. See docs/diamond-oracle-ecp5.md.

Three entry points, and the difference between them is the whole point:

  --mode lse       Verilog -> Diamond LSE synthesis -> map -> par -> bitgen.
                   Both synthesis and place-and-route are Diamond's.

  --mode synplify  Verilog -> Synplify Pro -> EDIF -> map -> par -> bitgen.
                   Diamond's production synthesiser rather than its lightweight
                   one, and multi-threaded where LSE is not.

  --mode yosys     yosys-emitted structural Verilog -> Diamond map -> par.
                   Synthesis is the open flow's, place-and-route is Diamond's.

Running lse or synplify against the same design as yosys splits the problem in
half: a difference in the vendor-synthesis modes is synthesis and belongs in
yosys; one that survives `yosys` mode is place-and-route and belongs in nextpnr.

Diamond's environment is sourced per-invocation rather than inherited, because
oss-cad-suite sets PYTHONHOME and prepends its own libstdc++, which stops
Diamond's engines loading their shared objects.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

# scripts/diamond/<name>.py, so the repo root is three levels up. These lived in
# scripts/ and were moved; a stale `.parent.parent` put every log and artifact
# under scripts/tmp/ instead of tmp/, which is silent and wrong.
ROOT = Path(__file__).resolve().parent.parent.parent
LOGDIR = ROOT / "tmp" / "logs"

sys.path.insert(0, str(ROOT / "scripts"))

from subprocess_timeout_from_history import run_bounded  # noqa: E402

DIAMOND = Path.home() / "lscc" / "diamond" / "3.14"
DIAMOND_BIN = DIAMOND / "bin" / "lin64"
FOUNDRY = DIAMOND / "ispfpga"
FPGA_BIN = FOUNDRY / "bin" / "lin64"

# The DIE on the board, which is a 25F under a 12F marking -- see
# docs/chips/ecp5/lfe5u-12f.md. nextpnr's chipdb for `LFE5U-12F` is already the
# 25k die, so 25F here is what makes the two flows target the same silicon;
# Diamond enforces the marking (12,096 reg / 32 EBR) and would refuse a design
# nextpnr places at 60% utilisation.
ARCH = "ECP5U"
DEVICE = "LFE5U-25F"
PACKAGE = "CABGA256"
SPEED = "8"

# Diamond's own directory for ECP5U, from DiamondDevFile.xml `ach="sa5p00"`.
# `ep5c00` is LatticeECP3.
ARCH_DIR = "sa5p00"


def diamond_env():
    """The environment Diamond's engines need, built from scratch.

    Deliberately not inheriting the caller's environment wholesale: the
    oss-cad-suite setup that the open flow needs actively breaks Diamond.
    """
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["LSC_DIAMOND"] = "true"
    env["FOUNDRY"] = str(FOUNDRY)
    env["QT_PLUGIN_PATH"] = ""
    env["NEOCAD_MAXLINEWIDTH"] = "32767"
    env["TCL_LIBRARY"] = str(DIAMOND / "tcltk" / "lib" / "tcl8.5")
    env["LM_LICENSE_FILE"] = str(DIAMOND / "license" / "license.dat")
    env["PATH"] = f"{DIAMOND_BIN}:{FPGA_BIN}:{env.get('PATH', '')}"
    # Diamond ships its own libstdc++ and Qt; theirs must come first.
    env["LD_LIBRARY_PATH"] = f"{DIAMOND_BIN}:{FPGA_BIN}"
    return env


# First-run ceiling per engine, seconds, with where the number came from. No
# engine had a bound at all before #498, so two runs were ended by hand after
# the better part of an hour with nothing saying whether waiting would help.
# run_bounded tightens each to 1.25x the slowest SUCCESSFUL run after that.
FIRST_RUN = {
    "synthesis": (5400, "83 min produced nothing (#498); longer buys nothing"),
    "edif2ngd": (600, "netlist read, no optimisation; 10x yosys write_edif"),
    "ngdbuild": (600, "library bind only; same order as edif2ngd"),
    "map": (1200, "nextpnr packs this design in ~1 min; 20x for a first run"),
    "par": (2400, "nextpnr places+routes in ~4 min; 10x at -l 5"),
    "trce": (900, "static analysis of a routed netlist; report-bound"),
    "bitgen": (600, "frame assembly; ecppack does it in seconds"),
}


def log(msg, handle):
    print(msg, flush=True)
    handle.write(msg + "\n")
    handle.flush()


def run(cmd, cwd, handle, env, step, engine, family=None):
    """Run one engine, bounded, timing it and failing loudly.

    The bound is per engine rather than per flow: a synthesis that overruns and
    a bitgen that overruns are different failures and a single number for both
    would have to be the larger, which is no bound at all on the smaller.
    `family` separates two engines doing the same job -- LSE and Synplify both
    synthesise, and one's history would kill the other.
    """
    floor, why = FIRST_RUN[engine]
    log(f"--- {step}: {' '.join(str(c) for c in cmd)}", handle)
    log(f"    bound >= {floor}s ({why})", handle)
    start = time.monotonic()
    proc = run_bounded([str(c) for c in cmd],
                       family=family or f"diamond-{engine}",
                       cwd=str(cwd), env=env, merge_stderr=True, floor=floor)
    elapsed = time.monotonic() - start
    if proc is None:
        # run_bounded already named the family, the limit and the elapsed time.
        log(f"!!! {step} KILLED after {elapsed:.0f}s -- see the TIMEOUT line "
            f"above", handle)
        raise SystemExit(f"{step} timed out")
    out = proc.stdout or ""
    handle.write(out + "\n")
    handle.flush()
    if proc.returncode != 0:
        log(f"!!! {step} failed rc={proc.returncode}", handle)
        log(out[-4000:], handle)
        raise SystemExit(f"{step} failed")
    log(f"    {step} ok in {elapsed:.1f}s", handle)
    return elapsed, out


FREQ_RE = re.compile(
    r'^\s*FREQUENCY\s+(PORT|NET)\s+("[^"]+"|\S+)\s+([\d.eE+]+)\s+HZ\s*;',
    re.IGNORECASE)

IO_TYPE_RE = re.compile(r'IO_TYPE=(\w+)')


LOCATE_RE = re.compile(r'^\s*LOCATE\s+COMP\s+"([^"]+)"', re.IGNORECASE)


def lpf_from_amaranth(src, dst, drop_locate=()):
    """Reuse the Amaranth-generated .lpf as Diamond preferences, with a fix.

    Amaranth emits LOCATE/IOBUF lines in Lattice's own preference syntax
    because nextpnr consumes the same format, so the pin assignment is
    already like-for-like between the flows -- which is what makes this a fair
    comparison rather than two differently-constrained builds.

    The frequency lines do not survive the trip. Amaranth writes

        FREQUENCY PORT "clk_60MHz_0__io" 60000000.0 HZ;
        FREQUENCY NET "car.clk_sync" 50000000.0 HZ;

    and Diamond's map rejects both:

        WARNING - map: top.lpf(242): Syntax error in "FREQUENCY PORT
        "clk_60MHz_0__io" 60000000.0 HZ;": error on token "HZ".

    Diamond wants MHz and an integer-ish literal. nextpnr accepts the HZ form,
    so this is a real portability gap in Amaranth's LPF writer, not a Diamond
    quirk -- see docs/diamond-oracle-ecp5.md. Rewriting rather than dropping
    the line matters: without it the design is unconstrained, and an
    unconstrained Diamond run reports whatever frequency it happened to reach
    instead of one it worked toward, which is not comparable to the open
    flow's binary-searched number.

    The configuration bank's voltage is missing for the same reason. Diamond
    infers every ordinary bank's VCCIO from the IO_TYPE of the pins located
    into it, but bank 8 is the ECP5's config bank and takes its voltage from
    SYSCONFIG CONFIG_IOVOLTAGE, which defaults to 2.5 V and cannot be moved by
    a BANK preference. par refused int_0__io at T6 on exactly that.

    `drop_locate` unpins named ports. Diagnostic only -- a run with a pin
    unpinned is not the shipping design's pinout, so say so with any number
    that comes out of it.
    """
    out_lines = []
    io_types = set()
    for line in Path(src).read_text().splitlines():
        io_types.update(IO_TYPE_RE.findall(line))
        pin = LOCATE_RE.match(line)
        if pin and pin.group(1) in drop_locate:
            out_lines.append(f"# LOCATE dropped by --drop-locate: {line.strip()}")
            continue
        m = FREQ_RE.match(line)
        if m:
            kind, name, hz = m.group(1), m.group(2), float(m.group(3))
            out_lines.append(f"FREQUENCY {kind.upper()} {name} "
                             f"{hz / 1e6:g} MHZ;")
        else:
            out_lines.append(line)

    # Only when the design speaks one I/O standard, which this one does -- every
    # pin is LVCMOS33. A mixed design needs the real per-bank map, and guessing
    # it would place pins at a voltage the board does not supply.
    if io_types and all(t.startswith("LVCMOS33") for t in io_types):
        out_lines += ["", "SYSCONFIG CONFIG_IOVOLTAGE=3.3;"]
    Path(dst).write_text("\n".join(out_lines) + "\n")


# Synplify names the same part differently from the Diamond engines: underscore
# rather than hyphen, and the short package code. From its own part database,
# synpbase/lib/parts/lattice_diamond_parts.txt.
SYNP_PART = DEVICE.replace("-", "_")
SYNP_PACKAGE = "BG256C"

# Synplify's implementation directory. One implementation is all this flow
# wants, and every output -- .edi, .srr, .areasrr -- lands inside it.
IMPL = "rev_1"


def synplify_project(prj, sources, top, freq, edif):
    """Write the Synplify Pro project file synpwrap consumes.

    `-frequency auto` rather than a number is the point of this mode's default:
    a synthesiser told to hit a clock the design cannot reach works
    indefinitely hard for nothing, which is what #498 measured on LSE.
    """
    lines = [f'add_file -verilog "{s}"' for s in sources]
    lines += [
        f"impl -add {IMPL} -type fpga",
        "set_option -vlog_std v2001",
        "set_option -technology ECP5U",
        f"set_option -part {SYNP_PART}",
        f"set_option -package {SYNP_PACKAGE}",
        f"set_option -speed_grade -{SPEED}",
        f"set_option -top_module {top}",
        f"set_option -frequency {freq if freq else 'auto'}",
        # The design's IO buffers come from the .lpf and Diamond's map, not from
        # synthesis; letting Synplify insert its own duplicates them.
        "set_option -disable_io_insertion 0",
        "set_option -resource_sharing 1",
        "set_option -write_apr_constraint 0",
        f'project -result_file "{edif}"',
        f'impl -active "{IMPL}"',
    ]
    Path(prj).write_text("\n".join(lines) + "\n")


def parse_map_report(text):
    """Pull cell counts out of Diamond's map report (.mrp).

    Diamond counts in its own vocabulary: LUT4s, registers, and slices, where
    nextpnr reports TRELLIS_COMB and TRELLIS_FF. The mapping is not one-to-one
    and the report is the only place the breakdown appears, so the per-cell
    lines matter as much as the totals.
    """
    util = {}
    patterns = {
        "LUT4": r"Number of LUT4s:\s+(\d+)",
        "SLICE": r"Number of SLICEs:\s+(\d+)",
        "FF": r"Number of registers:\s+(\d+)",
        "DP16KD": r"Number of block RAMs:\s+(\d+)",
        "MULT18X18D": r"Number of DSP.*?:\s+(\d+)",
        "PIO": r"Number of PIO.*?:\s+(\d+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            util[key] = int(m.group(1))
    return util


SUMMARY_ROW_RE = re.compile(
    r'FREQUENCY\s+(?:NET|PORT)\s+"([^"]+)".*?\|\s*([\d.]+)\s*MHz\s*\|'
    r'\s*([\d.]+)\s*MHz\s*\|\s*(\d+)\s*(\*?)', re.IGNORECASE | re.DOTALL)

COVERAGE_RE = re.compile(
    r"Constraints cover ([\d,]+) paths, ([\d,]+) nets?, and ([\d,]+) "
    r"connections \(([\d.]+)% coverage\)", re.IGNORECASE)


def parse_twr(text):
    """Read trce's Report Summary table, not the prose above it.

    The prose says "N MHz is the maximum frequency for this preference" with
    no clock name on the line, so scraping it attaches the number to whatever
    word happens to start the line -- this returned {'Warning:': 71.7}.

    The table has the name, the constraint, the achieved figure, the logic
    depth and a `*` on any preference not met. `covered` matters as much: a
    figure for a domain covering 4% of the design is not the design's Fmax,
    and only the coverage line says which case this is.
    """
    fmax, meta = {}, {}
    # Long preferences wrap across two table rows, so a row is matched from its
    # name to its first pair of MHz cells rather than within one line.
    summary = text.split("Report Summary", 1)[-1].split("Critical Nets", 1)[0]
    for m in SUMMARY_ROW_RE.finditer(summary):
        fmax[m.group(1)] = {
            "constraint_mhz": float(m.group(2)),
            "achieved_mhz": float(m.group(3)),
            "levels": int(m.group(4)),
            "met": m.group(5) != "*",
        }
    cover = COVERAGE_RE.search(text)
    if cover:
        meta["coverage_pct"] = float(cover.group(4))
        meta["paths"] = int(cover.group(1).replace(",", ""))
    return fmax, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verilog", required=True, type=Path,
                    help="Verilog source (behavioural for lse, structural for yosys)")
    ap.add_argument("--extra-verilog", type=Path, nargs="*", default=[],
                    help="additional Verilog sources, eg a pre-generated CPU core")
    ap.add_argument("--lpf", required=True, type=Path,
                    help="Amaranth-generated .lpf, reused as Diamond preferences")
    ap.add_argument("--top", default="top")
    ap.add_argument("--mode", choices=["lse", "synplify", "yosys"],
                    default="lse")
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--freq", type=float, default=None,
                    help="the synthesiser's own target in MHz; map/par/trce "
                         "take theirs from the Amaranth .lpf either way")
    ap.add_argument("--drop-locate", nargs="*", default=[],
                    help="unpin these ports. DIAGNOSTIC: the result is not the "
                         "shipping pinout, so label any number from it")
    ap.add_argument("--opt-goal", choices=["Area", "Balanced", "Timing"],
                    default="Area",
                    help="LSE optimisation goal; Area by default because this "
                         "project no longer optimises the SoC for timing")
    ap.add_argument("--par-effort", default="5",
                    help="par -l level, 1..5; 5 is the highest effort")
    ap.add_argument("--name", default="diamond_flow")
    args = ap.parse_args()

    LOGDIR.mkdir(parents=True, exist_ok=True)
    out = args.outdir
    out.mkdir(parents=True, exist_ok=True)
    env = diamond_env()
    logpath = LOGDIR / f"{args.name}.log"
    timings = {}

    with open(logpath, "w") as handle:
        log(f"design {args.verilog}", handle)
        log(f"mode {args.mode}  device {DEVICE} {PACKAGE} speed {SPEED}", handle)

        # Diamond's readers dispatch on file extension, so the staged copy has
        # to keep the source's own suffix (.v for LSE, .edf for edif2ngd).
        src = out / f"src{args.verilog.suffix}"
        shutil.copy(args.verilog, src)
        extra = []
        for i, e in enumerate(args.extra_verilog):
            d = out / f"extra{i}.v"
            shutil.copy(e, d)
            extra.append(d)

        # The Amaranth .lpf already constrains every clock domain at the
        # frequency the design runs at, under the net names this netlist uses.
        # A second hand-written `FREQUENCY NET "clk"` duplicated it against a
        # name Diamond does not have, so it constrained nothing.
        lpf = out / "top.lpf"
        lpf_from_amaranth(args.lpf, lpf, set(args.drop_locate))

        ngd = out / f"{args.top}.ngd"
        total = 0.0

        if args.mode == "lse":
            # LSE has its own target and does not read the .lpf; left at its
            # 200 MHz default it chases a clock this design does not reach.
            # That is not why it is slow, though -- see #498.
            cmd = ["synthesis", "-a", ARCH, "-d", DEVICE, "-t", PACKAGE,
                   "-s", SPEED, "-top", args.top, "-ngd", ngd.name,
                   "-optimization_goal", args.opt_goal,
                   "-ver", src.name]
            if args.freq:
                cmd[1:1] = ["-frequency", str(args.freq)]
            for e in extra:
                cmd.append(e.name)
            t, _ = run(cmd, out, handle, env, "synthesis(LSE)", "synthesis",
                       "diamond-synthesis-lse")
            timings["synthesis"] = t
            total += t
        elif args.mode == "synplify":
            # Synplify Pro, the production synthesiser LSE is the lightweight
            # alternative to. It emits EDIF rather than an .ngd, so the netlist
            # goes through the same edif2ngd/ngdbuild pair the yosys mode uses.
            prj = out / "synplify.prj"
            synplify_project(prj, [src.name] + [e.name for e in extra],
                             args.top, args.freq, "synplify.edi")
            t, _ = run(["synpwrap", "-prj", prj.name, "-nolog"], out, handle,
                       env, "synthesis(Synplify)", "synthesis",
                       "diamond-synthesis-synplify")
            timings["synthesis"] = t
            total += t
            # -result_file resolves inside the implementation directory, not
            # beside the project, so the path given to Synplify is not the path
            # the netlist appears at. Every engine runs with cwd=out, so what
            # edif2ngd is handed has to be relative to that.
            edif = f"{IMPL}/synplify.edi"
            if not (out / edif).is_file():
                raise SystemExit(f"Synplify wrote no netlist at {out / edif}")
            ngo = out / f"{args.top}.ngo"
            t, _ = run(["edif2ngd", "-l", ARCH, "-d", DEVICE,
                        edif, ngo.name], out, handle, env,
                       "edif2ngd", "edif2ngd")
            timings["edif2ngd"] = t
            total += t
            t, _ = run(["ngdbuild", "-a", ARCH, "-d", DEVICE,
                        "-p", str(FOUNDRY / ARCH_DIR / "data"),
                        ngo.name, ngd.name], out, handle, env,
                       "ngdbuild", "ngdbuild")
            timings["ngdbuild"] = t
            total += t
        else:
            # yosys already synthesised, so Diamond only binds the netlist
            # against the device library and any surviving difference is
            # place-and-route. EDIF because ngdbuild does not read Verilog, and
            # routing it back through LSE would re-synthesise it.
            ngo = out / f"{args.top}.ngo"
            t, _ = run(["edif2ngd", "-l", ARCH, "-d", DEVICE,
                        src.name, ngo.name], out, handle, env,
                       "edif2ngd", "edif2ngd")
            timings["edif2ngd"] = t
            total += t
            t, _ = run(["ngdbuild", "-a", ARCH, "-d", DEVICE,
                        "-p", str(FOUNDRY / ARCH_DIR / "data"),
                        ngo.name, ngd.name], out, handle, env, "ngdbuild", "ngdbuild")
            timings["ngdbuild"] = t
            total += t

        ncd = out / f"{args.top}.ncd"
        prf = out / f"{args.top}.prf"
        t, _ = run(["map", ngd.name, lpf.name, "-a", ARCH, "-p", DEVICE,
                    "-t", PACKAGE, "-s", SPEED, "-o", ncd.name,
                    "-pr", prf.name], out, handle, env, "map", "map")
        timings["map"] = t
        total += t

        par_ncd = out / f"{args.top}_par.ncd"
        t, _ = run(["par", "-w", "-l", args.par_effort, "-n", "1",
                    ncd.name, par_ncd.name, prf.name],
                   out, handle, env, "par", "par")
        timings["par"] = t
        total += t

        # par may write into a directory rather than a file when given
        # multiple iterations; normalise so the later steps find it.
        if par_ncd.is_dir():
            found = sorted(par_ncd.glob("*.ncd"))
            if found:
                par_ncd = found[0]

        t, _ = run(["trce", "-v", "10", "-o", f"{args.top}.twr",
                    par_ncd.name if par_ncd.parent == out
                    else str(par_ncd), prf.name],
                   out, handle, env, "trce", "trce")
        timings["trce"] = t
        total += t

        t, _ = run(["bitgen", "-w", "-d",
                    par_ncd.name if par_ncd.parent == out else str(par_ncd)],
                   out, handle, env, "bitgen", "bitgen")
        timings["bitgen"] = t
        total += t

        # Collect the numbers.
        util, fmax, timing_meta = {}, {}, {}
        for mrp in list(out.glob("*.mrp")):
            util.update(parse_map_report(mrp.read_text(errors="replace")))
        for twr in list(out.glob("*.twr")):
            found, meta = parse_twr(twr.read_text(errors="replace"))
            fmax.update(found)
            timing_meta.update(meta)

        log("\n=== Diamond result ===", handle)
        log(f"mode        {args.mode}", handle)
        log(f"utilisation {util}", handle)
        for name, row in fmax.items():
            log(f"  {name:24s} {row['achieved_mhz']:8.3f} MHz vs "
                f"{row['constraint_mhz']:.0f} asked, {row['levels']} levels, "
                f"{'met' if row['met'] else 'NOT MET'}", handle)
        log(f"coverage    {timing_meta}", handle)
        log(f"time        {total:.1f}s  {timings}", handle)

        res = {"mode": args.mode, "device": DEVICE, "util": util,
               "fmax": fmax, "timing": timing_meta,
               "unpinned": sorted(args.drop_locate),
               "seconds": round(total, 1),
               "steps": {k: round(v, 1) for k, v in timings.items()}}
        (out / "result.json").write_text(json.dumps(res, indent=2))
        log(f"wrote {out / 'result.json'}\nlog {logpath}", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
