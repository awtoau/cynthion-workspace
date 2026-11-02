#!/usr/bin/env python3
#
# The USB data path (gateware/soc/datapath/), driven by a PC model, an RV model and a phone.
# SPDX-License-Identifier: BSD-3-Clause

"""
Stage 1 of the data path (#580): blocks out to a phone and back, payload never via the CPU.

    ./scripts/soc/datapath_sim.py            # every group
    ./scripts/soc/datapath_sim.py out nak    # some groups
    ./scripts/soc/datapath_sim.py --soak     # adds the long sweeps

Groups (model engine: DataPath against a Python UsbPort engine port + phone, one clock):
- out:      byte-exact OUT blocks, 1 B .. 64 KiB, packet-boundary sizes, ZLP flag, toggles.
- in:       byte-exact IN blocks, segments (MORE), short packet, ZLP end, lost-ACK repeat.
- nak:      NAK retry (OUT re-sent from the ring), NAK limit, NAK_LIMIT reset value.
- errors:   error retry and stop, phone STALL mid-block, framing survives both.
- header:   descriptor fields, bad magic, length > MAX, rejected codec, CTL byte port.
- pressure: PC reader stalled (IN FIFO full: no IN tokens), phone NAKing (OUT ring full).
- mutation: the OUT ring with rewind disconnected; the nak group's check must FAIL.
Group (real engine: DataPath + UsbPort(engine_port) + SIE + ULPI PHY model + LUNA device):
- engine:   enumerate by CPU pump, then OUT/IN blocks byte-exact through the SIE.

The RV model (`Rv`) is the reference for `firmware/cynthion-soc/src/datapath.rs`.
"""

import argparse
import random
import subprocess
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("gateware", "gateware/soc", "gateware/probes", "scripts"):
    sys.path.insert(0, str(ROOT / sub))

import _paths  # noqa: F401,E402
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import Elaboratable, Module  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import datapath.core as dp_core  # noqa: E402
from datapath.core import DataPath, REG  # noqa: E402
from datapath.header import (  # noqa: E402
    DIR_IN, DIR_OUT, HEADER_BYTES, MAX_LEN, ST_BAD_HEADER, ST_ERROR, ST_NAK_LIMIT, ST_OK,
    ST_OVERRUN, ST_REJECTED, ST_STALL, TYPE_BULK, TYPE_ISO, Header, in_request, out_block)
from datapath.ring import RewindFifo  # noqa: E402
from datapath.sequencer import (  # noqa: E402
    NAK_LIMIT_DEFAULT, R_ABORT, R_ERROR, R_NAK_LIMIT, R_OVERRUN, R_SEG, R_SHORT, R_STALL)

SYNC_PERIOD = 15e-9
RESP_ACK, RESP_NAK, RESP_STALL, RESP_TIMEOUT = 1, 2, 3, 4
PID = {0: 0xC3, 1: 0x4B}
CTRL_POP, CTRL_ARM, CTRL_SEND, CTRL_ABORT, CTRL_FLUSH = 1, 2, 4, 8, 16
EP_DIR_IN, EP_TOGGLE, EP_ZLP, EP_DISCARD = 0x10, 0x20, 0x40, 0x80
ST_DESC, ST_SEQ_BUSY, ST_TX_BUSY = 1, 2, 4

# Model engine: cycles from `eng_start` to `eng_done`. The real one is ~1-2k usb cycles
# for a 512 B packet; the sequencer must not depend on the number, so it is small here.
ENGINE_LATENCY = 12

# RV poll bound per wait: the longest wait is one 64 KiB block = 128 packets x
# (512 push + latency + drain) ~ 70k cycles at one byte per cycle; 1.25 x, in polls of 2.
RV_POLLS = 55_000


class Csr:
    """Byte CSR access: strobe, sample, settle tick (as usb_host_shim_sim.Csr)."""

    def __init__(self, ctx, bus):
        self.ctx, self.bus = ctx, bus

    async def read(self, addr):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.r_stb, 1)
        await c.tick()
        c.set(self.bus.r_stb, 0)
        value = c.get(self.bus.r_data)
        await c.tick()
        return value

    async def write(self, addr, value):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.w_data, value)
        c.set(self.bus.w_stb, 1)
        await c.tick()
        c.set(self.bus.w_stb, 0)
        await c.tick()


# ---------------------------------------------------------------------------
# phone model: one Python object per endpoint pair, scripted per transaction
# ---------------------------------------------------------------------------

class Phone:
    """The far device. `script[(ep, dir)]` is a deque of overrides, one per transaction:
    None (normal), "NAK", "STALL", "TIMEOUT", "DUP" (IN: repeat the last packet, old PID)."""

    def __init__(self, mps=512):
        self.mps = mps
        self.out = {}            # ep -> bytearray received
        self.out_toggle = {}
        self.out_packets = {}    # ep -> [len, ...] ACKed data packets as seen on the wire
        self.toggle_errors = 0
        self.in_data = {}        # ep -> bytearray still to send
        self.in_toggle = {}
        self.in_zlp = {}         # ep -> send a ZLP when the data ends on a full packet
        self.in_sent = {}
        self.last_in = {}
        self.script = {}
        self.transactions = 0
        self.in_tokens = 0

    def override(self, ep, d):
        q = self.script.get((ep, d))
        return q.popleft() if q else None

    def handle(self, ep, is_in, toggle, data):
        self.transactions += 1
        o = self.override(ep, DIR_IN if is_in else DIR_OUT)
        if o == "NAK":
            return RESP_NAK, b"", 0
        if o == "STALL":
            return RESP_STALL, b"", 0
        if o == "TIMEOUT":
            return RESP_TIMEOUT, b"", 0
        if not is_in:
            want = self.out_toggle.get(ep, 0)
            if toggle != want:
                self.toggle_errors += 1           # ACK and ignore (USB 2.0 8.6.4)
                return RESP_ACK, b"", 0
            self.out.setdefault(ep, bytearray()).extend(data)
            self.out_packets.setdefault(ep, []).append(len(data))
            self.out_toggle[ep] = want ^ 1
            return RESP_ACK, b"", 0
        self.in_tokens += 1
        if o == "DUP" and ep in self.last_in:
            pkt, pid = self.last_in[ep]
            return RESP_ACK, pkt, pid
        pending = self.in_data.get(ep, bytearray())
        if not pending and not self.in_zlp.get(ep):
            return RESP_NAK, b"", 0
        pkt = bytes(pending[:self.mps])
        del pending[:self.mps]
        if not pkt or (len(pkt) < self.mps):
            self.in_zlp[ep] = False
        t = self.in_toggle.get(ep, 0)
        if toggle != t:
            self.toggle_errors += 1
        self.in_toggle[ep] = t ^ 1
        self.last_in[ep] = (pkt, PID[t])
        self.in_sent[ep] = self.in_sent.get(ep, 0) + len(pkt)
        return RESP_ACK, pkt, PID[t]


def engine_model(dut, phone, stats, tx_gap=0.0, seed=1):
    """UsbPort's engine port in Python: stage TX bytes, answer `eng_start` after a latency."""
    rng = random.Random(seed)

    async def process(ctx):
        staged, rxq = [], deque()
        pending = None                      # (cycle due, ep word)
        cycle = 0
        while True:
            ready = 0 if (tx_gap and rng.random() < tx_gap) else 1
            ctx.set(dut.eng_tx_ready, ready)
            ctx.set(dut.eng_rx_valid, 1 if rxq else 0)
            ctx.set(dut.eng_rx_data, rxq[0] if rxq else 0)
            done = pending is not None and pending[0] == cycle
            ctx.set(dut.eng_done, 1 if done else 0)
            if done:
                epw = pending[1]
                resp, pkt, pid = phone.handle(epw & 0xF, ((epw >> 4) & 3) == 1,
                                              (epw >> 6) & 1, bytes(staged))
                if ((epw >> 4) & 3) == 2:
                    stats["out_wire"].append(len(staged))
                staged.clear()
                rxq.extend(pkt)
                ctx.set(dut.eng_resp, resp)
                ctx.set(dut.eng_len, len(pkt))
                ctx.set(dut.eng_pid, pid)
                pending = None
            sel = ctx.get(dut.eng_sel)
            if ctx.get(dut.eng_tx_valid) and ready:
                staged.append(ctx.get(dut.eng_tx_data))
            if rxq and ctx.get(dut.eng_rx_ready) and sel and not done:
                rxq.popleft()
            if sel and ctx.get(dut.eng_start):
                if pending is not None:
                    stats["violations"].append(f"start while busy at {cycle}")
                if rxq:
                    stats["violations"].append(f"start with {len(rxq)} RX bytes undrained")
                pending = (cycle + ENGINE_LATENCY, ctx.get(dut.eng_ep))
                stats["starts"] += 1
                if (ctx.get(dut.eng_ep) >> 4) & 3 == 1:
                    stats["in_starts"].append(cycle)
            await ctx.tick()
            cycle += 1
    return process


# ---------------------------------------------------------------------------
# PC model: writer on pc_rx, reader on pc_tx
# ---------------------------------------------------------------------------

def pc_writer(dut, stream, state, gap=0.0, seed=2):
    """Feed `stream` (list of (bytes, ends_transfer)) into pc_rx; `last` on each chunk's end."""
    rng = random.Random(seed)

    async def process(ctx):
        for chunk, ends in stream:
            i = 0
            while i < len(chunk):
                valid = not (gap and rng.random() < gap)
                ctx.set(dut.pc_rx.valid, int(valid))
                ctx.set(dut.pc_rx.data, chunk[i])
                ctx.set(dut.pc_rx.last, int(ends and i == len(chunk) - 1))
                if not ctx.get(dut.pc_rx.ready):
                    state["rx_stalls"] += 1
                take = valid and ctx.get(dut.pc_rx.ready)
                await ctx.tick()
                if take:
                    i += 1
            ctx.set(dut.pc_rx.valid, 0)
        state["written"] = True
    return process


def pc_reader(dut, blocks, state, stall=None, seed=3):
    """Collect blocks from pc_tx. `stall(cycle)` -> True holds `ready` low that cycle."""
    async def process(ctx):
        cur, cycle, payload_bytes = bytearray(), 0, 0
        while True:
            ready = 0 if (stall and stall(cycle)) else 1
            ctx.set(dut.pc_tx.ready, ready)
            if ready and ctx.get(dut.pc_tx.valid):
                cur.append(ctx.get(dut.pc_tx.data))
                if len(cur) > HEADER_BYTES:
                    payload_bytes += 1
                if ctx.get(dut.pc_tx.last):
                    blocks.append(bytes(cur))
                    cur = bytearray()
            state["outstanding_peak"] = max(state.get("outstanding_peak", 0),
                                            state["phone_sent"]() - payload_bytes)
            await ctx.tick()
            cycle += 1
    return process


def split_result(raw):
    hdr = Header.unpack(raw[:HEADER_BYTES])
    return hdr, raw[HEADER_BYTES:]


# ---------------------------------------------------------------------------
# RV model: the firmware's per-block sequence (reference for datapath.rs)
# ---------------------------------------------------------------------------

class Rv:
    """Control only: pop, arm, wait, result, send. Never touches payload bytes."""

    def __init__(self, csr, *, mps=512, nak_limit=None, log=None):
        self.csr, self.mps, self.nak_limit = csr, mps, nak_limit
        self.toggles = {}
        self.log = log if log is not None else []
        self.descs = []

    async def wait(self, test, what):
        for _ in range(RV_POLLS):
            status = await self.csr.read(REG["STATUS"])
            if test(status):
                return status
        raise TimeoutError(f"rv: {what} not within {RV_POLLS} polls")

    async def put(self, base, value, n):
        for i in range(n):
            await self.csr.write(base + i, (value >> (8 * i)) & 0xFF)

    async def get(self, base, n):
        v = 0
        for i in range(n):
            v |= (await self.csr.read(base + i)) << (8 * i)
        return v

    async def result(self, flags, tag, aux, status, length):
        await self.wait(lambda s: not s & ST_TX_BUSY, "builder idle")
        for reg, value in (("H_FLAGS", flags), ("H_TAG", tag), ("H_AUX", aux),
                           ("H_STATUS", status)):
            await self.csr.write(REG[reg], value)
        await self.put(REG["H_LEN0"], length, 3)
        await self.csr.write(REG["CTRL"], CTRL_SEND)

    async def run(self, ep, ep_bits, length):
        """Arm one descriptor and wait for it. Returns (resp, rflags, moved, naks)."""
        c = self.csr
        await c.write(REG["ADDR"], 1)
        await c.write(REG["EP"], ep | ep_bits)
        await self.put(REG["MPS_LO"], self.mps, 2)
        await self.put(REG["LEN0"], length, 3)
        await c.write(REG["EVENTS"], 0x3)
        await c.write(REG["CTRL"], CTRL_ARM)
        await self.wait(lambda s: not s & ST_SEQ_BUSY, "descriptor done")
        resp = await c.read(REG["R_RESP"])
        rflags = await c.read(REG["R_FLAGS"])
        moved = await self.get(REG["R_MOVED0"], 3)
        naks = await self.get(REG["R_NAKS_LO"], 2)
        return resp, rflags, moved, naks

    @staticmethod
    def status_of(rflags):
        if rflags & R_STALL:
            return ST_STALL
        if rflags & R_NAK_LIMIT:
            return ST_NAK_LIMIT
        if rflags & (R_ERROR | R_ABORT):
            return ST_ERROR
        if rflags & R_OVERRUN:
            return ST_OVERRUN
        return ST_OK

    async def service(self, count):
        """Handle `count` descriptors."""
        c = self.csr
        if self.nak_limit is not None:
            await self.put(REG["NAK_LO"], self.nak_limit, 2)
        for _ in range(count):
            await self.wait(lambda s: s & ST_DESC, "descriptor")
            flags = await c.read(REG["D_FLAGS"])
            tag = await c.read(REG["D_TAG"])
            aux = await c.read(REG["D_AUX"])
            err = await c.read(REG["D_ERR"])
            length = await self.get(REG["D_LEN0"], 3)
            await c.write(REG["CTRL"], CTRL_POP)
            self.descs.append((flags, tag, aux, err, length))
            ep, d, typ, zlp = flags & 0xF, (flags >> 4) & 1, (flags >> 5) & 3, flags >> 7
            if err:
                await self.result(flags, tag, aux & 0xF, ST_BAD_HEADER, 0)
                continue
            if (aux & 0xF) or typ not in (TYPE_BULK, 3):
                if d == DIR_OUT and length:
                    await self.run(ep, EP_DISCARD, length)
                await self.result(flags, tag, aux & 0xF, ST_REJECTED, 0)
                continue
            left = length
            while True:
                t = self.toggles.get((ep, d), 0)
                bits = (EP_DIR_IN if d else 0) | (EP_TOGGLE if t else 0) | (EP_ZLP if zlp else 0)
                resp, rflags, moved, naks = await self.run(ep, bits, left)
                self.toggles[(ep, d)] = (resp >> 3) & 1
                status = self.status_of(rflags)
                self.log.append((flags, tag, resp, rflags, moved, naks))
                short = bool(rflags & R_SHORT)
                more = (d == DIR_IN and status == ST_OK and rflags & R_SEG and not short
                        and moved < left)
                a = (aux & 0xF) | (0x10 if more else 0) | (0x20 if short else 0)
                await self.result(flags, tag, a, status, moved)
                if d == DIR_OUT and status != ST_OK and moved < left:
                    await self.run(ep, EP_DISCARD, left - moved)
                if not more:
                    break
                left -= moved


# ---------------------------------------------------------------------------
# bench runner
# ---------------------------------------------------------------------------

def run_model(stream, n_desc, *, phone=None, mps=512, nak_limit=None, tx_gap=0.0,
              rx_gap=0.0, stall=None, dut=None, extra=None):
    """One simulation: PC writes `stream`, RV services `n_desc` descriptors."""
    dut = dut or DataPath()
    phone = phone or Phone(mps)
    stats = {"starts": 0, "violations": [], "in_starts": [], "out_wire": []}
    blocks, state = [], {"rx_stalls": 0, "written": False}
    state["phone_sent"] = lambda: sum(phone.in_sent.values())
    rv_box = {}

    async def bench(ctx):
        rv = Rv(Csr(ctx, dut.bus), mps=mps, nak_limit=nak_limit)
        rv_box["rv"] = rv
        try:
            await rv.service(n_desc)
        except TimeoutError as exc:
            state["error"] = str(exc)
        if extra:
            await extra(ctx, rv, state)
        # Let the builder finish the last block.
        for _ in range(HEADER_BYTES * 4 + 70_000):
            if not (await rv.csr.read(REG["STATUS"])) & ST_TX_BUSY:
                break
        await ctx.tick().repeat(8)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(SYNC_PERIOD)
    sim.add_testbench(engine_model(dut, phone, stats, tx_gap), background=True)
    sim.add_testbench(pc_writer(dut, stream, state, rx_gap), background=True)
    sim.add_testbench(pc_reader(dut, blocks, state, stall), background=True)
    sim.add_testbench(bench)
    sim.run()
    return dict(blocks=blocks, phone=phone, stats=stats, state=state, rv=rv_box.get("rv"))


def results_of(blocks):
    return [split_result(b) for b in blocks]


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

OUT_SIZES_ONCE = [1, 7, 8, 63, 64, 65, 511, 512, 513, 1023, 1024, 1025, 2047, 2048, 2049,
                  4096, 65536]
OUT_SIZES_SOAK = OUT_SIZES_ONCE + [3, 100, 1536, 3000, 8191, 8192, 8193, 30000, 65535]


def payload(n, seed):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(n))


def group_out(checks, soak=False):
    checks._emit("  out: PC -> phone, byte exact")
    sizes = OUT_SIZES_SOAK if soak else OUT_SIZES_ONCE
    for mps in (512, 64):
        for n in sizes:
            if mps == 64 and n > 4096:
                continue
            data = payload(n, n)
            r = run_model([(out_block(data, tag=n & 0xFF), True)], 1, mps=mps,
                          tx_gap=0.1, rx_gap=0.1)
            got = bytes(r["phone"].out.get(1, b""))
            res = results_of(r["blocks"])
            hdr = res[0][0] if res else None
            pk = r["phone"].out_packets.get(1, [])
            want_pk = [mps] * (n // mps) + ([n % mps] if n % mps else [])
            checks.check(
                f"out mps {mps} {n:>5} B: phone got every byte in order, packets of MPS then "
                f"the rest, one result (ok, length {n}, tag echoed)",
                got == data and pk == want_pk and len(res) == 1 and hdr.status == ST_OK
                and hdr.length == n and hdr.tag == (n & 0xFF) and hdr.dir == DIR_OUT
                and not res[0][1] and not r["stats"]["violations"]
                and r["phone"].toggle_errors == 0 and "error" not in r["state"],
                f"got {len(got)} B equal={got == data} packets {pk[:6]}.. want {want_pk[:6]}.. "
                f"res {res[:1]} viol {r['stats']['violations'][:3]} "
                f"toggle_err {r['phone'].toggle_errors} {r['state'].get('error')}")
    # ZLP flag: a multiple of MPS ends with a ZLP only when asked; length 0 is one ZLP.
    for n, zlp in ((1024, True), (1024, False), (1000, True), (0, False)):
        r = run_model([(out_block(payload(n, 5), zlp=zlp), True)], 1)
        wire = r["stats"]["out_wire"]
        want = [512] * (n // 512) + ([n % 512] if n % 512 else [])
        if n == 0 or (zlp and n % 512 == 0):
            want = want + [0]
        checks.check(f"out {n} B zlp={int(zlp)}: wire packets {want}",
                     wire == want and bytes(r["phone"].out.get(1, b"")) == payload(n, 5),
                     f"wire {wire}")
    # Toggle continuity across blocks: the RV carries R_RESP.toggle into the next arm.
    data = [payload(n, 40 + n) for n in (100, 512, 3)]
    r = run_model([(out_block(d, tag=i), True) for i, d in enumerate(data)], 3)
    checks.check("out: three blocks back to back keep the data toggle across blocks",
                 bytes(r["phone"].out.get(1, b"")) == b"".join(data)
                 and r["phone"].toggle_errors == 0 and [h.tag for h, _ in results_of(r["blocks"])]
                 == [0, 1, 2],
                 f"toggle errors {r['phone'].toggle_errors}")
    # A block split across several USB transfers: LENGTH, not `last`, frames it.
    d = payload(3000, 9)
    r = run_model([(out_block(d)[:700], True), (out_block(d)[700:2000], True),
                   (out_block(d)[2000:], True)], 1)
    checks.check("out: one block sent as three USB transfers arrives whole (LENGTH frames it)",
                 bytes(r["phone"].out.get(1, b"")) == d)


def group_in(checks, soak=False):
    checks._emit("  in: phone -> PC, byte exact")
    sizes = [1, 63, 64, 511, 512, 513, 1024, 2047, 2048, 2049, 4096, 65536]
    if soak:
        sizes += [2, 1000, 1535, 1536, 6000, 8192, 30001]
    for n in sizes:
        phone = Phone(512)
        data = payload(n, 100 + n)
        phone.in_data[1] = bytearray(data)
        phone.in_zlp[1] = n % 512 == 0
        r = run_model([(in_request(65536, tag=7), True)], 1, phone=phone, nak_limit=4,
                      stall=lambda c: c % 7 == 0)
        res = results_of(r["blocks"])
        got = b"".join(p for _, p in res)
        hdrs = [h for h, _ in res]
        segs = -(-n // 2048) if n else 1
        full = n == 65536                    # the request is met exactly: no short packet read
        if n % 2048 == 0 and not full:
            segs += 1                        # the ending short packet (ZLP) is its own segment
        ok = (got == data and hdrs and all(h.more for h in hdrs[:-1]) and not hdrs[-1].more
              and hdrs[-1].short != full and all(h.status == ST_OK and h.tag == 7 for h in hdrs)
              and all(h.length == len(p) for h, p in res) and len(hdrs) == segs
              and not r["stats"]["violations"])
        checks.check(
            f"in {n:>5} B (request 64 KiB): PC got every byte in order as {segs} block(s) of "
            f"<= 2048, MORE on all but the last, SHORT on the last unless the request was met",
            ok, f"got {len(got)} B equal={got == data} hdrs "
                f"{[(h.length, h.more, h.short, h.status) for h in hdrs][:6]} "
                f"viol {r['stats']['violations'][:3]} {r['state'].get('error')}")
    # Request shorter than what the phone has: stops at LENGTH; the rest stays on the phone.
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(5000, 3))
    r = run_model([(in_request(1024, tag=1), True), (in_request(1024, tag=2), True)], 2,
                  phone=phone)
    got = b"".join(p for _, p in results_of(r["blocks"]))
    checks.check("in: two 1024 B requests return the first 2048 phone bytes, not more",
                 got == payload(5000, 3)[:2048] and len(phone.in_data[1]) == 5000 - 2048,
                 f"got {len(got)}")
    # Lost ACK: the phone repeats a packet with the old PID; it must be dropped once.
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(1500, 4))
    phone.script[(1, DIR_IN)] = deque([None, "DUP", None, None])
    r = run_model([(in_request(1500), True)], 1, phone=phone, nak_limit=4)
    got = b"".join(p for _, p in results_of(r["blocks"]))
    checks.check("in: a repeated packet (old DATA PID, lost ACK) is dropped, data exact",
                 got == payload(1500, 4), f"got {len(got)}")
    # Toggle continuity on IN across two blocks.
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(700, 6))
    r = run_model([(in_request(512), True), (in_request(512), True)], 2, phone=phone,
                  nak_limit=3)
    got = b"".join(p for _, p in results_of(r["blocks"]))
    checks.check("in: the IN toggle carries across blocks (no toggle errors at the phone)",
                 got == payload(700, 6) and phone.toggle_errors == 0,
                 f"got {len(got)} terr {phone.toggle_errors}")


def group_nak(checks, soak=False, rewind_broken=False):
    checks._emit("  nak: retry, limit")
    phone = Phone(512)
    phone.script[(1, DIR_OUT)] = deque(["NAK", "NAK", None, "NAK", None, "NAK"] + [None] * 8)
    data = payload(2000, 77)
    r = run_model([(out_block(data), True)], 1, phone=phone)
    log = r["rv"].log if r["rv"] else []
    ok = checks.check(
        "nak: OUT NAKed 4 times is re-sent from the ring; phone data exact; R_NAKS = 4",
        bytes(phone.out.get(1, b"")) == data and log and log[0][5] == 4
        and Rv.status_of(log[0][3]) == ST_OK,
        f"got {len(phone.out.get(1, b''))} B equal={bytes(phone.out.get(1, b'')) == data} "
        f"log {log[:1]}")
    if rewind_broken:
        return ok
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(600, 78))
    phone.script[(1, DIR_IN)] = deque(["NAK"] * 3 + [None, "NAK", None])
    r = run_model([(in_request(600), True)], 1, phone=phone, nak_limit=10)
    got = b"".join(p for _, p in results_of(r["blocks"]))
    checks.check("nak: IN NAKed 4 times then answered; data exact; R_NAKS = 4",
                 got == payload(600, 78) and r["rv"].log[0][5] == 4, f"{r['rv'].log[:1]}")
    # Limit: a phone that never answers. OUT keeps its data for the RV to discard.
    phone = Phone(512)
    phone.script[(1, DIR_OUT)] = deque([None] + ["NAK"] * 50)
    r = run_model([(out_block(payload(1500, 1), tag=3), True),
                   (out_block(b"after", tag=4), True)], 2, phone=phone, nak_limit=5)
    res = results_of(r["blocks"])
    checks.check(
        "nak: limit 5 stops the block with NAK_LIMIT after 512 B, R_NAKS = 5; the rest is "
        "discarded and the next block parses",
        len(res) == 2 and res[0][0].status == ST_NAK_LIMIT and res[0][0].length == 512
        and r["rv"].log[0][5] == 5 and res[1][0].tag == 4,
        f"{[(h.status, h.length, h.tag) for h, _ in res]} {r['rv'].log[:2]}")
    # IN with nothing to send: the IN FIFO stays empty, result NAK_LIMIT with 0 bytes.
    phone = Phone(512)
    r = run_model([(in_request(512, tag=9), True)], 1, phone=phone, nak_limit=7)
    res = results_of(r["blocks"])
    checks.check("nak: IN from a silent phone returns NAK_LIMIT, 0 bytes, after 7 NAKs",
                 len(res) == 1 and res[0][0].status == ST_NAK_LIMIT and res[0][0].length == 0
                 and not res[0][1] and phone.in_tokens == 7, f"{res} tokens {phone.in_tokens}")

    async def readback(ctx, rv, state):
        state["nak_reset"] = await rv.get(REG["NAK_LO"], 2)
    r = run_model([], 0, extra=readback)
    checks.check(f"nak: NAK_LIMIT resets to {NAK_LIMIT_DEFAULT}",
                 r["state"].get("nak_reset") == NAK_LIMIT_DEFAULT, f"{r['state'].get('nak_reset')}")
    return ok


def group_errors(checks, soak=False):
    checks._emit("  errors: retry, stop, stall")
    phone = Phone(512)
    phone.script[(1, DIR_OUT)] = deque(["TIMEOUT", "TIMEOUT", None] + [None] * 4)
    data = payload(1200, 11)
    r = run_model([(out_block(data), True)], 1, phone=phone)
    checks.check("errors: two TIMEOUTs in a row are retried; data exact, status ok",
                 bytes(phone.out.get(1, b"")) == data
                 and results_of(r["blocks"])[0][0].status == ST_OK)
    phone = Phone(512)
    phone.script[(1, DIR_OUT)] = deque([None, "TIMEOUT", "TIMEOUT", "TIMEOUT"])
    r = run_model([(out_block(data, tag=1), True), (out_block(b"xyz", tag=2), True)], 2,
                  phone=phone)
    res = results_of(r["blocks"])
    checks.check("errors: three in a row stop the block with ERROR after 512 B; next block ok",
                 len(res) == 2 and res[0][0].status == ST_ERROR and res[0][0].length == 512
                 and res[1][0].status == ST_OK and bytes(phone.out[1]) == data[:512] + b"xyz",
                 f"{[(h.status, h.length) for h, _ in res]}")
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(900, 12))
    phone.script[(1, DIR_IN)] = deque([None, "TIMEOUT", "TIMEOUT", "TIMEOUT"])
    r = run_model([(in_request(900, tag=5), True)], 1, phone=phone)
    res = results_of(r["blocks"])
    checks.check("errors: IN stops with ERROR after 3 errors, the 512 B already moved are "
                 "returned", len(res) == 1 and res[0][0].status == ST_ERROR
                 and res[0][1] == payload(900, 12)[:512], f"{[(h.status, h.length) for h, _ in res]}")
    # Phone-side STALL in the middle of an OUT block.
    phone = Phone(512)
    phone.script[(1, DIR_OUT)] = deque([None, None, "STALL"])
    d1 = payload(3000, 13)
    r = run_model([(out_block(d1, tag=1), True), (out_block(b"next", tag=2), True)], 2,
                  phone=phone)
    res = results_of(r["blocks"])
    checks.check("errors: phone STALL on the 3rd packet: STALL after 1024 B, rest discarded, "
                 "next block delivered", len(res) == 2 and res[0][0].status == ST_STALL
                 and res[0][0].length == 1024 and bytes(phone.out[1]) == d1[:1024] + b"next",
                 f"{[(h.status, h.length, h.tag) for h, _ in res]}")
    phone = Phone(512)
    phone.in_data[1] = bytearray(payload(2000, 14))
    phone.script[(1, DIR_IN)] = deque([None, "STALL"])
    r = run_model([(in_request(2000, tag=6), True)], 1, phone=phone)
    res = results_of(r["blocks"])
    checks.check("errors: phone STALL on IN returns STALL with the 512 B before it",
                 len(res) == 1 and res[0][0].status == ST_STALL
                 and res[0][1] == payload(2000, 14)[:512])


def group_header(checks, soak=False):
    checks._emit("  header: descriptors, framing, control port")
    blocks = [out_block(b"\x01\x02\x03", ep=3, tag=0x5A, zlp=True),
              in_request(300, ep=2, tag=0xA5),
              Header(ep=5, dir=DIR_OUT, type=3, tag=0x11, length=4).pack() + b"abcd"]
    r = run_model([(b, True) for b in blocks], 3, nak_limit=3)
    want =[(0x83 | 0x40, 0x5A, 0, 0, 3), (0x12 | 0x40, 0xA5, 0, 0, 300),
            (0x05 | 0x60, 0x11, 0, 0, 4)]
    checks.check("header: D_FLAGS / D_TAG / D_AUX / D_ERR / D_LEN match the headers sent",
                 r["rv"].descs == want, f"{r['rv'].descs} want {want}")
    # Bad magic: dropped through `last`, one error descriptor, then the next block works.
    bad = b"\x00" + bytes(range(40))
    r = run_model([(bad, True), (out_block(b"ok", tag=8), True)], 2)
    res = results_of(r["blocks"])
    checks.check("header: bad MAGIC drops the transfer, one BAD_HEADER result, next block ok",
                 [d[3] for d in r["rv"].descs] == [1, 0]
                 and [h.status for h, _ in res] == [ST_BAD_HEADER, ST_OK]
                 and bytes(r["phone"].out[1]) == b"ok", f"{r['rv'].descs}")
    over = Header(dir=DIR_OUT, tag=3, length=MAX_LEN + 1).pack() + b"\x00" * 30
    r = run_model([(over, True), (out_block(b"ok2", tag=9), True)], 2)
    checks.check("header: LENGTH > 64 KiB is D_ERR.length, the transfer is dropped, next ok",
                 [d[3] for d in r["rv"].descs] == [2, 0] and bytes(r["phone"].out[1]) == b"ok2",
                 f"{r['rv'].descs}")
    rej = Header(dir=DIR_OUT, codec=1, tag=4, length=600).pack() + payload(600, 1)
    iso = Header(dir=DIR_OUT, type=TYPE_ISO, tag=5, length=10).pack() + b"0123456789"
    r = run_model([(rej, True), (iso, True), (out_block(b"ok3", tag=6), True)], 3)
    res = results_of(r["blocks"])
    checks.check("header: codec != 0 and iso are REJECTED, their payload discarded unsent",
                 [h.status for h, _ in res] == [ST_REJECTED, ST_REJECTED, ST_OK]
                 and bytes(r["phone"].out[1]) == b"ok3", f"{[h.status for h, _ in res]}")

    # Control endpoint byte port.
    async def ctl(ctx, rv, state):
        c = rv.csr
        seen = []
        for _ in range(4):
            st = await c.read(REG["CTL_STATUS"])
            if st & 1:
                seen.append((await c.read(REG["CTL_RX"]), bool(st & 2)))
        state["ctl_seen"] = seen
        await c.write(REG["CTL_TX"], 0x41)
        await c.write(REG["CTL_TX_LAST"], 0x42)
        await ctx.tick().repeat(4)
    dut = DataPath()
    sent = [(0x10, 0), (0x20, 1)]
    tx_seen = []

    async def ctl_feed(ctx):
        for b, last in sent:
            ctx.set(dut.ctl_rx.valid, 1)
            ctx.set(dut.ctl_rx.data, b)
            ctx.set(dut.ctl_rx.last, last)
            while True:
                await ctx.tick()
                if ctx.get(dut.ctl_rx.ready):
                    break
            ctx.set(dut.ctl_rx.valid, 0)
        while True:
            ctx.set(dut.ctl_tx.ready, 1)
            if ctx.get(dut.ctl_tx.valid):
                tx_seen.append((ctx.get(dut.ctl_tx.data), ctx.get(dut.ctl_tx.last)))
            await ctx.tick()

    async def bench(ctx):
        rv = Rv(Csr(ctx, dut.bus))
        await ctx.tick().repeat(3)
        await ctl(ctx, rv, state)
    state = {}
    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(SYNC_PERIOD)
    sim.add_testbench(ctl_feed, background=True)
    sim.add_testbench(bench)
    sim.run()
    checks.check("header: CTL_RX pops the control OUT bytes with their `last`; CTL_TX/_LAST "
                 "push to the control IN stream",
                 state.get("ctl_seen") == [(0x10, False), (0x20, True)]
                 and tx_seen == [(0x41, 0), (0x42, 1)], f"{state.get('ctl_seen')} {tx_seen}")


def group_pressure(checks, soak=False):
    checks._emit("  pressure: back-pressure both ways")
    phone = Phone(512)
    data = payload(12000, 21)
    phone.in_data[1] = bytearray(data)
    got = [0]
    r = run_model([(in_request(12000), True)], 1, phone=phone, nak_limit=4,
                  stall=lambda c: (c // 4000) % 2 == 0)
    res = results_of(r["blocks"])
    blob = b"".join(p for _, p in res)
    peak = r["state"].get("outstanding_peak", 0)
    checks.check("pressure: PC reader stalled in 4000-cycle spans: data exact, phone-sent "
                 "minus PC-received never above the 2048 B IN FIFO + one packet in flight",
                 blob == data and peak <= 2048 + 512, f"got {len(blob)} peak {peak}",
                 measurement=f"peak outstanding {peak} B")
    phone = Phone(512)
    # Each NAKed OUT re-pushes its packet (~530 cycles), so 30 NAKs hold the ring ~16k cycles.
    phone.script[(1, DIR_OUT)] = deque(["NAK"] * 30)
    data = payload(8000, 22)
    r = run_model([(out_block(data), True)], 1, phone=phone, nak_limit=400)
    checks.check("pressure: phone NAKing 30 times: OUT ring fills, pc_rx.ready drops, data "
                 "exact afterwards", bytes(phone.out.get(1, b"")) == data
                 and r["state"]["rx_stalls"] > 1000,
                 f"stalls {r['state']['rx_stalls']}",
                 measurement=f"pc_rx not ready for {r['state']['rx_stalls']} cycles")


class NoRewindFifo(Elaboratable):
    """Mutation: RewindFifo with `rewind` disconnected."""

    def __init__(self, depth=2048):
        self.inner = RewindFifo(depth)
        for name in self.inner.signature.members:
            setattr(self, name, getattr(self.inner, name))
        self.rewind = __import__("amaranth").Signal(name="rewind_dropped")

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner = self.inner
        return m


def group_mutation(checks, soak=False):
    checks._emit("  mutation: a broken ring must be caught")
    real = dp_core.RewindFifo
    dp_core.RewindFifo = NoRewindFifo
    try:
        sub = Checks(lambda line: None)
        caught = not group_nak(sub, rewind_broken=True)
    finally:
        dp_core.RewindFifo = real
    checks.check("mutation: with the ring's rewind disconnected the NAK OUT check FAILS",
                 caught, "the broken design passed: the check cannot see a lost rewind")


# ---------------------------------------------------------------------------
# engine: the real UsbPort + SIE + PHY model + a LUNA sink/source device
# ---------------------------------------------------------------------------

SINK_EP, SOURCE_EP = 1, 2


def first_diff(a, b):
    return next((i for i in range(min(len(a), len(b))) if a[i] != b[i]),
                None if len(a) == len(b) else min(len(a), len(b)))


class SinkSourceDevice(Elaboratable):
    """LUNA device: EP1 bulk OUT sink (`hold` stops it: the endpoint then NAKs), EP2 bulk
    IN source emitting an up-counter."""

    def __init__(self):
        from luna.gateware.interface.utmi import UTMIInterface
        from amaranth import Signal
        self.utmi = UTMIInterface()
        self.present = Signal(init=1)
        self.hold = Signal()
        self.ep_out = None

    def elaborate(self, platform):
        from amaranth import Signal
        from luna.usb2 import USBDevice, USBStreamInEndpoint, USBStreamOutEndpoint
        from usb_protocol.emitters import DeviceDescriptorCollection
        from usb_protocol.types import USBDirection, USBTransferType
        m = Module()
        d = DeviceDescriptorCollection()
        with d.DeviceDescriptor() as dd:
            dd.idVendor, dd.idProduct = 0x16D0, 0x0F3C
            dd.bNumConfigurations = 1
            dd.bMaxPacketSize0 = 64
        with d.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                for ep, direction in ((SINK_EP, USBDirection.OUT), (SOURCE_EP, USBDirection.IN)):
                    with i.EndpointDescriptor() as e:
                        e.bEndpointAddress = direction.to_endpoint_address(ep)
                        e.bmAttributes = USBTransferType.BULK
                        e.wMaxPacketSize = 512
        m.submodules.usb = usb = USBDevice(bus=self.utmi)
        usb.always_fs, usb.data_clock = False, 60e6
        usb.add_standard_control_endpoint(d)
        self.ep_out = ep_out = USBStreamOutEndpoint(endpoint_number=SINK_EP, max_packet_size=512)
        ep_in = USBStreamInEndpoint(endpoint_number=SOURCE_EP, max_packet_size=512)
        usb.add_endpoint(ep_out)
        usb.add_endpoint(ep_in)
        counter = Signal(8)
        with m.If(ep_in.stream.ready):
            m.d.usb += counter.eq(counter + 1)
        m.d.comb += [ep_out.stream.ready.eq(~self.hold), ep_in.stream.valid.eq(1),
                     ep_in.stream.payload.eq(counter), usb.connect.eq(self.present)]
        return m


class EngineBench(Elaboratable):
    def __init__(self):
        from usb_host_shim_sim import Bench
        self.device = SinkSourceDevice()
        self.host = Bench(phone=self.device, engine_port=True)
        self.port = self.host.port
        self.dp = DataPath()

    def elaborate(self, platform):
        m = Module()
        m.submodules.host, m.submodules.dp = self.host, self.dp
        for name, d, _ in dp_core.ENGINE_MEMBERS:
            src, dst = (self.dp, self.port) if d is __import__("amaranth").lib.wiring.Out \
                else (self.port, self.dp)
            m.d.comb += getattr(dst, name).eq(getattr(src, name))
        return m


ENGINE_SIZES_ONCE = [1, 511, 512, 513, 1500, 4096]
ENGINE_SIZES_SOAK = ENGINE_SIZES_ONCE + [64, 1024, 2048, 2049, 65536]


def group_engine(checks, soak=False):
    checks._emit("  engine: through UsbPort + SIE + PHY model + LUNA device")
    from usb_host_shim_sim import (BoardPlatform, Csr as PortCsr, control, host_up,
                                   REQ_SET_ADDRESS, REQ_SET_CONFIGURATION)
    from usb_host.model import scale_timing_for_simulation
    from peripherals.usb_host import REG_CTRL, CTRL_CLR_RX
    scale_timing_for_simulation()
    global RV_POLLS
    RV_POLLS = 400_000                        # a 64 KiB block through the real SIE (soak)
    sizes = ENGINE_SIZES_SOAK if soak else ENGINE_SIZES_ONCE
    bench = EngineBench()
    dut, port, dev = bench.dp, bench.port, bench.device
    outs = [payload(n, 500 + n) for n in sizes]
    # The source never ends a transfer, so every IN packet is full: 513 reads 1024 (OVERRUN).
    ins = [512, 4096, 513] + ([65536] if soak else [])
    in_want = [[(512, ST_OK)], [(2048, ST_OK), (2048, ST_OK)], [(1024, ST_OVERRUN)]]
    in_want += [[(2048, ST_OK)] * 32] if soak else []
    stream = [(out_block(d, ep=SINK_EP, tag=i), True) for i, d in enumerate(outs)]
    stream += [(in_request(n, ep=SOURCE_EP, tag=0x80 + i), True) for i, n in enumerate(ins)]
    stream += [(out_block(payload(3000, 9), ep=SINK_EP, tag=0x40), True)]
    seen = {"sunk": bytearray(), "nak_hold": None}
    blocks, state = [], {"rx_stalls": 0, "phone_sent": lambda: 0}
    stats = {}

    async def sink_capture(ctx):
        while True:
            _, _, valid, ready, data = await ctx.tick("usb").sample(
                dev.ep_out.stream.valid, dev.ep_out.stream.ready, dev.ep_out.stream.payload)
            if valid and ready:
                seen["sunk"].append(int(data))

    async def bench_tb(ctx):
        pc = PortCsr(ctx, port.bus)
        seen["link"] = await host_up(pc)
        if seen["link"] is None:
            return
        seen["addr"], _ = await control(pc, 0, 0x00, REQ_SET_ADDRESS, 1, 0)
        seen["config"], _ = await control(pc, 1, 0x00, REQ_SET_CONFIGURATION, 1, 0)
        await pc.write(REG_CTRL, CTRL_CLR_RX)
        rv = Rv(Csr(ctx, dut.bus), mps=512, nak_limit=64)
        stats["rv"] = rv
        try:
            await rv.service(len(outs))
            await rv.service(len(ins))
            # Last block: the device holds its sink, so the engine meets real NAKs.
            await rv.put(REG["NAK_LO"], 60000, 2)
            rv.nak_limit = None
            seen["hold_now"] = True
            await rv.service(1)
        except TimeoutError as exc:
            seen["error"] = str(exc)
        for _ in range(20_000):
            if not (await rv.csr.read(REG["STATUS"])) & ST_TX_BUSY:
                break
        # The device's 1023 B OUT buffer drains to its stream at one byte per usb cycle.
        await ctx.tick("usb").repeat(1300)

    seen["wire"] = []

    async def responses(ctx):
        while True:
            await ctx.tick()
            if ctx.get(port.eng_done):
                seen["wire"].append((ctx.get(port.eng_resp), ctx.get(dut.eng_ep) >> 6,
                                     len(seen["sunk"])))

    async def holder(ctx):
        # Hold the sink for 30k usb cycles from the last block's start: its 1023 B
        # buffer fills in ~3k, so the rest of the span is NAKs.
        while not seen.get("hold_now"):
            await ctx.tick("usb")
        ctx.set(dev.hold, 1)
        await ctx.tick("usb").repeat(30_000)
        ctx.set(dev.hold, 0)

    sim = Simulator(Fragment.get(bench, BoardPlatform()))
    from usb_host_shim_sim import SYNC_PERIOD as SP, USB_PERIOD
    sim.add_clock(SP)
    sim.add_clock(USB_PERIOD, domain="usb")
    sim.add_testbench(pc_writer(dut, stream, state), background=True)
    sim.add_testbench(pc_reader(dut, blocks, state), background=True)
    sim.add_testbench(sink_capture, background=True)
    sim.add_testbench(holder, background=True)
    sim.add_testbench(responses, background=True)
    sim.add_testbench(bench_tb)
    sim.run()

    checks.check("engine: host up and the device enumerated (SET_ADDRESS, SET_CONFIGURATION) "
                 "by the CPU pump", seen.get("link") is not None and seen.get("addr")
                 and seen.get("config"), f"{seen.get('link')} {seen.get('addr')} {seen.get('config')}")
    if seen.get("link") is None:
        return
    res = results_of(blocks)
    out_res = [h for h, _ in res if h.dir == DIR_OUT]
    want_sunk = b"".join(outs)
    sunk = bytes(seen["sunk"])
    checks.check(f"engine: OUT blocks {sizes} reach the device byte for byte through the SIE",
                 sunk[:len(want_sunk)] == want_sunk
                 and [h.status for h in out_res[:len(outs)]] == [ST_OK] * len(outs)
                 and [h.length for h in out_res[:len(outs)]] == sizes,
                 f"sunk {len(sunk)} want {len(want_sunk)} res "
                 f"{[(h.status, h.length) for h in out_res][:8]} {seen.get('error')}")
    in_res = [(h, p) for h, p in res if h.dir == DIR_IN]
    data = b"".join(p for _, p in in_res)
    flat = [w for blk in in_want for w in blk]
    want_in = bytes(i & 0xFF for i in range(sum(n for n, _ in flat)))
    checks.check(f"engine: IN requests {ins} return the device's counter byte for byte, "
                 f"as (length, status) {flat[:4]}..",
                 data == want_in and [(h.length, h.status) for h, _ in in_res] == flat,
                 f"got {len(data)} want {len(want_in)} first diff "
                 f"{next((i for i in range(min(len(data), len(want_in))) if data[i] != want_in[i]), None)} "
                 f"{[(h.status, h.length, h.more) for h, _ in in_res][:8]}")
    last = out_res[-1] if out_res else None
    log = stats["rv"].log if "rv" in stats else []
    checks.check("engine: a block into a sink that holds sees real NAKs from the LUNA endpoint, "
                 "then completes byte exact after release",
                 last is not None and last.tag == 0x40 and last.status == ST_OK
                 and sunk[len(want_sunk):] == payload(3000, 9) and log and log[-1][5] > 0,
                 f"last {last} naks {log[-1][5] if log else None} tail {len(sunk) - len(want_sunk)} "
                 f"first diff {first_diff(sunk[len(want_sunk):], payload(3000, 9))} "
                 f"wire {seen['wire'][-14:]}",
                 measurement=f"{log[-1][5] if log else 0} NAKs retried by the sequencer")


# ---------------------------------------------------------------------------
# device: the AUX composite device (console + vendor interface) against a host SIE
# ---------------------------------------------------------------------------

def group_device(checks, soak=False):
    checks._emit("  device: CDC-ACM console + vendor interface 2, enumerated by a host SIE")
    from amaranth import DomainRenamer
    from luna.gateware.interface.utmi import UTMIInterface
    from luna.gateware.usb.devices.acm import USBSerialDevice
    from usb_host.guh.sie import USBSIE
    from usb_host.model import connect_utmi, scale_timing_for_simulation
    from usb_host_sie_sim import (ACK, DATA0, DATA1, IN, OUT, SETUP, setup_packet,
                                  transact, wait_for_bringup)
    from datapath.device import CTL_EP, DATA_EP, DataPathDevice
    scale_timing_for_simulation()

    utmi = UTMIInterface()
    dev = DataPathDevice(bus=utmi, idVendor=0x1d50, idProduct=0x6180, max_packet_size=512)
    host = USBSIE(fifo_depth=512, fullspeed_only=False)

    class Top(Elaboratable):
        def elaborate(self, platform):
            m = Module()
            rename = DomainRenamer({"usb": "sync"})
            m.submodules.host, m.submodules.dev = rename(host), rename(dev)
            connect_utmi(m, host.utmi, utmi)
            return m

    ctrl = host.ctrl
    seen = {"data_rx": [], "ctl_rx": [], "con_rx": []}

    async def capture(ctx):
        for s in (dev.data_rx, dev.ctl_rx, dev.rx):
            ctx.set(s.ready, 1)
        while True:
            await ctx.tick()
            for key, s in (("data_rx", dev.data_rx), ("ctl_rx", dev.ctl_rx), ("con_rx", dev.rx)):
                if ctx.get(s.valid):
                    seen[key].append((ctx.get(s.payload), ctx.get(s.last)))

    async def feed(ctx, s, data):
        for i, b in enumerate(data):
            ctx.set(s.payload, b)
            ctx.set(s.last, int(i == len(data) - 1))
            ctx.set(s.valid, 1)
            await ctx.tick().until(s.ready == 1)
        ctx.set(s.valid, 0)

    async def control_in(ctx, addr, setup, n):
        await transact(ctx, ctrl, type=SETUP, address=addr, endpoint=0, payload=setup)
        got, toggle = [], DATA1
        while len(got) < n:
            data, resp, _, _ = await transact(ctx, ctrl, type=IN, address=addr, endpoint=0,
                                              data_pid=toggle)
            if resp != ACK:
                continue
            got += data
            toggle = DATA0 if toggle == DATA1 else DATA1
            if len(data) < 64:
                break
        await transact(ctx, ctrl, type=OUT, address=addr, endpoint=0, data_pid=DATA1)
        return got

    async def control_nodata(ctx, addr, setup):
        await transact(ctx, ctrl, type=SETUP, address=addr, endpoint=0, payload=setup)
        _, resp, _, _ = await transact(ctx, ctrl, type=IN, address=addr, endpoint=0,
                                       data_pid=DATA1)
        return resp

    async def bulk_in(ctx, addr, ep, toggle):
        for _ in range(20):
            data, resp, _, _ = await transact(ctx, ctrl, type=IN, address=addr, endpoint=ep,
                                              data_pid=toggle)
            if resp == ACK:
                return data
        return None

    async def bench(ctx):
        seen["up"] = await wait_for_bringup(ctx, ctrl)
        seen["config"] = await control_in(ctx, 0, setup_packet(0x80, 6, 0x0200, 0, 255), 255)
        seen["addr"] = await control_nodata(ctx, 0, setup_packet(0x00, 5, 9, 0, 0))
        seen["setconf"] = await control_nodata(ctx, 9, setup_packet(0x00, 9, 1, 0, 0))
        _, seen["out1"], _, _ = await transact(ctx, ctrl, type=OUT, address=9, endpoint=DATA_EP,
                                               payload=list(range(20)))
        _, seen["out2"], _, _ = await transact(ctx, ctrl, type=OUT, address=9, endpoint=CTL_EP,
                                               payload=[7, 8, 9])
        _, seen["out4"], _, _ = await transact(ctx, ctrl, type=OUT, address=9, endpoint=4,
                                               payload=list(b"ls\r"))
        await feed(ctx, dev.data_tx, list(range(100, 110)))
        seen["in1"] = await bulk_in(ctx, 9, DATA_EP, DATA0)
        await feed(ctx, dev.ctl_tx, [0x55, 0xAA])
        seen["in2"] = await bulk_in(ctx, 9, CTL_EP, DATA0)
        await ctx.tick().repeat(200)

    sim = Simulator(Fragment.get(Top(), None))
    sim.add_clock(1 / 60e6)
    sim.add_testbench(capture, background=True)
    sim.add_testbench(bench)
    sim.run()

    want_cfg = dev.create_descriptors().get_descriptor_bytes(2, 0)
    cdc_ref = USBSerialDevice(bus=None, idVendor=0x1d50, idProduct=0x6180, max_packet_size=512)
    cdc_ref._MustUse__silence = True          # descriptors only; never elaborated
    cdc_cfg = cdc_ref.create_descriptors().get_descriptor_bytes(2, 0)
    cfg = bytes(seen["config"])
    checks.check("device: enumerates; the configuration descriptor on the wire is the one built",
                 seen["up"] and cfg == want_cfg, f"{cfg.hex()} want {bytes(want_cfg).hex()}")
    checks.check("device: interfaces 0/1 (CDC-ACM, EP3, EP4) are USBSerialDevice's byte for byte; "
                 "only the interface count and interface 2 are added",
                 cfg[9:len(cdc_cfg)] == bytes(cdc_cfg)[9:] and cfg[4] == 3
                 and bytes(cdc_cfg)[4] == 2, f"{cfg.hex()}\n        {bytes(cdc_cfg).hex()}")
    intf2 = cfg[len(cdc_cfg):]
    eps = [(intf2[i + 2], intf2[i + 3], intf2[i + 4] | intf2[i + 5] << 8)
           for i in range(9, len(intf2), 7)]
    checks.check("device: interface 2 is class 0xFF with bulk EP 0x01/0x81 (data) 0x02/0x82 "
                 "(control), 512 B", intf2[2] == 2 and intf2[5] == 0xFF and eps
                 == [(0x01, 2, 512), (0x81, 2, 512), (0x02, 2, 512), (0x82, 2, 512)],
                 f"{intf2.hex()} {eps}")
    checks.check("device: SET_ADDRESS and SET_CONFIGURATION complete",
                 seen["addr"] == ACK and seen["setconf"] == ACK)
    checks.check("device: bulk OUT EP1 reaches data_rx byte for byte, `last` on the short "
                 "packet's final byte", seen["out1"] == ACK and seen["data_rx"]
                 == [(b, int(b == 19)) for b in range(20)], f"{seen['data_rx']}")
    checks.check("device: bulk OUT EP2 reaches ctl_rx; EP4 still reaches the console",
                 seen["ctl_rx"] == [(7, 0), (8, 0), (9, 1)]
                 and [b for b, _ in seen["con_rx"]] == list(b"ls\r"),
                 f"{seen['ctl_rx']} {seen['con_rx']}")
    checks.check("device: data_tx and ctl_tx with `last` come back as one short IN packet each",
                 seen["in1"] == list(range(100, 110)) and seen["in2"] == [0x55, 0xAA],
                 f"{seen['in1']} {seen['in2']}")


# ---------------------------------------------------------------------------

GROUPS = [("out", group_out), ("in", group_in), ("nak", group_nak), ("errors", group_errors),
          ("header", group_header), ("pressure", group_pressure),
          ("mutation", group_mutation), ("engine", group_engine), ("device", group_device)]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true", help="the long size sweeps")
    parser.add_argument("groups", nargs="*", help="only these groups")
    args = parser.parse_args()
    checks = Checks(emit)
    emit("USB data path (gateware/soc/datapath/)")
    chosen = [(n, g) for n, g in GROUPS if not args.groups or n in args.groups]
    if len(chosen) == 1:
        chosen[0][1](checks, soak=args.soak)
        return checks.summary()
    procs = [(name, subprocess.Popen(
        [sys.executable, __file__, name] + (["--soak"] if args.soak else []),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)) for name, _ in chosen]
    status = 0
    for name, proc in procs:
        out, _ = proc.communicate()
        for line in out.splitlines()[1:]:
            emit(line)
        status |= proc.returncode
    return status


if __name__ == "__main__":
    sys.exit(main())
