#
# The AUX device: the CDC-ACM console plus a vendor interface for the data path.
# SPDX-License-Identifier: BSD-3-Clause

"""`DataPathDevice`: drop-in for LUNA's `USBSerialDevice` that adds interface 2.

- Interfaces 0/1, EP3 (notification) and EP4 (console data): `USBSerialDevice`'s
  descriptors and handlers, unchanged; `rx`/`tx`/`connect` behave the same.
- Interface 2, class 0xFF, no kernel driver (libusb claims it):
  EP1 OUT/IN bulk = data blocks (`data_rx` / `data_tx`), EP2 OUT/IN bulk = control
  bytes to/from the RV (`ctl_rx` / `ctl_tx`). All four streams carry `last`:
  OUT `last` = final byte of a short packet; IN `last` ends the transfer (ZLP if full).
- Streams are in the `usb` domain; `docs/data-path.md#wiring` has the crossings.
- Why not `USBSerialDevice`: it builds its descriptors and `USBDevice` internally (#580).
"""

from amaranth                                 import Module
from luna.gateware.stream                     import StreamInterface
from luna.gateware.usb.devices.acm            import ACMRequestHandlers, USBSerialDevice
from luna.gateware.usb.usb2.request           import StallOnlyRequestHandler
from luna.usb2                                import (USBDevice, USBStreamInEndpoint,
                                                      USBStreamOutEndpoint)
from usb_protocol.emitters                    import DeviceDescriptorCollection
from usb_protocol.emitters.descriptors        import cdc
from usb_protocol.types                       import USBRequestType, USBTransferType

__all__ = ["DataPathDevice", "DATA_EP", "CTL_EP", "VENDOR_INTERFACE"]

DATA_EP, CTL_EP = 1, 2
VENDOR_INTERFACE = 2


class DataPathDevice(USBSerialDevice):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_rx, self.data_tx = StreamInterface(), StreamInterface()
        self.ctl_rx, self.ctl_tx = StreamInterface(), StreamInterface()

    def create_descriptors(self):
        d = DeviceDescriptorCollection()
        with d.DeviceDescriptor() as dd:
            dd.idVendor, dd.idProduct = self._idVendor, self._idProduct
            dd.iManufacturer = self._manufacturer_string
            dd.iProduct = self._product_string
            dd.iSerialNumber = self._serial_number
            dd.bNumConfigurations = 1
        mps = self._max_packet_size
        with d.ConfigurationDescriptor() as c:
            # Interfaces 0/1: as USBSerialDevice.create_descriptors.
            with c.InterfaceAssociationDescriptor() as ia:
                ia.bFirstInterface, ia.bInterfaceCount = 0, 2
                ia.bFunctionClass, ia.bFunctionSubClass, ia.bFunctionProtocol = 0x02, 0x02, 0x01
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                i.bInterfaceClass, i.bInterfaceSubclass, i.bInterfaceProtocol = 0x02, 0x02, 0x01
                i.add_subordinate_descriptor(cdc.HeaderDescriptorEmitter())
                union = cdc.UnionFunctionalDescriptorEmitter()
                union.bControlInterface, union.bSubordinateInterface0 = 0, 1
                i.add_subordinate_descriptor(union)
                call_management = cdc.CallManagementFunctionalDescriptorEmitter()
                call_management.bDataInterface = 1
                i.add_subordinate_descriptor(call_management)
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | self._STATUS_ENDPOINT_NUMBER
                    e.bmAttributes, e.wMaxPacketSize, e.bInterval = 0x03, mps, 11
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 1
                i.bInterfaceClass, i.bInterfaceSubclass, i.bInterfaceProtocol = 0x0a, 0, 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress, e.wMaxPacketSize = 0x80 | self._DATA_ENDPOINT_NUMBER, mps
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress, e.wMaxPacketSize = self._DATA_ENDPOINT_NUMBER, mps
            # Interface 2: the data path.
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = VENDOR_INTERFACE
                i.bInterfaceClass, i.bInterfaceSubclass, i.bInterfaceProtocol = 0xFF, 0, 0
                for ep in (DATA_EP, CTL_EP):
                    for address in (ep, 0x80 | ep):
                        with i.EndpointDescriptor() as e:
                            e.bEndpointAddress = address
                            e.bmAttributes = USBTransferType.BULK
                            e.wMaxPacketSize = mps
        return d

    def elaborate(self, platform):
        m = Module()
        m.submodules.usb = usb = USBDevice(bus=self._bus)
        control_ep = usb.add_standard_control_endpoint(self.create_descriptors())
        control_ep.add_request_handler(ACMRequestHandlers())
        control_ep.add_request_handler(StallOnlyRequestHandler(
            lambda setup: (setup.type == USBRequestType.VENDOR)
            | (setup.type == USBRequestType.RESERVED)))
        mps = self._max_packet_size
        usb.add_endpoint(USBStreamInEndpoint(endpoint_number=self._STATUS_ENDPOINT_NUMBER,
                                             max_packet_size=mps))
        console_rx = USBStreamOutEndpoint(endpoint_number=self._DATA_ENDPOINT_NUMBER,
                                          max_packet_size=mps)
        console_tx = USBStreamInEndpoint(endpoint_number=self._DATA_ENDPOINT_NUMBER,
                                         max_packet_size=mps)
        usb.add_endpoint(console_rx)
        usb.add_endpoint(console_tx)
        m.d.comb += [console_tx.stream.stream_eq(self.tx), self.rx.stream_eq(console_rx.stream),
                     usb.connect.eq(self.connect)]
        for ep, rx, tx in ((DATA_EP, self.data_rx, self.data_tx),
                           (CTL_EP, self.ctl_rx, self.ctl_tx)):
            out_ep = USBStreamOutEndpoint(endpoint_number=ep, max_packet_size=mps)
            in_ep = USBStreamInEndpoint(endpoint_number=ep, max_packet_size=mps)
            usb.add_endpoint(out_ep)
            usb.add_endpoint(in_ep)
            m.d.comb += [rx.stream_eq(out_ep.stream), in_ep.stream.stream_eq(tx)]
        return m
