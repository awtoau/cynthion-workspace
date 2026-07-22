#!/usr/bin/env python3
"""Profile a core-only VexiiRiscv configuration with a 4 KiB I-cache.

Flow:
1) Generate VexiiRiscv.v with fetch L1 enabled (64 sets x 1 way x 64B line = 4 KiB)
2) Run timing/metrics/report/log-scan via dev.py (sim steps skipped)
"""

from __future__ import annotations

import argparse
import pathlib

from profile_shared import run_dev_profile, run_sbt_main

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = ROOT / "riscv-64" / "work" / "vexiiriscv"
OUT = ROOT / "riscv-64" / "out" / "sim"
LOG = OUT / "core_icache_4k_profile.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="core_x64_sv_rvm_rvc_rdtime_i4k", help="Datapoint tag for metrics")
    parser.add_argument("--notes", default="Core x64 supervisor+rvm+rvc+rdtime with 4KiB I-cache", help="Datapoint notes")
    parser.add_argument("--target-mhz", type=float, default=25.0, help="Timing target")
    parser.add_argument("--threads", type=int, default=0, help="nextpnr thread count (0 uses default)")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Fail run if scanner finds warnings")
    return parser.parse_args()


def generate_icache_core() -> None:
    sbt_arg = (
        "runMain vexiiriscv.Generate "
        "--xlen 64 --with-supervisor --with-rvm --with-rvc --with-rdtime "
        "--with-fetch-l1 --fetch-l1-sets 64 --fetch-l1-ways 1"
    )
    run_sbt_main(WORK, sbt_arg, LOG)


def run_profile(tag: str, notes: str, target_mhz: float, threads: int, fail_on_warnings: bool) -> None:
    run_dev_profile(ROOT, tag, notes, target_mhz, threads, fail_on_warnings, LOG)


def main() -> int:
    args = parse_args()

    if LOG.exists():
        LOG.unlink()

    try:
        generate_icache_core()
        run_profile(args.tag, args.notes, args.target_mhz, args.threads, args.fail_on_warnings)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print(f"Log: {LOG}")
        return 2

    print("Core I-cache 4 KiB profile complete")
    print(f"Log: {LOG}")
    print(f"Report: {ROOT / 'riscv-64' / 'metrics' / 'reports' / 'ecp5_usage_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
