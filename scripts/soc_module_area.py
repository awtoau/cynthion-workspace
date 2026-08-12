#!/usr/bin/env python3
#
# What each module in a built SoC actually costs, in context. #451.
# SPDX-License-Identifier: BSD-3-Clause

"""Per-module area and critical-path share, read out of one finished build.

`scripts/soc_peripheral_area.py` synthesises each peripheral ALONE, which is an
UPPER BOUND: constants are not folded, unread outputs are not pruned, and CSR
read multiplexers are not merged with their neighbours. This reads the netlist
the build actually produced, so every number here is the module's cost with all
of that already done.

## How the attribution works

`yosys` keeps the Amaranth hierarchy in cell names -- `serial.usb.data_crc.
crc_TRELLIS_FF_Q_14`, `stager.fifo.produce_w_gry_...` -- and cells created by
ABC out of a submodule's logic inherit its prefix. So the first dot-component of
a cell name is the `m.submodules.<name>` it came from, and a name with no dot is
logic written in `top.py` itself, reported as `(top glue)`.

## A ROW IS A SNAPSHOT, NOT A MEASUREMENT (#442)

**Do not difference these rows across two builds, and do not quote one as what
a module would recover if deleted.** ABC names a mapped cell after whichever net
it kept, and those names move when anything upstream of them changes. Measured:
one build with the SPI controller stubbed moved `cpu` -- a pre-generated Verilog
netlist that did not change at all -- by +102 COMB, `hyper_probe` by +390 and
`flash_probe` by +186, while the whole design moved by 16.

Two things here ARE trustworthy: the TOTAL, which is checked against nextpnr,
and `--noflatten`, which keeps real module boundaries. For what a deletion
recovers, use `scripts/soc_trim_delta.py`, which builds both ways;
`scripts/soc_csr_mux_cost.py` is the controlled version of the one structural
claim this table did support, that a CSR block's multiplexer outweighs it.

`TRELLIS_COMB` is what nextpnr counts and what the 24,288 limit is against; it
does not exist before packing, so it is modelled here as
`LUT4 + PFUMX + L6MUX21 + CCU2C` and the model is CHECKED against the
`Info: Device utilisation` line of the same build's `top.tim`. A residual is
printed rather than hidden; a large one means the model is wrong for this build
and the columns should not be quoted.

## The critical path columns

`top.tim` prints the worst path per clock as a list of hops, each `logic` or
`routing` with a net or cell name. Those names carry the same hierarchy, so the
nanoseconds can be split by module. `paths` is how many of the reported critical
paths a module appears on at all, which is the congestion question: a module
that is small and on every path costs more than a large one off to the side.

    ./scripts/soc_module_area.py
    ./scripts/soc_module_area.py --build tmp/awto_soc/build/<variant> --depth 2
    ./scripts/soc_module_area.py --compare tmp/other-build

**#440: a build killed by its own bound leaves a parseable `top.tim`.** This
refuses any build whose report does not contain `Program finished normally.`

Output is mirrored to ./tmp/logs/soc_module_area.log.
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_module_area.log"

sys.path.insert(0, str(ROOT / "gateware"))

# The cell types that consume the two resources the die runs out of. CCU2C is a
# carry cell and occupies one TRELLIS_COMB, not two -- the check against
# `top.tim` below is what settles that rather than an assumption.
COMB_TYPES = ("LUT4", "PFUMX", "L6MUX21", "CCU2C")
COLUMNS = ("LUT4", "CCU2C", "PFUMX", "L6MUX21", "TRELLIS_FF", "DP16KD",
           "TRELLIS_DPR16X4", "MULT18X18D")

UTIL_RE = re.compile(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'(\S+)':\s+([\d.]+) MHz\s+\((\w+)")
HOP_RE = re.compile(r"^Info:\s+(logic|routing)\s+([\d.]+)\s+([\d.]+)\s+"
                    r"(?:Net|Source|Sink)?\s*(\S+)")
FINISHED = "Program finished normally."


def hierarchical(build, emit):
    """Re-synthesise the same netlist WITHOUT flattening, and report per module.

    The flat attribution above cannot split two modules `wiring.connect` joined:
    connect makes one net, yosys names the cells driving it after whichever end
    it kept, and the arbiter's address multiplexer therefore lands in whichever
    of the two the name came from. `synth_ecp5 -noflatten` keeps the module
    boundary, so each row is that module and nothing else.

    The cost is that cross-module optimisation does not happen, so these are
    slightly HIGHER than the flat numbers -- between out-of-context and the real
    thing.
    """
    script = build / "noflatten.ys"
    stat = build / "noflatten-stat.txt"
    if not stat.exists():
        script.write_text(
            "read_verilog VexiiRiscv.v\n"
            "read_rtlil top.il\n"
            "synth_ecp5 -top top -noflatten\n"
            f"tee -o {stat.name} stat\n")
        emit(f"  yosys -noflatten on {build} ...")
        proc = subprocess.run(["yosys", "-q", script.name], cwd=build,
                              capture_output=True, text=True,
                              env={**os.environ, "YOSYS_MAX_THREADS": "1"})
        if proc.returncode != 0:
            raise SystemExit(proc.stderr[-3000:])

    # `stat` prints "<count> <cell type>", and the same shape for "<n> wires".
    # Only the cell types below are counted, so the wire lines cannot leak in.
    known = set(COLUMNS) | set(COMB_TYPES) | {"TRELLIS_RAMW", "TRELLIS_IO"}
    modules, current = {}, None
    for line in stat.read_text().splitlines():
        header = re.match(r"=== (\S+) ===", line.strip())
        if header:
            current = header.group(1)
            modules[current] = collections.Counter()
            continue
        cell = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if cell and current and cell.group(2) in known:
            modules[current][cell.group(2)] = int(cell.group(1))

    emit()
    emit(f"  {'module (unflattened)':<44} {'COMB':>6} {'LUT4':>6} {'CCU2C':>6} "
         f"{'FF':>6} {'BRAM':>5}")
    emit("  " + "-" * 80)
    rows_out = []
    for name, counts in modules.items():
        comb = sum(counts.get(kind, 0) for kind in COMB_TYPES)
        rows_out.append((name, comb, counts))
    for name, comb, counts in sorted(rows_out, key=lambda row: -row[1])[:45]:
        emit(f"  {name[:44]:<44} {comb:>6} {counts.get('LUT4', 0):>6} "
             f"{counts.get('CCU2C', 0):>6} {counts.get('TRELLIS_FF', 0):>6} "
             f"{counts.get('DP16KD', 0):>5}")
    emit()
    emit("  Rows are per module INCLUDING its submodules where yosys nests them;")
    emit("  a parent and its children both appear, so these do not sum to the die.")


def default_build():
    """This worktree's own build directory for the current variant."""
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    import variant
    return variant.build_dir(ROOT)


def read_netlist(build, depth=1):
    """{module: Counter(cell type)} from one build's post-synthesis top.json."""
    design = json.loads((build / "top.json").read_text())
    cells = design["modules"]["top"]["cells"]
    per_module = collections.defaultdict(collections.Counter)
    for name, cell in cells.items():
        kind = cell["type"]
        if kind == "$scopeinfo":
            continue
        parts = name.split(".")
        head = ".".join(parts[:depth]) if len(parts) > 1 else "(top glue)"
        per_module[head][kind] += 1
    return per_module


def read_report(build):
    """(utilisation, fmax, hops) from one build's `top.tim`.

    #440: a nextpnr killed by its own bound writes a report that parses exactly
    like a finished one, so the completion line is required before any of it is
    returned.
    """
    text = (build / "top.tim").read_text()
    if FINISHED not in text:
        raise SystemExit(
            f"{build}/top.tim has no '{FINISHED}' -- the build did not finish, "
            f"and its numbers are not a result (#440)")

    utilisation, fmax, hops = {}, {}, []
    # Only hops inside a SAME-CLOCK critical path section count. The report also
    # prints a cross-domain path per clock pair, and summing those with the
    # intra-clock one attributes nanoseconds to a module for a path that is not
    # what the design closes on.
    inside = False
    for line in text.splitlines():
        section = re.search(r"Critical path report for (clock|cross-domain path)",
                            line)
        if section:
            inside = section.group(1) == "clock"
        match = UTIL_RE.match(line.strip())
        if match:
            utilisation[match.group(1)] = int(match.group(2))
        match = FMAX_RE.search(line)
        if match:
            fmax[match.group(1)] = (float(match.group(2)), match.group(3))
        match = HOP_RE.match(line)
        if match and inside:
            kind, delay, _total, name = match.groups()
            hops.append((kind, float(delay), name))
    return utilisation, fmax, hops


def path_share(hops, depth):
    """{module: (ns, hop count)} over every critical path the report lists."""
    share = collections.defaultdict(lambda: [0.0, 0])
    for _kind, delay, name in hops:
        head = ".".join(name.split(".")[:depth]) if "." in name else "(top glue)"
        share[head][0] += delay
        share[head][1] += 1
    return share


def rows(per_module, depth):
    """One row per module at `depth` levels of hierarchy, with a COMB estimate."""
    folded = collections.defaultdict(collections.Counter)
    for head, counts in per_module.items():
        folded[head] += counts
    out = []
    for name, counts in folded.items():
        comb = sum(counts[kind] for kind in COMB_TYPES)
        out.append((name, comb, counts))
    out.sort(key=lambda row: -row[1])
    return out


def report(build, depth, emit):
    per_module = read_netlist(build, depth)
    utilisation, fmax, hops = read_report(build)
    share = path_share(hops, 1)

    table = rows(per_module, depth)
    total_comb = sum(row[1] for row in table)
    measured = utilisation.get("TRELLIS_COMB", 0)

    emit(f"build: {build}")
    emit(f"  nextpnr: TRELLIS_COMB {measured}/24288, "
         f"TRELLIS_FF {utilisation.get('TRELLIS_FF', 0)}, "
         f"DP16KD {utilisation.get('DP16KD', 0)}/56")
    for clock, (mhz, verdict) in sorted(fmax.items()):
        emit(f"  {clock:<34} {mhz:>7.2f} MHz {verdict}")
    emit()

    emit(f"  {'module':<24} {'COMB':>6} {'%die':>5} {'LUT4':>6} {'CCU2C':>6} "
         f"{'MUX':>5} {'FF':>6} {'BRAM':>5} {'LUTRAM':>7} {'path ns':>8} "
         f"{'hops':>5}")
    emit("  " + "-" * 100)
    for name, comb, counts in table:
        ns, hop_count = share.get(name, [0.0, 0])
        emit(f"  {name[:24]:<24} {comb:>6} {100.0 * comb / 24288:>5.1f} "
             f"{counts['LUT4']:>6} {counts['CCU2C']:>6} "
             f"{counts['PFUMX'] + counts['L6MUX21']:>5} "
             f"{counts['TRELLIS_FF']:>6} {counts['DP16KD']:>5} "
             f"{counts['TRELLIS_DPR16X4']:>7} {ns:>8.2f} {hop_count:>5}")
    emit("  " + "-" * 100)
    emit(f"  {'sum of modules':<24} {total_comb:>6}")
    emit(f"  {'nextpnr, after packing':<24} {measured:>6}  "
         f"residual {measured - total_comb:+d} "
         f"({100.0 * abs(measured - total_comb) / max(measured, 1):.1f}% of the "
         f"total)")
    emit()
    emit("COMB is modelled as LUT4 + PFUMX + L6MUX21 + CCU2C before packing; the")
    emit("residual above is the whole of the error in that model for this build.")
    return {name: (comb, counts) for name, comb, counts in table}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", type=Path, default=None,
                        help="a finished build directory (default: this variant's)")
    parser.add_argument("--compare", type=Path, default=None,
                        help="a second build; report the per-module delta")
    parser.add_argument("--depth", type=int, default=1,
                        help="levels of hierarchy to fold to (default 1)")
    parser.add_argument("--noflatten", action="store_true",
                        help="also re-synthesise unflattened, which splits two "
                             "modules `wiring.connect` joined (~90 s)")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    build = args.build or default_build()
    first = report(build, args.depth, emit)

    if args.noflatten:
        hierarchical(build, emit)

    if args.compare:
        emit()
        second = report(args.compare, args.depth, emit)
        emit()
        emit(f"  {'module':<24} {'COMB a':>8} {'COMB b':>8} {'delta':>8}")
        emit("  " + "-" * 52)
        emit("  NOT A PER-MODULE DELTA. Cell names move when the netlist does,")
        emit("  so a row here is churn plus signal and the two cannot be split.")
        emit("  This is here to SHOW that churn, not to be quoted (#442).")
        for name in sorted(set(first) | set(second)):
            a = first.get(name, (0, None))[0]
            b = second.get(name, (0, None))[0]
            if a != b:
                emit(f"  {name[:24]:<24} {a:>8} {b:>8} {b - a:>+8}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
