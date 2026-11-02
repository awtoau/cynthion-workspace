# Apollo serial interface and mode exclusivity

JTAG programming must not be interrupted by serial/debug or USB-takeover transitions. Implemented in `repos/apollo/firmware/src/apollo_mode.{c,h}` (`MODE_APOLLO_HOLD`, `MODE_JTAG_PROGRAM`, `MODE_JTAG_PROGRAMMING`; emergency reset is vendor request `0xec`).

## Issues

- Tracker [#55](https://github.com/awtoau/cynthion-workspace/issues/55); prerequisites [#54](https://github.com/awtoau/cynthion-workspace/issues/54) (FPGA state / USB handoff sync), [#53](https://github.com/awtoau/cynthion-workspace/issues/53) (DFU); [#56](https://github.com/awtoau/cynthion-workspace/issues/56) dual-CDC (closed, deferred); [#22](https://github.com/awtoau/cynthion-workspace/issues/22) UART forwarding evidence; [#19](https://github.com/awtoau/cynthion-workspace/issues/19), [#20](https://github.com/awtoau/cynthion-workspace/issues/20) JTAG/GDB; [#15](https://github.com/awtoau/cynthion-workspace/issues/15) supervisor model; [#65](https://github.com/awtoau/cynthion-workspace/issues/65) pin exclusivity.
- Command surface: [apollo_change_process.md](apollo_change_process.md).

## Problem

Control-plane transitions (hold, FPGA takeover, offline/reset) overlap, and serial consumers run while JTAG/programming is active; competing paths drive the same resources. Result: interrupted JTAG sessions and flaky serial capture ([#54](https://github.com/awtoau/cynthion-workspace/issues/54), [#22](https://github.com/awtoau/cynthion-workspace/issues/22)).

## Mode model

| mode | meaning |
|---|---|
| `MODE_APOLLO_HOLD` | default; serial/debug may run; takeover is explicit policy |
| `MODE_JTAG_PROGRAM` | exclusive for JTAG/program/configure; mode changes, serial forwarding and takeover blocked |
| emergency reset | only preemption of JTAG mode; cancels ownership, returns to HOLD |

Invariants:

1. Exactly one mode owner.
2. In JTAG mode no change except emergency reset.
3. Non-JTAG control operations return `BUSY` / `INVALID_MODE` while JTAG is active.
4. USB takeover is explicit policy, never a side effect of reset helpers.
5. Emergency reset is the only preemption.

| command class | HOLD | JTAG | emergency |
|---|---|---|---|
| `flash-program` / `configure` / `svf` | must switch | yes | no |
| `jtag-scan` / `jtag-reg` | optional | yes | no |
| UART/CDC capture/probe | yes | no | no |
| `allow_fpga_takeover_usb` | explicit only | no | no |
| emergency reset | yes | yes | yes |

Transitions: enter = acquire ownership lock (reject if held; block serial/takeover handlers); exit = explicit stop only, release lock, return to HOLD; emergency reset = from any mode, cancel JTAG session first, force HOLD; takeover policy changes rejected while JTAG active.

## Decision record (accepted 2026-07-23, resolves item 2 of [#65](https://github.com/awtoau/cynthion-workspace/issues/65); d11 only)

Four SAMD11 pins are contended by three subsystems with no hardware arbitration; each steals the pins via `gpio_set_pin_function()`:

| pin | JTAG-over-USB (bit-bang) | JTAG-over-SPI (SERCOM0) | UART console (SERCOM2) |
|---|---|---|---|
| PA10 | TDO | TDO / MISO (PAD2) | - |
| PA11 | TMS | - | RX (PAD3) |
| PA14 | TDI | TDI / MOSI (PAD0) | TX (PAD0) |
| PA15 | TCK | TCK / SCK (PAD1) | - |

- Pins cannot be split: relocating UART to PA08/PA09 is not viable on d11 (PA08 = `FPGA_PROGRAM`, PA09 = `PHY_RESET` + `FPGA_ADV` on `EIC_EXTINT7`). d21 and samd11_xplained have spare PA08/PA09 and are out of scope.
- Decision (sticky mutual exclusion):
  1. JTAG owns the pins exclusively while `MODE_JTAG_PROGRAM` is active; any non-JTAG handler that would call `gpio_set_pin_function()` on PA10/PA11/PA14/PA15 (`uart_initialize()`, SERCOM2 console, debug SPI) returns `BUSY` and leaves the pinmux alone.
  2. JTAG programming is uninterruptible: runs to completion or emergency reset; the owner cannot yield mid-sequence (a half-programmed FPGA is the failure prevented).
  3. The lock guards the pinmux, not only command dispatch: `console.c` `tud_cdc_line_coding_cb` / `tud_cdc_line_state_cb` (host line-coding / DTR changes) must consult it before `uart_initialize()`.
  4. Emergency reset is the sole preemption: cancels ownership, restores safe pins, returns to HOLD.
  5. Default HOLD is UART/console-owner; first JTAG entry repinmuxes and takes the lock; no implicit hand-back (lazy UART init on the next CDC event).
- Enforcement: one global mode control; gate `uart_initialize()` and the three `console.c` CDC callbacks on `mode != MODE_JTAG_PROGRAM`; gate JTAG-SPI (`SPI_FPGA_JTAG`) pinmux on the same lock (a JTAG-family owner, allowed inside JTAG mode); programming vendor requests (`flash-program`, `configure`, `svf`, `reconfigure`) acquire, the matching stop releases.
- Consequences: UART console and JTAG are one-at-a-time on d11 by enforced invariant; a host opening the Apollo CDC port during a flash gets an uninitialised UART or `BUSY` until JTAG exits; no change on d21 / xplained.

## Second virtual serial port

- d11 has one CDC (`CFG_TUD_CDC 1`, `ITF_NUM_CDC = 0` in `mcu/samd11/usb_descriptors.c`) bridging to SERCOM2 on PA11/PA14.
- Redundant as a second UART bridge: d11 has exactly one UART-capable pin pair, and it is JTAG's.
- Still possible as a pinless side-channel that stays up while JTAG owns the pins: status/notification port (`BUSY`, progress, mode transitions), out-of-band control (emergency reset, mode query), framed GDB-server / diagnostics transport ([#20](https://github.com/awtoau/cynthion-workspace/issues/20)).
- Cost is USB endpoints and ~1-2 KB of the 4 KiB RAM: a memory-budget question, not a pin question (why #56 was deferred). If re-opened under #55, scope it as that side-channel, not a second UART.

## Validation

1. JTAG lock: enter JTAG mode, run a long program/configure, issue takeover/serial/reset-helper commands in parallel; blocked commands return deterministic errors.
2. Serial: CDC/serial debug during JTAG mode is rejected or paused.
3. Emergency reset during JTAG mode cancels ownership and returns to HOLD.
4. Attach logs and a pass/fail matrix to #54; update #55.

Order: minimal JTAG lock (#54) -> re-run UART forwarding validation (#22) -> dual-CDC only as a new child of #55.
