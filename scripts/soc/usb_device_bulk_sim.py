#!/usr/bin/env python3
#
# usbcore.device.Ep0Device, bulk endpoints EP1-4: packets, toggles, PING/NYET, halt, FIFOs.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Directed tests of the bulk data path of the device engine, without a host engine. #612 increment 4.

    python3 scripts/soc/usb_device_bulk_sim.py              # all sections, FS and HS
    python3 scripts/soc/usb_device_bulk_sim.py out in ping

Sections (each runs at full speed `[FS]` and high speed `[HS]` unless noted):
- out: OUT packets reach the consumer stream with boundaries; ZLP; toggle accept / duplicate drop;
  babble and bad CRC get no response; two endpoints stay separate.
- in: producer stream -> IN packets; DATA0/1 order; no ACK -> same packet again; NAK when empty does
  not advance the toggle; ZLP beat; short packet; split at maxp; auto-ZLP on a maxp multiple.
- backpressure: OUT FIFO full -> NYET (HS only, last slot) then NAK; drained -> accepted; IN FIFO full
  -> producer `ready` low, order kept.
- ping (HS): PING answers ACK with room, NAK when full, STALL when halted; none in FS.
- halt: STALL on both legs, clear resets the toggle, set without data loss of the other endpoint.
- control: enable/disable/flush/stale-seq/bus-reset rules of the endpoint commands.
- large: multi-packet transfers (multiples of maxp and not) with a throttled consumer / producer and a
  host loop that follows the PING / NYET protocol.
- `--negative`: stimulus mutations that must make the named checks FAIL; the engine itself is
  mutated by `usb_device_mutants.py`.
"""

import sys

from usb_device_ep0_sim import (K, J, SE0, SCALE, Bus, build, data_packet, handshake, reset_to, scaled,
                                setup, token, GET_DESC_DEV)
from amaranth.sim import Simulator  # noqa: F401  (re-exported for ad-hoc scripts)
from devlog import emit
from sim_check_harness import Checks
from usbcore.device import (Ep0Device, CMD_EP_CLEAR, CMD_EP_DISABLE, CMD_EP_ENABLE, CMD_EP_FLUSH, CMD_EP_HALT, ep_code)
from usbcore.pid import PID

ACK, NAK, STALL, NYET = (handshake(p) for p in (PID.ACK, PID.NAK, PID.STALL, PID.NYET))
SLOTS = 2               # engine default: two packet buffers per FIFO
PRODUCER_LIMIT = 100_000   # cycles one producer beat may wait: a stalled engine ends the run instead of hanging it
LOOP_LIMIT = 200        # host loops: retries per packet; a NAK storm longer than this is a hang
NEGATIVE = False


def pat(n, seed=0):
    return bytes((i * 7 + seed * 13 + 3) & 255 for i in range(n))


def maxp(hs):
    return 512 if hs else 64


class Ep:
    """Testbench handle on one endpoint: command port, producer / consumer streams, host transactions."""

    def __init__(self, bus, num, is_in, hs):
        self.bus, self.dev, self.ctx = bus, bus.dev, bus.ctx
        self.num, self.is_in, self.hs = num, is_in, hs
        self.s = (self.dev.ep_in if is_in else self.dev.ep_out)[num]
        self.tog = 0
        self.code = ep_code(num, is_in)

    async def cmd(self, cmd, *, seq=None, arg=0):
        await self.bus.cpu(cmd, self.bus.get(self.dev.seq) if seq is None else seq, ep=self.code, arg=arg)

    async def enable(self, *, zlp=False, mp=None):
        await self.cmd(CMD_EP_ENABLE, arg=(mp or maxp(self.hs)) | (1 << 10 if zlp else 0))
        self.tog = 0

    def status(self, field):
        self.ctx.set(self.dev.ep_sel, self.code)
        return self.bus.get(getattr(self.dev, field))

    # ---- producer (IN endpoints): bytes into the FIFO ----
    async def push(self, payload, *, last=True, zlp=False, limit=4000):
        s, ctx = self.s, self.ctx
        beats = [(b, last and i == len(payload) - 1, False) for i, b in enumerate(payload)]
        if zlp:
            beats = [(0, True, True)]
        for data, lst, z in beats:
            ctx.set(s.data, data)
            ctx.set(s.last, int(lst))
            ctx.set(s.zlp, int(z))
            ctx.set(s.valid, 1)
            for _ in range(limit):
                *_, ready = await ctx.tick("usb").sample(s.ready)
                self.bus.cycles += 1
                if ready:
                    break
            else:
                ctx.set(s.valid, 0)
                return False
        ctx.set(s.valid, 0)
        return True

    # ---- consumer (OUT endpoints): packets out of the FIFO ----
    async def take(self, count=1, *, wait=40):
        """Collects up to `count` packets (b"" for a ZLP); stops after `wait` idle cycles."""
        s, ctx = self.s, self.ctx
        pkts, cur, idle = [], bytearray(), 0
        ctx.set(s.ready, 1)
        while len(pkts) < count and idle < wait:
            *_, v, d, lst, z = await ctx.tick("usb").sample(s.valid, s.data, s.last, s.zlp)
            self.bus.cycles += 1
            if v:
                idle = 0
                if z:
                    pkts.append(b"")
                else:
                    cur.append(d)
                    if lst:
                        pkts.append(bytes(cur))
                        cur = bytearray()
            else:
                idle += 1
        ctx.set(s.ready, 0)
        return pkts

    # ---- host side ----
    def data_pid(self, toggle):
        return PID.DATA1 if toggle else PID.DATA0

    async def host_out(self, payload, *, toggle=None, bad_crc=False):
        t = self.tog if toggle is None else toggle
        r = await self.bus.transact(token(PID.OUT, 0, self.num),
                                    data_packet(self.data_pid(t), payload, bad_crc=bad_crc))
        return r

    async def host_in(self):
        return await self.bus.transact(token(PID.IN, 0, self.num), wait=maxp(self.hs) + 80)

    async def host_ack(self):
        await self.bus.send(bytes([PID.ACK.byte()]))
        await self.bus.tick(6)

    async def host_ping(self):
        return await self.bus.transact(token(PID.PING, 0, self.num))

    async def in_packet(self, want_payload, toggle=None):
        """One IN transaction expecting DATA; returns True if it matches; ACKs it."""
        t = self.tog if toggle is None else toggle
        r = await self.host_in()
        ok = r is not None and r == list(data_packet(self.data_pid(t), want_payload))
        if ok:
            await self.host_ack()
            self.tog ^= 1
        return ok, r


async def start(bus, hs):
    if hs:
        await reset_to(bus, hs=True)
    return bus


def both(fn):
    """Runs `fn(checks, hs)` for FS then HS; labels carry the speed."""
    def run(checks):
        for hs in (False, True):
            fn(checks, hs)
    run.__name__ = fn.__name__
    return run


def tag(hs):
    return "HS" if hs else "FS"


# ------------------------------------------------------------------ out

@both
def section_out(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        e1 = Ep(bus, 1, False, hs)
        e2 = Ep(bus, 2, False, hs)
        await e1.enable()
        await e2.enable()
        checks.check(f"out[{t}]: enabled, maxp, toggle 0", e1.status("ep_enabled") == 1
                     and e1.status("ep_maxp") == M and e1.status("ep_toggle") == 0)
        r = await e1.host_out(pat(10))
        checks.check(f"out[{t}]: DATA0 packet ACKed", r == ACK, f"{r}")
        got = await e1.take()
        checks.check(f"out[{t}]: consumer gets the 10 bytes, one packet", got == [pat(10)], f"{got}")
        checks.check(f"out[{t}]: toggle advanced", e1.status("ep_toggle") == 1)
        e1.tog = 1
        r = await e1.host_out(pat(M, 1))
        checks.check(f"out[{t}]: max-size packet ACKed", r == ACK, f"{r}")
        got = await e1.take()
        checks.check(f"out[{t}]: max-size packet intact", got == [pat(M, 1)], f"{len(got)} {[len(g) for g in got]}")
        e1.tog = 0
        r = await e1.host_out(b"")
        checks.check(f"out[{t}]: ZLP ACKed", r == ACK, f"{r}")
        got = await e1.take()
        checks.check(f"out[{t}]: ZLP reaches the consumer as a ZLP beat", got == [b""], f"{got}")
        e1.tog = 1
        r = await e1.host_out(pat(5, 2))
        await e1.take()
        dup0 = bus.get(dev.n_dup)
        r2 = await e1.host_out(pat(6, 3), toggle=1)
        checks.check(f"out[{t}]: duplicate (same toggle) ACKed", r == ACK and r2 == ACK, f"{r} {r2}")
        got = await e1.take()
        checks.check(f"out[{t}]: duplicate not delivered, counted",
                     got == [] and bus.get(dev.n_dup) == dup0 + 1, f"{got}")
        e1.tog = 0
        n = len(bus.tx_packets)
        r = await e1.host_out(pat(M + 1), toggle=0)
        checks.check(f"out[{t}]: maxp+1 bytes: no response", r is None and len(bus.tx_packets) == n, f"{r}")
        got = await e1.take()
        checks.check(f"out[{t}]: oversize packet not delivered", got == [], f"{[len(g) for g in got]}")
        r = await e1.host_out(pat(4, 4), toggle=0, bad_crc=True)
        checks.check(f"out[{t}]: bad CRC16: no response", r is None, f"{r}")
        got = await e1.take()
        checks.check(f"out[{t}]: bad CRC16 not delivered", got == [], f"{got}")
        r = await e1.host_out(pat(4, 4), toggle=0)
        got = await e1.take()
        checks.check(f"out[{t}]: retry with the same toggle accepted (errors do not toggle)",
                     r == ACK and got == [pat(4, 4)], f"{r} {got}")
        e1.tog = 1
        # two endpoints, own toggles and own consumers
        r = await e2.host_out(pat(7, 5))
        r_1 = await e1.host_out(pat(9, 6))
        g2 = await e2.take()
        g1 = await e1.take()
        checks.check(f"out[{t}]: EP2 and EP1 independent (toggle, data, consumer)",
                     r == ACK and r_1 == ACK and g2 == [pat(7, 5)] and g1 == [pat(9, 6)], f"{r} {r_1} {g1} {g2}")
        n = len(bus.tx_packets)
        await bus.send(token(PID.OUT, 0, 3))
        await bus.send(data_packet(PID.DATA0, b"x"))
        await bus.send(token(PID.OUT, 7, 1))
        await bus.send(data_packet(PID.DATA0, b"x"))
        await bus.tick(60)
        checks.check(f"out[{t}]: disabled endpoint / other address: no response", len(bus.tx_packets) == n)
    build(body)


# ------------------------------------------------------------------ in

@both
def section_in(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        e1 = Ep(bus, 1, True, hs)
        await e1.enable()
        r = await e1.host_in()
        checks.check(f"in[{t}]: empty FIFO: NAK", r == NAK, f"{r}")
        checks.check(f"in[{t}]: NAK does not advance the toggle", e1.status("ep_toggle") == 0)
        ok = await e1.push(pat(10))
        checks.check(f"in[{t}]: producer accepted", ok)
        await bus.tick(4)
        checks.check(f"in[{t}]: one packet queued", e1.status("ep_level") == 1)
        ok, r = await e1.in_packet(pat(10), toggle=0)
        e1.tog = 1 if ok else e1.tog
        checks.check(f"in[{t}]: first packet is DATA0 with the payload", ok, f"{r}")
        await e1.push(pat(M, 1))
        r = await e1.host_in()
        checks.check(f"in[{t}]: next packet DATA1", r == list(data_packet(PID.DATA1, pat(M, 1))), f"{r and len(r)}")
        r = await e1.host_in()
        checks.check(f"in[{t}]: no ACK -> same packet again, same toggle", r == list(data_packet(PID.DATA1, pat(M, 1))),
                     f"{r and len(r)}")
        await bus.tick(Ep0Device.ACK_WAIT_CYCLES + 50)
        checks.check(f"in[{t}]: ACK wait expired: packet kept, toggle unchanged",
                     e1.status("ep_level") == 1 and e1.status("ep_toggle") == 1)
        r = await e1.host_in()
        checks.check(f"in[{t}]: IN after the ACK window: same packet, same toggle",
                     r == list(data_packet(PID.DATA1, pat(M, 1))), f"{r and len(r)}")
        await e1.host_ack()
        e1.tog = 0
        checks.check(f"in[{t}]: ACK frees it, toggle DATA0 next", e1.status("ep_toggle") == 0
                     and e1.status("ep_level") == 0)
        r = await e1.host_in()
        checks.check(f"in[{t}]: empty again: NAK", r == NAK, f"{r}")
        r = await e1.host_in()
        await e1.push(pat(3, 2))
        ok, r = await e1.in_packet(pat(3, 2), toggle=0)
        e1.tog = 1
        checks.check(f"in[{t}]: toggle held across NAKs: DATA0 after two NAKs", ok, f"{r}")
        await e1.push(b"", zlp=True)
        ok, r = await e1.in_packet(b"", toggle=1)
        e1.tog = 0
        checks.check(f"in[{t}]: ZLP beat -> zero-length DATA1 packet", ok, f"{r}")
        # without auto-ZLP a maxp packet ending the transfer stays one packet
        await e1.push(pat(M, 3))
        ok, r = await e1.in_packet(pat(M, 3), toggle=0)
        e1.tog = 1
        r = await e1.host_in()
        checks.check(f"in[{t}]: maxp packet ends the transfer, no ZLP appended (auto-ZLP off)", ok and r == NAK, f"{r}")
    build(body)


@both
def section_in_zlp(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        e1 = Ep(bus, 1, True, hs)
        await e1.enable(zlp=True)
        await e1.push(pat(M, 1))
        ok, r = await e1.in_packet(pat(M, 1), toggle=0)
        checks.check(f"in_zlp[{t}]: maxp packet that ends the transfer is sent", ok, f"{r}")
        e1.tog = 1
        ok, r = await e1.in_packet(b"", toggle=1)
        checks.check(f"in_zlp[{t}]: auto-ZLP follows a maxp-multiple transfer", ok, f"{r}")
        e1.tog = 0
        r = await e1.host_in()
        checks.check(f"in_zlp[{t}]: then NAK", r == NAK, f"{r}")
        await e1.push(pat(2 * M, 2))
        got = []
        for _ in range(4):
            r = await e1.host_in()
            if r is None or r == NAK:
                break
            got.append(len(r) - 3)
            await e1.host_ack()
            e1.tog ^= 1
        checks.check(f"in_zlp[{t}]: 2*maxp transfer -> M, M, ZLP", got == [M, M, 0], f"{got}")
        await e1.push(pat(5, 3))
        got = []
        for _ in range(3):
            r = await e1.host_in()
            if r is None or r == NAK:
                break
            got.append(len(r) - 3)
            await e1.host_ack()
            e1.tog ^= 1
        checks.check(f"in_zlp[{t}]: short transfer gets no extra ZLP", got == [5], f"{got}")
    build(body)


# ------------------------------------------------------------------ backpressure

@both
def section_backpressure(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        o1 = Ep(bus, 1, False, hs)
        await o1.enable()
        sent = []
        first = await o1.host_out(pat(20, 1))
        sent.append(pat(20, 1))
        o1.tog = 1
        second = await o1.host_out(pat(21, 2))
        sent.append(pat(21, 2))
        o1.tog = 0
        want2 = NYET if hs else ACK
        checks.check(f"backpressure[{t}]: first packet ACK", first == ACK, f"{first}")
        checks.check(f"backpressure[{t}]: last free slot -> {'NYET' if hs else 'ACK'}", second == want2, f"{second}")
        n0 = bus.get(dev.n_nak)
        third = await o1.host_out(pat(22, 3))
        checks.check(f"backpressure[{t}]: FIFO full -> NAK, counted", third == NAK and bus.get(dev.n_nak) == n0 + 1,
                     f"{third}")
        checks.check(f"backpressure[{t}]: NAK does not advance the toggle", o1.status("ep_toggle") == 0)
        if hs:
            checks.check(f"backpressure[{t}]: NYET counted", bus.get(dev.n_nyet) >= 1)
            r = await o1.host_ping()
            checks.check(f"backpressure[{t}]: PING while full -> NAK", r == NAK, f"{r}")
        got = await o1.take(1)
        checks.check(f"backpressure[{t}]: consumer takes the oldest first", got == [sent[0]], f"{got}")
        if hs:
            r = await o1.host_ping()
            checks.check(f"backpressure[{t}]: PING after a slot freed -> ACK", r == ACK, f"{r}")
        r = await o1.host_out(pat(22, 3))
        sent.append(pat(22, 3))
        checks.check(f"backpressure[{t}]: retry with the same toggle accepted", r in (ACK, NYET), f"{r}")
        got = await o1.take(2)
        checks.check(f"backpressure[{t}]: order kept across the stall", got == sent[1:], f"{got}")

        # IN leg: producer blocks when both buffers are full, and unblocks after the host drains one
        i1 = Ep(bus, 2, True, hs)
        await i1.enable()
        await i1.push(pat(30, 1))
        await i1.push(pat(31, 2))
        blocked = not await i1.push(pat(32, 3), limit=50)
        checks.check(f"backpressure[{t}]: producer ready low while both buffers hold packets", blocked)
        ctx.set(i1.s.valid, 0)
        ok, r = await i1.in_packet(pat(30, 1), toggle=0)
        checks.check(f"backpressure[{t}]: host drains the first", ok, f"{r}")
        await bus.tick(4)
        checks.check(f"backpressure[{t}]: producer accepted again", await i1.push(pat(32, 3), limit=50))
        ok2, r2 = await i1.in_packet(pat(31, 2), toggle=1)
        ok3, r3 = await i1.in_packet(pat(32, 3), toggle=0)
        checks.check(f"backpressure[{t}]: packets in order after the stall", ok2 and ok3, f"{r2} {r3}")
    build(body)


# ------------------------------------------------------------------ ping

def section_ping(checks):
    hs, t = True, "HS"

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        o1 = Ep(bus, 1, False, hs)
        await o1.enable()
        r = await o1.host_ping()
        checks.check("ping[HS]: room in the FIFO -> ACK", r == ACK, f"{r}")
        checks.check("ping[HS]: PING does not toggle or write", o1.status("ep_toggle") == 0 and o1.status("ep_level") == 0)
        await o1.host_out(pat(8))
        o1.tog = 1
        await o1.host_out(pat(8, 1))
        o1.tog = 0
        r = await o1.host_ping()
        checks.check("ping[HS]: FIFO full -> NAK", r == NAK, f"{r}")
        await o1.take(2)
        r = await o1.host_ping()
        checks.check("ping[HS]: drained -> ACK again", r == ACK, f"{r}")
        await o1.cmd(CMD_EP_HALT)
        r = await o1.host_ping()
        checks.check("ping[HS]: halted -> STALL", r == STALL, f"{r}")
        await o1.cmd(CMD_EP_CLEAR)
        o1.tog = 0
        n = len(bus.tx_packets)
        await bus.send(token(PID.PING, 0, 2))
        await bus.send(token(PID.PING, 9, 1))
        await bus.tick(60)
        checks.check("ping[HS]: disabled endpoint / other address: no response", len(bus.tx_packets) == n)
        i1 = Ep(bus, 3, True, hs)
        await i1.enable()
        n = len(bus.tx_packets)
        await bus.send(token(PID.PING, 0, 3))
        await bus.tick(60)
        checks.check("ping[HS]: PING to an IN endpoint: no response", len(bus.tx_packets) == n)
    build(body)

    async def body_fs(ctx, dev):
        bus = Bus(ctx, dev)
        o1 = Ep(bus, 1, False, False)
        await o1.enable()
        n = len(bus.tx_packets)
        await bus.send(token(PID.PING, 0, 1))
        await bus.tick(60)
        checks.check("ping[FS]: PING is a high-speed protocol: no response", len(bus.tx_packets) == n)
    build(body_fs)


# ------------------------------------------------------------------ halt

@both
def section_halt(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        o1, i1, o2 = Ep(bus, 1, False, hs), Ep(bus, 1, True, hs), Ep(bus, 2, False, hs)
        for e in (o1, i1, o2):
            await e.enable()
        await o1.host_out(pat(4))
        await o1.take()
        o1.tog = 1
        await i1.push(pat(6))
        ok, _ = await i1.in_packet(pat(6), toggle=0)
        i1.tog = 1
        await i1.push(pat(7, 1))
        await o1.cmd(CMD_EP_HALT)
        await i1.cmd(CMD_EP_HALT)
        checks.check(f"halt[{t}]: halted flags", o1.status("ep_halted") == 1 and i1.status("ep_halted") == 1
                     and o2.status("ep_halted") == 0)
        r = await o1.host_out(pat(5, 1))
        checks.check(f"halt[{t}]: OUT to a halted endpoint -> STALL", r == STALL, f"{r}")
        got = await o1.take()
        checks.check(f"halt[{t}]: halted OUT delivers nothing", got == [], f"{got}")
        r = await i1.host_in()
        checks.check(f"halt[{t}]: IN from a halted endpoint -> STALL (data stays queued)", r == STALL, f"{r}")
        r = await o1.host_out(pat(5, 1))
        checks.check(f"halt[{t}]: STALL is sticky until cleared", r == STALL, f"{r}")
        r = await o2.host_out(pat(3, 2))
        got = await o2.take()
        checks.check(f"halt[{t}]: the other endpoint is unaffected", r == ACK and got == [pat(3, 2)], f"{r} {got}")
        await o1.cmd(CMD_EP_CLEAR)
        await i1.cmd(CMD_EP_CLEAR)
        checks.check(f"halt[{t}]: clear: not halted, toggles back to DATA0",
                     o1.status("ep_halted") == 0 and o1.status("ep_toggle") == 0
                     and i1.status("ep_halted") == 0 and i1.status("ep_toggle") == 0)
        r = await o1.host_out(pat(5, 1), toggle=0)
        got = await o1.take()
        checks.check(f"halt[{t}]: after clear DATA0 is accepted and delivered (toggle was reset)",
                     r == ACK and got == [pat(5, 1)], f"{r} {got}")
        r = await i1.host_in()
        checks.check(f"halt[{t}]: queued IN data sent after clear, as DATA0", r == list(data_packet(PID.DATA0, pat(7, 1))),
                     f"{r}")
    build(body)


# ------------------------------------------------------------------ control

@both
def section_control(checks, hs):
    t, M = tag(hs), maxp(hs)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        o1, i1 = Ep(bus, 1, False, hs), Ep(bus, 1, True, hs)
        await o1.enable()
        await i1.enable()
        await o1.host_out(pat(8))
        await i1.push(pat(9))
        await i1.push(pat(10, 1))
        checks.check(f"control[{t}]: data queued on both legs", o1.status("ep_level") == 1 and i1.status("ep_level") == 2)
        await o1.cmd(CMD_EP_FLUSH)
        await i1.cmd(CMD_EP_FLUSH)
        checks.check(f"control[{t}]: flush empties both FIFOs", o1.status("ep_level") == 0 and i1.status("ep_level") == 0)
        got = await o1.take()
        r = await i1.host_in()
        checks.check(f"control[{t}]: flushed data is gone (consumer empty, IN NAKs)", got == [] and r == NAK, f"{got} {r}")
        checks.check(f"control[{t}]: flush keeps the toggle", o1.status("ep_toggle") == 1)
        # a partly written producer packet is dropped by the flush
        s = i1.s
        ctx.set(s.data, 0x55)
        ctx.set(s.last, 0)
        ctx.set(s.zlp, 0)
        ctx.set(s.valid, 1)
        await bus.tick(3)
        ctx.set(s.valid, 0)
        await i1.cmd(CMD_EP_FLUSH)
        await i1.push(pat(3, 5))
        ok, r = await i1.in_packet(pat(3, 5), toggle=0)
        checks.check(f"control[{t}]: flush drops a half-written producer packet", ok, f"{r}")
        # stale seq: the command is dropped and counted
        seq = bus.get(dev.seq)
        await setup(bus, 0, GET_DESC_DEV)
        s0 = bus.get(dev.stale)
        await o1.cmd(CMD_EP_HALT, seq=seq)
        checks.check(f"control[{t}]: stale-seq endpoint command dropped, counted",
                     bus.get(dev.stale) == s0 + 1 and o1.status("ep_halted") == 0)
        await o1.cmd(CMD_EP_HALT)
        checks.check(f"control[{t}]: current-seq endpoint command applied", o1.status("ep_halted") == 1)
        await o1.cmd(CMD_EP_CLEAR)
        # disable: no response, FIFO flushed, re-enable starts at DATA0 with an empty FIFO
        await o1.host_out(pat(8, 7), toggle=o1.status("ep_toggle"))
        await o1.cmd(CMD_EP_DISABLE)
        n = len(bus.tx_packets)
        await bus.send(token(PID.OUT, 0, 1))
        await bus.send(data_packet(PID.DATA0, b"x"))
        await bus.tick(60)
        checks.check(f"control[{t}]: disabled endpoint: no response", len(bus.tx_packets) == n)
        await o1.enable()
        checks.check(f"control[{t}]: re-enable: empty FIFO, toggle 0, not halted",
                     o1.status("ep_level") == 0 and o1.status("ep_toggle") == 0 and o1.status("ep_halted") == 0)
        await o1.host_out(pat(8, 6))
        queued = o1.status("ep_level")
        await o1.cmd(CMD_EP_ENABLE, arg=M)
        checks.check(f"control[{t}]: ENABLE of an enabled endpoint flushes and resets the toggle",
                     queued == 1 and o1.status("ep_level") == 0 and o1.status("ep_toggle") == 0, f"{queued}")
        await o1.host_out(pat(8, 8))
        await i1.push(pat(4, 4))
        await reset_to(bus, hs)
        checks.check(f"control[{t}]: bus reset disables all endpoints and flushes",
                     o1.status("ep_enabled") == 0 and i1.status("ep_enabled") == 0
                     and o1.status("ep_level") == 0 and i1.status("ep_level") == 0)
        n = len(bus.tx_packets)
        await bus.send(token(PID.IN, 0, 1))
        await bus.tick(60)
        checks.check(f"control[{t}]: after reset the endpoint does not answer", len(bus.tx_packets) == n)
    build(body)


# ------------------------------------------------------------------ large

async def host_send(e, data, log):
    """Host side of a bulk OUT transfer, USB 2.0 8.5.1: PING after a NAK / NYET in HS; ZLP ends a maxp multiple."""
    M = maxp(e.hs)
    packets = [data[i:i + M] for i in range(0, len(data), M)] or [b""]
    if data and len(data) % M == 0:
        packets.append(b"")
    ping = False
    for pkt in packets:
        for _ in range(LOOP_LIMIT):
            if e.hs and ping:
                if await e.host_ping() != ACK:
                    await e.bus.tick(8)
                    continue
                ping = False
            r = await e.host_out(pkt)
            if r in (ACK, NYET):
                e.tog ^= 1
                ping = r == NYET
                break
            if r == NAK:
                ping = e.hs
                await e.bus.tick(8)
                continue
            log.append(f"OUT got {r}")
            return False
        else:
            log.append("OUT NAK storm")
            return False
    return True


async def host_receive(e, log, count=None):
    """Host side of a bulk IN transfer: packets until a short one (ZLP included), or `count` packets."""
    M = maxp(e.hs)
    pkts = []
    while True:
        for _ in range(LOOP_LIMIT * 4):
            r = await e.host_in()
            if r is None:
                log.append("IN timeout")
                return pkts
            if r != NAK:
                break
            await e.bus.tick(8)
        else:
            log.append("IN NAK storm")
            return pkts
        payload = bytes(r[1:-2])
        if r != list(data_packet(e.data_pid(e.tog), payload)):
            log.append(f"IN bad packet / toggle {e.tog}: {r[:4]}")
            return pkts
        await e.host_ack()
        e.tog ^= 1
        pkts.append(payload)
        if len(payload) < M or len(pkts) == count:
            return pkts


def packets_of(sizes, M, zlp):
    want = []
    for n in sizes:
        want += [M] * (n // M) + ([n % M] if n % M else [])
        if n % M == 0 and zlp:
            want.append(0)
    return want


@both
def section_large(checks, hs):
    t, M = tag(hs), maxp(hs)
    sizes = (M * 4, M * 3 + 37, M + 1, 1)
    out_data = [pat(n, n) for n in sizes]
    in_data = [pat(n, n + 1) for n in sizes]
    got_out, produced = [], []

    async def consumer(ctx, dev):
        """Throttled: ready toggles every 700 cycles, so the FIFO fills and the host sees NAK / NYET."""
        s = dev.ep_out[1]
        cur = bytearray()
        for cycle in range(400_000):
            ready = int((cycle // 700) % 2 == 0)
            ctx.set(s.ready, ready)
            *_, v, d, lst, z = await ctx.tick("usb").sample(s.valid, s.data, s.last, s.zlp)
            if v and ready:
                if z:
                    got_out.append(b"")
                else:
                    cur.append(d)
                    if lst:
                        got_out.append(bytes(cur))
                        cur = bytearray()

    async def producer(ctx, dev):
        """Bursty: a gap of 300 cycles after every 256 bytes, so the IN FIFO also runs dry."""
        s = dev.ep_in[2]
        n = 0
        for blob in in_data:
            for i, b in enumerate(blob):
                if n and n % 256 == 0:
                    ctx.set(s.valid, 0)
                    for _ in range(300):
                        await ctx.tick("usb")
                ctx.set(s.data, b)
                ctx.set(s.last, int(i == len(blob) - 1))
                ctx.set(s.zlp, 0)
                ctx.set(s.valid, 1)
                for _ in range(PRODUCER_LIMIT):
                    *_, ready = await ctx.tick("usb").sample(s.ready)
                    if ready:
                        break
                else:
                    return
                n += 1
            produced.append(len(blob))
        ctx.set(s.valid, 0)

    async def body(ctx, dev):
        bus = Bus(ctx, dev)
        await start(bus, hs)
        o1, i2 = Ep(bus, 1, False, hs), Ep(bus, 2, True, hs)
        await o1.enable()
        await i2.enable(zlp=True)
        log = []
        ok = True
        nak0 = bus.get(dev.n_nak) + bus.get(dev.n_nyet)
        for d in out_data:
            ok &= await host_send(o1, d, log)
        await bus.tick(1500)
        flat = b"".join(got_out)
        checks.check(f"large[{t}]: OUT transfers complete, no host-side error", ok, " ".join(log))
        checks.check(f"large[{t}]: OUT bytes arrive in order", flat == b"".join(out_data), f"{len(flat)} of {sum(sizes)}")
        want = packets_of(sizes, M, True)
        checks.check(f"large[{t}]: OUT packet boundaries kept (ZLP after a maxp multiple)",
                     [len(g) for g in got_out] == want, f"{[len(g) for g in got_out][:14]} vs {want[:14]}")
        checks.check(f"large[{t}]: throttled consumer made the device NAK / NYET",
                     bus.get(dev.n_nak) + bus.get(dev.n_nyet) > nak0)
        rec = []
        for n in sizes:
            pk = await host_receive(i2, log)
            rec.append(pk)
        flat_in = b"".join(b"".join(p) for p in rec)
        checks.check(f"large[{t}]: IN bytes arrive in order, no host-side error", flat_in == b"".join(in_data)
                     and not log, f"{len(flat_in)} of {sum(sizes)} {log}")
        shapes = [[len(x) for x in p] for p in rec]
        want_in = [packets_of([n], M, True) for n in sizes]
        checks.check(f"large[{t}]: IN transfers split at maxp, ZLP after a maxp multiple",
                     shapes == want_in, f"{shapes} vs {want_in}")
    build(body, extra=(consumer, producer))


# ------------------------------------------------------------------ rate

RATE_PACKETS = 12
RATE_SIM_CYCLES = 40_000    # consumer lifetime: 12 packets take <= ~14k cycles with one buffer, x ~2.8
RATE = {}               # slots -> (OUT cycles per packet, IN cycles per packet), filled by section_rate


def section_rate(checks):
    """HS, full-rate AUX side: cycles per 512 B packet for 1, 2, 4 buffers per FIFO (sim cycles, no inter-packet gaps)."""
    M = 512
    payload = pat(M * RATE_PACKETS, 9)
    for slots in (1, 2, 4):
        got = []

        async def consumer(ctx, dev):
            s = dev.ep_out[1]
            ctx.set(s.ready, 1)
            cur = bytearray()
            for _ in range(RATE_SIM_CYCLES):
                *_, v, d, lst, z = await ctx.tick("usb").sample(s.valid, s.data, s.last, s.zlp)
                if v:
                    cur.append(d)
                    if lst:
                        got.append(bytes(cur))
                        cur = bytearray()

        async def producer(ctx, dev):
            s = dev.ep_in[2]
            for i, b in enumerate(payload):
                ctx.set(s.data, b)
                ctx.set(s.last, int(i == len(payload) - 1))
                ctx.set(s.zlp, 0)
                ctx.set(s.valid, 1)
                for _ in range(PRODUCER_LIMIT):
                    *_, ready = await ctx.tick("usb").sample(s.ready)
                    if ready:
                        break
            ctx.set(s.valid, 0)

        res = {}

        async def body(ctx, dev):
            bus = Bus(ctx, dev)
            await start(bus, True)
            o1, i2 = Ep(bus, 1, False, True), Ep(bus, 2, True, True)
            await o1.enable()
            await i2.enable()
            log = []
            c0 = bus.cycles
            ok = await host_send(o1, payload, log)
            res["out"] = (bus.cycles - c0) / RATE_PACKETS
            await bus.tick(600)
            checks.check(f"rate[{slots} slot]: OUT data intact", ok and b"".join(got[:RATE_PACKETS]) == payload, " ".join(log))
            c0 = bus.cycles
            pk = await host_receive(i2, log, count=RATE_PACKETS)
            res["in"] = (bus.cycles - c0) / RATE_PACKETS
            res["in_naks"] = bus.get(dev.n_nak)
            checks.check(f"rate[{slots} slot]: IN data intact", b"".join(pk) == payload, " ".join(log))
        build(body, extra=(consumer, producer), fifo_slots=slots)
        RATE[slots] = (res["out"], res["in"])
        emit(f"  rate: {slots} slot(s) x 512 B per FIFO: OUT {res['out']:.0f} cycles/packet, IN {res['in']:.0f} cycles/packet, NAKs sent {res['in_naks']}")
    wire = 512 + 40
    checks.check("rate: 2 buffers per FIFO run within 15 % of the wire (OUT and IN)",
                 all(v <= wire * 1.15 for v in RATE[2]), f"{RATE[2]} vs {wire}")
    checks.check("rate: 1 buffer per FIFO is clearly slower than 2 (OUT)", RATE[1][0] > RATE[2][0] * 1.3, f"{RATE[1]} {RATE[2]}")
    checks.check("rate: 4 buffers are no faster than 2 within 5 % (OUT, IN)",
                 RATE[4][0] >= RATE[2][0] * 0.95 and RATE[4][1] >= RATE[2][1] * 0.95, f"{RATE[4]} {RATE[2]}")


SECTIONS = {"out": section_out, "in": section_in, "in_zlp": section_in_zlp,
            "backpressure": section_backpressure, "ping": section_ping, "halt": section_halt,
            "control": section_control, "large": section_large, "rate": section_rate}


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
