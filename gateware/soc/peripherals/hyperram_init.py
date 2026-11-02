#!/usr/bin/env python3
#
# HyperRAM CR0/CR1 init sequencer: gates traffic until the part is configured and read back.
# SPDX-License-Identifier: BSD-3-Clause

"""
`HyperRAMInit` -- reset, power-up wait, CR0 write, CR1 write, read-back, then release. (#319)

## Behaviour
  * Owns the controller's request inputs until `ready`; after that `req_*` pass through.
  * `req_start_transfer` is forced low until `ready`.
  * Each register write is ONE `start_transfer` cycle, issued only while `idle`.
    Held, CS# never rises between back-to-back register writes.
  * `write_data` is held until `idle` returns (the controller latches it in WRITE_DATA).
  * Read-back mismatch, a transaction ending `timed_out`, or no read word -> `error`;
    `ready` stays low and the sequencer parks. `restart` re-runs it.

## Register values
  * CR0 = DPD off | drive[14:12] | 1111 | latency[7:4] | fixed[3] | 1 | 11.
    Reserved CR0[11:8] stays at its default (datasheet 9.2).
  * CR1 = 0xffc1, with CR1[6] cleared for differential clock. CR1[1:0] = 01 is the
    only legal value. docs/chips/hyperram/w956a8.md.
  * Latency codes: 1110=3 1111=4 0000=5 0001=6 0010=7 clocks. Not applied to the
    controller's own latency inputs; the caller owns that.
"""

import math

from amaranth import Elaboratable, Module, Mux, ResetInserter, Signal

# docs/chips/hyperram/config-ac.md: tRP 200 ns (RESET# low), tRPH 400 ns, tVCS 150 us.
T_RP_NS = 200.0
T_RPH_NS = 400.0
T_VCS_NS = 150_000.0

CR0_ADDRESS = 0x0800
CR1_ADDRESS = 0x0801

LATENCY_CODES = {3: 0b1110, 4: 0b1111, 5: 0b0000, 6: 0b0001, 7: 0b0010}

STEPS = ("W_CR0", "W_CR1", "R_CR0", "R_CR1")


def cr0_value(*, latency_clocks=7, fixed_latency=True, drive=0b000):
    """CR0 with every reserved field at its default."""
    if latency_clocks not in LATENCY_CODES:
        raise ValueError(f"latency {latency_clocks} not in {sorted(LATENCY_CODES)}")
    if not 0 <= drive <= 0b111:
        raise ValueError("drive is 3 bits")
    return ((1 << 15) | (drive << 12) | (0b1111 << 8)
            | (LATENCY_CODES[latency_clocks] << 4)
            | (int(fixed_latency) << 3) | (1 << 2) | 0b11)


def cr1_value(*, differential_clock=False):
    """CR1 default 0xffc1; CR1[6] = 0 selects the CK/CK# crossing."""
    return 0xffc1 & ~((1 if differential_clock else 0) << 6)


def cycles(ns, sync_mhz):
    return max(1, math.ceil(ns * sync_mhz / 1000.0))


class HyperRAMInit(Elaboratable):
    """
    Parameters:
        ctrl      -- a `HyperRAMController`; its request inputs are driven from here.
        phy       -- the controller's PHY record; `phy.reset` (high = RESET# asserted).
        sync_mhz  -- controller clock, turns the ns waits into cycle counts.
        txn_cycles -- upper bound on one register transaction, in sync cycles.

    I: req_*         -- the user's request, passed to `ctrl` once `ready`
    I: restart       -- strobe; runs the whole sequence again
    O: ready         -- configured and verified; traffic released
    O: error         -- sticky until `restart`
    O: mismatch, timeout -- which kind of error
    O: cr0_read, cr1_read -- what the part returned
    """

    def __init__(self, *, ctrl, phy, sync_mhz, latency_clocks=7,
                 fixed_latency=True, drive=0b000, differential_clock=False,
                 txn_cycles=None):
        self.ctrl, self.phy = ctrl, phy
        self.cr0 = cr0_value(latency_clocks=latency_clocks,
                             fixed_latency=fixed_latency, drive=drive)
        self.cr1 = cr1_value(differential_clock=differential_clock)
        self.reset_cycles = cycles(T_RP_NS, sync_mhz)
        # tVCS (150 us) >= tRPH (400 ns): one wait covers both.
        self.powerup_cycles = cycles(max(T_VCS_NS, T_RPH_NS), sync_mhz)
        # Expected worst case: CS_SETUP + 3 CA + latency + data + recovery, x1.25.
        if txn_cycles is None:
            expected = 8 + ctrl.HIGH_LATENCY_CLOCKS + ctrl._max_recovery_cycles
            txn_cycles = math.ceil(1.25 * expected)
        self.txn_cycles = txn_cycles

        self.req_start_transfer = Signal()
        self.req_address        = Signal(32)
        self.req_register_space = Signal()
        self.req_perform_write  = Signal()
        self.req_single_page    = Signal()
        self.req_final_word     = Signal()
        self.req_write_data     = Signal(16)
        self.restart            = Signal()
        self.ready              = Signal()
        self.error              = Signal()
        self.mismatch           = Signal()
        self.timeout            = Signal()
        self.cr0_read           = Signal(16)
        self.cr1_read           = Signal(16)

    def elaborate(self, platform):
        m = Module()
        ctrl = self.ctrl
        step = Signal(range(len(STEPS)))
        got = Signal()
        wait = Signal(range(max(self.powerup_cycles, self.reset_cycles,
                                self.txn_cycles) + 1))

        owned = Signal()
        issue = Signal()
        is_write = Signal()
        m.d.comb += is_write.eq(step < 2)

        with m.If(owned):
            m.d.comb += [
                ctrl.register_space.eq(1),
                ctrl.single_page.eq(0),
                ctrl.final_word.eq(1),
                ctrl.perform_write.eq(is_write),
                ctrl.address.eq(Mux(step[0], CR1_ADDRESS, CR0_ADDRESS)),
                ctrl.write_data.eq(Mux(step[0], self.cr1, self.cr0)),
                ctrl.start_transfer.eq(issue & ctrl.idle),
            ]
        with m.Else():
            m.d.comb += [
                ctrl.register_space.eq(self.req_register_space),
                ctrl.single_page.eq(self.req_single_page),
                ctrl.final_word.eq(self.req_final_word),
                ctrl.perform_write.eq(self.req_perform_write),
                ctrl.address.eq(self.req_address),
                ctrl.write_data.eq(self.req_write_data),
                ctrl.start_transfer.eq(self.req_start_transfer & self.ready),
            ]

        with m.FSM():
            with m.State("RESET"):
                m.d.comb += [owned.eq(1), self.phy.reset.eq(1)]
                m.d.sync += wait.eq(wait + 1)
                with m.If(wait == self.reset_cycles - 1):
                    m.d.sync += wait.eq(0)
                    m.next = "POWERUP"

            with m.State("POWERUP"):
                m.d.comb += owned.eq(1)
                m.d.sync += wait.eq(wait + 1)
                with m.If(wait == self.powerup_cycles - 1):
                    m.d.sync += [wait.eq(0), step.eq(0)]
                    m.next = "ISSUE"

            with m.State("ISSUE"):
                m.d.comb += [owned.eq(1), issue.eq(1)]
                with m.If(ctrl.idle):
                    m.d.sync += [wait.eq(0), got.eq(0)]
                    m.next = "WAIT"

            with m.State("WAIT"):
                m.d.comb += owned.eq(1)
                m.d.sync += wait.eq(wait + 1)
                with m.If(ctrl.read_ready):
                    m.d.sync += got.eq(1)
                    with m.If(step == 2):
                        m.d.sync += self.cr0_read.eq(ctrl.read_data)
                    with m.If(step == 3):
                        m.d.sync += self.cr1_read.eq(ctrl.read_data)
                with m.If(ctrl.idle):
                    with m.If(ctrl.timed_out | (~is_write & ~got)):
                        m.d.sync += [self.error.eq(1), self.timeout.eq(1)]
                        m.next = "FAULT"
                    with m.Elif(step == len(STEPS) - 1):
                        m.next = "CHECK"
                    with m.Else():
                        m.d.sync += step.eq(step + 1)
                        m.next = "ISSUE"
                with m.Elif(wait == self.txn_cycles - 1):
                    m.d.sync += [self.error.eq(1), self.timeout.eq(1)]
                    m.next = "FAULT"

            with m.State("CHECK"):
                m.d.comb += owned.eq(1)
                with m.If((self.cr0_read == self.cr0) & (self.cr1_read == self.cr1)):
                    m.next = "READY"
                with m.Else():
                    m.d.sync += [self.error.eq(1), self.mismatch.eq(1)]
                    m.next = "FAULT"

            with m.State("READY"):
                m.d.comb += self.ready.eq(1)

            with m.State("FAULT"):
                m.d.comb += owned.eq(1)

        return ResetInserter({"sync": self.restart})(m)
