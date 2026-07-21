#!/usr/bin/env python3
"""Compile and run a minimal VexiiRiscv RTL smoke simulation."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEXII = ROOT / "riscv-64" / "work" / "vexiiriscv"
TB = ROOT / "riscv-64" / "sim" / "tb_vexiiriscv_smoke.v"
OUTDIR = ROOT / "riscv-64" / "out" / "sim"
OUTDIR.mkdir(parents=True, exist_ok=True)

RTL = VEXII / "VexiiRiscv.v"
SIMV = OUTDIR / "vexii_smoke.vvp"
SIMLOG = OUTDIR / "vexii_smoke_run.log"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)


def main() -> int:
    if not RTL.exists():
        print(f"Missing RTL: {RTL}")
        print("Generate it first with: sbt \"runMain vexiiriscv.Generate\"")
        return 1

    if not TB.exists():
        print(f"Missing testbench: {TB}")
        return 1

    compile_cmd = [
        "iverilog",
        "-g2012",
        "-s",
        "tb_vexiiriscv_smoke",
        "-o",
        str(SIMV),
        str(TB),
        str(RTL),
    ]

    cp = run(compile_cmd)
    compile_log = OUTDIR / "vexii_smoke_compile.log"
    compile_log.write_text(cp.stdout + cp.stderr, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        print(f"Compile failed. See {compile_log}")
        return cp.returncode

    rp = run(["vvp", str(SIMV)])
    SIMLOG.write_text(rp.stdout + rp.stderr, encoding="utf-8", errors="replace")
    print(f"Run log: {SIMLOG}")

    if rp.returncode != 0:
        print("Simulation failed.")
        return rp.returncode

    if "PASS: Fetch-loop signature observed" in rp.stdout:
        print("Smoke test PASS")
        return 0

    print("Smoke test did not report PASS sentinel.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
