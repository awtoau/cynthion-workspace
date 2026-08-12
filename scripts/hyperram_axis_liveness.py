#!/usr/bin/env python3
#
# Does each configuration axis actually reach the part? See #334.
# SPDX-License-Identifier: BSD-3-Clause

"""Prove an axis MOVED before believing a result that says it does not matter.

    ./scripts/hyperram_axis_liveness.py

## Why this exists

"No effect" and "not connected" produce identical data. Every fault found in the
2026-08-10 audit had that shape, and two of them survived for months because an
unapplied write is indistinguishable from an applied one that did nothing:

* CR0 writes went out as `0x0000` for a year and the sweep still printed rows
* the latency sweep moved the part and never the controller, so fifteen of
  sixteen codes were guaranteed to fail whatever the part did

The 4096-cell run says drive strength, capture phase, fixed/variable and
`CR1[6]` all have no effect. Only some of those claims are worth anything, and
the difference is whether something in the same run shows the axis moved.

## What each check can conclude

`REG_DEVICE_READBACK` holds CR0 as the PART reports it, latched by the engine's
`CONFIG_VERIFY`. So any CR0 field can be proven live by commanding two different
values and reading both back.

`REG_DEVICE_READBACK_CR1` does the same for CR1 (#334), so `CR1[6]` is settled
here too. It is a CAPABILITY bit: the vendor's application note (rev P01,
s6.5.5) says a part without differential-clock support silently DISCARDS the
write, so a readback that does not follow the command is the part refusing the
mode -- and every `dif`/`se` row ever taken is then void, not merely inert.

`sel` has NO read-back and never will: `READCLKSEL` goes to the FPGA's DQSBUFM,
not to the part. Nothing here can judge it, so it is reported from the engine's
own PHY flag and settled properly by `hyperram_axis_wiring.py` (#343).

**Every trial runs at a capture phase measured first, not at a constant (#421).**
The readback captures through the cell's own phase, so at a phase outside the
read eye both words belong to other transactions -- the board withholds them and
says so, which is not this script's own parse being wrong.

Transcript -> `tmp/logs/hyperram-axis-liveness.txt`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402
import soc_shell  # noqa: E402
from hyperram_readclksel_sweep import analyse_window, analysis_lines  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-axis-liveness.txt"

# Held at the power-on default so the phase probe below varies only the phase.
PROBE = "3 dif {sel} 2 fix"

# `bist status` prints `  CR0     part reports 0xbf2f  latency code 2 fixed  drive 3`.
# Matched against the board's real wording rather than an assumed one -- a regex
# that silently matches nothing is the same failure as an axis that silently
# does nothing.
READBACK = re.compile(r"CR0\s+part reports\s+(0x[0-9a-fA-F]+)")
READBACK_CR1 = re.compile(r"CR1\s+part reports\s+(0x[0-9a-fA-F]+)")
# `bist status` names the PHY, which is what decides whether `sel` is wired.
NON_DQS = re.compile(r"non-DQS PHY")

# CR0 fields, Table 8 rev A01-006 p.21.
FIELDS = {
    "drive  CR0[14:12]": (12, 0b111),
    "latency CR0[7:4]": (4, 0b1111),
    "fixed  CR0[3]": (3, 0b1),
}


class Board:
    def __init__(self):
        self.link = soc_shell.Link.open(None)
        self.log = []
        # See hyperram_ck_sweep.py: the first prompt after a configure is the
        # banner's, so an unprimed link returns before its own reply.
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget=8):
        self.link.write(command.encode() + b"\r")
        text = self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")
        self.log.append(f"$ {command}\n{text}")
        return text

    def close(self):
        self.link.close()
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text("\n".join(self.log))


def status_after(board, cell):
    """Run a cell, then read CR0 and CR1 back as the part reports them."""
    board.send(f"bist cell {cell}", 20)
    text = board.send("bist status")
    found = [pattern.search(text) for pattern in (READBACK, READBACK_CR1)]
    if not all(found):
        raise SystemExit(
            "no device readback in `bist status`. The parse, not the part, is the "
            f"first suspect. Reply was:\n{text}")
    return tuple(int(match.group(1), 0) for match in found)


def cr0_after(board, cell):
    return status_after(board, cell)[0]


def capture_phase(board):
    """Which `sel` to run the trials at, measured now rather than assumed (#421).

    `sel 0` was hardcoded here. It is outside the read eye on the current DQS
    build, so every readback came back WITHHELD -- the read path had slid -- and
    this script reported that as its own parse being wrong.

    Returns `(sel, why)`. Raises when no phase passes: nothing below can then be
    told from a part that did not store the field.
    """
    text = board.send("bist status")
    if NON_DQS.search(text):
        return 0, "non-DQS build: `sel` reaches nothing (#343), so there is no phase to choose"

    table = []
    for sel in range(8):
        command = "bist cell " + PROBE.format(sel=sel)
        row = bist_rows.require_rows(board.send(command, 20), command)[-1]
        table.append((sel, row["verdict"] == "PASS"))
    print("\nREADCLKSEL  the read eye, measured before anything is judged")
    for sel, passed in table:
        print(f"  sel {sel}: {'PASS' if passed else 'fail'}")
    analysis = analyse_window(table)
    for line in analysis_lines(analysis):
        print("  " + line)

    if analysis.kind == "no_pass":
        raise SystemExit(
            "NO CAPTURE PHASE PASSED, so no readback below could be trusted at "
            "any of them. This is the rig or the part, not the parse -- fix that "
            "first. `bist smoke` and `bist status` are the next two commands.")
    if analysis.kind == "all_pass":
        return 0, "every phase passed, so the choice cannot matter"
    if analysis.kind == "disjoint":
        (start, end), _ = max(zip(analysis.ranges, analysis.widths),
                              key=lambda pair: pair[1])
        return (start + end) // 2, f"the widest of {len(analysis.ranges)} windows"
    return analysis.centre, f"the centre of the one window {analysis.ranges[0]}"


def decode(value):
    return {name: (value >> shift) & mask for name, (shift, mask) in FIELDS.items()}


def main():
    board = Board()
    try:
        # THE PHASE IS MEASURED FIRST (#421). Every cell below captures through
        # it, the CR0/CR1 readback included, so a phase outside the eye makes
        # every trial report a word from another transaction.
        sel, why = capture_phase(board)
        print(f"\n  running every trial at sel {sel} -- {why}")

        # Each pair differs in ONE field, so a readback that changes can only be
        # that field moving.
        trials = [
            ("drive  CR0[14:12]", f"3 dif {sel} 2 fix", f"6 dif {sel} 2 fix"),
            ("latency CR0[7:4]", f"3 dif {sel} 2 fix", f"3 dif {sel} 15 fix"),
            ("fixed  CR0[3]", f"3 dif {sel} 2 fix", f"3 dif {sel} 2 var"),
        ]

        verdicts = {}
        for field, a, b in trials:
            first, second = cr0_after(board, a), cr0_after(board, b)
            fa, fb = decode(first)[field], decode(second)[field]
            live = fa != fb
            verdicts[field] = live
            print(f"\n{field}")
            print(f"  bist cell {a:16s} -> CR0 {first:#06x}  field {fa}")
            print(f"  bist cell {b:16s} -> CR0 {second:#06x}  field {fb}")
            print(f"  {'LIVE -- the part stored what was asked' if live else
                     'NOT PROVEN -- the field did not change in the readback'}")

        # CR1[6], the one axis with no CR0 field. Commanded both ways and read
        # back through #334's `REG_DEVICE_READBACK_CR1`.
        first, second = (status_after(board, f"3 dif {sel} 2 fix")[1],
                         status_after(board, f"3 se {sel} 2 fix")[1])
        bit_a, bit_b = (first >> 6) & 1, (second >> 6) & 1
        print("\nCR1[6]  clock mode")
        print(f"  bist cell 3 dif {sel} 2 fix  -> CR1 {first:#06x}  bit6 {bit_a}")
        print(f"  bist cell 3 se  {sel} 2 fix  -> CR1 {second:#06x}  bit6 {bit_b}")
        if first in (0, 0xffff) or second in (0, 0xffff):
            cr1_live = None
            print("  NOT PROVEN -- the CR1 read path is dead, so no dif/se row "
                  "carries information in either direction")
        elif bit_a == bit_b:
            cr1_live = False
            print("  NOT PROVEN -- the part DISCARDED the write, which the vendor "
                  "note says a part without differential-clock support does. Every "
                  "dif/se row is void, not inert.")
        else:
            cr1_live = True
            print("  LIVE -- the part stored the mode it was commanded")

        # `sel` from the ENGINE, because the part cannot answer for it. A
        # non-DQS build reads `READCLKSEL` nowhere, so its every row is a repeat
        # of its neighbour rather than a phase that made no difference (#343).
        sel_unwired = bool(NON_DQS.search(board.send("bist status")))
        print("\nREADCLKSEL  capture phase")
        if sel_unwired:
            print("  UNWIRED -- this is a non-DQS build and nothing in the "
                  "gateware reads the register. Every `sel` row is a REPEAT.")
        else:
            print("  no read-back exists for it in either build, so it is judged "
                  "by pass/fail: the eye above, and `hyperram_axis_wiring.py` "
                  "for it reaching the PHY")

        print("\nsummary")
        for field, live in verdicts.items():
            print(f"  {field:20s} {'live' if live else 'NOT PROVEN'}")
        print(f"  {'CR1[6] clock mode':20s} "
              f"{'live' if cr1_live else 'NOT PROVEN'}")
        print(f"  {'sel READCLKSEL':20s} "
              f"{'UNWIRED -- rows are repeats' if sel_unwired else f'live -- eye found, ran at {sel}'}")
        verdicts["CR1[6] clock mode"] = bool(cr1_live)

        if not all(verdicts.values()) or sel_unwired:
            print("\nAny sweep row varying a NOT PROVEN or UNWIRED field carries "
                  "no information, in either direction.")
    finally:
        board.close()
        print(f"\ntranscript -> {TRANSCRIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
