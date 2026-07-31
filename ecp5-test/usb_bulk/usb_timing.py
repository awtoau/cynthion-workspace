#!/usr/bin/env python3
#
# Measure USB transaction timing from inside the FPGA.
# SPDX-License-Identifier: BSD-3-Clause

"""
Instruments the USB IN path to find where the missing 38 Mbps goes.

Host-side measurement says 388.0 Mbps against a 426.0 Mbps protocol maximum:
11.84 transactions per 125 us microframe instead of 13, about 1.16 us of
unexplained overhead per transaction. From the host that gap can only be
attributed by guesswork -- host scheduling, bit stuffing and PHY turnaround all
produce the same aggregate number.

Inside the FPGA it is directly observable. The USB stack exposes the token
detector and both handshake interfaces, so the FPGA can timestamp the events
that bound each transaction and report the distribution rather than an average.

What is counted, all in the 60 MHz usb domain (16.67 ns per cycle):

**Tokens received.** Every IN token addressed to this endpoint. If this is
around 13 per microframe the host is scheduling fully and the loss is inside
the transaction; if it is around 11.84 the host is not asking often enough and
the loss is upstream of the device entirely. That single number decides where
to look, and the host cannot see it.

**ACKs received**, so NAKed or lost transactions are distinguishable from ones
that never happened. A device that NAKs is not ready; a device that is never
asked has nothing to be ready for.

**Cycles from token to first data byte.** The device's own response latency,
which is the only part of the budget this design controls.

**Cycles from last data byte to ACK.** Bus turnaround plus host processing,
which the device does not control but can measure.

**Idle cycles between the ACK and the next token.** This is the interesting
one: it is the gap the protocol model assumes is zero, and if it accounts for
1.16 us then host scheduling is the answer and no gateware change will help.

Minimum, maximum and total are kept for each, because an average hides the
thing worth seeing. A steady 1.16 us of overhead and an occasional 15 us stall
average identically and mean entirely different things.
"""

import sys as _uid_sys
from pathlib import Path as _uid_Path
_uid_sys.path.insert(0, str(_uid_Path(__file__).resolve().parent.parent))
import usb_ids


from amaranth                          import Cat, Const, Elaboratable, Module, Signal

from luna.gateware.architecture.car    import LunaECP5DomainGenerator
from luna.gateware.interface.jtag      import JTAGRegisterInterface
from luna.gateware.usb.usb2.device     import USBDevice
from luna.gateware.usb.usb2.endpoints.stream import (USBStreamInEndpoint,
                                                     USBStreamOutEndpoint)

from usb_protocol.emitters             import DeviceDescriptorCollection


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

MAX_PACKET_SIZE = 512

BULK_IN_ENDPOINT  = 1
BULK_OUT_ENDPOINT = 1

USB_VENDOR_ID  = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("usb_timing")

PHY_NAME = "aux_phy"

APPLET_ID = 0x5442494d   # "TBIM"

REGISTER_ID          = 1
REGISTER_TOKENS      = 2   # IN tokens addressed to our endpoint
REGISTER_ACKS        = 3   # ACKs received from the host
REGISTER_BYTES       = 4   # payload bytes handed to the endpoint
REGISTER_GAP_TOTAL   = 5   # total idle cycles between ACK and next token
REGISTER_GAP_MINMAX  = 6   # smallest and largest such gap
REGISTER_RESP_MINMAX = 7   # token to first byte, smallest and largest
REGISTER_ACKWAIT     = 8   # last byte to ACK, smallest and largest


class USBTiming(Elaboratable):
    """ Streams data and timestamps every phase of each IN transaction. """

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor  = USB_VENDOR_ID
            d.idProduct = USB_PRODUCT_ID
            d.iManufacturer = "Great Scott Gadgets"
            d.iProduct      = usb_ids.product_string("usb_timing")
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_IN_ENDPOINT
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_OUT_ENDPOINT
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

        return descriptors

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        bus = platform.request(PHY_NAME, 0)
        usb = USBDevice(bus=bus)
        m.submodules.usb = usb

        descriptors = self.create_descriptors()
        usb.add_standard_control_endpoint(descriptors)
        m.d.comb += usb.connect.eq(1)

        stream_in = USBStreamInEndpoint(
            endpoint_number=BULK_IN_ENDPOINT,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_in)

        # A saturated source, as in the throughput test: nothing here can
        # stall, so any gap measured belongs to the protocol or the host.
        in_counter = Signal(8)
        in_bytes   = Signal(32)
        m.d.comb += [
            stream_in.stream.payload.eq(in_counter),
            stream_in.stream.valid.eq(1),
            stream_in.stream.last.eq(0),
        ]
        with m.If(stream_in.stream.valid & stream_in.stream.ready):
            m.d.usb += [
                in_counter.eq(in_counter + 1),
                in_bytes.eq(in_bytes + 1),
            ]

        # An OUT endpoint, so the same bitstream still enumerates identically
        # to the throughput test and the comparison stays like-for-like.
        stream_out = USBStreamOutEndpoint(
            endpoint_number=BULK_OUT_ENDPOINT,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_out)
        m.d.comb += stream_out.stream.ready.eq(1)

        #
        # Instrumentation.
        #
        interface = stream_in.interface

        # An IN token addressed to this endpoint. `new_token` strobes for any
        # token; the endpoint comparison narrows it to ours.
        token_here = Signal()
        m.d.comb += token_here.eq(
            interface.tokenizer.new_token
            & (interface.tokenizer.endpoint == BULK_IN_ENDPOINT)
            & interface.tokenizer.is_in)

        ack_here = Signal()
        m.d.comb += ack_here.eq(interface.handshakes_in.ack)

        tokens = Signal(32)
        acks   = Signal(32)
        with m.If(token_here):
            m.d.usb += tokens.eq(tokens + 1)
        with m.If(ack_here):
            m.d.usb += acks.eq(acks + 1)

        # Free-running cycle counter, used as a timestamp source. Wrapping is
        # harmless: every measurement here is a difference over a few hundred
        # cycles, far short of a 32-bit wrap.
        now = Signal(32)
        m.d.usb += now.eq(now + 1)

        def tracker(name, start_strobe, stop_strobe):
            """Accumulate min, max and total cycles between two strobes.

            Returns (minimum, maximum, total). Min starts at all-ones so the
            first measurement replaces it; a min stuck at all-ones means the
            event never fired, which is itself informative.
            """
            started  = Signal(name=f"{name}_started")
            stamp    = Signal(32, name=f"{name}_stamp")
            smallest = Signal(16, init=0xFFFF, name=f"{name}_min")
            largest  = Signal(16, name=f"{name}_max")
            total    = Signal(32, name=f"{name}_total")

            with m.If(start_strobe):
                m.d.usb += [stamp.eq(now), started.eq(1)]

            with m.If(stop_strobe & started):
                delta = Signal(32, name=f"{name}_delta")
                m.d.comb += delta.eq(now - stamp)
                m.d.usb += [
                    started.eq(0),
                    total.eq(total + delta),
                ]
                with m.If(delta[16:] == 0):
                    with m.If(delta[:16] < smallest):
                        m.d.usb += smallest.eq(delta[:16])
                    with m.If(delta[:16] > largest):
                        m.d.usb += largest.eq(delta[:16])

            return smallest, largest, total

        # Token to first data byte: the device's own response latency.
        first_byte = Signal()
        sending    = Signal()
        with m.If(token_here):
            m.d.usb += sending.eq(1)
        with m.If(first_byte):
            m.d.usb += sending.eq(0)
        m.d.comb += first_byte.eq(
            sending & stream_in.stream.valid & stream_in.stream.ready)

        resp_min, resp_max, _ = tracker("resp", token_here, first_byte)

        # ACK to the next token: the idle gap the protocol model assumes away.
        gap_min, gap_max, gap_total = tracker("gap", ack_here, token_here)

        # Last byte to ACK: bus turnaround plus host processing.
        ackwait_min, ackwait_max, _ = tracker("ackwait", first_byte, ack_here)

        registers.add_read_only_register(REGISTER_TOKENS, read=tokens)
        registers.add_read_only_register(REGISTER_ACKS,   read=acks)
        registers.add_read_only_register(REGISTER_BYTES,  read=in_bytes)
        registers.add_read_only_register(REGISTER_GAP_TOTAL, read=gap_total)
        registers.add_read_only_register(
            REGISTER_GAP_MINMAX, read=Cat(gap_min, gap_max))
        registers.add_read_only_register(
            REGISTER_RESP_MINMAX, read=Cat(resp_min, resp_max))
        registers.add_read_only_register(
            REGISTER_ACKWAIT, read=Cat(ackwait_min, ackwait_max))

        return m
