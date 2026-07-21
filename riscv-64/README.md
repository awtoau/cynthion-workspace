# RV64 Bring-Up Workspace

This folder contains the focused assets for the Cynthion RV64 Linux bring-up experiment.

## Goal

Boot a minimal RV64 Linux userspace on Cynthion (ECP5 LFE5U-12F) using a stripped-down system architecture.

## What is in here

- `BRINGUP_PLAN.md`: execution plan, milestones, and risks.
- `scripts/00_check_env.py`: checks required host tools and mirror presence.
- `scripts/10_prepare_workdirs.py`: prepares local working trees from the mirrored core.
- `scripts/20_capture_soc_baseline.py`: captures current SoC memory/peripheral constants for reference.
- `scripts/30_qemu_linux_smoke.py`: runs a QEMU RV64 Linux smoke boot and captures console logs.
- `scripts/40_run_vexii_rtl_smoke.py`: instruction-driven RTL smoke test for standalone VexiiRiscv.
- `scripts/41_run_vexii_postsynth_smoke.py`: post-synthesis netlist smoke test.
- `scripts/42_run_vexii_nextpnr_timing.py`: ECP5-12F nextpnr timing/place flow on wrapper top.
- `code/cynthion_rv64_min.dts`: starter Linux device-tree skeleton.
- `sim/tb_vexiiriscv_smoke.v`: reusable simulation testbench.
- `sim/vexii_ecp5_wrap.v`: minimal wrapper top for standalone PnR/timing checks.
- `work/`: local source checkouts and generated artifacts.
- `out/`: generated reports and extracted baseline files.

## Quick start

Run from repository root:

```bash
python3 riscv-64/scripts/00_check_env.py
python3 riscv-64/scripts/10_prepare_workdirs.py
python3 riscv-64/scripts/20_capture_soc_baseline.py
python3 riscv-64/scripts/30_qemu_linux_smoke.py --kernel /path/to/Image --initrd /path/to/initramfs.cpio.gz
python3 riscv-64/scripts/40_run_vexii_rtl_smoke.py
python3 riscv-64/scripts/41_run_vexii_postsynth_smoke.py
python3 riscv-64/scripts/42_run_vexii_nextpnr_timing.py
```

Then execute tasks in `BRINGUP_PLAN.md` phase-by-phase.

## Key Output Logs

- `riscv-64/out/sim/vexii_smoke_run.log`
- `riscv-64/out/sim/vexii_postsynth_run.log`
- `riscv-64/out/sim/vexii_ecp5_nextpnr.log`
- `riscv-64/out/sim/vexii_ecp5_timing_summary.txt`
