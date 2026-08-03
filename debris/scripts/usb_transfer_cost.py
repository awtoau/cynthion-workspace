#!/usr/bin/env python3
#
# Separate the fixed and per-byte cost of a USB control transfer.
# See awtoau/cynthion-workspace#100.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures what a `SET_OUT_BUFFER` control transfer costs, by payload size.

Roughly half the USB cost of an FPGA configure was unaccounted for: at 512-byte
chunks the transport took 1466 us per chunk, of which two control transfers
explained 428 us and the payload's wire time 341 us, leaving ~700 us with no
explanation. The leading hypothesis was **full-speed frame scheduling** -- control
transfers are scheduled on 1 ms boundaries, so a transfer that straddles a boundary
might cost a whole frame regardless of size.

That distinction decides whether the remaining optimisations are worth building. If
the cost is per-transfer, collapsing two requests into one recovers it. If it is
per-frame, nothing on either end can.

## The test

Issue the same request at 8, 64, 256, 512 and 1024 bytes and fit
`cost = fixed + per_byte * size`.

- **Frame scheduling** predicts a near-flat line: 8 bytes and 1024 bytes both cost
  about a frame.
- **Per-transfer overhead plus real payload cost** predicts a straight line with a
  meaningful slope.

`SET_OUT_BUFFER` is the right request to measure because it is the one the configure
path actually issues per chunk, and because it does almost nothing on arrival -- the
firmware copies into a buffer. So the measurement is transport, not work.

## What it does to the board

Nothing persistent. `SET_OUT_BUFFER` stages bytes into `jtag_out_buffer` and no scan
is issued, so nothing is clocked to the FPGA and its configuration is untouched. The
JTAG lock is taken and released around the run.

    ./scripts/usb_transfer_cost.py
    ./scripts/usb_transfer_cost.py --sizes 8 256 1024 --runs 500
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "usb_transfer_cost.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

REQUEST_JTAG_SET_OUT_BUFFER = 0xB1

# Full-speed USB: 12 Mbit/s, so one byte is 0.667 us on the wire at best. Used only
# to put the marginal cost in context, not as a target.
WIRE_US_PER_BYTE = 8 / 12.0


def measure(dev, size, runs):
    """Mean microseconds per transfer of `size` bytes."""
    payload = bytes(size)
    started = time.perf_counter()
    for _ in range(runs):
        dev.ctrl_transfer(0x40, REQUEST_JTAG_SET_OUT_BUFFER, 0, 0, payload)
    return (time.perf_counter() - started) / runs * 1e6


def fit(points):
    """Least-squares fit of cost = fixed + per_byte * size."""
    n = len(points)
    sx = sum(s for s, _ in points)
    sy = sum(t for _, t in points)
    sxx = sum(s * s for s, _ in points)
    sxy = sum(s * t for s, t in points)
    per_byte = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    fixed = (sy - per_byte * sx) / n
    return fixed, per_byte


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+",
                        default=[8, 64, 256, 512, 1024])
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit(f"firmware: {debugger.get_firmware_version()}")

        limit = None
        try:
            info = bytes(debugger.device.ctrl_transfer(0xC0, 0xb8, 0, 0, 12))
            limit = int.from_bytes(info[0:4], "little")
            emit(f"buffer limit: {limit} bytes")
        except Exception:
            emit("GET_INFO unimplemented; assuming a 256-byte limit")
            limit = 256

        emit(f"{args.runs} transfers per size")
        emit()

        points = []
        with debugger.jtag as jtag:  # noqa: F841  (holds the JTAG lock)
            for size in args.sizes:
                if size > limit:
                    emit(f"  {size:>5} B: skipped, exceeds the {limit}-byte buffer")
                    continue
                per = measure(debugger.device, size, args.runs)
                points.append((size, per))
                emit(f"  {size:>5} B: {per:>8.1f} us/transfer   "
                     f"wire alone would be {size * WIRE_US_PER_BYTE:>7.1f} us")

        if len(points) < 2:
            emit("\nnot enough points to fit")
            return 1

        fixed, per_byte = fit(points)
        emit()
        emit(f"  fit: {fixed:.1f} us fixed + {per_byte:.3f} us/byte")
        emit(f"  wire is {WIRE_US_PER_BYTE:.3f} us/byte, so "
             f"{per_byte - WIRE_US_PER_BYTE:.3f} us/byte "
             f"({100 * (per_byte - WIRE_US_PER_BYTE) / per_byte:.0f}%) is overhead")
        emit(f"  effective throughput: {8 / per_byte:.2f} Mbit/s of a 12 Mbit/s bus "
             f"({100 * (8 / per_byte) / 12:.0f}%)")
        emit()

        # The verdict this test exists to deliver.
        flat = max(t for _, t in points) / min(t for _, t in points)
        if flat < 1.5:
            emit("  VERDICT: cost is nearly flat across payload size, which is the")
            emit("  frame-scheduling signature. Collapsing requests would recover")
            emit("  the fixed cost; nothing can recover the rest.")
        else:
            emit("  VERDICT: cost scales with payload, so this is NOT frame")
            emit("  scheduling. The per-byte term dominates at realistic chunk")
            emit("  sizes, and it is bus efficiency rather than anything either")
            emit("  end controls -- a 64-byte endpoint spends most of each packet")
            emit("  on token and handshake overhead.")
            emit()
            emit("  Consequence: reducing the NUMBER of transfers recovers only the")
            emit("  fixed term. Moving fewer bytes, or moving them on a bulk")
            emit("  endpoint with a larger packet size, is what would matter.")

        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
