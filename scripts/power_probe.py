#!/usr/bin/env python3
#
# Host-side driver for the PAC1954 bring-up gateware.
# See awtoau/cynthion-workspace#82.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Sweeps the PAC195X I2C address candidates over the Apollo debug link and
reports which one responds, then reads the identification registers.

The device's address is fixed by a resistor to ground on ADDRSEL and latched at
power-up (DS20006539B Table 6-1); which resistor is fitted on r1.4 is not
documented, so this discovers it rather than assuming.

Usage:
    ./scripts/power_probe.py                 # scan, then identify
    ./scripts/power_probe.py --address 0x10  # skip the scan
    ./scripts/power_probe.py --read 0xFE     # read one register

Requires the power_monitor bitstream to be loaded:
    apollo configure ecp5-test/power_monitor/build/top.bit
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))

from power_monitor.registers import (
    APPLET_ID, CANDIDATE_ADDRESSES, ADDRESS_RESISTORS,
    REGISTER_ID, REGISTER_DEV_ADDRESS, REGISTER_REG_ADDRESS,
    REGISTER_READ_TRIGGER, REGISTER_READ_DATA, REGISTER_STATUS,
    STATUS_DONE,
    REG_PRODUCT_ID, REG_MANUFACTURER_ID, REG_REVISION_ID,
    EXPECTED_MANUFACTURER_ID, EXPECTED_PRODUCT_ID, EXPECTED_REVISION_ID,
    PRODUCT_IDS,
)

from apollo_fpga import ApolloDebugger

LOG = ROOT / "tmp" / "power_probe.log"

# A completed I2C byte transfer at 100 kHz is ~100 us. Poll a bounded number of
# times rather than sleeping: if the device NAKs, done never asserts, and the
# loop must terminate so a missing device reads as "no response" not a hang.
POLL_ATTEMPTS = 200


class Probe:
    def __init__(self, registers):
        self.regs = registers

    def read_pac_register(self, dev_address, reg_address):
        """ Read one PAC195X register. Returns None if the device did not respond. """
        self.regs.register_write(REGISTER_DEV_ADDRESS, dev_address)
        self.regs.register_write(REGISTER_REG_ADDRESS, reg_address)
        self.regs.register_write(REGISTER_READ_TRIGGER, 1)

        for _ in range(POLL_ATTEMPTS):
            if self.regs.register_read(REGISTER_STATUS) & STATUS_DONE:
                return self.regs.register_read(REGISTER_READ_DATA) & 0xFF
        return None


def emit(handle, text=""):
    print(text)
    handle.write(text + "\n")
    handle.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--address", type=lambda v: int(v, 0), default=None,
                        help="skip the scan and use this 7-bit address")
    parser.add_argument("--read", type=lambda v: int(v, 0), default=None,
                        help="read a single PAC195X register and exit")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as log:
        device = ApolloDebugger()
        regs = device.registers

        applet = regs.register_read(REGISTER_ID)
        emit(log, f"applet ID: 0x{applet:08X}")
        if applet != APPLET_ID:
            emit(log, f"  ERROR: expected 0x{APPLET_ID:08X} -- is the "
                      f"power_monitor bitstream loaded?")
            return 1
        emit(log)

        probe = Probe(regs)

        if args.read is not None:
            address = args.address or CANDIDATE_ADDRESSES[0]
            value = probe.read_pac_register(address, args.read)
            emit(log, f"[0x{address:02X}] reg 0x{args.read:02X} = "
                      + (f"0x{value:02X}" if value is not None else "no response"))
            return 0 if value is not None else 1

        # Discover the address.
        if args.address is not None:
            found = [args.address]
            emit(log, f"using address 0x{args.address:02X} (scan skipped)")
        else:
            emit(log, "scanning Table 6-1 address candidates...")
            found = []
            for address in CANDIDATE_ADDRESSES:
                value = probe.read_pac_register(address, REG_MANUFACTURER_ID)
                resistor = ADDRESS_RESISTORS.get(address, "?")
                if value is None:
                    emit(log, f"  0x{address:02X} ({resistor:>9}): --")
                else:
                    emit(log, f"  0x{address:02X} ({resistor:>9}): "
                              f"responded, MANUFACTURER_ID=0x{value:02X}")
                    found.append(address)

        emit(log)
        if not found:
            emit(log, "FAIL: no device responded on any candidate address.")
            emit(log, "  Check PWRDN is de-asserted and the bus has pull-ups.")
            return 1
        if len(found) > 1:
            emit(log, f"WARNING: {len(found)} addresses responded: "
                      + ", ".join(f"0x{a:02X}" for a in found))

        address = found[0]
        emit(log, f"identifying device at 0x{address:02X} "
                  f"(ADDRSEL resistor {ADDRESS_RESISTORS.get(address, '?')})")

        checks = [
            ("MANUFACTURER_ID", REG_MANUFACTURER_ID, EXPECTED_MANUFACTURER_ID),
            ("PRODUCT_ID",      REG_PRODUCT_ID,      EXPECTED_PRODUCT_ID),
            ("REVISION_ID",     REG_REVISION_ID,     EXPECTED_REVISION_ID),
        ]

        failures = 0
        for name, reg, expected in checks:
            value = probe.read_pac_register(address, reg)
            if value is None:
                emit(log, f"  {name:<16} no response")
                failures += 1
                continue
            note = ""
            if name == "PRODUCT_ID":
                note = f"  ({PRODUCT_IDS.get(value, 'unknown variant')})"
            if value == expected:
                emit(log, f"  {name:<16} 0x{value:02X}  OK{note}")
            else:
                emit(log, f"  {name:<16} 0x{value:02X}  MISMATCH "
                          f"(expected 0x{expected:02X}){note}")
                failures += 1

        emit(log)
        if failures:
            emit(log, f"FAIL: {failures} identification check(s) failed.")
            return 1
        emit(log, f"PASS: PAC1954 identified at 0x{address:02X}.")
        emit(log, f"Record this address in the r1.4 platform docs (#82).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
