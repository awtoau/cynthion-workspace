#!/usr/bin/env python3
#
# Gadget test for cynthion_udc, as root: inside the vng VM (cudc_vm_test.py) or on the host.
# SPDX-License-Identifier: GPL-3.0-or-later

"""Load cynthion_udc, enumerate configfs gadgets on it, unload, scan dmesg.

    python3 -I cudc_vm_guest.py --ko <path>/cynthion_udc.ko [--backend stub|aux]

- stub: the in-kernel fake host (debugfs ctl). aux: the AUX backend (cudc_aux) bound to the Python
  model of the Cynthion (cudc_aux_model.py on vhci_hcd); the same cases run through the real link.

- Cases: ACM, NCM, ACM+NCM; each binds, waits for the stub host to configure it, loops data.
- Output: stdout only (the host side logs it); last line `CUDC_VM_TEST RESULT: PASS|FAIL`.
"""

import argparse
import errno
import fcntl
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import traceback
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # python -I: script dir is not on the path

UDC = "cynthion_udc"            # aux backend: the interface's device name, found after probe
UDC_SYSFS = Path("/sys/class/udc") / UDC
MODEL = None                    # aux backend: the AuxModel (host() goes there instead of debugfs)
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
LOOP_MS = 10          # aux backend: set in main() (relay adds socket/thread hops, MODEL)

BAD = re.compile(r"WARNING:|BUG:|Oops|Call Trace|general protection|UBSAN|KASAN|refcount_t:|"
                 r"list_(add|del) corruption|scheduling while atomic|sleeping function called|"
                 r"circular locking|inconsistent lock state|blocked for more than|stack-protector|"
                 r"Kernel panic")
OUR_ERRORS = re.compile(r"(stub|model) host: (?!ctl: ).*(failed|STALL|no ep0|signalled but empty|VIOLATION)")

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
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
            _, _, text = rec.partition(";")
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


class ModelLog:
    """The model's host log (it does not write /dev/kmsg: userspace writes there are rate limited)."""

    def wait_for(self, pattern, limit_ms, what):
        rx = re.compile(pattern)
        t0 = time.monotonic()
        with MODEL.m:
            hit = MODEL._wait(lambda: any(rx.search(ln) for ln in MODEL.lines),
                              t0 + limit_ms / 1000)
            line = next((ln for ln in MODEL.lines if rx.search(ln)), None) if hit else None
        log(f"{what}: {'after %.1f ms' % (1000 * (time.monotonic() - t0)) if hit else 'NOT seen'}")
        return line

    def drain(self):
        with MODEL.m:
            return list(MODEL.lines)


def host_log(km):
    """Host-side enumeration log lines: dmesg for the stub, the model's list for the aux backend."""
    if MODEL is None:
        return km
    with MODEL.m:
        del MODEL.lines[:]
    return ModelLog()


def wait_enum(what):
    """aux: the model's remote host finished its enumeration (the stub's is synchronous)."""
    if MODEL is None:
        return True
    ok = MODEL.enum_done.wait(ENUM_MS / 1000)
    check(ok, f"{what}: remote host enumeration done")
    return ok


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
    try:
        Path(path).write_text(value)
    except OSError as e:
        raise OSError(e.errno, f"{e.strerror}: write {value!r} to {path}") from e


FFS = "cudcffs"       # FunctionFS instance name = mount source


def inst(fn):
    return FFS if fn == "ffs" else "usb0"


def make_gadget(case, functions, attributes=0x80):
    GADGET.mkdir()
    write(GADGET / "idVendor", f"0x{VID:04x}")
    write(GADGET / "idProduct", f"0x{PID:04x}")
    write(GADGET / "bcdDevice", f"0x{0x0300 + int(str(case), 16):04x}")  # BCD digits only
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
    write(cfg / "bmAttributes", f"0x{attributes:02x}")
    for fn in functions:
        (GADGET / "functions" / f"{fn}.{inst(fn)}").mkdir()
        (cfg / f"{fn}.{inst(fn)}").symlink_to(GADGET / "functions" / f"{fn}.{inst(fn)}")


def remove_gadget(functions):
    cfg = GADGET / "configs/c.1"
    for fn in functions:
        (cfg / f"{fn}.{inst(fn)}").unlink()
    (cfg / "strings/0x409").rmdir()
    cfg.rmdir()
    for fn in functions:
        (GADGET / "functions" / f"{fn}.{inst(fn)}").rmdir()
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
    def current():      # udev may rename the interface right after creation: re-read, never cache
        return (GADGET / "functions/ncm.usb0/ifname").read_text().strip()
    def link(state):
        try:
            run("ip", "link", "set", current(), state)
        except RuntimeError:        # udev renamed it between the read and ip: once more, fresh name
            run("ip", "link", "set", current(), state)
    link("up")
    ifname = current()
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
        link("down")


def run_case(case, functions):
    log(f"=== case {case}: {'+'.join(functions)} ===")
    make_gadget(case, functions)
    km = Kmsg()
    hk = host_log(km)
    try:
        write(GADGET / "UDC", UDC)
        check(wait_state("configured", ENUM_MS, "enumeration"), "gadget state 'configured'")
        done = hk.wait_for(r"(stub|model) host: enumeration (done|failed)", ENUM_MS, "enumeration")
        check(done is not None and "done" in done, "host: enumeration done")
        text = "\n".join(hk.drain())
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


# ---- stub host control: debugfs `ctl` (contract: linux/cynthion_udc/README.md) ----

CTL = Path("/sys/kernel/debug/cynthion_udc/ctl")
PAT_MUL, PAT_ADD = 13, 17      # stub `out` stream byte j = (j * 13 + 17) & 0xFF
NAK_MS = 50                     # waits for: the gadget side to queue a request after a host token.
#   Expected: a few 100 us (function work item); 50 ms covers one vCPU deschedule. Expiry: NAK result.


def pattern(n, start=0):
    return bytes(((start + j) * PAT_MUL + PAT_ADD) & 0xFF for j in range(n))


def host(cmd):
    """One stub-host command; returns (status, {k: v}). Blocks until the stub is done (it bounds itself)."""
    if MODEL is not None:
        parts = MODEL.command(cmd).split()
    else:
        CTL.write_text(cmd + "\n")
        parts = CTL.read_text().split()
    kv = dict(p.partition("=")[::2] for p in parts[2:])
    log(f"  host `{cmd}` -> {' '.join(parts)[:160]}")
    return (parts[1] if len(parts) > 1 else "none"), kv


def ctrl(rt, req, val, idx, length, data=b""):
    st, kv = host(f"ctrl {rt:#x} {req:#x} {val:#x} {idx:#x} {length} {data.hex()}")
    return st, bytes.fromhex(kv.get("data", "")), kv


def stat(ep):
    st, kv = host(f"stat {ep:#x}")
    return {k: int(v) for k, v in kv.items()} if st == "ok" else None


FN_MS = 100     # waits for: a function's worker thread to act (mass storage enables endpoints and
#   wedges from its own thread). Expected a few ms; 100 ms covers a vCPU deschedule. Expiry: check fails.


def wait_stat(ep, pred, what):
    """Polls the stub's endpoint state (event-free: the function acts on its own thread), bounded."""
    t0 = time.monotonic()
    while True:
        s = stat(ep)
        if s is not None and pred(s):
            return s
        if time.monotonic() - t0 > FN_MS / 1000:
            check(False, f"{what}: not reached within {FN_MS} ms: {s}")
            return s
        select.select([], [], [], 0.002)


def devstat():
    st, kv = host("devstat")
    return {k: int(v) for k, v in kv.items()} if st == "ok" else {}


def wait_devstat(pred):
    """Polls devstat until pred holds (the aux backend's commands reach the Cynthion asynchronously)."""
    t0 = time.monotonic()
    while True:
        d = devstat()
        if pred(d) or time.monotonic() - t0 > FN_MS / 1000:
            return d
        select.select([], [], [], 0.002)


def pkts(kv):
    return [int(x) for x in kv.get("pkts", "").split(",") if x]


def togs(kv):
    return [int(x) for x in kv.get("tog", "").split(",") if x]


def bind_and_configure(what):
    write(GADGET / "UDC", UDC)
    ok = wait_state("configured", ENUM_MS, what)
    check(ok, f"{what}: state 'configured'")
    return ok and wait_enum(what)


def unbind():
    write(GADGET / "UDC", "\n")


def cleanup_gadget(functions):
    if (GADGET / "UDC").exists() and (GADGET / "UDC").read_text().strip():
        unbind()
    remove_gadget(functions)


def wrap(name):
    def deco(fn):
        def inner():
            log(f"=== {name} ===")
            try:
                fn()
            except Exception as e:  # noqa: BLE001  (one case failing must not hide the rest)
                check(False, f"{name}: {type(e).__name__}: {e}")
                fr = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__][-1]
                log(f"  at line {fr.lineno}: {fr.line}")
        return inner
    return deco


# ---- control: GET_STATUS / SET_FEATURE / CLEAR_FEATURE, strings, class requests ----

@wrap("control requests")
def case_control():
    host("autoloop 1")
    make_gadget(10, ["acm"])
    try:
        bind_and_configure("control")
        st, d, _ = ctrl(0x80, 0x00, 0, 0, 2)
        check(st == "ok" and d == b"\0\0", f"GET_STATUS(device) = 0000 (got {st} {d.hex()})")
        st, _, _ = ctrl(0x00, 0x03, 1, 0, 0)
        check(st == "ok", "SET_FEATURE(DEVICE_REMOTE_WAKEUP)")
        st, d, _ = ctrl(0x80, 0x00, 0, 0, 2)
        check(d == b"\x02\0", f"GET_STATUS(device) shows remote wakeup: {d.hex()}")
        ctrl(0x00, 0x01, 1, 0, 0)
        st, d, _ = ctrl(0x80, 0x00, 0, 0, 2)
        check(d == b"\0\0", f"CLEAR_FEATURE(DEVICE_REMOTE_WAKEUP) clears it: {d.hex()}")
        st, _, _ = ctrl(0x00, 0x03, 2, 0x0100, 0)
        check(st == "ok" and devstat().get("testmode") == 1, "SET_FEATURE(TEST_MODE, TEST_J)")
        st, _, _ = ctrl(0x00, 0x03, 0x7F, 0, 0)
        check(st == "stall", f"SET_FEATURE(unknown feature) STALLs (got {st})")
        st, d, _ = ctrl(0x81, 0x00, 0, 0, 2)
        check(st == "ok" and d == b"\0\0", f"GET_STATUS(interface 0) = 0000 (got {st} {d.hex()})")
        st, d, _ = ctrl(0x82, 0x00, 0, 0x81, 2)
        check(st == "ok" and d == b"\0\0", f"GET_STATUS(ep 0x81) = 0000 (got {st} {d.hex()})")
        st, _, _ = ctrl(0x82, 0x00, 0, 0x8F, 2)
        check(st == "stall", f"GET_STATUS(nonexistent ep) STALLs (got {st})")
        st, _, _ = ctrl(0x02, 0x03, 0, 0x00, 0)
        check(st == "stall", f"SET_FEATURE(ENDPOINT_HALT, ep0) STALLs (got {st})")
        st, _, _ = ctrl(0x02, 0x03, 0, 0x81, 0)
        check(st == "ok", "SET_FEATURE(ENDPOINT_HALT, ep 0x81)")
        st, d, _ = ctrl(0x82, 0x00, 0, 0x81, 2)
        check(d == b"\x01\0", f"GET_STATUS(ep 0x81) halted: {d.hex()}")
        s = stat(0x81)
        check(s is not None and s["halted"] == 1, f"stub sees ep 0x81 halted: {s}")
        st, _, _ = ctrl(0x02, 0x01, 0, 0x81, 0)
        s = stat(0x81)
        check(st == "ok" and s["halted"] == 0 and s["tog"] == 0,
              f"CLEAR_FEATURE(ENDPOINT_HALT) clears halt, toggle DATA0: {s}")

        # strings
        st, dev, _ = ctrl(0x80, 0x06, 0x0100, 0, 18)
        check(st == "ok" and len(dev) == 18, "GET_DESCRIPTOR(device)")
        st, d, _ = ctrl(0x80, 0x06, 0x0300, 0, 255)
        check(d == bytes([4, 3, 9, 4]), f"string 0 = LANGID 0409 (got {d.hex()})")
        for idx, want in ((dev[14], "cynthion-workspace"), (dev[15], "cudc stub test"),
                          (dev[16], "cudc10")):
            st, d, _ = ctrl(0x80, 0x06, 0x0300 | idx, 0x0409, 255)
            got = d[2:].decode("utf-16-le") if len(d) > 2 else ""
            check(got == want, f"string {idx} = {want!r} (got {got!r})")
        st, d, _ = ctrl(0x80, 0x06, 0x03FE, 0x0409, 255)
        check(st == "stall", f"string 0xFE (absent) STALLs (got {st})")
        st, d, _ = ctrl(0x80, 0x06, 0x0300 | dev[15], 0x0407, 255)
        check(st == "stall", f"string in unsupported langid STALLs (got {st})")
        st, d, _ = ctrl(0x80, 0x06, 0x0300 | dev[15], 0x0409, 4)
        check(st == "ok" and len(d) == 4 and d[0] > 4, f"string truncated to wLength 4: {d.hex()}")

        # class requests (CDC-ACM), IN and OUT data stages
        st, cfg, _ = ctrl(0x80, 0x06, 0x0200, 0, 255)
        comm = next((cfg[i + 2] for i in range(0, len(cfg) - 8, 1)
                     if cfg[i:i + 2] == bytes([9, 4]) and cfg[i + 5:i + 7] == bytes([2, 2])), None)
        check(comm is not None, f"ACM comm interface found in config: {comm}")
        st, d, _ = ctrl(0xA1, 0x21, 0, comm or 0, 7)
        check(st == "ok" and len(d) == 7, f"GET_LINE_CODING returns 7 bytes (got {st} {d.hex()})")
        lc = struct.pack("<IBBB", 115200, 0, 0, 8)
        st, _, _ = ctrl(0x21, 0x20, 0, comm or 0, 7, lc)
        check(st == "ok", "SET_LINE_CODING (OUT data stage)")
        st, d, _ = ctrl(0xA1, 0x21, 0, comm or 0, 7)
        check(d == lc, f"GET_LINE_CODING reads back 115200 8N1 (got {d.hex()})")
        st, _, _ = ctrl(0x21, 0x22, 3, comm or 0, 0)
        check(st == "ok", "SET_CONTROL_LINE_STATE")
        st, _, _ = ctrl(0xA1, 0xFF, 0, comm or 0, 7)
        check(st == "stall", f"unsupported class request STALLs (got {st})")
        unbind()
    finally:
        cleanup_gadget(["acm"])


# ---- FunctionFS helpers (userspace function: exact control over bulk request sizes) ----

FUNCTIONFS_CLEAR_HALT = 0x6703      # _IO('g', 3)


def ffs_blobs():
    intf = struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 0xFF, 0, 0, 1)

    def ep(addr, maxp):
        return struct.pack("<BBBBHB", 7, 5, addr, 2, maxp, 0)
    fs = intf + ep(0x81, 64) + ep(0x02, 64)
    hs = intf + ep(0x81, 512) + ep(0x02, 512)
    body = struct.pack("<I", 3) + struct.pack("<II", 3, 3) + fs + hs     # flags FS|HS, counts
    desc = struct.pack("<II", 3, 8 + len(body)) + body
    sbody = struct.pack("<II", 1, 1) + struct.pack("<H", 0x409) + b"cudc ffs\0"
    strs = struct.pack("<II", 2, 8 + len(sbody)) + sbody
    return desc, strs


class Ffs:
    """FunctionFS instance mounted at /dev/cudcffs; ep1 = bulk IN, ep2 = bulk OUT, ep0 events."""

    def __init__(self):
        self.mnt = Path("/dev") / FFS
        self.mnt.mkdir(exist_ok=True)
        run("mount", "-t", "functionfs", FFS, str(self.mnt))
        self.ep0 = os.open(self.mnt / "ep0", os.O_RDWR)
        desc, strs = ffs_blobs()
        os.write(self.ep0, desc)
        os.write(self.ep0, strs)
        self.ein = self.eout = None

    def open_eps(self):
        self.ein = os.open(self.mnt / "ep1", os.O_RDWR)
        self.eout = os.open(self.mnt / "ep2", os.O_RDWR)

    def close(self):
        for fd in (self.ein, self.eout, self.ep0):
            if fd is not None:
                os.close(fd)
        self.ein = self.eout = self.ep0 = None
        run("umount", str(self.mnt))
        self.mnt.rmdir()


class Reader(threading.Thread):
    """Blocking read on a FunctionFS fd in a thread; join() is bounded."""

    def __init__(self, fd, n):
        super().__init__(daemon=True)
        self.fd, self.n, self.data, self.err = fd, n, None, None
        self.start()

    def run(self):
        try:
            self.data = os.read(self.fd, self.n)
        except OSError as e:
            self.err = e

    def result(self, limit_ms=1000):
        # waits for: stub `out` packets to land in the request. Expected < 1 ms; 1 s = vCPU stall.
        self.join(limit_ms / 1000)
        return None if self.is_alive() else self.data


class Writer(Reader):
    """Blocking write on a FunctionFS fd in a thread: an IN write completes when the host took it."""

    def run(self):
        try:
            self.data = os.write(self.fd, self.n)
        except OSError as e:
            self.err = e


def ffs_gadget(case, attributes=0x80):
    host("autoloop 0")
    make_gadget(case, ["ffs"], attributes)
    f = Ffs()
    if not bind_and_configure("ffs"):
        raise RuntimeError("ffs gadget did not configure")
    f.open_eps()
    return f


def ffs_teardown(f):
    try:
        unbind()
    finally:
        f.close()
        cleanup_gadget(["ffs"])


@wrap("bulk IN packet boundaries and toggle")
def case_packets_in():
    f = ffs_gadget(20)
    try:
        seq = []                    # every IN data toggle seen, must alternate from DATA0
        writers = []

        def pull(payload, maxpkts, want, label):
            w = Writer(f.ein, payload)
            st, kv = host(f"in 0x81 {NAK_MS} {maxpkts}")
            check(st == "ok" and pkts(kv) == want, f"IN {label}: packets {pkts(kv)} (want {want})")
            seq.extend(togs(kv))
            writers.append(w)
            check(w.result() == len(payload), f"IN {label}: function write completed")
            check(bytes.fromhex(kv.get("data", "")) == payload, f"IN {label}: data intact")

        def nak(label):
            st, kv = host(f"in 0x81 {NAK_MS} 1")
            check(st == "nak" and togs(kv) == [], f"IN {label}: NAK, nothing sent (got {st})")

        pull(pattern(100), 1, [100], "100 B short")
        pull(pattern(512), 1, [512], "512 B")
        nak("after a 512 B write: no ZLP")
        pull(pattern(1024), 2, [512, 512], "1024 B")
        nak("after a 1024 B write: no ZLP")
        pull(b"", 1, [0], "explicit ZLP")
        pull(pattern(1500), 4, [512, 512, 476], "1500 B")
        s0 = stat(0x81)
        nak("nothing queued")
        s1 = stat(0x81)
        check(s1["tog"] == s0["tog"] == (seq[-1] ^ 1), f"toggle did not advance across NAK: {s0} {s1}")
        pull(pattern(10), 1, [10], "after the NAK")
        check(seq == [i & 1 for i in range(len(seq))], f"IN toggles alternate from DATA0: {seq}")
    finally:
        ffs_teardown(f)


@wrap("bulk OUT packet boundaries and toggle")
def case_packets_out():
    f = ffs_gadget(21)
    try:
        seq = []

        def push(read_n, lens, want, label, expect_tog_run=True):
            r = Reader(f.eout, read_n)
            st, kv = host(f"out 0x01 {NAK_MS} {' '.join(map(str, lens))}")
            check(st == "ok", f"OUT {label}: host sent {lens}: {st}")
            seq.extend(togs(kv))
            got = r.result()
            check(got is not None and len(got) == want, f"OUT {label}: read returned "
                  f"{None if got is None else len(got)} (want {want})")
            if got:
                check(got == pattern(len(got)), f"OUT {label}: data intact")

        push(1024, [512, 512], 1024, "two full packets fill the request")
        push(1024, [512, 100], 612, "short packet ends the request")
        push(1024, [100], 100, "single short packet")
        push(1024, [0], 0, "ZLP alone")
        push(1024, [512, 0], 512, "512 + ZLP ends a 1024 request early")
        r1 = Reader(f.eout, 1024)
        st, kv = host(f"out 0x01 {NAK_MS} 512 512")
        seq.extend(togs(kv))
        check(r1.result() is not None and len(r1.data) == 1024, "OUT exactly-full request completes")
        push(1024, [0], 0, "ZLP after a full request lands in the next request")
        if MODEL is not None:
            # the FIFO takes FIFO_PKTS packets before the remote host sees a NAK (aux-link.md section 8)
            for i in range(4):
                st, kv = host(f"out 0x01 {NAK_MS} 100")
                seq.extend(togs(kv))
                check(st == "ok", f"OUT into the FIFO with no request queued: packet {i + 1} accepted ({st})")
        # NAK: no request queued
        s0 = stat(0x01)
        st, kv = host(f"out 0x01 {NAK_MS} 100")
        check(st == "nak", f"OUT with no request queued: NAK (got {st})")
        s1 = stat(0x01)
        check(s1["tog"] == s0["tog"] and s1["naks"] > s0["naks"],
              f"toggle unchanged across NAK, NAK counted: {s0} -> {s1}")
        push(1024, [100], 100, "after NAK")
        check(seq == [i & 1 for i in range(len(seq))], f"OUT toggles alternate from DATA0: {seq}")
    finally:
        ffs_teardown(f)


class Interrupted(Exception):
    pass


def _alarm(signum, frame):
    raise Interrupted


@wrap("halt, clear halt, dequeue")
def case_halt_dequeue():
    f = ffs_gadget(22)
    try:
        # function halts IN (wrong-direction read on an IN endpoint)
        try:
            os.read(f.ein, 512)
        except OSError as e:
            log(f"  read on IN ep -> errno {e.errno} (expected: halted)")
        s = wait_stat(0x81, lambda s: s["halted"] == 1, "set_halt reaches the Cynthion")
        check(s["halted"] == 1, f"usb_ep_set_halt reaches the stub: {s}")
        st, kv = host(f"in 0x81 {NAK_MS} 1")
        check(st == "stall", f"IN token to a halted endpoint: STALL (got {st})")
        st, d, _ = ctrl(0x82, 0x00, 0, 0x81, 2)
        check(d == b"\x01\0", f"GET_STATUS shows the function's halt: {d.hex()}")
        st, _, _ = ctrl(0x02, 0x01, 0, 0x81, 0)
        s = stat(0x81)
        check(s["halted"] == 0 and s["tog"] == 0, f"host CLEAR_FEATURE clears a function halt: {s}")
        w = Writer(f.ein, pattern(10))
        st, kv = host(f"in 0x81 {NAK_MS} 1")
        check(st == "ok" and togs(kv) == [0], f"IN works again, DATA0 first: {st} {togs(kv)}")
        w.result()

        # host halts OUT; function clears it through the ioctl
        ctrl(0x02, 0x03, 0, 0x01, 0)
        st, kv = host(f"out 0x01 {NAK_MS} 100")
        check(st == "stall", f"OUT token to a host-halted endpoint: STALL (got {st})")
        fcntl.ioctl(f.eout, FUNCTIONFS_CLEAR_HALT)
        s = wait_stat(0x01, lambda s: s["halted"] == 0, "clear_halt reaches the Cynthion")
        check(s["halted"] == 0 and s["tog"] == 0, f"usb_ep_clear_halt reaches the stub: {s}")

        # dequeue: a blocked read is interrupted, the request leaves the stub
        d0 = stat(0x01)["dequeues"]
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            os.read(f.eout, 1024)
            check(False, "blocked read was not interrupted")
        except Interrupted:
            check(True, "blocked read interrupted (usb_ep_dequeue)")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
        s = wait_stat(0x01, lambda s: s["dequeues"] == d0 + 1, "dequeue reaches the Cynthion")
        check(s["dequeues"] == d0 + 1, "stub saw the dequeue")
        r = Reader(f.eout, 1024)
        st, kv = host(f"out 0x01 {NAK_MS} 64")
        check(st == "ok" and r.result() is not None and len(r.data) == 64,
              "OUT works after a dequeue")
    finally:
        ffs_teardown(f)


@wrap("EP0 data stage: ZLP, delayed status, stall")
def case_ep0():
    f = ffs_gadget(23)
    try:
        def answer(fn):
            def run_thread():
                while True:
                    ev = os.read(f.ep0, 12)
                    if len(ev) == 12 and ev[8] == 4:       # FUNCTIONFS_SETUP
                        fn()
                        return
            t = threading.Thread(target=run_thread, daemon=True)
            t.start()
            return t

        def drain():
            for _ in range(8):       # BIND/ENABLE events queued before the first SETUP
                r, _, _ = select.select([f.ep0], [], [], 0)
                if not r:
                    return
                os.read(f.ep0, 12)
        drain()
        # vendor IN, interface 0: answer 128 B to wLength 200 -> req->zero -> packets 64,64,0
        t = answer(lambda: os.write(f.ep0, pattern(128)))
        st, d, kv = ctrl(0xC1, 0x5A, 0, 0, 200)
        t.join(1)
        check(st == "ok" and d == pattern(128) and pkts(kv) == [64, 64, 0],
              f"EP0 IN 128 B of wLength 200: packets {pkts(kv)} (want [64, 64, 0])")
        t = answer(lambda: os.write(f.ep0, pattern(128)))
        st, d, kv = ctrl(0xC1, 0x5A, 0, 0, 128)
        t.join(1)
        check(st == "ok" and pkts(kv) == [64, 64], f"EP0 IN 128 B of wLength 128: no ZLP {pkts(kv)}")
        t = answer(lambda: os.write(f.ep0, pattern(20)))
        st, d, kv = ctrl(0xC1, 0x5A, 0, 0, 200)
        t.join(1)
        check(st == "ok" and pkts(kv) == [20], f"EP0 IN 20 B short packet: {pkts(kv)}")
        # vendor OUT with data
        got = []
        t = answer(lambda: got.append(os.read(f.ep0, 64)))
        st, _, _ = ctrl(0x41, 0x5B, 0, 0, 33, pattern(33))
        t.join(1)
        check(st == "ok" and got == [pattern(33)], f"EP0 OUT data stage delivered: {got}")
        # no-data request: FunctionFS answers with USB_GADGET_DELAYED_STATUS, status comes later
        t = answer(lambda: os.read(f.ep0, 0))
        st, _, _ = ctrl(0x41, 0x5C, 0, 0, 0)
        t.join(1)
        check(st == "ok", f"delayed-status request completes (got {st})")
        # function stalls: wrong-direction I/O on ep0
        def stall():
            try:
                os.read(f.ep0, 1)
            except OSError:
                pass
        t = answer(stall)
        st, _, _ = ctrl(0xC1, 0x5D, 0, 0, 8)
        t.join(1)
        check(st == "stall", f"function STALL reaches the host (got {st})")
        # a new SETUP aborts an unanswered control transfer
        answer(lambda: None)
        st, _, _ = ctrl(0xC1, 0x5E, 0, 0, 8)
        check(st in ("timeout", "stall"), f"unanswered request does not hang the stub (got {st})")
    finally:
        ffs_teardown(f)


# ---- wedge (mass storage: an invalid CBW wedges bulk-in until Bulk-Only Reset) ----

@wrap("wedge")
def case_wedge():
    host("autoloop 0")
    lun = Path("/tmp/cudc-lun.img")
    lun.write_bytes(bytes(1 << 20))
    make_gadget(30, ["mass_storage"])
    write(GADGET / "functions/mass_storage.usb0/lun.0/file", str(lun))
    try:
        bind_and_configure("mass storage")
        wait_stat(0x01, lambda s: s["enabled"], "mass storage bulk-out enabled")
        wait_stat(0x81, lambda s: s["enabled"], "mass storage bulk-in enabled")
        # bad CBW: 31 bytes, wrong signature
        st, _kv = host(f"out 0x01 {NAK_MS} 31")
        check(st == "ok", f"bad CBW accepted by the OUT endpoint (got {st})")
        s = wait_stat(0x81, lambda s: s["wedged"], "bulk-in wedged")
        check(s["halted"] == 1 and s["wedged"] == 1, f"bulk-in wedged by the function: {s}")
        ctrl(0x02, 0x01, 0, 0x81, 0)
        s = stat(0x81)
        check(s["halted"] == 1, f"host CLEAR_FEATURE does not clear a wedged endpoint: {s}")
        st, d, _ = ctrl(0x82, 0x00, 0, 0x81, 2)
        check(d == b"\x01\0", f"GET_STATUS still shows halt: {d.hex()}")
        _, cfg, _ = ctrl(0x80, 0x06, 0x0200, 0, 255)
        intf = next((cfg[i + 2] for i in range(len(cfg) - 8)
                     if cfg[i:i + 2] == bytes([9, 4]) and cfg[i + 5] == 8), 0)
        st, _, _ = ctrl(0x21, 0xFF, 0, intf, 0)        # Bulk-Only Mass Storage Reset
        s = wait_stat(0x81, lambda s: not s["halted"], "reset clears the wedge")
        check(st == "ok" and s["halted"] == 0 and s["wedged"] == 0 and s["tog"] == 0,
              f"Bulk-Only Reset clears the wedge, DATA0: {st} {s}")
        unbind()
    finally:
        cleanup_gadget(["mass_storage"])
        lun.unlink(missing_ok=True)


# ---- suspend / resume / remote wakeup / VBUS ----

@wrap("suspend, resume, remote wakeup, VBUS")
def case_power():
    host("autoloop 1")
    make_gadget(40, ["acm"], attributes=0xA0)      # bus powered + remote wakeup
    km = Kmsg()
    try:
        bind_and_configure("power")
        host("suspend")
        check(wait_state("suspended", 100, "suspend"), "host suspend: state 'suspended'")
        host("resume")
        check(wait_state("configured", 100, "resume"), "host resume: state 'configured'")
        d0 = devstat()
        (UDC_SYSFS / "srp").write_text("1")
        d1 = wait_devstat(lambda d: d["refused"] > d0["refused"] or d["wakeups"] > d0["wakeups"])
        check(d1["refused"] == d0["refused"] + 1 and d1["wakeups"] == d0["wakeups"],
              f"wakeup refused while the host has not enabled it: {d0} -> {d1}")
        ctrl(0x00, 0x03, 1, 0, 0)
        host("suspend")
        wait_state("suspended", 100, "suspend")
        (UDC_SYSFS / "srp").write_text("1")
        check(wait_state("configured", 100, "remote wakeup"), "remote wakeup resumes the bus")
        d2 = wait_devstat(lambda d: d["wakeups"] > d1["wakeups"])
        check(d2["wakeups"] == d1["wakeups"] + 1, f"wakeup counted: {d2}")
        # VBUS off: gadget detaches; VBUS on: re-attach and re-enumerate
        host("vbus 0")
        check(wait_state("not attached", 500, "vbus off"), "VBUS off: state 'not attached'")
        t0 = time.monotonic()
        while time.monotonic() - t0 < FN_MS / 1000 and devstat()["detaches"] <= d2["detaches"]:
            select.select([], [], [], 0.002)
        d3 = devstat()
        check(d3["detaches"] > d2["detaches"], f"stub saw the disconnect: {d3}")
        host("vbus 1")
        check(wait_state("configured", ENUM_MS, "vbus on"), "VBUS on: re-enumerates to 'configured'")
        wait_enum("vbus on")
        acm_loopback(40)
        unbind()
    finally:
        km.close()
        cleanup_gadget(["acm"])


# ---- dummy_hcd reference: the same configfs gadget must present the same descriptors ----

def stub_descriptors():
    _, dev, _ = ctrl(0x80, 0x06, 0x0100, 0, 18)
    _, c9, _ = ctrl(0x80, 0x06, 0x0200, 0, 9)
    total = struct.unpack_from("<H", c9, 2)[0]
    _, cfg, _ = ctrl(0x80, 0x06, 0x0200, 0, total)
    return dev, cfg


def mask_ep_addresses(cfg):
    """Zero bEndpointAddress low nibble: the UDCs' endpoint sets differ (4+4 vs dummy_hcd's 15)."""
    b, i, dirs = bytearray(cfg), 0, []
    while i + 2 <= len(b) and b[i] > 0:
        if b[i + 1] == 5:
            dirs.append(b[i + 2] & 0x80)
            b[i + 2] &= 0x80
        i += b[i]
    return bytes(b), dirs


def dummy_descriptors(functions, case):
    udc = "dummy_udc.0"
    make_gadget(case, functions)
    try:
        write(GADGET / "UDC", udc)
        want = f"cudc{case}"
        deadline = time.monotonic() + 5     # waits for: dummy_hcd + hub enumeration; ~100 ms in practice
        while time.monotonic() < deadline:
            for d in Path("/sys/bus/usb/devices").glob("[0-9]*"):
                if (d / "serial").exists() and (d / "serial").read_text().strip() == want:
                    raw = (d / "descriptors").read_bytes()
                    return raw[:18], raw[18:]
            select.select([], [], [], 0.02)
        raise RuntimeError(f"dummy_hcd device with serial {want} did not appear")
    finally:
        cleanup_gadget(functions)


@wrap("dummy_hcd reference")
def case_dummy():
    run("modprobe", "dummy_hcd")
    try:
        for n, functions in enumerate([["acm"], ["ncm"], ["acm", "ncm"]], 50):
            host("autoloop 1")
            make_gadget(n, functions)
            try:
                bind_and_configure(f"stub {'+'.join(functions)}")
                sdev, scfg = stub_descriptors()
                unbind()
            finally:
                cleanup_gadget(functions)
            ddev, dcfg = dummy_descriptors(functions, n)
            name = "+".join(functions)
            check(sdev == ddev, f"{name}: device descriptor identical ({sdev.hex()} vs {ddev.hex()})")
            ms, mdirs = mask_ep_addresses(scfg)
            md, ddirs = mask_ep_addresses(dcfg)
            check(ms == md and mdirs == ddirs,
                  f"{name}: config descriptor identical except endpoint numbers "
                  f"({len(scfg)} vs {len(dcfg)} bytes)")
            log(f"  {name}: endpoint addresses stub {[b for b in scfg_eps(scfg)]} "
                f"dummy {[b for b in scfg_eps(dcfg)]}")
    finally:
        run("modprobe", "-r", "dummy_hcd")


def scfg_eps(cfg):
    out, i = [], 0
    while i + 2 <= len(cfg) and cfg[i] > 0:
        if cfg[i + 1] == 5:
            out.append(f"{cfg[i + 2]:02x}")
        i += cfg[i]
    return out


# ---- AUX backend only: link rules the stub cannot show (docs/aux-link.md) ----

def model_wait(pred, limit_ms, what):
    """Waits on the model's condition for pred(), bounded; the model notifies on every change."""
    t0 = time.monotonic()
    deadline = t0 + limit_ms / 1000
    with MODEL.m:
        ok = MODEL._wait(pred, deadline)
    log(f"{what}: {'reached' if ok else 'NOT reached'} after {1000 * (time.monotonic() - t0):.1f} ms")
    return ok


def ffs_setup_event(f):
    """Blocks until FunctionFS delivers a SETUP event (bounded by the caller's own timers)."""
    while True:
        ev = os.read(f.ep0, 12)
        if len(ev) == 12 and ev[8] == 4:
            return


def ffs_drain(f):
    for _ in range(8):
        if not select.select([f.ep0], [], [], 0)[0]:
            return
        os.read(f.ep0, 12)


@wrap("link: control messages of 512 B (ZLP) and 4102 B, both directions")
def case_aux_messages():
    f = ffs_gadget(60)
    try:
        ffs_drain(f)

        def answer(fn):
            def run_thread():
                ffs_setup_event(f)
                fn()
            t = threading.Thread(target=run_thread, daemon=True)
            t.start()
            return t
        for n in (506, 4090, 4096):     # 6 + n = 512 (needs a ZLP), 4096, 4102
            t = answer(lambda n=n: os.write(f.ep0, pattern(n)))
            st, d, _ = ctrl(0xC1, 0x5A, 0, 0, 4096)
            t.join(1)
            check(st == "ok" and d == pattern(n), f"vendor IN reply of {n} B through the link ({st}, {len(d)} B)")
        for n in (506, 4090, 4096):
            got = []
            t = answer(lambda: got.append(os.read(f.ep0, 4096)))
            st, _, _ = ctrl(0x41, 0x5B, 0, 0, n, pattern(n))
            t.join(1)
            check(st == "ok" and got == [pattern(n)], f"vendor OUT data of {n} B through the link ({st})")
    finally:
        ffs_teardown(f)


@wrap("link: stale control reply is dropped and counted")
def case_aux_stale_ctl():
    f = ffs_gadget(61)
    try:
        ffs_drain(f)
        base = MODEL.stats["stale_ctl"]
        res = {}
        first = threading.Thread(target=lambda: res.setdefault("a", ctrl(0xC1, 0x5A, 0, 0, 8)), daemon=True)
        first.start()
        ffs_setup_event(f)
        with MODEL.m:
            MODEL.hold_events = True            # SETUP #2 is acked by the gateware but late on the wire
        second = threading.Thread(target=lambda: res.setdefault("b", ctrl(0xC1, 0x5B, 0, 0, 8)), daemon=True)
        second.start()
        check(model_wait(lambda: len(MODEL.held) == 1, 1000, "SETUP #2 held"), "SETUP #2 reached the gateware")
        os.write(f.ep0, pattern(8))              # the function answers #1 with the epoch it knows
        check(model_wait(lambda: MODEL.stats["stale_ctl"] == base + 1, 1000, "stale count"),
              f"late reply for the aborted transfer counted stale ({MODEL.stats['stale_ctl'] - base})")
        first.join(1)
        check(res.get("a", ("?",))[0] == "err", f"aborted transfer ended without data: {res.get('a')}")
        MODEL.release_held()
        ffs_setup_event(f)
        os.write(f.ep0, pattern(8, 3))
        second.join(1)
        check(res.get("b", ("?",))[0] == "ok" and res["b"][1] == pattern(8, 3),
              f"the next transfer completes normally: {res.get('b')}")
        check(MODEL.stats["stale_ctl"] == base + 1, "exactly one stale reply in total")
    finally:
        MODEL.release_held()
        ffs_teardown(f)


@wrap("link: endpoint command from before a bus reset is dropped and counted")
def case_aux_stale_ep():
    f = ffs_gadget(62)
    try:
        base = MODEL.stats["stale_ep"]
        with MODEL.m:
            MODEL.hold_events = True            # RESET is on the wire late
        MODEL.bus_reset()
        try:
            os.read(f.ein, 512)                 # wrong direction: the function halts the IN endpoint
        except OSError:
            pass
        check(model_wait(lambda: MODEL.stats["stale_ep"] == base + 1, 1000, "stale endpoint command"),
              f"EP_HALT with the old reset epoch counted stale ({MODEL.stats['stale_ep'] - base})")
        MODEL.release_held()
        ctrl(0x80, 0x06, 0x0100, 0, 18)     # round trip through the ordered event queue: RESET is handled
        check(not stat(0x81)["halted"], "the dropped EP_HALT did not halt the endpoint")
    finally:
        MODEL.release_held()
        ffs_teardown(f)


def console_tty():
    for d in Path("/sys/bus/usb/devices").glob("[0-9]*"):
        if (d / "serial").exists() and (d / "serial").read_text().strip() == "MODEL0001":
            for t in d.glob("*/tty/ttyACM*"):
                return f"/dev/{t.name}"
    return None


@wrap("link: AUX console keeps working next to the bridge")
def case_aux_console():
    host("autoloop 1")
    tty_dev = console_tty()
    check(tty_dev is not None, f"AUX console tty found: {tty_dev}")
    if not tty_dev:
        return
    make_gadget(63, ["acm"])
    fd = os.open(tty_dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)

        def echo(tag):
            msg = f"console {tag}\n".encode()
            os.write(fd, msg)
            got, deadline = b"", time.monotonic() + LOOP_MS / 1000
            while len(got) < len(msg) and time.monotonic() < deadline:
                if select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                    got += os.read(fd, 256)
            check(got == msg, f"console echo {tag}: {got!r}")
        echo("before")
        bind_and_configure("console coexistence")
        acm_loopback(63)
        echo("during")
        unbind()
        echo("after")
    finally:
        os.close(fd)
        cleanup_gadget(["acm"])


EXTRA_CASES = [case_control, case_packets_in, case_packets_out, case_halt_dequeue, case_ep0,
               case_wedge, case_power, case_dummy]
AUX_CASES = [case_aux_messages, case_aux_stale_ctl, case_aux_stale_ep, case_aux_console]


# Waits for: vhci port connect + usb core enumeration of the model + cudc_aux probe (HELLO).
# Expected ~0.3 s (hub reset/recovery delays dominate); limit 2 s. Expiry: check fails, elapsed logged.
PROBE_MS = 2000


def start_aux(args):
    global UDC, UDC_SYSFS, MODEL, LOOP_MS
    from cudc_aux_model import AuxModel
    run("insmod", str(args.ko), "stub=0", "dyndbg=+p")
    LOOP_MS = 100           # 3x the worst loopback seen over 8 runs (34 ms, host loadavg ~30); MODEL
    MODEL = AuxModel(log, mutate=args.model_mutate)
    km = Kmsg()
    t0 = time.monotonic()
    MODEL.attach()
    ok = MODEL.hello_ev.wait(PROBE_MS / 1000)
    check(ok, f"cudc_aux probed the model and exchanged HELLO ({1000 * (time.monotonic() - t0):.0f} ms)")
    km.wait_for(r"UDC registered, backend aux", 500, "UDC registered")
    km.close()
    names = [n for n in os.listdir("/sys/class/udc") if not n.startswith("dummy_udc")]
    check(len(names) == 1, f"one cynthion UDC listed: {names}")
    UDC = names[0] if names else UDC
    UDC_SYSFS = Path("/sys/class/udc") / UDC


def finish_aux():
    st = MODEL.stats
    bad = [ln for ln in MODEL.all_lines if re.search(r"failed|VIOLATION", ln)]
    check(not bad, f"model host log clean: {bad[:3]}")
    check(not MODEL.violations, f"no link protocol violations by the kernel: {MODEL.violations[:3]}")
    check(st["stale_ctl"] == 1 and st["stale_ep"] == 1 and st["bad_msg"] == 0,
          f"only the two deliberate stale commands were dropped, no malformed ones: {st}")
    rel = sorted(us for _, us in MODEL.relay_us)
    if rel:
        log(f"MODEL relay SETUP -> reply received: n={len(rel)} median {rel[len(rel) // 2]:.0f} us "
            f"p90 {rel[int(len(rel) * 0.9)]:.0f} us max {rel[-1]:.0f} us (software model, not USB timing)")
    MODEL.detach()
    deadline = time.monotonic() + 2         # waits for: usb disconnect -> cudc_aux disconnect -> UDC gone
    while time.monotonic() < deadline and any(not n.startswith("dummy_udc")
                                              for n in os.listdir("/sys/class/udc")):
        select.select([], [], [], 0.01)


def main():
    global UDC, UDC_SYSFS, MODEL, LOOP_MS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ko", required=True, type=Path)
    ap.add_argument("--backend", choices=("stub", "aux"), default="stub")
    ap.add_argument("--model-mutate", action="append", default=[],
                    help="aux: break one model rule (stale_ctl, stale_ep, console) - the run must FAIL")
    args = ap.parse_args()

    if os.getuid() != 0 or not Path("/dev/kmsg").exists():
        log("must run as root inside the VM")
        return 2
    log(f"kernel {os.uname().release}")
    session = Kmsg()        # dmesg from here on: what the scan at the end judges
    if not CONFIGFS.joinpath("usb_gadget").exists() and not os.path.ismount(CONFIGFS):
        run("mount", "-t", "configfs", "none", str(CONFIGFS))

    have_ncm = subprocess.run(["modprobe", "-n", "usb_f_ncm"], capture_output=True,
                              check=False).returncode == 0
    mods = ["udc_core", "libcomposite", "usb_f_acm", "usb_f_fs", "usb_f_mass_storage"] + (
        ["usb_f_ncm"] if have_ncm else [])
    if args.backend == "aux":
        mods += ["vhci_hcd", "cdc_acm"]
    if not os.path.ismount("/sys/kernel/debug"):
        run("mount", "-t", "debugfs", "none", "/sys/kernel/debug")
    for m in mods:
        run("modprobe", m)
    if args.backend == "aux":
        start_aux(args)
    else:
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

    for case_fn in EXTRA_CASES + (AUX_CASES if args.backend == "aux" else []):
        case_fn()

    if MODEL is not None:
        finish_aux()
    run("rmmod", "cynthion_udc")
    check(not UDC_SYSFS.exists(), "UDC gone after rmmod")
    for m in reversed(mods):
        run("modprobe", "-r", m)

    lines = session.drain()
    session.close()
    for ln in lines:
        log(f"  dmesg: {ln}")
    pc = sorted(int(m.group(1)) for ln in lines for m in [re.search(r"relay: .* after (\d+) us", ln)] if m)
    if pc:
        log(f"MODEL kernel SETUP received -> reply submitted: n={len(pc)} median {pc[len(pc) // 2]} us "
            f"p90 {pc[int(len(pc) * 0.9)]} us max {pc[-1]} us (driver + gadget + function, no USB)")
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
