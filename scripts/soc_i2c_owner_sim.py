#!/usr/bin/env python3
#
# Who owns the I2C bus, and who owns each device's protocol. See awtoau/cynthion-workspace#123.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the ownership rules in `firmware/cynthion-soc/src/bus.rs` and `power.rs`.

    python3 scripts/soc_i2c_owner_sim.py
    python3 scripts/soc_i2c_owner_sim.py -v            # every CSR access
    python3 scripts/soc_i2c_owner_sim.py --firmware DIR

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## The fault this is about

`power` used to issue its own read of the PAC1954. The background poll issues a
REFRESH every 50 ms and the part is unavailable for 1 ms afterwards -- it
acknowledges its address and then NACKs the register pointer -- so a hand-typed
command landed inside that window about one time in fifty and reported "no
acknowledge (register pointer)" on a bus that was working perfectly. It was found
on hardware by luck, after the fact, while integrating something else.

That is not a data race. Everything touching I2C runs in the main loop and the
Type-C handler only masks, deferring its I2C to normal context, so there is no
preemption and a mutex would protect nothing. It is a **device-state** race: the
PAC1954 has a state machine spanning transactions, and two callers were driving
it. The fix is one owner of that cycle, with everybody else reading what the owner
cached -- so the collision stops being rare and becomes impossible.

## What is checked here, and what each check is evidence for

**Sections 1-3 run the real gateware** -- `i2c_master.I2CMaster` and
`i2c_mux.I2CBusMux`, wired the way `top.py` wires them -- against
device models written here in Python, which react only to edges on SCL and SDA.
A model written in Amaranth would share the controller's idea of what an I2C bus
is and would agree with it whether or not either was right.

Each section runs the OLD arrangement and the NEW one against the same model in
the same simulation, and asserts that the old one fails. A check that only
demonstrates the fix passing is a check that would have passed before the fix.

  1. **A read driven into an open REFRESH window.** The model refuses the first
     byte after its address while it is busy, which is what the part was observed
     to do. A second caller reading on its own account is refused; a caller that
     takes the owner's cached sample issues nothing and so cannot be refused --
     the model sees no extra transaction at all.

  2. **Two owners interleaving on the same device.** The model's readable
     registers carry the number of the REFRESH that latched them, so a reader can
     tell whose sample it got. With two owners, a poll reads the sample the OTHER
     one asked for -- no error anywhere, just the wrong instant, which is the
     failure that would never have been noticed. With one owner every sample is
     the one its own previous REFRESH latched.

  3. **The mux select is not remembered across transactions.** Both FUSB302Bs
     answer `0x22` and both report `0x91`, so a stale select is ANSWERED rather
     than refused: the check reads a plausible, wrong `STATUS0` from the other
     port with no error raised. Naming the bus on every transfer reaches the
     intended device.

**Section 4 is structural, against the firmware source.** The three sections
above show what the arrangement must be; they cannot show that the Rust has it,
because the Rust does not run here. So the last section asserts the properties
that make the collision unreachable -- one call site for REFRESH, a private
transaction, a `power` command that touches no bus, a select written by every
transfer and by nothing else, and no wait-and-retry left over.

`--firmware DIR` points that section at another tree. Run it against a checkout
from before this change and section 4 fails, which is how a structural check earns
its place:

    python3 scripts/soc_i2c_owner_sim.py --firmware OLD/firmware/cynthion-soc/src
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware" / "cynthion-soc" / "src"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from devlog import emit  # noqa: E402

from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from peripherals.i2c_master import I2CMaster
from peripherals.i2c_mux import I2CBusMux, BUS_TARGET_C, BUS_AUX_C, BUS_POWER_MONITOR

# The slave model, the CSR accessor and the check reporter are shared with
# soc_board_sim.py rather than copied. A second I2C slave model would drift from
# the first, and the two would then disagree about what the bus does while both
# reported success.
from sim_check_harness import Checks
from soc_board_sim import (Bus, ModelSlave, CR_ACK, CR_RD, CR_STA,
                           CR_STO, CR_WR, CTR, CTR_EN, CR_SR, PRER, PRER_HI,
                           PRER_LO, SR_RXACK, SR_TIP, TXR_RXR)


# i2c_mux.I2CBusMux register offsets.
MUX_SELECT, MUX_LINES = 0, 1

# The parts on this board, at the addresses they are strapped to.
PAC_ADDRESS = 0x10
FUSB302_ADDRESS = 0x22

# PAC1954 registers this cares about: the Send Byte that latches a sample, and
# the start of the sixteen measurement bytes the poll reads in one go.
REG_REFRESH = 0x00
REG_VBUS1 = 0x07

# How many of those bytes a poll reads.
#
# THE PART HAS SIXTEEN; THE MECHANISM NEEDS FOUR. What the checks read of a block
# is byte 0 (the sample) and, once, that every byte is the same unrefreshed value
# -- so a pointer, an auto-increment and a block that returns the last latched
# sample are all proved by four bytes, and the other twelve are the same byte
# transfer repeated. On the wire a byte is 9 bits of 5*(PRER+1) = 180 sync cycles,
# and this file's whole cost is bytes on the wire: seven block reads of twelve
# surplus bytes each is 15,120 cycles of the 47,497 it simulated. `--soak`
# restores the real sixteen.
MEASUREMENT_BYTES = 4
SOAK_MEASUREMENT_BYTES = 16

# How many polls the one-owner section makes in a row.
#
# The mechanism is "poll n reads what poll n-1's REFRESH latched", which happens
# once between two polls. A third poll asserts it a second time, which is what a
# soak is for. Each surplus poll is a block read plus a PAST_WINDOW_CYCLES wait.
POLLS = 2
SOAK_POLLS = 3

# FUSB302B registers. Both parts answer the SAME identity byte, which is the
# entire reason a stale bus select is dangerous rather than merely wrong.
REG_DEVICE_ID = 0x01
REG_STATUS0 = 0x40
DEVICE_ID_FUSB302B = 0x91

# How long the PAC1954 model refuses business after a REFRESH, in `sync` cycles.
#
# The real part is unavailable for 1 ms (DS20006539B 5.2) and a byte at 80 kHz is
# 112 us, so the window is about nine byte times. Here a byte is 9 bits of
# 5*(PRER+1) = 20 cycles, or 180 cycles, so 1600 is the same relationship at this
# prescale: a read issued immediately after a REFRESH has its register pointer
# inside the window, and one issued after an explicit wait does not. Nothing here
# depends on the exact value -- only on those two facts, which the first two
# checks assert directly rather than assume.
REFRESH_WINDOW_CYCLES = 1600

# Long enough to be past the window with margin, for the checks that mean to be
# outside it. Not a timeout: it is the wait a driver would have had to do, made
# explicit so that "outside the window" is a fact of the test and not a hope.
PAST_WINDOW_CYCLES = REFRESH_WINDOW_CYCLES + 400


class ThreeBuses(Elaboratable):
    """One controller, one mux, three buses -- as `top.py` wires it.

    The fan-out is reproduced here in Amaranth rather than in the testbench,
    because it is part of what is being checked: an unselected bus is driven
    IDLE, and the select the mux applies is the one the controller's edges reach.
    `sda_i[k]` is driven by the testbench with the wired-AND of that bus.
    """

    def __init__(self):
        self.i2c = I2CMaster()
        self.mux = I2CBusMux()
        self.scl_o = [Signal(name=f"scl_o_{k}") for k in range(3)]
        self.sda_oe = [Signal(name=f"sda_oe_{k}") for k in range(3)]
        self.sda_i = [Signal(init=1, name=f"sda_i_{k}") for k in range(3)]

    def elaborate(self, platform):
        m = Module()
        m.submodules.i2c = self.i2c
        m.submodules.mux = self.mux

        # The select may only move while the controller is idle. Without this the
        # mux would be free to switch pin-sets between a START and its STOP.
        m.d.comb += self.mux.idle.eq(self.i2c.idle)

        for k in range(3):
            chosen = self.mux.select == k
            m.d.comb += [
                self.scl_o[k].eq(Mux(chosen, self.i2c.scl_o, 1)),
                self.sda_oe[k].eq(chosen & self.i2c.sda_oe),
            ]

        # An unselected bus must not be able to answer. Default high, so a select
        # value with no bus behind it reads as an idle bus rather than as SDA
        # held low.
        m.d.comb += self.i2c.sda_i.eq(1)
        for k in range(3):
            with m.If(self.mux.select == k):
                m.d.comb += self.i2c.sda_i.eq(self.sda_i[k])

        return m


class RegisterSlave(ModelSlave):
    """A slave with a register pointer and auto-increment, as these parts have.

    `ModelSlave` answers from a fixed list of bytes, which is enough to check a
    controller. It is not enough to check ownership: that needs a device whose
    answer depends on which register was asked for and on what has happened to it
    since, because the whole failure mode is a plausible answer to the wrong
    question.
    """

    def __init__(self, address):
        super().__init__(address)
        self._pointer = 0
        self._served = 0
        self._mark = 0             # index into `written` where this START began
        self._prev_starts = 0
        self._prev_stops = 0

    def register(self, address):
        """What this device holds at `address`. Overridden per part."""
        raise NotImplementedError

    def transaction(self, payload):
        """Called at the STOP, with the bytes written since the START."""

    def step(self, scl, sda):
        self.tick()
        super().step(scl, sda)
        if self.starts != self._prev_starts:
            self._prev_starts = self.starts
            self._mark = len(self.written)
            self._served = 0
        if self.stops != self._prev_stops:
            self._prev_stops = self.stops
            self.transaction(self.written[self._mark:])

    def tick(self):
        """One cycle of whatever this device is doing on its own. Overridden."""

    def ack_write(self, index, byte):
        if index == 0:
            self._pointer = byte
        return True

    def _load_byte(self):
        # Auto-increment within the read, which is what makes a sixteen-byte
        # block read of the measurement registers a single transaction.
        self._out = self.register(self._pointer + self._served) & 0xff
        self._served += 1
        self.pull_low = not ((self._out >> 7) & 1)
        self._bits = 1


class ModelPac1954(RegisterSlave):
    """A PAC1954 with the two behaviours that make it need an owner.

    **REFRESH latches, and reads before the first one are not measurements.** The
    measurement registers only change on command, so what they hold at power-up is
    not a reading of anything.

    **The part is busy for a while afterwards.** It acknowledges its address and
    then NACKs the first byte after it, which is where the register pointer of a
    read goes. That is what was observed on the board, and it is why the failure
    reads as "no acknowledge (register pointer)" rather than as silence.

    Every latched byte carries the number of the REFRESH that latched it, so a
    test can ask the question the console cannot: whose sample is this?
    """

    #: What the measurement registers hold before any REFRESH. Chosen so it can
    #: never be confused with a latched sample, whose sequence numbers start at 1.
    UNREFRESHED = 0x00

    def __init__(self, window_cycles=REFRESH_WINDOW_CYCLES):
        super().__init__(PAC_ADDRESS)
        self.window_cycles = window_cycles
        self.sequence = 0          # REFRESHes this part has been sent
        self.latched = None        # which one the readable registers hold
        self.window = 0            # cycles left in which it will refuse
        self.refusals = 0          # first bytes NACKed because it was busy
        self.refreshes = 0

    def tick(self):
        if self.window:
            self.window -= 1

    def transaction(self, payload):
        # SMBus Send Byte of register 0. One byte and no data: the command IS the
        # register address.
        if payload == [REG_REFRESH]:
            self.refreshes += 1
            self.sequence += 1
            self.latched = self.sequence
            self.window = self.window_cycles

    def ack_write(self, index, byte):
        if index == 0 and self.window:
            # The address was acknowledged; this is not. The model does not
            # distinguish a register pointer from a command byte, because on the
            # wire they are the same thing -- the first byte after the address --
            # and inventing a difference the datasheet does not state would make
            # this model evidence for something nobody measured.
            self.refusals += 1
            return False
        return super().ack_write(index, byte)

    def register(self, address):
        if self.latched is None:
            return self.UNREFRESHED
        # Every byte of the image identifies the REFRESH that latched it, so any
        # part of a block read answers "whose sample is this".
        return (self.latched + address - REG_VBUS1) & 0xff


class ModelFusb302(RegisterSlave):
    """One of the two Type-C controllers.

    Both are at `0x22` and both report `0x91`, so nothing in a reply says which
    one answered. `status0` is the only thing that differs, and only because the
    two ports genuinely have different things plugged into them -- which is
    exactly the situation in which reading the wrong one gives an answer that
    looks right.
    """

    def __init__(self, status0):
        super().__init__(FUSB302_ADDRESS)
        self.status0 = status0

    def register(self, address):
        if address == REG_DEVICE_ID:
            return DEVICE_ID_FUSB302B
        if address == REG_STATUS0:
            return self.status0
        return 0xff


class Controller:
    """The firmware's `Bus`, in Python, over the two CSR windows.

    The transaction shapes are `src/bus/i2c.rs` byte for byte, including which
    acknowledge failure produces which error, because the point of the first
    section is which one the part gives.

    `select` is a parameter of every transfer here for the same reason it is in
    the firmware. `StickyController` below is what the alternative looks like.
    """

    def __init__(self, ctx, dut, verbose=False):
        self.ctx = ctx
        self.i2c = Bus(ctx, dut.i2c.bus, verbose)
        self.mux = Bus(ctx, dut.mux.bus, verbose)

    async def setup(self):
        await self.i2c.write(PRER_LO, PRER & 0xff)
        await self.i2c.write(PRER_HI, PRER >> 8)
        await self.i2c.write(CTR, CTR_EN)

    async def select(self, bus):
        await self.mux.write(MUX_SELECT, bus)

    async def idle(self, cycles):
        """Do nothing on the bus for a while, and let the devices tick."""
        await self.ctx.tick().repeat(cycles)

    async def _command(self, command, data=None):
        if data is not None:
            await self.i2c.write(TXR_RXR, data)
        await self.i2c.write(CR_SR, command)
        for _ in range(20000):
            status = await self.i2c.read(CR_SR)
            if not status & SR_TIP:
                return status
        raise AssertionError("the controller never dropped TIP")

    async def _release(self):
        # Every error path, as in the firmware: a transfer abandoned between its
        # START and its STOP leaves the peripheral holding the bus, and the next
        # probe then fails for a reason that has nothing to do with the device.
        await self._command(CR_STO)

    async def read_registers(self, bus, address, register, count):
        """Returns (bytes, None) or (None, error). Errors are `i2c::Error` names."""
        await self.select(bus)
        status = await self._command(CR_STA | CR_WR, address << 1)
        if status & SR_RXACK:
            await self._release()
            return None, "nack-address"
        status = await self._command(CR_WR, register)
        if status & SR_RXACK:
            await self._release()
            return None, "nack-register"
        status = await self._command(CR_STA | CR_WR, (address << 1) | 1)
        if status & SR_RXACK:
            await self._release()
            return None, "nack-restart"

        out = []
        for index in range(count):
            last = index == count - 1
            await self._command(CR_RD | CR_ACK | CR_STO if last else CR_RD)
            out.append(await self.i2c.read(TXR_RXR))
        return out, None

    async def send_byte(self, bus, address, byte):
        """SMBus Send Byte. The PAC1954's REFRESH is one."""
        await self.select(bus)
        status = await self._command(CR_STA | CR_WR, address << 1)
        if status & SR_RXACK:
            await self._release()
            return "nack-address"
        status = await self._command(CR_WR | CR_STO, byte)
        if status & SR_RXACK:
            return "nack-command"
        return None


class StickyController(Controller):
    """The arrangement this design rejects: a select that is remembered.

    Selects only when told to, so a caller can "know" which bus it left the
    controller on. Exists to be run side by side with the real thing in section
    3 -- a check that only exercises the fix cannot show the fix was needed.
    """

    def __init__(self, ctx, dut, verbose=False):
        super().__init__(ctx, dut, verbose)
        # Whatever the mux came out of reset pointing at. A cache that starts by
        # believing something it never wrote is part of the trap.
        self._selected = BUS_POWER_MONITOR

    async def read_registers(self, bus, address, register, count):
        return await Controller.read_registers(self, self._selected, address,
                                               register, count)

    async def select(self, bus):
        self._selected = bus
        await self.mux.write(MUX_SELECT, bus)


def make_sim(devices, verbose=False):
    """A simulator with the controller, the mux, and a device on each bus.

    `devices` is indexed by bus select value; `None` is a bus with nothing on it.
    """
    dut = ThreeBuses()

    async def wires(ctx):
        while True:
            for k in range(3):
                device = devices[k]
                master_low = (ctx.get(dut.sda_oe[k])
                              and not ctx.get(dut.i2c.sda_o))
                low = master_low or (device is not None and device.pull_low)
                ctx.set(dut.sda_i[k], 0 if low else 1)
                if device is not None:
                    device.step(ctx.get(dut.scl_o[k]), 0 if low else 1)
            await ctx.tick()

    def build(testbench):
        sim = Simulator(Fragment.get(dut, None))
        sim.add_clock(1e-6)
        sim.add_testbench(wires, background=True)
        sim.add_testbench(testbench)
        return sim

    return dut, build


def buses(pac=None, target=None, aux=None):
    """Devices by select value, in the mux's order."""
    devices = [None, None, None]
    devices[BUS_TARGET_C] = target
    devices[BUS_AUX_C] = aux
    devices[BUS_POWER_MONITOR] = pac
    return devices


# --- 1. a read driven into an open REFRESH window ---------------------------

def run_window_checks(checks, verbose):
    pac = ModelPac1954()
    dut, build = make_sim(buses(pac=pac), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Controller(ctx, dut, verbose)
        await driver.setup()

        # Prime the part: one REFRESH so there is something to read at all.
        await driver.send_byte(BUS_POWER_MONITOR, PAC_ADDRESS, REG_REFRESH)
        await driver.idle(PAST_WINDOW_CYCLES)

        # THE COLLISION. A second caller reads on its own account immediately
        # after the owner's REFRESH -- which is what `power` used to do, and what
        # a hand-typed command did about one time in fifty.
        await driver.send_byte(BUS_POWER_MONITOR, PAC_ADDRESS, REG_REFRESH)
        data, error = await driver.read_registers(
            BUS_POWER_MONITOR, PAC_ADDRESS, REG_VBUS1, MEASUREMENT_BYTES)
        seen["inside"] = error
        seen["refusals"] = pac.refusals

        # The same read, outside the window. This is what the 2 ms wait bought,
        # and it is the check that stops the one above from passing for the wrong
        # reason -- a part that never answered would also "fail inside".
        await driver.idle(PAST_WINDOW_CYCLES)
        data, error = await driver.read_registers(
            BUS_POWER_MONITOR, PAC_ADDRESS, REG_VBUS1, MEASUREMENT_BYTES)
        seen["outside"] = error
        seen["outside_data"] = data

        # THE FIX. The owner polls; a reader takes the owner's cached sample. The
        # count of transactions the part saw is the evidence: a cached read adds
        # none, so there is no arrival to be badly timed.
        before = pac.starts
        cached = seen["outside_data"]
        await driver.idle(200)
        seen["cached"] = cached
        seen["starts_added_by_cached_read"] = pac.starts - before

    build(testbench).run()

    checks.check(
        "a read inside the REFRESH window is refused at the register pointer",
        seen.get("inside") == "nack-register" and seen.get("refusals", 0) >= 1,
        f"the read returned {seen.get('inside')!r} and the part refused "
        f"{seen.get('refusals')!r} byte(s). Expected 'nack-register': the part "
        f"acknowledges its ADDRESS and then declines the pointer, which is why "
        f"the console said 'no acknowledge (register pointer)' on a bus that was "
        f"working. A plain 'nack-address' here would mean the model is not "
        f"reproducing the fault at all.")
    checks.check(
        "the same read outside the window succeeds",
        seen.get("outside") is None and seen.get("outside_data"),
        f"the read returned {seen.get('outside')!r} with data "
        f"{seen.get('outside_data')!r}. If this fails, the check above proves "
        f"nothing -- a part that never answers is refused everywhere.")
    checks.check(
        "a reader taking the cached sample starts no transaction to be refused",
        seen.get("starts_added_by_cached_read") == 0
        and bool(seen.get("cached")),
        f"a cached read added {seen.get('starts_added_by_cached_read')!r} "
        f"START(s) to the part. It must add none: that is the whole of the fix, "
        f"and it is why the collision becomes impossible rather than rare.")


# --- 2. two owners interleaving on the same device --------------------------

async def one_cycle(driver, expect_latched=None):
    """A poll: read the sample the last REFRESH latched, then ask for the next.

    The order is the firmware's and it is not an optimisation -- reading first
    gives the part a whole interval to settle instead of none.
    """
    data, error = await driver.read_registers(
        BUS_POWER_MONITOR, PAC_ADDRESS, REG_VBUS1, MEASUREMENT_BYTES)
    await driver.send_byte(BUS_POWER_MONITOR, PAC_ADDRESS, REG_REFRESH)
    return data, error


def run_two_owner_checks(checks, verbose):
    pac = ModelPac1954()
    dut, build = make_sim(buses(pac=pac), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Controller(ctx, dut, verbose)
        await driver.setup()

        # The very first read predates every REFRESH. The registers hold what
        # they held at power-up, which is not a measurement of anything -- so the
        # firmware discards it, and this is the model of why.
        data, error = await one_cycle(driver)
        seen["first"] = data
        await driver.idle(PAST_WINDOW_CYCLES)

        # ONE OWNER. Polls in a row: each must read the sample that its own
        # previous REFRESH asked for. `own` records (expected, got) per poll.
        own = []
        for _ in range(POLLS):
            expected = pac.sequence          # the REFRESH that latched what we
            data, error = await one_cycle(driver)   # are about to read
            own.append((expected, None if data is None else data[0]))
            await driver.idle(PAST_WINDOW_CYCLES)
        seen["one_owner"] = own

        # TWO OWNERS. A second caller runs its own REFRESH cycle in between, far
        # enough from the first that neither is refused -- so there is no error
        # anywhere and nothing on the console to notice. What it takes is the
        # sample the first owner asked for.
        expected = pac.sequence
        await one_cycle(driver)              # the intruder: reads AND refreshes
        await driver.idle(PAST_WINDOW_CYCLES)
        data, error = await one_cycle(driver)   # the owner's next poll
        seen["two_owners"] = (expected, None if data is None else data[0], error)

    build(testbench).run()

    first = seen.get("first")
    checks.check(
        "the first read predates every REFRESH and is not a measurement",
        first is not None and set(first) == {ModelPac1954.UNREFRESHED},
        f"the first read returned {first!r}; expected every byte to be "
        f"{ModelPac1954.UNREFRESHED:#04x}, the value the registers hold before "
        f"the part has ever been told to latch. The firmware must discard this "
        f"one -- announcing it puts a fabricated connect event on the console at "
        f"every boot.")

    own = seen.get("one_owner") or []
    checks.check(
        "with one owner, every poll reads the sample its own REFRESH latched",
        len(own) == POLLS and all(got == expected for expected, got in own),
        f"(expected, got) per poll: {own!r}. A mismatch means a sample arrived "
        f"from an instant nobody asked about -- every individual number is "
        f"plausible, which is why this cannot be seen from the console.")

    expected, got, error = seen.get("two_owners", (None, None, "not run"))
    checks.check(
        "with two owners, a poll reads the OTHER owner's sample and no error",
        got is not None and got != expected and error is None,
        f"expected the poll to receive sample {expected!r} and it received "
        f"{got!r}, with error {error!r}. This check asserts the OLD arrangement "
        f"FAILS. If the two agree here, the model is not reproducing the "
        f"interleaving and the check above is worth nothing.")


# --- 3. the mux select is not remembered ------------------------------------

# What each port's FUSB302B says about itself. Different only because the two
# ports have different things plugged in -- which is the point: the wrong answer
# is a real answer from a real device.
TARGET_STATUS0 = 0x80        # VBUSOK, nothing on CC
AUX_STATUS0 = 0x03           # no VBUS, vRd-3.0A


def run_select_checks(checks, verbose):
    target = ModelFusb302(TARGET_STATUS0)
    aux = ModelFusb302(AUX_STATUS0)
    pac = ModelPac1954()
    dut, build = make_sim(buses(pac=pac, target=target, aux=aux), verbose)
    seen = {}

    async def testbench(ctx):
        driver = Controller(ctx, dut, verbose)
        await driver.setup()

        # Both parts, asked who they are. This is the check that makes the next
        # two matter: if the two identities differed, a stale select would be
        # caught by the first thing that looked.
        data, _ = await driver.read_registers(BUS_TARGET_C, FUSB302_ADDRESS,
                                              REG_DEVICE_ID, 1)
        seen["target_id"] = None if data is None else data[0]
        data, _ = await driver.read_registers(BUS_AUX_C, FUSB302_ADDRESS,
                                              REG_DEVICE_ID, 1)
        seen["aux_id"] = None if data is None else data[0]

        # THE OLD ARRANGEMENT. A driver that selected TARGET once and then talked
        # to "the controller" -- with the power monitor's 50 ms poll, or the other
        # port's driver, moving the select in between.
        sticky = StickyController(ctx, dut, verbose)
        await sticky.select(BUS_TARGET_C)
        await sticky.select(BUS_AUX_C)           # somebody else's transaction
        data, error = await sticky.read_registers(BUS_TARGET_C,
                                                  FUSB302_ADDRESS,
                                                  REG_STATUS0, 1)
        seen["stale"] = (None if data is None else data[0], error)

        # THE FIX. The bus is named on the call, and written immediately before
        # the transfer, so what happened in between does not matter.
        await driver.read_registers(BUS_AUX_C, FUSB302_ADDRESS, REG_DEVICE_ID, 1)
        data, error = await driver.read_registers(BUS_TARGET_C,
                                                  FUSB302_ADDRESS,
                                                  REG_STATUS0, 1)
        seen["named"] = (None if data is None else data[0], error)

    build(testbench).run()

    checks.check(
        "both Type-C controllers answer 0x22 with the same identity byte",
        seen.get("target_id") == DEVICE_ID_FUSB302B
        and seen.get("aux_id") == DEVICE_ID_FUSB302B,
        f"target reported {seen.get('target_id')!r} and aux "
        f"{seen.get('aux_id')!r}; both must be "
        f"{DEVICE_ID_FUSB302B:#04x}. If they differ, everything below is about a "
        f"fault that cannot happen on this board.")

    value, error = seen.get("stale", (None, "not run"))
    checks.check(
        "a stale select is ANSWERED, not refused -- with the wrong port's data",
        value == AUX_STATUS0 and error is None,
        f"the read returned {value!r} with error {error!r}; expected "
        f"{AUX_STATUS0:#04x} and no error. This check asserts the OLD "
        f"arrangement FAILS, and fails SILENTLY: an error here would be a fault "
        f"anyone could find, and the reason the select cannot be cached is that "
        f"there is no error to find.")

    value, error = seen.get("named", (None, "not run"))
    checks.check(
        "naming the bus on every transfer reaches the intended device",
        value == TARGET_STATUS0 and error is None,
        f"the read returned {value!r} with error {error!r}; expected "
        f"{TARGET_STATUS0:#04x} from the TARGET controller after the AUX one had "
        f"been talked to.")


# --- 4. the firmware has the structure the sections above require ------------

def source(root, name):
    path = root / name
    return path.read_text() if path.exists() else ""


def strip_comments(text):
    """Rust line comments only. Enough here: none of the patterns below are
    inside a string, and a comment that MENTIONS a call is not a call."""
    return "\n".join(re.sub(r"//.*", "", line) for line in text.splitlines())


def run_firmware_checks(checks, root):
    power = strip_comments(source(root, "power.rs"))
    main = strip_comments(source(root, "main.rs"))
    bus = strip_comments(source(root, "bus.rs"))
    files = {path.name: strip_comments(path.read_text())
             for path in sorted(root.rglob("*.rs"))}

    checks.check(
        "the firmware source is where this expects it",
        bool(power) and bool(main),
        f"no power.rs / main.rs under {root}. A structural check that reads "
        f"nothing passes forever; it must say so instead.")

    # One call site for REFRESH. Anything else is a second owner of the cycle,
    # whatever it is called and however careful it is.
    refresh_sites = [name for name, text in files.items()
                     for _ in re.findall(r"send_byte\([^)]*REG_REFRESH", text)]
    checks.check(
        "the PAC1954's REFRESH has exactly one call site in the firmware",
        refresh_sites == ["power.rs"],
        f"REFRESH is sent from {refresh_sites!r}; expected exactly one site, in "
        f"power.rs. Two callers of a cycle with a window in it is issue #123.")

    # ...and it is not reachable from outside its module, so a second one cannot
    # be added by accident from somewhere else.
    transfer = re.search(r"\n    (pub )?fn transfer\(", power)
    checks.check(
        "the transaction that issues it is private, and `poll` is its one caller",
        transfer is not None and transfer.group(1) is None
        and len(re.findall(r"self\.transfer\(", power)) == 1,
        f"expected a private `fn transfer` in power.rs with exactly one call; "
        f"found declaration "
        f"{transfer.group(0).strip() if transfer else None!r} and "
        f"{len(re.findall(r'self.transfer.', power))} call(s). Private is what "
        f"makes 'one owner' a fact rather than a convention.")

    # The command prints what the poller cached.
    #
    # Stated as an allowlist of what it may ask the monitor for, rather than a
    # list of bus calls to forbid. A driver method that starts a transfer does
    # not have to look like one from the call site -- the version this replaced
    # reached the bus as `devices.power.read(&bus, &selector)` -- so forbidding
    # `read_registers` here would have passed against the code that had the bug.
    # An ALLOWLIST, and it has to be: this check cannot see inside a method, so
    # it trusts that everything named here reads `Monitor`'s own fields and no
    # bus. Adding a name is asserting that, and the assertion is what the check
    # is worth.
    #
    # The `*_cached` accessors return the alert limits, debounce and enable mask
    # as the SETTERS recorded them -- `alert_set_limit`, `alert_set_nsamples`
    # and `alert_arm` write the cache while they write the part, and the
    # auto-backoff goes through the same setters. So `power` shows the part's
    # values without fetching them, which is the same arrangement `latest` has
    # for the measurement.
    #
    # `power alert` is the authoritative view and DOES read the part. It is a
    # different command for that reason, and it is not covered here.
    READS_ONLY = {
        "latest", "age", "floor", "set_floor", "phase", "failures",
        "alert_limit_cached", "alert_samples_cached", "alert_enable_cached",
        "alert_history",
    }
    body = re.search(r"fn board_power\(.*?\n\}", main, re.S)
    body = body.group(0) if body else ""
    asked = set(re.findall(r"\.power\.(\w+)", body))
    touches = re.findall(r"\b(?:read_registers|send_byte|write_register|probe|"
                         r"I2c::new|Mux::new)\s*\(", body)
    checks.check(
        "the `power` command reaches no bus",
        bool(body) and not touches and asked <= READS_ONLY,
        f"board_power calls {touches!r} and asks the monitor for "
        f"{sorted(asked - READS_ONLY)!r} beyond what a reader may. It must print "
        f"`Monitor::latest`: a command that reads the part on its own account is "
        f"the caller that collides with the poll's REFRESH window, and the 2 ms "
        f"retry that was deleted is what having one costs.")

    # The age. A cached number with no age is a stalled poller presenting as
    # plausible data, which is worse than the collision it replaced.
    checks.check(
        "the command prints how old the cached sample is",
        "power::Age::" in main and "sampled" in main
        and "pub fn age(" in power,
        "board_power must print `Monitor::age`. Without it a poll that has "
        "stopped shows four plausible voltages and nothing to contradict them.")

    # Nothing waits and retries any more.
    checks.check(
        "no wait-and-retry is left in the power driver",
        "clock::millis(2)" not in power
        and not re.search(r"while .*elapsed\(clock::now\(\)\)", power),
        "power.rs still spins waiting out a window. That retry existed only to "
        "survive a collision that one owner makes impossible, and a retry that "
        "can never fire is a lie about how the system works.")

    # The select: written by every transfer in `Bus`, and by nothing else.
    for method in ("probe", "read_registers", "write_register",
                   "write_registers", "send_byte"):
        found = re.search(r"pub fn " + method + r"\(.*?\n    \}", bus, re.S)
        body = found.group(0) if found else ""
        checks.check(
            f"`Bus::{method}` writes the select before it transfers",
            bool(body) and "self.mux.select(bus)" in body,
            f"no `self.mux.select(bus)` in `Bus::{method}`. Every transfer must "
            f"name its bus and write it: a stale select is answered, not "
            f"refused, by a part that looks exactly like the intended one.")

    elsewhere = sorted(name for name, text in files.items()
                       if name not in ("bus.rs", "mux.rs")
                       and re.search(r"\.select\(", text))
    checks.check(
        "nothing outside the bus module selects a bus",
        elsewhere == [],
        f"{elsewhere!r} write the mux select. A driver that selects and then "
        f"transfers in two steps has a window in which something else can move "
        f"it, and the failure is a plausible answer rather than an error.")

    constructors = sorted(name for name, text in files.items()
                          if name not in ("bus.rs", "i2c.rs", "mux.rs")
                          and re.search(r"\b(I2c|Mux)::new\(", text))
    checks.check(
        "nothing outside the bus module constructs a controller",
        constructors == [],
        f"{constructors!r} construct an I2c or a Mux directly. One owner of the "
        f"bus is enforced by the modules being private to `bus`, not by everyone "
        f"remembering; a second handle is a second owner.")


def main():
    # Rebound rather than threaded through three sections: how many bytes a poll
    # reads and how many polls it makes are properties of the run.
    global MEASUREMENT_BYTES, POLLS
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true",
                        help="the real %d-byte block read rather than %d, and %d "
                             "polls rather than %d. Same checks, more wire time."
                             % (SOAK_MEASUREMENT_BYTES, MEASUREMENT_BYTES,
                                SOAK_POLLS, POLLS))
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR access")
    parser.add_argument("--firmware", type=Path, default=FIRMWARE,
                        help="firmware source to check structurally "
                             "(default: firmware/cynthion-soc/src)")
    args = parser.parse_args()

    if args.soak:
        MEASUREMENT_BYTES = SOAK_MEASUREMENT_BYTES
        POLLS = SOAK_POLLS

    checks = Checks(emit)

    emit("1. a read driven into an open REFRESH window")
    run_window_checks(checks, args.verbose)
    emit()

    emit("2. two owners interleaving on the same device")
    run_two_owner_checks(checks, args.verbose)
    emit()

    emit("3. the mux select, not remembered across transactions")
    run_select_checks(checks, args.verbose)
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
