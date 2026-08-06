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

## `words` per beat: 3.0, and what it turned out to mean

The 3.0 words per 32-bit beat this measured -- against the 2.0 two 16-bit words
would give -- was recorded as not understood. It is the fault.

`RegisteredResponse` withholds STB for one cycle after each acknowledgement, so
the CPU supplies a beat every three cycles; a held-open HyperBus transaction
consumes one word per CK and cannot be stalled. The third cycle wrote a
duplicate word at a real address and left the engine's half-of-the-beat tracker
one ahead of the window's, so every following beat went out high-half-first.
48 words for a 32-word line, in BOTH directions. `sustained` in
`vexii_bootram.py` is the fix; sections 9 and 10 of `scripts/soc_hyperram_sim.py`
reproduce and then close it. So this counter should now read 2.0.

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
from amaranth.lib.wiring import In, Out, connect, flipped
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
        # The data phase. `words` counts 16-bit words delivered (32 per 64-byte
        # line); `busy` counts cycles with the controller not idle. busy/words is
        # the gap between words, and a streaming burst has a gap of 1.
        self._words = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                   access="r")
        self._busy = csr.Register({"count": csr.Field(csr.action.R, 32)},
                                  access="r")
        # The two stall buckets. 32-bit because a 16 KiB walk spends thousands of
        # cycles in them -- that is the point.
        self._want = csr.Register({"count": csr.Field(csr.action.R, 32)},
                                  access="r")
        self._arming = csr.Register({"count": csr.Field(csr.action.R, 32)},
                                    access="r")
        self._cyc = csr.Register({"count": csr.Field(csr.action.R, 32)},
                                 access="r")
        # The DQS read path's own verdict on itself. Without these a wrong
        # READCLKSEL, an unlocked DLL and a byte-order fault all present the same
        # way -- data that reads back wrong -- and there is nothing to tell them
        # apart from outside.
        #
        #   dll_locked   DDRDLLA asserted LOCK. Clear means the ECLK or the fast
        #                domain is wrong, and every delay code is meaningless.
        #   dll_ready    the settle sequence finished and PAUSE was released.
        #   burstdet     DQSBUFM saw a real strobe inside its window. THIS is
        #                what separates a working DQS read from a fixed-latency
        #                count that happened to land right -- a read that
        #                verifies with burstdet clear has demonstrated nothing.
        self._status = csr.Register({
            "dll_locked": csr.Field(csr.action.R, 1),
            "dll_ready": csr.Field(csr.action.R, 1),
            "burstdet": csr.Field(csr.action.R, 1),
        }, access="r")
        # Sticky and counted, because BURSTDET is a pulse per burst: sampling the
        # live signal from the CPU would miss every one of them.
        self._bursts = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                    access="r")
        # Cycles the Active Clock Stop gate held CK off (#185). Without this
        # there is no way to tell "the stall is misaligned" from "the stall
        # never fires", and those want opposite fixes.
        self._stalls = csr.Register({"count": csr.Field(csr.action.R, 32)},
                                    access="r")
        self._clear = csr.Register({"strobe": csr.Field(csr.action.W, 1)},
                                   access="w")
        # DQSBUFM's read clock tap, settable at RUN TIME. It is a property of
        # the board and of CK, so the alternative is eight bitstreams; this way
        # one bitstream sweeps all eight in a few milliseconds. See #148.
        # Bits 2:0 the DQSBUFM tap, bit 3 the read window's half-cycle phase,
        # bits 5:4 the Active Clock Stop read delay (#185). One register because
        # they are swept together, and a sweep that needs a rebuild per point is
        # a sweep nobody runs.
        # 2:0 DQSBUFM tap, 3 read-window phase, 5:4 clock-stop read delay,
        # 6 REGISTER SPACE for the staging port -- reading a known constant
        # (ID0 = 0x0c86) is the only absolute reference this SoC has for the
        # read path, since every window experiment moves reads and writes
        # together. #186.
        self._sel = csr.Register({"tap": csr.Field(csr.action.W, 7)},
                                 access="w")

        builder = csr.Builder(addr_width=6, data_width=8)
        builder.add("starts", self._starts)
        builder.add("beats", self._beats)
        builder.add("burst_beats", self._burst_beats)
        builder.add("max_run", self._max_run)
        builder.add("words", self._words)
        builder.add("busy", self._busy)
        builder.add("want", self._want)
        builder.add("arming", self._arming)
        builder.add("cyc", self._cyc)
        builder.add("status", self._status)
        builder.add("bursts", self._bursts)
        builder.add("stalls", self._stalls)
        builder.add("clear", self._clear)
        builder.add("sel", self._sel)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=6, data_width=8)),
            # All inputs, sampled from the existing bus.
            "start_transfer": In(unsigned(1)),
            "beat": In(unsigned(1)),
            "is_burst": In(unsigned(1)),
            "word": In(unsigned(1)),
            "busy": In(unsigned(1)),
            "want": In(unsigned(1)),
            "arming": In(unsigned(1)),
            "cyc": In(unsigned(1)),
            "dll_locked": In(unsigned(1)),
            "dll_ready": In(unsigned(1)),
            "burstdet": In(unsigned(1)),
            "stall": In(unsigned(1)),
            "sel": Out(unsigned(7)),
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
        words = Signal(16)
        busy = Signal(32)
        want = Signal(32)
        arming = Signal(32)
        cyc = Signal(32)
        bursts = Signal(16)
        burstdet_seen = Signal()
        stalls = Signal(32)

        # Upstream's default until firmware says otherwise, so a build that
        # never writes it behaves exactly as before.
        sel = Signal(7, init=0b010)
        with m.If(self._sel.f.tap.w_stb):
            m.d.sync += sel.eq(self._sel.f.tap.w_data)
        m.d.comb += self.sel.eq(sel)

        # `start_transfer` is a pulse from the owner FSM, but count the EDGE
        # anyway: a level held for two cycles by a stall would otherwise read as
        # two transactions and manufacture the very result being tested for.
        start_last = Signal()
        m.d.sync += start_last.eq(self.start_transfer)
        start_edge = self.start_transfer & ~start_last

        word_last = Signal()
        m.d.sync += word_last.eq(self.word)
        word_edge = self.word & ~word_last

        clear = Signal()
        m.d.comb += clear.eq(self._clear.f.strobe.w_stb)

        with m.If(clear):
            m.d.sync += [starts.eq(0), beats.eq(0), burst_beats.eq(0),
                         max_run.eq(0), run.eq(0), words.eq(0), busy.eq(0),
                         want.eq(0), arming.eq(0), cyc.eq(0),
                         bursts.eq(0), burstdet_seen.eq(0), stalls.eq(0)]
        with m.Else():
            with m.If(start_edge):
                m.d.sync += starts.eq(starts + 1)
                # A new transaction ends the previous run. Recorded here rather
                # than at the last beat, because the last beat of a run is not
                # distinguishable from any other until something follows it.
                with m.If(run > max_run):
                    m.d.sync += max_run.eq(run)
                m.d.sync += run.eq(0)
            # EDGE, not level. `read_ready` in luna's controller is asserted in
            # a state rather than pulsed, so counting the level counts cycles
            # spent ready and not words delivered. The first version did exactly
            # that and reported 3.00 words per 32-bit beat where two 16-bit words
            # must give 2.00 -- a number that was visibly impossible and would
            # have been quoted if the arithmetic it fed had come out plausible.
            with m.If(word_edge):
                m.d.sync += words.eq(words + 1)
            with m.If(self.busy):
                m.d.sync += busy.eq(busy + 1)
            with m.If(self.want):
                m.d.sync += want.eq(want + 1)
            with m.If(self.arming):
                m.d.sync += arming.eq(arming + 1)
            with m.If(self.cyc):
                m.d.sync += cyc.eq(cyc + 1)
            with m.If(self.stall):
                m.d.sync += stalls.eq(stalls + 1)
            with m.If(self.burstdet):
                m.d.sync += [bursts.eq(bursts + 1), burstdet_seen.eq(1)]
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
            self._words.f.count.r_data.eq(words),
            self._busy.f.count.r_data.eq(busy),
            self._want.f.count.r_data.eq(want),
            self._arming.f.count.r_data.eq(arming),
            self._cyc.f.count.r_data.eq(cyc),
            self._bursts.f.count.r_data.eq(bursts),
            self._stalls.f.count.r_data.eq(stalls),
            self._status.f.dll_locked.r_data.eq(self.dll_locked),
            self._status.f.dll_ready.r_data.eq(self.dll_ready),
            self._status.f.burstdet.r_data.eq(burstdet_seen),
        ]
        return m
