#!/usr/bin/env python3
#
# Does the second transmit buffer still earn its 512 bytes of RAM?
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures the shift path with and without the host's buffer alternation.

## Why this question exists

`jtag_tx_alt` is a second 512-byte transmit buffer in Apollo's `.bss`. It was added so
that a `SET_OUT_BUFFER` could fill one buffer over USB while `spi_send()` clocked the
other, because `spi_send()` was a blocking spin that held the CPU for the whole chunk
(~700 us at 1024 bytes) and `tud_task()` could not run meanwhile. Two buffers were the
only way to get any overlap: one had to be free for the DMA engine to fill.

Making the SPI clocking DMA-driven removes that constraint. The CPU is no longer inside
the transfer at all, so `tud_task()` runs throughout and the host's next request is
serviced immediately -- with or without a second buffer. If that is true, the alternation
buys nothing and 512 bytes of RAM plus the size-selection logic in flash are free.

On a part with 4 KB of RAM and 14 KB of flash, that is not a rounding error, so it is
worth measuring rather than assuming.

## What it measures

The same fixed payload as `jtag_fixed_benchmark.py`, twice:

  * **alternating** -- the host advertises and uses both buffers, sending chunks of
    1024 and 512 bytes in turn. This is current behaviour.
  * **single** -- `alt_buffer_bytes` is forced to 0, which is exactly what the host does
    against a firmware that advertises no second buffer: one fixed chunk size, every
    chunk into the same buffer.

No firmware change is needed for the second case: the host already has the code path,
because it must work against firmware that predates the alternation. So this measures
the thing that would actually happen if the buffer were removed, not a proxy for it.

Nothing is programmed -- the scan runs with the TAP left in SHIFT-DR and no ISC_ENABLE,
so the FPGA ignores the data and its configuration is undisturbed.

    ./scripts/jtag_single_buffer_probe.py
    ./scripts/jtag_single_buffer_probe.py --runs 5
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "jtag_single_buffer_probe.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

# Same constant as jtag_fixed_benchmark.py, so the two scripts' numbers are directly
# comparable. Changing it here without changing it there silently breaks that.
PAYLOAD_BYTES = 122880


def make_payload(size):
    """An incrementing byte pattern, as jtag_fixed_benchmark.py uses."""
    return bytes(i & 0xFF for i in range(size))


def time_shift(debugger, payload, chunk_bytes, use_alt):
    """Shift the payload once. Returns milliseconds.

    `use_alt` False forces the single-buffer path by zeroing `alt_buffer_bytes` after
    jtag_init() has negotiated it -- the host then picks one chunk size and reuses the
    primary buffer, which is what it does against firmware with no second buffer.
    """
    started = time.perf_counter()
    with debugger.jtag as jtag:
        # Clamped after jtag_init(), which negotiates both of these via GET_INFO on
        # entry and would otherwise overwrite them.
        jtag.max_bits_per_scan = chunk_bytes * 8
        if not use_alt:
            jtag.alt_buffer_bytes = 0
        jtag.shift_data(tdi=payload, length=len(payload) * 8,
                        ignore_response=True)
    return (time.perf_counter() - started) * 1000


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunk", type=int, default=1024,
                        help="primary chunk size in bytes")
    parser.add_argument("--runs", type=int, default=3)
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
        emit(f"payload:  {PAYLOAD_BYTES} bytes, {args.chunk} B chunks")
        emit()

        results = {}
        for label, use_alt in (("alternating", True), ("single", False)):
            times = [time_shift(debugger, payload, args.chunk, use_alt)
                     for _ in range(args.runs)]
            results[label] = times
            best = min(times)
            emit(f"  {label:>12}  best {best:>7.1f} ms  "
                 f"median {statistics.median(times):>7.1f} ms  "
                 f"spread {max(times) - min(times):>5.1f} ms  "
                 f"{PAYLOAD_BYTES / best:>6.1f} KB/s")

        emit()
        alt = min(results["alternating"])
        one = min(results["single"])
        emit(f"  cost of dropping the second buffer: {one - alt:+.1f} ms "
             f"({100 * (one - alt) / alt:+.1f}%)")
        emit()
        emit("  Best-of rather than mean: USB scheduling adds latency and never")
        emit("  removes it, so the minimum is closest to the path's real cost.")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
