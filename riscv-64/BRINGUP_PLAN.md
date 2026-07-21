# Cynthion RV64 Linux Bring-Up Plan

## Objective

Bring up a minimal RV64 Linux system on Cynthion with the highest probability of first boot on ECP5 LFE5U-12F.

Primary core choice: VexiiRiscv.
Secondary fallback: Rocket.

## Constraints We Must Respect

- FPGA is LFE5U-12F (tight area and timing margin).
- On-board SPI flash is 4 MiB.
- On-board HyperRAM is 8 MiB.
- Existing gateware path is strongly USB device oriented.
- First milestone should avoid USB host-mode storage on AUX.

## Bring-Up Strategy

- Validate Linux image, DTB, and bootargs in QEMU before FPGA attempts.
- Keep the SoC minimal.
- Keep peripherals minimal for first boot.
- Boot kernel and DTB from flash.
- Use HyperRAM as system RAM.
- Use USB network gadget + host NFS root for userspace.

## Non-Goals for First Boot

- No USB mass-storage host stack on AUX.
- No full moondancer/facedancer feature parity.
- No performance tuning beyond basic boot stability.

## Phases

## Phase 0: Baseline and Tooling Lock

Deliverables:

- Verified host tool availability report.
- Captured baseline memory/peripheral map from current SoC code.
- Local checkout of preferred RV64 core from mirror.
- QEMU tool availability confirmed.

Tasks:

1. Run `python3 riscv-64/scripts/00_check_env.py`.
2. Run `python3 riscv-64/scripts/10_prepare_workdirs.py`.
3. Run `python3 riscv-64/scripts/20_capture_soc_baseline.py`.
4. Install missing tools reported by phase outputs.
5. Confirm `qemu-system-riscv64` is available.

Exit criteria:

- `riscv-64/out/env_report.json` exists.
- `riscv-64/out/soc_baseline.json` exists.
- `riscv-64/work/vexiiriscv` exists.

## Phase 0.5: QEMU Linux Configuration Gate

Deliverables:

- One repeatable QEMU boot command for RV64 Linux.
- Captured UART boot log proving the Linux configuration is sane.

Tasks:

1. Build or obtain minimal RV64 kernel and initramfs/rootfs suitable for QEMU `virt`.
2. Compile a QEMU-specific DTB (or use QEMU `virt` defaults if the kernel supports it).
3. Run `python3 riscv-64/scripts/30_qemu_linux_smoke.py --kernel <Image> [--initrd <initramfs>] [--dtb <qemu.dtb>]`.
4. Validate:
   - kernel starts,
   - console works,
   - expected rootfs handoff behavior occurs.
5. Freeze known-good kernel cmdline in plan notes.

Exit criteria:

- `riscv-64/out/qemu_boot.log` contains successful early boot output.
- Linux command line and config choices are recorded before FPGA integration.

## Phase 1: Minimal RV64 SoC Architecture

Deliverables:

- New SoC top design for RV64 experiment branch.
- Clearly defined memory map and reset vector.
- Single UART console + timer + interrupt path operational in simulation or hardware smoke test.

Tasks:

1. Fork current SoC top into a new RV64-specific top module.
2. Replace RV32 VexRiscv CPU instantiation with RV64 core integration.
3. Keep only required blocks:
   - CPU, bus, interrupt controller, timer, UART, SPI flash mmap, HyperRAM.
4. Drop non-essential USB endpoints and optional peripherals for first build.
5. Preserve deterministic reset and boot address behavior.

Exit criteria:

- Synthesis completes for RV64 top.
- Post-PnR timing and utilization report is generated.
- UART output confirms first instruction execution.

## Phase 1.5: Pre-Hardware Core Validation (Completed)

This phase validates the standalone core before Cynthion SoC integration.

Deliverables:

- Instruction-driven RTL smoke test result.
- Post-synthesis netlist smoke test result.
- ECP5 nextpnr timing/place report for a wrapper top.

Tasks:

1. Run `python3 riscv-64/scripts/40_run_vexii_rtl_smoke.py`.
2. Run `python3 riscv-64/scripts/41_run_vexii_postsynth_smoke.py`.
3. Run `python3 riscv-64/scripts/42_run_vexii_nextpnr_timing.py`.

Verified outputs:

- `riscv-64/out/sim/vexii_smoke_run.log`
- `riscv-64/out/sim/vexii_postsynth_run.log`
- `riscv-64/out/sim/vexii_ecp5_nextpnr.log`
- `riscv-64/out/sim/vexii_ecp5_timing_summary.txt`

Notes:

- The timing flow uses `VexiiRiscvWrap` (`riscv-64/sim/vexii_ecp5_wrap.v`) so IO count fits the ECP5-12F package during standalone core evaluation.

## Phase 2: Boot Chain

Deliverables:

- Flashable boot artifact set.
- Known-good kernel + DTB placement strategy.

Tasks:

1. Confirm reset vector and ROM/flash mapping.
2. Build and package:
   - first-stage boot path,
   - Linux kernel image,
   - DTB from `riscv-64/code/cynthion_rv64_min.dts`.
3. Define flash layout with offsets and size guardrails.
4. Validate boot logs over UART.

Exit criteria:

- Board consistently reaches Linux early boot logs.
- No flash overlap or image layout ambiguity.

## Phase 3: Root Filesystem via USB Network + NFS

Deliverables:

- USB networking to host established.
- NFS root mount from host succeeds.

Tasks:

1. Enable Linux USB gadget network support in kernel config.
2. Configure fixed host/target addresses for deterministic setup.
3. Export host rootfs over NFS.
4. Set kernel command line for NFS root boot.
5. Validate full userspace startup from host-served rootfs.

Exit criteria:

- Linux reaches userspace shell from NFS root.
- Reboot repeats successfully without manual patching.

## Phase 4: Stabilization and Measurement

Deliverables:

- Repeatable bring-up playbook.
- Resource/timing summary and risk list.

Tasks:

1. Capture build reproducibility steps and exact tool versions.
2. Record LUT/BRAM/timing slack at each milestone.
3. Document failure modes and recovery actions.
4. Decide next step:
   - optimize VexiiRiscv config, or
   - evaluate Rocket fallback.

Exit criteria:

- Another developer can reproduce first boot from clean checkout.
- Decision memo for next milestone exists.

## Risk Register

1. Area overflow on 12F when enabling Linux-required core features.
2. Timing closure failures in HyperRAM or bus paths.
3. Boot-chain complexity in 4 MiB flash budget.
4. USB gadget networking integration overhead.
5. Toolchain mismatch across Yosys/nextpnr/ecppack or core generators.

## Unexpected Issues Encountered

1. Incomplete submodule tree caused sbt project load failure.
Cause:
`No project 'idslplugin' ... Valid project IDs: spinalhdl`
Fix:
Run `git submodule update --init --recursive` in `riscv-64/work/vexiiriscv`.

2. Trellis support database was missing when building nextpnr dependencies.
Cause:
`pytrellis` was built, but `devices.json` and family DB files were absent in install tree.
Fix:
Populate from mirrored `prjtrellis-db` into the local trellis install database path.

3. `yosys-config` binary is absent on this host.
Cause:
The packaged toolchain provides `yosys` but not `yosys-config`.
Fix:
Use `/usr/share/yosys/simcells.v` directly for post-synth simulation.

4. nextpnr CLI mismatch against expected options.
Cause:
This `nextpnr-ecp5` build does not accept `--pcf` in the way some scripts assume.
Fix:
Use wrapper top and unconstrained run mode without `--pcf` for early timing/place experiments.

5. Raw core top exceeded available package IO for ECP5-12F.
Cause:
Standalone core exposes many memory bus/debug ports as top-level IO.
Fix:
Use a wrapper top that internalizes bus handshakes and exposes only minimal IO (`clk`, `reset`) for PnR timing checks.

## Immediate Next Actions

1. Run the three setup scripts in `riscv-64/scripts`.
2. Validate Linux image config in QEMU with `scripts/30_qemu_linux_smoke.py`.
3. Create RV64 experiment branch in this workspace.
4. Draft the new minimal SoC top module and compile once.
5. Capture utilization/timing report into `riscv-64/out`.

## Source Anchors Used for This Plan

- `docs/riscv_alternatives.md`
- `cynthion_control.py`
- `/mnt/2tb/git/awtoau/awto-cynthion/cynthion/python/src/gateware/facedancer/top.py`
- `/mnt/2tb/git/awtoau/awto-cynthion/cynthion/python/src/commands/cynthion_build.py`
- `/mnt/2tb/git/awtoau/awto-cynthion/cynthion/python/src/commands/cynthion_flash.py`
