#!/usr/bin/env python3
#
# Who has the board open. The arbiter's answer to "someone went around me". #430
# SPDX-License-Identifier: BSD-3-Clause

"""Which processes hold the board's USB and tty nodes.

    import board_holders
    board_holders.processes_holding(board_holders.board_nodes())  # [(pid, cmd)]

- Moved out of the retired `fpga_job_runner.py`, which is what this replaced.
- Reads /proc directly: `fuser`/`lsof` are not guaranteed installed and both
  need parsing.
- Sees only this PID namespace -- a container's reader is invisible.
"""

import os
from pathlib import Path

# Cynthion identities. Apollo on CONTROL, the SoC console on AUX, plus the
# Great Scott / OpenMoko ranges the board has shipped under.
KNOWN_IDS = {(0x1d50, 0x615b), (0x1d50, 0x615c), (0x1209, 0x0010)}
KNOWN_NAMES = ("cynthion", "apollo", "luna")


def usb_identity(path):
    try:
        vid = int((path / "idVendor").read_text().strip(), 16)
        pid = int((path / "idProduct").read_text().strip(), 16)
    except (OSError, ValueError):
        return None
    names = []
    for field in ("manufacturer", "product"):
        try:
            names.append((path / field).read_text(errors="replace").strip().lower())
        except OSError:
            pass
    text = " ".join(names)
    known_id = ((vid, pid) in KNOWN_IDS
                or (vid == 0x1d50 and 0x6180 <= pid <= 0x61ff)
                or (vid == 0x1209 and 0x0001 <= pid <= 0x000f))
    return (vid, pid) if known_id or any(w in text for w in KNOWN_NAMES) else None


def board_nodes(sys_root=Path("/sys"), dev_root=Path("/dev")):
    """Every /dev node that IS the board: its USB bus nodes and their ttys."""
    nodes = set()
    for device in (sys_root / "bus" / "usb" / "devices").glob("*"):
        if usb_identity(device) is None:
            continue
        try:
            bus = int((device / "busnum").read_text())
            number = int((device / "devnum").read_text())
        except (OSError, ValueError):
            continue
        nodes.add(dev_root / "bus" / "usb" / f"{bus:03d}" / f"{number:03d}")
        for tty in (sys_root / "class" / "tty").glob("ttyACM*"):
            try:
                tty_device = (tty / "device").resolve()
                usb_device = device.resolve()
            except OSError:
                continue
            if tty_device == usb_device or usb_device in tty_device.parents:
                nodes.add(dev_root / tty.name)
    return nodes


def processes_holding(nodes, proc_root=Path("/proc")):
    """(pid, command) for every process but this one holding one of `nodes`."""
    wanted = {str(path.resolve()) for path in nodes if path.exists()}
    found = {}
    for process in proc_root.iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            for handle in (process / "fd").iterdir():
                if str(handle.resolve()) not in wanted:
                    continue
                command = (process / "cmdline").read_bytes()
                command = command.replace(b"\0", b" ").decode(errors="replace").strip()
                found[int(process.name)] = command or "?"
                break
        except OSError:
            continue
    return sorted(found.items())


if __name__ == "__main__":
    for pid, command in processes_holding(board_nodes()):
        print(f"pid {pid}: {command}")
