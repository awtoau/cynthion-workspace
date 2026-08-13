#!/usr/bin/env python3
#
# Simulate the board peripherals: GPIO, the I2C master, and the sideband control.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the three peripherals added at `BOARD_BASE` in `top.py`.

    python3 scripts/soc_board_sim.py
    python3 scripts/soc_board_sim.py -v      # print every CSR access and bus edge

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## What is checked, and why each check is here

**The I2C master** (`gateware/soc/peripherals/i2c_master.py`) is driven against a model
slave written in the testbench rather than in gateware -- a slave in Amaranth
would share this controller's idea of what an I2C bus is, and would agree with
it whether or not either was right. The model here reacts only to edges on SCL
and SDA and knows nothing about the master. It is what an address scan and a
register read are actually run against.

The bit timing is measured rather than asserted from the source: the checks
count `sync` cycles between edges and compare against the slot counts the
module's docstring claims, so a change to the state machine that quietly moves
a setup or hold interval fails here instead of on a bus.

**The GPIO peripheral** is `amaranth_soc.gpio`, upstream and already tested
upstream, so the checks here are about the *wiring decision* this design makes
with it -- that a pin in its reset mode leaves the fabric driving, and a pin put
in push-pull takes over. That is the LED handover, and it is the part that is
this design's to get wrong.

**The sideband control** is checked for the same handover property, from the
other direction: the fabric's bits reach the responder until the CPU claims the
link, and the claim is a single bit that resets clear. Three things sit outside
that claim and are checked separately, because they are not values the fabric
could have an opinion about -- the port request, the outgoing byte, and the
received byte with its count. The count is checked for the property it exists
for: that neither read clears anything, so a repeated byte and a silence are
distinguishable without a side-effecting register.

**The I2C bus mux** has never run on silicon in any form -- the retired prototype in `debris/`
is marked simulation-only -- so the checks here are about the two properties that
would be expensive to discover on a board: that the select cannot move underneath
a transfer, and that the shared interrupt is the OR of the two `int` lines and
does NOT include `fault`.

**The ULPI register window** is driven against a model PHY written here, for the
same reason the I2C slave is: a model in Amaranth would share the window's idea
of the protocol and agree with it whether or not either was right. This one
reacts to the command byte on the data lines and to `stp`, and knows nothing
about the FSM driving it. What is being checked is the part that is ours -- the
register map, the domain crossing and the timeout -- not LUNA's transaction
encoding, which the USB console already exercises continuously.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth.hdl import Fragment
from amaranth.sim import Simulator
from amaranth_soc import gpio

from peripherals.i2c_master import I2CMaster, prescale_for, SLOTS_BIT, SLOTS_COND
from peripherals.sideband_csr import SidebandControl
from peripherals.fabric_status import FabricStatus, DIE_PRESENT
from peripherals.ulpi_window import UlpiRegisters, TIMEOUT_CYCLES
from clocks import PHY_PAD_RESET_CYCLES, PHY_PREP_CYCLES
from peripherals.i2c_mux import (I2CBusMux, BUS_TARGET_C, BUS_AUX_C, BUS_POWER_MONITOR,
                     LINE_TARGET_INT, LINE_AUX_INT, LINE_TARGET_FAULT,
                     LINE_AUX_FAULT)


# Small enough that a byte is a few hundred simulated cycles rather than a few
# thousand. The bit engine's behaviour does not depend on it, which is why the
# real design's 149 is not used here.
PRER = 3
SLOT_CYCLES = PRER + 1

# I2C register offsets, per the OpenCores map.
PRER_LO, PRER_HI, CTR, TXR_RXR, CR_SR = 0, 1, 2, 3, 4

CTR_EN  = 0x80
CTR_IEN = 0x40

CR_STA, CR_STO, CR_RD, CR_WR, CR_ACK, CR_IACK = 0x80, 0x40, 0x20, 0x10, 0x08, 0x01

SR_IF, SR_TIP, SR_AL, SR_BUSY, SR_RXACK = 0x01, 0x02, 0x20, 0x40, 0x80

# The address the model slave answers on, and the byte it returns. 0x10 because
# that is what the PAC1954 on this board is strapped to; the value is arbitrary
# and deliberately not 0x00 or 0xff, both of which a broken bus can produce.
SLAVE_ADDR = 0x10
SLAVE_BYTE = 0x7b

# GPIO register offsets, from amaranth_soc.gpio with data_width=8, 8 pins.
GPIO_MODE, GPIO_INPUT, GPIO_OUTPUT, GPIO_SETCLR = 0, 2, 3, 4

MODE_INPUT_ONLY = 0b00
MODE_PUSH_PULL  = 0b01


class Bus:
    """Byte-wide CSR reads and writes, as the multiplexer's timing requires.

    Copied from `soc_intc_sim.py`, and for the reason given there: a read is a
    strobe on one cycle and data on the next, and sampling a cycle late reads
    zero rather than stale data, which turns failures into passes.
    """

    def __init__(self, ctx, bus, verbose=False):
        self.ctx = ctx
        self.bus = bus
        self.verbose = verbose

    async def read(self, addr):
        ctx = self.ctx
        ctx.set(self.bus.addr, addr)
        ctx.set(self.bus.r_stb, 1)
        await ctx.tick()
        ctx.set(self.bus.r_stb, 0)
        value = ctx.get(self.bus.r_data)
        await ctx.tick()
        if self.verbose:
            print(f"      read  {addr:#04x} -> {value:#04x}")
        return value

    async def write(self, addr, value):
        ctx = self.ctx
        ctx.set(self.bus.addr, addr)
        ctx.set(self.bus.w_data, value)
        ctx.set(self.bus.w_stb, 1)
        await ctx.tick()
        ctx.set(self.bus.w_stb, 0)
        await ctx.tick()
        if self.verbose:
            print(f"      write {addr:#04x} <- {value:#04x}")


class ModelSlave:
    """An I2C slave, in Python, driven only by what happens on the two wires.

    Deliberately ignorant of the master: it sees SCL and the wired-AND of SDA
    and nothing else, exactly as a part on the bus would. Everything it knows
    about the transfer it worked out from edges.

    `respond` is a list of bytes it will send when addressed for reading; it is
    a list rather than a single value so a multi-byte read is a real multi-byte
    read.
    """

    def __init__(self, address, respond=(SLAVE_BYTE,)):
        self.address = address
        self.respond = list(respond)
        self.written = []          # bytes the master sent to us
        self.starts = 0
        self.stops = 0
        self.pull_low = False      # what we are doing to SDA right now

        self._state = "idle"
        self._shift = 0
        self._bits = 0
        self._out = 0
        self._reading = False
        self._respond_index = 0
        self._wrote = 0            # bytes written since this START
        self._prev_scl = 1
        self._prev_sda = 1

    def ack_write(self, index, byte):
        """Acknowledge the byte just received? `index` counts from the START.

        A plain slave always does. A part with a window in which it is
        unavailable does not, and WHERE it refuses is the whole diagnostic --
        the PAC1954 acknowledges its address and then NACKs the register
        pointer, which is index 0 here. See `scripts/soc_i2c_owner_sim.py`.
        """
        return True

    def step(self, scl, sda):
        """One `sync` cycle. `sda` is the wired-AND already computed."""
        rising = scl and not self._prev_scl
        falling = not scl and self._prev_scl

        # START and STOP are SDA edges while SCL is high.
        if scl and self._prev_scl:
            if self._prev_sda and not sda:
                self.starts += 1
                self._state = "addr"
                self._shift = 0
                self._bits = 0
                self._wrote = 0
                self.pull_low = False
            elif not self._prev_sda and sda:
                self.stops += 1
                self._state = "idle"
                self.pull_low = False

        elif rising:
            if self._state == "addr":
                self._shift = ((self._shift << 1) | sda) & 0xff
                self._bits += 1
            elif self._state == "write":
                self._shift = ((self._shift << 1) | sda) & 0xff
                self._bits += 1
            elif self._state == "read_ack":
                # The master's acknowledge for the byte we just sent. A NACK
                # means it wants no more.
                self._reading = not sda

        elif falling:
            if self._state == "addr" and self._bits == 8:
                matched = (self._shift >> 1) == self.address
                self.pull_low = matched            # ACK by pulling SDA down
                self._state = "addr_ack" if matched else "ignored"
                self._reading = bool(self._shift & 1)
            elif self._state == "addr_ack":
                if self._reading:
                    self._state = "read"
                    self._load_byte()
                else:
                    self.pull_low = False
                    self._state = "write"
                    self._shift = 0
                    self._bits = 0
            elif self._state == "write" and self._bits == 8:
                self.written.append(self._shift)
                # ACK, unless this device is refusing right now.
                self.pull_low = self.ack_write(self._wrote, self._shift)
                self._wrote += 1
                self._state = "write_ack"
            elif self._state == "write_ack":
                self.pull_low = False
                self._state = "write"
                self._shift = 0
                self._bits = 0
            elif self._state == "read":
                if self._bits < 8:
                    self.pull_low = not ((self._out >> (7 - self._bits)) & 1)
                    self._bits += 1
                else:
                    self.pull_low = False          # release for the master's ack
                    self._state = "read_ack"
            elif self._state == "read_ack":
                if self._reading:
                    self._state = "read"
                    self._load_byte()
                else:
                    self._state = "ignored"
                    self.pull_low = False

        self._prev_scl = scl
        self._prev_sda = sda

    def _load_byte(self):
        """Fetch the next byte AND put its first bit on the wire.

        The first bit has to go out on the same falling edge that ended the
        acknowledge, not the one after: a slave that waits a clock shifts every
        byte by one bit and the master reads a plausible-looking wrong value.
        Getting this wrong in the model is how this file first "found a bug" in
        the controller that was not there.
        """
        if self._respond_index < len(self.respond):
            self._out = self.respond[self._respond_index]
            self._respond_index += 1
        else:
            self._out = 0xff       # an absent byte reads as an idle bus
        self.pull_low = not ((self._out >> 7) & 1)
        self._bits = 1


def make_i2c_sim(slave, verbose=False, watch=None):
    """A simulator with an I2CMaster and a model slave wired together.

    Returns (dut, add_testbench, run). The wired-AND of the two SDA drivers is
    maintained by a background testbench, which also steps the slave.
    """
    dut = I2CMaster()
    trace = []

    async def wires(ctx):
        while True:
            scl = ctx.get(dut.scl_o)
            master_low = ctx.get(dut.sda_oe) and not ctx.get(dut.sda_o)
            sda = 0 if (master_low or slave.pull_low) else 1
            ctx.set(dut.sda_i, sda)
            slave.step(scl, sda)
            if watch is not None:
                watch.append((scl, sda))
            await ctx.tick()

    def build(testbench):
        sim = Simulator(Fragment.get(dut, None))
        sim.add_clock(1e-6)
        sim.add_testbench(wires, background=True)
        sim.add_testbench(testbench)
        return sim

    return dut, build, trace


async def i2c_setup(bus, ien=False):
    await bus.write(PRER_LO, PRER & 0xff)
    await bus.write(PRER_HI, PRER >> 8)
    await bus.write(CTR, CTR_EN | (CTR_IEN if ien else 0))


async def i2c_wait(ctx, bus, limit=20000):
    """Spin on SR.TIP, then return SR. Bounded, because a hang here is a bug."""
    for _ in range(limit):
        status = await bus.read(CR_SR)
        if not status & SR_TIP:
            return status
    return None


async def i2c_command(ctx, bus, command, data=None):
    if data is not None:
        await bus.write(TXR_RXR, data)
    await bus.write(CR_SR, command)
    return await i2c_wait(ctx, bus)


def run_i2c_checks(checks, verbose):
    # --- an address that answers, and one that does not ---------------------
    slave = ModelSlave(SLAVE_ADDR)
    dut, build, _ = make_i2c_sim(slave, verbose)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await i2c_setup(bus)

        # A scan probe: START, write the address with the read/write bit clear,
        # STOP. RxACK afterwards is the whole answer.
        status = await i2c_command(ctx, bus, CR_STA | CR_WR | CR_STO,
                                   SLAVE_ADDR << 1)
        seen["hit"] = status
        seen["hit_starts"] = slave.starts
        seen["hit_stops"] = slave.stops

        status = await i2c_command(ctx, bus, CR_STA | CR_WR | CR_STO,
                                   (SLAVE_ADDR + 1) << 1)
        seen["miss"] = status

    build(testbench).run()

    checks.check(
        "a device on the bus acknowledges its address",
        seen.get("hit") is not None and not (seen["hit"] & SR_RXACK),
        f"SR after probing {SLAVE_ADDR:#04x} was {seen.get('hit')!r}; "
        f"RxACK should be clear (0 == acknowledged).")
    checks.check(
        "an address with nothing on it does not",
        seen.get("miss") is not None and (seen["miss"] & SR_RXACK),
        f"SR after probing an empty address was {seen.get('miss')!r}; "
        f"RxACK should be set. A scan against this would report a bus full of "
        f"devices.")
    checks.check(
        "the probe put exactly one START and one STOP on the wires",
        (seen.get("hit_starts"), seen.get("hit_stops")) == (1, 1),
        f"the model slave counted {seen.get('hit_starts')} START(s) and "
        f"{seen.get('hit_stops')} STOP(s) for one probe.")

    # --- a register read: write the pointer, repeated START, read -----------
    slave = ModelSlave(SLAVE_ADDR, respond=[SLAVE_BYTE, 0x2a])
    dut, build, _ = make_i2c_sim(slave, verbose)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await i2c_setup(bus)

        await i2c_command(ctx, bus, CR_STA | CR_WR, SLAVE_ADDR << 1)
        await i2c_command(ctx, bus, CR_WR, 0xfd)          # a register pointer
        # Repeated START, address again with the read bit set.
        await i2c_command(ctx, bus, CR_STA | CR_WR, (SLAVE_ADDR << 1) | 1)
        # First byte, acknowledged so the slave sends another.
        await i2c_command(ctx, bus, CR_RD)
        seen["first"] = await bus.read(TXR_RXR)
        # Second and last: NACK then STOP, which is how a master says "done".
        await i2c_command(ctx, bus, CR_RD | CR_ACK | CR_STO)
        seen["second"] = await bus.read(TXR_RXR)
        seen["again"] = await bus.read(TXR_RXR)
        seen["written"] = list(slave.written)
        seen["starts"] = slave.starts
        seen["stops"] = slave.stops

    build(testbench).run()

    checks.check(
        "the register pointer written reaches the slave",
        seen.get("written") == [0xfd],
        f"the slave received {seen.get('written')}, expected [0xfd]")
    checks.check(
        "a repeated START does not put a STOP on the bus first",
        (seen.get("starts"), seen.get("stops")) == (2, 1),
        f"the model slave counted {seen.get('starts')} START(s) and "
        f"{seen.get('stops')} STOP(s); a register read is two STARTs and one "
        f"STOP. An extra STOP would release the pointer and read register 0.")
    checks.check(
        "the first byte read back is the byte the slave sent",
        seen.get("first") == SLAVE_BYTE,
        f"RXR held {seen.get('first')!r}, expected {SLAVE_BYTE:#04x}")
    checks.check(
        "an acknowledged read is followed by a second byte",
        seen.get("second") == 0x2a,
        f"RXR held {seen.get('second')!r}, expected 0x2a")
    checks.check(
        "reading RXR twice gives the same byte -- it is a register, not a FIFO",
        seen.get("again") == seen.get("second"),
        f"the second read of RXR gave {seen.get('again')!r} after "
        f"{seen.get('second')!r}. A data register that changes when read is the "
        f"bug uart16550.py exists to describe.")

    # --- the interrupt, and how it is cleared -------------------------------
    slave = ModelSlave(SLAVE_ADDR)
    dut, build, _ = make_i2c_sim(slave, verbose)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await i2c_setup(bus, ien=False)

        await i2c_command(ctx, bus, CR_STA | CR_WR | CR_STO, SLAVE_ADDR << 1)
        seen["if_set"] = await bus.read(CR_SR)
        seen["irq_masked"] = ctx.get(dut.irq)

        # Reading SR must not clear IF -- that is the whole point of putting the
        # clear on a write.
        seen["if_still_set"] = await bus.read(CR_SR)

        await bus.write(CTR, CTR_EN | CTR_IEN)
        await ctx.tick()
        seen["irq_unmasked"] = ctx.get(dut.irq)

        await bus.write(CR_SR, CR_IACK)
        seen["after_iack"] = await bus.read(CR_SR)
        seen["irq_cleared"] = ctx.get(dut.irq)
        seen["starts_after_iack"] = slave.starts

    build(testbench).run()

    checks.check(
        "a completed transfer sets IF",
        seen.get("if_set") is not None and (seen["if_set"] & SR_IF),
        f"SR was {seen.get('if_set')!r}")
    checks.check(
        "reading SR does not clear IF",
        seen.get("if_still_set") is not None and (seen["if_still_set"] & SR_IF),
        f"the second read of SR gave {seen.get('if_still_set')!r}. A "
        f"clear-on-read here would be a read with a side effect on a status "
        f"register, which is the one thing this project's peripherals do not do.")
    checks.check(
        "irq stays low while IEN is clear",
        seen.get("irq_masked") == 0,
        f"irq was {seen.get('irq_masked')!r} with interrupts disabled")
    checks.check(
        "and follows IF once IEN is set",
        seen.get("irq_unmasked") == 1,
        f"irq was {seen.get('irq_unmasked')!r} with IF set and IEN set")
    checks.check(
        "writing IACK clears IF and drops irq",
        seen.get("after_iack") is not None
        and not (seen["after_iack"] & SR_IF)
        and seen.get("irq_cleared") == 0,
        f"SR after IACK was {seen.get('after_iack')!r}, irq "
        f"{seen.get('irq_cleared')!r}")
    checks.check(
        "an IACK on its own does not start a transfer",
        seen.get("starts_after_iack") == 1,
        f"the slave saw {seen.get('starts_after_iack')} STARTs; a command with "
        f"no STA, STO, RD or WR bit must move nothing on the bus.")

    # --- a wedged bus -------------------------------------------------------
    #
    # A slave holding SDA down is what a half-finished transfer leaves behind,
    # and without the arbitration check the master would clock happily through
    # it and read 0x00 from every address.
    class StuckSlave(ModelSlave):
        def step(self, scl, sda):
            self.pull_low = True

    slave = StuckSlave(SLAVE_ADDR)
    dut, build, _ = make_i2c_sim(slave, verbose)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await i2c_setup(bus)
        status = await i2c_command(ctx, bus, CR_STA | CR_WR | CR_STO, 0xfe)
        seen["status"] = status
        seen["sda_oe"] = ctx.get(dut.sda_oe)

    build(testbench).run()

    checks.check(
        "a bus held low by something else sets AL rather than reading zeros",
        seen.get("status") is not None and (seen["status"] & SR_AL),
        f"SR was {seen.get('status')!r} against a slave clamping SDA low.")
    checks.check(
        "and the master releases the bus when it does",
        seen.get("sda_oe") == 0,
        "the master was still driving SDA after losing arbitration.")


def run_i2c_timing_checks(checks, verbose):
    """Measure the bit timing rather than trust the state machine's shape."""
    slave = ModelSlave(SLAVE_ADDR)
    watch = []
    dut, build, _ = make_i2c_sim(slave, verbose, watch=watch)

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await i2c_setup(bus)
        watch.clear()
        await i2c_command(ctx, bus, CR_STA | CR_WR | CR_STO, SLAVE_ADDR << 1)

    build(testbench).run()

    # Split the SCL trace into runs, and look at the ones between the START and
    # the STOP -- the data bits.
    runs = []
    for scl, _sda in watch:
        if runs and runs[-1][0] == scl:
            runs[-1][1] += 1
        else:
            runs.append([scl, 1])

    highs = [length for level, length in runs if level]
    lows = [length for level, length in runs if not level]
    # The first high and the last high belong to START and STOP, which are
    # longer by design; the data bits are everything between.
    data_highs = [n for n in highs if n == 2 * SLOT_CYCLES]
    data_lows = [n for n in lows if n == 3 * SLOT_CYCLES]

    checks.check(
        f"SCL is high for {2 * SLOT_CYCLES} cycles on a data bit (2 slots)",
        len(data_highs) >= 8,
        f"only {len(data_highs)} of {len(highs)} high periods were 2 slots: "
        f"{highs}")
    checks.check(
        f"and low for {3 * SLOT_CYCLES} cycles between them (3 slots)",
        len(data_lows) >= 8,
        f"only {len(data_lows)} of {len(lows)} low periods were 3 slots: "
        f"{lows}")

    # The START condition: SDA falls while SCL is high. The setup before it is
    # "at least" rather than "exactly" two slots -- a START on an idle bus
    # inherits however long the bus has been idle, and only a repeated START,
    # which begins from SCL low, is bounded by the state machine's own slots.
    start_index = next(i for i, (scl, sda) in enumerate(watch)
                       if i and scl and sda == 0
                       and watch[i - 1][1] == 1 and watch[i - 1][0])
    scl_fall = next(i for i in range(start_index, len(watch))
                    if not watch[i][0])
    lows_before = [i for i in range(0, start_index) if not watch[i][0]]
    scl_rise = (max(lows_before) + 1) if lows_before else 0

    checks.check(
        f"t_SU;STA is at least 2 slots ({2 * SLOT_CYCLES} cycles)",
        start_index - scl_rise >= 2 * SLOT_CYCLES,
        f"SCL rose at {scl_rise} and SDA fell at {start_index}: "
        f"{start_index - scl_rise} cycles, wanted at least {2 * SLOT_CYCLES}")
    checks.check(
        f"t_HD;STA is 2 slots ({2 * SLOT_CYCLES} cycles)",
        scl_fall - start_index == 2 * SLOT_CYCLES,
        f"SDA fell at {start_index} and SCL fell at {scl_fall}: "
        f"{scl_fall - start_index} cycles, wanted {2 * SLOT_CYCLES}")

    # And the STOP: SDA rises while SCL is high, two slots after the last
    # rising edge of SCL.
    stop_index = max(i for i, (scl, sda) in enumerate(watch)
                     if i and scl and sda == 1
                     and watch[i - 1][1] == 0 and watch[i - 1][0])
    stop_scl_rise = max(i for i in range(0, stop_index)
                        if not watch[i][0]) + 1
    checks.check(
        f"t_SU;STO is 2 slots ({2 * SLOT_CYCLES} cycles)",
        stop_index - stop_scl_rise == 2 * SLOT_CYCLES,
        f"SCL rose at {stop_scl_rise} and SDA rose at {stop_index}: "
        f"{stop_index - stop_scl_rise} cycles, wanted {2 * SLOT_CYCLES}")

    # The published rate has to be the rate. A data bit is SLOTS_BIT slots.
    checks.check(
        "a data bit is exactly the period the f_SCL formula claims",
        data_highs and data_lows
        and (2 * SLOT_CYCLES + 3 * SLOT_CYCLES) == SLOTS_BIT * SLOT_CYCLES,
        f"the bit period measured {2 * SLOT_CYCLES + 3 * SLOT_CYCLES} cycles "
        f"against a formula of {SLOTS_BIT} slots. A driver computing a prescale "
        f"from f_sync / (5 * (PRER + 1)) would get a bus running at the wrong "
        f"rate, and I2C fails by working most of the time.")

    checks.check(
        "prescale_for never rounds a bus faster than it was asked for",
        all(60e6 / (SLOTS_BIT * (prescale_for(60e6, target) + 1)) <= target
            for target in (10_000, 80_000, 100_000, 400_000)),
        "prescale_for produced a divider that overshoots the requested rate.")


# ULPI register-window offsets, from ulpi_window.UlpiRegisters.
ULPI_ADDRESS, ULPI_DATA, ULPI_CONTROL, ULPI_STATUS = 0, 1, 2, 3

ULPI_START_READ  = 1 << 0
ULPI_START_WRITE = 1 << 1
ULPI_PHY_RESET   = 1 << 2

ULPI_BUSY      = 1 << 0
ULPI_TIMEOUT   = 1 << 1
ULPI_RESETTING = 1 << 2

# What a USB3343 answers with. Confirmed on all three PHYs of this board by
# debris/scripts/phy_probe.py; deliberately not 0x00 or 0xff, either of which a dead
# bus can produce.
PHY_IDENTITY = {0x00: 0x24, 0x01: 0x04, 0x02: 0x09, 0x03: 0x00}


class ModelPhy:
    """A USB3343 as far as a ULPI register transaction can see it.

    Driven only by the command byte on the data lines and by `stp`. It has no
    idea what a `ULPIRegisterWindow` is, which is the point: a model that shared
    the window's idea of the protocol would agree with it whether or not either
    was right.

    `stall` holds `dir` asserted forever, which is what an absent PHY, a PHY
    held in reset, and a PHY receiving a packet all look like from the link side.
    That is the case the gateware's timeout exists for.
    """

    def __init__(self, registers, stall=False):
        self.registers = dict(registers)
        self.stall = stall
        self.dir = 1 if stall else 0
        self.nxt = 0
        self.data_i = 0
        self.reads = 0
        self.writes = 0
        self._state = "idle"
        self._address = 0

    def step(self, data_o):
        if self.stall:
            # Never releases the bus. The window waits for `dir` to fall before
            # it may drive, so nothing else ever happens.
            return

        if self._state == "idle":
            self.nxt = 0
            self.dir = 0
            # 0b11xxxxxx is a register read, 0b10xxxxxx a register write; the
            # low six bits are the address. Anything else -- 0x00 is the NOP the
            # window drives while idle -- is not a command.
            if data_o & 0xc0 == 0xc0:
                self._address = data_o & 0x3f
                self.nxt = 1
                self._state = "read_turnaround"
            elif data_o & 0xc0 == 0x80:
                self._address = data_o & 0x3f
                self.nxt = 1
                self._state = "write_data"
        elif self._state == "read_turnaround":
            # The link has released the bus; take it and present the byte.
            self.nxt = 0
            self.dir = 1
            self.data_i = self.registers.get(self._address, 0xff)
            self._state = "read_data"
        elif self._state == "read_data":
            # The window latches what is on the bus this cycle.
            self._state = "read_release"
        elif self._state == "read_release":
            self.dir = 0
            self.reads += 1
            self._state = "idle"
        elif self._state == "write_data":
            # The value is on the bus now, one cycle after the command was
            # accepted. Take it and acknowledge.
            self.nxt = 1
            self.registers[self._address] = data_o
            self.writes += 1
            self._state = "write_stop"
        elif self._state == "write_stop":
            self.nxt = 0
            self._state = "idle"


def make_ulpi_sim(phy, **kwargs):
    """A simulator with a UlpiRegisters and a model PHY wired together.

    Two clock domains, and that is the point of the exercise: the CSR bus is in
    `sync` and the ULPI side is in `usb`. They are given DIFFERENT periods here
    -- 1 us and 0.7 us -- deliberately. Equal periods would let a crossing that
    only works when the two clocks happen to agree pass, and the design's whole
    reason for using a handshake is that `SYNC_MHZ` is a free parameter.
    """
    dut = UlpiRegisters(**kwargs)

    async def wires(ctx):
        while True:
            phy.step(ctx.get(dut.data_o))
            ctx.set(dut.dir_i, phy.dir)
            ctx.set(dut.nxt_i, phy.nxt)
            ctx.set(dut.data_i, phy.data_i)
            await ctx.tick("usb")

    def build(testbench):
        sim = Simulator(Fragment.get(dut, None))
        sim.add_clock(1e-6)
        sim.add_clock(0.7e-6, domain="usb")
        sim.add_testbench(wires, background=True)
        sim.add_testbench(testbench)
        return sim

    return dut, build


async def ulpi_settle(bus, limit=40000):
    """Spin on STATUS.busy, then return STATUS. Bounded, like the firmware's."""
    for _ in range(limit):
        status = await bus.read(ULPI_STATUS)
        if not status & ULPI_BUSY:
            return status
    return None


def run_ulpi_checks(checks, verbose):
    # --- a register read, and a scratch round trip --------------------------
    phy = ModelPhy({**PHY_IDENTITY, 0x16: 0x00})
    dut, build = make_ulpi_sim(phy)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        await bus.write(ULPI_ADDRESS, 0x00)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        # Sampled before the wait: `busy` has to be observable, or firmware has
        # nothing to poll and would read the previous transaction's byte.
        seen["busy"] = await bus.read(ULPI_STATUS)
        seen["status"] = await ulpi_settle(bus)
        seen["vendor_low"] = await bus.read(ULPI_DATA)
        # Twice. A data register that changed on read is the hazard this whole
        # project's register discipline exists to eliminate.
        seen["vendor_low_again"] = await bus.read(ULPI_DATA)

        await bus.write(ULPI_ADDRESS, 0x02)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        await ulpi_settle(bus)
        seen["product_low"] = await bus.read(ULPI_DATA)

        # Write then read back, which is the walking-bit test's building block.
        await bus.write(ULPI_ADDRESS, 0x16)
        await bus.write(ULPI_DATA, 0x5a)
        await bus.write(ULPI_CONTROL, ULPI_START_WRITE)
        await ulpi_settle(bus)
        seen["written"] = phy.registers.get(0x16)

        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        await ulpi_settle(bus)
        seen["scratch"] = await bus.read(ULPI_DATA)
        seen["reads"] = phy.reads
        seen["writes"] = phy.writes

    build(testbench).run()

    checks.check(
        "busy is set while a transaction is in flight",
        seen.get("busy") is not None and (seen["busy"] & ULPI_BUSY),
        f"STATUS read {seen.get('busy')!r} immediately after starting a read. "
        f"Firmware polls this bit; if it is never set there is nothing to wait "
        f"for and every read returns the previous byte.")
    checks.check(
        "a register read returns what the PHY holds, and does not time out",
        seen.get("vendor_low") == 0x24 and seen.get("status") == 0,
        f"read {seen.get('vendor_low')!r} from register 0x00 with STATUS "
        f"{seen.get('status')!r}; expected 0x24 and a clear status.")
    checks.check(
        "reading the data register twice gives the same byte",
        seen.get("vendor_low") == seen.get("vendor_low_again"),
        f"first read {seen.get('vendor_low')!r}, second "
        f"{seen.get('vendor_low_again')!r}. A read with a side effect here "
        f"would make a widened or replayed bus access consume a result.")
    checks.check(
        "a second read addresses a different register",
        seen.get("product_low") == 0x09,
        f"read {seen.get('product_low')!r} from register 0x02, expected 0x09 -- "
        f"a window that ignored ADDRESS would return 0x24 again.")
    checks.check(
        "a write reaches the PHY and reads back",
        seen.get("written") == 0x5a and seen.get("scratch") == 0x5a,
        f"the model PHY holds {seen.get('written')!r} in scratch and the "
        f"read-back was {seen.get('scratch')!r}; expected 0x5a for both.")
    checks.check(
        "each start produced exactly one transaction",
        (seen.get("reads"), seen.get("writes")) == (3, 1),
        f"the model PHY saw {seen.get('reads')} read(s) and "
        f"{seen.get('writes')} write(s); expected 3 and 1.")

    # --- a PHY that never releases the bus -----------------------------------
    #
    # Without the timeout this hangs `busy` for the rest of the session, and
    # every later read reports "busy" instead of "absent" -- so an unplugged or
    # unpowered PHY would look like a broken peripheral forever.
    stalled = ModelPhy(PHY_IDENTITY, stall=True)
    dut, build = make_ulpi_sim(stalled)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await bus.write(ULPI_ADDRESS, 0x00)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        seen["status"] = await ulpi_settle(bus)
        # Twice, because a timeout flag that cleared on read would be gone
        # before the caller that polls STATUS in a loop could act on it.
        seen["again"] = await bus.read(ULPI_STATUS)

    build(testbench).run()

    checks.check(
        "a PHY that never releases the bus times out rather than hanging",
        seen.get("status") == ULPI_TIMEOUT,
        f"STATUS settled at {seen.get('status')!r}, expected "
        f"{ULPI_TIMEOUT} (busy clear, timeout set) after "
        f"{TIMEOUT_CYCLES} usb cycles. None means busy never cleared at all.")
    checks.check(
        "and the timeout flag survives being read",
        seen.get("again") == ULPI_TIMEOUT,
        f"a second read of STATUS gave {seen.get('again')!r}. Clear-on-read "
        f"would be a state-changing read sharing a 32-bit word with the "
        f"command register.")

    # --- a stalled window recovers ------------------------------------------
    #
    # The timeout resets the register window, and the point of doing so is that
    # the NEXT transaction works. A design that reported the timeout and left
    # the FSM parked would pass the two checks above and be useless.
    phy = ModelPhy(PHY_IDENTITY, stall=True)
    dut, build = make_ulpi_sim(phy)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await bus.write(ULPI_ADDRESS, 0x00)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        await ulpi_settle(bus)

        # The PHY comes back.
        phy.stall = False
        phy.dir = 0
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        seen["status"] = await ulpi_settle(bus)
        seen["data"] = await bus.read(ULPI_DATA)

    build(testbench).run()

    checks.check(
        "a window that timed out works again once the PHY answers",
        seen.get("status") == 0 and seen.get("data") == 0x24,
        f"after a timeout the next read gave STATUS {seen.get('status')!r} and "
        f"data {seen.get('data')!r}; expected a clear status and 0x24. The "
        f"timeout flag must also be cleared by the next START, not left set.")

    # --- the timeout reset actually reaches the window ----------------------
    #
    # The check above passes whether or not it does, which is how #241 survived:
    # LUNA's window parks in START_READ waiting for `dir` to fall, so simply
    # dropping `dir` lets the stuck FSM walk out on its own and complete the read
    # nobody asked for any more.
    #
    # What only a real reset gives is a window that will accept a DIFFERENT KIND
    # of transaction next. A window still parked in START_READ ignores
    # `write_request` -- IDLE is the only state that looks at it -- and finishes
    # the old READ instead. The outer FSM sees `done`, reports success, and the
    # PHY was never written.
    #
    # The PHY therefore has to still be stalled when the write STARTS, and let go
    # during it. Releasing the bus beforehand, as the check above does, lets the
    # parked FSM walk out on its own and hides the whole thing -- which is how
    # this defect survived a passing test suite.
    #
    # `ResetInserter(sig)` means `{"sync": sig}` and this module is entirely
    # `usb`, so the bare form inserts nothing at all and this check fails.
    phy = ModelPhy({**PHY_IDENTITY, 0x16: 0x00}, stall=True)
    dut, build = make_ulpi_sim(phy)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await bus.write(ULPI_ADDRESS, 0x00)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        seen["timed_out"] = await ulpi_settle(bus)

        # Still stalled. The write starts against a bus the PHY still owns.
        await bus.write(ULPI_ADDRESS, 0x16)
        await bus.write(ULPI_DATA, 0x5a)
        await bus.write(ULPI_CONTROL, ULPI_START_WRITE)
        await bus.read(ULPI_STATUS)
        await bus.read(ULPI_STATUS)

        # And now it lets go, well inside the outer timeout.
        phy.stall = False
        phy.dir = 0
        seen["status"] = await ulpi_settle(bus)
        seen["written"] = phy.registers.get(0x16)
        seen["writes"] = phy.writes

    build(testbench).run()

    checks.check(
        "the timeout reset reaches the window, so the next WRITE is a write",
        seen.get("timed_out") == ULPI_TIMEOUT and seen.get("written") == 0x5a
        and seen.get("writes") == 1,
        f"after a timed-out read the model PHY saw {seen.get('writes')} "
        f"write(s) and holds {seen.get('written')!r} in 0x16; expected 1 and "
        f"0x5a. A window still parked in START_READ ignores `write_request` and "
        f"completes the old read, which the outer FSM reports as success.")

    # --- the PHY reset ------------------------------------------------------
    #
    # Before #241 the PHYs had no reset under firmware control at all: both pads
    # were driven from `ResetSignal("usb")`, which `clocks.py` had tied to 0. A
    # PHY that glitched could only be recovered by reconfiguring the FPGA, which
    # the firmware running on it cannot do.
    #
    # Short durations here -- the real ones are 128 + 72000 usb cycles, and
    # simulating 1.2 ms at 0.7 us a cycle to watch a counter count is not a
    # better test than checking the constants' arithmetic, which is done below.
    PAD, PREP = 8, 24
    phy = ModelPhy(PHY_IDENTITY)
    dut, build = make_ulpi_sim(phy, pad_reset_cycles=PAD, prep_cycles=PREP)
    seen = {}

    async def watch(ctx):
        """Count usb cycles the pad is asserted, and cycles after it releases.

        The second counter is what the preparation time is checked against. It
        is read by the testbench at the moment STATUS first shows the reset
        finished, so polling latency can only make it larger -- and larger is
        the safe direction. Releasing EARLY is the defect.
        """
        low = 0
        fell = False
        after = 0
        while True:
            if ctx.get(dut.phy_rst):
                low += 1
            elif low:
                fell = True
            if fell:
                after += 1
            seen["low"] = low
            seen["after_pad"] = after
            await ctx.tick("usb")

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        seen["idle"] = await bus.read(ULPI_STATUS)
        await bus.write(ULPI_CONTROL, ULPI_PHY_RESET)
        # Read immediately: firmware has to be able to SEE that it started, or
        # it polls a bit that is already clear and carries on into a PHY that is
        # still in reset.
        seen["started"] = await bus.read(ULPI_STATUS)
        for _ in range(2000):
            status = await bus.read(ULPI_STATUS)
            if not (status & ULPI_RESETTING):
                break
        seen["finished"] = status
        seen["at_clear"] = seen.get("after_pad")

        # And the window works afterwards -- the reset holds it, so a design
        # that held it for ever would pass every check above.
        await bus.write(ULPI_ADDRESS, 0x00)
        await bus.write(ULPI_CONTROL, ULPI_START_READ)
        seen["after"] = await ulpi_settle(bus)
        seen["vendor"] = await bus.read(ULPI_DATA)

    def build_with_watch(testbench):
        sim = build(testbench)
        sim.add_testbench(watch, background=True)
        return sim

    build_with_watch(testbench).run()

    checks.check(
        "STATUS.resetting is clear until firmware asks for a reset",
        seen.get("idle") == 0,
        f"STATUS read {seen.get('idle')!r} before anything was written. A bit "
        f"that is set at rest is one firmware would wait on for ever.")
    checks.check(
        "a PHY reset is visible in STATUS the moment it is asked for",
        seen.get("started") is not None
        and (seen["started"] & ULPI_RESETTING),
        f"STATUS read {seen.get('started')!r} straight after writing the reset "
        f"bit; bit 2 must already be set or firmware polls a flag that has not "
        f"arrived and proceeds into a PHY still in reset.")
    checks.check(
        "the reset pad is asserted for the pad time and no longer",
        seen.get("low") == PAD,
        f"RESETB was asserted for {seen.get('low')!r} usb cycles, expected "
        f"{PAD}. Short of it violates the USB334x's 1 us minimum (Rev 1.2 "
        f"section 5.6.2); longer means the counter is not measuring what it "
        f"claims.")
    checks.check(
        "and STATUS stays busy through the PHY's preparation time",
        seen.get("at_clear") is not None and seen["at_clear"] >= PREP
        and not (seen.get("finished", 1) & ULPI_RESETTING),
        f"STATUS showed the reset finished {seen.get('at_clear')!r} usb cycles "
        f"after RESETB released, and settled at {seen.get('finished')!r}; "
        f"expected at least {PREP} and bit 2 clear. Releasing at the end of the "
        f"pulse would let firmware talk to a PHY still inside TPREP.")
    checks.check(
        "the register window still works after a PHY reset",
        seen.get("after") == 0 and seen.get("vendor") == 0x24,
        f"the first read after a reset gave STATUS {seen.get('after')!r} and "
        f"data {seen.get('vendor')!r}; expected a clear status and 0x24. The "
        f"window is held in reset for the whole sequence and has to come back.")

    # The real constants, checked as arithmetic rather than by simulating them.
    checks.check(
        "the PHY reset durations meet the USB334x datasheet",
        PHY_PAD_RESET_CYCLES / 60.0 >= 1.0
        and 1.0 <= PHY_PREP_CYCLES / 60_000.0 <= 1.2,
        f"RESETB low for {PHY_PAD_RESET_CYCLES / 60.0:.3f} us and TPREP "
        f"{PHY_PREP_CYCLES / 60_000.0:.3f} ms at 60.000 MHz. The datasheet asks "
        f"for at least 1 us (Rev 1.2 section 5.6.2) and 1.0..1.2 ms "
        f"(Table 4.3); TPREP must be the maximum, not the typical.")


# i2c_mux.I2CBusMux register offsets.
MUX_SELECT, MUX_LINES = 0, 1


def run_i2c_mux_checks(checks, verbose):
    dut = I2CBusMux()
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        # Out of reset the controller points at the power monitor, so the rail
        # readings work before anything touches the select. A mux that came up
        # pointing at a Type-C bus would make the power monitor unreachable until
        # some firmware happened to write this register.
        ctx.set(dut.idle, 1)
        await ctx.tick()
        seen["reset_select"] = ctx.get(dut.select)

        await bus.write(MUX_SELECT, BUS_TARGET_C)
        await ctx.tick()
        seen["selected"] = ctx.get(dut.select)
        seen["readback"] = await bus.read(MUX_SELECT)

        # THE ONE THAT MATTERS: a select written while a transfer is in flight
        # must not reach the pins. Switching pin-sets between a START and its
        # STOP leaves one bus half-driven and puts an edge on another that every
        # device listening reads as a START.
        ctx.set(dut.idle, 0)
        await bus.write(MUX_SELECT, BUS_AUX_C)
        await ctx.tick().repeat(4)
        seen["held"] = ctx.get(dut.select)
        seen["held_readback"] = await bus.read(MUX_SELECT)

        # ...and it takes effect when the transfer ends, rather than being lost.
        ctx.set(dut.idle, 1)
        await ctx.tick().repeat(2)
        seen["applied"] = ctx.get(dut.select)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "the mux comes out of reset pointing at the power monitor",
        seen.get("reset_select") == BUS_POWER_MONITOR,
        f"select was {seen.get('reset_select')!r}, expected "
        f"{BUS_POWER_MONITOR}. Anything else makes the rail readings depend on "
        f"firmware having written this register first.")
    checks.check(
        "a select written while idle reaches the pins and reads back",
        seen.get("selected") == BUS_TARGET_C
        and seen.get("readback") == BUS_TARGET_C,
        f"select was {seen.get('selected')!r} and read back "
        f"{seen.get('readback')!r} after writing {BUS_TARGET_C}.")
    checks.check(
        "a select written mid-transfer does NOT move the pins",
        seen.get("held") == BUS_TARGET_C,
        f"select moved to {seen.get('held')!r} while the controller was busy. "
        f"Switching pin-sets between a START and its STOP leaves one bus "
        f"half-driven and puts a START on another.")
    checks.check(
        "and the register still reads back what was asked for",
        seen.get("held_readback") == BUS_AUX_C,
        f"the register read {seen.get('held_readback')!r}; a write that is "
        f"deferred must not also be forgotten, or firmware cannot tell the "
        f"difference between deferred and ignored.")
    checks.check(
        "the deferred select applies once the transfer ends",
        seen.get("applied") == BUS_AUX_C,
        f"select was {seen.get('applied')!r} after idle went high; the write "
        f"was dropped rather than held.")

    # --- one interrupt per controller ----------------------------------------
    dut = I2CBusMux()
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        ctx.set(dut.idle, 1)

        # FFSynchronizer is two stages, so give every level four edges to
        # arrive. Sampling earlier would read the synchroniser rather than the
        # signal, and would pass or fail on timing rather than on logic.
        async def settle():
            await ctx.tick().repeat(4)

        def irqs():
            return (ctx.get(dut.target_irq), ctx.get(dut.aux_irq))

        await settle()
        seen["quiet"] = (irqs(), await bus.read(MUX_LINES))

        ctx.set(dut.target_int, 1)
        await settle()
        seen["target"] = (irqs(), await bus.read(MUX_LINES))

        ctx.set(dut.target_int, 0)
        ctx.set(dut.aux_int, 1)
        await settle()
        seen["aux"] = (irqs(), await bus.read(MUX_LINES))

        ctx.set(dut.target_int, 1)
        await settle()
        seen["both"] = (irqs(), await bus.read(MUX_LINES))

        # Only one goes away. The other line must be unaffected -- this is the
        # property that makes clearing one device unable to strand the other.
        ctx.set(dut.aux_int, 0)
        await settle()
        seen["one_cleared"] = (irqs(), await bus.read(MUX_LINES))

        ctx.set(dut.target_int, 0)
        ctx.set(dut.target_fault, 1)
        ctx.set(dut.aux_fault, 1)
        await settle()
        seen["fault"] = (irqs(), await bus.read(MUX_LINES))
        # Twice: LINES must be pure. The FUSB302B's own interrupt registers are
        # read-to-clear and that is where clearing belongs -- a CSR here that
        # cleared on read would be a state-changing read one byte from the
        # register the handler polls.
        seen["fault_again"] = await bus.read(MUX_LINES)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "nothing asserting means no interrupt and no lines set",
        seen.get("quiet") == ((0, 0), 0),
        f"((target_irq, aux_irq), lines) was {seen.get('quiet')!r} with every "
        f"input low.")
    checks.check(
        "target int raises the TARGET source and only that one",
        seen.get("target") == ((1, 0), 1 << LINE_TARGET_INT),
        f"((target_irq, aux_irq), lines) was {seen.get('target')!r}, expected "
        f"((1, 0), {1 << LINE_TARGET_INT}) with only target int asserting. A "
        f"source that answers for the other device is a handler that clears "
        f"the wrong one.")
    checks.check(
        "aux int raises the AUX source and only that one",
        seen.get("aux") == ((0, 1), 1 << LINE_AUX_INT),
        f"((target_irq, aux_irq), lines) was {seen.get('aux')!r}, expected "
        f"((0, 1), {1 << LINE_AUX_INT}) with only aux int asserting.")
    checks.check(
        "both asserting raises both sources",
        seen.get("both")
        == ((1, 1), (1 << LINE_TARGET_INT) | (1 << LINE_AUX_INT)),
        f"((target_irq, aux_irq), lines) was {seen.get('both')!r}. Two devices "
        f"asserting is two pending bits, and the handler takes both -- neither "
        f"waits on the other being decoded.")
    checks.check(
        "one device going away leaves the other's source asserted",
        seen.get("one_cleared") == ((1, 0), 1 << LINE_TARGET_INT),
        f"((target_irq, aux_irq), lines) was {seen.get('one_cleared')!r} after "
        f"aux dropped and target stayed. THIS IS WHY THERE ARE TWO SOURCES: on "
        f"one OR-ed line, clearing aux left the level high with nothing saying "
        f"target was the reason, and a handler that returned there re-fired "
        f"forever.")
    checks.check(
        "fault raises NEITHER source, and is still reported",
        seen.get("fault")
        == ((0, 0), (1 << LINE_TARGET_FAULT) | (1 << LINE_AUX_FAULT)),
        f"((target_irq, aux_irq), lines) was {seen.get('fault')!r}. `fault` "
        f"means something different from `int`, nothing in the firmware can "
        f"clear it, and it is meant to be distinguishable without a register "
        f"read.")
    checks.check(
        "reading the lines register twice gives the same answer",
        seen.get("fault_again") == seen.get("fault", (None, None))[1],
        f"first read {seen.get('fault', (None, None))[1]!r}, second "
        f"{seen.get('fault_again')!r}.")


def run_gpio_checks(checks, verbose):
    """The handover: fabric drives until the CPU takes a pin."""
    dut = gpio.Peripheral(pin_count=8, addr_width=4, data_width=8)
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        seen["reset_mode"] = await bus.read(GPIO_MODE)
        seen["reset_oe"] = [ctx.get(dut.pins[n].oe) for n in range(8)]

        # Put pin 0 -- the red LED -- in push-pull and light it.
        #
        # MODE IS TWO BYTES AND THE HIGH BYTE COMMITS THE WRITE. `csr.Multiplexer`
        # accumulates the low bytes of a wide register in a shadow and raises the
        # register's write strobe only on the LAST address of its range
        # (`amaranth_soc/csr/bus.py`, `if chunk_addr == reg_range.stop - 1`), so
        # a driver that writes byte 0 and stops has written nothing at all. The
        # firmware driver has the same obligation and the same comment.
        await bus.write(GPIO_MODE, MODE_PUSH_PULL)
        await bus.write(GPIO_MODE + 1, 0x00)
        await bus.write(GPIO_OUTPUT, 0x01)
        await ctx.tick()
        seen["taken_oe"] = ctx.get(dut.pins[0].oe)
        seen["taken_o"] = ctx.get(dut.pins[0].o)
        seen["neighbour_oe"] = ctx.get(dut.pins[1].oe)

        # SetClr, which is what a firmware uses so it never has to
        # read-modify-write a register an interrupt handler might also touch.
        # Two bits per pin: set at 2n, clear at 2n+1, so byte 0 covers pins 0..3
        # and byte 1 covers pins 4..7. Byte 1 commits.
        await bus.write(GPIO_SETCLR, 0b01 << 2)     # pin 1: set
        await bus.write(GPIO_SETCLR + 1, 0b10)      # pin 4: clear
        await ctx.tick()
        seen["after_setclr"] = await bus.read(GPIO_OUTPUT)

        # And the input path, which is the only reason this block has an Input
        # register worth reading: the USER button is on pin 7.
        ctx.set(dut.pins[7].i, 1)
        # input_stages defaults to 2, so the value needs two edges to arrive.
        await ctx.tick().repeat(4)
        seen["button"] = await bus.read(GPIO_INPUT)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "every pin comes out of reset with its output disabled",
        seen.get("reset_mode") == 0 and seen.get("reset_oe") == [0] * 8,
        f"Mode read {seen.get('reset_mode')!r}, oe {seen.get('reset_oe')!r}. "
        f"A pin that drives out of reset would fight the fabric's LED "
        f"diagnostic before any firmware ran.")
    checks.check(
        "a pin put in push-pull drives, and its neighbours do not",
        seen.get("taken_oe") == 1 and seen.get("taken_o") == 1
        and seen.get("neighbour_oe") == 0,
        f"pin 0 oe={seen.get('taken_oe')!r} o={seen.get('taken_o')!r}, "
        f"pin 1 oe={seen.get('neighbour_oe')!r}")
    checks.check(
        "set and clear are atomic and hit only the bits written",
        seen.get("after_setclr") == 0b011,
        f"Output read {seen.get('after_setclr')!r}, expected 0b011 -- pin 0 "
        f"still set, pin 1 set, pin 4 cleared (it was never set).")
    checks.check(
        "an input pin reaches the Input register",
        seen.get("button") is not None and (seen["button"] & 0x80),
        f"Input read {seen.get('button')!r} with pin 7 driven high.")


def run_sideband_checks(checks, verbose):
    dut = SidebandControl()
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        # The fabric's account, with nothing written.
        ctx.set(dut.fabric_state, 0b10)
        ctx.set(dut.fabric_events, 1)
        ctx.set(dut.fabric_error, 1)
        await ctx.tick()
        seen["fabric"] = (ctx.get(dut.state), ctx.get(dut.events),
                          ctx.get(dut.error), ctx.get(dut.own))
        seen["advertise_reset"] = ctx.get(dut.advertise)

        # Written, but not owned: still the fabric's.
        await bus.write(0, 0b0000_0001)
        seen["not_owned"] = (ctx.get(dut.state), ctx.get(dut.own))

        # Owned.
        await bus.write(0, 0b1001_0001)
        seen["owned"] = (ctx.get(dut.state), ctx.get(dut.events),
                         ctx.get(dut.error), ctx.get(dut.reconfigured),
                         ctx.get(dut.own))
        seen["readback"] = await bus.read(0)
        seen["readback_again"] = await bus.read(0)

        # The port request, on its own and without the ownership bit: it is an
        # action rather than a reported value, so `own` has no say in it.
        await bus.write(0, 0b0010_0000)
        seen["advertise_alone"] = (ctx.get(dut.advertise), ctx.get(dut.own),
                                   ctx.get(dut.state))
        await bus.write(0, 0b0000_0000)
        seen["advertise_cleared"] = ctx.get(dut.advertise)

        # The byte channel. `tx` is what a PING returns; it is outside `own` for
        # the same reason `advertise` is -- the fabric has no byte to be
        # overridden, so there is nothing to arbitrate.
        await bus.write(1, 0x5A)
        seen["message"] = ctx.get(dut.message)

        # And the receive side: latched, counted, and neither read clears
        # anything.
        seen["rx_reset"] = (await bus.read(2), await bus.read(3))
        for value in (0x2A, 0x2A, 0x7F):
            ctx.set(dut.received, value)
            ctx.set(dut.received_strobe, 1)
            await ctx.tick()
            ctx.set(dut.received_strobe, 0)
            await ctx.tick()
        seen["rx"] = await bus.read(2)
        seen["rxcnt"] = await bus.read(3)
        seen["rx_again"] = (await bus.read(2), await bus.read(3))

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "out of reset the fabric drives the sideband, not the CPU",
        seen.get("fabric") == (0b10, 1, 1, 0),
        f"(state, events, error, own) was {seen.get('fabric')!r}, expected "
        f"(0b10, 1, 1, 0). A design whose firmware never runs has to keep "
        f"answering this link with what the fabric knows.")
    checks.check(
        "writing the payload without the ownership bit changes nothing",
        seen.get("not_owned") == (0b10, 0),
        f"(state, own) was {seen.get('not_owned')!r}; the fabric's 0b10 should "
        f"still be reaching the responder.")
    checks.check(
        "setting the ownership bit hands the link to the CPU",
        seen.get("owned") == (0b01, 0, 0, 1, 1),
        f"(state, events, error, reconfigured, own) was {seen.get('owned')!r}, "
        f"expected (0b01, 0, 0, 1, 1)")
    checks.check(
        "and the register reads back what was written, twice",
        seen.get("readback") == 0b1001_0001
        and seen.get("readback_again") == seen.get("readback"),
        f"read back {seen.get('readback')!r} then "
        f"{seen.get('readback_again')!r}")
    checks.check(
        "the CONTROL port is not requested out of reset",
        seen.get("advertise_reset") == 0,
        f"advertise was {seen.get('advertise_reset')!r}. A bitstream that seized "
        f"CONTROL on configuration would take the port from the Apollo debug "
        f"interface used to recover a board that will not boot.")
    checks.check(
        "the port request is independent of the ownership bit",
        seen.get("advertise_alone") == (1, 0, 0b10),
        f"(advertise, own, state) was {seen.get('advertise_alone')!r}, expected "
        f"(1, 0, 0b10): asking for the port must not also take the payload from "
        f"the fabric.")
    checks.check(
        "and clearing it hands the port back",
        seen.get("advertise_cleared") == 0,
        f"advertise was {seen.get('advertise_cleared')!r} after the bit was "
        f"cleared; there would be no way to release CONTROL from firmware.")
    checks.check(
        "the outgoing byte reaches the link without the ownership bit",
        seen.get("message") == 0x5A,
        f"message was {seen.get('message')!r}, expected 0x5a")
    checks.check(
        "nothing has been received before anything is sent",
        seen.get("rx_reset") == (0, 0),
        f"(rx, rxcnt) was {seen.get('rx_reset')!r}; a count that is not zero out "
        f"of reset would read as traffic that never happened")
    checks.check(
        "a byte from Apollo is latched and counted",
        seen.get("rx") == 0x7F and seen.get("rxcnt") == 3,
        f"(rx, rxcnt) was ({seen.get('rx')!r}, {seen.get('rxcnt')!r}), expected "
        f"(0x7f, 3). The count is what tells a repeated byte from silence -- the "
        f"two 0x2a values must count twice.")
    checks.check(
        "and reading either of them changes neither",
        seen.get("rx_again") == (seen.get("rx"), seen.get("rxcnt")),
        f"read back {seen.get('rx_again')!r} the second time; a read-to-clear "
        f"flag is the hazard this peripheral is shaped to avoid")


def run_fabric_status_checks(checks, verbose):
    """The live window, byte by byte, in the order firmware reads it.

    What the map has to get right: `die` at +0x00 and `bus_fault` at +0x04, a
    32-bit value coming back little-endian from four byte reads, and the three
    counters inside `bus_fault` landing in the fields `src/info.rs` shifts them
    out of. An offset wrong here reports a plausible number from the wrong
    register on the board.
    """
    dut = FabricStatus()
    seen = {}

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        async def word(offset):
            # Low byte FIRST: reading the lowest address is what latches the
            # multiplexer's shadow, and firmware does the same.
            value = 0
            for index in range(4):
                value |= await bus.read(offset + index) << (8 * index)
            return value

        # Distinct in every field, so a swapped shift shows up as a wrong number
        # rather than as the same number.
        ctx.set(dut.fault_unclaimed, 0x12)
        ctx.set(dut.fault_timeouts, 0x34)
        ctx.set(dut.fault_worst, 0xBEEF)

        seen["die_low"] = await bus.read(0x00)
        seen["die_high"] = await bus.read(0x01)
        seen["bus_fault"] = await word(0x04)
        # Twice, because a register whose read changed something would be a
        # register a diagnostic could not poll.
        seen["bus_fault_again"] = await word(0x04)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "with no platform there is no DTR, and the register says so",
        seen.get("die_low") == 0 and seen.get("die_high") == 0,
        f"die read {seen.get('die_high')!r}{seen.get('die_low')!r}. The block is "
        f"a hard macro instantiated only when a device is being built for; bit 8 "
        f"({DIE_PRESENT:#x}) is what tells 'no block' from 'no conversion yet', "
        f"and it is the window's presence guard.")
    checks.check(
        "the fault counters land in their own fields",
        seen.get("bus_fault") == 0xBEEF_3412,
        f"read {seen.get('bus_fault')!r}, expected 0xbeef3412: unclaimed in "
        f"7..0, timeouts in 15..8, worst wait in 31..16. `worst` is the number "
        f"that keeps BUS_TIMEOUT_CYCLES honest (#409), so a shift wrong here "
        f"silently reports the margin as something else.")
    checks.check(
        "reading the window twice gives the same answer",
        seen.get("bus_fault_again") == seen.get("bus_fault"),
        f"{seen.get('bus_fault')!r} then {seen.get('bus_fault_again')!r}; no "
        f"read here may change anything")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR access and every bus edge")
    args = parser.parse_args()

    checks = Checks(emit)

    emit("i2c_master.I2CMaster -- transfers")
    run_i2c_checks(checks, args.verbose)
    emit()

    emit("i2c_master.I2CMaster -- bit timing")
    run_i2c_timing_checks(checks, args.verbose)
    emit()

    emit("amaranth_soc.gpio.Peripheral -- as this design wires it")
    run_gpio_checks(checks, args.verbose)
    emit()

    emit("sideband_csr.SidebandControl")
    run_sideband_checks(checks, args.verbose)
    emit()

    emit("i2c_mux.I2CBusMux -- bus select and a Type-C interrupt per device")
    run_i2c_mux_checks(checks, args.verbose)
    emit()

    emit("ulpi_window.UlpiRegisters -- against a model USB3343")
    run_ulpi_checks(checks, args.verbose)
    emit()

    emit("fabric_status.FabricStatus -- what the die and the bus are doing")
    run_fabric_status_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
