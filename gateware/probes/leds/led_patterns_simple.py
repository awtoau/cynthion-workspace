#!/usr/bin/env python3
"""
LED Pattern Generator with Button Control - Hello World

Cynthion on-board LED patterns (6 LEDs) controlled by user button.
Press button to cycle through: Chase → Pulse → Strobe → Rainbow → Chase...

Patterns:
  0. Chase: Single LED rotating through all 6 (~0.56s per LED)
  1. Pulse: All LEDs fade in/out with PWM (~3.6 kHz)
  2. Strobe: All LEDs flash on/off (~1 Hz)
  3. Rainbow: Alternating pattern rotation (~1s per flip)

Usage:
  python3 led_patterns_simple.py build
  cynthion-control program led_patterns.bit
"""

from amaranth import *
from amaranth.hdl import *
from amaranth.cli import main


class LEDPatternWithButton(Elaboratable):
    """LED patterns controlled by user button."""
    
    def elaborate(self, platform):
        m = Module()
        
        # Get all 6 LEDs
        leds = Cat(platform.request("led", i).o for i in range(6))
        
        # Get user button (active-low)
        user_button = platform.request("button_user").i
        
        # Main counter for timing patterns
        # 60 MHz clock, so 2^25 = 33.5M counts ≈ 0.56 seconds
        counter = Signal(26)
        m.d.sync += counter.eq(counter + 1)
        
        # Pattern mode (0-3, cycles with button press)
        pattern_mode = Signal(2, init=0)
        
        # Button edge detection
        button_prev = Signal(init=1)
        m.d.sync += button_prev.eq(user_button)
        
        # Falling edge detector (1→0 transition = button pressed)
        button_falling_edge = ~user_button & button_prev
        
        # Cycle pattern on button press
        with m.If(button_falling_edge):
            m.d.sync += pattern_mode.eq(pattern_mode + 1)
        
        # ===== Pattern 0: Chase =====
        # Single LED rotates through all 6 positions
        # Speed: one LED every ~0.5 seconds
        chase_idx = counter[23:26]  # Extracts bits 23-25 (0-7, we use 0-5)
        chase_pattern = Mux(chase_idx < 6, 1 << chase_idx, 0)
        
        # ===== Pattern 1: Pulse (PWM) =====
        # All LEDs pulse in/out with PWM brightness
        pwm_counter = counter[16:20]  # 0-15, changes ~3.6 kHz
        pwm_brightness = Mux(
            counter[20],
            15 - pwm_counter,  # Fade out
            pwm_counter        # Fade in
        )
        # All 6 LEDs on if brightness >= 8
        all_on_pulse = pwm_brightness >= 8
        pulse_pattern = Cat(
            all_on_pulse, all_on_pulse, all_on_pulse,
            all_on_pulse, all_on_pulse, all_on_pulse
        )
        
        # ===== Pattern 2: Strobe =====
        # All LEDs flash on/off (~1 Hz using bit 25)
        strobe_bit = counter[25]
        strobe_pattern = Cat(
            strobe_bit, strobe_bit, strobe_bit,
            strobe_bit, strobe_bit, strobe_bit
        )
        
        # ===== Pattern 3: Rainbow =====
        # Alternating pattern rotation
        rainbow_pattern = Mux(counter[24], 0b010101, 0b101010)
        
        # ===== Select output pattern =====
        with m.Switch(pattern_mode):
            with m.Case(0):
                m.d.comb += leds.eq(chase_pattern)
            with m.Case(1):
                m.d.comb += leds.eq(pulse_pattern)
            with m.Case(2):
                m.d.comb += leds.eq(strobe_pattern)
            with m.Case(3):
                m.d.comb += leds.eq(rainbow_pattern)
        
        return m


if __name__ == "__main__":
    from luna import top_level_cli
    
    # Build with button control for pattern cycling
    top_level_cli(LEDPatternWithButton)
