# Architectural Decisions - Cynthion Serial Communication Redesign

**Date**: 2026-05-22  
**Status**: Design Review  
**Label**: rover

## Core Finding: Original Design Lost in Translation

### What Was Intended
- **Apollo ↔ moondancer**: Serial communication (not USB port switching)
- **moondancer serial**: Only tool for moondancer to communicate with host (via Apollo tunneling)
- **moondancer RISC-V CPU**: Logs to serial, sends messages to Apollo — no USB handling
- **USB port switching**: Reserved for future high-filtered MITM capture scenarios (e.g., real-time Wireshark), not for moondancer port ownership

### What Actually Happened (April 2024)
Commit 4208bc6 added:
- ApolloAdvertiser (pulse-train on FPGA_ADV pin)
- USB port switching logic (moondancer takes over CONTROL port)
- Second UART (Serial1 on PMOD B) — **still defined but unused**

Someone misunderstood the architecture and implemented USB port switching for moondancer to "own" the CONTROL port, instead of routing serial data through Apollo.

## Current State Analysis

### What Still Exists (Infrastructure In Place)
✓ moondancer logs to Serial0 and Serial1 (both enabled)  
✓ PMOD B UART defined in gateware (uart1 resource)  
✓ moondancer HAL supports Serial0 and Serial1  
✓ Firmware has log.rs with Port::Both (logs to both UARTs)  

### What's Broken (Misuse of Infrastructure)
✗ Serial0 (R14/T14) wired to internal FPGA_ADV pulse-train hack  
✗ Apollo counts rising edges instead of reading proper serial frames  
✗ moondancer forced to surrender CONTROL USB port  
✗ If moondancer crashes: advertiser keeps running → host loses all USB access  

### What's Unused (But Available)
- PMOD B UART (Serial1) — moondancer logs to it but nobody reads it
- Original serial communication path — infrastructure defined but not used
- USB switch capability — meant for MITM filtering, not port ownership

## Proposed Fix: Return to Original Architecture

### Serial-Only Design
```
moondancer (FPGA RISC-V CPU)
    ↓ UART (serial data only)
Apollo MCU
    ↓ USB CDC-ACM
Host
  - /dev/ttyACM0: JTAG (always available)
  - /dev/ttyACM1: moondancer serial (log + messages)
```

### Changes Required

**moondancer firmware**:
- Remove USB communication entirely
- Log ONLY to serial (Serial0 or Serial1)
- Send all status/diagnostics via serial to Apollo
- No advertiser CSR manipulation
- Let Apollo handle all host communication

**Apollo firmware**:
- Dual CDC interfaces: JTAG + console/moondancer-serial
- Read moondancer serial from FPGA pins
- No USB port switching logic
- Tunnel moondancer serial to host as /dev/ttyACM1

**FPGA gateware**:
- Remove advertiser pulse-train
- Keep moondancer serial outputs (R14/T14 or alternate pins)
- No USB mux control from FPGA

**UART between Apollo and moondancer**:
- PA08 (TX) → moondancer RX
- PA09 (RX) ← moondancer TX
- Heartbeat protocol: 0x5A request, status response
- INT pin (PA03) for watchdog timeout detection

### Future Optimization (Not Required Now)
USB port switching can be added later for:
- High-speed filtered MITM capture
- Real-time packet analysis (Wireshark integration)
- Conditional port routing based on filter rules

## Evidence Supporting This Understanding

**Commit 4208bc6** (April 2024): "moondancer: add a second uart and an ApolloAdvertiser to soc"
- Before: moondancer had PMOD B UART + JTAG resources defined (original design)
- After: Added advertiser + USB switching (deviation from original)
- Author: Antoine van Gelder

**Apollo issue #116** (upstream, OPEN): "FPGA flash can fail if gateware takes over JTAG pins"
- States: "Apollo should be able to handle this situation without having to work around it in gateware"
- Implies: Pulse-train is a workaround, not intentional design

**Cynthion issue #255** (MERGED): "gateware: increase facedancer softcore startup delay"
- Adds timing hack to work around JTAG takeover race condition
- Proof the current design is fragile and timing-sensitive

**moondancer log.rs** (existing code):
- Serial0 and Serial1 fully implemented and working
- Port::Both logs to both UARTs
- Shows original design expectation that serial would be primary communication

## Key Insight: moondancer is a RISC-V

Since moondancer is a full RISC-V CPU inside the FPGA, wasting its resources on USB port switching and pulse-train negotiation is inefficient. Let Apollo (the MCU) handle serial communication — it's designed for it.

## Related Issues

- [PMOD B serial debugging option](https://github.com/awtoau/awto-cynthion/issues/33) — documented as future debugging path if users have PMOD B connector
- Apollo issue #116 — FPGA/JTAG pin conflict workaround
- Cynthion issue #245/255 — bitstream configuration race condition
