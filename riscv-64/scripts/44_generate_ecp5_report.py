#!/usr/bin/env python3
"""Generate a Markdown trend report from ECP5 usage history CSV."""

from __future__ import annotations

import argparse
import csv
import pathlib
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CSV_DEFAULT = REPO_ROOT / "riscv-64" / "metrics" / "ecp5_usage_history.csv"
REPORT_DEFAULT = REPO_ROOT / "riscv-64" / "metrics" / "reports" / "ecp5_usage_report.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=pathlib.Path, default=CSV_DEFAULT, help="Input CSV history file")
    parser.add_argument("--out", type=pathlib.Path, default=REPORT_DEFAULT, help="Output Markdown report")
    parser.add_argument("--max-rows", type=int, default=50, help="Max table rows to render")
    return parser.parse_args()


def read_rows(csv_path: pathlib.Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"History file not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def f(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def i(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def bram_kib(blocks: int) -> float:
    # ECP5 DP16KD block capacity is 18 Kb = 2.25 KiB.
    return blocks * 2.25


def pct_change(old: float, new: float) -> str:
    if old == 0:
        return "n/a"
    return f"{((new - old) / old) * 100.0:+.2f}%"


def mermaid_xy(rows: list[dict[str, str]], key: str, title: str, y_label: str = "percent") -> str:
    points = []
    for idx, row in enumerate(rows):
        val = f(row.get(key, ""), default=-1.0)
        if val >= 0:
            points.append((idx + 1, val))
    if not points:
        return "```mermaid\nxychart-beta\n  title \"No data\"\n  x-axis []\n  y-axis \"value\" 0 --> 1\n  line []\n```"

    x_labels = ", ".join(str(x) for x, _ in points)
    y_vals = ", ".join(f"{y:.2f}" for _, y in points)
    y_max = max(y for _, y in points)
    y_upper = max(1.0, y_max * 1.15)

    return (
        "```mermaid\n"
        "xychart-beta\n"
        f"  title \"{title}\"\n"
        f"  x-axis [{x_labels}]\n"
        f"  y-axis \"{y_label}\" 0 --> {y_upper:.2f}\n"
        f"  line [{y_vals}]\n"
        "```"
    )


def bram_kib_pair(row: dict[str, str]) -> str:
    used_raw = row.get("bram_used", "")
    total_raw = row.get("bram_total", "")
    if not used_raw and not total_raw:
        return ""
    used_kib = bram_kib(i(used_raw, default=0))
    total_kib = bram_kib(i(total_raw, default=0)) if total_raw else 0.0
    if total_raw:
        return f"{used_kib:.2f}/{total_kib:.2f}"
    return f"{used_kib:.2f}"


def bram_blocks_pair(row: dict[str, str]) -> str:
    used_raw = row.get("bram_used", "")
    total_raw = row.get("bram_total", "")
    if not used_raw and not total_raw:
        return ""
    if total_raw:
        return f"{used_raw}/{total_raw}"
    return used_raw


def build_report(rows: list[dict[str, str]], max_rows: int) -> str:
    if not rows:
        return "# ECP5 Usage Report\n\nNo rows found.\n"

    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    latest_bram_used_blocks = i(latest.get("bram_used", ""), default=0)
    latest_bram_total_blocks = i(latest.get("bram_total", ""), default=0)
    latest_bram_used_kib = bram_kib(latest_bram_used_blocks)
    latest_bram_total_kib = bram_kib(latest_bram_total_blocks)

    lines: list[str] = []
    lines.append("# ECP5 Usage Report")
    lines.append("")
    lines.append("This report tracks ECP5 resource and timing growth as RV64 features are added.")
    lines.append("")
    lines.append("## Latest Snapshot")
    lines.append("")
    lines.append(f"- Timestamp: {latest.get('timestamp', '')}")
    lines.append(f"- Commit: {latest.get('git_commit', '')}")
    lines.append(f"- Tag: {latest.get('tag', '')}")
    lines.append(
        f"- Device: {latest.get('device', '')} {latest.get('package', '')} speed {latest.get('speed', '')}"
    )
    lines.append(
        f"- LUT: {latest.get('luts_used', '')}/{latest.get('luts_total', '')} ({latest.get('luts_pct', '')}%)"
    )
    lines.append(
        f"- FF: {latest.get('ffs_used', '')}/{latest.get('ffs_total', '')} ({latest.get('ffs_pct', '')}%)"
    )
    lines.append(
        f"- BRAM: {latest.get('bram_used', '')}/{latest.get('bram_total', '')} blocks ({latest.get('bram_pct', '')}%), {latest_bram_used_kib:.2f}/{latest_bram_total_kib:.2f} KiB"
    )
    lines.append(
        f"- Fmax: {latest.get('fmax_mhz', '')} MHz (target {latest.get('target_mhz', '')} MHz, pass={latest.get('timing_pass', '')})"
    )
    if latest.get("notes"):
        lines.append(f"- Notes: {latest.get('notes', '')}")

    if prev:
        prev_bram_used_blocks = i(prev.get("bram_used", ""), default=0)
        prev_bram_total_blocks = i(prev.get("bram_total", ""), default=0)
        prev_bram_used_kib = bram_kib(prev_bram_used_blocks)
        _ = prev_bram_total_blocks
        lines.append("")
        lines.append("## Delta Vs Previous")
        lines.append("")
        lines.append(f"- LUT percent delta: {pct_change(f(prev.get('luts_pct', '0')), f(latest.get('luts_pct', '0')))}")
        lines.append(f"- FF percent delta: {pct_change(f(prev.get('ffs_pct', '0')), f(latest.get('ffs_pct', '0')))}")
        lines.append(f"- BRAM percent delta: {pct_change(f(prev.get('bram_pct', '0')), f(latest.get('bram_pct', '0')))}")
        lines.append(f"- BRAM blocks delta: {latest_bram_used_blocks - prev_bram_used_blocks:+d} blocks")
        lines.append(f"- BRAM KiB delta: {latest_bram_used_kib - prev_bram_used_kib:+.2f} KiB")
        lines.append(f"- Fmax delta: {f(latest.get('fmax_mhz', '0')) - f(prev.get('fmax_mhz', '0')):+.2f} MHz")

    lines.append("")
    lines.append("## Trend Graphs")
    lines.append("")
    lines.append(mermaid_xy(rows, "luts_pct", "LUT Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(rows, "ffs_pct", "FF Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(rows, "bram_pct", "BRAM Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(rows, "bram_used", "BRAM Blocks Used", y_label="blocks"))
    lines.append("")
    lines.append(mermaid_xy(rows, "fmax_mhz", "Fmax MHz", y_label="MHz"))

    lines.append("")
    lines.append("## History")
    lines.append("")
    lines.append("| # | timestamp | commit | tag | LUT % | FF % | BRAM % | BRAM blocks | BRAM KiB | Fmax MHz | timing pass |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|")

    sliced = rows[-max_rows:]
    start_index = len(rows) - len(sliced) + 1
    for offset, row in enumerate(sliced):
        idx = start_index + offset
        lines.append(
            "| {idx} | {ts} | {commit} | {tag} | {luts} | {ffs} | {bram} | {bram_blocks} | {bram_kib} | {fmax} | {pass_} |".format(
                idx=idx,
                ts=row.get("timestamp", ""),
                commit=row.get("git_commit", ""),
                tag=row.get("tag", ""),
                luts=row.get("luts_pct", ""),
                ffs=row.get("ffs_pct", ""),
                bram=row.get("bram_pct", ""),
                bram_blocks=bram_blocks_pair(row),
                bram_kib=bram_kib_pair(row),
                fmax=row.get("fmax_mhz", ""),
                pass_=row.get("timing_pass", ""),
            )
        )

    lines.append("")
    lines.append("## How To Update")
    lines.append("")
    lines.append("1. Run the build flow (`42_run_vexii_nextpnr_timing.py`).")
    lines.append("2. Append a datapoint (`43_record_ecp5_metrics.py --tag ...`).")
    lines.append("3. Regenerate this report (`44_generate_ecp5_report.py`).")
    lines.append("")
    lines.append("Or run all three steps together:")
    lines.append("")
    lines.append("- `python3 riscv-64/scripts/dev.py --tag <change-name> --notes \"what changed\"`")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    rows = read_rows(args.csv)
    report = build_report(rows, max_rows=args.max_rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Wrote report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
