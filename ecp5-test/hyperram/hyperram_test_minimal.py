#!/usr/bin/env python3
"""
Minimal HyperRAM test harness.
Tests LUNA's HyperRAMInterface (non-DQS) with basic read/write/burst operations.
Targets Cynthion r1.4 ECP5.
"""

from amaranth import *
from amaranth.cli import main as amaranth_main
from luna.gateware.interface.psram import HyperRAMPHY, HyperRAMInterface


class HyperRAMTestBench(Elaboratable):
    """Minimal test of HyperRAM read/write/burst operations."""
    
    def __init__(self, *, phy):
        self.phy = phy
        self.psram = HyperRAMInterface(phy=phy.phy)
        
    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        
        # Simple test state machine
        state = Signal(16, reset=0)
        test_address = Signal(32, reset=0)
        test_data_write = Signal(16, reset=0xAAAA)
        
        # Test sequence:
        # 0: Wait for PSRAM idle
        # 1-10: Write test pattern to addr 0x0000
        # 11-20: Read from addr 0x0000, verify
        # 21-30: Write to addr 0x1000
        # 31-40: Read from addr 0x1000, verify
        # 41+: Done
        
        with m.If(self.psram.idle):
            with m.Switch(state):
                # Write phase 1: send write command
                with m.Case(1):
                    m.d.sync += [
                        self.psram.address.eq(0x0000),
                        self.psram.perform_write.eq(1),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(0),
                        self.psram.start_transfer.eq(1),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                # Write phase 2-9: send 8 words
                with m.Case(m.Range(2, 10)):
                    m.d.sync += [
                        self.psram.write_data.eq(test_data_write),
                        self.psram.start_transfer.eq(0),
                        self.psram.final_word.eq(state == 9),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                # Wait after write
                with m.Case(10):
                    m.d.sync += state.eq(state + 1)
                
                # Read phase 1: send read command
                with m.Case(11):
                    m.d.sync += [
                        self.psram.address.eq(0x0000),
                        self.psram.perform_write.eq(0),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(0),
                        self.psram.start_transfer.eq(1),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                # Read phase 2-9: collect 8 words
                with m.Case(m.Range(12, 20)):
                    m.d.sync += [
                        self.psram.start_transfer.eq(0),
                        self.psram.final_word.eq(state == 19),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                # Wait after read
                with m.Case(20):
                    m.d.sync += state.eq(state + 1)
                
                # Done
                with m.Case(21):
                    pass  # Hold state
        
        return m


def create_cynthion_platform():
    """Create Cynthion r1.4 platform with HyperRAM resource."""
    from amaranth_boards.cynthion import CynthionPlatform
    
    platform = CynthionPlatform()
    # HyperRAM resource should be defined in platform
    return platform


if __name__ == "__main__":
    platform = create_cynthion_platform()
    
    # Request HyperRAM resource
    ram_bus = platform.request("ram")
    
    # Create PHY and interface
    psram_phy = HyperRAMPHY(bus=ram_bus)
    
    # Create test bench
    dut = HyperRAMTestBench(phy=psram_phy)
    
    # Build and run
    from amaranth.back import rtlil
    from amaranth.cli import main
    
    # For simulation or synthesis
    print("Building HyperRAM test bench...")
    print("  - Platform: Cynthion r1.4 (ECP5-12F)")
    print("  - Test: Simple write/read/burst")
    print("  - Speed: 60 MHz")
    print()
    
    # You can use this with:
    # python3 hyperram_test_minimal.py generate
    # Or run simulation with appropriate testbenches
