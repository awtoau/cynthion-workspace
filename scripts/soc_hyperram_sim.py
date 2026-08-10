#!/usr/bin/env python3
#
# The HyperBus protocol layer, against a model of the part. See awtoau/cynthion-workspace#92, #90.
# SPDX-License-Identifier: BSD-3-Clause

"""The CONTROLLER and the SoC above it, against a deliberately unfaithful device.

    python3 scripts/soc_hyperram_sim.py
    python3 scripts/soc_hyperram_sim.py -v          # every bus beat

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## THE SPLIT: nothing here may assert a fact about the PART

`ModelHyperRAM` and `ModelHyperRAM16` were written from the same datasheet
reading as the controller they check, so on any protocol question the two can be
wrong together and every check still passes. Every such assertion left this file
under #346. What is left is what only a Python model can do:

* **fault injection** -- `deliver`, a caller that drops `final_word`, one that
  never ends a burst. A faithful model cannot test a controller's response to an
  unfaithful device, and a correct part never misbehaves.
* **the SoC ABOVE the controller** -- the Wishbone window, the arbiter, cache-line
  refill, coalescing, the clock-stop path. `hyperram_model.v` models the chip;
  none of those questions are about the chip.

So a check here may claim things about the CONTROLLER and about the SoC, and it
may MEASURE the bus. It may not JUDGE the bus against a datasheet number. The
part's own bounds -- tCSHI, tCSM, the CA encoding, the latency count, the word
order -- are judged in `gateware/probes/hyperram/controller_model_tb.sv` against
`hyperram_model.v`, which is held equal to Winbond's encrypted model over
`vendor_model_tb.sv`.

**A new conformance check belongs there, not here.**
`scripts/hyperram_model_sim.py` runs it and carries a defect run for each, so a
check that stops discriminating fails instead of reading as coverage.

**It cannot check DQS timing, and nothing here pretends to.** `DQSBUFM`,
`IDDRX2DQA` and `DDRDLLA` have no simulation model; the read strobe's alignment
is the property #92 is about and it is decided on silicon.

The pin-group question, which is the other half of #92, is answered from the
device database by `scripts/hyperram_dqs_pins.py` and is not repeated here.

## Each section runs the wrong arrangement too

Every section drives the same controller and the same model twice: once the way
this workspace does it, and once the way that was tried first. A check that only
shows the fix passing is a check that would have passed before the fix.

  1. **One command per request.** That a request produces exactly one CS#
     assertion, and that it completes. What the CA CONTAINS is case 2b of
     `controller_model_tb.sv`.

  2. **Upstream's forced long branch.** `extra_latency | 1`, in source. The
     latency COUNT is swept at six codes in both modes by the bridge, which
     grades the controller's first data beat against the device's own decode.

  3. **Held, not pulsed.** `final_word` and `perform_write`/`write_data` must
     stay asserted for the whole transfer (`docs/upstream-boundary.md`). The
     pulsed drivers are run against the model and asserted to produce the wrong
     bus behaviour -- which is the point: they produce plausible wrong answers,
     not failures. Caller-side faults, so they stay.

  4. **The gap between transactions, MEASURED.** `HyperRAMDQSInterface`'s
     `RECOVERY` state carries `# TODO: implement recovery`, so nothing in
     upstream keeps CS# high. The two controllers are compared against each
     other; whether the gap clears tCSHI is the device's judgement, and
     `hyperram_model.v` makes it.

 4b. **The same gap on the non-DQS controller**, and through the SoC's window:
     the master above may not shorten what the protocol layer leaves.

  5. **Structural, against the PHY source.** The three reasons upstream's PHY
     cannot be instantiated on r1.4, asserted rather than described.

 5b. **`latency_clocks` is a live input.** Counted on `HANDLE_LATENCY`
     occupancy, not on `read_ready`: a strobe measures the MODEL's latency
     rather than the controller's, which is the whole of #331.

  6. **Wishbone memory window.** The real port is delayed before grant, then
     exercised with reads, full writes, and partial writes. A pulsed request is
     run against the same delayed grant and asserted to fail.

  7. **Shared engine.** The real three-master state machine drives a controllable
     interface for a two-word read and write.

  8. **Cache-line burst.** Incrementing CTI produces one CS# assertion; classic
     CTI is the pre-change negative control and produces sixteen.

  9. **Cache-line WRITE, through the SoC's real bus path.** `RegisteredResponse`
     in front of the window, which is the arrangement the board has. Coalescing
     under that master is asserted to reproduce `hr cross`'s exact line --
     `8/16 correct, bad 1010101010101010`, `want 200f0e0d got 0e0d200f`.

 9b. **As built.** The one combination this repository synthesises.

 10. **The same fault on reads.** `hr cross` writes and reads through one path,
     so the two skews cancel and a total read fault showed as a half write
     fault. Checked separately against pre-filled memory.

 11. **Active Clock Stop, and coalescing turned back on.** Section 9 stays
     exactly where it is as the negative control, and this section runs two
     more: the gate removed, and the gate level-aligned rather than
     write-aligned, which is worse than no gate at all.

 12. **Every transaction ENDS.** A device that never answers, one that answers 7
     of the 8 beats asked for, and a consumer that stops asking -- on both
     controllers, and on upstream's two as the negative control, which hang in
     all three. Every transaction driven through `run`/`run16` is judged against
     `completion_bound`.

 13. **How long CS# stays Low.** Ours against upstream's, and against the
     controller's own burst budget. tCSM itself is the device's to judge.
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
from peripherals.hyperram_controller import (HyperRAMController,
                                             min_read_latency_clocks)
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


# `sync`. Only the ratio to the device matters here: nothing in this file measures
# a real duration, and since #346 nothing judges one either.
SYNC_MHZ = 120.0
SYNC_HZ = SYNC_MHZ * 1e6

# tCSHI is NOT restated here any more. Its only use was judging the gap, and the
# judge is `hyperram_model.v` -- which carries the T166 6 ns from the same
# `Config-AC.v` column #341 put in the controllers. What is left here is the
# controller's own `_recovery_cycles`, read off the controller.

# tCSM, the longest CS# may stay Low, W956A8 rev A01-006 Table 24. Used for ONE
# thing: `completion_bound`, the harness's own give-up limit. It is not a
# judgement -- `hyperram_model.v` is what says whether the bus honoured it.
T_CSM_NS = 4000.0

# `sync` for every non-DQS section. The controller needs the figure too, to turn
# tCSHI into a cycle count, so the two must come from the same place or the sim
# would be judging a controller built for one clock against a model running
# another. On this path `HyperRAMPHY` emits one CK per `sync` cycle, so it is CK.
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
        # The SHORTEST CS#-high gap this run saw, in `sync` cycles. MEASURED, not
        # judged: counting violations stops discriminating as soon as tCSHI falls
        # under one cycle -- at T166's 6 ns and sync 120 it does, and upstream's
        # no-recovery controller then passes the same check ours does. Whether a
        # gap clears the part's bound is `hyperram_model.v`'s answer. (#341, #346)
        self.cs_high_min = None
        self._cs_low_beats = 0
        self.cs_low_max = 0

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
            # CS# just fell: a new transaction. The gap it followed is recorded
            # here, before anything about the command is known.
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

    emit(f"        one command, decoded at {command['address']:#010x}")
    # WHAT THE CA CONTAINS is not asserted here any more: address, R/W#, address
    # space and burst type were graded against `encode_ca`, a second decoder in
    # this same file. They are case 2b of `controller_model_tb.sv`, where the
    # reference is a model held equal to Winbond's own. (#346)


def section_latency(checks, emit):
    """2. Why upstream's forced long branch is right for this part."""
    emit("\n2. The forced long branch, in upstream's source\n")

    async def body(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True)

    check_completed(checks, simulate(body), "the fixed-latency read")

    # The latency COUNT is not asserted here. It was checked against a model whose
    # latency is `latency_beats()` -- the controller's own constant -- so it could
    # not fail for a part reason. `controller_model_tb.sv` sweeps CR0[7:4] at six
    # codes in both modes and grades the controller's first data beat against the
    # device's own decode; `--negative-control` proves a 1 CK error is caught.
    checks.check("upstream forces the long branch unconditionally",
                 "extra_latency | 1" in _upstream_source())
    emit(f"        CR0 0x8f2f selects FIXED latency, so the device takes the")
    emit(f"        long count every time and RWDS says nothing about it.")
    emit(f"        `extra_latency | 1` is therefore CORRECT for this part as")
    emit(f"        configured; the #90 defect only pays after CR0 is set to")
    emit(f"        variable latency, which is what #338 measures.")


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
    """4. The gap between transactions, on the DQS controller.

    MEASURED here and judged elsewhere. Whether a gap clears tCSHI is a fact about
    the part, and the Python model used to judge it against a copy of the same
    number the controller was handed. `hyperram_model.v` carries the monitor now;
    what is left is the comparison between the two controllers, which needs no
    datasheet number at all. (#346)
    """
    emit("\n4. The gap the DQS controller's RECOVERY state leaves\n")

    checks.check("upstream's RECOVERY state is still a TODO",
                 "# TODO: implement recovery" in _upstream_source(),
                 "upstream implemented it; the vendored copy may be redundant")

    async def back_to_back(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run(ctx, dut, model, address=TEST_ADDRESS + 1, read=True, gap=0)

    # THE NEGATIVE CONTROL, and it is the whole value of this section. Upstream's
    # controller is run through the same harness to prove the gap separates them.
    #
    # It is the GAP and not a violation count: with tCSHI at T166's 6 ns and sync
    # 120, one cycle (8.33 ns) already clears it, so upstream's `# TODO: implement
    # recovery` would score zero violations too. Upstream leaves the ONE cycle its
    # caller's state count happens to give; ours leaves what the counter was told
    # to. (#341)
    control = simulate(back_to_back, upstream=True)
    checks.check("upstream's controller leaves ONE cycle and nothing more",
                 control.cs_high_min == 1,
                 f"{control.cs_high_min} cycles -- if upstream already held a gap "
                 f"this harness cannot tell the two apart")

    model = simulate(back_to_back)
    check_completed(checks, model, "back-to-back DQS reads")
    # The controller's own count, read off the controller. Whether that count is
    # long enough for the PART is `hyperram_model.v`'s tCSHI monitor, exercised on
    # the non-DQS controller in `controller_model_tb.sv` with `+cs_hold_ns` as the
    # control that proves it fires. (#346)
    required = model_recovery_cycles(HyperRAMDQSController, HyperBusDQSPHY,
                                     SYNC_MHZ)
    checks.check("...and the gap is the count RECOVERY was given",
                 model.cs_high_min == required,
                 f"{model.cs_high_min} cycles against {required}")
    # At sync 120 the two agree, because T166's 6 ns is under one cycle there and
    # upstream's accidental single cycle already clears it. What separates them at
    # EVERY clock is that ours moves when `recovery_cycles` is driven and upstream
    # has nothing to drive -- `scripts/hyperram_timing_levers_sim.py`. (#341)
    if required == 1:
        emit("        at this clock tCSHI is under one cycle, so upstream's "
             "accident clears it too -- the lever sweep is the discriminator")

    emit(f"        shortest gap -- upstream: {control.cs_high_min} cycle(s), "
         f"vendored: {model.cs_high_min}, its own count {required}")


def model_recovery_cycles(controller, phy, sync_mhz):
    """Cycles of CS# high a controller says it holds, off the controller itself.

    Not from a tCSHI restated here: the part's requirement is the twin's to judge
    since #346, and what this file may still ask is whether the controller left
    the gap it computed.
    """
    built = controller(phy=phy(), sync_mhz=sync_mhz)
    Fragment.get(built, None)          # only its arithmetic is wanted; mark it used
    return built._recovery_cycles


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
                 fixed_latency=True, max_latency_clocks=None,
                 clock_stop=False):
        self.phy = HyperBusPHY()
        self.sync_mhz = sync_mhz
        # `clock_stop` puts `ClockStopPHY` between the controller and the model
        # with `stall` left for the testbench to drive -- a master with no
        # arrival pattern of its own, so what is measured is the CONTROLLER's
        # response to a stall rather than some engine's stall pattern. (#340)
        self.gate = ClockStopPHY(dev=self.phy) if clock_stop else None
        if upstream:
            self.psram = HyperRAMInterface(phy=self.gate.ctrl if self.gate
                                           else self.phy)
        else:
            # `ModelHyperRAM16` hangs off the `HyperBusPHY` record directly and
            # answers in the same cycle, so the round trip here is 0 against
            # upstream's `HyperRAMPHY` at 4, and the RWDS sample instant follows it.
            # `scripts/hyperram_phy_rwds_sim.py` is where the 4 is measured and where
            # the real PHY is exercised. (#338)
            self.psram = HyperRAMController(
                phy=self.gate.ctrl if self.gate else self.phy,
                sync_mhz=sync_mhz, fixed_latency=fixed_latency,
                max_latency_clocks=max_latency_clocks, phy_round_trip_cycles=0)

    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        if self.gate is not None:
            m.submodules.gate = self.gate
            hold = getattr(self.psram, "register_active", None)
            if hold is not None:
                m.d.comb += self.gate.hold.eq(hold)
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
               max_latency_clocks=None, clock_stop=False, monitor=None):
    """Run `body(ctx, dut, model)` on the 16-bit path and return the model.

    `monitor` is a second testbench run alongside `body`, one sample per cycle.
    It is where a hostile master lives: driving `gate.stall` from the transaction
    itself rather than from an arrival pattern makes the measurement about the
    controller.
    """
    dut = NonDQSProtocolHarness(upstream=upstream, sync_mhz=sync_mhz,
                                max_latency_clocks=max_latency_clocks,
                                clock_stop=clock_stop)
    model = ModelHyperRAM16(sync_mhz=sync_mhz, deliver=deliver)

    async def testbench(ctx):
        await body(ctx, dut, model)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / (sync_mhz * 1e6), domain="sync")
    sim.add_testbench(testbench)
    if monitor is not None:
        async def watcher(ctx):
            await monitor(ctx, dut, model)
        sim.add_testbench(watcher)
    sim.run()
    return model


def section_recovery_non_dqs(checks, emit):
    """4b. The same gap on the non-DQS controller, which is what the SoC ships.

    Whether the gap clears tCSHI is judged in `controller_model_tb.sv`, against
    `hyperram_model.v`'s own monitor and with `+cs_hold_ns=25` as the control that
    proves it fires. What stays here is the SoC-layer question no device model can
    answer: whether the window ABOVE the controller shortens what the protocol
    layer leaves. (#346)
    """
    emit("\n4b. The gap on the non-DQS controller, and through the window\n")

    async def back_to_back(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run16(ctx, dut, model, address=TEST_ADDRESS + 1, read=True, gap=0)

    control = simulate16(back_to_back, upstream=True)
    required = model_recovery_cycles(HyperRAMController, HyperBusPHY,
                                     NON_DQS_SYNC_MHZ)
    checks.check("upstream's non-DQS controller leaves LESS than the count needs",
                 control.cs_high_min is not None and control.cs_high_min < required,
                 f"upstream left {control.cs_high_min} cycles against a count of "
                 f"{required}, so this harness cannot tell the two apart")
    checks.check("...and it is the GAP that differs, not the transaction count",
                 len(control.commands) == 2,
                 f"{len(control.commands)} commands from the control")

    model = simulate16(back_to_back)
    check_completed(checks, model, "back-to-back non-DQS reads")
    checks.check("the vendored controller leaves its whole count, unaided",
                 model.cs_high_min is not None and model.cs_high_min >= required,
                 f"{model.cs_high_min} cycles against a count of {required}, "
                 f"upstream's {control.cs_high_min}")
    checks.check("...and still issues both transactions",
                 len(model.commands) == 2,
                 f"{len(model.commands)} commands")

    # THE SoC-LAYER CLAIM. The window asks for the next line the moment the last
    # one acknowledges, and it may not shorten the gap the protocol layer leaves.
    # Compared against the protocol layer's own figure, so no datasheet number
    # enters it.
    refill_model, _ = simulate_line_refill(incrementing=False)
    checks.check("sixteen classic transactions through the window keep the same gap",
                 refill_model.cs_high_min is not None
                 and refill_model.cs_high_min >= model.cs_high_min,
                 f"{refill_model.cs_high_min} cycles through the window against "
                 f"{model.cs_high_min} at the protocol layer, across "
                 f"{len(refill_model.commands)} transactions")

    emit(f"        shortest CS#-high gap, in cycles: upstream {control.cs_high_min}, "
         f"vendored {model.cs_high_min}, through the window "
         f"{refill_model.cs_high_min}; the count is {required}")


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
    # REPORTED, not asserted: the controller is given 4 while the model keeps
    # luna's 5, which is a latency MISMATCH and not a fact about either side.
    # Where the part serves its first beat is `hyperram_dqs_model_sim.py --stage
    # probe`, measured on the twin and on Winbond's model at every code.
    emit(f"        as-built write lands: {landed} "
         f"(controller 4, model {latency_beats()} -- a mismatch, not a verdict)")

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
    emit(f"        as-built shortest CS#-high gap: {model.cs_high_min} beats "
         f"at sync 60 MHz")
    emit(f"        HYPERRAM_LATENCY_CLOCKS = {HYPERRAM_LATENCY_CLOCKS}, "
         f"sync 60 MHz")


# 7b, "which 16-bit word of a DQS write reaches the device FIRST", was HERE and
# asserted nothing: its harness never got `jtag_ack`, so nothing reached the model
# and the section printed a report about an empty capture. It read as coverage.
# The question is answered by `hyperram_dqs_model_sim.py --stage order`, against
# the twin and Winbond's model, with a deliberately rewired run required to fail.
# (#206, #346)


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

    # The floor is `phy_round_trip_cycles + 3`, not 2: below it READ_DATA begins
    # while the device's RWDS fall is still in flight and latches it as a strobe
    # over a tristate bus. #353 derived it twice, from a burst and from the AC
    # parameters. The check here used to say "below 2", which the floor refutes.
    floor = min_read_latency_clocks(0)
    at_floor = latency_cycles_held(latency=floor)
    checks.check("a count under the read floor is clamped up to it, not wrapped",
                 at_floor is not None and latency_cycles_held(latency=0) == at_floor,
                 f"latency 0 held {latency_cycles_held(latency=0)} cycles, the "
                 f"floor of {floor} holds {at_floor} -- a counter that wrapped "
                 f"waits ~2^n cycles, which is a hang and not a short read")

    # `fixed_latency` reaching the variable branch is not asserted here any more.
    # It rested on `ModelHyperRAM16` holding RWDS low through the CA, which is not
    # what the part does -- the twin drives it from `CR0[3] | refresh` after a
    # 12 ns tDSV float. `controller_model_tb.sv` sweeps both modes at six codes
    # against that device, and `+rwds_float=1` is the control. (#338, #346)
    checks.check("`fixed_latency` is an input, not a compile-time constant",
                 hasattr(HyperRAMController(phy=HyperBusPHY(), sync_mhz=60.0),
                         "fixed_latency"),
                 "no `fixed_latency` signal on the controller")


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

    def __init__(self, *, sync_mhz=NON_DQS_SYNC_MHZ, deliver=None):
        self.commands = []
        self.ca_words = []
        self.transaction_cycles = []
        # Read beats this device serves before going silent. See `ModelHyperRAM`.
        self.deliver = deliver
        self.read_beats = 0
        # Transactions that never brought `idle` back inside `completion_bound`,
        # and what `timed_out` read at the end of the last one.
        self.incomplete = 0
        self.durations = []
        self.timed_out = None
        self.burst_beats = None
        self.beats_taken = 0
        # MEASURED, not judged: the shortest gap between transactions and the
        # longest CS#-Low run, in cycles. Whether either clears the part's bound
        # is `hyperram_model.v`'s answer, not this file's. (#346)
        self._cs_high_cycles = 10**6      # nothing before the first transaction
        self.cs_high_min = None
        self._cs_low_cycles = 0
        self.cs_low_max = 0
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
        else:
            self._cs_low_cycles = 0

        if cs and not self._previous_cs:
            # CS# just fell. The gap it followed is recorded here, before anything
            # about the command is known.
            if self._cs_high_cycles < 10**6:
                self.cs_high_min = (self._cs_high_cycles if self.cs_high_min is None
                                    else min(self.cs_high_min, self._cs_high_cycles))
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

    # The CK totals are REPORTED. Asserting 49 and 304 was asserting the
    # controller's own `HIGH_LATENCY_CLOCKS` back at it -- the model's data phase
    # is `HIGH_LATENCY_CLOCKS - 2` by construction, so both numbers move together
    # and the check reports an agreement it built in. Where the data phase starts
    # is graded in `controller_model_tb.sv` against the device's own decode, at
    # six codes in both latency modes. (#346)
    burst_cycles = sum(burst_model.transaction_cycles)
    classic_cycles = sum(classic_model.transaction_cycles)
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


def section_register_clock_stop(checks, emit):
    """11b. The clock must not stop during a register access. (#340)

    Winbond's 2025 application note 7.2.2 and the datasheet's 10.2.2 are the same
    paragraph until their last sentence, where the datasheet says *"stop the
    clock when it is in Low state"* and the note says *"do not stop the clock
    during register access"*. Neither carries both; the note REPLACES the rule.

    Register accesses run through the same FSM as memory ones, so a master that
    stalls mid-transaction stops CK during a register read or write. Coalescing is
    off for correctness (#185), which makes this latent rather than live -- the
    shape #240 tracks, a defect that wakes when a build flag moves.

    THE MASTER HERE IS MAXIMALLY HOSTILE: `stall` is asserted on every cycle CS#
    is Low. That is not an arrival pattern any engine produces; it is the
    strongest form of the question, and it makes the answer about the controller.
    Memory in the same harness is the positive control -- it MUST still have its
    clock withheld, or the gate has simply been disconnected.
    """
    emit("\n11b. The clock during a register access\n")

    def under_stall(register_space, read):
        """One transaction with a master that stalls whenever CS# is Low.

        Returns (model, withheld states). `withheld` is every cycle the
        controller asked for a clock and the gate took it away, recorded against
        the controller's own FSM state so a failure names where.
        """
        withheld = []

        async def body(ctx, dut, model):
            await run16(ctx, dut, model, address=0, read=read,
                        data=0x8f2f, register_space=register_space)

        async def monitor(ctx, dut, model):
            # One sample per cycle for as long as the body can run: `run16`'s own
            # bound plus the request and RECOVERY cycles around it. Expiry is not
            # an error here -- the body decides whether the transaction finished.
            for _ in range(completion_bound(dut.sync_mhz) + 8):
                cs = ctx.get(dut.phy.cs)
                ctx.set(dut.gate.stall, cs)
                if (cs and ctx.get(dut.gate.ctrl.clk_en)
                        and not ctx.get(dut.phy.clk_en)):
                    withheld.append(HyperRAMController.STATES[
                        ctx.get(dut.psram.state)])
                await ctx.tick()

        model = simulate16(body, clock_stop=True, monitor=monitor)
        return model, withheld

    reg_write, write_withheld = under_stall(register_space=True, read=False)
    reg_read, read_withheld = under_stall(register_space=True, read=True)
    memory, mem_withheld = under_stall(register_space=False, read=False)

    # THE POSITIVE CONTROL FIRST. If the gate withheld nothing anywhere, the two
    # checks below would pass on a harness whose `stall` goes nowhere.
    checks.check("the same stall DOES withhold clocks from a memory access",
                 bool(mem_withheld),
                 "no clock was withheld from memory either, so `stall` is not "
                 "reaching the gate and this section proves nothing")

    checks.check("a register WRITE keeps its clock under a stalling master",
                 not write_withheld,
                 f"{len(write_withheld)} cycles withheld, in "
                 f"{sorted(set(write_withheld))}")
    checks.check("a register READ keeps its clock under a stalling master",
                 not read_withheld,
                 f"{len(read_withheld)} cycles withheld, in "
                 f"{sorted(set(read_withheld))}")
    # A clock held for the register access is only useful if the access then
    # finishes: a hold that leaves the FSM parked is a different defect.
    checks.check("...and both register transactions still complete",
                 reg_write.incomplete == 0 and reg_read.incomplete == 0,
                 f"write incomplete {reg_write.incomplete}, "
                 f"read incomplete {reg_read.incomplete}")
    # Completing via the tCSM watchdog is not completing. A register read whose
    # clock is taken away never gets its data, so it ends the only other way there
    # is -- which reads as "finished" to `incomplete` alone.
    checks.check("...by serving the transaction, not by the tCSM watchdog",
                 not reg_write.timed_out and not reg_read.timed_out,
                 f"write timed_out {reg_write.timed_out}, "
                 f"read timed_out {reg_read.timed_out}")

    emit(f"        withheld cycles -- register write: {len(write_withheld)}, "
         f"register read: {len(read_withheld)}, memory: {len(mem_withheld)}")


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
    """13. How long CS# is held Low, ours against upstream's.

    tCSM is a part fact and `hyperram_model.v` is what judges it -- an unended
    burst runs there under `+stim=1`, with `+cs_hold_ns=1000` as the control that
    proves its monitor fires. What stays here is the caller-side half: a caller
    that never ends a burst, and the controller's own word cap chopping it. (#317)
    """
    emit("\n13. CS# Low on a burst nobody ends\n")

    async def endless_read(ctx, dut, model):
        await run16(ctx, dut, model, address=TEST_ADDRESS, read=True, beats=0)

    # THE NEGATIVE CONTROL: a device that keeps answering and a caller that never
    # ends the burst. Upstream holds CS# Low until the harness gives up.
    control = simulate16(endless_read, upstream=True)
    chopped = simulate16(endless_read)
    budget = chopped.burst_beats
    checks.check("upstream holds CS# Low until the harness gives up",
                 control.cs_low_max > chopped.cs_low_max,
                 f"upstream {control.cs_low_max} cycles, ours "
                 f"{chopped.cs_low_max} -- so this harness cannot tell the two "
                 f"apart and the checks below prove nothing")
    checks.check("ours chops it inside the budget the controller computed",
                 chopped.cs_low_max <= completion_bound(NON_DQS_SYNC_MHZ),
                 f"CS# Low up to {chopped.cs_low_max} cycles")
    checks.check("...at the word cap exactly, not one beat past it",
                 chopped.beats_taken == budget,
                 f"took {chopped.beats_taken} beats against a cap of {budget}")
    checks.check("...and the caller is told the controller ended it",
                 chopped.timed_out == 1, f"timed_out={chopped.timed_out}")

    dqs = simulate(
        lambda ctx, dut, model: run(ctx, dut, model, address=TEST_ADDRESS,
                                    read=True, beats=0))
    checks.check("the DQS controller chops the same shape",
                 dqs.cs_low_max <= completion_bound(SYNC_MHZ),
                 f"CS# Low up to {dqs.cs_low_max} beats")

    emit(f"        CS# Low, longest run: upstream {control.cs_low_max} cycles, "
         f"ours {chopped.cs_low_max}, DQS {dqs.cs_low_max} beats")
    emit(f"        word cap {budget} beats, taken {chopped.beats_taken}; the device "
         f"clocked out {chopped.read_beats}, the extra one on the RECOVERY cycle")


# 14, the extra-latency sample taken inside the CA, and 15, CA[45] forced for
# register space, were HERE. Both are facts about the part:
#
#   * 14 rested on `ModelHyperRAM16` splitting stale from answered at a
#     hand-chosen one-cycle tDSV driven as a hard 0 or 1. The part floats RWDS for
#     12 ns and then answers; a controller sampling early reads a float, which no
#     Python model here can express. `controller_model_tb.sv` `+stim=2` runs it
#     against a device that never drives RWDS, with `+rwds_float=1` as the
#     control. (#321, #338)
#   * 15's five checks are the command byte, and case 2b grades all of them off
#     the independent model's own capture: 0x60 forced for a single_page register
#     write, 0x80 left alone for a wrapped memory burst. (#320)
#
# (#346)


def main():
    parser = argparse.ArgumentParser(
        description="The controller and the SoC above it, against a model that "
                    "is deliberately allowed to misbehave.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every bus beat")
    args = parser.parse_args()

    emit("HyperRAM: the controller and the SoC above it, against an unfaithful model")
    emit("no fact about the PART is asserted here -- see the docstring, and #346")

    checks = Checks(emit)
    for section in (section_command, section_latency, section_held,
                    section_as_built,
                    section_recovery, section_recovery_non_dqs,
                    section_latency_input, section_structural, section_wishbone,
                    section_shared_engine, section_line_refill,
                    section_line_write, section_line_read_bubble,
                    section_clock_stop, section_register_clock_stop,
                    section_escape, section_tcsm):
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
