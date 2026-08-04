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

Comparing configurations
------------------------

`--compare` takes several configs and reports how much of the placement they
share. A sweep that varies the nextpnr seed is only worth its build time if the
seed actually moves the logic; "different seed" is an input, not a result. The
overlap is measured as the share of occupied LUT sites two builds have in
common -- the exact `RxCy` tile and `SLICEn.Kn` position, not a row histogram,
because two placements can have identical row totals and share no site at all.

    ./scripts/fabric_placement.py
    ./scripts/fabric_placement.py --config path/to/top.config
    ./scripts/fabric_placement.py --compare tmp/fabric-coverage/seed-*/top.config
"""

import argparse
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

CONFIG = ROOT / "ecp5-test" / "fabric" / "build" / "top.config"

TILE = re.compile(r"^\.tile\s+R(\d+)C(\d+)")
SITE = re.compile(r"^word:\s+(SLICE[A-D]\.K\d)\.INIT")


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


def sites(path):
    """Returns {(row, column, "SLICEn.Kn")} -- every site holding a function.

    The identity of a placement, at the finest granularity the config records.
    Two builds that disagree here put the logic in physically different LUTs,
    which is what a differing seed is supposed to achieve and what has to be
    checked rather than assumed.
    """
    placed = set()
    row = column = None
    for line in path.read_text().splitlines():
        match = TILE.match(line)
        if match:
            row, column = int(match.group(1)), int(match.group(2))
            continue
        match = SITE.match(line)
        if match and row is not None:
            placed.add((row, column, match.group(1)))
    return placed


def overlap(first, second):
    """Share of occupied sites two placements have in common, 0.0 to 1.0."""
    union = first | second
    return len(first & second) / len(union) if union else 1.0


def forced_overlap(first, second, total_sites):
    """The smallest overlap two placements of these sizes could possibly have.

    A design occupying 20,300 of the die's 24,288 LUT sites cannot be moved very
    far, because there is nowhere for it to go: two such placements must share
    at least |A|+|B|-N sites however differently they are placed. Quoting a raw
    overlap without this floor makes a dense design look like it barely moved
    when in fact it moved as much as the die allows -- and the whole reason to
    fill the die is the fabric question, so the density is not negotiable.
    """
    least = max(0, len(first) + len(second) - total_sites)
    union = min(total_sites, len(first | second))
    return least / union if union else 1.0


def compare(paths, emit, total_sites=24288):
    """Pairwise placement overlap, against the floor density forces.

    `total_sites` is the die's LUT4 count -- 24,288 for the LFE5U-12F -- because
    a LUT site is one LUT4 and that is what bounds how far a placement can move.
    """
    placements = {}
    for path in paths:
        placements[path] = sites(path)
        emit(f"{path}: {len(placements[path])} occupied LUT sites of "
             f"{total_sites} on the die "
             f"({100 * len(placements[path]) / total_sites:.1f}%)")
    emit()

    names = [p.parent.name or p.name for p in paths]
    width = max(len(n) for n in names)
    # The full matrix is worth printing while it is readable. Past ten
    # configurations each row is wider than a terminal and the eye gets nothing
    # from it that the min/mean/max below does not say better.
    show = len(paths) <= 10
    if show:
        emit("pairwise overlap, as a share of the union of occupied sites:")
        emit(" " * (width + 2) + " ".join(f"{n[-6:]:>7}" for n in names))
    values = []
    floors = []
    for i, first in enumerate(paths):
        cells = []
        for j, second in enumerate(paths):
            share = overlap(placements[first], placements[second])
            cells.append(f"{share:7.3f}")
            if i < j:
                values.append(share)
                floors.append(forced_overlap(placements[first],
                                             placements[second], total_sites))
        if show:
            emit(f"{names[i]:<{width}}  " + " ".join(cells))
    if not show:
        emit(f"{len(paths)} configurations; the {len(values)}-pair matrix is "
             f"too wide to read, so only its range is reported")

    emit()
    if not values:
        emit("only one configuration -- nothing to compare")
        return {"pairs": 0}
    worst, best = max(values), min(values)
    mean = sum(values) / len(values)
    floor = sum(floors) / len(floors)
    emit(f"over {len(values)} pairs: overlap min {best:.3f}, "
         f"mean {mean:.3f}, max {worst:.3f}")
    emit(f"density forces at least {floor:.3f} of it: two placements this "
         f"large cannot share less")
    # What is left after the floor is what the seed actually bought, expressed
    # as a share of the room it had to move in. This is the number that says
    # whether the extra builds were different designs; the raw overlap above
    # mostly reports how full the die is.
    room = 1.0 - floor
    moved = (1.0 - mean) / room if room > 0 else 0.0
    emit(f"of the {100 * room:.0f}% of sites that were free to move, the seed "
         f"moved {100 * moved:.0f}%")
    if moved < 0.25:
        emit("  The seed barely moved the logic. The extra configurations are "
             "near-repeats of the first and the coverage they add is small; "
             "check the arc measurement before claiming otherwise.")
    else:
        emit("  The seed moved the placement about as far as a design this "
             "dense can move. Site occupancy is largely forced, so the real "
             "question is how much routing changed -- see fabric_arcs.py, "
             "which measures it directly rather than inferring it from here.")
    return {"pairs": len(values), "min": best, "mean": mean, "max": worst,
            "forced_floor": floor, "moved_share": moved,
            "sites": {str(p): len(s) for p, s in placements.items()}}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--compare", nargs="+", type=Path, default=None,
                        help="several configs; report how much placement they "
                             "share instead of surveying one")
    args = parser.parse_args()

    if args.compare:
        for path in args.compare:
            if not path.exists():
                print(f"no config at {path}")
                return 1
        compare(args.compare, emit)
        return 0

    if not args.config.exists():
        print(f"no config at {args.config} -- run scripts/fabric_build.py")
        return 1

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
