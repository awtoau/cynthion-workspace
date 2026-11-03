#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""ULPI bridge simulations (#581): option (a) AsyncBridge and option (b) RatioBridge.

- Byte-exact both directions with gaps, back-pressure and long stalls; reset mid-stream.
- ULPI-rate stream: RX ignores ready (ULPI cannot wait) and must not overflow; TX must
  have no bubble inside a packet (ULPI TX cannot underrun).
- (b): phase offsets and wrong ratios must be refused or faulted; a relock out of phase must fault.
- Negative controls must FAIL: byte-swap mutation (both options), phase check disabled.
- Log: tmp/logs/ulpi_bridge_sim.log; results: tmp/ulpi_bridge/sim.json. Usage: [--only NAME]
"""

import argparse
import json
import logging
import os
import random
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, cwd=Path(__file__).parent, check=True).stdout.strip()
        return Path(out)
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent


ROOT = repo_root()
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import ClockDomain, Elaboratable, Module  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from ulpi_bridge import AsyncBridge, RatioBridge  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "ulpi_bridge_sim.log"
OUT = ROOT / "tmp" / "ulpi_bridge" / "sim.json"

TICK = 1042e-12         # T/8; fast period 8 ticks (~119.96 MHz), usb 16 ticks: exact 2:1
NOMINAL_OFFSET = 2      # usb rising edge T/4 after a fast rising edge (ratio_bridge.py)
QUALIFY_LIMIT = 200     # fast cycles: 2 warm-up + 16 usb periods (32) + margin x1.25 -> 43; use 200
FAULT_LIMIT = 8         # fast cycles from a phase jump to `fault`: one usb period (2) + pipeline (3)


class Harness(Elaboratable):
    def __init__(self, dut):
        self.dut = dut
        self.usb = ClockDomain("usb")
        self.fast = ClockDomain("fast")

    def elaborate(self, platform):
        m = Module()
        m.domains += [self.usb, self.fast]
        m.submodules.dut = self.dut
        return m


class Clocks:
    """Locked mode: both clocks from one tick grid with `offset` (ticks of T/8). Free: own periods."""

    def __init__(self, h, *, offset=NOMINAL_OFFSET, fast_ps=None, usb_ps=None):
        self.h, self.offset, self.fast_ps, self.usb_ps = h, offset, fast_ps, usb_ps
        self.now = 0        # tick of the current clock levels (locked mode)

    def ticks_to_usb_edge(self):
        return (self.offset - self.now) % 16

    def add(self, sim):
        if self.fast_ps is None:
            sim.add_process(self._locked)
        else:
            sim.add_process(self._free(self.h.fast.clk, self.fast_ps))
            sim.add_process(self._free(self.h.usb.clk, self.usb_ps))

    def _levels(self, t):
        return int(t % 8 < 4), int((t - self.offset) % 16 < 8)

    async def _locked(self, ctx):
        t = 0
        while True:
            f, u = self._levels(t)
            ctx.set(self.h.fast.clk, f)
            ctx.set(self.h.usb.clk, u)
            self.now = t
            n = 1
            while self._levels(t + n) == (f, u):
                n += 1
            await ctx.delay(n * TICK)
            t += n

    @staticmethod
    def _free(clk, period_ps):
        async def proc(ctx):
            half = period_ps / 2 * 1e-12
            while True:
                ctx.set(clk, 1)
                await ctx.delay(half)
                ctx.set(clk, 0)
                await ctx.delay(half)
        return proc


def make_packets(rng, total, sizes=(1, 2, 3, 4, 5, 7, 8, 511, 512, 513, 1024)):
    pkts = [bytes(rng.randrange(256) for _ in range(s)) for s in sizes]
    while sum(map(len, pkts)) < total:
        pkts.append(bytes(rng.randrange(256) for _ in range(rng.randint(1, 1024))))
    rng.shuffle(pkts)
    return pkts


def to_words(pkt):
    out = []
    for i in range(0, len(pkt), 4):
        chunk = pkt[i:i + 4]
        last = i + 4 >= len(pkt)
        out.append(int.from_bytes(chunk, "little") | (len(chunk) - 1) << 32 | int(last) << 34)
    return out


class Traffic:
    """Sources, sinks and scoreboards for both directions."""

    def __init__(self, h, rng, *, total, p_rx_src=0.7, p_rx_sink=0.7, p_tx_src=0.7,
                 p_tx_sink=0.7, rx_ulpi=False, rx_stall=0, tx_stall=0):
        self.h, self.d, self.rng = h, h.dut, rng
        self.p = dict(rx_src=p_rx_src, rx_sink=p_rx_sink, tx_src=p_tx_src, tx_sink=p_tx_sink)
        self.rx_ulpi, self.rx_stall, self.tx_stall = rx_ulpi, rx_stall, tx_stall
        self.rx_pkts = make_packets(rng, total)
        self.tx_pkts = make_packets(rng, total)
        self.rx_sent, self.rx_got, self.tx_sent, self.tx_got = [], [], [], []
        self.rx_part, self.tx_part = bytearray(), bytearray()
        self.paused = False
        self.gate = getattr(h.dut, "up", None)      # (b): wait for qualification
        self.idle = {"rx": False, "tx": False}
        self.tx_bubbles = 0

    def flush(self):
        """After a reset: everything before it is discarded on both sides."""
        self.rx_sent.clear(), self.rx_got.clear(), self.tx_sent.clear(), self.tx_got.clear()
        self.rx_part.clear(), self.tx_part.clear()

    def done(self):
        return (not self.rx_pkts and not self.tx_pkts and len(self.rx_got) == len(self.rx_sent)
                and len(self.tx_got) == len(self.tx_sent))

    async def rx_source(self, ctx):
        d = self.d
        while True:
            if self.paused or not self.rx_pkts or (self.gate is not None and not ctx.get(self.gate)):
                self.idle["rx"] = True
                ctx.set(d.rx_valid, 0)
                await ctx.tick("usb")
                continue
            self.idle["rx"] = False
            pkt = self.rx_pkts.pop(0)
            i = 0
            while i < len(pkt):
                go = self.rx_ulpi or self.rng.random() < self.p["rx_src"]
                ctx.set(d.rx_valid, int(go))
                ctx.set(d.rx_data, pkt[i])
                ctx.set(d.rx_last, int(i == len(pkt) - 1))
                _, rst, ready = await ctx.tick("usb").sample(d.rx_ready)
                if go and (ready or self.rx_ulpi):
                    i += 1
            ctx.set(d.rx_valid, 0)
            self.rx_sent.append(pkt)

    async def rx_sink(self, ctx):
        d = self.d
        stall_at = 3000
        n = 0
        while True:
            n += 1
            stalled = self.rx_stall and stall_at <= n < stall_at + self.rx_stall
            rdy = (not stalled) and self.rng.random() < self.p["rx_sink"]
            ctx.set(d.rxw_ready, int(rdy))
            _, _, valid, w = await ctx.tick("fast").sample(d.rxw_valid, d.rxw_word)
            if valid and rdy:
                cnt = ((w >> 32) & 3) + 1
                self.rx_part += (w & 0xFFFFFFFF).to_bytes(4, "little")[:cnt]
                if w >> 34:
                    self.rx_got.append(bytes(self.rx_part))
                    self.rx_part.clear()

    async def tx_source(self, ctx):
        d = self.d
        while True:
            if self.paused or not self.tx_pkts:
                self.idle["tx"] = True
                ctx.set(d.txw_valid, 0)
                await ctx.tick("fast")
                continue
            self.idle["tx"] = False
            pkt = self.tx_pkts.pop(0)
            words = to_words(pkt)
            i = 0
            while i < len(words):
                go = self.rng.random() < self.p["tx_src"]
                ctx.set(d.txw_valid, int(go))
                ctx.set(d.txw_word, words[i])
                _, _, ready = await ctx.tick("fast").sample(d.txw_ready)
                if go and ready:
                    i += 1
            ctx.set(d.txw_valid, 0)
            self.tx_sent.append(pkt)

    async def tx_sink(self, ctx):
        d = self.d
        stall_at = 1500
        n = 0
        while True:
            n += 1
            stalled = self.tx_stall and stall_at <= n < stall_at + self.tx_stall
            rdy = (not stalled) and self.rng.random() < self.p["tx_sink"]
            ctx.set(d.tx_ready, int(rdy))
            _, _, valid, data, last = await ctx.tick("usb").sample(d.tx_valid, d.tx_data,
                                                                   d.tx_last)
            if self.tx_part and not valid and not self.paused:
                self.tx_bubbles += 1
            if valid and rdy:
                self.tx_part.append(data)
                if last:
                    self.tx_got.append(bytes(self.tx_part))
                    self.tx_part.clear()

    def add(self, sim):
        for tb in (self.rx_source, self.rx_sink, self.tx_source, self.tx_sink):
            sim.add_testbench(tb, background=True)

    def compare(self):
        errs = []
        for name, sent, got in (("rx", self.rx_sent, self.rx_got), ("tx", self.tx_sent, self.tx_got)):
            if len(sent) != len(got):
                errs.append(f"{name}: {len(sent)} packets sent, {len(got)} received")
            bad = sum(a != b for a, b in zip(sent, got))
            if bad:
                first = next(i for i, (a, b) in enumerate(zip(sent, got)) if a != b)
                errs.append(f"{name}: {bad} packets differ (first #{first}: "
                            f"{len(sent[first])} B sent, {len(got[first])} B got)")
        return errs


def build(option, **kw):
    return AsyncBridge(**kw) if option == "a" else RatioBridge(**kw)


def run(sim, h, until, limit, clk="fast"):
    """Run until `until()` or `limit` fast cycles; returns cycles used (None on expiry)."""
    result = {}

    async def bench(ctx):
        for n in range(limit):
            await ctx.tick(clk)
            if until():
                result["n"] = n + 1
                return
    sim.add_testbench(bench)
    sim.run()
    return result.get("n")


# ---- cases --------------------------------------------------------------------------------

def launch_monitor(sim, h, clocks, state):
    """(b): every fast-launched, usb-read register may change only >= T/2 before a usb edge."""
    regs = (h.dut.to_usb.data, h.dut.to_usb.t_tog, h.dut.to_fast.a_tog)

    async def mon(ctx):
        async for _, _, *before in ctx.tick("fast").sample(*regs):
            if any(ctx.get(r) != b for r, b in zip(regs, before)):
                state["launches"] = state.get("launches", 0) + 1
                if clocks.ticks_to_usb_edge() < 4:
                    state["unsafe"] = state.get("unsafe", 0) + 1
    sim.add_testbench(mon, background=True)


def case_traffic(option, seed=1, swap=False, gate=True, **tkw):
    """Random traffic, gaps, back-pressure, long stalls both sides; byte-exact."""
    rng = random.Random(seed)
    kw = dict(swap=swap) if option == "a" else dict(swap=swap, gate=gate)
    h = Harness(build(option, **kw))
    sim = Simulator(h)
    clocks = Clocks(h)
    clocks.add(sim)
    mon = {}
    if option == "b":
        launch_monitor(sim, h, clocks, mon)
    t = Traffic(h, rng, total=tkw.pop("total", 12_000), **tkw)
    t.add(sim)
    total = sum(map(len, t.rx_pkts)) + sum(map(len, t.tx_pkts))
    # Per direction ~total/2 bytes at <= p_src*p_sink per usb cycle (2 fast); x1.25 + stalls.
    pmin = min(t.p.values())
    limit = int(1.25 * (total / 2 / (pmin * pmin) * 2 + 2 * max(t.rx_stall, t.tx_stall) + 400))
    n = run(sim, h, t.done, limit)
    errs = t.compare()
    if n is None:
        errs.append(f"timed out after {limit} fast cycles: rx {len(t.rx_got)}/{len(t.rx_sent)} "
                    f"tx {len(t.tx_got)}/{len(t.tx_sent)} packets")
    if mon.get("unsafe"):
        errs.append(f"{mon['unsafe']} fast-launched usb-read register changes < T/2 before a usb edge")
    return dict(errors=errs, fast_cycles=n, bytes=total, rx_packets=len(t.rx_got),
                tx_packets=len(t.tx_got), **mon)


def case_stream(option, seed=2, swap=False):
    """ULPI rate: RX valid every cycle ignoring ready; TX sink always ready; no overflow/bubble."""
    rng = random.Random(seed)
    h = Harness(build(option, swap=swap))
    sim = Simulator(h)
    Clocks(h).add(sim)
    t = Traffic(h, rng, total=8_000, p_rx_src=1.0, p_rx_sink=1.0, p_tx_src=1.0, p_tx_sink=1.0,
                rx_ulpi=True)
    t.add(sim)
    state = {}

    async def watch(ctx):
        while True:
            _, _, ovf = await ctx.tick("usb").sample(h.dut.rx_overflow)
            if ovf:
                state["ovf"] = True
    sim.add_testbench(watch, background=True)
    total = sum(map(len, t.rx_pkts)) + sum(map(len, t.tx_pkts))
    limit = int(1.25 * (total / 2 * 2 + 400))     # 1 byte per usb cycle each way, run concurrently
    n = run(sim, h, t.done, limit)
    errs = t.compare()
    if n is None:
        errs.append(f"timed out after {limit} fast cycles")
    if state.get("ovf"):
        errs.append("rx_overflow set at ULPI rate")
    if t.tx_bubbles:
        errs.append(f"{t.tx_bubbles} TX bubbles inside packets (ULPI TX underrun)")
    return dict(errors=errs, fast_cycles=n, bytes=total)


def case_latency(option):
    """Empty bridge: one word each way; fast cycles from the input edge to valid at the output."""
    h = Harness(build(option))
    sim = Simulator(h)
    Clocks(h).add(sim)
    d = h.dut
    lat = dict(rx=[], tx=[])

    async def bench(ctx):
        ctx.set(d.rxw_ready, 1)
        ctx.set(d.tx_ready, 1)
        for _ in range(200):                    # qualification (b) and FIFO reset release
            await ctx.tick("fast")
        for skew in range(4):
            for _ in range(skew):
                await ctx.tick("fast")
            await ctx.tick("usb")
            ctx.set(d.rx_valid, 1), ctx.set(d.rx_last, 1), ctx.set(d.rx_data, 0x5A)
            await ctx.tick("usb")               # byte (and last) accepted at this edge
            ctx.set(d.rx_valid, 0)
            for n in range(1, 64):
                _, _, v = await ctx.tick("fast").sample(d.rxw_valid)
                if v:
                    lat["rx"].append(n)
                    break
            for _ in range(skew):
                await ctx.tick("fast")
            ctx.set(d.txw_valid, 1), ctx.set(d.txw_word, 0x5A | 1 << 34)
            await ctx.tick("fast")              # word accepted at this edge
            ctx.set(d.txw_valid, 0)
            for n in range(1, 64):
                _, _, v = await ctx.tick("fast").sample(d.tx_valid)
                if v:
                    lat["tx"].append(n)
                    break
            for _ in range(20):
                await ctx.tick("fast")
    sim.add_testbench(bench)
    sim.run()
    errs = [] if len(lat["rx"]) == len(lat["tx"]) == 4 else [f"missing words: {lat}"]
    return dict(errors=errs, rx_fast_cycles=sorted(lat["rx"]), tx_fast_cycles=sorted(lat["tx"]))


def case_overflow(option):
    """RX sink never ready: ULPI-style source must hit rx_overflow; report capacity in bytes."""
    rng = random.Random(3)
    h = Harness(build(option))
    sim = Simulator(h)
    Clocks(h).add(sim)
    d = h.dut
    state = dict(accepted=0, ovf_at=None)

    async def src(ctx):
        ctx.set(d.rxw_ready, 0)
        gate = getattr(d, "up", None)
        while gate is not None and not ctx.get(gate):
            await ctx.tick("usb")
        for i in range(8192):
            ctx.set(d.rx_valid, 1)
            ctx.set(d.rx_data, rng.randrange(256))
            ctx.set(d.rx_last, int(i % 512 == 511))
            _, _, ready, ovf = await ctx.tick("usb").sample(d.rx_ready, d.rx_overflow)
            if ready and state["ovf_at"] is None:
                state["accepted"] += 1
            if ovf and state["ovf_at"] is None:
                state["ovf_at"] = i
        ctx.set(d.rx_valid, 0)
    sim.add_testbench(src)
    sim.run_until(8192 * 16 * TICK + 400 * 8 * TICK)
    errs = [] if state["ovf_at"] is not None else ["rx_overflow never set"]
    return dict(errors=errs, capacity_bytes=state["accepted"])


def case_reset(option, seed=4):
    """Reset both domains mid-stream; output after reset must equal input after reset exactly."""
    rng = random.Random(seed)
    h = Harness(build(option))
    sim = Simulator(h)
    Clocks(h).add(sim)
    t = Traffic(h, rng, total=12_000)
    t.add(sim)
    state = {}

    async def ctl(ctx):
        for _ in range(6000):
            await ctx.tick("fast")
        t.p["rx_sink"] = 0.0            # RX words pile up in the bridge: reset hits both FIFOs full-ish
        t.paused = True
        while not (t.idle["rx"] and t.idle["tx"]):
            await ctx.tick("fast")
        ctx.set(h.usb.rst, 1)
        ctx.set(h.fast.rst, 1)
        for _ in range(20):
            await ctx.tick("fast")
        state["dropped"] = (len(t.rx_sent) - len(t.rx_got), len(t.tx_sent) - len(t.tx_got))
        t.flush()
        ctx.set(h.usb.rst, 0)
        ctx.set(h.fast.rst, 0)
        t.p["rx_sink"] = 0.7
        t.paused = False
        state["released"] = True
    sim.add_testbench(ctl, background=True)
    limit = int(1.25 * (12_000 * 2 / 0.49 + 6000 + 400))
    n = run(sim, h, lambda: state.get("released") and t.done(), limit)
    errs = t.compare()
    if n is None:
        errs.append(f"timed out after {limit} fast cycles")
    return dict(errors=errs, fast_cycles=n, after_reset_packets=(len(t.rx_got), len(t.tx_got)),
                in_flight_at_reset=state.get("dropped"))


def case_phase(offset=None, fast_ps=None, usb_ps=None, expect="refuse", check=True,
               jump_to=None):
    """(b) only. expect: 'ok' (qualifies, never faults), 'refuse' (never ok), 'fault'."""
    h = Harness(RatioBridge(check=check))
    sim = Simulator(h)
    clocks = Clocks(h, offset=NOMINAL_OFFSET if offset is None else offset,
                    fast_ps=fast_ps, usb_ps=usb_ps)
    clocks.add(sim)
    chk = h.dut.check
    st = dict(ok_at=None, fault_at=None, ever_ok=False, jump_at=None, faults=0)
    horizon = 4000

    async def mon(ctx):
        for n in range(horizon):
            _, _, ok, fault = await ctx.tick("fast").sample(chk.ok, chk.fault)
            if ok and st["ok_at"] is None:
                st["ok_at"] = n
            st["ever_ok"] |= bool(ok)
            if fault and st["fault_at"] is None:
                st["fault_at"] = n
            if jump_to is not None and st["ok_at"] is not None and st["jump_at"] is None \
                    and n >= st["ok_at"] + 500:
                clocks.offset = jump_to
                st["jump_at"] = n
    sim.add_testbench(mon)
    sim.run()
    errs = []
    if expect == "ok":
        if st["ok_at"] is None or st["ok_at"] > QUALIFY_LIMIT:
            errs.append(f"not qualified within {QUALIFY_LIMIT} fast cycles (ok_at={st['ok_at']})")
        if st["fault_at"] is not None:
            errs.append(f"fault at cycle {st['fault_at']}")
    elif expect == "refuse":
        if st["ever_ok"]:
            errs.append(f"bridge enabled (ok at cycle {st['ok_at']}) on a bad clock")
    elif expect == "fault":
        if st["ever_ok"] and st["fault_at"] is None:
            errs.append("ran then never faulted")
        if jump_to is not None:
            if st["jump_at"] is None:
                errs.append("never qualified before the jump")
            elif st["fault_at"] is None or st["fault_at"] - st["jump_at"] > FAULT_LIMIT:
                errs.append(f"fault {st['fault_at']} not within {FAULT_LIMIT} of jump {st['jump_at']}")
    return dict(errors=errs, ok_at=st["ok_at"], fault_at=st["fault_at"], jump_at=st["jump_at"])


def cases():
    c = []
    for opt in ("a", "b"):
        c += [(f"{opt}/traffic", True, case_traffic, dict(option=opt)),
              (f"{opt}/traffic-stalls", True, case_traffic,
               dict(option=opt, seed=11, rx_stall=3000, tx_stall=3000, p_rx_sink=0.5,
                    p_tx_sink=0.5)),
              (f"{opt}/stream-ulpi-rate", True, case_stream, dict(option=opt)),
              (f"{opt}/overflow", True, case_overflow, dict(option=opt)),
              (f"{opt}/latency", True, case_latency, dict(option=opt)),
              (f"{opt}/reset-mid-stream", True, case_reset, dict(option=opt)),
              (f"{opt}/NEG-byte-swap", False, case_traffic, dict(option=opt, swap=True))]
    for off in range(8):
        # 0 and T/2 are the window edges: either answer is right (hardware: metastable there)
        exp = "ok" if off in (0, 1, 2, 3) else "refuse"
        role = None if off in (0, 4) else True
        c.append((f"b/phase-offset-{off}T8", role, case_phase, dict(offset=off, expect=exp)))
    T = 8 * 1042
    for name, fps, ups in (("fast100-usb60", 10_000, 2 * T), ("fast125-usb60", 8_000, 2 * T),
                           ("1to1", T, T), ("3to1", T, 3 * T),
                           ("drift-fast+0.4pct", T - 33, 2 * T)):
        c.append((f"b/ratio-{name}", True, case_phase,
                  dict(fast_ps=fps, usb_ps=ups, expect="fault" if "drift" in name else "refuse")))
    c.append(("b/relock-out-of-phase", True, case_phase, dict(expect="fault", jump_to=5)))
    c.append(("b/NEG-safe-gate-off", False, case_traffic, dict(option="b", gate=False)))
    c.append(("b/NEG-check-off-offset5", False, case_phase,
              dict(offset=5, expect="refuse", check=False)))
    c.append(("b/NEG-check-off-relock", False, case_phase,
              dict(expect="fault", jump_to=5, check=False)))
    return c


def _run_case(name, fn, kw):
    try:
        res = fn(**kw)
    except Exception as e:  # report as a failure, not a crash of the pool
        res = dict(errors=[f"exception: {type(e).__name__}: {e}"])
    return name, res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="substring filter on case names")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler()])
    log = logging.getLogger()
    todo = [c for c in cases() if not args.only or args.only in c[0]]
    expect = {name: positive for name, positive, _, _ in todo}
    results = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futs = [pool.submit(_run_case, name, fn, kw) for name, _, fn, kw in todo]
        for fut in as_completed(futs):
            name, res = fut.result()
            passed = not res["errors"]
            good = expect[name] is None or passed == expect[name]
            res.update(passed=passed, expected_pass=expect[name], as_expected=good)
            results[name] = res
            tag = "PASS" if passed else "FAIL"
            want = {True: "", False: " (negative control: must FAIL)",
                    None: " (window edge: informational)"}[expect[name]]
            log.info("%-28s %s%s %s %s", name, tag, want, "ok" if good else "UNEXPECTED",
                     {k: v for k, v in res.items() if k not in ("passed", "expected_pass",
                                                                 "as_expected")})
    n_pos = sum(v is True for v in expect.values())
    n_neg = sum(v is False for v in expect.values())
    pos_pass = sum(r["passed"] for n, r in results.items() if expect[n] is True)
    neg_fail = sum(not r["passed"] for n, r in results.items() if expect[n] is False)
    log.info("positive cases: %d/%d pass; negative controls: %d/%d fail as required; "
             "%d informational", pos_pass, n_pos, neg_fail, n_neg, len(todo) - n_pos - n_neg)
    OUT.write_text(json.dumps(dict(sorted(results.items())), indent=1, default=str))
    return 0 if all(r["as_expected"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
