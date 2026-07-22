## Watchdog Architecture (Phase 3)

### Problem
Apollo (ARM debug controller) and moondancer (RISC-V firmware) need robust supervision:
- Currently: no watchdog protection
- Risk: firmware hangs, no recovery mechanism
- Impact: requires manual device restart

### Solution: Apollo ARM Supervisor
Apollo becomes the watchdog for moondancer:
1. moondancer sends periodic "heartbeat" to Apollo
2. Apollo monitors heartbeat over serial/CAN
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

See design_proposals/apollo_moondancer_uart_watchdog_design.md for full technical details.

---

