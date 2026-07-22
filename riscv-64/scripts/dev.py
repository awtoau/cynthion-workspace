#!/usr/bin/env python3
"""Run full RV64 validation + ECP5 growth-monitor pipeline in one command.

Default sequence:
1) RTL smoke simulation (40)
2) post-synthesis smoke simulation (41)
3) timing flow (42)
4) record metrics row (43)
5) generate trend report (44)
6) scan logs for warnings/errors (45)
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "riscv-64" / "scripts"
OUT_DIR = REPO_ROOT / "riscv-64" / "out" / "sim"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default=None,
        help="Datapoint tag (default: auto tag monitor-YYYYMMDD-HHMMSS).",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional notes recorded with this datapoint.",
    )
    parser.add_argument(
        "--target-mhz",
        type=float,
        default=25.0,
        help="Timing target for step 43.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="nextpnr thread count for step 42 (0 uses nextpnr default).",
    )
    parser.add_argument(
        "--skip-timing",
        action="store_true",
        help="Skip step 42 and use existing timing/log artifacts.",
    )
    parser.add_argument(
        "--skip-rtl-sim",
        action="store_true",
        help="Skip step 40 RTL smoke simulation.",
    )
    parser.add_argument(
        "--skip-postsynth-sim",
        action="store_true",
        help="Skip step 41 post-synthesis smoke simulation.",
    )
    parser.add_argument(
        "--skip-log-scan",
        action="store_true",
        help="Skip step 45 log scanning.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Make step 45 fail when warnings are found.",
    )
    return parser.parse_args()


def run_cmd(cmd: list[str], log_file: pathlib.Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        log.write(f"\n[{stamp}] CMD: {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=log, stderr=log)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")


def main() -> int:
    args = parse_args()
    tag = args.tag or dt.datetime.now().strftime("monitor-%Y%m%d-%H%M%S")

    run_log = OUT_DIR / "ecp5_monitor_run.log"
    run_log.parent.mkdir(parents=True, exist_ok=True)
    run_log.write_text("", encoding="utf-8")

    print(f"Run log: {run_log}")
    print(f"Tag: {tag}")

    try:
        if not args.skip_rtl_sim:
            print("Step 1/6: running RTL smoke simulation (40)...")
            run_cmd([sys.executable, str(SCRIPTS_DIR / "40_run_vexii_rtl_smoke.py")], run_log)
        else:
            print("Step 1/6: skipped RTL smoke simulation (--skip-rtl-sim)")

        if not args.skip_postsynth_sim:
            print("Step 2/6: running post-synth smoke simulation (41)...")
            run_cmd([sys.executable, str(SCRIPTS_DIR / "41_run_vexii_postsynth_smoke.py")], run_log)
        else:
            print("Step 2/6: skipped post-synth smoke simulation (--skip-postsynth-sim)")

        if not args.skip_timing:
            print("Step 3/6: running timing flow (42)...")
            timing_cmd = [sys.executable, str(SCRIPTS_DIR / "42_run_vexii_nextpnr_timing.py")]
            if args.threads > 0:
                timing_cmd.extend(["--threads", str(args.threads)])
            run_cmd(timing_cmd, run_log)
        else:
            print("Step 3/6: skipped timing flow (--skip-timing)")

        print("Step 4/6: recording metrics (43)...")
        run_cmd(
            [
                sys.executable,
                str(SCRIPTS_DIR / "43_record_ecp5_metrics.py"),
                "--tag",
                tag,
                "--notes",
                args.notes,
                "--target-mhz",
                str(args.target_mhz),
            ],
            run_log,
        )

        print("Step 5/6: generating report (44)...")
        run_cmd([sys.executable, str(SCRIPTS_DIR / "44_generate_ecp5_report.py")], run_log)

        if not args.skip_log_scan:
            print("Step 6/6: scanning logs for warnings/errors (45)...")
            scan_cmd = [sys.executable, str(SCRIPTS_DIR / "45_scan_logs.py")]
            if args.fail_on_warnings:
                scan_cmd.append("--fail-on-warnings")
            run_cmd(scan_cmd, run_log)
        else:
            print("Step 6/6: skipped log scanning (--skip-log-scan)")

    except Exception as exc:
        print(f"ERROR: {exc}")
        print(f"See log: {run_log}")
        return 2

    print("Done.")
    print(f"CSV: {REPO_ROOT / 'riscv-64' / 'metrics' / 'ecp5_usage_history.csv'}")
    print(f"Report: {REPO_ROOT / 'riscv-64' / 'metrics' / 'reports' / 'ecp5_usage_report.md'}")
    print(f"Log scan summary: {REPO_ROOT / 'riscv-64' / 'out' / 'sim' / 'ecp5_log_scan_summary.txt'}")
    print(f"Run log: {run_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
