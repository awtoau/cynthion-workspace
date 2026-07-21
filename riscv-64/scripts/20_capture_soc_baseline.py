#!/usr/bin/env python3
"""Extract baseline SoC constants from facedancer top for RV64 planning."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R64 = ROOT / "riscv-64"
OUT = R64 / "out"
OUT.mkdir(parents=True, exist_ok=True)

TOP = ROOT / "debris" / "awto-cynthion" / "cynthion" / "python" / "src" / "gateware" / "facedancer" / "top.py"

ASSIGN_RE = re.compile(r"^\s*self\.(?P<name>[a-zA-Z0-9_]+)\s*=\s*(?P<value>.+?)\s*$")


def main() -> int:
    if not TOP.exists():
        print(f"Missing source file: {TOP}")
        return 1

    text = TOP.read_text(encoding="utf-8")
    constants = {}

    for line in text.splitlines():
        match = ASSIGN_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        if not (
            name.endswith("_base")
            or name.endswith("_size")
            or name.endswith("_irq")
            or name in {"firmware_start"}
        ):
            continue
        constants[name] = match.group("value")

    payload = {
        "source": str(TOP),
        "count": len(constants),
        "constants": constants,
    }

    out_file = OUT / "soc_baseline.json"
    out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="ascii")
    print(f"Wrote {out_file} with {len(constants)} constants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
