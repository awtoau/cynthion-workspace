#!/usr/bin/env python3
"""Collect open-flow and Diamond results into one comparison table.

Reads what each flow actually wrote -- nextpnr's .tim log and Diamond's .mrp
and .twr -- rather than re-running anything, so the table can be regenerated
from an existing set of builds.

Two things this deliberately does *not* do:

  It does not compare Fmax numbers that were produced under different
  constraints. nextpnr's "Max frequency for clock" from a --freq-targeted run
  and Diamond's unconstrained timing report do not mean the same thing, and
  presenting them side by side would invite exactly the wrong conclusion.

  It does not treat a small utilisation difference as significant without
  reference to the measured noise floor. Run ./scripts/pnr_noise.py first;
  nextpnr's packing is deterministic (spread 0) but its Fmax is not (spread
  ~6%), and that threshold is printed alongside the table.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# scripts/diamond/<name>.py, so the repo root is three levels up. These lived in
# scripts/ and were moved; a stale `.parent.parent` put every log and artifact
# under scripts/tmp/ instead of tmp/, which is silent and wrong.
ROOT = Path(__file__).resolve().parent.parent.parent
LOGDIR = ROOT / "tmp" / "logs"

NEXTPNR_UTIL = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+\d+%")
NEXTPNR_FMAX = re.compile(
    r"^Info: Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz")

# Diamond's map report counts in its own vocabulary. These are the lines that
# correspond to what nextpnr reports.
MRP_PATTERNS = {
    "LUT4": r"Number of LUT4s:\s+(\d+)",
    "SLICE": r"Number of SLICEs:\s+(\d+)",
    "FF": r"Number of registers:\s+(\d+)",
    "DP16KD": r"Number of block RAMs:\s+(\d+)",
    "DSP": r"Number of DSP\w*:\s+(\d+)",
    "PIO": r"Number of PIOs?:\s+(\d+)",
}


def parse_nextpnr(tim_path):
    """Utilisation and per-clock Fmax from a nextpnr log."""
    util, fmax = {}, {}
    for line in Path(tim_path).read_text(errors="replace").splitlines():
        m = NEXTPNR_UTIL.match(line)
        if m:
            util[m.group(1)] = int(m.group(2))
        m = NEXTPNR_FMAX.match(line)
        if m:
            fmax[m.group(1)] = float(m.group(2))
    return util, fmax


def parse_diamond(outdir):
    """Utilisation from Diamond's .mrp, plus the LSE area report if present.

    The area report (.arearep) is the more useful of the two when map has not
    completed, because LSE writes it straight after synthesis. It is also the
    place a destroyed design shows up: if it reports more register bits than
    the part has, the memories did not survive the handoff.
    """
    outdir = Path(outdir)
    util, notes = {}, []

    for mrp in outdir.glob("*.mrp"):
        text = mrp.read_text(errors="replace")
        for key, pat in MRP_PATTERNS.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                util[key] = int(m.group(1))

    for area in outdir.glob("*.arearep"):
        text = area.read_text(errors="replace")
        cells = {}
        for m in re.finditer(r"^\s+(\w+)\s+(\d+)\s+[\d.]+\s*$", text,
                             re.MULTILINE):
            cells[m.group(1)] = int(m.group(2))
        if cells:
            util.setdefault("_lse_cells", cells)
        m = re.search(r"Register bits:\s+(\d+) of (\d+) \(([\d.]+)%\)", text)
        if m:
            used, avail, pct = int(m.group(1)), int(m.group(2)), float(m.group(3))
            util["_register_bits"] = used
            if pct > 100:
                notes.append(
                    f"register bits {used} exceed the part's {avail} "
                    f"({pct:.0f}%) -- the design did not survive the handoff, "
                    f"utilisation numbers are not comparable")
    return util, notes


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--open-tim", type=Path, action="append", default=[],
                    metavar="NAME=PATH",
                    help="open-flow nextpnr log, as design=path")
    ap.add_argument("--diamond", action="append", default=[],
                    metavar="NAME=DIR",
                    help="Diamond output directory, as design=dir")
    ap.add_argument("--noise", type=Path, default=None,
                    help="pnr_noise.py JSON, to print the significance floor")
    ap.add_argument("--name", default="diamond_compare")
    args = ap.parse_args()

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logpath = LOGDIR / f"{args.name}.log"
    lines = []

    def emit(msg):
        print(msg, flush=True)
        lines.append(msg)

    if args.noise and args.noise.exists():
        runs = json.loads(args.noise.read_text())
        emit("=== measured noise floor (same netlist, same tool, N seeds) ===")
        for key in ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD"):
            vals = [r["util"].get(key) for r in runs if key in r.get("util", {})]
            if vals:
                emit(f"  {key:14s} spread {max(vals) - min(vals)}")
        clocks = {c for r in runs for c in r.get("fmax", {})}
        for clock in sorted(clocks):
            vals = [r["fmax"][clock] for r in runs if clock in r.get("fmax", {})]
            if vals and min(vals):
                pct = 100.0 * (max(vals) - min(vals)) / min(vals)
                emit(f"  fmax {clock:26s} spread {pct:.1f}%")
        emit("  -> utilisation differences are signal; Fmax differences below")
        emit("     the spread above are not.\n")

    emit("=== open flow (yosys + nextpnr-ecp5) ===")
    for spec in args.open_tim:
        name, _, path = str(spec).partition("=")
        if not path:
            emit(f"  {spec}: expected design=path")
            continue
        util, fmax = parse_nextpnr(path)
        emit(f"  {name}")
        emit(f"    LUT={util.get('TRELLIS_COMB')} FF={util.get('TRELLIS_FF')} "
             f"BRAM={util.get('DP16KD')} DSP={util.get('MULT18X18D')}")
        for clock, mhz in fmax.items():
            emit(f"    fmax {clock}: {mhz} MHz")

    emit("\n=== Diamond ===")
    for spec in args.diamond:
        name, _, path = str(spec).partition("=")
        if not path:
            emit(f"  {spec}: expected design=dir")
            continue
        util, notes = parse_diamond(path)
        emit(f"  {name}")
        if not util:
            emit("    no report found -- the run did not get far enough")
        cells = util.pop("_lse_cells", None)
        for key, val in sorted(util.items()):
            emit(f"    {key}={val}")
        if cells:
            top = sorted(cells.items(), key=lambda kv: -kv[1])[:8]
            emit("    LSE cell usage: " +
                 ", ".join(f"{k}={v}" for k, v in top))
        for note in notes:
            emit(f"    WARNING: {note}")

    logpath.write_text("\n".join(lines) + "\n")
    print(f"\nlog {logpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
