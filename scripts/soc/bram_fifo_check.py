#!/usr/bin/env python3
#
# Small SoC FIFOs and buffers live in block RAM, not distributed RAM.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks for `gateware/soc/peripherals/bram_fifo.py` and for its users.

    ./scripts/soc/bram_fifo_check.py            # lockstep + standalone mapping + pinned memories
    ./scripts/soc/bram_fifo_check.py --synth    # also synthesise the peripherals (minutes)

Sections:
- lockstep: each Bram* class against the amaranth class it wraps, every output every cycle,
  random stimulus (sync: depths 8, 16, 64; async: write/read clocks 10 ns / 17 ns, depth 16).
- mapping: each Bram* class alone through `synth_ecp5`: no TRELLIS_DPR16X4, at least one DP16KD.
- pinned: every memory in the in-scope modules shallower than `DEEP` words carries
  `ram_style = "block"`; deeper ones reach block RAM unaided.
- peripherals (`--synth`): in-scope peripherals through `synth_ecp5` have no TRELLIS_DPR16X4.
"""

import argparse
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "soc"))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402

from amaranth import ClockDomain, Elaboratable, Module  # noqa: E402
from amaranth.back import verilog  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.hdl._mem import MemoryInstance  # noqa: E402
from amaranth.lib import fifo as stock  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from peripherals import bram_fifo as bram  # noqa: E402

BUILD = ROOT / "tmp" / "bram-fifo-check"
DEEP = 128          # words; yosys picks block RAM unaided from here (usb_host's 128 x 10)

SYNC = ((bram.BramSyncFIFOBuffered, stock.SyncFIFOBuffered, 8, 18),
        (bram.BramSyncFIFOBuffered, stock.SyncFIFOBuffered, 16, 8),
        (bram.BramSyncFIFOBuffered, stock.SyncFIFOBuffered, 64, 8))
ASYNC = ((bram.BramAsyncFIFO, stock.AsyncFIFO, 16, 38),
         (bram.BramAsyncFIFOBuffered, stock.AsyncFIFOBuffered, 16, 8),
         (bram.BramAsyncFIFOBuffered, stock.AsyncFIFOBuffered, 32, 18))


def lockstep_sync(rng, ours_cls, ref_cls, depth, width, n_cycles=4000):
    m = Module()
    m.submodules.ref = ref = ref_cls(width=width, depth=depth)
    m.submodules.dut = dut = ours_cls(width=width, depth=depth)
    mism, seen = [], {"full": 0, "empty_after_data": 0}

    async def tb(ctx):
        p_w = p_r = 0.5
        had_data = False
        for cycle in range(n_cycles):
            if cycle % rng.randrange(50, 400) == 0:
                p_w, p_r = rng.choice((0, 0.1, 0.5, 0.9, 1)), rng.choice((0, 0.1, 0.5, 0.9, 1))
            w_en, r_en, data = rng.random() < p_w, rng.random() < p_r, rng.randrange(1 << width)
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
            seen["full"] += level == depth
            seen["empty_after_data"] += had_data and level == 0
            had_data |= level != 0
            await ctx.tick()

    sim = Simulator(m)
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()
    return mism, seen


def lockstep_async(rng, ours_cls, ref_cls, depth, width, n_cycles=3000):
    m = Module()
    m.domains.w = ClockDomain("w")
    m.domains.r = ClockDomain("r")
    kw = dict(width=width, depth=depth, w_domain="w", r_domain="r")
    m.submodules.ref = ref = ref_cls(**kw)
    m.submodules.dut = dut = ours_cls(**kw)
    mism, seen = [], {"full": 0, "reads": 0}

    async def writer(ctx):
        p = 0.5
        for cycle in range(n_cycles):
            if cycle % 200 == 0:
                p = rng.choice((0, 0.2, 0.6, 1))
            w_en, data = rng.random() < p, rng.randrange(1 << width)
            for f in (ref, dut):
                ctx.set(f.w_en, w_en)
                ctx.set(f.w_data, data)
            for name in ("w_rdy", "w_level"):
                if ctx.get(getattr(ref, name)) != ctx.get(getattr(dut, name)):
                    mism.append((cycle, name))
            seen["full"] += not ctx.get(ref.w_rdy)
            await ctx.tick("w")

    async def reader(ctx):
        p = 0.5
        for cycle in range(n_cycles):
            if cycle % 200 == 0:
                p = rng.choice((0, 0.2, 0.6, 1))
            r_en = rng.random() < p
            for f in (ref, dut):
                ctx.set(f.r_en, r_en)
            for name in ("r_rdy", "r_level"):
                if ctx.get(getattr(ref, name)) != ctx.get(getattr(dut, name)):
                    mism.append((cycle, name))
            if ctx.get(ref.r_rdy) and ctx.get(ref.r_data) != ctx.get(dut.r_data):
                mism.append((cycle, "r_data"))
            seen["reads"] += r_en and ctx.get(ref.r_rdy)
            await ctx.tick("r")

    sim = Simulator(m)
    sim.add_clock(1.0e-8, domain="w")
    sim.add_clock(1.7e-8, domain="r")
    sim.add_testbench(writer)
    sim.add_testbench(reader)
    sim.run()
    return mism, seen


def section_lockstep(checks, rng):
    for ours, ref, depth, width in SYNC:
        mism, seen = lockstep_sync(rng, ours, ref, depth, width)
        checks.check(f"{ours.__name__} {width}x{depth}: == {ref.__name__} every cycle",
                     not mism, f"{len(mism)} mismatches, first {mism[:4]}")
        checks.check(f"{ours.__name__} {width}x{depth}: stimulus reached full and empty-after-data",
                     all(seen.values()), f"{seen}")
    for ours, ref, depth, width in ASYNC:
        mism, seen = lockstep_async(rng, ours, ref, depth, width)
        checks.check(f"{ours.__name__} {width}x{depth}: == {ref.__name__} every cycle",
                     not mism, f"{len(mism)} mismatches, first {mism[:4]}")
        checks.check(f"{ours.__name__} {width}x{depth}: stimulus reached full and read data",
                     all(seen.values()), f"{seen}")


# ---------------------------------------------------------------- synthesis

def cells_of(name, verilog_text):
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / f"{name}.v"
    source.write_text(verilog_text)
    proc = subprocess.run(
        ["yosys", "-p", f"read_verilog {source}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True)
    (BUILD / f"{name}.yosys.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-1500:])
    cells = {}
    for line in proc.stdout.rsplit(f"=== {name} ===", 1)[-1].splitlines():
        match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if match:
            cells[match.group(2)] = int(match.group(1))
    return cells


class Wrapped(Elaboratable):
    """A FIFO class in its own top with both clock domains, every port exposed."""

    def __init__(self, cls, width, depth, asynchronous):
        self.fifo = cls(width=width, depth=depth, **(
            dict(w_domain="w", r_domain="r") if asynchronous else {}))
        self.asynchronous = asynchronous
        self.domains = [ClockDomain("w"), ClockDomain("r")] if asynchronous else [ClockDomain("sync")]

    def elaborate(self, platform):
        m = Module()
        for domain in self.domains:
            m.domains += domain
        m.submodules.fifo = self.fifo
        return m

    def ports(self):
        f = self.fifo
        ports = [f.w_data, f.w_en, f.w_rdy, f.r_data, f.r_en, f.r_rdy]
        for domain in self.domains:
            ports += [domain.clk, domain.rst]
        return ports


def section_mapping(checks):
    cases = [(ours, depth, width, False) for ours, _, depth, width in SYNC]
    cases += [(ours, depth, width, True) for ours, _, depth, width in ASYNC]
    for ours, depth, width, asynchronous in cases:
        name = f"{ours.__name__}_{width}x{depth}"
        dut = Wrapped(ours, width, depth, asynchronous)
        cells = cells_of(name, verilog.convert(dut, name=name, ports=dut.ports()))
        dpr, ebr = cells.get("TRELLIS_DPR16X4", 0), cells.get("DP16KD", 0)
        checks.check(f"{name}: no distributed RAM, block RAM used",
                     dpr == 0 and ebr >= 1, f"DPR16X4 {dpr}, DP16KD {ebr}",
                     measurement=f"DPR16X4 {dpr}, DP16KD {ebr}")


# ---------------------------------------------------------------- the users

def memories(fragment, path=""):
    for sub, name, _ in fragment.subfragments:
        here = f"{path}.{name}" if name else path
        if isinstance(sub, MemoryInstance):
            yield here, sub
        yield from memories(sub, here)


def modules():
    """(label, thunk) of every in-scope module. The CPU register file is out of scope."""
    def uart():
        from peripherals.uart16550 import Uart16550
        return Uart16550()

    def stream(**kw):
        def build():
            from peripherals.stream_buffer import StreamBuffer
            return StreamBuffer(**kw)
        return build

    def flash():
        from peripherals.flash import Flash
        return Flash(sim=True)

    def hyperram():
        from peripherals.hyperram import HyperRAM
        return HyperRAM(sim=True)

    def stager():
        from bus.jtag_stage import JTAGStager
        return JTAGStager()

    return [("uart16550", uart),
            ("stream_buffer sync 64", stream(depth=64)),
            ("stream_buffer sync 16", stream(depth=16)),
            ("stream_buffer async 16", stream(depth=16, i_domain="sync", o_domain="usb")),
            ("flash", flash), ("hyperram", hyperram), ("jtag_stage", stager)]


def section_pinned(checks):
    for label, thunk in modules():
        try:
            found = list(memories(Fragment.get(thunk(), None)))
        except Exception as exc:                                     # noqa: BLE001
            checks.check(f"{label}: elaborates", False, f"{type(exc).__name__}: {exc}")
            continue
        loose = [(n, mi._data.depth, mi._data.shape) for n, mi in found
                 if mi._data.depth < DEEP and mi._attrs.get("ram_style") != "block"]
        checks.check(f"{label}: every memory under {DEEP} words is pinned to block RAM "
                     f"({len(found)} memories)", not loose, f"unpinned: {loose}")


def section_peripherals(checks):
    for label, thunk in modules():
        if label == "hyperram":
            continue            # its sim build does not convert standalone (hr_rst driver conflict)
        name = re.sub(r"\W+", "_", label)
        try:
            cells = cells_of(name, verilog.convert(thunk(), name=name))
        except Exception as exc:                                     # noqa: BLE001
            checks.check(f"{label}: synthesises", False, f"{type(exc).__name__}: {str(exc)[-80:]}")
            continue
        dpr, ebr = cells.get("TRELLIS_DPR16X4", 0), cells.get("DP16KD", 0)
        checks.check(f"{label}: no distributed RAM", dpr == 0, f"DPR16X4 {dpr}",
                     measurement=f"DPR16X4 {dpr}, DP16KD {ebr}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=619)
    parser.add_argument("--synth", action="store_true", help="also synthesise the peripherals")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    checks = Checks(emit)
    emit(f"block-RAM FIFO checks (seed {args.seed})")
    section_lockstep(checks, rng)
    section_mapping(checks)
    section_pinned(checks)
    if args.synth:
        section_peripherals(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
