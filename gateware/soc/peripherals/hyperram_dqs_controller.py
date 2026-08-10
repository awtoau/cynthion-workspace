#!/usr/bin/env python3
#
# HyperBus DQS controller: luna's, vendored, with two defects fixed.
# SPDX-License-Identifier: BSD-3-Clause
#
# Vendored from luna-usb 0.2.3, luna/gateware/interface/psram.py, which is
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com> and
# BSD-3-Clause. The FSM below is theirs; the changes are marked in place.

"""
`HyperRAMDQSInterface`, vendored so the two defects in it can be fixed.

## Why vendored rather than subclassed

`docs/upstream-boundary.md` states the rule: **do not inherit a stack to get one
file -- vendor the file.** Subclassing bought nothing here. luna arrives as a pip
package, not a submodule, so "it keeps whatever fixes upstream makes" means
"whenever someone runs pip install -U", and this project tracks upstream *main*
while pip serves releases. The previous subclass also had to re-drive a signal by
relying on Amaranth resolving conflicting drivers in statement order, which is a
trick rather than an interface.

The PHY beneath this was already ours -- upstream's cannot be instantiated on
r1.4 -- so the boundary ran through one class.

## The two changes

**tCSHI is now enforced.** Upstream's `RECOVERY` carries `# TODO: implement
recovery` and falls straight through to `IDLE`, so CS# can be re-asserted on the
next cycle. The W956A8 wants 10 ns between transactions; at CK 180 (sync 90) one
`sync` cycle is 11.1 ns, so the old arrangement had about a nanosecond of margin
and only by accident. `hyperram_dqs_top.py` counted the gap outside the
controller, but `bootram.py` -- the SoC, the thing that actually runs --
enforced nothing at all. Now the controller holds it, so every caller gets it.

**The latency branch says what it means.** Upstream forces the long count with
`with m.If(extra_latency | 1)`, which makes the low-latency branch dead and
carries its own FIXME. The long count is correct for this part -- CR0 reads
`0x8f2f` and bit 3 selects fixed latency -- so the behaviour is unchanged by
default. `fixed_latency=False` honours the RWDS sample for a part reprogrammed to
variable latency, which is a change to make deliberately and measure.

`HIGH_LATENCY_CLOCKS` is a constructor argument rather than a class constant, which
is what `hyperram_latency.py` used to exist for.

## What is NOT fixed here

The read path still has no bitslip. `IDDRX2DQA` feeds the data path directly, and
no delay tap or read-phase setting can move the boundary at which four byte-phases
pack into a 32-bit word -- measured over all sixteen combinations, best 2/16. That
belongs in the PHY, where LiteDRAM's ECP5 equivalent puts `BitSlip(4)`. See #186.
"""

import math

from amaranth import (Cat, ClockSignal, Const, Elaboratable, Instance, Module, Mux,
                      Record, ResetSignal, Signal)
from amaranth.hdl.rec import DIR_FANIN, DIR_FANOUT
from amaranth.lib.cdc import FFSynchronizer

from . import hyperram_controller

# CS# high between transactions, and tRWR/tACC, for the grade fitted -- a `6I`,
# T166. Both from `Config-AC.v` via the non-DQS controller, which holds the
# per-grade table. Was 10.0 here too, which is the T100 column. (#341)
T_CSHI_NS = hyperram_controller.T_CSHI_NS
T_RWR_NS = hyperram_controller.T_RWR_NS

# tCSM, the longest CS# may stay Low: 4 us, W956A8 rev A01-006 Table 24, and
# section 10 makes it the HOST's obligation. Overrunning it drops a refresh, with
# no error at the transaction that caused it.
T_CSM_NS = 4000.0

# How much of tCSM one transaction may spend. The rest covers the PLL landing off
# `sync_mhz` and cycle counts that are counted states, not measured gaps. Same
# figure as `bootram.HYPERRAM_TCSM_MARGIN`.
T_CSM_MARGIN = 0.9


class HyperRAMDQSController(Elaboratable):
    """ Gateware interface to HyperRAM series self-refreshing DRAM chips, using ECP5 DQS logic.

    I/O port:
        B: phy              -- The primary physical connection to the DRAM chip.

        I: address[32]      -- The address to be targeted by the given operation.
        I: register_space   -- When set to 1, read and write requests target registers instead of normal RAM.
        I: perform_write    -- When set to 1, a transfer request is viewed as a write, rather than a read.
        I: single_page      -- If set, data accesses will wrap around to the start of the current page when done.
        I: start_transfer   -- Strobe that goes high for 1-8 cycles to request a read operation.
                               [This added duration allows other clock domains to easily perform requests.]
        I: final_word       -- Flag that indicates the current word is the last word of the transaction.

        O: read_data[32]    -- word that holds the 32 bits most recently read from the PSRAM
        I: write_data[32]   -- word that accepts the data to output during this transaction

        O: idle             -- High whenever the transmitter is idle (and thus we can start a new piece of data.)
        O: read_ready       -- Strobe that indicates when new data is ready for reading
        O: write_ready      -- Strobe that indicates `write_data` has been latched and is ready for new data
        O: state[4]         -- Which FSM state, indexed into `STATES`. `idle` alone says
                               a stuck controller is stuck; this says where.
        O: timed_out        -- Sticky: the watchdog ended the last transaction, not the
                               caller. Cleared by the next `start_transfer`.
    """

    # In 2 CK cycles, and both are UPSTREAM's -- every caller here overrides
    # `HIGH_LATENCY_CLOCKS` with `bootram.HYPERRAM_LATENCY_CLOCKS`. 5 is `L - 1`
    # for L = 6; this part powers up at L = 7 and wants 6.
    #
    # LOW is the variable-latency branch (1 x L CK), which is dead while
    # `fixed_latency` is True -- and cannot be exact for an odd L anyway, since
    # L / 2 is not a whole cycle. Only the doubled count divides evenly.
    LOW_LATENCY_CLOCKS  = 3
    HIGH_LATENCY_CLOCKS = 5

    # The FSM encoding, which is what `state` reports. Amaranth numbers a state
    # when it is first NAMED, not where it is defined -- `WRITE_DATA` is 4 because
    # `SHIFT_COMMAND1` jumps to it before `HANDLE_LATENCY` exists. `elaborate`
    # checks this list against `fsm.encoding` and fails the build if the two drift,
    # so a rig decoding `state` decodes something verified rather than assumed.
    # See #318.
    STATES = ("IDLE", "CS_SETUP", "SHIFT_COMMAND0", "SHIFT_COMMAND1",
              "WRITE_DATA", "HANDLE_LATENCY", "READ_DATA", "RECOVERY")

    def __init__(self, *, phy, sync_mhz, high_latency_clocks=None,
                 max_latency_clocks=None, fixed_latency=True,
                 tcshi_ns=T_CSHI_NS, trwr_ns=T_RWR_NS,
                 max_recovery_cycles=None):
        """
        Parameters:
            phy                 -- The RAM record that should be connected to this chip.
            sync_mhz            -- The `sync` clock, in MHz. Only used to turn tCSHI into
                                   a cycle count; pass the real number or CS# recovery is
                                   wrong.
            high_latency_clocks -- Override the fixed-latency count, in 2 CK cycles.
                                   `L - 1` for a part at initial latency L with CR0[3]
                                   set; 6 here. Upstream's 5 is `L - 1` for L = 6,
                                   which is not this part's power-on code.
            fixed_latency       -- True when the part takes the long latency on every
                                   transaction, which is what CR0 = 0x8f2f selects.
            tcshi_ns            -- CS# high between transactions, in ns. RESET value
                                   of `recovery_cycles`.
            trwr_ns             -- tRWR/tACC, in ns. RESET value of
                                   `min_latency_clocks`; see the non-DQS twin.
            max_recovery_cycles -- Ceiling for `recovery_cycles`, which sizes it.
        """
        self._fixed_latency = fixed_latency
        # Rounded UP, and at least one: a gap shorter than tCSHI is the violation this
        # exists to prevent, so the rounding may only ever be generous.
        self._recovery_cycles = max(1, math.ceil(tcshi_ns * sync_mhz / 1000.0))
        self._max_recovery_cycles = max(self._recovery_cycles,
                                        max_recovery_cycles or 0)
        # The tRWR/tACC floor, in CK. One cycle here is 2 CK, so the count this
        # controller waits is compared at 2x. Reported, never enforced -- the
        # device waits what CR0[7:4] says. (#341)
        self._min_latency_clocks = hyperram_controller.min_latency_code(
            2.0 * sync_mhz, trwr_ns)
        if high_latency_clocks is not None:
            self.HIGH_LATENCY_CLOCKS = high_latency_clocks
        # Ceiling for `latency_clocks`; see the non-DQS controller. Defaults to the
        # fixed count, so a caller that never drives the input gets the old build.
        self._max_latency_clocks = max(self.HIGH_LATENCY_CLOCKS,
                                       max_latency_clocks or 0)

        # CS#-Low cycles before the first data beat: CS_SETUP, two command beats,
        # and HANDLE_LATENCY, which runs one cycle per remaining count plus the
        # zero cycle. Worst case, so the tCSM check covers the longest latency the
        # caller can select at runtime.
        self._data_entry_cycles = 3 + self._max_latency_clocks + 1

        # WATCHDOG on READ_DATA/WRITE_DATA, whose only exit was a device beat
        # meeting `final_word` -- 7 of 8 beats hung for ever (#316). Waits for the
        # caller to end the burst; bounded by tCSM because the burst length is the
        # caller's and tCSM is its only ceiling. 0.9 is a margin BELOW a limit the
        # part imposes, not 1.25x above an expected duration. Less two cycles: the
        # exit into RECOVERY leaves CS# Low for two more. Expiry sets `timed_out`.
        self._burst_cycles = int(T_CSM_NS * T_CSM_MARGIN * sync_mhz / 1000.0) - 2
        if self._burst_cycles <= self._data_entry_cycles:
            raise ValueError(
                f"tCSM allows {self._burst_cycles} cycles at sync {sync_mhz} MHz, "
                f"which does not cover {self._data_entry_cycles} cycles of command "
                f"and latency: no transaction could complete inside tCSM")

        # The same budget in BEATS, one cycle ahead of the watchdog: reaching it
        # means the device kept up and the burst was simply too long for tCSM,
        # where the watchdog behind it means the device stopped. A beat is two
        # device words here. Callers keep their own cap
        # (`bootram.hyperram_max_burst_words`) and never reach this. (#317)
        self._burst_beats = self._burst_cycles - self._data_entry_cycles

        #
        # I/O port.
        #
        self.phy              = phy

        # Control signals.
        self.address          = Signal(32)
        self.register_space   = Signal()
        self.perform_write    = Signal()
        self.single_page      = Signal()
        self.start_transfer   = Signal()
        self.final_word       = Signal()
        # Initial latency before the data body, in this controller's own cycles.
        # ONE CYCLE IS 2 CK, not 4: the 4:1 gearing carries four BYTES per cycle
        # and the part moves two bytes per CK. `HANDLE_LATENCY` runs this count
        # plus a zero cycle, so with fixed latency (2 x L CK) the exact value is
        # `L - 1` -- 6 for the power-on code's L = 7, which is what `bootram`
        # passes. Reset is the build-time constant; drive it to sweep in step
        # with the part's CR0[7:4] (#331).
        self.latency_clocks   = Signal(range(0, self._max_latency_clocks + 1),
                                       reset=self.HIGH_LATENCY_CLOCKS)
        # CR0[3] as the part is set to; see the non-DQS controller. Was
        # `int(self._fixed_latency)`, which made the variable path dead (#338).
        self.fixed_latency    = Signal(reset=int(fixed_latency))
        # The same three levers the non-DQS twin grew, same reasons, same reset
        # values. `recovery_cycles` is tCSHI in whole `sync` cycles; the burst
        # bounds are the tCSM watchdog and can only be shortened. (#341)
        self.recovery_cycles  = Signal(range(0, self._max_recovery_cycles + 1),
                                       reset=self._recovery_cycles)
        self.burst_cycles     = Signal(range(0, self._burst_cycles + 1),
                                       reset=self._burst_cycles)
        self.burst_beats      = Signal(range(0, self._burst_beats + 1),
                                       reset=self._burst_beats)
        # The tRWR floor in CK, against `2 x latency_clocks + 2` -- this
        # controller's count is in 2 CK cycles and HANDLE_LATENCY runs it plus a
        # zero cycle.
        self.min_latency_clocks = Signal(
            range(0, 2 * self._max_latency_clocks + 3),
            reset=min(self._min_latency_clocks, 2 * self._max_latency_clocks + 2))

        # Status signals.
        self.idle             = Signal()
        self.read_ready       = Signal()
        self.write_ready      = Signal()
        # Fixed at 4 bits, not `range(len(STATES))`: a caller's register field must
        # not move when a state is added.
        self.state            = Signal(4)
        # Sticky, cleared by the next `start_transfer`: this transaction was ended
        # by the watchdog rather than by the caller. Tells a device fault from a
        # controller fault without a bus trace.
        self.timed_out        = Signal()
        # The configured latency does not cover tRWR/tACC at this CK. Under fixed
        # latency the part spends 2 x L CK and this controller counts L - 1, so
        # the CK it waits is `2 x latency_clocks + 2`. (#341)
        self.latency_below_trwr = Signal()

        # Data signals.
        self.read_data        = Signal(32)
        self.write_data       = Signal(32)


    def elaborate(self, platform):
        m = Module()

        recovery_remaining = Signal(range(self._max_recovery_cycles + 1))

        m.d.comb += self.latency_below_trwr.eq(
            (2 * self.latency_clocks + 2) < self.min_latency_clocks)

        #
        # Latched control/addressing signals.
        #
        is_read         = Signal()
        is_register     = Signal()
        current_address = Signal(32)
        is_multipage    = Signal()

        #
        # FSM datapath signals.
        #

        # Tracks whether we need to add an extra latency period between our
        # command and the data body.
        extra_latency   = Signal()

        # Tracks how many cycles of latency we have remaining between a command
        # and the relevant data stages.
        latency_clocks_remaining  = Signal(range(0, self._max_latency_clocks + 1))

        #
        # Core operation FSM.
        #

        # Provide defaults for our control/status signals.
        m.d.sync += [
            self.phy.clk_en     .eq(0b11),
            self.phy.cs         .eq(1),
            self.phy.rwds.e     .eq(0),
            self.phy.dq.e       .eq(0),
            self.phy.read       .eq(0),
        ]
        m.d.comb += self.write_ready.eq(0),

        # Commands, in order of bytes sent:
        #   - WRBAAAAA
        #     W         => selects read or write; 1 = read, 0 = write
        #      R        => selects register or memory; 1 = register, 0 = memory
        #       B       => selects burst behavior; 0 = wrapped, 1 = linear
        #        AAAAA  => address bits [27:32]
        #
        #   - AAAAAAAA  => address bits [19:27]
        #   - AAAAAAAA  => address bits [11:19]
        #   - AAAAAAAA  => address bits [ 3:16]
        #   - 00000000  => [reserved]
        #   - 00000AAA  => address bits [ 0: 3]
        ca = Signal(48)
        m.d.comb += ca.eq(Cat(
            current_address[0:3],
            Const(0, 13),
            current_address[3:32],
            # CA[45] = 1 whenever CA[46] is: 9.1 allows only linear single-word
            # register access, and the burst type is meaningless for a register
            # read. It was `is_multipage` alone, correct only because all four
            # callers happen to pass `single_page=0`. (#320)
            is_multipage | is_register,
            is_register,
            is_read
        ))

        # Keep the recovery counter loaded everywhere except RECOVERY itself, which
        # decrements it. Amaranth takes the last assignment in a domain, so the
        # state's own statement wins while it is active and this re-arms on exit.
        # Arming at each `m.next = 'RECOVERY'` instead needs every transition to
        # remember to do it -- there are three, and a patch that added the line by
        # text match got two of them at the wrong indentation.
        m.d.sync += recovery_remaining.eq(self.recovery_cycles)

        # Armed while CS# is High: loaded before the data phase rather than at each
        # of the three edges into it, as pulp does (`hyperbus_phy.sv:308`). Counts
        # `sync` cycles and not beats -- tCSM is wall-clock, so a stopped CK still
        # spends it. (#316)
        burst_remaining = Signal(range(self._burst_cycles + 1))
        beats_remaining = Signal(range(self._burst_beats + 1))
        burst_expired   = Signal()
        beat_cap        = Signal()
        m.d.comb += [burst_expired.eq(burst_remaining == 0),
                     beat_cap.eq(beats_remaining <= 1)]
        with m.If(~self.phy.cs):
            m.d.sync += [burst_remaining.eq(self.burst_cycles),
                         beats_remaining.eq(self.burst_beats)]
        with m.Else():
            with m.If(~burst_expired):
                m.d.sync += burst_remaining.eq(burst_remaining - 1)
            with m.If((self.read_ready | self.write_ready) & ~beat_cap):
                m.d.sync += beats_remaining.eq(beats_remaining - 1)

        with m.FSM() as fsm:

            # IDLE state: waits for a transaction request
            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(1)
                m.d.sync += self.phy.clk_en  .eq(0)

                # Once we have a transaction request, latch in our control
                # signals, and assert our chip-select.
                with m.If(self.start_transfer):
                    m.next = 'CS_SETUP'

                    m.d.sync += [
                        is_read             .eq(~self.perform_write),
                        is_register         .eq(self.register_space),
                        is_multipage        .eq(~self.single_page),
                        current_address     .eq(self.address),
                        self.phy.dq.o       .eq(0),
                        self.timed_out      .eq(0),
                        extra_latency       .eq(0),
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)


            # CS_SETUP -- CS# is Low and CK is still stopped, covering the
            # registered `dq.o` reaching the pins. It sampled RWDS here until
            # #321, which is before the CA the device answers with: the value
            # latched was whatever the PREVIOUS transaction left.
            with m.State("CS_SETUP"):
                m.d.sync += self.phy.clk_en.eq(0b11)
                m.next = "SHIFT_COMMAND0"


            # SHIFT_COMMANDx -- shift each of our command words out
            with m.State('SHIFT_COMMAND0'):
                # Output the first 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(ca[16:48])),
                    self.phy.dq.e.eq(1),
                ]
                # Mid-CA, which is where the device raises RWDS to ask for the
                # extra latency; SHIFT_COMMAND1 ORs the live input on top and
                # decides at the end of the CA. (#321)
                m.d.sync += extra_latency.eq(extra_latency | self.phy.rwds.i.any())
                m.next = 'SHIFT_COMMAND1'

            with m.State('SHIFT_COMMAND1'):
                # Output the remaining 32 bits of our command.
                m.d.sync += [
                    # THE SPARE TWO BYTES ARE THE REGISTER DATA WORD.
                    #
                    # The 4:1 beat carries four bytes and the CA is six, so this
                    # second beat is CA[15:0] plus two spare. A register write
                    # has ZERO initial latency (datasheet 9.2), so whatever sits
                    # there IS the word written -- and it was Const(0, 16).
                    #
                    # Every DQS register write therefore wrote 0x0000, and
                    # CR0[15]=0 is DEEP POWER DOWN (Table 10). The part went
                    # silent, the next read returned nothing, and the unbounded
                    # READ_DATA hung on it. The wedge and the writes that
                    # "changed nothing" were one event, not two. See #226.
                    self.phy.dq.o.eq(Cat(Mux(is_register & ~is_read,
                                             self.write_data[0:16], 0),
                                         ca[0:16])),
                    self.phy.dq.e.eq(1),
                ]

                # If we have a register write, we don't need to handle
                # any latency. Move directly to our SHIFT_DATA state.
                with m.If(is_register & ~is_read):
                    m.next = 'WRITE_DATA'

                # Otherwise, react with either a short period of latency
                # or a longer one, depending on what the RAM requested via
                # RWDS.
                with m.Else():
                    m.next = "HANDLE_LATENCY"

                    # CR0 = 0x8f2f selects FIXED latency, so the long count is
                    # right for this part; upstream's `extra_latency | 1` says so
                    # by accident. `fixed_latency=False` honours the RWDS sample,
                    # which is #319's sweep. The decision is taken at the end of
                    # the CA, so a later RWDS change cannot erase it. (#321)
                    with m.If(extra_latency | self.phy.rwds.i.any()
                              | self.fixed_latency):
                        m.d.sync += latency_clocks_remaining.eq(self.latency_clocks)
                    with m.Else():
                        m.d.sync += latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS)


            # HANDLE_LATENCY -- applies clock cycles until our latency period is over.
            with m.State('HANDLE_LATENCY'):
                m.d.sync += latency_clocks_remaining.eq(latency_clocks_remaining - 1)

                with m.If(latency_clocks_remaining == 0):
                    with m.If(is_read):
                        m.next = 'READ_DATA'
                    with m.Else():
                        m.next = 'WRITE_DATA'


            # READ_DATA -- reads words from the PSRAM
            with m.State('READ_DATA'):
                m.d.sync += self.phy.read.eq(0b11)

                datavalid_delay = Signal()
                m.d.sync += datavalid_delay.eq(self.phy.datavalid)

                with m.If(self.phy.datavalid):
                    m.d.comb += [
                        self.read_data     .eq(self.phy.dq.i),
                        self.read_ready    .eq(1),
                    ]

                    # If our controller is done with the transaction, end it.
                    with m.If(self.final_word):
                        m.d.sync += self.phy.clk_en.eq(0),
                        m.next = 'RECOVERY'

                # The escape (#316) and the tCSM chop (#317), one exit: the branch
                # above needs `phy.datavalid`, and neither bounds how long a live
                # device may be held selected. Stops CK as the ordinary exit does.
                with m.If(burst_expired | (self.read_ready & beat_cap)):
                    m.d.sync += [self.phy.clk_en.eq(0), self.timed_out.eq(1)]
                    m.next = 'RECOVERY'

            # WRITE_DATA -- write a word to the PSRAM
            with m.State("WRITE_DATA"):
                m.d.sync += [
                    self.phy.dq.o    .eq(self.write_data),
                    self.phy.dq.e    .eq(1),
                    self.phy.rwds.e  .eq(~is_register),
                    self.phy.rwds.o  .eq(0),
                ]
                m.d.comb += self.write_ready.eq(1),

                # If we just finished a register write, we're done -- there's no need for recovery.
                with m.If(is_register):
                    # THROUGH RECOVERY, not straight to IDLE. Going to IDLE
                    # asserts `idle` in the same cycle the data word is still
                    # being clocked out, one cycle before CS# drops -- so
                    # back-to-back register writes with `start_transfer` held
                    # never raise CS# at all, and tCSHI is violated outright.
                    #
                    # Callers escape today only by accident: their *_WAIT states
                    # happen not to drive `start_transfer`, leaving exactly ONE
                    # cycle of CS# high -- 10.0 ns at 100 MHz, 6.06 ns at 165 --
                    # which is tCSHI minimum with zero margin, held by a property
                    # of the caller's state count rather than by this FSM.
                    m.next = 'RECOVERY'

                with m.Elif(self.final_word):
                    # Straight to RECOVERY, and a WRITE_FLUSH state added here was
                    # REVERTED. The reasoning for it was that `dq.o` is registered
                    # and so reaches the pins a cycle late, after RECOVERY has
                    # stopped CK. But `clk_en` is registered too -- the per-cycle
                    # defaults reload it every cycle, and RECOVERY's `clk_en.eq(0)`
                    # lands on the cycle AFTER entry, which is the same cycle the
                    # data becomes valid. The last word is clocked out. Holding an
                    # extra cycle would emit an EXTRA word instead.
                    m.next = 'RECOVERY'

                # Same exit (#316, #317). `write_ready` is high on every cycle
                # here, so the word cap and the cycle count run together.
                with m.If(burst_expired | beat_cap):
                    m.d.sync += self.timed_out.eq(1)
                    m.next = 'RECOVERY'

            # RECOVERY: CS# High for tCSHI. Deasserting and counting are both
            # needed -- counting alone holds the state, not the gap. tCSHI is a
            # TIME, so `_recovery_cycles` follows `sync_mhz` rather than being a
            # constant that silently stops being enough. See #316's sim section 4.
            with m.State('RECOVERY'):
                m.d.sync += self.phy.clk_en .eq(0)
                m.d.sync += self.phy.cs.eq(0)

                m.d.sync += recovery_remaining.eq(recovery_remaining - 1)
                with m.If(recovery_remaining == 0):
                    m.next = 'IDLE'

        if tuple(fsm.encoding) != self.STATES or len(self.STATES) > 16:
            raise ValueError(
                f"STATES does not describe this FSM: declared {self.STATES}, "
                f"elaborated {tuple(fsm.encoding)}")
        m.d.comb += self.state.eq(fsm.state)

        return m


