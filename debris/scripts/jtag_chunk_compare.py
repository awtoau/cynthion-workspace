#!/usr/bin/env python3
#
# Time an ECP5 configure at different JTAG chunk sizes.
# See awtoau/cynthion-workspace#100.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures configure time against JTAG chunk size, isolating that one variable.

FPGA configuration over JTAG is dominated by per-request USB overhead, not by
clocking: 612 ms of 953 ms measured, across 394 chunks at roughly 215 us of fixed
cost each. Doubling the chunk halves the request count, and that was one of two
levers identified on #100 that had never been tried.

## Why the host side is overridden rather than the firmware rebuilt

Rebuilding the firmware at each size would confound the measurement: two builds
differ in more than the buffer, and reflashing between runs adds a DFU cycle to
every data point. Instead the firmware advertises its real buffer size once, and
this script clamps the HOST's `max_bits_per_scan` downwards per run.

That is valid in one direction only. The host can always use a **smaller** chunk
than the firmware's buffer -- the firmware's bound check is
`wLength > sizeof(jtag_out_buffer)`, so anything under the limit is accepted. It
cannot be pushed above, and asking for more would simply stall.

So this compares 512 against 256 with a single firmware, which is the comparison
that isolates chunk size from everything else.

    ./scripts/jtag_chunk_compare.py
    ./scripts/jtag_chunk_compare.py --sizes 128 256 512 --runs 5
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_chunk_compare.log"
DEFAULT_BITSTREAM = ROOT / "ecp5-test" / "sideband" / "build" / "top.bit"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))


def configure_at(debugger, bitstream, chunk_bytes):
    """Configure the FPGA with the host clamped to a chunk size. Returns ms."""
    from apollo_fpga.ecp5 import ECP5_JTAGProgrammer

    started = time.perf_counter()
    with debugger.jtag as jtag:
        # Clamp inside the context, after jtag_init() has negotiated via
        # GET_INFO -- otherwise the negotiated value overwrites this.
        jtag.max_bits_per_scan = chunk_bytes * 8
        programmer = ECP5_JTAGProgrammer(jtag)
        programmer.configure(bitstream.read_bytes())
    return (time.perf_counter() - started) * 1000


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=[256, 512])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--bitstream", type=Path, default=DEFAULT_BITSTREAM)
    args = parser.parse_args()

    if not args.bitstream.exists():
        print(f"no bitstream at {args.bitstream}")
        return 1

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        size_bytes = args.bitstream.stat().st_size
        emit(f"firmware: {debugger.get_firmware_version()}")
        emit(f"bitstream: {args.bitstream.name}, {size_bytes} bytes")

        # What the firmware actually advertises. If GET_INFO is unimplemented the
        # host falls back to 256, and a "512" row would silently be a 256 row.
        advertised = None
        try:
            info = bytes(debugger.device.ctrl_transfer(0xC0, 0xb8, 0, 0, 8))
            advertised = int.from_bytes(info[0:4], "little")
            emit(f"firmware advertises {advertised} bytes via GET_INFO")
        except Exception:
            emit("GET_INFO unimplemented -- host would fall back to 256, so any "
                 "larger size below is NOT what actually ran")
        emit()

        results = {}
        for chunk in args.sizes:
            if advertised is not None and chunk > advertised:
                emit(f"  {chunk:>4} B: skipped -- exceeds the {advertised}-byte "
                     f"buffer, the host cannot go above it")
                continue
            times = []
            for _ in range(args.runs):
                times.append(configure_at(debugger, args.bitstream, chunk))
            results[chunk] = times
            chunks = -(-size_bytes // chunk)
            emit(f"  {chunk:>4} B/chunk, ~{chunks:>4} chunks: "
                 f"best {min(times):>6.0f} ms  "
                 f"median {statistics.median(times):>6.0f} ms  "
                 f"spread {max(times) - min(times):>4.0f} ms")

        emit()
        if len(results) >= 2:
            ordered = sorted(results)
            slowest, fastest = ordered[0], ordered[-1]
            a, b = min(results[slowest]), min(results[fastest])
            emit(f"  {slowest} -> {fastest} bytes: {a:.0f} -> {b:.0f} ms, "
                 f"{a / b:.2f}x, {a - b:.0f} ms saved")
            emit()
            emit("  Best-of rather than mean, deliberately: USB scheduling adds")
            emit("  latency but never removes it, so the minimum is the closest")
            emit("  estimate of the path's real cost.")

        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
