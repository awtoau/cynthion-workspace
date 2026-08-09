#!/usr/bin/env python3
#
# How many bitstreams a CK sweep costs, once a rung is a register write. #313, #228.
# SPDX-License-Identifier: BSD-3-Clause

"""
Cover a CK range at a given step, using as few bitstreams as the PLL allows.

    ./scripts/hyperram_ck_rungs.py                       # 100-200 MHz, 5 MHz steps
    ./scripts/hyperram_ck_rungs.py --low 100 --high 200 --step 5
    ./scripts/hyperram_ck_rungs.py --dqs                 # the other path

## What it is counting

`HyperRAMDomains` can put up to `MAX_RUNGS` CK values in ONE bitstream on the
non-DQS path: they are output dividers of a shared VCO and `DCSC` selects
between them at run time. So the cost of a sweep is bitstreams, not rungs, and
this is the arithmetic that says how many.

`MAX_RUNGS` is 2 and that is the silicon: `DCSC` is a 2:1 mux and the two DCS
bels do not cascade. The DQS path is 1 -- `hr_fast` is an edge clock, the bank
ECLK input mux takes CLKOP/CLKOS only, and `hr = hr_fast / 2` leaves nothing to
select. See `gateware/soc/hyperram_clocks.py`.

Output goes to `tmp/logs/hyperram_ck_rungs.log` as well as the terminal.
"""

import argparse
import itertools
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from hyperram_clocks import (MAX_RUNGS, reachable_ck,  # noqa: E402
                             solve_hr_pll, solve_hr_pll_rungs)

LOG = ROOT / "tmp" / "logs" / "hyperram_ck_rungs.log"


def cover(targets, rungs_per_build, input_mhz):
    """Greedy: the pair covering the most uncovered targets, until none are left.

    Greedy rather than exact because the set is tiny and an optimal cover would
    be a solver for a number that differs by at most one bitstream.
    """
    remaining = list(targets)
    builds = []
    while remaining:
        best, best_hit = None, ()
        # Permutations, not combinations: CLKOP carries rung 0 and closes the
        # feedback loop, so the two orders are different questions and only one
        # of them may have an answer.
        for combo in itertools.permutations(targets, min(rungs_per_build,
                                                         len(targets))):
            if solve_hr_pll_rungs(list(combo), input_mhz) is None:
                continue
            hit = tuple(t for t in remaining if t in combo)
            if len(hit) > len(best_hit):
                best, best_hit = combo, hit
        if best is None:
            # Nothing pairs; every remaining target is its own bitstream.
            builds += [(t,) for t in remaining]
            break
        builds.append(best)
        remaining = [t for t in remaining if t not in best_hit]
    return builds


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--low", type=float, default=100.0)
    parser.add_argument("--high", type=float, default=200.0)
    parser.add_argument("--step", type=float, default=5.0)
    parser.add_argument("--input-mhz", type=float, default=60.0)
    parser.add_argument("--dqs", action="store_true",
                        help="the DQS path, which is one rung per bitstream")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    reach = reachable_ck(args.low, args.high, dqs=args.dqs, input_mhz=args.input_mhz)
    path = "dqs" if args.dqs else "non-dqs"
    log.info("%s: %d reachable CK in %g..%g MHz", path, len(reach),
             args.low, args.high)
    log.info("  %s", " ".join(f"{ck:g}" for ck in reach))

    # Snap each grid point to the nearest reachable CK; a rung that does not
    # exist is not a rung, and the grid is a request rather than a result.
    grid = [args.low + args.step * i
            for i in range(int((args.high - args.low) / args.step) + 1)]
    targets = sorted({min(reach, key=lambda ck: abs(ck - t)) for t in grid})
    log.info("%d distinct rungs cover a %g MHz grid", len(targets), args.step)

    per_build = 1 if args.dqs else MAX_RUNGS
    builds = cover(targets, per_build, args.input_mhz)
    log.info("%d bitstreams at %d rung(s) each:", len(builds), per_build)
    for combo in builds:
        # The DQS path clocks the fabric at CK/2 and gears `hr_fast` off the
        # same VCO, so its solver is the single-rung one and its divisors are
        # not this one's.
        if args.dqs:
            solved = solve_hr_pll(combo[0] / 2, args.input_mhz, with_fast=True)
            vco = f"VCO {solved[0]:g} MHz div {list(solved[3:])}"
        else:
            solved = solve_hr_pll_rungs(list(combo), args.input_mhz)
            vco = f"VCO {solved[0]:g} MHz div {solved[3]}"
        log.info("  %-24s %s", " ".join(f"{ck:g}" for ck in combo), vco)

    log.info("%d bitstreams against %d one-rung builds", len(builds), len(targets))


if __name__ == "__main__":
    main()
