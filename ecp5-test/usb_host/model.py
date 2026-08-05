#
# A model of the wire and of the device on the other end of it.
# SPDX-License-Identifier: BSD-3-Clause

"""
What the host engine is exercised against, since there is no packet path here.

This SoC has no USB controller of any kind: `ecp5-test/riscv/ulpi_window.py`
reads a USB3343's registers and cannot send or receive a packet
(`docs/rtic-usb-port.md` section 1). So a host engine cannot be tested against
anything already in the tree, and the model below is what stands in for the bus
and for the device on the far end of it.

## Three pieces

- `connect_utmi` -- the wire. Two UTMI interfaces facing each other, plus the
  line-state arbitration that makes the high-speed chirp handshake work.
- `ModelDevice` -- LUNA's own `USBDevice` with a control endpoint and one bulk
  IN endpoint. Upstream device gateware, which is the point: it is not our
  model of what a device does, it is a device, and it was written by people who
  were not thinking about our host when they wrote it.
- `scale_timing_for_simulation` -- the reset and SOF constants divided down, so
  a bring-up that takes 50 ms of a 60 MHz clock takes a simulable number of
  cycles.

## The wire is a model, and here is exactly how much of one

Byte timing is not modelled: UTMI hands over one byte per clock in both HS and
FS, and only the SOF interval and the reset timers distinguish the two speeds
here. Bit stuffing, NRZI, the SYNC pattern and EOP are below UTMI and do not
appear at all -- the three-cycle `rx_active` lead-in is the only nod to them,
and it exists because LUNA's depacketiser expects `rx_active` before the first
byte. A packet that is malformed at the bit level is therefore not expressible
in this model, which is the main thing it cannot test.

What it does model faithfully is the part the host engine gets wrong if nobody
checks: who drives the line state, and when. Priority is device chirp, then host
chirp, then the host's SE0 during a bus reset, then SE0 while any packet is on
the wire, then idle J. Get that order wrong and speed detection lands on the
wrong speed -- which is why the simulation asserts the detected speed for a
high-speed device and a full-speed one, rather than assuming it.

Informed by `guh/util/test_util.py` and `guh/util/test_devices.py`, which do the
same job for GUH's own tests. Written here rather than vendored because the
model is what the assertions are *about*: vendoring the check with the thing
being checked leaves nothing that is ours to disagree with upstream.
"""

from typing import NamedTuple

from amaranth import Elaboratable, Module, Mux, Signal

from luna.gateware.interface.utmi import UTMIInterface, UTMIOperatingMode
from luna.gateware.usb.usb2.control import USBControlEndpoint
from luna.usb2 import USBDevice, USBStreamInEndpoint

from usb_protocol.emitters import DeviceDescriptorCollection


# The model device's identity, asserted against byte for byte when the
# descriptor comes back over the wire.
VENDOR_ID = 0x16D0
PRODUCT_ID = 0x0F3B
CONTROL_MAX_PACKET_SIZE = 64  # bMaxPacketSize0; 64 is the high-speed value
BULK_IN_ENDPOINT = 4
BULK_MAX_PACKET_SIZE = 512  # the high-speed bulk maximum, and 2 x the SIE's rx_len field


_timing_scaled = False


def scale_timing_for_simulation():
    """Divide the reset and SOF timers down to a simulable number of cycles.

    A bus reset is 50 ms and a full-speed frame is 1 ms; at 60 MHz that is three
    million cycles for the reset alone, and the simulation would spend all of its
    time counting. Every divisor below is on a *timer*, not on a protocol
    sequence -- the chirp still alternates K and J the same number of times, the
    SOF still precedes every transaction, and the tx window still opens after the
    SOF rather than with it.

    That the scaling did not break the negotiation is not assumed: the simulation
    asserts the detected speed for both a high-speed and a full-speed device, and
    those are the two answers the chirp handshake is choosing between.

    Divisors are GUH's own (`guh/util/test_util.py`), which its 27 tests run
    against, so a difference in behaviour here is a difference in our harness
    rather than in how the engine was tuned.

    Idempotent: the constants are class attributes, so applying the division
    twice would quietly quarter them.
    """
    global _timing_scaled
    if _timing_scaled:
        return
    _timing_scaled = True

    from luna.gateware.usb.usb2.reset import USBResetSequencer

    from .guh.reset import USBResetController
    from .guh.sie import USBSOFController

    # The device's side of the chirp: how long it drives chirp K, and how long
    # it waits for the host's K/J pairs before giving up on high speed.
    USBResetSequencer._CYCLES_2_MILLISECONDS //= 20
    USBResetSequencer._CYCLES_2P5_MILLISECONDS //= 20

    # The host's side: connect settle, total reset length, how long before it
    # starts looking for the device chirp, the chirp-K filter, and the duration
    # of each host chirp.
    USBResetController._SETTLE_TIME //= 10
    USBResetController._MAX_RESET_TIME //= 200
    USBResetController._MIN_RESET_BEFORE_CHIRP //= 10
    USBResetController._CHIRP_FILTER_CYCLES //= 10
    USBResetController._CHIRP_DURATION //= 10

    # Frame and microframe intervals, and the window within each one where the
    # engine is allowed to start a transaction.
    USBSOFController._SOF_CYCLES_FS //= 10
    USBSOFController._SOF_TX_TO_TX_MIN_FS //= 10
    USBSOFController._SOF_TX_TO_TX_MAX_FS //= 10
    USBSOFController._SOF_TX_TO_RX_MAX_FS //= 10
    USBSOFController._SOF_CYCLES_HS //= 2
    USBSOFController._SOF_TX_TO_TX_MIN_HS //= 2
    USBSOFController._SOF_TX_TO_TX_MAX_HS = USBSOFController._SOF_CYCLES_HS - 900
    USBSOFController._SOF_TX_TO_RX_MAX_HS = USBSOFController._SOF_CYCLES_HS - 60


def connect_utmi(m, host_utmi, device_utmi):
    """Face two UTMI interfaces at each other. Returns the shared line state.

    Each direction is a byte pipe with a three-cycle lead-in on `rx_active`,
    standing in for the SYNC pattern that LUNA's depacketiser waits for before
    the first data byte.
    """

    def bridge(source, sink):
        lead_in = Signal(2)
        m.d.comb += [
            sink.rx_active.eq(0),
            sink.rx_valid.eq(source.tx_valid & source.tx_ready),
            sink.rx_data.eq(source.tx_data),
        ]
        with m.If(source.tx_valid):
            m.d.comb += sink.rx_active.eq(1)
            with m.If(lead_in == 0b11):
                m.d.comb += source.tx_ready.eq(1)
            with m.Else():
                m.d.sync += lead_in.eq(lead_in + 1)
        with m.Else():
            m.d.sync += lead_in.eq(0)

    bridge(host_utmi, device_utmi)
    bridge(device_utmi, host_utmi)

    # VBUS present, from the device's point of view.
    m.d.comb += device_utmi.session_end.eq(0)

    # Who owns the line state, in priority order. The device's chirp outranks
    # the host's SE0 because that is the whole mechanism: the host holds SE0 for
    # the reset, and a high-speed device answers *through* it with chirp K.
    # A chirp's payload is the line state directly: 0x00 is K, 0xff is J.
    def chirped(tx_data):
        return Mux(tx_data == 0x00, 0b10, 0b01)

    line_state = Signal(2)
    with m.If((device_utmi.op_mode == UTMIOperatingMode.CHIRP) & device_utmi.tx_valid):
        m.d.comb += line_state.eq(chirped(device_utmi.tx_data))
    with m.Elif((host_utmi.op_mode == UTMIOperatingMode.CHIRP) & host_utmi.tx_valid):
        m.d.comb += line_state.eq(chirped(host_utmi.tx_data))
    with m.Elif(host_utmi.op_mode == UTMIOperatingMode.RAW_DRIVE):
        m.d.comb += line_state.eq(0b00)  # SE0: the host is driving a bus reset
    with m.Elif(device_utmi.tx_valid | host_utmi.tx_valid):
        m.d.comb += line_state.eq(0b00)  # a packet is on the wire
    with m.Else():
        m.d.comb += line_state.eq(0b01)  # idle, and something is connected

    m.d.comb += [
        host_utmi.line_state.eq(line_state),
        device_utmi.line_state.eq(line_state),
    ]
    return line_state


class ModelDevice(Elaboratable):
    """A LUNA device on the far end: control endpoint plus one bulk IN endpoint.

    The bulk endpoint emits an up-counter, so a received packet has a value the
    simulation can predict byte by byte rather than merely count. Its packet size
    is the high-speed maximum of 512, which is also what makes the SIE's 8-bit
    `rx_len` wrap -- the trap that `docs/usb-host-proposal.md` section 15.2 warns
    about, and that the simulation now asserts rather than describes.
    """

    def __init__(self, *, full_speed_only=False,
                 bulk_max_packet_size=BULK_MAX_PACKET_SIZE):
        self.full_speed_only = full_speed_only
        self.bulk_max_packet_size = bulk_max_packet_size
        self.utmi = UTMIInterface()
        super().__init__()

    def descriptors(self):
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor = VENDOR_ID
            d.idProduct = PRODUCT_ID
            d.iManufacturer = "awto"
            d.iProduct = "host model device"
            d.iSerialNumber = "0001"
            d.bNumConfigurations = 1
            d.bMaxPacketSize0 = CONTROL_MAX_PACKET_SIZE

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_IN_ENDPOINT
                    e.wMaxPacketSize = self.bulk_max_packet_size
        return descriptors

    def elaborate(self, platform):
        m = Module()

        usb = USBDevice(bus=self.utmi)
        # https://github.com/greatscottgadgets/luna/issues/276 -- speed is not
        # taken from the constructor, and the UTMI here is already at 60 MHz.
        usb.always_fs = self.full_speed_only
        usb.data_clock = 60e6
        m.submodules.usb = usb

        control = USBControlEndpoint(
            utmi=self.utmi, max_packet_size=CONTROL_MAX_PACKET_SIZE)
        control.add_standard_request_handlers(self.descriptors())
        usb.add_endpoint(control)

        stream_ep = USBStreamInEndpoint(
            endpoint_number=BULK_IN_ENDPOINT,
            max_packet_size=self.bulk_max_packet_size)
        usb.add_endpoint(stream_ep)

        counter = Signal(8)
        with m.If(stream_ep.stream.ready):
            m.d.usb += counter.eq(counter + 1)
        m.d.comb += [
            stream_ep.stream.valid.eq(1),
            stream_ep.stream.payload.eq(counter),
        ]

        m.d.comb += [
            usb.connect.eq(1),
            usb.full_speed_only.eq(self.full_speed_only),
        ]
        return m


class Bench:
    """Host engine, wire and device in one module, on one clock.

    `usb` is renamed onto `sync` for the same reason GUH's own tests do it: the
    simulation has one clock, and the CDC that the real integration needs
    (`docs/usb-host-proposal.md` section 14 -- the host belongs in `usb`, the CPU
    is in `sync`) is a property of the SoC boundary rather than of the engine.
    What this bench proves therefore stops at that boundary, and the crossing
    needs its own test when the shim is written.
    """

    def __init__(self, *, full_speed_only=False, fifo_depth=BULK_MAX_PACKET_SIZE,
                 bulk_max_packet_size=BULK_MAX_PACKET_SIZE):
        from amaranth.hdl import DomainRenamer

        from .guh.sie import USBSIE

        scale_timing_for_simulation()

        self.module = Module()
        self.host = USBSIE(fifo_depth=fifo_depth, fullspeed_only=False)
        self.device = ModelDevice(
            full_speed_only=full_speed_only,
            bulk_max_packet_size=bulk_max_packet_size)

        rename = DomainRenamer({"usb": "sync"})
        self.module.submodules.host = rename(self.host)
        self.module.submodules.device = rename(self.device)
        self.line_state = connect_utmi(
            self.module, self.host.utmi, self.device.utmi)

    @property
    def ctrl(self):
        """The `USBSIEInterface` a driver -- firmware, or a CSR shim -- would own."""
        return self.host.ctrl


class Packet(NamedTuple):
    """One host-to-device packet as the device saw it: PID byte first."""
    cycle: int
    data: list

    @property
    def pid(self):
        return self.data[0]


def capture_host_packets(device_utmi, packets):
    """A process that appends every host-to-device `Packet` to `packets`.

    This is the only place the simulation sees the wire rather than the engine's
    own status, and it is what lets the token and CRC checks be independent of
    the engine that generated them. The cycle stamp is the end of the packet,
    which is what makes the interval between two SOFs measurable.
    """
    async def process(ctx):
        cycle = 0
        current = []
        while True:
            # `tick()` yields the clock and reset values first, then the samples.
            _, _, valid, active, data = await ctx.tick().sample(
                device_utmi.rx_valid, device_utmi.rx_active, device_utmi.rx_data)
            if valid:
                current.append(int(data))
            if current and not active:
                packets.append(Packet(cycle, current))
                current = []
            cycle += 1
    return process
