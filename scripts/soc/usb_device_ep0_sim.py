#!/usr/bin/env python3
#
# usbcore.device.Ep0Device against a byte-level host stimulus: handshakes, toggles, seq rules, speed.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Directed tests of the device engine's EP0 and its CPU command port, without a host engine. #612 S2/S3.

    python3 scripts/soc/usb_device_ep0_sim.py            # all sections
    python3 scripts/soc/usb_device_ep0_sim.py setup stale

Sections (each its own simulation):
- speed: bus reset -> chirp K -> three host K-J pairs -> HS; without pairs -> FS.
- control: SETUP/ACK, IN NAK until the CPU answers, DATA1 first, retransmit without ACK,
  status OUT, STALL until the next SETUP, wrong address / endpoint ignored, bad CRC ignored.
- address: SET_ADDRESS applies only after the host ACKs the status ZLP.
- outdata: OUT data stage, NAK while the buffer is held, duplicate ACKed but dropped.
- stale: commands carrying an old `seq` are dropped and counted; a new SETUP aborts the transfer.
- sof: SOF tokens give frame / microframe / count; bad CRC5 ignored; no response.
- suspend: 3 ms idle -> suspended + event; activity defers it; K resume (host) and remote wakeup
  (device drives K) return to the prior speed; SE0 while suspended = reset; HS probes via FS first.
- vbus: session loss detaches (term off, address and endpoints cleared); return re-attaches.
- Bulk endpoints (EP1-4), PING, NYET, halt, toggles, FIFOs: `usb_device_bulk_sim.py`.
- `--negative`: the `stale` section's "old" commands carry the current seq instead; it must FAIL
  (the stale checks can fail).

Time constants are divided down (the engine's class attributes), like model.py does for the host.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, os.environ.get("USB_DEVICE_GATEWARE", str(ROOT / "gateware")))     # mutants: usb_device_mutants.py
sys.path.insert(0, str(ROOT / "scripts"))

from amaranth import ClockDomain, Module
from amaranth.sim import Simulator
from devlog import emit
from sim_check_harness import Checks
from usbcore.crc import crc5_reference, crc16_reference
from usbcore.device import (CMD_IN, CMD_OACK, CMD_STALL, CMD_STATUS, CMD_WAKEUP, EV_RESUME, EV_SUSPEND,
                            EV_VBUS, Ep0Device, Stage)
from usbcore.pid import PID

USB_PERIOD = 1 / 60e6
SCALE = {"CHIRP_K_CYCLES": 300, "CHIRP_WAIT_CYCLES": 400, "HS_RESET_CYCLES": 500, "FS_RESET_CYCLES": 40,
         "SUSPEND_CYCLES": 600, "PROBE_CYCLES": 60, "RESUME_K_CYCLES": 200, "DEBOUNCE_CYCLES": 16}
K, J, SE0 = 0b10, 0b01, 0b00
GET_DESC_DEV = bytes([0x80, 6, 0, 1, 0, 0, 18, 0])
DEV18 = bytes(range(0x40, 0x40 + 18))
# Waits below: a response comes within the HS turnaround (24 clocks, timer.HS_COUNTS) plus the
# engine's registered strobes and the 3-byte packet itself; 60 clocks, 2.5x.
RESPONSE_CLOCKS = 60

NEGATIVE = False        # --negative: see the module docstring


def token(pid, addr, endp):
    body = addr | endp << 7
    crc = crc5_reference(body)
    return bytes([PID(pid).byte(), body & 0xFF, (body >> 8) | crc << 3])


def data_packet(pid, payload, *, bad_crc=False):
    crc = crc16_reference(payload) ^ (1 if bad_crc else 0)
    return bytes([PID(pid).byte()]) + payload + bytes([crc & 0xFF, crc >> 8])


class Bus:
    """Host side of the UTMI link: sends packets on rx, collects what the engine transmits."""

    def __init__(self, ctx, dev):
        self.ctx, self.dev = ctx, dev
        self.tx_packets = []
        self.cycles = 0
        ctx.set(dev.utmi.tx_ready, 1)
        ctx.set(dev.utmi.line_state, J)

    async def tick(self, n=1):
        for _ in range(n):
            await self.ctx.tick("usb")
            self.cycles += 1
            u = self.dev.utmi
            if self.ctx.get(u.tx_valid) and self.ctx.get(u.op_mode) == 0:
                if self._cur is None:
                    self._cur = []
                self._cur.append(int(self.ctx.get(u.tx_data)))
            elif self._cur is not None:
                self.tx_packets.append(self._cur)
                self._cur = None

    _cur = None

    async def send(self, raw):
        u, c = self.dev.utmi, self.ctx
        c.set(u.rx_active, 1)
        c.set(u.line_state, SE0)
        await self.tick(3)                  # SYNC lead-in, as the model wire has
        for b in raw:
            c.set(u.rx_data, b)
            c.set(u.rx_valid, 1)
            await self.tick()
        c.set(u.rx_valid, 0)
        c.set(u.rx_active, 0)
        c.set(u.line_state, J)
        await self.tick()

    async def transact(self, *packets, wait=RESPONSE_CLOCKS):
        """Sends packets back to back; returns the engine's reply packet (list of bytes) or None."""
        before = len(self.tx_packets)
        for raw in packets:
            await self.send(raw)
        for _ in range(wait):
            await self.tick()
            if len(self.tx_packets) > before:
                return self.tx_packets[-1]
        return None

    async def cpu(self, cmd, seq, **kw):
        c, e = self.ctx, self.dev
        c.set(e.cmd_seq, seq)
        c.set(e.cmd, cmd)
        c.set(e.cmd_len, kw.get("length", 0))
        c.set(e.cmd_last, kw.get("last", 0))
        c.set(e.cmd_addr, kw.get("addr", 0))
        c.set(e.cmd_addr_valid, int("addr" in kw))
        c.set(e.cmd_ep, kw.get("ep", 0))
        c.set(e.cmd_arg, kw.get("arg", 0))
        c.set(e.cmd_valid, 1)
        await self.tick()
        c.set(e.cmd_valid, 0)
        await self.tick()

    async def load_in(self, payload):
        for i, b in enumerate(payload):
            self.ctx.set(self.dev.i_addr, i)
            self.ctx.set(self.dev.i_data, b)
            self.ctx.set(self.dev.i_we, 1)
            await self.tick()
        self.ctx.set(self.dev.i_we, 0)
        await self.tick()

    def get(self, sig):
        return int(self.ctx.get(sig))


def handshake(pid):
    return [PID(pid).byte()]


def is_data(pkt, pid, payload):
    return pkt == list(data_packet(pid, payload))


async def setup(bus, addr, req8, *, bad_crc=False):
    return await bus.transact(token(PID.SETUP, addr, 0), data_packet(PID.DATA0, req8, bad_crc=bad_crc))


def build(sim_body, *, extra=(), **dev_kw):
    """`extra`: further testbench coroutines `f(ctx, dev)` running beside `sim_body`."""
    m = Module()
    m.domains.usb = ClockDomain("usb")
    dev = Ep0Device(**dev_kw)
    m.submodules.dev = dev
    sim = Simulator(m)
    sim.add_clock(USB_PERIOD, domain="usb")

    def wrap(fn):
        async def tb(ctx):
            await fn(ctx, dev)
        return tb
    sim.add_testbench(wrap(sim_body))
    for fn in extra:
        sim.add_testbench(wrap(fn))
    sim.run()


def scaled():
    for k, v in SCALE.items():
        setattr(Ep0Device, k, v)


def section_speed(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        u = dev.utmi
        for pairs, want in ((3, "HS"), (2, "FS"), (0, "FS")):
            ctx.set(u.line_state, SE0)
            chirp_seen = False
            for _ in range(SCALE["HS_RESET_CYCLES"] + 100 + SCALE["CHIRP_K_CYCLES"] // 2):
                await bus.tick()
                if bus.get(u.op_mode) == 2 and bus.get(u.tx_valid) and bus.get(u.tx_data) == 0:
                    chirp_seen = True
                    break
            checks.check(f"speed: {pairs} host pairs: device chirp K after reset", chirp_seen)
            checks.check(f"speed: {pairs} host pairs: chirp mode HS xcvr, chirp termination",
                         bus.get(u.xcvr_select) == 0 and bus.get(u.term_select) == 1)
            while bus.get(u.tx_valid):
                await bus.tick()
            ctx.set(u.line_state, SE0)
            await bus.tick(20)
            for _ in range(pairs):
                ctx.set(u.line_state, K)
                await bus.tick(10)
                ctx.set(u.line_state, J)
                await bus.tick(10)
            ctx.set(u.line_state, J)
            await bus.tick(SCALE["CHIRP_WAIT_CYCLES"] + 50)
            got = "HS" if bus.get(u.xcvr_select) == 0 and bus.get(u.term_select) == 0 else "FS"
            checks.check(f"speed: {pairs} host pairs -> {want} (got {got})", got == want)
            checks.check(f"speed: {pairs} host pairs: reset_seen set, address 0",
                         bus.get(dev.reset_seen) == 1 and bus.get(dev.address) == 0)
            ctx.set(dev.reset_ack, 1)
            await bus.tick()
            ctx.set(dev.reset_ack, 0)
            # a second reset from the speed just negotiated (HS needs the 3 ms-equivalent SE0)
            await bus.tick(5)
    build(body)


def section_control(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        seq0 = bus.get(dev.seq)
        r = await setup(bus, 0, GET_DESC_DEV)
        checks.check("control: SETUP data is ACKed", r == handshake(PID.ACK), f"{r}")
        checks.check("control: setup_valid, 8 bytes, seq +1",
                     bus.get(dev.setup_valid) == 1 and bus.get(dev.setup_data).to_bytes(8, "little") == GET_DESC_DEV
                     and bus.get(dev.seq) == seq0 + 1 and bus.get(dev.stage) == Stage.DATA_IN)
        seq = bus.get(dev.seq)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: IN is NAKed until the CPU answers", r == handshake(PID.NAK), f"{r}")
        await bus.load_in(DEV18[:8])
        await bus.cpu(CMD_IN, seq, length=8, last=0)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: first data packet is DATA1", r is not None and is_data(r, PID.DATA1, DEV18[:8]), f"{r}")
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: no host ACK -> same packet again (DATA1)",
                     r is not None and is_data(r, PID.DATA1, DEV18[:8]), f"{r}")
        await bus.send(bytes([PID.ACK.byte()]))
        await bus.tick(4)
        checks.check("control: ACK frees the buffer (in_busy low)", bus.get(dev.in_busy) == 0)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: next IN before the CPU refills: NAK", r == handshake(PID.NAK), f"{r}")
        await bus.load_in(DEV18[8:])
        await bus.cpu(CMD_IN, seq, length=10, last=1)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: second packet is DATA0", r is not None and is_data(r, PID.DATA0, DEV18[8:]), f"{r}")
        await bus.send(bytes([PID.ACK.byte()]))
        await bus.tick(4)
        checks.check("control: last packet ACKed -> status OUT stage", bus.get(dev.stage) == Stage.STATUS_OUT)
        r = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA1, b""))
        checks.check("control: status OUT ZLP is ACKed by the engine alone", r == handshake(PID.ACK), f"{r}")
        checks.check("control: transfer done, stage idle", bus.get(dev.stage) == Stage.IDLE)

        # STALL until the next SETUP
        await setup(bus, 0, GET_DESC_DEV)
        seq = bus.get(dev.seq)
        await bus.cpu(CMD_STALL, seq)
        r1 = await bus.transact(token(PID.IN, 0, 0))
        r2 = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA1, b""))
        checks.check("control: CMD_STALL -> IN and OUT get STALL", r1 == handshake(PID.STALL) and r2 == handshake(PID.STALL),
                     f"{r1} {r2}")
        r = await setup(bus, 0, GET_DESC_DEV)
        checks.check("control: SETUP after STALL is ACKed", r == handshake(PID.ACK), f"{r}")
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("control: STALL cleared by the new SETUP (IN NAKs again)", r == handshake(PID.NAK), f"{r}")

        # things the engine must not answer
        n = len(bus.tx_packets)
        await bus.send(token(PID.IN, 5, 0))
        await bus.send(token(PID.IN, 0, 1))
        await bus.tick(RESPONSE_CLOCKS)
        checks.check("control: other address / other endpoint: no response", len(bus.tx_packets) == n)
        r = await setup(bus, 0, GET_DESC_DEV, bad_crc=True)
        checks.check("control: SETUP data with bad CRC16: no ACK", r is None, f"{r}")
        seq = bus.get(dev.seq)
        r = await setup(bus, 0, GET_DESC_DEV)
        checks.check("control: the host's retry is ACKed", r == handshake(PID.ACK), f"{r}")
        checks.check("control: every SETUP token bumps seq", bus.get(dev.seq) == seq + 1)
    build(body)


def section_address(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        set_addr = bytes([0x00, 5, 5, 0, 0, 0, 0, 0])
        r = await setup(bus, 0, set_addr)
        seq = bus.get(dev.seq)
        checks.check("address: SET_ADDRESS SETUP ACKed, stage status IN",
                     r == handshake(PID.ACK) and bus.get(dev.stage) == Stage.STATUS_IN)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("address: status IN NAKs until released", r == handshake(PID.NAK), f"{r}")
        await bus.cpu(CMD_STATUS, seq, addr=5)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("address: status is a zero-length DATA1", r is not None and is_data(r, PID.DATA1, b""), f"{r}")
        checks.check("address: not applied before the host ACK", bus.get(dev.address) == 0)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("address: status resent without ACK", r is not None and is_data(r, PID.DATA1, b""), f"{r}")
        await bus.send(bytes([PID.ACK.byte()]))
        await bus.tick(4)
        checks.check("address: applied after the host ACK", bus.get(dev.address) == 5)
        n = len(bus.tx_packets)
        await bus.send(token(PID.IN, 0, 0))
        await bus.tick(RESPONSE_CLOCKS)
        checks.check("address: old address 0 no longer answered", len(bus.tx_packets) == n)
        r = await setup(bus, 5, GET_DESC_DEV)
        checks.check("address: new address answered", r == handshake(PID.ACK), f"{r}")
    build(body)


def section_outdata(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        payload = bytes(range(1, 9))
        vendor_out = bytes([0x40, 1, 0, 0, 0, 0, 8, 0])
        r = await setup(bus, 0, vendor_out)
        seq = bus.get(dev.seq)
        checks.check("outdata: OUT data stage SETUP ACKed", r == handshake(PID.ACK) and bus.get(dev.stage) == Stage.DATA_OUT)
        r = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA1, payload))
        checks.check("outdata: first OUT packet (DATA1) ACKed", r == handshake(PID.ACK), f"{r}")
        got = []
        for i in range(8):
            ctx.set(dev.o_addr, i)
            await bus.tick()
            got.append(bus.get(dev.o_data))
        checks.check("outdata: payload in the buffer, o_len 8", bytes(got) == payload and bus.get(dev.o_len) == 8
                     and bus.get(dev.o_valid) == 1, f"{got}")
        r = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA0, payload))
        checks.check("outdata: next packet NAKed while the buffer is held", r == handshake(PID.NAK), f"{r}")
        await bus.cpu(CMD_OACK, seq)
        r = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA1, payload))
        checks.check("outdata: duplicate (same toggle as the ACKed one) is ACKed but dropped",
                     r == handshake(PID.ACK) and bus.get(dev.o_valid) == 0, f"{r}")
        r = await bus.transact(token(PID.OUT, 0, 0), data_packet(PID.DATA0, payload))
        checks.check("outdata: next packet (DATA0) accepted once freed", r == handshake(PID.ACK), f"{r}")
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("outdata: status IN NAKs until released", r == handshake(PID.NAK), f"{r}")
        await bus.cpu(CMD_STATUS, seq)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("outdata: status ZLP DATA1 after release", r is not None and is_data(r, PID.DATA1, b""), f"{r}")
    build(body)


def section_stale(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await setup(bus, 0, GET_DESC_DEV)
        seq_a = bus.get(dev.seq)
        await setup(bus, 0, GET_DESC_DEV)           # SETUP B overtakes A before the CPU answered
        seq_b = bus.get(dev.seq)
        stale0 = bus.get(dev.stale)
        await bus.load_in(DEV18[:8])
        await bus.cpu(CMD_IN, seq_b if NEGATIVE else seq_a, length=8, last=1)
        checks.check("stale: reply tagged with the old seq is dropped and counted",
                     bus.get(dev.in_busy) == 0 and bus.get(dev.stale) == stale0 + 1,
                     f"in_busy {bus.get(dev.in_busy)} stale {bus.get(dev.stale)}")
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("stale: the host still gets NAK, not the stale data", r == handshake(PID.NAK), f"{r}")
        await bus.cpu(CMD_IN, seq_b, length=8, last=1)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("stale: reply tagged with the current seq is sent",
                     r is not None and is_data(r, PID.DATA1, DEV18[:8]), f"{r}")

        # a new SETUP aborts a transfer in flight: the armed packet and the stage are gone
        await bus.send(bytes([PID.ACK.byte()]))
        await bus.tick(4)
        await setup(bus, 0, GET_DESC_DEV)
        seq_c = bus.get(dev.seq)
        await bus.load_in(DEV18[:8])
        await bus.cpu(CMD_IN, seq_c, length=8, last=0)
        checks.check("stale: packet armed", bus.get(dev.in_busy) == 1)
        await setup(bus, 0, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))     # SET_CONFIGURATION(1)
        checks.check("stale: new SETUP aborts the armed packet and the old stage",
                     bus.get(dev.in_busy) == 0 and bus.get(dev.stage) == Stage.STATUS_IN)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("stale: the aborted transfer's data is never sent (status NAKs)", r == handshake(PID.NAK), f"{r}")
        s0 = bus.get(dev.stale)
        await bus.cpu(CMD_STATUS, seq_c)
        checks.check("stale: late release of the aborted transfer is dropped",
                     bus.get(dev.stale) == s0 + 1 and bus.get(dev.stage) == Stage.STATUS_IN)
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("stale: still NAK for the aborted transfer's release", r == handshake(PID.NAK), f"{r}")
        await bus.cpu(CMD_STATUS, bus.get(dev.seq))
        r = await bus.transact(token(PID.IN, 0, 0))
        checks.check("stale: current release answers", r is not None and is_data(r, PID.DATA1, b""), f"{r}")

        # a bus reset also bumps seq: an answer to a pre-reset SETUP is stale
        seq_d = bus.get(dev.seq)
        ctx.set(dev.utmi.line_state, SE0)
        await bus.tick(SCALE["FS_RESET_CYCLES"] + 10)
        ctx.set(dev.utmi.line_state, J)
        await bus.tick(SCALE["CHIRP_K_CYCLES"] + SCALE["CHIRP_WAIT_CYCLES"] + 50)
        checks.check("stale: bus reset bumps seq", bus.get(dev.seq) != seq_d)
    build(body)


def sof_token(frame, *, bad_crc=False):
    raw = bytearray(token(PID.SOF, frame & 0x7F, frame >> 7))
    if bad_crc:
        raw[2] ^= 0x08
    return bytes(raw)


async def reset_to(bus, hs):
    """Bus reset from the current state, then chirp with three host pairs (HS) or none (FS)."""
    ctx, dev = bus.ctx, bus.dev
    u = dev.utmi
    ctx.set(u.line_state, SE0)
    for _ in range(SCALE["HS_RESET_CYCLES"] + SCALE["PROBE_CYCLES"] + SCALE["CHIRP_K_CYCLES"]):
        await bus.tick()
        if bus.get(u.op_mode) == 2 and bus.get(u.tx_valid):
            break
    while bus.get(u.tx_valid):
        await bus.tick()
    ctx.set(u.line_state, SE0)
    await bus.tick(20)
    for _ in range(3 if hs else 0):
        ctx.set(u.line_state, K)
        await bus.tick(10)
        ctx.set(u.line_state, J)
        await bus.tick(10)
    ctx.set(u.line_state, J)
    await bus.tick(SCALE["CHIRP_WAIT_CYCLES"] + 50)
    ctx.set(dev.reset_ack, 1)
    await bus.tick()
    ctx.set(dev.reset_ack, 0)


async def clear_events(bus, mask=0xF):
    bus.ctx.set(bus.dev.ev_clr, mask)
    await bus.tick()
    bus.ctx.set(bus.dev.ev_clr, 0)
    await bus.tick()


def section_sof(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        n0 = bus.get(dev.sof_count)
        await bus.send(sof_token(0x2A5))
        await bus.tick(4)
        checks.check("sof: frame number from the token", bus.get(dev.frame) == 0x2A5, f"{bus.get(dev.frame):#x}")
        checks.check("sof: count +1, microframe 0", bus.get(dev.sof_count) == n0 + 1 and bus.get(dev.uframe) == 0)
        for want in range(1, 8):
            await bus.send(sof_token(0x2A5))
            await bus.tick(4)
            checks.check(f"sof: same frame again -> microframe {want}", bus.get(dev.uframe) == want
                         and bus.get(dev.frame) == 0x2A5, f"{bus.get(dev.uframe)}")
        await bus.send(sof_token(0x2A6))
        await bus.tick(4)
        checks.check("sof: next frame number -> microframe 0", bus.get(dev.frame) == 0x2A6 and bus.get(dev.uframe) == 0)
        checks.check("sof: count counts every SOF", bus.get(dev.sof_count) == (n0 + 9) & 0xFF, f"{bus.get(dev.sof_count)}")
        await bus.send(sof_token(0x111, bad_crc=True))
        await bus.tick(4)
        checks.check("sof: bad CRC5 ignored", bus.get(dev.frame) == 0x2A6 and bus.get(dev.sof_count) == (n0 + 9) & 0xFF)
        await bus.send(sof_token(0x7FF))
        await bus.tick(4)
        await bus.send(sof_token(0))
        await bus.tick(4)
        checks.check("sof: frame wraps 0x7FF -> 0", bus.get(dev.frame) == 0 and bus.get(dev.uframe) == 0)
        checks.check("sof: no response on the wire", not bus.tx_packets)
        seq0 = bus.get(dev.seq)
        await bus.send(sof_token(5))
        await bus.tick(4)
        checks.check("sof: does not bump seq", bus.get(dev.seq) == seq0)
    build(body)


def section_suspend(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        u = dev.utmi
        S = SCALE["SUSPEND_CYCLES"]
        await bus.tick(S // 2)
        checks.check("suspend: half the idle time: not suspended", bus.get(dev.suspended) == 0)
        await bus.send(sof_token(1))
        await bus.tick(S - 100)
        checks.check("suspend: a SOF restarts the idle count", bus.get(dev.suspended) == 0)
        await bus.tick(200)
        checks.check("suspend: idle for SUSPEND_CYCLES -> suspended", bus.get(dev.suspended) == 1)
        checks.check("suspend: event raised", bus.get(dev.events) & EV_SUSPEND)
        await clear_events(bus)
        checks.check("suspend: ev_clr clears the event", bus.get(dev.events) == 0)
        checks.check("suspend: FS terminations kept", bus.get(u.term_select) == 1 and bus.get(u.xcvr_select) == 1)

        # host resume: K for longer than the debounce, then back to idle
        ctx.set(u.line_state, K)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] + 4)
        checks.check("suspend: host K -> resumed + event", bus.get(dev.suspended) == 0 and bus.get(dev.events) & EV_RESUME)
        await bus.tick(40)
        ctx.set(u.line_state, J)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] + 8)
        await clear_events(bus)
        r = await setup(bus, 0, GET_DESC_DEV)
        checks.check("suspend: answers a SETUP after resume", r == handshake(PID.ACK), f"{r}")

        # a glitch shorter than the debounce is not a resume
        await bus.tick(S + 50)
        checks.check("suspend: suspended again", bus.get(dev.suspended) == 1)
        await clear_events(bus)
        ctx.set(u.line_state, K)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] // 2)
        ctx.set(u.line_state, J)
        await bus.tick(40)
        checks.check("suspend: K glitch below the debounce: still suspended",
                     bus.get(dev.suspended) == 1 and not bus.get(dev.events) & EV_RESUME)

        # remote wakeup: the device drives K for RESUME_K_CYCLES
        seq = bus.get(dev.seq)
        await bus.cpu(CMD_WAKEUP, seq)
        driven = 0
        for _ in range(SCALE["RESUME_K_CYCLES"] + 60):
            ctx.set(u.line_state, K if bus.get(u.tx_valid) else J)
            await bus.tick()
            driven += int(bus.get(u.tx_valid) and bus.get(u.op_mode) == 2 and bus.get(u.tx_data) == 0)
        checks.check("suspend: wakeup drives K for RESUME_K_CYCLES",
                     SCALE["RESUME_K_CYCLES"] - 4 <= driven <= SCALE["RESUME_K_CYCLES"] + 8, f"{driven}")
        checks.check("suspend: wakeup ends suspended, event raised",
                     bus.get(dev.suspended) == 0 and bus.get(dev.events) & EV_RESUME)
        ctx.set(u.line_state, J)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] + 8)
        # wakeup while not suspended does nothing
        n = len(bus.tx_packets)
        await bus.cpu(CMD_WAKEUP, bus.get(dev.seq))
        await bus.tick(20)
        checks.check("suspend: wakeup while awake: no K", not bus.get(u.tx_valid) and len(bus.tx_packets) == n)

        # SE0 while suspended = reset
        await bus.tick(S + 50)
        checks.check("suspend: suspended for the reset test", bus.get(dev.suspended) == 1)
        ctx.set(u.line_state, SE0)
        chirp = False
        for _ in range(SCALE["FS_RESET_CYCLES"] + 60):
            await bus.tick()
            chirp |= bool(bus.get(u.op_mode) == 2 and bus.get(u.tx_valid))
        checks.check("suspend: SE0 while suspended -> reset, chirp", chirp and bus.get(dev.reset_seen) == 1)
        checks.check("suspend: reset leaves suspend", bus.get(dev.suspended) == 0)
    build(body)


def section_suspend_hs(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        u = dev.utmi
        await reset_to(bus, hs=True)
        checks.check("suspend_hs: HS up", bus.get(u.xcvr_select) == 0 and bus.get(u.term_select) == 0)
        ctx.set(u.line_state, SE0)              # HS idle
        await bus.tick(SCALE["HS_RESET_CYCLES"] // 2)
        await bus.send(sof_token(9))
        ctx.set(u.line_state, SE0)
        await bus.tick(SCALE["HS_RESET_CYCLES"] - 100)
        checks.check("suspend_hs: a SOF restarts the HS idle count", bus.get(dev.suspended) == 0
                     and bus.get(u.term_select) == 0)
        for _ in range(300):
            await bus.tick()
            if bus.get(u.term_select) == 1:
                break
        checks.check("suspend_hs: 3 ms idle -> FS terminations to look at the line",
                     bus.get(u.term_select) == 1 and bus.get(u.xcvr_select) == 1)
        ctx.set(u.line_state, J)                # pull-up wins: not a reset
        await bus.tick(SCALE["PROBE_CYCLES"] + 20)
        checks.check("suspend_hs: J after the probe -> suspended", bus.get(dev.suspended) == 1
                     and bus.get(dev.events) & EV_SUSPEND)
        ctx.set(u.line_state, K)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] + 30)
        ctx.set(u.line_state, SE0)
        await bus.tick(SCALE["DEBOUNCE_CYCLES"] + 8)
        checks.check("suspend_hs: resume returns to HS", bus.get(dev.suspended) == 0
                     and bus.get(u.xcvr_select) == 0 and bus.get(u.term_select) == 0)
        # SE0 that outlasts the probe is a reset
        ctx.set(u.line_state, SE0)
        chirp = False
        for _ in range(SCALE["HS_RESET_CYCLES"] + SCALE["PROBE_CYCLES"] + 200):
            await bus.tick()
            chirp |= bool(bus.get(u.op_mode) == 2 and bus.get(u.tx_valid))
        checks.check("suspend_hs: SE0 still there after the probe -> reset, chirp", chirp and bus.get(dev.suspended) == 0)
    build(body)


def section_vbus(checks):
    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        u = dev.utmi
        checks.check("vbus: attached after power-up", bus.get(dev.attached) == 1 and bus.get(u.term_select) == 1)
        set_addr = bytes([0x00, 5, 5, 0, 0, 0, 0, 0])
        await setup(bus, 0, set_addr)
        await bus.cpu(CMD_STATUS, bus.get(dev.seq), addr=5)
        await bus.transact(token(PID.IN, 0, 0))
        await bus.send(bytes([PID.ACK.byte()]))
        await bus.tick(4)
        checks.check("vbus: address 5 applied", bus.get(dev.address) == 5)
        ctx.set(dev.vbus, 0)
        await bus.tick(4)
        checks.check("vbus: session lost -> detached, no pull-up, not driving",
                     bus.get(dev.attached) == 0 and bus.get(u.term_select) == 0 and bus.get(u.op_mode) == 1)
        checks.check("vbus: address and stage cleared, event", bus.get(dev.address) == 0
                     and bus.get(dev.stage) == Stage.IDLE and bus.get(dev.events) & EV_VBUS)
        n = len(bus.tx_packets)
        await bus.send(token(PID.SETUP, 0, 0))
        await bus.tick(RESPONSE_CLOCKS)
        checks.check("vbus: detached device answers nothing", len(bus.tx_packets) == n)
        await clear_events(bus)
        ctx.set(dev.vbus, 1)
        await bus.tick(4)
        checks.check("vbus: back -> attached, pull-up on, event",
                     bus.get(dev.attached) == 1 and bus.get(u.term_select) == 1 and bus.get(u.op_mode) == 0
                     and bus.get(dev.events) & EV_VBUS)
        r = await setup(bus, 0, GET_DESC_DEV)
        checks.check("vbus: answers at address 0 again", r == handshake(PID.ACK), f"{r}")
        await clear_events(bus)
        ctx.set(dev.connect, 0)
        await bus.tick(4)
        checks.check("vbus: connect=0 (soft disconnect) detaches too", bus.get(dev.attached) == 0
                     and bus.get(u.term_select) == 0)
        ctx.set(dev.connect, 1)
        await bus.tick(4)
        checks.check("vbus: connect=1 re-attaches", bus.get(dev.attached) == 1 and bus.get(u.term_select) == 1)
    build(body)


SECTIONS = {"speed": section_speed, "control": section_control, "address": section_address,
            "outdata": section_outdata, "stale": section_stale, "sof": section_sof,
            "suspend": section_suspend, "suspend_hs": section_suspend_hs, "vbus": section_vbus}


def main(argv):
    global NEGATIVE
    names = [a for a in argv if not a.startswith("-")] or list(SECTIONS)
    NEGATIVE = "--negative" in argv
    scaled()
    checks = Checks(emit)
    for n in names:
        emit(f"== {n}")
        SECTIONS[n](checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
