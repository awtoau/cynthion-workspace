#!/usr/bin/env python3
#
# The block-RAM SIE FIFO against amaranth's SyncFIFO: alone, and inside the host engine.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Equivalence of `gateware/usbcore/fifo.py` with `amaranth.lib.fifo.SyncFIFO` (#619).

    ./scripts/soc/sie_fifo_equiv_sim.py              # everything
    ./scripts/soc/sie_fifo_equiv_sim.py --only fifo  # one section
    ./scripts/soc/sie_fifo_equiv_sim.py --seed 7     # another random draw

Sections:
- fifo: ours and amaranth's in lockstep on random stimulus with rates changing every few
  hundred cycles (0 to 1 each side), depths 2, 3, 5, 16, 500, 512. Every cycle: `w_rdy`,
  `r_rdy`, `level`, `w_level`, `r_level`; `r_data` while `r_rdy`.
- negative: the same lockstep must FAIL for amaranth's `SyncFIFOBuffered` (one cycle late)
  and for a registered-read FIFO wired as if it answered in the same cycle.
- engine: GUH's `USBSIE` on the LUNA device bench (`usb_host/model.py`), run once on each
  FIFO; per-cycle trace of the CPU side and the UTMI TX must be identical, plus absolute
  checks: OUT 1 and 512 bytes on the wire with no gap (the bench PHY takes a byte every
  cycle), last byte written the cycle before `start`, OUT ZLP, IN 512 drained at rates
  1, 1/2, 1/7 and after the packet, TX writes during a transfer, TX flush mid-fill; at
  depth 500, an IN of 512 overflows (RX_OVERFLOW) and 500 bytes drain.
- A bus reset 100 bytes into an OUT is in the trace only: the bare engine never returns
  to idle on either FIFO (the shim's watchdog resets it; #572 class).
- engine-negative: the engine on the same-cycle-wired FIFO must fail an absolute check.
- No TX underrun case exists to test: `tx_len` is `w_level` at `start` and nothing reads
  the TX FIFO before SEND_OUT_DATA, so every counted byte is present when the PHY asks.
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

from amaranth import Elaboratable, Module  # noqa: E402
from amaranth.lib import fifo as amaranth_fifo  # noqa: E402
from amaranth.lib.fifo import FIFOInterface  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from usbcore import fifo as bram_fifo  # noqa: E402
import usb_host.guh.sie as sie_module  # noqa: E402
from usb_host.guh.sie import DataPID, TransferResponse, TransferType  # noqa: E402
from usb_host.model import BULK_IN_ENDPOINT, Bench  # noqa: E402

DEPTHS = (2, 3, 5, 16, 500, 512)
# A frame is 3750 cycles in the scaled bench; a timeout takes one. 12 000 = 3.2 frames.
TRANSACTION_CYCLES = 12_000
BRINGUP_CYCLES = 40_000     # reset (MAX_RESET_TIME/200) plus settle and chirps
QUIET_CYCLES = 16           # RX drained: no rxs.valid for longer than any 1/7 gap
NOBODY = 0x55               # no device answers here: OUT data reaches the wire, then TIMEOUT
PID_DATA = (0xC3, 0x4B)


class SameCycleWired(Elaboratable, FIFOInterface):
    """Negative control: `SyncFIFOBuffered` with `r_rdy` claimed from `level`, as if same-cycle."""

    def __init__(self, *, width, depth):
        super().__init__(width=width, depth=depth)
        self.level = self.w_level

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner = inner = amaranth_fifo.SyncFIFOBuffered(width=self.width, depth=self.depth)
        m.d.comb += [
            inner.w_data.eq(self.w_data), inner.w_en.eq(self.w_en), self.w_rdy.eq(inner.w_rdy),
            self.w_level.eq(inner.level), self.r_level.eq(inner.level),
            self.r_rdy.eq(inner.level != 0), self.r_data.eq(inner.r_data),
            inner.r_en.eq(self.r_en),
        ]
        return m


FIFOS = {
    "ours": bram_fifo,
    "amaranth": amaranth_fifo,
    "buffered": SimpleNamespace(SyncFIFO=amaranth_fifo.SyncFIFOBuffered),
    "same-cycle-wired": SimpleNamespace(SyncFIFO=SameCycleWired),
}


# ---------------------------------------------------------------- FIFO alone

def lockstep_fifo(rng, cls, depth, n_cycles):
    """Mismatches of `cls` against amaranth SyncFIFO, plus coverage counters."""
    m = Module()
    m.submodules.ref = ref = amaranth_fifo.SyncFIFO(width=8, depth=depth)
    m.submodules.dut = dut = cls(width=8, depth=depth)
    mism, cover = [], {"full": 0, "empty_after_data": 0, "write_into_empty": 0, "reads": 0}

    async def tb(ctx):
        p_w = p_r = 0.5
        seen_data = False
        for cycle in range(n_cycles):
            if cycle % rng.randrange(50, 400) == 0:
                p_w, p_r = rng.choice((0, 0.1, 0.5, 0.9, 1)), rng.choice((0, 0.1, 0.5, 0.9, 1))
            w_en, r_en, data = rng.random() < p_w, rng.random() < p_r, rng.randrange(256)
            for f in (ref, dut):
                ctx.set(f.w_en, w_en)
                ctx.set(f.r_en, r_en)
                ctx.set(f.w_data, data)
            for name in ("w_rdy", "r_rdy", "level", "w_level", "r_level"):
                if ctx.get(getattr(ref, name)) != ctx.get(getattr(dut, name)):
                    mism.append((cycle, name))
            if ctx.get(ref.r_rdy) and ctx.get(ref.r_data) != ctx.get(dut.r_data):
                mism.append((cycle, "r_data"))
            level = ctx.get(ref.level)
            cover["full"] += level == depth
            cover["empty_after_data"] += seen_data and level == 0
            cover["write_into_empty"] += w_en and (level == 0 or (level == 1 and r_en))
            cover["reads"] += r_en and level != 0
            seen_data |= level != 0
            await ctx.tick()

    sim = Simulator(m)
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()
    return mism, cover


def section_fifo(checks, rng):
    for depth in DEPTHS:
        n = max(4000, 12 * depth)
        mism, cover = lockstep_fifo(rng, bram_fifo.SyncFIFO, depth, n)
        checks.check(f"fifo depth {depth}: ours == amaranth SyncFIFO every cycle ({n} cycles)",
                     not mism, f"{len(mism)} mismatches, first {mism[:4]}")
        checks.check(f"fifo depth {depth}: stimulus reached full, empty-after-data and "
                     "write-into-empty (the bypass path)",
                     all(cover.values()), f"{cover}", measurement=f"{cover}")


def section_negative(checks, rng):
    for label, cls in (("SyncFIFOBuffered (one cycle late)", amaranth_fifo.SyncFIFOBuffered),
                       ("registered read wired as same-cycle", SameCycleWired)):
        mism, _ = lockstep_fifo(rng, cls, 16, 4000)
        checks.check(f"negative: {label} FAILS the depth-16 lockstep", bool(mism),
                     "no mismatch: the lockstep cannot see the difference",
                     measurement=f"{len(mism)} mismatches, first {mism[:3]}")


# ---------------------------------------------------------------- inside the engine

def run_engine(fifo_ns, depth, rng_seed):
    """One bench run on `fifo_ns`: (per-cycle trace, results dict)."""
    rng = random.Random(rng_seed)
    saved = sie_module.fifo
    sie_module.fifo = fifo_ns
    try:
        bench = Bench(full_speed_only=False, fifo_depth=depth)
        ctrl, host_utmi, dev_utmi = bench.ctrl, bench.host.utmi, bench.device.utmi
        trace, wire, res = [], [], {}

        async def sampler(ctx):
            cycle = 0
            while True:
                _, _, *v = await ctx.tick().sample(
                    ctrl.tx_pending, ctrl.txs.ready, ctrl.rxs.valid, ctrl.rxs.payload,
                    ctrl.status.as_value(), host_utmi.tx_valid, host_utmi.tx_data,
                    dev_utmi.rx_valid, dev_utmi.rx_active, dev_utmi.rx_data)
                pend, trdy, rval, rpay, status, txv, txd, dval, dact, ddat = v
                trace.append((pend, trdy, rval, rpay if rval else None, status,
                              txv, txd if txv else None))
                wire.append((cycle, dval, dact, ddat))
                cycle += 1

        async def put(ctx, payload, start_with_last=None):
            for i, byte in enumerate(payload):
                ctx.set(ctrl.txs.payload, byte)
                ctx.set(ctrl.txs.valid, 1)
                if start_with_last is not None and i == len(payload) - 1:
                    start_with_last(ctx)
                await ctx.tick().until(ctrl.txs.ready == 1)
                ctx.set(ctrl.xfer.start, 0)
            ctx.set(ctrl.txs.valid, 0)

        def fields(ctx, type, address, endpoint, data_pid=DataPID.DATA0):
            ctx.set(ctrl.xfer.type, type)
            ctx.set(ctrl.xfer.dev_addr, address)
            ctx.set(ctrl.xfer.ep_addr, endpoint)
            ctx.set(ctrl.xfer.data_pid, data_pid)

        async def transact(ctx, type, address, endpoint, *, payload=(), ready=lambda c: 1,
                           drain=None, gap=0, start_with_last=False, writes_every=0,
                           bus_reset_after=None):
            """Issue one transfer; returns (rx bytes, response, completed, wire mark)."""
            drain = drain or ready
            mark = len(wire)
            fields(ctx, type, address, endpoint)
            if payload and start_with_last:
                await put(ctx, payload, start_with_last=lambda c: c.set(ctrl.xfer.start, 1))
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
                ctx.set(ctrl.txs.valid, bool(writes_every) and cycle % writes_every == 0)
                ctx.set(ctrl.txs.payload, rng.randrange(256))
                tx_bytes += ctx.get(host_utmi.tx_valid)
                ctx.set(ctrl.bus_reset, bus_reset_after is not None and tx_bytes == bus_reset_after)
                idle = ctx.get(ctrl.status.idle)
                await ctx.tick()
                if not idle:
                    started = True
                elif started:
                    break
            ctx.set(ctrl.txs.valid, 0)
            ctx.set(ctrl.bus_reset, 0)
            if not started or not ctx.get(ctrl.status.idle):
                return received, None, False, mark
            response = ctx.get(ctrl.status.response)
            quiet, cycle = 0, 0
            while quiet < QUIET_CYCLES:
                r = drain(cycle)
                ctx.set(ctrl.rxs.ready, r)
                valid = ctx.get(ctrl.rxs.valid)
                if valid and r:
                    received.append(int(ctx.get(ctrl.rxs.payload)))
                quiet = 0 if valid else quiet + 1
                await ctx.tick()
                cycle += 1
            ctx.set(ctrl.rxs.ready, 0)
            return received, response, True, mark

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

        async def flush(ctx, cycles):
            ctx.set(ctrl.tx_flush, 1)
            for _ in range(cycles):
                await ctx.tick()
            ctx.set(ctrl.tx_flush, 0)
            await ctx.tick()

        async def tb(ctx):
            res["up"] = await wait_idle(ctx)
            out = {}
            for name, length, gap in (("out1", 1, 0), ("out512", depth, 0),
                                      ("out512_gap", depth, 20), ("zlp", 0, 0)):
                payload = [rng.randrange(256) for _ in range(length)]
                _, resp, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1,
                                                     payload=payload, gap=gap)
                out[name] = (payload, data_packets(mark), resp, done)
            # Last byte written in the same cycle as start: not counted, drained in IPD.
            payload = [rng.randrange(256) for _ in range(5)]
            _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload,
                                              start_with_last=True)
            res["same_cycle_start"] = (payload, data_packets(mark), done,
                                       int(ctx.get(ctrl.tx_pending)))
            res["out"] = out
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
            # TX bytes arriving every 37 cycles during an OUT (and its IPD drain).
            payload = [rng.randrange(256) for _ in range(64)]
            _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload,
                                              writes_every=37)
            res["writes_during"] = (payload, data_packets(mark), done)
            # Flush mid-fill: 300 bytes, flush, 3 bytes, OUT carries exactly the 3.
            await put(ctx, [rng.randrange(256) for _ in range(300)])
            await flush(ctx, depth + 8)
            res["flushed_pending"] = int(ctx.get(ctrl.tx_pending))
            payload = [rng.randrange(256) for _ in range(3)]
            _, _, done, mark = await transact(ctx, TransferType.OUT, NOBODY, 1, payload=payload)
            res["after_flush"] = (payload, data_packets(mark), done)
            # Last: a bus reset 100 bytes into a 512-byte OUT. The bare engine does not return
            # to idle (FIFO-independent; the shim's watchdog resets it): trace comparison only.
            await transact(ctx, TransferType.OUT, NOBODY, 1, payload=[rng.randrange(256)
                                                                   for _ in range(depth)],
                           bus_reset_after=100)
            for _ in range(2 * TRANSACTION_CYCLES):
                await ctx.tick()
            res["idle_after_reset"] = int(ctx.get(ctrl.status.idle))

        sim = Simulator(bench.module)
        sim.add_clock(1 / 60e6)
        sim.add_process(sampler)
        sim.add_testbench(tb)
        sim.run()
        return trace, res
    finally:
        sie_module.fifo = saved


def run_overflow(fifo_ns, depth):
    """An IN of 512 into a `depth`-byte RX FIFO with nobody reading, then drained."""
    saved = sie_module.fifo
    sie_module.fifo = fifo_ns
    try:
        bench = Bench(full_speed_only=False, fifo_depth=depth)
        ctrl, res = bench.ctrl, {}

        async def tick_n(ctx, n):
            for _ in range(n):
                await ctx.tick()

        async def xfer(ctx, type, endpoint, payload=()):
            ctx.set(ctrl.xfer.type, type)
            ctx.set(ctrl.xfer.dev_addr, 0)
            ctx.set(ctrl.xfer.ep_addr, endpoint)
            ctx.set(ctrl.xfer.data_pid, DataPID.DATA0)
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
                    return ctx.get(ctrl.status.response)
            return None

        async def tb(ctx):
            for _ in range(BRINGUP_CYCLES):
                await ctx.tick()
                if ctx.get(ctrl.status.idle):
                    break
            ctx.set(ctrl.rxs.ready, 1)
            await xfer(ctx, TransferType.SETUP, 0, [0, 9, 1, 0, 0, 0, 0, 0])
            await xfer(ctx, TransferType.IN, 0)
            await tick_n(ctx, 8)
            ctx.set(ctrl.rxs.ready, 0)
            res["response"] = await xfer(ctx, TransferType.IN, BULK_IN_ENDPOINT)
            got, quiet = [], 0
            ctx.set(ctrl.rxs.ready, 1)
            while quiet < QUIET_CYCLES:
                if ctx.get(ctrl.rxs.valid):
                    got.append(int(ctx.get(ctrl.rxs.payload)))
                    quiet = 0
                else:
                    quiet += 1
                await ctx.tick()
            res["drained"] = got

        sim = Simulator(bench.module)
        sim.add_clock(1 / 60e6)
        sim.add_testbench(tb)
        sim.run()
        return res
    finally:
        sie_module.fifo = saved


def counting(data):
    return all((b - a) % 256 == 1 for a, b in zip(data, data[1:]))


def absolute_checks(res, depth):
    """(name, ok, detail) for one engine run: what must hold whatever FIFO is inside."""
    out = []

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
                    ok and done and resp == TransferResponse.TIMEOUT, why or f"resp {resp}"))
    payload, packets, done, pending = res["same_cycle_start"]
    ok, why = packet_ok(payload[:-1], packets)
    out.append(("OUT with the last write in the start cycle: 4 of 5 bytes sent, the 5th "
                "drained (tx_pending 0 after)", ok and done and pending == 0,
                why or f"pending {pending}"))
    for name, (got, resp, done) in res["in"].items():
        out.append((f"IN 512 drained at rate {name}: 512 counting bytes, ACK",
                    done and resp == TransferResponse.ACK and len(got) == 512 and counting(got),
                    f"resp {resp}, {len(got)} bytes, counting {counting(got)}"))
    payload, packets, done = res["writes_during"]
    ok, why = packet_ok(payload, packets)
    out.append(("OUT 64 with TX writes every 37 cycles during the transfer: the 64 on the wire",
                ok and done, why))
    out.append(("TX flush mid-fill empties the FIFO (tx_pending 0)", res["flushed_pending"] == 0,
                f"tx_pending {res['flushed_pending']}"))
    payload, packets, done = res["after_flush"]
    ok, why = packet_ok(payload, packets)
    out.append(("OUT 3 after the flush: exactly those 3 bytes", ok and done, why))
    return out


def section_engine(checks, seed, negative):
    depth = 512
    runs = {}
    for name in ("amaranth", "ours") + (("same-cycle-wired", "buffered") if negative else ()):
        runs[name] = run_engine(FIFOS[name], depth, seed)
    ref_trace, _ = runs["amaranth"]
    for name, (trace, res) in runs.items():
        results = absolute_checks(res, depth)
        if name in ("amaranth", "ours"):
            for what, ok, detail in results:
                checks.check(f"engine on {name} FIFO: {what}", ok, detail)
        else:
            failed = [what for what, ok, _ in results if not ok]
            if name == "same-cycle-wired":
                checks.check("engine-negative: on the same-cycle-wired FIFO at least one absolute "
                             "check fails", bool(failed), "every check passed",
                             measurement=f"{len(failed)} failed, first: {failed[:2]}")
            else:
                checks.note(f"{name}: {len(failed)} absolute checks fail {failed[:2]}")
        if name == "amaranth":
            checks.note(f"bare engine idle after a bus reset mid-OUT: {res['idle_after_reset']}")
            continue
        diff = next((i for i, (a, b) in enumerate(zip(trace, ref_trace)) if a != b), None)
        if diff is None and len(trace) != len(ref_trace):
            diff = min(len(trace), len(ref_trace))
        if name == "ours":
            checks.check(f"engine: per-cycle trace on ours == on amaranth SyncFIFO "
                         f"({len(trace)} cycles: tx_pending, txs.ready, rxs, status, UTMI TX)",
                         diff is None, f"first difference at cycle {diff}: "
                         f"{trace[diff] if diff is not None and diff < len(trace) else '-'} vs "
                         f"{ref_trace[diff] if diff is not None and diff < len(ref_trace) else '-'}")
        else:
            checks.check(f"engine-negative: the trace on {name} differs from amaranth SyncFIFO",
                         diff is not None, "identical trace: the lockstep cannot see it",
                         measurement=f"first difference at cycle {diff}")

    for name in ("amaranth", "ours"):
        res = run_overflow(FIFOS[name], 500)
        got = res["drained"]
        checks.check(f"engine on {name} FIFO, depth 500: IN 512 with nobody reading is "
                     "RX_OVERFLOW and 500 counting bytes drain",
                     res["response"] == TransferResponse.RX_OVERFLOW and len(got) == 500
                     and counting(got), f"resp {res['response']}, {len(got)} bytes")


SECTIONS = ("fifo", "negative", "engine", "engine-negative")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=619)
    parser.add_argument("--only", nargs="*", choices=SECTIONS, default=SECTIONS)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    checks = Checks(emit)
    emit(f"block-RAM SIE FIFO vs amaranth SyncFIFO (seed {args.seed})")
    if "fifo" in args.only:
        section_fifo(checks, rng)
    if "negative" in args.only:
        section_negative(checks, rng)
    if "engine" in args.only or "engine-negative" in args.only:
        section_engine(checks, args.seed, "engine-negative" in args.only)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
