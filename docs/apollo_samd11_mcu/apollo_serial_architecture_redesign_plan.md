# Implementation Plan - Cynthion Serial Architecture Redesign

**Status**: Ready for Phase 1  

## Phase Overview

| Phase | Task | Goal | Blockers |
|-------|------|------|----------|
| **1** | Toolchain Build | Get complete build working (firmware + gateware) | Resolve toolchain issues |
| **2** | Apollo Firmware Fixes | DFU buffers, races, dual CDC ports | See Apollo Fixes section |
| **3** | JTAG Always Available | Separate JTAG from moondancer control | Requires Phase 2 |
| **4** | FPGA Stub Test | Minimal gateware, test serial path | Requires Phase 1 |
| **5** | moondancer Diagnostic | Serial-only output, heartbeat protocol | Requires Phase 4 |
| **6** | Serial Loop Test | AUX/TARGET/CONTROL port verification | Requires Phase 5 |
| **7** | Full moondancer | Facedancer with serial-only architecture | Requires Phase 6 |
| **8** | UART Watchdog | PA03/PA04 INT-based timeout detection | Requires Phase 7 |

## Phase 1: Toolchain Build

**Objective**: Get complete clean build of Apollo firmware, moondancer firmware, and gateware.

**Status snapshot (2026-07-22)**:
- Tracker issue: [#55](https://github.com/awtoau/cynthion-workspace/issues/55)

### P1.1: Environment Setup
- [ ] Run prerequisite validation and environment setup via the canonical install flow
  (`./scripts/install.py prereqs`, then setup as needed)

**Commands to run**:
```bash
./scripts/install.py prereqs
```

### P1.2: Apollo Firmware Build
- [ ] Clean build of Apollo firmware for Cynthion d11
- [ ] Identify any compiler warnings/errors
- [ ] Document build time, binary size
- [ ] Verify firmware produces .elf/.bin artifacts

Use the canonical workspace entry points (`cyn apollo build` or
`./scripts/install.py setup`) rather than direct per-repo build commands here.

### P1.3: moondancer Firmware Build
- [ ] Clean build of moondancer RISC-V firmware
- [ ] Identify any compiler warnings/errors
- [ ] Document build time, binary size
- [ ] Verify firmware produces .elf artifact

**Command**:
```bash
cd awto-cynthion/firmware/moondancer && cargo build --release
```

### P1.4: Gateware Build (Analyzer)
- [ ] Build analyzer gateware bitstream
- [ ] Identify synthesis/place&route warnings
- [ ] Document build time
- [ ] Verify .bit artifact produced

**Command**:
```bash
cd awto-cynthion/cynthion/python && python -m cynthion.gateware.analyzer.top --dry-run
```

### P1.5: Gateware Build (Facedancer)
- [ ] Build facedancer gateware bitstream
- [ ] Identify synthesis issues
- [ ] Document build time
- [ ] Verify .bit artifact produced

**Command**:
```bash
cd awto-cynthion/cynthion/python && python -m cynthion.gateware.facedancer.top --dry-run
```

### P1.6: Document Build Issues
- [ ] List all warnings/errors encountered
- [ ] Categorize: toolchain version, dependency, code issue
- [ ] Note any timeouts or resource exhaustion
- [ ] Propose fixes for Phase 2+

## Phase 2: Apollo Firmware Fixes

**Objective**: Address DFU buffers, potential races, dual CDC serial ports.

### P2.1: DFU Memory Buffer Optimization
- [ ] Review current JTAG buffer sizing (256 bytes fixed)
- [ ] Review DFU descriptor (4096 bytes configured)
- [ ] Implement DFU memory pool for dynamic allocation
- [ ] Benchmark: stack usage, timing, throughput

**Files**:
- `awto-apollo/firmware/src/jtag.c` — JTAG buffers
- `awto-apollo/firmware/src/mcu/samd11/usb_descriptors.c` — DFU descriptor

### P2.2: Race Condition Analysis
- [ ] Review round-robin scheduler in main.c
- [ ] Identify potential races between tud_task(), fpga_adv_task()
- [ ] Identify potential races between UART operations and USB control
- [ ] Add synchronization primitives where needed (atomic flags, spinlocks)

**Files**:
- `awto-apollo/firmware/src/main.c` — scheduler loop
- `awto-apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` — FPGA_ADV task
- `awto-apollo/firmware/src/vendor.c` — USB control request handlers

### P2.3: Dual CDC Interface (JTAG + Console)
- [ ] Add second CDC interface to USB descriptors
- [ ] Map /dev/ttyACM0 → JTAG (debug console)
- [ ] Map /dev/ttyACM1 → Apollo console (moondancer relay)
- [ ] Route UART input/output to correct CDC interface

Current status:
- Primary tracking is in [#55](https://github.com/awtoau/cynthion-workspace/issues/55).
- Earlier dual-CDC feasibility work is historical context from
  [#56](https://github.com/awtoau/cynthion-workspace/issues/56) (closed as deferred).
- If promoting the near-working second-port path, open a new child issue under #55.

**Files**:
- `awto-apollo/firmware/src/mcu/samd11/usb_descriptors.c` — dual CDC setup
- `awto-apollo/firmware/src/console.c` — console output routing

## Phase 3-8: Implementation

See ../apollo_samd11_mcu/apollo_moondancer_uart_watchdog_design.md for details on:
- Phase 3: JTAG always available (separate from moondancer)
- Phase 4: FPGA stub gateware test
- Phase 5: moondancer diagnostic serial build
- Phase 6: Serial loop verification
- Phase 7: Full moondancer integration
- Phase 8: UART watchdog on PA03/PA04

## Success Criteria

Phase 1 complete when:
- [ ] Apollo firmware builds without errors
- [ ] moondancer firmware builds without errors
- [ ] Analyzer gateware elaborates without errors
- [ ] Facedancer gateware elaborates without errors
- [ ] No Python 2 code paths used
- [ ] Build times documented

