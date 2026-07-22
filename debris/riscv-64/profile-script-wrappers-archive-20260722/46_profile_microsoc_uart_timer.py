#!/usr/bin/env python3
"""Profile MicroSoc (with built-in UART + CLINT timer) on ECP5.

This runs a dedicated feature profile flow and appends results to the same
resource history used by the core-only flow.

Flow:
1) Generate MicroSoc verilog via sbt runMain
2) Run yosys synth_ecp5
3) Run nextpnr-ecp5 timing
4) Record metrics via script 43
5) Regenerate report via script 44
6) Optionally scan logs via script 45
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time

from profile_shared import build_nextpnr_cmd, ensure_tool, run_logged, run_sbt_main

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "riscv-64" / "scripts"
WORK = ROOT / "riscv-64" / "work" / "vexiiriscv"
OUT = ROOT / "riscv-64" / "out" / "sim"

GENERATE_LOG = OUT / "microsoc_uart_timer_generate.log"
YOSYS_LOG = OUT / "microsoc_uart_timer_yosys.log"
NEXTPNR_LOG = OUT / "microsoc_uart_timer_nextpnr.log"
SUMMARY = OUT / "microsoc_uart_timer_timing_summary.txt"
JSON = OUT / "MicroSoc_uart_timer_ecp5.json"
TEXTCFG = OUT / "MicroSoc_uart_timer_ecp5_out.config"


def reset_run_outputs() -> None:
    for p in [
        GENERATE_LOG,
        YOSYS_LOG,
        NEXTPNR_LOG,
        SUMMARY,
        OUT / "microsoc_uart_timer_metrics.log",
    ]:
        if p.exists():
            p.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default="soc_x64_sv_rvm_rvc_rdtime_clint_uart",
        help="Datapoint tag for metrics history",
    )
    parser.add_argument(
        "--notes",
        default="MicroSoc x64 supervisor+rvm+rvc+rdtime with CLINT and UART",
        help="Datapoint notes",
    )
    parser.add_argument("--target-mhz", type=float, default=25.0, help="Timing target used for metrics pass/fail")
    parser.add_argument("--threads", type=int, default=0, help="nextpnr thread count (0 uses default)")
    parser.add_argument("--skip-generate", action="store_true", help="Skip sbt verilog generation")
    parser.add_argument("--skip-log-scan", action="store_true", help="Skip running log scanner (script 45)")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Fail if log scan sees warnings")
    return parser.parse_args()


def find_microsoc_verilog(start_ts: float) -> pathlib.Path:
    candidates = sorted(WORK.glob("*.v"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        if p.stat().st_mtime >= start_ts - 1 and "microsoc" in p.name.lower():
            return p

    for p in candidates:
        if p.name == "MicroSoc.v":
            return p

    raise FileNotFoundError(f"Could not find generated MicroSoc verilog in {WORK}")


def run_generation(skip_generate: bool) -> pathlib.Path:
    start_ts = time.time()

    if not skip_generate:
        # Keep this relatively close to RV64 Linux-oriented settings while disabling
        # debug tap to reduce top-level IO count for FPGA profiling.
        sbt_arg = (
            "runMain vexiiriscv.soc.micro.MicroSocGen "
            "--xlen 64 --with-supervisor --with-rvm --with-rvc --with-rdtime "
            "--jtag-tap false"
        )
        run_sbt_main(WORK, sbt_arg, GENERATE_LOG)

    return find_microsoc_verilog(start_ts)


def run_timing(rtl_path: pathlib.Path, threads: int) -> None:
    yosys = ensure_tool("yosys")
    nextpnr = ensure_tool("nextpnr-ecp5")

    ys_script = f"read_verilog {rtl_path}; synth_ecp5 -top MicroSoc -json {JSON}; stat"
    run_logged([yosys, "-q", "-l", str(YOSYS_LOG), "-p", ys_script], YOSYS_LOG, OUT)

    nextpnr_cmd = build_nextpnr_cmd(nextpnr, JSON, TEXTCFG, threads, freq_mhz=25.0)
    run_logged(nextpnr_cmd, NEXTPNR_LOG, OUT)

    log = NEXTPNR_LOG.read_text(encoding="utf-8", errors="replace")
    achieved = re.findall(r"Max frequency for clock '[^']+':\s*([0-9.]+) MHz", log)

    status_lines = []
    for line in log.splitlines():
        if "Info: Max frequency" in line or "Info: Critical path" in line:
            status_lines.append(line)

    lines = [
        "ECP5 nextpnr timing summary (MicroSoc UART+CLINT)",
        f"rtl={rtl_path}",
        f"json={JSON}",
        f"textcfg={TEXTCFG}",
        f"yosys_log={YOSYS_LOG}",
        f"nextpnr_log={NEXTPNR_LOG}",
    ]
    if achieved:
        lines.append("max_frequencies_mhz=" + ", ".join(achieved))
    if status_lines:
        lines.append("key_lines:")
        lines.extend(status_lines)

    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_metrics(tag: str, notes: str, target_mhz: float) -> None:
    run_logged(
        [
            sys.executable,
            str(SCRIPTS / "43_record_ecp5_metrics.py"),
            "--timing-summary",
            str(SUMMARY),
            "--nextpnr-log",
            str(NEXTPNR_LOG),
            "--tag",
            tag,
            "--notes",
            notes,
            "--target-mhz",
            str(target_mhz),
        ],
        OUT / "microsoc_uart_timer_metrics.log",
        ROOT,
    )

    run_logged(
        [sys.executable, str(SCRIPTS / "44_generate_ecp5_report.py")],
        OUT / "microsoc_uart_timer_metrics.log",
        ROOT,
    )


def run_scan(fail_on_warnings: bool) -> None:
    cmd = [sys.executable, str(SCRIPTS / "45_scan_logs.py")]
    if fail_on_warnings:
        cmd.append("--fail-on-warnings")
    run_logged(cmd, OUT / "microsoc_uart_timer_metrics.log", ROOT)


def main() -> int:
    args = parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    reset_run_outputs()

    try:
        rtl = run_generation(args.skip_generate)
        run_timing(rtl, args.threads)
        run_metrics(args.tag, args.notes, args.target_mhz)
        if not args.skip_log_scan:
            run_scan(args.fail_on_warnings)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print(f"See logs: {GENERATE_LOG}, {YOSYS_LOG}, {NEXTPNR_LOG}")
        return 2

    print("MicroSoc UART+CLINT profile complete")
    print(f"Timing summary: {SUMMARY}")
    print(f"CSV: {ROOT / 'riscv-64' / 'metrics' / 'ecp5_usage_history.csv'}")
    print(f"Report: {ROOT / 'riscv-64' / 'metrics' / 'reports' / 'ecp5_usage_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
