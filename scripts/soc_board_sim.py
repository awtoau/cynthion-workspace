#!/usr/bin/env python3
#
# Simulate the board peripherals: GPIO, the I2C master, and the sideband control.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the three peripherals added at `BOARD_BASE` in `vexii_hello_soc.py`.

    python3 scripts/soc_board_sim.py
    python3 scripts/soc_board_sim.py -v      # print every CSR access and bus edge

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/soc_board_sim.log`.

## What is checked, and why each check is here

**The I2C master** (`ecp5-test/riscv/i2c_master.py`) is driven against a model
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
link, and the claim is a single bit that resets clear.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_board_sim.log"

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))

from amaranth.hdl import Fragment
from amaranth.sim import Simulator
from amaranth_soc import gpio

from i2c_master import I2CMaster, prescale_for, SLOTS_BIT, SLOTS_COND
from sideband_csr import SidebandControl


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

    Copied from `soc_plic_sim.py`, and for the reason given there: a read is a
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


class Checks:
    def __init__(self, emit):
        self.emit = emit
        self.failures = []

    def check(self, name, ok, detail=""):
        self.emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            self.failures.append(name)
            for line in str(detail).splitlines():
                self.emit(f"        {line}")


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
        self._prev_scl = 1
        self._prev_sda = 1

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
                self.pull_low = True               # ACK
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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR access and every bus edge")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

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

        if checks.failures:
            emit(f"{len(checks.failures)} FAILED: {', '.join(checks.failures)}")
        else:
            emit("all checks passed")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 1 if checks.failures else 0


if __name__ == "__main__":
    sys.exit(main())
