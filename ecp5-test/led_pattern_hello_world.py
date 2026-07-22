#!/usr/bin/env python3
"""
LED Pattern Generator - Hello World for Cynthion + USB + Gateware

Simple example showing:
  - Gateware LED control via USB vendor requests
  - Python host-side pattern generation
  - 6-LED chase, pulse, and strobe patterns

Usage:
  python3 led_pattern_hello_world.py

Gateware Side:
  - Vendor request 0x01: SET_LED_PATTERN (write 1 byte pattern to 6 LEDs)
  - Vendor request 0x02: SET_LED_MODE (0=static, 1=chase, 2=pulse)
  - Vendor request 0x03: GET_BUTTON (read user button state)
"""

import sys
import time
from pathlib import Path

# Try to import usb library
try:
    import usb.core
    import usb.util
except ImportError:
    print("ERROR: pyusb not installed. Install with: pip install pyusb")
    sys.exit(1)


class CynthionLEDController:
    """Control Cynthion LEDs via USB."""
    
    # USB IDs (from gateware example)
    VENDOR_ID = 0x1209
    PRODUCT_ID = 0x0001
    
    # Vendor request codes
    VENDOR_SET_LED_PATTERN = 0x01
    VENDOR_SET_LED_MODE = 0x02
    VENDOR_GET_USER_BUTTON = 0x03
    
    # LED modes
    MODE_STATIC = 0
    MODE_CHASE = 1
    MODE_PULSE = 2
    
    def __init__(self):
        """Find and connect to Cynthion board."""
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        
        if self.dev is None:
            raise RuntimeError(
                f"Cynthion board not found (VID:PID={self.VENDOR_ID:04x}:{self.PRODUCT_ID:04x})\n"
                "  Make sure gateware is programmed and device is connected."
            )
        
        print(f"✓ Found Cynthion: {self.dev.manufacturer} {self.dev.product}")
    
    def set_led_pattern(self, pattern):
        """Set LED pattern (6-bit value, one bit per LED).
        
        pattern: int (0-63)
          Bit 0 = LED 1, Bit 1 = LED 2, ..., Bit 5 = LED 6
        """
        assert 0 <= pattern <= 0x3F, "Pattern must be 0-63 (6 bits)"
        
        self.dev.ctrl_transfer(
            bmRequestType=0x40,  # Device-to-host, vendor
            bRequest=self.VENDOR_SET_LED_PATTERN,
            wValue=0,
            wIndex=0,
            data_or_wLength=bytes([pattern & 0x3F])
        )
    
    def set_led_mode(self, mode):
        """Set LED animation mode.
        
        mode: int
          0 = Static (no animation)
          1 = Chase (LED shift pattern)
          2 = Pulse (all LEDs fade in/out)
        """
        assert 0 <= mode <= 2, "Mode must be 0-2"
        
        self.dev.ctrl_transfer(
            bmRequestType=0x40,
            bRequest=self.VENDOR_SET_LED_MODE,
            wValue=0,
            wIndex=0,
            data_or_wLength=bytes([mode])
        )
    
    def get_button_state(self):
        """Read user button state.
        
        Returns: bool (True if pressed, False if released)
        """
        result = self.dev.ctrl_transfer(
            bmRequestType=0xC0,  # Host-to-device, vendor
            bRequest=self.VENDOR_GET_USER_BUTTON,
            wValue=0,
            wIndex=0,
            data_or_wLength=1
        )
        return bool(result[0] & 0x01)


def main():
    print("=" * 70)
    print("Cynthion LED Pattern - Hello World")
    print("=" * 70)
    print()
    
    try:
        led_ctrl = CynthionLEDController()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    
    print("LED Patterns:")
    print()
    
    # Pattern 1: Single LED sweep (chase)
    print("1. Single LED sweep (chase pattern)")
    for i in range(6):
        pattern = 1 << i  # Single bit at position i
        print(f"   LED {i+1} = 0b{pattern:06b}")
        led_ctrl.set_led_pattern(pattern)
        time.sleep(0.2)
    print()
    
    # Pattern 2: All LEDs off
    print("2. All LEDs off")
    led_ctrl.set_led_pattern(0x00)
    time.sleep(0.5)
    print()
    
    # Pattern 3: All LEDs on
    print("3. All LEDs on")
    led_ctrl.set_led_pattern(0x3F)  # 0b111111
    time.sleep(0.5)
    print()
    
    # Pattern 4: Alternating pattern
    print("4. Alternating pattern")
    for pattern in [0x15, 0x2A]:  # 0b010101, 0b101010
        print(f"   Pattern = 0b{pattern:06b}")
        led_ctrl.set_led_pattern(pattern)
        time.sleep(0.3)
    print()
    
    # Pattern 5: Growing bar
    print("5. Growing bar pattern")
    for i in range(1, 7):
        pattern = (1 << i) - 1  # 0b000001, 0b000011, ..., 0b111111
        print(f"   LEDs 1-{i} = 0b{pattern:06b}")
        led_ctrl.set_led_pattern(pattern)
        time.sleep(0.2)
    time.sleep(0.5)
    print()
    
    # Pattern 6: Shrinking bar
    print("6. Shrinking bar pattern")
    for i in range(6, 0, -1):
        pattern = (1 << i) - 1
        print(f"   LEDs 1-{i} = 0b{pattern:06b}")
        led_ctrl.set_led_pattern(pattern)
        time.sleep(0.2)
    time.sleep(0.5)
    print()
    
    # Final: All off
    print("7. Turning all LEDs off")
    led_ctrl.set_led_pattern(0x00)
    print()
    
    print("=" * 70)
    print("✓ LED pattern demo complete!")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
