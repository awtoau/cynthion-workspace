# Apollo Firmware Code Review

**Date**: 2026-05-23  
**Scope**: Review of known/potential issues in Apollo debug controller firmware  
**Status**: Phase 2 documentation task

## Summary

Apollo is the ARM-based debug controller on Cynthion. This review covers three areas flagged for Phase 2 analysis:
1. **DFU buffer handling**
2. **Race conditions** in USB/FPGA state management
3. **Dual CDC** (composite USB with two serial ports)

---

## 1. DFU (Device Firmware Update) Buffer Issues

### Current Implementation
- **Location**: `src/mcu/rp2040/dfu.c`, `src/mcu/samd11/usb_descriptors.c`
- **Config**: DFU Runtime interface enabled (`CFG_TUD_DFU_RUNTIME = 1`)
- **Buffer size**: 4096 bytes (`TUD_DFU_RT_DESCRIPTOR(..., 500, 4096)`)

### Known Considerations

1. **Buffer allocation on SAMD11** (limited RAM)
   - ATSAMD11D14A has 4KB SRAM and 16KB flash
   - 4KB DFU buffer + USB stack + application stack = tight memory
   - No explicit mention of buffer overflow checks

2. **Detach/Reboot sequence**
   - `dfu_mount()` handler triggers reboot to bootloader
   - Timing-dependent: detach request must complete before reboot
   - No explicit synchronization mechanism

3. **Missing documentation**
   - Buffer size limits for different MCUs not documented
   - DFU timeout behavior not specified
   - Recovery path if DFU transfer interrupted is unclear

### Recommendations

- [ ] Document buffer requirements per MCU variant
- [ ] Add explicit buffer overflow protection
- [ ] Review detach sequence synchronization
- [ ] Add timeout handling for incomplete transfers

---

## 2. Race Conditions

### USB State Management

**Location**: `src/vendor.c`, `src/fpga.c`, `src/fpga_adv.c`

#### Issue 1: FPGA Handoff Race

```c
// From src/vendor.c (simplified)
case VENDOR_REQUEST_TAKE_OVER:
    fpga_take_over();  // No synchronization
    break;
```

**Problem**: 
- Multiple USB hosts could request `TAKE_OVER` simultaneously
- FPGA state changes without atomic operations
- No mutex or flag to prevent concurrent access

**Impact**: 
- Undefined FPGA state if host commands race
- Potential USB enumeration issues

#### Issue 2: FPGA Online/Offline Transition

**Location**: `src/fpga.c`

```c
void fpga_set_state(int state) {
    // Sets pins, waits, resets - but no locking
    // Another thread could call this while in progress
}
```

**Problem**:
- No protection against concurrent state changes
- Pin state changes are not atomic
- Timing assumptions not validated

#### Issue 3: USB Disconnect During FPGA Access

**Location**: `src/debug_spi.c`

```c
// TODO: don't run this on r0.2+ boards?
```

The TODO comment suggests uncertain behavior on hardware revisions.

### Recommendations

- [ ] Add mutex protection to `fpga_set_state()`
- [ ] Implement atomic flags for USB handoff
- [ ] Add state validation checks
- [ ] Document hardware revision-specific behavior
- [ ] Review timing assumptions on all FPGA transitions

---

## 3. Dual CDC (Composite USB)

### Current Implementation

**Location**: `src/mcu/samd11/usb_descriptors.c`, `src/mcu/rp2040/usb_descriptors.c`

```c
#define CONFIG_TOTAL_LEN    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_DFU_RT_DESC_LEN)
```

Current descriptor structure:
- 1× CDC (UART console)
- 1× DFU Runtime

### Investigation Results

**Status**: The codebase **does not currently implement dual CDC**

What exists:
- Single CDC interface for debug UART
- DFU Runtime interface
- Debug SPI interface

What's missing:
- Second CDC endpoint/interface
- Dual-endpoint configuration
- Composite descriptor for 2×CDC + DFU

### Known Limitations

1. **Single serial port** to MCU limits debug capability
2. **No separate FPGA→Host debug channel** (only JTAG indirect access)
3. **Console and vendor requests share** a single USB pipe

### Recommendations for Dual CDC

If implementing dual CDC:

- [ ] Add second CDC interface descriptor
- [ ] Allocate additional endpoints (IN/OUT per CDC)
- [ ] Update `tusb_config.h` with second CDC config
- [ ] Map second CDC to FPGA debug/UART bridge
- [ ] Test with TinyUSB's composite example
- [ ] Verify buffer requirements under dual-channel load

---

## Code Quality Issues Found

| Issue | File | Line | Severity | Notes |
|-------|------|------|----------|-------|
| Incomplete UART config | `src/boards/cynthion_d11/uart.c` | — | Low | TODO: support parity, stop bits |
| Incomplete board init | `src/boards/daisho/uart.c` | — | Low | FIXME: implement all of this! |
| Platform-specific JTAG TODO | `src/boards/daisho/platform_jtag.h` | — | Low | TODO: optimize these? |
| Uncertain r0.2 behavior | `src/debug_spi.c` | — | Medium | TODO: don't run on r0.2+? |
| Neopixel support missing | `src/boards/qtpy/apollo_board.h` | — | Low | TODO: handle RGB neopixel |
| SPI FIFO optimization | `src/boards/cynthion_d21/spi.c` | — | Low | TODO: use FIFO to bulk send |

---

## Testing Requirements

### For DFU Buffer Issues
- [ ] Test firmware update on SAMD11 with <16KB free memory
- [ ] Test interrupted DFU transfer recovery
- [ ] Validate timeout behavior

### For Race Conditions
- [ ] Concurrent FPGA state change requests
- [ ] USB disconnect during FPGA reconfiguration
- [ ] Rapid online/offline cycling
- [ ] Multi-host access scenarios (if supported)

### For Dual CDC (if implemented)
- [ ] Simultaneous writes to both CDC ports
- [ ] High-speed data transfer on both channels
- [ ] USB buffer exhaustion handling
- [ ] Device enumeration with full descriptor set

---

## Priority Assessment

### High Priority
- Race condition fixes (state synchronization)
- Buffer overflow protection (DFU and USB)

### Medium Priority
- Code documentation (hardware revision behavior)
- Timeout handling (DFU detach sequence)
- Dual CDC implementation (if feature needed)

### Low Priority
- Minor TODOs (UART config, SPI optimization)
- Neopixel support

---

## Next Actions

1. **Implement mutex/synchronization** for FPGA state changes
2. **Document buffer requirements** per MCU variant
3. **Add DFU timeout handling**
4. **Resolve hardware revision TODOs** (mark r0.2 as unsupported if needed)
5. **If dual CDC needed**: Plan integration of second CDC interface

---

## Files to Review Further

- `src/vendor.c` — USB request handler (potential races)
- `src/fpga.c` — FPGA state management (critical section)
- `src/debug_spi.c` — Hardware version detection
- `src/mcu/rp2040/dfu.c` — DFU implementation
