#!/usr/bin/env python3
#
# Can the CPU read the BIST engine at all? Over CSR, with two clocks. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Does `present()` read back, and does a result come from the right window?

## Why this exists, and why it exists LATE

`hr sweep` hung the shell three separate times, and each diagnosis cost a
~90 second synthesis plus a reconfigure to test. Every one of those failures was
reachable in simulation in under a second:

  * the CSR bridge was dragged into `hr` by a `DomainRenamer`, so the first
    register read never completed and the CPU stalled with an empty terminal;
  * before that, the bootloader trapped on a deleted window;
  * before that, the engine ran on the CPU's clock with a dead PLL.

The board was used as the debugger for a question the board was never needed to
answer. This file is that question, asked cheaply.

## What it checks

`present()` is the whole rig's gate: it reads `REG_ID` and refuses to measure
unless it equals `HRC1`. So the single most important property is that a CSR
read of the ident register, issued in `sync`, returns a value the engine drives
in `hr`. That is one read across a clock-domain boundary through a bridge, and
it is exactly what was broken.

## The negative control

A read of the PARAMETER window at the same register number must NOT return the
ident. Parameters are CPU-written and read back as whatever the CPU last wrote;
results are engine-driven and live one window up. If both windows answered the
same, the split would be decorative and a result read would be returning the
CPU's own writes -- zero errors out of zero words, which is what a *passing*
cell looks like to anything that does not check.

Without that control, "the ident read back" is not evidence the windows are
distinct, and this file would pass just as happily with the offset wrong.

    ./scripts/soc_bist_transport_sim.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "soc" / "peripherals"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

from amaranth import ClockDomain, Elaboratable, Module  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402
from cynthion.gateware.platform.cynthion_r1_4 import (  # noqa: E402
    CynthionPlatformRev1D4)

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from hyperram.hyperram_ceiling_top import APPLET_ID, REG_ID  # noqa: E402
from peripherals.hyperram_bist import HyperRAMBist  # noqa: E402
from peripherals.bist_csr import BistCsrTransport  # noqa: E402

# The CPU is pinned and the engine is not; that independence is the point of the
# variant, so the simulation must not run them at the same rate. `hr` is CK/2 on
# the DQS path, so CK 160 is 80 MHz against a 30 MHz CPU.
SYNC_MHZ = 30.0
HR_MHZ = 80.0


class Fixture(Elaboratable):
    """The peripheral, with `sync` and `hr` as genuinely separate domains."""

    def __init__(self):
        self.dut = HyperRAMBist(ck_mhz=2 * HR_MHZ, dqs=True)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.domains.hr = ClockDomain()
        m.domains.hr_fast = ClockDomain()
        m.submodules.dut = self.dut
        return m


async def read32(ctx, bus, byte_address):
    """One 32-bit register, as the four byte accesses the CSR bus takes.

    `amaranth_soc` splits a wide register across consecutive byte addresses and
    latches the whole value on the first access, so the four reads must be
    issued in order and the assembled word is little-endian.
    """
    value = 0
    for index in range(4):
        ctx.set(bus.addr, byte_address + index)
        ctx.set(bus.r_stb, 1)
        await ctx.tick("sync")
        ctx.set(bus.r_stb, 0)
        byte = ctx.get(bus.r_data)
        value |= (byte & 0xFF) << (8 * index)
        await ctx.tick("sync")
    return value


def read_register(number, *, window):
    """Read engine register `number` from `window`, and report what came back."""
    fixture = Fixture()
    # Elaborated against the REAL platform, because the engine requests the
    # HyperRAM pins and a `None` platform has nothing to request from. No
    # toolchain is involved -- this only needs the resource definitions, and
    # using the genuine ones means the DUT here is the DUT that gets built.
    sim = Simulator(Fragment.get(fixture, CynthionPlatformRev1D4()))
    sim.add_clock(1e-6 / SYNC_MHZ, domain="sync")
    sim.add_clock(1e-6 / HR_MHZ, domain="hr")
    sim.add_clock(1e-6 / (2 * HR_MHZ), domain="hr_fast")

    got = {}

    async def testbench(ctx):
        bus = fixture.dut.bus
        # Let the engine settle: it drives its idents combinationally, but the
        # domains start unaligned and a read on the first edge proves nothing.
        for _ in range(8):
            await ctx.tick("sync")
        got["value"] = await read32(ctx, bus, window + 4 * number)

    sim.add_testbench(testbench)
    sim.run()
    return got.get("value")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    emit("soc_bist_transport_sim: can the CPU read the engine over CSR?")
    emit(f"  sync {SYNC_MHZ:g} MHz, hr {HR_MHZ:g} MHz -- deliberately unequal")
    emit("")
    checks = Checks(emit)

    result_window = BistCsrTransport.RESULT_WINDOW

    ident = read_register(REG_ID, window=result_window)
    checks.check(
        f"ident reads back from the result window: got {ident:#010x}, "
        f"want {APPLET_ID:#010x} -- this is what `present()` gates on",
        ident == APPLET_ID)

    # THE CONTROL. The same register number, read from the parameter window,
    # must not answer with the ident -- otherwise the two windows are the same
    # window and a result read returns the CPU's own last write.
    shadow = read_register(REG_ID, window=0)
    checks.check(
        f"NEGATIVE CONTROL: the parameter window does NOT answer with the "
        f"ident (got {shadow:#010x}), so the two windows are distinct and the "
        f"0x{result_window:x} offset is load-bearing",
        shadow != APPLET_ID)

    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
