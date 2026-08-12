#!/usr/bin/env python3
#
# The CPU picks which HyperRAM CK rung is live, and who owns the part. #228, #432.
# SPDX-License-Identifier: BSD-3-Clause

"""
One write selects the device clock, so CK is a runtime axis and not a rebuild.
A second selects the part's role, so THAT is a boot-time mode and not a build.

    +0  ctrl    RW  bit 0    sel        which rung drives `hr`
                    bit 1    bist       hand the part to the BIST engine
                    bit 2    refused_clear  W1C for status.refused
    +4  status  RO  bit 0    locked     the HyperRAM PLL has locked
                    bit 1    mode       who the mux is actually on
                    bit 2    refused    sticky: a staging transaction was
                                        asked for while the engine had it
                    bits 7:4 rungs      how many this bitstream carries
    +8  rung0   RO           CK in kHz
    +c  rung1   RO           CK in kHz, or 0 when this build has one rung

## The mode, and what firmware owes it

`bist` is a request; `status.mode` is what the mux is on. They differ while a
HyperBus transaction is in flight, because the mux follows the request only at
an idle controller -- so firmware writes `bist` and then waits for `mode` to
agree before assuming either half owns the part.

Selecting a mode is a step BETWEEN passes, exactly as selecting a rung is: the
part is a boot-time role, and nothing here can tell a mid-pass write from a
between-pass one.

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

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
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
            "bist":     csr.Field(csr.action.RW, 1),
            "refused_clear": csr.Field(csr.action.W, 1),
            "reserved": csr.Field(csr.action.R, 29),
        }, access="rw")
        self._status = csr.Register({
            "locked":   csr.Field(csr.action.R, 1),
            "mode":     csr.Field(csr.action.R, 1),
            "refused":  csr.Field(csr.action.R, 1),
            "reserved": csr.Field(csr.action.R, 1),
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
            # Out to `HyperRAMShared.sel`, back from its `mode`. The mux
            # synchronises the request itself; `mode` comes back from `hr`.
            "bist":   Out(1),
            "mode":   In(1),
            "refused":       In(1),
            "refused_clear": Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        rungs = self._ck_rungs
        khz = [round(ck * 1000) for ck in rungs]

        # `locked` is synchronised into `hr` by `HyperRAMDomains` and read here
        # in `sync`, so it crosses a second time. Two FFs rather than a raw
        # sample: `hr` is 60..200 MHz against a 50 MHz CPU, the two PLLs share
        # no phase relationship, and a metastable status bit is exactly the kind
        # of once-in-a-while wrong answer this rig cannot afford to chase.
        locked_sync = Signal()
        m.submodules.locked_cdc = FFSynchronizer(self.locked, locked_sync)

        # The live mode, out of `hr`. Same reasoning as `locked`: a level the
        # firmware polls, and a metastable answer here would be read as the
        # handover having happened.
        mode_sync = Signal()
        m.submodules.mode_cdc = FFSynchronizer(self.mode, mode_sync)

        m.d.comb += [
            self.sel.eq(self._ctrl.f.sel.data),
            self.bist.eq(self._ctrl.f.bist.data),
            self.refused_clear.eq(self._ctrl.f.refused_clear.w_stb
                                  & self._ctrl.f.refused_clear.w_data),
            self._ctrl.f.reserved.r_data.eq(0),

            self._status.f.locked.r_data.eq(locked_sync),
            self._status.f.mode.r_data.eq(mode_sync),
            self._status.f.refused.r_data.eq(self.refused),
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
