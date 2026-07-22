# Apollo Serial Interface and Mode Exclusivity Design

## Purpose

This document consolidates the current Apollo serial/control interface, links the related issue set, and defines a minimal, enforceable exclusivity policy so JTAG programming cannot be interrupted by serial/debug or takeover transitions.

## Linked Issue Map

Primary tracker:
- [#55](https://github.com/awtoau/cynthion-workspace/issues/55) - Apollo serial architecture redesign tracker

Reliability prerequisites:
- [#54](https://github.com/awtoau/cynthion-workspace/issues/54) - synchronize FPGA state transitions and USB handoff
- [#53](https://github.com/awtoau/cynthion-workspace/issues/53) - DFU buffer and detach robustness

Second UART / dual-CDC work:
- [#56](https://github.com/awtoau/cynthion-workspace/issues/56) - dual-CDC feasibility (closed, deferred)
- [#22](https://github.com/awtoau/cynthion-workspace/issues/22) - UART forwarding verification and runtime evidence

JTAG debug architecture:
- [#19](https://github.com/awtoau/cynthion-workspace/issues/19) - route VexRiscv JTAG via ECP5 JTAGG
- [#20](https://github.com/awtoau/cynthion-workspace/issues/20) - Apollo Python GDB server over serial path

Broader architecture context:
- [#15](https://github.com/awtoau/cynthion-workspace/issues/15) - Apollo supervisor model and multi-channel control direction

## Current Apollo Interface Surface

Reference source: `docs/apollo_samd11_mcu/apollo_change_process.md`.

Host-side command groups:
- Device and introspection: `info`, `jtag-scan`, `flash-info`
- Flash/programming: `flash-erase`, `flash-program`, `flash-fast`, `flash-read`
- FPGA control: `configure`, `reconfigure`, `force-offline`
- Low-level buses: `spi`, `spi-inv`, `spi-reg`, `jtag-spi`, `jtag-reg`, `svf`
- Utility: `leds`

Device-side request groups (in Apollo firmware `firmware/src/vendor.c`):
- JTAG requests: start/stop, scan, run clock, goto state, get state, bulk scan
- Programming/control: reconfigure, force offline, allow FPGA USB takeover
- SPI paths: debug SPI send/read, flash SPI send, flash-line arbitration

## Problem Statement

Observed in issues [#54](https://github.com/awtoau/cynthion-workspace/issues/54) and [#22](https://github.com/awtoau/cynthion-workspace/issues/22):
- control-plane transitions (Apollo hold, FPGA takeover, offline/reset) can overlap
- runtime serial consumers can run while JTAG/programming operations are active
- the same physical/functional resources are being driven by competing paths

Net effect:
- JTAG programming/debug sessions are vulnerable to interruption
- serial verification can become flaky due to mode transitions during capture windows

## Design Goal

Prevent interruption of JTAG programming operations by making JTAG mode sticky and exclusive until explicit exit or explicit emergency reset.

## Proposed Mode Model

Define a small control-plane state model:

- `MODE_APOLLO_HOLD`
  - default mode
  - serial/debug flows may run
  - takeover remains explicit policy

- `MODE_JTAG_PROGRAM`
  - exclusive mode for JTAG/program/configure sequences
  - mode changes are blocked while active
  - serial forwarding and takeover transitions are blocked

- `MODE_EMERGENCY_RESET`
  - explicit override path
  - cancels active JTAG ownership and returns to `MODE_APOLLO_HOLD`

## Hard Invariants

1. Exactly one mode owner at a time.
2. While in `MODE_JTAG_PROGRAM`, no mode change is allowed except emergency reset.
3. All non-JTAG control operations return explicit `BUSY` or `INVALID_MODE` while JTAG mode is active.
4. USB takeover is explicit policy, never an implicit side effect of reset helpers.
5. Emergency reset is the only preemption path for JTAG mode.

## Command Policy Matrix

| Command Class | Allowed in Hold | Allowed in JTAG Program | Allowed in Emergency Reset | Notes |
|---|---|---|---|---|
| `flash-program` / `configure` / `svf` | No (must switch) | Yes | No | Exclusive JTAG owner required |
| `jtag-scan` / `jtag-reg` | Optional | Yes | No | JTAG command family |
| UART/CDC capture/probe | Yes | No | No | Block during active programming |
| `allow_fpga_takeover_usb` | Explicit only | No | No | Block while JTAG mode is active |
| Emergency reset command | Yes | Yes | Yes | Only legal preemption path |

## Implementation Sketch

In Apollo firmware control path:

- add one global mode state (`HOLD`, `JTAG_PROGRAM`, `EMERGENCY_RESET`)
- gate vendor request handlers with a simple policy check
- return explicit error code when blocked (`BUSY`, `INVALID_MODE`)
- ensure emergency reset clears JTAG ownership and returns to HOLD deterministically

Suggested control struct:

```c
typedef enum {
    MODE_APOLLO_HOLD,
    MODE_JTAG_PROGRAM,
  MODE_EMERGENCY_RESET
} apollo_mode_t;

typedef struct {
    volatile apollo_mode_t mode;
  volatile bool jtag_owner_active;
} apollo_mode_ctrl_t;
```

## Transition Rules (Minimal)

1. Enter `MODE_JTAG_PROGRAM`:
- acquire ownership lock
- reject if another owner exists
- block serial/takeover handlers while active

2. Exit `MODE_JTAG_PROGRAM`:
- explicit stop command only
- release ownership lock and return to `MODE_APOLLO_HOLD`

3. Emergency reset:
- can run from any mode
- if JTAG mode active, cancel JTAG session first
- force return to `MODE_APOLLO_HOLD`

4. Takeover policy changes:
- rejected while JTAG mode active
- allowed again only after JTAG exit/reset

## Validation Plan

1. JTAG lock test:
- enter JTAG mode and issue long program/configure sequence
- in parallel, issue takeover/serial/reset-helper commands
- verify blocked commands return deterministic errors

2. Serial interruption test:
- while JTAG mode active, attempt CDC/serial debug operations
- verify they are rejected or paused

3. Emergency reset override test:
- trigger emergency reset during JTAG mode
- verify JTAG ownership is canceled and device returns to HOLD

4. Evidence requirements:
- attach logs and pass/fail matrix to [#54](https://github.com/awtoau/cynthion-workspace/issues/54)
- update [#55](https://github.com/awtoau/cynthion-workspace/issues/55) with milestone result

## Execution Order

1. Land minimal JTAG lock policy for [#54](https://github.com/awtoau/cynthion-workspace/issues/54).
2. Re-run UART forwarding validation in [#22](https://github.com/awtoau/cynthion-workspace/issues/22).
3. Re-open dual-CDC implementation as a new child issue under [#55](https://github.com/awtoau/cynthion-workspace/issues/55), using [#56](https://github.com/awtoau/cynthion-workspace/issues/56) as historical context.