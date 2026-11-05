#!/usr/bin/env python3
# Compare the non-routing content of two nextpnr-ecp5 text configurations, by tile type.
# SPDX-License-Identifier: BSD-3-Clause

"""
Two nextpnr runs on one netlist and one constraint file place and route differently, so their
`arc:` lines differ. Everything else a tile carries (pin type, drive, slew, pull, clock and PLL
setup) comes from the constraints and the netlist, and should match.

    ./scripts/ecp5_config_diff.py a.config b.config [--show PIC_T0 --limit 10]

- Prints, per tile type: tiles in both, tiles only in one, tiles whose non-arc lines differ.
- `--show TYPE` prints the first differences for that tile type.
- Log: ./tmp/logs/ecp5_config_diff.log
"""

import argparse
import collections
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "ecp5_config_diff.log"


def parse(path):
    """tile name -> (tile type, sorted non-arc lines), plus the lines outside any tile."""
    tiles, outside, current = {}, [], None
    for line in Path(path).read_text().splitlines():
        if line.startswith(".tile "):
            name, _, kind = line[6:].partition(":")
            current = tiles[name] = (kind, [])
        elif line.startswith("."):
            current = None
            outside.append(line)
        elif current is not None and line and not line.startswith("arc:"):
            current[1].append(line)
        elif current is None and line:
            outside.append(line)
    return {name: (kind, sorted(lines)) for name, (kind, lines) in tiles.items()}, outside


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--show", help="tile type whose differences to print")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    tiles_a, outside_a = parse(args.a)
    tiles_b, outside_b = parse(args.b)
    logging.info("a=%s  b=%s", args.a, args.b)
    logging.info("lines outside tiles: a=%d b=%d, equal=%s", len(outside_a), len(outside_b),
                 outside_a == outside_b)

    stats = collections.defaultdict(lambda: collections.Counter())
    examples = collections.defaultdict(list)
    for name in sorted(set(tiles_a) | set(tiles_b)):
        in_a, in_b = tiles_a.get(name), tiles_b.get(name)
        kind = (in_a or in_b)[0]
        if in_a and in_b:
            stats[kind]["both"] += 1
            if in_a[1] != in_b[1]:
                stats[kind]["differ"] += 1
                only_a = [x for x in in_a[1] if x not in in_b[1]]
                only_b = [x for x in in_b[1] if x not in in_a[1]]
                examples[kind].append((name, only_a, only_b))
        else:
            stats[kind]["only_a" if in_a else "only_b"] += 1
            lines_a, lines_b = (in_a[1] if in_a else []), (in_b[1] if in_b else [])
            if lines_a or lines_b:      # a tile present in one run only, carrying more than routing
                examples[kind].append((name, lines_a, lines_b))

    logging.info("%-14s %6s %6s %7s %7s", "tile type", "both", "differ", "only_a", "only_b")
    for kind in sorted(stats):
        s = stats[kind]
        logging.info("%-14s %6d %6d %7d %7d", kind, s["both"], s["differ"], s["only_a"],
                     s["only_b"])
    if args.show:
        for name, only_a, only_b in examples[args.show][:args.limit]:
            logging.info("%s\n  only a: %s\n  only b: %s", name, only_a[:6], only_b[:6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
