#!/usr/bin/env python3
"""Generate a Markdown trend report from ECP5 usage history CSV."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import re
import pathlib
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CSV_DEFAULT = REPO_ROOT / "riscv-64" / "metrics" / "ecp5_usage_history.csv"
REPORT_DEFAULT = REPO_ROOT / "riscv-64" / "metrics" / "reports" / "ecp5_usage_report.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=pathlib.Path, default=CSV_DEFAULT, help="Input CSV history file")
    parser.add_argument("--out", type=pathlib.Path, default=REPORT_DEFAULT, help="Output Markdown report")
    parser.add_argument("--max-rows", type=int, default=500, help="Max table rows to render")
    return parser.parse_args()


def read_rows(csv_path: pathlib.Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"History file not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def row_timestamp(row: dict[str, str]) -> datetime:
    timestamp = row.get("timestamp", "")
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


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


FEATURE_ORDER = ["fetch_l1", "lsu_l1", "btb", "gshare", "ras", "dual_issue", "clint", "uart"]

STAGE_ORDER = [
    "soc_x64_sv_rvm_rvc_rdtime_clint_uart",
    "core_x64_sv_rvm_rvc_rdtime_i4k",
    "core_x64_sv_rvm_rvc_rdtime_i4k_d4k",
    "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras",
    "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual",
    "soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart",
]


def base_tag(tag: str) -> str:
    return re.sub(r"_t\d+$", "", tag)


def feature_flags(tag: str) -> dict[str, bool]:
    base = base_tag(tag)
    return {
        "fetch_l1": "i4k" in base,
        "lsu_l1": "d4k" in base,
        "btb": "btb" in base,
        "gshare": "gshare" in base,
        "ras": "ras" in base,
        "dual_issue": "dual" in base,
        "clint": "clint" in base,
        "uart": "uart" in base,
    }


def stage_label(tag: str) -> str:
    base = base_tag(tag)
    if base == "soc_x64_sv_rvm_rvc_rdtime_clint_uart":
        return "MicroSoc x64 + CLINT + UART"
    if base == "core_x64_sv_rvm_rvc_rdtime_i4k":
        return "Core x64 + I-cache"
    if base == "core_x64_sv_rvm_rvc_rdtime_i4k_d4k":
        return "Core x64 + I-cache + D-cache"
    if base == "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras":
        return "Core x64 + I-cache + D-cache + branch prediction"
    if base == "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual":
        return "Core x64 + I-cache + D-cache + branch prediction + dual issue"
    if base == "soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart":
        return "MicroSoc x64 + core stack + CLINT + UART"
    return base


def stage_score(tag: str) -> tuple[int, str]:
    order = {name: idx for idx, name in enumerate(STAGE_ORDER)}
    return order.get(base_tag(tag), 99), tag


def current_stage_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    latest_by_stage: dict[str, dict[str, str]] = {}
    for row in rows:
        stage = base_tag(row.get("tag", ""))
        if stage in STAGE_ORDER:
            latest_by_stage[stage] = row
    return [latest_by_stage[stage] for stage in STAGE_ORDER if stage in latest_by_stage]


def current_stage_rows_for_family(rows: list[dict[str, str]], family: str) -> list[dict[str, str]]:
    family_rows = [row for row in rows if family_label(row.get("tag", "")) == family]
    latest_by_stage: dict[str, dict[str, str]] = {}
    for row in family_rows:
        stage = base_tag(row.get("tag", ""))
        if stage in STAGE_ORDER:
            latest_by_stage[stage] = row
    return [latest_by_stage[stage] for stage in STAGE_ORDER if stage in latest_by_stage]


def latest_combination_rows_for_family(rows: list[dict[str, str]], family: str) -> list[dict[str, str]]:
    # Keep the latest datapoint per exact base tag so exhaustive profile reruns do not duplicate rows.
    latest_by_tag: dict[str, dict[str, str]] = {}
    for row in rows:
        tag = row.get("tag", "")
        if family_label(tag) != family:
            continue
        latest_by_tag[base_tag(tag)] = row

    feature_key = ["fetch_l1", "lsu_l1", "btb", "gshare", "ras", "dual_issue", "clint", "uart"]

    def sort_key(row: dict[str, str]) -> tuple[int, tuple[int, ...], str]:
        flags = feature_flags(row.get("tag", ""))
        bits = tuple(1 if flags[k] else 0 for k in feature_key)
        return (sum(bits), bits, row.get("tag", ""))

    return sorted(latest_by_tag.values(), key=sort_key)


def checkbox(value: bool) -> str:
    return "yes" if value else ""


def feature_cell(row: dict[str, str], key: str) -> str:
    return checkbox(feature_flags(row.get("tag", "")).get(key, False))


def stage_delta_label(tag: str) -> str:
    base = base_tag(tag)
    deltas = {
        "soc_x64_sv_rvm_rvc_rdtime_clint_uart": "UART + CLINT",
        "core_x64_sv_rvm_rvc_rdtime_i4k": "+ fetch L1",
        "core_x64_sv_rvm_rvc_rdtime_i4k_d4k": "+ LSU L1",
        "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras": "+ branch prediction",
        "core_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual": "+ dual issue",
        "soc_x64_sv_rvm_rvc_rdtime_i4k_d4k_btb_gshare_ras_dual_clint_uart": "+ cumulative SoC stack",
    }
    return deltas.get(base, base)


def display_notes(notes: str) -> str:
    cleaned = re.sub(r"(?:;\s*threads=\d+|\s+threads=\d+)\s*$", "", notes).strip()
    return cleaned


def should_show_history_row(row: dict[str, str]) -> bool:
    return base_tag(row.get("tag", "")) not in {"baseline-bram", "baseline-bram-fixed"}


def family_label(tag: str) -> str:
    base = base_tag(tag)
    if base.startswith("core_"):
        return "Core"
    if base.startswith("soc_"):
        return "SoC"
    return "Other"


def thread_suffix(tag: str) -> str:
    m = re.search(r"(_t\d+)$", tag)
    return m.group(1) if m else ""


def same_thread_mode(row_tag: str, active_suffix: str) -> bool:
    # Keep rows from the same explicit thread mode as the latest datapoint.
    if not active_suffix:
        return True
    return thread_suffix(row_tag) == active_suffix


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


def render_history_table(rows: list[dict[str, str]], lines: list[str]) -> None:
    lines.append("| # | tag | LUT % | FF % | BRAM % | Fmax MHz | notes |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for idx, row in enumerate(rows, start=1):
        lines.append(
            "| {idx} | {tag} | {luts} | {ffs} | {bram} | {fmax} | {notes} |".format(
                idx=idx,
                tag=row.get("tag", ""),
                luts=row.get("luts_pct", ""),
                ffs=row.get("ffs_pct", ""),
                bram=row.get("bram_pct", ""),
                fmax=row.get("fmax_mhz", ""),
                notes=display_notes(row.get("notes", "")),
            )
        )


def render_cumulative_matrix(title: str, rows: list[dict[str, str]], lines: list[str]) -> None:
    lines.append(title)
    lines.append("")
    lines.append("Legend: rows are ordered by enabled feature count (fewest to most), then by feature bit pattern.")
    lines.append("")
    lines.append("| # | fetch L1 | lsu L1 | btb | gshare | ras | dual issue | clint | uart | LUT % | FF % | BRAM % | Fmax MHz |")
    lines.append("|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|")
    for row in rows:
        flags = feature_flags(row.get("tag", ""))
        lines.append(
            "| {idx} | {fetch} | {lsu} | {btb} | {gshare} | {ras} | {dual} | {clint} | {uart} | {luts} | {ffs} | {bram} | {fmax} |".format(
                idx=rows.index(row) + 1,
                fetch=checkbox(flags["fetch_l1"]),
                lsu=checkbox(flags["lsu_l1"]),
                btb=checkbox(flags["btb"]),
                gshare=checkbox(flags["gshare"]),
                ras=checkbox(flags["ras"]),
                dual=checkbox(flags["dual_issue"]),
                clint=checkbox(flags["clint"]),
                uart=checkbox(flags["uart"]),
                luts=row.get("luts_pct", ""),
                ffs=row.get("ffs_pct", ""),
                bram=row.get("bram_pct", ""),
                fmax=row.get("fmax_mhz", ""),
            )
        )


def build_report(rows: list[dict[str, str]], max_rows: int) -> str:
    if not rows:
        return "# ECP5 Usage Report\n\nNo rows found.\n"

    all_rows = sorted(rows, key=row_timestamp)
    latest = all_rows[-1]
    active_thread_suffix = thread_suffix(latest.get("tag", ""))
    prev = all_rows[-2] if len(all_rows) > 1 else None
    staged_rows = current_stage_rows(rows)
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
    lines.append(f"- Stage: {stage_label(latest.get('tag', ''))}")
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
        lines.append(f"- Notes: {display_notes(latest.get('notes', ''))}")

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
    history_rows = [
        row
        for row in all_rows
        if should_show_history_row(row) and same_thread_mode(row.get("tag", ""), active_thread_suffix)
    ]
    core_rows = [row for row in history_rows if family_label(row.get("tag", "")) == "Core"]
    soc_rows = [row for row in history_rows if family_label(row.get("tag", "")) == "SoC"]

    lines.append("## Core History")
    lines.append("")
    render_history_table(core_rows[-max_rows:], lines)

    lines.append("")
    lines.append("## SoC History")
    lines.append("")
    render_history_table(soc_rows[-max_rows:], lines)

    lines.append("")
    core_combo_rows = latest_combination_rows_for_family(history_rows, "Core")
    soc_combo_rows = latest_combination_rows_for_family(history_rows, "SoC")
    render_cumulative_matrix("## Core Cumulative Matrix", core_combo_rows[:max_rows], lines)

    lines.append("")
    render_cumulative_matrix("## SoC Cumulative Matrix", soc_combo_rows[:max_rows], lines)

    lines.append("")
    lines.append("## Trend Graphs")
    lines.append("")
    lines.append(mermaid_xy(staged_rows, "luts_pct", "LUT Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(staged_rows, "ffs_pct", "FF Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(staged_rows, "bram_pct", "BRAM Usage Percent"))
    lines.append("")
    lines.append(mermaid_xy(staged_rows, "bram_used", "BRAM Blocks Used", y_label="blocks"))
    lines.append("")
    lines.append(mermaid_xy(staged_rows, "fmax_mhz", "Fmax MHz", y_label="MHz"))

    lines.append("")
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
