# Apollo Summary (Moved from full.md)

This document holds Apollo-specific material that was removed from `docs/full.md` to keep the workspace-wide summary focused.

## Scope

This summary covers:
- Apollo watchdog architecture context
- Apollo/UART/SPI design-conflict context
- Apollo-focused roadmap links

## Watchdog Architecture Context

Apollo supervises moondancer to improve recovery behavior:
1. moondancer emits heartbeat/status signals
2. Apollo monitors heartbeat and state transitions
3. Apollo initiates recovery/reset behavior on heartbeat loss

Detailed design and implementation phases:
- [apollo_watchdog_architecture.md](apollo_watchdog_architecture.md)
- [apollo_moondancer_uart_watchdog_design.md](apollo_moondancer_uart_watchdog_design.md)
- [apollo_moondancer_uart_watchdog_workstream.md](apollo_moondancer_uart_watchdog_workstream.md)

## UART/SPI Conflict Context

Apollo debug and control-path design involves trade-offs between:
- UART-based watchdog/control signaling
- SPI-based FPGA debug path
- Pinmux and JTAG overlap constraints

Detailed analysis and recommendations:
- [apollo_uart_spi_design_conflict_analysis.md](apollo_uart_spi_design_conflict_analysis.md)
- [cynthion_architecture_scan_2026_05_22.md](cynthion_architecture_scan_2026_05_22.md)
- [apollo_code_review.md](apollo_code_review.md)

## Apollo Roadmap and Change Tracking

- [apollo_serial_architecture_redesign_plan.md](apollo_serial_architecture_redesign_plan.md)
- [apollo_change_process.md](apollo_change_process.md)
- [apollo_dfu_buffer_analysis.md](apollo_dfu_buffer_analysis.md)
- [apollo_race_conditions.md](apollo_race_conditions.md)

## Related Workspace Docs

- [../full.md](../full.md)
- [../implementation_roadmap.md](../implementation_roadmap.md)
- [../hardware_architecture.md](../hardware_architecture.md)
