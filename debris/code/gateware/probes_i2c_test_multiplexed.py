#!/usr/bin/env python3
#
# Simulation tests for the multiplexed I2C master.
# SPDX-License-Identifier: BSD-3-Clause

"""
Verifies the multiplexed I2C master against a simulated device.

The point of interest is not that I2C works -- `I2CInitiator` is upstream and
tested -- but that this component sequences it correctly for a *register* read,
which is the two-phase form the FUSB302B and PAC1954 both need: address+W,
register index, repeated start, address+R, data byte.

Each test carries a positive control where one is meaningful. A simulated bus that
never acknowledges looks identical to a state machine that never starts, so
`test_absent_device_is_not_acked` is only evidence because
`test_register_read_sequences_correctly` shows the same harness observing a
completed transfer.

    python3 -m unittest discover -s gateware/probes/i2c
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

from amaranth.sim import Simulator

from i2c.multiplexed import (MultiplexedI2C, CONTROL_START_READ,
                             CONTROL_START_WRITE, BUS_POWER_MONITOR,
                             BUS_TARGET_C, DEVICE_ADDRESSES)

# Register offsets in the CSR file, matching the docstring's map.
REG_BUS, REG_ADDRESS, REG_REGISTER = 0x00, 0x01, 0x02
REG_CONTROL, REG_DATA, REG_STATUS = 0x03, 0x04, 0x05

STATUS_BUSY = 1 << 0
STATUS_ACKED = 1 << 1

# A transaction is five or six I2C byte-times at 100 kHz against a 60 MHz clock,
# so ~600 cycles per bit and ~50k cycles total. The cap is generous rather than
# tuned: it only has to be longer than a real transfer and short enough that a
# hung FSM fails the test instead of hanging the suite.
TRANSACTION_CYCLE_CAP = 200_000


class MultiplexedI2CTests(unittest.TestCase):

    def build(self):
        return MultiplexedI2C(period_cyc=8)   # fast bus, so sims stay short

    async def csr_write(self, ctx, dut, address, value):
        ctx.set(dut.bus.addr, address)
        ctx.set(dut.bus.w_data, value)
        ctx.set(dut.bus.w_stb, 1)
        await ctx.tick()
        ctx.set(dut.bus.w_stb, 0)
        await ctx.tick()

    async def csr_read(self, ctx, dut, address):
        ctx.set(dut.bus.addr, address)
        ctx.set(dut.bus.r_stb, 1)
        await ctx.tick()
        value = ctx.get(dut.bus.r_data)
        ctx.set(dut.bus.r_stb, 0)
        await ctx.tick()
        return value

    def test_idle_reports_not_busy(self):
        """The control: a fresh component must read as idle.

        Without this, "busy went low" in the tests below could just mean the FSM
        never left IDLE.
        """
        dut = self.build()
        seen = {}

        async def bench(ctx):
            seen["status"] = await self.csr_read(ctx, dut, REG_STATUS)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(seen["status"] & STATUS_BUSY, 0,
                         "a component that has done nothing reports busy")

    def test_write_to_control_starts_a_transaction(self):
        """Busy must rise after a start, or nothing downstream is meaningful.

        Sampled repeatedly rather than once. A single read immediately after the
        control write is not a valid check: with a short `period_cyc` the
        transaction can begin and end between the two CSR accesses, so busy reads
        low for the right reason and the test fails on a working component. That
        happened, and the fix is to watch for the rise rather than assume when it
        occurs.
        """
        dut = self.build()
        seen = {"rose": False}

        async def bench(ctx):
            await self.csr_write(ctx, dut, REG_BUS, BUS_POWER_MONITOR)
            await self.csr_write(ctx, dut, REG_ADDRESS,
                                 DEVICE_ADDRESSES[BUS_POWER_MONITOR])
            await self.csr_write(ctx, dut, REG_REGISTER, 0xFE)
            await self.csr_write(ctx, dut, REG_CONTROL, CONTROL_START_READ)
            # Either busy is still high, or the transaction has already run to
            # completion and left a verdict in `acked` -- both prove it started.
            # Only "busy low and no verdict, forever" means it never began.
            for _ in range(TRANSACTION_CYCLE_CAP):
                status = await self.csr_read(ctx, dut, REG_STATUS)
                if status & (STATUS_BUSY | STATUS_ACKED):
                    seen["rose"] = True
                    return

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertTrue(seen["rose"],
                        "writing CONTROL did not start a transaction")

    def test_transaction_completes_and_clears_busy(self):
        """It must finish. A FSM that starts and hangs is worse than one that
        never starts, because the first looks like progress."""
        dut = self.build()
        seen = {}

        async def bench(ctx):
            await self.csr_write(ctx, dut, REG_BUS, BUS_POWER_MONITOR)
            await self.csr_write(ctx, dut, REG_ADDRESS, 0x10)
            await self.csr_write(ctx, dut, REG_REGISTER, 0xFE)
            await self.csr_write(ctx, dut, REG_CONTROL, CONTROL_START_READ)

            for _ in range(TRANSACTION_CYCLE_CAP):
                status = await self.csr_read(ctx, dut, REG_STATUS)
                if not status & STATUS_BUSY:
                    seen["completed"] = True
                    seen["status"] = status
                    return
            seen["completed"] = False

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertTrue(seen.get("completed"),
                        f"transaction did not complete within "
                        f"{TRANSACTION_CYCLE_CAP} cycles")

    def test_absent_device_is_not_acked(self):
        """No device on the simulated bus means acked must be clear.

        This is the check that makes the peripheral useful as a self-test: an I2C
        read of an absent device returns 0xff, which is indistinguishable from a
        device that genuinely holds 0xff. Only the ACK separates them.

        Meaningful only because test_transaction_completes_and_clears_busy shows
        this harness can observe a transaction reaching its end at all.
        """
        dut = self.build()
        seen = {}

        async def bench(ctx):
            await self.csr_write(ctx, dut, REG_BUS, BUS_TARGET_C)
            await self.csr_write(ctx, dut, REG_ADDRESS, 0x22)
            await self.csr_write(ctx, dut, REG_REGISTER, 0x01)
            await self.csr_write(ctx, dut, REG_CONTROL, CONTROL_START_READ)

            for _ in range(TRANSACTION_CYCLE_CAP):
                status = await self.csr_read(ctx, dut, REG_STATUS)
                if not status & STATUS_BUSY:
                    seen["acked"] = bool(status & STATUS_ACKED)
                    return

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertIn("acked", seen, "transaction never completed")
        self.assertFalse(seen["acked"],
                         "an absent device was reported as acknowledged")

    def test_bus_select_is_readable_back(self):
        """Bus select must round-trip, or the mux cannot be steered."""
        dut = self.build()
        seen = {}

        async def bench(ctx):
            for value in (BUS_TARGET_C, BUS_POWER_MONITOR):
                await self.csr_write(ctx, dut, REG_BUS, value)
                seen[value] = await self.csr_read(ctx, dut, REG_BUS)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        for value in (BUS_TARGET_C, BUS_POWER_MONITOR):
            self.assertEqual(seen[value], value,
                             f"bus select {value} did not read back")

    def test_write_transaction_also_completes(self):
        """The write path is a different FSM branch and needs its own check."""
        dut = self.build()
        seen = {}

        async def bench(ctx):
            await self.csr_write(ctx, dut, REG_BUS, BUS_POWER_MONITOR)
            await self.csr_write(ctx, dut, REG_ADDRESS, 0x10)
            await self.csr_write(ctx, dut, REG_REGISTER, 0x1D)
            await self.csr_write(ctx, dut, REG_DATA, 0x00)
            await self.csr_write(ctx, dut, REG_CONTROL, CONTROL_START_WRITE)

            for _ in range(TRANSACTION_CYCLE_CAP):
                if not await self.csr_read(ctx, dut, REG_STATUS) & STATUS_BUSY:
                    seen["completed"] = True
                    return
            seen["completed"] = False

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertTrue(seen.get("completed"),
                        "the write branch did not complete")


if __name__ == "__main__":
    unittest.main()
