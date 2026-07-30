#!/usr/bin/env python3
#
# Split the JTAG chunk cost into its USB and SERCOM halves.
# SPDX-License-Identifier: BSD-3-Clause

"""
Attributes the per-chunk cost of the JTAG write path to USB or to clocking.

`jtag_fixed_benchmark.py` gives one number for the whole path. That number cannot
say whether an optimisation should target the USB side or the SERCOM side, and the
two are strictly serialised today, so the total is just their sum. This script
measures each half in isolation against the same board.

## The three measurements

**SET_OUT_BUFFER alone.** A `SET_OUT_BUFFER` with no `SCAN` after it moves the
bytes across USB and writes them into the firmware's buffer, then completes. No
clocking happens. Repeated N times this is the pure cost of staging a chunk.

**SCAN alone.** A `SCAN` with no `SET_OUT_BUFFER` before it clocks whatever the
buffer already holds. The setup packet is tiny, so nearly all of the time is
SERCOM. Repeated N times this is the pure cost of clocking a chunk, plus one small
control transfer of overhead per iteration.

**Zero-length control transfer.** A request that does nothing measurable
(`GET_STATE`, an IN of one byte) establishes the fixed per-control-transfer floor,
which must be subtracted from the SCAN figure to get the clocking cost alone.

Both of the first two leave the TAP wherever it was and neither asserts
ISC_ENABLE, so the FPGA's configuration is untouched -- the same safety argument
as jtag_fixed_benchmark.py.

## Why this matters for double-buffering

Overlapping USB with clocking can save at most the smaller of the two. If USB is
320 ms of a 458 ms total and clocking is 137 ms, perfect overlap yields 320 ms and
the 137 ms is the prize. If instead the split is 430/28, there is almost nothing
to win and the work should not be done.

    ./scripts/jtag_cost_split.py
    ./scripts/jtag_cost_split.py --chunk 512 --iterations 240
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_cost_split.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

REQUEST_JTAG_SET_OUT_BUFFER = 0xb1
REQUEST_JTAG_SCAN = 0xb3
REQUEST_JTAG_GET_STATE = 0xb6

# Bit 2 of SCAN's wIndex: the host does not want TDO, so the firmware discards it
# rather than storing it. Set here because this measures the write path, which is
# the bulk case in a configure.
FLAG_DISCARD_TDO = 0b100


def timed(fn, iterations):
    """Median-of-3 milliseconds for `iterations` calls of fn.

    Median rather than mean: USB scheduling adds latency and never removes it, so
    an outlier is always upward and the median rejects it.
    """
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        for _ in range(iterations):
            fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), min(samples)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunk", type=int, default=1024)
    parser.add_argument("--iterations", type=int, default=120)
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

        emit(f"firmware: {debugger.get_firmware_version()}")
        emit(f"chunk {args.chunk} B, {args.iterations} iterations per sample")
        emit()

        with debugger.jtag:
            # Park in SHIFT-DR so a scan shifts data rather than walking the TAP
            # into a state where the clocking cost would differ.
            debugger.jtag.move_to_state('DRSHIFT')

            floor_med, floor_min = timed(
                lambda: debugger.in_request(REQUEST_JTAG_GET_STATE, length=1),
                args.iterations)

            stage_med, stage_min = timed(
                lambda: debugger.out_request(
                    REQUEST_JTAG_SET_OUT_BUFFER, data=payload),
                args.iterations)

            # A bare repeated SCAN is not measurable on double-buffered firmware:
            # the chunk size alternates between the two buffers, so a 1024-bit scan
            # against the 512-byte buffer is refused and the request stalls. Sizing
            # every scan to the SMALLER buffer keeps it legal against either, which
            # costs a factor of two in bytes clocked but measures the same per-byte
            # rate.
            alt = 0
            try:
                info = bytes(debugger.device.ctrl_transfer(0xC0, 0xb8, 0, 0, 16))
                if len(info) >= 16:
                    alt = int.from_bytes(info[12:16], "little")
            except Exception:
                pass

            scan_bytes = min(args.chunk, alt) if alt else args.chunk
            scan_bits = scan_bytes * 8
            scan_med, scan_min = timed(
                lambda: debugger.out_request(
                    REQUEST_JTAG_SCAN, value=scan_bits, index=FLAG_DISCARD_TDO),
                args.iterations)
            # Scale back up to the requested chunk, so the figure is comparable with
            # the staging cost measured at the full chunk size.
            if scan_bytes != args.chunk:
                scan_min *= args.chunk / scan_bytes
                scan_med *= args.chunk / scan_bytes
                emit(f"  (SCAN measured at {scan_bytes} B and scaled to "
                     f"{args.chunk} B; the two firmware buffers differ in size)")

            both_med, both_min = timed(
                lambda: (debugger.out_request(
                            REQUEST_JTAG_SET_OUT_BUFFER, data=payload),
                         debugger.out_request(
                            REQUEST_JTAG_SCAN, value=bits,
                            index=FLAG_DISCARD_TDO)),
                args.iterations)

        n = args.iterations
        emit(f"  control-transfer floor   {floor_min:>7.1f} ms  "
             f"{floor_min / n * 1000:>6.1f} us each")
        emit(f"  SET_OUT_BUFFER only      {stage_min:>7.1f} ms  "
             f"{stage_min / n * 1000:>6.1f} us each")
        emit(f"  SCAN only                {scan_min:>7.1f} ms  "
             f"{scan_min / n * 1000:>6.1f} us each")
        emit(f"  both, serialised         {both_min:>7.1f} ms  "
             f"{both_min / n * 1000:>6.1f} us each")
        emit()

        clocking = scan_min - floor_min
        emit(f"  clocking alone (SCAN minus floor)      {clocking:>7.1f} ms")
        emit(f"  staging alone (SET_OUT_BUFFER)         {stage_min:>7.1f} ms")
        emit()
        emit(f"  perfect overlap would cost the larger of the two: "
             f"{max(stage_min, scan_min):>7.1f} ms")
        emit(f"  against {both_min:.1f} ms serialised, so the ceiling on the "
             f"win is {both_min - max(stage_min, scan_min):.1f} ms "
             f"({100 * (both_min - max(stage_min, scan_min)) / both_min:.0f}%)")
        emit()
        emit("  Overlap can only hide the smaller half behind the larger. If")
        emit("  staging dominates, double-buffering buys little and the cost is")
        emit("  in USB itself.")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
