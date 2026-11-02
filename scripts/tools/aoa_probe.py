#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""AOA accessory handshake from the PC: the byte sequence the Cynthion must reproduce.

    ./scripts/tools/aoa_probe.py --strings 'MAKER,MODEL,DESCRIPTION,VERSION,URI,SERIAL'
    ./scripts/tools/aoa_probe.py --vid 18d1 --pid 4ee1 --strings ...

- Plays a generic accessory (USB host); the phone is the device. Sequence: GET_PROTOCOL (51),
  SEND_STRING x6 (52), START (53), wait for `18d1:2d00/2d01`, list its bulk endpoints.
- No vendor identity here. The DJI RC (T600) identity and behaviour live in awto-dji
  (`lib/transport/aoa_identity.dart`, its RC emulator under scripts/re/).
- Does not claim or read the bulk pipe. The phone shows an "Allow ... to access" dialog;
  accept it with `adb shell input tap` or replug (docs/aoa-adaptor.md).
- Log: tmp/logs/aoa_probe.log.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import usb.core

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "tmp" / "logs" / "aoa_probe.log"

GOOGLE_VID = 0x18D1
ACCESSORY_PIDS = (0x2D00, 0x2D01)
REQ_GET_PROTOCOL, REQ_SEND_STRING, REQ_START = 51, 52, 53
STRING_FIELDS = 6       # manufacturer, model, description, version, uri, serial

# Phone drops and re-enumerates as an accessory: 4.0 s and >5 s on the Pixel 3 XL
# (tmp/logs/aoa-probe.log, 2026-11-02); 8 s is ~1.25x a 6.5 s worst case.
# Poll every 100 ms: enumeration is not faster.
REATTACH_LIMIT_S = 8.0
POLL_S = 0.1

log = logging.getLogger("aoa_probe")


def find_phone(vid, pid):
    for dev in usb.core.find(find_all=True, idVendor=vid):
        if pid is not None and dev.idProduct != pid:
            continue
        return dev
    return None


def find_accessory(vid):
    for pid in ACCESSORY_PIDS:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is not None:
            return dev
    return None


def switch(dev, strings):
    version = dev.ctrl_transfer(0xC0, REQ_GET_PROTOCOL, 0, 0, 2)
    protocol = version[0] | version[1] << 8
    log.info("GET_PROTOCOL -> %d", protocol)
    if protocol < 1:
        raise SystemExit("device does not support AOA")
    for index, text in enumerate(strings):
        dev.ctrl_transfer(0x40, REQ_SEND_STRING, 0, index, text.encode() + b"\0")
        log.info("SEND_STRING %d = %r", index, text)
    try:
        dev.ctrl_transfer(0x40, REQ_START, 0, 0, None)
    except usb.core.USBError as error:
        # The phone drops off the bus mid-request: expected.
        log.info("START: device detached (%s)", error)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vid", type=lambda s: int(s, 16), default=GOOGLE_VID)
    parser.add_argument("--pid", type=lambda s: int(s, 16))
    parser.add_argument("--strings", required=True,
                        help="six comma-separated AOA strings: manufacturer, model, "
                             "description, version, uri, serial")
    args = parser.parse_args()
    strings = args.strings.split(",")
    if len(strings) != STRING_FIELDS:
        parser.error(f"--strings needs {STRING_FIELDS} comma-separated values")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOG)])

    started = time.monotonic()
    accessory = find_accessory(args.vid)
    if accessory is None:
        phone = find_phone(args.vid, args.pid)
        if phone is None:
            log.error("no %04x device on this PC's USB: is the phone on a PC port?", args.vid)
            return 2
        log.info("phone %04x:%04x", phone.idVendor, phone.idProduct)
        switch(phone, strings)
        started = time.monotonic()      # the wait is measured from START
        while time.monotonic() - started < REATTACH_LIMIT_S:
            accessory = find_accessory(args.vid)
            if accessory is not None:
                break
            time.sleep(POLL_S)
    if accessory is None:
        log.error("no accessory device within %.1f s of START", REATTACH_LIMIT_S)
        return 1
    log.info("accessory %04x:%04x after %.2f s", accessory.idVendor, accessory.idProduct,
             time.monotonic() - started)
    for config in accessory:
        for interface in config:
            for endpoint in interface:
                log.info("  if %d ep 0x%02x attrs 0x%02x max %d", interface.bInterfaceNumber,
                         endpoint.bEndpointAddress, endpoint.bmAttributes,
                         endpoint.wMaxPacketSize)
    return 0


if __name__ == "__main__":
    sys.exit(main())
