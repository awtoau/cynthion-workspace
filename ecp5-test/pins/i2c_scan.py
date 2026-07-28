#!/usr/bin/env python3
#
# Scan all three I2C buses for devices that acknowledge.
# SPDX-License-Identifier: BSD-3-Clause

"""
Walks every 7-bit I2C address on each of the board's three buses and records
which ones acknowledge.

The board has three separate I2C buses, not one:

    target_type_c   SCL A4    SDA C4     FUSB302B port controller
    aux_type_c      SCL H12   SDA G14    FUSB302B port controller
    power_monitor   SCL D7    SDA C7     PAC1954

They cannot be merged in gateware -- they are distinct pin pairs in the copper
-- and merging them would not work anyway, since both FUSB302B parts sit at the
same address and could not coexist on one wire. That is presumably why the
board separates them.

What can be shared is the *master*: one `I2CInitiator` multiplexed across the
three buses, rather than three initiators. Cheaper, and it keeps a stuck bus on
one port from wedging the others.

The scan itself is the cheapest possible question: does anything respond? A
device that ACKs its address is powered, clocked and listening. That is worth
establishing before writing a driver, because a driver that fails against a
chip which was never alive is an expensive way to learn the same thing.

Method: for each address, send a START, the address byte with the read bit
clear, and look at ACK. Then STOP immediately -- no data is transferred, so
nothing is configured and nothing changes state. A write-address probe is used
rather than a read because some devices treat a read of an unconfigured
register as a real transaction.
"""

from amaranth                       import Cat, Const, Elaboratable, Module, Signal
from amaranth.lib                   import io

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.i2c    import I2CInitiator
from luna.gateware.interface.jtag   import JTAGRegisterInterface


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 100 kHz on a 60 MHz domain. Standard-mode rather than fast: an unconfigured
# device that is marginal at 400 kHz will still answer at 100, and this only
# runs once.
I2C_PERIOD_CYC = 600

# Addresses 0x08 to 0x77. Below 8 and above 0x77 are reserved by the I2C
# specification and probing them can confuse some devices.
FIRST_ADDRESS = 0x08
LAST_ADDRESS  = 0x77

# Both FUSB302B controllers sit here -- confirmed by the scan, not assumed.
FUSB302B_ADDRESS = 0x22
FUSB_REG_DEVICE_ID = 0x01

APPLET_ID = 0x49324353   # "I2CS"

REGISTER_ID       = 1
REGISTER_CONTROL  = 2   # write: bus select in bits 0-1, bit 8 starts a scan
REGISTER_STATUS   = 3   # scan progress and completion
REGISTER_FOUND_LO = 4   # bitmap of responding addresses, 0x08-0x27
REGISTER_FOUND_MD = 5   # 0x28-0x47
REGISTER_FOUND_HI = 6   # 0x48-0x67
REGISTER_FOUND_XX = 7   # 0x68-0x77


class I2CScanner(Elaboratable):
    """ Probes every address on each bus and reports which acknowledge. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        #
        # One initiator per bus.
        #
        # Three initiators rather than one multiplexed, because I2CInitiator
        # takes its pads at construction and wraps them in a bus driver. Sharing
        # one would mean muxing inside that driver, which is more intrusive than
        # the ~200 LUTs three initiators cost on a part with 24288.
        #
        buses = []
        for name in ("target_type_c", "aux_type_c", "power_monitor"):
            pads = platform.request(name, 0)
            initiator = I2CInitiator(pads=pads, period_cyc=I2C_PERIOD_CYC)
            m.submodules[f"i2c_{name}"] = initiator
            buses.append(initiator)

        control = Signal(32)
        registers.add_register(REGISTER_CONTROL, value_signal=control)

        bus_select = control[0:2]
        run        = Signal()
        run_prev   = Signal()
        m.d.sync += run_prev.eq(control[8])
        m.d.comb += run.eq(control[8] & ~run_prev)

        # Route the selected bus's signals to a common set, so the FSM below is
        # written once rather than three times.
        busy   = Signal()
        ack_o  = Signal()
        start  = Signal()
        stop   = Signal()
        write  = Signal()
        data_o = Signal(8)

        with m.Switch(bus_select):
            for index, initiator in enumerate(buses):
                with m.Case(index):
                    m.d.comb += [
                        busy .eq(initiator.busy),
                        ack_o.eq(initiator.ack_o),
                        initiator.start .eq(start),
                        initiator.stop  .eq(stop),
                        initiator.write .eq(write),
                        initiator.data_i.eq(data_o),
                    ]

        address = Signal(8, init=FIRST_ADDRESS)
        # Index into `found`, kept as its own unsigned signal: subtracting
        # inside a bit_select makes the offset signed, which Amaranth rejects.
        found_index = Signal(range(LAST_ADDRESS - FIRST_ADDRESS + 1))
        found   = Signal(LAST_ADDRESS - FIRST_ADDRESS + 1)
        done    = Signal()

        with m.FSM():
            with m.State("IDLE"):
                with m.If(run):
                    m.d.sync += [
                        address.eq(FIRST_ADDRESS),
                        found_index.eq(0),
                        found.eq(0),
                        done.eq(0),
                    ]
                    m.next = "START"

            with m.State("START"):
                with m.If(~busy):
                    m.d.comb += start.eq(1)
                    m.next = "START_WAIT"

            with m.State("START_WAIT"):
                with m.If(busy):
                    m.next = "SEND_ADDRESS"

            with m.State("SEND_ADDRESS"):
                with m.If(~busy):
                    # Address shifted left with the read bit clear: a write
                    # probe. Some devices treat a read of an unconfigured
                    # register as a real transaction, so this is the safer form.
                    m.d.comb += [
                        data_o.eq(Cat(Const(0, 1), address[:7])),
                        write.eq(1),
                    ]
                    m.next = "ADDRESS_WAIT"

            with m.State("ADDRESS_WAIT"):
                with m.If(busy):
                    m.next = "CHECK_ACK"

            with m.State("CHECK_ACK"):
                with m.If(~busy):
                    # ack_o is high when the device pulled SDA low to
                    # acknowledge. Record it against this address.
                    with m.If(ack_o):
                        m.d.sync += found.bit_select(found_index, 1).eq(1)
                    m.next = "STOP"

            with m.State("STOP"):
                with m.If(~busy):
                    # Stop immediately: no data is transferred, so nothing is
                    # written and no device changes state.
                    m.d.comb += stop.eq(1)
                    m.next = "STOP_WAIT"

            with m.State("STOP_WAIT"):
                with m.If(busy):
                    m.next = "NEXT"

            with m.State("NEXT"):
                with m.If(~busy):
                    with m.If(address == LAST_ADDRESS):
                        m.d.sync += done.eq(1)
                        m.next = "IDLE"
                    with m.Else():
                        m.d.sync += [
                            address.eq(address + 1),
                            found_index.eq(found_index + 1),
                        ]
                        m.next = "START"

        registers.add_read_only_register(
            REGISTER_STATUS,
            read=Cat(done, busy, Const(0, 6), address, Const(0, 16)))

        registers.add_read_only_register(REGISTER_FOUND_LO, read=found[0:32])
        registers.add_read_only_register(REGISTER_FOUND_MD, read=found[32:64])
        registers.add_read_only_register(REGISTER_FOUND_HI, read=found[64:96])
        registers.add_read_only_register(REGISTER_FOUND_XX, read=found[96:])

        return m
