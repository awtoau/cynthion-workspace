#!/usr/bin/env python3
#
# RESET# polarity at the pad, and the DLL gate the engine waits on. #479 #475.
# SPDX-License-Identifier: BSD-3-Clause

"""Two signals the merge got wrong, both silent in every local gate.

## RESET# polarity

`ram.reset` is `PinsN("C1")`, so Amaranth's pad buffer inverts once: driving
`reset.o` high pulls the pad LOW, which is RESET# ASSERTED. `HyperRAMShared`
must therefore hand the buffer the assertion itself, not its complement.

The merged non-DQS path drove `~phy_reset`, and `phy_reset` is 0 in the
power-on STAGE mode -- so the part was held in hardware reset for the whole
life of the bitstream. It answered nothing, stored nothing, and every settled
read came back zero with no error (#479, #484).

## The DLL gate

`HyperRAMCeiling` leaves its `RESET` state on `heartbeat[16] & dll_ready`. The
non-DQS PHY has no DQSBUFM and so no DLL: nothing will ever report ready, and
the port must say so, or the engine parks in `RESET` with `busy=1 done=0` and
zero cycles for ever (#475).

Both are checked at the port and at the pad rather than in the source, so a
rewrite that keeps the defect in different words still fails.
"""

import sys
from pathlib import Path

from amaranth.build.dsl import Subsignal
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from board.cynthion_r1_4 import CynthionPlatformRev1D4  # noqa: E402
from hyperram_share import (MODE_BIST, MODE_STAGE, HyperRAMPort,  # noqa: E402
                            HyperRAMShared)
from peripherals.hyperram_controller import HyperRAMController  # noqa: E402


class _HoldsTheBus:
    """The real platform, with the `ram` pads handed to the test as well.

    `platform.request` may be called once per resource, and the module calls it
    inside `elaborate`. This lets the test hold the same bus the module drives.
    """

    def __init__(self):
        self._platform = CynthionPlatformRev1D4()
        self.bus = self._platform.request("ram", 0)

    def __getattr__(self, name):
        return getattr(self._platform, name)

    def request(self, name, number=0, **kwargs):
        assert (name, number) == ("ram", 0), f"unexpected request {name}/{number}"
        return self.bus


def _run(bench, *, dqs=False):
    platform = _HoldsTheBus()
    dut = HyperRAMShared(dqs=dqs, ck_mhz=80.0, latency_clocks=7)
    sim = Simulator(Fragment.get(dut, platform))
    # The module's `sync` is `hr` once `top.py` renames it; one clock either way.
    sim.add_clock(1 / 80e6)

    async def run(ctx):
        await bench(ctx, dut, platform.bus)

    sim.add_testbench(run)
    sim.run()


def test_the_pad_is_active_low_so_a_driven_high_is_an_assertion():
    """The premise the polarity check rests on, read from the platform."""
    resource = CynthionPlatformRev1D4().lookup("ram", 0)
    reset = next(sub for sub in resource.ios
                 if isinstance(sub, Subsignal) and sub.name == "reset")
    assert reset.ios[0].invert, (
        "ram.reset is no longer an inverting pad; the polarity below inverts "
        "with it")


def test_staging_releases_reset_at_the_pad():
    """The default mode, the one the bootloader stages in. #479, #484."""
    async def bench(ctx, dut, bus):
        ctx.set(dut.sel, MODE_STAGE)
        await ctx.tick().repeat(4)
        assert ctx.get(dut.mode) == MODE_STAGE
        assert ctx.get(bus.reset.o) == 0, (
            "RESET# is asserted at the pad in STAGE mode -- the part is held in "
            "reset and cannot answer anyone")

    _run(bench)


def test_the_engine_can_still_assert_reset_when_it_owns_the_part():
    """The pulse is not merely absent; the path has to work in the other mode."""
    async def bench(ctx, dut, bus):
        ctx.set(dut.sel, MODE_BIST)
        ctx.set(dut.bist.phy_reset, 1)
        await ctx.tick().repeat(4)
        assert ctx.get(dut.mode) == MODE_BIST
        assert ctx.get(bus.reset.o) == 1, "the engine's RESET# does not reach the pad"
        ctx.set(dut.bist.phy_reset, 0)
        await ctx.tick()
        assert ctx.get(bus.reset.o) == 0

    _run(bench)


def test_the_engine_leaves_reset_on_a_path_with_no_dll():
    """Otherwise it parks there: `busy=1 done=0`, FSM at 0, zero cycles. #475.

    Watched at the port, which is the whole of the engine's contract with the
    mux. In `RESET` it drives `phy_reset` from `~heartbeat[15]`, so a stuck
    engine is a square wave that never stops and a `start_transfer` that never
    comes.
    """
    from peripherals.hyperram_bist import HyperRAMBist  # noqa: PLC0415

    port = HyperRAMPort(HyperRAMController(phy=None, sync_mhz=80.0), name="bist")
    dut = HyperRAMBist(ck_mhz=80.0, port=port, dqs=False, domain="sync")
    sim = Simulator(dut)
    sim.add_clock(1 / 80e6)

    async def bench(ctx):
        ctx.set(port.granted, 1)
        ctx.set(port.idle, 1)
        # `RESET` runs for 2**16 cycles of the heartbeat; a little past it is
        # where a working engine has already asked for its first transaction.
        started = False
        for _ in range(2 ** 16 + 4096):
            await ctx.tick()
            if ctx.get(port.start_transfer):
                started = True
                break
        assert started, (
            "the engine never left RESET: no transaction after the whole "
            "heartbeat, which is `busy=1 done=0` with zero cycles on the board")
        assert ctx.get(port.phy_reset) == 0, "RESET# still asserted mid-transfer"

    sim.add_testbench(bench)
    sim.run()
