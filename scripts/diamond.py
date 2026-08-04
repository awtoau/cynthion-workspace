#!/usr/bin/env python3
#
# One door for everything Lattice Diamond.
# SPDX-License-Identifier: BSD-3-Clause

"""
Drive Lattice Diamond: probe the install, run a design, sweep a clock, compare.

    ./dev.py diamond probe            # is the install working
    ./dev.py diamond ladder           # sweep the SoC's clock, Diamond vs nextpnr
    ./dev.py diamond flow  -- ...     # one design through Diamond
    ./dev.py diamond compare -- ...   # collect results into a table

## Why one script

`scripts/` grew nineteen `diamond_*` files. Fifteen of them are one-off
extractions -- the bitstream frame database, package pinouts, primitive
cross-references, webhelp harvesting -- run once to answer a reverse-engineering
question, with the answer now living in a doc. Those are reference material and
stay in `debris/scripts/`.

Four do operational work, and they are what this dispatches to. They keep their
own implementations because each is a few hundred lines of real logic; what was
missing was a single name to reach them by, and a single place that knows where
Diamond is installed.

## Diamond as an oracle, not a replacement

The open flow is `yosys -> nextpnr-ecp5 -> ecppack` and it is what ships. Diamond
is an independent implementation of all three stages, so where it does better the
gap names a specific missing capability in the open tools rather than being a
reason to switch.

The immediate question it answers: **nextpnr's achieved frequency rises with the
frequency requested** -- 60 asked gives 72.6 MHz achieved, 80 gives 76.3 -- which
means its result is not a property of the design alone. That matters right now,
because the flash PHY's own domain failed at 144 MHz with 124.77 MHz achieved and
at 120 with 111.26. If a better placer closes 120, the flash runs twice as fast.
See #110 and #100.

## The install

`~/lscc/diamond/3.14`, which `flow.py` finds for itself. It is NOT on PATH and is
not a distro package; `probe` is the cheap way to find out whether it works
before spending a build on it.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "scripts" / "diamond"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import spawn  # noqa: E402

# Where Diamond is, stated once. `flow.py` carries the same path because it is
# the one that builds the environment; this is here so `probe` can say something
# useful before anything is run.
DIAMOND = Path.home() / "lscc" / "diamond" / "3.14"

COMMANDS = {
    "probe": ("check the install and the yosys -> Diamond EDIF handoff",
              "probe.py"),
    "flow": ("run one design through Diamond (needs --verilog/--lpf/--outdir)",
             "flow.py"),
    "ladder": ("sweep the SoC's clock constraint, Diamond against nextpnr",
               "ladder.py"),
    "compare": ("collect existing open-flow and Diamond results into a table",
                "compare.py"),
}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS),
                        help="what to run")
    parser.add_argument("rest", nargs=argparse.REMAINDER,
                        help="arguments passed through to it")
    args = parser.parse_args()

    if not args.command:
        print(__doc__.split("## Why one script")[0].rstrip())
        print("\ncommands:")
        for name, (summary, _) in sorted(COMMANDS.items()):
            print(f"  {name:9} {summary}")
        print(f"\ndiamond: {DIAMOND} "
              f"({'present' if DIAMOND.is_dir() else 'NOT FOUND'})")
        return 2

    if not DIAMOND.is_dir():
        print(f"Diamond is not at {DIAMOND}.", file=sys.stderr)
        print("It is not a distro package and is not on PATH; install it there "
              "or edit DIAMOND in this file.", file=sys.stderr)
        return 1

    _summary, script = COMMANDS[args.command]
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    return spawn([sys.executable, str(HERE / script), *rest], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
