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
- `--negative`: the `stale` section's "old" commands carry the current seq instead; it must FAIL
  (the stale checks can fail).

Time constants are divided down (the engine's class attributes), like model.py does for the host.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from amaranth import ClockDomain, Module
from amaranth.sim import Simulator
from devlog import emit
from sim_check_harness import Checks
from usbcore.crc import crc5_reference, crc16_reference
from usbcore.device import CMD_IN, CMD_OACK, CMD_STALL, CMD_STATUS, Ep0Device, Stage
from usbcore.pid import PID

USB_PERIOD = 1 / 60e6
SCALE = {"CHIRP_K_CYCLES": 300, "CHIRP_WAIT_CYCLES": 400, "HS_RESET_CYCLES": 500, "FS_RESET_CYCLES": 40}
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


def build(sim_body, *, negative=False):
    m = Module()
    m.domains.usb = ClockDomain("usb")
    dev = Ep0Device()
    m.submodules.dev = dev
    sim = Simulator(m)
    sim.add_clock(USB_PERIOD, domain="usb")

    async def tb(ctx):
        await sim_body(ctx, dev)
    sim.add_testbench(tb)
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


SECTIONS = {"speed": section_speed, "control": section_control, "address": section_address,
            "outdata": section_outdata, "stale": section_stale}


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
