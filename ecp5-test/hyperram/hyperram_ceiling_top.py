#!/usr/bin/env python3
#
# Where the HyperRAM stops working, measured against the clock the part sees.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sustained write/read/verify against HyperRAM, at an arbitrary clock, either PHY.

Driven by `scripts/hyperram_ceiling.py`. Building this file directly is for
checking that a frequency places at all:

    python3 ecp5-test/hyperram/hyperram_ceiling_top.py --build --sync-mhz 100 --dqs

`--build` never programs the board.

## The x-axis is CK, not `sync`

The two PHYs clock the part differently, and comparing them by `sync` compares
nothing:

| PHY | gearing | bits per `sync` cycle | **device CK** |
|---|---|---|---|
| `HyperRAMPHY` (non-DQS) | `ODDRX1F`, 2:1 | 16 | `sync` |
| `HyperRAMDQSPHY` (DQS) | `ODDRX2F`, 4:1 | 32 | **2 x `sync`** |

`ODDRX2F` emits `0, clk_en[1], 0, clk_en[0]` per `sync` cycle, so with
`clk_en = 0b11` the outgoing clock toggles twice per `sync` cycle. A DQS build at
`sync` 100 MHz runs the part at 200 MHz -- already past its 166 MHz rating --
while a non-DQS build at `sync` 100 runs it at 100. Everything here is reported
against CK so the two ladders lie on one axis.

## tCSM caps the burst, and the previous measurement exceeded it

CR1 reads `0xffc1`; CR1[1:0] = `01b` is **4 us tCSM**, the maximum time CS# may
stay low. Refresh is distributed and cannot run while CS# is low, so a burst
longer than that is not slow, it is illegal -- and it fails by forgetting later
rather than by returning anything wrong at the time.

`hyperram_speed.py` moved 2048 words in one transaction: 17 us at 120 MHz, over
four times tCSM. Its 220.2 MB/s is therefore a rate the part is not specified to
sustain. `BURST_WORDS` here is 128, which is 2.13 us of CS# low at the slowest
clock swept and less above it, so every transaction is legal and the throughput
reported is one the part can actually hold.

## What makes a pass believable

**BURSTDET.** With fixed latency set in CR0 the part takes the long count every
time, so a read can come back clean because the count landed right rather than
because the strobe was found. `burstdet_seen` is latched from `DQSBUFM` and
reported; a DQS pass with it clear has not demonstrated DQS.

**The DDRDLL lock.** The whole read path's delay codes are invalid until
`DDRDLLA` locks, so `dll_locked`/`dll_ready` are reported too.

**An address-derived pattern.** A controller that stopped advancing its address
would return one word forever, and a constant fill would score that as perfect.

**Every mismatch counted, not the first.** One bad word in a million and a
million bad words are different faults. The first mismatch is recorded with its
index, what arrived and what was due, because *how* it is wrong separates a
half-word slip from a dead lane from noise.
"""

import sys
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "riscv"))

from amaranth import (Cat, ClockDomain, ClockSignal, Const, Elaboratable,
                      Instance, Module, Mux, ResetSignal, Signal)
from amaranth.lib.memory import Memory

from luna.gateware.interface.psram import (HyperRAMDQSInterface, HyperRAMPHY,
                                           HyperRAMInterface)

from bist import BISTAddresses, BISTHarness


APPLET_ID = 0x48524331   # "HRC1"

REG_ID          = 1
REG_STATUS      = 2
REG_WRITE_CYCLES = 3
REG_READ_CYCLES = 4
REG_CAPTURE_ADDR = 5
REG_CAPTURE_DATA = 6
REG_ERRORS      = 7
REG_WORDS       = 8
REG_DIE         = 9
REG_CLOCK       = 10   # sync in kHz, as built -- the host checks its own idea
REG_CONFIG      = 11   # dqs flag, bytes/word, burst length
REG_BAD_INDEX   = 12
REG_BAD_GOT     = 13
REG_BAD_WANT    = 14
REG_CONTROL     = 15
REG_READCLKSEL  = 16
REG_ACTUAL      = 17
REG_GOLDEN      = 18

# Words per transaction. Set by tCSM (4 us, CR1[1:0] = 01b), not by preference:
# CS# may not stay low longer than that or distributed refresh is starved. 128
# `sync` cycles is 2.13 us at 60 MHz and less at every higher clock, leaving room
# for the command and latency phases inside the same 4 us.
BURST_WORDS = 128

# Device word address bits. 64 Mbit is 8 MiB is 2**22 sixteen-bit words -- the
# bits-versus-bytes step that produced three wrong explanations once already.
# The counter is exactly this wide so it wraps inside the array: above 8 MiB the
# part returns the dead-bus pattern `0x8484`, which would be scored as millions
# of errors and read as a failing clock.
ADDRESS_BITS = 22

# Words of the first read pass kept for inspection. A summary count says a read
# was wrong; these say *how* -- shifted, stuck, or noise.
CAPTURE_DEPTH = 64

# tCSHI, CS# high between transactions, from the datasheet. The controller's
# RECOVERY state is `# TODO: implement recovery` and falls through to IDLE, so
# this gap exists only because the caller makes it.
T_CSHI_NS = 10.0

# The DTR conversion is retriggered by the wrap of a free-running counter. 2**19
# cycles is milliseconds -- far longer than the block's 8-cycle conversion and far
# shorter than the die's thermal time constant, so the reading is neither stale
# nor restarted mid-conversion. The figure `fabric_gateware.py` uses.
DTR_PERIOD_BITS = 19
DIE_PRESENT = 1 << 8

# ECP5 EHXPLLL limits, from the datasheet.
VCO_MIN_MHZ, VCO_MAX_MHZ, MAX_DIV = 400.0, 800.0, 128


def solve_pll(sync_mhz, input_mhz=60.0, fast_ratio=None, tolerance=0.001):
    """Dividers giving `sync_mhz`, and `fast_ratio * sync_mhz` when asked.

    Feedback is taken from CLKOP, so CLKFB_DIV counts *output* periods:
    VCO = input * CLKFB_DIV * CLKOP_DIV / CLKI_DIV, and therefore
    sync = input * CLKFB_DIV / CLKI_DIV independently of CLKOP_DIV. Treating
    CLKFB_DIV as a plain VCO multiplier is the mistake `variable_clock.py`
    records as having produced clocks at twice the requested rate.

    **`usb` is deliberately not solved for.** `VariableClockDomainGenerator`
    constrains it to exactly 60 MHz because the ULPI PHY does not enumerate
    otherwise, which leaves only 60, 100 and 120 MHz reachable below 130. This
    design has no ULPI -- Apollo reaches it over JTAG through the SAMD11, and
    `JTAGRegisterInterface` runs in `sync` plus a local JTCK domain -- so the
    constraint does not apply and the ladder gets 61 rungs instead of 3.

    Returns (vco, clki_div, clkfb_div, clkop_div, clkos2_div) or None.
    """
    for clki_div in range(1, 8):
        for clkfb_div in range(1, 81):
            if abs(input_mhz * clkfb_div / clki_div - sync_mhz) > tolerance:
                continue
            for clkop_div in range(1, MAX_DIV + 1):
                vco = sync_mhz * clkop_div
                if not VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ:
                    continue
                # `fast` divides the same VCO, so it only exists where CLKOP_DIV
                # is a multiple of the ratio. Rejected here rather than checked
                # afterwards: a silently-wrong `fast` corrupts data instead of
                # failing to build.
                if fast_ratio is not None and clkop_div % fast_ratio:
                    continue
                clkos2 = None if fast_ratio is None else clkop_div // fast_ratio
                return (vco, clki_div, clkfb_div, clkop_div, clkos2)
    return None


def reachable(low, high, fast_ratio=None, input_mhz=60.0):
    """Every `sync` this PLL can produce in [low, high], ascending."""
    out = []
    for clki in range(1, 8):
        for clkfb in range(1, 81):
            sync = input_mhz * clkfb / clki
            if low - 1e-9 <= sync <= high + 1e-9 and solve_pll(
                    sync, input_mhz, fast_ratio):
                out.append(round(sync, 6))
    return sorted(set(out))


class HyperRAMClocks(Elaboratable):
    """`sync`, and `fast` at twice it when the DQS PHY needs it. No `usb`."""

    def __init__(self, *, sync_mhz, with_fast):
        self.sync_mhz = sync_mhz
        self.with_fast = with_fast
        solved = solve_pll(sync_mhz, fast_ratio=2 if with_fast else None)
        if solved is None:
            raise ValueError(
                f"no PLL configuration gives sync {sync_mhz:g} MHz"
                + (" with fast at twice it" if with_fast else "")
                + f"; VCO must land in {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz")
        (self.vco_mhz, self.clki_div, self.clkfb_div,
         self.clkop_div, self.clkos2_div) = solved
        self.fast_mhz = None if not with_fast else self.vco_mhz / self.clkos2_div

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        if self.with_fast:
            m.domains.fast = ClockDomain()

        clk_sync, clk_fast, locked = Signal(), Signal(), Signal()

        m.submodules.pll = Instance(
            "EHXPLLL",
            i_CLKI=platform.request(platform.default_clk).i,
            i_CLKFB=clk_sync,
            i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_STDBY=0, i_PLLWAKESYNC=0, i_RST=0, i_ENCLKOP=0,
            o_CLKOP=clk_sync, o_CLKOS2=clk_fast, o_LOCK=locked,
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            p_STDBY_ENABLE="DISABLED", p_DPHASE_SOURCE="DISABLED",
            p_OUTDIVIDER_MUXA="DIVA", p_OUTDIVIDER_MUXB="DIVB",
            p_OUTDIVIDER_MUXC="DIVC", p_OUTDIVIDER_MUXD="DIVD",
            p_CLKI_DIV=self.clki_div, p_CLKFB_DIV=self.clkfb_div,
            p_FEEDBK_PATH="CLKOP",
            p_CLKOP_ENABLE="ENABLED", p_CLKOP_DIV=self.clkop_div,
            p_CLKOP_CPHASE=self.clkop_div - 1, p_CLKOP_FPHASE=0,
            p_CLKOS2_ENABLE="ENABLED" if self.with_fast else "DISABLED",
            p_CLKOS2_DIV=self.clkos2_div or 1,
            p_CLKOS2_CPHASE=(self.clkos2_div or 1) - 1, p_CLKOS2_FPHASE=0,
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8",
            a_MFG_ENABLE_FILTEROPAMP="1", a_MFG_GMCREF_SEL="2",
        )

        # Both domains held in reset until lock: a domain clocked by an unlocked
        # PLL sees a frequency that drifts while it settles.
        m.d.comb += [ClockSignal("sync").eq(clk_sync),
                     ResetSignal("sync").eq(~locked)]
        if self.with_fast:
            m.d.comb += [ClockSignal("fast").eq(clk_fast),
                         ResetSignal("fast").eq(~locked)]
        return m


class HyperRAMCeiling(Elaboratable):
    """Write a burst, read it back, verify, repeat -- and count what disagrees."""

    def __init__(self, *, sync_mhz=100.0, dqs=True, burst_words=BURST_WORDS,
                 negative_control=False):
        self.sync_mhz = sync_mhz
        self.dqs = dqs
        self.burst_words = burst_words

        # The negative control. Reads are checked against the COMPLEMENT of what
        # was written, which the part cannot return, so a working detector must
        # report every word wrong. Without it, "zero errors at every rung" is
        # equally consistent with a comparator that never fires -- and this
        # sweep found no failure to demonstrate the detector on.
        self.negative_control = negative_control

        # 32 bits per `sync` cycle over DQS, 16 without. A test written to the
        # wrong width reads back looking bit-shifted, which is a convincing
        # impersonation of a timing fault.
        self.word_bits = 32 if dqs else 16
        self.bytes_per_word = self.word_bits // 8

        # Device CK. The rate the *part* is being asked to run at, which is what
        # a ceiling is a ceiling of.
        self.ck_mhz = sync_mhz * 2 if dqs else sync_mhz

    def pattern(self, word_addr):
        """The word due at a device address. Address-derived: a stuck address shows."""
        half = self.word_bits // 2
        return Cat(word_addr[:half], ~word_addr[:half])

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = HyperRAMClocks(sync_mhz=self.sync_mhz,
                                          with_fast=self.dqs)

        harness = BISTHarness(
            applet_id=APPLET_ID,
            addresses=BISTAddresses(
                ident=REG_ID, control=REG_CONTROL, status=REG_STATUS,
                checks=REG_WORDS, errors=REG_ERRORS,
                actual=REG_ACTUAL, golden=REG_GOLDEN),
            width=self.word_bits, negative_control=self.negative_control)
        m.submodules.harness = harness

        # DQSBUFM has eight phase selections. Keeping this in a JTAG parameter
        # is what lets one configured design ask whether BURSTDET identifies a
        # useful phase rather than rebuilding the same experiment eight times.
        readclksel = Signal(3, init=0b010)
        harness.add_register(REG_READCLKSEL, value_signal=readclksel)

        dll_locked = Signal(reset=1)
        dll_ready = Signal(reset=1)
        burstdet = Signal()

        if self.dqs:
            from hyperram_dqs_phy import HyperRAMDQSPHY
            # `dir="-"`: this PHY drives raw pads. The pin map is the platform's.
            bus = platform.request("ram", 0, dir="-")
            m.submodules.phy = phy = HyperRAMDQSPHY(
                bus=bus, readclksel=readclksel)
            m.submodules.psram = psram = HyperRAMDQSInterface(phy=phy.phy)
            m.d.comb += [dll_locked.eq(phy.dll_locked),
                         dll_ready.eq(phy.dll_ready)]
            reset_assert = phy.phy.reset
            m.d.comb += burstdet.eq(phy.phy.burstdet)
        else:
            bus = platform.request("ram")
            m.submodules.phy = phy = HyperRAMPHY(bus=bus)
            m.submodules.psram = psram = HyperRAMInterface(phy=phy.phy)
            # The non-DQS PHY never drives RESET#; the platform's buffer holds it
            # released, which is the behaviour this path has always had.
            reset_assert = Signal()

        # Device word address. The controller advances internally within a burst,
        # so only the start address is issued; it steps by `burst_words` scaled to
        # the interface width, because one 32-bit DQS word is two device words.
        base = Signal(ADDRESS_BITS)
        index = Signal(range(self.burst_words + 1))
        write_cycles = Signal(32)
        read_cycles = Signal(32)
        passes = Signal(16)
        burstdet_seen = Signal()
        heartbeat = Signal(24)

        # All three are combinational and driven from the FSM state, so
        # `perform_write` is already high on the cycle `start_transfer` fires.
        #
        # It has to be. The controller latches `is_read <= ~perform_write` in the
        # same cycle it sees `start_transfer`, so a `perform_write` raised in
        # `m.d.sync` on entry to WRITE_START arrives one cycle late: the
        # controller latches a READ, runs a read transaction, and never asserts
        # `write_ready`. Measured, not reasoned about -- that version reported
        # 398 M write cycles and zero words moved. `hyperram_dqs_top.py` still
        # has this shape and has never been on hardware.
        writing = Signal()
        last = Signal()
        start = Signal()

        device_words = self.burst_words * (2 if self.dqs else 1)
        word_addr = Signal(ADDRESS_BITS)
        m.d.comb += word_addr.eq(base + (index * (2 if self.dqs else 1)))
        expected = Signal(self.word_bits)
        m.d.comb += expected.eq(self.pattern(word_addr))

        # What reads are judged against. The same value that was written, unless
        # this is the negative control, in which case it is a value the part
        # cannot produce.
        checked_against = Signal(self.word_bits)
        m.d.comb += checked_against.eq(
            Mux(harness.negative, ~expected, expected))

        m.d.sync += heartbeat.eq(heartbeat + 1)
        with m.If(burstdet):
            m.d.sync += burstdet_seen.eq(1)

        # tCSHI in whole `sync` cycles, rounded up.
        recovery_cycles = max(1, ceil(T_CSHI_NS * self.sync_mhz / 1000.0))
        recovery = Signal(range(recovery_cycles + 1))

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.address.eq(base),
            # Held for the whole transfer, never pulsed -- both of these are traps
            # already paid for on this interface.
            psram.perform_write.eq(writing),
            psram.write_data.eq(expected),
            psram.final_word.eq(last),
            psram.start_transfer.eq(start),
        ]

        # First-pass capture, and the first mismatch wherever it happens.
        memory = Memory(shape=self.word_bits, depth=CAPTURE_DEPTH, init=[])
        m.submodules.memory = memory
        write_port = memory.write_port()
        read_port = memory.read_port(domain="sync")
        capture_addr = Signal(range(CAPTURE_DEPTH))
        harness.add_register(REG_CAPTURE_ADDR, value_signal=capture_addr)
        m.d.comb += read_port.addr.eq(capture_addr)

        bad_index = Signal(32)
        bad_got = Signal(32)
        bad_want = Signal(32)
        bad_seen = Signal()

        with m.If(harness.go):
            m.d.sync += [bad_index.eq(0), bad_got.eq(0), bad_want.eq(0),
                         bad_seen.eq(0)]

        with m.FSM():
            with m.State("RESET"):
                # RESET# low for the first half of this state, released for the
                # second. tRP is 200 ns and tRPH 400 ns; halves of 2**16 `sync`
                # cycles are hundreds of microseconds, three orders past both.
                # It happens once per configuration and nothing is timed across
                # it, so being generous costs nothing.
                m.d.comb += reset_assert.eq(~heartbeat[15])
                with m.If(heartbeat[16] & dll_ready):
                    m.next = "WRITE_START"

            with m.State("WRITE_START"):
                # `writing` high in the SAME cycle as `start`, not one after it.
                m.d.comb += [writing.eq(1), start.eq(1)]
                m.d.sync += [index.eq(0),
                             write_cycles.eq(write_cycles + 1)]
                m.next = "WRITE"

            with m.State("WRITE"):
                m.d.comb += writing.eq(1)
                m.d.sync += write_cycles.eq(write_cycles + 1)
                m.d.comb += last.eq(index == self.burst_words - 1)
                with m.If(psram.write_ready):
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == self.burst_words - 1):
                        m.d.sync += recovery.eq(0)
                        m.next = "WRITE_RECOVER"

            with m.State("WRITE_RECOVER"):
                # Still a write as far as the controller is concerned: it is
                # draining the transaction this state is waiting on.
                m.d.comb += writing.eq(1)
                m.d.sync += [write_cycles.eq(write_cycles + 1),
                             recovery.eq(recovery + 1)]
                with m.If(psram.idle & (recovery >= recovery_cycles)):
                    m.d.sync += [index.eq(0), recovery.eq(0)]
                    m.next = "READ_START"

            with m.State("READ_START"):
                # `writing` left at its default 0, so the controller latches a
                # read on this very cycle.
                m.d.comb += start.eq(1)
                m.d.sync += [index.eq(0),
                             read_cycles.eq(read_cycles + 1)]
                m.next = "READ"

            with m.State("READ"):
                m.d.sync += read_cycles.eq(read_cycles + 1)
                m.d.comb += [
                    last.eq(index == self.burst_words - 1),
                    write_port.addr.eq(index),
                    write_port.data.eq(psram.read_data),
                    write_port.en.eq(psram.read_ready & (passes == 0)
                                     & (index < CAPTURE_DEPTH)),
                ]
                with m.If(psram.read_ready):
                    m.d.sync += index.eq(index + 1)
                    with m.If(psram.read_data != checked_against):
                        with m.If(~bad_seen):
                            m.d.sync += [
                                bad_seen.eq(1),
                                bad_index.eq(Cat(index, passes)),
                                bad_got.eq(psram.read_data),
                                bad_want.eq(checked_against),
                            ]
                    with m.If(index == self.burst_words - 1):
                        m.d.sync += recovery.eq(0)
                        m.next = "READ_RECOVER"

            with m.State("READ_RECOVER"):
                m.d.sync += [read_cycles.eq(read_cycles + 1),
                             recovery.eq(recovery + 1)]
                with m.If(psram.idle & (recovery >= recovery_cycles)):
                    m.d.sync += [recovery.eq(0), passes.eq(passes + 1),
                                 base.eq(base + device_words)]
                    m.next = "WRITE_START"

        #
        # Die temperature. DTROUT[7] is the valid flag; sampling without it
        # latches a mid-conversion value that looks like a temperature.
        #
        dtr_counter = Signal(DTR_PERIOD_BITS)
        m.d.sync += dtr_counter.eq(dtr_counter + 1)
        dtr_bits = [Signal(name=f"dtr{i}") for i in range(8)]
        m.submodules.dtr = Instance(
            "DTR", i_STARTPULSE=(dtr_counter == 0),
            **{f"o_DTROUT{i}": bit for i, bit in enumerate(dtr_bits)})
        die = Signal(8)
        with m.If(dtr_bits[7]):
            m.d.sync += die.eq(Cat(*dtr_bits))

        m.d.comb += [
            harness.busy.eq(1),
            harness.done.eq(psram.idle & (recovery >= recovery_cycles)
                            & (index == self.burst_words - 1)),
            harness.check.eq(psram.read_ready),
            harness.actual.eq(psram.read_data),
            harness.golden.eq(expected),
            harness.status_extra.eq(
                Cat(dll_locked, dll_ready, burstdet_seen, bad_seen,
                    Const(int(round(self.ck_mhz)) & 0xFF, 8), passes)),
        ]

        harness.add_read_only_register(REG_WRITE_CYCLES, read=write_cycles)
        harness.add_read_only_register(REG_READ_CYCLES, read=read_cycles)
        harness.add_read_only_register(REG_CAPTURE_DATA, read=read_port.data)
        harness.add_read_only_register(REG_BAD_INDEX, read=bad_index)
        harness.add_read_only_register(REG_BAD_GOT, read=bad_got)
        harness.add_read_only_register(REG_BAD_WANT, read=bad_want)
        harness.add_read_only_register(
            REG_DIE, read=Cat(die, Const(1, 1), Const(0, 23)))
        harness.add_read_only_register(
            REG_CLOCK, read=Const(int(round(self.sync_mhz * 1000)), 32))
        harness.add_read_only_register(
            REG_CONFIG, read=Cat(Const(1 if self.dqs else 0, 1),
                                 Const(0, 7),
                                 Const(self.bytes_per_word, 8),
                                 Const(self.burst_words, 16)))
        #
        # LEDs. Not the evidence -- the registers are -- but a board that shows
        # nothing is indistinguishable from a board that is not configured.
        #
        if platform is not None:
            leds = [platform.request("led", n, dir="o") for n in range(6)]
            m.d.comb += [
                leds[0].o.eq(~dll_locked),                    # red:    no DLL
                leds[1].o.eq(~burstdet_seen if self.dqs else 0),  # orange: no strobe
                leds[2].o.eq(passes == 0),                    # yellow: first pass
                leds[3].o.eq((passes != 0) & ~harness.error), # green:  clean
                leds[4].o.eq(harness.error),                  # blue:   mismatches
                leds[5].o.eq(heartbeat[23]),                  # violet: alive
            ]

        return m


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        description="HyperRAM clock ceiling bitstream. Never programs the board.")
    parser.add_argument("--build", action="store_true",
                        help="run yosys, nextpnr and ecppack; do not program")
    parser.add_argument("--sync-mhz", type=float, default=100.0)
    parser.add_argument("--dqs", action="store_true",
                        help="DQS PHY; device CK is twice --sync-mhz")
    parser.add_argument("--build-dir", default=None)
    parser.add_argument("--negative-control", action="store_true",
                        help="check reads against the complement of what was "
                             "written; every word must then be reported wrong")
    parser.add_argument("--list", action="store_true",
                        help="print reachable sync frequencies and exit")
    args = parser.parse_args(argv)

    if args.list:
        for tag, ratio in (("non-DQS (CK = sync)", None), ("DQS (CK = 2x sync)", 2)):
            freqs = reachable(55, 260, ratio)
            print(f"\n{tag}: {len(freqs)} reachable")
            print("  " + "  ".join(f"{f:g}" for f in freqs))
        return 0

    if not args.build:
        print(__doc__)
        return 0

    from cynthion_platform import CynthionPlatformRev1D4

    root = Path(__file__).resolve().parent.parent.parent
    tag = "dqs" if args.dqs else "sdr"
    out = args.build_dir or str(
        root / "tmp" / "hyperram-ceiling" / f"manual-{tag}-{args.sync_mhz:g}")
    design = HyperRAMCeiling(sync_mhz=args.sync_mhz, dqs=args.dqs,
                             negative_control=args.negative_control)
    print(f"sync {args.sync_mhz:g} MHz -> device CK {design.ck_mhz:g} MHz "
          f"({'DQS' if args.dqs else 'non-DQS'})")
    CynthionPlatformRev1D4().build(design, build_dir=out, do_program=False)
    print(f"built into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
