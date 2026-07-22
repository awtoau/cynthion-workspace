#!/usr/bin/env python3
"""Scan RV64 build/sim logs for warnings and errors.

Creates a summary file under riscv-64/out/sim so each dev run has a
machine-readable and human-readable quality check.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
SIM_OUT = ROOT / "riscv-64" / "out" / "sim"
SUMMARY = SIM_OUT / "ecp5_log_scan_summary.txt"


ERROR_RE = re.compile(r"\b(error|fatal|traceback|failed)\b", re.IGNORECASE)
WARN_RE = re.compile(r"\b(warn|warning)\b", re.IGNORECASE)

# Known benign tokens in command lines or status summaries.
IGNORE_RE = [
    re.compile(r"timing-allow-fail", re.IGNORECASE),
    re.compile(r"did not report PASS sentinel", re.IGNORECASE),
    re.compile(r"^\[error\]\s+WARNING:", re.IGNORECASE),
    re.compile(r"^FAIL:\s+error signatures found in logs", re.IGNORECASE),
]


@dataclass
class Hit:
    line_no: int
    text: str


@dataclass
class FileScan:
    path: pathlib.Path
    warnings: list[Hit]
    errors: list[Hit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=pathlib.Path,
        default=SIM_OUT,
        help="Directory containing logs to scan.",
    )
    parser.add_argument(
        "--summary",
        type=pathlib.Path,
        default=SUMMARY,
        help="Path to write summary report.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Return non-zero if warnings are found.",
    )
    return parser.parse_args()


def is_ignored(line: str) -> bool:
    for rex in IGNORE_RE:
        if rex.search(line):
            return True
    return False


def scan_file(path: pathlib.Path) -> FileScan:
    warnings: list[Hit] = []
    errors: list[Hit] = []

    text = path.read_text(encoding="utf-8", errors="replace")
    for idx, line in enumerate(text.splitlines(), start=1):
        if is_ignored(line):
            continue
        if ERROR_RE.search(line):
            errors.append(Hit(idx, line.strip()))
        elif WARN_RE.search(line):
            warnings.append(Hit(idx, line.strip()))

    return FileScan(path=path, warnings=warnings, errors=errors)


def collect_logs(directory: pathlib.Path) -> list[pathlib.Path]:
    patterns = ["*.log", "*.txt"]
    logs: list[pathlib.Path] = []
    for pat in patterns:
        logs.extend(sorted(directory.glob(pat)))
    return [p for p in logs if p.is_file() and p.name != SUMMARY.name]


def write_summary(summary_path: pathlib.Path, scans: list[FileScan]) -> tuple[int, int]:
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    total_warn = sum(len(s.warnings) for s in scans)
    total_err = sum(len(s.errors) for s in scans)

    lines: list[str] = []
    lines.append("ECP5 log scan summary")
    lines.append(f"timestamp={dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"scan_dir={SIM_OUT}")
    lines.append(f"files_scanned={len(scans)}")
    lines.append(f"warnings={total_warn}")
    lines.append(f"errors={total_err}")
    lines.append("")

    for scan in scans:
        lines.append(f"[{scan.path.name}] warnings={len(scan.warnings)} errors={len(scan.errors)}")
        for hit in scan.errors[:10]:
            lines.append(f"  ERROR L{hit.line_no}: {hit.text}")
        for hit in scan.warnings[:10]:
            lines.append(f"  WARN  L{hit.line_no}: {hit.text}")
        if len(scan.errors) > 10:
            lines.append(f"  ... {len(scan.errors) - 10} more errors")
        if len(scan.warnings) > 10:
            lines.append(f"  ... {len(scan.warnings) - 10} more warnings")
        lines.append("")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return total_warn, total_err


def main() -> int:
    args = parse_args()
    scan_dir = args.dir

    if not scan_dir.exists():
        print(f"Missing scan directory: {scan_dir}")
        return 2

    logs = collect_logs(scan_dir)
    if not logs:
        print(f"No logs found in: {scan_dir}")
        return 2

    scans = [scan_file(p) for p in logs]
    total_warn, total_err = write_summary(args.summary, scans)

    print(f"Summary: {args.summary}")
    print(f"Scanned files: {len(scans)}")
    print(f"Warnings: {total_warn}")
    print(f"Errors: {total_err}")

    if total_err > 0:
        print("FAIL: error signatures found in logs")
        return 1
    if args.fail_on_warnings and total_warn > 0:
        print("FAIL: warning signatures found in logs")
        return 1

    print("PASS: no blocking log issues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
