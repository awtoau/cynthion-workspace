# Apollo Change Tracking Process

## Purpose
Use this process for Apollo firmware work related to USB mode control, CDC/UART behavior, and issue verification.

Primary design reference for mode arbitration and interface boundaries:
- `docs/apollo_samd11_mcu/apollo_serial_interface_and_mode_exclusivity_design.md`

Goals:
- Keep changes reproducible.
- Keep evidence attached to each change.
- Keep issue status aligned with runtime proof.

## Scope
- Apollo source tree: `/mnt/2tb/git/awtoau/awto-apollo`
- Workspace evidence and patch archive: this repo
- Primary issue tracking: GitHub issue comments (for example issue 22)

## Apollo Command Structure (Reference)

Host-side Apollo CLI top-level commands:

1. Device/introspection:
  - `info`
  - `jtag-scan`
  - `flash-info`
2. Flash programming:
  - `flash-erase`
  - `flash-program`
  - `flash-fast`
  - `flash-read`
3. FPGA config/control:
  - `configure`
  - `reconfigure`
  - `force-offline`
4. Low-level buses:
  - `spi`, `spi-inv`, `spi-reg`
  - `jtag-spi`, `jtag-reg`
  - `svf`
5. Utility:
  - `leds`

Device-side request dispatch lives in Apollo firmware `firmware/src/vendor.c` and routes:

1. JTAG requests:
  - start/stop
  - set/get buffers
  - scan / run clock / goto state / get state / bulk scan
2. Programming/control requests:
  - trigger reconfiguration
  - force FPGA offline
  - allow FPGA takeover USB
3. Debug/flash SPI requests:
  - debug SPI send/read
  - flash SPI send
  - take/release flash lines

## Critical Constraint: UART vs JTAG on cynthion_d11

On cynthion_d11, UART and JTAG share pins (PA14/PA11 overlap), so they are operationally mutually exclusive during programming operations.

Implications:

1. `apollo flash-program` and `apollo configure` require JTAG ownership of those pins.
2. Any host process opening CDC ACM ports (for example `apollod.py`) can trigger UART callbacks and interfere with JTAG transactions.
3. During flash/program/configure operations:
  - stop port consumers first (`apollod`, serial monitors, other `ttyACM*` readers)
  - use Apollo hold mode (`cyn reset --mode hold-apollo`)
  - then run `cyn riscv flash`

Typical symptom of contention:
- `usb.core.USBError: [Errno 32] Pipe error`
- ECP5/JTAG operation timeout during `apollo flash-program`

## Required Artifacts Per Change
For each meaningful firmware change set, collect all of the following:

1. Change note (what/why).
2. Build result.
3. Flash result.
4. Runtime test output (USB mode, tty mapping, capture/probe output).
5. Patch file exported to `patches/apollo/`.
6. Issue comment linking the artifacts.

## Step-by-Step Process

1. Record intent before editing
- Create a short note in the issue comment draft with:
  - hypothesis
  - target files
  - verification plan

2. Make firmware changes
- Edit files under Apollo repo, typically:
  - `firmware/src/console.c`
  - `firmware/src/main.c`

3. Build and flash
- Build:
  - `cd /mnt/2tb/git/awtoau/awto-apollo/firmware`
  - `make APOLLO_BOARD=cynthion`
- Flash:
  - `make APOLLO_BOARD=cynthion dfu`

4. Capture runtime evidence in this repo
- Save logs to `tmp/` with ISO-style date stamps.
- Minimum runtime checks:
  - `lsusb` mode stability polling (615c vs 615b)
  - tty discovery from sysfs
  - serial capture/probe output

5. Export patch files
- Preferred (commit-based patch):
  - `cd /mnt/2tb/git/awtoau/awto-apollo`
  - `git add <files>`
  - `git commit -m "apollo: <summary>"`
  - `mkdir -p /mnt/2tb/git/cynthion-workspace/patches/apollo`
  - `git format-patch -1 HEAD -o /mnt/2tb/git/cynthion-workspace/patches/apollo`
- If not ready to commit (WIP snapshot):
  - `git diff > /mnt/2tb/git/cynthion-workspace/patches/apollo/0000-wip-<topic>.diff`

6. Update issue with evidence
- Post one concise comment containing:
  - change summary
  - build/flash outcome
  - runtime outcome
  - links to artifact filenames
  - explicit pass/fail against close criteria

## Close Criteria (Verification Issues)
Do not close until all are true:

1. Control-plane is stable (expected USB mode remains stable for test window).
2. Data-plane behavior is demonstrated by runtime evidence.
3. Reproduction steps are documented.
4. Patch artifact is exported under `patches/apollo/`.
5. Final issue comment references all evidence files.

## Change Log Template
Append one entry per firmware change set.

```
## YYYY-MM-DDTHH:MM:SS+10:00 - <short title>

- Hypothesis:
- Files changed:
- Build result:
- Flash result:
- Runtime result:
- Artifacts:
  - tmp/<file1>
  - tmp/<file2>
  - patches/apollo/<patch-file>
- Issue update:
  - <issue URL>
- Status:
  - pass | fail | partial
```

## Current Work Notes (Issue 22)
Latest active Apollo files changed:

1. `/mnt/2tb/git/awtoau/awto-apollo/firmware/src/console.c`
2. `/mnt/2tb/git/awtoau/awto-apollo/firmware/src/main.c`

Patch export is required before final closure of issue 22.