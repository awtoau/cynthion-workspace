#!/usr/bin/env python3
#
# Every tuning knob the part and the PHY offer, in combination. See #186, #188.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps capture phase against the part's own CR0 tuning, looking for a setting
where BURSTDET is SET and the error count is zero at the same time.

    ./scripts/hyperram_tune.py --ck 180
    ./scripts/hyperram_tune.py --ck 140 180 --drive 0 1 2 3

## Why one test rather than one sweep per knob

Sweeping a single axis cannot find a corner. Measured at CK 140: capture phase 0
is the ONLY phase that latches BURSTDET, and it fails in bulk; phase 2 reads
4,156,631 words with zero errors and BURSTDET clear. So the configuration that
passes is not using the strobe -- data is arriving by latency count, which the
ceiling harness's own docstring says demonstrates nothing about DQS -- and the
configuration that uses the strobe does not read correctly.

Those two facts cannot be reconciled by moving the phase alone. They are a
window in the wrong place, and where the window sits depends on the DEVICE's
drive strength and latency as much as on the FPGA's capture point.

## What is swept, and what it costs

Runtime, no rebuild -- so these are the inner loops:

  * **capture phase**, `REG_READCLKSEL`, 0-7
  * **CR0[14:12] drive strength**, seven settings from 19 to 115 ohms. Default is
    34 ohms and nothing here has ever moved it. It is the classic fix for signal
    integrity at speed.
  * **CR0[7:4] initial latency**, 3 to 7 clocks. 7 is the power-on default and is
    rated to 200 MHz; a lower count is legal below that and shortens every
    transaction.

Build time, so an outer loop and expensive:

  * **CK**, via the PLL
  * the controller's `HIGH_LATENCY_CLOCKS`

`hyperram_regfuzz.py` proved the CR0 write path works on this board, which is why
the device-side knobs are reachable at runtime at all.

## Two stages: screen on 256 bytes, confirm on millions

A combination that cannot move 256 bytes will not move 300,000 words, so
spending a long run on it is wasted. Every combination gets ONE burst first --
128 words, 256 bytes, a few microseconds -- and only survivors get the full run
with the negative control and the BURSTDET check.

The measured spread says how much this saves. At CK 180, six of eight capture
phases fail with 87% or more of words wrong; those die on the first burst. One
stalls outright and moves zero words, which the screen catches immediately. Only
phase 3, at 0.78%, would reach the second stage -- and 0.78% is sparse enough
that a 128-word screen may pass it by chance, which is exactly why the screen
REJECTS rather than accepts: surviving it means "not obviously broken", not
"good".

That asymmetry matters. A screen that accepted would let an intermittent
combination through as a pass; one that only rejects cannot, because the
expensive stage still has to agree.

## The output is a map, not a list

A failing combination says nothing about its neighbours, but the SET of results
has structure, and only a 2-D map shows it. This prints a shmoo: one axis the
capture phase, the other the clock (or the drive strength), each cell the error
rate by magnitude.

    CK/phase   0   1   2   3   4   5   6   7
       140     B   5   .   6   .   6   4   6
       180     B   6   6   4   X   6   6   6

    .  zero errors      4..7  log10 of the error count      B  BURSTDET set
    X  stalled, no words moved

Read across and the window is visible: at CK 140 it is centred on phase 2, at
CK 180 the only near-miss is phase 3. **The window MOVES with frequency**, which
is exactly what a list of verdicts hides and what makes a fixed capture phase
the wrong way to run a ladder -- the CK 180 rung failed at phase 2 because phase
2 stopped being right somewhere below it, not because the part gave up.

A map also shows what a single point cannot: whether a passing setting sits in
the MIDDLE of a window or on its edge. A pass on the edge is a pass that will
fail on another board, at another temperature, or after a rebuild places the
design differently.

## What counts as a result

**BURSTDET set, zero errors, and a live negative control, together.** Any two of
those without the third has already been measured and already misled:

  * zero errors with BURSTDET clear -- reading by latency count, not by strobe
  * zero errors with a dead control -- the comparator was not discriminating
  * BURSTDET set with bulk errors -- the strobe is found, the data is not

The table this prints puts all three in one row so a partial success cannot be
read as a whole one.

## Not swept here

CR1[6], which selects differential clocking. `docs/chips/hyperram/w956a8.md`
records that the FPGA has been driving CK# into a part configured to ignore it,
and that switching is untried. It belongs in this sweep eventually, but a CR1
write that lands wrong changes how the part clocks EVERYTHING, and that wants
its own bring-up rather than being one column of a matrix.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

from devlog import emit, log  # noqa: E402

from hyperram.hyperram_ceiling_top import (  # noqa: E402
    APPLET_ID, REG_ID, REG_SWEEP_ADDR, REG_SWEEP_DATA, REG_SWEEP_DONE,
    REG_CTRL_STATE, REG_FSM_STATE, REG_SWEEP_GO, REG_SWEEP_MASK, REG_SWEEP_PASSES, ROW_BURSTDET,
    ROW_ERROR_BITS, ROW_SKIPPED, ROW_STALLED, SWEEP_CELLS)

# CR0 fields, from docs/chips/hyperram/w956a8.md. The power-on value is 0x8f2f.
CR0_DEFAULT = 0x8F2F
CR0_DRIVE_SHIFT = 12
CR0_DRIVE_MASK = 0b111
CR0_LATENCY_SHIFT = 4
CR0_LATENCY_MASK = 0b1111

# CR0[14:12] to output impedance, from datasheet rev A01-006 section 9.4.5.
# Eight codes, seven distinct values -- 000 and 100 are both 34 ohms, which is
# why the default is described as the mid-point of seven options.
DRIVE_OHMS = {0b000: 34, 0b001: 115, 0b010: 67, 0b011: 46,
              0b100: 34, 0b101: 27, 0b110: 22, 0b111: 19}

# Initial-latency codes to clocks. Table 10; the part doubles this when CR0[3]
# selects fixed latency, which it does by default.
LATENCY_CLOCKS = {0b0000: 5, 0b0001: 6, 0b0010: 7, 0b1110: 3, 0b1111: 4}


def cr0_with(*, drive=None, latency_code=None):
    """CR0 with one or both fields replaced, everything else left alone."""
    value = CR0_DEFAULT
    if drive is not None:
        value &= ~(CR0_DRIVE_MASK << CR0_DRIVE_SHIFT)
        value |= (drive & CR0_DRIVE_MASK) << CR0_DRIVE_SHIFT
    if latency_code is not None:
        value &= ~(CR0_LATENCY_MASK << CR0_LATENCY_SHIFT)
        value |= (latency_code & CR0_LATENCY_MASK) << CR0_LATENCY_SHIFT
    return value


def cell(result):
    """One shmoo cell: what this combination did, in one character.

    Magnitude rather than pass/fail, because "4" next to "." is a window edge
    and "6" next to "6" is a wall, and a verdict column cannot tell them apart.
    """
    if result is None:
        return " "
    # `words` ABSENT is not the same as `words` zero. Defaulting it to 0 made
    # every cell read "stalled" and the whole map useless -- a missing key means
    # not measured, which is a blank.
    if result.get("stalled") or result.get("words") == 0:
        return "X"
    errors = result.get("errors", 0)
    if errors == 0:
        return "B" if result.get("burstdet") else "."
    magnitude = len(str(errors)) - 1
    return str(min(magnitude, 9))


def shmoo(results, *, rows, columns, row_label, emit=emit):
    """Print the map. ``results`` is {(row, column): result-dict}."""
    emit("")
    emit(f"  {row_label:>8}  " + "  ".join(f"{c}" for c in columns))
    for row in rows:
        cells = "  ".join(cell(results.get((row, column))) for column in columns)
        emit(f"  {row:>8}  {cells}")
    emit("")
    emit("  .  zero errors, BURSTDET clear      B  zero errors, BURSTDET SET")
    emit("  4..9  log10 of the error count      X  stalled, no words moved")
    emit("")
    emit("  A window that MOVES between rows means a fixed capture phase cannot")
    emit("  ladder the clock -- which is how CK 180 came to be recorded as a")
    emit("  device limit when phase 2 had simply stopped being the right one.")


def read_table(registers, cells):
    """The whole results table, one row per cell, in one JTAG session."""
    rows = []
    for index in range(cells):
        registers.register_write(REG_SWEEP_ADDR, index)
        rows.append(registers.register_read(REG_SWEEP_DATA))
    return rows


def decode(row):
    """One BRAM row into the dict the shmoo renderer wants."""
    return {
        "errors": row & ((1 << ROW_ERROR_BITS) - 1),
        "burstdet": bool(row & ROW_BURSTDET),
        "stalled": bool(row & ROW_STALLED),
        "skipped": bool(row & ROW_SKIPPED),
        "words": 0 if row & ROW_STALLED else 1,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--ck", type=float, default=180.0,
                        help="device CK, MHz -- must match a built bitstream")
    parser.add_argument("--passes", type=int, default=1,
                        help="passes per cell. 1 is the 256-byte screen; raise "
                             "it to soak the cells that survive")
    parser.add_argument("--phases", type=lambda v: int(v, 0), default=0xFF,
                        help="bitmask of capture phases to run")
    parser.add_argument("--drives", type=lambda v: int(v, 0), default=0xFF,
                        help="bitmask of CR0 drive codes to run")
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger                      # noqa: E402

    log(f"CK {args.ck:g}, {args.passes} pass(es) per cell, "
        f"phases {args.phases:#04x}, drives {args.drives:#04x}")

    debugger = ApolloDebugger()
    registers = debugger.registers

    applet = registers.register_read(REG_ID)
    if applet != APPLET_ID:
        emit(f"  applet {applet:#010x}, expected {APPLET_ID:#010x} -- configure "
             f"the ceiling bitstream for this CK first")
        return 1

    registers.register_write(REG_SWEEP_PASSES, args.passes)
    registers.register_write(REG_SWEEP_MASK, (args.drives << 8) | args.phases)
    # Zero first: the gateware starts on a RISING EDGE of this bit, so leaving
    # it set from a previous run means the next GO does nothing and the host
    # reads back the old table -- which it did, silently, and a soak returned
    # its own screen.
    registers.register_write(REG_SWEEP_GO, 0)
    registers.register_write(REG_SWEEP_GO, 1)

    # The gateware walks every cell itself. One pass is 128 device words, so the
    # whole matrix is microseconds of transfer -- the poll is here for the soak
    # case, where a raised pass count makes it genuinely long.
    # WATCH IT PROGRESS, do not just wait for done.
    #
    # This used to spin 20,000 blind reads on the done bit. When a CR0 write put
    # the part into deep power down the sweep stalled at cell 0 and the host sat
    # there for eleven minutes learning nothing -- while the very register it was
    # polling carries the cell index next to the done bit, so "stuck at cell 0"
    # was one decode away the whole time.
    previous_cell, stuck = -1, 0
    for _ in range(20_000):
        status = registers.register_read(REG_SWEEP_DONE)
        if status & 1:
            break
        cell = (status >> 1) & 0x7F
        if cell == previous_cell:
            stuck += 1
            if stuck == 200:
                emit(f"  STALLED at cell {cell} (drive {cell // 8}, "
                     f"phase {cell % 8}) -- it has not advanced in 200 polls")
                emit(f"  engine FSM state "
                     f"{registers.register_read(REG_FSM_STATE)} "
                     f"-- see the state order in hyperram_ceiling_top.py")
                emit(f"  applet id reads {registers.register_read(REG_ID):#010x}, "
                     f"want {APPLET_ID:#010x}")
                emit("  a cell that never finishes usually means the part stopped")
                emit("  answering: check the CR0 value being written, since bit 15")
                emit("  is deep power down and a zero there puts it to sleep")
                return 1
        else:
            previous_cell, stuck = cell, 0
    else:
        emit(f"  the sweep never reported done; last cell {previous_cell}")
        return 1

    rows = [decode(row) for row in read_table(registers, SWEEP_CELLS)]
    results = {(cell // 8, cell % 8): row for cell, row in enumerate(rows)}

    shmoo(results, rows=list(range(8)), columns=list(range(8)),
          row_label="drive/phase")

    emit("  candidates, best first:")
    ranked = sorted((k for k in results if not results[k]["skipped"]),
                    key=lambda k: (results[k]["errors"],
                                   0 if results[k]["burstdet"] else 1))
    for drive, phase in ranked[:8]:
        row = results[(drive, phase)]
        emit(f"    drive {drive} ({DRIVE_OHMS[drive]:>3} ohm)  phase {phase}  "
             f"errors {row['errors']:>9}  "
             f"BURSTDET {'SET' if row['burstdet'] else '-'}"
             f"{'  STALLED' if row['stalled'] else ''}")

    both = [k for k in ranked
            if results[k]["errors"] == 0 and results[k]["burstdet"]]
    emit("")
    if both:
        drive, phase = both[0]
        emit(f"  drive {drive}, phase {phase}: zero errors AND BURSTDET set -- "
             f"the first configuration where the strobe found the data")
    else:
        emit("  No cell has zero errors AND BURSTDET set. Every clean cell is")
        emit("  reading by latency count with the strobe undetected, which is")
        emit("  what every passing measurement in this project has been doing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
