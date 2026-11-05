#!/usr/bin/env python3
# Read where Apollo's main loop was stuck when the watchdog early warning fired (experiment firmware).
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/apollo/wd_capture.py

Experiment firmware only (repos/apollo branch exp-hang-pc): vendor 0xB9 returns [magic, pc, lr,
xpsr] captured by the WDT early-warning handler 250 ms after the last kick, and clears the magic.
Resolve the pc with: arm-none-eabi-addr2line -f -e repos/apollo/firmware/_build/cynthion_d11/firmware.elf <pc>
Log: ./tmp/logs/wd_capture.log.
"""

import logging
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "wd_capture.log"
APOLLO = (0x1d50, 0x615c)
REQUEST_CAPTURE = 0xB9
MAGIC = 0x57444350
FAULT_MAGIC = 0x48464C54
DUMP_BYTES = 180
USB_TIMEOUT_MS = 500        # a local USB request answers in ms


def decode(raw):
    magic, pc, lr, xpsr = struct.unpack("<4I", bytes(raw)[:16])
    return {"valid": magic in (MAGIC, FAULT_MAGIC), "kind": "HardFault" if magic == FAULT_MAGIC else "watchdog early warning",
            "pc": pc, "lr": lr, "xpsr": xpsr, "dump": bytes(raw)[16:196], "last": struct.unpack("<9I", bytes(raw)[236:272])}


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    import usb.core
    device = usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])
    if device is None:
        logging.error("Apollo is not on the bus")
        return 2
    got = decode(device.ctrl_transfer(0xC0, REQUEST_CAPTURE, 0, 0, 236 + 36, USB_TIMEOUT_MS))
    if not got["valid"]:
        logging.info("nothing captured")
        return 1
    logging.info("%s at pc=0x%08x lr=0x%08x xpsr=0x%08x", got["kind"], got["pc"], got["lr"], got["xpsr"])
    device.ctrl_transfer(0x40, REQUEST_CAPTURE, 1, 0, None, USB_TIMEOUT_MS)   # clear
    words, count = got["last"][:8], got["last"][8]
    for i in range(min(count, 4)):
        n = count - 1 - i                      # newest first
        a, b = words[(n & 3) * 2], words[(n & 3) * 2 + 1]
        logging.info("request -%d: bRequest 0x%02x wLength %d wValue 0x%04x wIndex 0x%04x",
                     i, a & 0xFF, a >> 16, b & 0xFFFF, b >> 16)
    dump = got["dump"]
    logging.info("180 bytes after comms: %s", dump.hex(" "))
    logging.info("distinct values: %s", sorted(set(dump)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
