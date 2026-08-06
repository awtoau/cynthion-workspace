#!/usr/bin/env python3
#
# One door for the HyperRAM harnesses. See awtoau/cynthion-workspace#189.
# SPDX-License-Identifier: BSD-3-Clause

"""
Run any HyperRAM harness, and say what each measures and whether it has ever run.

    ./dev.py hyperram                       # the inventory below
    ./dev.py hyperram --markdown            # the same table, for a doc
    ./dev.py hyperram identify
    ./dev.py hyperram ceiling -- --build
    ./dev.py hyperram measure -- --note "DQS, CK 120"
    ./dev.py hyperram ladder -- --clocks 60 120

## Every harness is TWO files with the SAME name

A runner under `scripts/` builds a bitstream, loads it and reads the result
registers back; a gateware top under `gateware/probes/hyperram/` is what it builds.
The pair share a basename because they are halves of one thing.

That shape was read as duplication in #189 -- `hyperram_fifo.py`,
`hyperram_regfuzz.py` and `hyperram_identify.py` each "existed twice". None of
them did. The real fault was worse and the opposite way round: two of the three
runners had been archived to `debris/scripts/` as spent one-offs while their
gateware stayed live, so `gateware/README.md` and `docs/chips/w956a8-hyperram.md`
both name `scripts/hyperram_fifo.py`, `scripts/hyperram_regfuzz.py` and
`scripts/hyperram_ladder.py` as the host drivers for paths that did not exist.
`./dev.py audit` could not see it -- its DANGLING check reads only `scripts/`,
so a doc or a gateware top referring to an archived runner is invisible to it.

The runners are back, and this file is what makes them reachable from `dev.py`
rather than reachable by remembering a path and a flag.

## Exactly one of these touches the #185 write path

#185 is about coalescing: a HyperBus data phase cannot be stalled, and the SoC's
Wishbone master bubbles, so holding a transaction open corrupts the write. That
master is `HyperRAMWishbone` in `gateware/soc/vexii_bootram.py` (`sustained`
defaults to False for exactly this reason), and **not one of the eleven tops
under `gateware/probes/hyperram/` goes near it.** Every one drives `HyperRAMInterface`
or `HyperRAMDQSInterface` from its own FSM, which supplies and consumes a word
per cycle by construction, so none of them can express the fault.

That is not a criticism of them -- they measure the part, correctly, which is why
they report 198-314 MB/s against the SoC's 5.43. It is the reason the fault
survived: the only harness on the affected path is `measure`, which drives the
shipping SoC over its console and has no gateware of its own, and the only
regression cover is `scripts/soc_hyperram_sim.py`, which now runs the
`sustained=True` and `sustained=False` cases against each other.

Eleven HyperRAM test programs in one directory, and the write path the CPU uses
is in none of them.

## "Has it ever run" is a separate question from "does it build"

`gateware/README.md` records the build state of every test bitstream. This
records whether anyone ever put the result on silicon and wrote the number down,
which is a different and less flattering fact: a harness that was built and left
is not coverage, and `hyperram_ceiling.py` sat in that state from the day it was
written until 2026-08-03.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TOPS = ROOT / "gateware" / "probes" / "hyperram"


# name -> (runner, gateware top, what it measures, whether it has ever run)
#
# `run` states the evidence, not a verdict: a hash, a doc, or the absence of
# both. "no" means nothing in the tree records a result from it.
HARNESSES = {
    "identify": (
        "hyperram_identify.py", "hyperram_identify.py",
        "ID0/ID1/CR0/CR1, the density they decode to, and whether banks alias",
        "yes -- ID0 0x0c86 and the 8 MiB boundary bracketed (d77f052, c535ba7, "
        "1c8d10a); the part is in docs/chips/w956a8-hyperram.md because of it",
    ),
    "regfuzz": (
        "hyperram_regfuzz.py", "hyperram_regfuzz.py",
        "sixteen register addresses, reporting any that answers other than 0x8484",
        "yes -- found the undocumented 0x1000 block, ASCII '00029740' (32fa29f), "
        "and proved the write path against CR0 (50e5760)",
    ),
    "ceiling": (
        "hyperram_ceiling.py", "hyperram_ceiling_top.py",
        "the device CK at which reads stop verifying, both PHYs, with BURSTDET "
        "and a per-word error count under a legal tCSM burst cap",
        "yes, but not until 2026-08-03 -- written, built and left before that. "
        "Full 22-rung sweep 2026-08-05 (tmp/hyperram-ceiling/results.json): DQS "
        "clean to CK 180 at 313.5 MB/s, non-DQS capped at CK 140 by the FABRIC, "
        "not the part -- it clocks the fabric at CK, so CK 150/160/180 miss "
        "timing at 139.3/147.7/134.6 MHz and never produce a bitstream. Six "
        "rungs report 'invalid control' and are correctly refused, not scored.",
    ),
    "readclksel": (
        "hyperram_readclksel_sweep.py", "hyperram_ceiling_top.py",
        "the eight DQS capture phases at one CK, and how wide the passing window is",
        "NO -- af28b08 says so outright: 'nothing here has run on hardware'. "
        "A procedure, not a measurement.",
    ),
    "fifo": (
        "hyperram_fifo.py", "hyperram_fifo.py",
        "throughput against chunk size when writes and reads alternate, i.e. how "
        "much of the streaming rate survives turning the bus around",
        "yes, once -- ten chunk sizes at 120 MHz, 163,840 words verified "
        "(18144d3); the table is in docs/chips/w956a8-hyperram.md",
    ),
    "measure": (
        "hyperram_measure.py", None,
        "the SoC's own path: `hr cross` and `hr test` for correctness, then "
        "`hr bench` and `cpu stats` for MB/s and where the cycles went. Talks to "
        "the running firmware -- no bitstream of its own, and it configures nothing",
        "yes, repeatedly, and it is THE ONE that measures the #185 path: 5.43 "
        "MB/s sequential and 20.71 MB/s four-deep are its numbers (37ac105), as "
        "is the 1.89x from four loads deep (a7e4147)",
    ),
    "dqs-pins": (
        "hyperram_dqs_pins.py", None,
        "whether DQSBUFM can reach this board's HyperRAM pins, from the prjtrellis "
        "database -- no board involved",
        "yes -- left DQS group 8, bank 7, RWDS on the strobe pin; it is what "
        "settled #92 (5795b9a). Runs anywhere, in about a second.",
    ),
}

# Gateware tops with no runner. Listed because a top nobody can drive is the
# other half of the same problem, and #189 is about being able to find both.
#
# The run/never-run calls here are from commit evidence, and two disagree with
# `gateware/README.md`'s silicon-result column -- see the note under each.
ORPHAN_TOPS = {
    "hyperram_stress.py":
        "bulk, retention and random-access fill/verify; JTAG registers, no host "
        "runner ever written. RUN, and passed: 0/16384 bulk, 0/16384 after ~6 ms "
        "retention, 0/4096 random, 119.8 MB/s at 120 MHz (3f436d4). The README "
        "row saying 'hardware required' is stale -- the file is unchanged since.",
}

# RETIRED, and deleted rather than kept as a row here: `hyperram_test_minimal.py`
# and `hyperram_burst_test.py` had never run and could not (one never builds, the
# other's only driver carries a literal '/path/to/cynthion/constraints');
# `hyperram_burst_test_sim.py` ran once and failed verification while exiting
# zero; `hyperram_dqs_top.py` had never run and would hang if it did, because
# `perform_write` rises in `m.d.sync` on the start edge; `hyperram_speed.py` and
# its `hyperram_ladder.py` runner produced a figure that is invalid as a
# sustained rate, holding CS# low ~17 us against CR1's 4 us tCSM.
#
# `gateware/README.md` already said "retire" for every one of them. They are in
# git history, which is where superseded code belongs.


def table(markdown=False):
    rows = [(n, *v) for n, v in sorted(HARNESSES.items())]
    if markdown:
        out = ["| harness | runner | gateware top | measures | ever run |",
               "|---|---|---|---|---|"]
        out += [f"| `{n}` | `scripts/{r}` | "
                f"{'`gateware/probes/hyperram/' + t + '`' if t else 'none (host only)'} "
                f"| {m} | {e} |" for n, r, t, m, e in rows]
        out.append("")
        out.append("| gateware top with no runner | what it would measure |")
        out.append("|---|---|")
        out += [f"| `gateware/probes/hyperram/{t}` | {w} |"
                for t, w in sorted(ORPHAN_TOPS.items())]
        return "\n".join(out)

    out = []
    for name, runner, top, measures, evidence in rows:
        out.append(f"  {name:<11} {measures}")
        out.append(f"              runner  scripts/{runner}")
        out.append(f"              top     "
                   f"{'gateware/probes/hyperram/' + top if top else '-- host only'}")
        out.append(f"              run?    {evidence}")
        out.append("")
    out.append("  gateware tops with no runner:")
    for top, what in sorted(ORPHAN_TOPS.items()):
        out.append(f"    {top:<28} {what}")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("harness", nargs="?", choices=sorted(HARNESSES),
                        help="which one to run")
    parser.add_argument("--markdown", action="store_true",
                        help="print the inventory as a Markdown table")
    parser.add_argument("rest", nargs=argparse.REMAINDER,
                        help="arguments passed through to the runner")
    args = parser.parse_args()

    if args.markdown:
        print(table(markdown=True))
        return 0

    if not args.harness:
        print(__doc__.split("## Every harness")[0].rstrip())
        print("\nharnesses:\n")
        print(table())
        print("\n  dqs-pins needs no board at all; ceiling and readclksel have a")
        print("  --build mode that stops before one. Everything else reaches it.")
        # 0, not the usual usage 2: printing the inventory IS the default action
        # here, and #189 exists because nobody could find it.
        return 0

    runner, top, _measures, _evidence = HARNESSES[args.harness]
    if top and not (TOPS / top).exists():
        print(f"missing gateware top: gateware/probes/hyperram/{top}", file=sys.stderr)
        return 1
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    return subprocess.call([sys.executable, str(SCRIPTS / runner), *rest],
                           cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
