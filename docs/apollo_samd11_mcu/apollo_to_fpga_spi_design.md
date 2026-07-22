# Apollo-to-FPGA SPI Design

**Status**: Design proposal  
**Related Issues**: [#15](https://github.com/awtoau/cynthion-workspace/issues/15), [#33](https://github.com/awtoau/cynthion-workspace/issues/33)

## Scope

This document defines the SPI-only design path between Apollo (ATSAMD11D14A)
and FPGA-facing debug/control surfaces. It intentionally excludes watchdog
control protocol design, which is documented in
[apollo_moondancer_uart_watchdog_design.md](apollo_moondancer_uart_watchdog_design.md).

## Problem

The existing Apollo board support contains partial support for a dedicated
FPGA debug SPI path on SERCOM2, but that path is not fully wired in firmware.
Clocking is configured while pinmux setup was left incomplete.

Consequence:
- debug/maintenance flows rely on existing JTAG/SPI paths only
- a cleaner Apollo-to-FPGA SPI transport is not currently available as a
  dedicated interface

## Hardware Context

Board-level signals relevant to this design:
- `PA08`
- `PA09`
- `PA10`

Current firmware context:
- `SERCOM0` is used for existing SPI/JTAG-related behavior
- `SERCOM2` has a partial debug SPI setup path in firmware

## Proposed SPI Topology

Use `SERCOM2` as the dedicated Apollo-to-FPGA SPI controller for debug/control.

Planned mapping:
- `PA08` -> `SERCOM2 PAD0` (MOSI)
- `PA09` -> `SERCOM2 PAD1` (SCK)
- `PA10` <- `SERCOM0 PAD2` (MISO shared with existing path)

Notes:
- Shared MISO and board-level routing must be validated against actual
  constraints before bring-up.
- If shared-MISO behavior is unstable, this design should be revised to a fully
  dedicated SPI routing.

## Firmware Design

Target files (Apollo firmware):
- `src/boards/cynthion_d11/spi.c`
- `src/boards/cynthion_d11/apollo_board.h`

Required changes:
1. Complete `SPI_FPGA_DEBUG` pinmux setup in `spi.c`.
2. Define/confirm `PA08`/`PA09` debug SPI mappings in board headers.
3. Gate feature with board capability macro (for example
   `_BOARD_HAS_DEBUG_SPI`) so unsupported revisions fail clearly.
4. Add concise runtime logging for SPI initialization failure modes.

## Verification Plan

1. Build-time checks
- Verify firmware compiles with debug SPI feature enabled.
- Verify unsupported boards fail with explicit messages.

2. Electrical/protocol checks
- Confirm clock and MOSI activity on expected pins.
- Confirm stable MISO reads across repeated transfers.

3. Functional checks
- Read/write a known debug register via SPI path.
- Validate no regression in existing JTAG/SPI behavior.

## Risks

Low:
- Uses existing MCU peripheral capability.

Medium:
- Shared-signal interactions with existing SPI/JTAG paths.

High:
- Hidden board-routing assumptions that invalidate the shared-MISO plan.

## Out of Scope

- Watchdog heartbeat protocol and timeout semantics.
- USB role-switching policy changes.
- moondancer supervisory behavior.

Those remain in
[apollo_moondancer_uart_watchdog_design.md](apollo_moondancer_uart_watchdog_design.md).
