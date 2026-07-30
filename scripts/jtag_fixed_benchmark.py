#!/usr/bin/env python3
#
# A fixed-payload JTAG SRAM benchmark, comparable across sessions.
# See awtoau/cynthion-workspace#100.
# SPDX-License-Identifier: BSD-3-Clause

"""
Times the JTAG SRAM configuration path against a payload of constant size.

Every configure measurement in this project so far has used a real bitstream, and
that makes the numbers quietly incomparable: `top.bit` changes size whenever the
gateware changes, and the available bitstreams here range from 103364 to 136184
bytes. A later "faster" result can therefore be a smaller bitstream, not a faster
path.

That is not hypothetical. A 1680 ms figure was compared against 890 ms and reported
as 1.89x, when the two came from different bitstreams; the same-payload numbers were
846 and 774 ms. This script exists so that cannot happen again.

## What it measures

The **shift path only**, not a full configure. It sends a fixed number of bytes
through `SET_OUT_BUFFER` + `SCAN` exactly as `configure()` does, without the ECP5
command sequence around it, so the result is attributable to the transport rather
than to anything the FPGA does with the data.

Consequences worth stating:

  * **Nothing is programmed.** The scan happens with the TAP left in SHIFT-DR and
    no ISC_ENABLE, so the FPGA ignores the data entirely. Its current
    configuration is undisturbed, which means this is safe to run repeatedly
    against a board that is doing something else.
  * The absolute number is therefore **lower** than a real configure, which adds
    the ECP5 preamble, ISC_ENABLE, the CRC check and DONE polling. It is a
    transport benchmark, not a configure-time prediction.

## Why a fixed payload rather than a real bitstream

A synthetic payload is a constant. `PAYLOAD_BYTES` below is committed, so two runs
months apart compare the same work. Using a real bitstream means every gateware
change silently moves the baseline.

    ./scripts/jtag_fixed_benchmark.py
    ./scripts/jtag_fixed_benchmark.py --chunks 128 256 512 --runs 5
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_fixed_benchmark.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

# The fixed payload size, in bytes. Committed deliberately: this is the constant
# that makes results comparable, so changing it invalidates every prior number and
# should be a considered decision rather than a convenience.
#
# 122880 = 120 KiB, chosen to sit in the middle of the real bitstreams here
# (103364 to 136184 bytes) and to divide evenly by 128, 256, 512 and 1024 so no
# chunk size gets a short final transfer that the others do not.
PAYLOAD_BYTES = 122880


def make_payload(size):
    """An incrementing byte pattern.

    Counting rather than constant or random: if this is ever extended to verify
    TDO, a dropped byte shows as a gap and a corrupted one as a wrong value at a
    known position, where a repeating pattern resynchronises and hides the loss.
    The same reasoning as ecp5-test/adv_speed uses for the sideband.
    """
    return bytes(i & 0xFF for i in range(size))


def time_shift(debugger, payload, chunk_bytes):
    """Shift the payload in chunks. Returns milliseconds."""
    started = time.perf_counter()
    with debugger.jtag as jtag:
        # Clamp after jtag_init() -- it negotiates via GET_INFO on entry and
        # would otherwise overwrite this.
        jtag.max_bits_per_scan = chunk_bytes * 8
        # ignore_response: the ECP5 self-validates configuration by CRC, so TDO
        # is not the integrity mechanism here. Reading it back costs a
        # GET_IN_BUFFER control transfer per chunk, measured at 28% of runtime.
        # tdi takes bytes with an explicit bit length. ignore_response
        # suppresses the per-chunk GET_IN_BUFFER readback.
        jtag.shift_data(tdi=payload, length=len(payload) * 8,
                        ignore_response=True)
    return (time.perf_counter() - started) * 1000


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunks", type=int, nargs="+", default=[256, 512])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--bytes", type=int, default=PAYLOAD_BYTES,
                        help="override the fixed payload; makes results "
                             "incomparable with committed numbers")
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()
    payload = make_payload(args.bytes)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit(f"firmware: {debugger.get_firmware_version()}")
        emit(f"payload:  {args.bytes} bytes"
             + ("" if args.bytes == PAYLOAD_BYTES
                else "  (OVERRIDDEN -- not comparable with committed numbers)"))

        advertised = None
        try:
            info = bytes(debugger.device.ctrl_transfer(0xC0, 0xb8, 0, 0, 8))
            advertised = int.from_bytes(info[0:4], "little")
            emit(f"firmware buffer: {advertised} bytes (GET_INFO)")
        except Exception:
            emit("GET_INFO unimplemented -- the host falls back to 256, so any "
                 "larger row below did NOT run at the size it claims")
        emit()

        results = {}
        for chunk in args.chunks:
            if advertised is not None and chunk > advertised:
                emit(f"  {chunk:>4} B: skipped, exceeds the {advertised}-byte "
                     f"firmware buffer")
                continue
            times = [time_shift(debugger, payload, chunk)
                     for _ in range(args.runs)]
            results[chunk] = times
            count = -(-args.bytes // chunk)
            best = min(times)
            emit(f"  {chunk:>4} B/chunk  {count:>4} chunks  "
                 f"best {best:>7.1f} ms  "
                 f"median {statistics.median(times):>7.1f} ms  "
                 f"spread {max(times) - min(times):>5.1f} ms  "
                 f"{args.bytes / best:>6.1f} KB/s")

        emit()
        if len(results) >= 2:
            ordered = sorted(results)
            a, b = min(results[ordered[0]]), min(results[ordered[-1]])
            emit(f"  {ordered[0]} -> {ordered[-1]} B: {a:.1f} -> {b:.1f} ms, "
                 f"{a / b:.2f}x, {a - b:.1f} ms saved")

        emit()
        emit("  Shift path only: no ISC_ENABLE, no CRC check, no DONE poll, so")
        emit("  the FPGA's configuration is untouched and this is safe to repeat.")
        emit("  A real configure is slower by whatever that sequence costs.")
        emit()
        emit("  Best-of rather than mean: USB scheduling adds latency and never")
        emit("  removes it, so the minimum is closest to the path's real cost.")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
