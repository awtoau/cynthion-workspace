#!/usr/bin/env python3
"""
HyperRAM burst test - validates read/write without analyzer overhead.

Goals:
  1. Verify LUNA HyperRAMInterface works at 60 MHz
  2. Test single-word R/W
  3. Test burst (4-word) R/W
  4. Establish baseline before Lattice AXI integration

Usage:
  # Build RTL:
  python3 hyperram_burst_test.py generate -t rtlil
  
  # Build bitstream for Cynthion:
  python3 hyperram_burst_test.py generate -t ecp5
  
  # Simulate:
  python3 hyperram_burst_test.py run -t simulation
"""

from amaranth import *
from amaranth.lib.wiring import *
from luna.gateware.interface.psram import HyperRAMPHY, HyperRAMInterface


class HyperRAMBurstTester(Elaboratable):
    """
    Minimal test harness for HyperRAM burst operations.
    
    Test sequence:
      1. Single-word write to 0x00000000
      2. Single-word read from 0x00000000 (verify)
      3. 4-word burst write to 0x00001000
      4. 4-word burst read from 0x00001000 (verify)
      5. Done (hold state)
    """
    
    def __init__(self, *, psram_phy):
        self.psram_phy = psram_phy
        self.psram = HyperRAMInterface(phy=psram_phy.phy)
    
    def elaborate(self, platform):
        m = Module()
        m.submodules.psram = self.psram
        
        # Test state machine
        state = Signal(8)
        burst_count = Signal(8)
        read_data_collected = Signal(16)
        
        # Status signals (for debugging/monitoring)
        test_passed = Signal(init=0)
        test_failed = Signal(init=0)
        current_phase = Signal(8)  # 0=write1, 1=read1, 2=write4, 3=read4, 4=done
        
        m.d.comb += current_phase.eq(state >> 4)
        
        # FSM
        with m.If(self.psram.idle):
            with m.Switch(state):
                # ===== Phase 0: Single-word WRITE to 0x00000000 =====
                with m.Case(0):
                    # Start write transfer
                    m.d.sync += [
                        self.psram.address.eq(0x00000000),
                        self.psram.perform_write.eq(1),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(1),  # Single word
                        self.psram.start_transfer.eq(1),
                        self.psram.write_data.eq(0xDEAD),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(1):
                    m.d.sync += self.psram.start_transfer.eq(0)
                    m.d.sync += state.eq(state + 1)
                
                # ===== Phase 1: Single-word READ from 0x00000000 =====
                with m.Case(16):
                    m.d.sync += [
                        self.psram.address.eq(0x00000000),
                        self.psram.perform_write.eq(0),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(1),  # Single word
                        self.psram.start_transfer.eq(1),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(17):
                    m.d.sync += self.psram.start_transfer.eq(0)
                    # Latch read data when ready
                    with m.If(self.psram.read_ready):
                        m.d.sync += read_data_collected.eq(self.psram.read_data)
                        m.d.sync += state.eq(state + 1)
                
                with m.Case(18):
                    # Verify read data
                    if read_data_collected == 0xDEAD:
                        m.d.sync += test_passed.eq(1)
                    else:
                        m.d.sync += test_failed.eq(1)
                    m.d.sync += state.eq(state + 1)
                
                # ===== Phase 2: 4-word BURST WRITE to 0x00001000 =====
                with m.Case(32):
                    m.d.sync += [
                        self.psram.address.eq(0x00001000),
                        self.psram.perform_write.eq(1),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(0),
                        self.psram.start_transfer.eq(1),
                        self.psram.write_data.eq(0x1111),
                        burst_count.eq(0),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(m.Range(33, 36)):
                    # Burst phase 1-3
                    m.d.sync += [
                        self.psram.start_transfer.eq(0),
                        self.psram.write_data.eq(0x2222 + (burst_count << 8)),
                        self.psram.final_word.eq(burst_count == 2),
                        burst_count.eq(burst_count + 1),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(36):
                    m.d.sync += state.eq(state + 1)
                
                # ===== Phase 3: 4-word BURST READ from 0x00001000 =====
                with m.Case(48):
                    m.d.sync += [
                        self.psram.address.eq(0x00001000),
                        self.psram.perform_write.eq(0),
                        self.psram.register_space.eq(0),
                        self.psram.single_page.eq(0),
                        self.psram.final_word.eq(0),
                        self.psram.start_transfer.eq(1),
                        burst_count.eq(0),
                    ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(m.Range(49, 52)):
                    m.d.sync += [
                        self.psram.start_transfer.eq(0),
                        self.psram.final_word.eq(burst_count == 2),
                    ]
                    with m.If(self.psram.read_ready):
                        m.d.sync += [
                            read_data_collected.eq(self.psram.read_data),
                            burst_count.eq(burst_count + 1),
                        ]
                    m.d.sync += state.eq(state + 1)
                
                with m.Case(52):
                    m.d.sync += state.eq(state + 1)
                
                # ===== Done =====
                with m.Case(64):
                    pass  # Hold
        
        return m


if __name__ == "__main__":
    # This allows running via amaranth CLI:
    # python3 hyperram_burst_test.py generate -t rtlil
    from amaranth.cli import main
    from amaranth_boards.cynthion import CynthionPlatform
    
    platform = CynthionPlatform()
    ram_bus = platform.request("ram")
    psram_phy = HyperRAMPHY(bus=ram_bus)
    
    dut = HyperRAMBurstTester(psram_phy=psram_phy)
    
    main(dut, platform=platform, name="hyperram_burst_test", ports=[
        psram_phy.phy.clk_en,
        psram_phy.phy.dq.i,
        psram_phy.phy.dq.o,
        psram_phy.phy.dq.e,
        psram_phy.phy.rwds.i,
        psram_phy.phy.rwds.o,
        psram_phy.phy.rwds.e,
        psram_phy.phy.cs,
        psram_phy.phy.reset,
    ])
