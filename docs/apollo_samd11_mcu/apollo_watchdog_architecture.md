## Apollo Watchdog Architecture (Phase 3)

### Problem
Apollo (SAMD11 ARM Cortex-M0+ MCU) and Moondancer (RISC-V softcore on ECP5 FPGA) need robust supervision:
- Currently: no watchdog protection
- Risk: firmware hangs, no recovery mechanism
- Impact: requires manual device restart

### Apollo-Specific Solution: ARM Supervisor
Apollo becomes the watchdog for moondancer:
1. moondancer sends periodic "heartbeat" to Apollo
2. Apollo monitors heartbeat over serial
3. If heartbeat lost → Apollo asserts reset
4. moondancer automatically restarts

### Benefits
- ✓ No additional hardware needed
- ✓ Apollo (always-on) supervises moondancer
- ✓ Automatic recovery on firmware hang
- ✓ Future: can log reboot events

### Implementation Phases
1. **Phase 3a:** Serial heartbeat protocol design
2. **Phase 3b:** Apollo supervisor firmware
3. **Phase 3c:** moondancer integration (send heartbeat)
4. **Phase 3d:** Testing & validation

See [../../debris/docs/apollo_moondancer_uart_watchdog_design.md](../../debris/docs/apollo_moondancer_uart_watchdog_design.md)
(retired) for the original proposal. The shipping link is the FPGA_ADV sideband:
[`../chips/cynone-sideband.md`](../chips/cynone-sideband.md).

---

