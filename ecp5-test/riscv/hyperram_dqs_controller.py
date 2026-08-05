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
controller, but `vexii_bootram.py` -- the SoC, the thing that actually runs --
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

from amaranth import (Cat, ClockSignal, Const, Elaboratable, Instance, Module,
                      Record, ResetSignal, Signal)
from amaranth.hdl.rec import DIR_FANIN, DIR_FANOUT
from amaranth.lib.cdc import FFSynchronizer

# CS# high between transactions, from the W956A8 datasheet. Longer than one cycle
# at any sync above 100 MHz, which is why it cannot be left to chance.
T_CSHI_NS = 10.0


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
    """

    LOW_LATENCY_CLOCKS  = 3
    HIGH_LATENCY_CLOCKS = 5

    def __init__(self, *, phy, sync_mhz, high_latency_clocks=None,
                 fixed_latency=True, tcshi_ns=T_CSHI_NS):
        """
        Parameters:
            phy                 -- The RAM record that should be connected to this chip.
            sync_mhz            -- The `sync` clock, in MHz. Only used to turn tCSHI into
                                   a cycle count; pass the real number or CS# recovery is
                                   wrong.
            high_latency_clocks -- Override the fixed-latency count. Upstream's constant
                                   is 5, which at 4:1 gearing is 10 CK and lands the
                                   32-bit group at least one word late on this board.
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

        # Data signals.
        self.read_data        = Signal(32)
        self.write_data       = Signal(32)


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
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)


            # LATCH_RWDS -- latch in the value of the RWDS signal,
            # which determines our read/write latency.
            with m.State("LATCH_RWDS"):
                m.d.sync += extra_latency.eq(self.phy.rwds.i),
                m.d.sync += self.phy.clk_en.eq(0b11)
                m.next="SHIFT_COMMAND0"


            # SHIFT_COMMANDx -- shift each of our command words out
            with m.State('SHIFT_COMMAND0'):
                # Output the first 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(ca[16:48])),
                    self.phy.dq.e.eq(1),
                ]
                m.next = 'SHIFT_COMMAND1'

            with m.State('SHIFT_COMMAND1'):
                # Output the remaining 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(Const(0, 16), ca[0:16])),
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

                    # Upstream writes `with m.If(extra_latency | 1)`, which makes the
                    # low-latency branch dead code and carries its own FIXME. The part
                    # this drives reports CR0 = 0x8f2f, whose bit 3 selects FIXED
                    # latency, so the long count IS right here -- but forcing it with
                    # `| 1` says that by accident. `fixed_latency=False` honours the
                    # RWDS sample, for a part reprogrammed to variable latency.
                    with m.If(extra_latency | int(self._fixed_latency)):
                        m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS)
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
                    m.next = 'IDLE'

                with m.Elif(self.final_word):
                    m.next = 'RECOVERY'


            # RECOVERY state: hold CS# high for tCSHI before the next transaction.
            #
            # Upstream carries `# TODO: implement recovery` here and falls straight
            # through to IDLE, so CS# can be re-asserted on the very next cycle. The
            # W956A8 wants 10 ns of CS# high between transactions, which is more than
            # one `sync` cycle above 100 MHz -- at CK 180 (sync 90) a cycle is 11.1 ns
            # and the margin is about a nanosecond.
            #
            # `recovery_cycles` is computed from the caller's `sync_mhz`, so the count
            # follows the clock instead of being a constant that silently stops being
            # enough. Nothing here relies on the FSM above happening to take extra
            # cycles to come back around.
            with m.State('RECOVERY'):
                m.d.sync += self.phy.clk_en .eq(0)

                # DEASSERT CS#. tCSHI is CS#-HIGH time, so counting cycles here
                # without this holds the state and not the gap -- which is what a
                # first attempt did, and what the negative control in
                # `soc_hyperram_sim.py` section 4 caught immediately. The
                # per-cycle defaults above assert `cs` on every cycle, and only
                # IDLE's no-request branch clears it, so upstream's fall-through
                # left CS# low from one transaction straight into the next.
                m.d.sync += self.phy.cs.eq(0)

                m.d.sync += recovery_remaining.eq(recovery_remaining - 1)
                with m.If(recovery_remaining == 0):
                    m.next = 'IDLE'



        return m


