## Apollo UART/SPI Design Conflict Analysis

**Status**: Phase 2 Design Review  
**Related Issues**: [#15](https://github.com/awtoau/cynthion-workspace/issues/15), [#33](https://github.com/awtoau/cynthion-workspace/issues/33)  
**Reference Docs**: 
- [`apollo_moondancer_uart_watchdog_design.md`](apollo_moondancer_uart_watchdog_design.md) — Proposed UART redesign
- [`cynthion_architecture_scan_2026_05_22.md`](cynthion_architecture_scan_2026_05_22.md) — Pin analysis & Debug SPI discovery
- [`apollo_code_review.md`](apollo_code_review.md) — Phase 2 code review findings

### The Problem

**Current Architecture Vulnerability** (Issue [#15](https://github.com/awtoau/cynthion-workspace/issues/15)):
- moondancer enables `FPGA_ADV` pulse-train to request USB port
- Apollo counts rising edges (>2 in 200ms) and surrenders CONTROL USB
- **If moondancer crashes**: Advertiser stays enabled → Host loses all USB access
- **Recovery**: Power cycle required

**Current Pin Conflict**:
- UART uses PA11/PA14 (same as JTAG TMS/TDI)
- Forces choice between UART console debugging and JTAG access
- Limits diagnostic capability

### Hardware: ATSAMD11D14A

**Pin Summary** (board-level pin usage):
```
ATSAMD11D14A @ 48 MHz, 4KB SRAM, 16KB flash

Used Pins (currently):
├── PA03  ← FPGA_INITN (status input)
├── PA04  ← FPGA_DONE (status input)
├── PA06  ← USB_SWITCH (mux control)
├── PA08  → FPGA_PROGRAM (reset trigger, post-bootup only)
├── PA09  ← FPGA_ADV (pulse-train input for USB negotiation)
├── PA10  ← TDO (JTAG data out)
├── PA11  ← TMS (JTAG mode select) [CONFLICT: also UART RX]
├── PA14  → TDI (JTAG data in) [CONFLICT: also UART TX]
├── PA15  → TCK (JTAG clock)
├── PA16-PA17, PA22-PA23, PA27 → LEDs (5× blue/pink)

Unassigned:
├── PA00, PA01, PA02, PA05, PA07, PA12, PA13, PA18-PA21, PA24-PA26, PA28-PA31
│   (Availability depends on the exact package and board routing.)
└── PA05  ? CONTROL_RESET_DETECT (not currently defined in firmware)
```

### SERCOM Peripheral Options

**SERCOM0** (SPI JTAG — already configured):
```c
// File: src/boards/cynthion_d11/spi.c:44-47
PA14 → SERCOM0 PAD0 (MOSI)
PA15 → SERCOM0 PAD1 (SCK)
PA10 ← SERCOM0 PAD2 (MISO)
```
Status: ✅ Implemented, used for FPGA programming via bit-bang SPI

**SERCOM2** (Dual-use candidate):
```c
// File: src/boards/cynthion_d11/uart.c:85-88 (CURRENT — conflicts with JTAG)
PA11 ← SERCOM2 PAD3 (RX) [conflicts with TMS]
PA14 → SERCOM2 PAD0 (TX) [conflicts with TDI]

// File: src/boards/cynthion_d11/spi.c:92-95 (PLANNED but unimplemented)
case SPI_FPGA_DEBUG:
    _pm_enable_bus_clock(PM_BUS_APBC, SERCOM2);
    _gclk_enable_channel(SERCOM2_GCLK_ID_CORE, GCLK_CLKCTRL_GEN_GCLK0_Val);
    // TODO: pinmux never completed (line 56)
```

### Design Proposals

#### Option 1: UART-Based Watchdog ⭐ **Recommended**

**Reference**: [`apollo_moondancer_uart_watchdog_design.md`](apollo_moondancer_uart_watchdog_design.md)

**Architecture**:
```
Apollo (SAMD11) ←→ moondancer (ECP5/VexRiscv)

PA08 → SERCOM2 TX ──→ moondancer UART RX
PA09 ← SERCOM2 RX ──← moondancer UART TX
PA03 ← INT (watchdog) ← moondancer watchdog signal
PA04 ← status ────── ← moondancer status output
```

**Benefits**:
- ✅ Bidirectional communication (heartbeat/diagnostics)
- ✅ Solves USB vulnerability: Apollo stays on CONTROL USB always
- ✅ Frees JTAG pins PA11/PA14 for debugging
- ✅ Hardware UART (reliable, no bit-banging)

**Implementation** (3 phases):
1. Apollo: Change SERCOM2 pinmux PA11/PA14 → PA08/PA09
2. FPGA: Add UART slave CSR on facedancer gateware
3. moondancer: Replace advertiser with UART response handler

**Risks**:
- ⚠️ Breaks backward compatibility (old UART on PA11/PA14 becomes unavailable)
- ⚠️ FPGA gateware changes needed (adds complexity)

---

#### Option 2: Debug SPI on SERCOM2

**Reference**: [`cynthion_architecture_scan_2026_05_22.md` (Section 5)](cynthion_architecture_scan_2026_05_22.md#5-unimplemented-spi_fpga_debug)

**Architecture**:
```
Apollo (SAMD11) ←→ FPGA (ECP5)

PA08 → SERCOM2 PAD0 (MOSI)
PA09 → SERCOM2 PAD1 (SCK)
PA10 ← SERCOM0 PAD2 (MISO) [shared with JTAG SPI]
```

**Status**: Clocking already configured, pinmux TODO

**Benefits**:
- ✅ Direct SPI access to FPGA debug/config
- ✅ Uses existing infrastructure (clocking set up)
- ✅ Can coexist with JTAG SPI (different chip selects)

**Implementation**:
1. Complete `src/boards/cynthion_d11/spi.c` case SPI_FPGA_DEBUG: pinmux
2. Define PA08/PA09 SPI pin mappings in apollo_board.h
3. Enable `_BOARD_HAS_DEBUG_SPI` for D11
4. Test with existing debug tools

**Risks**:
- ⚠️ Still uses pulse-train for USB negotiation (vulnerability remains)
- ⚠️ **Conflict with Option 1**: Both want PA08/PA09

---

#### Option 3: Hybrid (Requires SERCOM1 or SERCOM3-5)

Move UART to different SERCOM to support both UART + Debug SPI:
- Investigate PA00/PA01/PA05/PA07/PA12/PA13 for SERCOM1/SERCOM3-5 availability
- Requires additional datasheet review for MUX options
- More complex, lower priority

---

### Pin Reallocation Summary

| Pin | Current | Option 1 (UART) | Option 2 (SPI) | Hardware SPI? |
|-----|---------|-----------------|----------------|--------------|
| PA03 | FPGA_INITN | INT watchdog | FPGA_INITN | SERCOM0 PAD1 |
| PA04 | FPGA_DONE | status | FPGA_DONE | SERCOM0 PAD0 |
| PA08 | FPGA_PROGRAM | SERCOM2 TX ✅ | SERCOM2 MOSI ✅ | **CONFLICT** |
| PA09 | FPGA_ADV | SERCOM2 RX ✅ | SERCOM2 SCK ✅ | **CONFLICT** |
| PA10 | TDO (JTAG) | TDO (freed) | TDO (MISO) | SERCOM0 PAD2 |
| PA11 | TMS (JTAG) + UART RX | TMS (freed) | TMS (JTAG) | SERCOM2 PAD3 |
| PA14 | TDI (JTAG) + UART TX | TDI (freed) | TDI (JTAG) | SERCOM0 PAD0 |
| PA15 | TCK (JTAG) | TCK (JTAG) | TCK (JTAG) | SERCOM0 PAD1 |

---

### Recommendation

**Implement Option 1 (UART Redesign)** because:

1. **Solves critical vulnerability** (Issue #15) — moondancer crash no longer breaks CONTROL USB
2. **Enables true debugging** — bidirectional heartbeat/diagnostics
3. **Frees JTAG pins** — PA11/PA14 available for actual JTAG debugging
4. **Better long-term** — Debug SPI is convenient but not critical (existing bit-bang SPI works)
5. **Simpler hardware** — UART is native SERCOM feature, no special pinmux needed

**Next Steps**:
- [ ] Finalize UART protocol specification
- [ ] Implement Phase 1: Apollo firmware (SERCOM2 pinmux PA08/PA09)
- [ ] Implement Phase 2: FPGA gateware (add UART slave CSR)
- [ ] Implement Phase 3: moondancer firmware (UART response handler)
- [ ] Test watchdog timeout scenarios

---

### udev Rules

Install via `cyn setup` or manually:

```udev
# /etc/udev/rules.d/54-cynthion.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615c", MODE="0664", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="615b", MODE="0664", GROUP="plugdev", TAG+="uaccess"
```

---

**For more help, see the full documentation files or run:**
```bash
./scripts/install.py --help
cyn --help
cyn list
```

