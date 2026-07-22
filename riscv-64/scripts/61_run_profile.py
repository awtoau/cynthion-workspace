#!/usr/bin/env python3
"""Unified config-driven RV64 profile runner.

Profile definitions are loaded from riscv-64/config/profile_matrix.json.
This replaces per-profile wrapper scripts while preserving reproducibility.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from profile_shared import (
    append_event,
    build_nextpnr_cmd,
    ensure_tool,
    run_dev_profile,
    run_logged,
    run_sbt_main,
    with_shared_pipeline_lock,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "riscv-64" / "scripts"
WORK = ROOT / "riscv-64" / "work" / "vexiiriscv"
OUT = ROOT / "riscv-64" / "out" / "sim"
CONFIG_DEFAULT = ROOT / "riscv-64" / "config" / "profile_matrix.json"


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    sbt_main: str
    sbt_args: list[str]
    tag: str
    notes: str
    top_module: str | None = None
    output_prefix: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Profile name from config")
    parser.add_argument("--config", type=pathlib.Path, default=CONFIG_DEFAULT, help="Profile config JSON")
    parser.add_argument("--tag", default=None, help="Override datapoint tag")
    parser.add_argument("--notes", default=None, help="Override datapoint notes")
    parser.add_argument("--target-mhz", type=float, default=25.0, help="Timing target")
    parser.add_argument("--threads", type=int, default=0, help="nextpnr thread count (0 uses default)")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Fail on scanner warnings")
    parser.add_argument("--skip-log-scan", action="store_true", help="Skip log scanner step")
    parser.add_argument(
        "--allow-log-scan-fail",
        action="store_true",
        help="Do not fail profile run if log scan exits non-zero.",
    )
    parser.add_argument("--skip-generate", action="store_true", help="Skip sbt generation for microsoc_direct profiles")
    parser.add_argument(
        "--run-suffix",
        default="",
        help="Optional suffix for per-run artifacts (useful for concurrent matrix jobs).",
    )
    return parser.parse_args()


def load_profiles(path: pathlib.Path) -> dict[str, Profile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, Profile] = {}
    for item in data.get("profiles", []):
        profile = Profile(
            name=item["name"],
            kind=item["kind"],
            sbt_main=item["sbt_main"],
            sbt_args=item.get("sbt_args", []),
            tag=item["tag"],
            notes=item["notes"],
            top_module=item.get("top_module"),
            output_prefix=item.get("output_prefix"),
        )
        by_name[profile.name] = profile
    return by_name


def sbt_arg_string(main: str, args: list[str]) -> str:
    return "runMain " + main + " " + " ".join(args)


def prepare_isolated_workspace(run_suffix: str, gen_log: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    token = run_suffix.strip() or f"pid{os.getpid()}"
    ws_root = OUT / "workspaces" / token
    iso_work = ws_root / "vexiiriscv"

    if ws_root.exists():
        shutil.rmtree(ws_root)
    ws_root.mkdir(parents=True, exist_ok=True)

    append_event(gen_log, f"ISOLATED_COPY src={WORK} dst={iso_work}")
    shutil.copytree(
        WORK,
        iso_work,
        symlinks=True,
        ignore=shutil.ignore_patterns("target", ".bloop", ".metals", ".idea", "__pycache__"),
    )
    return ws_root, iso_work


def find_generated_verilog(
    work_dir: pathlib.Path,
    start_ts: float,
    before: set[pathlib.Path],
    hint: str | None = None,
) -> pathlib.Path:
    candidates = sorted(work_dir.glob("*.v"), key=lambda p: p.stat().st_mtime, reverse=True)
    new_files = [p for p in candidates if p not in before]

    if hint:
        hinted_new = [p for p in new_files if hint.lower() in p.name.lower()]
        if hinted_new:
            return hinted_new[0]
    if new_files:
        return new_files[0]

    for p in candidates:
        if p.stat().st_mtime >= start_ts - 1 and (hint is None or hint.lower() in p.name.lower()):
            return p
    if hint:
        for p in candidates:
            if hint.lower() in p.name.lower():
                return p
    if not candidates:
        raise FileNotFoundError(f"No generated verilog files found in {work_dir}")
    return candidates[0]


def run_core_dev(
    profile: Profile,
    tag: str,
    notes: str,
    target_mhz: float,
    threads: int,
    fail_on_warnings: bool,
    skip_log_scan: bool,
    allow_log_scan_fail: bool,
    run_log: pathlib.Path,
) -> None:
    # run_dev_profile already serializes the shared core pipeline.
    run_sbt_main(WORK, sbt_arg_string(profile.sbt_main, profile.sbt_args), run_log)

    # When scan failures are non-fatal, run dev.py with scan skipped and handle scanner separately.
    effective_skip_scan = skip_log_scan or allow_log_scan_fail
    run_dev_profile(ROOT, tag, notes, target_mhz, threads, fail_on_warnings, effective_skip_scan, run_log)

    if not skip_log_scan and allow_log_scan_fail:
        scan_cmd = [sys.executable, str(SCRIPTS / "45_scan_logs.py")]
        if fail_on_warnings:
            scan_cmd.append("--fail-on-warnings")
        try:
            run_logged(scan_cmd, run_log, ROOT)
        except Exception as exc:
            print(f"WARNING: non-fatal log scan failure ({profile.name}): {exc}")


def run_microsoc_direct(
    profile: Profile,
    tag: str,
    notes: str,
    target_mhz: float,
    threads: int,
    fail_on_warnings: bool,
    skip_log_scan: bool,
    allow_log_scan_fail: bool,
    skip_generate: bool,
    run_suffix: str,
) -> None:
    if profile.top_module is None or profile.output_prefix is None:
        raise RuntimeError(f"Profile '{profile.name}' is missing top_module/output_prefix")

    suffix = run_suffix.strip()
    prefix = profile.output_prefix if not suffix else f"{profile.output_prefix}_{suffix}"
    gen_log = OUT / f"{prefix}_generate.log"
    yosys_log = OUT / f"{prefix}_yosys.log"
    nextpnr_log = OUT / f"{prefix}_nextpnr.log"
    summary = OUT / f"{prefix}_timing_summary.txt"
    metrics_log = OUT / f"{prefix}_metrics.log"
    json_path = OUT / f"{profile.top_module}_{prefix}_ecp5.json"
    textcfg = OUT / f"{profile.top_module}_{prefix}_ecp5_out.config"

    OUT.mkdir(parents=True, exist_ok=True)
    for p in [gen_log, yosys_log, nextpnr_log, summary, metrics_log]:
        if p.exists():
            p.unlink()

    ws_root, isolated_work = prepare_isolated_workspace(run_suffix, gen_log)
    append_event(metrics_log, f"ISOLATED_WORKSPACE path={isolated_work}")

    start_ts = time.time()
    before_verilog = set(isolated_work.glob("*.v"))

    if not skip_generate:
        run_sbt_main(
            isolated_work,
            sbt_arg_string(profile.sbt_main, profile.sbt_args),
            gen_log,
            sbt_boot_dir=ws_root / ".sbt-boot",
            sbt_dir=ws_root / ".sbt",
            ivy_home=ws_root / ".ivy2",
            coursier_cache=ws_root / ".coursier",
            no_server=True,
        )

    rtl = find_generated_verilog(isolated_work, start_ts, before_verilog, hint="microsoc")

    yosys = ensure_tool("yosys")
    nextpnr = ensure_tool("nextpnr-ecp5")

    ys_script = f"read_verilog {rtl}; synth_ecp5 -top {profile.top_module} -json {json_path}; stat"
    run_logged([yosys, "-q", "-l", str(yosys_log), "-p", ys_script], yosys_log, OUT)

    nextpnr_cmd = build_nextpnr_cmd(nextpnr, json_path, textcfg, threads, freq_mhz=25.0)
    run_logged(nextpnr_cmd, nextpnr_log, OUT)

    log = nextpnr_log.read_text(encoding="utf-8", errors="replace")
    achieved = re.findall(r"Max frequency for clock '[^']+':\s*([0-9.]+) MHz", log)
    status_lines = [
        line for line in log.splitlines() if "Info: Max frequency" in line or "Info: Critical path" in line
    ]

    lines = [
        f"ECP5 nextpnr timing summary ({profile.name})",
        f"rtl={rtl}",
        f"json={json_path}",
        f"textcfg={textcfg}",
        f"yosys_log={yosys_log}",
        f"nextpnr_log={nextpnr_log}",
    ]
    if achieved:
        lines.append("max_frequencies_mhz=" + ", ".join(achieved))
    if status_lines:
        lines.append("key_lines:")
        lines.extend(status_lines)
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # CSV/report/scan consume shared files; serialize only this tiny section.
    with with_shared_pipeline_lock(ROOT, event_log=metrics_log, section=f"{profile.name}:metrics"):
        run_logged(
            [
                sys.executable,
                str(SCRIPTS / "43_record_ecp5_metrics.py"),
                "--timing-summary",
                str(summary),
                "--nextpnr-log",
                str(nextpnr_log),
                "--tag",
                tag,
                "--notes",
                notes,
                "--target-mhz",
                str(target_mhz),
            ],
            metrics_log,
            ROOT,
        )
        run_logged([sys.executable, str(SCRIPTS / "44_generate_ecp5_report.py")], metrics_log, ROOT)

        if not skip_log_scan:
            scan_cmd = [sys.executable, str(SCRIPTS / "45_scan_logs.py")]
            if fail_on_warnings:
                scan_cmd.append("--fail-on-warnings")
            if allow_log_scan_fail:
                try:
                    run_logged(scan_cmd, metrics_log, ROOT)
                except Exception as exc:
                    print(f"WARNING: non-fatal log scan failure ({profile.name}): {exc}")
            else:
                run_logged(scan_cmd, metrics_log, ROOT)


def main() -> int:
    args = parse_args()

    try:
        profiles = load_profiles(args.config)
    except Exception as exc:
        print(f"ERROR: failed to load config: {exc}")
        return 2

    if args.profile not in profiles:
        print(f"ERROR: unknown profile '{args.profile}'")
        return 2

    p = profiles[args.profile]
    tag = args.tag or p.tag
    notes = args.notes or p.notes

    log_suffix = args.run_suffix.strip() or "single"
    run_log = OUT / f"{p.name}_{log_suffix}_profile.log"
    if run_log.exists():
        run_log.unlink()

    print(f"Profile: {p.name}")
    print(f"Kind: {p.kind}")
    print(f"Tag: {tag}")

    try:
        if p.kind == "core_dev":
            run_core_dev(
                p,
                tag,
                notes,
                args.target_mhz,
                args.threads,
                args.fail_on_warnings,
                args.skip_log_scan,
                args.allow_log_scan_fail,
                run_log,
            )
        elif p.kind == "microsoc_direct":
            run_microsoc_direct(
                p,
                tag,
                notes,
                args.target_mhz,
                args.threads,
                args.fail_on_warnings,
                args.skip_log_scan,
                args.allow_log_scan_fail,
                args.skip_generate,
                args.run_suffix,
            )
        else:
            raise RuntimeError(f"Unsupported profile kind '{p.kind}'")
    except Exception as exc:
        print(f"ERROR: {exc}")
        print(f"Run log: {run_log}")
        return 2

    print("Profile run complete")
    print(f"CSV: {ROOT / 'riscv-64' / 'metrics' / 'ecp5_usage_history.csv'}")
    print(f"Report: {ROOT / 'riscv-64' / 'metrics' / 'reports' / 'ecp5_usage_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
