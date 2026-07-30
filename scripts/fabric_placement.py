#!/usr/bin/env python3
#
# Where on the die did the fabric test's logic actually land?
# See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reports the distribution of configured LUTs across the die's tile rows.

A high utilisation number is necessary but not sufficient. "20,143 of 24,288"
is a count, and a count is compatible with a placement that packs everything
into one dense corner -- which would leave open the possibility that the design
never touched whatever region a 12F might have failed test in. The interesting
question is not how many LUTs were used but *where they are*.

So this parses `top.config`, the textual bitstream ecppack consumes, and counts
LUT initialisation entries per tile row. That file is the placement as the tools
finally committed it, after packing and routing -- not an intention read back
out of the source, and not nextpnr's summary.

Rows with no entries are worth reading carefully rather than as gaps: on this
die every twelfth row is EBR or DSP rather than logic, so an empty row there is
the floorplan, not a hole in the placement.

    ./scripts/fabric_placement.py
    ./scripts/fabric_placement.py --config path/to/top.config
"""

import argparse
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_placement.log"
CONFIG = ROOT / "ecp5-test" / "fabric" / "build" / "top.config"

TILE = re.compile(r"^\.tile\s+R(\d+)C(\d+)")


def survey(path):
    """Returns (per-row counts, per-column counts, total entries)."""
    rows = collections.Counter()
    columns = collections.Counter()
    total = 0
    row = column = None
    for line in path.read_text().splitlines():
        match = TILE.match(line)
        if match:
            row, column = int(match.group(1)), int(match.group(2))
            continue
        # A LUT that holds a function has an INIT setting. Tiles appear in the
        # config for routing alone, so counting tiles would overstate how much
        # logic is placed; counting INIT entries counts logic.
        if row is not None and ".INIT" in line:
            rows[row] += 1
            columns[column] += 1
            total += 1
    return rows, columns, total


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()

    if not args.config.exists():
        print(f"no config at {args.config} -- run scripts/fabric_build.py")
        return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        rows, columns, total = survey(args.config)
        if not rows:
            emit("no LUT INIT entries found -- refusing to describe a "
                 "placement that was not parsed")
            return 1

        span = range(min(rows), max(rows) + 1)
        populated = [r for r in span if rows[r]]
        empty = [r for r in span if not rows[r]]
        counts = [rows[r] for r in populated]

        emit(f"{args.config.name}: {total} LUT INIT entries")
        emit(f"tile rows spanned: R{min(rows)} to R{max(rows)}")
        emit(f"rows carrying logic: {len(populated)} of {len(span)}")
        emit(f"per-row entries: min {min(counts)}, max {max(counts)}, "
             f"mean {sum(counts) / len(counts):.0f}")
        emit()

        widest = max(counts)
        for row in span:
            count = rows[row]
            if count:
                bar = "#" * max(1, round(40 * count / widest))
                emit(f"  R{row:>3} {count:>6}  {bar}")
            else:
                emit(f"  R{row:>3} {'-':>6}  (no logic; EBR/DSP row)")

        emit()
        emit(f"columns carrying logic: {len(columns)}, "
             f"C{min(columns)} to C{max(columns)}")
        emit()

        # The point of the histogram: a flat one means the placement is spread
        # over the whole die, so the design cannot have avoided whichever region
        # a salvage part would have failed in. A peaked one would mean the
        # utilisation figure is real but concentrated, and the experiment would
        # be weaker than its headline number suggests.
        spread = min(counts) / max(counts)
        emit(f"flatness (min/max row occupancy): {spread:.2f}")
        if spread < 0.5:
            emit("  Uneven. The utilisation total is real but the logic is "
                 "concentrated, so it is not safe to say the whole die was "
                 "exercised.")
        else:
            emit("  Even. Logic is distributed across every logic row of the "
                 "die, so the design could not have confined itself to a "
                 "12k-sized subset.")
        emit(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
