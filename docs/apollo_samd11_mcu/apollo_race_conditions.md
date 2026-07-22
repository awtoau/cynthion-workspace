# Apollo Race Conditions in State Management

**Status**: Phase 2 Code Review  
**Date**: 2026-05-23  
**Severity**: High  

## Overview

Multiple race conditions identified in FPGA state management and USB handoff logic. These could cause undefined FPGA state or USB enumeration issues under concurrent access.

---

## Issue 1: FPGA Handoff Race (Critical)

**Location**: `src/vendor.c` — USB vendor request handler

```c
case VENDOR_REQUEST_TAKE_OVER:
    fpga_take_over();  // No synchronization!
    break;
```

### Problem
- Multiple USB hosts could simultaneously request `TAKE_OVER`
- FPGA state changes without atomic operations
- No mutex, semaphore, or flag to serialize access
- Concurrent calls lead to undefined pin state

### Impact
- **Severity**: HIGH
- FPGA could end up in unknown state
- Host firmware expects exclusive access; concurrent hosts violate assumption
- May cause USB re-enumeration loops

### Example Race
```
Host A: TAKE_OVER request → fpga_take_over() enters
Host B: TAKE_OVER request → fpga_take_over() enters (concurrent)
Result: Pin state toggles unpredictably
```

---

## Issue 2: FPGA Online/Offline Transition (High)

**Location**: `src/fpga.c`

```c
void fpga_set_state(int state) {
    // Changes pins, waits, resets
    // No protection against concurrent calls
    set_pin(FPGA_RESET, ...);
    set_pin(FPGA_NCONFIG, ...);
    // ... timing-sensitive sequence ...
}
```

### Problem
- No locking mechanism for state transition
- Pin changes not atomic
- Another thread could call `fpga_set_state()` mid-transition
- Timing assumptions invalidated by concurrent access

### Affected Transitions
- Online → Offline
- Offline → Online
- Reset sequences

### Impact
- FPGA could be partially configured
- Pin state inconsistent with expected state
- Timing violations in FPGA startup

---

## Issue 3: USB Disconnect During FPGA Access (Medium)

**Location**: `src/debug_spi.c`

```c
// TODO: don't run this on r0.2+ boards?
```

### Problem
- Uncertain behavior on r0.2 hardware revision
- No explicit handling for USB disconnect during SPI transfer
- Timing-sensitive operation could leave hardware in bad state

### Impact
- Hardware revision-specific bugs
- Potential FPGA corruption if SPI interrupted mid-transaction

---

## Recommended Fixes

### Fix 1: Mutex for FPGA State (Priority: IMMEDIATE)

```c
static mutex_t fpga_state_lock;

void fpga_set_state(int state) {
    mutex_acquire(&fpga_state_lock);
    
    // Safe to modify FPGA state now
    set_pin(FPGA_RESET, ...);
    set_pin(FPGA_NCONFIG, ...);
    delay_ms(...);
    
    mutex_release(&fpga_state_lock);
}

bool fpga_take_over(void) {
    if (!mutex_try_acquire(&fpga_state_lock)) {
        return false;  // Already in use
    }
    
    // Safe handoff
    // ...
    
    mutex_release(&fpga_state_lock);
    return true;
}
```

### Fix 2: Atomic State Flag

```c
typedef enum {
    FPGA_STATE_OFFLINE,
    FPGA_STATE_ONLINE,
    FPGA_STATE_TRANSITIONING  // Prevents concurrent access
} fpga_state_t;

static volatile fpga_state_t current_state = FPGA_STATE_OFFLINE;

void fpga_set_state(int state) {
    // Atomically check-and-set
    if (!atomic_compare_and_set(&current_state, 
                                FPGA_STATE_TRANSITIONING)) {
        return false;  // Already transitioning
    }
    
    // Perform transition
    // ...
    
    // Update final state
    atomic_set(&current_state, state);
}
```

### Fix 3: Hardware Revision Clarification

```c
// Mark r0.2 as unsupported or document specific behavior
#if BOARD_REV == 2
    #error "Debug SPI not supported on r0.2 boards"
#endif
```

---

## Testing Requirements

### Concurrent Access Test
```c
// Spawn multiple threads with TAKE_OVER requests
// Verify only one succeeds
// Check FPGA pin state is consistent
```

### State Transition Stress Test
```c
// Rapid online/offline cycling
// Verify no pin state corruption
// Check timing tolerances not violated
```

### USB Disconnect During Access
```c
// Disconnect USB during active SPI transfer
// Verify firmware recovery (no hang or reset loop)
// Check FPGA remains in known state
```

---

## Files Requiring Changes

- `src/vendor.c` — Add mutex to `fpga_take_over()`
- `src/fpga.c` — Protect `fpga_set_state()` with lock
- `src/fpga.h` — Export state lock interface
- `src/debug_spi.c` — Clarify r0.2 behavior
- Platform headers — Define mutex implementation per MCU

---

## Priority Assessment

| Issue | Severity | Fix Complexity | Risk |
|-------|----------|----------------|------|
| FPGA Handoff Race | HIGH | Low | HIGH |
| State Transition Race | HIGH | Medium | HIGH |
| USB Disconnect Behavior | MEDIUM | Low | MEDIUM |

## Summary

Race conditions in FPGA state management are real and could cause silent failures (undefined FPGA state). Fixes are straightforward (add mutex/atomic ops) but critical for reliability.

**Recommendation**: Implement mutex protection before any other FPGA state management changes.
