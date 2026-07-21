#!/usr/bin/env python3
"""Check host prerequisites for RV64 bring-up work."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R64 = ROOT / "riscv-64"
OUT = R64 / "out"
OUT.mkdir(parents=True, exist_ok=True)

TOOLS = [
    "git",
    "python3",
    "qemu-system-riscv64",
    "yosys",
    "nextpnr-ecp5",
    "ecppack",
    "dtc",
    "make",
]

MIRROR = Path("/mnt/2tb/git_mirror/SpinalHDL/VexiiRiscv.git")


def main() -> int:
    report = {
        "workspace": str(ROOT),
        "mirror": {
            "path": str(MIRROR),
            "exists": MIRROR.exists(),
        },
        "tools": {},
    }

    missing = []
    for tool in TOOLS:
        found = shutil.which(tool)
        report["tools"][tool] = found
        if found is None:
            missing.append(tool)

    out_file = OUT / "env_report.json"
    out_file.write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")

    print(f"Wrote {out_file}")
    if missing:
        print("Missing tools:")
        for tool in missing:
            print(f"- {tool}")
        return 1

    print("All required tools found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
