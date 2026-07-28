#!/usr/bin/env python3
#
# Measure each direction of a USB bulk link separately.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures IN and OUT throughput independently against the one-way bitstream.

A loopback measures neither direction cleanly. Every byte crosses the bus twice,
so latency is paid twice; and both directions share the endpoint buffering, so a
stall in one throttles the other. That is not hypothetical here -- a
combinational loopback held the CDC path to 195 Mbps because `rx.ready` was
wired to `tx.ready`, and a packet awaiting acknowledgement NAKed the next one
arriving.

So this drives one direction at a time:

    IN   the device generates a counting sequence and never stops; the host
         reads it. Nothing can back-pressure the device, so this is how fast
         it can fill the bus.

    OUT  the host sends a counting sequence; the device verifies and discards
         it. Nothing is transmitted, so no buffer is shared, and this is how
         fast the device can drain the bus.

Both figures are one-way, so they compare directly against the 480 Mbps line
rate -- unlike a loopback figure, which must be doubled first.

Correctness is checked in both directions. The host verifies the counting
sequence it reads, and the gateware counts mismatches in what it receives.
Measuring throughput without establishing the data was right would report a
broken link as a fast one.

    ./scripts/usb_oneway_speed.py
    ./scripts/usb_oneway_speed.py --seconds 5
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

LOG = ROOT / "tmp" / "usb_oneway_speed.log"

USB_VENDOR_ID  = 0x1d50
USB_PRODUCT_ID = 0x615b

BULK_IN_ADDRESS  = 0x81
BULK_OUT_ADDRESS = 0x01

APPLET_ID = 0x314f5741

REGISTER_ID        = 1
REGISTER_IN_COUNT  = 2
REGISTER_OUT_COUNT = 3
REGISTER_ERRORS    = 4

# Transfer size per call. Large enough that per-transfer overhead is amortised,
# small enough that a stalled link is noticed promptly rather than after a long
# blocking read.
CHUNK = 64 * 1024


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def open_device():
    import usb.core
    device = usb.core.find(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID)
    if device is None:
        return None
    try:
        device.set_configuration()
    except Exception:                       # noqa: BLE001
        # Already configured, which is fine -- another handle may hold it.
        pass
    return device


def measure_in(device, seconds):
    """Read continuously and verify the counting sequence.

    Returns (bytes, elapsed, mismatches).
    """
    total = 0
    mismatches = 0
    expected = None

    start = time.perf_counter()
    deadline = start + seconds

    while time.perf_counter() < deadline:
        try:
            data = device.read(BULK_IN_ADDRESS, CHUNK, timeout=2000)
        except Exception:                   # noqa: BLE001
            break
        if not len(data):
            break

        # The first byte can be anything -- the device is free-running and the
        # host joins wherever it happens to be. From there each byte should be
        # the previous plus one.
        for byte in data:
            if expected is not None and byte != expected:
                mismatches += 1
            expected = (byte + 1) & 0xFF

        total += len(data)

    return total, time.perf_counter() - start, mismatches


def measure_out(device, seconds):
    """Write a counting sequence continuously.

    Returns (bytes, elapsed).
    """
    payload = bytes(range(256)) * (CHUNK // 256)
    total = 0

    start = time.perf_counter()
    deadline = start + seconds

    while time.perf_counter() < deadline:
        try:
            written = device.write(BULK_OUT_ADDRESS, payload, timeout=2000)
        except Exception:                   # noqa: BLE001
            break
        total += written

    return total, time.perf_counter() - start


def read_registers():
    """Gateware-side counters, as an independent check on the host's figures."""
    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()
    return {
        "applet":  debugger.registers.register_read(REGISTER_ID),
        "in":      debugger.registers.register_read(REGISTER_IN_COUNT),
        "out":     debugger.registers.register_read(REGISTER_OUT_COUNT),
        "errors":  debugger.registers.register_read(REGISTER_ERRORS) & 0xFFFF,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=2.0,
                        help="how long to run each direction")
    args = parser.parse_args()

    device = open_device()
    if device is None:
        print(f"no device at {USB_VENDOR_ID:04x}:{USB_PRODUCT_ID:04x} -- "
              f"is the one-way bitstream loaded and AUX cabled?")
        return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, "USB bulk, one direction at a time")
        emit(handle, "figures are one-way, so compare directly against "
                     "480 Mbps")
        emit(handle)
        emit(handle, f"  {'direction':<10}{'MiB':>8}{'seconds':>9}{'MB/s':>9}"
                     f"{'Mbps':>9}  data")

        in_bytes, in_time, mismatches = measure_in(device, args.seconds)
        if in_time > 0 and in_bytes:
            rate = in_bytes / in_time
            emit(handle, f"  {'IN':<10}{in_bytes/2**20:>8.1f}{in_time:>9.2f}"
                         f"{rate/1e6:>9.2f}{rate*8/1e6:>9.1f}  "
                         f"{'sequence ok' if not mismatches else f'{mismatches} mismatches'}")
        else:
            emit(handle, f"  {'IN':<10}  no data")

        out_bytes, out_time = measure_out(device, args.seconds)
        if out_time > 0 and out_bytes:
            rate = out_bytes / out_time
            emit(handle, f"  {'OUT':<10}{out_bytes/2**20:>8.1f}{out_time:>9.2f}"
                         f"{rate/1e6:>9.2f}{rate*8/1e6:>9.1f}  "
                         f"(device-side check below)")
        else:
            emit(handle, f"  {'OUT':<10}  no data")

        emit(handle)
        try:
            counters = read_registers()
            emit(handle, f"  gateware: applet 0x{counters['applet']:08x}, "
                         f"sent {counters['in']} B, received {counters['out']} B, "
                         f"{counters['errors']} sequence errors")
            if counters["errors"]:
                emit(handle, "  OUT data was corrupted -- the device saw bytes "
                             "out of sequence")
        except Exception as exc:            # noqa: BLE001
            emit(handle, f"  gateware counters unavailable: {exc}")

        emit(handle)
        emit(handle, "Ceiling for HS bulk is about 426 Mbps: 13 transactions of "
                     "512 bytes per")
        emit(handle, "125 us microframe, roughly 89% of the 480 Mbps line rate.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
