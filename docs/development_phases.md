## Development Phases

### Phase 0: Toolchain Review (COMPLETE ✓)
- ✓ RISC-V CPU model (VexRiscv proven)
- ✓ Apollo firmware toolchain (ARM GCC 15.2.0)
- ✓ Python 3.14 no-GIL compatibility (Amaranth 0.5 tested)
- ✓ FPGA programming flow (Yosys/nextpnr/trellis)
- ✓ OSS CAD Suite installation (2026-05-22)

### Phase 1: Toolchain Build (COMPLETE ✓)
- ✓ Apollo firmware clean build (TinyUSB submodule fixed)
- ✓ moondancer firmware clean build (Rust/RISC-V)
- ✓ Analyzer gateware elaboration (Amaranth → bitstream)
- ✗ Facedancer gateware (known luna_soc SPIflash issue)
- ✓ Documented all build issues and improvements

**Status:** 3/4 builds successful
**Improvements:** Fail-fast checks, comprehensive logging, 55% speedup via parallelization

### Phase 2: Apollo Firmware Fixes (NEXT)
- [ ] DFU memory buffer optimization
- [ ] Race condition analysis and fixes
- [ ] Dual CDC interface implementation

### Phase 3-8: UART Architecture
See: [serial_architecture_redesign_plan.md](implementation_plans/serial_architecture_redesign_plan.md)

---

