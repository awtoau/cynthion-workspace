# Apollo Summary

This document holds Apollo-specific material that was moved out of the workspace-wide overview and into topic-focused docs.

## Scope

This summary covers:
- Apollo watchdog architecture context
- Apollo-to-FPGA SPI design context
- Apollo-focused roadmap links

## Watchdog Architecture Context

Apollo supervises moondancer to improve recovery behavior:
1. moondancer emits heartbeat/status signals
2. Apollo monitors heartbeat and state transitions
3. Apollo initiates recovery/reset behavior on heartbeat loss

Detailed design and implementation phases:
- [apollo_moondancer_uart_watchdog_design.md](apollo_moondancer_uart_watchdog_design.md) — canonical design and execution notes

## Apollo-to-FPGA SPI Design Context

Dedicated SPI design details:
- [apollo_to_fpga_spi_design.md](apollo_to_fpga_spi_design.md)
- [cynthion_architecture_scan_2026_05_22.md](cynthion_architecture_scan_2026_05_22.md)
- [apollo_code_review.md](apollo_code_review.md)

## Apollo Roadmap and Change Tracking

- [apollo_serial_architecture_redesign_plan.md](apollo_serial_architecture_redesign_plan.md)
- [apollo_change_process.md](apollo_change_process.md)
- [apollo_dfu_buffer_analysis.md](apollo_dfu_buffer_analysis.md)
- [apollo_race_conditions.md](apollo_race_conditions.md)

## Related Workspace Docs

- [../patchset/patchset_overview.md](../patchset/patchset_overview.md)
- [../build_system.md](../build_system.md)
- [../install.md](../install.md)
- [../implementation_roadmap.md](../implementation_roadmap.md)
- [../hardware_architecture.md](../hardware_architecture.md)
