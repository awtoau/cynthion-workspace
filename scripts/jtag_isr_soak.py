#!/usr/bin/env python3
#
# Soak the JTAG shift path with alternating chunk sizes.
# SPDX-License-Identifier: BSD-3-Clause

"""
Repeats the fixed-payload shift many times, alternating the chunk size each run.

## Why alternating sizes

A first version of the USB-interrupt fast path passed three consecutive
`jtag_fixed_benchmark.py` runs and then failed 14 of 30 runs here. The difference is
that this alternates 512 and 1024 bytes.

That is not arbitrary. The firmware picks the staging buffer by request size -- a
chunk too large for the 512-byte alternate buffer is placed in the 1024-byte primary
one instead -- so which buffer is in use depends on the sizes of *previous* requests.
A soak that uses one size never changes that state and cannot see the whole class of
fault that depends on it. Any predicate that has to reason about which buffer a
request will land in is only exercised by varying the size.

## What it does to the board

Nothing persistent, for the same reason `jtag_fixed_benchmark.py` does not: the scan
runs with the TAP left in SHIFT-DR and no ISC_ENABLE, so the FPGA ignores the data
and its configuration is untouched. Safe to repeat against a board doing something
else.

## Reading the output

A pipe error is the device stalling a control request, which on this path means the
firmware refused something the host had every right to send. One failure in fifty is
a defect, not noise -- report it rather than re-running until it passes.

    ./scripts/jtag_isr_soak.py
    ./scripts/jtag_isr_soak.py --runs 60 --chunks 256 512 1024
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_isr_soak.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "scripts"))

from jtag_fixed_benchmark import PAYLOAD_BYTES, make_payload


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--chunks", type=int, nargs="+", default=[1024, 512],
                        help="cycled in order, one per run")
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()
    payload = make_payload(PAYLOAD_BYTES)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit(f"firmware: {debugger.get_firmware_version()}")
        emit(f"payload:  {PAYLOAD_BYTES} bytes")
        emit(f"runs:     {args.runs}, cycling chunk sizes {args.chunks}")
        emit()

        results = {chunk: [] for chunk in args.chunks}
        failures = []
        for index in range(args.runs):
            chunk = args.chunks[index % len(args.chunks)]
            started = time.perf_counter()
            try:
                with debugger.jtag as jtag:
                    jtag.max_bits_per_scan = chunk * 8
                    jtag.shift_data(tdi=payload, length=len(payload) * 8,
                                    ignore_response=True)
            except Exception as error:
                failures.append((index, chunk, repr(error)))
                emit(f"  run {index:>3}  {chunk:>4} B  FAILED  {error!r}")
                continue
            results[chunk].append((time.perf_counter() - started) * 1000)

        emit()
        total_ok = sum(len(v) for v in results.values())
        emit(f"  {total_ok} of {args.runs} runs completed, "
             f"{len(failures)} failed")
        for chunk in args.chunks:
            times = results[chunk]
            if not times:
                emit(f"  {chunk:>4} B: no successful runs")
                continue
            emit(f"  {chunk:>4} B: n={len(times):<3} "
                 f"best {min(times):>7.1f}  "
                 f"median {statistics.median(times):>7.1f}  "
                 f"worst {max(times):>7.1f} ms")

        emit()
        if failures:
            emit("  FAILURES PRESENT. A stall on this path means the firmware")
            emit("  refused a request the host was entitled to send; it is a")
            emit("  defect regardless of how many runs passed around it.")
        else:
            emit("  No failures. Note that a clean soak at one chunk size proves")
            emit("  much less than a clean soak across sizes -- see the module")
            emit("  docstring for why.")
        emit()
        emit(f"log: {LOG}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
