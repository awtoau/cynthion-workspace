#!/usr/bin/env python3
#
# Run a command on the SoC shell and print what it says.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sends one or more commands to the RISC-V shell and reports the reply.

    ./scripts/soc_shell.py help
    ./scripts/soc_shell.py check id "read 40"
    ./scripts/soc_shell.py --listen        # just watch, send nothing
    ./scripts/soc_shell.py --port /dev/ttyACM0 help    # the Apollo-facing console

## Why this exists

Hand-rolled one-shot reads of this console are unreliable, and repeatedly produced
*false negatives* -- empty output that read as "the firmware is dead" when it was
running perfectly. Three separate causes, all of which this handles:

**The tty is not ready the moment it appears.** After a reconfigure the node is created
before it can be opened, so the first `serial.Serial(...)` raises PortNotOpenError, and a
read immediately after can return zero bytes. Retrying is not a workaround; it is what
the device actually requires.

**The banner is printed once, before the host enumerates.** The CPU starts the instant
the FPGA is configured and USB takes about half a second to bind a tty, so a reader that
opens the port and waits has already missed everything. Prompting the shell is the only
reliable way to make it speak; waiting is not.

**Two readers corrupt each other.** If `tio_user.py --serve` is running it owns the port,
so this talks through its socket instead. Never both -- each steals bytes the other never
sees, which looks like garbled firmware output rather than contention.

A reader that is NOT serving cannot be talked through, and that case is far worse: this
opens the tty successfully, writes successfully, and reads nothing, because the other
process took every byte. The board looks dead and is not. A plain `tio_user.py` left
running in another terminal costs an afternoon this way -- it happened, with a bitstream
that was working the whole time. So before reporting silence, this looks for the other
reader in /proc and names it. Silence with an explanation is a result; silence without one
is a trap.

Exit status is 1 if the shell said nothing at all, so this can be used as a check.

## `--port`, and the reason it exists

The firmware answers on every UART in `target::UART_BASES`, not just the USB one.
The second is the Apollo-facing port on the shared JTAG pins, which the host sees
as the Apollo debugger's own CDC-ACM node (`/dev/ttyACM0` here: `1d50:615c`).

That is the way out of the contention above without touching someone else's
process. When another reader owns the USB console, the board is not unreachable
-- it is unreachable *on that port*. `--port` talks to the other one, which is a
completely independent 16550 with its own interrupt source, so a reply there is
evidence about the firmware and the interrupt path rather than about who holds a
tty.

It is also the only way to exercise the second console at all. `target::ANNOUNCING`
keeps that port silent unless spoken to, because its TX pin is JTAG TMS and the
FPGA must never transmit on it unbidden -- so nothing appears there until
something types, and this is what types.
"""

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# How many times to retry opening the tty.
#
# Not a timeout on the device -- it is a retry against a udev race, where the node exists
# but is not yet openable. Each attempt settles udev first, so this is bounded by real
# events rather than by elapsed time.
OPEN_ATTEMPTS = 6

# Time to let the shell answer before reading.
#
# The commands here print at most a few dozen bytes over USB bulk, which takes well under
# a millisecond. This is generous purely so a slow command (a HyperRAM probe, a flash
# read) is not truncated; the cost of being long is a slower test, the cost of being
# short is a false empty result -- which is the exact failure this script exists to stop.
REPLY_S = 2.0


def other_readers(node):
    """Every other process holding `node` open, as (pid, command) pairs.

    Reads /proc directly rather than shelling out to `fuser` or `lsof`: neither is
    guaranteed installed, both need parsing, and the answer is two readlinks away.

    Errors are swallowed per-process on purpose. A pid that exits mid-scan, or one owned
    by another user, is not a reason to fail a diagnostic -- and a partial list still
    names the culprit in the case that matters, which is the user's own terminal.
    """
    import os

    try:
        target = os.path.realpath(node)
    except OSError:
        return []

    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            handles = os.listdir(fd_dir)
        except OSError:
            continue
        for handle in handles:
            try:
                if os.path.realpath(f"{fd_dir}/{handle}") != target:
                    continue
                with open(f"/proc/{entry}/cmdline", "rb") as cmdline:
                    command = cmdline.read().replace(b"\0", b" ").decode().strip()
                found.append((int(entry), command or "?"))
            except OSError:
                pass
            break
    return found


class Link:
    """The console, via the serve socket if one is running, else the tty directly."""

    def __init__(self, sock=None, port=None, how="", node=None):
        self.sock, self.port, self.how, self.node = sock, port, how, node

    @classmethod
    def open(cls, node=None):
        import serial

        # An explicit node bypasses both the service and the USB lookup. The
        # service on 9000 forwards the USB console specifically, so routing an
        # explicit port through it would silently talk to a different console
        # than the one that was asked for -- which is worse than not offering it.
        if node:
            for _ in range(OPEN_ATTEMPTS):
                subprocess.run(["udevadm", "settle"], capture_output=True)
                try:
                    return cls(port=serial.Serial(node, 115200,
                                                  timeout=REPLY_S + 2),
                               how=node, node=node)
                except Exception as error:
                    last = f"{node}: {error}"
            raise RuntimeError(last)

        try:
            sock = socket.create_connection(("127.0.0.1", 9000), timeout=3)
            sock.settimeout(REPLY_S + 2)
            return cls(sock=sock, how="service on 9000")
        except OSError:
            pass

        import usb_ids

        last = "no console tty appeared"
        for attempt in range(OPEN_ATTEMPTS):
            subprocess.run(["udevadm", "settle"], capture_output=True)
            node = usb_ids.wait_for_tty("riscv_console")
            if not node:
                last = "no console tty appeared"
                continue
            try:
                return cls(port=serial.Serial(node, 115200, timeout=REPLY_S + 2),
                           how=node, node=node)
            except Exception as error:
                last = f"{node}: {error}"
        raise RuntimeError(last)

    def write(self, data):
        if self.sock:
            self.sock.sendall(data)
        else:
            self.port.write(data)
            self.port.flush()

    def read_available(self):
        """Drain whatever has arrived. Never raises -- a read error means no data."""
        try:
            if self.sock:
                return self.sock.recv(4096)
            waiting = self.port.in_waiting
            return self.port.read(waiting) if waiting else self.port.read(1)
        except OSError:
            return b""

    def close(self):
        try:
            (self.sock or self.port).close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("commands", nargs="*", help="commands to send, in order")
    parser.add_argument("--listen", action="store_true",
                        help="print whatever arrives; send nothing")
    parser.add_argument("--port",
                        help="a tty node to use instead of the USB console; "
                             "/dev/ttyACM0 is the Apollo-facing one")
    args = parser.parse_args()

    try:
        link = Link.open(args.port)
    except RuntimeError as error:
        emit(f"could not reach the console: {error}")
        return 1
    emit(f"console: {link.how}")

    got_anything = False

    if args.listen:
        emit("--- listening ---")
        deadline = time.monotonic() + 10
        # Reassembled into lines before emitting: the console arrives in
        # whatever chunks the tty hands over, and a log line that is half a
        # word is not searchable.
        pending = ""
        while time.monotonic() < deadline:
            chunk = link.read_available()
            if chunk:
                got_anything = True
                pending += chunk.decode("ascii", "replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    emit(line)
        if pending:
            emit(pending)
        link.close()
        return 0 if got_anything else 1

    # A bare Enter first: it lands at a clean prompt whatever was half-typed, and its
    # reply proves the shell is alive before any real command is judged.
    for command in ["", *args.commands]:
        link.write(command.encode() + b"\r")
        time.sleep(REPLY_S)
        reply = b""
        while True:
            chunk = link.read_available()
            if not chunk:
                break
            reply += chunk
        text = reply.decode("ascii", "replace")
        if text.strip():
            got_anything = True
        emit(f"--- {command or '<enter>'} ---")
        emit(text.strip() or "(nothing)")

    link.close()

    # Silence is only evidence about the board if nothing else was eating the bytes.
    # Checked here rather than before the commands, because a reader that attaches
    # while this runs is just as fatal and the list is only interesting when the
    # result was empty anyway.
    if not got_anything and link.node:
        thieves = other_readers(link.node)
        if thieves:
            emit()
            emit("*** ANOTHER PROCESS IS READING THIS PORT ***")
            for pid, command in thieves:
                emit(f"      pid {pid}: {command}")
            emit("The shell above said nothing because that process took every")
            emit("byte, not because the firmware is silent. A tty has exactly one")
            emit("reader. Stop it, or restart it as `./tio_user.py --serve` so this")
            emit("script reads through its socket on port 9000 instead of competing.")

    emit()
    return 0 if got_anything else 1


if __name__ == "__main__":
    sys.exit(main())
