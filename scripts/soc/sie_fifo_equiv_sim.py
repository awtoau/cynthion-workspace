#!/usr/bin/env python3
#
# The host engine on registered-read (block RAM) FIFOs against upstream GUH on SyncFIFO.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The #619 engine change: GUH's SIE FIFOs are `SyncFIFOBuffered` and its "has data" tests
read `r_level` instead of `r_rdy`. This checks that change against upstream GUH.

    ./scripts/soc/sie_fifo_equiv_sim.py              # everything
    ./scripts/soc/sie_fifo_equiv_sim.py --only engine
    ./scripts/soc/sie_fifo_equiv_sim.py --seed 7     # another random draw

Engines, all on the LUNA device bench (`usb_host/model.py`; the PHY takes a byte every cycle):
- `upstream`: GUH `sie.py` with the #619 edits reverted (generated into tmp/sie_fifo_equiv/)
  on amaranth `SyncFIFO`: the reference.
- `native`: `sie.py` as built, on `SyncFIFOBuffered`.
- `native+SyncFIFO` (read latency 0) and `native+latency2` (one more output register):
  the edits claim "any read latency"; both must meet the same contract.

Contract, every engine against `upstream`:
- every cycle identical up to the stray-write scenarios: UTMI TX (the wire), `status`,
  `tx_pending`, `txs.ready`;
- the RX byte stream identical in order (`rxs.valid` may arrive later: read latency);
- absolute: OUT 1 and 512 on the wire byte for byte with no gap; start the cycle after the
  last write; last write in the start cycle (4 of 5 sent, 5th drained); OUT ZLP; IN 512
  drained at 1, 1/2, 1/7 and after the packet; TX writes during a transfer; flush mid-fill;
  RX overflow at depth 500 with 500 bytes drained; 16 OUTs with random TX writes complete.
- Deliberate deviation, not held against `upstream`: stray TX writes every 3 cycles across
  IPD_DRAIN_TX's exit cycle (3 phases) end the drain within 5 cycles of the last write.
  Upstream's exit (`ipd == max-1` and empty, else `ipd` wraps) extends it by ~1024 cycles.

Negative controls (each must fail):
- `upstream+SyncFIFOBuffered`: the plain FIFO swap without the engine edits; the per-cycle
  contract must catch it.
- `native+same-cycle-wired`: `r_rdy` claimed from `level`, data not there yet (the old
  same-cycle contract on a registered read).
- `native+half-rate`: one read per two cycles; the engine assumes one per cycle.

A bus reset 100 bytes into an OUT is in the trace only: the bare engine never returns to idle
(on every FIFO; the shim's watchdog resets it; #572 class).
"""

import argparse
import importlib
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

from amaranth import Elaboratable, Module, Signal  # noqa: E402
from amaranth.hdl import DomainRenamer  # noqa: E402
from amaranth.lib import fifo as amaranth_fifo  # noqa: E402
from amaranth.lib.fifo import FIFOInterface  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import usb_host.guh.sie as sie_module  # noqa: E402
# Enums by value: the reference module defines its own copies of these classes.
from usb_host.guh.sie import TransferResponse, TransferType  # noqa: E402
from usb_host.model import (BULK_IN_ENDPOINT, ModelDevice, connect_utmi,  # noqa: E402
                            scale_timing_for_simulation)

# A frame is 3750 cycles in the scaled bench; a timeout takes one. 12 000 = 3.2 frames.
TRANSACTION_CYCLES = 12_000
BRINGUP_CYCLES = 40_000     # reset (MAX_RESET_TIME/200) plus settle and chirps
# Fixed post-transfer RX drain, so stimulus never depends on when rxs.valid rises:
IN_DRAIN_CYCLES = 4_000     # 512 bytes at 1/7 is 3584 cycles
OUT_DRAIN_CYCLES = 16
# A FIFO with room takes a byte every cycle; a full one never frees outside a transfer.
PUT_WAIT_CYCLES = 1
# IPD_DRAIN_TX after a stray TX write at W: readable by W+1+latency (<= 2 here), read, idle
# seen the cycle after: W+5. An ipd wrap (the old exit) is ~1024.
STRAY_WRITE_PERIOD = 3
STRAY_DRAIN_BOUND = 5
NOBODY = 0x55               # no device answers here: OUT data reaches the wire, then TIMEOUT
PID_DATA = (0xC3, 0x4B)

# The #619 edits in sie.py as (ours, upstream). The reference is generated from these, so a
# changed line fails loudly here, and the list is the documented diff against the pin.
UPSTREAM_EDITS = [
    ("fifo.SyncFIFOBuffered(width=8, depth=self.fifo_depth))\n"
     "        m.submodules.rx_fifo = rx_fifo = DomainRenamer(\"usb\")"
     "(fifo.SyncFIFOBuffered(width=8, depth=self.fifo_depth))",
     "fifo.SyncFIFO(width=8, depth=self.fifo_depth))\n"
     "        m.submodules.rx_fifo = rx_fifo = DomainRenamer(\"usb\")"
     "(fifo.SyncFIFO(width=8, depth=self.fifo_depth))"),
    ("self.ctrl.tx_pending.eq(tx_fifo.r_level != 0)", "self.ctrl.tx_pending.eq(tx_fifo.r_rdy)"),
    ("with m.If(rx_fifo.r_level == 0):", "with m.If(~rx_fifo.r_rdy):"),
    ("with m.If((ipd == (ipd_max-1)) & (tx_fifo.r_level == 0)):",
     "with m.If((ipd == (ipd_max-1)) & ~tx_fifo.r_rdy):"),
    ("from ..guh_lower import *", "from usb_host.guh_lower import *"),
    ("from .types import *", "from usb_host.guh.types import *"),
    ("from .reset import *", "from usb_host.guh.reset import *"),
]


def upstream_sie():
    """GUH sie.py with the #619 edits reverted, as module `guh_upstream_ref`."""
    out = ROOT / "tmp" / "sie_fifo_equiv"
    out.mkdir(parents=True, exist_ok=True)
    text = (ROOT / "gateware" / "probes" / "usb_host" / "guh" / "sie.py").read_text()
    for ours, upstream in UPSTREAM_EDITS:
        if text.count(ours) != 1:
            raise RuntimeError(f"guh/sie.py: {ours!r} changed; update UPSTREAM_EDITS")
        text = text.replace(ours, upstream)
    (out / "guh_upstream_ref.py").write_text(text)
    sys.path.insert(0, str(out))
    module = importlib.import_module("guh_upstream_ref")
    scale_timing_for_simulation()
    for attr in dir(sie_module.USBSOFController):
        if attr.startswith("_SOF"):
            setattr(module.USBSOFController, attr, getattr(sie_module.USBSOFController, attr))
    return module


class HarnessStall(Exception):
    """A bounded testbench wait ran out: reported as a failed run, not a hang."""


# ---------------------------------------------------------------- FIFO variants

def _wrap(m, inner, outer):
    m.d.comb += [inner.w_data.eq(outer.w_data), inner.w_en.eq(outer.w_en)]


class SameCycleWired(Elaboratable, FIFOInterface):
    """Negative: `SyncFIFOBuffered` with `r_rdy` claimed from `level`, as if same-cycle."""

    def __init__(self, *, width, depth):
        super().__init__(width=width, depth=depth)

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner = inner = amaranth_fifo.SyncFIFOBuffered(width=self.width, depth=self.depth)
        _wrap(m, inner, self)
        m.d.comb += [
            self.w_rdy.eq(inner.w_rdy), self.w_level.eq(inner.level), self.r_level.eq(inner.level),
            self.r_rdy.eq(inner.level != 0), self.r_data.eq(inner.r_data), inner.r_en.eq(self.r_en),
        ]
        return m


class HalfRate(Elaboratable, FIFOInterface):
    """Negative: `SyncFIFOBuffered` that hides `r_rdy` the cycle after each read."""

    def __init__(self, *, width, depth):
        super().__init__(width=width, depth=depth)

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner = inner = amaranth_fifo.SyncFIFOBuffered(width=self.width, depth=self.depth)
        _wrap(m, inner, self)
        just_read = Signal()
        m.d.sync += just_read.eq(self.r_rdy & self.r_en)
        m.d.comb += [
            self.w_rdy.eq(inner.w_rdy), self.w_level.eq(inner.level), self.r_level.eq(inner.level),
            self.r_rdy.eq(inner.r_rdy & ~just_read), self.r_data.eq(inner.r_data),
            inner.r_en.eq(self.r_en & self.r_rdy),
        ]
        return m


class Latency2(Elaboratable, FIFOInterface):
    """`SyncFIFOBuffered` into a 4-deep one: a write is readable a cycle later still."""

    def __init__(self, *, width, depth):
        super().__init__(width=width, depth=depth)

    def elaborate(self, platform):
        m = Module()
        m.submodules.a = a = amaranth_fifo.SyncFIFOBuffered(width=self.width, depth=self.depth)
        m.submodules.b = b = amaranth_fifo.SyncFIFOBuffered(width=self.width, depth=4)
        _wrap(m, a, self)
        level = a.level + b.level
        m.d.comb += [
            b.w_data.eq(a.r_data), b.w_en.eq(a.r_rdy), a.r_en.eq(b.w_rdy),
            self.w_rdy.eq(a.w_rdy & (level < self.depth)),
            self.w_level.eq(level), self.r_level.eq(level),
            self.r_rdy.eq(b.r_rdy), self.r_data.eq(b.r_data), b.r_en.eq(self.r_en),
        ]
        return m


def native(cls):
    return ("native", SimpleNamespace(SyncFIFOBuffered=cls))


def upstream(cls):
    return ("upstream", SimpleNamespace(SyncFIFO=cls))


ENGINES = {
    "upstream": upstream(amaranth_fifo.SyncFIFO),
    "native": native(amaranth_fifo.SyncFIFOBuffered),
    "native+SyncFIFO": native(amaranth_fifo.SyncFIFO),
    "native+latency2": native(Latency2),
}
NEGATIVES = {
    "upstream+SyncFIFOBuffered": upstream(amaranth_fifo.SyncFIFOBuffered),
    "native+same-cycle-wired": native(SameCycleWired),
    "native+half-rate": native(HalfRate),
}


def make_bench(engine, depth, ref_module):
    """(module, sie, device) for one engine: Bench's wiring with a chosen SIE module."""
    logic, fifo_ns = engine
    module = sie_module if logic == "native" else ref_module
    scale_timing_for_simulation()
    saved = module.fifo
    module.fifo = fifo_ns
    m = Module()
    host = module.USBSIE(fifo_depth=depth, fullspeed_only=False)
    device = ModelDevice(full_speed_only=False)
    rename = DomainRenamer({"usb": "sync"})
    m.submodules.host = rename(host)
    m.submodules.device = rename(device)
    connect_utmi(m, host.utmi, device.utmi)
    return m, host, device, lambda: setattr(module, "fifo", saved)


# ---------------------------------------------------------------- scenarios

def run_engine(engine, depth, rng_seed, ref_module):
    """One bench run: (per-cycle trace, results dict). Elaborated before `fifo` is restored."""
    rng = random.Random(rng_seed)
    m, host, device, restore = make_bench(engine, depth, ref_module)
    ctrl, host_utmi, dev_utmi = host.ctrl, host.utmi, device.utmi
    trace, wire, sofs, data_ends, res = [], [], [], [], {"rx": []}

    async def sampler(ctx):
        cycle, in_packet, first = 0, False, None
        while True:
            _, _, *v = await ctx.tick().sample(
                ctrl.tx_pending, ctrl.txs.ready, ctrl.status.as_value(), host_utmi.tx_valid,
                host_utmi.tx_data, dev_utmi.rx_valid, dev_utmi.rx_active, dev_utmi.rx_data)
            pend, trdy, status, txv, txd, dval, dact, ddat = v
            trace.append((pend, trdy, status, txv, txd if txv else None))
            wire.append((cycle, dval, dact, ddat))
            if dval and not in_packet:
                first = int(ddat)
                if first == 0xA5:
                    sofs.append(cycle)
            if in_packet and not dact and first in PID_DATA:
                data_ends.append(cycle)
            in_packet = bool(dact) and (in_packet or bool(dval))
            cycle += 1

    async def put(ctx, payload, start_with_last=False):
        for i, byte in enumerate(payload):
            ctx.set(ctrl.txs.payload, byte)
            ctx.set(ctrl.txs.valid, 1)
            if start_with_last and i == len(payload) - 1:
                ctx.set(ctrl.xfer.start, 1)
            for _ in range(PUT_WAIT_CYCLES):
                accepted = ctx.get(ctrl.txs.ready)
                await ctx.tick()
                if accepted:
                    break
            else:
                raise HarnessStall(f"{res.get('stage')}: txs.ready low for {PUT_WAIT_CYCLES} "
                                   f"cycle(s) at byte {i}, cycle {len(wire)}")
            ctx.set(ctrl.xfer.start, 0)
        ctx.set(ctrl.txs.valid, 0)

    async def transact(ctx, type, address, endpoint, *, payload=(), ready=lambda c: 1,
                       drain=None, gap=0, start_with_last=False, write_prob=0.0,
                       writes_every=0, write_at=None, bus_reset_after=None):
        """One transfer; returns (rx bytes, response, completed, wire mark)."""
        drain = drain or ready
        mark = len(wire)
        ctx.set(ctrl.xfer.type, type.value)
        ctx.set(ctrl.xfer.dev_addr, address)
        ctx.set(ctrl.xfer.ep_addr, endpoint)
        ctx.set(ctrl.xfer.data_pid, 0)
        if payload and start_with_last:
            await put(ctx, payload, start_with_last=True)
        else:
            await put(ctx, payload)
            for _ in range(gap):
                await ctx.tick()
            ctx.set(ctrl.xfer.start, 1)
            await ctx.tick()
            ctx.set(ctrl.xfer.start, 0)
        received, started, tx_bytes = [], False, 0
        for cycle in range(TRANSACTION_CYCLES):
            r = ready(cycle)
            ctx.set(ctrl.rxs.ready, r)
            if ctx.get(ctrl.rxs.valid) and r:
                received.append(int(ctx.get(ctrl.rxs.payload)))
            write = ((bool(writes_every) and cycle % writes_every == 0)
                     or rng.random() < write_prob or (write_at is not None and write_at(len(wire))))
            ctx.set(ctrl.txs.valid, write)
            ctx.set(ctrl.txs.payload, rng.randrange(256))
            tx_bytes += ctx.get(host_utmi.tx_valid)
            ctx.set(ctrl.bus_reset, bus_reset_after is not None and tx_bytes == bus_reset_after)
            idle = ctx.get(ctrl.status.idle)
            await ctx.tick()
            if not idle:
                started = True
            elif started:
                res["idle_at"] = len(wire) - 1
                break
        ctx.set(ctrl.txs.valid, 0)
        ctx.set(ctrl.bus_reset, 0)
        done = started and ctx.get(ctrl.status.idle)
        response = ctx.get(ctrl.status.response).value if done else None
        n_drain = IN_DRAIN_CYCLES if type == TransferType.IN else OUT_DRAIN_CYCLES
        for cycle in range(n_drain if done else 0):
            r = drain(cycle)
            ctx.set(ctrl.rxs.ready, r)
            if ctx.get(ctrl.rxs.valid) and r:
                received.append(int(ctx.get(ctrl.rxs.payload)))
            await ctx.tick()
        ctx.set(ctrl.rxs.ready, 0)
        res["rx"] += received
        return received, response, done, mark

    def data_packets(since):
        """Device-side DATA packets since `since`: list of (cycles, bytes)."""
        out, cur = [], []
        for cycle, valid, active, data in wire[since:]:
            if valid:
                cur.append((cycle, int(data)))
            if cur and not active:
                if cur[0][1] in PID_DATA:
                    out.append(([c for c, _ in cur], [b for _, b in cur]))
                cur = []
        return out

    async def wait_idle(ctx):
        for _ in range(BRINGUP_CYCLES):
            await ctx.tick()
            if ctx.get(ctrl.status.idle):
                return True
        return False

    async def tb(ctx):
        res["stage"] = "directed transfers"
        res["up"] = await wait_idle(ctx)
        out = {}
        for name, length, gap in (("out1", 1, 0), ("out512", depth, 0),
                                  ("out512_gap", depth, 20), ("zlp", 0, 0)):
            payload = [rng.randrange(256) for _ in range(length)]
            _, resp, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1,
                                                 payload=payload, gap=gap)
            out[name] = (payload, data_packets(mark), resp, done)
        res["out"] = out
        payload = [rng.randrange(256) for _ in range(5)]
        _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload,
                                          start_with_last=True)
        res["same_cycle_start"] = (payload, data_packets(mark), done,
                                   int(ctx.get(ctrl.tx_pending)))
        # SET_CONFIGURATION(1) so the bulk IN endpoint answers.
        await transact(ctx, TransferType.SETUP, 0, 0, payload=[0, 9, 1, 0, 0, 0, 0, 0])
        await transact(ctx, TransferType.IN, 0, 0)
        rates = {"1": lambda c: 1, "1/2": lambda c: int(c % 2 == 0),
                 "1/7": lambda c: int(c % 7 == 0)}
        res["in"] = {}
        for name, rate in rates.items():
            got, resp, done, _ = await transact(ctx, TransferType.IN, 0, BULK_IN_ENDPOINT,
                                                ready=rate)
            res["in"][name] = (got, resp, done)
        got, resp, done, _ = await transact(ctx, TransferType.IN, 0, BULK_IN_ENDPOINT,
                                            ready=lambda c: 0, drain=lambda c: 1)
        res["in"]["after"] = (got, resp, done)
        payload = [rng.randrange(256) for _ in range(64)]
        _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload,
                                          writes_every=37)
        res["writes_during"] = (payload, data_packets(mark), done)
        res["contract_end"] = len(trace)
        res["stage"] = "stray writes across the IPD exit"
        # Stray TX writes every STRAY_WRITE_PERIOD cycles across IPD_DRAIN_TX's exit cycle.
        # A TIMEOUT OUT ends at a fixed offset from the SOF before it, so a reference run
        # without writes finds that cycle; each phase then puts writes across it.
        await transact(ctx, TransferType.OUT, NOBODY, 1, payload=[1] * 8)
        end = res["idle_at"] - sofs[-1]
        res["stray"] = []
        for phase in range(STRAY_WRITE_PERIOD):
            offsets = [end - 6 + STRAY_WRITE_PERIOD * k + phase for k in range(4)]
            sent, writes = len(data_ends), []

            def write_at(now, offsets=offsets, sent=sent, writes=writes):
                hit = len(data_ends) > sent and now - sofs[-1] in offsets
                if hit:
                    writes.append(now)
                return hit
            *_, done, _ = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=[1] * 8,
                                         write_at=write_at)
            res["stray"].append((done, len(writes), res["idle_at"] - max(writes, default=0)))
        res["stage"] = "16 OUTs with random TX writes"
        res["race_done"] = 0
        for _ in range(16):
            payload = [rng.randrange(256) for _ in range(rng.randrange(1, 64))]
            *_, done, _ = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload,
                                         write_prob=0.3)
            res["race_done"] += bool(done)
        ctx.set(ctrl.tx_flush, 1)
        for _ in range(depth + 8):
            await ctx.tick()
        ctx.set(ctrl.tx_flush, 0)
        await ctx.tick()
        # Flush mid-fill: 300 bytes, flush, 3 bytes, OUT carries exactly the 3.
        await put(ctx, [rng.randrange(256) for _ in range(300)])
        ctx.set(ctrl.tx_flush, 1)
        for _ in range(depth + 8):
            await ctx.tick()
        ctx.set(ctrl.tx_flush, 0)
        await ctx.tick()
        res["flushed_pending"] = int(ctx.get(ctrl.tx_pending))
        payload = [rng.randrange(256) for _ in range(3)]
        _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload)
        res["after_flush"] = (payload, data_packets(mark), done)
        await transact(ctx, TransferType.OUT, NOBODY, 1,
                       payload=[rng.randrange(256) for _ in range(depth)], bus_reset_after=100)
        for _ in range(2 * TRANSACTION_CYCLES):
            await ctx.tick()
        res["idle_after_reset"] = int(ctx.get(ctrl.status.idle))

    try:
        sim = Simulator(m)
    finally:
        restore()
    sim.add_clock(1 / 60e6)
    sim.add_process(sampler)
    sim.add_testbench(tb)
    try:
        sim.run()
    except HarnessStall as stall:
        res["stall"] = str(stall)
    return trace, res


def run_overflow(engine, depth, ref_module):
    """An IN of 512 into a `depth`-byte RX FIFO with nobody reading, then drained."""
    m, host, _, restore = make_bench(engine, depth, ref_module)
    ctrl, res = host.ctrl, {}

    async def xfer(ctx, type, endpoint, payload=()):
        ctx.set(ctrl.xfer.type, type.value)
        ctx.set(ctrl.xfer.dev_addr, 0)
        ctx.set(ctrl.xfer.ep_addr, endpoint)
        ctx.set(ctrl.xfer.data_pid, 0)
        for byte in payload:
            ctx.set(ctrl.txs.payload, byte)
            ctx.set(ctrl.txs.valid, 1)
            await ctx.tick().until(ctrl.txs.ready == 1)
        ctx.set(ctrl.txs.valid, 0)
        ctx.set(ctrl.xfer.start, 1)
        await ctx.tick()
        ctx.set(ctrl.xfer.start, 0)
        started = False
        for _ in range(TRANSACTION_CYCLES):
            idle = ctx.get(ctrl.status.idle)
            await ctx.tick()
            if not idle:
                started = True
            elif started:
                return ctx.get(ctrl.status.response).value
        return None

    async def tb(ctx):
        for _ in range(BRINGUP_CYCLES):
            await ctx.tick()
            if ctx.get(ctrl.status.idle):
                break
        ctx.set(ctrl.rxs.ready, 1)
        await xfer(ctx, TransferType.SETUP, 0, [0, 9, 1, 0, 0, 0, 0, 0])
        await xfer(ctx, TransferType.IN, 0)
        for _ in range(8):
            await ctx.tick()
        ctx.set(ctrl.rxs.ready, 0)
        res["response"] = await xfer(ctx, TransferType.IN, BULK_IN_ENDPOINT)
        got = []
        ctx.set(ctrl.rxs.ready, 1)
        for _ in range(depth + 64):
            if ctx.get(ctrl.rxs.valid):
                got.append(int(ctx.get(ctrl.rxs.payload)))
            await ctx.tick()
        res["drained"] = got

    try:
        sim = Simulator(m)
    finally:
        restore()
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()
    return res


# ---------------------------------------------------------------- checks

def counting(data):
    return all((b - a) % 256 == 1 for a, b in zip(data, data[1:]))


def absolute_checks(res, depth, *, stray=True):
    """(name, ok, detail) for one engine run: what must hold whatever FIFO is inside.

    After a stall, the scenarios that ran are still checked; the rest are absent.
    """
    out = [("testbench ran to the end", "stall" not in res, res.get("stall", ""))]
    if "writes_during" not in res:
        return out

    def packet_ok(payload, packets):
        if len(packets) != 1:
            return False, f"{len(packets)} DATA packets on the wire"
        cycles, data = packets[0]
        body = data[1:-2]
        gaps = [b - a for a, b in zip(cycles, cycles[1:]) if b - a != 1]
        if body != list(payload):
            first = next((i for i, (a, b) in enumerate(zip(body, payload)) if a != b), None)
            return False, f"payload differs (len {len(body)} vs {len(payload)}, first index {first})"
        if gaps:
            return False, f"{len(gaps)} gaps in the data phase"
        return True, ""

    out.append(("engine came up", res.get("up", False), "no idle after bring-up"))
    for name, label in (("out1", "OUT 1 byte, start the cycle after the write"),
                        ("out512", f"OUT {depth} bytes, start the cycle after the last write"),
                        ("out512_gap", f"OUT {depth} bytes, start 20 cycles after"),
                        ("zlp", "OUT ZLP")):
        payload, packets, resp, done = res["out"][name]
        ok, why = packet_ok(payload, packets)
        out.append((f"{label}: on the wire byte for byte, no gap, response TIMEOUT",
                    ok and done and resp == TransferResponse.TIMEOUT.value, why or f"resp {resp}"))
    payload, packets, done, pending = res["same_cycle_start"]
    ok, why = packet_ok(payload[:-1], packets)
    out.append(("OUT with the last write in the start cycle: 4 of 5 bytes sent, the 5th "
                "drained (tx_pending 0 after)", ok and done and pending == 0,
                why or f"pending {pending}"))
    for name, (got, resp, done) in res["in"].items():
        out.append((f"IN 512 drained at rate {name}: 512 counting bytes, ACK",
                    done and resp == TransferResponse.ACK.value and len(got) == 512 and counting(got),
                    f"resp {resp}, {len(got)} bytes, counting {counting(got)}"))
    payload, packets, done = res["writes_during"]
    ok, why = packet_ok(payload, packets)
    out.append(("OUT 64 with TX writes every 37 cycles during the transfer: the 64 on the wire",
                ok and done, why))
    if stray and "stray" in res:
        bad = [r for r in res["stray"] if not (r[0] and r[1] and 0 <= r[2] <= STRAY_DRAIN_BOUND)]
        out.append((f"stray TX writes across the IPD_DRAIN_TX exit ({STRAY_WRITE_PERIOD} phases): "
                    f"idle within {STRAY_DRAIN_BOUND} cycles of the last write, no ipd wrap",
                    not bad, f"(done, writes, idle - last write) {res['stray']}"))
    if "flushed_pending" not in res:
        return out
    out.append(("16 OUTs with random TX writes during each: all complete", res["race_done"] == 16,
                f"{res['race_done']} of 16"))
    out.append(("TX flush mid-fill empties the FIFO (tx_pending 0)", res["flushed_pending"] == 0,
                f"tx_pending {res['flushed_pending']}"))
    payload, packets, done = res["after_flush"]
    ok, why = packet_ok(payload, packets)
    out.append(("OUT 3 after the flush: exactly those 3 bytes", ok and done, why))
    return out


def contract(run, ref):
    """None if `run` meets the per-cycle contract against `ref`, else why not."""
    (trace, res), (ref_trace, ref_res) = run, ref
    if "stall" in res:
        return res["stall"]
    end = ref_res["contract_end"]
    trace, ref_trace = trace[:end], ref_trace[:end]
    diff = next((i for i, (a, b) in enumerate(zip(trace, ref_trace)) if a != b), None)
    if diff is None and len(trace) != len(ref_trace):
        diff = min(len(trace), len(ref_trace))
    if diff is not None:
        got = trace[diff] if diff < len(trace) else "-"
        want = ref_trace[diff] if diff < len(ref_trace) else "-"
        return f"cycle {diff}: (tx_pending, txs.ready, status, tx_valid, tx_data) {got} vs {want}"
    if res["rx"] != ref_res["rx"]:
        return f"RX byte stream differs ({len(res['rx'])} vs {len(ref_res['rx'])} bytes)"
    return None


def section_engine(checks, seed, negative):
    depth = 512
    ref_module = upstream_sie()
    names = list(ENGINES) + (list(NEGATIVES) if negative else [])
    runs = {}
    for name in names:
        runs[name] = run_engine({**ENGINES, **NEGATIVES}[name], depth, seed, ref_module)
        checks.note(f"ran {name}: {len(runs[name][0])} cycles, bare engine idle after a bus "
                    f"reset mid-OUT: {runs[name][1]['idle_after_reset']}")
    ref = runs["upstream"]
    for name in ENGINES:
        # The stray-write drain is the deliberate deviation from upstream (UPSTREAM_EDITS).
        for what, ok, detail in absolute_checks(runs[name][1], depth, stray=name != "upstream"):
            checks.check(f"engine {name}: {what}", ok, detail)
        if name != "upstream":
            why = contract(runs[name], ref)
            checks.check(f"engine {name} == upstream every cycle up to the stray-write scenarios "
                         f"({ref[1]['contract_end']} cycles: wire, status, tx_pending, txs.ready) "
                         "and the same RX bytes",
                         why is None, why or "")
    if negative:
        why = contract(runs["upstream+SyncFIFOBuffered"], ref)
        failed = [w for w, ok, _ in absolute_checks(runs["upstream+SyncFIFOBuffered"][1], depth)
                  if not ok]
        checks.check("negative: the FIFO swap without the engine edits breaks the contract",
                     why is not None, "contract met: the edits are not shown to be needed",
                     measurement=f"{why}; absolute checks failing: {len(failed)} {failed[:2]}")
        for name in ("native+same-cycle-wired", "native+half-rate"):
            failed = [w for w, ok, _ in absolute_checks(runs[name][1], depth) if not ok]
            checks.check(f"negative: engine {name} fails an absolute check", bool(failed),
                         "every absolute check passed",
                         measurement=f"{len(failed)} failed, first: {failed[:2]}")

    for name in ("upstream", "native", "native+latency2"):
        res = run_overflow(ENGINES[name], 500, ref_module)
        got = res["drained"]
        checks.check(f"engine {name}, depth 500: IN 512 with nobody reading is RX_OVERFLOW "
                     "and 500 counting bytes drain",
                     res["response"] == TransferResponse.RX_OVERFLOW.value and len(got) == 500
                     and counting(got), f"resp {res['response']}, {len(got)} bytes")


SECTIONS = ("engine", "negative")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=619)
    parser.add_argument("--only", nargs="*", choices=SECTIONS, default=SECTIONS)
    args = parser.parse_args()
    checks = Checks(emit)
    emit(f"SIE on registered-read FIFOs vs upstream GUH on SyncFIFO (seed {args.seed})")
    section_engine(checks, args.seed, "negative" in args.only)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
