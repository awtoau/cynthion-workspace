#!/usr/bin/env python3
#
# Run ONE BIST cell many times and tabulate what it actually returns. #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Repeat one cell and report the distribution of its outcomes.

    ./scripts/hyperram_cell_repeat.py 3 dif 0 --repeat 40
    ./scripts/hyperram_cell_repeat.py 3 dif 1 --repeat 20 --settle "bist status"

## Why this and not the matrix

`hyperram_matrix_diff.py` compares two runs of 4096 cells, so it finds a cell
that moved -- but it prints one verdict per cell per run and cannot say how a
cell fails, or how often. Two failures with different word counts and different
data score the same there.

That distinction is the whole question at a bad capture phase. `bist cell 3 dif
0` has been seen to return 8192 errors over 8192 words, and to return 99 words
of 8192 -- a burst that ENDED EARLY. Those are different faults wearing one
verdict, and only a repeat count separates a rare one from a misread.

## The residue column

At a failing phase the first bad word is not random: it is what the data
registers were still holding, and it tracks the last register READ. Varying
CR1 through the `se` axis moves it one-for-one (0xff81ff81 -> 0xffc1ffc1), which
is what proves the axis reaches the part rather than the report. So the word is
tabulated rather than summarised -- a residue that changes with history is the
signal, and a mean would erase it.

`--settle` runs a command before each repeat, because the residue depends on
what ran last: after `bist status` the sel-0 word is 0x0000003f, after another
cell it is 0xff810000. Holding history constant is how a repeat count measures
the cell instead of the order.

Log: `tmp/logs/hyperram-cell-repeat.log`.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402
import soc_shell  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "hyperram-cell-repeat.log"

FIRST_BAD = bist_rows.FIRST_BAD


def emit(line=""):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drive", type=int)
    parser.add_argument("clock", choices=("dif", "se"))
    parser.add_argument("sel", type=int)
    parser.add_argument("--latency", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--settle", default=None,
                        help="a command to run before each repeat, to hold the "
                             "residue's history constant")
    args = parser.parse_args()

    cell = f"bist cell {args.drive} {args.clock} {args.sel}"
    if args.latency is not None:
        cell += f" {args.latency}"

    link = soc_shell.Link.open(None)
    link.settle(0.05)
    link.write(b"\r")
    link.read_until_prompt(budget_s=3)

    def send(command, budget=8):
        link.write(command.encode() + b"\r")
        return link.read_until_prompt(budget_s=budget).decode("ascii", "replace")

    emit(f"\n=== {cell}  x{args.repeat}"
         + (f"  settle {args.settle!r}" if args.settle else ""))
    outcomes, residues, unparsed = Counter(), Counter(), 0
    try:
        for _ in range(args.repeat):
            if args.settle:
                send(args.settle)
            text = send(cell)
            row = bist_rows.ROW.search(text)
            if not row:
                unparsed += 1
                continue
            got = bist_rows.cell(row)
            outcomes[(got["verdict"], got["errors"], got["words"],
                      got["control"])] += 1
            bad = FIRST_BAD.search(text)
            residues[bad["got"] if bad else "-"] += 1
    finally:
        link.close()

    # An unparsed reply is NOT a pass. It is the reply arriving after the read
    # gave up, and counting it as anything else is how a silent rig scores clean.
    if unparsed:
        emit(f"  {unparsed} of {args.repeat} replies did not parse -- NOT scored "
             "as pass or fail")
    # EVERY reply unparsed is the parser, not the part. An empty distribution
    # reads exactly like a board that reported nothing.
    if unparsed == args.repeat:
        raise SystemExit(
            f"NOT ONE of {args.repeat} replies parsed. The row format is "
            "scripts/bist_rows.py; tests/test_bist_row_parsers.py is the check "
            f"that should have caught a drift. Log: {LOG}")
    emit(f"  {'verdict':8s} {'errors':>8s} {'words':>8s} {'control':>8s}  count")
    for (verdict, errors, words, control), count in outcomes.most_common():
        short = "  SHORT" if words < control else ""
        emit(f"  {verdict:8s} {errors:8d} {words:8d} {control:8d}  {count:5d}{short}")
    emit("  first bad word:")
    for word, count in residues.most_common():
        emit(f"    {word}  x{count}")

    distinct = len(outcomes)
    emit(f"  {distinct} distinct outcome(s) over {args.repeat} runs"
         + ("  -- THIS CELL IS MARGINAL" if distinct > 1 else ""))
    # A cell with one outcome is a verdict; more than one is a distribution, and
    # a single run would have reported whichever it drew.
    return 1 if distinct > 1 or unparsed else 0


if __name__ == "__main__":
    sys.exit(main())
