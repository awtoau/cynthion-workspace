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

# One per bitstream: (product_id, description, port).
#
# The product string is built from the last two as "Cynthion <PORT>: <description>", so
# `lsusb` answers the question a human actually has -- **which socket do I plug into** --
# without needing to open the source. Cynthion has three USB ports and they are not
# interchangeable:
#
#   AUX      belongs to the FPGA outright. Most test gateware lives here.
#   TARGET   the port under test. Gateware here is talking to a device, not to you.
#   CONTROL  shared with Apollo; claiming it needs an ApolloAdvertiser.
#
# Plugging into the wrong one gives a device that never enumerates, which looks exactly
# like a bitstream that failed to load.
PRODUCTS = {
    # RISC-V SoCs. Three separate bitstreams that all claimed 1209:000e, so a host tool
    # could not tell which core it had just configured.
    "riscv_console":     (0x6180, "VexiiRiscv console",   "AUX"),
    "riscv_vex_console": (0x6186, "VexRiscv console",      "TARGET"),
    "riscv_bench":       (0x6187, "RISC-V benchmark",      "TARGET"),

    # USB test gateware. usb_bulk, usb_oneway and usb_timing all claimed LUNA's 1d50:615b.
    "usb_serial":        (0x6181, "USB serial terminal",  "AUX"),
    "usb_bulk":          (0x6182, "USB bulk loopback",     "AUX"),
    "usb_oneway":        (0x6188, "USB one-way sink",      "AUX"),
    "usb_timing":        (0x6183, "USB timing",            "AUX"),

    # Loader and LED bring-up. The two LED bitstreams shared 1209:0001.
    "bitstream_sink":    (0x6184, "bitstream sink",       "TARGET"),
    "led_patterns":      (0x6185, "LED patterns",         "AUX"),
    "led_gateware":      (0x6189, "LED gateware",         "TARGET"),
}


def product_id(name):
    """The product ID for a bitstream, by name. Raises rather than guessing."""
    if name not in PRODUCTS:
        raise KeyError(
            f"no USB product ID allocated for {name!r}; add one to "
            f"ecp5-test/usb_ids.py rather than picking a number locally")
    return PRODUCTS[name][0]


def product_string(name):
    """The USB product string: "Cynthion <PORT>: <description>".

    The port comes first because it is the actionable part -- a human reading `lsusb`
    wants to know which socket to use before they care what the gateware does.
    """
    if name not in PRODUCTS:
        raise KeyError(f"no USB product ID allocated for {name!r}")
    _, description, port = PRODUCTS[name]
    return f"Cynthion {port}: {description}"


def port(name):
    """Which physical USB port this bitstream appears on: AUX, TARGET or CONTROL."""
    return PRODUCTS[name][2]


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


def wait_for_tty(name, settles=60):
    """Wait for a bitstream's tty to appear and be openable, or return None.

    Use this after configuring the FPGA, not `find_tty()` alone. Two things go wrong
    otherwise, and both have:

    **Spinning is not waiting.** A bare `for _ in range(600): find_tty(...)` loop completes
    in microseconds, long before the FPGA has reconfigured, the device re-enumerated and
    the kernel bound a CDC driver. It returns None for a perfectly healthy board -- a false
    negative that condemned every frequency in a clock ladder before it was caught.

    **The old node lingers.** Immediately after a reconfigure, `find_tty()` can return the
    node from *before* it, which then fails to open. So this settles first and confirms the
    port actually opens rather than trusting the path to exist.

    Each iteration blocks on `udevadm settle`, which drains the kernel's uevent queue, so
    the loop advances on real progress rather than a counter. `settles` bounds how many to
    wait through.

    **The default was 20 and that was not enough.** Under load -- a build running, several
    agents working -- a reconfigure can take longer than 20 settles to produce a bound tty,
    and the result is a false negative on a board that is working perfectly. That has now
    happened three times, and once it cost an entire investigation: an agent read "no
    console", concluded the environment had broken underneath it, stashed working changes
    and stopped. The device was on the USB bus the whole time.

    So 60. The cost of waiting too long is a slow script; the cost of not waiting long
    enough is a wrong conclusion about hardware.
    """
    import subprocess

    import serial

    on_bus = False
    for _ in range(settles):
        subprocess.run(["udevadm", "settle"], capture_output=True)
        node = find_tty(name)
        if node:
            try:
                serial.Serial(node, 115200, timeout=0.1).close()
                return node
            except Exception:
                # The node exists but will not open. That is usually the console service
                # holding it exclusively, which is not absence -- returning None here sent
                # callers chasing a hardware fault that was another process doing its job.
                # A caller that reads through the service does not need to open it.
                if find_usb(name) is not None:
                    return node
                continue
        # Remember whether the USB device was ever seen. A device present on the bus but
        # without a bound tty is mid-enumeration, not absent, and giving up on it produces
        # the false negative this function exists to prevent.
        if not on_bus and find_usb(name) is not None:
            on_bus = True

    # One last, longer look if the device is on the bus. Callers kept working around this
    # by checking lsusb themselves and looking again -- which always found it. Doing that
    # here means they do not have to.
    if on_bus or find_usb(name) is not None:
        for _ in range(settles):
            subprocess.run(["udevadm", "settle"], capture_output=True)
            node = find_tty(name)
            if node:
                try:
                    serial.Serial(node, 115200, timeout=0.1).close()
                    return node
                except Exception:
                    continue
    return None


def find_usb(name):
    """Return the pyusb device for a bitstream, or None. Same rule as find_tty()."""
    import usb.core
    return usb.core.find(idVendor=VENDOR_ID, idProduct=product_id(name))


def check_ports():
    """Verify each bitstream's declared port matches the PHY it actually requests.

    A wrong port label is worse than none: it sends someone to the wrong socket, where the
    device never enumerates, which looks exactly like a bitstream that failed to load.
    Returns a list of problems, empty if consistent.
    """
    import re
    from pathlib import Path

    here = Path(__file__).resolve().parent
    problems = []
    for name in PRODUCTS:
        matches = list(here.rglob("*.py"))
        for path in matches:
            src = path.read_text()
            if f'product_id("{name}")' not in src:
                continue
            phys = set(re.findall(r'"(aux|target|control)_phy"', src))
            if not phys:
                continue
            declared = port(name).lower()
            if declared not in phys:
                problems.append(
                    f"{path.name}: declared {declared.upper()} but requests "
                    f"{'/'.join(sorted(p.upper() for p in phys))}")
    return problems


if __name__ == "__main__":
    print(f"vendor 0x{VENDOR_ID:04x}\n")
    print("reserved:")
    for pid, what in sorted(RESERVED.items()):
        print(f"  0x{pid:04x}  {what}")
    print("\nallocated:")
    for name, (pid, description, prt) in sorted(PRODUCTS.items(),
                                                key=lambda kv: kv[1][0]):
        node = find_tty(name)
        where = f"  -> {node}" if node else ""
        print(f"  0x{pid:04x}  {prt:<7} {name:<18} {description}{where}")

    problems = check_ports()
    if problems:
        print("\nPORT MISMATCHES:")
        for problem in problems:
            print(f"  {problem}")
    else:
        print("\nevery declared port matches the PHY its gateware requests")
