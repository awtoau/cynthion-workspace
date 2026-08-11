#!/usr/bin/env python3
#
# Can the CPU read the BIST engine at all? Over CSR, with two clocks. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Does `present()` read back, and does a result come from the right window?

## Why this exists

Three separate failures on the `hyperram-bist` branch each cost a ~90 s
synthesis plus a reconfigure to diagnose, and every one was reachable in
simulation in under a second:

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

Results are mirrored to ./tmp/logs/soc_bist_transport_sim.log.
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

from amaranth import (ClockDomain, DomainRenamer, Elaboratable,  # noqa: E402
                      Module)
from amaranth.lib.wiring import connect, flipped  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402
from amaranth_soc.csr.wishbone import WishboneCSRBridge  # noqa: E402
from board.cynthion_r1_4 import (  # noqa: E402
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

    def __init__(self, dut=None):
        self.dut = dut if dut is not None else HyperRAMBist(ck_mhz=2 * HR_MHZ,
                                                            dqs=True)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.domains.hr = ClockDomain()
        m.domains.hr_fast = ClockDomain()
        m.submodules.dut = self.dut
        return m


# -- the two known-bad constructions, rebuilt on purpose ---------------------
#
# A check that has only ever been seen to pass is not yet known to be able to
# fail. Both of these cost a ~90 s synthesis and a reconfigure to diagnose on
# the `hyperram-bist` branch; both are reproduced here so the checks above are
# demonstrated to discriminate rather than assumed to.


class TransportInsideRenamer(HyperRAMBist):
    """The bridge dragged into `hr` -- the construction that hung the shell.

    `DomainRenamer` renames everything elaborated inside it, so a CSR bridge put
    there is clocked by the engine rather than by the CPU.

    **What this file can and cannot show.** The `amaranth_soc` CSR bus has no
    acknowledge: a read presents an address and samples data a fixed number of
    cycles later. So with `hr` FASTER than `sync` -- the normal case, and the
    one measured above -- the misplaced bridge still answers in time and the
    read succeeds. The board's hang was the CPU's *Wishbone* access going
    unacknowledged, one layer up from here, and this file does not model it.

    What it does show is that the read is genuinely sensitive to which domain
    the bridge is in: below about `sync`/4 the value does not arrive. That is
    enough to demonstrate the check above can fail, which is the only claim
    being made for it.
    """

    def elaborate(self, platform):
        m = Module()
        inner = Module()
        inner.submodules.engine = self._engine
        inner.submodules.transport = self._transport
        m.submodules.both = DomainRenamer({"sync": self._domain,
                                           "fast": f"{self._domain}_fast"})(inner)
        connect(m, flipped(self.bus), self._transport.bus)
        return m


class TransportBeforeEngine(HyperRAMBist):
    """The transport elaborated first -- the failure that read zero everywhere.

    The engine declares its registers during its own elaborate, so a transport
    that goes first binds nothing. Every result then reads zero, which is
    indistinguishable from a clean pass to anything that does not check the word
    count.
    """

    def elaborate(self, platform):
        m = Module()
        m.submodules.transport = self._transport
        m.submodules.engine = DomainRenamer({"sync": self._domain,
                                             "fast": f"{self._domain}_fast"})(
            self._engine)
        connect(m, flipped(self.bus), self._transport.bus)
        return m


class OverWishbone(Elaboratable):
    """The peripheral as `top.py` actually instantiates it: behind a bridge.

    This is the layer the board hangs in, and the layer the CSR-only fixture
    above cannot see. `amaranth_soc`'s CSR bus has no acknowledge -- a read
    presents an address and samples data a fixed number of cycles later -- so a
    CSR-level testbench cannot express "the access never completed". Wishbone
    can: `ack` either arrives or it does not, and the CPU stalls on exactly that.

    `bist status` hangs the shell dead on hardware. Reproducing it here turns a
    ~2 minute build-and-load into a second.
    """

    def __init__(self, hr_mhz):
        self.dut = HyperRAMBist(ck_mhz=2 * hr_mhz, dqs=True)
        self.bridge = WishboneCSRBridge(self.dut.bus, data_width=32)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.domains.hr = ClockDomain()
        m.domains.hr_fast = ClockDomain()
        m.submodules.dut = self.dut
        m.submodules.bridge = self.bridge
        return m


async def wishbone_read(ctx, wb, word_address, limit):
    """One Wishbone read, bounded. Returns (data, cycles) or (None, limit).

    **Waits for**: `ack`, which the CPU's load waits on with no bound at all --
    a stalled access is a stalled CPU and that is what the board does.

    **Bound**: `limit` cycles. A CSR bridge answers a 32-bit register in a
    handful; anything past a few dozen is not slow, it is never. Expiry returns
    None rather than looping, so the failure is a reported result instead of a
    simulation that hangs the way the board did.
    """
    ctx.set(wb.adr, word_address)
    ctx.set(wb.cyc, 1)
    ctx.set(wb.stb, 1)
    ctx.set(wb.we, 0)
    ctx.set(wb.sel, 0b1111)
    for cycle in range(limit):
        await ctx.tick("sync")
        if ctx.get(wb.ack):
            data = ctx.get(wb.dat_r)
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            return data, cycle + 1
    ctx.set(wb.cyc, 0)
    ctx.set(wb.stb, 0)
    return None, limit


def read_over_wishbone(number, *, window, hr_mhz=None, limit=64):
    """Read an engine register the way the CPU does, and say if `ack` came."""
    hr_mhz = HR_MHZ if hr_mhz is None else hr_mhz
    fixture = OverWishbone(hr_mhz)
    sim = Simulator(Fragment.get(fixture, CynthionPlatformRev1D4()))
    sim.add_clock(1e-6 / SYNC_MHZ, domain="sync")
    sim.add_clock(1e-6 / hr_mhz, domain="hr")
    sim.add_clock(1e-6 / (2 * hr_mhz), domain="hr_fast")

    got = {}

    async def testbench(ctx):
        for _ in range(8):
            await ctx.tick("sync")
        # The bridge is 32-bit, so it addresses in WORDS: the CSR byte address
        # divided by four.
        byte_address = window + 4 * number
        got["value"], got["cycles"] = await wishbone_read(
            ctx, fixture.bridge.wb_bus, byte_address // 4, limit)

    sim.add_testbench(testbench)
    sim.run()
    return got.get("value"), got.get("cycles")


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


def read_register(number, *, window, dut=None, hr_mhz=None):
    """Read engine register `number` from `window`, and report what came back."""
    hr_mhz = HR_MHZ if hr_mhz is None else hr_mhz
    fixture = Fixture(dut)
    # Elaborated against the REAL platform, because the engine requests the
    # HyperRAM pins and a `None` platform has nothing to request from. No
    # toolchain is involved -- this only needs the resource definitions, and
    # using the genuine ones means the DUT here is the DUT that gets built.
    sim = Simulator(Fragment.get(fixture, CynthionPlatformRev1D4()))
    sim.add_clock(1e-6 / SYNC_MHZ, domain="sync")
    sim.add_clock(1e-6 / hr_mhz, domain="hr")
    sim.add_clock(1e-6 / (2 * hr_mhz), domain="hr_fast")

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

    # -- do the checks above discriminate? -----------------------------------
    emit("")
    emit("  the same read, against the two constructions known to be wrong:")

    # `hr` SLOWER than `sync` here, and that is not a detail. This bus has no
    # acknowledge, so a misplaced bridge running faster than the CPU still
    # answers in time; the sensitivity only appears below about `sync`/4. See
    # `TransportInsideRenamer` for what that does and does not demonstrate.
    slow_mhz = SYNC_MHZ / 4
    dragged = read_register(
        REG_ID, window=result_window, hr_mhz=slow_mhz,
        dut=TransportInsideRenamer(ck_mhz=2 * slow_mhz, dqs=True))
    checks.check(
        f"a bridge clocked by `hr` at {slow_mhz:g} MHz does NOT read back the "
        f"ident (got {dragged:#010x}) -- so the first check can fail, and the "
        f"domain the bridge sits in is what decides it",
        dragged != APPLET_ID)

    try:
        read_register(REG_ID, window=result_window,
                      dut=TransportBeforeEngine(ck_mhz=2 * HR_MHZ, dqs=True))
    except ValueError as error:
        bound_nothing = "no registers were declared" in str(error)
    else:
        bound_nothing = False
    checks.check(
        "a transport elaborated before the engine RAISES rather than binding "
        "nothing -- the alternative reads zero everywhere, which is what a "
        "passing cell looks like",
        bound_nothing)

    # -- the layer the board actually hangs in -------------------------------
    emit("")
    emit("  the same read again, but over Wishbone -- how the CPU issues it:")

    value, cycles = read_over_wishbone(REG_ID, window=result_window)
    checks.check(
        f"the bridge ACKNOWLEDGES a read of the ident"
        + (f" (in {cycles} cycles)" if value is not None else
           f" -- NO ACK in {cycles} cycles, which is what stalls the CPU dead")
        + (f", value {value:#010x}" if value is not None else ""),
        value is not None)

    if value is not None:
        checks.check(
            f"and the value that arrives over Wishbone is the ident: got "
            f"{value:#010x}, want {APPLET_ID:#010x} -- an ack carrying the "
            f"wrong word is worse than no ack, because it looks like a result",
            value == APPLET_ID)

    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
