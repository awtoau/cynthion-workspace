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

### Fix 1: Sticky JTAG Programming Mode (Priority: IMMEDIATE)

Adopt a simple hard rule:
- If Apollo is in JTAG programming mode, no other mode transitions are allowed.
- Serial/debug and takeover-related commands must fail fast with explicit status.
- Only an explicit emergency reset path may preempt JTAG mode.

This directly addresses interruption during programming without requiring a large multi-mode scheduler.

```c
typedef enum {
    MODE_APOLLO_HOLD,
    MODE_JTAG_PROGRAM,
    MODE_EMERGENCY_RESET
} apollo_mode_t;

static volatile apollo_mode_t apollo_mode = MODE_APOLLO_HOLD;

bool mode_change_allowed(apollo_mode_t requested) {
    if (apollo_mode == MODE_JTAG_PROGRAM && requested != MODE_EMERGENCY_RESET) {
        return false;
    }
    return true;
}
```

### Fix 2: Gate Vendor Requests by Mode

Apply mode checks in `vendor.c` request handlers:
- JTAG family requests require `MODE_JTAG_PROGRAM`.
- Takeover/offline/policy-flip requests are rejected while JTAG mode is active.
- Serial forwarding handlers pause or return `BUSY` during JTAG mode.

```c
if (apollo_mode == MODE_JTAG_PROGRAM && request_is_non_jtag(req)) {
    return vendor_error_response(VENDOR_RESPONSE_BUSY);
}
```

### Fix 3: Deterministic Emergency Reset Path

Emergency reset behavior must be explicit and deterministic:
- cancel active JTAG session
- clear ownership/lock state
- return to HOLD mode

```c
void emergency_reset_mode_override(void) {
    cancel_jtag_session();
    apollo_mode = MODE_APOLLO_HOLD;
}
```

### Legacy Alternatives (Superseded for now)

The previous mutex and multi-state-transition alternatives remain valid engineering options, but the current recommended path is the simpler sticky JTAG policy because it directly enforces non-interruption with lower implementation complexity.

#### Previous mutex-oriented sketch

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

#### Previous atomic state-flag sketch

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

#### Hardware revision clarification (still recommended)

```c
// Mark r0.2 as unsupported or document specific behavior
#if BOARD_REV == 2
    #error "Debug SPI not supported on r0.2 boards"
#endif
```

---

## Testing Requirements

### JTAG Non-Interruption Test

```c
// Enter JTAG programming mode
// Attempt takeover/policy/serial-mode commands in parallel
// Verify all non-JTAG commands return BUSY or INVALID_MODE
// Verify programming sequence completes without handoff interruption
```

### Emergency Reset Override Test

```c
// Enter JTAG programming mode
// Trigger explicit emergency reset command
// Verify JTAG session is canceled and mode returns to HOLD
```

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

- `src/vendor.c` — Enforce sticky JTAG-mode policy checks
- `src/fpga.c` — Ensure offline/reset paths honor mode policy
- `src/fpga_adv.c` — Block takeover transitions while JTAG mode is active
- `src/console.c` — Pause or reject serial forwarding while JTAG mode is active
- `src/fpga.h` — Export mode/ownership helpers if needed
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
