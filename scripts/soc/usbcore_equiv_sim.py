#!/usr/bin/env python3
#
# usbcore against luna (simulation oracle) and an independent Python reference.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Equivalence of `gateware/usbcore` with luna, without hardware. #612 stage S1.

    ./scripts/soc/usbcore_equiv_sim.py              # everything
    ./scripts/soc/usbcore_equiv_sim.py --seed 7     # another random draw
    ./scripts/soc/usbcore_equiv_sim.py --only crc lockstep

Sections:
- crc: CRC5 exhaustive (2048 token values) and CRC16 per byte: ours vs the bit-serial
  reference here vs luna's CRC equations.
- codec: packets encoded by ours are decoded by luna and by ours; packets encoded by luna
  and by the reference are decoded by ours. Every PID; tokens at address 0/127, endpoint
  0/15, SOF frame 0/0x7FF; data lengths 0, 1, 8, 64, 512, 1024 for DATA0/1/2/MDATA.
- reject: corrupted CRC5, CRC16 and PID check nibbles must be rejected by our decoders
  (luna's rejection of the same bytes is checked too).
- negative: the reject check is run against a receiver whose CRC check is sabotaged;
  the check must fail there, or it proves nothing.
- lockstep: the classes GUH imports (`usb_host/guh_lower.py`) against luna's, on the same
  random stimulus, every output compared every cycle: data receiver, handshake detector,
  data generator, handshake generator, inter-packet timer, ULPI translator.
- sie: the whole GUH engine on usbcore in lockstep with the same GUH source re-pointed at
  luna (written to tmp/usbcore_equiv/), on a ULPI PHY stimulus with random transfers.
  Known divergence, excluded from the stimulus: DIR taken mid-transmit (an aborted packet)
  makes luna's shared CRC count RX bytes into the TX CRC; ours keeps them apart.
- Out of scope: bit stuffing, NRZI, SYNC, EOP (the PHY's; ULPI hands over bytes).
"""

import argparse
import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402

from amaranth import ClockDomain, Module, Signal  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from luna.gateware.interface import ulpi as luna_ulpi  # noqa: E402
from luna.gateware.usb.usb2 import packet as luna_packet  # noqa: E402

import usbcore.data as core_data  # noqa: E402
from usbcore.crc import CRC16, crc5, crc5_reference, crc16_reference  # noqa: E402
from usbcore.data import DataGenerator, DataReceiver  # noqa: E402
from usbcore.handshake import HandshakeDetector, HandshakeGenerator  # noqa: E402
from usbcore.pid import PID, pid_byte  # noqa: E402
from usbcore.token import TokenDetector, TokenGenerator  # noqa: E402
from usb_host import guh_lower as compat  # noqa: E402

USB_PERIOD = 1 / 60e6
DATA_LENGTHS = (0, 1, 8, 64, 512, 1024)
DATA_PIDS = (PID.DATA0, PID.DATA1, PID.DATA2, PID.MDATA)
TOKEN_PIDS = (PID.OUT, PID.IN, PID.SETUP, PID.PING)
HANDSHAKES = (PID.ACK, PID.NAK, PID.STALL, PID.NYET)
GAP = 48            # idle cycles after a packet: > the FS tx_allowed count (10) plus margin


class BoardPlatform:
    """What the ULPI translators read from the platform: the board's D+/D- swap."""
    ulpi_extra_registers = {0x39: 0b000110}
    device = "LFE5U"


# ---------------------------------------------------------------- references

def ref_token(pid, body11):
    return [pid_byte(pid), body11 & 0xFF, (body11 >> 8) | (crc5_reference(body11) << 3)]


def ref_data(pid, payload):
    crc = crc16_reference(bytes(payload))
    return [pid_byte(pid), *payload, crc & 0xFF, crc >> 8]


def run(module, testbench, platform=None):
    top = Module()
    top.domains.usb = ClockDomain("usb")
    top.submodules.dut = module
    sim = Simulator(top) if platform is None else Simulator(_fragment(top, platform))
    sim.add_clock(USB_PERIOD, domain="usb")
    sim.add_testbench(testbench)
    sim.run()


def _fragment(module, platform):
    from amaranth.hdl import Fragment
    return Fragment.get(module, platform)


def rx_bus():
    return SimpleNamespace(rx_data=Signal(8), rx_valid=Signal(), rx_active=Signal())


def packet_cycles(data, rng=None, gap_prob=0.0):
    """Per-cycle (active, valid, data) for one received packet, then GAP idle cycles."""
    cycles = [(1, 0, 0)]
    for byte in data:
        while rng is not None and rng.random() < gap_prob:
            cycles.append((1, 0, rng.randrange(256)))
        cycles.append((1, 1, byte))
    cycles.append((0, 0, 0))
    cycles.extend([(0, 0, 0)] * GAP)
    return cycles


async def drive_rx(ctx, bus, cycles, observe):
    for index, (active, valid, data) in enumerate(cycles):
        ctx.set(bus.rx_active, active)
        ctx.set(bus.rx_valid, valid)
        ctx.set(bus.rx_data, data)
        observe(ctx, index)
        await ctx.tick("usb")


# ---------------------------------------------------------------- crc

def section_crc(checks, rng):
    m = Module()
    value = Signal(11)
    ours, luna5 = Signal(5), Signal(5)
    m.d.comb += [ours.eq(crc5(value)),
                 luna5.eq(luna_packet.USBTokenDetector._generate_crc_for_token(value))]
    bad = []

    async def tb5(ctx):
        for v in range(2048):
            ctx.set(value, v)
            got, ref, lun = ctx.get(ours), crc5_reference(v), ctx.get(luna5)
            if not (got == ref == lun):
                bad.append((v, got, ref, lun))
    run(m, tb5)
    checks.check("crc5: ours == reference == luna for all 2048 token values", not bad,
                 f"first mismatch (value, ours, ref, luna) {bad[:1]}")

    m = Module()
    m.submodules.crc = crc = CRC16()
    data = [rng.randrange(256) for _ in range(3000)]
    errors = []

    async def tb16(ctx):
        sent = []
        for index, byte in enumerate(data):
            if index % 300 == 0:
                ctx.set(crc.clear, 1)
                await ctx.tick("usb")
                ctx.set(crc.clear, 0)
                sent = []
            ctx.set(crc.update, 1)
            ctx.set(crc.data, byte)
            await ctx.tick("usb")
            sent.append(byte)
            if ctx.get(crc.crc) != crc16_reference(bytes(sent)):
                errors.append(index)
        ctx.set(crc.update, 0)
    run(m, tb16)
    checks.check("crc16: running register == reference after each of 3000 random bytes",
                 not errors, f"{len(errors)} mismatches, first at byte {errors[:1]}")
    known = crc16_reference(b"123456789")
    checks.check("crc16: reference check value of '123456789' is 0xB4C8 (CRC-16/USB)",
                 known == 0xB4C8, f"{known:#06x}")


# ---------------------------------------------------------------- codec helpers

async def collect_tx(ctx, tx, n_packets, limit, rng=None, ready_prob=1.0, observe=None):
    """Capture packets from a UTMI transmitter. A packet ends when `valid` drops."""
    packets, current = [], []
    for _ in range(limit):
        ready = 1 if rng is None or rng.random() < ready_prob else 0
        ctx.set(tx.ready, ready)
        valid = ctx.get(tx.valid)
        if observe:
            observe(ctx)
        if valid and ready:
            current.append(ctx.get(tx.data))
        elif not valid and current:
            packets.append(current)
            current = []
            if len(packets) == n_packets:
                break
        await ctx.tick("usb")
    return packets


def decoders(m, bus, *, luna=True, receiver_cls=DataReceiver):
    """Our three decoders (and luna's) on one UTMI receive bus."""
    d = SimpleNamespace()
    for name, cls in (("data", receiver_cls), ("hs", HandshakeDetector), ("tok", TokenDetector)):
        dut = cls()
        setattr(d, name, dut)
        m.submodules[name] = dut
        m.d.comb += [dut.rx.data.eq(bus.rx_data), dut.rx.valid.eq(bus.rx_valid),
                     dut.rx.active.eq(bus.rx_active)]
    m.submodules.timer = timer = compat.USBInterpacketTimer()
    timer.add_interface(d.data.timer)
    if luna:
        d.l_data = luna_packet.USBDataPacketReceiver(utmi=bus, standalone=True)
        d.l_hs = luna_packet.USBHandshakeDetector(utmi=bus)
        d.l_tok = luna_packet.USBTokenDetector(utmi=bus, filter_by_address=False)
        m.submodules.l_data, m.submodules.l_hs, m.submodules.l_tok = d.l_data, d.l_hs, d.l_tok
    return d


def decode_events(d, luna=True):
    """Per-cycle observer accumulating decoded events for ours (and luna's)."""
    ev = SimpleNamespace(ours=[], luna=[])
    cur = {"ours": [], "luna": []}

    def observe(ctx, index):
        for side, data, hs, tok in (("ours", d.data, d.hs, d.tok),) + (
                (("luna", d.l_data, d.l_hs, d.l_tok),) if luna else ()):
            out = getattr(ev, side)
            if ctx.get(data.stream.next):
                cur[side].append(ctx.get(data.stream.payload))
            if ctx.get(data.packet_complete):
                out.append(("data", ctx.get(data.packet_id), cur[side]))
                cur[side] = []
            if ctx.get(data.crc_mismatch):
                out.append(("crc_mismatch",))
                cur[side] = []
            for name in ("ack", "nak", "stall", "nyet"):
                if ctx.get(getattr(hs.detected, name)):
                    out.append(("hs", name))
            if ctx.get(tok.interface.new_token if side == "luna" else tok.new_token):
                if side == "luna":
                    i = tok.interface
                    out.append(("token", ctx.get(i.pid), ctx.get(i.address), ctx.get(i.endpoint)))
                else:
                    out.append(("token", ctx.get(tok.pid), ctx.get(tok.addr), ctx.get(tok.endp)))
            new_frame = tok.interface.new_frame if side == "luna" else tok.new_frame
            if ctx.get(new_frame):
                frame = tok.interface.frame if side == "luna" else tok.frame
                out.append(("sof", ctx.get(frame)))
    return ev, observe


def expected_event(kind, pid, body):
    if kind == "data":
        return ("data", int(pid), list(body))
    if kind == "hs":
        return ("hs", pid.name.lower())
    if pid == PID.SOF:
        return ("sof", body)
    return ("token", int(pid), body & 0x7F, body >> 7)


def decode_all(packets, rng, *, luna=True, receiver_cls=DataReceiver, gap_prob=0.0):
    """Feed byte packets to the decoders; returns (ours events, luna events)."""
    m = Module()
    bus = rx_bus()
    d = decoders(m, bus, luna=luna, receiver_cls=receiver_cls)
    ev, observe = decode_events(d, luna=luna)
    cycles = []
    for packet in packets:
        cycles += packet_cycles(packet, rng, gap_prob)

    async def tb(ctx):
        await drive_rx(ctx, bus, cycles, observe)
    run(m, tb)
    return ev.ours, ev.luna


# ---------------------------------------------------------------- codec

def codec_cases(rng):
    cases = []          # (kind, pid, body): token body = 11 bits, data body = payload list
    for pid in TOKEN_PIDS:
        for addr in (0, 1, 0x55, 127):
            for endp in (0, 1, 8, 15):
                cases.append(("token", pid, addr | (endp << 7)))
        cases += [("token", pid, rng.randrange(2048)) for _ in range(8)]
    for frame in (0, 1, 0x400, 0x7FF, *(rng.randrange(2048) for _ in range(8))):
        cases.append(("token", PID.SOF, frame))
    for pid in DATA_PIDS:
        for length in DATA_LENGTHS + tuple(rng.randrange(2, 100) for _ in range(3)):
            cases.append(("data", pid, [rng.randrange(256) for _ in range(length)]))
    for pid in HANDSHAKES:
        cases.append(("hs", pid, None))
    return cases


def encode_ours(cases, rng):
    """Encode every case with our generators; returns the captured byte packets."""
    m = Module()
    m.submodules.tok = tok = TokenGenerator()
    m.submodules.dat = dat = DataGenerator()
    m.submodules.hs = hs = HandshakeGenerator()
    m.submodules.mux = mux = compat.UTMIInterfaceMultiplexer()
    for tx in (tok.tx, dat.tx, hs.tx):
        mux.add_input(tx)
    out = []

    async def tb(ctx):
        for kind, pid, body in cases:
            if kind == "token":
                ctx.set(tok.i.payload, {"pid": int(pid), "data": body})
                ctx.set(tok.i.valid, 1)
                await ctx.tick("usb")
                ctx.set(tok.i.valid, 0)
            elif kind == "hs":
                strobe = getattr(hs, f"issue_{pid.name.lower()}")
                ctx.set(strobe, 1)
                await ctx.tick("usb")
                ctx.set(strobe, 0)
            else:
                ctx.set(dat.data_pid, DATA_PIDS.index(pid))
                if not body:
                    ctx.set(dat.stream.valid, 1)
                    ctx.set(dat.stream.last, 1)
                    await ctx.tick("usb")
                    ctx.set(dat.stream.valid, 0)
                    ctx.set(dat.stream.last, 0)
                else:
                    # GUH style: the payload is waiting; present byte 0 with `first`.
                    out.extend(await _feed_data(ctx, dat, mux.output, body, rng))
                    continue
            out.extend(await collect_tx(ctx, mux.output, 1, 5000, rng, 0.7))
    run(m, tb)
    return out


async def _feed_data(ctx, gen, tx, payload, rng):
    """Drive a payload into a data generator while capturing its UTMI output."""
    index, captured = 0, []
    for _ in range(20 * (len(payload) + 8)):
        ctx.set(gen.stream.valid, index < len(payload))
        ctx.set(gen.stream.payload, payload[min(index, len(payload) - 1)])
        ctx.set(gen.stream.first, index == 0)
        ctx.set(gen.stream.last, index == len(payload) - 1)
        ready = 1 if rng.random() < 0.7 else 0
        ctx.set(tx.ready, ready)
        if ctx.get(tx.valid) and ready:
            captured.append(ctx.get(tx.data))
        elif not ctx.get(tx.valid) and captured:
            break
        if ctx.get(gen.stream.ready) and index < len(payload):
            index += 1
        await ctx.tick("usb")
    ctx.set(gen.stream.valid, 0)
    return [captured]


def encode_luna(cases, rng):
    """Encode the data and ACK/NAK/STALL cases with luna's generators (luna has no token
    generator and no NYET)."""
    m = Module()
    m.submodules.dat = dat = luna_packet.USBDataPacketGenerator(standalone=True)
    m.submodules.hs = hs = luna_packet.USBHandshakeGenerator()
    out, kept = [], []

    async def tb(ctx):
        for kind, pid, body in cases:
            if kind == "hs" and pid != PID.NYET:
                strobe = getattr(hs, f"issue_{pid.name.lower()}")
                ctx.set(strobe, 1)
                await ctx.tick("usb")
                ctx.set(strobe, 0)
                out.extend(await collect_tx(ctx, hs.tx, 1, 100))
                kept.append((kind, pid, body))
            elif kind == "data":
                ctx.set(dat.data_pid, DATA_PIDS.index(pid))
                if not body:
                    ctx.set(dat.stream.valid, 1)
                    ctx.set(dat.stream.last, 1)
                    await ctx.tick("usb")
                    ctx.set(dat.stream.valid, 0)
                    ctx.set(dat.stream.last, 0)
                    out.extend(await collect_tx(ctx, dat.tx, 1, 100))
                else:
                    out.extend(await _feed_luna_data(ctx, dat, body))
                kept.append((kind, pid, body))
    run(m, tb)
    return kept, out


async def _feed_luna_data(ctx, gen, payload):
    """luna's standalone CRC counts `payload` on every `tx.ready`: keep ready high."""
    index, captured = 0, []
    ctx.set(gen.tx.ready, 1)
    for _ in range(len(payload) + 16):
        ctx.set(gen.stream.valid, 1)
        ctx.set(gen.stream.payload, payload[min(index, len(payload) - 1)])
        ctx.set(gen.stream.first, index == 0)
        ctx.set(gen.stream.last, index == len(payload) - 1)
        if ctx.get(gen.tx.valid):
            captured.append(ctx.get(gen.tx.data))
        elif captured:
            break
        if ctx.get(gen.stream.ready) and index < len(payload) - 1:
            index += 1
        await ctx.tick("usb")
    ctx.set(gen.stream.valid, 0)
    return [captured]


def reference_bytes(kind, pid, body):
    if kind == "token":
        return ref_token(int(pid), body)
    if kind == "data":
        return ref_data(int(pid), body)
    return [pid_byte(int(pid))]


def section_codec(checks, rng):
    cases = codec_cases(rng)
    n_tok = sum(k == "token" for k, _, _ in cases)
    n_dat = sum(k == "data" for k, _, _ in cases)
    checks.note(f"{len(cases)} cases: {n_tok} tokens, {n_dat} data packets "
                f"(lengths {DATA_LENGTHS} + random), {len(HANDSHAKES)} handshakes")

    ours_bytes = encode_ours(cases, rng)
    ref = [reference_bytes(*c) for c in cases]
    diff = [i for i, (a, b) in enumerate(zip(ours_bytes, ref)) if a != b]
    checks.check("encode: our generators' bytes == the reference packets",
                 len(ours_bytes) == len(ref) and not diff,
                 f"{len(ours_bytes)} packets vs {len(ref)}; first differing case {diff[:1]}")

    expected = [expected_event(*c) for c in cases]
    ours_ev, luna_ev = decode_all(ours_bytes, rng)
    checks.check("ours -> luna: luna decodes every packet we encoded (tokens, data, handshakes)",
                 luna_ev == expected, _first_diff(luna_ev, expected))
    checks.check("ours -> ours: our decoders agree", ours_ev == expected,
                 _first_diff(ours_ev, expected))

    ours_ev, _ = decode_all(ref, rng, luna=False, gap_prob=0.75)
    checks.check("reference -> ours: decoded with FS-like byte gaps (valid 1 cycle in ~4)",
                 ours_ev == expected, _first_diff(ours_ev, expected))

    kept, luna_bytes = encode_luna(cases, rng)
    ref_kept = [reference_bytes(*c) for c in kept]
    checks.check("luna's generators' bytes == the reference packets (oracle sanity)",
                 luna_bytes == ref_kept, _first_diff(luna_bytes, ref_kept))
    ours_ev, _ = decode_all(luna_bytes, rng, luna=False)
    expected = [expected_event(*c) for c in kept]
    checks.check("luna -> ours: our decoders decode every packet luna encoded",
                 ours_ev == expected, _first_diff(ours_ev, expected))


def _first_diff(got, want):
    if len(got) != len(want):
        return f"{len(got)} events, expected {len(want)}"
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return f"event {i}: got {str(a)[:120]} want {str(b)[:120]}"
    return ""


# ---------------------------------------------------------------- reject / negative

def corrupted(rng):
    """(description, bytes) packets that a correct decoder must not accept."""
    out = []
    for pid in DATA_PIDS:
        for length in (0, 1, 8, 64):
            good = ref_data(int(pid), [rng.randrange(256) for _ in range(length)])
            for where in sorted({1, len(good) - 2, len(good) - 1}):
                bad = list(good)
                bad[where] ^= 1 << rng.randrange(8)
                out.append((f"{pid.name} len {length}: bit flip at byte {where}", bad))
            bad = list(good)
            bad[0] ^= 0x10 << rng.randrange(4)
            out.append((f"{pid.name} len {length}: PID check nibble", bad))
    for pid in TOKEN_PIDS + (PID.SOF,):
        good = ref_token(int(pid), rng.randrange(2048))
        bad = list(good)
        bad[2] ^= 0x08 << rng.randrange(5)
        out.append((f"{pid.name}: CRC5 bit", bad))
        bad = list(good)
        bad[rng.choice((1, 2))] ^= 1 << rng.randrange(3)
        out.append((f"{pid.name}: token body bit", bad))
        bad = list(good)
        bad[0] ^= 0x10 << rng.randrange(4)
        out.append((f"{pid.name}: PID check nibble", bad))
    for pid in HANDSHAKES:
        out.append((f"{pid.name}: PID check nibble", [pid_byte(int(pid)) ^ (0x10 << rng.randrange(4))]))
    return out


def accepted(events):
    return [e for e in events if e[0] != "crc_mismatch"]


def reject_failures(rng, receiver_cls=DataReceiver):
    """Corrupted packets that our decoders (or luna's) accepted."""
    cases = corrupted(rng)
    ours_bad, luna_bad = [], []
    for desc, packet in cases:
        ours, luna = decode_all([packet], rng, receiver_cls=receiver_cls)
        if accepted(ours):
            ours_bad.append(desc)
        if accepted(luna):
            luna_bad.append(desc)
    return len(cases), ours_bad, luna_bad


class _CRCAlwaysGood(CRC16):
    def elaborate(self, platform):
        m = super().elaborate(platform)
        m.d.comb += self.good.eq(1)
        return m


class SabotagedReceiver(DataReceiver):
    """A receiver whose CRC16 check passes everything: the negative control."""

    def elaborate(self, platform):
        saved = core_data.CRC16
        core_data.CRC16 = _CRCAlwaysGood
        try:
            return super().elaborate(platform)
        finally:
            core_data.CRC16 = saved


def section_reject(checks, rng):
    n, ours_bad, luna_bad = reject_failures(random.Random(rng.random()))
    checks.check(f"reject: our decoders accept none of {n} corrupted packets "
                 "(CRC16, CRC5, token body, PID check nibble)", not ours_bad,
                 f"accepted: {ours_bad[:4]}")
    checks.check("reject: luna rejects the same packets (the corruption is real)",
                 not luna_bad, f"luna accepted: {luna_bad[:4]}")


def section_negative(checks, rng):
    _, sabotaged_bad, _ = reject_failures(random.Random(rng.random()), SabotagedReceiver)
    data_flips = [d for d in sabotaged_bad if "bit flip" in d]
    checks.check("negative control: with the CRC16 check sabotaged, the reject check FAILS "
                 "(it catches an accepting decoder)", bool(data_flips),
                 "the sabotaged receiver was not caught: the reject check proves nothing")
    checks.note(f"sabotaged receiver accepted {len(data_flips)} CRC-corrupted data packets")


# ---------------------------------------------------------------- lockstep

def lockstep_rx_stimulus(rng, n_packets):
    cycles = []
    for _ in range(n_packets):
        roll = rng.random()
        pid = rng.choice(list(PID))
        if roll < 0.35:
            pkt = ref_data(int(rng.choice(DATA_PIDS)),
                           [rng.randrange(256) for _ in range(rng.choice((0, 1, 2, 3, 8, 30, 64)))])
        elif roll < 0.5:
            pkt = ref_data(int(rng.choice(DATA_PIDS)), [rng.randrange(256) for _ in range(9)])
            pkt[rng.randrange(len(pkt))] ^= 1 << rng.randrange(8)
        elif roll < 0.65:
            pkt = [pid_byte(int(rng.choice(HANDSHAKES)))]
        elif roll < 0.75:
            pkt = ref_token(int(rng.choice(TOKEN_PIDS + (PID.SOF,))), rng.randrange(2048))
        elif roll < 0.85:
            pkt = [pid_byte(int(pid))] + [rng.randrange(256) for _ in range(rng.randrange(3))]
        else:
            # Noise: active and valid at random, any data.
            for _ in range(rng.randrange(4, 40)):
                cycles.append((rng.random() < 0.7, rng.random() < 0.5, rng.randrange(256)))
            continue
        cycles += packet_cycles(pkt, rng, rng.choice((0.0, 0.0, 0.5)))[:-GAP + rng.randrange(GAP)]
    return cycles


def section_lockstep_rx(checks, rng, speed):
    m = Module()
    bus = rx_bus()
    ours = compat.USBDataPacketReceiver(utmi=bus)
    ours_hs = compat.USBHandshakeDetector(utmi=bus)
    ours_timer = compat.USBInterpacketTimer()
    ours_timer.add_interface(ours.timer)
    luna = luna_packet.USBDataPacketReceiver(utmi=bus)
    luna_hs = luna_packet.USBHandshakeDetector(utmi=bus)
    luna_crc = luna_packet.USBDataPacketCRC()
    luna_crc.add_interface(luna.data_crc)
    luna_timer = luna_packet.USBInterpacketTimer()
    luna_timer.add_interface(luna.timer)
    for name, dut in (("ours", ours), ("ours_hs", ours_hs), ("ours_timer", ours_timer),
                      ("luna", luna), ("luna_hs", luna_hs), ("luna_crc", luna_crc),
                      ("luna_timer", luna_timer)):
        m.submodules[name] = dut
    m.d.comb += [luna_crc.rx_data.eq(bus.rx_data), luna_crc.rx_valid.eq(bus.rx_valid),
                 ours_timer.speed.eq(speed), luna_timer.speed.eq(speed)]
    pairs = [("stream.next", ours.stream.next, luna.stream.next),
             ("stream.valid", ours.stream.valid, luna.stream.valid),
             ("packet_complete", ours.packet_complete, luna.packet_complete),
             ("crc_mismatch", ours.crc_mismatch, luna.crc_mismatch),
             ("ready_for_response", ours.ready_for_response, luna.ready_for_response),
             ("packet_id", ours.packet_id, luna.packet_id),
             ("active_pid", ours.active_pid, luna.active_pid)]
    pairs += [(f"detected.{n}", getattr(ours_hs.detected, n), getattr(luna_hs.detected, n))
              for n in ("ack", "nak", "stall", "nyet")]
    cycles = lockstep_rx_stimulus(rng, 600)
    mism = []
    counts = {"payload": 0, "complete": 0, "mismatch": 0, "handshake": 0}

    def observe(ctx, index):
        for name, a, b in pairs:
            if ctx.get(a) != ctx.get(b):
                mism.append((index, name))
        if ctx.get(luna.stream.next):
            counts["payload"] += 1
            if ctx.get(ours.stream.payload) != ctx.get(luna.stream.payload):
                mism.append((index, "stream.payload"))
        counts["complete"] += ctx.get(luna.packet_complete)
        counts["mismatch"] += ctx.get(luna.crc_mismatch)
        counts["handshake"] += sum(ctx.get(getattr(luna_hs.detected, n))
                                   for n in ("ack", "nak", "stall", "nyet"))

    async def tb(ctx):
        await drive_rx(ctx, bus, cycles, observe)
    run(m, tb)
    label = "HS" if speed == 0 else "FS"
    checks.check(f"lockstep {label}: data receiver + handshake detector == luna's, "
                 f"{len(cycles)} cycles", not mism, f"{len(mism)} mismatches, first {mism[:3]}")
    checks.note(f"exercised: {counts}")


def section_lockstep_tx(checks, rng):
    m = Module()
    ours, luna = compat.USBDataPacketGenerator(), luna_packet.USBDataPacketGenerator()
    ours_hs, luna_hs = compat.USBHandshakeGenerator(), luna_packet.USBHandshakeGenerator()
    luna_crc = luna_packet.USBDataPacketCRC()
    luna_crc.add_interface(luna.crc)
    for name, dut in (("ours", ours), ("luna", luna), ("ours_hs", ours_hs),
                      ("luna_hs", luna_hs), ("luna_crc", luna_crc)):
        m.submodules[name] = dut
    # Inputs shared by both: driven on ours, copied to luna's.
    ready = Signal()
    m.d.comb += [
        luna.data_pid.eq(ours.data_pid), luna.stream.valid.eq(ours.stream.valid),
        luna.stream.payload.eq(ours.stream.payload), luna.stream.first.eq(ours.stream.first),
        luna.stream.last.eq(ours.stream.last), ours.tx.ready.eq(ready), luna.tx.ready.eq(ready),
        luna_hs.issue_ack.eq(ours_hs.issue_ack), luna_hs.issue_nak.eq(ours_hs.issue_nak),
        luna_hs.issue_stall.eq(ours_hs.issue_stall),
        ours_hs.tx.ready.eq(ready), luna_hs.tx.ready.eq(ready),
        # luna's CRC is fed as GUH feeds it: accepted UTMI bytes.
        luna_crc.tx_valid.eq(luna.tx.valid & ready), luna_crc.tx_data.eq(luna.tx.data),
    ]
    mism, stats = [], {"packets": 0, "bytes": 0, "handshakes": 0}

    def compare(ctx, cycle):
        for name, a, b in (("tx.valid", ours.tx.valid, luna.tx.valid),
                           ("stream.ready", ours.stream.ready, luna.stream.ready),
                           ("hs.tx.valid", ours_hs.tx.valid, luna_hs.tx.valid)):
            if ctx.get(a) != ctx.get(b):
                mism.append((cycle, name))
        if ctx.get(luna.tx.valid) and ctx.get(ours.tx.data) != ctx.get(luna.tx.data):
            mism.append((cycle, "tx.data"))
        if ctx.get(luna_hs.tx.valid) and ctx.get(ours_hs.tx.data) != ctx.get(luna_hs.tx.data):
            mism.append((cycle, "hs.tx.data"))

    async def tb(ctx):
        cycle = 0
        for _ in range(250):
            length = rng.choice((0, 1, 2, 5, 17, 64, 200))
            payload = [rng.randrange(256) for _ in range(length)]
            ctx.set(ours.data_pid, rng.randrange(4))
            underflow = rng.random() < 0.1
            index, guard = 0, 0
            while guard < 20 * (length + 10):
                guard += 1
                gap = underflow and rng.random() < 0.05
                ctx.set(ours.stream.valid, (index < length or length == 0) and not gap)
                ctx.set(ours.stream.payload, payload[index] if index < length else 0)
                ctx.set(ours.stream.first, index == 0 and length > 0)
                ctx.set(ours.stream.last, index == length - 1 or length == 0)
                ctx.set(ready, rng.random() < 0.6)
                if rng.random() < 0.02:
                    ctx.set(rng.choice((ours_hs.issue_ack, ours_hs.issue_nak, ours_hs.issue_stall)), 1)
                compare(ctx, cycle)
                took = ctx.get(ours.stream.ready) and ctx.get(ours.stream.valid)
                stats["bytes"] += took
                stats["handshakes"] += ctx.get(ours_hs.tx.valid) and ctx.get(ready)
                await ctx.tick("usb")
                cycle += 1
                for s in (ours_hs.issue_ack, ours_hs.issue_nak, ours_hs.issue_stall):
                    ctx.set(s, 0)
                if length == 0:
                    ctx.set(ours.stream.valid, 0)
                    length = -1
                if took:
                    index += 1
                    if index >= length:
                        ctx.set(ours.stream.valid, 0)
                if index >= length and not ctx.get(ours.tx.valid) and guard > 4:
                    break
            stats["packets"] += 1
            for _ in range(rng.randrange(1, 6)):
                ctx.set(ready, rng.random() < 0.6)
                compare(ctx, cycle)
                await ctx.tick("usb")
                cycle += 1
        stats["cycles"] = cycle
    run(m, tb)
    checks.check("lockstep: data generator + handshake generator == luna's (random tx_ready, "
                 "ZLPs, stream underflow)", not mism,
                 f"{len(mism)} mismatches, first {mism[:3]}")
    checks.note(f"exercised: {stats}")


def section_lockstep_timer(checks, rng):
    m = Module()
    speed = Signal(2)
    starts = [Signal(name=f"start{i}") for i in range(2)]
    duts = []
    for fs_only in (False, True):
        ours, luna = compat.USBInterpacketTimer(fs_only=fs_only), \
            luna_packet.USBInterpacketTimer(fs_only=fs_only)
        o_if = [compat.InterpacketTimerInterface() for _ in starts]
        l_if = [luna_packet.InterpacketTimerInterface() for _ in starts]
        for o, l, s in zip(o_if, l_if, starts):
            ours.add_interface(o)
            luna.add_interface(l)
            m.d.comb += [o.start.eq(s), l.start.eq(s)]
        m.d.comb += [ours.speed.eq(speed), luna.speed.eq(speed)]
        m.submodules[f"ours{int(fs_only)}"], m.submodules[f"luna{int(fs_only)}"] = ours, luna
        duts.append((fs_only, o_if, l_if))
    mism = []

    async def tb(ctx):
        for cycle in range(20000):
            if cycle % 700 == 0:
                ctx.set(speed, rng.randrange(4))
            for s in starts:
                ctx.set(s, rng.random() < 0.01)
            for fs_only, o_if, l_if in duts:
                for o, l in zip(o_if, l_if):
                    for name in ("tx_allowed", "tx_timeout", "rx_timeout"):
                        if ctx.get(getattr(o, name)) != ctx.get(getattr(l, name)):
                            mism.append((cycle, fs_only, name))
            await ctx.tick("usb")
    run(m, tb)
    checks.check("lockstep: inter-packet timer == luna's (all four speed codes, fs_only both, "
                 "two clients, 20000 cycles)", not mism, f"first {mism[:3]}")


def ulpi_pins(shared):
    return SimpleNamespace(
        dir=SimpleNamespace(i=shared.dir), nxt=SimpleNamespace(i=shared.nxt),
        data=SimpleNamespace(i=shared.data, o=Signal(8), oe=Signal()),
        stp=SimpleNamespace(o=Signal()))


def section_lockstep_ulpi(checks, rng, n_cycles):
    m = Module()
    shared = SimpleNamespace(dir=Signal(), nxt=Signal(), data=Signal(8))
    o_pins, l_pins = ulpi_pins(shared), ulpi_pins(shared)
    ours = compat.UTMITranslator(ulpi=o_pins, handle_clocking=False)
    luna = luna_ulpi.UTMITranslator(ulpi=l_pins, handle_clocking=False)
    m.submodules.ours, m.submodules.luna = ours, luna
    inputs = ("tx_data", "tx_valid", "op_mode", "xcvr_select", "term_select", "suspend",
              "id_pullup", "dm_pulldown", "dp_pulldown", "chrg_vbus", "dischrg_vbus",
              "use_external_vbus_indicator")
    m.d.comb += [getattr(luna, n).eq(getattr(ours, n)) for n in inputs]
    outputs = ("tx_ready", "rx_data", "rx_valid", "rx_active", "line_state", "vbus_valid",
               "session_valid", "session_end", "rx_error", "host_disconnect", "id_digital",
               "busy", "last_rx_command")
    pins = (("data.o", o_pins.data.o, l_pins.data.o), ("data.oe", o_pins.data.oe, l_pins.data.oe),
            ("stp.o", o_pins.stp.o, l_pins.stp.o))
    mism, stats = [], {"tx_bytes": 0, "rx_bytes": 0, "rx_active_cycles": 0, "busy_cycles": 0,
                       "stp": 0}

    async def tb(ctx):
        dir_left, tx_left = 0, 0
        for cycle in range(n_cycles):
            # PHY side: DIR in runs; NXT and data random (RxActive bit often set).
            if dir_left == 0:
                ctx.set(shared.dir, not ctx.get(shared.dir) if rng.random() < 0.7 else 0)
                dir_left = rng.choice((1, 2, 3, 5, 8, 20, 60))
            dir_left -= 1
            ctx.set(shared.nxt, rng.random() < (0.6 if ctx.get(shared.dir) else 0.4))
            data = rng.randrange(256)
            if rng.random() < 0.5:
                data |= 0x10
            ctx.set(shared.data, data)
            # Link side: GUH-like packets (valid held until the last byte is taken).
            if tx_left == 0 and rng.random() < 0.04:
                tx_left = rng.randrange(1, 8)
                ctx.set(ours.tx_data, rng.randrange(256))
            ctx.set(ours.tx_valid, tx_left > 0)
            if cycle % 400 == 0:
                ctx.set(ours.op_mode, rng.choice((0, 0, 2, 1, 3)))
                ctx.set(ours.xcvr_select, rng.randrange(4))
                ctx.set(ours.term_select, rng.randrange(2))
                ctx.set(ours.dp_pulldown, rng.random() < 0.8)
                ctx.set(ours.dm_pulldown, rng.random() < 0.8)
                ctx.set(ours.suspend, rng.random() < 0.05)
            for name in outputs:
                if ctx.get(getattr(ours, name)) != ctx.get(getattr(luna, name)):
                    mism.append((cycle, name))
            for name, a, b in pins:
                if ctx.get(a) != ctx.get(b):
                    mism.append((cycle, name))
            stats["rx_bytes"] += ctx.get(luna.rx_valid)
            stats["rx_active_cycles"] += ctx.get(luna.rx_active)
            stats["busy_cycles"] += ctx.get(luna.busy)
            stats["stp"] += ctx.get(l_pins.stp.o)
            if tx_left and ctx.get(ours.tx_ready):
                stats["tx_bytes"] += 1
                tx_left -= 1
                ctx.set(ours.tx_data, rng.randrange(256))
            await ctx.tick("usb")
    run(m, tb, BoardPlatform())
    checks.check(f"lockstep: ULPI translator == luna's UTMITranslator ({n_cycles} cycles, random "
                 "DIR/NXT/RXCMD, TX packets, op_mode incl. chirp, register writes, 0x39 swap)",
                 not mism, f"{len(mism)} mismatches, first {mism[:4]}")
    checks.note(f"exercised: {stats}")


# ---------------------------------------------------------------- whole engine

GUH_DIR = ROOT / "gateware" / "probes" / "usb_host" / "guh"
LUNA_IMPORTS = {
    "sie.py": ("from ..guh_lower import *\n",
               "from luna.gateware.interface.ulpi import UTMITranslator\n"
               "from luna.gateware.interface.utmi import *\n"
               "from luna.gateware.usb.usb2.packet import *\n"),
    "reset.py": ("from ..guh_lower import UTMITransmitInterface\n",
                 "from luna.gateware.interface.utmi import UTMITransmitInterface\n"),
    "types.py": ("from ..guh_lower import UTMIOperatingMode, UTMITerminationSelect\n"
                 "from ..guh_lower import USBSpeed as LUNAUSBSpeed\n",
                 "from luna.gateware.interface.utmi import UTMIOperatingMode, UTMITerminationSelect\n"
                 "from luna.gateware.usb.usb2 import USBSpeed as LUNAUSBSpeed\n"),
}
# Shared, simulable timers for both engines (same role as model.scale_timing_for_simulation).
SIE_TIMING = {
    "USBResetController": {"_SETTLE_TIME": 600, "_MAX_RESET_TIME": 15000,
                           "_MIN_RESET_BEFORE_CHIRP": 300, "_CHIRP_FILTER_CYCLES": 1500,
                           "_CHIRP_DURATION": 300},
    "USBSOFController": {"_SOF_CYCLES_FS": 6000, "_SOF_TX_TO_TX_MIN_FS": 1200,
                         "_SOF_TX_TO_TX_MAX_FS": 4200, "_SOF_TX_TO_RX_MAX_FS": 5400,
                         "_SOF_CYCLES_HS": 3750, "_SOF_TX_TO_TX_MIN_HS": 30,
                         "_SOF_TX_TO_TX_MAX_HS": 2850, "_SOF_TX_TO_RX_MAX_HS": 3690},
}


def guh_on_luna():
    """The current GUH source with its import lines pointed back at luna, as `guh_luna_ref`."""
    import importlib
    out = ROOT / "tmp" / "usbcore_equiv" / "guh_luna_ref"
    out.mkdir(parents=True, exist_ok=True)
    (out / "__init__.py").write_text("")
    for name, (ours, luna) in LUNA_IMPORTS.items():
        text = (GUH_DIR / name).read_text()
        if ours not in text:
            raise RuntimeError(f"guh/{name}: import line changed; update LUNA_IMPORTS")
        (out / name).write_text(text.replace(ours, luna))
    sys.path.insert(0, str(out.parent))
    return importlib.import_module("guh_luna_ref.sie"), importlib.import_module("guh_luna_ref.reset")


def section_sie(checks, rng, n_cycles):
    from usb_host.guh import reset as ours_reset, sie as ours_sie
    ref_sie, ref_reset = guh_on_luna()
    for module_sie, module_reset in ((ours_sie, ours_reset), (ref_sie, ref_reset)):
        for cls, values in SIE_TIMING.items():
            target = getattr(module_reset if cls == "USBResetController" else module_sie, cls)
            for attr, value in values.items():
                setattr(target, attr, value)

    m = Module()
    shared = SimpleNamespace(dir=Signal(), nxt=Signal(), data=Signal(8))
    o_pins, l_pins = ulpi_pins(shared), ulpi_pins(shared)
    ours = ours_sie.USBSIE(bus=o_pins, handle_clocking=False, fifo_depth=64)
    luna = ref_sie.USBSIE(bus=l_pins, handle_clocking=False, fifo_depth=64)
    m.submodules.ours, m.submodules.luna = ours, luna
    oc, lc = ours.ctrl, luna.ctrl
    m.d.comb += [
        lc.bus_reset.eq(oc.bus_reset), lc.tx_flush.eq(oc.tx_flush),
        lc.xfer.as_value().eq(oc.xfer.as_value()),
        lc.txs.valid.eq(oc.txs.valid), lc.txs.payload.eq(oc.txs.payload),
        lc.rxs.ready.eq(oc.rxs.ready),
    ]
    compare = (("data.o", o_pins.data.o, l_pins.data.o), ("data.oe", o_pins.data.oe, l_pins.data.oe),
               ("stp.o", o_pins.stp.o, l_pins.stp.o), ("status", oc.status.as_value(),
               lc.status.as_value()), ("tx_pending", oc.tx_pending, lc.tx_pending),
               ("txs.ready", oc.txs.ready, lc.txs.ready), ("rxs.valid", oc.rxs.valid, lc.rxs.valid),
               ("utmi.busy", ours.utmi.busy, luna.utmi.busy),
               ("utmi.tx_valid", ours.utmi.tx_valid, luna.utmi.tx_valid),
               ("utmi.op_mode", ours.utmi.op_mode, luna.utmi.op_mode))
    mism = []
    stats = {"starts": 0, "txcmds": 0, "rx_packets": 0, "states": set(), "speeds": set()}
    replies = [[pid_byte(int(p))] for p in HANDSHAKES] + [
        ref_data(int(PID.DATA0), list(range(n))) for n in (0, 1, 8)] + [
        ref_data(int(PID.DATA1), [rng.randrange(256) for _ in range(20)])]

    async def tb(ctx):
        line, line_left, phy = 0b01, 0, []   # phy: queued (dir, nxt, data) cycles
        for cycle in range(n_cycles):
            if line_left == 0:
                line = rng.choice((0b01, 0b01, 0b01, 0b10, 0b00))
                line_left = rng.randrange(2000, 25000)
            line_left -= 1
            # Link transmitting (TXCMD on the pins while DIR is low): accept, then reply.
            data_o, stp = ctx.get(o_pins.data.o), ctx.get(o_pins.stp.o)
            # The PHY does not take DIR while the link transmits (that aborts the packet,
            # ULPI 3.8.2): there luna's shared CRC counts RX bytes into the TX CRC, ours not.
            link_tx = ctx.get(ours.utmi.tx_valid)
            if not phy and link_tx:
                if data_o & 0xC0 == 0x40 and rng.random() < 0.8:
                    stats["txcmds"] += 1
                    phy.append((0, 1, 0))
            elif not phy:
                if data_o & 0xC0 == 0x40 and rng.random() < 0.8:
                    stats["txcmds"] += 1
                    phy.append((0, 1, 0))
                elif stp and rng.random() < 0.7:
                    reply = rng.choice(replies)
                    stats["rx_packets"] += 1
                    phy += [(1, 1, 0)] + [(1, int(rng.random() < 0.8), b) for b in reply]
                    phy += [(1, 0, 0x10 | line)] * rng.randrange(2) + [(0, 0, 0)]
                elif rng.random() < 0.01:
                    phy += [(1, 0, 0)] + [(1, 0, line | 0x0C)] * rng.randrange(1, 4) + [(0, 0, 0)]
                elif rng.random() < 0.002:
                    phy += [(1, int(rng.random() < 0.5), rng.randrange(256))
                            for _ in range(rng.randrange(1, 12))] + [(0, 0, 0)]
            if phy:
                d, n, v = phy.pop(0)
            else:
                # Link owns the bus (TX body or a register write): NXT most cycles.
                d, n, v = 0, int(ctx.get(ours.utmi.busy) and rng.random() < 0.7), 0
            ctx.set(shared.dir, d)
            ctx.set(shared.nxt, n)
            ctx.set(shared.data, v)
            # CPU side of the engine.
            ctx.set(oc.xfer.start, 0)
            ctx.set(oc.bus_reset, rng.random() < 0.00005)
            if ctx.get(oc.status.idle) and rng.random() < 0.02:
                ctx.set(oc.xfer.type, rng.randrange(3))
                ctx.set(oc.xfer.dev_addr, rng.randrange(128))
                ctx.set(oc.xfer.ep_addr, rng.randrange(16))
                ctx.set(oc.xfer.data_pid, rng.randrange(2))
                ctx.set(oc.xfer.start, 1)
                stats["starts"] += 1
            ctx.set(oc.txs.valid, rng.random() < 0.05)
            ctx.set(oc.txs.payload, rng.randrange(256))
            ctx.set(oc.rxs.ready, rng.random() < 0.9)
            ctx.set(oc.tx_flush, rng.random() < 0.001)
            for name, a, b in compare:
                if ctx.get(a) != ctx.get(b):
                    mism.append((cycle, name))
            if ctx.get(lc.rxs.valid) and ctx.get(oc.rxs.payload) != ctx.get(lc.rxs.payload):
                mism.append((cycle, "rxs.payload"))
            stats["speeds"].add(str(ctx.get(oc.status.detected_speed)))
            stats["states"].add((int(ctx.get(oc.status.reset_active)),
                                 str(ctx.get(oc.status.detected_speed)),
                                 str(ctx.get(oc.status.response))))
            await ctx.tick("usb")
    run(m, tb, BoardPlatform())
    stats["states"] = len(stats["states"])
    checks.check(f"lockstep: whole GUH engine on usbcore == the same GUH source on luna "
                 f"({n_cycles} cycles, ULPI PHY stimulus, random transfers)", not mism,
                 f"{len(mism)} mismatches, first {mism[:4]}")
    checks.check("lockstep engine: the stimulus reached transfers, TXCMDs and replies "
                 "(a stuck engine would compare equal)",
                 stats["starts"] > 0 and stats["txcmds"] > 0 and stats["rx_packets"] > 0,
                 f"{stats}")
    checks.note(f"exercised: {stats} (states = distinct reset/speed/response tuples)")


# ---------------------------------------------------------------- main

SECTIONS = ("crc", "codec", "reject", "negative", "lockstep", "sie")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=612)
    parser.add_argument("--only", nargs="*", choices=SECTIONS, default=SECTIONS)
    parser.add_argument("--ulpi-cycles", type=int, default=40000)
    parser.add_argument("--sie-cycles", type=int, default=120000)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    checks = Checks(emit)
    emit(f"usbcore vs luna and a Python reference (seed {args.seed})")
    if "crc" in args.only:
        section_crc(checks, rng)
    if "codec" in args.only:
        section_codec(checks, rng)
    if "reject" in args.only:
        section_reject(checks, rng)
    if "negative" in args.only:
        section_negative(checks, rng)
    if "lockstep" in args.only:
        section_lockstep_rx(checks, rng, 0)
        section_lockstep_rx(checks, rng, 1)
        section_lockstep_tx(checks, rng)
        section_lockstep_timer(checks, rng)
        section_lockstep_ulpi(checks, rng, args.ulpi_cycles)
    if "sie" in args.only:
        section_sie(checks, rng, args.sie_cycles)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
