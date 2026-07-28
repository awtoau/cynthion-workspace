#
# Register map for the PAC1954 bring-up gateware (awtoau/cynthion-workspace#82).
#
# SPDX-License-Identifier: BSD-3-Clause

# Distinct from the selftest applet's 0x54455354 ("TEST") so the host can tell
# which bitstream is actually loaded.
APPLET_ID = 0x504D4F4E  # "PMON"

# JTAG register addresses. Register 0 is reserved by JTAGRegisterInterface for
# size auto-negotiation, so the map starts at 1.
REGISTER_ID            = 1
REGISTER_DEV_ADDRESS   = 2  # I2C device address (7-bit), host-writable
REGISTER_REG_ADDRESS   = 3  # PAC195X register to read
REGISTER_READ_TRIGGER  = 4  # write anything to start a read
REGISTER_READ_DATA     = 5  # latched result of the last completed read
REGISTER_STATUS        = 6  # bit 0: done, bit 1: i2c busy

STATUS_DONE = 0b01
STATUS_BUSY = 0b10

# CONFIRMED on Cynthion r1.4 hardware, 2026-07-28: the device answers at 0x10
# and no other candidate responds, i.e. ADDRSEL is strapped to GND (0R) per
# DS20006539B Table 6-1. Verified by MANUFACTURER_ID=0x54, PRODUCT_ID=0x7B
# (PAC1954-1), REVISION_ID=0x02.
CONFIRMED_ADDRESS_R1_4 = 0x10

# Full candidate list from Table 6-1, kept so the scan still works on boards
# with a different strap. The address is latched at power-up and cannot be
# changed at runtime.
CANDIDATE_ADDRESSES = list(range(0x10, 0x1F))

ADDRESS_RESISTORS = {
    0x10: "0R (GND)",
    0x11: "499R",
    0x12: "806R",
    0x13: "1.27k",
    0x14: "2.05k",
    0x15: "3.24k",
    0x16: "5.23k",
    0x17: "8.45k",
    0x18: "13.3k",
    0x19: "21.5k",
    0x1A: "34.0k",
    0x1B: "54.9k",
    0x1C: "88.7k",
    0x1D: "140k",
    0x1E: "226k",
}

# PAC195X identification registers (DS20006539B). Reading these back with the
# expected values is the check that proves the I2C link before any measurement
# is trusted.
REG_PRODUCT_ID      = 0xFD
REG_MANUFACTURER_ID = 0xFE
REG_REVISION_ID     = 0xFF

EXPECTED_MANUFACTURER_ID = 0x54  # Microchip (DS20006539B, Register 7-38)
EXPECTED_REVISION_ID     = 0x02  # POR value per the register summary

# Product ID identifies the family member (Register 7-37). The schematic part
# is PAC195X-1-VQFN and the board monitors four rails, so PAC1954-1 (0x7B) is
# expected -- but the scan reports whatever it actually reads, so a different
# variant shows up as a mismatch rather than a silent wrong assumption.
PRODUCT_IDS = {
    0x78: "PAC1951-1",
    0x79: "PAC1952-1",
    0x7A: "PAC1953-1",
    0x7B: "PAC1954-1",
    0x7C: "PAC1951-2",
    0x7D: "PAC1952-2",
}
EXPECTED_PRODUCT_ID = 0x7B  # PAC1954-1
