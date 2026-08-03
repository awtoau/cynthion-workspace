#!/usr/bin/env python3
#
# Does luna_soc's SPI crossbar ever grant the controller port?
# SPDX-License-Identifier: BSD-3-Clause

"""
Simulates `SPIControlPortCrossbar` to find out why the controller path is mute.

On hardware the memory-mapped read path works perfectly -- an ordinary load
returns bytes that match `apollo flash-read` exactly -- while the JEDEC ID read
through `SPIController` returns all zeros. Both share one PHY through this
crossbar, so the difference is either the controller, or the arbitration
between them.

That is not a question hardware can answer: on the board the only observable
quantity is the word the CPU got back, and every signal in between is inside
the FPGA. Here they are all visible.

The specific suspicion is the round-robin's grant. `SPIControlPortCrossbar`
wires its ports with `wiring.connect` inside `m.Switch`/`m.Case`:

    with m.Switch(rr.grant):
        for i in range(self._num_ports):
            with m.Case(i):
                connect(m, wiring.flipped(self.get_port(i)), ...)

and if the grant never moves off whichever port is asserting `cs` most of the
time, the other port is never served. The memory map holds `cs` high for
MMAP_DEFAULT_TIMEOUT (256) cycles after every burst, which is a long time to
hold a shared resource.

This drives slave0 (the controller's position) asking for a transfer while
slave1 (the memory map's position) does or does not hold `cs`, and reports
whether slave0's request ever reaches the shared controller port.

    ./scripts/riscv_flash_crossbar_sim.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_flash_crossbar_sim.log"

from amaranth.sim import Simulator

from luna_soc.gateware.core.spiflash.port import SPIControlPortCrossbar

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
from vexii_flash import FairSPIControlPortCrossbar

# Long enough to cover the memory map's 256-cycle chip-select hold with room to
# spare, so a grant that is merely slow is distinguishable from one that never
# arrives. Not a duration -- the simulation has no wall clock.
CYCLES = 600


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


async def drive(ctx, dut, mmap_holds_cs, observed):
    """slave0 asks for a transfer; slave1 models the memory map between bursts.

    The distinction being tested is the whole point, so slave1 is modelled the
    way `SPIFlashMemoryMap` actually behaves rather than as a signal held high:

      cs           = 1   -- asserted through the post-burst hold, so the chip
                            stays selected for a possible sequential follow-on
      source.valid = 0   -- no transfer pending; the burst has completed

    A crossbar that arbitrates on `cs` sees a busy port and never moves the
    grant. One that arbitrates on `source.valid` sees an idle port and does.
    Holding both high would model a port mid-burst, which SHOULD keep the PHY,
    and would not distinguish the two designs at all.
    """
    ctx.set(dut.slave0.cs, 1)
    ctx.set(dut.slave0.source.valid, 1)
    ctx.set(dut.slave0.source.data, 0x9F000000)
    ctx.set(dut.slave0.source.len, 8)
    ctx.set(dut.slave0.source.width, 1)
    ctx.set(dut.slave0.source.mask, 1)

    ctx.set(dut.slave1.cs, 1 if mmap_holds_cs else 0)
    ctx.set(dut.slave1.source.valid, 0)

    # The PHY side always accepts and immediately returns data, so nothing is
    # stalled downstream and the only thing that can withhold the transfer is
    # the arbitration itself. Completing the handshake also lets any
    # transfer-in-flight interlock clear, which a permanently-asserted `valid`
    # would wedge -- an earlier version of this testbench did exactly that and
    # made a working crossbar look broken.
    ctx.set(dut.controller.source.ready, 1)
    ctx.set(dut.controller.sink.valid, 1)

    for cycle in range(CYCLES):
        await ctx.tick()
        if ctx.get(dut.controller.source.valid) and \
                ctx.get(dut.controller.source.data) == 0x9F000000:
            if not observed["granted"]:
                observed["grant_cycle"] = cycle
            observed["granted"] = True


def run(handle, mmap_holds_cs, factory):
    dut = factory()
    observed = {"granted": False}

    async def testbench(ctx):
        await drive(ctx, dut, mmap_holds_cs, observed)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    label = ("memory map holding cs" if mmap_holds_cs
             else "memory map idle")
    if observed["granted"]:
        emit(handle, f"  {label}: controller GRANTED "
                     f"(cycle {observed.get('grant_cycle')})")
    else:
        emit(handle, f"  {label}: controller NEVER granted in {CYCLES} cycles")
    return observed["granted"]


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, "Does slave0 (the controller's position) reach the PHY?")
        emit(handle)

        emit(handle, "luna_soc SPIControlPortCrossbar:")
        upstream = lambda: SPIControlPortCrossbar(
            data_width=32, num_ports=2, domain="sync")
        idle_ok = run(handle, False, upstream)
        busy_ok = run(handle, True, upstream)

        emit(handle)
        emit(handle, "FairSPIControlPortCrossbar (ecp5-test/riscv/vexii_flash.py):")
        fixed = lambda: FairSPIControlPortCrossbar(
            data_width=32, num_ports=2, domain="sync")
        fixed_idle_ok = run(handle, False, fixed)
        fixed_busy_ok = run(handle, True, fixed)

        emit(handle)
        if idle_ok and not busy_ok:
            emit(handle, "Upstream starves the controller whenever the memory "
                         "map holds cs -- which it does for 256 cycles after "
                         "every burst. That is the fault.")
        elif idle_ok and busy_ok:
            emit(handle, "Upstream arbitration serves both ports; the mute "
                         "controller path is NOT the crossbar.")
        else:
            emit(handle, "Upstream never granted the controller at all.")

        if fixed_idle_ok and fixed_busy_ok:
            emit(handle, "The replacement serves the controller in both "
                         "cases, including while the memory map holds cs.")
        else:
            emit(handle, "The replacement does NOT fix it "
                         f"(idle={fixed_idle_ok}, busy={fixed_busy_ok}).")

        emit(handle)
        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
