#!/usr/bin/env python3
#
# The mode register the firmware twiddles by hand, bit for bit. #432.
# SPDX-License-Identifier: BSD-3-Clause

"""`hyperram_ck`'s ctrl/status, as `firmware/cynthion-soc/src/hyperram.rs` reads them.

That driver writes the whole 32-bit `ctrl` word and picks `mode` and `refused`
out of `status` by shift and mask, because the CSR is byte-lane and a generated
accessor emits the wrong bus transaction for it. So the bit positions are a
contract between two files, and nothing else checks it:

    ctrl    bit 0 sel        bit 1 bist    bit 2 refused_clear
    status  bit 0 locked     bit 1 mode    bit 2 refused    bits 7:4 rungs

A wrong bit here does not fail to build. It hands the part to the engine when
firmware asked for staging, and the boot reads all ones.
"""

import re
import sys
from pathlib import Path

from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from peripherals.hyperram_ck import HyperRAMClockSelect  # noqa: E402

DRIVER = ROOT / "firmware" / "cynthion-soc" / "src" / "hyperram.rs"

CTRL, STATUS = 0x00, 0x04


async def _write(ctx, bus, offset, value):
    """One 32-bit CSR write, byte at a time and low byte first."""
    for byte in range(4):
        ctx.set(bus.addr, offset + byte)
        ctx.set(bus.w_data, (value >> (8 * byte)) & 0xFF)
        ctx.set(bus.w_stb, 1)
        await ctx.tick()
    ctx.set(bus.w_stb, 0)
    await ctx.tick()


async def _read(ctx, bus, offset):
    value = 0
    for byte in range(4):
        ctx.set(bus.addr, offset + byte)
        ctx.set(bus.r_stb, 1)
        await ctx.tick()
        value |= ctx.get(bus.r_data) << (8 * byte)
    ctx.set(bus.r_stb, 0)
    await ctx.tick()
    return value


def _run(bench):
    dut = HyperRAMClockSelect(ck_rungs=[80.0])
    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)

    async def run(ctx):
        await bench(ctx, dut)

    sim.add_testbench(run)
    sim.run()


def test_ctrl_bit_1_hands_the_part_over_and_bit_0_still_picks_a_rung():
    async def bench(ctx, dut):
        await _write(ctx, dut.bus, CTRL, 0b10)
        assert ctx.get(dut.bist) == 1
        assert ctx.get(dut.sel) == 0
        # Both at once: the rung selector must survive a mode write, and the
        # driver's read-modify-write depends on `ctrl` reading back.
        await _write(ctx, dut.bus, CTRL, 0b11)
        assert (ctx.get(dut.bist), ctx.get(dut.sel)) == (1, 1)
        assert await _read(ctx, dut.bus, CTRL) & 0b11 == 0b11

    _run(bench)


def test_status_reports_the_live_mode_and_the_sticky_refusal():
    async def bench(ctx, dut):
        ctx.set(dut.mode, 1)
        ctx.set(dut.refused, 1)
        ctx.set(dut.locked, 1)
        # Through two flops out of `hr`, so the mode needs a moment to arrive.
        for _ in range(4):
            await ctx.tick()
        status = await _read(ctx, dut.bus, STATUS)
        assert status & 0b111 == 0b111, f"{status:#x}"
        assert (status >> 4) & 0xF == 1, "one rung, reported as one"

    _run(bench)


def test_writing_bit_2_clears_the_refusal_and_nothing_else():
    """The strobe is one write long, and the mode it was OR-ed into stays."""
    dut = HyperRAMClockSelect(ck_rungs=[80.0])
    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    pulses = []

    async def watch(ctx):
        async for *_, clear, bist in ctx.tick().sample(dut.refused_clear,
                                                       dut.bist):
            if clear:
                pulses.append(bist)

    async def bench(ctx):
        await _write(ctx, dut.bus, CTRL, 0b010)
        await _write(ctx, dut.bus, CTRL, 0b110)
        await _write(ctx, dut.bus, CTRL, 0b010)
        # The write-one-to-clear bit holds no state, so the mode survives it.
        assert ctx.get(dut.bist) == 1

    sim.add_process(watch)
    sim.add_testbench(bench)
    sim.run()
    assert pulses == [1], pulses


def test_the_driver_reads_the_same_bits():
    """The shifts in `hyperram.rs`, against the field order above."""
    source = DRIVER.read_text()
    for what, pattern in (
            ("mode", r"read_volatile\(MODE_STATUS\)\s*>>\s*1"),
            ("refused", r"read_volatile\(MODE_STATUS\)\s*>>\s*2"),
            ("bist", r"\(ctrl & !2\) \| \(\(bist as u32\) << 1\)"),
            ("refused_clear", r"write_volatile\(MODE_CTRL, ctrl \| 4\)")):
        assert re.search(pattern, source), f"hyperram.rs: {what} moved"
