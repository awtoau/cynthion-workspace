#!/usr/bin/env python3
#
# Measure USB CDC-ACM loopback throughput against the FPGA.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures round-trip throughput over the USB serial loopback bitstream.

Reads and writes concurrently, which is not optional. A naive
write-everything-then-read-everything deadlocks: the gateware echoes each byte
as it arrives, so the inbound tty buffer fills while the script is still
writing, the device stops accepting, and both ends wait for the other. That is
what a first attempt here did -- it hung on 4 KiB while the gateware counters
showed 25635 bytes received and 25635 echoed, proving the hardware was fine and
the script was not.

Throughput is therefore a round-trip figure over a loopback: every byte crosses
the bus twice. It is a measure of the CDC path rather than of raw USB
capability, and CDC adds framing and per-transfer overhead that a bulk endpoint
would not. For a raw speed ceiling, use a bulk endpoint instead -- LUNA's own
speed_test applet does exactly that, and for the same reason.

    ./scripts/usb_serial_speed.py
    ./scripts/usb_serial_speed.py --port /dev/ttyACM10 --sizes 4096 65536
"""

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "usb_serial_speed.log"

DEFAULT_SIZES = [1024, 4096, 16384, 65536]


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def find_port():
    """The most recently created ttyACM, which is almost always the one just
    enumerated. Explicit --port beats guessing when several are present."""
    candidates = sorted(Path("/dev").glob("ttyACM*"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else None


def measure(port, size, timeout=10.0):
    """Round-trip `size` bytes, reading in a separate thread.

    `timeout` is derived from a measured rate rather than guessed -- see
    main(). A fixed generous timeout means a hung transfer costs the full
    wait every time, which is the slow way to discover something is wrong.

    Returns (seconds, bytes_returned, matched) or None on timeout.
    """
    import serial

    # A repeating counting pattern rather than constant data: a dropped or
    # duplicated byte shows as a discontinuity at a known position, where a
    # constant fill would hide it entirely.
    payload = bytes(range(256)) * (size // 256) + bytes(range(size % 256))

    received = bytearray()
    error = []

    def reader(handle):
        try:
            while len(received) < len(payload):
                chunk = handle.read(min(4096, len(payload) - len(received)))
                if not chunk:
                    break
                received.extend(chunk)
        except Exception as exc:      # noqa: BLE001 - reported, not swallowed
            error.append(exc)

    with serial.Serial(port, timeout=1) as handle:
        handle.reset_input_buffer()
        handle.reset_output_buffer()

        thread = threading.Thread(target=reader, args=(handle,), daemon=True)
        start = time.perf_counter()
        thread.start()
        handle.write(payload)
        handle.flush()
        thread.join(timeout=timeout)
        elapsed = time.perf_counter() - start

    if error:
        raise error[0]
    if len(received) < len(payload):
        return None
    return elapsed, len(received), bytes(received) == payload


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=None)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES)
    args = parser.parse_args()

    port = args.port or find_port()
    if port is None:
        print("no ttyACM device found")
        return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, f"USB CDC-ACM loopback on {port}")
        emit(handle, "round trip: every byte crosses the bus twice")
        emit(handle)
        emit(handle, f"  {'bytes':>7} {'ms':>9} {'MB/s':>8} {'Mbps':>9}  data")

        # Calibrated from the first successful transfer, then used to bound
        # every later one. Starts generous because nothing is known yet.
        observed_rate = None

        for size in args.sizes:
            if observed_rate is None:
                timeout = 10.0
            else:
                # Allow five times the expected duration, with a floor for
                # per-transfer overhead. A transfer that overruns that is not
                # slow, it is stuck -- and waiting longer only delays finding
                # out. The earlier version of this script hung for minutes on
                # a deadlock that this would have caught in under a second.
                timeout = max(0.25, 5.0 * size / observed_rate)

            try:
                result = measure(port, size, timeout=timeout)
            except Exception as exc:                     # noqa: BLE001
                emit(handle, f"  {size:>7}  failed: {exc}")
                continue

            if result is None:
                emit(handle, f"  {size:>7}  aborted after {timeout*1000:.0f} ms "
                             f"-- expected about "
                             f"{1000*size/observed_rate:.0f} ms"
                             if observed_rate else
                             f"  {size:>7}  timed out")
                continue

            elapsed, returned, matched = result
            rate = size / elapsed
            # Track the best rate seen: a later stall should be judged against
            # what the link has demonstrably achieved, not against its own
            # degraded figure.
            if observed_rate is None or rate > observed_rate:
                observed_rate = rate
            emit(handle, f"  {size:>7} {elapsed*1000:>9.2f} {rate/1e6:>8.2f} "
                         f"{rate*8/1e6:>9.1f}  "
                         f"{'match' if matched else 'MISMATCH'}")

        emit(handle)
        emit(handle, "CDC framing and per-transfer overhead mean this is well "
                     "below the raw")
        emit(handle, "480 Mbps the link negotiates. A bulk endpoint measures "
                     "the ceiling.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
