#!/usr/bin/env python3
#
# Write, list and verify partitions in the Cynthion configuration flash.
# SPDX-License-Identifier: BSD-3-Clause

"""
Partition tool for the 4 MiB W25Q32 configuration flash on Cynthion r1.4.

Layout and rationale: docs/luna_ecp5_fpga/flash-partitioning.md. In short --
slot 0 holds a permanent loader and is never erased, slots 1..7 hold test
images, and a table in the last 4 KiB sector records each slot's **explicit
image length**. Length is never inferred from where the erased region begins:
a smaller bitstream written over a larger one leaves the tail behind, and
scanning back for the first non-0xff byte reports the wrong size. That has
already caused one misdiagnosis on this board (248515 bytes reported where
100336 were written).

The ECP5 does not read this table -- it cannot. Boot selection is done by the
BOOTADDR fuses in the *running* bitstream, set at build time by
`ecppack --bootaddr`. The table is bookkeeping for the host and the loader.

    ./scripts/flashparts.py list
    ./scripts/flashparts.py init
    ./scripts/flashparts.py write 3 tmp/build/top.bit --label blinky
    ./scripts/flashparts.py verify
    ./scripts/flashparts.py verify --slot 3
    ./scripts/flashparts.py dump-table

Every destructive operation refuses to touch slot 0 unless --force is given,
and refuses to run at all unless a full-chip backup exists.
"""

import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

from usb.core import USBError

LOG = ROOT / "tmp" / "logs" / "flashparts.log"

FLASH_SIZE = 4 * 1024 * 1024
SECTOR = 4096
BLOCK = 64 * 1024
PAGE = 256

TABLE_ADDR = 0x3FF000
TABLE_SHADOW = 0x3FF800
TABLE_SPAN = 0x800

MAGIC = b"CYNPART1"
VERSION = 1
HEADER_SIZE = 0x40
ENTRY_SIZE = 0x20
MAX_ENTRIES = 8

FLAG_VALID = 1 << 0
FLAG_LOCKED = 1 << 1
FLAG_BOOTADDR = 1 << 2

# Slot 0 is the loader and is locked. Slots are 512 KiB, which fits every
# bitstream measured in this workspace (99963 to 402957 bytes) with headroom.
SLOTS = [(i * 0x80000, 0x80000) for i in range(7)] + [(0x380000, 0x70000)]

BACKUP = ROOT / "tmp" / "flashbackup" / "full-4MiB-verified.bin"


def emit(handle, text=""):
    print(text, flush=True)
    if handle:
        handle.write(text + "\n")
        handle.flush()


# ---------------------------------------------------------------- device ---

def wait_for_usb(vid=0x1D50, pid=0x615C, attempts=6000):
    """Wait for the Apollo VID:PID, then reset the port.

    The port reset is what clears `[Errno 32] Pipe error`, which the link
    throws after sustained background SPI. Reconstructing ApolloDebugger alone
    does not clear it; `emergency_reset()` does not either.
    """
    import usb.core

    def present():
        for _ in range(attempts):
            dev = usb.core.find(idVendor=vid, idProduct=pid)
            if dev is not None:
                return dev
        return None

    dev = present()
    if dev is None:
        return False
    try:
        dev.reset()
    except USBError:
        pass
    # reset() drops the device off the bus briefly; wait for it to return
    # before handing it to ApolloDebugger or construction races it.
    return present() is not None


def open_device(attempts=40):
    """Open a debugger with the FPGA held offline.

    `force_offline=True` must be passed at construction -- calling
    `force_fpga_offline()` afterwards is not equivalent and leaves the running
    gateware owning the SPI lines. A port reset sometimes lands the board in
    DFU; `exit_dfu()` brings it back.
    """
    from apollo_fpga import ApolloDebugger, DebuggerNotFound

    for remaining in range(attempts, 0, -1):
        wait_for_usb()
        try:
            return ApolloDebugger(force_offline=True)
        except (DebuggerNotFound, USBError):
            try:
                ApolloDebugger.exit_dfu()
            except Exception:
                pass
            if remaining == 1:
                raise
    raise DebuggerNotFound("device did not come back")


def read_flash(dut, offset, length):
    """Read `length` bytes, retrying the failure modes this board shows.

    `unconfigure()` runs in its own JTAG context and the read in a second one.
    Both inside a single context makes the flash read back all-0xff, and
    read_flash then raises "Flash does not seem correctly connected" on a board
    `flash-info` reports as healthy.
    """
    def once():
        with dut.jtag as jtag:
            dut.create_jtag_programmer(jtag).unconfigure()
        with dut.jtag as jtag:
            programmer = dut.create_jtag_programmer(jtag)
            return bytes(programmer.read_flash(length, offset=offset))

    for _ in range(12):
        try:
            data = once()
        except (USBError, IOError):
            dut = open_device()
            continue
        if not looks_echoed(data, offset):
            return data, dut
    raise IOError(f"could not read 0x{offset:06x}+{length} cleanly")


def looks_echoed(data, offset):
    """Detect the opcode-echo corruption in long background-SPI reads.

    Past roughly 1.9 MB of a single sustained read, pages come back as
    `03 <addr> 00 00 ...` -- the READ_PAGE opcode and the page's own address
    instead of data. Silent, and it looks like real content.
    """
    for page in range(0, len(data), PAGE):
        addr = offset + page
        if data[page:page + 1] == b"\x03" and \
           data[page + 1:page + 4] == addr.to_bytes(3, "big"):
            return True
    return False


def write_flash(dut, offset, data):
    """Erase exactly the range being written, then program it.

    `ECP5_JTAGProgrammer.flash()` calls `_flash_erase(offset, len(data))`,
    which issues 64K/32K/4K erases covering only that range -- not a chip
    erase. That containment is what makes a slot-scoped write safe for the
    loader in slot 0. `erase_flash()` (no underscore) IS a chip erase; do not
    confuse the two.

    Note the erase is rounded *down* to a 4 KiB boundary, so an offset must be
    4 KiB aligned or the erase reaches backwards into the preceding slot. Every
    offset this tool uses is 64 KiB or 4 KiB aligned.
    """
    if offset % SECTOR:
        raise ValueError(f"offset 0x{offset:06x} is not 4 KiB aligned; "
                         f"erase would reach into the previous slot")

    with dut.jtag as jtag:
        dut.create_jtag_programmer(jtag).unconfigure()
    with dut.jtag as jtag:
        programmer = dut.create_jtag_programmer(jtag)
        programmer.flash(data, offset=offset)


# ----------------------------------------------------------------- table ---

def pack_table(entries, sequence):
    body = bytearray(b"\xff" * (HEADER_SIZE + ENTRY_SIZE * MAX_ENTRIES))
    for i, e in enumerate(entries[:MAX_ENTRIES]):
        struct.pack_into(
            "<IIIIHH12s", body, HEADER_SIZE + i * ENTRY_SIZE,
            e["start"], e["size"], e["length"], e["crc"],
            e["flags"], e["bootaddr"] >> 16,
            e["label"].encode("ascii", "replace")[:12])

    entries_region = bytes(body[HEADER_SIZE:])
    struct.pack_into("<8sHHIII", body, 0,
                     MAGIC, VERSION, min(len(entries), MAX_ENTRIES),
                     FLASH_SIZE, zlib.crc32(entries_region), sequence)
    return bytes(body)


def unpack_table(raw):
    """Parse a table image, or return None if it is absent or corrupt."""
    if len(raw) < HEADER_SIZE or raw[:8] != MAGIC:
        return None
    magic, version, count, size, crc, sequence = struct.unpack_from(
        "<8sHHIII", raw, 0)
    if version != VERSION or count > MAX_ENTRIES:
        return None

    entries_region = raw[HEADER_SIZE:HEADER_SIZE + ENTRY_SIZE * MAX_ENTRIES]
    if zlib.crc32(entries_region) != crc:
        return None

    entries = []
    for i in range(count):
        start, slot_size, length, ecrc, flags, boothi, label = \
            struct.unpack_from("<IIIIHH12s", raw,
                               HEADER_SIZE + i * ENTRY_SIZE)
        entries.append({
            "start": start, "size": slot_size, "length": length,
            "crc": ecrc, "flags": flags, "bootaddr": boothi << 16,
            "label": label.rstrip(b"\x00\xff").decode("ascii", "replace"),
        })
    return {"sequence": sequence, "flash_size": size, "entries": entries}


def load_table(dut):
    """Read both copies, return the newer intact one and the device."""
    raw, dut = read_flash(dut, TABLE_ADDR, TABLE_SPAN * 2)
    primary = unpack_table(raw[:TABLE_SPAN])
    shadow = unpack_table(raw[TABLE_SPAN:])

    candidates = [t for t in (primary, shadow) if t]
    if not candidates:
        return None, dut
    return max(candidates, key=lambda t: t["sequence"]), dut


def default_entries():
    entries = []
    for i, (start, size) in enumerate(SLOTS):
        entries.append({
            "start": start, "size": size, "length": 0, "crc": 0,
            "flags": FLAG_LOCKED if i == 0 else 0,
            "bootaddr": 0,
            "label": "loader" if i == 0 else "",
        })
    return entries


# -------------------------------------------------------------- commands ---

def require_backup(handle):
    if not BACKUP.is_file() or BACKUP.stat().st_size != FLASH_SIZE:
        emit(handle, f"refusing to write: no verified {FLASH_SIZE}-byte backup "
                     f"at {BACKUP}")
        emit(handle, "run ./scripts/flash_backup.py first")
        return False
    return True


def cmd_list(args, handle):
    dut = open_device()
    table, dut = load_table(dut)
    if table is None:
        emit(handle, "no partition table found -- run `init`")
        return 1

    emit(handle, f"table sequence {table['sequence']}, "
                 f"flash {table['flash_size']} bytes")
    emit(handle)
    emit(handle, f"  {'slot':>4} {'start':>9} {'size':>9} {'length':>9} "
                 f"{'crc32':>10}  {'flags':<14} label")
    for i, e in enumerate(table["entries"]):
        flags = []
        if e["flags"] & FLAG_VALID:
            flags.append("valid")
        if e["flags"] & FLAG_LOCKED:
            flags.append("locked")
        if e["flags"] & FLAG_BOOTADDR:
            flags.append(f"boot=0x{e['bootaddr']:06x}")
        emit(handle, f"  {i:>4} 0x{e['start']:06x}  {e['size']:>8} "
                     f"{e['length']:>9} 0x{e['crc']:08x}  "
                     f"{','.join(flags) or '-':<14} {e['label']}")
    return 0


def cmd_init(args, handle):
    if not require_backup(handle):
        return 1

    dut = open_device()
    existing, dut = load_table(dut)
    if existing and not args.force:
        emit(handle, f"table already present (sequence {existing['sequence']})"
                     f" -- use --force to overwrite")
        return 1

    sequence = (existing["sequence"] + 1) if existing else 1
    body = pack_table(default_entries(), sequence)
    image = bytearray(b"\xff" * SECTOR)
    image[0:len(body)] = body
    image[TABLE_SPAN:TABLE_SPAN + len(body)] = body

    write_flash(dut, TABLE_ADDR, bytes(image))
    emit(handle, f"wrote table at 0x{TABLE_ADDR:06x} "
                 f"(+ shadow at 0x{TABLE_SHADOW:06x}), sequence {sequence}")
    emit(handle, f"{len(SLOTS)} slots, slot 0 locked as loader")
    return 0


def cmd_write(args, handle):
    if not require_backup(handle):
        return 1

    image = Path(args.image).read_bytes()
    slot = args.slot
    if slot < 0 or slot >= len(SLOTS):
        emit(handle, f"slot {slot} out of range 0..{len(SLOTS) - 1}")
        return 1

    start, size = SLOTS[slot]
    if len(image) > size:
        emit(handle, f"image is {len(image)} bytes, slot {slot} holds {size}")
        return 1

    dut = open_device()
    table, dut = load_table(dut)
    if table is None:
        emit(handle, "no partition table -- run `init` first")
        return 1

    entry = table["entries"][slot]
    if entry["flags"] & FLAG_LOCKED and not args.force:
        emit(handle, f"slot {slot} is locked ({entry['label']}) -- "
                     f"refusing without --force")
        emit(handle, "this is the loader; erasing it removes the recovery path")
        return 1

    emit(handle, f"writing {len(image)} bytes to slot {slot} "
                 f"at 0x{start:06x}")
    write_flash(dut, start, image)

    entry.update({
        "length": len(image),
        "crc": zlib.crc32(image),
        "flags": (entry["flags"] | FLAG_VALID),
        "label": args.label or Path(args.image).stem[:12],
    })

    sequence = table["sequence"] + 1
    body = pack_table(table["entries"], sequence)
    image_out = bytearray(b"\xff" * SECTOR)
    image_out[0:len(body)] = body
    image_out[TABLE_SPAN:TABLE_SPAN + len(body)] = body
    write_flash(dut, TABLE_ADDR, bytes(image_out))

    emit(handle, f"slot {slot} recorded: length {len(image)}, "
                 f"crc32 0x{entry['crc']:08x}, sequence {sequence}")
    return 0


def cmd_verify(args, handle):
    dut = open_device()
    table, dut = load_table(dut)
    if table is None:
        emit(handle, "no partition table found -- run `init`")
        return 1

    targets = [args.slot] if args.slot is not None else \
        range(len(table["entries"]))
    failures = 0

    for i in targets:
        entry = table["entries"][i]
        if not entry["flags"] & FLAG_VALID:
            emit(handle, f"  slot {i}: empty")
            continue

        # Read exactly the recorded length. Never scan for 0xff to find the
        # end -- a smaller image over a larger one leaves the tail behind.
        data, dut = read_flash(dut, entry["start"], entry["length"])
        crc = zlib.crc32(data)
        if crc == entry["crc"]:
            emit(handle, f"  slot {i}: OK, {entry['length']} bytes, "
                         f"crc32 0x{crc:08x} ({entry['label']})")
        else:
            failures += 1
            emit(handle, f"  slot {i}: MISMATCH, recorded 0x{entry['crc']:08x} "
                         f"read 0x{crc:08x} ({entry['label']})")

    emit(handle)
    emit(handle, "all recorded slots verified" if not failures
         else f"{failures} slot(s) failed verification")
    return 1 if failures else 0


def cmd_dump_table(args, handle):
    dut = open_device()
    raw, dut = read_flash(dut, TABLE_ADDR, TABLE_SPAN * 2)
    for name, blob in (("primary", raw[:TABLE_SPAN]),
                       ("shadow", raw[TABLE_SPAN:])):
        parsed = unpack_table(blob)
        emit(handle, f"{name} @0x{TABLE_ADDR if name == 'primary' else TABLE_SHADOW:06x}: "
                     f"{'sequence ' + str(parsed['sequence']) if parsed else 'absent or corrupt'}")
        emit(handle, f"  first 64 bytes: {blob[:64].hex()}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show the partition table")

    p_init = sub.add_parser("init", help="write a fresh partition table")
    p_init.add_argument("--force", action="store_true")

    p_write = sub.add_parser("write", help="write an image into a slot")
    p_write.add_argument("slot", type=int)
    p_write.add_argument("image")
    p_write.add_argument("--label", default="")
    p_write.add_argument("--force", action="store_true",
                         help="permit writing a locked slot")

    p_verify = sub.add_parser("verify", help="check recorded CRCs")
    p_verify.add_argument("--slot", type=int, default=None)

    sub.add_parser("dump-table", help="raw table bytes, both copies")

    args = parser.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)

    handlers = {
        "list": cmd_list, "init": cmd_init, "write": cmd_write,
        "verify": cmd_verify, "dump-table": cmd_dump_table,
    }

    with LOG.open("a") as handle:
        emit(handle, f"--- flashparts {args.command} ---")
        try:
            return handlers[args.command](args, handle)
        except Exception as error:
            emit(handle, f"error: {error}")
            raise


if __name__ == "__main__":
    sys.exit(main())
