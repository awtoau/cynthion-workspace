#!/usr/bin/env python3
#
# The JTAG register readback, against the clock ratio that used to slip it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `probes/jtag_registers.py` reads back correctly at ANY sync:TCK ratio,
and that luna's version -- the one every measurement in this workspace came
through -- does not. See #204.

    ./scripts/soc_jtag_registers_sim.py        # every check
    ./scripts/soc_jtag_registers_sim.py -v     # and print every transaction
    ./scripts/soc_jtag_registers_sim.py --soak # more ratios, more phases

## Why the ratio is now a free variable

`soc/clocks.py` takes `usb` off the PLL, so `sync` is no longer pinned to 60 MHz
by the USB PHY: 63..130 MHz is reachable and the CPU clock is expected to move.
TCK is constrained at 20 MHz. So sync:TCK is whatever the build chose -- as low
as 1:1 -- and a transport that is only correct above a ratio is a measurement
that is only correct on some builds. Nothing in the protocol says which.

## The three designs under test

  * `luna` -- `luna.gateware.interface.jtag.JTAGRegisterInterface`, ELABORATED
    FROM THE INSTALLED PACKAGE, not transcribed. `JTAGG` is an ECP5 hard block
    with no simulation model, so the `Instance` constructor is swapped for one
    that wires the block's ports to this testbench for the duration of the
    build. What runs here is the real thing, off `site-packages`.
  * `fixed` -- ours, clocked by TCK.
  * `slipped` -- ours with one register stage added on TDO, which moves every
    bit the host receives by one slot and MUST fail. Without it, a green run
    would only say the fault is absent from the designs tested, not that this
    harness can see one. It misreads the applet id as `0x90a48663` against
    `0x48524331` -- one bit off the `0x48a48663` the board actually returned.

## The model is calibrated, not assumed

A testbench for an asynchronous interface has a free parameter -- where in the
TCK period the host takes a bit -- and it is worth a whole slot. So it is pinned
to what the board does:

  * at sync 60 / TCK 12, the ratio the ceiling harness ran at, luna reads an
    applet id back exactly, writes a register and reads it back, and answers
    `0xDEADBEEF` for an address it does not implement. The model must agree,
    and does.
  * the widths the host measures by shifting ones out of the two registers are
    33 and 17, and the same host code reads this hardware in the field.

Both readings are then run against everything: TDO taken just before the rising
edge, and TDO taken at the falling edge -- the pessimistic reading of JTAGG,
which registers JTDO into the pad there. They differ by one slot, and no design
that advances TDO on one edge of TCK can satisfy both; the fixed design advances
it on the rising edge with an enable captured on the falling one, which does.
That is the reason for a construction that otherwise looks like the wrong edge.

## Why every check costs about the same

`sim_check_harness` warns when the per-check cost is flat, because a flat cost is
usually a poll interval. Here it is not: nearly every check is one whole
simulation of the same transaction script at a different ratio, phase or design,
so they cost the same because they are the same amount of work. Nothing in this
file waits on a clock.

## What this cannot say

It says nothing about metastability. A `sync`-domain sampler fed an asynchronous
TCK can also settle late, and no functional simulation shows that -- so the
simulated luna is OPTIMISTIC: it slips below 2x here, where the board slipped at
2.5x. The mechanism is the one #204 describes; the margin is not.

It does not run place-and-route, so it does not say the `jtck` domains close
timing. The evidence for a TCK-clocked shifter closing on this part is
`soc/bus/jtag_stage.py`, which has been staging firmware images over the same
primitive at the same rate.

It does not prove the host is unchanged by running the host. Apollo's
`ECP5_JTAGRegisters` talks to a SAMD11 over USB. What is proven instead is
stronger than a re-implementation of it would be: for identical stimulus, the
fixed design hands back the identical bits luna hands back in the regime where
luna works. A host that could not tell the difference cannot need changing.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import ClockDomain, Elaboratable, Module, Signal  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import luna.gateware.interface.jtag as luna_jtag  # noqa: E402

from jtag_registers import JTAGRegisterInterface  # noqa: E402

# The register map both designs are built with. Addresses 28..31 and the top of
# the space are here because #204's third failure was a register added at 29
# that silently did not exist while 28 worked.
REG_IDENT = 1
REG_SCRATCH = 2
REG_TOGGLE = 3
REG_SPREAD = (28, 29, 30, 31)
REG_TOP = (1 << 15) - 1
REG_MISSING = 9

IDENT = 0x48524331          # "HRC1", the constant the ceiling harness checks
SPREAD_VALUES = {address: 0xA0000000 | address for address in REG_SPREAD}
TOP_VALUE = 0x7F7F7F7F
DEFAULT_READ = 0xDEADBEEF

# Apollo's TCK, and the clock the ceiling harness ran `sync` at. This pair is
# the calibration point: the board reads correctly here.
APOLLO_TCK_HZ = 12e6
CALIBRATION_SYNC_HZ = 60e6

# What the board will actually run once `sync` is free: TCK is constrained at
# 20 MHz in `jtag_stage.py`, and `clocks.py` reaches 63..130 MHz. 20 MHz `sync`
# is 1:1 -- the ratio that hides a crossing, so it is the one to test.
JTCK_HZ = 20e6
RATIO_SYNC_HZ = (20e6, 30e6, 63e6, 130e6)
SOAK_SYNC_HZ = (20e6, 25e6, 30e6, 40e6, 63e6, 100e6, 130e6)

# The ladder the reference is walked down, to find where it stops working
# rather than to assert a number this simulation cannot know. The board's own
# boundary is lower than this one -- see "What this cannot say".
BOUNDARY_SYNC_HZ = (20e6, 25e6, 30e6, 40e6, 63e6, 130e6)

# Where TCK's first edge falls inside the `sync` period. The two clocks are
# unrelated on the board, so a testbench that only ever aligned them would be
# testing one phase of many; and exact alignment makes the simulator's ordering
# of simultaneous events part of the answer, which is how a deterministic
# harness acquires a flake.
PHASES = (0.13, 0.61)
SOAK_PHASES = (0.13, 0.37, 0.61, 0.89)

# How many TCK edges the host spends between the command shift and the data
# shift. Apollo's `register_transaction` spends `run_test(32)` plus an 8-bit IR
# shift; 32 is the part this testbench can name.
IDLE_TCK = 32


class Tap:
    """The `JTAGG` ports, owned by the testbench."""

    NAMES = ("tck", "tdi", "ce1", "ce2", "shift", "update", "rstn", "rti1",
             "rti2", "tdo1", "tdo2")

    def __init__(self):
        for name in self.NAMES:
            setattr(self, name, Signal(name=f"tap_{name}"))


# JTAGG's port names, and which way each one faces.
JTAGG_PORTS = {
    "o_JTCK": "tck", "o_JTDI": "tdi", "o_JCE1": "ce1", "o_JCE2": "ce2",
    "o_JSHIFT": "shift", "o_JUPDATE": "update", "o_JRSTN": "rstn",
    "o_JRTI1": "rti1", "o_JRTI2": "rti2",
    "i_JTDO1": "tdo1", "i_JTDO2": "tdo2",
}


class FakeJTAGG(Elaboratable):
    """`JTAGG` as a set of wires to the testbench.

    The hard block has no simulation model, so luna's design cannot be
    simulated as written. Rather than transcribe luna into this file -- where a
    transcription error would be indistinguishable from a finding -- the
    `Instance` constructor is swapped out while luna's module elaborates, and
    the ports are connected here instead.
    """

    def __init__(self, tap, kwargs):
        self.tap = tap
        self.kwargs = kwargs

    def elaborate(self, platform):
        m = Module()
        for name, value in self.kwargs.items():
            port = JTAGG_PORTS.get(name)
            if port is None:
                continue
            signal = getattr(self.tap, port)
            if name.startswith("o_"):
                m.d.comb += value.eq(signal)
            else:
                m.d.comb += signal.eq(value)
        return m


class SlippedRegisters(JTAGRegisterInterface):
    """The fix with one register stage added on TDO: the negative control.

    One method changes. Every bit the host receives moves one TCK slot later,
    which is the shape of failure #204 reported, and being able to see it is the
    only thing that makes a passing equivalence check mean anything.
    """

    def _present(self, m, command_bit, data_bit):
        m.d.jtck_rise += [self.tdo1.eq(command_bit), self.tdo2.eq(data_bit)]


def add_registers(design, scratch, toggle):
    """The same register map on either design."""
    design.add_read_only_register(REG_IDENT, read=IDENT)
    design.add_register(REG_SCRATCH, value_signal=scratch)
    design.add_read_only_register(REG_TOGGLE, read=toggle)
    for address, value in SPREAD_VALUES.items():
        design.add_read_only_register(address, read=value)
    design.add_read_only_register(REG_TOP, read=TOP_VALUE)


def build(which):
    """Returns (tap, fragment, design) for one of the three designs."""
    tap = Tap()
    scratch = Signal(32, name="scratch")

    # A register that changes every `sync` cycle, so a readback assembled from
    # more than one cycle shows a value that is neither -- which is the only
    # deterministic way to see a torn multi-bit crossing.
    toggle = Signal(32, name="toggle")

    m = Module()
    m.domains.sync = ClockDomain()
    m.d.sync += toggle.eq(~toggle)

    if which == "luna":
        original = luna_jtag.Instance
        luna_jtag.Instance = lambda name, **kwargs: FakeJTAGG(tap, kwargs)
        try:
            design = luna_jtag.JTAGRegisterInterface(
                default_read_value=DEFAULT_READ)
            add_registers(design, scratch, toggle)
            m.submodules.design = design
            fragment = Fragment.get(m, None)
        finally:
            luna_jtag.Instance = original
        return tap, fragment, design

    cls = SlippedRegisters if which == "slipped" else JTAGRegisterInterface
    design = cls(default_read_value=DEFAULT_READ, simulate=True)
    add_registers(design, scratch, toggle)
    m.submodules.design = design
    m.d.comb += [
        design.tck.eq(tap.tck), design.tdi.eq(tap.tdi),
        design.ce1.eq(tap.ce1), design.ce2.eq(tap.ce2),
        design.shift.eq(tap.shift), design.update.eq(tap.update),
        design.rstn.eq(tap.rstn), design.rti1.eq(tap.rti1),
        tap.tdo1.eq(design.tdo1), tap.tdo2.eq(design.tdo2),
    ]
    return tap, Fragment.get(m, None), design


class Chain:
    """A TAP whose JTAGG outputs change on the rising edge, as the block's do.

    `pulse` takes the state the TAP is in AFTER its rising edge, because that is
    when JTDI, JSHIFT and JCE move. Getting this wrong is not cosmetic: JSHIFT
    applied half a period late makes luna's design appear to lose its first bit
    at every ratio, which reads as a finding and is a modelling error.

    `sample` is where in the period the host reads TDO. "rise" is just before
    the rising edge, which is what the JTAG standard says and what reproduces
    the board; "fall" is at the falling edge, the pessimistic reading of JTAGG's
    output register. The fixed design must pass under both.
    """

    def __init__(self, ctx, tap, *, jtck_hz, sample="rise", verbose=False):
        self.ctx = ctx
        self.tap = tap
        self.half = 1 / (2 * jtck_hz)
        self.sample = sample
        self.verbose = verbose

    async def pulse(self, *, tdi=0, ce1=0, ce2=0, shift=0, rti1=0, tdo=None):
        ctx = self.ctx
        at_fall = ctx.get(getattr(self.tap, tdo)) if tdo else 0
        ctx.set(self.tap.tck, 0)
        await ctx.delay(self.half)
        at_rise = ctx.get(getattr(self.tap, tdo)) if tdo else 0
        ctx.set(self.tap.tck, 1)
        ctx.set(self.tap.tdi, tdi)
        ctx.set(self.tap.ce1, ce1)
        ctx.set(self.tap.ce2, ce2)
        ctx.set(self.tap.shift, shift)
        ctx.set(self.tap.rti1, rti1)
        await ctx.delay(self.half)
        return at_fall if self.sample == "fall" else at_rise

    async def idle(self, count=4, rti1=0):
        """TCK with neither enable asserted, which is what TAP navigation is."""
        for _ in range(count):
            await self.pulse(rti1=rti1)

    async def scan(self, which, value, length, *, pad=4):
        """One DR scan on tap 1 or 2. Returns the TDO bits, first bit as LSB.

        `pad` is the TAP navigation either side. It is a parameter because the
        handshake measurement below counts edges, and padding a scan with four
        of them by default would hide four of the ones being counted.
        """
        enable = {"ce1": 1} if which == 1 else {"ce2": 1}
        tdo = "tdo1" if which == 1 else "tdo2"
        await self.idle(pad)
        await self.pulse(**enable)                    # CAPTURE-DR
        await self.pulse(**enable, shift=1)           # SHIFT-DR
        received = 0
        for index in range(length):
            last = index == length - 1
            bit = await self.pulse(tdi=(value >> index) & 1, tdo=tdo,
                                   **enable, shift=0 if last else 1)
            received |= bit << index
        await self.pulse(**enable)                    # EXIT1/PAUSE-DR
        await self.idle(pad)
        return received

    async def transact(self, address, value=0, *, write=False, idle=IDLE_TCK,
                       pad=4):
        """A register transaction, shaped like Apollo's `register_transaction`.

        Command into tap 1, `run_test` in between, value through tap 2. luna
        needs the idle pulses to load its data register from RUN-TEST-IDLE; the
        fixed design needs them for its handshake. Neither is asked to work
        without them, because the host always sends them.
        """
        command = address | (int(write) << 15)
        await self.scan(1, command, 16, pad=pad)
        await self.idle(idle, rti1=1)
        result = await self.scan(2, value, 32, pad=pad)
        if self.verbose:
            print(f"      {'write' if write else 'read '} {address:5} "
                  f"-> {result:#010x}")
        return result

    async def reset(self):
        """TEST-LOGIC-RESET, which is where the host measures the widths."""
        self.ctx.set(self.tap.rstn, 0)
        await self.idle(5)
        self.ctx.set(self.tap.rstn, 1)
        await self.idle(4)


class Run:
    """One simulation of one design at one ratio and phase."""

    def __init__(self, which, *, sync_hz, jtck_hz=JTCK_HZ, phase=0.13,
                 sample="rise", verbose=False):
        self.which = which
        self.sync_hz = sync_hz
        self.jtck_hz = jtck_hz
        self.phase = phase
        self.sample = sample
        self.verbose = verbose
        self.tap, self.fragment, self.design = build(which)

    def go(self, script):
        results = {"design": self.design}

        async def testbench(ctx):
            ctx.set(self.tap.tck, 1)
            # Offset TCK inside the `sync` period, so no edge of one lands on an
            # edge of the other and the simulator never has to order two events
            # that the hardware would not present together.
            await ctx.delay(self.phase / self.sync_hz)
            chain = Chain(ctx, self.tap, jtck_hz=self.jtck_hz,
                          sample=self.sample, verbose=self.verbose)
            await chain.reset()
            await script(chain, ctx, results)

        sim = Simulator(self.fragment)
        sim.add_clock(1 / self.sync_hz, domain="sync")
        sim.add_testbench(testbench)
        sim.run()
        return results

    def label(self):
        return (f"{self.which} at sync {self.sync_hz/1e6:g} / TCK "
                f"{self.jtck_hz/1e6:g} = {self.sync_hz/self.jtck_hz:.2f}x, "
                f"phase {self.phase:g}, TDO at {self.sample}")


#
# The scripts. Each one is a sequence of transactions; the checks read the
# results out afterwards, so the same script can be run against any design.
#

async def basic_script(chain, ctx, results):
    """Everything a measurement depends on: a constant, a write, a miss."""
    results["ident"] = await chain.transact(REG_IDENT)
    await chain.transact(REG_SCRATCH, 0x12345678, write=True)
    results["scratch"] = await chain.transact(REG_SCRATCH)
    results["missing"] = await chain.transact(REG_MISSING)
    results["ident_again"] = await chain.transact(REG_IDENT)


async def widths_script(chain, ctx, results):
    """What the host counts to discover the register shape, after a reset."""
    results["data_ones"] = bin(await chain.scan(2, 0, 48)).count("1")
    results["command_ones"] = bin(await chain.scan(1, 0, 48)).count("1")


async def spread_script(chain, ctx, results):
    """Every address in the map, including the ones #204 lost one of."""
    read = {address: await chain.transact(address) for address in REG_SPREAD}
    read[REG_TOP] = await chain.transact(REG_TOP)
    results["spread"] = read


async def stream_script(chain, ctx, results):
    """Fixed stimulus whose every returned word is compared between designs."""
    words = []
    words.append(await chain.scan(2, 0, 40))
    words.append(await chain.scan(1, 0, 40))
    words.append(await chain.transact(REG_IDENT))
    words.append(await chain.transact(REG_SCRATCH, 0xA5A5F00F, write=True))
    words.append(await chain.transact(REG_SCRATCH))
    words.append(await chain.transact(REG_MISSING))
    words.append(await chain.transact(REG_TOP))
    # The command tap answers too, and the host reads those bits whether or not
    # it uses them -- so they are part of the contract being preserved.
    words.append(await chain.scan(1, 0, 16))
    results["words"] = words


async def coherency_script(chain, ctx, results):
    """Read the every-cycle register repeatedly; a torn word is neither value."""
    results["samples"] = [await chain.transact(REG_TOGGLE) for _ in range(6)]


def gap_script_for(gap):
    """Read the ident with `gap` TCK between the two scans and no padding.

    The command shift ends, then `gap` idle edges, then CAPTURE-DR and SHIFT-DR
    -- so the snapshot handshake is given exactly `gap + 2` edges. Priming with
    another address first makes a stale answer recognisable as stale rather
    than as noise.
    """
    async def script(chain, ctx, results):
        await chain.transact(REG_TOP)
        results["ident"] = await chain.transact(REG_IDENT, idle=gap, pad=0)
        results["edges"] = gap + 2
    return script


async def decode_error_script(chain, ctx, results):
    """The sticky flag that makes `0xDEADBEEF` distinguishable from data."""
    await chain.transact(REG_IDENT)
    results["clean"] = ctx.get(results["design"].decode_error)
    await chain.transact(REG_MISSING)
    results["after_miss"] = ctx.get(results["design"].decode_error)


def check_transport(checks, run, script=basic_script, expect_ok=True):
    """Run one configuration and check the three values a measurement needs."""
    results = run.go(script)
    ok = (results["ident"] == IDENT
          and results["scratch"] == 0x12345678
          and results["missing"] == DEFAULT_READ
          and results["ident_again"] == IDENT)
    detail = (f"ident {results['ident']:#010x}, scratch "
              f"{results['scratch']:#010x}, missing {results['missing']:#010x}")
    if expect_ok:
        checks.check(f"reads back through {run.label()}", ok, detail)
    else:
        checks.check(f"MISREADS, as it must, through {run.label()}", not ok,
                     f"it read cleanly: {detail}", measurement=detail)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Prove the JTAG register readback survives any clock ratio.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every transaction")
    parser.add_argument("--soak", action="store_true",
                        help="more ratios and more TCK phases")
    args = parser.parse_args()

    checks = Checks(emit)
    emit("\nJTAG register transport (#204)\n")

    ratios = SOAK_SYNC_HZ if args.soak else RATIO_SYNC_HZ
    phases = SOAK_PHASES if args.soak else PHASES

    #
    # 1. The model reproduces the board, so a failure below means something.
    #
    emit("  the reference, at the ratio the board ran")
    reference = Run("luna", sync_hz=CALIBRATION_SYNC_HZ, jtck_hz=APOLLO_TCK_HZ,
                    verbose=args.verbose)
    check_transport(checks, reference)

    widths = Run("luna", sync_hz=CALIBRATION_SYNC_HZ, jtck_hz=APOLLO_TCK_HZ
                 ).go(widths_script)
    checks.check("the reference presents the widths the host measures",
                 widths["data_ones"] == 33 and widths["command_ones"] == 17,
                 f"{widths['data_ones']} and {widths['command_ones']} ones",
                 measurement=f"{widths['data_ones']} data, "
                             f"{widths['command_ones']} command")

    #
    # 2. The negative controls. A harness that cannot fail proves nothing.
    #
    emit("  where the reference stops working, against TCK at its constraint")
    boundary = {}
    for sync_hz in BOUNDARY_SYNC_HZ:
        results = Run("luna", sync_hz=sync_hz).go(basic_script)
        boundary[sync_hz / JTCK_HZ] = results["ident"] == IDENT
    slipping = [f"{ratio:.2f}x" for ratio, ok in boundary.items() if not ok]
    working = [ratio for ratio, ok in boundary.items() if ok]
    checks.check("the reference misreads below a ratio, and #204 is real",
                 bool(slipping),
                 "it read cleanly at every ratio down to "
                 f"{min(boundary):.2f}x -- then this harness cannot see the "
                 "fault it was written for",
                 measurement=f"slips at {', '.join(slipping)}; clean from "
                             f"{min(working):.2f}x up")
    checks.check("and that boundary is inside the range `clocks.py` reaches",
                 max(float(r.rstrip('x')) for r in slipping) > 1.0,
                 "only 1:1 slips, which no build would choose anyway")

    emit("  the fix, deliberately slipped by one TCK slot")
    check_transport(checks, Run("slipped", sync_hz=CALIBRATION_SYNC_HZ,
                                jtck_hz=APOLLO_TCK_HZ, verbose=args.verbose),
                    expect_ok=False)

    #
    # 3. The fix, at every ratio the new clock topology can produce, under both
    #    readings of when the host takes a bit.
    #
    emit("  the fix, across the ratios `clocks.py` makes reachable")
    for sync_hz in ratios:
        for phase in phases:
            for sample in ("rise", "fall"):
                check_transport(checks, Run("fixed", sync_hz=sync_hz,
                                            phase=phase, sample=sample,
                                            verbose=args.verbose))
    check_transport(checks, Run("fixed", sync_hz=CALIBRATION_SYNC_HZ,
                                jtck_hz=APOLLO_TCK_HZ, verbose=args.verbose))

    # Below anything `clocks.py` can produce, to show there is no floor rather
    # than a lower one: `sync` at half TCK, which no build would choose and
    # which a design with a ratio requirement could not survive.
    check_transport(checks, Run("fixed", sync_hz=JTCK_HZ / 2,
                                verbose=args.verbose))

    #
    # 4. The host does not change, because the bits do not.
    #
    emit("  the bits the host receives, against the reference")
    golden = Run("luna", sync_hz=CALIBRATION_SYNC_HZ, jtck_hz=APOLLO_TCK_HZ
                 ).go(stream_script)["words"]
    for sync_hz in ratios:
        words = Run("fixed", sync_hz=sync_hz).go(stream_script)["words"]
        mismatch = [(index, f"{a:#x}", f"{b:#x}")
                    for index, (a, b) in enumerate(zip(golden, words)) if a != b]
        checks.check(f"identical to the reference at {sync_hz/JTCK_HZ:.2f}x",
                     not mismatch, f"differs at {mismatch}")

    slipped = Run("slipped", sync_hz=CALIBRATION_SYNC_HZ,
                  jtck_hz=APOLLO_TCK_HZ).go(stream_script)["words"]
    checks.check("and that comparison can see a one-edge slip",
                 slipped != golden,
                 "the misaligned build produced identical bits")

    widths = Run("fixed", sync_hz=JTCK_HZ).go(widths_script)
    checks.check("the fix presents the same widths at 1:1",
                 widths["data_ones"] == 33 and widths["command_ones"] == 17,
                 f"{widths['data_ones']} and {widths['command_ones']} ones")

    #
    # 5. The other two failures in #204.
    #
    emit("  addresses, and what an address that is not there answers")
    spread = Run("fixed", sync_hz=JTCK_HZ, verbose=args.verbose
                 ).go(spread_script)
    expected = {**SPREAD_VALUES, REG_TOP: TOP_VALUE}
    wrong = {address: f"{value:#010x}"
             for address, value in spread["spread"].items()
             if value != expected[address]}
    checks.check("every address added is readable, 28 and 29 alike",
                 not wrong, f"wrong: {wrong}")

    for what, arguments in (
            ("out of range", dict(address=1 << 15, read=0)),
            ("already in use", dict(address=REG_IDENT, read=0)),
            ("too wide for the register", dict(address=40,
                                               read=Signal(33))),
    ):
        design = JTAGRegisterInterface(simulate=True)
        # Never elaborated -- only its bookkeeping is under test here.
        design._MustUse__used = True
        design.add_read_only_register(REG_IDENT, read=IDENT)
        try:
            design.add_read_only_register(**arguments)
            raised = False
        except ValueError:
            raised = True
        checks.check(f"an address {what} is refused when it is added", raised,
                     "it was accepted, and would have gone missing silently")

    missing = Run("fixed", sync_hz=JTCK_HZ).go(basic_script)
    checks.check("an address that is not implemented reads the default",
                 missing["missing"] == DEFAULT_READ,
                 f"{missing['missing']:#010x}")

    # `0xDEADBEEF` is a legal value for a counter to hold, so the answer alone
    # cannot say "no such register". The sticky flag can, and an applet can put
    # it in its status word.
    flagged = Run("fixed", sync_hz=JTCK_HZ).go(decode_error_script)
    checks.check("an undecoded address is reported, not only answered",
                 flagged["clean"] == 0 and flagged["after_miss"] == 1,
                 f"clear {flagged['clean']}, after a miss "
                 f"{flagged['after_miss']}")

    #
    # 6. The requirement that replaces the ratio: edges, not a frequency.
    #
    emit("  the handshake, measured rather than claimed")
    needed = None
    stale = []
    for gap in range(0, 12):
        result = Run("fixed", sync_hz=JTCK_HZ).go(gap_script_for(gap))
        if result["ident"] != IDENT:
            stale.append((result["edges"], result["ident"]))
            needed = None       # it must stay right once it is right
        elif needed is None:
            needed = result["edges"]
    checks.check("the value is right after a bounded number of TCK edges",
                 needed is not None and needed <= 8,
                 f"took {needed} edges at 1:1, or never settled",
                 measurement=f"{needed} TCK edges at 1:1 between the command "
                             f"shift and CAPTURE-DR; Apollo's `run_test` alone "
                             f"spends {IDLE_TCK}")

    # A host that did not wait gets the PREVIOUS command's answer. Wrong, but a
    # whole value and always the same one -- which is what "no torn word" means
    # when the alternative is half of one count and half of the next.
    torn = [f"{value:#010x}" for _, value in stale
            if value not in (IDENT, TOP_VALUE)]
    checks.check("a host that does not wait reads the previous value, not a "
                 "torn one", not torn,
                 f"{torn} is neither the answer nor the one before it",
                 measurement=f"{len(stale)} too-early reads, all whole")

    #
    # 7. A value that moves every `sync` cycle still crosses whole.
    #
    coherency = Run("fixed", sync_hz=JTCK_HZ).go(coherency_script)
    torn = [f"{value:#010x}" for value in coherency["samples"]
            if value not in (0, 0xFFFFFFFF)]
    checks.check("a register changing every cycle never reads back torn",
                 not torn, f"torn: {torn}",
                 measurement=f"{len(coherency['samples'])} reads at 1:1")

    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
