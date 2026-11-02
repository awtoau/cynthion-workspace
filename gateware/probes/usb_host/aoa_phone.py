#
# An Android phone as an AOA device, for the host-engine simulations.
# SPDX-License-Identifier: BSD-3-Clause

"""
Two LUNA devices behind one UTMI port; the phone swaps from one to the other on AOA START.

- Before: `18d1:4ee2`, control endpoint + vendor requests 51 / 52 / 53, no data endpoints.
- 51 GET_PROTOCOL returns 2 (LE). 52 SEND_STRING records, per wIndex 0-5, a byte sum.
- 53 START: after the status stage the phone detaches (`present` low, line state SE0)
  for `detach_cycles` usb cycles, then attaches as `18d1:2d00` (`2d01` with `adb`).
- After: bulk OUT 1 -> bulk IN 1, an echo, `bulk_max_packet_size` per packet.

Not modelled: MTP, AOA 2.0, the phone rejecting strings or START without them.
Spec: https://source.android.com/docs/core/interaction/accessories/aoa
"""

from amaranth import Elaboratable, Module, Mux, Signal

from luna.gateware.interface.utmi import UTMIInterface
from luna.gateware.stream.generator import StreamSerializer
from luna.gateware.usb.request.control import ControlRequestHandler
from luna.gateware.usb.usb2.transfer import USBInStreamInterface
from luna.gateware.usb.usb2.control import USBControlEndpoint
from luna.usb2 import USBDevice, USBStreamInEndpoint, USBStreamOutEndpoint

from usb_protocol.emitters import DeviceDescriptorCollection
from usb_protocol.types import USBRequestType, USBDirection, USBTransferType

GOOGLE_VID = 0x18D1
PID_PHONE = 0x4EE2          # MTP + adb: what a phone shows before AOA
PID_AOA, PID_AOA_ADB = 0x2D00, 0x2D01
AOA_PROTOCOL = 2
REQ_GET_PROTOCOL, REQ_SEND_STRING, REQ_START = 51, 52, 53
N_STRINGS = 6
CONTROL_MPS = 64
BULK_MPS = 512


class AoaVendorHandler(ControlRequestHandler):
    """51 / 52 / 53 on the pre-AOA device."""

    def __init__(self):
        super().__init__()
        self.string_sum = [Signal(16, name=f"string_sum{i}") for i in range(N_STRINGS)]
        self.string_len = [Signal(8, name=f"string_len{i}") for i in range(N_STRINGS)]
        self.start      = Signal()      # usb pulse: 53 completed (host ACKed the status ZLP)
        self.got_start  = Signal()      # sticky

    def elaborate(self, platform):
        m = Module()
        interface, setup = self.interface, self.interface.setup

        m.submodules.serializer = serializer = StreamSerializer(
            domain="usb", stream_type=USBInStreamInterface, data_length=2,
            max_length_width=2)

        index = setup.index[0:3]
        with m.If(setup.type == USBRequestType.VENDOR):
            with m.FSM(domain="usb"):
                with m.State("IDLE"):
                    with m.If(setup.received):
                        with m.Switch(setup.request):
                            with m.Case(REQ_GET_PROTOCOL):
                                m.next = "GET_PROTOCOL"
                            with m.Case(REQ_SEND_STRING):
                                for i in range(N_STRINGS):
                                    with m.If(index == i):
                                        m.d.usb += [self.string_sum[i].eq(0),
                                                    self.string_len[i].eq(0)]
                                m.next = "SEND_STRING"
                            with m.Case(REQ_START):
                                m.next = "START"

                with m.State("GET_PROTOCOL"):
                    m.d.comb += interface.claim.eq(1)
                    self.handle_simple_data_request(m, serializer, AOA_PROTOCOL, length=2)

                with m.State("SEND_STRING"):
                    m.d.comb += interface.claim.eq(1)
                    with m.If(interface.rx.valid & interface.rx.next):
                        for i in range(N_STRINGS):
                            with m.If(index == i):
                                m.d.usb += [
                                    self.string_sum[i].eq(self.string_sum[i] + interface.rx.payload),
                                    self.string_len[i].eq(self.string_len[i] + 1)]
                    with m.If(interface.rx_ready_for_response):
                        m.d.comb += interface.handshakes_out.ack.eq(1)
                    with m.If(interface.status_requested):
                        m.d.comb += self.send_zlp()
                        m.next = "IDLE"

                with m.State("START"):
                    m.d.comb += interface.claim.eq(1)
                    with m.If(interface.status_requested):
                        m.d.comb += self.send_zlp()
                    with m.If(interface.handshakes_in.ack):
                        m.d.comb += self.start.eq(1)
                        m.d.usb += self.got_start.eq(1)
                        m.next = "IDLE"
        return m


def _descriptors(pid, *, accessory):
    d = DeviceDescriptorCollection()
    with d.DeviceDescriptor() as dd:
        dd.idVendor = GOOGLE_VID
        dd.idProduct = pid
        dd.iManufacturer = "Google"
        dd.iProduct = "Accessory" if accessory else "Pixel"
        dd.iSerialNumber = "0001"
        dd.bNumConfigurations = 1
        dd.bMaxPacketSize0 = CONTROL_MPS
    with d.ConfigurationDescriptor() as c:
        with c.InterfaceDescriptor() as i:
            i.bInterfaceNumber = 0
            i.bInterfaceClass = 0xFF
            if accessory:
                i.bInterfaceSubclass = 0xFF
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = USBDirection.IN.to_endpoint_address(1)
                    e.bmAttributes = USBTransferType.BULK
                    e.wMaxPacketSize = BULK_MPS
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = USBDirection.OUT.to_endpoint_address(1)
                    e.bmAttributes = USBTransferType.BULK
                    e.wMaxPacketSize = BULK_MPS
    return d


class AoaPhone(Elaboratable):
    """The phone. `utmi` faces the wire; `present` drives `connect_utmi(present=...)`."""

    def __init__(self, *, adb=False, detach_cycles=2000):
        self.adb = adb
        self.detach_cycles = detach_cycles
        self.utmi = UTMIInterface()
        self.present = Signal(init=1)
        self.accessory = Signal()       # high once re-attached as 2d00 / 2d01
        self.vendor = AoaVendorHandler()

    def elaborate(self, platform):
        m = Module()
        utmi = self.utmi
        ua, ub = UTMIInterface(), UTMIInterface()
        sel_b = self.accessory
        sel_a = ~self.accessory & self.present

        # Device A: control + vendor requests.
        m.submodules.dev_a = dev_a = USBDevice(bus=ua)
        dev_a.always_fs, dev_a.data_clock = False, 60e6
        control = USBControlEndpoint(utmi=ua, max_packet_size=CONTROL_MPS)
        control.add_standard_request_handlers(_descriptors(PID_PHONE, accessory=False))
        control.add_request_handler(self.vendor)
        dev_a.add_endpoint(control)

        # Device B: the accessory-mode phone, bulk echo.
        m.submodules.dev_b = dev_b = USBDevice(bus=ub)
        dev_b.always_fs, dev_b.data_clock = False, 60e6
        pid = PID_AOA_ADB if self.adb else PID_AOA
        dev_b.add_standard_control_endpoint(_descriptors(pid, accessory=True))
        ep_out = USBStreamOutEndpoint(endpoint_number=1, max_packet_size=BULK_MPS)
        ep_in = USBStreamInEndpoint(endpoint_number=1, max_packet_size=BULK_MPS)
        dev_b.add_endpoint(ep_out)
        dev_b.add_endpoint(ep_in)
        m.d.comb += [
            ep_in.stream.valid.eq(ep_out.stream.valid),
            ep_in.stream.payload.eq(ep_out.stream.payload),
            ep_in.stream.last.eq(ep_out.stream.last),
            ep_out.stream.ready.eq(ep_in.stream.ready),
        ]

        m.d.comb += [
            dev_a.connect.eq(sel_a),
            dev_b.connect.eq(sel_b),
        ]

        # Detach / attach on START.
        wait = Signal(range(self.detach_cycles + 1))
        with m.FSM(domain="usb"):
            with m.State("PRE"):
                with m.If(self.vendor.start):
                    m.d.usb += [self.present.eq(0), wait.eq(0)]
                    m.next = "GONE"
            with m.State("GONE"):
                m.d.usb += wait.eq(wait + 1)
                with m.If(wait == self.detach_cycles - 1):
                    m.d.usb += [self.accessory.eq(1), self.present.eq(1)]
                    m.next = "POST"
            with m.State("POST"):
                pass

        # Mux the one wire onto the selected device.
        for sel, dev in ((sel_a, ua), (sel_b, ub)):
            m.d.comb += [
                dev.rx_data.eq(Mux(sel, utmi.rx_data, 0)),
                dev.rx_active.eq(Mux(sel, utmi.rx_active, 0)),
                dev.rx_valid.eq(Mux(sel, utmi.rx_valid, 0)),
                dev.tx_ready.eq(Mux(sel, utmi.tx_ready, 0)),
                dev.line_state.eq(utmi.line_state),
                dev.session_end.eq(utmi.session_end),
                dev.vbus_valid.eq(1),
                dev.session_valid.eq(1),
            ]
        with m.If(sel_b):
            m.d.comb += [utmi.tx_data.eq(ub.tx_data), utmi.tx_valid.eq(ub.tx_valid),
                         utmi.op_mode.eq(ub.op_mode), utmi.xcvr_select.eq(ub.xcvr_select),
                         utmi.term_select.eq(ub.term_select)]
        with m.Elif(sel_a):
            m.d.comb += [utmi.tx_data.eq(ua.tx_data), utmi.tx_valid.eq(ua.tx_valid),
                         utmi.op_mode.eq(ua.op_mode), utmi.xcvr_select.eq(ua.xcvr_select),
                         utmi.term_select.eq(ua.term_select)]
        return m

