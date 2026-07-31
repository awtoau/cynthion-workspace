#!/usr/bin/env python3
#
# One USB product ID per bitstream, so a device can be found by identity.
# SPDX-License-Identifier: BSD-3-Clause

"""
Product IDs for the test bitstreams, and the helper that finds a device by one.

## Why this exists

Devices were being located by `/dev/ttyACM*` node number or by newest mtime. Both are
wrong, and one of them cost real time: an investigation into a silent RISC-V SoC spent
hours reading `/dev/ttyACM1`, which on this machine is an **ST-LINK**. The node number
depends on plug order and on whatever else is attached -- this workstation currently has
eleven `ttyACM` nodes across four vendors.

Worse, several bitstreams shipped the *same* ID, and two of them were **Apollo's**:

- `1d50:615c` is the Apollo debugger and its Saturn-V bootloader
- `1d50:615b` is LUNA

A test bitstream claiming those does not just make itself ambiguous; it makes
`ApolloDebugger()` capable of opening a device that is not Apollo. Every bitstream needs
its own ID.

## The allocation

`1d50` is Great Scott Gadgets' vendor ID. `0x61xx` is where Cynthion products sit, so
test gateware uses a distinct block to avoid colliding with anything shipping.

Add new bitstreams here rather than picking a number locally -- a number picked locally
is a number that collides eventually, which is the situation this replaces.

## udev

`54-cynthion.rules` grants uaccess to the shipping IDs. IDs outside that list enumerate
but cannot be opened without root, which looks exactly like a dead bitstream. `1209:000e`
(the pid.codes example ID) is covered and is the fallback when a rules update is not
wanted.
"""

VENDOR_ID = 0x1d50

# Reserved elsewhere -- do NOT use these for test gateware.
RESERVED = {
    0x615c: "Apollo debugger and Saturn-V bootloader",
    0x615b: "LUNA",
}

# One per bitstream. The string is what the device reports as its product name, so it is
# also what a human sees in `lsusb` and what `find_device()` matches on.
PRODUCTS = {
    # RISC-V SoCs. Three separate bitstreams that all claimed 1209:000e, so a host tool
    # could not tell which core it had just configured.
    "riscv_console":     (0x6180, "Cynthion RISC-V console"),
    "riscv_vex_console": (0x6186, "Cynthion VexRiscv console"),
    "riscv_bench":       (0x6187, "Cynthion RISC-V benchmark"),

    # USB test gateware. usb_bulk, usb_oneway and usb_timing all claimed LUNA's 1d50:615b.
    "usb_serial":        (0x6181, "Cynthion USB Serial"),
    "usb_bulk":          (0x6182, "Cynthion USB bulk"),
    "usb_oneway":        (0x6188, "Cynthion USB one-way"),
    "usb_timing":        (0x6183, "Cynthion USB timing"),

    # Loader and LED bring-up. The two LED bitstreams shared 1209:0001.
    "bitstream_sink":    (0x6184, "Cynthion bitstream sink"),
    "led_patterns":      (0x6185, "Cynthion LED patterns"),
    "led_gateware":      (0x6189, "Cynthion LED gateware"),
}


def product_id(name):
    """The product ID for a bitstream, by name. Raises rather than guessing."""
    if name not in PRODUCTS:
        raise KeyError(
            f"no USB product ID allocated for {name!r}; add one to "
            f"ecp5-test/usb_ids.py rather than picking a number locally")
    return PRODUCTS[name][0]


def product_string(name):
    """The product string for a bitstream, by name."""
    return PRODUCTS[name][1]


def find_tty(name):
    """Return the /dev/ttyACM* node for a bitstream, or None.

    Matched by VID:PID from sysfs, never by node number or mtime. Returns None rather
    than a best guess: a wrong device is worse than no device, because reads from it
    succeed and mean nothing.
    """
    import glob
    import os

    wanted = product_id(name)
    for node in sorted(glob.glob("/dev/ttyACM*")):
        path = os.path.realpath(f"/sys/class/tty/{os.path.basename(node)}/device")
        # Walk up to the USB device node, which is where idVendor/idProduct live; the
        # tty's own directory is the interface, several levels below.
        for _ in range(8):
            vendor = os.path.join(path, "idVendor")
            if os.path.exists(vendor):
                vid = int(open(vendor).read().strip(), 16)
                pid = int(open(os.path.join(path, "idProduct")).read().strip(), 16)
                if (vid, pid) == (VENDOR_ID, wanted):
                    return node
                break
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
    return None


def find_usb(name):
    """Return the pyusb device for a bitstream, or None. Same rule as find_tty()."""
    import usb.core
    return usb.core.find(idVendor=VENDOR_ID, idProduct=product_id(name))


if __name__ == "__main__":
    print(f"vendor 0x{VENDOR_ID:04x}\n")
    print("reserved:")
    for pid, what in sorted(RESERVED.items()):
        print(f"  0x{pid:04x}  {what}")
    print("\nallocated:")
    for name, (pid, string) in sorted(PRODUCTS.items()):
        node = find_tty(name)
        where = f"  -> {node}" if node else ""
        print(f"  0x{pid:04x}  {name:<16} {string}{where}")
