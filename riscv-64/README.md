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
- `code/cynthion_rv64_min.dts`: starter Linux device-tree skeleton.
- `work/`: local source checkouts and generated artifacts.
- `out/`: generated reports and extracted baseline files.

## Quick start

Run from repository root:

```bash
python3 riscv-64/scripts/00_check_env.py
python3 riscv-64/scripts/10_prepare_workdirs.py
python3 riscv-64/scripts/20_capture_soc_baseline.py
python3 riscv-64/scripts/30_qemu_linux_smoke.py --kernel /path/to/Image --initrd /path/to/initramfs.cpio.gz
```

Then execute tasks in `BRINGUP_PLAN.md` phase-by-phase.
