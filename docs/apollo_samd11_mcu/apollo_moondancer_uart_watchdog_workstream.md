# Apollo (SAMD11 ARM Cortex-M0+ MCU) - Moondancer (RISC-V softcore on ECP5 FPGA) UART Watchdog Workstream

**Status**: Active Workstream

**Labels**: `rover`, `enhancement`, `architecture`, `firmware`, `gateware`  
**Related**: #15  
**Blocks**: Removing USB port switching vulnerability

## Summary

The current Apollo (SAMD11 ARM Cortex-M0+ MCU) ↔ Moondancer (RISC-V softcore on ECP5 FPGA) communication uses a **crude pulse-train signaling** on the FPGA_ADV pin that forces:
- USB port switching (Apollo surrenders CONTROL port when moondancer boots)
- Loss of host access if moondancer crashes
- No bidirectional data exchange
- 6+ GPIO pins with bit-banging instead of hardware peripherals

## Proposal

Replace with **hardware UART on Apollo's SERCOM2** using the existing 4 wired pins (PA08/PA09/PA03/PA04):

```
Apollo          FPGA
PA08 TX ─────→ RX
PA09 RX ←───── TX  
PA03 INT ←───── watchdog
PA04 GPIO ←───── status
```

**Benefits:**
- ✅ True bidirectional communication
- ✅ Apollo stays on CONTROL USB always
- ✅ Frees JTAG pins (PA11, PA14)
- ✅ Hardware peripheral integration (no bit-banging)

## Design Document

See [apollo_moondancer_uart_watchdog_design.md](apollo_moondancer_uart_watchdog_design.md) for:
- Detailed architecture
- Implementation plan (3 phases)
- Risk assessment
- Investigation questions

## Key Questions

1. **Has this been tried before?** (label: rover suggests prior work)
2. **Why wasn't UART used initially?**
   - Gate count constraint?
   - Timing concerns?
   - Design decision not documented?
3. **Why does Apollo do so little?** Just JTAG + FPGA loading, not supervisory functions

## Implementation

### Phase 1: Apollo Firmware
- [ ] Change uart.c pinmux: PA11/PA14 → PA08/PA09
- [ ] Add watchdog task to monitor PA03 (INT)
- [ ] Test UART at 115200 baud

### Phase 2: FPGA Gateware  
- [ ] Add UART slave CSR on PA08/PA09
- [ ] Route moondancer UART to CSR
- [ ] Implement INT pin (PA03) logic

### Phase 3: moondancer Firmware
- [ ] Replace advertiser pulse-train with UART handler
- [ ] Implement heartbeat response protocol
- [ ] Implement INT assertion on watchdog timeout

## Testing

- [ ] Apollo can send heartbeat to moondancer over UART
- [ ] moondancer responds with status bytes
- [ ] INT pin toggles on timeout
- [ ] Apollo can hard-reset FPGA via PROGRAM_B
- [ ] No USB port switching occurs
- [ ] Host maintains CONTROL USB connection even if moondancer crashes

## Files Changed

**awto-apollo:**
- firmware/src/boards/cynthion_d11/uart.c (pinmux PA08/PA09)
- firmware/src/boards/cynthion_d11/fpga_adv.c (watchdog INT handler)

**awto-cynthion:**
- cynthion/python/src/gateware/facedancer/top.py (UART slave)
- firmware/moondancer/src/bin/moondancer.rs (UART handler)

## Notes

- This is a **breaking change** - old and new firmware are incompatible
- Deprecation period recommended before fully removing pulse-train support
- Investigation needed: search commit history for clues about why pulse-train was chosen

