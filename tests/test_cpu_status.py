# The liveness watchdog, and the two ways it could lie. #411.
# SPDX-License-Identifier: BSD-3-Clause

"""`alive` must fall when kicks stop, and STAY down.

The failure this guards is not "the watchdog does not work" -- it is a watchdog
that reports a dead CPU as alive. Two ways that happens:

* a counter that WRAPS re-arms itself, so `alive` returns once per period and a
  wedged board blinks healthy
* a counter that never counts at all leaves `alive` stuck high, which is
  indistinguishable from a healthy board and is how five of this repo's six LEDs
  came to carry no information

Both are asserted against directly, so a change that reintroduces either fails
here rather than on the bench.
"""

import sys
from pathlib import Path

import pytest
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from soc.peripherals.cpu_status_csr import CpuStatus  # noqa: E402

# Short enough to simulate, long enough that "reloads" and "counts down" are
# distinguishable. The real bound comes from the firmware's kick interval.
PERIOD = 16


def run(dut, testbench):
    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def kick(ctx, dut):
    """One write to +0, which is what the firmware does."""
    ctx.set(dut.bus.addr, 0)
    ctx.set(dut.bus.w_stb, 1)
    ctx.set(dut.bus.w_data, 1)
    await_ = ctx.tick()
    return await_


def test_alive_is_high_out_of_reset():
    """A board that has not yet run firmware must not read as dead.

    The counter starts loaded, so the first kick has a period to arrive. The
    alternative -- starting at zero -- reports every boot as a wedge for as long
    as it takes firmware to reach its first kick, and an indicator that cries
    wolf at every reset is one nobody reads.
    """
    dut = CpuStatus(period_cycles=PERIOD)

    async def bench(ctx):
        assert ctx.get(dut.alive) == 1

    run(dut, bench)


def test_alive_falls_when_kicks_stop_and_stays_down():
    """The whole point: expiry must LATCH low, not blink."""
    dut = CpuStatus(period_cycles=PERIOD)

    async def bench(ctx):
        for _ in range(PERIOD + 2):
            await ctx.tick()
        assert ctx.get(dut.alive) == 0, "did not expire"

        # THE WRAPPING CONTROL. A counter that wrapped would re-arm here and
        # `alive` would come back, so a wedged CPU would blink healthy once per
        # period -- the most misleading outcome available.
        for _ in range(PERIOD * 3):
            await ctx.tick()
            assert ctx.get(dut.alive) == 0, "alive returned without a kick"

    run(dut, bench)


def test_a_kick_reloads_the_countdown():
    """And the counter must actually COUNT -- stuck-high is the other lie."""
    dut = CpuStatus(period_cycles=PERIOD)

    async def bench(ctx):
        # Run most of the way down, then kick.
        for _ in range(PERIOD - 2):
            await ctx.tick()
        assert ctx.get(dut.alive) == 1

        ctx.set(dut.bus.addr, 0)
        ctx.set(dut.bus.w_stb, 1)
        ctx.set(dut.bus.w_data, 0xa5)
        await ctx.tick()
        ctx.set(dut.bus.w_stb, 0)

        # Past where it would have expired without the kick.
        for _ in range(4):
            await ctx.tick()
        assert ctx.get(dut.alive) == 1, "the kick did not reload the countdown"

        # ...and it still expires afterwards, so the kick reloaded rather than
        # disabling it. Without this the test passes on a watchdog welded high.
        for _ in range(PERIOD + 2):
            await ctx.tick()
        assert ctx.get(dut.alive) == 0, "never expires after a kick -- welded high"

    run(dut, bench)


def test_period_must_leave_room_to_count():
    with pytest.raises(ValueError):
        CpuStatus(period_cycles=1)
