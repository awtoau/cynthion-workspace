#!/usr/bin/env python3
#
# Measure flash read throughput for small random reads, in both quad modes.
# SPDX-License-Identifier: BSD-3-Clause

"""
Compares 0x6B quad output against 0xEB quad I/O for small scattered reads.

The streaming benchmark measures one 32 KiB transaction, which pays its
per-transaction overhead once and so cannot distinguish the two opcodes -- 0xEB
saved 40 cycles out of 131157 there, 0.03%. This measures the opposite case:
many short reads at scattered addresses, each paying overhead in full.

That is the access pattern for a RISC-V executing from flash, filling cache
lines of 16 to 64 bytes, and the datasheet says explicitly that 0xEB exists to
allow "faster random access for code execution (XIP)".

Addresses stride by 4099 bytes -- a prime, so successive reads land in
different pages and rarely repeat. A sequential walk would stay inside one page
and measure the wrong thing entirely.

    ./scripts/qspi_burst.py
    ./scripts/qspi_burst.py --sizes 4 32 --reads 512
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

LOG = ROOT / "tmp" / "logs" / "qspi_burst.log"

REG_ID = 1
REG_STATUS = 5
REG_DIVISOR = 6
REG_START = 7
REG_MODE = 8
REG_BURST_LEN = 10
REG_BURST_COUNT = 11
REG_BURST_CYCLES = 12
REG_BURST_DONE = 13

APPLET_ID = 0x51535049

# Poll bound for a burst to finish. Each JTAG register read is itself hundreds
# of microseconds, so a few hundred polls is many milliseconds -- far longer
# than any burst here, and small enough that a hang is reported rather than
# spinning forever.
POLL_LIMIT = 400


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def run_burst(dut, *, mode, size, reads, tag):
    """Returns cycles for `reads` reads of `size` bytes, or None if it hung.

    The completed-read count is checked as well as the status bits. A gateware
    fault that stops the run partway leaves `done` set from an earlier read, and
    a poll that trusted the status word alone would take that run's cycle count
    for this one -- which is how the wedge in #89 reported plausible numbers.
    """
    dut.registers.register_write(REG_MODE, mode)
    dut.registers.register_write(REG_BURST_LEN, size)
    dut.registers.register_write(REG_BURST_COUNT, reads)
    dut.registers.register_write(REG_START, tag)

    for _ in range(POLL_LIMIT):
        status = dut.registers.register_read(REG_STATUS)
        if (status & 1) and not (status >> 1) & 1:
            if dut.registers.register_read(REG_BURST_DONE) != reads:
                return None
            return dut.registers.register_read(REG_BURST_CYCLES)
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+",
                        default=[4, 16, 32, 64, 256])
    parser.add_argument("--reads", type=int, default=256)
    parser.add_argument("--divisor", type=int, default=1)
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger

    LOG.parent.mkdir(parents=True, exist_ok=True)
    dut = ApolloDebugger()

    applet = dut.registers.register_read(REG_ID)
    if applet != APPLET_ID:
        print(f"wrong bitstream: applet 0x{applet:08x}, "
              f"expected 0x{APPLET_ID:08x}")
        return 1

    sync = (dut.registers.register_read(REG_STATUS) >> 8) & 0xFFFF
    sck = sync / (args.divisor + 1)
    dut.registers.register_write(REG_DIVISOR, args.divisor)

    with LOG.open("w") as handle:
        emit(handle, f"quad read modes for small scattered reads")
        emit(handle, f"sync {sync} MHz, SCK {sck:.1f} MHz, "
                     f"{args.reads} reads per point")
        emit(handle)
        emit(handle, f"  {'bytes':>6} {'0x6B':>10} {'0xEB':>10} {'gain':>7}  "
                     f"{'MB/s 0x6B':>10} {'MB/s 0xEB':>10}")

        tag = 1
        for size in args.sizes:
            results = {}
            for mode in (0, 1):
                tag += 1
                results[mode] = run_burst(dut, mode=mode, size=size,
                                          reads=args.reads, tag=tag)

            if results[0] is None or results[1] is None:
                emit(handle, f"  {size:>6} did not complete")
                continue

            quad_out, quad_io = results[0], results[1]
            gain = 100 * (quad_out - quad_io) / quad_out if quad_out else 0
            total = size * args.reads
            rate_out = total / (quad_out / (sync * 1e6)) / 1e6
            rate_io = total / (quad_io / (sync * 1e6)) / 1e6

            emit(handle, f"  {size:>6} {quad_out:>10} {quad_io:>10} "
                         f"{gain:>6.1f}% {rate_out:>10.2f} {rate_io:>10.2f}")

        emit(handle)
        emit(handle, "0xEB sends the address on four lanes as well as the "
                     "data, saving 20 clocks per")
        emit(handle, "transaction. That is a fixed saving, so it matters in "
                     "proportion to how short the")
        emit(handle, "reads are -- and disappears entirely into a streaming "
                     "transfer.")
        emit(handle)
        emit(handle, "BURST_CYCLES includes one arming cycle per read, so a "
                     "per-reader figure is")
        emit(handle, f"cycles - {args.reads}. It is below the resolution of "
                     f"anything measured here.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
