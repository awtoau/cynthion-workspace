#!/usr/bin/env python3
#
# Guest half of cudc_vm_test.py: runs as root INSIDE the virtme-ng VM, never on the host.
# SPDX-License-Identifier: GPL-3.0-or-later

"""Load cynthion_udc (stub backend), enumerate configfs gadgets on it, unload, scan dmesg.

    python3 -I cudc_vm_guest.py --ko <path>/cynthion_udc.ko

- Cases: ACM, NCM, ACM+NCM; each binds, waits for the stub host to configure it, loops data.
- Output: stdout only (the host side logs it); last line `CUDC_VM_TEST RESULT: PASS|FAIL`.
"""

import argparse
import errno
import os
import re
import select
import socket
import struct
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

UDC = "cynthion_udc"
UDC_SYSFS = Path("/sys/class/udc") / UDC
CONFIGFS = Path("/sys/kernel/config")
GADGET = CONFIGFS / "usb_gadget" / "cudc"
VID, PID = 0x1D6B, 0x0104       # Linux Foundation multifunction composite gadget

# Waits for: the stub host's whole enumeration (bind -> state 'configured').
# Stub worst case = its own per-request limits (cudc_stub.c, USB 2.0 9.2.6.4): 4 descriptor
# reads x 500 ms + SET_CONFIGURATION 50 ms + up to 2 SET_INTERFACE x 50 ms = 2150 ms.
# 1.25x so the stub's own timeout message fires first. Expiry: case fails, elapsed logged.
ENUM_MS = int(1.25 * (4 * 500 + 50 + 2 * 50))

# Waits for: payload written to the gadget to come back through the stub loopback
# (IN queue -> stub workqueue -> OUT giveback -> function rx work; NCM adds a 300 us TX timer).
# Expected well under 1 ms (docs/usb-device-mode.md#increment-3-status); 10 ms covers the vCPU
# being descheduled once on the shared host. Expiry: check fails with bytes back + elapsed.
LOOP_MS = 10

BAD = re.compile(r"WARNING:|BUG:|Oops|Call Trace|general protection|UBSAN|KASAN|refcount_t:|"
                 r"list_(add|del) corruption|scheduling while atomic|sleeping function called|"
                 r"circular locking|inconsistent lock state|blocked for more than|stack-protector|"
                 r"Kernel panic")
OUR_ERRORS = re.compile(r"stub host: .*(failed|STALL|no ep0|signalled but empty)")

FAILS = []


def log(msg):
    print(f"[guest {time.monotonic():9.3f}] {msg}", flush=True)


def check(ok, what):
    log(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        FAILS.append(what)
    return ok


def run(*cmd):
    log("$ " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    for line in (p.stdout + p.stderr).splitlines():
        log(f"  {line}")
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} exited {p.returncode}")
    return p.stdout


class Kmsg:
    """Reader over /dev/kmsg; `wait_for` is event-driven (select), bounded by a deadline."""

    def __init__(self):
        self.fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
        os.lseek(self.fd, 0, os.SEEK_END)
        self.lines = []

    def _read_ready(self):
        while True:
            try:
                rec = os.read(self.fd, 8192).decode(errors="replace")
            except BlockingIOError:
                return
            except OSError as e:
                if e.errno == errno.EPIPE:      # ring buffer overwrote unread records
                    continue
                raise
            head, _, text = rec.partition(";")
            self.lines.append(text.split("\n", 1)[0])

    def drain(self):
        self._read_ready()
        return self.lines

    def wait_for(self, pattern, limit_ms, what):
        rx = re.compile(pattern)
        t0 = time.monotonic()
        deadline = t0 + limit_ms / 1000
        seen = 0
        while True:
            self._read_ready()
            for line in self.lines[seen:]:
                if rx.search(line):
                    log(f"{what}: after {1000 * (time.monotonic() - t0):.1f} ms")
                    return line
            seen = len(self.lines)
            left = deadline - time.monotonic()
            if left <= 0:
                log(f"{what}: no /{pattern}/ in dmesg within {limit_ms} ms "
                    f"(elapsed {1000 * (time.monotonic() - t0):.1f} ms)")
                return None
            select.select([self.fd], [], [], left)

    def close(self):
        os.close(self.fd)


def udc_state():
    return (UDC_SYSFS / "state").read_text().strip()


def wait_state(want, limit_ms, what):
    """Waits for the UDC sysfs `state` (udc-core sysfs_notify -> POLLPRI), bounded."""
    t0 = time.monotonic()
    deadline = t0 + limit_ms / 1000
    with open(UDC_SYSFS / "state", "rb", buffering=0) as f:
        poller = select.poll()
        poller.register(f, select.POLLPRI | select.POLLERR)
        while True:
            f.seek(0)
            state = f.read().decode().strip()
            if state == want:
                log(f"{what}: state '{state}' after {1000 * (time.monotonic() - t0):.1f} ms")
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                log(f"{what}: state '{state}', not '{want}', within {limit_ms} ms")
                return False
            poller.poll(left * 1000)


def write(path, value):
    Path(path).write_text(value)


def make_gadget(case, functions):
    GADGET.mkdir()
    write(GADGET / "idVendor", f"0x{VID:04x}")
    write(GADGET / "idProduct", f"0x{PID:04x}")
    write(GADGET / "bcdDevice", f"0x{0x0300 + case:04x}")
    write(GADGET / "bcdUSB", "0x0200")
    (GADGET / "strings/0x409").mkdir()
    write(GADGET / "strings/0x409/manufacturer", "cynthion-workspace")
    write(GADGET / "strings/0x409/product", "cudc stub test")
    write(GADGET / "strings/0x409/serialnumber", f"cudc{case}")
    cfg = GADGET / "configs/c.1"
    cfg.mkdir()
    (cfg / "strings/0x409").mkdir()
    write(cfg / "strings/0x409/configuration", "+".join(functions))
    write(cfg / "MaxPower", "100")
    for fn in functions:
        (GADGET / "functions" / f"{fn}.usb0").mkdir()
        (cfg / f"{fn}.usb0").symlink_to(GADGET / "functions" / f"{fn}.usb0")


def remove_gadget(functions):
    cfg = GADGET / "configs/c.1"
    for fn in functions:
        (cfg / f"{fn}.usb0").unlink()
    (cfg / "strings/0x409").rmdir()
    cfg.rmdir()
    for fn in functions:
        (GADGET / "functions" / f"{fn}.usb0").rmdir()
    (GADGET / "strings/0x409").rmdir()
    GADGET.rmdir()


def acm_loopback(case):
    port = (GADGET / "functions/acm.usb0/port_num").read_text().strip()
    dev = f"/dev/ttyGS{port}"
    payload = bytes((i * 7 + case) & 0xFF for i in range(2000))   # > 3 x 512-byte packets
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        t0 = time.monotonic()
        deadline = t0 + LOOP_MS / 1000
        os.write(fd, payload)
        got = b""
        while len(got) < len(payload):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                break
            got += os.read(fd, 4096)
        ms = 1000 * (time.monotonic() - t0)
        check(got == payload, f"ACM loopback {dev}: {len(got)}/{len(payload)} bytes back, "
                              f"{ms:.1f} ms (limit {LOOP_MS} ms)")
    finally:
        os.close(fd)


ETH_P_CUDC = 0x88B5      # IEEE local experimental ethertype


def ncm_loopback(case):
    ifname = (GADGET / "functions/ncm.usb0/ifname").read_text().strip()
    run("ip", "link", "set", ifname, "up")
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_CUDC))
    try:
        s.bind((ifname, ETH_P_CUDC))
        mac = s.getsockname()[4][:6]
        marker = f"cudc-ncm-loop-{case}".encode()
        frame = b"\xff" * 6 + mac + struct.pack("!H", ETH_P_CUDC) + marker.ljust(46, b"\0")
        t0 = time.monotonic()
        deadline = t0 + LOOP_MS / 1000
        s.send(frame)
        back = False
        while not back:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([s], [], [], left)[0]:
                break
            data, addr = s.recvfrom(2048)
            back = addr[2] != socket.PACKET_OUTGOING and marker in data
        ms = 1000 * (time.monotonic() - t0)
        check(back, f"NCM loopback {ifname}: frame received back, {ms:.1f} ms (limit {LOOP_MS} ms)")
    finally:
        s.close()
        run("ip", "link", "set", ifname, "down")


def run_case(case, functions):
    log(f"=== case {case}: {'+'.join(functions)} ===")
    make_gadget(case, functions)
    km = Kmsg()
    try:
        write(GADGET / "UDC", UDC)
        check(wait_state("configured", ENUM_MS, "enumeration"), "gadget state 'configured'")
        done = km.wait_for(r"stub host: enumeration (done|failed)", ENUM_MS, "stub enumeration")
        check(done is not None and "done" in done, "stub host: enumeration done")
        text = "\n".join(km.drain())
        check(re.search(rf"device descriptor {VID:04x}:{PID:04x} bcdUSB 0200 .*"
                        r"bMaxPacketSize0 64", text) is not None, "device descriptor read")
        check("configuration 1 set" in text, "SET_CONFIGURATION(1)")
        if "ncm" in functions:
            check(re.search(r"interface \d+ alt 1 set", text) is not None, "NCM data alt 1 set")
        if "acm" in functions:
            acm_loopback(case)
        if "ncm" in functions:
            ncm_loopback(case)
        write(GADGET / "UDC", "\n")     # synchronous: udc_stop has run when this returns
        check(any("gadget driver unbound" in ln for ln in km.drain()), "unbind")
        check(udc_state() == "not attached", f"state after unbind: {udc_state()}")
    finally:
        km.close()
        if (GADGET / "UDC").exists() and (GADGET / "UDC").read_text().strip():
            write(GADGET / "UDC", "\n")
        remove_gadget(functions)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ko", required=True, type=Path)
    args = ap.parse_args()

    if os.getuid() != 0 or not Path("/dev/kmsg").exists():
        log("must run as root inside the VM")
        return 2
    log(f"kernel {os.uname().release}")
    session = Kmsg()        # dmesg from here on: what the scan at the end judges
    if not CONFIGFS.joinpath("usb_gadget").exists() and not os.path.ismount(CONFIGFS):
        run("mount", "-t", "configfs", "none", str(CONFIGFS))

    have_ncm = subprocess.run(["modprobe", "-n", "usb_f_ncm"], capture_output=True).returncode == 0
    mods = ["udc_core", "libcomposite", "usb_f_acm"] + (["usb_f_ncm"] if have_ncm else [])
    for m in mods:
        run("modprobe", m)
    run("insmod", str(args.ko), "dyndbg=+p")
    check(UDC in os.listdir("/sys/class/udc"), f"/sys/class/udc lists {UDC}")

    cases = [["acm"]] + ([["ncm"], ["acm", "ncm"]] if have_ncm else [])
    if not have_ncm:
        log("usb_f_ncm not available: NCM cases skipped")
    for case, functions in enumerate(cases, 1):
        try:
            run_case(case, functions)
        except Exception as e:  # noqa: BLE001  (one case failing must not hide the rest)
            check(False, f"case {case} {'+'.join(functions)}: {type(e).__name__}: {e}")

    run("rmmod", "cynthion_udc")
    check(not UDC_SYSFS.exists(), "UDC gone after rmmod")
    for m in reversed(mods):
        run("modprobe", "-r", m)

    lines = session.drain()
    session.close()
    for ln in lines:
        log(f"  dmesg: {ln}")
    bad = [ln for ln in lines if BAD.search(ln) or OUR_ERRORS.search(ln)]
    for ln in bad:
        log(f"  bad dmesg: {ln}")
    check(not bad, f"dmesg clean ({len(lines)} lines scanned)")

    log(f"CUDC_VM_TEST RESULT: {'FAIL' if FAILS else 'PASS'}")
    for f in FAILS:
        log(f"  failed: {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
