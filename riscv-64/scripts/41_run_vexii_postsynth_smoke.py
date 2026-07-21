#!/usr/bin/env python3
"""Run post-synthesis (generic gate-level) smoke simulation for VexiiRiscv."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VEXII = ROOT / "riscv-64" / "work" / "vexiiriscv"
TB = ROOT / "riscv-64" / "sim" / "tb_vexiiriscv_smoke.v"
OUTDIR = ROOT / "riscv-64" / "out" / "sim"
OUTDIR.mkdir(parents=True, exist_ok=True)

RTL = VEXII / "VexiiRiscv.v"
NETLIST = OUTDIR / "VexiiRiscv_postsynth.v"
COMPILE_LOG = OUTDIR / "vexii_postsynth_compile.log"
RUN_LOG = OUTDIR / "vexii_postsynth_run.log"
SIMV = OUTDIR / "vexii_postsynth.vvp"
YOSYS_LOG = OUTDIR / "vexii_postsynth_yosys.log"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True)


def main() -> int:
    if not RTL.exists():
        print(f"Missing RTL: {RTL}")
        return 1

    yosys = shutil.which("yosys")
    if yosys is None:
        print("Missing tool: yosys")
        return 1

    simcells = Path("/usr/share/yosys/simcells.v")
    if not simcells.exists():
        datdir = run(["yosys", "-Q", "-p", "echo [datdir]"])
        if datdir.returncode == 0:
            candidate = datdir.stdout.strip().splitlines()[-1].strip()
            simcells = Path(candidate) / "simcells.v"
    if not simcells.exists():
        print(f"Missing yosys simcells.v: {simcells}")
        return 1

    ys = (
        f"read_verilog {RTL}; "
        f"synth -top VexiiRiscv; "
        f"write_verilog -noattr {NETLIST}"
    )

    yp = run([yosys, "-q", "-l", str(YOSYS_LOG), "-p", ys])
    if yp.returncode != 0:
        print(f"Yosys synthesis failed. See {YOSYS_LOG}")
        return yp.returncode

    cp = run([
        "iverilog",
        "-g2012",
        "-s",
        "tb_vexiiriscv_smoke",
        "-o",
        str(SIMV),
        str(TB),
        str(NETLIST),
        str(simcells),
    ])
    COMPILE_LOG.write_text(cp.stdout + cp.stderr, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        print(f"Post-synth compile failed. See {COMPILE_LOG}")
        return cp.returncode

    rp = run(["vvp", str(SIMV)])
    RUN_LOG.write_text(rp.stdout + rp.stderr, encoding="utf-8", errors="replace")
    print(f"Run log: {RUN_LOG}")

    if rp.returncode != 0:
        print("Post-synth simulation failed.")
        return rp.returncode

    if "PASS: Fetch-loop signature observed" in rp.stdout:
        print("Post-synth smoke PASS")
        return 0

    print("Post-synth smoke did not report PASS sentinel.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
