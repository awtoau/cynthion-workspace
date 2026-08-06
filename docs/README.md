# The documentation, and what belongs in it

**Index of every file under `docs/`, and the rule for what goes here rather than
into an issue.**

## The rule

> **`docs/` is for what stays true. An issue is for what is not done yet.**

Apply it as a test on each sentence:

| ask | answer | where |
|---|---|---|
| Will this still be true in six months? | yes | `docs/` |
| Does it describe work not yet done? | yes | issue |
| Is it a measurement of how the thing behaves? | yes | `docs/` |
| Is it a measurement of how far along we are? | yes | issue |
| Would a new reader need it to understand the code? | yes | `docs/` |
| Does it contain "we should", "next", "TODO"? | yes | issue |

The failure this prevents is a document that ages into a lie. A file describing
"the current state of the HyperRAM work" is wrong the week after it is written
and nobody notices, because it still reads plausibly. A file describing *how the
HyperBus data phase behaves* is either right or falsifiable, and a measurement
with its conditions attached stays useful even when the design moves.

The inverse failure is real too: a finding that lives only in an issue is lost
when the issue closes. **A closed issue is not documentation.** When an issue
produces a durable fact -- a measurement, a constraint, a reason a thing cannot
be done -- that fact moves here and the issue links to it.

### Practical splits

* A **bug** is an issue. The **behaviour that made it possible** is a doc.
* A **plan** is an issue. The **constraint the plan is working around** is a doc.
* A **benchmark run** is an issue comment. The **number, with its conditions and
  what it was measured against**, is a doc.
* **Status of anything** is an issue, always. `docs/` has no "current state"
  sections and no checkboxes.

### Where a decision goes

**Once a choice is made it stops being a decision and becomes architecture.**
[`soc-architecture.md`](soc-architecture.md) says what the SoC is made of and
where each piece came from — a table, no arguments.
[`decisions.md`](decisions.md) holds only what is still open and what would
settle it. Moving a row from the second to the first is the point at which the
reasoning gets dropped; git keeps it.
[`upstream-boundary.md`](upstream-boundary.md) holds the policy on Great Scott
Gadgets code, separately, because that is policy rather than measurement.

### Subjects with one canonical file

Where a subject has proved able to sprawl across several files, one file owns it and
the others link. **[`chips/cynone-sideband.md`](chips/cynone-sideband.md)** owns the FPGA_ADV wire — the
electrical rules, the protocol, the port request, and the list of things already
settled. Do not restate any of it elsewhere; a second account is how the contradictions
in its §13 got there in the first place.

## Index

### Top level

* [`ci_cd_workflows.md`](ci_cd_workflows.md) — or manually:
* [`codex-agent.md`](codex-agent.md) — Handing work to Codex — here
* [`decisions.md`](decisions.md) — Decisions still open, and what would settle them
* [`gateware-architecture-plan.md`](gateware-architecture-plan.md) — Making the test gateware reusable by the CPU build
* [`git.md`](git.md) — Git & Submodules Reference
* [`github_actions.md`](github_actions.md) — (no title)
* [`gsg-scenarios.md`](gsg-scenarios.md) — What upstream Cynthion officially does, and what implements it
* [`hardware.md`](hardware.md) — Cynthion r1.4 hardware — the index
* [`install.md`](install.md) — Cynthion Workspace Installation & Build Guide
* [`linux-on-cynthion.md`](linux-on-cynthion.md) — Linux on Cynthion — what it would take, and whether it is worth it
* [`rtic.md`](rtic.md) — RTIC and the four alternatives: what the workload needs, and what each costs
* [`soc-architecture.md`](soc-architecture.md) — **what the SoC is made of, and where each piece came from**
* [`soc-clocking.md`](soc-clocking.md) — The RISC-V SoC clock ceiling: it was never place-and-route
* [`riscv-core-build.md`](riscv-core-build.md) — Building the RISC-V core — and why you should, more often than you think
* [`chips/cynone-sideband.md`](chips/cynone-sideband.md) — **The FPGA_ADV sideband link** — the wire, the protocol, the port request, and what is settled
* [`soc-size-review.md`](soc-size-review.md) — Where the SoC's size actually is — per-peripheral area, per-module `.text`, and why two of the obvious ways to measure both give wrong answers
* [`toolchain-simplification.md`](toolchain-simplification.md) — Can we drop `luna_soc`?
* [`toolchain-versions.md`](toolchain-versions.md) — Python toolchain: what is pinned, what is stale, what is a trap
* [`upstream-boundary.md`](upstream-boundary.md) — What we take from upstream, and what we have replaced
* [`upstream-ci-workflow.md`](upstream-ci-workflow.md) — Running upstream CI locally, before submitting anything
* [`upstream-patch-plan.md`](upstream-patch-plan.md) — Upstream patch plan: Great Scott Gadgets
* [`upstream-patch-process.md`](upstream-patch-process.md) — Upstream patch validation process
* [`upstream-yosys-edif-notes.md`](upstream-yosys-edif-notes.md) — yosys -> Lattice Diamond EDIF handoff: three blockers
* [`upstream-yosys-write-edif-hierarchy.md`](upstream-yosys-write-edif-hierarchy.md) — yosys `write_edif` emits `$scopeinfo` cells as instances of undeclared cells
* [`upstreamable-patches.md`](upstreamable-patches.md) — Patches worth sending upstream
* [`usb-host-options.md`](usb-host-options.md) — USB Host Mode on Cynthion at 480 Mbps — Proposal

### Per-chip notes

* [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) — FUSB302B ×2 — the USB-C PD controllers
* [`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md) — ECP5 `LFE5U-12F` — the FPGA, and it is a 25F die
* [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) — NS16550A — the console UART, in fabric
* [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md) — PAC1954-1 — the power monitor
* [`chips/samd11-apollo.md`](chips/samd11-apollo.md) — ATSAMD11D14A — the Apollo debug microcontroller
* [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) — USB3343 ×3 — the ULPI PHYs
* [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md) — VexiiRiscv — the SoC's CPU
* [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md) — Winbond W25Q32 — the configuration flash
* [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) — Winbond W956A8MBYA6I — the HyperRAM

### Moondancer / the SoC


### The ECP5, beyond the part itself

* [`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md) — the part: a 25F die, its primitives, and the DDR path
* [`chips/ecp5/bram-budget.md`](chips/ecp5/bram-budget.md) — block RAM: who actually uses it
* [`chips/ecp5/config-engine-probe.md`](chips/ecp5/config-engine-probe.md) — probing the configuration engine on live silicon
* [`chips/ecp5/flashing.md`](chips/ecp5/flashing.md) — the gateware load process
* [`chips/ecp5/flash-partitioning.md`](chips/ecp5/flash-partitioning.md) — partitioned configuration flash on r1.4
* [`chips/ecp5/qspi-boot-time.md`](chips/ecp5/qspi-boot-time.md) — does quad-SPI speed up configuration from flash?
* [`chips/ecp5/reconfigure-initn-gap.md`](chips/ecp5/reconfigure-initn-gap.md) — `trigger_fpga_reconfiguration()` leaves INITN held low
* [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md) — RV32 Equivalence and Variation Report (2026-07-22)
* [`usb-performance.md`](usb-performance.md) — LUNA USB gateware: measured performance
* [`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md) — Where the ECP5 findings live

### Apollo, SAMD11 firmware

* [`apollo_samd11_mcu/apollo-configure-speed-investigation.md`](apollo_samd11_mcu/apollo-configure-speed-investigation.md) — `apollo configure`: speed investigation and results
* [`apollo_samd11_mcu/apollo_change_process.md`](apollo_samd11_mcu/apollo_change_process.md) — Apollo Change Tracking Process
* [`apollo_samd11_mcu/apollo_code_review.md`](apollo_samd11_mcu/apollo_code_review.md) — Apollo Firmware Code Review
* [`apollo_samd11_mcu/apollo_dfu_buffer_analysis.md`](apollo_samd11_mcu/apollo_dfu_buffer_analysis.md) — Apollo DFU Buffer Issues
* [`apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md`](apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md) — Implementation Plan - Cynthion Serial Architecture Redesign
* [`apollo_samd11_mcu/apollo_serial_interface_and_mode_exclusivity_design.md`](apollo_samd11_mcu/apollo_serial_interface_and_mode_exclusivity_design.md) — Apollo Serial Interface and Mode Exclusivity Design
* [`apollo_samd11_mcu/apollo_to_fpga_spi_design.md`](apollo_samd11_mcu/apollo_to_fpga_spi_design.md) — Apollo-to-FPGA SPI Design
* [`apollo_samd11_mcu/apollo_uart_spi_design_conflict_analysis.md`](apollo_samd11_mcu/apollo_uart_spi_design_conflict_analysis.md) — /etc/udev/rules.d/54-cynthion.rules
* [`apollo_samd11_mcu/apollo_watchdog_architecture.md`](apollo_samd11_mcu/apollo_watchdog_architecture.md) — (no title)
* [`apollo_samd11_mcu/cynthion_architecture_scan_2026_05_22.md`](apollo_samd11_mcu/cynthion_architecture_scan_2026_05_22.md) — Cynthion Architecture Scan Report
The FPGA_ADV wire is documented at [`chips/cynone-sideband.md`](chips/cynone-sideband.md), not here.

### Patch sets

* [`patchset/0001-improved-build-system-logging-fail-fast-parallelization.md`](patchset/0001-improved-build-system-logging-fail-fast-parallelization.md) — (no title)
* [`patchset/0002-parallel-build-execution-setup-and-build-threading.md`](patchset/0002-parallel-build-execution-setup-and-build-threading.md) — Sequential (original, ~33 minutes)
* [`patchset/patchset_overview.md`](patchset/patchset_overview.md) — (no title)

