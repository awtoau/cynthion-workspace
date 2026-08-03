#!/usr/bin/env python3
#
# Count HyperBus transactions against Wishbone beats. See #173.
# SPDX-License-Identifier: BSD-3-Clause

"""
Does a cache-line refill reach the HyperRAM as one burst, or as sixteen?

## The question, and why reading the source did not answer it

`bench hyperram` measures 360 CK per 64-byte cache line. `acfaa5d` records 51 CK
for a coalesced burst and 336 CK for sixteen separate transactions. 360 against
336 is a 1.07x match, so the board is paying the un-coalesced cost.

Every readable thing says it should not be. The CPU emits `CTI = INCR_BURST` and
`BTE = LINEAR` (`matrix/soc-cpu/VexiiRiscv.v:6492-6493`); `HyperRAMWishbone`
tests for exactly those; the decoder and arbiter both carry `features={"cti",
"bte", "err"}`; the burst cap is 374 beats against the 16 a line needs; and
`soc_hyperram_sim.py` passes 56 checks including "incrementing CTI produces one
CS# assertion".

So five hypotheses are eliminated and the board still disagrees with all of them.
That is the point to stop reading and start counting.

## What it counts

    starts        HyperBus transactions begun -- `start_transfer` edges
    beats         Wishbone beats acknowledged on the window
    burst_beats   beats that arrived with CTI=INCR_BURST and BTE=LINEAR
    max_run       longest unbroken run of beats within one transaction

**`beats / starts` is the answer.** 16 means every beat is its own transaction and
the burst is being broken; 1 means the line is one burst and the 336 CK is coming
from somewhere else entirely.

`max_run` distinguishes "never bursts" from "bursts but gets cut short" -- a cap,
an arbitration steal, or a refresh landing mid-line would each show as a run
shorter than 16 rather than as a run of 1.

`burst_beats` separates the CPU's intent from the outcome. If it tracks `beats`
but `beats/starts` is still 16, the signals arrive and something downstream
ignores them; if it stays at zero, they are being lost before the window.

## Cleared on write, like `FlashPinProbe`

Totals since reset cannot separate "this operation did nothing" from "this
operation did nothing but an earlier one did". The firmware clears, runs one
walk, and reads -- so every number is about that walk.

## It drives nothing

Every port is an input sampled from the existing bus. This module cannot perturb
the thing it measures, which matters when the measurement is about timing.
"""

from amaranth import Module, Signal, unsigned
from amaranth.lib import wiring
from amaranth.lib.wiring import In, connect, flipped
from amaranth_soc import csr


class HyperRAMProbe(wiring.Component):
    """Transaction and beat counters on the HyperRAM window."""

    def __init__(self):
        # 16 bits: a 16 KiB walk is 4096 beats and 256 lines, so neither counter
        # saturates over one `bench` row. A walk large enough to overflow would
        # be measuring the counter rather than the bus.
        self._starts = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                    access="r")
        self._beats = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                   access="r")
        self._burst_beats = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                         access="r")
        self._max_run = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                     access="r")
        self._clear = csr.Register({"strobe": csr.Field(csr.action.W, 1)},
                                   access="w")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("starts", self._starts)
        builder.add("beats", self._beats)
        builder.add("burst_beats", self._burst_beats)
        builder.add("max_run", self._max_run)
        builder.add("clear", self._clear)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=5, data_width=8)),
            # All inputs, sampled from the existing bus.
            "start_transfer": In(unsigned(1)),
            "beat": In(unsigned(1)),
            "is_burst": In(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        starts = Signal(16)
        beats = Signal(16)
        burst_beats = Signal(16)
        max_run = Signal(16)
        run = Signal(16)

        # `start_transfer` is a pulse from the owner FSM, but count the EDGE
        # anyway: a level held for two cycles by a stall would otherwise read as
        # two transactions and manufacture the very result being tested for.
        start_last = Signal()
        m.d.sync += start_last.eq(self.start_transfer)
        start_edge = self.start_transfer & ~start_last

        clear = Signal()
        m.d.comb += clear.eq(self._clear.f.strobe.w_stb)

        with m.If(clear):
            m.d.sync += [starts.eq(0), beats.eq(0), burst_beats.eq(0),
                         max_run.eq(0), run.eq(0)]
        with m.Else():
            with m.If(start_edge):
                m.d.sync += starts.eq(starts + 1)
                # A new transaction ends the previous run. Recorded here rather
                # than at the last beat, because the last beat of a run is not
                # distinguishable from any other until something follows it.
                with m.If(run > max_run):
                    m.d.sync += max_run.eq(run)
                m.d.sync += run.eq(0)
            with m.If(self.beat):
                m.d.sync += [beats.eq(beats + 1), run.eq(run + 1)]
                with m.If(self.is_burst):
                    m.d.sync += burst_beats.eq(burst_beats + 1)
                # Also update at the beat, so a walk that ends without a further
                # transaction still reports its longest run rather than dropping
                # the last one.
                with m.If((run + 1) > max_run):
                    m.d.sync += max_run.eq(run + 1)

        m.d.comb += [
            self._starts.f.count.r_data.eq(starts),
            self._beats.f.count.r_data.eq(beats),
            self._burst_beats.f.count.r_data.eq(burst_beats),
            self._max_run.f.count.r_data.eq(max_run),
        ]
        return m
