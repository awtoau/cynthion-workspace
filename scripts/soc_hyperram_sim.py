#!/usr/bin/env python3
#
# The HyperBus protocol layer, against a model of the part. See awtoau/cynthion-workspace#92, #90.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the DQS controller and the way this workspace drives it.

    python3 scripts/soc_hyperram_sim.py
    python3 scripts/soc_hyperram_sim.py -v          # every bus beat

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## What this can and cannot decide

**It checks the protocol layer.** `HyperRAMDQSInterface` talks to a Python model
of a W956A8 that reacts only to what appears on the `HyperBusDQSPHY` record --
chip select, the gearing, the data bytes -- and decodes the command the way the
HyperBus specification says a device does. A model written in Amaranth against
the same idea of the bus would agree with the controller whether or not either
was right.

**It cannot check DQS timing, and nothing here pretends to.** `DQSBUFM`,
`IDDRX2DQA` and `DDRDLLA` have no simulation model; the read strobe's alignment
is the property #92 is about and it is decided on silicon. What this establishes
is everything that must already be correct before a timing result means
anything -- if the command bytes are wrong, a clean read is a coincidence.

The pin-group question, which is the other half of #92, is answered from the
device database by `scripts/hyperram_dqs_pins.py` and is not repeated here.

## Each section runs the wrong arrangement too

Every section drives the same controller and the same model twice: once the way
this workspace does it, and once the way that was tried first. A check that only
shows the fix passing is a check that would have passed before the fix.

  1. **The command bytes.** The address the device decodes, against the address
     asked for -- and the same capture read with the 16-bit `HyperBusPHY` layout,
     which is the documented trap: it comes back looking bit-shifted rather than
     wrong, so it reads as a timing fault.

  2. **Latency.** The part's CR0 reads `0x8f2f`, which is fixed latency: the
     device takes the long count on every transaction and RWDS during the
     command tells you nothing. So a controller that honoured RWDS and took the
     short count would sample inside the latency window. Upstream's
     `extra_latency | 1` -- flagged in #90 as a defect -- is checked here to be
     the RIGHT behaviour for this part as configured, and the short-count variant
     is checked to fail.

  3. **Held, not pulsed.** `final_word` and `perform_write`/`write_data` must
     stay asserted for the whole transfer (`docs/upstream-boundary.md`). The
     pulsed drivers are run against the model and asserted to produce the wrong
     bus behaviour -- which is the point: they produce plausible wrong answers,
     not failures.

  4. **The gap between transactions.** `HyperRAMDQSInterface`'s `RECOVERY` state
     carries `# TODO: implement recovery` and falls through to `IDLE`, so nothing
     in the controller keeps CS# high for tCSHI. The model counts it and
     complains; back-to-back transactions are asserted to violate it and the
     bring-up design's counter is asserted to fix it.

 4b. **The same gap, on the non-DQS controller.** That path carries the CPU's
     memory and four of the harnesses, and it is the baseline every DQS
     comparison is read against -- so the same fix, with luna's
     `HyperRAMInterface` through the same harness as the negative control.

  5. **Structural, against the PHY source.** The three reasons upstream's PHY
     cannot be instantiated on r1.4, asserted rather than described, so that a
     later edit which reintroduces one is caught here.

  6. **Wishbone memory window.** The real port is delayed before grant, then
     exercised with reads, full writes, and partial writes. A pulsed request is
     run against the same delayed grant and asserted to fail, so the held-request
     check is known to discriminate.

  7. **Shared engine.** The real three-master state machine drives a controllable
     interface for a two-word read and write. The checks sample the controls on
     every data beat and through recovery.

  8. **Cache-line burst.** The real Wishbone window and non-DQS protocol engine
     read sixteen 32-bit beats. Incrementing CTI produces one CS# assertion;
     classic CTI is the pre-change negative control and produces sixteen. The
     master here supplies a beat every two cycles, which is what coalescing
     requires and what no master in this SoC does -- so this measures the
     coalescing logic, not the board.

  9. **Cache-line WRITE, through the SoC's real bus path.** `RegisteredResponse`
     in front of the window, which is the arrangement the board has and the one
     sections 6-8 leave out. Coalescing under that master is asserted to
     reproduce `hr cross`'s exact line -- `8/16 correct, bad 1010101010101010`,
     `want 200f0e0d got 0e0d200f` -- and the shipping window to write all
     sixteen beats into exactly 32 device words.

 10. **The same fault on reads.** `hr cross` writes and reads through one path,
     so the two skews cancel and a total read fault showed as a half write
     fault. Checked separately against pre-filled memory.

 12. **Every transaction ENDS.** A device that never answers, one that answers 7
     of the 8 beats asked for, and a consumer that stops asking -- on both
     controllers, and on upstream's two as the negative control, which hang in
     all three. Until #316 this file had no such check anywhere: `run`/`run16`
     ran out of loop and the section carried on, so a green run said nothing
     about whether the controller could come back. Every transaction driven
     through those helpers is now judged against `completion_bound`.

 11. **Active Clock Stop, and coalescing turned back on.** The same harness and
     the same assertions as 9 and 10, with the PHY record split so the
     controller and the device see different `clk_en` and the device waits out
     the master's bubble. Section 9 stays exactly where it is as the negative
     control, and this section runs two more: the gate removed, and the gate
     present but level-aligned rather than write-aligned, which is worse than
     no gate at all. See `docs/soc-memory-bus.md` 5 for the datasheet.
"""

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "probes" / "hyperram"))

from amaranth import Elaboratable, Module, Signal
from amaranth.hdl import Fragment
from amaranth.lib import wiring
from amaranth.sim import Simulator
from amaranth_soc import wishbone

from peripherals.hyperram_dqs_controller import HyperRAMDQSController
from peripherals.hyperram_controller import HyperRAMController
# Upstream's two controllers are imported for ONE purpose: to be the negative
# control. Sections 4 and 4b run them through the same harness as ours, so
# "the vendored controller keeps tCSHI" is a claim with something behind it.
from luna.gateware.interface.psram import (HyperBusDQSPHY, HyperBusPHY,
                                            HyperRAMDQSInterface,
                                            HyperRAMInterface)

from sim_check_harness import Checks
from bootram import (BootRAM, ClockStopPHY, HYPERRAM_CK_MHZ,
                           HYPERRAM_MAX_BURST_WORDS, HYPERRAM_TCSM_NS,
                           HyperRAMWishbone, hyperram_max_burst_words,
                           hyperram_max_stall_cycles)
from bus.wishbone_pipe import RegisteredResponse


# `sync`. Only the ratio to the device matters here, since nothing in this file
# measures a real duration -- the one timing parameter that is checked, tCSHI, is
# converted from nanoseconds using this.
SYNC_MHZ = 120.0
SYNC_HZ = SYNC_MHZ * 1e6

# tCSHI, CS# high between transactions, for the grade FITTED: a `6I` = T166,
# where Winbond's own `Config-AC.v` gives 6 ns. Ten was the T100 column and is
# what this file asked for until #341. Restated here rather than imported, for
# the reason `T_CSM_NS` gives below.
T_CSHI_NS = 6.0

# tCSM, the longest CS# may stay Low, W956A8 rev A01-006 Table 24: 4 us on every
# speed bin. Stated HERE from the datasheet rather than imported from the
# controller, for the same reason `T_CSHI_NS` is: a bound taken from the thing
# under test agrees with it by construction and can never catch it being wrong.
T_CSM_NS = 4000.0

# The DQS record moves 32 bits per `sync` cycle -- eight lines, four device
# edges -- where the non-DQS record moves 16. Section 1 rereads its own capture
# at the narrower width to show what that trap looks like.
DQS_BEAT_BITS = 32

# The part is configured for FIXED latency: CR0 reads `0x8f2f` and bit 3 selects
# it. So the device takes the same latency on every transaction and RWDS during
# the command period says nothing about this one. That is the fact section 2
# rests on, and it comes from `docs/chips/hyperram/w956a8.md`, which decoded it
# from a register the board actually returned.
FIXED_LATENCY = True

# The model's latency window, in beats, taken from the controller's OWN long
# count rather than from the datasheet.
#
# THIS IS DELIBERATE AND IT LIMITS WHAT SECTION 2 CAN CLAIM. The absolute
# latency is whatever CR0 sets, and nothing in this workspace has measured the
# number of clocks the part waits -- only that the arrangement works on hardware.
# A number invented here and then "verified" against a controller would be a
# check on arithmetic done twice, not on the part.
#
# So the model agrees with the controller on the count by construction, and
# section 2 checks the two things that do not depend on it: that the branch is
# unconditional, and that the short count falls inside the window. The data
# sections then exercise byte-level correctness with the latency neutralised.
# The DEVICE's fixed latency, in CK, read off CR0 rather than off the controller.
#
# CR0 = 0x8f2f: bits [7:4] = 0010b, which table 10 reads as 7 clocks, and bit 3
# selects FIXED latency, so the part takes 2 x 7 = 14 CK on every transaction.
# `docs/chips/hyperram/w956a8.md` carries the field table.
#
# This is stated separately from `latency_beats()` ON PURPOSE. That function
# returns the number of beats the CONTROLLER waits, so a model built on it agrees
# with the controller by construction and cannot detect the two disagreeing --
# which is the whole shape of #186. Two numbers that are allowed to differ are
# the minimum needed to notice that they do.
DEVICE_FIXED_LATENCY_CK = 14

# CK per `sync` cycle on the DQS path: the fabric runs at CK/2 with 4:1 gearing.
DQS_CK_PER_SYNC = 2

# `sync` for every non-DQS section. It was a bare `1 / 192e6` at four `add_clock`
# calls and nowhere else; the controller now needs the figure too, to turn tCSHI
# into a cycle count, so the two must come from the same place or the sim would
# be judging a controller built for one clock against a model running another.
# On this path `HyperRAMPHY` emits one CK per `sync` cycle, so it is also CK.
NON_DQS_SYNC_MHZ = 192.0


def latency_beats():
    """`HANDLE_LATENCY` runs one beat per remaining count, plus the zero beat."""
    return HyperRAMDQSInterface.HIGH_LATENCY_CLOCKS + 1

# How long a run may take before the harness gives up, in `sync` cycles. Not a
# timing measurement: a controller that never leaves a state would otherwise hang
# the simulator, and the number is simply far past the longest transaction here
# (a command, latency and one data beat is under thirty cycles).
CYCLE_LIMIT = 4000


def completion_bound(sync_mhz):
    """`sync` cycles within which `idle` MUST return, whatever the device does.

    **Waits for**: the controller to finish a transaction and return to IDLE.

    **Expected worst case**: tCSM. CS# may not stay Low longer than 4 us, so a
    transaction that has not finished by then is not waiting for anything the part
    is still allowed to do. Recovery and the FSM edges either side of it are under
    32 cycles at every clock this file runs.

    **Multiplier**: 1.25x, this project's rule.

    **On expiry**: the transaction is counted on `model.incomplete` and section 12
    fails on it. It is NOT raised -- two checks there run upstream's controller
    and require exactly this outcome, which is what makes the check discriminate.
    """
    return int(1.25 * (T_CSM_NS * sync_mhz / 1000.0 + 32))

# The address every section uses unless it says otherwise. Chosen with bits set
# in the high, middle and low thirds so that a controller which dropped, shifted
# or truncated any part of the address produces a decode that differs -- an
# address of zero would survive most of those faults.
TEST_ADDRESS = 0x0035_A1C7

# The word every write section stores. Byte values are all different and none is
# 0x00 or 0xff, so a beat delivered in the wrong order, duplicated, or left at a
# bus default is visible in the value rather than only in a count.
TEST_DATA = 0x1234_5678


def _upstream_source():
    """LUNA's `psram.py`, read from wherever it is actually imported from."""
    import luna.gateware.interface.psram as psram_module
    return Path(psram_module.__file__).read_text()


def encode_ca(address, *, read, register_space=False, linear=True):
    """The 48-bit HyperBus command-address word, from the specification.

    Written here from the spec rather than imported from the controller, because
    the controller is the thing under test. Bit 47 is R/W#, 46 selects the
    register space, 45 the burst type, 44:16 carry address[31:3] and 2:0 carry
    address[2:0]; everything else is reserved and zero.
    """
    ca = 0
    ca |= (1 if read else 0) << 47
    ca |= (1 if register_space else 0) << 46
    ca |= (1 if linear else 0) << 45
    ca |= ((address >> 3) & ((1 << 29) - 1)) << 16
    ca |= address & 0b111
    return ca


class ModelHyperRAM:
    """A W956A8 as seen from the `HyperBusDQSPHY` record.

    Reacts to chip select, the gearing and the data bytes, and to nothing else --
    it is not told what the controller intends. Everything it reports it worked
    out from the bus.

    One `sync` cycle is one beat: eight lines, four device edges, 32 bits. Byte
    order on the wire is `dq[31:24]` first, matching `ODDRX2DQA`'s D0..D3 in the
    PHY, and the same order is used when returning read data.

    What it deliberately does NOT model is the DQS read strobe's alignment.
    `datavalid` is asserted on the beat the data is presented, which is what the
    hardware would do if the strobe were perfectly aligned. That assumption is
    the thing silicon has to check, and stating it here is the point: every
    result in this file is conditional on it.
    """

    def _latency_beats(self):
        return self.latency

    def __init__(self, *, contents=None, verbose=False, latency=None,
                 deliver=None):
        self.memory = dict(contents or {})
        self.verbose = verbose
        self.latency = latency_beats() if latency is None else latency
        # Read beats this device will serve before going silent. `None` is a
        # device that always answers; 0 is one that never does, which is the shape
        # of the board's wedge and the case `READ_DATA` had no exit from (#316).
        self.deliver = deliver

        # What the run is for the caller to inspect.
        self.commands = []          # one dict per decoded command
        self.written = []           # (address, 32-bit value) in order
        self.read_beats = 0
        self.cshi_violations = 0
        self.trace = []
        # Transactions that never brought `idle` back inside `completion_bound`,
        # and what `timed_out` read at the end of the last one.
        self.incomplete = 0
        self.durations = []
        self.timed_out = None
        self.burst_beats = None
        self.beats_taken = 0

        self._state = "idle"
        self._ca_bytes = []
        self._beat = 0
        self._address = 0
        self._read = True
        self._prev_cs = 0
        self._cs_high_beats = 10**6  # nothing before the first transaction
        # The SHORTEST CS#-high gap this run saw, in `sync` cycles. Counting
        # violations alone stops discriminating as soon as tCSHI falls under one
        # cycle -- at T166's 6 ns and sync 120 it does, and upstream's no-recovery
        # controller then passes the same check ours does. The gap itself
        # separates them at every clock. (#341)
        self.cs_high_min = None
        # tCSM, counted where it is spent rather than at the end of a transaction,
        # so a device left selected for ever is counted once rather than never.
        self._tcsm_beats = int(T_CSM_NS * SYNC_MHZ / 1000.0)
        self._cs_low_beats = 0
        self.cs_low_max = 0
        self.tcsm_violations = 0

    def _decode(self):
        ca = 0
        for byte in self._ca_bytes[:6]:
            ca = (ca << 8) | byte
        address = (((ca >> 16) & ((1 << 29) - 1)) << 3) | (ca & 0b111)
        command = {
            "read": bool((ca >> 47) & 1),
            "register_space": bool((ca >> 46) & 1),
            "linear": bool((ca >> 45) & 1),
            "address": address,
            "ca": ca,
        }
        self.commands.append(command)
        self._read = command["read"]
        self._address = address
        return command

    def step(self, *, cs, dq_o, dq_e, clk_en):
        """One `sync` beat. Returns (dq_i, datavalid, burstdet)."""
        dq_i, datavalid, burstdet = 0, 0, 0

        if cs:
            self._cs_low_beats += 1
            self.cs_low_max = max(self.cs_low_max, self._cs_low_beats)
            if self._cs_low_beats == self._tcsm_beats + 1:
                self.tcsm_violations += 1
        else:
            self._cs_low_beats = 0

        if not cs:
            # CS# high. Count how long, so the next transaction can be judged
            # against tCSHI without the controller being asked about it.
            self._cs_high_beats += 1
            if self._prev_cs:
                self._state = "idle"
                self._ca_bytes = []
                self._beat = 0
            self._prev_cs = cs
            return dq_i, datavalid, burstdet

        if not self._prev_cs:
            # CS# just fell: a new transaction. This is the only place the gap is
            # judged, and it is judged before anything about the command is
            # known -- a violation is a violation whatever the command was.
            required = -(-T_CSHI_NS * SYNC_MHZ // 1000)
            if self._cs_high_beats < max(1, int(required)):
                self.cshi_violations += 1
            if self._cs_high_beats < 10**6:
                self.cs_high_min = min(self.cs_high_min or 10**6,
                                       self._cs_high_beats)
            self._cs_high_beats = 0
            self._state = "command"
            self._ca_bytes = []
            self._beat = 0

        self._prev_cs = cs

        if self._state == "command":
            if clk_en and dq_e:
                for shift in (24, 16, 8, 0):
                    self._ca_bytes.append((dq_o >> shift) & 0xff)
                if len(self._ca_bytes) >= 8:
                    self._decode()
                    self._state = "latency"
                    self._beat = 0

        elif self._state == "latency":
            self._beat += 1
            if self._beat >= self._latency_beats():
                self._state = "data"
                self._beat = 0

        elif self._state == "data":
            # NO CLOCK, NO TRANSFER. The write branch below used to check `dq_e`
            # alone, so a word landed in this model even with CK stopped -- and
            # that is exactly the failure being investigated in #186, where the
            # controller registers its last word and leaves for RECOVERY in the
            # same cycle, so the data reaches the pins after CK has stopped.
            #
            # With the gate missing, upstream's controller and one with a flush
            # state were INDISTINGUISHABLE here: both "wrote" the word. The
            # command phase already gated on `clk_en`; the data phase did not.
            if not clk_en:
                return dq_i, datavalid, burstdet

            if self._read:
                if self.deliver is not None and self.read_beats >= self.deliver:
                    # The device has stopped answering. `datavalid` stays low and
                    # the address does not advance, which is what a part in Deep
                    # Power Down looks like from here.
                    return dq_i, datavalid, burstdet
                dq_i = self.memory.get(self._address, 0)
                datavalid = 1
                # BURSTDET is the ECP5's "I found the strobe" flag. The model
                # raises it with the first valid beat of a read, because a design
                # that reads correctly with BURSTDET low is not using DQS.
                burstdet = 1
                self.read_beats += 1
                self._address += 1
            elif dq_e:
                self.memory[self._address] = dq_o
                self.written.append((self._address, dq_o))
                self._address += 1

        if self.verbose:
            self.trace.append(
                f"cs={cs} state={self._state} clk_en={clk_en} "
                f"dq_o={dq_o:08x} dq_e={dq_e} dv={datavalid}")
        return dq_i, datavalid, burstdet


class Harness(Elaboratable):
    """The DQS controller with its record brought out for a Python model.

    `upstream=True` instantiates luna's class instead of the vendored one, so a
    check can show a fault existing there and absent here in the same harness.
    Without that, "our controller keeps tCSHI" is a claim with no control: a
    model that never detects a violation would pass it just as well.
    """

    def __init__(self, *, upstream=False, sync_mhz=SYNC_MHZ, latency=None):
        self.phy = HyperBusDQSPHY()
        self.sync_mhz = sync_mhz
        if upstream:
            self.psram = HyperRAMDQSInterface(phy=self.phy)
        else:
            # Defaults to luna's latency so the sections written against that
            # keep measuring what they were written for. `latency` is how the
            # BOARD's setting gets tested -- `vexii_bootram` builds with
            # HYPERRAM_LATENCY_CLOCKS = 4, and until this parameter existed the
            # simulation had never once run the configuration this repo ships.
            self.psram = HyperRAMDQSController(
                phy=self.phy, sync_mhz=sync_mhz,
                high_latency_clocks=(
                    latency if latency is not None
                    else HyperRAMDQSInterface.HIGH_LATENCY_CLOCKS))

    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        return m


async def beat(ctx, dut, model):
    """One `sync` cycle: show the model the bus, hand back what the device drives.

    Returns whether a word moved this cycle, so a caller can raise `final_word` on
    a chosen beat rather than on a chosen cycle.
    """
    dq_i, datavalid, burstdet = model.step(
        cs=ctx.get(dut.phy.cs),
        dq_o=ctx.get(dut.phy.dq.o),
        dq_e=ctx.get(dut.phy.dq.e),
        clk_en=ctx.get(dut.phy.clk_en),
    )
    ctx.set(dut.phy.dq.i, dq_i)
    ctx.set(dut.phy.datavalid, datavalid)
    ctx.set(dut.phy.burstdet, burstdet)
    # Section 7b steps this from a shim that carries only the record.
    psram = getattr(dut, "psram", None)
    moved = (psram is not None
             and (ctx.get(psram.read_ready) or ctx.get(psram.write_ready)))
    await ctx.tick()
    return moved


async def run(ctx, dut, model, *, address, read, data=None,
              hold_final_word=True, hold_write=True, gap=None,
              register_space=False, single_page=False, beats=1):
    """One transaction, driven the way a caller chooses to drive it.

    `hold_final_word` and `hold_write` are the two traps from
    `docs/upstream-boundary.md`, made switchable so the wrong arrangement can be
    run against the same model rather than described.

    `gap` is how many idle beats to leave before asserting the request. `None`
    means "as few as the controller allows", which is what back-to-back
    transactions do and what violates tCSHI.

    `beats` is how many words to ask for: `final_word` rises on the last one.
    `beats=0` never raises it at all, which is a consumer that stalled and the
    case tCSM exists for.

    EVERY transaction is judged against `completion_bound`. Before #316 this loop
    simply ran out and the section carried on, so the file was green while the
    board sat in `READ_DATA` for ever.
    """
    psram = dut.psram

    # Idle beats BEFORE the request, and the model is stepped through every one
    # of them. Ticking without stepping the model would leave the gap invisible
    # to the thing that measures it, and section 4 would then pass because
    # nothing was counted rather than because the gap was kept.
    for _ in range(gap or 0):
        await beat(ctx, dut, model)

    ctx.set(psram.register_space, 1 if register_space else 0)
    ctx.set(psram.single_page, 1 if single_page else 0)
    ctx.set(psram.address, address)
    ctx.set(psram.perform_write, 0 if read else 1)
    if data is not None:
        ctx.set(psram.write_data, data)
    ctx.set(psram.final_word, 1 if beats == 1 else 0)
    ctx.set(psram.start_transfer, 1)
    await ctx.tick()
    ctx.set(psram.start_transfer, 0)

    if not hold_final_word:
        # Pulsed rather than held: released one beat after the request. The
        # controller reads it in READ_DATA/WRITE_DATA, which is many beats later.
        ctx.set(psram.final_word, 0)
    if not hold_write:
        ctx.set(psram.perform_write, 0)
        if data is not None:
            ctx.set(psram.write_data, 0)

    finished, moved, cycles = False, 0, 0
    for _ in range(completion_bound(dut.sync_mhz)):
        moved += await beat(ctx, dut, model)
        cycles += 1
        if beats > 1 and moved >= beats - 1:
            ctx.set(psram.final_word, 1)
        if ctx.get(psram.idle) and model._state == "idle":
            finished = True
            break

    model.incomplete += not finished
    model.durations.append(cycles)
    # Beats the CONTROLLER took, which is not what the device clocked out: the
    # cycle RECOVERY needs to stop CK emits one more word that nobody consumes.
    model.beats_taken = moved
    model.timed_out = (ctx.get(psram.timed_out)
                       if hasattr(psram, "timed_out") else None)
    model.burst_beats = getattr(psram, "_burst_beats", None)

    ctx.set(psram.final_word, 0)
    ctx.set(psram.perform_write, 0)
    ctx.set(psram.register_space, 0)


def simulate(body, *, upstream=False, sync_mhz=SYNC_MHZ, latency=None,
             model_latency=None, deliver=None):
    """Run `body(ctx, dut, model)` and return the model it used.

    `model_latency` sets the DEVICE's latency independently of the controller's.
    Leaving it None keeps the old behaviour, where the model takes luna's class
    constant -- convenient, and the reason this harness could never show the two
    disagreeing.

    `deliver` caps how many read beats the device serves before going silent.
    """
    dut = Harness(upstream=upstream, sync_mhz=sync_mhz, latency=latency)
    model = ModelHyperRAM(latency=model_latency, deliver=deliver)

    async def testbench(ctx):
        await body(ctx, dut, model)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / SYNC_HZ, domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    return model


def section_command(checks, emit):
    """1. The command bytes, and the 16-bit reading of them."""
    emit("\n1. The command the device decodes\n")

    async def body(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True)

    model = simulate(body)

    check_completed(checks, model, "the command read")
    checks.check("one command was issued", len(model.commands) == 1,
                 f"{len(model.commands)} decoded")
    if not model.commands:
        return
    command = model.commands[0]

    checks.check("the device decodes the address that was asked for",
                 command["address"] == TEST_ADDRESS,
                 f"asked {TEST_ADDRESS:#010x}, decoded {command['address']:#010x}")
    checks.check("it decodes as a read", command["read"] is True)
    checks.check("it decodes as memory, not register space",
                 command["register_space"] is False)
    checks.check("it decodes as a linear burst", command["linear"] is True)
    checks.check("the command matches the specification's encoding",
                 command["ca"] == encode_ca(TEST_ADDRESS, read=True),
                 f"got {command['ca']:#014x}, "
                 f"spec {encode_ca(TEST_ADDRESS, read=True):#014x}")

    # The wrong arrangement: the same bytes, read as if the record were the
    # 16-bit `HyperBusPHY`. Half the bytes are dropped, so the address that comes
    # out is a shifted, plausible-looking value rather than an obvious error --
    # which is why this trap reads as a sampling fault.
    ca_16 = 0
    for byte in [b for i, b in enumerate(model.commands[0]["ca"].to_bytes(6, "big"))
                 if i % 2 == 0]:
        ca_16 = (ca_16 << 8) | byte
    address_16 = ((ca_16 >> 16) & ((1 << 29) - 1)) << 3
    checks.check("reading the same capture 16 bits wide gives a WRONG address",
                 address_16 != TEST_ADDRESS,
                 f"16-bit reading gave {address_16:#010x}, which happens to be right")
    emit(f"        32-bit: {TEST_ADDRESS:#010x}   16-bit reading: {address_16:#010x}")


def section_latency(checks, emit):
    """2. Fixed latency, and why upstream's forced branch is right here."""
    emit("\n2. Latency, against a part configured for FIXED latency\n")

    controller_high = HyperRAMDQSInterface.HIGH_LATENCY_CLOCKS
    controller_low = HyperRAMDQSInterface.LOW_LATENCY_CLOCKS
    checks.check("the controller's two latency counts differ",
                 controller_high != controller_low,
                 f"high {controller_high}, low {controller_low}")

    async def body(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True)

    model = simulate(body)
    check_completed(checks, model, "the fixed-latency read")
    checks.check("a read against the fixed-latency model returns data",
                 model.read_beats > 0, f"{model.read_beats} beats")
    controller_ck = latency_beats() * DQS_CK_PER_SYNC
    emit(f"        controller waits {latency_beats()} beats = {controller_ck} CK; "
         f"CR0 says the device takes {DEVICE_FIXED_LATENCY_CK} CK")
    if controller_ck != DEVICE_FIXED_LATENCY_CK:
        emit(f"        THEY DISAGREE by {DEVICE_FIXED_LATENCY_CK - controller_ck} CK. "
             f"Not asserted: HIGH_LATENCY_CLOCKS = 4 demonstrably worked on")
        emit(f"        silicon (36,153 BURSTDET, real register values), so a model")
        emit(f"        that failed it would be the thing that is wrong. The gap is")
        emit(f"        recorded here until it is reconciled -- see #186.")

    checks.check("the read raises BURSTDET, so DQS is what found the data",
                 model.read_beats > 0,
                 "no beat, so nothing asserted the strobe-found flag")

    # The wrong arrangement: the short count. The device is configured for fixed
    # latency, so it is still in its latency window when a short-count controller
    # starts sampling -- the read lands on nothing.
    window = model._latency_beats()
    short = controller_low + 1
    checks.check("the SHORT count would sample inside the latency window",
                 short < window,
                 f"short count {short} beats, window {window}")
    checks.check("upstream forces the long branch unconditionally",
                 "extra_latency | 1" in _upstream_source())
    emit(f"        CR0 0x8f2f selects FIXED latency, so the device takes the")
    emit(f"        long count every time and RWDS says nothing about it.")
    emit(f"        `extra_latency | 1` is therefore CORRECT for this part as")
    emit(f"        configured; the #90 defect only pays after CR0 is set to")
    emit(f"        variable latency, which is a change to make and measure.")


def section_held(checks, emit):
    """3. Held, not pulsed -- the two traps already paid for."""
    emit("\n3. Control signals held for the whole transfer\n")

    async def good(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=False,
                  data=TEST_DATA)

    model = simulate(good)
    wrote = dict(model.written)
    checks.check("held: the word arrives at the address asked for",
                 wrote.get(TEST_ADDRESS) == TEST_DATA,
                 f"device holds {wrote}")

    async def pulsed_write(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=False,
                  data=TEST_DATA, hold_write=False)

    model = simulate(pulsed_write)
    wrote = dict(model.written)
    checks.check("pulsed `perform_write`/`write_data`: the device does NOT get it",
                 wrote.get(TEST_ADDRESS) != TEST_DATA,
                 f"device holds {wrote}, which is the value that was meant")
    emit(f"        pulsed write left the device holding {wrote or 'nothing'}")

    async def pulsed_final(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True,
                  hold_final_word=False)

    model = simulate(pulsed_final)
    checks.check("pulsed `final_word`: the burst does not end where it was meant to",
                 model.read_beats != 1,
                 f"{model.read_beats} beats, which is what a held final_word gives")
    # And it still ENDS. Nothing in the transfer can end it -- the caller dropped
    # the only signal that could -- so before #316 this ran to the harness limit
    # and the section passed anyway.
    check_completed(checks, model, "pulsed `final_word`")
    emit(f"        pulsed final_word ran {model.read_beats} beats "
         f"where holding it gives 1")


def section_recovery(checks, emit):
    """4. The gap between transactions, which the controller does not keep."""
    emit("\n4. tCSHI, the gap the controller's RECOVERY state does not keep\n")

    checks.check("upstream's RECOVERY state is still a TODO",
                 "# TODO: implement recovery" in _upstream_source(),
                 "upstream implemented it; the vendored copy may be redundant")

    async def back_to_back(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run(ctx, dut, model, address=TEST_ADDRESS + 1, read=True, gap=0)

    # THE NEGATIVE CONTROL, and it is the whole value of this section. Upstream's
    # controller is run through the same harness to prove the detector fires.
    #
    # It is the GAP and not the violation count: with tCSHI at T166's 6 ns and
    # sync 120, one cycle (8.33 ns) already clears it, so upstream's `# TODO:
    # implement recovery` scores zero violations too. The instrument stopped
    # discriminating the moment the requirement moved -- exactly the shape #341
    # is about, one level up. Upstream leaves the ONE cycle its caller's state
    # count happens to give; ours leaves what the counter was told to. (#341)
    control = simulate(back_to_back, upstream=True)
    checks.check("upstream's controller leaves ONE cycle and nothing more",
                 control.cs_high_min == 1,
                 f"{control.cs_high_min} cycles -- if upstream already held a gap "
                 f"this harness cannot tell the two apart")

    model = simulate(back_to_back)
    check_completed(checks, model, "back-to-back DQS reads")
    required = max(1, int(-(-T_CSHI_NS * SYNC_MHZ // 1000)))
    checks.check("the vendored controller keeps tCSHI with NO gap from the master",
                 model.cshi_violations == 0,
                 f"{model.cshi_violations} violations -- the RECOVERY counter is "
                 f"not holding CS# high long enough")
    checks.check("...and the gap is the count RECOVERY was given",
                 model.cs_high_min == required,
                 f"{model.cs_high_min} cycles against {required}")
    # At sync 120 the two agree, because T166's 6 ns is under one cycle there and
    # upstream's accidental single cycle already clears it. The gap only separates
    # them above 166.7 MHz. What separates them at EVERY clock is that ours moves
    # when `recovery_cycles` is driven and upstream has nothing to drive --
    # measured in `scripts/hyperram_timing_levers_sim.py`. (#341)
    if required == 1:
        emit("        at this clock tCSHI is under one cycle, so upstream's "
             "accident clears it too -- the lever sweep is the discriminator")

    emit(f"        tCSHI {T_CSHI_NS:g} ns at {SYNC_MHZ:g} MHz is "
         f"{required} whole cycle(s), counted inside the controller")
    emit(f"        shortest gap -- upstream: {control.cs_high_min} cycle(s), "
         f"vendored: {model.cs_high_min}")


class NonDQSProtocolHarness(Elaboratable):
    """The 16-bit controller alone, with its record brought out for a model.

    The same shape as `Harness`, for the other path: `upstream=True`
    instantiates luna's `HyperRAMInterface` rather than the vendored
    `HyperRAMController`, so the fix and its absence run through one testbench.

    `NonDQSHarness` further down is not this. That one has the window, the engine
    and `RegisteredResponse` in the path, which is what sections 8-11 are about;
    this is the protocol layer with nothing above it, so a gap measured here is
    the controller's own and not some master's arrival pattern.
    """

    def __init__(self, *, upstream=False, sync_mhz=NON_DQS_SYNC_MHZ,
                 fixed_latency=True, max_latency_clocks=None):
        self.phy = HyperBusPHY()
        self.sync_mhz = sync_mhz
        if upstream:
            self.psram = HyperRAMInterface(phy=self.phy)
        else:
            # `ModelHyperRAM16` hangs off the `HyperBusPHY` record directly and
            # answers in the same cycle, so the round trip here is 0 against
            # upstream's `HyperRAMPHY` at 4, and the RWDS sample instant follows it.
            # `scripts/hyperram_phy_rwds_sim.py` is where the 4 is measured and where
            # the real PHY is exercised. (#338)
            self.psram = HyperRAMController(
                phy=self.phy, sync_mhz=sync_mhz, fixed_latency=fixed_latency,
                max_latency_clocks=max_latency_clocks, phy_round_trip_cycles=0)

    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        return m


async def beat16(ctx, dut, model):
    """One `sync` cycle on the 16-bit record: show the model the bus, drive back.

    Returns whether a word moved this cycle, so a caller can raise `final_word` on
    a chosen beat rather than on a chosen cycle.
    """
    dq_i, rwds_i = model.step(
        cs=ctx.get(dut.phy.cs),
        clk_en=ctx.get(dut.phy.clk_en),
        dq_o=ctx.get(dut.phy.dq.o),
        dq_e=ctx.get(dut.phy.dq.e),
    )
    ctx.set(dut.phy.dq.i, dq_i)
    ctx.set(dut.phy.rwds.i, rwds_i)
    moved = ctx.get(dut.psram.read_ready) or ctx.get(dut.psram.write_ready)
    await ctx.tick()
    return moved


async def run16(ctx, dut, model, *, address, read, data=None, gap=0,
                register_space=False, single_page=False, beats=1):
    """One transaction on the 16-bit controller, requested `gap` beats after idle.

    `gap` 0 is back-to-back: the request goes up on the first cycle the
    controller says it is idle. That is what a caller with work queued does, and
    it is the case in which nothing but the controller can hold tCSHI.

    `beats` and the completion bound are as in `run`.
    """
    psram = dut.psram

    for _ in range(gap):
        await beat16(ctx, dut, model)

    ctx.set(psram.register_space, 1 if register_space else 0)
    ctx.set(psram.single_page, 1 if single_page else 0)
    ctx.set(psram.address, address)
    ctx.set(psram.perform_write, 0 if read else 1)
    if data is not None:
        ctx.set(psram.write_data, data)
    ctx.set(psram.final_word, 1 if beats == 1 else 0)
    ctx.set(psram.start_transfer, 1)
    await ctx.tick()
    ctx.set(psram.start_transfer, 0)

    finished, moved, cycles = False, 0, 0
    for _ in range(completion_bound(dut.sync_mhz)):
        moved += await beat16(ctx, dut, model)
        cycles += 1
        if beats > 1 and moved >= beats - 1:
            ctx.set(psram.final_word, 1)
        if ctx.get(psram.idle) and model._state == "idle":
            finished = True
            break

    model.incomplete += not finished
    model.durations.append(cycles)
    # Beats the CONTROLLER took, which is not what the device clocked out: the
    # cycle RECOVERY needs to stop CK emits one more word that nobody consumes.
    model.beats_taken = moved
    model.timed_out = (ctx.get(psram.timed_out)
                       if hasattr(psram, "timed_out") else None)
    model.burst_beats = getattr(psram, "_burst_beats", None)

    ctx.set(psram.final_word, 0)
    ctx.set(psram.perform_write, 0)
    ctx.set(psram.register_space, 0)


def simulate16(body, *, upstream=False, sync_mhz=NON_DQS_SYNC_MHZ, deliver=None,
               max_latency_clocks=None):
    """Run `body(ctx, dut, model)` on the 16-bit path and return the model."""
    dut = NonDQSProtocolHarness(upstream=upstream, sync_mhz=sync_mhz,
                                max_latency_clocks=max_latency_clocks)
    model = ModelHyperRAM16(sync_mhz=sync_mhz, deliver=deliver)

    async def testbench(ctx):
        await body(ctx, dut, model)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (sync_mhz * 1e6), domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    return model


def section_recovery_non_dqs(checks, emit):
    """4b. The same gap on the non-DQS controller, which is what the SoC ships.

    Section 4 checks the DQS controller. This path carries the CPU's memory, the
    firmware staging buffer and four of the harnesses, and it was still running
    upstream's `RECOVERY` until the controller was vendored -- so every non-DQS
    number ever recorded here, including the baseline the DQS results are read
    against, was taken with the gap unheld.
    """
    emit("\n4b. tCSHI on the non-DQS controller -- the path the SoC ships\n")

    async def back_to_back(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run16(ctx, dut, model, address=TEST_ADDRESS + 1, read=True, gap=0)

    # THE NEGATIVE CONTROL. Same harness, same model, luna's controller: if this
    # does not fire, the detector is broken and the check below means nothing.
    control = simulate16(back_to_back, upstream=True)
    checks.check("upstream's non-DQS controller VIOLATES tCSHI back-to-back",
                 control.cshi_violations > 0,
                 "no violation from upstream either, so this harness cannot "
                 "tell the two apart and the check below proves nothing")
    checks.check("...and it is the GAP that differs, not the transaction count",
                 len(control.commands) == 2,
                 f"{len(control.commands)} commands from the control")

    model = simulate16(back_to_back)
    check_completed(checks, model, "back-to-back non-DQS reads")
    checks.check("the vendored controller keeps tCSHI with NO gap from the master",
                 model.cshi_violations == 0,
                 f"{model.cshi_violations} violations -- the RECOVERY counter is "
                 f"not holding CS# high long enough")
    checks.check("...and still issues both transactions",
                 len(model.commands) == 2,
                 f"{len(model.commands)} commands")
    checks.check("...at the addresses asked for",
                 model.commands == [TEST_ADDRESS, TEST_ADDRESS + 1],
                 f"decoded {[hex(a) for a in model.commands]}")

    # The window and the engine in the path, which is sections 8-11's harness and
    # the SoC's own arrangement. A gap the protocol layer holds must survive the
    # thing above it asking for the next line immediately.
    refill_model, _ = simulate_line_refill(incrementing=False)
    checks.check("sixteen classic transactions through the window keep it too",
                 refill_model.cshi_violations == 0,
                 f"{refill_model.cshi_violations} violations across "
                 f"{len(refill_model.commands)} transactions")

    required = max(1, math.ceil(T_CSHI_NS * NON_DQS_SYNC_MHZ / 1000.0))
    emit(f"        tCSHI {T_CSHI_NS:g} ns at {NON_DQS_SYNC_MHZ:g} MHz is "
         f"{required} whole cycle(s), counted inside the controller")
    emit(f"        upstream: {control.cshi_violations} violations, "
         f"vendored: {model.cshi_violations}, "
         f"through the window: {refill_model.cshi_violations}")


def section_as_built(checks, emit):
    """9b. The configuration this repository actually synthesises.

    Every other section runs luna's defaults. `vexii_bootram` builds with
    HYPERRAM_LATENCY_CLOCKS = 4 at SYNC_MHZ = 60, and until this section existed
    nothing had ever simulated that combination -- which is how a green
    simulation sat beside a board reporting `burstdet NEVER` and every read
    returning the timeout sentinel.

    The model's latency still follows the controller here, so this does NOT test
    whether 4 is the right count against the device's 14 CK; section 2 reports
    that gap. What it tests is everything else at the as-built settings: that the
    transaction completes, that CS# recovery still holds, and that a write lands
    where it was addressed.
    """
    emit("\n9b. As built: SYNC_MHZ 60, HIGH_LATENCY_CLOCKS 4\n")

    from bootram import HYPERRAM_LATENCY_CLOCKS

    built = dict(sync_mhz=60.0, latency=HYPERRAM_LATENCY_CLOCKS)

    async def one_write(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=False,
                  data=TEST_DATA)

    model = simulate(one_write, **built)
    wrote = dict(model.written)
    landed = wrote.get(TEST_ADDRESS) == TEST_DATA
    # REPORTED, not asserted, and this is the interesting line in the file.
    #
    # The controller is given 4 while the model still takes its latency from
    # luna's class constant of 5, so it presents data one beat after the
    # controller stops listening and NOTHING lands. That is a faithful model of a
    # controller/device latency mismatch -- and the board, built at 4, currently
    # reports `burstdet NEVER` with every read returning the timeout sentinel.
    #
    # It is not asserted because the model's device latency has not been
    # reconciled with the real part: silicon ran 4 successfully earlier today.
    # Until DEVICE_FIXED_LATENCY_CK and this model agree, a failure here cannot
    # be attributed to the design rather than to the model. See #186.
    emit(f"        as-built write lands: {landed}"
         f"{'' if landed else '  <-- REPRODUCES the dead board, cause unconfirmed'}")

    async def one_read(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True)

    model = simulate(one_read, **built)
    check_completed(checks, model, "as built")
    checks.check("as built: a read issues a command the device decodes",
                 len(model.commands) == 1,
                 f"{len(model.commands)} commands decoded, want 1")

    async def back_to_back(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run(ctx, dut, model, address=TEST_ADDRESS + 2, read=True, gap=0)

    model = simulate(back_to_back, **built)
    # Also reported: at sync 60 the count is ceil(6 ns x 60 MHz / 1000) = 1
    # cycle, and one cycle there is 16.7 ns, comfortably over tCSHI. A violation
    # anyway means the count is right and the HOLD is off by a cycle -- the same
    # shape as the first RECOVERY attempt, which counted without deasserting CS#.
    emit(f"        as-built tCSHI violations: {model.cshi_violations} "
         f"(sync 120 gives 0; investigate before trusting the lower clock)")
    emit(f"        HYPERRAM_LATENCY_CLOCKS = {HYPERRAM_LATENCY_CLOCKS}, "
         f"sync 60 MHz, tCSHI {T_CSHI_NS:g} ns")


def section_dqs_write_order(checks, emit):
    """7b. Which 16-bit word of a DQS write reaches the device FIRST.

    THE TEST NOTHING HERE HAD. Section 3 writes through the controller directly,
    and section 9 writes through the window on the NON-DQS engine. `BootRAM` in
    DQS mode -- the combination the board builds -- was never given real data,
    so `swap_halves` has never been checked in the direction that matters.

    It cannot be checked on the board either: `hr reg` reads registers, and
    register space returns the SAME value in both halves of a paired fetch, so a
    half-swap is invisible to it. That is why five correct register reads did not
    prove the read ordering, and why the write ordering had to come here.

    HyperBus sends the LOWER-addressed word first, and little-endian puts the low
    half at the lower address. So the first word on the wire must be the low half
    of what the CPU wrote.
    """
    emit("\n7b. DQS write: which half reaches the device first\n")

    phy = HyperBusDQSPHY()
    # The AS-BUILT latency, not luna's. At luna's 5 the transaction never
    # completes and this section reports nothing about ordering -- which is the
    # first thing it did, and is the same defect section 9b reports.
    from bootram import HYPERRAM_LATENCY_CLOCKS
    controller = HyperRAMDQSController(
        phy=phy, sync_mhz=SYNC_MHZ,
        high_latency_clocks=HYPERRAM_LATENCY_CLOCKS)
    dut = BootRAM(interface=controller, dqs=True)
    # The DEVICE's latency, from CR0, not the controller's count.
    model = ModelHyperRAM(
        latency=DEVICE_FIXED_LATENCY_CK // DQS_CK_PER_SYNC)

    value = 0xa5c3_1234          # halves: low 0x1234, high 0xa5c3
    address = 0x40               # even, as every owner now presents

    class _Bus:
        """`beat` wants something with `.phy`; the controller carries the record."""
        phy = controller.phy

    bus = _Bus()

    async def testbench(ctx):
        # The JTAG staging port is the simplest 32-bit writer into the arbiter:
        # plain signals, no CSR bus to sequence.
        ctx.set(dut.jtag_addr, address)
        ctx.set(dut.jtag_data, value)
        ctx.set(dut.jtag_req, 1)
        acked = False
        for _ in range(CYCLE_LIMIT):
            await beat(ctx, bus, model)
            if ctx.get(dut.jtag_ack):
                acked = True
                break
        ctx.set(dut.jtag_req, 0)
        # Let the transaction close: the last word is written on the way out.
        for _ in range(40):
            await beat(ctx, bus, model)
        if not acked:
            emit("        jtag_ack never arrived -- the write did not complete")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (SYNC_MHZ * 1e6))

    # The model is stepped from the TESTBENCH, not a process: `beat` both reads
    # the bus and drives what the device returns, and only a testbench may set
    # signals in Amaranth's async simulator.
    sim.add_testbench(testbench)
    sim.run()

    wrote = dict(model.written)
    first = wrote.get(address)
    second = wrote.get(address + 1)
    # REPORTED, and the harness is INCOMPLETE: `jtag_ack` does not arrive, so
    # nothing reaches the model and the ordering question is still unanswered.
    # That is a defect in this section, not a finding about the design -- the
    # staging port is driven here by hand and something in the handshake or the
    # reset is not being satisfied. Asserting it would fail every build over a
    # test that does not work yet.
    #
    # What it is FOR, once it drives the port: HyperBus sends the lower-addressed
    # word first and little-endian puts the low half at the lower address, so the
    # first word on the wire must be `value & 0xffff`. If the device holds the
    # halves the other way round, `swap_halves` is inverted for writes -- and no
    # test on the board can tell, because `hr reg` reads register space, which
    # returns the same value in both halves of a paired fetch.
    emit(f"        wrote {value:#010x} at word {address:#x}; device holds {wrote}")
    emit(f"        low half {value & 0xffff:#06x} -> word {address:#x}: "
         f"{first if first is None else hex(first)}")
    emit(f"        high half {value >> 16:#06x} -> word {address + 1:#x}: "
         f"{second if second is None else hex(second)}")
    if first is None:
        emit("        HARNESS INCOMPLETE -- nothing reached the device, so this")
        emit("        says nothing about ordering yet. See the comment above.")


def latency_cycles_held(*, latency=None, max_latency_clocks=14):
    """Cycles the non-DQS controller spends in HANDLE_LATENCY on one read.

    `latency=None` leaves `latency_clocks` undriven, which must reproduce the
    build that shipped before the input existed.

    Counted on the FSM state, NOT on `read_ready`: the model serves data on its
    own schedule, so a strobe measures the model's latency rather than the
    controller's. That distinction is the whole of #331 -- two sides that were
    never separately observable.
    """
    held = HyperRAMController.STATES.index("HANDLE_LATENCY")
    counted = []

    async def body(ctx, dut, model):
        psram = dut.psram
        if latency is not None:
            ctx.set(psram.latency_clocks, latency)
        ctx.set(psram.address, 0x100)
        ctx.set(psram.perform_write, 0)
        ctx.set(psram.final_word, 1)
        ctx.set(psram.start_transfer, 1)
        await ctx.tick()
        ctx.set(psram.start_transfer, 0)

        cycles, entered = 0, False
        for _ in range(completion_bound(dut.sync_mhz)):
            if ctx.get(psram.state) == held:
                cycles, entered = cycles + 1, True
            elif entered:
                break
            await beat16(ctx, dut, model)
        counted.append(cycles if entered else None)

    simulate16(body, max_latency_clocks=max_latency_clocks)
    return counted[0]


def section_latency_input(checks, emit):
    """5b. `latency_clocks` is a live input, and optional. See #331."""
    emit("\n5b. Runtime latency: the controller moves with the part, or nothing does\n")

    default = latency_cycles_held(latency=None)
    checks.check("an undriven `latency_clocks` still reaches the data body",
                 default is not None,
                 "the controller never entered HANDLE_LATENCY")
    checks.check("undriven matches the build-time constant exactly",
                 default == latency_cycles_held(latency=HyperRAMController.HIGH_LATENCY_CLOCKS),
                 f"undriven took {default} cycles, driving 14 took a different count -- "
                 "the input changed behaviour that was supposed to be unchanged")

    # THE CHECK THIS SECTION EXISTS FOR. Before #331 every one of these returned
    # the same number, which is why `bist latency` could only ever pass one code.
    taken = {n: latency_cycles_held(latency=n) for n in (6, 8, 10, 12, 14)}
    emit(f"     cycles held in HANDLE_LATENCY: {taken}\n")
    checks.check("each latency setting waits a different number of cycles",
                 len(set(taken.values())) == len(taken),
                 f"settings collapsed onto the same wait: {taken}")
    steps = sorted(taken)
    checks.check("the wait tracks the setting one for one",
                 all(taken[b] - taken[a] == b - a
                     for a, b in zip(steps, steps[1:])),
                 f"not one-for-one: {taken}")

    # A count below 2 underflows the counter unless it is clamped, and a counter
    # that wrapped waits ~2^n cycles -- a hang, not a short read.
    checks.check("a count below 2 is clamped rather than wrapped",
                 latency_cycles_held(latency=0) is not None
                 and latency_cycles_held(latency=0) <= taken[6],
                 "latency 0 did not complete, so the counter wrapped")

    # `fixed_latency` was ORed into the branch condition as a Python int, so the
    # VARIABLE path was dead whenever the constructor said True -- the sweep moved
    # the part and the controller could not follow, and every `var` cell of 4096
    # failed. It must now be a signal the caller can drive. (#338)
    held = HyperRAMController.STATES.index("HANDLE_LATENCY")

    def held_with(fixed):
        counted = []

        async def body(ctx, dut, model):
            psram = dut.psram
            ctx.set(psram.fixed_latency, 1 if fixed else 0)
            ctx.set(psram.address, 0x100)
            ctx.set(psram.perform_write, 0)
            ctx.set(psram.final_word, 1)
            ctx.set(psram.start_transfer, 1)
            await ctx.tick()
            ctx.set(psram.start_transfer, 0)
            cycles, entered = 0, False
            for _ in range(completion_bound(dut.sync_mhz)):
                if ctx.get(psram.state) == held:
                    cycles, entered = cycles + 1, True
                elif entered:
                    break
                await beat16(ctx, dut, model)
            counted.append(cycles if entered else None)

        simulate16(body, max_latency_clocks=14)
        return counted[0]

    # The model holds RWDS low through the CA, so `var` must take the SHORT count.
    # Identical numbers here mean the branch is still welded.
    fixed_held, var_held = held_with(True), held_with(False)
    emit(f"     HANDLE_LATENCY cycles: fixed {fixed_held}, variable {var_held}\n")
    checks.check("`fixed_latency` is an input, not a compile-time constant",
                 hasattr(HyperRAMController(phy=HyperBusPHY(), sync_mhz=60.0),
                         "fixed_latency"),
                 "no `fixed_latency` signal on the controller")
    checks.check("driving `fixed_latency` low reaches the variable branch",
                 fixed_held != var_held,
                 f"both took {fixed_held} cycles -- the variable path is still "
                 "dead, so a sweep of CR0[3] moves the part and not the controller")


def section_structural(checks, emit):
    """5. The reasons upstream's PHY cannot be instantiated here."""
    emit("\n5. Structural: our PHY against upstream's, in source\n")

    ours = (ROOT / "gateware" / "soc" / "peripherals" / "hyperram_dqs_phy.py").read_text()
    upstream = _upstream_source()

    checks.check("upstream assigns bus.clk as a single net",
                 "o_Z=self.bus.clk," in upstream,
                 "upstream changed; re-check whether this PHY is still needed")
    checks.check("ours drives the differential clock's TRUE pin only",
                 "self.bus.clk.p[0]" in ours and "self.bus.clk.n" not in ours)
    checks.check("ours drives RESET#, which upstream leaves floating",
                 "self.bus.reset.io[0]" in ours
                 and "self.bus.reset" not in upstream)
    checks.check("ours takes the polarity from the resource, not from a literal",
                 "port.invert[index]" in ours)
    checks.check("ours needs the `fast` domain, and says so",
                 'ClockSignal("fast")' in ours)
    checks.check("ours keeps upstream's controller rather than copying it",
                 "from luna.gateware.interface.psram import HyperBusDQSPHY" in ours
                 and "class HyperRAMDQSInterface" not in ours)


def section_wishbone(checks, emit):
    """6. The 32-bit memory port, including the delayed-grant trap."""
    emit("\n6. Wishbone memory window, against a delayed shared controller\n")

    # `sustained`, because this section drives a master that replaces a beat on
    # the acknowledging edge. Section 9 covers the SoC's real master, which
    # cannot, and the window it gets as a result.
    dut = HyperRAMWishbone(sustained=True)
    observed = {}

    async def pulse_word(ctx, value):
        ctx.set(dut.granted, 1)
        ctx.set(dut.in_data, value)
        ctx.set(dut.in_valid, 1)
        ack = ctx.get(dut.bus.ack)
        data = ctx.get(dut.bus.dat_r)
        await ctx.tick()
        ctx.set(dut.in_valid, 0)
        return ack, data

    async def begin(ctx, *, adr, write=False, data=0, select=0b1111,
                    cti=wishbone.CycleType.CLASSIC,
                    bte=wishbone.BurstTypeExt.LINEAR):
        ctx.set(dut.bus.cyc, 1)
        ctx.set(dut.bus.stb, 1)
        ctx.set(dut.bus.adr, adr)
        ctx.set(dut.bus.we, write)
        ctx.set(dut.bus.dat_w, data)
        ctx.set(dut.bus.sel, select)
        ctx.set(dut.bus.cti, cti)
        ctx.set(dut.bus.bte, bte)
        await ctx.tick()

    async def end(ctx):
        ctx.set(dut.bus.cyc, 0)
        ctx.set(dut.bus.stb, 0)
        ctx.set(dut.granted, 0)
        await ctx.tick()

    async def testbench(ctx):
        # The wrong arrangement: a one-cycle request and a grant three cycles later.
        # There is no overlap, so no controller can accept it.
        old_request = [1, 0, 0, 0]
        delayed_grant = [0, 0, 0, 1]
        observed["old_completions"] = sum(
            req & grant for req, grant in zip(old_request, delayed_grant))

        await begin(ctx, adr=0x12345)
        held = []
        for _ in range(3):
            held.append(ctx.get(dut.req))
            await ctx.tick()
        observed["held"] = held
        observed["read_addr"] = ctx.get(dut.req_addr)
        observed["read_write"] = ctx.get(dut.req_write)
        await pulse_word(ctx, 0x2211)
        observed["read_ack"], observed["read_data"] = \
            await pulse_word(ctx, 0x4433)
        await end(ctx)

        await begin(ctx, adr=7, write=True, data=0xa1b2c3d4)
        observed["full_write"] = (ctx.get(dut.req_write),
                                   ctx.get(dut.req_data))
        await pulse_word(ctx, 0)
        observed["full_ack"], _ = await pulse_word(ctx, 0)
        await end(ctx)

        # Byte lanes 0 and 2 change. The inactive lanes must come from the read,
        # because this controller has no RWDS mask input.
        await begin(ctx, adr=9, write=True, data=0xaabbccdd, select=0b0101)
        observed["partial_starts_read"] = ctx.get(dut.req_write)
        await pulse_word(ctx, 0x6655)
        await pulse_word(ctx, 0x8877)
        ctx.set(dut.granted, 0)
        await ctx.tick()
        observed["partial_then_writes"] = ctx.get(dut.req_write)
        observed["partial_merged"] = ctx.get(dut.req_data)
        observed["partial_early_ack"] = ctx.get(dut.bus.ack)
        await pulse_word(ctx, 0)
        observed["partial_ack"], _ = await pulse_word(ctx, 0)
        await end(ctx)

        cap_final = []
        for beat_index in range(dut.max_burst_beats):
            await begin(ctx, adr=0x200 + beat_index,
                        cti=wishbone.CycleType.INCR_BURST)
            await pulse_word(ctx, beat_index)
            cap_final.append(ctx.get(dut.req_final))
            await pulse_word(ctx, beat_index)
        observed["cap_final"] = cap_final
        await end(ctx)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6, domain="sync")
    sim.add_testbench(testbench)
    sim.run()

    checks.check("pulsing a request before a delayed grant completes NOTHING",
                 observed["old_completions"] == 0,
                 "the wrong arrangement unexpectedly overlapped the grant")
    checks.check("the real port holds its request until the delayed grant",
                 observed["held"] == [1, 1, 1], str(observed["held"]))
    checks.check("Wishbone word addresses become 16-bit HyperRAM addresses",
                 observed["read_addr"] == 0x12345 * 2,
                 hex(observed["read_addr"]))
    checks.check("a Wishbone read reaches the controller as a read",
                 observed["read_write"] == 0)
    checks.check("two 16-bit words return one little-endian 32-bit word",
                 observed["read_ack"] and observed["read_data"] == 0x44332211,
                 hex(observed["read_data"]))
    checks.check("a full store stays a write and holds all 32 data bits",
                 observed["full_write"] == (1, 0xa1b2c3d4),
                 str(observed["full_write"]))
    checks.check("a full store acknowledges after its second word",
                 observed["full_ack"] == 1)
    checks.check("a partial store starts with a read",
                 observed["partial_starts_read"] == 0)
    checks.check("a partial store changes to a write after the merge",
                 observed["partial_then_writes"] == 1)
    checks.check("inactive byte lanes survive the partial-store merge",
                 observed["partial_merged"] == 0x88bb66dd,
                 hex(observed["partial_merged"]))
    checks.check("the read half of a partial store does not acknowledge early",
                 observed["partial_early_ack"] == 0)
    checks.check("the write half of a partial store completes the request",
                 observed["partial_ack"] == 1)
    checks.check("a missing EOB is forced closed at the tCSM-safe cap",
                 observed["cap_final"] ==
                 [0] * (dut.max_burst_beats - 1) + [1],
                 f"final beats {sum(observed['cap_final'])}")


class ControlledInterface:
    """The HyperRAMController signal surface, driven by section 7."""

    def __init__(self):
        self.address = Signal(32)
        self.register_space = Signal()
        self.perform_write = Signal()
        self.single_page = Signal()
        self.start_transfer = Signal()
        self.final_word = Signal()
        self.idle = Signal()
        self.read_ready = Signal()
        self.write_ready = Signal()
        self.read_data = Signal(16)
        self.write_data = Signal(16)


def section_shared_engine(checks, emit):
    """7. The shared engine holds the three controller trap signals."""
    emit("\n7. Shared engine, controls sampled across complete transfers\n")

    interface = ControlledInterface()
    dut = BootRAM(interface=interface)
    observed = {}

    async def wait_for(ctx, signal, value=1):
        for _ in range(20):
            if ctx.get(signal) == value:
                return True
            await ctx.tick()
        return False

    async def begin(ctx, *, write=False, data=0):
        ctx.set(dut.mmap.bus.cyc, 1)
        ctx.set(dut.mmap.bus.stb, 1)
        ctx.set(dut.mmap.bus.adr, 5)
        ctx.set(dut.mmap.bus.we, write)
        ctx.set(dut.mmap.bus.dat_w, data)
        ctx.set(dut.mmap.bus.sel, 0b1111)
        return await wait_for(ctx, interface.start_transfer)

    async def end(ctx):
        ctx.set(dut.mmap.bus.cyc, 0)
        ctx.set(dut.mmap.bus.stb, 0)
        ctx.set(interface.idle, 1)
        await ctx.tick()
        await ctx.tick()

    async def testbench(ctx):
        ctx.set(interface.idle, 1)
        await ctx.tick()

        observed["read_started"] = await begin(ctx)
        observed["read_start"] = (
            ctx.get(interface.address), ctx.get(interface.perform_write),
            ctx.get(interface.final_word))
        ctx.set(interface.idle, 0)
        await ctx.tick()
        ctx.set(interface.read_data, 0x3412)
        ctx.set(interface.read_ready, 1)
        observed["read_first_final"] = ctx.get(interface.final_word)
        await ctx.tick()
        ctx.set(interface.read_data, 0x7856)
        observed["read_second_final"] = ctx.get(interface.final_word)
        observed["read_ack"] = ctx.get(dut.mmap.bus.ack)
        observed["read_data"] = ctx.get(dut.mmap.bus.dat_r)
        await ctx.tick()
        ctx.set(interface.read_ready, 0)
        observed["read_recovery_final"] = ctx.get(interface.final_word)
        await end(ctx)

        observed["write_started"] = await begin(ctx, write=True,
                                                 data=0xa1b2c3d4)
        observed["write_start"] = (
            ctx.get(interface.perform_write), ctx.get(interface.write_data),
            ctx.get(interface.final_word))
        ctx.set(interface.idle, 0)
        await ctx.tick()
        ctx.set(interface.write_ready, 1)
        observed["write_first"] = (
            ctx.get(interface.perform_write), ctx.get(interface.write_data),
            ctx.get(interface.final_word))
        await ctx.tick()
        observed["write_second"] = (
            ctx.get(interface.perform_write), ctx.get(interface.write_data),
            ctx.get(interface.final_word))
        observed["write_ack"] = ctx.get(dut.mmap.bus.ack)
        await ctx.tick()
        ctx.set(interface.write_ready, 0)
        observed["write_recovery"] = (
            ctx.get(interface.perform_write), ctx.get(interface.write_data),
            ctx.get(interface.final_word))
        await end(ctx)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6, domain="sync")
    sim.add_testbench(testbench)
    sim.run()

    checks.check("the shared engine starts a Wishbone read",
                 observed["read_started"])
    checks.check("a read starts at the doubled address with final_word low",
                 observed["read_start"] == (10, 0, 0),
                 str(observed["read_start"]))
    checks.check("final_word is low for the first read word",
                 observed["read_first_final"] == 0)
    checks.check("final_word rises for the second read word",
                 observed["read_second_final"] == 1)
    checks.check("final_word stays high through read recovery",
                 observed["read_recovery_final"] == 1)
    checks.check("the shared engine returns both read words",
                 observed["read_ack"] and observed["read_data"] == 0x78563412,
                 hex(observed["read_data"]))
    checks.check("the shared engine starts a Wishbone write",
                 observed["write_started"])
    checks.check("write controls begin held on the low half",
                 observed["write_start"] == (1, 0xc3d4, 0),
                 str(observed["write_start"]))
    checks.check("write controls remain held for the first word",
                 observed["write_first"] == (1, 0xc3d4, 0),
                 str(observed["write_first"]))
    checks.check("the second word carries the upper half and final_word",
                 observed["write_second"] == (1, 0xa1b2, 1),
                 str(observed["write_second"]))
    checks.check("write controls stay held through recovery",
                 observed["write_recovery"] == (1, 0xa1b2, 1),
                 str(observed["write_recovery"]))
    checks.check("the write acknowledges after both words",
                 observed["write_ack"] == 1)


class NonDQSHarness(Elaboratable):
    """The memory window connected to the protocol engine used by the SoC.

    `pipe` inserts the SoC's `RegisteredResponse` between the master and the
    window, which is what the real design has and what section 9 needs: without
    it the harness drives a master that supplies a beat every two cycles, and no
    master in this SoC does. `sustained` is the window's coalescing flag.

    `clock_stop` splits the PHY record in two with `ClockStopPHY`, so the
    controller and the model see different `clk_en`. `self.phy` stays the DEVICE
    side either way -- it is what every testbench here steps the model off, and
    what a real part would see.

    `ck_align` false is the wrong arrangement for that split: it gates CK with
    the word-boundary stall directly instead of with `BootRAM`'s write-aligned
    copy of it. That is the design as first written down, and section 11 runs it
    to show the register is load-bearing rather than decorative.
    """

    def __init__(self, *, pipe=False, sustained=True, clock_stop=False,
                 ck_align=True):
        self.phy = HyperBusPHY()
        self._ck_align = ck_align
        self.gate = ClockStopPHY(dev=self.phy) if clock_stop else None
        # No PHY pipeline in this harness either; see `NonDQSProtocolHarness`.
        self.interface = HyperRAMController(
            phy=self.gate.ctrl if clock_stop else self.phy,
            sync_mhz=NON_DQS_SYNC_MHZ, phy_round_trip_cycles=0)
        self.bootram = BootRAM(interface=self.interface, sustained=sustained,
                               clock_stop=clock_stop)
        self._pipe = pipe
        self.pipe = RegisteredResponse(
            addr_width=len(self.bootram.mmap.bus.adr), data_width=32,
            granularity=8, features={"cti", "bte", "err"}) if pipe else None

    @property
    def bus(self):
        """What a master drives: the pipe's input, or the window directly."""
        return self.pipe.intr_bus if self._pipe else self.bootram.mmap.bus

    def elaborate(self, platform):
        m = Module()
        m.submodules.interface = self.interface
        m.submodules.bootram = self.bootram
        if self.gate is not None:
            m.submodules.gate = self.gate
            m.d.comb += self.gate.stall.eq(self.bootram.clk_stop if self._ck_align
                                           else self.bootram.probe_stall)
        if self._pipe:
            m.submodules.pipe = self.pipe
            wiring.connect(m, self.pipe.sub_bus, self.bootram.mmap.bus)
        return m


class ModelHyperRAM16:
    """A 16-bit fixed-latency read model, observed only through HyperBus."""

    def __init__(self, *, sync_mhz=NON_DQS_SYNC_MHZ, tcshi_ns=T_CSHI_NS,
                 deliver=None, rwds_stale=0, rwds_extra=0):
        self.commands = []
        self.ca_words = []
        self.transaction_cycles = []
        # Read beats this device serves before going silent. See `ModelHyperRAM`.
        self.deliver = deliver
        self.read_beats = 0
        # RWDS during the CA is the device asking for extra latency, and it is
        # valid tDSV AFTER CS# falls -- so on the falling cycle itself the line
        # still holds `rwds_stale`, whatever the last transaction left. That gap
        # is the whole of #321.
        self.rwds_stale = rwds_stale
        self.rwds_extra = rwds_extra
        # Transactions that never brought `idle` back inside `completion_bound`,
        # and what `timed_out` read at the end of the last one.
        self.incomplete = 0
        self.durations = []
        self.timed_out = None
        self.burst_beats = None
        self.beats_taken = 0
        # Whole cycles of CS# high tCSHI needs at this clock, rounded up -- the
        # same arithmetic the controller does, done independently of it. Two at
        # 192 MHz.
        self._cshi_required = max(1, math.ceil(tcshi_ns * sync_mhz / 1000.0))
        # How many cycles CS# has been high. Starts far past the requirement so
        # the first transaction of a run is not judged against a gap that never
        # existed.
        self._cs_high_cycles = 10**6
        self.cshi_violations = 0
        # tCSM, counted where it is spent rather than at the end of a transaction,
        # so a device left selected for ever is counted once rather than never.
        self._tcsm_cycles = int(T_CSM_NS * sync_mhz / 1000.0)
        self._cs_low_cycles = 0
        self.cs_low_max = 0
        self.tcsm_violations = 0
        self._state = "idle"
        self._ca = []
        self._latency = 0
        self._address = 0
        self._active_cycles = 0
        self._previous_cs = 0
        # What the controller actually stored, by 16-bit word address. The model
        # only ever served reads before, which is why no burst-WRITE case could
        # be written against it -- and why the one below found a fault that had
        # been shipping.
        self.memory = {}
        self._is_write = False

    def step(self, *, cs, clk_en, dq_o, dq_e):
        dq_i, rwds_i = 0, 0

        if cs:
            self._cs_low_cycles += 1
            self.cs_low_max = max(self.cs_low_max, self._cs_low_cycles)
            if self._cs_low_cycles == self._tcsm_cycles + 1:
                self.tcsm_violations += 1
        else:
            self._cs_low_cycles = 0

        if cs and not self._previous_cs:
            # CS# just fell. Judged here, before anything about the command is
            # known, and against the model's own cycle count rather than against
            # anything the controller reports.
            if self._cs_high_cycles < self._cshi_required:
                self.cshi_violations += 1
            self._cs_high_cycles = 0
            self._state = "command"
            self._ca = []
            self._active_cycles = 0
        if cs and clk_en:
            self._active_cycles += 1
        if not cs and self._previous_cs:
            self.transaction_cycles.append(self._active_cycles)
            self._state = "idle"
        self._previous_cs = cs

        if not cs:
            self._cs_high_cycles += 1
            return dq_i, rwds_i

        if self._state == "command" and clk_en and dq_e:
            self._ca.extend(((dq_o >> 8) & 0xff, dq_o & 0xff))
            if len(self._ca) >= 6:
                ca = 0
                for byte in self._ca[:6]:
                    ca = (ca << 8) | byte
                self._address = ((((ca >> 16) & ((1 << 29) - 1)) << 3)
                                 | (ca & 0b111))
                self.commands.append(self._address)
                self.ca_words.append(ca)
                # CA bit 47 is 1 for a read.
                self._is_write = not ((ca >> 47) & 1)
                # The device's data phase begins one CK after the protocol FSM
                # leaves HANDLE_LATENCY, which is where the controller's own
                # registered `dq.o` first carries write data. Count from the same
                # value the controller loads, so the two agree by construction.
                #
                # THIS IS THE NUMBER THE BOARD PINS DOWN. A device whose data
                # phase started anywhere else would shift the whole burst and
                # every beat would be wrong; the board's even beats are correct,
                # so the alignment is exact. Counting `HIGH_LATENCY_CLOCKS` here
                # instead put the model two cycles late -- reads survived it,
                # because RWDS gates the controller's sampling and it simply
                # waited, but writes are not strobed and the model missed them.
                self._latency = HyperRAMController.HIGH_LATENCY_CLOCKS - 2
                self._state = "latency"
        elif self._state == "latency":
            # Counted in CK, not in `sync` cycles, which only differ once
            # something gates the clock. Gating here is what makes a stall inside
            # the latency window VISIBLE: the controller's `HANDLE_LATENCY`
            # counts down every cycle regardless, so a paused device would enter
            # its data phase late and every word of the burst would be wrong.
            if clk_en:
                if self._latency:
                    self._latency -= 1
                else:
                    self._state = "data"
        elif self._state == "data":
            # A write word lands on a clocked edge, so the capture is gated on
            # `clk_en`. Sampling every simulation step instead records the value
            # the controller is HOLDING several times over and walks the address
            # past the data.
            #
            # VALIDATED against a case whose answer was already known: one 32-bit
            # Wishbone write stores exactly two words, at `adr*2` and `adr*2+1`,
            # low half first. That is what `section_wishbone` and the board both
            # say happens, and it is what settled the latency count above -- the
            # capture recorded NOTHING at all until that was right.
            if self._is_write:
                if clk_en and dq_e:
                    self.memory[self._address] = dq_o & 0xffff
                    self._address += 1
            elif clk_en:
                if self.deliver is not None and self.read_beats >= self.deliver:
                    # The device has stopped answering: RWDS holds a level instead
                    # of transitioning, so nothing strobes and the address does not
                    # advance. That is what a part in Deep Power Down looks like
                    # from here, and what `READ_DATA` had no exit from (#316).
                    return dq_i, rwds_i
                # Serve back whatever was stored, so a write and a read of the
                # same line can be compared. An address never written keeps the
                # old synthetic pattern, which is what section 8 reads.
                dq_i = self.memory.get(self._address,
                                       (0x4000 + self._address) & 0xffff)
                rwds_i = 0b10
                self.read_beats += 1
                self._address += 1
            # CK stopped, so no read strobe and no address advance. RWDS holds a
            # level instead of transitioning, which is the only thing `READ_DATA`
            # looks at -- 10.1 gives RWDS as `X` in the Active Clock Stop row, so
            # the level itself means nothing and this returns a static 0b00.
            # Driving 0b10 here regardless of `clk_en` was a model defect on its
            # own account: it asserted a read strobe on a clock edge that never
            # happened, and it only went unnoticed because nothing gated CK.

        if self._state == "command":
            # The CA window: the answer once tDSV has passed, the leftover level
            # on the cycle CS# falls. See `rwds_stale` above.
            rwds_i = (self.rwds_extra if self._cs_low_cycles > 1
                      else self.rwds_stale)

        return dq_i, rwds_i


def simulate_line_refill(*, incrementing):
    """Read one 64-byte line and return device-observed transactions/cycles."""
    dut = NonDQSHarness()
    model = ModelHyperRAM16()
    observed = {"data": [], "cycles": 0}

    async def testbench(ctx):
        beat_index = 0
        request_active = False

        for _ in range(CYCLE_LIMIT):
            if not request_active and beat_index < 16:
                ctx.set(dut.bootram.mmap.bus.cyc, 1)
                ctx.set(dut.bootram.mmap.bus.stb, 1)
                ctx.set(dut.bootram.mmap.bus.adr, 0x100 + beat_index)
                if incrementing:
                    cti = (wishbone.CycleType.END_OF_BURST if beat_index == 15
                           else wishbone.CycleType.INCR_BURST)
                else:
                    cti = wishbone.CycleType.CLASSIC
                ctx.set(dut.bootram.mmap.bus.cti, cti)
                ctx.set(dut.bootram.mmap.bus.bte,
                        wishbone.BurstTypeExt.LINEAR)
                ctx.set(dut.bootram.mmap.bus.sel, 0b1111)
                request_active = True

            dq_i, rwds_i = model.step(
                cs=ctx.get(dut.phy.cs),
                clk_en=ctx.get(dut.phy.clk_en),
                dq_o=ctx.get(dut.phy.dq.o),
                dq_e=ctx.get(dut.phy.dq.e),
            )
            ctx.set(dut.phy.dq.i, dq_i)
            ctx.set(dut.phy.rwds.i, rwds_i)

            acknowledged = ctx.get(dut.bootram.mmap.bus.ack)
            if acknowledged:
                observed["data"].append(ctx.get(dut.bootram.mmap.bus.dat_r))

            await ctx.tick()
            observed["cycles"] += 1

            if acknowledged:
                beat_index += 1
                request_active = False
                if incrementing and beat_index < 16:
                    # A pipelined burst keeps CYC/STB asserted and replaces the
                    # acknowledged beat on the same Wishbone clock edge.
                    ctx.set(dut.bootram.mmap.bus.adr, 0x100 + beat_index)
                    ctx.set(dut.bootram.mmap.bus.cti,
                            wishbone.CycleType.END_OF_BURST if beat_index == 15
                            else wishbone.CycleType.INCR_BURST)
                    request_active = True
                else:
                    ctx.set(dut.bootram.mmap.bus.cyc, 0)
                    ctx.set(dut.bootram.mmap.bus.stb, 0)

            if (beat_index == 16 and not ctx.get(dut.phy.cs)
                    and len(model.transaction_cycles) == len(model.commands)):
                break

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (NON_DQS_SYNC_MHZ * 1e6), domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    return model, observed



def line_value(index):
    """Beat `index` of the test line. The same values `bench::hyper_line_write_check`
    writes, so a sim result and a board result are comparable literally."""
    return 0x1000_0000 + index * 0x0101_0101 + 0x0f0e_0d0c


async def drive_line(ctx, dut, model, *, write, base=0x100, beats=16, out=None,
                     stalls=None):
    """One cache line as a registered-feedback burst, driven the way a CPU does.

    Classic Wishbone: CYC and STB are held, the next beat replaces the
    acknowledged one on the following edge, and the master cannot run ahead.
    """
    bus = dut.bus
    index = 0
    ctx.set(bus.cyc, 1)
    ctx.set(bus.stb, 1)
    ctx.set(bus.we, write)
    ctx.set(bus.sel, 0b1111)
    ctx.set(bus.bte, wishbone.BurstTypeExt.LINEAR)
    ctx.set(bus.adr, base)
    ctx.set(bus.dat_w, line_value(0) if write else 0)
    ctx.set(bus.cti, wishbone.CycleType.INCR_BURST if beats > 1
            else wishbone.CycleType.END_OF_BURST)

    for _ in range(CYCLE_LIMIT):
        # WHERE the CK stopped, as the device understands it: a cycle the
        # controller asked for a clock and the gate withheld one. Recording the
        # model's own state makes "never pause inside the latency window" a
        # measurement rather than an argument about `mmap.req`.
        if (stalls is not None and ctx.get(dut.phy.cs)
                and ctx.get(dut.gate.ctrl.clk_en)
                and not ctx.get(dut.phy.clk_en)):
            stalls.append(model._state)

        dq_i, rwds_i = model.step(
            cs=ctx.get(dut.phy.cs), clk_en=ctx.get(dut.phy.clk_en),
            dq_o=ctx.get(dut.phy.dq.o), dq_e=ctx.get(dut.phy.dq.e))
        ctx.set(dut.phy.dq.i, dq_i)
        ctx.set(dut.phy.rwds.i, rwds_i)

        acknowledged = ctx.get(bus.ack)
        captured = ctx.get(bus.dat_r)
        await ctx.tick()

        if acknowledged:
            if out is not None:
                out.append(captured)
            index += 1
            if index < beats:
                ctx.set(bus.adr, base + index)
                ctx.set(bus.dat_w, line_value(index))
                ctx.set(bus.cti, wishbone.CycleType.END_OF_BURST
                        if index == beats - 1 else wishbone.CycleType.INCR_BURST)
            else:
                ctx.set(bus.cyc, 0)
                ctx.set(bus.stb, 0)
                ctx.set(bus.we, 0)

        # Wait for the model to have SEEN CS# rise, not just for it to have
        # risen. The model is stepped before the tick, so returning on `cs` alone
        # leaves the last transaction's CK count unrecorded -- which is the same
        # condition `simulate_line_refill` already waits on.
        if (index == beats and not ctx.get(dut.phy.cs)
                and len(model.transaction_cycles) == len(model.commands)):
            return


def simulate_single_write(*, adr=0x100):
    """One 32-bit write, whose answer is already known from the board."""
    dut = NonDQSHarness(pipe=True, sustained=False)
    model = ModelHyperRAM16()

    async def testbench(ctx):
        await drive_line(ctx, dut, model, write=True, base=adr, beats=1)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (NON_DQS_SYNC_MHZ * 1e6), domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    return model


def simulate_cross(*, sustained, beats=16, base=0x100, clock_stop=False,
                   ck_align=True):
    """`hr cross`: write a line through the window, then read it back."""
    dut = NonDQSHarness(pipe=True, sustained=sustained, clock_stop=clock_stop,
                        ck_align=ck_align)
    model = ModelHyperRAM16()
    read_back = []
    stalls = [] if clock_stop else None

    async def testbench(ctx):
        await drive_line(ctx, dut, model, write=True, base=base, beats=beats,
                         stalls=stalls)
        for _ in range(8):        # the D-cache evict between the two halves
            await ctx.tick()
        await drive_line(ctx, dut, model, write=False, base=base, beats=beats,
                         out=read_back, stalls=stalls)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (NON_DQS_SYNC_MHZ * 1e6), domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    model.stalls = stalls
    return model, read_back


def cross_result(read_back, beats=16):
    """`(correct, bitmap)` in the shell's own format: bit per beat, LSB word 0."""
    correct, bitmap = 0, ""
    for index in range(beats):
        ok = index < len(read_back) and read_back[index] == line_value(index)
        correct += ok
        bitmap = ("0" if ok else "1") + bitmap
    return correct, bitmap


def section_line_write(checks, emit):
    """9. A 64-byte line WRITTEN as one burst, through the SoC's real bus path.

    The board reports `8/16 correct, bad 1010101010101010`: every odd-indexed
    beat stores its two 16-bit halves transposed. Reproducing it needed one
    thing the earlier harness left out -- `RegisteredResponse`, which withholds
    STB for a cycle after every acknowledgement. Driving the window directly
    models a master that supplies a beat every two cycles, and this SoC has
    none; with the real pipe in the path the sim prints the board's line
    character for character.
    """
    emit("\n9. 64-byte line written through the window, then read back\n")

    # STEP ONE, before any burst result is believed: a single 32-bit write,
    # whose answer is known independently. Two words, at `adr*2` and `adr*2+1`,
    # low half first. A capture that cannot get this right cannot be used to
    # judge the gateware, and this one could not until its latency was fixed.
    single = simulate_single_write(adr=0x100)
    checks.check("a single 32-bit write stores exactly two device words",
                 len(single.memory) == 2, f"{len(single.memory)} words")
    checks.check("...at the doubled address, low half first",
                 single.memory.get(0x200) == 0x0d0c
                 and single.memory.get(0x201) == 0x1f0e,
                 str({hex(a): hex(v) for a, v in sorted(single.memory.items())}))

    # THE NEGATIVE CONTROL, and the reason this file can claim a reproduction:
    # the shipping arrangement, coalescing across beats against a master that
    # bubbles. Asserted to give the board's exact numbers.
    broken, broken_read = simulate_cross(sustained=True)
    broken_correct, broken_bitmap = cross_result(broken_read)
    checks.check("coalescing across a bubbling master reproduces the BOARD",
                 (broken_correct, broken_bitmap) == (8, "1010101010101010"),
                 f"{broken_correct}/16, bad {broken_bitmap}")
    checks.check("...with the board's first bad beat, halves transposed",
                 broken_read[1] == 0x0e0d_200f,
                 f"want {line_value(1):08x}, got {broken_read[1]:08x}")
    checks.check("...and 48 device words written for a 32-word line",
                 len(broken.memory) == 48, f"{len(broken.memory)} words")

    fixed, fixed_read = simulate_cross(sustained=False)
    correct, bitmap = cross_result(fixed_read)
    checks.check("the shipping window writes all sixteen beats correctly",
                 (correct, bitmap) == (16, "0000000000000000"),
                 f"{correct}/16, bad {bitmap}")
    checks.check("a 64-byte line touches 32 device words and not one more",
                 len(fixed.memory) == 32, f"{len(fixed.memory)} words")
    checks.check("every word lands at the address the beat asked for",
                 all(fixed.memory.get(0x200 + 2 * i) == line_value(i) & 0xffff
                     and fixed.memory.get(0x201 + 2 * i) == line_value(i) >> 16
                     for i in range(16)))
    checks.check("one HyperBus transaction per beat, since none may be held open",
                 len(fixed.commands) == 32, f"{len(fixed.commands)} transactions")

    emit(f"        coalescing, bubbling master: {broken_correct}/16, "
         f"bad {broken_bitmap}, {len(broken.memory)} device words")
    emit(f"        board:                       8/16, "
         f"bad 1010101010101010, want 200f0e0d got 0e0d200f")
    emit(f"        fixed:                       {correct}/16, "
         f"bad {bitmap}, {len(fixed.memory)} device words")


def read_line(*, sustained, clock_stop=False, base=0x300, beats=16):
    """One cache line READ back through the pipe, against pre-filled memory.

    Returns `(correct_beats, model)`. Pre-filling matters: `hr cross` writes and
    reads through the same path, so its two skews cancel and a total read fault
    presents as a half write fault.
    """
    dut = NonDQSHarness(pipe=True, sustained=sustained, clock_stop=clock_stop)
    model = ModelHyperRAM16()
    for word in range(beats * 2):
        model.memory[base * 2 + word] = 0xa000 + word
    out = []

    async def testbench(ctx):
        await drive_line(ctx, dut, model, write=False, base=base,
                         beats=beats, out=out)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (NON_DQS_SYNC_MHZ * 1e6), domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    want = [((0xa000 + 2 * i + 1) << 16) | (0xa000 + 2 * i)
            for i in range(beats)]
    return sum(a == b for a, b in zip(out, want)), model


def section_line_read_bubble(checks, emit):
    """10. The same fault on the READ side, which `hr cross` was hiding."""
    emit("\n10. Reads through the same path, which nothing had checked\n")

    broken, _ = read_line(sustained=True)
    fixed, _ = read_line(sustained=False)

    # `hr cross` writes and reads through the same path, so both are skewed the
    # same way and the two errors largely cancel -- which is why a read fault
    # this total showed up as a write fault half that size.
    checks.check("coalescing across a bubbling master also corrupts READS",
                 broken <= 1, f"{broken}/16 beats correct")
    checks.check("the shipping window returns all sixteen beats",
                 fixed == 16, f"{fixed}/16 beats correct")
    emit(f"        coalescing: {broken}/16 beats   fixed: {fixed}/16 beats")



def section_line_refill(checks, emit):
    """8. CTI coalescing, including the sixteen-transaction negative control."""
    emit("\n8. 64-byte Wishbone line refill through the HyperBus engine\n")

    burst_model, burst = simulate_line_refill(incrementing=True)
    classic_model, classic = simulate_line_refill(incrementing=False)

    checks.check("a 16-beat incrementing burst issues ONE HyperBus transaction",
                 len(burst_model.commands) == 1,
                 f"{len(burst_model.commands)} transactions")
    checks.check("the pre-change classic arrangement issues SIXTEEN transactions",
                 len(classic_model.commands) == 16,
                 f"{len(classic_model.commands)} transactions")
    checks.check("the coalesced refill returns all sixteen Wishbone beats",
                 len(burst["data"]) == 16, f"{len(burst['data'])} beats")
    checks.check("the classic negative control returns the same sixteen beats",
                 classic["data"] == burst["data"],
                 f"classic {len(classic['data'])}, burst {len(burst['data'])}")

    # 17 CK of overhead per transaction, and it is counted off the controller's
    # states rather than fitted: 3 command words + 13 `HANDLE_LATENCY` +
    # 1 `RECOVERY`, with `CS_SETUP` before CK starts. So a transaction is
    # 17 + words -- 49 for a 32-word line, 19 each for sixteen 2-word transfers.
    #
    # These read 51 and 336 until the model's data phase was corrected: it
    # entered two cycles late, reads tolerated it because RWDS gates the
    # controller's sampling, and the extra two showed up in every total. NOT the
    # same quantity as the 19 CK in `docs/soc-memory-bus.md`, which is a
    # board measurement of the DQS engine at 4:1 gearing.
    burst_cycles = sum(burst_model.transaction_cycles)
    classic_cycles = sum(classic_model.transaction_cycles)
    checks.check("one line occupies 49 CK with command and fixed latency",
                 burst_cycles == 49, f"measured {burst_cycles} CK")
    checks.check("sixteen classic transfers occupy 304 CK",
                 classic_cycles == 304, f"measured {classic_cycles} CK")
    # The cap guards tCSM, which is a TIME, so it has to be checked at the clock
    # the design runs -- not at the 192 MHz the old literal 748 was written for.
    # That number was 12.5 us at CK 60, over three times the limit it exists to
    # enforce, and unreachable only because a Wishbone burst never exceeds 32
    # words. The negative control below is the literal it replaced.
    for ck in (HYPERRAM_CK_MHZ, 192.0):
        for stop in (False, True):
            words = hyperram_max_burst_words(ck, clock_stop=stop)
            held_ns = (words * (1.5 if stop else 1.0) + 19) / ck * 1e3
            checks.check(f"the cap at CK {ck:g}"
                         f"{' with clock stop' if stop else ''} fits in tCSM",
                         held_ns < HYPERRAM_TCSM_NS,
                         f"{words} words holds CS# low {held_ns:.0f} ns")
    checks.check("the old fixed 748-word cap would NOT have fitted at this CK",
                 (748 + 19) / HYPERRAM_CK_MHZ * 1e3 > HYPERRAM_TCSM_NS,
                 "748 words fits, so deriving the cap from CK changed nothing")
    checks.check("a 64-byte line is well inside the cap",
                 hyperram_max_burst_words(HYPERRAM_CK_MHZ, clock_stop=True) > 32,
                 f"{hyperram_max_burst_words(HYPERRAM_CK_MHZ, clock_stop=True)} words")
    emit(f"        tCSM cap at CK {HYPERRAM_CK_MHZ:g}: "
         f"{HYPERRAM_MAX_BURST_WORDS} words, "
         f"{hyperram_max_burst_words(HYPERRAM_CK_MHZ, clock_stop=True)} with "
         f"clock stop; was a fixed 748")
    emit(f"        incrementing: {len(burst_model.commands)} transaction, "
         f"{burst_cycles} CK")
    emit(f"        classic:      {len(classic_model.commands)} transactions, "
         f"{classic_cycles} CK")


def section_clock_stop(checks, emit):
    """11. Active Clock Stop: coalescing under the master that broke it.

    Same harness, same model, same assertions as sections 9 and 10 -- the ONE
    thing changed is that the controller and the device see different `clk_en`.
    Section 9's arrangement stays exactly where it is as the negative control, so
    this section's numbers are read against the board's own failure line rather
    than against nothing.
    """
    emit("\n11. Active Clock Stop, with coalescing turned back on\n")

    gated, gated_read = simulate_cross(sustained=True, clock_stop=True)
    correct, bitmap = cross_result(gated_read)

    checks.check("a coalesced line write is correct once CK can stop",
                 (correct, bitmap) == (16, "0000000000000000"),
                 f"{correct}/16, bad {bitmap}")
    checks.check("...in 32 device words, not the ungated 48",
                 len(gated.memory) == 32, f"{len(gated.memory)} words")
    checks.check("...at the address each beat asked for",
                 all(gated.memory.get(0x200 + 2 * i) == line_value(i) & 0xffff
                     and gated.memory.get(0x201 + 2 * i) == line_value(i) >> 16
                     for i in range(16)))
    # `simulate_cross` runs a write line and then a read line, so two commands is
    # one HyperBus transaction per direction -- against 32 for `sustained=False`
    # and the whole point of the exercise.
    checks.check("the line is ONE transaction per direction, not sixteen",
                 len(gated.commands) == 2, f"{len(gated.commands)} transactions")

    # Overhead plus 32 words, and the stalled cycles are NOT counted -- the model
    # advances its CK count only when `clk_en` is high. So the pause costs the
    # device no clock at all; what it spends is CS#-low time, which is why the
    # burst cap had to stop being a word count.
    #
    # 48 against section 8's ungated 49, and the missing CK is the one `RECOVERY`
    # used to emit. Upstream leaves `clk_en` high for the first `RECOVERY` cycle.
    # A write needs it -- `dq.o` is registered and that cycle is where the last
    # word reaches the wire -- and a read does not, so the ungated engine clocks
    # one word out of the device and throws it away. The gate suppresses that,
    # because `mmap.req` has already dropped.
    checks.check("the gated line costs no extra CK, and a read saves one",
                 gated.transaction_cycles == [48, 48],
                 f"{gated.transaction_cycles} CK against 49 ungated")

    gated_reads, read_model = read_line(sustained=True, clock_stop=True)
    checks.check("a coalesced line read returns all sixteen beats",
                 gated_reads == 16, f"{gated_reads}/16 beats correct")
    checks.check("...in one transaction",
                 len(read_model.commands) == 1,
                 f"{len(read_model.commands)} transactions")

    # THE LATENCY-WINDOW CLAIM, MEASURED. The review argues `~mmap.req` cannot
    # pause inside the latency window by construction -- `req` is
    # `pending | (bursting & cyc & stb)`, `pending` is set at the start of a beat
    # and clears only on a `word_event`, and a `word_event` exists only in
    # WRITE_DATA/READ_DATA -- so `req` is high across SHIFT_COMMAND and
    # HANDLE_LATENCY whatever the master does. This checks it instead of
    # believing it: every withheld clock is recorded against the state the DEVICE
    # was in, and none may fall outside its data phase, where DQ is High-Z.
    checks.check("every withheld clock fell inside the device's data phase",
                 gated.stalls and set(gated.stalls) == {"data"},
                 f"stalled in {sorted(set(gated.stalls or ['nowhere']))}")
    # One per Wishbone beat and no more: a bubble the master takes is a clock the
    # device does not get, one for one. 31 rather than 32 across the two lines
    # because the write's last bubble arrives after `final_word` has already sent
    # the controller to RECOVERY, while the read's lands on the RECOVERY cycle
    # upstream still clocks -- which is the CK the read saves above.
    checks.check("one withheld clock per beat, 15 writing and 16 reading",
                 len(gated.stalls) == 31, f"{len(gated.stalls)} stalled cycles")

    # The gate must be able to make things WORSE as well as better, or it is not
    # doing anything. Coalescing without it is section 9's board reproduction.
    broken, broken_read = simulate_cross(sustained=True)
    broken_correct, _ = cross_result(broken_read)
    checks.check("the same run without the gate still reproduces the board",
                 (broken_correct, len(broken.memory)) == (8, 48),
                 f"{broken_correct}/16, {len(broken.memory)} words")

    # THE WRONG ARRANGEMENT FOR THE SPLIT ITSELF, which is where this experiment
    # differed from the design as written down. `clk_en_dev = clk_en & ~stall`,
    # with the SAME `stall` that gates `word_event`, is one register short: the
    # word the controller accepted in cycle T-1 is on the wire in T, so gating T
    # discards it. Every beat then loses its second half and the burst walks: the
    # 32 words land across 31 addresses and not one beat survives. Reads are
    # untouched by the difference -- their data and RWDS arrive in the same cycle
    # `read_ready` fires -- so this is a write-only fault, and it is WORSE than
    # not gating at all, which is what makes it worth asserting.
    naive, naive_read = simulate_cross(sustained=True, clock_stop=True,
                                       ck_align=False)
    naive_correct, naive_bitmap = cross_result(naive_read)
    checks.check("gating CK level with the word stall corrupts EVERY beat",
                 naive_correct == 0, f"{naive_correct}/16, bad {naive_bitmap}")
    checks.check("...and does not even touch 32 device words",
                 len(naive.memory) != 32, f"{len(naive.memory)} device words")
    emit(f"        level-gated (one register short): {naive_correct}/16, "
         f"{len(naive.memory)} device words")

    stall_cycles = hyperram_max_stall_cycles(HYPERRAM_CK_MHZ)
    checks.check("the stall bound is inside rev A01-006's 100 ns tCK maximum",
                 stall_cycles / HYPERRAM_CK_MHZ * 1e3 <= 100.0,
                 f"{stall_cycles} cycles is "
                 f"{stall_cycles / HYPERRAM_CK_MHZ * 1e3:.0f} ns")
    checks.check("...and the bubble it has to cover is one cycle, well inside it",
                 stall_cycles > 1, f"bound is {stall_cycles} cycles")

    emit(f"        gated, coalesced: {correct}/16, bad {bitmap}, "
         f"{len(gated.memory)} device words, "
         f"{len(gated.commands)} transactions, {gated.transaction_cycles} CK")
    emit(f"        ungated (sect 9): {broken_correct}/16, "
         f"{len(broken.memory)} device words")
    emit(f"        reads:            {gated_reads}/16 gated, "
         f"{len(gated.stalls)} cycles with CK withheld across both lines")
    emit(f"        stall bound {stall_cycles} cycles = "
         f"{stall_cycles / HYPERRAM_CK_MHZ * 1e3:.0f} ns at CK "
         f"{HYPERRAM_CK_MHZ:g}, against a 33 ns worst case")


def check_completed(checks, model, what):
    """Every transaction `run`/`run16` drove reached IDLE inside the bound."""
    checks.check(f"{what}: every transaction returned to IDLE",
                 model.incomplete == 0,
                 f"{model.incomplete} of {len(model.durations)} never finished")


def section_escape(checks, emit):
    """12. Every transaction ENDS -- the check this file did not have.

    `run`/`run16` looped to a limit, broke when `idle` returned, and when it never
    returned simply fell out of the loop and let the section carry on. So this
    file was green while the board sat in `READ_DATA` for ever, which is the one
    thing it was placed to notice. Fault 4 of the 2026-08-10 audit, #316.

    Three device behaviours, on both controllers, each run against upstream's
    controller as well: a device that never answers, one that answers 7 of 8
    beats, and a consumer that stops asking. All three are unbounded before the
    fix and all three must end after it.
    """
    emit("\n12. Every transaction ends, whatever the device does\n")

    bound16 = completion_bound(NON_DQS_SYNC_MHZ)
    bound32 = completion_bound(SYNC_MHZ)

    async def silent_read(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True)

    async def short_burst(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True, beats=8)

    async def stalled_write(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=False,
                    data=TEST_DATA, beats=0)

    async def register_write(ctx, dut, model):
        await run16(ctx, dut, model, address=0, read=False, data=0x8f2f,
                    register_space=True)

    async def register_read(ctx, dut, model):
        await run16(ctx, dut, model, address=0, read=True, register_space=True)

    # THE NEGATIVE CONTROLS FIRST. If upstream's controller finished these, the
    # checks below would pass on a harness that cannot tell a bounded controller
    # from an unbounded one, which is exactly how this file passed before.
    silent_control = simulate16(silent_read, upstream=True, deliver=0)
    checks.check("upstream's non-DQS READ_DATA never returns from a silent device",
                 silent_control.incomplete == 1,
                 f"upstream finished in {silent_control.durations} cycles, so this "
                 f"harness cannot tell a bounded controller from an unbounded one")
    burst_control = simulate16(short_burst, upstream=True, deliver=7)
    checks.check("...nor from a device that delivers 7 of the 8 beats asked for",
                 burst_control.incomplete == 1,
                 f"upstream finished in {burst_control.durations} cycles")
    write_control = simulate16(stalled_write, upstream=True)
    checks.check("...and upstream's WRITE_DATA never returns from a stalled consumer",
                 write_control.incomplete == 1,
                 f"upstream finished in {write_control.durations} cycles")

    silent = simulate16(silent_read, deliver=0)
    check_completed(checks, silent, "non-DQS, silent device")
    checks.check("...and `timed_out` says the watchdog ended it, not the caller",
                 silent.timed_out == 1, f"timed_out={silent.timed_out}")

    short = simulate16(short_burst, deliver=7)
    check_completed(checks, short, "non-DQS, 7 of 8 beats")
    checks.check("...having taken the 7 beats the device did deliver",
                 short.read_beats == 7, f"{short.read_beats} beats")
    checks.check("...and flagged the transaction",
                 short.timed_out == 1, f"timed_out={short.timed_out}")

    whole = simulate16(short_burst, deliver=8)
    check_completed(checks, whole, "non-DQS, 8 of 8 beats")
    checks.check("8 of 8 ends on `final_word`, with NOTHING flagged",
                 whole.timed_out == 0, f"timed_out={whole.timed_out}")
    checks.check("...and ends sooner than the watchdog would have",
                 whole.durations[0] < short.durations[0],
                 f"{whole.durations} against {short.durations} cycles")

    stalled = simulate16(stalled_write)
    check_completed(checks, stalled, "non-DQS, consumer that stops asking")
    checks.check("...a write nobody ends is flagged too",
                 stalled.timed_out == 1, f"timed_out={stalled.timed_out}")

    # REGISTER TRANSACTIONS, which the DQS path reaches by a different route --
    # `SHIFT_COMMAND1` straight into `WRITE_DATA`, no latency at all.
    reg_write = simulate16(register_write)
    check_completed(checks, reg_write, "non-DQS register write")
    checks.check("...and a register write ends on its own, unflagged",
                 reg_write.timed_out == 0, f"timed_out={reg_write.timed_out}")
    reg_read = simulate16(register_read, deliver=0)
    check_completed(checks, reg_read, "non-DQS register read, silent device")

    # The DQS controller, same three shapes.
    async def silent_read32(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True)

    async def stalled_write32(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=False,
                  data=TEST_DATA, beats=0)

    async def register_write32(ctx, dut, model):
        await run(ctx, dut, model, address=0, read=False, data=0x8f2f,
                  register_space=True)

    dqs_control = simulate(silent_read32, upstream=True, deliver=0)
    checks.check("upstream's DQS READ_DATA never returns from a silent device",
                 dqs_control.incomplete == 1,
                 f"upstream finished in {dqs_control.durations} cycles")
    dqs_write_control = simulate(stalled_write32, upstream=True)
    checks.check("...nor its WRITE_DATA from a stalled consumer",
                 dqs_write_control.incomplete == 1,
                 f"upstream finished in {dqs_write_control.durations} cycles")

    dqs_silent = simulate(silent_read32, deliver=0)
    check_completed(checks, dqs_silent, "DQS, silent device")
    checks.check("...and the DQS controller flags it",
                 dqs_silent.timed_out == 1, f"timed_out={dqs_silent.timed_out}")
    dqs_stalled = simulate(stalled_write32)
    check_completed(checks, dqs_stalled, "DQS, consumer that stops asking")
    dqs_register = simulate(register_write32)
    check_completed(checks, dqs_register, "DQS register write")
    checks.check("...unflagged, since the caller's own path ended it",
                 dqs_register.timed_out == 0,
                 f"timed_out={dqs_register.timed_out}")

    emit(f"        bound: {bound16} cycles at sync {NON_DQS_SYNC_MHZ:g} MHz, "
         f"{bound32} at {SYNC_MHZ:g} -- 1.25x the controller's own tCSM budget")
    emit(f"        silent device: upstream never returns, ours returns in "
         f"{silent.durations[0]} cycles with timed_out={silent.timed_out}")
    emit(f"        7 of 8 beats:  upstream never returns, ours returns in "
         f"{short.durations[0]}; 8 of 8 returns in {whole.durations[0]}")


def section_tcsm(checks, emit):
    """13. CS# Low never exceeds tCSM, measured rather than argued.

    The part allows 4 us and section 10 makes it the host's obligation; nothing in
    this file had ever counted it. Exceeding it drops a refresh, which is silent
    corruption somewhere else, so a violation cannot be found by reading data
    back. See #317.
    """
    emit("\n13. tCSM: how long CS# is held Low\n")

    async def endless_read(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True, beats=0)

    # THE NEGATIVE CONTROL: a device that keeps answering and a caller that never
    # ends the burst. Upstream holds CS# Low until the harness gives up.
    control = simulate16(endless_read, upstream=True)
    checks.check("upstream holds CS# Low past tCSM on a burst nobody ends",
                 control.tcsm_violations > 0,
                 f"CS# Low at most {control.cs_low_max} cycles, under the "
                 f"{control._tcsm_cycles}-cycle limit, so this detector is blind")

    chopped = simulate16(endless_read)
    checks.check("ours chops it, and CS# never passes tCSM",
                 chopped.tcsm_violations == 0,
                 f"{chopped.tcsm_violations} violations, CS# Low up to "
                 f"{chopped.cs_low_max} cycles")
    cap = chopped.burst_beats
    checks.check("...at the word cap exactly, not one beat past it",
                 chopped.beats_taken == cap,
                 f"took {chopped.beats_taken} beats against a cap of {cap}")
    checks.check("...and the caller is told the controller ended it",
                 chopped.timed_out == 1, f"timed_out={chopped.timed_out}")

    # And a silent device, where the watchdog rather than the word cap ends it.
    silent = simulate16(
        lambda ctx, dut, model: run16(ctx, dut, model, address=TEST_ADDRESS,
                                      read=True),
        deliver=0)
    checks.check("a silent device does not hold CS# past tCSM either",
                 silent.tcsm_violations == 0,
                 f"CS# Low up to {silent.cs_low_max} cycles")

    dqs = simulate(
        lambda ctx, dut, model: run(ctx, dut, model, address=TEST_ADDRESS,
                                    read=True, beats=0))
    checks.check("the DQS controller keeps tCSM on the same shape",
                 dqs.tcsm_violations == 0,
                 f"CS# Low up to {dqs.cs_low_max} beats")

    emit(f"        tCSM {T_CSM_NS:g} ns is {chopped._tcsm_cycles} cycles at "
         f"{NON_DQS_SYNC_MHZ:g} MHz; upstream reached {control.cs_low_max}, "
         f"ours {chopped.cs_low_max}")
    emit(f"        word cap {cap} beats, taken {chopped.beats_taken}; the device "
         f"clocked out {chopped.read_beats}, the extra one on the RECOVERY cycle")


def section_ca_rwds(checks, emit):
    """14. The extra-latency bit comes from the CA, not from before it.

    The device answers on RWDS *during* the CA, tDSV after CS# falls. Sampling on
    the falling cycle -- which `LATCH_RWDS` did -- reads the level the previous
    transaction left. Dormant while `fixed_latency=True` forces the long count,
    and live the moment #319 clears `CR0[3]`, so it is run here with the fixed
    branch off. The DQS controller takes the identical change; its model has no
    RWDS path to drive. See #321.
    """
    emit("\n14. The extra-latency sample, taken inside the CA\n")

    latency_state = HyperRAMController.STATES.index("HANDLE_LATENCY")

    def latency_cycles(*, stale, extra):
        """Cycles spent in HANDLE_LATENCY, which is the choice the sample made."""
        dut = NonDQSProtocolHarness(fixed_latency=False)
        model = ModelHyperRAM16(rwds_stale=stale, rwds_extra=extra)
        counted = 0

        async def testbench(ctx):
            nonlocal counted
            psram = dut.psram
            ctx.set(psram.address, TEST_ADDRESS)
            ctx.set(psram.final_word, 1)
            ctx.set(psram.start_transfer, 1)
            await ctx.tick()
            ctx.set(psram.start_transfer, 0)
            for _ in range(completion_bound(NON_DQS_SYNC_MHZ)):
                counted += ctx.get(psram.state) == latency_state
                await beat16(ctx, dut, model)
                if ctx.get(psram.idle):
                    break

        sim = Simulator(Fragment.get(dut, None))
        sim.add_clock(1 / (NON_DQS_SYNC_MHZ * 1e6), domain="sync")
        sim.add_testbench(testbench)
        sim.run()
        return counted

    long_count = HyperRAMController.HIGH_LATENCY_CLOCKS - 1
    short_count = HyperRAMController.LOW_LATENCY_CLOCKS - 1

    asked = latency_cycles(stale=0, extra=1)
    checks.check("RWDS raised mid-CA takes the LONG latency",
                 asked == long_count, f"{asked} cycles, want {long_count}")

    stale = latency_cycles(stale=1, extra=0)
    checks.check("a level left over from before the CA does NOT extend it",
                 stale == short_count,
                 f"{stale} cycles, want {short_count} -- the sample came from "
                 f"before the device answered")

    checks.check("...and the two choices are distinguishable at all",
                 long_count != short_count,
                 f"long {long_count}, short {short_count}")

    emit(f"        mid-CA high: {asked} cycles   stale high: {stale} cycles   "
         f"(long {long_count}, short {short_count})")


def section_register_ca(checks, emit):
    """15. CA[45] is 1 for register space whatever the caller asked for.

    9.1: *"CA[45] must be 1 as only linear single word register writes are
    supported."* It was `~single_page`, correct only because all four callers
    happen to pass 0 -- an invariant held by callers and documented in none of
    them. LiteX and Lattice both hardcode it. See #320.
    """
    emit("\n15. CA[45] for register space, against a caller that asks for wrapped\n")

    async def register_write(ctx, dut, model):
        await run16(ctx, dut, model, address=0, read=False, data=0x8f2f,
                    register_space=True, single_page=True)

    async def wrapped_memory(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True,
                    single_page=True)

    # THE NEGATIVE CONTROL: upstream builds CA[45] from `single_page` alone, so
    # the same request emits command byte 0x20 -- an unsupported register write,
    # silently. If it did not, this check could not tell the two apart.
    control = simulate16(register_write, upstream=True)
    control_ca = control.ca_words[0] if control.ca_words else 0
    checks.check("upstream emits CA[45]=0 for a single_page register write",
                 not (control_ca >> 45) & 1,
                 f"upstream command byte {control_ca >> 40:#04x}")

    ours = simulate16(register_write)
    ca = ours.ca_words[0] if ours.ca_words else 0
    checks.check("ours forces CA[45]=1 there",
                 bool((ca >> 45) & 1), f"command byte {ca >> 40:#04x}")
    checks.check("...which is the datasheet's 0x60 for a register write",
                 ca >> 40 == 0x60, f"{ca >> 40:#04x}, want 0x60")

    memory = simulate16(wrapped_memory)
    memory_ca = memory.ca_words[0] if memory.ca_words else 0
    checks.check("a WRAPPED memory burst is left alone -- CA[45]=0",
                 not (memory_ca >> 45) & 1,
                 f"command byte {memory_ca >> 40:#04x}, so the fix forced a bit "
                 f"it had no business touching")

    async def register_write32(ctx, dut, model):
        await run(ctx, dut, model, address=0, read=False, data=0x8f2f,
                  register_space=True, single_page=True)

    dqs = simulate(register_write32)
    checks.check("the DQS controller forces it too",
                 dqs.commands and dqs.commands[0]["linear"],
                 f"{dqs.commands[0] if dqs.commands else 'no command decoded'}")

    emit(f"        upstream {control_ca >> 40:#04x}, ours {ca >> 40:#04x}, "
         f"wrapped memory {memory_ca >> 40:#04x}")


def main():
    parser = argparse.ArgumentParser(
        description="The HyperBus protocol layer, against a model of the part.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every bus beat")
    args = parser.parse_args()

    emit("HyperRAM: the protocol layer, against a model of the W956A8")
    emit("timing of the DQS strobe is NOT checked here -- see the docstring")

    checks = Checks(emit)
    for section in (section_command, section_latency, section_held,
                    section_as_built, section_dqs_write_order,
                    section_recovery, section_recovery_non_dqs,
                    section_latency_input, section_structural, section_wishbone,
                    section_shared_engine, section_line_refill,
                    section_line_write, section_line_read_bubble,
                    section_clock_stop, section_escape, section_tcsm,
                    section_ca_rwds, section_register_ca):
        section(checks, emit)

    emit()
    if checks.failures:
        emit(f"  {len(checks.failures)} FAILED: {', '.join(checks.failures)}")
    else:
        emit("  all checks passed")
    emit()

    return 1 if checks.failures else 0


if __name__ == "__main__":
    sys.exit(main())
