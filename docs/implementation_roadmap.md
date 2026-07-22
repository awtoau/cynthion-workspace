## Implementation Roadmap (Phases)

### Phase 0: Toolchain Review (COMPLETE ✓)
- Verified Python 3.14, Yosys 0.65, nextpnr 0.10
- Identified RISC-V model (VexRiscv)
- Confirmed FPGA flow with Amaranth 0.5
- Status: Ready for Phase 1

### Phase 1: Build System & Parallelization (COMPLETE ✓)
- Implemented fail-fast prerequisite checks
- Added Python 3.14 no-GIL parallelization (55% faster)
- Built all components: Apollo ✓, moondancer ✓, analyzer ✓, facedancer ✗
- Status: 3/4 successful (facedancer blocked by luna_soc bug)

### Phase 2: Issue Resolution & Cleanup
- **Facedancer:** Resolve luna_soc SPIflash Field TypeError
- **Apollo:** Firmware improvements (DFU buffers, race conditions, CDC interfaces)
- **Documentation:** Consolidate and categorize docs under `docs/` with informative filenames
- Status: In progress

### Phase 3-8: Serial Architecture Redesign
- Redesign Apollo-moondancer communication
- Implement UART watchdog supervisor
- Improve reliability and maintainability
- See: apollo_samd11_mcu/apollo_moondancer_uart_watchdog_design.md for details

---

