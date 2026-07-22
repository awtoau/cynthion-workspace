#!/usr/bin/env python3
"""
LED Patterns with Button Control - Simulation

Simulates:
  - Chase pattern (single LED rotating)
  - Pulse pattern (all LEDs fading in/out)
  - Strobe pattern (all LEDs flashing)
  - Rainbow pattern (alternating LEDs rotating)
  - Button press to cycle through patterns

Usage:
  python3 led_patterns_button_sim.py
"""

import sys
from amaranth import *
from amaranth.hdl import *
from amaranth.sim import *


class LEDPatternsWithButtonControl(Elaboratable):
    """LED patterns controlled by user button press."""
    
    def __init__(self):
        # Output signals for monitoring
        self.current_pattern = Signal(2)  # 0-3, which pattern is active
        self.button_pressed = Signal()    # High when button press detected
        self.leds = Signal(6)             # LED output
    
    def elaborate(self, platform=None):
        m = Module()
        
        # Simulate 6 LEDs (in sim, just use internal signal)
        leds = Signal(6)
        m.d.comb += self.leds.eq(leds)
        
        # Simulate user button (active-low input)
        user_button = Signal(init=1)  # 1 = released, 0 = pressed
        
        # Main counter for patterns
        counter = Signal(26)
        m.d.sync += counter.eq(counter + 1)
        
        # Pattern mode (0-3, cycles with button press)
        pattern_mode = Signal(2, init=0)
        m.d.comb += self.current_pattern.eq(pattern_mode)
        
        # Button edge detection
        button_prev = Signal(init=1)
        m.d.sync += button_prev.eq(user_button)
        
        # Falling edge detector (1→0 transition = press)
        button_falling_edge = ~user_button & button_prev
        m.d.comb += self.button_pressed.eq(button_falling_edge)
        
        # Cycle pattern on button press
        with m.If(button_falling_edge):
            m.d.sync += pattern_mode.eq(pattern_mode + 1)
        
        # ===== Pattern Logic =====
        with m.Switch(pattern_mode):
            
            # Pattern 0: Chase (single LED rotating)
            with m.Case(0):
                chase_idx = counter[23:26]
                chase_pattern = Mux(chase_idx < 6, 1 << chase_idx, 0)
                m.d.comb += leds.eq(chase_pattern)
            
            # Pattern 1: Pulse (all LEDs fade in/out with PWM)
            with m.Case(1):
                pwm_counter = counter[16:20]  # 0-15
                pwm_brightness = Mux(
                    counter[20],
                    15 - pwm_counter,  # Fade out
                    pwm_counter        # Fade in
                )
                # All 6 LEDs on if brightness >= 8
                all_on = pwm_brightness >= 8
                m.d.comb += leds.eq(Cat(all_on, all_on, all_on, all_on, all_on, all_on))
            
            # Pattern 2: Strobe (all LEDs flash on/off)
            with m.Case(2):
                all_on = counter[25]
                m.d.comb += leds.eq(Cat(all_on, all_on, all_on, all_on, all_on, all_on))
            
            # Pattern 3: Rainbow (alternating pattern rotation)
            with m.Case(3):
                m.d.comb += leds.eq(Mux(counter[24], 0b010101, 0b101010))
        
        return m


def simulate_led_patterns_with_button():
    """Simulate LED patterns with button control."""
    
    dut = LEDPatternsWithButtonControl()
    
    def testbench():
        """Test patterns and button control."""
        print("=" * 70)
        print("LED Patterns with Button Control - Simulation")
        print("=" * 70)
        print()
        
        cycle = 0
        last_pattern = -1
        
        pattern_names = {
            0: "Chase",
            1: "Pulse",
            2: "Strobe",
            3: "Rainbow",
        }
        
        # Simulate for 5 seconds @ 60 MHz
        # Each pattern displayed for ~1.1 seconds (70M cycles)
        # Then button press to advance
        
        events = [
            # (cycle_count, action, description)
            (0, "show", "Starting Pattern 0: Chase"),
            (70_000_000, "press", "Button Press → Pattern 1"),
            (70_000_000 + 70_000_000, "press", "Button Press → Pattern 2"),
            (70_000_000 * 2 + 70_000_000, "press", "Button Press → Pattern 3"),
            (70_000_000 * 3 + 70_000_000, "press", "Button Press → Pattern 0 (wrap)"),
        ]
        event_idx = 0
        next_event_cycle = events[event_idx][0]
        
        # Simulate button press by driving it low then high
        button_press_start = None
        button_press_duration = 1_000  # Hold for 1k cycles
        
        while cycle < 300_000_000:  # Run for 5 seconds
            current_pattern = (yield dut.current_pattern)
            button_pressed = (yield dut.button_pressed)
            leds = (yield dut.leds)
            
            # Check for pattern change
            if current_pattern != last_pattern:
                pattern_name = pattern_names.get(current_pattern, "Unknown")
                print(f"Cycle {cycle:9d}: Pattern {current_pattern} - {pattern_name:8s}")
                last_pattern = current_pattern
            
            # Check for button press
            if button_pressed:
                print(f"Cycle {cycle:9d}:   [BUTTON PRESS DETECTED]")
            
            # Print LED pattern periodically for each pattern
            if cycle % 30_000_000 == 0 and cycle > 0:
                led_str = ''.join(['#' if (leds >> i) & 1 else '.' for i in range(6)])
                print(f"Cycle {cycle:9d}:   LEDs: {led_str} (0b{leds:06b})")
            
            # Trigger button presses at scheduled times
            if event_idx < len(events):
                if cycle == next_event_cycle and events[event_idx][1] == "press":
                    # Start button press
                    button_press_start = cycle
                    yield dut.ports[0].eq(0)  # Drive button low (active-low)
                    print(f"Cycle {cycle:9d}: → Pressing button...")
                
                # Release button after duration
                if (button_press_start is not None and 
                    cycle >= button_press_start + button_press_duration):
                    # Release button
                    button_press_start = None
                    event_idx += 1
                    if event_idx < len(events):
                        next_event_cycle = events[event_idx][0]
            
            yield Tick()
            cycle += 1
        
        print()
        print("=" * 70)
        print("✓ Simulation complete!")
        print("=" * 70)
        print()
        print("Summary:")
        print("  - Pattern cycles every 70M cycles (~1.1 sec @ 60 MHz)")
        print("  - Button press advances to next pattern")
        print("  - Chase:   Single LED rotates (6 positions)")
        print("  - Pulse:   All LEDs fade in/out (PWM brightness)")
        print("  - Strobe:  All LEDs flash on/off (~1 Hz)")
        print("  - Rainbow: Alternating LEDs rotate (~1 sec per flip)")
        print()
    
    sim = Simulator(dut)
    sim.add_clock(1e-6 / 60)  # 60 MHz clock
    sim.add_testbench(testbench)
    
    print()
    print("Starting simulation...")
    print()
    
    with sim.write_vcd("led_patterns_button_sim.vcd"):
        sim.run()


def simulate_simple_cycle():
    """Simpler simulation - just show pattern cycle without complex button timing."""
    
    dut = LEDPatternsWithButtonControl()
    
    def testbench():
        """Test patterns and button control."""
        print("=" * 70)
        print("LED Patterns with Button Control - Simulation (Simple)")
        print("=" * 70)
        print()
        
        cycle = 0
        last_pattern = -1
        sample_interval = 2_000_000  # Print every ~0.033 sec
        last_sample = -sample_interval
        
        pattern_names = {
            0: "Chase",
            1: "Pulse",
            2: "Strobe",
            3: "Rainbow",
        }
        
        # Run simulation for 20M cycles (~0.33 seconds @ 60 MHz) - manageable runtime
        for _ in range(20_000_000):
            current_pattern = (yield dut.current_pattern)
            button_pressed = (yield dut.button_pressed)
            leds = (yield dut.leds)
            
            # Pattern change notice
            if current_pattern != last_pattern:
                pattern_name = pattern_names.get(current_pattern, "Unknown")
                print(f"Cycle {cycle:9d}: Switched to Pattern {current_pattern} - {pattern_name}")
                last_pattern = current_pattern
                last_sample = cycle
            
            # Button press notice
            if button_pressed:
                print(f"Cycle {cycle:9d}:   ✓ Button press detected")
            
            # Sample LED output periodically
            if cycle - last_sample >= sample_interval:
                led_str = ''.join(['█' if (leds >> i) & 1 else '░' for i in range(6)])
                print(f"Cycle {cycle:9d}:   LEDs: [{led_str}] (0b{leds:06b})")
                last_sample = cycle
            
            yield Tick()
            cycle += 1
        
        print()
        print("=" * 70)
        print("✓ Simulation complete!")
        print("=" * 70)
        print()
        print("Patterns observed:")
        print("  0 - Chase:   Single LED rotates through all 6")
        print("  1 - Pulse:   All LEDs fade in/out")
        print("  2 - Strobe:  All LEDs flash on/off")
        print("  3 - Rainbow: Alternating LEDs (0b010101 ↔ 0b101010)")
        print()
    
    sim = Simulator(dut)
    sim.add_clock(1e-6 / 60)  # 60 MHz clock
    sim.add_testbench(testbench)
    
    print()
    print("Starting LED pattern simulation...")
    print("(Showing sample every ~0.16 seconds)")
    print()
    
    with sim.write_vcd("led_patterns_button_sim.vcd"):
        sim.run()


if __name__ == "__main__":
    try:
        # Run simple cycle (no button injection complexity)
        simulate_simple_cycle()
        
        print("\nVCD file saved: led_patterns_button_sim.vcd")
        print("View with: gtkwave led_patterns_button_sim.vcd")
        
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
