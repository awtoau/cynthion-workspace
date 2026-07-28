#!/usr/bin/env python3
#
# Measure raw bulk-endpoint loopback throughput against the FPGA.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures round-trip throughput over the raw bulk loopback bitstream.

This is the control for `usb_serial_speed.py`. That script measures CDC-ACM
through a tty; this one measures the same loopback over a vendor-specific bulk
interface via libusb, with no cdc-acm driver, line discipline or termios in the
path. Comparing the two separates USB's own protocol overhead from the cost of
the host's serial stack.

The measurement itself mirrors the serial script -- concurrent reader thread,
same counting payload, same round-trip accounting -- so the numbers are
directly comparable. The one knob that matters is `--chunk`: the host's
transfer size, not the endpoint's 512-byte packet size, sets how many packets
the controller schedules back to back before the host has to turn around.

    ./scripts/usb_bulk_speed.py
    ./scripts/usb_bulk_speed.py --chunk 262144
"""

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "usb_bulk_speed.log"

VENDOR_ID  = 0x1d50
PRODUCT_ID = 0x615b
BULK_OUT   = 0x01
BULK_IN    = 0x81

DEFAULT_SIZES = [1024, 4096, 16384, 65536, 262144, 1048576]


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def measure(dev, total, chunk, timeout=20.0):
    """Round-trip `total` bytes, reading on a separate thread.

    Concurrency is not optional, for the same reason it is not optional in
    `usb_serial_speed.py`: the gateware echoes as it receives, and its buffer
    is a few packets deep. Writing everything before reading anything fills
    that buffer, the device stops accepting, and both ends wait on each other.
    A first version of this script did exactly that and hung on the first
    transfer.

    Returns (seconds, matched) or None if the read did not complete.
    """
    payload = bytes(range(256)) * (total // 256) + bytes(range(total % 256))

    received = bytearray()
    error    = []

    def reader():
        try:
            while len(received) < total:
                want = min(chunk, total - len(received))
                # Read timeout is per-transfer and generous; the outer join
                # bounds the whole measurement.
                data = dev.read(BULK_IN, want, timeout=5000)
                if len(data) == 0:
                    break
                received.extend(data)
        except Exception as exc:      # noqa: BLE001 - reported, not swallowed
            error.append(exc)

    thread = threading.Thread(target=reader, daemon=True)
    start  = time.perf_counter()
    thread.start()

    sent = 0
    try:
        while sent < total:
            n = min(chunk, total - sent)
            dev.write(BULK_OUT, payload[sent:sent + n], timeout=5000)
            sent += n
    except Exception as exc:          # noqa: BLE001
        error.append(exc)

    thread.join(timeout=timeout)
    elapsed = time.perf_counter() - start

    if error:
        raise error[0]
    if len(received) < total:
        return None
    return elapsed, bytes(received) == payload


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES)
    parser.add_argument("--chunk", type=int, default=65536,
                        help="host transfer size in bytes")
    args = parser.parse_args()

    import usb.core
    import usb.util

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print(f"no device {VENDOR_ID:04x}:{PRODUCT_ID:04x} found")
        return 1

    dev.set_configuration()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, f"USB bulk loopback on {VENDOR_ID:04x}:{PRODUCT_ID:04x}")
        emit(handle, f"chunk={args.chunk} B")
        emit(handle, "round trip: every byte crosses the bus twice")
        emit(handle)
        emit(handle, f"  {'bytes':>9} {'ms':>9} {'payload':>9} {'bus':>9} "
                     f"{'bus Mbps':>10}  data")

        for size in args.sizes:
            try:
                result = measure(dev, size, args.chunk)
            except Exception as exc:                     # noqa: BLE001
                emit(handle, f"  {size:>9}  failed: {exc}")
                continue

            if result is None:
                emit(handle, f"  {size:>9}  incomplete -- device stopped "
                             f"returning data")
                continue

            elapsed, matched = result
            rate     = size / elapsed
            bus_rate = 2 * rate
            emit(handle, f"  {size:>9} {elapsed*1000:>9.2f} {rate/1e6:>8.2f}M "
                         f"{bus_rate/1e6:>8.2f}M {bus_rate*8/1e6:>10.1f}  "
                         f"{'match' if matched else 'MISMATCH'}")

        emit(handle)
        emit(handle, "payload = bytes delivered to the caller; bus = twice that.")
        emit(handle, "Compare the bus figure against 480 Mbps line rate and")
        emit(handle, "against ~426 Mbps, the USB 2.0 HS bulk ceiling for 512 B")
        emit(handle, "packets (13 transactions per 125 us microframe).")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
