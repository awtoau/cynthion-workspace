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
REGISTER_READ_DATA     = 5  # latched result of the last completed read (16-bit)
REGISTER_STATUS        = 6  # bit 0: done, bit 1: i2c busy
REGISTER_READ_SIZE     = 7  # bytes per read: 1 for ID registers, 2 for measurements

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

# Measurement registers. All 16-bit, one per channel (DS20006539B Reg 7-5..7-9).
REG_REFRESH   = 0x00  # Send Byte: latch a coherent sample set before reading
REG_VBUS_BASE = 0x07  # VBUSn   07h-0Ah
REG_VSENSE_BASE = 0x0B  # VSENSEn 0Bh-0Eh
REG_VBUS_AVG_BASE = 0x0F    # VBUSn_AVG   0Fh-12h (8x averaged)
REG_VSENSE_AVG_BASE = 0x13  # VSENSEn_AVG 13h-16h
REG_VPOWER_BASE = 0x17      # VPOWERn 17h-1Ah, 32-bit

#
# Channel -> physical port mapping.
#
# Derived from the CLEAN r1.4.0 schematic tag (not the in-progress Coppelia
# branch) by matching PAC SENSEn pin coordinates to the global labels on the
# power_distribution sheet, then CONFIRMED against hardware: with the board
# attached via CONTROL and AUX only, channels 3 and 4 read ~5.2 V while
# channels 1 and 2 read zero.
#
# Note this is NOT the obvious ordering -- channel 1 is TARGET_A, not CONTROL.
#
CHANNEL_PORTS = {
    1: "TARGET_A",
    2: "TARGET_C",
    3: "AUX",
    4: "CONTROL",
}

#
# Real-world scaling.
#
# VBUS:   0-32 V FSR, 16-bit  -> 32/65536   = 488.3 uV per LSB
# VSENSE: 0-100 mV FSR, 16-bit -> 0.1/65536 = 1.526 uV per LSB
# Sense resistors are 0.02 ohm 1% (R1, R2, R42, R59 on the r1.4.0 sheet), so
# 1.526 uV / 20 mohm = 76.3 uA per LSB, and full scale is 100 mV / 20 mohm = 5 A.
#
VBUS_FSR_VOLTS    = 32.0
VSENSE_FSR_VOLTS  = 0.100
SENSE_RESISTOR_OHMS = 0.020
ADC_COUNTS        = 65536

VBUS_VOLTS_PER_LSB = VBUS_FSR_VOLTS / ADC_COUNTS            # 488.3 uV
VSENSE_VOLTS_PER_LSB = VSENSE_FSR_VOLTS / ADC_COUNTS        # 1.526 uV
AMPS_PER_LSB = VSENSE_VOLTS_PER_LSB / SENSE_RESISTOR_OHMS   # 76.3 uA
MAX_CURRENT_AMPS = VSENSE_FSR_VOLTS / SENSE_RESISTOR_OHMS   # 5 A


def raw_to_volts(raw):
    """Convert a 16-bit VBUS reading to volts."""
    return raw * VBUS_VOLTS_PER_LSB


def raw_to_amps(raw):
    """Convert a 16-bit VSENSE reading to amps, given the 20 mohm shunt."""
    return raw * AMPS_PER_LSB

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
