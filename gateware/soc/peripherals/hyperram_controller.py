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

**And it samples RWDS when the device has answered.** Upstream reads it in
SHIFT_COMMAND1/2; through `HyperRAMPHY` those are pin cycles 2 and 3 against a
CS# that falls at 4, so the branch decided on an undriven bus and an idle-line
level chose the latency. Measured in `scripts/hyperram_phy_rwds_sim.py`, where a
weak pull on RWDS while CS# is High moved every variable-latency election. The
sample now follows `phy_round_trip_cycles`; `fixed_latency` is untouched by any
of it. (#338)

**And READ_DATA cannot begin before the CA-period RWDS request has fallen.** That
request is a LEVEL held High over the CA, not a strobe, and the PHY's round trip
put the data phase early enough at code 14 (2L = 6) to latch its fall as a read
strobe over a tristate bus. The LONG count is floored at
`min_read_latency_clocks` on reads; writes are untouched. (#353)

`HIGH_LATENCY_CLOCKS` is a constructor argument rather than a class constant, so
the count can be swept without a source edit, as it can on the DQS side.

**Every timing parameter is a runtime input.** `latency_clocks`,
`low_latency_clocks`, `fixed_latency`, `recovery_cycles`, `burst_cycles`,
`burst_beats` and `min_latency_clocks` are `Signal`s reset to the build-time
value, so an undriven caller is unchanged and yosys folds them away. Three
separate defects were one habit -- a swept axis whose consumer held a constant
(#331, #338), and the audit's "`T_CSHI_NS = 10.0` is right" was a datasheet
reading of the wrong grade's column with no way to measure it. (#341)

**And a register access says so.** `register_active` is High while a register
transaction holds CS# Low; anything gating CK beneath this controller must hold
off while it is, because the 2025 app note replaces the datasheet's clock-stop
sentence with "recommended not to stop the clock during register access". (#340)

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

from amaranth import Cat, Const, Elaboratable, Module, Mux, Signal

# Winbond's own AC parameters per speed grade, from `Config-AC.v`. tACC and tRWR
# are ONE number under two names at every grade. `docs/chips/hyperram/config-ac.md`.
#   grade: (tCK, tCSHI, tACC == tRWR), all ns
GRADE_AC_NS = {
    "T100": (10.0, 10.0, 40.0),
    "T133": (7.5, 7.5, 37.5),
    "T166": (6.0, 6.0, 36.0),
    "T200": (5.0, 6.0, 35.0),
    "T250": (4.0, 6.0, 28.0),
}

# The part on this board: a `6I` -- packaged, 3.0 V, 166 MHz. T250 is the KGD
# block and does not describe a packaged part.
FITTED_GRADE = "T166"

# CS# high between transactions. Was 10.0, which is the T100 column applied to a
# T166 part: safe, and a recovery cycle per transaction at sync 166. (#341)
T_CSHI_NS = GRADE_AC_NS[FITTED_GRADE][1]

# tRWR, the recovery a transaction following a write observes, measured from CS#
# falling to first data -- not a CS#-high gap. Equal to tACC, and the reason the
# minimum latency code exists. Implemented as `min_latency_clocks`. (#341)
T_RWR_NS = GRADE_AC_NS[FITTED_GRADE][2]

# tCSM, the longest CS# may stay Low: 4 us, W956A8 rev A01-006 Table 24, and
# section 10 makes it the HOST's obligation. Overrunning it drops a refresh, with
# no error at the transaction that caused it.
T_CSM_NS = 4000.0

# How much of tCSM one transaction may spend. The rest covers the PLL landing off
# `sync_mhz` and cycle counts that are counted states, not measured gaps. Same
# figure as `bootram.HYPERRAM_TCSM_MARGIN`.
T_CSM_MARGIN = 0.9

# Sync cycles between this FSM emitting something and its consequence arriving back:
# upstream's `HyperRAMPHY` is 3 out (`FFSynchronizer(stages=3)` on CS#/OE,
# `ODDRX1F` on CK/DQ/RWDS) and 1 back (`IDDRX1F`). Both measured by
# `scripts/hyperram_phy_rwds_sim.py`. The RWDS sample instant is derived from this,
# so a harness with a different PHY -- or none -- must say so. (#338)
PHY_ROUND_TRIP_CYCLES = 4


def min_latency_code(ck_mhz, t_acc_ns=T_RWR_NS):
    """Smallest legal CR0[7:4] latency code at this CK, in CK cycles. (#341)

    `ceil(tACC / tCK)` reproduces the datasheet's minimum-code-per-frequency
    column at all five grades, so the table is a derived quantity rather than a
    lookup -- and this answers at frequencies nobody tabulated. tRWR == tACC, so
    the same number covers both.
    """
    return max(1, math.ceil(t_acc_ns * ck_mhz / 1000.0))


def min_read_latency_clocks(phy_round_trip_cycles=PHY_ROUND_TRIP_CYCLES):
    """Smallest `latency_clocks` a READ may take, in half-clocks. (#353)

    The device holds RWDS High over the CA as its extra-latency request and drops it
    in pin cycle 6+P; the IDDR shows that fall at R+6. `READ_DATA` begins at
    `xact_age` 4 + `latency_clocks`, so anything below R+3 enters while the fall is
    still in flight and latches it as a read strobe over a tristate bus.
    """
    return phy_round_trip_cycles + 3


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
    STATES = ("IDLE", "CS_SETUP", "SHIFT_COMMAND0", "SHIFT_COMMAND1",
              "SHIFT_COMMAND2", "WRITE_DATA", "HANDLE_LATENCY", "READ_DATA",
              "RECOVERY")

    def __init__(self, *, phy, sync_mhz, high_latency_clocks=None,
                 max_latency_clocks=None, fixed_latency=True,
                 tcshi_ns=T_CSHI_NS, trwr_ns=T_RWR_NS, tcsm_ns=T_CSM_NS,
                 max_recovery_cycles=None,
                 phy_round_trip_cycles=PHY_ROUND_TRIP_CYCLES):
        """
        Parameters:
            phy                 -- The RAM record that should be connected to this chip.
            sync_mhz            -- The `sync` clock, in MHz. Only used to turn tCSHI into
                                   a cycle count; pass the real number or CS# recovery is
                                   wrong. `HyperRAMPHY` emits one CK per `sync` cycle, so
                                   on this path it is also CK.
            high_latency_clocks -- Override the fixed-latency count. Upstream's constant
                                   is 14 and every non-DQS measurement in this repository
                                   was taken with it, so it is the default. It is the
                                   RESET value of `latency_clocks`, not a hard wiring.
            max_latency_clocks  -- Ceiling for `latency_clocks`, which sizes the signal
                                   and sets the worst case the tCSM check assumes.
                                   Defaults to `high_latency_clocks`, i.e. a fixed count.
            fixed_latency       -- True when the part takes the long latency on every
                                   transaction, which is what CR0 = 0x8f2f selects.
            tcshi_ns            -- CS# high between transactions, in ns. RESET value
                                   of `recovery_cycles`.
            trwr_ns             -- tRWR/tACC, in ns. RESET value of
                                   `min_latency_clocks`, the floor `latency_below_trwr`
                                   reports against.
            tcsm_ns             -- Longest CS# may stay Low, in ns. Sets
                                   `_burst_cycles`. A lever so a sim can build a
                                   controller whose chop lands PAST tCSM, which is
                                   the only way to show a tCSM check failing. Also
                                   the 1 us above 85 C -- see #341, #317.
            max_recovery_cycles -- Ceiling for `recovery_cycles`, which sizes it.
                                   Defaults to the count `tcshi_ns` gives, i.e. a
                                   lever that can only shorten the gap.
            phy_round_trip_cycles -- Sync cycles between this FSM emitting a signal
                                   and the device's answer to it arriving back. Sets
                                   WHEN the extra-latency RWDS sample is taken; see
                                   `_rwds_sample_cycle`. 4 is upstream's
                                   `HyperRAMPHY`, 0 a record wired straight to a
                                   model.
        """
        self._fixed_latency = fixed_latency
        # WHEN to sample RWDS for the extra-latency request, counted in sync cycles
        # from `start_transfer`. Derivation, with R = `phy_round_trip_cycles` split
        # into P out and Q back (R = P + Q):
        #
        #   CS# falls at the pin       1 + P
        #   CA occupies pin cycles     3 + P .. 5 + P
        #   the request is valid       2 + P .. 4 + P
        #   the controller sees pin M  at cycle M + Q
        #   this FSM's cycles          1 CS_SETUP, 2..4 SHIFT_COMMANDx, 5.. latency
        #
        # so the sample belongs in cycle R+2 .. R+4. The window opens one cycle after
        # CS# because tDSV (12 ns) is under one sync cycle but not zero, and closes
        # one cycle before the CA ends because the device drops RWDS on the last CA
        # edge of a write. R+2 is the earliest, which is the one that leaves the most
        # room inside the SHORT count at the shortest latency code.
        #
        # With R = 4 that is cycle 6, the second cycle of HANDLE_LATENCY, and the
        # upgrade there has to fit inside the short count: `low_latency_clocks` >= 3,
        # which every code the part offers satisfies.
        #
        # It was sampled in SHIFT_COMMAND1 and SHIFT_COMMAND2, cycles 3 and 4. With
        # R = 4 the window starts at 6, so both landed on pin cycles 2 and 3 against
        # a CS# that falls at 4 -- a deselected, undriven bus. (#338)
        self._rwds_sample_cycle = phy_round_trip_cycles + 2
        # Floor on the LONG count, for READS only -- see `min_read_latency_clocks`.
        # Writes keep the count they were given: their data phase has to land where
        # the device expects it, and WRITE_DATA never looks at RWDS. (#353)
        self._min_read_latency_clocks = min_read_latency_clocks(phy_round_trip_cycles)
        # Rounded UP, and at least one: a gap shorter than tCSHI is the violation this
        # exists to prevent, so the rounding may only ever be generous.
        self._recovery_cycles = max(1, math.ceil(tcshi_ns * sync_mhz / 1000.0))
        self._max_recovery_cycles = max(self._recovery_cycles,
                                        max_recovery_cycles or 0)
        # The floor tRWR/tACC puts under the latency, in CK. Reported, never
        # enforced: the DEVICE waits what CR0[7:4] says, so a controller that
        # silently waited longer would miss the data rather than fix anything.
        self._min_latency_clocks = min_latency_code(sync_mhz, trwr_ns)
        if high_latency_clocks is not None:
            self.HIGH_LATENCY_CLOCKS = high_latency_clocks
        # The ceiling `latency_clocks` may be driven to, which sizes it and which
        # the tCSM check below must assume. Defaults to the fixed count, so a
        # caller that never drives the input gets exactly that count.
        self._max_latency_clocks = max(self.HIGH_LATENCY_CLOCKS,
                                       max_latency_clocks or 0)
        # The count HANDLE_LATENCY can actually run to, which is the ceiling or the
        # read floor, whichever is higher.
        self._max_wait_clocks = max(self._max_latency_clocks,
                                    self._min_read_latency_clocks)

        # CS#-Low cycles before the first data beat: CS_SETUP, three command
        # words, and HANDLE_LATENCY, which runs one cycle per remaining count plus
        # the zero cycle. Worst case, so the tCSM check covers the longest latency
        # the caller can select at runtime.
        self._data_entry_cycles = 4 + self._max_wait_clocks - 1

        # WATCHDOG on READ_DATA/WRITE_DATA, whose only exit was a device beat
        # meeting `final_word` -- 7 of 8 beats hung for ever (#316). Waits for the
        # caller to end the burst; bounded by tCSM because the burst length is the
        # caller's and tCSM is its only ceiling. 0.9 is a margin BELOW a limit the
        # part imposes, not 1.25x above an expected duration. Less two cycles: the
        # exit into RECOVERY leaves CS# Low for two more. Expiry sets `timed_out`.
        self._tcsm_ns = tcsm_ns
        self._burst_cycles = int(tcsm_ns * T_CSM_MARGIN * sync_mhz / 1000.0) - 2
        if self._burst_cycles <= self._data_entry_cycles:
            raise ValueError(
                f"tCSM allows {self._burst_cycles} cycles at sync {sync_mhz} MHz, "
                f"which does not cover {self._data_entry_cycles} cycles of command "
                f"and latency: no transaction could complete inside tCSM")

        # The same budget in BEATS, one cycle ahead of the watchdog, so a burst
        # nobody ends is chopped on a whole word -- the clock-inversion path
        # assembles one out of two cycles. Reaching this means the device kept up;
        # the watchdog behind it means it stopped. Callers keep their own cap
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
        # HALF-CLOCKS of initial latency to wait before the data body. Reset is the
        # build-time constant, so a caller that leaves it alone behaves as before
        # and yosys folds it away. Drive it to sweep the controller in step with
        # the part's CR0[7:4], which is the whole point -- moving one side only
        # can never pass more than a single code (#331).
        self.latency_clocks   = Signal(range(0, self._max_latency_clocks + 1),
                                       reset=self.HIGH_LATENCY_CLOCKS)
        # CR0[3] AS THE PART IS SET TO, so the controller follows the part rather
        # than being welded to one mode. High takes the long count on every
        # transaction; low honours the RWDS sample. Reset is the build-time value,
        # so an undriven caller is unchanged.
        # A constant folded into the branch condition kills the variable path
        # whenever the constructor says True: the sweep moves the PART into
        # variable mode and the controller cannot respond (#338).
        self.fixed_latency    = Signal(reset=int(fixed_latency))
        # The count for the SHORT branch, when the part declines the extra
        # latency. Fixed latency is 2L and variable is 1L, so this is half
        # `latency_clocks` -- but it is an input rather than a derivation because
        # the derivation is the datasheet's, not the silicon's, and this rig
        # exists to test that kind of claim.
        # A class constant here is a swept axis whose consumer never moves: the
        # part elects the short latency and the controller waits a number with
        # nothing to do with the code (#331, #338).
        self.low_latency_clocks = Signal(range(0, self._max_latency_clocks + 1),
                                         reset=self.LOW_LATENCY_CLOCKS)
        # tCSHI, in whole sync cycles, as RECOVERY will count it. Reset is the
        # `tcshi_ns` arithmetic, so an undriven caller is unchanged; drive it to
        # sweep the one parameter that binds hardest as CK rises. The gap CS# is
        # actually High is this plus one -- RECOVERY drives CS# from its second
        # cycle and IDLE holds it for one more.
        # A constant here is unfalsifiable: "T_CSHI_NS = 10.0 is right" stays a
        # datasheet reading with no way to measure it (#341).
        self.recovery_cycles  = Signal(range(0, self._max_recovery_cycles + 1),
                                       reset=self._recovery_cycles)
        # The floor tRWR puts under the initial latency, in CK: `ceil(tRWR/tCK)`,
        # which is also the datasheet's minimum CR0[7:4] at this CK because
        # tRWR == tACC. Only `latency_below_trwr` reads it. (#341)
        self.min_latency_clocks = Signal(range(0, self._max_latency_clocks + 1),
                                         reset=min(self._min_latency_clocks,
                                                   self._max_latency_clocks))
        # The tCSM watchdog bound and the same budget in beats. Their ceiling IS
        # the tCSM arithmetic, so the lever can only ever SHORTEN the leash --
        # the RISC-V may test the watchdog, and cannot use it to overrun tCSM.
        # Safety limits rather than tuning, but making them settable is what lets
        # tCSM behaviour be tested rather than assumed. (#341)
        self.burst_cycles     = Signal(range(0, self._burst_cycles + 1),
                                       reset=self._burst_cycles)
        self.burst_beats      = Signal(range(0, self._burst_beats + 1),
                                       reset=self._burst_beats)

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
        # The device asked for the extra latency on this transaction, latched at the
        # sample and held to the end of it. Brought out so a rig can COUNT elections
        # per cell, which is the measurement #338 asks for and which no register
        # currently reports. Always 0 under `fixed_latency`, where nothing is asked.
        self.extra_latency    = Signal()
        # The configured latency does not cover tRWR/tACC at this CK, so the
        # CR0[7:4] the part is set to is illegal here. Combinational over the
        # configuration, not per transaction: under fixed latency the count is
        # always `latency_clocks`, and under variable the SHORT branch is the one
        # that can be taken, so that is the binding one.
        #
        # tRWR was implemented nowhere and covered only by LC7 spending 7 CK --
        # an accident, and one that goes if the code drops. (#341)
        self.latency_below_trwr = Signal()
        # This transaction is a REGISTER access and CS# is Low. Winbond's 2025
        # app note 7.2.2 replaces the datasheet's clock-stop sentence with
        # "recommended not to stop the clock during register access", and register
        # accesses run through the same FSM as memory ones -- so a master that
        # stalls mid-transaction stops CK during one. Anything gating CK below
        # this controller must hold off while it is High. (#340)
        self.register_active  = Signal()

        # Data signals.
        self.read_data        = Signal(16)
        self.write_data       = Signal(16)


    def elaborate(self, platform):
        m = Module()

        recovery_remaining = Signal(range(self._max_recovery_cycles + 1))

        m.d.comb += self.latency_below_trwr.eq(
            Mux(self.fixed_latency,
                self.latency_clocks < self.min_latency_clocks,
                self.low_latency_clocks < self.min_latency_clocks))

        #
        # Latched control/addressing signals.
        #
        is_read         = Signal()
        is_register     = Signal()
        current_address = Signal(32)
        is_multipage    = Signal()

        # Both latched at IDLE, so this is High from CS_SETUP to the cycle
        # RECOVERY drops CS#.
        m.d.comb += self.register_active.eq(is_register & self.phy.cs)

        #
        # FSM datapath signals.
        #

        # Tracks whether we need to add an extra latency period between our
        # command and the data body.
        extra_latency   = self.extra_latency

        # Tracks how many cycles of latency we have remaining between a command
        # and the relevant data stages.
        latency_clocks_remaining  = Signal(range(0, self._max_wait_clocks + 1))

        # The LONG count as this transaction will actually wait it: floored on a READ
        # so READ_DATA cannot begin while the CA-period RWDS request is still in
        # flight. See `min_read_latency_clocks`. (#353)
        long_latency = Signal(range(0, self._max_wait_clocks + 1))
        m.d.comb += long_latency.eq(
            Mux(is_read & (self.latency_clocks < self._min_read_latency_clocks),
                self._min_read_latency_clocks, self.latency_clocks))

        # Sync cycles since `start_transfer`, stopped one past the sample so the
        # counter cannot wrap and the sample is a single cycle. Which cycle that is,
        # and why it is not a state, is `_rwds_sample_cycle`.
        xact_age = Signal(range(0, self._rwds_sample_cycle + 2))
        sample_now = Signal()
        rwds_asks  = Signal()
        m.d.comb += [
            sample_now.eq(xact_age == self._rwds_sample_cycle),
            # `rwds.i[0]` is `IDDRX1F`'s NEGATIVE-edge capture, the later of the
            # pair, so where tDSV crosses a cycle boundary it is still the driven
            # half. The request is a level held for the whole CA, so one half-beat is
            # the whole answer -- `.any()` ORs the other half's float back in.
            rwds_asks.eq(sample_now & ~self.fixed_latency & self.phy.rwds.i[0]),
        ]
        with m.If(xact_age <= self._rwds_sample_cycle):
            m.d.sync += xact_age.eq(xact_age + 1)
        with m.If(rwds_asks):
            m.d.sync += extra_latency.eq(1)

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
                        xact_age            .eq(1),
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)


            # CS_SETUP -- CS# is Low and CK is still stopped, covering the
            # registered `dq.o` reaching the pins. It sampled RWDS here until
            # #321, which is before the CA the device answers with: the value
            # latched was whatever the PREVIOUS transaction left.
            with m.State("CS_SETUP"):
                m.d.sync += self.phy.clk_en.eq(0)
                m.next = "SHIFT_COMMAND0"


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
                # NO RWDS SAMPLE HERE. This is cycle 3, which the PHY puts on the
                # pins at cycle 6 -- the device has not even been selected yet. The
                # sample is taken in HANDLE_LATENCY. (#338)
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

                    # CR0 = 0x8f2f selects FIXED latency, so the long count is
                    # right for this part; upstream's `extra_latency | 1` says so
                    # by accident. Under variable latency the answer may not have
                    # arrived yet -- with upstream's PHY it arrives two cycles from
                    # now -- so this loads the SHORT count and HANDLE_LATENCY
                    # upgrades it in place. `rwds_asks` covers the case where the
                    # sample IS this cycle, which is a shallower PHY. (#338)
                    with m.If(self.fixed_latency | extra_latency | rwds_asks):
                        # Clamped: the two cycles come off HANDLE_LATENCY's own
                        # entry and exit, so a count below 2 would wrap the
                        # counter and wait ~2^n cycles instead of none.
                        m.d.sync += latency_clocks_remaining.eq(
                            Mux(long_latency >= 2, long_latency - 2, 0))
                    with m.Else():
                        m.d.sync += latency_clocks_remaining.eq(
                            Mux(self.low_latency_clocks >= 2,
                                self.low_latency_clocks - 2, 0))


            # HANDLE_LATENCY -- applies clock cycles until our latency period is over,
            # and it is where the device's extra-latency request is read.
            with m.State('HANDLE_LATENCY'):
                # SHORT plus the difference, applied in place. The count is still
                # running, so honouring the request costs no cycle of its own --
                # which is what makes a decision this late possible at every code.
                extra_wait = Mux(long_latency >= self.low_latency_clocks,
                                 long_latency - self.low_latency_clocks, 0)
                with m.If(rwds_asks):
                    m.d.sync += latency_clocks_remaining.eq(
                        latency_clocks_remaining - 1 + extra_wait)
                with m.Else():
                    m.d.sync += latency_clocks_remaining.eq(latency_clocks_remaining - 1)

                # `~rwds_asks` matters at the shortest code: with L = 3 the short
                # count reaches zero on the sample cycle itself, and exiting there
                # would leave the upgrade nowhere to happen.
                with m.If((latency_clocks_remaining == 0) & ~rwds_asks):
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

                # The escape (#316) and the tCSM chop (#317), one exit: both
                # branches above need a device beat, and neither bounds how long a
                # live device may be held selected.
                with m.If(burst_expired | (self.read_ready & beat_cap)):
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
