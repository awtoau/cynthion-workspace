#!/usr/bin/env python3
#
# One I2C master, multiplexed across the board's three buses, as a CSR peripheral.
# See awtoau/cynthion-workspace#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
A single I2C controller reaching all three of the board's I2C buses.

**Simulation only.** The design that runs on hardware is
`gateware/soc/peripherals/i2c_mux.py` plus `peripherals/i2c_master.py`, which has the OpenCores
register map and a CPU behind it. Keep this for the sim tests in
`test_multiplexed.py`.

r1.4 has three physically separate I2C buses, and the split is forced:

    select  bus              pins        device
    0       target_type_c    A4 / C4     FUSB302B @ 0x22
    1       aux_type_c       H12 / G14   FUSB302B @ 0x22
    2       power_monitor    D7 / C7     PAC1954  @ 0x10

Both FUSB302Bs answer 0x22, so they cannot share a bus. One `I2CInitiator`
fans out to all three pin-sets under a 2-bit select.

## CSR, not a JTAG register file

A peripheral bound to `JTAGRegisterInterface` cannot attach to a CPU bus. A CSR
peripheral serves both, with JTAG as a bridge in front of the same registers:

    host over JTAG    ->  JTAG-to-CSR bridge  ->|
                                                |-> this peripheral
    CPU over Wishbone                        ->|

## Registers

    0x00  BUS      w   which bus, values above
    0x01  ADDRESS  w   7-bit device address
    0x02  REGISTER w   register within the device
    0x03  CONTROL  w   1 starts a read, 2 starts a write
    0x04  DATA     rw  byte read, or byte to write
    0x05  STATUS   r   bit 0 busy, bit 1 last transaction acked

**`STATUS.acked` is what makes a self-test mean anything.** A read of an absent
device returns 0xff indistinguishably from a device holding 0xff; only the ACK
tells them apart.
"""

from amaranth import Elaboratable, Module, Signal, Cat, Mux
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from amaranth_soc import csr


# Bus select values. Order matches the platform resource names rather than
# anything electrical, so it is stable if a bus is added.
BUS_TARGET_C = 0
BUS_AUX_C = 1
BUS_POWER_MONITOR = 2

BUS_RESOURCES = {
    BUS_TARGET_C: ("target_type_c", 0),
    BUS_AUX_C: ("aux_type_c", 0),
    BUS_POWER_MONITOR: ("power_monitor", 0),
}

# Known devices, for the test code's benefit rather than the gateware's.
DEVICE_ADDRESSES = {
    BUS_TARGET_C: 0x22,          # FUSB302B
    BUS_AUX_C: 0x22,             # FUSB302B, same address, other bus
    BUS_POWER_MONITOR: 0x10,     # PAC1954
}

CONTROL_START_READ = 1
CONTROL_START_WRITE = 2


class MultiplexedI2C(wiring.Component):
    """One I2C master, selectable across three buses, behind a CSR file.

    Attributes
    ----------
    bus : csr.Signature
        The register file described in the module docstring.
    """

    def __init__(self, *, period_cyc=600):
        # 600 cycles at 60 MHz is 100 kHz, the rate every device on this board
        # supports. The FUSB302B and PAC1954 both do 400 kHz, so this is
        # conservative -- chosen because a self-test is not throughput-bound and
        # 100 kHz has the most margin against pull-up strength, which is
        # PULLMODE="NONE" on these pins and therefore board pull-ups only.
        self.period_cyc = period_cyc

        self._bus_select = csr.Register(
            {"select": csr.Field(csr.action.RW, 2)}, access="rw")
        self._address = csr.Register(
            {"address": csr.Field(csr.action.RW, 7)}, access="rw")
        self._register = csr.Register(
            {"register": csr.Field(csr.action.RW, 8)}, access="rw")
        self._control = csr.Register(
            {"start": csr.Field(csr.action.W, 2)}, access="w")
        # Write and read data are separate registers rather than one RW.
        #
        # A csr.action.RW field reads back whatever was written to it -- the
        # gateware cannot drive its r_data, because the whole point of RW is that
        # software owns the value. Trying to return an I2C read byte through the
        # same field is an elaboration error, not a subtle bug, which is how this
        # was caught.
        self._write_data = csr.Register(
            {"data": csr.Field(csr.action.RW, 8)}, access="rw")
        self._read_data = csr.Register(
            {"data": csr.Field(csr.action.R, 8)}, access="r")
        self._status = csr.Register(
            {"busy": csr.Field(csr.action.R, 1),
             "acked": csr.Field(csr.action.R, 1)}, access="r")

        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("bus", self._bus_select)
        builder.add("address", self._address)
        builder.add("register", self._register)
        builder.add("control", self._control)
        builder.add("write_data", self._write_data)
        builder.add("read_data", self._read_data)
        builder.add("status", self._status)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({"bus": In(csr.Signature(addr_width=4, data_width=8))})
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        # The bridge adds every register in the builder as its own submodule, so
        # adding them here as well raises DuplicateElaboratable -- the registers
        # must NOT be attached a second time.
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        from luna.gateware.interface.i2c import I2CInitiator

        # One set of tristate signals, fanned out below. The initiator drives
        # these; which physical pins see them is the mux's decision.
        class Pads:
            pass

        pads = Pads()
        pads.scl = Signal(name="mux_scl")
        pads.sda_o = Signal(name="mux_sda_o")
        pads.sda_oe = Signal(name="mux_sda_oe")
        pads.sda_i = Signal(name="mux_sda_i", init=1)

        # I2CInitiator expects pin-like objects with .o/.oe/.i. Build shims
        # rather than passing platform pins, since the point is to intercept.
        class Shim:
            def __init__(self, o=None, oe=None, i=None):
                self.o, self.oe, self.i = o, oe, i

        scl_shim = Shim(o=pads.scl, oe=Signal(init=1), i=Signal(init=1))
        sda_shim = Shim(o=pads.sda_o, oe=pads.sda_oe, i=pads.sda_i)

        class MuxPads:
            scl = scl_shim
            sda = sda_shim

        m.submodules.i2c = initiator = I2CInitiator(
            pads=MuxPads(), period_cyc=self.period_cyc, clk_stretch=False)

        select = self._bus_select.f.select.data

        # Fan out to the three physical buses.
        #
        # Every bus is driven every cycle; only the selected one carries real
        # traffic and the others are held idle -- SCL high, SDA released. Holding
        # them idle rather than leaving them undriven matters: these pins are
        # declared PULLMODE="NONE", so an undriven line floats rather than idling
        # high, and a floating SDA on an unselected bus can look like a start
        # condition to a device that is listening.
        if platform is not None:
            for value, (name, number) in BUS_RESOURCES.items():
                port = platform.request(name, number)
                chosen = select == value
                m.d.comb += [
                    port.scl.o.eq(Mux(chosen, pads.scl, 1)),
                    port.sda.o.eq(0),
                    port.sda.oe.eq(chosen & pads.sda_oe),
                ]
                with m.If(chosen):
                    m.d.comb += pads.sda_i.eq(port.sda.i)

        # Transaction sequencing.
        #
        # Written as an explicit FSM rather than reusing I2CRegisterInterface,
        # which owns its own pads and a fixed device address -- the whole point
        # here is one initiator with a switchable bus and a runtime address.
        #
        # A register read is the standard two-phase form: write the register
        # index with the device in write mode, repeated start, then read. Every
        # ACK is accumulated, so a single missing ACK anywhere marks the whole
        # transaction unacknowledged rather than only the last byte.
        busy = Signal()
        acked = Signal()
        all_acked = Signal()
        address = self._address.f.address.data
        register = self._register.f.register.data
        write_data = self._write_data.f.data.data

        m.d.comb += [
            self._status.f.busy.r_data.eq(busy),
            self._status.f.acked.r_data.eq(acked),
        ]

        start = self._control.f.start
        with m.FSM() as fsm:
            m.d.comb += busy.eq(~fsm.ongoing("IDLE"))

            with m.State("IDLE"):
                with m.If(start.w_stb & (start.w_data != 0)):
                    m.d.sync += all_acked.eq(1)
                    with m.If(start.w_data == CONTROL_START_READ):
                        m.next = "READ_ADDR"
                    with m.Else():
                        m.next = "WRITE_ADDR"

            # ---- register read: address+W, register, restart, address+R, data
            with m.State("READ_ADDR"):
                with m.If(~initiator.busy):
                    m.d.comb += initiator.start.eq(1)
                    m.next = "READ_SEND_ADDR"

            with m.State("READ_SEND_ADDR"):
                with m.If(~initiator.busy):
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(Cat(0, address))]
                    m.next = "READ_SEND_REG"

            with m.State("READ_SEND_REG"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(register)]
                    m.next = "READ_RESTART"

            with m.State("READ_RESTART"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    m.d.comb += initiator.start.eq(1)
                    m.next = "READ_SEND_ADDR_R"

            with m.State("READ_SEND_ADDR_R"):
                with m.If(~initiator.busy):
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(Cat(1, address))]
                    m.next = "READ_DATA"

            with m.State("READ_DATA"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    # ack_i low: NACK the final byte, which tells the device this
                    # is the last one it should send.
                    m.d.comb += [initiator.read.eq(1), initiator.ack_i.eq(0)]
                    m.next = "READ_CAPTURE"

            with m.State("READ_CAPTURE"):
                with m.If(~initiator.busy):
                    m.d.sync += self._read_data.f.data.r_data.eq(initiator.data_o)
                    m.next = "STOP"

            # ---- register write: address+W, register, data
            with m.State("WRITE_ADDR"):
                with m.If(~initiator.busy):
                    m.d.comb += initiator.start.eq(1)
                    m.next = "WRITE_SEND_ADDR"

            with m.State("WRITE_SEND_ADDR"):
                with m.If(~initiator.busy):
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(Cat(0, address))]
                    m.next = "WRITE_SEND_REG"

            with m.State("WRITE_SEND_REG"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(register)]
                    m.next = "WRITE_SEND_DATA"

            with m.State("WRITE_SEND_DATA"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    m.d.comb += [initiator.write.eq(1),
                                 initiator.data_i.eq(write_data)]
                    m.next = "STOP"

            with m.State("STOP"):
                with m.If(~initiator.busy):
                    m.d.sync += all_acked.eq(all_acked & initiator.ack_o)
                    m.d.comb += initiator.stop.eq(1)
                    m.next = "FINISH"

            with m.State("FINISH"):
                with m.If(~initiator.busy):
                    m.d.sync += acked.eq(all_acked)
                    m.next = "IDLE"

        return m
