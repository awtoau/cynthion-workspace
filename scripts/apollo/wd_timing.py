#!/usr/bin/env python3
# Measure Apollo's main-loop watchdog timeout: send BOOT_TO_DFU, time until the bootloader enumerates.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/apollo/wd_timing.py [--runs N]

Vendor 0xED acks, then dfu.c spins without kicking the WDT, so the reset lands one WDT timeout after
the last kick (kicks are 20 ms apart, so the measurement carries up to +20 ms). The bootloader
shows as product "Cynthion Bootloader" on 1d50:615c. Needs Apollo answering. Do not run while the
board arbiter holds the board. Leaves the board in the bootloader; run `apollo exit-dfu` after.
Log: ./tmp/logs/wd_timing.log.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "wd_timing.log"

APOLLO = (0x1d50, 0x615c)
REQUEST_BOOT_TO_DFU = 0xED
USB_TIMEOUT_MS = 500        # a local USB request answers in ms
POLL_S = 0.005              # enumeration is the event; 5 ms resolution is 0.1% of a 5 s reading
# The longest period configured, 16384 clocks: 16 s at 1.024 kHz. 20 s = 1.25x; on expiry the run
# is reported as no reset.
LIMIT_S = 20


def product(device):
    try:
        return device.product
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    import usb.core

    device = usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])
    if device is None:
        logging.error("Apollo is not on the bus")
        return 2
    logging.info("before: product %r", product(device))
    device.ctrl_transfer(0x40, REQUEST_BOOT_TO_DFU, 0, 0, None, USB_TIMEOUT_MS)
    start = time.monotonic()
    first_gone = None
    while time.monotonic() - start < LIMIT_S:
        found = usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])
        name = product(found) if found is not None else None
        if found is None and first_gone is None:
            first_gone = time.monotonic() - start
        if name and "Bootloader" in name:
            logging.info("bootloader enumerated %.3f s after 0xED (device gone at %s)",
                         time.monotonic() - start,
                         f"{first_gone:.3f} s" if first_gone is not None else "never seen")
            return 0
        time.sleep(POLL_S)
    logging.error("no bootloader within %d s", LIMIT_S)
    return 1


if __name__ == "__main__":
    sys.exit(main())
