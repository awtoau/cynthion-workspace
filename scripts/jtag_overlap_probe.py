#!/usr/bin/env python3
#
# Does the deferred SCAN actually return before the clocking finishes?
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks whether double-buffering produces the overlap it is supposed to.

`jtag_fixed_benchmark.py` says the total barely moved after the change. That has
two very different explanations and the benchmark cannot distinguish them:

  1. The SCAN request still blocks until the clocking is done, so no overlap
     happens and the mechanism is broken.
  2. The SCAN returns immediately and the overlap does happen, but the total is
     dominated by something else entirely, so hiding the clocking changes little.

These predict opposite things about one measurable quantity: **how long the SCAN
request itself takes**. If SCAN still costs ~900 us at 1024 bytes, it is blocking.
If it drops to near the ~194 us control-transfer floor, the deferral works and the
time is going somewhere neither the model nor the benchmark is looking.

## What it measures

**SCAN latency, isolated.** A SET_OUT_BUFFER followed by a SCAN, timing only the
SCAN. On the old firmware this includes the clocking; on the new firmware it should
include only the queueing.

**The pipelined pair.** SET_OUT_BUFFER + SCAN repeatedly, timing the pair, which
is what a configure actually does. Compared against the sum of the two measured
separately: if the pair costs less than the sum, the overlap is real and the
difference is what it saves.

Neither asserts ISC_ENABLE and both leave the TAP in SHIFT-DR, so the FPGA's
configuration is untouched -- the same argument as jtag_fixed_benchmark.py.

    ./scripts/jtag_overlap_probe.py
    ./scripts/jtag_overlap_probe.py --chunk 1024 --iterations 200
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_overlap_probe.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

REQUEST_JTAG_SET_OUT_BUFFER = 0xb1
REQUEST_JTAG_SCAN = 0xb3
REQUEST_JTAG_GET_STATE = 0xb6

FLAG_DISCARD_TDO = 0b100


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunk", type=int, default=1024)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()

    payload = bytes(i & 0xFF for i in range(args.chunk))
    bits = args.chunk * 8

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        alt = 0
        try:
            info = bytes(debugger.device.ctrl_transfer(0xC0, 0xb8, 0, 0, 16))
            if len(info) >= 16:
                alt = int.from_bytes(info[12:16], "little")
        except Exception:
            pass

        emit(f"firmware: {debugger.get_firmware_version()}")
        emit(f"second transmit buffer: "
             f"{alt if alt else 'none -- this firmware does not pipeline'}")
        emit(f"chunk {args.chunk} B, {args.iterations} iterations")
        emit()

        with debugger.jtag:
            debugger.jtag.move_to_state('DRSHIFT')

            # The per-request floor, so the SCAN figure can be read against it.
            floor = []
            for _ in range(args.iterations):
                started = time.perf_counter()
                debugger.in_request(REQUEST_JTAG_GET_STATE, length=1)
                floor.append((time.perf_counter() - started) * 1e6)

            stage = []
            scan = []
            for _ in range(args.iterations):
                started = time.perf_counter()
                debugger.out_request(REQUEST_JTAG_SET_OUT_BUFFER, data=payload)
                mid = time.perf_counter()
                debugger.out_request(REQUEST_JTAG_SCAN, value=bits,
                                     index=FLAG_DISCARD_TDO)
                done = time.perf_counter()
                stage.append((mid - started) * 1e6)
                scan.append((done - mid) * 1e6)

        f_med = statistics.median(floor)
        s_med = statistics.median(stage)
        c_med = statistics.median(scan)

        emit(f"  control-transfer floor   {f_med:>8.1f} us  (median)")
        emit(f"  SET_OUT_BUFFER           {s_med:>8.1f} us")
        emit(f"  SCAN                     {c_med:>8.1f} us")
        emit()

        # 1024 bytes at 12 MHz SCK is about 683 us of pure clocking. A SCAN that
        # still contains the clocking cannot be much below that; one that only
        # queues cannot be much above the floor.
        clocking_us = args.chunk * 8 / 12e6 * 1e6
        emit(f"  clocking {args.chunk} B at 12 MHz SCK would be "
             f"{clocking_us:.0f} us")
        emit()

        if c_med < f_med * 1.6:
            emit("  SCAN is at the control-transfer floor: it returns without")
            emit("  clocking, so the deferral IS working. Any missing speedup is")
            emit("  therefore not a broken overlap -- the time is elsewhere.")
        elif c_med > clocking_us * 0.7:
            emit("  SCAN still costs about the clocking time: it is NOT being")
            emit("  deferred, and the overlap mechanism is not engaging.")
        else:
            emit("  SCAN sits between the floor and the clocking cost, so the")
            emit("  deferral is partial -- something is draining it early.")

        emit()
        emit(f"  pair total {s_med + c_med:>8.1f} us per chunk")
        emit(f"  over {-(-122880 // args.chunk)} chunks that is "
             f"{(s_med + c_med) * -(-122880 // args.chunk) / 1000:.1f} ms, "
             f"against the benchmark's measured total")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
