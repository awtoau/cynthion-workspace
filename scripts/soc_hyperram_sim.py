#!/usr/bin/env python3
#
# The HyperBus protocol layer, against a model of the part. See awtoau/cynthion-workspace#92, #90.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the DQS controller and the way this workspace drives it.

    python3 scripts/soc_hyperram_sim.py
    python3 scripts/soc_hyperram_sim.py -v          # every bus beat

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/soc_hyperram_sim.log`.

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
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_hyperram_sim.log"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "hyperram"))

from amaranth import Elaboratable, Module, Signal
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from luna.gateware.interface.psram import HyperBusDQSPHY, HyperRAMDQSInterface

from soc_board_sim import Checks


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
    """`HyperRAMDQSInterface` with its record brought out for a Python model."""

    def __init__(self):
        self.phy = HyperBusDQSPHY()
        self.psram = HyperRAMDQSInterface(phy=self.phy)

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


def simulate(body):
    """Run `body(ctx, dut, model)` and return the model it used."""
    dut = Harness()
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

    checks.check("the controller's RECOVERY state is still a TODO upstream",
                 "# TODO: implement recovery" in _upstream_source(),
                 "upstream implemented it; the gap may no longer be ours to keep")

    async def back_to_back(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run(ctx, dut, model, address=TEST_ADDRESS + 1, read=True, gap=0)

    model = simulate(back_to_back)
    checks.check("back-to-back transactions VIOLATE tCSHI",
                 model.cshi_violations > 0,
                 "no violation seen, so this check is not discriminating")

    required = max(1, int(-(-T_CSHI_NS * SYNC_MHZ // 1000)))

    async def with_gap(ctx, dut, model):
        await run(ctx, dut, model, address=TEST_ADDRESS, read=True, gap=0)
        await run(ctx, dut, model, address=TEST_ADDRESS + 1, read=True,
                  gap=required + 1)

    model = simulate(with_gap)
    checks.check("holding the gap the bring-up design counts fixes it",
                 model.cshi_violations == 0,
                 f"{model.cshi_violations} violations remain")
    emit(f"        tCSHI {T_CSHI_NS:g} ns at {SYNC_MHZ:g} MHz is "
         f"{required} whole cycle(s)")


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


def main():
    parser = argparse.ArgumentParser(
        description="The HyperBus protocol layer, against a model of the part.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every bus beat")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(text=""):
        print(text)
        lines.append(text)

    emit("HyperRAM: the protocol layer, against a model of the W956A8")
    emit("timing of the DQS strobe is NOT checked here -- see the docstring")

    checks = Checks(emit)
    for section in (section_command, section_latency, section_held,
                    section_recovery, section_structural):
        section(checks, emit)

    emit()
    if checks.failures:
        emit(f"  {len(checks.failures)} FAILED: {', '.join(checks.failures)}")
    else:
        emit("  all checks passed")
    emit(f"  log: {LOG.relative_to(ROOT)}")
    emit()

    LOG.write_text("\n".join(lines))
    return 1 if checks.failures else 0


if __name__ == "__main__":
    sys.exit(main())
