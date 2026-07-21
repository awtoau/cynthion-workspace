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
- `scripts/43_record_ecp5_metrics.py`: append one resource/timing datapoint to CSV history.
- `scripts/44_generate_ecp5_report.py`: generate Markdown trend report with graphs.
- `scripts/45_scan_logs.py`: scans generated logs for warning/error signatures.
- `scripts/dev.py`: one-command runner for steps 40 + 41 + 42 + 43 + 44 + 45.
- `code/cynthion_rv64_min.dts`: starter Linux device-tree skeleton.
- `sim/tb_vexiiriscv_smoke.v`: reusable simulation testbench.
- `sim/vexii_ecp5_wrap.v`: minimal wrapper top for standalone PnR/timing checks.
- `metrics/ecp5_usage_history.csv`: history ledger for LUT/FF/BRAM/Fmax.
- `metrics/reports/ecp5_usage_report.md`: generated trend dashboard.
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
python3 riscv-64/scripts/43_record_ecp5_metrics.py --tag baseline --notes "wrapper core-only"
python3 riscv-64/scripts/44_generate_ecp5_report.py
python3 riscv-64/scripts/45_scan_logs.py
python3 riscv-64/scripts/dev.py --tag with-uart --notes "added uart block"
```

Then execute tasks in `BRINGUP_PLAN.md` phase-by-phase.

## Key Output Logs

- `riscv-64/out/sim/vexii_smoke_run.log`
- `riscv-64/out/sim/vexii_postsynth_run.log`
- `riscv-64/out/sim/vexii_ecp5_nextpnr.log`
- `riscv-64/out/sim/vexii_ecp5_timing_summary.txt`

## ECP5 Growth Monitoring

Use this to track FPGA growth as features are added to the processor.
The generated report includes BRAM in both DP16KD blocks and KiB capacity.

1. Run timing flow: `python3 riscv-64/scripts/42_run_vexii_nextpnr_timing.py`
2. Record one datapoint: `python3 riscv-64/scripts/43_record_ecp5_metrics.py --tag <change-name> --notes "what changed"`
3. Refresh trend report: `python3 riscv-64/scripts/44_generate_ecp5_report.py`

Or run all three with one command:

- `python3 riscv-64/scripts/dev.py --tag <change-name> --notes "what changed"`

Default `dev.py` flow:

1. RTL smoke sim (40)
2. Post-synth smoke sim (41)
3. nextpnr timing flow (42)
4. metrics append (43)
5. report generation (44)
6. log scan for warnings/errors (45)

Useful flags:

- `--skip-rtl-sim`
- `--skip-postsynth-sim`
- `--skip-timing`
- `--skip-log-scan`
- `--fail-on-warnings`

Open report:

- `riscv-64/metrics/reports/ecp5_usage_report.md`
