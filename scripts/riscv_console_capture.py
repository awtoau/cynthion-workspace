#!/usr/bin/env python3
#
# Capture the RISC-V console from the first byte, across a reconfigure.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads the RISC-V console starting at the banner, not partway through it.

The firmware prints its prologue -- the arithmetic checks and every flash result
-- once, immediately after reset, and then ticks forever. Opening the port after
configuring the FPGA always misses part of that prologue: the SoC is running and
printing before the host has finished enumerating the device and opening the
tty. Three attempts here caught `ame`, `00  same` and `nsole.` as their first
line, losing exactly the values being tested.

Reversing the order does not work either, because the tty node does not exist
until the bitstream is loaded and the device enumerates.

What works is loading the bitstream, waiting for the node, opening it, and then
resetting the CPU so the prologue is printed AGAIN with a reader already
attached. There is no reset line exposed here, so the reset is a second
`apollo configure` of the same bitstream: the FPGA reconfigures, the CPU
restarts from its reset vector, and the USB device re-enumerates.

Re-enumeration means the file handle opened before it is stale, so the port is
reopened after the second configure -- but by then the node exists and udev has
it, so the reopen is fast enough to catch the banner. That is the race this
script is built around rather than one it pretends does not exist.

    ./scripts/riscv_console_capture.py
    ./scripts/riscv_console_capture.py --lines 20
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_console_capture.log"
BITSTREAM = ROOT / "tmp" / "vexii_hello" / "build" / "top.bit"
APOLLO_CLI = ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"

sys.path.insert(0, str(ROOT / "ecp5-test"))

# How many tty events to wait through for the console to appear.
#
# Waiting for: FPGA reconfiguration, USB enumeration, and the kernel binding a
# CDC-ACM driver. Why this value: `udevadm monitor` emits a line per device
# event and enumerating this board produces a few dozen, so 60 covers it with
# margin. On expiry: return None; the caller reports it rather than guessing a
# node, because this workstation has eleven ttyACM nodes and one of them is an
# ST-LINK.
TTY_ROUNDS = 60

# Serial read timeout per line, in seconds.
#
# Waiting for: one line of console output. Why this value: the firmware's tick
# loop is a busy-wait of roughly a second, so a healthy board emits a line at
# least that often; 5 s spans more than one tick period while bounding a dead
# board to seconds. On expiry: stop reading and report what arrived -- a partial
# transcript is diagnostic, a hang is not.
LINE_TIMEOUT_S = 5.0


def emit(handle, text=""):
    print(text, flush=True)
    if handle:
        handle.write(text + "\n")
        handle.flush()


def configure(handle):
    """Load the bitstream over JTAG. Also serves as the CPU reset."""
    result = subprocess.run(
        [sys.executable, str(APOLLO_CLI), "configure", str(BITSTREAM)],
        capture_output=True, text=True, cwd=str(ROOT))
    for line in (result.stdout + result.stderr).strip().splitlines():
        emit(handle, f"    {line}")
    return result.returncode == 0


def wait_for_console(handle):
    """Block on kernel tty events until the console appears, or give up.

    Identification is `usb_ids.wait_for_tty`, by VID:PID from sysfs -- never by
    node number. The waiting is done here because that helper's rounds block on
    `udevadm settle`, which returns instantly when the uevent queue is empty,
    and straight after a reconfigure it IS empty -- the device has not begun
    enumerating, so there is nothing yet to drain. `udevadm monitor` subscribes
    to the event stream instead, so reading a line from it genuinely blocks
    until the kernel does something.
    """
    import usb_ids

    node = usb_ids.wait_for_tty("riscv_console", settles=1)
    if node:
        return node

    monitor = subprocess.Popen(
        ["udevadm", "monitor", "--udev", "--subsystem-match=tty"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for _ in range(TTY_ROUNDS):
            if monitor.stdout.readline() == "":
                break
            node = usb_ids.wait_for_tty("riscv_console", settles=1)
            if node:
                return node
    finally:
        monitor.terminate()
        monitor.wait()
    return None


def capture(handle, lines_wanted):
    """Reconfigure with a reader attached and return the console lines."""
    import serial

    emit(handle, f"configuring {BITSTREAM}")
    if not configure(handle):
        emit(handle, "configure failed")
        return None

    node = wait_for_console(handle)
    if node is None:
        emit(handle, "no riscv_console tty appeared")
        return None
    emit(handle, f"console: {node}")

    # The second configure restarts the CPU so the banner is printed again.
    # The device re-enumerates, so the node has to be re-resolved afterwards --
    # it usually comes back as the same path, but that is not guaranteed and
    # assuming it is how the wrong device gets read.
    emit(handle, "reconfiguring to restart the CPU with a reader attached")
    if not configure(handle):
        emit(handle, "second configure failed")
        return None

    node = wait_for_console(handle)
    if node is None:
        emit(handle, "console did not come back after the reset")
        return None

    lines = []
    with serial.Serial(node, 115200, timeout=LINE_TIMEOUT_S) as port:
        while len(lines) < lines_wanted:
            raw = port.readline()
            if not raw:
                emit(handle, "  read timed out; reporting what arrived")
                break
            lines.append(raw.decode("ascii", "replace").rstrip("\r\n"))
    return lines


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lines", type=int, default=14)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        lines = capture(handle, args.lines)
        if lines is None:
            return 1
        emit(handle)
        emit(handle, "transcript:")
        for line in lines:
            emit(handle, f"    {line!r}")
        emit(handle)
        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
