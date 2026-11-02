#
# A device with every endpoint kind the host engine can drive, for scripts/soc/usb_target_sim.py.
# SPDX-License-Identifier: BSD-3-Clause

"""
`TargetDevice`: one LUNA device behind one UTMI port.

- Control EP0 (64 B): standard requests, strings 1-3 (manufacturer, product, serial), lang ID at 0.
- Bulk OUT 1 -> bulk IN 1: echo, packet-for-packet (`bulk_mps`: 512 HS, 64 FS).
- Interrupt IN 2 (8 B): an up-counter.
- `present` low detaches (line state SE0), as in `AoaPhone`.

Not modelled: isochronous (the engine has no ISO token type), LS, suspend.
"""

from amaranth import Elaboratable, Module, Signal

from luna.gateware.interface.utmi import UTMIInterface
from luna.gateware.usb.usb2.control import USBControlEndpoint
from luna.usb2 import USBDevice, USBStreamInEndpoint, USBStreamOutEndpoint

from usb_protocol.emitters import DeviceDescriptorCollection
from usb_protocol.types import USBDirection, USBTransferType

VID, PID = 0x1D50, 0x6199
MANUFACTURER, PRODUCT, SERIAL = "awto", "target test device", "0001"
CONTROL_MPS = 64
INT_MPS = 8


def descriptors(bulk_mps):
    d = DeviceDescriptorCollection()
    with d.DeviceDescriptor() as dd:
        dd.idVendor = VID
        dd.idProduct = PID
        dd.iManufacturer = MANUFACTURER
        dd.iProduct = PRODUCT
        dd.iSerialNumber = SERIAL
        dd.bNumConfigurations = 1
        dd.bMaxPacketSize0 = CONTROL_MPS
    with d.ConfigurationDescriptor() as c:
        with c.InterfaceDescriptor() as i:
            i.bInterfaceNumber = 0
            i.bInterfaceClass = 0xFF
            for number, direction, kind, size in (
                    (1, USBDirection.IN, USBTransferType.BULK, bulk_mps),
                    (1, USBDirection.OUT, USBTransferType.BULK, bulk_mps),
                    (2, USBDirection.IN, USBTransferType.INTERRUPT, INT_MPS)):
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = direction.to_endpoint_address(number)
                    e.bmAttributes = kind
                    e.wMaxPacketSize = size
                    e.bInterval = 1
    return d


class TargetDevice(Elaboratable):
    def __init__(self, *, full_speed_only=False):
        self.full_speed_only = full_speed_only
        self.bulk_mps = 64 if full_speed_only else 512
        self.utmi = UTMIInterface()
        self.present = Signal(init=1)

    def elaborate(self, platform):
        m = Module()
        m.submodules.usb = usb = USBDevice(bus=self.utmi)
        usb.always_fs = self.full_speed_only      # luna#276: not taken from the constructor
        usb.data_clock = 60e6
        control = USBControlEndpoint(utmi=self.utmi, max_packet_size=CONTROL_MPS)
        control.add_standard_request_handlers(descriptors(self.bulk_mps))
        usb.add_endpoint(control)

        ep_out = USBStreamOutEndpoint(endpoint_number=1, max_packet_size=self.bulk_mps)
        ep_in = USBStreamInEndpoint(endpoint_number=1, max_packet_size=self.bulk_mps)
        ep_int = USBStreamInEndpoint(endpoint_number=2, max_packet_size=INT_MPS)
        for ep in (ep_out, ep_in, ep_int):
            usb.add_endpoint(ep)
        m.d.comb += [
            ep_in.stream.valid.eq(ep_out.stream.valid),
            ep_in.stream.payload.eq(ep_out.stream.payload),
            ep_in.stream.last.eq(ep_out.stream.last),
            ep_out.stream.ready.eq(ep_in.stream.ready),
        ]
        counter = Signal(8)
        with m.If(ep_int.stream.ready):
            m.d.usb += counter.eq(counter + 1)
        m.d.comb += [ep_int.stream.valid.eq(1), ep_int.stream.payload.eq(counter)]

        m.d.comb += [usb.connect.eq(self.present),
                     usb.full_speed_only.eq(self.full_speed_only)]
        return m
