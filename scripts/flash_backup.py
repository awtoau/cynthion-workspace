#!/usr/bin/env python3
#
# Read the whole configuration flash to a file, in independent chunks.
# SPDX-License-Identifier: BSD-3-Clause

"""
Dumps the W25Q32 configuration flash, one chunk per background-SPI session.

A single `flash-read` of the full 4 MiB does not survive: past roughly 1.9 MB
every 256-byte page comes back as `03 <addr> 00 00 ...` -- the READ_PAGE opcode
and the address echoed back instead of data. Re-reading the same offset in a
short transfer returns the true contents, so the flash is fine and the long
background-SPI session is not.

This reads in `--chunk` sized pieces, each in its own background-SPI session,
and verifies each chunk by reading it twice and comparing. A chunk that does
not reproduce is retried rather than silently written, because a corrupted
backup is worse than no backup.

Sustained reading also drops the USB link with `[Errno 32] Pipe error` after
roughly 1.5-1.9 MB, and a recovery port-reset sometimes lands the board in its
DFU bootloader. Both are handled and retried from the same offset, so a full
4 MiB dump completes without intervention.

    ./scripts/flash_backup.py tmp/flashbackup/full.bin
    ./scripts/flash_backup.py tmp/flashbackup/full.bin --chunk 65536
"""

import argparse
import sys
from pathlib import Path

from usb.core import USBError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402


FLASH_SIZE = 4 * 1024 * 1024


def wait_for_usb(vid=0x1d50, pid=0x615c, attempts=6000):
    """Block until the Apollo VID:PID is on the bus, then reset the port.

    Retrying `ApolloDebugger()` in a tight Python loop spins far faster than
    USB re-enumeration takes, so it exhausts its attempts while the device is
    still coming back. Waiting on the bus listing is a poll on a real event
    rather than a fixed delay.

    The `dev.reset()` is the part that actually clears `[Errno 32] Pipe error`.
    After that error the device stays enumerated but every control transfer
    keeps failing -- `emergency_reset()` does not help, and neither does simply
    reconstructing ApolloDebugger. A port-level reset restores it, confirmed by
    `flash-info` reporting W25Q32DV again immediately afterwards.
    """
    import usb.core

    def present():
        for remaining in range(attempts, 0, -1):
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
    # reset() drops the device off the bus for a moment; wait for it to come
    # back before handing it to ApolloDebugger, or construction races it and
    # raises DebuggerNotFound.
    return present() is not None


def open_device(attempts=40):
    """One debugger for the whole run, reopened if the link drops.

    `force_offline=True` must be passed at construction -- calling
    `force_fpga_offline()` on an already-built debugger is not equivalent, and
    leaves the running gateware owning the SPI lines.

    A port reset sometimes lands the board in its DFU bootloader rather than
    back in Apollo. `exit_dfu()` brings it out; without that the run dies with
    DebuggerNotFound on a board that is present and perfectly recoverable.
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


def read_chunk(dut, offset, length):
    """One chunk, in its own background-SPI session.

    `unconfigure()` must run in its **own** `with dut.jtag` block and the read
    in a second one. Doing both inside a single JTAG context leaves the flash
    reading back all-0xff, so `read_flash` raises "Flash does not seem
    correctly connected to the FPGA!" on a board that is perfectly healthy --
    `flash-info` reports W25Q32DV throughout. Measured, not guessed.
    """
    with dut.jtag as jtag:
        dut.create_jtag_programmer(jtag).unconfigure()
    with dut.jtag as jtag:
        programmer = dut.create_jtag_programmer(jtag)
        return bytes(programmer.read_flash(length, offset=offset))


def looks_echoed(data, offset):
    """True if pages hold the READ_PAGE opcode and their own address."""
    for page in range(0, len(data), 256):
        addr = offset + page
        if data[page:page + 1] == b"\x03" and \
           data[page + 1:page + 4] == addr.to_bytes(3, "big"):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chunk", type=int, default=256 * 1024,
                        help="bytes per background-SPI session")
    parser.add_argument("--size", type=int, default=FLASH_SIZE)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    out = bytearray()
    dut = open_device()
    emit(f"reading {args.size} bytes in {args.chunk}-byte chunks")

    for offset in range(0, args.size, args.chunk):
        length = min(args.chunk, args.size - offset)

        for attempt in range(1, args.retries + 1):
            try:
                first = read_chunk(dut, offset, length)
                if looks_echoed(first, offset):
                    emit(f"  0x{offset:06x} attempt {attempt}: "
                         f"opcode echo, retrying")
                    continue
                second = read_chunk(dut, offset, length)
            except (USBError, IOError) as error:
                # Two failure modes, both transient and both cured by
                # reopening: the USB link drops after roughly 1.5-1.9 MB of
                # sustained background SPI ([Errno 32] Pipe error), and the
                # flash intermittently reads back all-0xff so read_flash
                # decides it is "not correctly connected". The board is
                # healthy in both cases -- flash-info still reports
                # W25Q32DV. Rebuild the debugger and retry this offset
                # rather than losing the whole run.
                emit(f"  0x{offset:06x} attempt {attempt}: "
                     f"{error}, reopening device")
                dut = open_device()
                continue
            if first != second:
                emit(f"  0x{offset:06x} attempt {attempt}: "
                     f"reads disagree, retrying")
                continue
            used = length - first.count(0xFF)
            emit(f"  0x{offset:06x} +{length} verified, "
                 f"{used} non-0xff")
            out.extend(first)
            break
        else:
            emit(f"  0x{offset:06x} FAILED after {args.retries}")
            return 1

    args.output.write_bytes(out)
    emit()
    emit(f"wrote {len(out)} bytes to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
