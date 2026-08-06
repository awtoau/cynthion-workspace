# Cynthion RISC-V Bring-Up Plan

## Status: 64-bit parked, RV32 is the live target

This plan was written for RV64 Linux. That is no longer the active plan. 64-bit
was evaluated and parked: it does not fit the LFE5U-12F with any useful
advantage over RV32, so the sweep tooling now builds `--xlen 32` only
(`scripts/riscv_matrix_config.py`). The directory was renamed `riscv-64` →
`riscv` to match.

The RV64 phases below are retained as the record of what was planned and what
was reached before the decision. Where a phase is 64-bit-specific it is marked.
Nothing in Phases 2–4 was executed.

## Objective

Bring up a minimal RISC-V system on Cynthion (ECP5 LFE5U-12F) with the highest
probability of first boot.

Primary core: VexiiRiscv (`repos/vexiiriscv`, submodule). Fallback: Rocket.

## Constraints

| Constraint | Value |
|---|---|
| FPGA | LFE5U-12F, CABGA256 |
| Block RAM | 56 × DP16KD = 112 KiB, shared with firmware and USB buffers |
| SPI flash | 4 MiB |
| HyperRAM | 8 MiB |

Existing gateware is USB-device oriented. First milestone avoids USB host-mode
storage on AUX.

## Strategy

- Validate Linux image, DTB, and bootargs in QEMU before FPGA attempts.
- Keep the SoC and its peripherals minimal.
- Boot kernel and DTB from flash; HyperRAM as system RAM.
- USB network gadget + host NFS root for userspace.

Not in scope for first boot: USB mass-storage host stack on AUX,
moondancer/facedancer feature parity, performance tuning.

## Phase 0: Baseline and tooling lock

1. `python3 linux-on-cynthion/scripts/00_check_env.py`
2. `python3 linux-on-cynthion/scripts/10_prepare_workdirs.py`
3. `python3 linux-on-cynthion/scripts/20_capture_soc_baseline.py`
4. Install any tools reported missing.
5. Confirm `qemu-system-riscv64` is available.

Exit: `riscv/out/env_report.json` and `riscv/out/soc_baseline.json` exist;
`repos/vexiiriscv` is checked out with `git submodule update --init
--recursive` (its `ext/` submodules carry SpinalHDL).

## Phase 0.5: QEMU Linux configuration gate (64-bit; parked)

1. Build or obtain a minimal RV64 kernel and initramfs for QEMU `virt`.
2. Compile a QEMU-specific DTB, or use `virt` defaults if the kernel supports it.
3. `python3 linux-on-cynthion/scripts/30_qemu_linux_smoke.py --kernel <Image> [--initrd <initramfs>] [--dtb <qemu.dtb>]`
4. Confirm kernel starts, console works, rootfs handoff occurs.
5. Freeze the known-good kernel cmdline here.

Exit: `riscv/out/qemu_boot.log` shows successful early boot; cmdline and config
recorded before any FPGA integration.

## Phase 1: Minimal SoC architecture

1. Fork the current SoC top into a new RISC-V-specific top module.
2. Keep only CPU, bus, interrupt controller, timer, UART, SPI flash mmap,
   HyperRAM. Drop non-essential USB endpoints and optional peripherals.
3. Preserve deterministic reset and boot address behaviour.

Exit: synthesis completes; post-PnR timing and utilisation report generated;
UART output confirms first instruction execution.

## Phase 1.5: Pre-hardware core validation (reached)

Standalone core validation before SoC integration.

1. `python3 linux-on-cynthion/scripts/40_run_vexii_rtl_smoke.py`
2. `python3 linux-on-cynthion/scripts/41_run_vexii_postsynth_smoke.py`
3. `python3 linux-on-cynthion/scripts/42_run_vexii_nextpnr_timing.py`

Outputs (regenerated into gitignored `riscv/out/`):

- `riscv/out/sim/vexii_smoke_run.log`
- `riscv/out/sim/vexii_postsynth_run.log`
- `riscv/out/sim/vexii_ecp5_nextpnr.log`
- `riscv/out/sim/vexii_ecp5_timing_summary.txt`

**The timing numbers this phase produced are withdrawn.** Script 42 routes at
`--freq 25.0` with `--timing-allow-fail`, and the wrapper it uses
(`riscv/sim/vexii_ecp5_wrap.v`) ties core outputs to unconnected wires and
feeds the instruction bus a constant nop, so synthesis prunes the output side.
`scripts/riscv_core_wrapper.py` replaces that wrapper with one that attaches
block RAM to both buses with a one-cycle-latency ready handshake. The
place-and-route flow itself is still valid; only the numbers are not.

## Phase 2: Boot chain (not started)

1. Confirm reset vector and ROM/flash mapping.
2. Build and package first-stage boot path, kernel image, and DTB from
   `linux-on-cynthion/code/cynthion_rv64_min.dts`.
3. Define flash layout with offsets and size guardrails.
4. Validate boot logs over UART.

Exit: board consistently reaches Linux early boot logs; no flash overlap.

Note: the DTS in `linux-on-cynthion/code/` is the RV64 skeleton and has not been revised
for the RV32 decision.

## Phase 3: Root filesystem via USB network + NFS (not started)

1. Enable Linux USB gadget network support in kernel config.
2. Configure fixed host/target addresses.
3. Export host rootfs over NFS; set the kernel cmdline for NFS root.

Exit: Linux reaches a userspace shell from NFS root and repeats across reboots
without manual patching.

## Phase 4: Stabilisation and measurement (not started)

1. Capture build reproducibility steps and exact tool versions.
2. Record LUT/BRAM/timing slack at each milestone.
3. Document failure modes and recovery actions.
4. Decide: optimise the VexiiRiscv config, or evaluate the Rocket fallback.

Exit: another developer reproduces first boot from a clean checkout.

## Risks

1. Area overflow on the 12F once Linux-required core features are enabled.
2. Timing closure on HyperRAM or bus paths.
3. Boot-chain complexity within the 4 MiB flash budget.
4. USB gadget networking integration overhead.
5. Toolchain mismatch across Yosys/nextpnr/ecppack or the core generators.

## Issues encountered, and their fixes

| Issue | Cause | Fix |
|---|---|---|
| sbt project load failed: `No project 'idslplugin' ... Valid project IDs: spinalhdl` | Incomplete submodule tree | `git submodule update --init --recursive` in `repos/vexiiriscv` |
| nextpnr build missing Trellis database | `pytrellis` built, but `devices.json` and family DB files absent from the install tree | Populate from the mirrored `prjtrellis-db` into the local trellis install DB path |
| `yosys-config` absent | Packaged toolchain ships `yosys` but not `yosys-config` | Use `/usr/share/yosys/simcells.v` directly for post-synth simulation |
| `--pcf` rejected by `nextpnr-ecp5` | This build does not accept the option as some scripts assume | Run unconstrained without `--pcf` for early timing experiments |
| Raw core top exceeded package IO | Standalone core exposes ~29 top-level bus/debug ports; CABGA256 has no pins for them | Wrapper top exposing only `clk`/`reset`. The original wrapper pruned the design — see Phase 1.5 |

## Next actions

1. Re-run the sweep with the replacement tooling: `scripts/riscv_matrix_config.py`,
   `scripts/riscv_core_wrapper.py`, `scripts/riscv_sweep_report.py`. No sweep has
   been run since the old data was discarded.
2. Draft the minimal RV32 SoC top and compile once.
3. Capture utilisation and timing into `riscv/out/`.

The Linux-oriented phases above are **RV64-era leftovers and are not the
current target.** `riscv_alternatives.md` parked RV64 on 2026-07-28 and names
running Linux as the one motivation that would have justified it -- explicitly
not being pursued. Moondancer ships the `cynthion+jtag` VexRiscv variant, which
is bare-metal with no supervisor mode and no MMU.

So `scripts/riscv_matrix_config.py` building with supervisor mode off is
correct, and any phase here that assumes S-mode, an MMU or a Linux boot applies
only if that decision is revisited.

## Source anchors

- `docs/moondancer/riscv_alternatives.md`
- `docs/moondancer/riscv_state_of_play.md`
- `debris/code/legacy_cli/cynthion_control.py` (retired since this plan was written)
- `repos/cynthion/cynthion/python/src/gateware/facedancer/top.py`
- `repos/cynthion/cynthion/python/src/commands/cynthion_build.py`
- `repos/cynthion/cynthion/python/src/commands/cynthion_flash.py`
