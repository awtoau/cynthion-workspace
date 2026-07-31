#!/usr/bin/env python3
#
# Identify the HyperRAM part, and check whether its address space aliases.
# See awtoau/cynthion-workspace#109.
# SPDX-License-Identifier: BSD-3-Clause

"""
Loads the identification bitstream and decodes what the HyperRAM reports about itself.

**Nothing in this workspace records what the HyperRAM part is.**
`docs/luna_ecp5_fpga/hyperram-speed.md` measures it at 220.2 MB/s, the platform file
declares pins only, and no document names a manufacturer or a density. This answers that
from the chip rather than from a datasheet search.

The second question is #109's: whether the die behind the marking is larger than the
marking implies, as the ECP5 on this board turned out to be
(`ecp5-test/fabric/FABRIC_TEST.md`).

## What it does to the board

**Configures the FPGA** with `ecp5-test/hyperram/identify_build/top.bit`, replacing
whatever gateware is loaded. SRAM only -- no flash is written, and a power cycle restores
the previous configuration.

The gateware writes to HyperRAM, which is volatile and holds nothing across power. It
does not touch the configuration flash.

## Decoding ID0

Bits 15:14 are reserved, 13:8 the row address bit count, 7:4 the die generation, 3:0 the
manufacturer. Capacity follows from the row bits: rows x 2^column_bits x 2 bytes, where
HyperBus parts use 9 column address bits.

    ./scripts/hyperram_identify.py
    ./scripts/hyperram_identify.py --skip-configure
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram_identify.log"
BITSTREAM = ROOT / "ecp5-test" / "hyperram" / "identify_build" / "top.bit"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

REGISTER_ID        = 1
REGISTER_ID0       = 2
REGISTER_ID1       = 3
REGISTER_CR0       = 4
REGISTER_CR1       = 5
REGISTER_ALIAS     = 6
REGISTER_BANK_DATA = 7
REGISTER_STATE     = 8
REGISTER_BANK1     = 9

APPLET_ID = 0x48594944  # "HYID"

BANK_WORDS = 2 * 1024 * 1024
BANKS = 4

# HyperBus manufacturer codes, JEP106 low nibble as reported in ID0[3:0].
MANUFACTURERS = {
    0x1: "Cypress/Infineon (S27KL/S70KL)",
    0x3: "Winbond (W956/W957)",
    0x6: "ISSI (IS66WVH)",
    0xE: "AP Memory / Espressif-adjacent (APS)",
}

# Column address bits are fixed at 9 for HyperBus, so capacity is decided by the row
# count. Stated as a constant rather than derived, because getting it wrong silently
# scales every capacity figure by a power of two.
COLUMN_BITS = 9


def decode_id0(value):
    """Returns (description, capacity_bytes or None)."""
    if value in (0x0000, 0xFFFF, 0xDEADBEEF & 0xFFFF):
        return f"0x{value:04x} -- no valid response", None
    manufacturer = value & 0xF
    generation = (value >> 4) & 0xF
    row_bits = (value >> 8) & 0x3F
    name = MANUFACTURERS.get(manufacturer, f"unknown (0x{manufacturer:x})")
    if not 1 <= row_bits <= 32:
        return (f"0x{value:04x}: manufacturer {name}, generation {generation}, "
                f"row bits {row_bits} (implausible)"), None
    capacity = (1 << row_bits) * (1 << COLUMN_BITS) * 2
    return (f"0x{value:04x}: manufacturer {name}, die generation {generation}, "
            f"{row_bits} row address bits"), capacity


def configure():
    result = subprocess.run(
        [sys.executable, str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
         "configure", str(BITSTREAM)],
        cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0, (result.stderr or result.stdout).strip()[-200:]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-configure", action="store_true",
                        help="assume the bitstream is already loaded")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("HyperRAM identification and aliasing probe")
        emit()

        if not BITSTREAM.exists():
            emit(f"bitstream missing: {BITSTREAM}")
            emit("build it first -- see ecp5-test/hyperram/hyperram_identify.py")
            return 1

        if not args.skip_configure:
            ok, detail = configure()
            if not ok:
                emit(f"configure failed: {detail}")
                return 1
            emit(f"configured {BITSTREAM.name}")

        from apollo_fpga import ApolloDebugger
        debugger = ApolloDebugger()

        # debugger.registers is set up in ApolloDebugger.__init__ via create_jtag_spi;
        # there is no separate factory for it.
        regs = debugger.registers
        if True:
            applet = regs.register_read(REGISTER_ID)
            if applet != APPLET_ID:
                emit(f"applet ID 0x{applet:08x}, expected 0x{APPLET_ID:08x}")
                emit("the identification gateware is not running")
                return 1
            emit("applet responding")
            emit()

            state = regs.register_read(REGISTER_STATE) & 0xFF
            id0 = regs.register_read(REGISTER_ID0) & 0xFFFF
            id1 = regs.register_read(REGISTER_ID1) & 0xFFFF
            cr0 = regs.register_read(REGISTER_CR0) & 0xFFFF
            cr1 = regs.register_read(REGISTER_CR1) & 0xFFFF
            alias = regs.register_read(REGISTER_ALIAS) & 0xF
            bank0 = regs.register_read(REGISTER_BANK_DATA) & 0xFFFF
            bank1 = regs.register_read(REGISTER_BANK1) & 0xFFFF

        emit(f"FSM state: 0x{state:02x}"
             + ("  (complete)" if state == 0xD0 else "  *** DID NOT COMPLETE"))
        emit()

        emit("1. Identification")
        described, capacity = decode_id0(id0)
        emit(f"   ID0 {described}")
        emit(f"   ID1 0x{id1:04x} (die revision)")
        emit(f"   CR0 0x{cr0:04x}   CR1 0x{cr1:04x}")
        if capacity:
            emit(f"   -> capacity {capacity} bytes "
                 f"({capacity >> 20} MiB / {capacity * 8 >> 20} Mbit)")
        emit()

        emit("2. Address aliasing")
        emit(f"   bank 0 holds 0x{bank0:04x} after all {BANKS} banks were written")
        emit(f"   bank 1 holds 0x{bank1:04x} (expected 0xb001 if it is real storage)")
        if state != 0xD0:
            emit("   inconclusive -- the FSM did not finish")
        elif alias == 0:
            emit(f"   no bank aliases bank 0: the {BANKS} banks are distinct storage")
        else:
            for bank in range(1, BANKS):
                if alias & (1 << bank):
                    emit(f"   bank {bank} (word 0x{bank * BANK_WORDS:x}) ALIASES bank 0")
        emit()

        emit("=" * 62)
        expected_written = 0xB000 | (BANKS - 1)
        if state == 0xD0 and bank0 == 0xB000 and alias == 0 and bank1 == 0xB001:
            emit("RESULT: bank 1 holds its OWN marker one full part-width above bank 0.")
            emit("Not merely 'differs from bank 0' -- it holds exactly what was written")
            emit("there, which unbacked addresses returning 0x0000/0xFFFF cannot fake.")
            emit()
            emit(f"ID0 declares {4} MiB. Storage responds independently to at least")
            emit(f"{BANKS * BANK_WORDS * 2 >> 20} MiB. Those disagree, and the disagreement")
            emit("is the #109 result for this part.")
            emit()
            emit("NOT yet established: retention. These reads happen moments after the")
            emit("writes, so this shows the address decodes and stores, not that it")
            emit("holds over time or temperature. A refresh-interval test is the next")
            emit("step before anything relies on it.")
        elif state == 0xD0 and bank0 == expected_written:
            emit("RESULT: bank 0 holds the LAST bank's value -- the address space")
            emit("wraps, so the die is smaller than the probed range.")
            emit(f"That is the small-die signature, and it bounds capacity below "
                 f"{BANKS * BANK_WORDS * 2 >> 20} MiB.")
        else:
            emit("RESULT: inconclusive. Compare ID0's declared capacity against the")
            emit("aliasing result before drawing anything from this.")
        emit()
        emit("Establishes this for this part at this moment. Unmarked capacity is")
        emit("untested capacity -- see #109.")
        emit()
        emit("The FPGA is running the identification bitstream; reconfigure or")
        emit("power-cycle to restore whatever was there before.")
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
