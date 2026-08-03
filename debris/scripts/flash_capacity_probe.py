#!/usr/bin/env python3
#
# Ask whether the configuration flash carries more silicon than it is marked.
# See awtoau/cynthion-workspace#109.
# SPDX-License-Identifier: BSD-3-Clause

"""
Probes the W25Q32 for storage above its marked 4 MiB capacity.

The ECP5 on this board places and routes 20,143 of 24,288 LUT4s on a part marked 12F
(#116), so the marking is not the whole story there. SPI
NOR is a family where the same question is cheap to ask: **capacity is a single byte of
the JEDEC ID**. `EF 40 16` decodes as Winbond / type 0x40 / 2^22 bytes, and whether the
die behind that byte stops at 4 MiB is a separate question from what the byte says.

## What this does to the board

**Nothing. Every operation here is a read.** No erase, no program, no write-enable. The
bitstream at offset 0 is not touched, and the flash is left exactly as found.

The one intrusive step is `force_fpga_offline()`, which the existing `apollo flash-info`
also does and which is required before anything can drive the configuration SPI lines --
a configured FPGA holds them. The FPGA is offline afterwards; reconfigure it if you want
it back.

## The three questions, in order of strength

1. **SFDP** (opcode 0x5A) is the strong one. It is a JEDEC-standard parameter table the
   die itself publishes, and it declares density independently of the ID byte. A part
   whose SFDP density disagrees with its ID byte is interesting on its own.

2. **Reads above 4 MiB.** A 24-bit address space tops out at 16 MiB, so a 4 MiB part has
   12 MiB of addresses that must resolve somehow. Three outcomes and they mean different
   things:

   - *aliases* -- address bits above the die are ignored, the classic small-die
     signature. Data at `addr` and `addr + 4 MiB` are identical.
   - *returns 0xFF* -- reads off the end of the array, or a smaller die that returns
     erased state.
   - *returns distinct non-erased data* -- the interesting case, and the only one that
     suggests real storage.

3. **A read past 16 MiB** using 4-byte addressing, which the part is not supposed to
   support. Included because a negative here is worth as much as a positive: it
   distinguishes "the addressing mode is absent" from "the storage is absent".

Aliasing is checked with the bitstream itself as the pattern. Offset 0 holds real,
non-uniform data, which makes it a far better comparison than an erased region -- two
regions of `0xFF` are trivially "identical" and prove nothing. That is the trap this
script is written to avoid.

    ./scripts/flash_capacity_probe.py
    ./scripts/flash_capacity_probe.py --length 256
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "flash_capacity_probe.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

# Marked capacity, from JEDEC ID EF 40 16: the 0x16 is a capacity code, 2^22 bytes.
MARKED_CAPACITY = 4 * 1024 * 1024

# Opcodes the Apollo programmer does not already wrap.
OP_READ_SFDP = 0x5A
OP_READ_DATA = 0x03          # 3-byte address, so it cannot reach past 16 MiB
OP_READ_DATA_4B = 0x13       # 4-byte address; unsupported on a W25Q32, which is the point
OP_READ_STATUS_3 = 0x15      # holds ADS/ADP on parts that have 4-byte addressing


def spi(programmer, payload, response_offset):
    """One background SPI transaction. Returns the response after the sent bytes."""
    raw = programmer._background_spi_transfer(list(payload))
    return bytes(raw[response_offset:])


def read_at(programmer, address, length, four_byte=False):
    """Read `length` bytes at `address`. Read-only."""
    if four_byte:
        header = [OP_READ_DATA_4B,
                  (address >> 24) & 0xFF, (address >> 16) & 0xFF,
                  (address >> 8) & 0xFF, address & 0xFF]
    else:
        header = [OP_READ_DATA,
                  (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF]
    return spi(programmer, header + [0] * length, len(header))


def decode_sfdp(table):
    """Pull the density out of an SFDP basic parameter table, if it looks valid."""
    if len(table) < 16 or table[0:4] != b"SFDP":
        return None, "no SFDP signature"

    # The basic table's first parameter header sits at byte 8; its pointer is at +12.
    ptr = int.from_bytes(table[12:15], "little")
    words = table[ptr:ptr + 64]
    if len(words) < 8:
        return None, f"parameter table at 0x{ptr:x} not readable in this dump"

    # DWORD 2 (bytes 4..8) is the flash density.
    density = int.from_bytes(words[4:8], "little")
    if density & (1 << 31):
        # Bit 31 set: the lower bits are log2 of the density in BITS.
        bits = 1 << (density & 0x7FFFFFFF)
    else:
        bits = density + 1
    return bits // 8, None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--length", type=int, default=64,
                        help="bytes to compare per probe point (default 64)")
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("Flash capacity probe -- READ ONLY, nothing is written or erased")
        emit(f"marked capacity: {MARKED_CAPACITY} bytes ({MARKED_CAPACITY >> 20} MiB)")
        emit()

        debugger = ApolloDebugger()
        # Required before anything can drive the configuration SPI lines: a configured
        # FPGA holds them. `apollo flash-info` does the same thing.
        debugger.force_fpga_offline()

        with debugger.jtag as jtag:
            programmer = debugger.create_jtag_programmer(jtag)

            manufacturer, full_id = programmer.read_flash_id()
            emit(f"JEDEC ID: 0x{full_id:06x}  (manufacturer 0x{manufacturer:02x})")
            capacity_code = full_id & 0xFF
            emit(f"  capacity code 0x{capacity_code:02x} -> "
                 f"{1 << capacity_code} bytes if read as 2^n")
            emit()

            # 1. SFDP -- the die's own declaration.
            emit("1. SFDP (the die's own density declaration)")
            programmer._enter_background_spi()
            sfdp = spi(programmer,
                       [OP_READ_SFDP, 0, 0, 0, 0] + [0] * 256, 5)
            declared, why = decode_sfdp(sfdp)
            if declared is None:
                emit(f"   unavailable: {why}")
                emit(f"   first bytes: {sfdp[:16].hex()}")
            else:
                emit(f"   declares {declared} bytes ({declared >> 20} MiB)")
                if declared != MARKED_CAPACITY:
                    emit(f"   *** DISAGREES with the marked {MARKED_CAPACITY >> 20} MiB")
                else:
                    emit("   agrees with the marking")
            emit()

            # 2. Reads above the marked capacity, compared against offset 0.
            #
            # Offset 0 holds the bitstream: real, non-uniform data. Comparing against an
            # erased region would make any two 0xFF blocks look "identical" and prove
            # nothing.
            emit(f"2. Reads above {MARKED_CAPACITY >> 20} MiB, compared with offset 0")
            programmer._enter_background_spi()
            base = read_at(programmer, 0, args.length)
            emit(f"   offset 0x000000: {base[:16].hex()}"
                 f"{'  (erased -- a poor reference)' if base[:16] == b'\xff' * 16 else ''}")

            findings = []
            for multiple in (1, 2, 3):
                address = MARKED_CAPACITY * multiple
                programmer._enter_background_spi()
                data = read_at(programmer, address, args.length)
                if data == base:
                    verdict = "ALIASES offset 0 -- small-die signature"
                elif set(data) == {0xFF}:
                    verdict = "all 0xFF -- erased or off the end of the array"
                elif set(data) == {0x00}:
                    verdict = "all 0x00 -- no responder"
                else:
                    verdict = "*** DISTINCT non-erased data"
                    findings.append(address)
                emit(f"   offset 0x{address:06x}: {data[:16].hex()}  {verdict}")
            emit()

            # 3. Past the 24-bit ceiling, which needs an addressing mode this part
            #    should not have. A negative is informative: it separates "no addressing
            #    mode" from "no storage".
            emit("3. Past 16 MiB, via 4-byte addressing (not expected to work)")
            programmer._enter_background_spi()
            status3 = spi(programmer, [OP_READ_STATUS_3, 0], 1)
            emit(f"   status register 3: 0x{status3[0]:02x} "
                 f"(ADS bit {'set' if status3[0] & 0x01 else 'clear'})")
            programmer._enter_background_spi()
            beyond = read_at(programmer, 16 * 1024 * 1024, args.length, four_byte=True)
            if set(beyond) in ({0xFF}, {0x00}):
                emit(f"   0x1000000: {beyond[:16].hex()}  "
                     f"no response -- 4-byte addressing absent, as expected")
            else:
                emit(f"   0x1000000: {beyond[:16].hex()}  *** SOMETHING RESPONDED")
                findings.append(16 * 1024 * 1024)
            emit()

        emit("=" * 62)
        if findings:
            emit("RESULT: distinct data above the marked capacity at "
                 + ", ".join(f"0x{a:06x}" for a in findings))
            emit()
            emit("That is necessary but NOT sufficient. Distinct-looking data can be")
            emit("floating lines or an unclocked bus rather than storage. Proving")
            emit("storage needs a write and a power cycle, which is destructive and")
            emit("is deliberately not done here -- see #109.")
        else:
            emit("RESULT: no evidence of storage above the marked capacity.")
            emit()
            emit("Establishes this for this part at this moment, on reads alone. It")
            emit("does not rule out storage reachable through a vendor-specific")
            emit("command this probe does not issue.")
        emit()
        emit("The FPGA was forced offline to reach the flash and is still offline.")
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
