# Apollo DFU Buffer Issues

**Status**: Phase 2 Code Review  
**Date**: 2026-05-23  
**Severity**: Medium  

## Problem Statement

DFU (Device Firmware Update) buffer handling on SAMD11 MCU variant has potential issues:

1. **Tight Memory Constraints**: SAMD11 has only 32KB total RAM
   - 4KB allocated to DFU buffer (`TUD_DFU_RT_DESCRIPTOR(..., 500, 4096)`)
   - USB stack requires ~2-3KB
   - Application code + stack share remainder
   - No explicit overflow protection

2. **Detach Sequence Synchronization**: 
   - `dfu_mount()` handler triggers immediate reboot to bootloader
   - No guarantee detach request completes before reboot
   - Potential for incomplete transfers leaving device in bad state

3. **Missing Documentation**:
   - Buffer size limits per MCU not documented
   - DFU timeout behavior unspecified
   - Recovery path for interrupted transfers unclear

## Affected Files

- `src/mcu/rp2040/dfu.c` — DFU implementation
- `src/mcu/samd11/usb_descriptors.c` — Descriptor configuration
- `src/boards/cynthion_d11/tusb_config.h` — TinyUSB config

## Current Code

```c
// src/mcu/samd11/usb_descriptors.c
TUD_DFU_RT_DESCRIPTOR(ITF_NUM_DFU_RT, 0, 0x0d, 500, 4096),
```

## Recommended Actions

- [ ] Document buffer size limits (min/max) per MCU variant
- [ ] Add compile-time assertion: buffer + USB stack + app < total_ram
- [ ] Implement explicit buffer overflow checks in DFU handler
- [ ] Add timeout mechanism for detach sequence
- [ ] Define recovery procedure for interrupted transfers
- [ ] Test firmware update on SAMD11 with <16KB free memory

## Testing Matrix

| Test Case | MCU | Status |
|-----------|-----|--------|
| Normal DFU update | SAMD11 | ? |
| Interrupted transfer | SAMD11 | ? |
| Low memory condition | SAMD11 | ? |
| Timeout behavior | SAMD11 | ? |
| Buffer exhaustion | SAMD11 | ? |

## Notes

- RP2040 variant has more RAM (264KB), less critical but should follow same pattern
- TinyUSB version may have built-in protection; check version and settings
- Actual buffer exhaustion testing needed to confirm issue severity
