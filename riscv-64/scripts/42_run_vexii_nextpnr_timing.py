#!/usr/bin/env python3
"""Run ECP5 synthesis->nextpnr timing flow for standalone VexiiRiscv."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEXII = ROOT / "riscv-64" / "work" / "vexiiriscv"
RTL = VEXII / "VexiiRiscv.v"
WRAP = ROOT / "riscv-64" / "sim" / "vexii_ecp5_wrap.v"
OUTDIR = ROOT / "riscv-64" / "out" / "sim"
OUTDIR.mkdir(parents=True, exist_ok=True)

JSON = OUTDIR / "VexiiRiscv_ecp5.json"
TEXTCFG = OUTDIR / "VexiiRiscv_ecp5_out.config"
YOSYS_LOG = OUTDIR / "vexii_ecp5_yosys.log"
NEXTPNR_LOG = OUTDIR / "vexii_ecp5_nextpnr.log"
SUMMARY = OUTDIR / "vexii_ecp5_timing_summary.txt"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, text=True, capture_output=True)


def main() -> int:
    if not RTL.exists() or not WRAP.exists():
        print(f"Missing RTL or wrapper: {RTL} / {WRAP}")
        return 1

    yosys = shutil.which("yosys")
    nextpnr = shutil.which("nextpnr-ecp5")
    if yosys is None or nextpnr is None:
        print("Missing required tools: yosys and/or nextpnr-ecp5")
        return 1

    ys = (
        f"read_verilog {RTL} {WRAP}; "
        f"synth_ecp5 -top VexiiRiscvWrap -json {JSON}; "
        "stat"
    )

    yp = run([yosys, "-q", "-l", str(YOSYS_LOG), "-p", ys])
    if yp.returncode != 0:
        print(f"ECP5 synthesis failed. See {YOSYS_LOG}")
        return yp.returncode

    np = run([
        nextpnr,
        "--12k",
        "--package",
        "CABGA256",
        "--speed",
        "8",
        "--json",
        str(JSON),
        "--textcfg",
        str(TEXTCFG),
        "--timing-allow-fail",
        "--freq",
        "25",
    ])
    NEXTPNR_LOG.write_text(np.stdout + np.stderr, encoding="utf-8", errors="replace")
    if np.returncode != 0:
        print(f"nextpnr failed. See {NEXTPNR_LOG}")
        return np.returncode

    log = NEXTPNR_LOG.read_text(encoding="utf-8", errors="replace")
    achieved = re.findall(r"Max frequency for clock '[^']+':\s*([0-9.]+) MHz", log)
    status_line = ""
    for line in log.splitlines():
        if "Info: Critical path" in line or "Info: Max frequency" in line:
            status_line += line + "\n"

    summary_lines = [
        "ECP5 nextpnr timing summary",
        f"json={JSON}",
        f"textcfg={TEXTCFG}",
        f"yosys_log={YOSYS_LOG}",
        f"nextpnr_log={NEXTPNR_LOG}",
    ]
    if achieved:
        summary_lines.append("max_frequencies_mhz=" + ", ".join(achieved))
    if status_line:
        summary_lines.append("key_lines:")
        summary_lines.extend(status_line.rstrip().splitlines())

    SUMMARY.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"Timing summary: {SUMMARY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
