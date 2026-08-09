#!/usr/bin/env python3
#
# HyperBus controller, non-DQS: luna's, vendored, with two defects fixed.
# SPDX-License-Identifier: BSD-3-Clause
#
# Vendored from luna-usb 0.2.3, luna/gateware/interface/psram.py, which is
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com> and
# BSD-3-Clause. The FSM below is theirs; the changes are marked in place.

"""
`HyperRAMInterface`, vendored so the two defects in it can be fixed.

## Why this one too

`hyperram_dqs_controller.py` was vendored first, for the same two defects. This
is the OTHER path -- 2:1 gearing, 16 bits per `sync` cycle, upstream's
`HyperRAMPHY` beneath it -- and it matters more than the order suggests:

  * **It is what the SoC ships.** `bootram.py` builds the non-DQS path by
    default, so the fixes were in the experimental controller and not in the one
    the board runs.
  * **It is the baseline every DQS result is read against.** #205 puts the two
    side by side, and a comparison in which only one side keeps tCSHI is not a
    comparison of the two paths. The instrument has to be the same on both.

Four harnesses drive it as well -- `stress`, `identify`, `regfuzz` and `fifo` --
and `docs/chips/hyperram/bist-plan.md` names `stress` as the validated
known-good reference the BIST is to be judged against.

## The two changes

**tCSHI is now enforced.** Upstream's `RECOVERY` carries `# TODO: implement
recovery` and returns to `IDLE` unconditionally, so CS# can be re-asserted on the
next cycle. The W956A8 wants 10 ns between transactions; at the 120 MHz two of
these harnesses run, one `sync` cycle is 8.3 ns and the gap was short. Only
`hyperram_ceiling_top.py` counted it outside the controller; `bootram.py` -- the
SoC -- enforced nothing at all. Now the controller holds it, so every caller
gets it.

**The latency branch says what it means.** Upstream forces the long count with
`with m.If(extra_latency | 1)`, which makes the low-latency branch dead and
carries its own FIXME. The long count is correct for this part -- CR0 reads
`0x8f2f` and bit 3 selects fixed latency -- so the behaviour is unchanged by
default. `fixed_latency=False` honours the RWDS sample for a part reprogrammed to
variable latency, which is a change to make deliberately and measure.

`HIGH_LATENCY_CLOCKS` is a constructor argument rather than a class constant, so
the count can be swept without a source edit, as it can on the DQS side.

## What is NOT changed here

Everything else is upstream's, deliberately:

  * The **clock-inversion path** -- `last_half_rwds`/`last_half_dq`, and the
    `Cat(rwds.i[1], last_half_rwds) == 0b10` branch that reassembles a word split
    across two cycles. It is the thing this controller does that the DQS one
    does not, it is untouched, and nothing here has measured which of the two
    branches the board takes.
  * The **`- 2`** on both latency loads. Upstream writes
    `HIGH_LATENCY_CLOCKS - 2` and gives no reason; every figure this project has
    recorded on the non-DQS path was taken with it, so changing it would move the
    baseline in the same commit that fixes it. `high_latency_clocks` is the knob
    for anyone who wants to sweep it.
  * The **PHY**. `HyperRAMPHY` elaborates on r1.4 and works, which is why
    `docs/upstream-boundary.md` splits the DQS path at the PHY and this one does
    not.
"""

import math

from amaranth import Cat, Const, Elaboratable, Module, Signal

# CS# high between transactions, from the W956A8 datasheet. Longer than one cycle
# at any sync above 100 MHz, which is why it cannot be left to chance.
T_CSHI_NS = 10.0

# tCSM, the longest CS# may stay Low: 4 us, W956A8 rev A01-006 Table 24, and
# section 10 makes it the HOST's obligation. Overrunning it drops a refresh, with
# no error at the transaction that caused it.
T_CSM_NS = 4000.0

# How much of tCSM one transaction may spend. The rest covers the PLL landing off
# `sync_mhz` and cycle counts that are counted states, not measured gaps. Same
# figure as `bootram.HYPERRAM_TCSM_MARGIN`.
T_CSM_MARGIN = 0.9


class HyperRAMController(Elaboratable):
    """ Gateware interface to HyperRAM series self-refreshing DRAM chips.

    I/O port:
        B: phy              -- The primary physical connection to the DRAM chip.

        I: address[32]      -- The address to be targeted by the given operation.
        I: register_space   -- When set to 1, read and write requests target registers instead of normal RAM.
        I: perform_write    -- When set to 1, a transfer request is viewed as a write, rather than a read.
        I: single_page      -- If set, data accesses will wrap around to the start of the current page when done.
        I: start_transfer   -- Strobe that goes high for 1-8 cycles to request a read operation.
                               [This added duration allows other clock domains to easily perform requests.]
        I: final_word       -- Flag that indicates the current word is the last word of the transaction.

        O: read_data[16]    -- word that holds the 16 bits most recently read from the PSRAM
        I: write_data[16]   -- word that accepts the data to output during this transaction

        O: idle             -- High whenever the transmitter is idle (and thus we can start a new piece of data.)
        O: read_ready       -- Strobe that indicates when new data is ready for reading
        O: write_ready      -- Strobe that indicates `write_data` has been latched and is ready for new data
        O: state[4]         -- Which FSM state, indexed into `STATES`. `idle` alone says
                               a stuck controller is stuck; this says where.
        O: timed_out        -- Sticky: the watchdog ended the last transaction, not the
                               caller. Cleared by the next `start_transfer`.
    """

    LOW_LATENCY_CLOCKS  = 7
    HIGH_LATENCY_CLOCKS = 14

    # The FSM encoding, which is what `state` reports. Amaranth numbers a state
    # when it is first NAMED, not where it is defined -- `WRITE_DATA` is 5 because
    # `SHIFT_COMMAND2` jumps to it before `HANDLE_LATENCY` exists. `elaborate`
    # checks this list against `fsm.encoding` and fails the build if the two drift,
    # so a rig decoding `state` decodes something verified rather than assumed.
    # See #318.
    STATES = ("IDLE", "LATCH_RWDS", "SHIFT_COMMAND0", "SHIFT_COMMAND1",
              "SHIFT_COMMAND2", "WRITE_DATA", "HANDLE_LATENCY", "READ_DATA",
              "RECOVERY")

    def __init__(self, *, phy, sync_mhz, high_latency_clocks=None,
                 fixed_latency=True, tcshi_ns=T_CSHI_NS):
        """
        Parameters:
            phy                 -- The RAM record that should be connected to this chip.
            sync_mhz            -- The `sync` clock, in MHz. Only used to turn tCSHI into
                                   a cycle count; pass the real number or CS# recovery is
                                   wrong. `HyperRAMPHY` emits one CK per `sync` cycle, so
                                   on this path it is also CK.
            high_latency_clocks -- Override the fixed-latency count. Upstream's constant
                                   is 14 and every non-DQS measurement in this repository
                                   was taken with it, so it is the default.
            fixed_latency       -- True when the part takes the long latency on every
                                   transaction, which is what CR0 = 0x8f2f selects.
            tcshi_ns            -- CS# high between transactions, in ns.
        """
        self._fixed_latency = fixed_latency
        # Rounded UP, and at least one: a gap shorter than tCSHI is the violation this
        # exists to prevent, so the rounding may only ever be generous.
        self._recovery_cycles = max(1, math.ceil(tcshi_ns * sync_mhz / 1000.0))
        if high_latency_clocks is not None:
            self.HIGH_LATENCY_CLOCKS = high_latency_clocks

        # CS#-Low cycles before the first data beat: LATCH_RWDS, three command
        # words, and HANDLE_LATENCY, which runs one cycle per remaining count plus
        # the zero cycle.
        self._data_entry_cycles = 4 + self.HIGH_LATENCY_CLOCKS - 1

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

        # Data signals.
        self.read_data        = Signal(16)
        self.write_data       = Signal(16)


    def elaborate(self, platform):
        m = Module()

        recovery_remaining = Signal(range(self._recovery_cycles + 1))

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
        latency_clocks_remaining  = Signal(range(0, self.HIGH_LATENCY_CLOCKS + 1))

        # Store last edge values of RWDS and DQ lines.
        # This is used to handle clock inversion cases.
        last_half_rwds = Signal()
        last_half_dq   = Signal(len(self.phy.dq.i)//2)

        #
        # Core operation FSM.
        #

        # Provide defaults for our control/status signals.
        m.d.sync += [
            self.phy.clk_en     .eq(1),
            self.phy.cs         .eq(1),
            self.phy.rwds.e     .eq(0),
            self.phy.dq.e       .eq(0),
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
            is_multipage,
            is_register,
            is_read
        ))

        # Keep the recovery counter loaded everywhere except RECOVERY itself, which
        # decrements it. Amaranth takes the last assignment in a domain, so the
        # state's own statement wins while it is active and this re-arms on exit.
        # Arming at each `m.next = 'RECOVERY'` instead needs every transition to
        # remember to do it -- there are three, and a patch that added the line by
        # text match got two of them at the wrong indentation.
        m.d.sync += recovery_remaining.eq(self._recovery_cycles)

        # Armed while CS# is High: loaded before the data phase rather than at each
        # of the three edges into it, as pulp does (`hyperbus_phy.sv:308`). Counts
        # `sync` cycles and not beats -- tCSM is wall-clock, so a stopped CK still
        # spends it. (#316)
        burst_remaining = Signal(range(self._burst_cycles + 1))
        burst_expired   = Signal()
        m.d.comb += burst_expired.eq(burst_remaining == 0)
        with m.If(~self.phy.cs):
            m.d.sync += burst_remaining.eq(self._burst_cycles)
        with m.Elif(~burst_expired):
            m.d.sync += burst_remaining.eq(burst_remaining - 1)

        with m.FSM() as fsm:

            # IDLE state: waits for a transaction request
            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(1)
                m.d.sync += self.phy.clk_en  .eq(0)

                # Once we have a transaction request, latch in our control
                # signals, and assert our chip-select.
                with m.If(self.start_transfer):
                    m.next = 'LATCH_RWDS'

                    m.d.sync += [
                        is_read             .eq(~self.perform_write),
                        is_register         .eq(self.register_space),
                        is_multipage        .eq(~self.single_page),
                        current_address     .eq(self.address),
                        self.phy.dq.o       .eq(0),
                        self.timed_out      .eq(0),
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)


            # LATCH_RWDS -- latch in the value of the RWDS signal,
            # which determines our read/write latency.
            with m.State("LATCH_RWDS"):
                m.d.sync += extra_latency.eq(self.phy.rwds.i),
                m.d.sync += self.phy.clk_en.eq(0)
                m.next="SHIFT_COMMAND0"


            # SHIFT_COMMANDx -- shift each of our command words out
            with m.State('SHIFT_COMMAND0'):
                # Output our first byte of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(ca[32:48]),
                    self.phy.dq.e.eq(1)
                ]
                m.next = 'SHIFT_COMMAND1'

            with m.State('SHIFT_COMMAND1'):
                m.d.sync += [
                    self.phy.dq.o.eq(ca[16:32]),
                    self.phy.dq.e.eq(1)
                ]
                m.next = 'SHIFT_COMMAND2'

            with m.State('SHIFT_COMMAND2'):
                m.d.sync += [
                    self.phy.dq.o.eq(ca[0:16]),
                    self.phy.dq.e.eq(1)
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

                    # Upstream writes `with m.If(extra_latency | 1)`, which makes the
                    # low-latency branch dead code and carries its own FIXME. The part
                    # this drives reports CR0 = 0x8f2f, whose bit 3 selects FIXED
                    # latency, so the long count IS right here -- but forcing it with
                    # `| 1` says that by accident. `fixed_latency=False` honours the
                    # RWDS sample, for a part reprogrammed to variable latency.
                    with m.If(extra_latency | int(self._fixed_latency)):
                        m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS-2)
                    with m.Else():
                        m.d.sync += latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS-2)


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

                # Store data sampled in last edge.
                m.d.sync += [
                    last_half_rwds .eq(self.phy.rwds.i[0]),
                    last_half_dq   .eq(self.phy.dq.i[:8])
                ]

                # If RWDS has changed, the host has just sent us new data.
                # Sample it, and indicate that we now have a valid piece of new data.
                with m.If(self.phy.rwds.i == 0b10):
                    m.d.comb += [
                        self.read_data     .eq(self.phy.dq.i),
                        self.read_ready    .eq(1),
                    ]

                    # If our controller is done with the transaction, end it.
                    with m.If(self.final_word):
                        m.next = 'RECOVERY'

                # Manage clock inversion: the data is divided between the current cycle
                # and the preceding one.
                with m.Elif(Cat(self.phy.rwds.i[1], last_half_rwds) == 0b10):
                    m.d.comb += [
                        self.read_data     .eq(Cat(self.phy.dq.i[8:], last_half_dq)),
                        self.read_ready    .eq(1),
                    ]

                    # If our controller is done with the transaction, end it.
                    with m.If(self.final_word):
                        m.next = 'RECOVERY'

                # THE ESCAPE (#316). Both branches above need a device beat, so
                # until this the state had no exit a silent device could reach.
                with m.If(burst_expired):
                    m.d.sync += self.timed_out.eq(1)
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
                    m.next = 'RECOVERY'

                # A caller that stops asserting `final_word` holds CS# Low for
                # ever, and the device counts that against tCSM. Same exit (#316).
                with m.If(burst_expired):
                    m.d.sync += self.timed_out.eq(1)
                    m.next = 'RECOVERY'


            # RECOVERY state: hold CS# high for tCSHI before the next transaction.
            #
            # Upstream carries `# TODO: implement recovery` here and returns to IDLE
            # unconditionally, so CS# can be re-asserted on the very next cycle. The
            # W956A8 wants 10 ns of CS# high between transactions, which is more than
            # one `sync` cycle above 100 MHz -- and this path clocks CK from `sync`,
            # so at the 120 MHz `stress` and `fifo` run, a cycle is 8.3 ns.
            #
            # `recovery_cycles` is computed from the caller's `sync_mhz`, so the count
            # follows the clock instead of being a constant that silently stops being
            # enough. Nothing here relies on the FSM above happening to take extra
            # cycles to come back around.
            #
            # Upstream already deasserts CS# here, unlike the DQS controller, which
            # only stopped the clock -- so the only thing missing was the wait. Both
            # halves are needed: counting cycles without deasserting CS# holds the
            # state and not the gap, and that arrangement is what the negative control
            # in `soc_hyperram_sim.py` section 4 caught on the DQS side.
            with m.State('RECOVERY'):
                m.d.sync += [
                    self.phy.cs     .eq(0),
                    self.phy.clk_en .eq(0),
                    last_half_rwds  .eq(0),
                    last_half_dq    .eq(0),

                ]

                m.d.sync += recovery_remaining.eq(recovery_remaining - 1)
                with m.If(recovery_remaining == 0):
                    m.next = 'IDLE'

        if tuple(fsm.encoding) != self.STATES or len(self.STATES) > 16:
            raise ValueError(
                f"STATES does not describe this FSM: declared {self.STATES}, "
                f"elaborated {tuple(fsm.encoding)}")
        m.d.comb += self.state.eq(fsm.state)

        return m
