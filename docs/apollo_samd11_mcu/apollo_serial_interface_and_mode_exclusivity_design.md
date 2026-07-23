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

---

## Decision Record: Arbitration Model (ADR)

**Status:** Accepted — 2026-07-23. Resolves item 2 of
[#65](https://github.com/awtoau/cynthion-workspace/issues/65).
**Scope:** Cynthion **d11** board (ATSAMD11D14A). The d21 / samd11_xplained
boards have a different pin budget and are out of scope for the hardware
constraint below (their arbitration can be softer).

### Context — the constraint is physical, not a policy choice

On d11, four SAMD11 pins are contended by three Apollo subsystems with **no
hardware arbitration** — each path steals the pins into its own peripheral via
`gpio_set_pin_function()` (see [[apollo-d11-pin-exclusivity]] and
[#65](https://github.com/awtoau/cynthion-workspace/issues/65)):

| Pin  | JTAG-over-USB (bit-bang GPIO) | JTAG-over-SPI (SERCOM0)   | UART console (SERCOM2) |
|------|-------------------------------|---------------------------|------------------------|
| PA10 | TDO                           | TDO / MISO (PAD2)         | —                      |
| PA11 | TMS                           | —                         | **RX** (PAD3)          |
| PA14 | TDI                           | TDI / MOSI (PAD0)         | **TX** (PAD0)          |
| PA15 | TCK                           | TCK / SCK (PAD1)          | —                      |

PA14 is the triple-overlap (TDI *and* SPI-MOSI *and* UART-TX). The pins **cannot
be split**: relocating UART to PA08/PA09 is not viable because on d11 those are
`FPGA_PROGRAM` (PA08) and `PHY_RESET` + `FPGA_ADV`→`EIC_EXTINT7` (PA09) — Apollo's
core supervisory lines. See the correction note added to
`apollo_moondancer_uart_watchdog_design.md`.

Because the resource is physically single-owner, arbitration is not a
convenience feature — it is the only thing standing between a JTAG programming
sequence and silent corruption when a UART/console consumer repinmuxes PA14
mid-flash.

### Decision

Adopt the **sticky mutual-exclusion state machine** already sketched in the
"Proposed Mode Model" above, with these d11-specific bindings and one
strengthening:

1. **JTAG owns the pins exclusively while `MODE_JTAG_PROGRAM` is active.** Entry
   into JTAG-program / configure / SVF acquires the pin-owner lock. While held,
   any handler that would call `gpio_set_pin_function()` on PA10/PA11/PA14/PA15
   for a non-JTAG peripheral (i.e. `uart_initialize()`, SERCOM2 console, debug
   SPI) returns `BUSY` and does **not** touch the pinmux.

2. **JTAG programming is uninterruptible.** Once a program/configure/SVF
   sequence has begun, it runs to completion or to explicit emergency reset.
   There is no "pause and lend the pins" path. The lock is released only by (a)
   the JTAG command family completing and issuing explicit stop, or (b)
   `MODE_EMERGENCY_RESET`. This is stricter than a plain mutex: even the *owner*
   cannot voluntarily yield mid-sequence, because a half-programmed FPGA is the
   failure we are preventing.

3. **The lock guards the pinmux, not just the command dispatcher.** The check
   lives at the point of `gpio_set_pin_function()` / `uart_initialize()`, so a
   TinyUSB CDC callback (line-coding / DTR change — see `console.c`
   `tud_cdc_line_coding_cb`, `tud_cdc_line_state_cb`) firing during a flash
   cannot lazily re-init the UART and steal PA14. Today those callbacks call
   `uart_initialize()` unconditionally; under this decision they must consult the
   lock first.

4. **Emergency reset is the sole preemption path** (unchanged from Hard
   Invariant 5). It cancels JTAG ownership, restores pins to a safe default, and
   returns to `MODE_APOLLO_HOLD`.

5. **Default HOLD is UART/console-owner.** In `MODE_APOLLO_HOLD` the pins may be
   held by the UART console bridge; the *first* JTAG entry request repinmuxes
   them to JTAG and takes the lock. There is no implicit hand-back — returning to
   HOLD re-enables lazy UART init on the next CDC event.

### Enforcement points (firmware)

- One global `apollo_mode_ctrl_t` (already in the Implementation Sketch).
- Gate `uart_initialize()` and the three `console.c` CDC callbacks on
  `mode != MODE_JTAG_PROGRAM`.
- Gate the JTAG-SPI (`SPI_FPGA_JTAG`) and any future debug-SPI pinmux on the
  same lock — SPI is a JTAG-family owner, so it is allowed *inside*
  `MODE_JTAG_PROGRAM` but blocked for a console consumer while JTAG holds it.
- Entry to programming vendor requests (`flash-program`, `configure`, `svf`,
  `reconfigure`) acquires the lock; the matching stop releases it.

### Consequences

- The UART console and JTAG are strictly one-at-a-time on d11 — this is now an
  enforced invariant instead of an undefined race.
- A host that opens the Apollo CDC port during a flash no longer corrupts the
  flash; it gets a UART that stays uninitialized (or a `BUSY`) until JTAG exits.
- No behavioural change on d21 / xplained, which are not pin-starved.

---

## Second virtual serial port (host side): redundant on d11

**Question:** does the sticky-exclusion decision make a *second* USB virtual
serial port (the deferred dual-CDC work, [#56](https://github.com/awtoau/cynthion-workspace/issues/56))
redundant, or can it still be used?

**Current reality.** d11 Apollo exposes a **single** CDC interface today
(`CFG_TUD_CDC 1`; `ITF_NUM_CDC = 0` in `mcu/samd11/usb_descriptors.c`). That one
CDC is a lazy USB↔UART bridge: `console.c` forwards host bytes to the SERCOM2
UART on **PA11/PA14** — the JTAG pins. There is no second port; "the second
virtual serial port" means the dual-CDC idea from #56.

**Decision — the second CDC is redundant *for its originally-intended purpose*
on d11, but not useless.**

- **Redundant as a second UART bridge.** The obvious use for a second CDC — a
  separate host serial channel bridged to a *second* physical UART — has no
  physical UART to bridge to. SAMD11 SERCOM/pin budget on d11 has exactly one
  UART-capable pin pair, and it is the JTAG-contended one. A second CDC cannot
  surface a second independent serial line to the FPGA. So the classic
  "console + second forwarded UART" motivation is dead on d11 (it was only ever
  viable on the boards with spare PA08/PA09).

- **Still usable as a non-UART control/status channel.** A second CDC endpoint
  does **not** have to be a UART bridge. Under the exclusion model it is actually
  attractive as a **pinless side-channel** that stays up while JTAG owns the
  pins:
  - a **status/notification port** the host can keep open during a flash to
    receive `BUSY` / progress / mode-transition events — precisely when the
    primary UART bridge is (correctly) muted by the lock;
  - a **control port** for issuing `emergency reset` / mode queries out-of-band,
    so the operator is never locked out while JTAG is uninterruptible;
  - a **structured GDB-server / diagnostics** transport (aligns with #20's
    "GDB over serial path") that carries framed messages rather than raw UART
    bytes and therefore needs no SERCOM pins at all.

  These consume USB endpoints and ~1–2 KB RAM on the 4 KB-SRAM SAMD11, so
  feasibility is a memory-budget question (the reason #56 was deferred), **not**
  a pin question.

**Recommendation.** Keep the second CDC *deferred* as a UART bridge (it can never
be one on d11), and if dual-CDC is re-opened under #55, re-scope it explicitly as
a **pinless control/status side-channel** that complements the uninterruptible
JTAG lock — the one CDC role the pin constraint does not foreclose. Record this
so #56's revival doesn't re-inherit the dead "second forwarded UART" framing.