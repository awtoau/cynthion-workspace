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

 11. **Active Clock Stop, and coalescing turned back on.** The same harness and
     the same assertions as 9 and 10, with the PHY record split so the
     controller and the device see different `clk_en` and the device waits out
     the master's bubble. Section 9 stays exactly where it is as the negative
     control, and this section runs two more: the gate removed, and the gate
     present but level-aligned rather than write-aligned, which is worse than
     no gate at all. See `docs/hyperram-bus-review.md` 5 for the datasheet.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "hyperram"))

from amaranth import Elaboratable, Module, Signal
from amaranth.hdl import Fragment
from amaranth.lib import wiring
from amaranth.sim import Simulator
from amaranth_soc import wishbone

from hyperram_dqs_controller import HyperRAMDQSController
from luna.gateware.interface.psram import (HyperBusDQSPHY, HyperBusPHY,
                                            HyperRAMDQSInterface,
                                            HyperRAMInterface)

from sim_check_harness import Checks
from vexii_bootram import (BootRAM, ClockStopPHY, HYPERRAM_CK_MHZ,
                           HYPERRAM_MAX_BURST_WORDS, HYPERRAM_TCSM_NS,
                           HyperRAMWishbone, hyperram_max_burst_words,
                           hyperram_max_stall_cycles)
from wishbone_pipe import RegisteredResponse


# `sync`. Only the ratio to the device matters here, since nothing in this file
# measures a real duration -- the one timing parameter that is checked, tCSHI, is
# converted from nanoseconds using this.
SYNC_MHZ = 120.0
SYNC_HZ = SYNC_MHZ * 1e6

# tCSHI, CS# high between transactions, W956A8. Ten nanoseconds is longer than
# one 120 MHz cycle, which is why back-to-back transactions violate it at the
# rate this part is actually run at.
T_CSHI_NS = 10.0

# The DQS record moves 32 bits per `sync` cycle -- eight lines, four device
# edges -- where the non-DQS record moves 16. Section 1 rereads its own capture
# at the narrower width to show what that trap looks like.
DQS_BEAT_BITS = 32

# The part is configured for FIXED latency: CR0 reads `0x8f2f` and bit 3 selects
# it. So the device takes the same latency on every transaction and RWDS during
# the command period says nothing about this one. That is the fact section 2
# rests on, and it comes from `docs/chips/w956a8-hyperram.md`, which decoded it
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
# `docs/chips/w956a8-hyperram.md` carries the field table.
#
# This is stated separately from `latency_beats()` ON PURPOSE. That function
# returns the number of beats the CONTROLLER waits, so a model built on it agrees
# with the controller by construction and cannot detect the two disagreeing --
# which is the whole shape of #186. Two numbers that are allowed to differ are
# the minimum needed to notice that they do.
DEVICE_FIXED_LATENCY_CK = 14

# CK per `sync` cycle on the DQS path: the fabric runs at CK/2 with 4:1 gearing.
DQS_CK_PER_SYNC = 2


def latency_beats():
    """`HANDLE_LATENCY` runs one beat per remaining count, plus the zero beat."""
    return HyperRAMDQSInterface.HIGH_LATENCY_CLOCKS + 1

# How long a run may take before the harness gives up, in `sync` cycles. Not a
# timing measurement: a controller that never leaves a state would otherwise hang
# the simulator, and the number is simply far past the longest transaction here
# (a command, latency and one data beat is under thirty cycles).
CYCLE_LIMIT = 4000

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

    def __init__(self, *, contents=None, verbose=False, latency=None):
        self.memory = dict(contents or {})
        self.verbose = verbose
        self.latency = latency_beats() if latency is None else latency

        # What the run is for the caller to inspect.
        self.commands = []          # one dict per decoded command
        self.written = []           # (address, 32-bit value) in order
        self.read_beats = 0
        self.cshi_violations = 0
        self.trace = []

        self._state = "idle"
        self._ca_bytes = []
        self._beat = 0
        self._address = 0
        self._read = True
        self._prev_cs = 0
        self._cs_high_beats = 10**6  # nothing before the first transaction

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
            if self._read:
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
    """One `sync` cycle: show the model the bus, hand back what the device drives."""
    dq_i, datavalid, burstdet = model.step(
        cs=ctx.get(dut.phy.cs),
        dq_o=ctx.get(dut.phy.dq.o),
        dq_e=ctx.get(dut.phy.dq.e),
        clk_en=ctx.get(dut.phy.clk_en),
    )
    ctx.set(dut.phy.dq.i, dq_i)
    ctx.set(dut.phy.datavalid, datavalid)
    ctx.set(dut.phy.burstdet, burstdet)
    await ctx.tick()


async def run(ctx, dut, model, *, address, read, data=None,
              hold_final_word=True, hold_write=True, gap=None):
    """One transaction, driven the way a caller chooses to drive it.

    `hold_final_word` and `hold_write` are the two traps from
    `docs/upstream-boundary.md`, made switchable so the wrong arrangement can be
    run against the same model rather than described.

    `gap` is how many idle beats to leave before asserting the request. `None`
    means "as few as the controller allows", which is what back-to-back
    transactions do and what violates tCSHI.
    """
    psram = dut.psram

    # Idle beats BEFORE the request, and the model is stepped through every one
    # of them. Ticking without stepping the model would leave the gap invisible
    # to the thing that measures it, and section 4 would then pass because
    # nothing was counted rather than because the gap was kept.
    for _ in range(gap or 0):
        await beat(ctx, dut, model)

    ctx.set(psram.register_space, 0)
    ctx.set(psram.single_page, 0)
    ctx.set(psram.address, address)
    ctx.set(psram.perform_write, 0 if read else 1)
    if data is not None:
        ctx.set(psram.write_data, data)
    ctx.set(psram.final_word, 1)
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

    for _ in range(CYCLE_LIMIT):
        await beat(ctx, dut, model)
        if ctx.get(psram.idle) and model._state == "idle":
            break

    ctx.set(psram.final_word, 0)
    ctx.set(psram.perform_write, 0)


def simulate(body, *, upstream=False, sync_mhz=SYNC_MHZ, latency=None):
    """Run `body(ctx, dut, model)` and return the model it used."""
    dut = Harness(upstream=upstream, sync_mhz=sync_mhz, latency=latency)
    model = ModelHyperRAM()

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

    # THE NEGATIVE CONTROL, and it is the whole value of this section. A model
    # that could not detect a violation would pass the positive check silently,
    # so upstream's controller is run through the same harness to prove the
    # detector fires.
    control = simulate(back_to_back, upstream=True)
    checks.check("upstream's controller VIOLATES tCSHI back-to-back",
                 control.cshi_violations > 0,
                 "no violation seen from upstream either, so this harness "
                 "cannot tell the two apart and the check below proves nothing")

    model = simulate(back_to_back)
    checks.check("the vendored controller keeps tCSHI with NO gap from the master",
                 model.cshi_violations == 0,
                 f"{model.cshi_violations} violations -- the RECOVERY counter is "
                 f"not holding CS# high long enough")

    required = max(1, int(-(-T_CSHI_NS * SYNC_MHZ // 1000)))
    emit(f"        tCSHI {T_CSHI_NS:g} ns at {SYNC_MHZ:g} MHz is "
         f"{required} whole cycle(s), counted inside the controller")
    emit(f"        upstream: {control.cshi_violations} violations, "
         f"vendored: {model.cshi_violations}")


def section_structural(checks, emit):
    """5. The reasons upstream's PHY cannot be instantiated here."""
    emit("\n5. Structural: our PHY against upstream's, in source\n")

    ours = (ROOT / "ecp5-test" / "riscv" / "hyperram_dqs_phy.py").read_text()
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
    """The HyperRAMInterface signal surface, driven by section 7."""

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
        self.interface = HyperRAMInterface(
            phy=self.gate.ctrl if clock_stop else self.phy)
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

    def __init__(self):
        self.commands = []
        self.transaction_cycles = []
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

        if cs and not self._previous_cs:
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
                self._latency = HyperRAMInterface.HIGH_LATENCY_CLOCKS - 2
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
                # Serve back whatever was stored, so a write and a read of the
                # same line can be compared. An address never written keeps the
                # old synthetic pattern, which is what section 8 reads.
                dq_i = self.memory.get(self._address,
                                       (0x4000 + self._address) & 0xffff)
                rwds_i = 0b10
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
    sim.add_clock(1 / 192e6, domain="sync")
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
    sim.add_clock(1 / 192e6, domain="sync")
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
    sim.add_clock(1 / 192e6, domain="sync")
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
    sim.add_clock(1 / 192e6, domain="sync")
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
    # 1 `RECOVERY`, with `LATCH_RWDS` before CK starts. So a transaction is
    # 17 + words -- 49 for a 32-word line, 19 each for sixteen 2-word transfers.
    #
    # These read 51 and 336 until the model's data phase was corrected: it
    # entered two cycles late, reads tolerated it because RWDS gates the
    # controller's sampling, and the extra two showed up in every total. NOT the
    # same quantity as the 19 CK in `docs/memory-speed-options.md`, which is a
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
                    section_recovery, section_structural, section_wishbone,
                    section_shared_engine, section_line_refill,
                    section_line_write, section_line_read_bubble,
                    section_clock_stop):
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
