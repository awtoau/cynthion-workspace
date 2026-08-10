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

There is no CR1 readback, which is why `CR1[6]` cannot be settled here at all --
that needs gateware, and it is the substance of #334.

Transcript -> `tmp/logs/hyperram-axis-liveness.txt`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

TRANSCRIPT = ROOT / "tmp" / "logs" / "hyperram-axis-liveness.txt"

# `bist status` prints `  CR0     part reports 0xbf2f  latency code 2 fixed  drive 3`.
# Matched against the board's real wording rather than an assumed one -- a regex
# that silently matches nothing is the same failure as an axis that silently
# does nothing.
READBACK = re.compile(r"CR0\s+part reports\s+(0x[0-9a-fA-F]+)")

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


def cr0_after(board, cell):
    """Run a cell, then read CR0 back as the part reports it."""
    board.send(f"bist cell {cell}", 20)
    text = board.send("bist status")
    found = READBACK.search(text)
    if not found:
        raise SystemExit(
            "no device readback in `bist status`. The parse, not the part, is the "
            f"first suspect. Reply was:\n{text}")
    return int(found.group(1), 0)


def decode(value):
    return {name: (value >> shift) & mask for name, (shift, mask) in FIELDS.items()}


def main():
    board = Board()
    try:
        # Each pair differs in ONE field, so a readback that changes can only be
        # that field moving.
        trials = [
            ("drive  CR0[14:12]", "3 dif 0 2 fix", "6 dif 0 2 fix"),
            ("latency CR0[7:4]", "3 dif 0 2 fix", "3 dif 0 15 fix"),
            ("fixed  CR0[3]", "3 dif 0 2 fix", "3 dif 0 2 var"),
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

        print("\nsummary")
        for field, live in verdicts.items():
            print(f"  {field:20s} {'live' if live else 'NOT PROVEN'}")
        print("  CR1[6]  clock mode  NO READBACK EXISTS -- see #334")

        if not all(verdicts.values()):
            print("\nAny sweep row varying a NOT PROVEN field carries no "
                  "information, in either direction.")
    finally:
        board.close()
        print(f"\ntranscript -> {TRANSCRIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
