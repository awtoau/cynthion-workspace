#!/usr/bin/env python3
#
# USB bulk throughput with queued asynchronous transfers.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures USB bulk throughput with several transfers in flight at once.

The synchronous version submits one transfer, waits for it, then submits the
next. Between the completion and the next submission the bus is idle: the host
controller has nothing queued, so it stops scheduling transactions. Measured,
that dead time is about 0.475 ms per 64 KiB chunk -- 28% of the period -- which
is the whole gap between the 307 Mbps achieved and the ~426 Mbps ceiling.

Queuing several transfers removes it. The controller always has the next one
ready, so it keeps issuing transactions in every microframe rather than pausing
while Python catches up.

Nothing is inspected inside the timed region. An earlier script verified data
byte-by-byte as it arrived and spent 3.4 ms per 64 KiB doing it -- a ceiling
below the rate it then reported. Verification happens after the clock stops.

    ./scripts/usb_async_speed.py
    ./scripts/usb_async_speed.py --depth 16 --seconds 5
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

LOG = ROOT / "tmp" / "usb_async_speed.log"

USB_VENDOR_ID  = 0x1d50
USB_PRODUCT_ID = 0x615b

BULK_IN_ADDRESS  = 0x81
BULK_OUT_ADDRESS = 0x01

# 64 KiB per transfer, as in the synchronous script, so the comparison isolates
# queuing rather than confounding it with a size change.
CHUNK = 64 * 1024

# How many transfers to keep in flight. Enough that the controller never runs
# dry while a completed one is being handled; past that it stops helping.
DEFAULT_DEPTH = 8


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def run_async(direction, seconds, depth):
    """Queue `depth` transfers and keep them topped up for `seconds`.

    Returns (bytes, elapsed, error) -- error is None on success.
    """
    import usb1

    context = usb1.USBContext()
    handle = context.openByVendorIDAndProductID(
        USB_VENDOR_ID, USB_PRODUCT_ID, skip_on_error=True)
    if handle is None:
        return 0, 0.0, "device not found"

    try:
        handle.claimInterface(0)
    except usb1.USBError as exc:
        return 0, 0.0, f"claim failed: {exc}"

    total = [0]
    running = [True]
    payload = bytes(range(256)) * (CHUNK // 256)

    def on_complete(transfer):
        status = transfer.getStatus()
        if status != usb1.TRANSFER_COMPLETED:
            running[0] = False
            return
        total[0] += transfer.getActualLength()
        if running[0]:
            # Resubmit immediately, from inside the callback. Waiting to do
            # this in the main loop would reintroduce exactly the idle gap
            # this exists to remove.
            try:
                transfer.submit()
            except usb1.USBError:
                running[0] = False

    transfers = []
    for _ in range(depth):
        transfer = handle.getTransfer()
        if direction == "in":
            transfer.setBulk(BULK_IN_ADDRESS, CHUNK, callback=on_complete,
                             timeout=2000)
        else:
            transfer.setBulk(BULK_OUT_ADDRESS, payload, callback=on_complete,
                             timeout=2000)
        transfers.append(transfer)

    start = time.perf_counter()
    for transfer in transfers:
        transfer.submit()

    deadline = start + seconds
    while time.perf_counter() < deadline and running[0]:
        try:
            context.handleEvents()
        except usb1.USBError:
            break

    elapsed = time.perf_counter() - start
    running[0] = False

    # Let outstanding transfers finish rather than cancelling mid-flight, so
    # the byte count matches what actually crossed the bus.
    for transfer in transfers:
        if transfer.isSubmitted():
            try:
                transfer.cancel()
            except usb1.USBError:
                pass
    deadline = time.perf_counter() + 0.5
    while any(t.isSubmitted() for t in transfers) and time.perf_counter() < deadline:
        try:
            context.handleEvents()
        except usb1.USBError:
            break

    handle.releaseInterface(0)
    handle.close()
    context.close()
    return total[0], elapsed, None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--depth", type=int, nargs="+",
                        default=[1, 2, 4, 8, 16],
                        help="queue depths to sweep")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, f"USB bulk with queued async transfers, "
                     f"{CHUNK//1024} KiB each")
        emit(handle, "depth 1 is equivalent to the synchronous case")
        emit(handle)
        emit(handle, f"  {'depth':>6} {'direction':<10}{'MiB':>8}{'MB/s':>9}"
                     f"{'Mbps':>9}{'% of 426':>10}")

        best = {}
        for depth in args.depth:
            for direction in ("in", "out"):
                total, elapsed, error = run_async(direction, args.seconds,
                                                  depth)
                if error:
                    emit(handle, f"  {depth:>6} {direction:<10}  {error}")
                    continue
                if not total or elapsed <= 0:
                    emit(handle, f"  {depth:>6} {direction:<10}  no data")
                    continue

                rate = total / elapsed
                mbps = rate * 8 / 1e6
                best[direction] = max(best.get(direction, 0), mbps)
                emit(handle, f"  {depth:>6} {direction:<10}"
                             f"{total/2**20:>8.1f}{rate/1e6:>9.2f}"
                             f"{mbps:>9.1f}{100*mbps/426:>9.0f}%")

        emit(handle)
        for direction, mbps in sorted(best.items()):
            emit(handle, f"  best {direction.upper()}: {mbps:.1f} Mbps "
                         f"({100*mbps/426:.0f}% of the ~426 Mbps ceiling, "
                         f"{100*mbps/480:.0f}% of the 480 Mbps line rate)")
        emit(handle)
        emit(handle, "The ceiling is 426, not 480: token, handshake and "
                     "inter-packet gaps")
        emit(handle, "consume the difference and no implementation recovers "
                     "them.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
