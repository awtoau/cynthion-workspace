#!/usr/bin/env python3
#
# Software model of the Cynthion's AUX side + the TARGET remote host, for cynthion_udc's AUX backend.
# SPDX-License-Identifier: GPL-3.0-or-later

"""Model of firmware relay + gateware FIFOs + Ep0Device semantics (docs/aux-link.md), as a virtual
USB device on vhci_hcd (cudc_usbip.py), plus the remote host on TARGET driven by `command()`.

- VM only (cudc_vm_test.py --backend aux). Nothing here is the board: every timing is MODEL.
- `command()` speaks the same lines as the stub's debugfs `ctl` (linux/cynthion_udc/README.md), so
  the existing test groups run unchanged against the real backend.
- `violations`: protocol rules the kernel backend must keep (docs/aux-link.md); tests assert empty.
"""

import os
import struct
import threading
import time

from cudc_usbip import EOVERFLOW_, EPIPE_, VirtualDevice

# ---- link constants (docs/aux-link.md section 5, 10) ----
T = dict(HELLO=0x01, SETUP=0x02, CTRL_DATA_OUT=0x03, CTRL_DATA_IN=0x04, STATUS=0x05, RESET=0x06,
         BUS_STATE=0x07, DEV_CONFIG=0x08, WAKEUP=0x09, EP_CONFIG=0x0A, EP_HALT=0x0B,
         EP_CLEAR=0x0C, EP_FLUSH=0x0D, EP_DATA=0x0E, ACK=0x10, NAK=0x11, ERROR=0x12,
         GET_STATS=0x13, STATS=0x14)
TNAME = {v: k for k, v in T.items()}
E = dict(STALE=1, BAD_TYPE=2, BAD_LEN=3, BAD_EP=4, NO_PIPE=5, NOT_CONFIGURED=6, NOT_ENABLED=7,
         STATE=8, TIMEOUT=9, OVERRUN=10, UNSUPPORTED=11, VERSION=12, TOO_BIG=13)
EV_SUSPEND, EV_RESUME, EV_VBUS_ON, EV_VBUS_OFF, EV_ADDRESS, EV_FLAGS = 1, 2, 3, 4, 5, 6
STATS_ORDER = ("stale_ctl", "stale_ep", "bad_msg", "setup_aborts", "answer_timeouts", "dequeues",
               "resets", "events_dropped")
HDR = struct.Struct("<BBHH")
AUX_MAXP = 512
CTRL_MAXP = 64
MAX_PAYLOAD = 4096
FIFO_PKTS = 4                       # 2 KiB per pipe (docs/aux-link.md section 8)

# Remote host waits: USB 2.0 9.2.6.4, same limits as the stub (cudc_stub.c) and T_SETUP_* in the spec.
LIMIT_NODATA_MS, LIMIT_IN_MS, LIMIT_OUT_MS = 50, 500, 5000

VID, PID = 0x1D50, 0x6189           # gateware/usb_ids.py "udc_bridge"
TARGET_EPS = [0x81 + i for i in range(4)] + [0x01 + i for i in range(4)]
EP_NAMES = {a: f"ep{a & 0xF}{'in' if a & 0x80 else 'out'}" for a in TARGET_EPS}


def aux_ep_of(target_addr):
    """Docs/aux-link.md section 2: aux_addr = (target_addr ^ 0x80) + 3, as (number, is_in)."""
    return (target_addr & 0x7F) + 3, not (target_addr & 0x80)


def pattern(n, start=0):
    return bytes(((start + j) * 13 + 17) & 0xFF for j in range(n))


def msg(typ, ep=0, seq=0, payload=b""):
    return HDR.pack(typ, ep, seq & 0xFFFF, len(payload)) + payload


def descriptors():
    dev = struct.pack("<BBHBBBBHHHBBBB", 18, 1, 0x0200, 0xEF, 2, 1, 64, VID, PID, 0x0100, 1, 2, 3, 1)

    def ep(addr, attr, maxp, iv=0):
        return struct.pack("<BBBBHB", 7, 5, addr, attr, maxp, iv)

    def intf(n, cls, sub, proto, neps, istr=0):
        return struct.pack("<BBBBBBBBB", 9, 4, n, 0, neps, cls, sub, proto, istr)
    body = struct.pack("<BBBBBBBB", 8, 11, 0, 2, 2, 2, 0, 0)                     # IAD
    body += intf(0, 2, 2, 0, 1)
    body += bytes([5, 0x24, 0, 0x10, 1, 5, 0x24, 1, 0, 1, 4, 0x24, 2, 2, 5, 0x24, 6, 0, 1])
    body += ep(0x82, 3, 8, 16)
    body += intf(1, 10, 0, 0, 2) + ep(0x81, 2, 512) + ep(0x01, 2, 512)
    body += intf(2, 0xFF, 0x43, 0x01, 10, 4)
    body += ep(0x83, 2, 512) + ep(0x03, 2, 512)
    for i in range(4):
        body += ep(0x84 + i, 2, 512)
    for i in range(4):
        body += ep(0x04 + i, 2, 512)
    cfg = struct.pack("<BBHBBBBB", 9, 2, 9 + len(body), 3, 1, 0, 0x80, 50) + body
    return dev, cfg


STRINGS = {1: "Great Scott Gadgets", 2: "Cynthion AUX: USB device-mode bridge (MODEL)",
           3: "MODEL0001", 4: "Cynthion UDC link"}


class Ep:
    """One TARGET endpoint as the gateware holds it (docs/aux-link.md section 8)."""

    def __init__(self, addr):
        self.addr = addr
        self.in_dir = bool(addr & 0x80)
        self.enabled = False
        self.maxp = 512
        self.tog = 0
        self.halted = self.wedged = False
        self.fifo = []                  # packets (bytes), EOP kept by being separate items
        self.naks = 0
        self.dequeues = 0
        self.pend_out = []              # AUX OUT URBs: [urb, [packets still to push]]
        self.pend_in = []               # AUX IN URBs waiting for packets; urb.acc = bytes so far

    def reset(self):
        self.enabled = False
        self.tog = 0
        self.halted = self.wedged = False
        self.fifo.clear()


class Xfer:
    """The control transfer in flight (firmware state, one per ctl_seq)."""

    def __init__(self, seq, setup):
        self.seq, self.setup = seq, setup
        self.rt, self.req, self.val, self.idx, self.wlen = struct.unpack("<BBHHH", setup)
        self.dir_in = bool(self.rt & 0x80)
        self.out_data = None
        self.reply = None               # (bytes, zero)
        self.status = None              # 0 ack, 1 stall
        self.done = False
        self.t_sent = None


class AuxModel(VirtualDevice):
    def __init__(self, log, hold_events=False, mutate=()):
        super().__init__()
        self.mutate = set(mutate)       # test-the-test: deliberately broken model rules
        self.log = log
        self.m = threading.RLock()
        self.cv = threading.Condition(self.m)
        self.dev_desc, self.cfg_desc = descriptors()
        self.cfg_value = 0
        self.line_coding = struct.pack("<IBBB", 9600, 0, 0, 8)
        self.eps = {a: Ep(a) for a in TARGET_EPS}
        self.pend_ev = []               # event IN URBs
        self.txq = []                   # messages waiting for an event URB
        self.console_in = []            # console bulk IN URBs
        self.console_data = b""
        self.hello_ev = threading.Event()
        self.violations = []
        self.stats = dict.fromkeys(STATS_ORDER, 0)
        self.relay_us = []              # (what, microseconds) MODEL
        self.hello_seen = False
        # firmware / device state
        self.ctl_seq = 0
        self.rst_seq = 0
        self.xfer = None
        self.connected = False
        self.max_speed = 2
        self.address = 0
        self.suspended = False
        self.flags_wake = self.flags_self = False
        self.testmode = 0
        self.attaches = self.detaches = self.wakeups = self.refused = 0
        self.gen = 0                    # connect generation: threads of an old one exit
        self.autoloop = True
        self.pairs = []
        self.hold_events = hold_events  # test knob: delay SETUP/RESET events on the wire
        self.held = []
        self.enum_done = threading.Event()
        self.lines = []
        self.all_lines = []

    # ---------------- logging ----------------

    def kmsg(self, text):
        """Host-side log line (kept in `lines` for the test; /dev/kmsg writes are rate limited)."""
        self.log(f"model host: {text}")
        with self.m:
            self.lines.append(f"model host: {text}")
            self.all_lines.append(f"model host: {text}")
            self.cv.notify_all()

    def violation(self, text):
        self.violations.append(text)
        self.kmsg(f"VIOLATION {text}")

    # ---------------- USB device side (what the kernel's xHCI-less vhci sees) ----------------

    def on_urb(self, urb):
        with self.m:
            ep = urb.ep
            if ep == 0:
                self._control(urb)
            elif ep == 3 and not urb.is_in:
                urb.complete(0)
                self._command(urb)
            elif ep == 3 and urb.is_in:
                if urb.length % AUX_MAXP:
                    self.violation(f"control IN URB length {urb.length} not a multiple of {AUX_MAXP}")
                self.pend_ev.append(urb)
                self._pump_events()
            elif 4 <= ep <= 7:
                e = self.eps[(ep - 3) | (0 if urb.is_in else 0x80)]
                if urb.is_in:           # AUX IN pipe <- TARGET OUT endpoint
                    if urb.length % e.maxp:
                        self.violation(f"pipe IN URB length {urb.length} not a multiple of maxp")
                    urb.acc = b""
                    e.pend_in.append(urb)
                    self._deliver_in(e)
                else:                   # AUX OUT pipe -> TARGET IN endpoint
                    e.pend_out.append([urb, None])
                    self._pump_out(e)
            elif ep == 1 and not urb.is_in:
                self.console_data += urb.data
                urb.complete(0)
                self._console_pump()
            elif ep == 1 and urb.is_in:
                self.console_in.append(urb)
                self._console_pump()
            elif ep == 2 and urb.is_in:
                pass                    # CDC notification endpoint: never any
            else:
                self.violation(f"URB to unknown endpoint {ep} in={urb.is_in}")
                urb.complete(EPIPE_)

    def on_unlink(self, urb):
        with self.m:
            for lst in [self.pend_ev, self.console_in] + [e.pend_in for e in self.eps.values()]:
                if urb in lst:
                    lst.remove(urb)
                    return True
            for e in self.eps.values():
                for item in e.pend_out:
                    if item[0] is urb:
                        e.pend_out.remove(item)
                        return True
            return urb.ep == 2

    def _console_pump(self):
        if "console" in self.mutate:
            self.console_data = b""
        while self.console_data and self.console_in:
            urb = self.console_in.pop(0)
            n = min(urb.length, len(self.console_data))
            data, self.console_data = self.console_data[:n], self.console_data[n:]
            urb.complete(0, data)

    def _control(self, urb):
        rt, req, val, idx, ln = struct.unpack("<BBHHH", urb.setup)
        data = None
        if (rt, req) == (0x80, 6):
            kind, i = val >> 8, val & 0xFF
            if kind == 1:
                data = self.dev_desc
            elif kind == 2:
                data = self.cfg_desc
            elif kind == 3:
                if i == 0:
                    data = bytes([4, 3, 9, 4])
                elif i in STRINGS:
                    s = STRINGS[i].encode("utf-16-le")
                    data = bytes([2 + len(s), 3]) + s
        elif (rt, req) == (0x00, 9):
            self.cfg_value = val
            data = b""
        elif (rt, req) == (0x01, 11):
            data = b""
        elif (rt, req) == (0x80, 8):
            data = bytes([self.cfg_value])
        elif (rt, req) in ((0x80, 0), (0x81, 0), (0x82, 0)):
            data = bytes(2)
        elif rt == 0x21 and req == 0x20:
            self.line_coding = urb.data[:7]
            data = b""
        elif rt == 0xA1 and req == 0x21:
            data = self.line_coding
        elif rt == 0x21 and req == 0x22:
            data = b""
        elif rt == 0x02 and req == 1:
            data = b""
        if data is None:
            self.kmsg(f"AUX ep0 request {rt:02x} {req:02x} {val:04x} {idx:04x} {ln}: STALL")
            urb.complete(EPIPE_)
        else:
            urb.complete(0, data[:ln] if urb.is_in else b"")

    # ---------------- the link: Cynthion -> PC ----------------

    def send(self, typ, ep=0, seq=None, payload=b""):
        with self.m:
            self.txq.append(msg(typ, ep, self.ctl_seq if seq is None else seq, payload))
            self._pump_events()

    def _send_event(self, typ, ep, seq, payload=b""):
        """SETUP / RESET go through hold_events (a test knob modelling a slow link)."""
        blob = msg(typ, ep, seq, payload)
        if self.hold_events and typ in (T["SETUP"], T["RESET"]):
            self.held.append(blob)
            return
        self.txq.append(blob)
        self._pump_events()

    def release_held(self):
        with self.m:
            self.hold_events = False
            self.txq.extend(self.held)
            self.held.clear()
            self._pump_events()

    def _pump_events(self):
        while self.txq and self.pend_ev:
            urb = self.pend_ev.pop(0)
            blob = self.txq.pop(0)
            if len(blob) > urb.length:
                urb.complete(EOVERFLOW_)
                self.violation(f"event {len(blob)} B does not fit the {urb.length} B IN URB")
                continue
            urb.complete(0, blob)

    def _flags(self):
        return int(self.flags_wake) | int(self.flags_self) << 1 | int(bool(self.testmode)) << 2

    def _bus_state(self, event, arg=0):
        self.txq.append(msg(T["BUS_STATE"], 0, 0, struct.pack("<BBH", event, arg, self._flags())))
        self._pump_events()

    # ---------------- the link: PC -> Cynthion ----------------

    def _reply(self, ok, typ, ep, seq, code=0):
        if ok:
            self.send(T["ACK"], 0, payload=struct.pack("<BBH", typ, ep, seq))
        else:
            self.send(T["NAK"], 0, payload=struct.pack("<BBHB", typ, ep, seq, code))
            if code in (E["BAD_TYPE"], E["BAD_LEN"], E["BAD_EP"], E["UNSUPPORTED"], E["STATE"]):
                self.stats["bad_msg"] += 1
            self.cv.notify_all()

    def _command(self, urb):
        d = urb.data
        if len(d) < HDR.size:
            self.violation(f"command shorter than the header ({len(d)} B)")
            return
        typ, ep, seq, ln = HDR.unpack_from(d)
        pl = d[HDR.size:]
        if len(pl) != ln:
            self.violation(f"command {TNAME.get(typ, typ)}: len {ln} but {len(pl)} B payload")
            return self._reply(False, typ, ep, seq, E["BAD_LEN"])
        if len(d) % AUX_MAXP == 0 and not urb.zero_packet:
            self.violation(f"command {TNAME.get(typ, typ)} of {len(d)} B sent without a ZLP")
        if typ != T["HELLO"] and not self.hello_seen:
            self.violation(f"command {TNAME.get(typ, typ)} before HELLO")
        now = time.monotonic()
        name = TNAME.get(typ)
        if name == "HELLO":
            self._hello(pl)
        elif name == "CTRL_DATA_IN":
            self._ctl_cmd(typ, ep, seq, pl, now)
        elif name == "STATUS":
            self._ctl_cmd(typ, ep, seq, pl, now)
        elif name in ("EP_CONFIG", "EP_HALT", "EP_CLEAR", "EP_FLUSH"):
            self._ep_cmd(name, typ, ep, seq, pl)
        elif name == "DEV_CONFIG":
            self._dev_config(pl)
            self._reply(True, typ, ep, seq)
        elif name == "WAKEUP":
            if not self.flags_wake:
                self.refused += 1
                self._reply(False, typ, ep, seq, E["NOT_ENABLED"])
            else:
                self.wakeups += 1
                self.suspended = False
                self._bus_state(EV_RESUME)
                self._reply(True, typ, ep, seq)
        elif name == "GET_STATS":
            self.send(T["STATS"], 0, 0, struct.pack("<8I", *(self.stats[k] for k in STATS_ORDER)))
        else:
            self._reply(False, typ, ep, seq, E["BAD_TYPE"])

    def _hello(self, pl):
        ver = pl[0] if pl else 0
        self.hello_seen = True
        self.xfer = None
        self.send(T["HELLO"], 0, 0, struct.pack("<BBHBBHH", 1 if ver == 1 else 0, 2, MAX_PAYLOAD, 8,
                                                  0, 1, FIFO_PKTS))
        self.hello_ev.set()

    def _ctl_cmd(self, typ, ep, seq, pl, now):
        x = self.xfer
        if seq != self.ctl_seq and "stale_ctl" not in self.mutate:
            self.stats["stale_ctl"] += 1
            return self._reply(False, typ, ep, seq, E["STALE"])
        if x is None or x.done or x.seq != seq:
            return self._reply(False, typ, ep, seq, E["STATE"])
        if x.t_sent is not None:
            self.relay_us.append((TNAME[typ], 1e6 * (now - x.t_sent)))
            x.t_sent = None
        if typ == T["CTRL_DATA_IN"]:
            if not x.dir_in or not x.wlen or x.reply is not None:
                return self._reply(False, typ, ep, seq, E["STATE"])
            x.reply = (bytes(pl), bool(ep & 1))
        else:
            if x.status is not None:
                return self._reply(False, typ, ep, seq, E["STATE"])
            x.status = pl[0] if pl else 0
        self._reply(True, typ, ep, seq)
        self.cv.notify_all()

    def _ep_cmd(self, name, typ, ep, seq, pl):
        e = self.eps.get(ep)
        if seq != self.rst_seq and "stale_ep" not in self.mutate:
            self.stats["stale_ep"] += 1
            return self._reply(False, typ, ep, seq, E["STALE"])
        if e is None:
            return self._reply(False, typ, ep, seq, E["BAD_EP"])
        if name == "EP_CONFIG":
            enable, xfer, maxp = struct.unpack_from("<BBH", pl)
            if enable:
                if xfer not in (2, 3) or maxp == 0 or maxp > 512:
                    return self._reply(False, typ, ep, seq, E["UNSUPPORTED"])
                e.reset()
                e.enabled, e.maxp = True, maxp
                self._pump_out(e)
            else:
                e.reset()
        elif not e.enabled:
            return self._reply(False, typ, ep, seq, E["NOT_CONFIGURED"])
        elif name == "EP_HALT":
            e.halted = True
            e.wedged = bool(pl and pl[0] & 1)
        elif name == "EP_CLEAR":
            e.halted = e.wedged = False
            e.tog = 0
        else:
            e.dequeues += 1
            self.stats["dequeues"] += 1
            if pl and pl[0] & 1:
                e.fifo.clear()
        self._reply(True, typ, ep, seq)
        self.cv.notify_all()

    def _dev_config(self, pl):
        connect, max_speed, selfpow = pl[0], pl[1], pl[2]
        self.flags_self = bool(selfpow)
        self.max_speed = max_speed
        if connect and not self.connected:
            self.connected = True
            self.attaches += 1
            self.gen += 1
            self.enum_done.clear()
            threading.Thread(target=self._enumerate, args=(self.gen,), daemon=True,
                             name="model-enum").start()
        elif not connect and self.connected:
            self.connected = False
            self.detaches += 1
            self.gen += 1
            self._bus_down()
            self.kmsg("detach")
            self.cv.notify_all()

    def _bus_down(self):
        self.address = 0
        self.testmode = 0
        self.suspended = False
        self.flags_wake = False
        self.pairs = []
        for e in self.eps.values():
            e.reset()
        self.xfer = None

    # ---------------- gateware: FIFOs and pipes ----------------

    def _pump_out(self, e):
        """AUX OUT pipe -> TARGET IN FIFO: accept packets while there is room (else NAK)."""
        if not e.enabled:
            return
        while e.pend_out:
            item = e.pend_out[0]
            urb = item[0]
            if item[1] is None:
                d, mp = urb.data, e.maxp
                pk = [d[i:i + mp] for i in range(0, len(d), mp)]
                if not d or (urb.zero_packet and len(d) % mp == 0):
                    pk.append(b"")
                item[1] = pk
            while item[1] and len(e.fifo) < FIFO_PKTS:
                e.fifo.append(item[1].pop(0))
            if item[1]:
                return
            e.pend_out.pop(0)
            urb.complete(0)
        self.cv.notify_all()

    def _deliver_in(self, e):
        """TARGET OUT FIFO -> AUX IN pipe: an IN URB takes packets until short or full."""
        while e.pend_in and e.fifo:
            urb = e.pend_in[0]
            pkt = e.fifo.pop(0)
            if len(urb.acc) + len(pkt) > urb.length:
                self.violation("pipe IN URB too small for a packet")
                e.pend_in.pop(0)
                urb.complete(EOVERFLOW_)
                continue
            urb.acc += pkt
            if len(pkt) < e.maxp or len(urb.acc) == urb.length:
                e.pend_in.pop(0)
                urb.complete(0, urb.acc)
        self.cv.notify_all()

    # ---------------- remote host on TARGET ----------------

    def _wait(self, pred, deadline):
        """Waits on the model condition for pred() or the deadline; True if pred() holds."""
        while not pred():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.cv.wait(left)
        return True

    def bus_reset(self):
        with self.m:
            self.ctl_seq = (self.ctl_seq + 1) & 0xFFFF
            self.rst_seq = self.ctl_seq
            self.stats["resets"] += 1
            self.address = 0
            self.xfer = None
            for e in self.eps.values():
                e.reset()
            self._send_event(T["RESET"], 0, self.rst_seq, bytes([2]))

    def set_address(self, a):
        with self.m:
            self.address = a
            self._bus_state(EV_ADDRESS, a)

    def _local(self, x):
        """Firmware's local answers (docs/usb-device-mode.md 'Locally answered').
        Returns (handled, data|None); None data = STALL."""
        rt, rq, val, idx = x.rt, x.req, x.val, x.idx
        ep = self.eps.get(idx & 0xFF)
        if rq == 0 and x.dir_in:
            if rt == 0x80:
                return True, struct.pack("<H", int(self.flags_self) | int(self.flags_wake) << 1)
            if rt == 0x81:
                return True, bytes(2)
            if rt == 0x82:
                if not idx & 0x7F:
                    return True, bytes(2)
                return True, (struct.pack("<H", int(ep.halted)) if ep and ep.enabled else None)
            return False, None
        if rq in (1, 3) and not x.dir_in:
            setf = rq == 3
            if rt == 0x00:
                if val == 1:
                    self.flags_wake = setf
                    self._bus_state(EV_FLAGS)
                    return True, b""
                if val == 2 and setf and not idx & 0xFF and 1 <= idx >> 8 <= 5:
                    self.testmode = idx >> 8
                    self._bus_state(EV_FLAGS)
                    return True, b""
                return True, None
            if rt == 0x02:
                if val != 0:
                    return True, None
                if not idx & 0x7F:
                    return True, None if setf else b""
                if not ep or not ep.enabled:
                    return True, None
                if setf:
                    ep.halted = True
                    self.txq.append(msg(T["EP_HALT"], ep.addr, 0))
                elif not ep.wedged:
                    ep.halted = False
                    ep.tog = 0
                    self.txq.append(msg(T["EP_CLEAR"], ep.addr, 0))
                self._pump_events()
                self.cv.notify_all()
                return True, b""
        return False, None

    def ctrl(self, rt, rq, val, idx, length, data=b"", what="ctl"):
        """One control transfer by the remote host. Returns (status, n, bytes, pkts) with status
        ok|stall|timeout|err (same words as the stub's ctl)."""
        setup = struct.pack("<BBHHH", rt, rq, val, idx, length)
        with self.m:
            if self.xfer and not self.xfer.done:
                self.stats["setup_aborts"] += 1
                self.xfer.done = True
                self.cv.notify_all()
            self.ctl_seq = (self.ctl_seq + 1) & 0xFFFF
            x = self.xfer = Xfer(self.ctl_seq, setup)
            handled, ldata = self._local(x)
            if handled:
                x.done = True
                if ldata is None:
                    self.kmsg(f"{what}: STALL (local)")
                    return "stall", 0, b"", []
                n = min(length, len(ldata))
                return "ok", n, ldata[:n], ([n] if x.dir_in and length else [])
            if not x.dir_in and length > MAX_PAYLOAD:
                self.stats["bad_msg"] += 1
                x.done = True
                return "stall", 0, b"", []
            limit = LIMIT_NODATA_MS if not length else LIMIT_IN_MS if x.dir_in else LIMIT_OUT_MS
            x.t_sent = time.monotonic()
            self._send_event(T["SETUP"], 0, x.seq, setup)
            if length and not x.dir_in:
                x.out_data = data[:length]
                self.txq.append(msg(T["CTRL_DATA_OUT"], 0, x.seq, x.out_data))
                self._pump_events()
            deadline = time.monotonic() + limit / 1000
            ok = self._wait(lambda: x.done or (x.reply is not None) or x.status is not None
                            or not self.connected, deadline)
            if x.done and x.reply is None and x.status is None:
                return "err", 0, b"", []      # aborted by a newer SETUP / detach
            if not ok:
                self.stats["answer_timeouts"] += 1
                x.done = True
                self.kmsg(f"{what}: no answer within {limit} ms")
                return "timeout", 0, b"", []
            x.done = True
            if x.status == 1:
                self.kmsg(f"{what}: STALL (function)")
                return "stall", 0, b"", []
            if x.dir_in and length:
                if x.reply is None:
                    return "err", 0, b"", []
                rep, zero = x.reply
                n = min(length, len(rep))
                if n and n % CTRL_MAXP == 0 and n < length and not zero:
                    self.kmsg(f"{what}: {n} of {length} bytes on a packet boundary without ZLP")
                    return "err", -71, b"", []
                pk = [CTRL_MAXP] * (n // CTRL_MAXP)
                if n % CTRL_MAXP or (n < length and n % CTRL_MAXP == 0 and zero) or n == 0:
                    pk.append(n % CTRL_MAXP)
                return "ok", n, rep[:n], pk
            if length and not x.dir_in:
                return "ok", length, b"", []
            return "ok", 0, b"", []

    def _take_in(self, e):
        """One IN token. -> ('ok', pkt, tog) | ('nak',) | ('stall',) | ('err',)."""
        if not e.enabled:
            return ("err",)
        if e.halted:
            return ("stall",)
        if not e.fifo:
            return ("nak",)
        pkt = e.fifo.pop(0)
        tog = e.tog
        e.tog ^= 1
        self._pump_out(e)
        return ("ok", pkt, tog)

    def _put_out(self, e, pkt):
        if not e.enabled:
            return ("err",)
        if e.halted:
            return ("stall",)
        if len(e.fifo) >= FIFO_PKTS:
            return ("nak",)
        e.fifo.append(pkt)
        tog = e.tog
        e.tog ^= 1
        self._deliver_in(e)
        return ("ok", tog)

    def host_in(self, addr, wait_ms, maxpkts):
        e = self.eps[addr]
        lens, togs, buf, st = [], [], b"", "ok"
        with self.m:
            while len(lens) < maxpkts:
                deadline = time.monotonic() + wait_ms / 1000
                counted = False
                while True:
                    r = self._take_in(e)
                    if r[0] != "nak":
                        break
                    if not counted:
                        e.naks += 1
                        counted = True
                    if time.monotonic() >= deadline:
                        break
                    self.cv.wait(deadline - time.monotonic())
                if r[0] != "ok":
                    st = r[0]
                    break
                buf += r[1]
                lens.append(len(r[1]))
                togs.append(r[2])
                if len(r[1]) < e.maxp:
                    break
        return st, buf, lens, togs

    def host_out(self, addr, wait_ms, lens):
        e = self.eps[addr]
        togs, off, st = [], 0, "ok"
        with self.m:
            for ln in lens:
                if ln > e.maxp:
                    return "err", togs
                pkt = pattern(ln, off)
                deadline = time.monotonic() + wait_ms / 1000
                counted = False
                while True:
                    r = self._put_out(e, pkt)
                    if r[0] != "nak":
                        break
                    if not counted:
                        e.naks += 1
                        counted = True
                    if time.monotonic() >= deadline:
                        break
                    self.cv.wait(deadline - time.monotonic())
                if r[0] != "ok":
                    st = r[0]
                    break
                off += ln
                togs.append(r[1])
        return st, togs

    # ---------------- enumeration + loopback, as the stub host does ----------------

    def _alive(self, gen):
        return self.connected and self.gen == gen

    def _enumerate(self, gen):
        def ctl(rt, rq, val, idx, ln, what, data=b""):
            if not self._alive(gen):
                raise RuntimeError("detached")
            st, n, d, _ = self.ctrl(rt, rq, val, idx, ln, data, what)
            if st != "ok":
                raise RuntimeError(f"{what}: {st}")
            return d
        try:
            self.kmsg("attach, high speed")
            self.bus_reset()
            ctl(0x80, 6, 0x0100, 0, 64, "GET_DESCRIPTOR(device, 64)")
            self.bus_reset()
            self.set_address(1)
            dev = ctl(0x80, 6, 0x0100, 0, 18, "GET_DESCRIPTOR(device)")
            vid, pid, bcd = struct.unpack_from("<HH", dev, 8) + (struct.unpack_from("<H", dev, 2)[0],)
            self.kmsg(f"device descriptor {vid:04x}:{pid:04x} bcdUSB {bcd:04x} class {dev[4]:02x} "
                      f"bMaxPacketSize0 {dev[7]} bNumConfigurations {dev[17]}")
            c9 = ctl(0x80, 6, 0x0200, 0, 9, "GET_DESCRIPTOR(config, 9)")
            total = min(struct.unpack_from("<H", c9, 2)[0], 4096)
            cfg = ctl(0x80, 6, 0x0200, 0, total, "GET_DESCRIPTOR(config)")
            intfs = self._parse(cfg)
            self.kmsg(f"config {c9[5]}: wTotalLength {total}, {len(intfs)} interfaces")
            ctl(0x00, 9, c9[5], 0, 0, "SET_CONFIGURATION")
            self.kmsg(f"configuration {c9[5]} set")
            pairs = []
            for num, alt, ein, eout in intfs:
                if alt:
                    ctl(0x01, 11, alt, num, 0, "SET_INTERFACE")
                    self.kmsg(f"interface {num} alt {alt} set")
                if ein and eout and self.autoloop:
                    pairs.append((self.eps[ein], self.eps[eout]))
                    self.kmsg(f"loopback interface {num}: ep{ein:02x} -> ep{eout:02x}")
            with self.m:
                self.pairs = [[a, b, None, 0] for a, b in pairs]
            self.kmsg("enumeration done")
            self.enum_done.set()
            self._loop(gen)
        except RuntimeError as err:
            if self._alive(gen):
                self.kmsg(f"enumeration failed: {err}")
        except Exception as err:  # noqa: BLE001
            self.kmsg(f"enumeration failed: {type(err).__name__}: {err}")

    @staticmethod
    def _parse(cfg):
        """Per interface: first alt with bulk endpoints -> (num, alt, bulk in, bulk out)."""
        out, cur, pos = {}, None, 0
        while pos + 2 <= len(cfg) and cfg[pos]:
            ln, typ = cfg[pos], cfg[pos + 1]
            if typ == 4:
                num, alt = cfg[pos + 2], cfg[pos + 3]
                ent = out.setdefault(num, [num, alt, 0, 0, False])
                cur = None if ent[4] else ent
                if cur:
                    cur[1] = alt
            elif typ == 5 and cur and (cfg[pos + 3] & 3) == 2:
                addr = cfg[pos + 2]
                if addr & 0x80 and not cur[2]:
                    cur[2] = addr
                elif not addr & 0x80 and not cur[3]:
                    cur[3] = addr
                cur[4] = True
            pos += ln
        return [tuple(v[:4]) for v in out.values() if v[4]]

    def _loop(self, gen):
        """Loopback: IN endpoint packet -> OUT endpoint, boundaries kept. Event driven."""
        with self.m:
            while self._alive(gen):
                progress = False
                for p in self.pairs:
                    ein, eout = p[0], p[1]
                    if p[2] is None:
                        r = self._take_in(ein)
                        if r[0] == "ok":
                            p[2] = r[1]
                            progress = True
                    if p[2] is not None and self._put_out(eout, p[2])[0] == "ok":
                        p[3] += len(p[2])
                        p[2] = None
                        progress = True
                if not progress:
                    self.cv.wait()

    # ---------------- test commands, same lines as the stub's debugfs ctl ----------------

    def command(self, line):
        p = line.split()
        c = p[0] if p else ""
        num = lambda s: int(s, 0)  # noqa: E731
        try:
            if c == "ctrl":
                rt, rq, val, idx, ln = (num(x) for x in p[1:6])
                data = bytes.fromhex(p[6]) if len(p) > 6 else b""
                st, n, d, pk = self.ctrl(rt, rq, val, idx, ln, data, "ctl")
                if st == "stall":
                    return "ctrl stall"
                if st == "timeout":
                    return "ctrl timeout"
                if st == "err":
                    return f"ctrl err {n or -71}"
                out = f"ctrl ok n={n}"
                if pk:
                    out += " pkts=" + ",".join(map(str, pk))
                if rt & 0x80:
                    out += " data=" + d.hex()
                return out
            if c == "in":
                st, buf, lens, togs = self.host_in(num(p[1]), num(p[2]), num(p[3]))
                return (f"in {st} bytes={len(buf)} pkts={','.join(map(str, lens))} "
                        f"tog={','.join(map(str, togs))} data={buf.hex()}")
            if c == "out":
                st, togs = self.host_out(num(p[1]), num(p[2]), [num(x) for x in p[3:]])
                return f"out {st} done={len(togs)} tog={','.join(map(str, togs))}"
            if c == "stat":
                e = self.eps.get(num(p[1]))
                if not e:
                    return "stat err ep"
                with self.m:
                    return (f"stat ok tog={e.tog} halted={int(e.halted)} wedged={int(e.wedged)} "
                            f"enabled={int(e.enabled)} naks={e.naks} dequeues={e.dequeues}")
            if c == "devstat":
                return (f"devstat ok address={self.address} wakeup={int(self.flags_wake)} "
                        f"selfpowered={int(self.flags_self)} testmode={self.testmode} "
                        f"wakeups={self.wakeups} refused={self.refused} attaches={self.attaches} "
                        f"detaches={self.detaches} suspended={int(self.suspended)}")
            if c == "suspend":
                with self.m:
                    self.suspended = True
                    self._bus_state(EV_SUSPEND)
                return "suspend ok"
            if c == "resume":
                with self.m:
                    self.suspended = False
                    self._bus_state(EV_RESUME)
                return "resume ok"
            if c == "vbus":
                with self.m:
                    self._bus_state(EV_VBUS_ON if num(p[1]) else EV_VBUS_OFF)
                return "vbus ok"
            if c == "autoloop":
                self.autoloop = bool(num(p[1]))
                return "autoloop ok"
        except (IndexError, ValueError):
            return f"{c} err usage"
        return f"{c} err unknown"

    def request_stats(self):
        """GET_STATS via the PC is not possible (the PC asks); the model reads its own counters."""
        with self.m:
            return dict(self.stats)
