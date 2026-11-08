#!/usr/bin/env python3
#
# Utilisation of finished variant builds, from nextpnr's own report.
# SPDX-License-Identifier: BSD-3-Clause

"""
Print COMB, FF, DP16KD and LUTRAM (TRELLIS_RAMW) of each variant build under tmp/awto_soc/build.

    ./scripts/soc/soc_util_report.py                 # every variant dir
    ./scripts/soc/soc_util_report.py ck180-sync60    # named dirs

- Reads the LAST `Device utilisation` block of `top.tim` (it accumulates across runs).
- Cells only; no timing is read or reported.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BUILD = ROOT / "tmp" / "awto_soc" / "build"
KEYS = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "TRELLIS_RAMW", "MULT18X18D")


def utilisation(path):
    text = path.read_text(errors="replace")
    block = text.split("Device utilisation:")[-1].split("Info: Placed")[0]
    out = {}
    for key in KEYS:
        match = re.search(rf"{key}:\s+(\d+)/\s*(\d+)", block)
        out[key] = (int(match.group(1)), int(match.group(2))) if match else None
    return out


def main(names):
    names = names or sorted(p.name for p in BUILD.iterdir() if (p / "top.tim").exists())
    for name in names:
        tim = BUILD / name / "top.tim"
        if not tim.exists():
            print(f"{name:<44} no top.tim")
            continue
        util = utilisation(tim)
        print(f"{name:<44} " + "  ".join(
            f"{key.replace('TRELLIS_', '')}={util[key][0]}" if util[key] else f"{key}=?"
            for key in KEYS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
