#!/usr/bin/env python3
#
# Cable orientation, and naming the cause of a Type-C interrupt. See awtoau/cynthion-workspace#151.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the orientation sweep and the interrupt decode in `src/fusb302.rs`.

    python3 scripts/soc_typec_sim.py
    python3 scripts/soc_typec_sim.py -v            # every CSR access
    python3 scripts/soc_typec_sim.py --firmware DIR

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## The two faults this is about

**Only CC1 was ever routed to the comparator.** `SWITCHES0.MEAS_CC1` was written
once at configuration and never moved, and no `MEAS_CC2` constant existed, so a
cable whose CC landed on CC2 read exactly like no cable at all -- `nothing on CC`,
which is what the TARGET port reported on the board with VBUS present. The
comparator is one comparator, so seeing both pins means moving the select and
reading twice.

**The three interrupt registers were read and thrown away.** They have to be read
-- they are read-to-clear and the `int` line is their OR -- and the read is the
only moment their contents exist anywhere. Discarding them left a serviced
interrupt that nothing could name.

## What is checked here, and what each check is evidence for

**Sections 1-3 run the real gateware** -- `i2c_master.I2CMaster` and
`i2c_mux.I2CBusMux` wired as `vexii_hello_soc.py` wires them -- against a
FUSB302B model written here in Python that reacts only to edges on SCL and SDA.
The model is a *part*, not a mirror of the driver: its `STATUS0` answers with
whichever CC pin the measure select currently points at, its interrupt registers
clear when read, and its `int` output is the OR of them. So a driver that never
moves the select genuinely cannot see CC2, in the model as on the board.

  1. **Both bands, from one comparator.** The sweep is driven against a model
     with the cable on CC1, on CC2, on both, and on neither. The OLD sequence --
     `MEAS_CC1` only -- is run against the same models first, and the check
     asserts it CANNOT distinguish a CC2 cable from an empty port. A check that
     only shows the fix working is a check that would have passed before it.

  2. **The sweep changes nothing electrically.** `SWITCHES0` also holds the pull
     enables, and one of these controllers is on AUX, which carries the console.
     Every value the sweep writes is inspected: the pull-ups a source role set
     stay set, `PDWN1`/`PDWN2` stay clear throughout, one measure bit is selected
     at a time, and the register ends holding exactly what it held before. With
     `CONTROL2.TOGGLE` set -- the part's own logic driving that register -- the
     sweep must not write it at all.

  3. **The interrupt registers are read once and their contents survive.** A
     second read gives zero, which is the point: whatever the first read
     returned is the only record of why the line asserted, and the model's `int`
     output drops only once all three have been read.

**Section 4 is structural, against the firmware source.** The sections above show
what the arrangement must be; they cannot show the Rust has it, because the Rust
does not run here. So the last section reads `fusb302.rs`, `typec.rs`, `board.rs`
and `events.rs` and asserts the properties the sections above assume: that
`MEAS_CC2` exists and is bit 3, that the sweep restores what it read, that the
bit-name tables match the datasheet name for name, that the cause reaches the
deferred event ring rather than a `writeln!` in a handler, and that **nothing
anywhere enables `PDWN1`/`PDWN2`** -- the one electrically visible change the
module comment reserves for someone with the board in hand.

It also asserts the docstrings still say the orientation is **unvalidated on
hardware**, which it is: nothing here or in QEMU can insert a cable one way round
and then the other, and that is the only test that can tell a correct reading
from a plausible one.

`--firmware DIR` points section 4 at another tree. Run it against a checkout from
before this change and the section fails, which is how a structural check earns
its place.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware" / "cynthion-soc" / "src"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))

from devlog import emit  # noqa: E402

from i2c_mux import BUS_AUX_C, BUS_TARGET_C

# The gateware harness, the model-slave base and the reporter are shared with
# soc_i2c_owner_sim.py rather than copied, for the reason that file gives about
# sharing with soc_board_sim.py: a second copy of a device model drifts from the
# first, and the two then disagree while both report success.
from sim_check_harness import Checks
from soc_board_sim import (Bus, CR_ACK, CR_RD, CR_STA, CR_STO, CR_WR,
                           CR_SR, SR_RXACK)
from soc_i2c_owner_sim import (Controller, RegisterSlave, buses,
                               FUSB302_ADDRESS, make_sim, strip_comments)

# FUSB302B registers this cares about. Addresses are from
# docs/chips/fusb302b-type-c.md, which cites two independent open implementations
# agreeing.
REG_DEVICE_ID = 0x01
REG_SWITCHES0 = 0x02
REG_CONTROL2 = 0x08
REG_INTERRUPTA = 0x3E
REG_INTERRUPTB = 0x3F
REG_STATUS1A = 0x3D
REG_STATUS0 = 0x40
REG_INTERRUPT = 0x42

DEVICE_ID_FUSB302B = 0x91

# SWITCHES0 fields.
PDWN1, PDWN2 = 1 << 0, 1 << 1
MEAS_CC1, MEAS_CC2 = 1 << 2, 1 << 3
PU_EN1, PU_EN2 = 1 << 6, 1 << 7

# CONTROL2.TOGGLE: the part's autonomous CC polling is running, and while it is,
# that state machine -- not this driver -- owns SWITCHES0.
CONTROL2_TOGGLE = 1 << 0

# STATUS0 fields.
STATUS0_BC_LVL = 0b11
STATUS0_VBUSOK = 1 << 7

# The CC bands, as BC_LVL reports them. Named so a check reads as the situation
# it is about rather than as a number.
BAND_NONE, BAND_USB, BAND_1A5, BAND_3A = 0, 1, 2, 3

# INTERRUPT, INTERRUPTA and INTERRUPTB bit names, bit 0 first, from the FUSB302B
# datasheet. Written out here so section 4 can compare the firmware's tables
# against a list that was typed from the datasheet rather than from the firmware.
INTERRUPT_NAMES = ["I_BC_LVL", "I_COLLISION", "I_WAKE", "I_ALERT", "I_CRC_CHK",
                   "I_COMP_CHNG", "I_ACTIVITY", "I_VBUSOK"]
INTERRUPTA_NAMES = ["I_HARDRST", "I_SOFTRST", "I_TXSENT", "I_HARDSENT",
                    "I_RETRYFAIL", "I_SOFTFAIL", "I_TOGDONE", "I_OCP_TEMP"]
INTERRUPTB_NAMES = ["I_GCRCSENT"]


class ModelFusb302B(RegisterSlave):
    """A FUSB302B with the three behaviours the orientation work depends on.

    **One comparator, pointed by `SWITCHES0`.** `STATUS0.BC_LVL` answers about
    whichever CC pin the measure select currently reaches, and about nothing when
    it reaches neither. This is the whole reason a driver that never moves the
    select is blind to half the connector, and the model has to have it or the
    check would be about a part that does not exist.

    **`SWITCHES0` is a real register**, so a write that meant to change the
    measure select and cleared a pull-up is visible as a value in `switches0_writes`
    rather than as a silent electrical change nobody can see in simulation.

    **The interrupt registers are read-to-clear**, and `int_asserted` is their OR,
    exactly as the part's pin is. A model that let them be read twice would make
    "carry the values out of the read" look like a nicety.
    """

    def __init__(self, cc1=BAND_NONE, cc2=BAND_NONE, vbus=False,
                 switches0=MEAS_CC1, control2=0, togss=0,
                 interrupt=0, interrupta=0, interruptb=0):
        super().__init__(FUSB302_ADDRESS)
        self.cc1 = cc1
        self.cc2 = cc2
        self.vbus = vbus
        self.switches0 = switches0
        self.control2 = control2
        self.togss = togss
        self.interrupt = interrupt
        self.interrupta = interrupta
        self.interruptb = interruptb
        #: Every value written to SWITCHES0, in order. Section 2 reads this.
        self.switches0_writes = []
        #: How many times each register was READ, so "read once" is checkable.
        self.reads = {}

    @property
    def int_asserted(self):
        """The `int` pin: the OR of the three read-to-clear registers."""
        return bool(self.interrupt or self.interrupta or self.interruptb)

    def status0(self):
        measuring = self.switches0 & (MEAS_CC1 | MEAS_CC2)
        if measuring == MEAS_CC1:
            band = self.cc1
        elif measuring == MEAS_CC2:
            band = self.cc2
        elif measuring == 0:
            # Nothing routed to the comparator: it reports nothing, which is not
            # the same as the pins being idle.
            band = BAND_NONE
        else:
            # Both selected. The datasheet permits it and the comparator then
            # sees the higher of the two, which is a reading that can be
            # attributed to neither pin.
            band = max(self.cc1, self.cc2)
        return (STATUS0_VBUSOK if self.vbus else 0) | (band & STATUS0_BC_LVL)

    def register(self, address):
        self.reads[address] = self.reads.get(address, 0) + 1
        if address == REG_DEVICE_ID:
            return DEVICE_ID_FUSB302B
        if address == REG_SWITCHES0:
            return self.switches0
        if address == REG_CONTROL2:
            return self.control2
        if address == REG_STATUS0:
            return self.status0()
        if address == REG_STATUS1A:
            return (self.togss & 0b111) << 3
        if address in (REG_INTERRUPT, REG_INTERRUPTA, REG_INTERRUPTB):
            # Read-to-clear. The value is handed over exactly once; after this
            # the reason the line asserted exists only in the reader's hand.
            name = {REG_INTERRUPT: "interrupt", REG_INTERRUPTA: "interrupta",
                    REG_INTERRUPTB: "interruptb"}[address]
            value = getattr(self, name)
            setattr(self, name, 0)
            return value
        return 0xff

    def ack_write(self, index, byte):
        # Index 0 is the register pointer, which the base class latches; index 1
        # is the data byte of a register write.
        if index == 1:
            if self._pointer == REG_SWITCHES0:
                self.switches0 = byte
                self.switches0_writes.append(byte)
            elif self._pointer == REG_CONTROL2:
                self.control2 = byte
        return super().ack_write(index, byte)


class Driver(Controller):
    """The firmware's `Bus`, plus the register write the orientation sweep needs.

    `Controller` covers reads and SMBus Send Byte because that is what the
    ownership checks needed. A sweep writes, so the write is here, in the same
    shape `src/bus/i2c.rs` has it: address, pointer, data, STOP.
    """

    async def write_register(self, bus, address, register, value):
        await self.select(bus)
        status = await self._command(CR_STA | CR_WR, address << 1)
        if status & SR_RXACK:
            await self._release()
            return "nack-address"
        status = await self._command(CR_WR, register)
        if status & SR_RXACK:
            await self._release()
            return "nack-register"
        status = await self._command(CR_WR | CR_STO, value)
        if status & SR_RXACK:
            return "nack-data"
        return None

    async def read_register(self, bus, address, register):
        data, error = await self.read_registers(bus, address, register, 1)
        return None if data is None else data[0], error


async def read_cc1_only(driver, bus):
    """The OLD reading: whatever the comparator happens to be pointed at.

    Not a straw man -- it is what `state()` did, and what it did was correct for
    every port whose cable landed on CC1. It is here so the check that the sweep
    sees CC2 can be stated as a difference rather than as an assertion in the air.
    """
    status0, _ = await driver.read_register(bus, FUSB302_ADDRESS, REG_STATUS0)
    return None if status0 is None else status0 & STATUS0_BC_LVL


async def sweep(driver, bus):
    """The NEW reading: `state()`'s sequence, byte for byte.

    Returns `(cc1, cc2)`, or `(None, None)` when `CONTROL2.TOGGLE` says the part's
    own logic owns SWITCHES0 and the sweep must keep its hands off.
    """
    control2, _ = await driver.read_register(bus, FUSB302_ADDRESS, REG_CONTROL2)
    switches0, _ = await driver.read_register(bus, FUSB302_ADDRESS, REG_SWITCHES0)
    if control2 & CONTROL2_TOGGLE:
        return None, None

    base = switches0 & ~(MEAS_CC1 | MEAS_CC2)
    await driver.write_register(bus, FUSB302_ADDRESS, REG_SWITCHES0,
                                base | MEAS_CC1)
    first, _ = await driver.read_register(bus, FUSB302_ADDRESS, REG_STATUS0)
    await driver.write_register(bus, FUSB302_ADDRESS, REG_SWITCHES0,
                                base | MEAS_CC2)
    second, _ = await driver.read_register(bus, FUSB302_ADDRESS, REG_STATUS0)
    # Back to what was found, not to a constant: a caller that had neither select
    # on gets neither back.
    await driver.write_register(bus, FUSB302_ADDRESS, REG_SWITCHES0, switches0)
    return first & STATUS0_BC_LVL, second & STATUS0_BC_LVL


def orientation(cc1, cc2, togss=0):
    """`State::orientation`, in Python, so a check can name the answer.

    A restatement of the Rust, and therefore evidence about the sequence rather
    than about the firmware -- section 4 is what ties the two together. What it
    IS evidence for is that the sequence above yields enough to answer at all,
    which the CC1-only read does not.
    """
    if togss in (1, 3):
        return "cc1"
    if togss in (2, 4):
        return "cc2"
    if cc1 is None or cc2 is None:
        return "?"
    if cc1 and cc2:
        return "both"
    if cc1:
        return "cc1"
    if cc2:
        return "cc2"
    return "none"


# --- 1. both bands, from one comparator --------------------------------------

CABLES = [
    # (name, cc1 band, cc2 band, orientation the sweep should reach)
    ("a cable on CC1", BAND_USB, BAND_NONE, "cc1"),
    ("a cable on CC2", BAND_NONE, BAND_USB, "cc2"),
    ("nothing attached", BAND_NONE, BAND_NONE, "none"),
    ("an accessory on both pins", BAND_1A5, BAND_1A5, "both"),
]

# One scenario per branch of `orientation`, and one more for each: a 3 A source
# on CC2 takes the SAME path through the sequence as a cable on CC2 -- select
# moves to CC2, the comparator answers about CC2 -- and differs only in the
# BC_LVL value it reports. That is a value sweep, and a value sweep is a soak:
# each entry here is a whole simulation, 0.6 s of bit-banged I2C, for a path the
# entry above it already walked.
SOAK_CABLES = CABLES[:2] + [
    ("a 3 A source on CC2", BAND_NONE, BAND_3A, "cc2"),
] + CABLES[2:]

# Which CC2 scenario section 1's blindness pair reads. The check asserts the old
# read cannot distinguish it from an empty port, which is true of any band on
# CC2; the soak run keeps the 3 A source this section was written against.
BLINDNESS_CC2 = "a cable on CC2"
SOAK_BLINDNESS_CC2 = "a 3 A source on CC2"


def run_orientation_checks(checks, verbose):
    """Runs every cable scenario, and returns what each one read.

    The answers are returned rather than dropped because the blindness section
    below asks a question about two of them. It used to re-simulate those two --
    same models, same driver, same two reads -- which was 1.2 s spent producing
    numbers this loop had already produced.
    """
    answers = {}
    for name, cc1, cc2, expected in CABLES:
        target = ModelFusb302B(cc1=cc1, cc2=cc2, vbus=True)
        dut, build = make_sim(buses(target=target), verbose)
        seen = {}

        async def testbench(ctx, target=target, seen=seen):
            driver = Driver(ctx, dut, verbose)
            await driver.setup()
            seen["old"] = await read_cc1_only(driver, BUS_TARGET_C)
            seen["new"] = await sweep(driver, BUS_TARGET_C)

        build(testbench).run()
        answers[name] = seen
        swept = seen.get("new", (None, None))
        checks.check(
            f"the sweep reads both bands with {name}",
            swept == (cc1, cc2),
            f"the sweep read {swept!r}, expected {(cc1, cc2)!r}. One comparator "
            f"sees one pin at a time, so both numbers exist only if the measure "
            f"select was moved and STATUS0 read again.")
        checks.check(
            f"orientation with {name} comes out as {expected}",
            orientation(*swept) == expected,
            f"derived {orientation(*swept)!r} from bands {swept!r}, expected "
            f"{expected!r}.")

        if cc2 and not cc1:
            checks.check(
                f"the CC1-only read is blind to {name}",
                seen.get("old") == BAND_NONE,
                f"the old sequence read band {seen.get('old')!r}. It must read "
                f"NOTHING here: that is the fault -- `vbus present  nothing on "
                f"CC` on a port with a cable in it -- and a check that cannot "
                f"reproduce it is not evidence the sweep fixed anything.")

    return answers


def run_blindness_check(checks, swept):
    """The old read gives the same answer to two different connectors.

    `swept` is what section 1 read, by scenario name. Two of its runs are the
    two connectors this compares, so the comparison is free.
    """
    answers = {"empty": swept["nothing attached"],
               "cc2": swept[BLINDNESS_CC2]}

    checks.check(
        "the CC1-only read cannot tell an empty port from a cable on CC2",
        answers["empty"]["old"] == answers["cc2"]["old"] == BAND_NONE,
        f"empty read {answers['empty']['old']!r} and a CC2 cable read "
        f"{answers['cc2']['old']!r}. These two MUST be identical -- the bug is "
        f"that they were, and this check is what makes the next one mean "
        f"something.")
    checks.check(
        "the sweep tells them apart",
        answers["empty"]["new"] != answers["cc2"]["new"]
        and orientation(*answers["cc2"]["new"]) == "cc2",
        f"empty swept to {answers['empty']['new']!r} and a CC2 cable to "
        f"{answers['cc2']['new']!r}; they must differ, and the second must "
        f"resolve to cc2.")


# --- 2. the sweep changes nothing electrically -------------------------------

def run_electrical_checks(checks, verbose):
    # A TARGET port in the source role: PU_EN1/PU_EN2 set, pull-downs clear. This
    # is the state `fusb302::source_target` leaves, and the state in which a
    # careless sweep would drop the port's Rp mid-reading.
    target = ModelFusb302B(cc1=BAND_NONE, cc2=BAND_1A5,
                           switches0=PU_EN1 | PU_EN2, vbus=True)
    dut, build = make_sim(buses(target=target), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Driver(ctx, dut, verbose)
        await driver.setup()
        seen["bands"] = await sweep(driver, BUS_TARGET_C)

    build(testbench).run()

    written = target.switches0_writes
    checks.check(
        "the sweep never clears the pull-ups a source role set",
        bool(written) and all(value & (PU_EN1 | PU_EN2) == (PU_EN1 | PU_EN2)
                              for value in written),
        f"SWITCHES0 was written {[hex(v) for v in written]}. Dropping PU_EN "
        f"even for one transaction takes the port's Rp away while something is "
        f"plugged into it, which is an electrical change made by a command that "
        f"only meant to look.")
    checks.check(
        "the sweep never sets PDWN1 or PDWN2",
        all(not value & (PDWN1 | PDWN2) for value in written),
        f"SWITCHES0 was written {[hex(v) for v in written]}. Presenting Rd is "
        f"the one electrically visible change the module comment reserves for "
        f"someone with the board in hand, and one of these controllers is on "
        f"AUX, which carries the console.")
    checks.check(
        "one measure select at a time",
        [value & (MEAS_CC1 | MEAS_CC2) for value in written]
        == [MEAS_CC1, MEAS_CC2, 0],
        f"the measure bits went "
        f"{[hex(v & (MEAS_CC1 | MEAS_CC2)) for v in written]}; expected CC1, "
        f"then CC2, then back to what was found. Both bits at once gives the OR "
        f"of the pins, which is a band attributable to neither.")
    checks.check(
        "SWITCHES0 ends holding exactly what it held before",
        target.switches0 == PU_EN1 | PU_EN2,
        f"SWITCHES0 was left {target.switches0:#04x}, expected "
        f"{PU_EN1 | PU_EN2:#04x}. The register is read first and put back byte "
        f"for byte; restoring a constant instead would turn a reading into a "
        f"configuration change.")
    checks.check(
        "a source-role port still reports the band on its CC2 pin",
        seen.get("bands") == (BAND_NONE, BAND_1A5),
        f"the sweep read {seen.get('bands')!r}, expected "
        f"{(BAND_NONE, BAND_1A5)!r}. Preserving the pulls must not cost the "
        f"reading.")

    # ...and with the part's own toggle logic running, the sweep keeps out.
    toggling = ModelFusb302B(cc1=BAND_NONE, cc2=BAND_1A5, togss=2,
                             switches0=PU_EN1 | PU_EN2,
                             control2=CONTROL2_TOGGLE, vbus=True)
    dut, build = make_sim(buses(target=toggling), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Driver(ctx, dut, verbose)
        await driver.setup()
        seen["bands"] = await sweep(driver, BUS_TARGET_C)

    build(testbench).run()

    checks.check(
        "with CONTROL2.TOGGLE set the sweep does not write SWITCHES0 at all",
        toggling.switches0_writes == [],
        f"SWITCHES0 was written {[hex(v) for v in toggling.switches0_writes]} "
        f"while the part's autonomous polling owned it. That is a second writer "
        f"of a register a state machine is using, and the damage is not a bad "
        f"reading but a disturbed search.")
    checks.check(
        "a skipped sweep reports no bands rather than zero bands",
        seen.get("bands") == (None, None)
        and orientation(*seen.get("bands", (0, 0)), togss=0) == "?",
        f"the sweep returned {seen.get('bands')!r}. A pin that was not measured "
        f"and a pin measuring nothing are different facts; reporting the second "
        f"for the first is `nothing on CC` about a port nobody looked at.")
    checks.check(
        "TOGSS answers the orientation the sweep did not",
        orientation(None, None, togss=toggling.togss) == "cc2",
        "with its own polling settled on CC2 the part has already answered, and "
        "that answer is better than two bands: it is what the state machine "
        "resolved rather than what a comparator saw.")


# --- 3. the interrupt registers are read once, and their contents survive -----

def run_interrupt_checks(checks, verbose):
    # I_BC_LVL and I_VBUSOK: the two events `MASK` leaves unmasked, so this is
    # the pair a real attach produces.
    latched = (1 << 0) | (1 << 7)
    aux = ModelFusb302B(cc1=BAND_USB, vbus=True, interrupt=latched,
                        interrupta=1 << 6, interruptb=1 << 0)
    dut, build = make_sim(buses(aux=aux), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Driver(ctx, dut, verbose)
        await driver.setup()
        seen["asserted_before"] = aux.int_asserted
        # `fusb302::clear`, in order. All three, always -- the pin is their OR.
        first = []
        for register in (REG_INTERRUPT, REG_INTERRUPTA, REG_INTERRUPTB):
            value, _ = await driver.read_register(BUS_AUX_C, FUSB302_ADDRESS,
                                                  register)
            first.append(value)
            seen[f"asserted_after_{register:#04x}"] = aux.int_asserted
        seen["first"] = first

        second = []
        for register in (REG_INTERRUPT, REG_INTERRUPTA, REG_INTERRUPTB):
            value, _ = await driver.read_register(BUS_AUX_C, FUSB302_ADDRESS,
                                                  register)
            second.append(value)
        seen["second"] = second

    build(testbench).run()

    checks.check(
        "the three registers hand over what latched",
        seen.get("first") == [latched, 1 << 6, 1 << 0],
        f"the reads returned {seen.get('first')!r}, expected "
        f"{[latched, 1 << 6, 1 << 0]!r}.")
    checks.check(
        "reading them a second time gives nothing",
        seen.get("second") == [0, 0, 0],
        f"the second pass returned {seen.get('second')!r}. These are "
        f"read-to-clear: the first read is the ONLY moment the reason exists, "
        f"which is why discarding the values left an interrupt nobody could "
        f"name and why a second reader would take the event away from the "
        f"service path.")
    checks.check(
        "the `int` line drops only when all three have been read",
        seen.get("asserted_before") is True
        and seen.get(f"asserted_after_{REG_INTERRUPT:#04x}") is True
        and seen.get(f"asserted_after_{REG_INTERRUPTB:#04x}") is False,
        f"the pin went {seen.get('asserted_before')!r} -> "
        f"{seen.get(f'asserted_after_{REG_INTERRUPT:#04x}')!r} -> "
        f"{seen.get(f'asserted_after_{REG_INTERRUPTB:#04x}')!r}. Leaving one "
        f"register set leaves a level-sensitive line high, and the source "
        f"re-fires the moment it is re-enabled.")

    # A serviced interrupt with nothing latched: not an error, and the decode has
    # to have something to say about it.
    quiet = ModelFusb302B()
    dut, build = make_sim(buses(aux=quiet), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Driver(ctx, dut, verbose)
        await driver.setup()
        values = []
        for register in (REG_INTERRUPT, REG_INTERRUPTA, REG_INTERRUPTB):
            value, _ = await driver.read_register(BUS_AUX_C, FUSB302_ADDRESS,
                                                  register)
            values.append(value)
        seen["values"] = values

    build(testbench).run()

    checks.check(
        "a clear with nothing latched is readable, not an error",
        seen.get("values") == [0, 0, 0],
        f"reading a quiet part gave {seen.get('values')!r}. A masked event still "
        f"latches without raising the pin, and the part can clear its own "
        f"condition before the read, so all-zero has to be a thing the decode "
        f"can say out loud rather than a blank where a cause should be.")


# --- 4. the firmware has the structure the sections above require ------------

def rust_string_array(text, name):
    """The `&str` literals of a `const NAME: [&str; N] = [...]` declaration."""
    found = re.search(r"const " + name + r":[^=]*=\s*\[(.*?)\];", text, re.S)
    return re.findall(r'"([^"]*)"', found.group(1)) if found else []


def function_body(text, signature):
    """One `fn`, from its signature to its closing brace, by counting braces.

    Not a regex to the first line-starting `}`: the functions checked below open
    blocks of their own, and a pattern that stopped at the first one would read
    half a body and then assert about what it could not see -- a structural check
    that fails for a reason that has nothing to do with the code.

    Comments are already stripped by the caller, so every brace counted is code.
    """
    start = text.find(signature)
    if start < 0:
        return ""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def run_firmware_checks(checks, root):
    def read(name):
        path = root / name
        return strip_comments(path.read_text()) if path.exists() else ""

    fusb302 = read("fusb302.rs")
    typec = read("typec.rs")
    board = read("board.rs")
    events = read("events.rs")
    irq = read("irq.rs")
    # The docstring checks need the comments, which `strip_comments` removes.
    fusb302_full = (root / "fusb302.rs").read_text() if root.exists() else ""

    checks.check(
        "the firmware source is where this expects it",
        bool(fusb302) and bool(typec) and bool(events),
        f"no fusb302.rs / typec.rs / events.rs under {root}. A structural check "
        f"that reads nothing passes forever; it must say so instead.")

    # --- orientation ---------------------------------------------------------
    checks.check(
        "MEAS_CC2 exists and is bit 3",
        re.search(r"const SWITCHES0_MEASURE_CC2: u8 = 1 << 3;", fusb302)
        is not None,
        "no `const SWITCHES0_MEASURE_CC2: u8 = 1 << 3;` in fusb302.rs. Without "
        "it there is no way to point the comparator at the other pin, and the "
        "driver is blind to half of every connector.")

    state = function_body(fusb302, "pub fn state(")
    checks.check(
        "`state` moves the measure select and reads STATUS0 twice",
        state.count("REG_STATUS0") >= 2
        and "SWITCHES0_MEASURE_CC1" in state and "SWITCHES0_MEASURE_CC2" in state,
        f"`state` mentions REG_STATUS0 {state.count('REG_STATUS0')} time(s) and "
        f"the two measure selects "
        f"{'both' if 'SWITCHES0_MEASURE_CC2' in state else 'not both'}. One "
        f"comparator means two readings or one blind spot.")
    checks.check(
        "`state` restores the SWITCHES0 value it read",
        re.search(r"let switches0 = read\(bus, port, REG_SWITCHES0\)", state)
        is not None
        and re.search(r"write\(bus, port, REG_SWITCHES0, switches0\)", state)
        is not None,
        "`state` must read SWITCHES0 first and write that same value back. "
        "Restoring a constant would make a command that only looked at the port "
        "change what the port presents.")
    checks.check(
        "`state` keeps out while CONTROL2.TOGGLE is set",
        "CONTROL2_TOGGLE" in state,
        "`state` does not consult CONTROL2.TOGGLE. While the part's own polling "
        "runs it is driving SWITCHES0, and a sweep that writes underneath it is "
        "a second writer of a register a state machine is using.")

    checks.check(
        "`State` carries both bands, and each may be absent",
        re.search(r"pub cc1: Option<u8>", fusb302) is not None
        and re.search(r"pub cc2: Option<u8>", fusb302) is not None,
        "`State` must hold `cc1: Option<u8>` and `cc2: Option<u8>`. A plain `u8` "
        "cannot distinguish a pin measuring nothing from a pin nobody measured, "
        "and reporting the second as the first is the original fault in a new "
        "place.")
    checks.check(
        "an orientation is derived from them",
        "pub fn orientation(&self) -> Orientation" in fusb302
        and "pub enum Orientation" in fusb302,
        "no `State::orientation` returning an `Orientation` in fusb302.rs.")

    # The two commands that report it.
    checks.check(
        "`typec` prints the orientation",
        "orientation().name()" in typec,
        "the `typec` command does not print `state.orientation().name()`. Code "
        "that derives an answer nothing shows is code nobody can check.")
    checks.check(
        "the `board` tree prints the orientation",
        "orientation().name()" in board,
        "src/board.rs does not print the orientation. `board` is the one command "
        "that shows the whole port at a glance.")

    # --- unvalidated, said plainly -------------------------------------------
    docs = re.findall(r"///.*|//!.*", fusb302_full)
    unvalidated = [line for line in docs
                   if re.search(r"unvalidated on hardware", line, re.I)]
    checks.check(
        "the docstrings say the orientation is unvalidated on hardware",
        len(unvalidated) >= 2,
        f"found {len(unvalidated)} docstring line(s) saying so. Nothing in "
        f"simulation or QEMU can insert a cable one way round and then the "
        f"other, so the code is code and not evidence -- and a docstring that "
        f"does not say it will be read as a claim that it works.")

    # --- the pull-downs, which stay off --------------------------------------
    files = {path.name: strip_comments(path.read_text())
             for path in sorted(root.rglob("*.rs"))}
    pdwn = sorted(name for name, text in files.items() if "PDWN" in text)
    checks.check(
        "nothing in the firmware code enables PDWN1 or PDWN2",
        pdwn == [],
        f"{pdwn!r} name PDWN outside a comment. Presenting Rd is the only "
        f"electrically visible change in the configuration sequence, one of "
        f"these controllers is on AUX which carries the console, and the module "
        f"comment reserves the decision for someone with the board in hand.")

    # --- the interrupt decode ------------------------------------------------
    clear = function_body(fusb302, "pub fn clear(")
    checks.check(
        "`clear` still reads all three interrupt registers",
        all(register in clear for register in
            ("REG_INTERRUPT", "REG_INTERRUPTA", "REG_INTERRUPTB")),
        "`clear` must read every one: they are read-to-clear and the pin is "
        "their OR, so leaving one set leaves a level-sensitive source asserted.")
    checks.check(
        "`clear` returns what they said instead of dropping it",
        "pub fn clear(bus: &mut Bus, port: Port) -> Result<Interrupts, bus::Error>"
        in fusb302,
        "`clear` must return the register values. The read is what clears them, "
        "so the values it discards are the only record of why the line "
        "asserted, and a serviced interrupt with no cause beside it is a number "
        "that moved.")

    for name, expected in [("INTERRUPT_NAMES", INTERRUPT_NAMES),
                           ("INTERRUPTA_NAMES", INTERRUPTA_NAMES),
                           ("INTERRUPTB_NAMES", INTERRUPTB_NAMES)]:
        found = rust_string_array(fusb302, name)
        checks.check(
            f"{name} matches the datasheet, bit for bit",
            found == expected,
            f"fusb302.rs has {found!r}\nthe datasheet has {expected!r}\n"
            f"The order IS the decode: a table off by one names every interrupt "
            f"as its neighbour, which is worse than printing the hex.")

    checks.check(
        "the decoded cause goes through the deferred event ring",
        "TYPE_C_CAUSE" in typec and "events::push" in typec
        and "TYPE_C_CAUSE" in events,
        "`typec::service` must push `events::TYPE_C_CAUSE`. The cause belongs in "
        "the same stamped column as the `TYPE_C_INT` record the handler pushed, "
        "and all event formatting belongs in `events::report`.")
    checks.check(
        "`events::report` decodes that code by name",
        re.search(r"TYPE_C_CAUSE => \{", events) is not None
        and "Interrupts::from_payload" in events,
        "no `TYPE_C_CAUSE` arm in `events::report` rendering an `Interrupts`. "
        "Without one the record falls through to the generic arm and prints a "
        "u32, which is the hex it already had.")

    # The single-owner rule the decode depends on, restated as a check: nothing
    # but `clear` may touch a read-to-clear register, and the handler least of
    # all -- it may not even reach the bus.
    readers = sorted(name for name, text in files.items()
                     if name != "fusb302.rs" and "REG_INTERRUPT" in text)
    checks.check(
        "only fusb302.rs names the read-to-clear registers",
        readers == [],
        f"{readers!r} name REG_INTERRUPT*. A second reader takes the event away "
        f"from the thing that services it, and the symptom is an interrupt whose "
        f"cause nobody can name.")
    checks.check(
        "the handler does not read them itself",
        "REG_INTERRUPT" not in irq and "fusb302::clear" not in irq,
        "src/irq.rs reaches for the interrupt registers. Clearing a FUSB302B is "
        "about a millisecond of I2C on the controller the foreground is using, "
        "which is a long spin in interrupt context AND a second master on a "
        "peripheral with no lock.")


def main():
    # Rebound rather than passed down: which cable scenarios run is a property
    # of the run, not of any one check.
    global CABLES, BLINDNESS_CC2
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true",
                        help="all %d cable scenarios rather than %d -- the extra "
                             "one is a second band on a pin the run already "
                             "reads." % (len(SOAK_CABLES), len(CABLES)))
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR access")
    parser.add_argument("--firmware", type=Path, default=FIRMWARE,
                        help="firmware source to check structurally "
                             "(default: firmware/cynthion-soc/src)")
    args = parser.parse_args()

    if args.soak:
        CABLES = SOAK_CABLES
        BLINDNESS_CC2 = SOAK_BLINDNESS_CC2

    checks = Checks(emit)

    emit("1. both bands, from one comparator")
    run_blindness_check(checks, run_orientation_checks(checks, args.verbose))
    emit()

    emit("2. the sweep changes nothing electrically")
    run_electrical_checks(checks, args.verbose)
    emit()

    emit("3. the interrupt registers, read once and carried out")
    run_interrupt_checks(checks, args.verbose)
    emit()

    emit(f"4. the firmware structure ({args.firmware})")
    run_firmware_checks(checks, args.firmware)
    emit()

    if checks.failures:
        emit(f"{len(checks.failures)} FAILED: {', '.join(checks.failures)}")
    else:
        emit("all checks passed")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    sys.exit(main())
