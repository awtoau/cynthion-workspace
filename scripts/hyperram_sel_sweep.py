#!/usr/bin/env python3
#
# Sweep every READCLKSEL tap and read-phase combination against the window's
# 64-byte line check. See #186.
# SPDX-License-Identifier: BSD-3-Clause

"""
Which capture setting, if any, reads a cache line back correctly?

    ./scripts/hyperram_sel_sweep.py

`hr sel` packs three things into one CSR -- bits 2:0 the DQSBUFM READCLKSEL tap,
bit 3 the read-phase half-cycle shift, bits 5:4 the clock-stop read delay -- and
the sweeps run so far only ever moved bits 2:0. This walks taps 0-7 against
phase 0 and 1, sixteen settings, and reports the line check for each.

## What the two knobs actually do, and why neither may be enough

READCLKSEL moves the capture point WITHIN a cycle. Read-phase moves the DQSBUFM
read window by half a `sync` cycle. Neither moves the boundary at which four
captured byte-phases are packed into a 32-bit word -- that is a BITSLIP, and
`IDDRX2DQA` feeds `phy.dq.i` directly with no slip stage in between.

LiteDRAM's ECP5 PHY puts `BitSlip(4)` in exactly that position and drives it
from its own CSR, separate from the delay taps, because the two correct
different faults. So the expected result here is that NO setting reads 16/16:
a uniform whole-word displacement is not reachable from either knob.

That expectation is the point. If some setting does reach 16/16 then the bitslip
is unnecessary, and this script is what says so before the gateware is written.

## Reading the output

`good` is out of 16 32-bit beats. `want`/`got` are the first mismatching beat.
A CLEAN displacement shows `got` holding a neighbour's value -- the pattern is
`0x1000_0000 + i*0x0101_0101 + 0x0f0e_0d0c`, so beat i and beat i+1 differ by a
constant and a shifted `got` is recognisable by eye. CORRUPT output looks like
neither, and means the setting broke capture rather than moved it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

SHELL = ROOT / "scripts" / "soc_shell.py"

# bits 2:0 tap, bit 3 phase. Bits 5:4 (clock-stop read delay) stay 0: #185
# measured them as having no effect on returned data, and holding them fixed
# keeps this sweep one variable wide.
TAPS = range(8)
PHASES = (0, 1)

LINE = re.compile(
    r"line write:\s*(\d+)/16 correct, bad ([01]+) want ([0-9a-f]+) got ([0-9a-f]+)"
)


def measure(sel):
    """Set the capture register, run the line check, return (good, want, got)."""
    done = subprocess.run(
        [sys.executable, str(SHELL), f"hr sel {sel:02x}", "hr cross"],
        capture_output=True, text=True,
    )
    found = LINE.search(done.stdout)
    if not found:
        return None
    return (int(found.group(1)), found.group(3), found.group(4))


def main():
    log(f"sweeping {len(TAPS) * len(PHASES)} capture settings against the line check")
    emit("  sel  tap  phase   good  want      got")

    best = []
    for phase in PHASES:
        for tap in TAPS:
            sel = tap | (phase << 3)
            result = measure(sel)
            if result is None:
                emit(f"  {sel:02x}   {tap}      {phase}     -- no reply")
                continue
            good, want, got = result
            emit(f"  {sel:02x}   {tap}      {phase}    {good:2}/16  {want}  {got}")
            best.append((good, sel))

    if not best:
        emit("no setting replied -- is the DQS gateware on the board?")
        return 1

    best.sort(reverse=True)
    top, sel = best[0]
    emit("")
    if top == 16:
        emit(f"  sel {sel:02x} reads the line CORRECTLY -- no bitslip needed")
    else:
        emit(f"  best is sel {sel:02x} at {top}/16. No capture setting fixes it,")
        emit("  which is what a whole-word displacement predicts: the boundary")
        emit("  where byte-phases pack into a 32-bit word is not one of these knobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
