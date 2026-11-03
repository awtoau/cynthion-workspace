#!/usr/bin/env python3
#
# Does dynamic phase shift do anything on this part, through this flow? #228.
# SPDX-License-Identifier: BSD-3-Clause

"""Step the HyperRAM PLL's phase around a full rotation and watch the probe.

    ./scripts/hyperram_phase_sweep.py                  # CLKOS2, one rotation
    ./scripts/hyperram_phase_sweep.py --output clkos   # the DQS edge clock
    ./scripts/hyperram_phase_sweep.py --bist           # a BIST cell per step

Needs a bitstream built with the lever and the probe. `CYNTHION_HYPERRAM_PHASE`
and `bist phase` are NOT on `main` -- see #228. This is the method and the
record; without them it exits on the first reply.

    CYNTHION_HYPERRAM_BIST=1 CYNTHION_HYPERRAM_BIST_DQS=0 \\
      CYNTHION_HYPERRAM_CK_MHZ=80,90 CYNTHION_HYPERRAM_PHASE=1 \\
      CYNTHION_CLOCK_MIRROR=1 \\
      python3 scripts/soc_run.py --skip-tests --force-flash

## What it establishes, and what it cannot

#228 was logged with four things unknown. This answers three of them from the
board rather than from a datasheet:

  * **does a step move anything** -- the probe bit must flip, twice per rotation.
    No flip means the lever is decoration.
  * **how big a step is** -- the flips are half a rotation apart if the gateware's
    `8 * CLKOP_DIV` arithmetic is right. Counting them measures it.
  * **does the PLL relock** -- `locked` is read after every step. A drop, or a
    step that costs a lock time, shows here.

It CANNOT say whether the phase moved the HyperRAM read window, and no run of it
should be quoted as though it had. `--bist` runs a cell per step, but on this
build both the CK output and the capture are clocked from the SAME PLL output, so
a shift moves them together and the read margin is unchanged by construction. That
is the refutation in `docs/chips/hyperram/bist-plan.md`, "PLL dynamic phase shift
is NOT a capture-phase axis".

Transcript -> `tmp/logs/hyperram-phase-sweep.txt`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-phase-sweep.txt"

# `  steps 12  of 72 per rotation  level 1  count 65536`
STATE = re.compile(r"steps\s+(-?\d+)\s+of\s+(\d+)\s+per rotation\s+"
                   r"level\s+(\d+)\s+count\s+(\d+)")
CELL = re.compile(r"errors\s+(\d+)\s+words\s+(\d+)\s+control\s+(\d+)")

OUTPUTS = ("clkos", "clkos2", "clkos3", "clkop")

# `WINDOW_CYCLES` in `gateware/soc/peripherals/pll_phase.py`. Only the midpoint
# is used, to split "the probe is high" from "the probe is low".
WINDOW = 65536


class Board:
    def __init__(self):
        self.link = soc_shell.Link.open(None)
        self.log = []
        # Prime the link: after a configure the first prompt seen is the
        # banner's, so the first real reply would be read as empty.
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget=4):
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        self.log.append(f"$ {command}\n{text}")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.log))


def state(text):
    """(steps, rotation, level, count, locked) from a `bist phase` reply."""
    match = STATE.search(text)
    if not match:
        raise SystemExit(
            "no phase state in the reply. Either this bitstream was built "
            f"without CYNTHION_HYPERRAM_PHASE, or the firmware predates `bist "
            f"phase`. Reply was:\n{text}")
    steps, rotation, level, count = (int(g) for g in match.groups())
    return steps, rotation, level, count, "NOT LOCKED" not in text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="clkos2", choices=OUTPUTS,
                        help="which PLL output to shift (default clkos2, the "
                             "probe clock; clkop is this PLL's feedback and "
                             "moves everything ELSE instead)")
    parser.add_argument("--rotations", type=float, default=1.0,
                        help="how far to walk, in rotations (default 1)")
    parser.add_argument("--bist", action="store_true",
                        help="run one BIST cell per step, to see whether the "
                             "memory path noticed")
    args = parser.parse_args()

    board = Board()
    try:
        # RUNG 0, FIRST. The probe clock is built at rung 0's frequency, so with
        # rung 1 live `hr` and the probe differ and the XOR beats instead of
        # holding still -- the count then lands on aliases of the counting clock
        # and reads as a phase that is not there. `hyperram_ck_sweep.py` leaves
        # the last rung selected, so this is the normal state to arrive in.
        board.send("bist ck 0")

        first = board.send("bist phase")
        _, rotation, level, count, locked = state(first)
        if not locked:
            raise SystemExit("the PLL is not locked before any step; nothing "
                             "measured after this would mean anything")
        total = max(1, round(rotation * args.rotations))
        print(f"{rotation} steps per rotation, walking {total} on {args.output}")
        print(f"start: level {level} count {count}\n")
        print("  step  level      count  locked" + ("  bist" if args.bist else ""))

        # Keyed on `count`, not on `level`. The level is one sample taken
        # whenever the CPU happened to read; the count is that bit accumulated
        # over a whole window, so a crossing shows as an intermediate value
        # instead of as a coin flip.
        flips, previous = [], count > WINDOW // 2
        for index in range(1, total + 1):
            text = board.send(f"bist phase {args.output} 1")
            steps, _, level, count, locked = state(text)
            row = f"  {steps:>4}  {level:>5}  {count:>9}  {locked!s:>6}"
            if args.bist:
                cell = board.send("bist cell 3 dif 0 2 fix", 10)
                found = CELL.search(cell)
                row += f"  {'?' if not found else found.group(1) + ' err'}"
            print(row)
            high = count > WINDOW // 2
            if high != previous:
                flips.append(index)
                previous = high
            if not locked:
                print("  PLL LOST LOCK -- stopping")
                break

        print()
        if not flips:
            print("NO FLIP in a full rotation. The probe never moved, so either "
                  "the shifter did nothing or the probe cannot see it. Do not "
                  "read a phase sweep off this bitstream.")
            return 1
        print(f"flips at steps {flips}")
        if len(flips) >= 2:
            spacing = [b - a for a, b in zip(flips, flips[1:])]
            print(f"spacing {spacing}; a rotation is {rotation} steps")
        print("The phase MOVED. That is all this says -- see the docstring for "
              "what it does not say about the read window.")
        return 0
    finally:
        board.close()
        print(f"\ntranscript -> {TRANSCRIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
