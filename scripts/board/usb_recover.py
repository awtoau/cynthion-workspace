#!/usr/bin/env python3
# Try to bring an unresponsive Apollo back without touching the cables; escalate, verify, stop.
# SPDX-License-Identifier: BSD-3-Clause

"""
The software recovery ladder for a Cynthion whose Apollo stopped answering. Notes and the measured
results: docs/board-usb-recovery.md.

    ./scripts/board/usb_recover.py --status        # where the Cynthion is; read-only
    ./scripts/board/usb_recover.py                 # probe; if silent, climb the ladder
    ./scripts/board/usb_recover.py --port 1-1.3.1.3.3.3   # when it is not enumerated at all

1. Probe: does Apollo answer GET_ID? If so, stop.
2. Cut and restore power on the Cynthion's own hub ports (`uhubctl`, 5 s off).
3. Reset only the hub it hangs off (`usbreset`), unless something else on that hub is held open.
4. Still silent: report that a physical power cycle is needed. The script never resets a whole
   USB controller: that drops every device, including other projects' serial adapters.

Needs `sudo -n` for 2 and 3. Log: ./tmp/logs/usb_recover.log. Exit 0 when Apollo answers, 1 if not.
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "usb_recover.log"
REMEMBERED = ROOT / "tmp" / "usb_recover_ports.txt"
SYSFS = Path("/sys/bus/usb/devices")

CYNTHION_VID = "1d50"
CYNTHION_PIDS = {"615b", "615c", "6180"}        # analyzer stub, Apollo, SoC console
APOLLO = (0x1d50, 0x615c)
REQUEST_GET_ID = 0xA0                           # apollo_fpga.ApolloDebugger.REQUEST_GET_ID
REQUEST_BOOT_TO_DFU = 0xED                      # apollo_fpga.ApolloDebugger.REQUEST_BOOT_TO_DFU
# The bootloader was listed by the first lsusb after `apollo enter-dfu` returned (2026-11-05, not
# timed). 5 s covers that with margin; on expiry the step is reported failed and the ladder goes on.
BOOTLOADER_S = 5

# A dead Apollo costs the kernel two 5 s descriptor reads per attempt: 16 s measured (2026-11-05
# 14:53:16 -> 14:53:32). 25 s is 1.5x that; on expiry the step counts as not having recovered it.
SETTLE_S = 25
POLL_S = 0.5            # a local sysfs / USB probe is cheap
# After a power-on the kernel retries enumerating a dead device for ~92 s ("unable to enumerate"), and
# every request to that hub (uhubctl's status read, usbreset) queues behind it. Measured 92, 93, 92 s
# for `uhubctl cycle` on the dead port (2026-11-05); the healthy case was never measured. Terminating
# early does not help: uhubctl runs as root under sudo and cannot be signalled. 120 s = 1.25x the
# measured; on expiry the step is reported failed and the ladder goes on.
STEP_LIMIT_S = 120
OFF_S = 5               # port off time: long enough for a bus-powered board's rails to discharge


class Skip(Exception):
    """A step that must not run; the message says why."""


class Failed(Exception):
    """A step that ran and failed or hung. The ladder still probes: the power may have been cut."""


def hub_and_port(name):
    """'1-1.3.1.3.3.3' -> ('1-1.3.1.3.3', 3); a root-hub port '1-3' -> ('1', 3), as uhubctl names it."""
    hub, _, port = name.rpartition(".")
    if hub:
        return hub, int(port)
    bus, _, port = name.partition("-")
    return bus, int(port)


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def describe(path):
    return {"name": path.name, "path": path, "vid": read(path / "idVendor"),
            "pid": read(path / "idProduct"), "product": read(path / "product"),
            "busnum": int(read(path / "busnum", "0") or 0),
            "devnum": int(read(path / "devnum", "0") or 0)}


def devices_on_hub(hub, sysfs=SYSFS):
    """port number -> device, for the devices directly on `hub`."""
    prefix = hub + "." if "-" in hub else hub + "-"
    found = {}
    for path in sorted(Path(sysfs).iterdir()):
        match = re.fullmatch(re.escape(prefix) + r"(\d+)", path.name)
        if match:
            found[int(match.group(1))] = describe(path)
    return found


def find_cynthion(sysfs=SYSFS):
    """Sysfs names of the enumerated Cynthion devices (analyzer stub, Apollo, SoC console)."""
    return sorted(path.name for path in Path(sysfs).iterdir()
                  if read(path / "idVendor") == CYNTHION_VID
                  and read(path / "idProduct") in CYNTHION_PIDS)


def nodes_of(device):
    """The device nodes a process could hold for this device: the usb node and its tty ports."""
    nodes = {f"/dev/bus/usb/{device['busnum']:03d}/{device['devnum']:03d}"}
    nodes |= {f"/dev/{tty.name}" for tty in Path(device["path"]).glob("*/tty/*")}
    return nodes


def holders(devices, proc="/proc"):
    """pid -> node, for every process holding a node of any of `devices` open."""
    wanted = set()
    for device in devices:
        wanted |= nodes_of(device)
    found = {}
    for pid_dir in Path(proc).iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                target = os.readlink(fd)
                if target in wanted:
                    found[int(pid_dir.name)] = target
        except OSError:
            continue        # a process that is gone, or not ours to inspect
    return found


def recover(probe, steps, settle=None):
    """Probe; if silent run each (name, action) in order, probing after each, until it answers.
    An action raises Skip to refuse. Returns (answers, log lines)."""
    log = []
    if probe():
        log.append("Apollo answers; nothing to do")
        return True, log
    for name, action in steps:
        try:
            action()
        except Skip as why:
            log.append(f"{name}: skipped: {why}")
            continue
        except Failed as why:
            log.append(f"{name}: failed: {why}")
        if settle:
            settle()
        if probe():
            log.append(f"{name}: Apollo answers again")
            return True, log
        log.append(f"{name}: Apollo still silent")
    return False, log


# --- the real world -------------------------------------------------------------------

def apollo_answers():
    """One GET_ID, 500 ms, as apollo_fpga does when it looks for a debugger."""
    import usb.core
    import usb.util
    device = usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])
    if device is None:
        return False
    try:
        request_type = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR,
                                                   usb.util.CTRL_RECIPIENT_DEVICE)
        device.ctrl_transfer(request_type, REQUEST_GET_ID, data_or_wLength=256, timeout=500)
        return True
    except usb.core.USBError:
        return False


def wait_for_apollo():
    deadline = time.monotonic() + SETTLE_S
    while time.monotonic() < deadline:
        if apollo_answers():
            return True
        time.sleep(POLL_S)
    return False


def run_step(command, limit=None, prefix=("sudo", "-n")):
    """Run one command of a step; a non-zero exit or the limit is a Failed step."""
    limit = STEP_LIMIT_S if limit is None else limit
    try:
        done = subprocess.run([*prefix, *command], capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        raise Failed(f"{command[0]} timed out after {limit:g} s") from None
    logging.info("  %s -> rc=%d %s", " ".join(command), done.returncode,
                 (done.stdout + done.stderr).strip().replace("\n", " | ")[-300:])
    if done.returncode != 0:
        raise Failed(f"{command[0]} rc={done.returncode}: {done.stderr.strip()[:120]}")


def find_apollo():
    import usb.core
    return usb.core.find(idVendor=APOLLO[0], idProduct=APOLLO[1])


def bootloader_reset():
    """Vendor 0xED sent raw (the apollo CLI gates on GET_ID, which a silent Apollo fails), then
    exit-dfu once the bootloader is up. A WDT reset of the SAMD11: no hub, no cables."""
    import usb.core
    device = find_apollo()
    if device is None:
        raise Skip("Apollo is not on the bus")
    try:
        device.ctrl_transfer(0x40, REQUEST_BOOT_TO_DFU, 0, 0, None, 500)
    except usb.core.USBError as why:
        raise Failed(f"0xED not accepted: {why}") from None
    deadline = time.monotonic() + BOOTLOADER_S
    while True:
        try:
            run_step(["apollo", "exit-dfu"], limit=BOOTLOADER_S, prefix=())
            return
        except Failed:
            if time.monotonic() >= deadline:
                raise
            time.sleep(POLL_S)


def leave_bootloader():
    """A watchdog reset lands in the Saturn-V bootloader and stays there (#67): exit-dfu, then
    force-offline to take CONTROL back from the FPGA the application hands it to."""
    device = find_apollo()
    if device is None:
        raise Skip("Apollo is not on the bus")
    if "Bootloader" not in (device.product or ""):
        raise Skip("Apollo is not in the bootloader")
    run_step(["apollo", "exit-dfu"], limit=BOOTLOADER_S, prefix=())
    deadline = time.monotonic() + BOOTLOADER_S
    while True:
        try:
            run_step(["apollo", "force-offline"], limit=BOOTLOADER_S, prefix=())
            return
        except Failed:
            if time.monotonic() >= deadline:
                raise
            time.sleep(POLL_S)


def build_steps(hub, ports, reset_hub_too):
    steps = [("leave bootloader", leave_bootloader),
             ("bootloader reset", bootloader_reset),
             ("port power cycle", lambda: cycle_ports(hub, ports))]
    if reset_hub_too:
        steps.append(("hub reset", lambda: reset_hub(hub, set(ports))))
    return steps


def cycle_ports(hub, ports):
    try:
        listing = subprocess.run(["uhubctl", "-l", hub], capture_output=True, text=True,
                                 timeout=STEP_LIMIT_S).stdout
    except subprocess.TimeoutExpired:
        raise Failed(f"uhubctl listing of hub {hub} timed out after {STEP_LIMIT_S} s") from None
    if "ppps" not in listing:
        raise Skip(f"hub {hub} does not advertise per-port power switching")
    run_step(["uhubctl", "-l", hub, "-p", ",".join(map(str, ports)), "-a", "cycle", "-d", str(OFF_S)])


def reset_hub(hub, ours, sysfs=SYSFS):
    siblings = {port: dev for port, dev in devices_on_hub(hub, sysfs).items() if port not in ours}
    busy = holders(siblings.values())
    if busy:
        raise Skip("something else on the hub is in use: "
                   + ", ".join(f"pid {pid} holds {node}" for pid, node in sorted(busy.items())))
    node = describe(Path(sysfs) / hub)
    run_step(["usbreset", f"{node['busnum']:03d}/{node['devnum']:03d}"])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show where the Cynthion is; read-only")
    parser.add_argument("--port", action="append", metavar="NAME",
                        help="sysfs name of a Cynthion port (repeat for CONTROL and AUX); default: "
                             "found by id, else the last ones seen")
    parser.add_argument("--no-reset-hub", action="store_true", help="stop after the port power cycle")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])

    names = args.port or find_cynthion()
    if names and not args.port:
        REMEMBERED.write_text("\n".join(names) + "\n")
    elif not names and REMEMBERED.exists():
        names = REMEMBERED.read_text().split()
        logging.info("not enumerated; using the ports last seen: %s", " ".join(names))
    if not names:
        logging.error("no Cynthion found and none remembered: pass --port <sysfs name>")
        return 2
    hubs = {hub_and_port(name)[0] for name in names}
    if len(hubs) != 1:
        logging.error("the Cynthion's ports are on different hubs %s: pass the ports of one hub",
                      sorted(hubs))
        return 2
    hub = hubs.pop()
    ports = sorted(hub_and_port(name)[1] for name in names)
    logging.info("Cynthion on hub %s, ports %s", hub, ports)

    if args.status:
        for port, device in sorted(devices_on_hub(hub).items()):
            logging.info("  port %d: %s:%s %s%s", port, device["vid"], device["pid"],
                         device["product"], "  <- Cynthion" if port in ports else "")
        logging.info("Apollo answers: %s", apollo_answers())
        return 0

    steps = build_steps(hub, ports, reset_hub_too=not args.no_reset_hub)
    ok, log = recover(apollo_answers, steps, settle=wait_for_apollo)
    for line in log:
        logging.info(line)
    if not ok:
        logging.info("Apollo is still silent: software could not cut the board's power. Unplug every "
                     "cable of the Cynthion for a few seconds (docs/board-usb-recovery.md).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
