#!/usr/bin/env python3
#
# The CPU picks which HyperRAM CK rung is live. See #228, #313.
# SPDX-License-Identifier: BSD-3-Clause

"""
One write selects the device clock, so CK is a runtime axis and not a rebuild.

    +0  ctrl    RW  bit 0    sel        which rung drives `hr`
    +4  status  RO  bit 0    locked     the HyperRAM PLL has locked
                    bits 7:4 rungs      how many this bitstream carries
    +8  rung0   RO           CK in kHz
    +c  rung1   RO           CK in kHz, or 0 when this build has one rung

## Why the rates are registers and not firmware constants

The whole point of the rig is that a measurement names its own conditions. A
firmware constant for CK is a second copy of a number chosen in `top.py`, and
`clock_monitor.py` exists because this tree has already shipped a build whose
reported clock was a constant describing a PLL that never locked.

kHz, not Hz: every reachable rung is an exact number of kHz (`60000 * clkfb /
clki` and integer VCO divisors), and kHz keeps 200 MHz inside a 32-bit register
with room to spare.

## What it does NOT do

**No settling wait, and no interlock with the engine.** `DCSC` is glitchless
with both clocks running -- both rungs are outputs of one locked VCO -- so the
handover cannot produce a runt. What it does not do is finish a HyperBus burst:
the period changes mid-transaction if one is in flight, and the device's own
tCSM is measured in nanoseconds, not cycles. Selecting a rung is therefore a
firmware step between passes, not during one, and gateware cannot tell the
difference between the two. See `firmware`'s BIST driver.

**No `rungs == 0` case.** A build with one rung still answers, with `rungs = 1`
and `rung1 = 0`, so a driver reads the same registers either way and finds out
what it has rather than being built to know.
"""

from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr

__all__ = ["HyperRAMClockSelect"]


class HyperRAMClockSelect(wiring.Component):
    """`ctrl.sel` out to `HyperRAMDomains.sel`, and the rung table back.

    `ck_rungs` is the same list the clock generator was built from -- passed in
    rather than recomputed, so the registers cannot disagree with the dividers.
    """

    def __init__(self, *, ck_rungs):
        self._ck_rungs = [float(ck) for ck in ck_rungs]

        self._ctrl = csr.Register({
            "sel":      csr.Field(csr.action.RW, 1),
            "reserved": csr.Field(csr.action.R, 31),
        }, access="rw")
        self._status = csr.Register({
            "locked":   csr.Field(csr.action.R, 1),
            "reserved": csr.Field(csr.action.R, 3),
            "rungs":    csr.Field(csr.action.R, 4),
            "pad":      csr.Field(csr.action.R, 24),
        }, access="r")
        self._rung0 = csr.Register({"khz": csr.Field(csr.action.R, 32)},
                                   access="r")
        self._rung1 = csr.Register({"khz": csr.Field(csr.action.R, 32)},
                                   access="r")

        # Four 32-bit registers: 16 bytes, so four address bits.
        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("ctrl", self._ctrl, offset=0x00)
        builder.add("status", self._status, offset=0x04)
        builder.add("rung0", self._rung0, offset=0x08)
        builder.add("rung1", self._rung1, offset=0x0c)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":    In(csr.Signature(addr_width=4, data_width=8)),
            "sel":    Out(1),
            "locked": In(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        rungs = self._ck_rungs
        khz = [round(ck * 1000) for ck in rungs]

        m.d.comb += [
            self.sel.eq(self._ctrl.f.sel.data),
            self._ctrl.f.reserved.r_data.eq(0),

            # `locked` arrives already synchronised into `hr` by
            # `HyperRAMDomains`, and is read here in `sync`. A status bit read
            # one CPU cycle late is not a hazard; a FIFO for it would be.
            self._status.f.locked.r_data.eq(self.locked),
            self._status.f.reserved.r_data.eq(0),
            self._status.f.rungs.r_data.eq(len(rungs)),
            self._status.f.pad.r_data.eq(0),

            self._rung0.f.khz.r_data.eq(khz[0]),
            # 0 rather than a repeat of rung 0: a driver must be able to tell
            # "one rung" from "two that happen to match", and duplicates are
            # refused at elaboration anyway.
            self._rung1.f.khz.r_data.eq(khz[1] if len(khz) > 1 else 0),
        ]
        return m
