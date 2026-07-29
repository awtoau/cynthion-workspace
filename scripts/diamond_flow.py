#!/usr/bin/env python3
"""Run a design through Lattice Diamond, for comparison against the open flow.

Diamond as an oracle, not as a replacement. The open flow is

    yosys -> nextpnr-ecp5 -> ecppack

and Diamond is an independent implementation of all three stages. Where it does
better, the gap names a specific missing capability in the open tools, which is
the thing worth fixing. See docs/diamond-oracle-ecp5.md.

Two entry points, and the difference between them is the whole point:

  --mode lse    Verilog -> Diamond LSE synthesis -> map -> par -> bitgen.
                Both synthesis and place-and-route are Diamond's.

  --mode yosys  yosys-emitted structural Verilog -> Diamond map -> par.
                Synthesis is the open flow's, place-and-route is Diamond's.

Running both against the same design splits the problem in half: if Diamond
only wins in `lse` mode, the gap is synthesis and belongs in yosys; if it wins
in `yosys` mode too, the gap is in place-and-route and belongs in nextpnr.

Diamond's environment is sourced per-invocation rather than inherited, because
oss-cad-suite sets PYTHONHOME and prepends its own libstdc++, which stops
Diamond's engines loading their shared objects.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGDIR = ROOT / "tmp" / "logs"

DIAMOND = Path("/home/dan/lscc/diamond/3.14")
DIAMOND_BIN = DIAMOND / "bin" / "lin64"
FOUNDRY = DIAMOND / "ispfpga"
FPGA_BIN = FOUNDRY / "bin" / "lin64"

# The part actually on the board. -12F is the constraint the whole project
# is working against; using a bigger part would make every number meaningless.
ARCH = "ECP5U"
DEVICE = "LFE5U-12F"
PACKAGE = "CABGA256"
SPEED = "8"


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


def log(msg, handle):
    print(msg, flush=True)
    handle.write(msg + "\n")
    handle.flush()


def run(cmd, cwd, handle, env, step):
    """Run one engine, timing it and failing loudly."""
    log(f"--- {step}: {' '.join(str(c) for c in cmd)}", handle)
    start = time.monotonic()
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env,
                          capture_output=True, text=True)
    elapsed = time.monotonic() - start
    out = proc.stdout + proc.stderr
    handle.write(out + "\n")
    handle.flush()
    if proc.returncode != 0:
        log(f"!!! {step} failed rc={proc.returncode}", handle)
        log(out[-4000:], handle)
        raise SystemExit(f"{step} failed")
    log(f"    {step} ok in {elapsed:.1f}s", handle)
    return elapsed, out


FREQ_PORT_RE = re.compile(
    r'^\s*FREQUENCY\s+PORT\s+("[^"]+"|\S+)\s+([\d.eE+]+)\s+HZ\s*;',
    re.IGNORECASE)


def lpf_from_amaranth(src, dst):
    """Reuse the Amaranth-generated .lpf as Diamond preferences, with a fix.

    Amaranth emits LOCATE/IOBUF lines in Lattice's own preference syntax
    because nextpnr consumes the same format, so the pin assignment is
    already like-for-like between the flows -- which is what makes this a fair
    comparison rather than two differently-constrained builds.

    One line does not survive the trip. Amaranth writes

        FREQUENCY PORT "clk_60MHz_0__io" 60000000.0 HZ;

    and Diamond's map rejects it:

        WARNING - map: top.lpf(242): Syntax error in "FREQUENCY PORT
        "clk_60MHz_0__io" 60000000.0 HZ;": error on token "HZ".

    Diamond wants MHz and an integer-ish literal. nextpnr accepts the HZ form,
    so this is a real portability gap in Amaranth's LPF writer, not a Diamond
    quirk -- see docs/diamond-oracle-ecp5.md. Rewriting rather than dropping
    the line matters: without it the design is unconstrained, and an
    unconstrained Diamond run reports whatever frequency it happened to reach
    instead of one it worked toward, which is not comparable to the open
    flow's binary-searched number.
    """
    out_lines = []
    for line in Path(src).read_text().splitlines():
        m = FREQ_PORT_RE.match(line)
        if m:
            port, hz = m.group(1), float(m.group(2))
            out_lines.append(f"FREQUENCY PORT {port} {hz / 1e6:g} MHZ;")
        else:
            out_lines.append(line)
    Path(dst).write_text("\n".join(out_lines) + "\n")


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


def parse_twr(text):
    """Pull the achieved frequency out of Diamond's timing report (.twr).

    Diamond reports slack against the constraint that was given, and also a
    'maximum frequency' per clock domain. The latter is what compares against
    nextpnr's 'Max frequency for clock', but only if the constraint was tight
    enough to make Diamond work for it -- an unconstrained run reports
    whatever it happened to reach, exactly the trap the open-flow numbers
    avoided by binary search.
    """
    fmax = {}
    for m in re.finditer(
            r"^\s*(\S+)\s+.*?([\d.]+)\s*MHz\s*is the maximum frequency",
            text, re.MULTILINE | re.IGNORECASE):
        fmax[m.group(1)] = float(m.group(2))
    if not fmax:
        for m in re.finditer(r"([\d.]+)\s*MHz.*?maximum frequency", text,
                             re.IGNORECASE):
            fmax.setdefault("overall", float(m.group(1)))
    return fmax


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
    ap.add_argument("--mode", choices=["lse", "yosys"], default="lse")
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--freq", type=float, default=None,
                    help="constraint in MHz written into the .lpf before map")
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

        lpf = out / "top.lpf"
        lpf_from_amaranth(args.lpf, lpf)
        if args.freq:
            # A frequency constraint is what makes the timing number mean
            # "the tool worked for this", rather than "this is what fell out".
            with open(lpf, "a") as f:
                f.write(f'\nFREQUENCY NET "clk" {args.freq} MHz;\n')

        ngd = out / f"{args.top}.ngd"
        total = 0.0

        if args.mode == "lse":
            # Diamond's own synthesis, straight from Verilog.
            cmd = ["synthesis", "-a", ARCH, "-d", DEVICE, "-t", PACKAGE,
                   "-s", SPEED, "-top", args.top, "-ngd", ngd.name,
                   "-ver", src.name]
            for e in extra:
                cmd.append(e.name)
            t, _ = run(cmd, out, handle, env, "synthesis(LSE)")
            timings["synthesis"] = t
            total += t
        else:
            # yosys already did synthesis, and its netlist instantiates ECP5
            # primitives by name. Diamond only needs to read it in and bind it
            # against the device library, with no synthesis of its own -- so
            # any difference that survives is place-and-route, not synthesis.
            #
            # The handoff is EDIF: ngdbuild reads .ngo/.edif but not Verilog,
            # and routing structural Verilog back through LSE would let LSE
            # re-synthesise it, which would destroy the separation this mode
            # exists to create.
            ngo = out / f"{args.top}.ngo"
            t, _ = run(["edif2ngd", "-l", ARCH, "-d", DEVICE,
                        src.name, ngo.name], out, handle, env, "edif2ngd")
            timings["edif2ngd"] = t
            total += t
            t, _ = run(["ngdbuild", "-a", ARCH, "-d", DEVICE,
                        "-p", str(FOUNDRY / "ep5c00" / "data"),
                        ngo.name, ngd.name], out, handle, env, "ngdbuild")
            timings["ngdbuild"] = t
            total += t

        ncd = out / f"{args.top}.ncd"
        prf = out / f"{args.top}.prf"
        t, _ = run(["map", ngd.name, lpf.name, "-a", ARCH, "-p", DEVICE,
                    "-t", PACKAGE, "-s", SPEED, "-o", ncd.name,
                    "-pr", prf.name], out, handle, env, "map")
        timings["map"] = t
        total += t

        par_ncd = out / f"{args.top}_par.ncd"
        t, _ = run(["par", "-w", "-l", args.par_effort, "-n", "1",
                    ncd.name, par_ncd.name, prf.name],
                   out, handle, env, "par")
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
                   out, handle, env, "trce")
        timings["trce"] = t
        total += t

        t, _ = run(["bitgen", "-w", "-d",
                    par_ncd.name if par_ncd.parent == out else str(par_ncd)],
                   out, handle, env, "bitgen")
        timings["bitgen"] = t
        total += t

        # Collect the numbers.
        util, fmax = {}, {}
        for mrp in list(out.glob("*.mrp")):
            util.update(parse_map_report(mrp.read_text(errors="replace")))
        for twr in list(out.glob("*.twr")):
            fmax.update(parse_twr(twr.read_text(errors="replace")))

        log("\n=== Diamond result ===", handle)
        log(f"mode        {args.mode}", handle)
        log(f"utilisation {util}", handle)
        log(f"fmax        {fmax}", handle)
        log(f"time        {total:.1f}s  {timings}", handle)

        res = {"mode": args.mode, "device": DEVICE, "util": util,
               "fmax": fmax, "seconds": round(total, 1),
               "steps": {k: round(v, 1) for k, v in timings.items()}}
        (out / "result.json").write_text(json.dumps(res, indent=2))
        log(f"wrote {out / 'result.json'}\nlog {logpath}", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
