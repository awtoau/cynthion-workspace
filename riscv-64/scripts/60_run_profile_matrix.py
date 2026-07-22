#!/usr/bin/env python3
"""Run profile scripts from a config file, optionally across a thread sweep.

This allows one generic entrypoint for all profile variants and standardizes
threaded reruns (e.g. 32-thread tests) with consistent tags and notes.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import datetime as dt
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "riscv-64" / "scripts"
DEFAULT_CONFIG = ROOT / "riscv-64" / "config" / "profile_matrix.json"


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    tag: str
    notes: str


def parse_threads(raw: str) -> list[int]:
    values = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError(f"Invalid thread value '{token}'") from exc
        if value < 0:
            raise ValueError("Thread counts must be >= 0")
        values.append(value)
    if not values:
        raise ValueError("No thread values provided")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=DEFAULT_CONFIG,
        help="Path to profile config JSON",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help="Profile name to run (repeat for multiple). If omitted, uses --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all configured profiles.",
    )
    parser.add_argument(
        "--threads",
        default="8",
        help="Comma-separated thread counts (for example: 8,16,32).",
    )
    parser.add_argument(
        "--target-mhz",
        type=float,
        default=25.0,
        help="Timing target passed to profile scripts.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Pass fail-on-warnings through to profile scripts.",
    )
    parser.add_argument(
        "--skip-log-scan",
        action="store_true",
        help="Skip log scanner step in profile scripts.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available profiles and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of profile jobs to dispatch concurrently (default: 1).",
    )
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help="Clear metrics CSV before running matrix (clean full recreate).",
    )
    return parser.parse_args()


def load_profiles(path: pathlib.Path) -> list[Profile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("profiles", [])
    profiles = []
    for item in items:
        profiles.append(
            Profile(
                name=item["name"],
                kind=item["kind"],
                tag=item["tag"],
                notes=item["notes"],
            )
        )
    return profiles


def select_profiles(all_profiles: list[Profile], selected: list[str], run_all: bool) -> list[Profile]:
    if run_all:
        return all_profiles

    if not selected:
        raise ValueError("Select at least one profile with --profile, or use --all")

    by_name = {p.name: p for p in all_profiles}
    missing = [name for name in selected if name not in by_name]
    if missing:
        raise ValueError(f"Unknown profile(s): {', '.join(missing)}")

    return [by_name[name] for name in selected]


def run_cmd(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def build_invocation(
    profile: Profile,
    config_path: pathlib.Path,
    threads: int,
    target_mhz: float,
    fail_on_warnings: bool,
    skip_log_scan: bool,
) -> list[str]:
    tag = f"{profile.tag}_t{threads}" if threads > 0 else profile.tag
    notes = f"{profile.notes}; threads={threads}" if threads > 0 else profile.notes

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "61_run_profile.py"),
        "--profile",
        profile.name,
        "--config",
        str(config_path),
        "--threads",
        str(threads),
        "--tag",
        tag,
        "--notes",
        notes,
        "--target-mhz",
        str(target_mhz),
    ]
    if fail_on_warnings:
        cmd.append("--fail-on-warnings")
    if skip_log_scan:
        cmd.append("--skip-log-scan")
    return cmd


def warn_if_likely_contention(threads: list[int]) -> None:
    cpu_total = os.cpu_count() or 1
    # nextpnr scaling usually flattens before full logical-CPU saturation.
    warn_threshold = max(1, cpu_total // 2)
    high = sorted({t for t in threads if t > warn_threshold})
    if high:
        values = ", ".join(str(t) for t in high)
        print(
            f"WARNING: requested thread count(s) {values} exceed contention threshold "
            f"{warn_threshold} for this host ({cpu_total} logical CPUs)."
        )
        print("WARNING: start with 8-16 threads and only increase if wall time improves.")


def main() -> int:
    args = parse_args()

    try:
        threads = parse_threads(args.threads)
        profiles = load_profiles(args.config)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    warn_if_likely_contention(threads)

    if args.list:
        for p in profiles:
            print(f"{p.name}\t{p.kind}\t{p.tag}")
        return 0

    try:
        chosen = select_profiles(profiles, args.profile, args.all)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    print(f"Profiles: {', '.join(p.name for p in chosen)}")
    print(f"Threads: {threads}")
    print(f"Jobs: {args.jobs}")

    if args.jobs < 1:
        print("ERROR: --jobs must be >= 1")
        return 2

    if args.reset_history:
        csv = ROOT / "riscv-64" / "metrics" / "ecp5_usage_history.csv"
        if csv.exists():
            archive = csv.with_name(
                f"{csv.stem}.archive-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}{csv.suffix}"
            )
            csv.replace(archive)
            print(f"Reset history: archived previous CSV to {archive}")

    try:
        commands: list[list[str]] = []
        for p in chosen:
            for t in threads:
                cmd = build_invocation(
                    p,
                    args.config,
                    t,
                    args.target_mhz,
                    args.fail_on_warnings,
                    args.skip_log_scan,
                )
                if args.dry_run:
                    print("$", " ".join(cmd))
                else:
                    commands.append(cmd)

        if not args.dry_run:
            if args.jobs == 1:
                for cmd in commands:
                    run_cmd(cmd)
            else:
                print("Running in concurrent mode; pipeline lock will serialize shared build sections safely.")
                with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
                    futures = [ex.submit(run_cmd, cmd) for cmd in commands]
                    for fut in cf.as_completed(futures):
                        fut.result()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    print("Profile matrix run complete")
    print(f"CSV: {ROOT / 'riscv-64' / 'metrics' / 'ecp5_usage_history.csv'}")
    print(f"Report: {ROOT / 'riscv-64' / 'metrics' / 'reports' / 'ecp5_usage_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
