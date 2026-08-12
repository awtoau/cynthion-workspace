#!/usr/bin/env python3
#
# BootRAM reaches a controller in another clock domain, one pair at a time. #432
# SPDX-License-Identifier: BSD-3-Clause

"""The `sync`/`hr` handover, simulated against the real `BootRAM`.

One design now, and the part is a boot-time mode: PHY, controller and BIST
engine live in `hr`, BootRAM stays in `sync`, and `HyperRAMHandover` carries one
32-bit pair per transaction between them.

What is checked here, on both controller widths:

  * a Wishbone write then a read returns what was written, through the
    handover's collect/deliver halves -- the 16-bit path assembles two device
    words per beat and the 32-bit one does not;
  * a JTAG staging write lands at the address it was given;
  * a transaction asked for while the engine owns the part is REFUSED and
    completes, rather than hanging a poll for ever.

The device end is a model of `HyperRAMController`'s handshake, not the
controller itself: the point is the crossing, and the two clocks are
deliberately unequal so a same-domain assumption cannot pass.
"""

import sys
from pathlib import Path

import pytest
from amaranth import Elaboratable, Module, Signal
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from bootram import BootRAM                       # noqa: E402
from hyperram_share import HyperRAMHandover, HyperRAMPort   # noqa: E402

SYNC_HZ = 60e6
HR_HZ = 80e6

# Cycles the model spends in command and latency before the data phase, and in
# recovery after it. Any non-zero pair exercises the arm/busy handshake; these
# are the shape of `HyperRAMController`'s, not its exact counts.
LATENCY_CYCLES = 3
RECOVERY_CYCLES = 2


class ControllerModel(Elaboratable):
    """`HyperRAMController`'s word handshake over a dict, in `hr`.

    Words are 16 or 32 bits wide and the address counts 16-bit device words, so
    a 32-bit write covers two of them on the narrow path and one on the wide.
    """

    def __init__(self, port, *, width, depth=16, domain="hr"):
        self.port = port
        self.width = width
        self.step = width // 16
        self._domain = domain
        self.memory = [Signal(width, name=f"cell{index}")
                       for index in range(depth)]

    def elaborate(self, platform):
        m = Module()
        hr = self._domain
        port = self.port

        address = Signal(32)
        writing = Signal()
        count = Signal(range(max(LATENCY_CYCLES, RECOVERY_CYCLES) + 1))

        m.d.comb += port.idle.eq(0)

        with m.FSM(domain=hr):
            with m.State("IDLE"):
                m.d.comb += port.idle.eq(1)
                with m.If(port.start_transfer):
                    m.d[hr] += [address.eq(port.address),
                                writing.eq(port.perform_write),
                                count.eq(0)]
                    m.next = "LATENCY"

            with m.State("LATENCY"):
                m.d[hr] += count.eq(count + 1)
                with m.If(count == LATENCY_CYCLES - 1):
                    m.next = "DATA"

            with m.State("DATA"):
                m.d.comb += [port.write_ready.eq(writing),
                             port.read_ready.eq(~writing)]
                for index, cell in enumerate(self.memory):
                    with m.If(address == index * self.step):
                        with m.If(writing):
                            m.d[hr] += cell.eq(port.write_data)
                        with m.Else():
                            m.d.comb += port.read_data.eq(cell)
                m.d[hr] += [address.eq(address + self.step), count.eq(0)]
                with m.If(port.final_word):
                    m.next = "RECOVERY"

            with m.State("RECOVERY"):
                m.d[hr] += count.eq(count + 1)
                with m.If(count == RECOVERY_CYCLES - 1):
                    m.next = "IDLE"

        return m


class ControllerPortShape:
    """Just enough of a controller for `HyperRAMPort` to size itself from."""

    def __init__(self, *, width):
        self.address = Signal(32)
        self.register_space = Signal()
        self.perform_write = Signal()
        self.single_page = Signal()
        self.start_transfer = Signal()
        self.final_word = Signal()
        self.write_data = Signal(width)
        self.latency_clocks = Signal(4)
        self.low_latency_clocks = Signal(4)
        self.fixed_latency = Signal()
        self.idle = Signal()
        self.read_ready = Signal()
        self.write_ready = Signal()
        self.read_data = Signal(width)


class Fixture(Elaboratable):
    """BootRAM in `sync`, the model in `hr`, the handover between them."""

    def __init__(self, *, dqs):
        width = 32 if dqs else 16
        self.granted = Signal(init=1)
        self.port = HyperRAMPort(ControllerPortShape(width=width), name="stage")
        self.handover = HyperRAMHandover(port=self.port, width=width)
        self.bootram = BootRAM(dqs=dqs, interface=self.handover.interface)
        self.model = ControllerModel(self.port, width=width)

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.port.granted.eq(self.granted)
        m.submodules.handover = self.handover
        m.submodules.bootram = self.bootram
        m.submodules.model = self.model
        return m


def _build(dqs):
    fixture = Fixture(dqs=dqs)
    sim = Simulator(fixture)
    sim.add_clock(1 / SYNC_HZ, domain="sync")
    sim.add_clock(1 / HR_HZ, domain="hr")
    return fixture, sim


# The Wishbone window's own bound: a beat that has not been acknowledged by
# here is a hang, not a slow crossing. Two full transactions' worth of latency,
# recovery and handshake at the slower of the two clocks -- ~40 cycles -- times
# 1.25. On expiry the test names the beat rather than timing out silently.
ACK_LIMIT = 50


async def _wishbone(ctx, bus, adr, *, data=None):
    """One 32-bit beat. Returns what was read; `data` makes it a write."""
    ctx.set(bus.adr, adr)
    ctx.set(bus.sel, 0b1111)
    ctx.set(bus.we, data is not None)
    ctx.set(bus.dat_w, 0 if data is None else data)
    ctx.set(bus.cyc, 1)
    ctx.set(bus.stb, 1)
    for _ in range(ACK_LIMIT):
        await ctx.tick()
        if ctx.get(bus.ack):
            got = ctx.get(bus.dat_r)
            ctx.set(bus.cyc, 0)
            ctx.set(bus.stb, 0)
            await ctx.tick()
            return got
    raise AssertionError(
        f"no ack in {ACK_LIMIT} sync cycles for {'write' if data else 'read'} "
        f"at {adr:#x} -- the handover did not complete a transaction")


@pytest.mark.parametrize("dqs", [False, True], ids=["16bit", "32bit"])
def test_a_window_beat_crosses_and_comes_back(dqs):
    fixture, sim = _build(dqs)

    async def bench(ctx):
        bus = fixture.bootram.mmap.bus
        await _wishbone(ctx, bus, 4, data=0xDEADBEEF)
        assert await _wishbone(ctx, bus, 4) == 0xDEADBEEF
        # A second address, so a window that ignored the address bits and
        # returned the last thing written cannot pass.
        await _wishbone(ctx, bus, 5, data=0x0BADF00D)
        assert await _wishbone(ctx, bus, 4) == 0xDEADBEEF
        assert await _wishbone(ctx, bus, 5) == 0x0BADF00D

    sim.add_testbench(bench)
    sim.run()


@pytest.mark.parametrize("dqs", [False, True], ids=["16bit", "32bit"])
def test_a_jtag_pair_lands_where_it_was_addressed(dqs):
    fixture, sim = _build(dqs)

    async def bench(ctx):
        bootram = fixture.bootram
        ctx.set(bootram.jtag_addr, 8)       # device words, and even
        ctx.set(bootram.jtag_data, 0x12345678)
        ctx.set(bootram.jtag_req, 1)
        for _ in range(ACK_LIMIT):
            await ctx.tick()
            if ctx.get(bootram.jtag_ack):
                break
        else:
            raise AssertionError(
                f"no jtag_ack in {ACK_LIMIT} sync cycles -- the staging write "
                f"never completed")
        ctx.set(bootram.jtag_req, 0)
        await ctx.tick()
        assert await _wishbone(ctx, fixture.bootram.mmap.bus, 4) == 0x12345678

    sim.add_testbench(bench)
    sim.run()


def test_a_refused_transaction_completes_rather_than_hanging():
    """The engine owns the part, so the window is answered rather than left.

    A READ, because a write is acknowledged as soon as the handover has the
    pair -- the beat is posted, and its ordering against the next transaction
    is what the single-transaction crossing guarantees.
    """
    fixture, sim = _build(False)

    async def bench(ctx):
        ctx.set(fixture.granted, 0)
        assert await _wishbone(ctx, fixture.bootram.mmap.bus, 4) == 0xFFFFFFFF
        assert ctx.get(fixture.handover.refused) == 1

    sim.add_testbench(bench)
    sim.run()
