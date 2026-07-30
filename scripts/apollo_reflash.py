#!/usr/bin/env python3
#
# Reflash the Apollo SAMD11 firmware over DFU.
# SPDX-License-Identifier: BSD-3-Clause

"""
Puts Apollo into its bootloader and programs a firmware image over DFU.

Exists because the reflash cycle is three steps that are easy to get subtly wrong:
`enter-dfu`, wait for the device to re-enumerate as the bootloader, then program.
The middle step is the one worth having in a script -- the device keeps the same
VID:PID (1d50:615c) across the reboot and is distinguished only by its product
string, so "is the bootloader up yet" is not a question `lsusb | grep 1d50` can
answer.

## Waiting without a timeout

The poll below is bounded by an iteration count, not a duration. What is being
waited for is a USB re-enumeration, which on this part completes in tens of
milliseconds; the cap is set high enough that reaching it means the device is not
coming back rather than that it was slow. On expiry the script reports what it did
see and exits non-zero, rather than programming a device that may not be in the
bootloader.

    ./scripts/apollo_reflash.py
    ./scripts/apollo_reflash.py --firmware path/to/firmware.bin
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "apollo_reflash.log"
APOLLO = ROOT / "repos" / "apollo"
DEFAULT_FIRMWARE = APOLLO / "firmware" / "_build" / "cynthion_d11" / "firmware.bin"

sys.path.insert(0, str(APOLLO))

# How many times to re-scan USB while waiting for a re-enumeration.
#
# What is being waited for: the SAMD11 detaching and coming back in the other mode,
# which requires the host to notice the disconnect, re-enumerate, and load the
# string descriptors. On this host a full cycle is a few hundred milliseconds --
# dominated by the kernel's own reset and address assignment, not by the device.
#
# Why this number: one iteration is a libusb enumeration of the whole bus, measured
# at roughly 1-3 ms here, so 4000 covers several seconds. It is set generously
# because the cost of being wrong in the two directions is asymmetric: polling too
# long wastes a few seconds, while giving up early means programming a device that
# is not in the bootloader, or reporting a working board as broken. A first attempt
# at 400 expired against a device that was in fact coming back fine.
#
# On expiry: report what mode the device was last seen in and exit non-zero, without
# programming anything.
ENUMERATION_POLL_LIMIT = 4000

# The bootloader and the application share this VID:PID and are told apart by their
# product string. Matching on the string is therefore not a convenience.
APOLLO_VID = 0x1D50
APOLLO_PID = 0x615C
BOOTLOADER_MARKER = "bootloader"


def find_device(want_bootloader):
    """Return a usb device whose product string does/doesn't say 'bootloader'.

    The product string is the only thing distinguishing the two modes, so a device
    that will not answer the string descriptor cannot be classified and is skipped.
    That happens transiently while it is still enumerating, which is why the caller
    polls rather than asking once.
    """
    import usb.core

    for device in usb.core.find(find_all=True, idVendor=APOLLO_VID,
                               idProduct=APOLLO_PID):
        try:
            product = (device.product or "").lower()
        except Exception:
            continue
        if (BOOTLOADER_MARKER in product) == want_bootloader:
            return device
    return None


def wait_for(want_bootloader, emit):
    """Poll for the device in the wanted mode. Returns it, or None on expiry."""
    what = "bootloader" if want_bootloader else "application"
    for attempt in range(ENUMERATION_POLL_LIMIT):
        device = find_device(want_bootloader)
        if device is not None:
            emit(f"  {what} present after {attempt} polls")
            return device
    emit(f"  {what} did NOT appear within {ENUMERATION_POLL_LIMIT} polls")
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--firmware", type=Path, default=DEFAULT_FIRMWARE)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        if not args.firmware.is_file():
            emit(f"no such firmware image: {args.firmware}")
            return 1
        emit(f"firmware: {args.firmware} ({args.firmware.stat().st_size} bytes)")

        # Already in the bootloader (e.g. a previous run failed part-way) is fine;
        # skip straight to programming rather than failing on a missing app.
        if find_device(want_bootloader=True) is None:
            emit("requesting DFU...")
            from apollo_fpga import ApolloDebugger
            debugger = ApolloDebugger()
            try:
                # boot_to_dfu() is the underlying call; the `enter-dfu` CLI command
                # is a thin wrapper over it.
                debugger.boot_to_dfu()
            except Exception as error:
                # The device rebooting mid-request is the expected outcome here, not
                # a failure: it cannot send a status stage for a request that resets
                # it. Anything else is worth showing.
                emit(f"  enter-dfu returned {error!r} (expected on reboot)")
            if wait_for(True, emit) is None:
                return 1

        emit("programming...")
        from fwup.dfu import DFUTarget
        target = DFUTarget(idVendor=APOLLO_VID, idProduct=APOLLO_PID)
        target.program(args.firmware.read_bytes())
        emit("  programmed")

        # Run the application again, then confirm it came back and says who it is.
        target.run_user_program()
        if wait_for(False, emit) is None:
            return 1

        from apollo_fpga import ApolloDebugger
        debugger = ApolloDebugger()
        emit(f"  running: {debugger.get_firmware_version()}")

        emit()
        emit(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
