#!/usr/bin/env python3
# Select Apollo's FPGA_ADV mode and read the sideband health bytes.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/apollo/adv_health.py --mode            # EIC (0) or UART (1)
    ./scripts/apollo/adv_health.py --uart            # select UART mode: the watchdog poll runs
    ./scripts/apollo/adv_health.py --eic             # back to the power-on default
    ./scripts/apollo/adv_health.py --health          # [ok, crc_fail, timeout, watchdog]; counts clear on read

Vendor request 0xC3 (apollo firmware vendor.c handle_fpga_adv_mode). Notes: docs/chips/cynone-sideband.md
section 14. Log: ./tmp/logs/adv_health.log. Do not run while the board arbiter holds the board.
"""

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "adv_health.log"

APOLLO = (0x1d50, 0x615c)
REQUEST_FPGA_ADV_MODE = 0xC3
W_MODE = 0xFFFF
W_HEALTH = 0xFFFC
MODES = {"eic": 0, "uart": 1}
USB_TIMEOUT_MS = 500        # same as apollo_fpga's control transfers: a local USB request answers in ms


def decode(raw):
    """[ok, crc_fail, timeout, watchdog] -> named fields; watchdog bit 0 link dead, bit 1 CPU dead."""
    if len(raw) != 4:
        raise ValueError(f"expected 4 health bytes, got {len(raw)}")
    ok, crc_fail, timeout, watchdog = raw
    dead = [name for bit, name in ((1, "link dead"), (2, "cpu dead")) if watchdog & bit]
    return {"ok": ok, "crc_fail": crc_fail, "timeout": timeout, "watchdog": watchdog,
            "verdict": ", ".join(dead) or "healthy"}


def open_apollo():
    import usb.core
    device = usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])
    if device is None:
        raise SystemExit("Apollo (1d50:615c) is not on the bus")
    return device


def read(device, value, length):
    return bytes(device.ctrl_transfer(0xC0, REQUEST_FPGA_ADV_MODE, value, 0, length, USB_TIMEOUT_MS))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mode", action="store_true")
    group.add_argument("--uart", action="store_true")
    group.add_argument("--eic", action="store_true")
    group.add_argument("--health", action="store_true")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    device = open_apollo()
    if args.uart or args.eic:
        mode = MODES["uart" if args.uart else "eic"]
        device.ctrl_transfer(0x40, REQUEST_FPGA_ADV_MODE, mode, 0, None, USB_TIMEOUT_MS)
    if args.health:
        logging.info("health: %s", decode(read(device, W_HEALTH, 4)))
    else:
        logging.info("mode: %s", {v: k for k, v in MODES.items()}[read(device, W_MODE, 1)[0]])


if __name__ == "__main__":
    sys.exit(main())
