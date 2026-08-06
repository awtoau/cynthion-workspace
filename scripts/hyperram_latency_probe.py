#!/usr/bin/env python3
#
# Which controller latency completes a transaction, and which the model refuses.
# See #186.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps `HIGH_LATENCY_CLOCKS` against the W956A8 model and reports what lands.

    ./scripts/hyperram_latency_probe.py

## Why this exists separately from the simulation

`soc_hyperram_sim.py` asks whether ONE configuration behaves. This asks which
configurations exist at all, which is a different question and the one open
right now: the model says the as-built `HIGH_LATENCY_CLOCKS = 4` completes
nothing, and silicon ran 4 earlier today with 36,153 BURSTDET and real register
values read back. One of the two is wrong.

A sweep answers it in the only way that can be trusted here. If the model
accepts exactly one value, and that value is not 4, then the model's device
latency is tied to the controller's count rather than to the part -- an artifact,
because a real device does not change its latency to match whatever is asking.
If it accepts a RANGE and 4 is inside it, the model is behaving like a part with
a capture window, and the as-built failure means something else.

## The known-good anchor

**Latency 4 demonstrably worked on the board.** Any model that cannot complete a
transaction at 4 is the thing that is wrong, not the design. That is the control
this probe is read against, and it is why the output says which values the MODEL
accepts rather than which values are correct.

`gateware/probes/hyperram/hyperram_ceiling_top.py` is the instrument for the opposite
question -- what the part does at speed, with no CPU in the loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from devlog import emit, log  # noqa: E402

import soc_hyperram_sim as sim  # noqa: E402
from bootram import HYPERRAM_LATENCY_CLOCKS  # noqa: E402

# What the board is built with, and what the ceiling harness ran at.
BOARD_SYNC_MHZ = 60.0

# Wide enough to bracket luna's 5 and our 4 on both sides. Beyond about 8 the
# transaction is longer than the harness's own patience bound.
LATENCIES = range(1, 9)


def probe(latency, *, sync_mhz, model_latency=None):
    """Run one write at `latency`; return (landed, commands decoded)."""

    async def one_write(ctx, dut, model):
        await sim.run(ctx, dut, model, address=sim.TEST_ADDRESS, read=False,
                      data=sim.TEST_DATA)

    try:
        model = sim.simulate(one_write, sync_mhz=sync_mhz, latency=latency,
                             model_latency=model_latency)
    except Exception as failure:                       # noqa: BLE001
        return (False, 0, f"{type(failure).__name__}: {failure}")
    landed = dict(model.written).get(sim.TEST_ADDRESS) == sim.TEST_DATA
    return (landed, len(model.commands), "")


def main():
    log(f"sweeping HIGH_LATENCY_CLOCKS at sync {BOARD_SYNC_MHZ:g} MHz")
    emit(f"  the model's own latency is {sim.latency_beats()} beats, taken from "
         f"luna's class constant")
    emit(f"  CR0 says the device takes {sim.DEVICE_FIXED_LATENCY_CK} CK "
         f"= {sim.DEVICE_FIXED_LATENCY_CK // sim.DQS_CK_PER_SYNC} beats at 4:1")
    emit("")
    emit("  latency  lands  commands  note")

    device_beats = sim.DEVICE_FIXED_LATENCY_CK // sim.DQS_CK_PER_SYNC

    accepted = []
    for latency in LATENCIES:
        landed, commands, note = probe(latency, sync_mhz=BOARD_SYNC_MHZ)
        if landed:
            accepted.append(latency)
        mark = "  <-- as built" if latency == HYPERRAM_LATENCY_CLOCKS else ""
        emit(f"  {latency:>7}  {str(landed):>5}  {commands:>8}  {note}{mark}")

    emit("")
    if not accepted:
        emit("  the model completes NO transaction at any latency, so it is not")
        emit("  measuring the design at all -- fix the harness before reading it")
        return 1

    emit(f"  model accepts: {accepted}")
    if HYPERRAM_LATENCY_CLOCKS in accepted:
        emit(f"  {HYPERRAM_LATENCY_CLOCKS} is among them, so the as-built failure "
             f"in section 9b is NOT the latency")
    else:
        emit(f"  {HYPERRAM_LATENCY_CLOCKS} is NOT among them -- but it ran on the "
             f"board, so the model's device")
        emit(f"  latency follows the controller instead of the part. That is the")
        emit(f"  artifact to fix before any latency conclusion is drawn. #186")
    # And again with the model taking the PART's latency instead of the
    # controller's, which is the whole point of separating the two.
    emit("")
    emit(f"  with the model at the DEVICE's {device_beats} beats "
         f"({sim.DEVICE_FIXED_LATENCY_CK} CK from CR0):")
    device_accepted = [n for n in LATENCIES
                       if probe(n, sync_mhz=BOARD_SYNC_MHZ,
                                model_latency=device_beats)[0]]
    emit(f"  model accepts: {device_accepted}")
    if HYPERRAM_LATENCY_CLOCKS in device_accepted:
        emit(f"  {HYPERRAM_LATENCY_CLOCKS} IS accepted against the real device "
             f"latency -- the as-built")
        emit(f"  failure is then a harness artifact and not the design")
    else:
        emit(f"  {HYPERRAM_LATENCY_CLOCKS} is still rejected. Either the CA phase "
             f"contributes cycles this")
        emit(f"  arithmetic ignores, or the board's success at 4 was the "
             f"displacement itself")
        emit(f"  rather than a correct read. Both are testable; neither is "
             f"assumed here.")

    if len(accepted) == 1:
        emit("  exactly ONE value accepted: a real part has a capture window, so a")
        emit("  model this sharp is describing itself rather than the device")
    return 0


if __name__ == "__main__":
    sys.exit(main())
