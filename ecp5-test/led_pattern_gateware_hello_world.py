#!/usr/bin/env python3
"""
Cynthion LED Pattern Gateware - Hello World

Simple USB device that accepts vendor requests to control LEDs.

Vendor Requests:
  0x01 (SET_LED_PATTERN): Write 1 byte to set LED pattern
       wValue=0, wIndex=0, Data: [pattern_byte]
       Bits: 1=LED on, 0=LED off (bit 0 = LED 1, bit 5 = LED 6)

  0x02 (SET_LED_MODE): Write 1 byte to set animation mode
       wValue=0, wIndex=0, Data: [mode]
       Mode: 0=static, 1=chase, 2=pulse (not implemented in this simple version)

  0x03 (GET_USER_BUTTON): Read user button state (1 byte response)
       Returns: [button_state] (1 = pressed, 0 = released)

Usage:
  python3 led_pattern_gateware_hello_world.py
"""

from amaranth import *
from amaranth.lib.fifo import SyncFIFO
from luna.usb2 import USBDevice
from usb_protocol.emitters import DeviceDescriptorCollection
from luna.gateware.usb.request.windows import (
    MicrosoftOS10DescriptorCollection,
    MicrosoftOS10RequestHandler,
)
from usb_protocol.emitters.descriptors.standard import get_string_descriptor
from usb_protocol.types.descriptors.microsoft10 import RegistryTypes
from luna.gateware.stream.generator import StreamSerializer
from luna.gateware.usb.request.control import ControlRequestHandler
from luna.gateware.usb.request.interface import SetupPacket
from luna.gateware.usb.usb2.request import RequestHandlerInterface
from luna.gateware.usb.usb2.transfer import USBInStreamInterface
from usb_protocol.types import USBRequestType
from luna.usb2 import USBStreamInEndpoint, USBStreamOutEndpoint
from usb_protocol.types import USBDirection, USBTransferType

VENDOR_ID = 0x1209   # https://pid.codes/1209/
PRODUCT_ID = 0x0001

MAX_PACKET_SIZE = 512


class LEDVendorRequestHandler(ControlRequestHandler):
    """Handle vendor requests for LED control."""
    
    VENDOR_SET_LED_PATTERN = 0x01
    VENDOR_SET_LED_MODE = 0x02
    VENDOR_GET_USER_BUTTON = 0x03

    def elaborate(self, platform):
        m = Module()

        # Shortcuts
        interface: RequestHandlerInterface = self.interface
        setup: SetupPacket = self.interface.setup

        # Get references to LEDs and user button
        leds = Cat(platform.request("led", i).o for i in range(6))
        user_button = platform.request("button_user").i

        # Create a serializer for transmitting IN data back to host
        serializer = StreamSerializer(
            domain="usb",
            stream_type=USBInStreamInterface,
            data_length=1,
            max_length_width=1,
        )
        m.submodules += serializer

        # FSM for vendor request handling
        with m.If(setup.type == USBRequestType.VENDOR):
            with m.FSM(domain="usb"):
                with m.State("IDLE"):
                    with m.If(setup.received):
                        with m.Switch(setup.request):
                            with m.Case(self.VENDOR_SET_LED_PATTERN):
                                m.next = "HANDLE_SET_LED_PATTERN"
                            with m.Case(self.VENDOR_SET_LED_MODE):
                                m.next = "HANDLE_SET_LED_MODE"
                            with m.Case(self.VENDOR_GET_USER_BUTTON):
                                m.next = "HANDLE_GET_USER_BUTTON"

                # Handle SET_LED_PATTERN
                with m.State("HANDLE_SET_LED_PATTERN"):
                    m.d.comb += interface.claim.eq(1)
                    
                    # If we have active data, set LEDs to payload
                    with m.If(interface.rx.valid & interface.rx.next):
                        m.d.usb += leds.eq(interface.rx.payload[0:6])
                    
                    # Once receive complete, respond with ACK
                    with m.If(interface.rx_ready_for_response):
                        m.d.comb += interface.handshakes_out.ack.eq(1)
                    
                    # After status stage, send ZLP and return to IDLE
                    with m.If(interface.status_requested):
                        m.d.comb += self.send_zlp()
                        m.next = "IDLE"

                # Handle SET_LED_MODE (for future animation control)
                with m.State("HANDLE_SET_LED_MODE"):
                    m.d.comb += interface.claim.eq(1)
                    
                    with m.If(interface.rx.valid & interface.rx.next):
                        # Store mode value (not used in this simple version)
                        # m.d.usb += led_mode.eq(interface.rx.payload[0:2])
                        pass
                    
                    with m.If(interface.rx_ready_for_response):
                        m.d.comb += interface.handshakes_out.ack.eq(1)
                    
                    with m.If(interface.status_requested):
                        m.d.comb += self.send_zlp()
                        m.next = "IDLE"

                # Handle GET_USER_BUTTON
                with m.State("HANDLE_GET_USER_BUTTON"):
                    m.d.comb += interface.claim.eq(1)
                    
                    # Prepare button state in data register
                    button_data = Signal(8)
                    m.d.comb += button_data[0].eq(user_button)
                    
                    # Use built-in handler to send data back
                    self.handle_simple_data_request(m, serializer, button_data)

        return m


class LEDPatternGateware(Elaboratable):
    """Simple USB device for LED pattern control."""

    def create_descriptors(self):
        """Create USB descriptors for the device."""
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor = VENDOR_ID
            d.idProduct = PRODUCT_ID
            d.iManufacturer = "Cynthion Project"
            d.iProduct = "LED Pattern Hello World"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                
                # Bulk OUT endpoint for receiving data from host
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = USBDirection.OUT.to_endpoint_address(0x01)
                    e.bmAttributes = USBTransferType.BULK
                    e.wMaxPacketSize = MAX_PACKET_SIZE
                
                # Bulk IN endpoint for sending data to host
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = USBDirection.IN.to_endpoint_address(0x02)
                    e.bmAttributes = USBTransferType.BULK
                    e.wMaxPacketSize = MAX_PACKET_SIZE

        return descriptors

    def elaborate(self, platform):
        m = Module()

        # Configure clocking and reset
        m.submodules.car = platform.clock_domain_generator()

        # Request USB PHY
        ulpi = platform.request("target_phy")

        # Create USB device
        m.submodules.usb = usb = USBDevice(bus=ulpi)

        # Add standard control endpoint with descriptors
        descriptors = self.create_descriptors()
        control_endpoint = usb.add_standard_control_endpoint(descriptors)

        # Add Microsoft OS 1.0 descriptors (for WinUSB driver)
        descriptors.add_descriptor(get_string_descriptor("MSFT100\xee"), index=0xee)
        msft_descriptors = MicrosoftOS10DescriptorCollection()
        with msft_descriptors.ExtendedCompatIDDescriptor() as c:
            with c.Function() as f:
                f.bFirstInterfaceNumber = 0
                f.compatibleID = 'WINUSB'
        with msft_descriptors.ExtendedPropertiesDescriptor() as d:
            with d.Property() as p:
                p.dwPropertyDataType = RegistryTypes.REG_SZ
                p.PropertyName = "DeviceInterfaceGUID"
                p.PropertyData = "{88bae032-5a81-49f0-bc3d-a4ff138216d6}"
        msft_handler = MicrosoftOS10RequestHandler(msft_descriptors, request_code=0xee)
        control_endpoint.add_request_handler(msft_handler)

        # Add LED vendor request handler
        control_endpoint.add_request_handler(LEDVendorRequestHandler())

        # Create bulk IN and OUT endpoints
        ep_out = USBStreamOutEndpoint(
            endpoint_number=0x01,
            max_packet_size=MAX_PACKET_SIZE,
        )
        usb.add_endpoint(ep_out)
        
        ep_in = USBStreamInEndpoint(
            endpoint_number=0x02,
            max_packet_size=MAX_PACKET_SIZE
        )
        usb.add_endpoint(ep_in)

        # Create FIFO for bulk transfers
        m.submodules.fifo = fifo = DomainRenamer("usb")(
            SyncFIFO(width=8, depth=MAX_PACKET_SIZE)
        )

        # Connect OUT endpoint to FIFO write port
        stream_out = ep_out.stream
        m.d.comb += fifo.w_data.eq(stream_out.payload)
        m.d.comb += fifo.w_en.eq(stream_out.valid)
        m.d.comb += stream_out.ready.eq(fifo.w_rdy)

        # Connect IN endpoint to FIFO read port
        stream_in = ep_in.stream
        m.d.comb += stream_in.payload.eq(fifo.r_data)
        m.d.comb += stream_in.valid.eq(fifo.r_rdy)
        m.d.comb += fifo.r_en.eq(stream_in.ready)

        # Connect USB device
        m.d.comb += usb.connect.eq(1)

        return m


if __name__ == "__main__":
    from luna import top_level_cli
    top_level_cli(LEDPatternGateware)
