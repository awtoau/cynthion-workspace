#!/usr/bin/env python3
#
# Where the HyperRAM stops working, measured against the clock the part sees.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sustained write/read/verify against HyperRAM, at an arbitrary clock, either PHY.

Driven by `scripts/hyperram_ceiling.py`. Building this file directly is for
checking that a frequency places at all:

    python3 gateware/probes/hyperram/hyperram_ceiling_top.py --build --sync-mhz 100 --dqs

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

`hyperram_speed.py` (retired) moved 2048 words in one transaction: 17 us at 120 MHz, over
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
# `gateware/soc`, for the two vendored controllers and `bootram`'s constants.
# This line used to name `gateware/probes/riscv`, which does not exist -- so the
# DQS branch below could only ever import when the caller had already put the SoC
# on the path, and the non-DQS branch now needs it too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "soc"))

from amaranth import (Cat, ClockDomain, ClockSignal, Const, Elaboratable,
                      Instance, Module, Mux, ResetSignal, Signal)
from amaranth.lib.memory import Memory

from luna.gateware.interface.psram import HyperRAMPHY

from peripherals.hyperram_controller import HyperRAMController

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
# The PART's own tuning, writable at runtime. Bit 16 is "apply this"; bits 15:0
# are the value. Left clear the part keeps its power-on configuration, which
# matters because a CR1 write changes how it clocks EVERYTHING.
REG_DEVICE_CR0  = 19
REG_DEVICE_CR1  = 20
# Stop after this many PASSES, zero meaning free-run. One pass is `burst_words`
# device words, so a limit of 1 is a 128-word / 256-byte run -- the cheap screen.
REG_PASS_LIMIT  = 21

# The gateware-driven sweep. Writing 1 to REG_SWEEP_GO starts it; REG_SWEEP_DONE
# reads 1 when the table is complete; REG_SWEEP_ADDR selects a row and
# REG_SWEEP_DATA reads it back. The whole matrix is then ONE JTAG session
# instead of one configure and one round trip per combination -- which was the
# entire cost, measured at 72 seconds for 64 cells with none finished.
#
# It also cuts the exposure to the readback slipping. 320 round trips is 320
# chances to slip a bit; one table read is one, and the applet id either side
# validates it.
REG_SWEEP_GO    = 22
REG_SWEEP_DONE  = 23
REG_SWEEP_ADDR  = 24
REG_SWEEP_DATA  = 25
# The engine FSM's current state, so a stall says WHERE. Without it a hung sweep
# is a silent poll loop, which cost fourteen minutes before the poll was taught
# to report the cell index -- and the cell index still does not say which state.
REG_FSM_STATE   = 28
REG_CTRL_STATE  = 29   # the HyperBus controller's own FSM state
# CR0 AS THE PART REPORTS IT, not as the host asked for it. The one
# register that can say whether a configuration write landed.
REG_DEVICE_READBACK = 30

# Combinations the sweep walks: capture phase 0-7 against CR0 drive code 0-7.
SWEEP_PHASES = 8
SWEEP_DRIVES = 8
SWEEP_CELLS = SWEEP_PHASES * SWEEP_DRIVES

# Passes per cell, and which cells to run, both set over JTAG.
#
# THIS IS WHAT MAKES IT REUSABLE. A screen is one pass across every cell -- 256
# bytes each, seconds for the matrix. A SOAK is the same gateware with the mask
# narrowed to the settings worth trusting and the pass count raised: same code
# path, same comparator, same negative control, just pointed at fewer cells for
# longer. Without that split, a soak means a different harness and a different
# set of bugs.
REG_SWEEP_PASSES = 26
REG_SWEEP_MASK   = 27   # phases in bits 7:0, drive codes in bits 15:8

# One row: errors in the low 24 bits, then the flags. Errors saturate rather
# than wrap -- a wrapped count that lands near zero reads as a pass.
ROW_ERROR_BITS = 24
ROW_BURSTDET = 1 << 24
ROW_STALLED = 1 << 25
ROW_SKIPPED = 1 << 26   # masked out, so its zero error count means nothing
ROW_CONTROL_OK = 1 << 27  # the negative pass mismatched everywhere, as it must

# Each cell runs TWICE: the measurement, then one pass with the comparator
# pointed at a value the part cannot return. Without that second pass a zero
# error count says only that nothing was reported, which is exactly what made
# this harness's entire retired ladder worthless. It costs one burst per cell.

# Where those registers live in the part's register space.
CR0_ADDRESS = 0x0800
CR1_ADDRESS = 0x0801

# CR0 as the part powers up. The sweep replaces only the drive-strength field.
CR0_POWER_ON = 0x8F2F
APPLY_BIT = 1 << 16
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

# tCSHI, CS# high between transactions, from the datasheet.
#
# BOTH controllers now hold this themselves -- upstream's `RECOVERY` was
# `# TODO: implement recovery` and fell through to IDLE, which is why this
# applet counted the gap itself. The counter below is kept: it is the only place
# a violation would show up as a wrong measurement rather than as data
# corruption, and a harness that measures a ceiling should not also be the thing
# that assumes the controller is right.
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
    # CLKI_DIV stops at 6, not 7. The ECP5's phase detector has a 10 MHz
    # minimum (FPGA-DS-02012 Table 3.23), and 60/7 = 8.57 MHz is under it --
    # a configuration that builds and whose jitter the datasheet does not
    # guarantee. `gateware/soc/clocks.py` carries the same floor as
    # `PFD_MIN_MHZ`, where it removed 55 of 68 candidate frequencies.
    for clki_div in range(1, 7):
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
                 negative_control=False, transport=None, own_clocks=True,
                 own_leds=True, own_dtr=True):
        """The four `own_*`/`transport` arguments exist so this can be EMBEDDED.

        Defaults reproduce the standalone JTAG applet exactly. Passing a
        transport puts the same register window on another bus -- the CPU's CSR
        bus, via `BistCsrTransport` -- without the engine knowing, since it only
        ever calls `add_register` / `add_read_only_register` on the harness.
        The `own_*` flags say the enclosing design already has that resource.

        None of them changes what is measured. That is the point: the
        comparator, the negative control and the sweep FSM are the same logic
        either way, so a number from the embedded rig and a number from the
        applet are comparable.
        """
        self.sync_mhz = sync_mhz
        self.dqs = dqs
        self.burst_words = burst_words
        self._transport = transport
        # `own_clocks=False` says the caller has already made the domain, which
        # is what an SoC that pins its own `sync` does.
        self._own_clocks = own_clocks
        # A top-level applet owns the board; an embedded engine owns nothing it
        # was not handed. `platform.request` is not idempotent, so an embedded
        # copy asking for `led` fights the SoC's own GPIO for it and the build
        # dies on "Resource led#0 has already been requested" -- a failure that
        # names the resource and not the reason.
        self._own_leds = own_leds
        # The ECP5 has exactly ONE DTR, and two instantiations do not fail at
        # elaboration: they fail in the placer as "no BELs remaining to
        # implement cell type 'DTR'", which names neither design that wanted it.
        # Die temperature is a property of the chip, not of the engine, so an
        # embedded copy reads it from `gateware_id` and REG_DIE reads zero here.
        self._own_dtr = own_dtr

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
        """The word due at a device address, using EVERY address bit.

        This used to be `Cat(addr[:half], ~addr[:half])` -- the low 16 bits of
        the address and their complement. The part is 4 Mi words, so that pattern
        REPEATED 64 TIMES ACROSS IT: an addressing fault anywhere in bits 16..21
        wrote and read the same value and was scored correct. The harness could
        not see aliasing over the whole upper part, which is the one question
        `docs/chips/hyperram/w956a8.md` raises about the undocumented upper 4 MiB.

        At 32 bits the low half carries `addr[0:16]` and the high half carries
        `addr[16:32]` complemented, so the value is INVERTIBLE: given a word that
        came back wrong, the address it actually belongs to can be decoded rather
        than guessed. That is what makes a displacement readable instead of
        inferred -- the same property the 0-255 ramp has, and the reason it found
        in one run what a self-comparing check had hidden all day.

        At 16 bits an address of 22 bits cannot be encoded, so the high bits are
        folded in by XOR. Aliasing then changes the value and is DETECTED, though
        the source address is no longer uniquely recoverable.
        """
        half = self.word_bits // 2
        if half >= 16:
            return Cat(word_addr[:half], ~word_addr[half:2 * half])
        folded = word_addr[:half] ^ word_addr[half:2 * half] ^ word_addr[2 * half:]
        return Cat(folded, ~folded)

    def elaborate(self, platform):
        m = Module()

        if self._own_clocks:
            m.submodules.car = HyperRAMClocks(sync_mhz=self.sync_mhz,
                                              with_fast=self.dqs)

        harness = BISTHarness(
            applet_id=APPLET_ID,
            addresses=BISTAddresses(
                ident=REG_ID, control=REG_CONTROL, status=REG_STATUS,
                checks=REG_WORDS, errors=REG_ERRORS,
                actual=REG_ACTUAL, golden=REG_GOLDEN),
            width=self.word_bits, negative_control=self.negative_control,
            transport=self._transport)
        m.submodules.harness = harness

        # DQSBUFM has eight phase selections. Keeping this in a JTAG parameter
        # is what lets one configured design ask whether BURSTDET identifies a
        # useful phase rather than rebuilding the same experiment eight times.
        readclksel = Signal(3, init=0b010)
        harness.add_register(REG_READCLKSEL, value_signal=readclksel)

        # --- the gateware-driven sweep --------------------------------------
        #
        # The host used to drive every combination: configure, write registers,
        # run, read back. That is one FPGA configuration per cell, and it was the
        # whole cost. Here the applet walks the combinations itself and records a
        # row each, so the host writes one bit and reads one table.
        sweep_passes = Signal(32, init=1)
        sweep_mask = Signal(32, init=0xFFFF)
        harness.add_register(REG_SWEEP_PASSES, value_signal=sweep_passes)
        harness.add_register(REG_SWEEP_MASK, value_signal=sweep_mask)

        sweep_go = Signal(32)
        sweep_done = Signal()
        sweep_addr = Signal(32)
        sweep_cell = Signal(range(SWEEP_CELLS + 1))
        sweeping = Signal()

        # EDGE, not level. `sweep_done` stays set after a sweep, and the start
        # condition tested `~sweep_done`, so a second GO never started anything:
        # the host wrote GO, read DONE still set from last time, and pulled back
        # the PREVIOUS table. A soak silently returned its own screen.
        # Which half of the cell is running: the measurement, or the negative
        # control that must fail.
        sweep_control_pass = Signal()
        sweep_control_ok = Signal()

        # The MEASUREMENT's results, latched before the control pass runs and
        # overwrites the counters. Recording `harness.errors` in SWEEP_RECORD
        # would otherwise report the control's error count as the cell's.
        cell_errors = Signal(32)
        cell_burstdet = Signal()

        sweep_go_previous = Signal()
        sweep_start = Signal()
        m.d.sync += sweep_go_previous.eq(sweep_go[0])
        m.d.comb += sweep_start.eq(sweep_go[0] & ~sweep_go_previous)

        # LATCH THE REQUEST. `sweep_start` is a one-cycle pulse and the only
        # place that acts on it is READ_RECOVER, under a condition that is true
        # on a small fraction of cycles -- so the pulse was essentially always
        # thrown away and the sweep never began. The engine kept free-running,
        # which read as "stalled at cell 0" when it had never started cell 0.
        #
        # A level survives until it is consumed, which is what a request across
        # two clock-rate-mismatched things has to be.
        sweep_pending = Signal()
        with m.If(sweep_start):
            m.d.sync += sweep_pending.eq(1)

        harness.add_register(REG_SWEEP_GO, value_signal=sweep_go)
        harness.add_register(REG_SWEEP_ADDR, value_signal=sweep_addr)
        harness.add_read_only_register(
            REG_SWEEP_DONE, read=Cat(sweep_done, sweep_cell,
                                     Const(0, 32 - 1 - len(sweep_cell))))

        results_mem = Memory(shape=32, depth=SWEEP_CELLS, init=[])
        m.submodules.results_mem = results_mem
        results_write = results_mem.write_port()
        results_read = results_mem.read_port(domain="sync")
        m.d.comb += results_read.addr.eq(sweep_addr[:len(results_read.addr)])
        harness.add_read_only_register(REG_SWEEP_DATA, read=results_read.data)

        # The cell index decomposes into the two knobs. Phase is the fast axis so
        # a row of the printed map is one drive strength.
        sweep_phase = Signal(3)
        sweep_drive = Signal(3)
        m.d.comb += [sweep_phase.eq(sweep_cell[:3]),
                     sweep_drive.eq(sweep_cell[3:6])]

        # Is this cell in the mask? A masked-out cell is recorded as SKIPPED
        # rather than left blank, because a zero error count with no flag reads
        # exactly like a clean pass -- which is the mistake that has cost this
        # project the most today.
        # CR0 for this cell: the power-on value with the drive field replaced.
        sweep_cr0 = Signal(16)
        m.d.comb += sweep_cr0.eq(
            (CR0_POWER_ON & ~(0b111 << 12)) | (sweep_drive << 12))

        cell_selected = Signal()
        m.d.comb += cell_selected.eq(sweep_mask.bit_select(sweep_phase, 1)
                                     & sweep_mask[8:16].bit_select(sweep_drive, 1))

        # Until now this applet exposed exactly ONE knob -- the capture phase --
        # so drive strength and the device's own initial latency could not be
        # swept at all, and every ladder varied the clock against one arbitrary
        # set of device settings. `hyperram_regfuzz.py` proved the CR0 write path
        # works on this board; it was simply never wired here.
        device_cr0 = Signal(32)
        device_cr1 = Signal(32)
        harness.add_register(REG_DEVICE_CR0, value_signal=device_cr0)
        harness.add_register(REG_DEVICE_CR1, value_signal=device_cr1)

        # A BOUNDED RUN, which this applet could not do.
        #
        # The engine free-ran and the host could only poll a counter, so asking
        # for 128 words returned five million: by the time a poll came back the
        # engine had lapped it many times. Every "cheap screen" therefore cost a
        # full run, which is why a 64-cell sweep took as long as a ladder.
        #
        # Zero keeps the old behaviour exactly.
        pass_limit = Signal(32)
        harness.add_register(REG_PASS_LIMIT, value_signal=pass_limit)

        dll_locked = Signal(reset=1)
        dll_ready = Signal(reset=1)
        burstdet = Signal()

        if self.dqs:
            from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY
            from peripherals.hyperram_dqs_controller import HyperRAMDQSController
            from bootram import HYPERRAM_LATENCY_CLOCKS
            # `dir="-"`: this PHY drives raw pads. The pin map is the platform's.
            bus = platform.request("ram", 0, dir="-")
            m.submodules.phy = phy = HyperRAMDQSPHY(
                bus=bus, readclksel=Mux(sweeping, sweep_phase, readclksel))
            m.submodules.psram = psram = HyperRAMDQSController(
                phy=phy.phy, sync_mhz=self.sync_mhz,
                high_latency_clocks=HYPERRAM_LATENCY_CLOCKS)
            m.d.comb += [dll_locked.eq(phy.dll_locked),
                         dll_ready.eq(phy.dll_ready)]
            reset_assert = phy.phy.reset
            m.d.comb += burstdet.eq(phy.phy.burstdet)
        else:
            bus = platform.request("ram")
            m.submodules.phy = phy = HyperRAMPHY(bus=bus)
            m.submodules.psram = psram = HyperRAMController(
                phy=phy.phy, sync_mhz=self.sync_mhz)
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
        # 398 M write cycles and zero words moved. `hyperram_dqs_top.py` had the
        # same shape and was retired for it -- never run on hardware, and it
        # would have hung if it had been.
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
        # The negative control compares against the pattern of an address the
        # PART DOES NOT HAVE, rather than against the complement.
        #
        # `~expected` is not a safe "impossible" value. Now that `pattern` uses
        # every address bit it is INVERTIBLE, which means every 32-bit value is
        # the pattern of exactly one address -- including the complement, which
        # is `pattern(~word_addr)`. So a read landing on the wrong address could
        # legitimately match it, and the control could not tell a broken
        # comparator from a displaced read. Measured: 1,191 words of 3,566,592
        # "matched" their own complement, which is 1 in 3,000 and far too
        # systematic to be chance.
        #
        # The part is 8 MiB, so 2**22 sixteen-bit words: address bit 22 is the
        # first one it cannot have. `pattern(word_addr | 1 << 22)` is therefore a
        # value no legitimate read can return, whatever address it lands on.
        unreachable = Signal(32)
        m.d.comb += unreachable.eq(word_addr | (1 << 22))

        checked_against = Signal(self.word_bits)
        m.d.comb += checked_against.eq(
            Mux(harness.negative | sweep_control_pass,
                self.pattern(unreachable), expected))

        m.d.sync += heartbeat.eq(heartbeat + 1)
        with m.If(burstdet):
            m.d.sync += burstdet_seen.eq(1)

        # tCSHI in whole `sync` cycles, rounded up.
        recovery_cycles = max(1, ceil(T_CSHI_NS * self.sync_mhz / 1000.0))
        recovery = Signal(range(recovery_cycles + 1))

        # How long a *_RECOVER state may wait for `psram.idle` before giving up.
        #
        # **Waits for**: the controller to finish draining a burst and return to
        # IDLE.
        #
        # **Expected duration**: `recovery_cycles` for tCSHI (1 at every rung
        # this rig builds, since tCSHI is 10 ns and `hr` is 50-100 MHz) plus the
        # controller's own RECOVERY and its FSM transitions back to IDLE -- not
        # enumerated here, so budget ~16 cycles.
        #
        # **Multiplier**: 16x that. Larger than this project's 1.25x rule
        # because the controller's internal path is not enumerated, and a bound
        # that false-trips would be reported as a device fault -- the one error
        # this rig must not make. 256 cycles is 5.1 us at `hr` 50 MHz, which is
        # still instant next to the alternative.
        #
        # **On expiry**: `stalled` latches, the FSM returns to RESET, and the CPU
        # reads the bit. Before this the engine simply spun -- observed parked in
        # READ_RECOVER with `read_cycles` past 693,000,000 and no way back except
        # reconfiguring the FPGA, which is ~130 s per attempt. A rig that can
        # wedge with no reset path costs more than the measurement it takes.
        stall_limit = 16 * (recovery_cycles + 16)
        stall = Signal(range(stall_limit + 1))
        # Sticky, and NOT cleared by `go`: a run that had to be rescued from a
        # stall is not a clean run, and the next reader must be able to see that
        # even if the pass after it succeeds.
        stalled = Signal()

        # What the PART says CR0 is, read back after configuring it.
        device_readback = Signal(32)


        # DID THE RUN FINISH. `harness.done` used to be an expression over
        # leftover signal values --
        #
        #     psram.idle & (recovery >= recovery_cycles)
        #                & (index == burst_words - 1)
        #
        # -- and the transition into HALTED clears `recovery` to 0, so a run that
        # COMPLETED could never assert it. Read off the board as `idle=1
        # recovered=0` with the engine sitting in HALTED and the CPU polling for
        # a `done` that was unreachable by construction (#226).
        #
        # A state means "I finished", so say that, and set it where the FSM
        # decides it has finished rather than inferring it afterwards.
        finished = Signal()

        # A register-space write, when the configuration phase is running. It
        # steals the controller's inputs for one short transaction and leaves
        # every other part of the datapath alone.
        configuring = Signal()
        config_address = Signal(32)
        config_data = Signal(32)

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(configuring),
            psram.address.eq(Mux(configuring, config_address, base)),
            # Held for the whole transfer, never pulsed -- both of these are traps
            # already paid for on this interface.
            psram.perform_write.eq(writing),
            psram.write_data.eq(Mux(configuring, config_data, expected)),
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

        with m.FSM() as engine:
            with m.State("RESET"):
                # RESET# low for the first half of this state, released for the
                # second. tRP is 200 ns and tRPH 400 ns; halves of 2**16 `sync`
                # cycles are hundreds of microseconds, three orders past both.
                # It happens once per configuration and nothing is timed across
                # it, so being generous costs nothing.
                m.d.comb += reset_assert.eq(~heartbeat[15])
                with m.If(heartbeat[16] & dll_ready):
                    # Configure the PART before measuring it, if the host asked.
                    # Skipped entirely when neither apply bit is set, so the
                    # default path is byte-for-byte what it was.
                    with m.If(sweep_pending):
                        m.d.sync += [sweeping.eq(1), sweep_cell.eq(0),
                                     sweep_done.eq(0), passes.eq(0),
                                     sweep_pending.eq(0)]
                        m.next = "SWEEP_CELL"
                    with m.Elif(device_cr0[16] | device_cr1[16]):
                        m.next = "CONFIG_CR0"
                    with m.Else():
                        m.next = "WRITE_START"

            # CONFIG_CR0 / CONFIG_CR1 -- one register-space write each.
            #
            # A register write needs no recovery: the controller returns straight
            # to IDLE from WRITE_DATA when `is_register` is latched, so waiting
            # on `psram.idle` is the whole handshake. `final_word` is held high
            # because one word IS the transaction.
            with m.State("CONFIG_CR0"):
                m.d.comb += [
                    configuring.eq(1),
                    config_address.eq(CR0_ADDRESS),
                    # While sweeping, the cell index chooses the drive code and
                    # everything else in CR0 keeps its power-on value.
                    config_data.eq(Mux(sweeping, sweep_cr0, device_cr0[:16])),
                    writing.eq(1),
                    last.eq(1),
                    # `sweeping` counts as "apply". This gated on the HOST's
                    # apply bit alone, which a gateware sweep never sets, so
                    # every cell fell through without writing CR0 and ran at the
                    # power-on drive strength. The eight drive rows were eight
                    # repeats of one configuration.
                    start.eq(psram.idle & (sweeping | device_cr0[16])),
                ]
                with m.If(~(sweeping | device_cr0[16])):
                    m.next = "CONFIG_CR1"
                with m.Elif(~psram.idle):
                    m.next = "CONFIG_CR0_WAIT"

            with m.State("CONFIG_CR0_WAIT"):
                # THE SAME VALUE as the state that started the write. The
                # controller latches `write_data` during WRITE_DATA, which
                # happens HERE -- so holding the host's register instead of the
                # sweep's wrote CR0 = 0x0000 during a sweep. CR0[15] is deep
                # power down, and 1 is normal operation, so that put the part to
                # sleep and the sweep hung waiting for a device that had stopped
                # answering.
                m.d.comb += [configuring.eq(1), writing.eq(1), last.eq(1),
                             config_address.eq(CR0_ADDRESS),
                             config_data.eq(Mux(sweeping, sweep_cr0,
                                                device_cr0[:16]))]
                with m.If(psram.idle):
                    m.next = "CONFIG_CR1"

            with m.State("CONFIG_CR1"):
                m.d.comb += [
                    configuring.eq(1),
                    config_address.eq(CR1_ADDRESS),
                    config_data.eq(device_cr1[:16]),
                    writing.eq(1),
                    last.eq(1),
                    start.eq(psram.idle & device_cr1[16]),
                ]
                with m.If(~device_cr1[16]):
                    m.next = "WRITE_START"
                with m.Elif(~psram.idle):
                    m.next = "CONFIG_CR1_WAIT"

            with m.State("CONFIG_CR1_WAIT"):
                m.d.comb += [configuring.eq(1), writing.eq(1), last.eq(1),
                             config_address.eq(CR1_ADDRESS),
                             config_data.eq(device_cr1[:16])]
                with m.If(psram.idle):
                    m.next = "CONFIG_VERIFY"

            # READ CR0 BACK. The engine only ever WROTE the part's registers --
            # every state that set `configuring` also set `writing` -- so a write
            # that never landed was indistinguishable from one that did.
            #
            # That is not hypothetical. A latency sweep passed on codes 2 and 6
            # only, where the datasheet says 0, 1, 2 and 15 are legal and 3..13
            # are RESERVED. 2 is the power-on default and 6 is the default with
            # one bit flipped, which is the signature of the write not landing at
            # all. CR1[6] selecting a differential clock and CR0[3] selecting
            # variable latency likewise changed nothing, and both must.
            #
            # A register READ is `configuring` WITHOUT `writing`. Its result goes
            # to REG_DEVICE_READBACK so the CPU can compare it against what it
            # asked for, which turns "did the configuration apply" from an
            # inference about behaviour into a measurement.
            with m.State("CONFIG_VERIFY"):
                # `start` IS THE TRANSACTION. Setting `configuring` alone only
                # selects register space -- it does not issue anything, so the
                # first version of this state read nothing and reported 0x0000,
                # which looked exactly like a broken register path on the part.
                # It was a missing strobe. Modelled on CONFIG_CR0 for that
                # reason: same handshake, `writing` left low to make it a READ.
                m.d.comb += [configuring.eq(1), last.eq(1),
                             config_address.eq(CR0_ADDRESS),
                             start.eq(psram.idle)]
                with m.If(~psram.idle):
                    m.next = "CONFIG_VERIFY_WAIT"

            with m.State("CONFIG_VERIFY_WAIT"):
                m.d.comb += [configuring.eq(1), last.eq(1),
                             config_address.eq(CR0_ADDRESS)]
                with m.If(psram.read_ready):
                    m.d.sync += device_readback.eq(psram.read_data)
                with m.If(psram.idle):
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
                             recovery.eq(recovery + 1),
                             stall.eq(stall + 1)]
                with m.If(psram.idle & (recovery >= recovery_cycles)):
                    m.d.sync += [index.eq(0), recovery.eq(0), stall.eq(0)]
                    m.next = "READ_START"
                with m.Elif(stall >= stall_limit):
                    m.d.sync += [stalled.eq(1), stall.eq(0), recovery.eq(0)]
                    m.next = "RESET"

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
                        m.d.sync += [recovery.eq(0), stall.eq(0)]
                        m.next = "READ_RECOVER"

            with m.State("READ_RECOVER"):
                m.d.sync += [read_cycles.eq(read_cycles + 1),
                             recovery.eq(recovery + 1),
                             stall.eq(stall + 1)]
                # The stall escape comes FIRST, so a controller that never
                # returns to IDLE cannot outlast it. Observed doing exactly that
                # on hardware: idle=0 for ever after a read burst, while the same
                # wait in WRITE_RECOVER passed. See #226.
                with m.If(stall >= stall_limit):
                    m.d.sync += [stalled.eq(1), stall.eq(0), recovery.eq(0)]
                    m.next = "RESET"
                with m.Elif(psram.idle & (recovery >= recovery_cycles)):
                    m.d.sync += [recovery.eq(0), passes.eq(passes + 1),
                                 base.eq(base + device_words)]
                    # Halt at the limit instead of looping. `passes` is the count
                    # BEFORE this increment, so `+ 1` is what it will become.
                    #
                    # While sweeping, the limit is the per-cell pass count and
                    # reaching it ends the CELL, not the run.
                    # START HERE TOO, not only out of RESET. `sweep_go` used to
                    # be sampled in RESET alone -- a state the FSM leaves once at
                    # power-up -- so a host writing GO afterwards was ignored and
                    # the sweep never began. This is the state the engine returns
                    # to every pass, so GO is seen within one burst of the write.
                    control_done = Signal()
                    m.d.comb += control_done.eq(
                        Mux(sweep_control_pass, passes >= 0,
                            passes + 1 >= sweep_passes))
                    with m.If(sweeping & control_done):
                        with m.If(sweep_control_pass):
                            # The control has run: it passes only if EVERY word
                            # mismatched.
                            m.d.sync += [
                                sweep_control_ok.eq(harness.errors
                                                    >= harness.checks),
                                sweep_control_pass.eq(0),
                            ]
                            m.next = "SWEEP_RECORD"
                        with m.Else():
                            m.d.sync += [cell_errors.eq(harness.errors),
                                         cell_burstdet.eq(burstdet_seen)]
                            m.next = "SWEEP_CONTROL"
                    with m.Elif(sweep_pending & ~sweeping):
                        m.d.sync += [sweeping.eq(1), sweep_cell.eq(0),
                                     passes.eq(0), sweep_done.eq(0),
                                     sweep_pending.eq(0)]
                        m.next = "SWEEP_CELL"
                    with m.Elif((pass_limit != 0) & (passes + 1 >= pass_limit)):
                        m.d.sync += finished.eq(1)
                        m.next = "HALTED"
                    with m.Else():
                        m.next = "WRITE_START"

            # SWEEP_RECORD -- one row, then on to the next cell.
            #
            # Errors SATURATE at the field width rather than wrapping: a wrapped
            # count that lands near zero reads as a pass, which is the failure
            # mode this whole exercise exists to stop.
            # SWEEP_CONTROL -- one pass with the comparator pointed at an
            # unreachable value. EVERY checked word must mismatch; if any match,
            # the comparator is not discriminating and the measurement above is
            # silence rather than evidence.
            with m.State("SWEEP_CONTROL"):
                m.d.comb += harness.clear.eq(1)
                m.d.sync += [sweep_control_pass.eq(1), passes.eq(0),
                             base.eq(0)]
                m.next = "WRITE_START"

            with m.State("SWEEP_RECORD"):
                saturated = Signal(ROW_ERROR_BITS)
                m.d.comb += saturated.eq(
                    Mux(cell_errors > (2**ROW_ERROR_BITS - 1),
                        2**ROW_ERROR_BITS - 1, cell_errors))
                m.d.comb += [
                    results_write.addr.eq(sweep_cell),
                    results_write.data.eq(
                        Cat(saturated, cell_burstdet,
                            cell_errors == 0, ~cell_selected,
                            sweep_control_ok)),
                    results_write.en.eq(1),
                ]
                m.d.sync += sweep_cell.eq(sweep_cell + 1)
                with m.If(sweep_cell + 1 >= SWEEP_CELLS):
                    m.d.sync += [sweep_done.eq(1), sweeping.eq(0),
                                 finished.eq(1)]
                    m.next = "HALTED"
                with m.Else():
                    m.next = "SWEEP_CELL"

            # SWEEP_CELL -- reset the per-cell counters and configure the part
            # for the new drive code before measuring it.
            with m.State("SWEEP_CELL"):
                # Clear the harness counters too, not just the local ones. They
                # were left accumulating across every cell, which saturated the
                # error field during the first one and made all 64 rows read
                # 16,777,215 -- obvious only because the field saturates instead
                # of wrapping.
                m.d.comb += harness.clear.eq(1)
                m.d.sync += [passes.eq(0), base.eq(0), burstdet_seen.eq(0),
                             bad_seen.eq(0)]
                with m.If(cell_selected):
                    m.next = "CONFIG_CR0"
                with m.Else():
                    # Masked out: record it as skipped without touching the part.
                    m.next = "SWEEP_RECORD"

            # HALTED -- the bounded run is over. Nothing is driven, the counters
            # hold, and the host reads them at its leisure.
            with m.State("HALTED"):
                # NOT terminal. `go` starts a fresh run.
                #
                # It used to be `pass`, so an engine that completed one run could
                # never be asked for another -- and the negative control is
                # ALWAYS a second run, which means no cell could ever produce a
                # verdict however well the first pass went (#226).
                #
                # Back to WRITE_START rather than RESET: RESET waits out a 2**16
                # cycle heartbeat for tRP/tRPH, which is right once per
                # configuration and wrong once per pass.
                with m.If(harness.go):
                    m.d.sync += [passes.eq(0), index.eq(0), recovery.eq(0),
                                 stall.eq(0), bad_seen.eq(0), base.eq(0)]
                    # THROUGH THE CONFIG STATES when an apply bit is set.
                    #
                    # `CONFIG_CR0` was reachable only from RESET, which runs once
                    # at power-up -- microseconds in, while `device_cr0` is still
                    # zero from CSR reset. The host writes CR0 long afterwards
                    # and then writes GO, so the part was NEVER configured: every
                    # measurement ran at its power-on default.
                    #
                    # It is invisible in the results because it makes them
                    # PERFECT: 16 latency codes x fixed/variable produced 32
                    # byte-identical rows, and 8 drive codes x 2 clock modes x 8
                    # phases produced 128 rows with the same first mismatch. An
                    # axis that does nothing looks like an axis that does not
                    # matter (#226).
                    with m.If(device_cr0[16] | device_cr1[16]):
                        m.next = "CONFIG_CR0"
                    with m.Else():
                        m.next = "WRITE_START"

        #
        # The engine's state, so a stalled sweep says WHERE.
        harness.add_read_only_register(REG_FSM_STATE, read=engine.state)
        harness.add_read_only_register(REG_DEVICE_READBACK,
                                       read=device_readback)

        # The controller's HANDSHAKE, not its FSM state. Amaranth's FSM carries
        # no readable `state` attribute here and adding one broke
        # `soc_hyperram_sim`, so `REG_CTRL_STATE` sat DECLARED AND UNBOUND -- and
        # an unbound address reads zero through the CSR transport, which is
        # indistinguishable from "the controller is in state 0". It was read off
        # the board as exactly that and used to rule out the controller, wrongly.
        # A register that cannot answer must not answer plausibly.
        #
        # What goes here instead is the exit condition of READ_RECOVER, split
        # into its two halves, because that is the state the engine parks in and
        # `psram.idle & (recovery >= recovery_cycles)` is what it is waiting for.
        # Reading the halves separately says which one is false, which the state
        # number never could.
        harness.add_read_only_register(
            REG_CTRL_STATE,
            read=Cat(psram.idle,                        # bit 0
                     recovery >= recovery_cycles,       # bit 1
                     start,                             # bit 2
                     stalled,                           # bit 3, sticky
                     Const(0, 28)))

        #
        # Die temperature. DTROUT[7] is the valid flag; sampling without it
        # latches a mid-conversion value that looks like a temperature.
        #
        die = Signal(8)
        if self._own_dtr:
            dtr_counter = Signal(DTR_PERIOD_BITS)
            m.d.sync += dtr_counter.eq(dtr_counter + 1)
            dtr_bits = [Signal(name=f"dtr{i}") for i in range(8)]
            m.submodules.dtr = Instance(
                "DTR", i_STARTPULSE=(dtr_counter == 0),
                **{f"o_DTROUT{i}": bit for i, bit in enumerate(dtr_bits)})
            with m.If(dtr_bits[7]):
                m.d.sync += die.eq(Cat(*dtr_bits))

        # A new command clears the finished flag. Placed after the FSM so that a
        # `go` arriving in the same cycle as a completion wins: Amaranth takes
        # the last assignment in a domain, and "the host asked for a new run" is
        # the more recent fact.
        with m.If(harness.go):
            m.d.sync += finished.eq(0)

        m.d.comb += [
            harness.busy.eq(1),
            harness.done.eq(finished),
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
        if platform is not None and self._own_leds:
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

    from board import CynthionPlatformRev1D4

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
