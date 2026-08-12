#!/usr/bin/env python3
#
# Read a gateware build log line by line, because the summary hides the cost.
# SPDX-License-Identifier: BSD-3-Clause

"""
Audit a Yosys/nextpnr build log for what it costs and what it quietly did.

    ./scripts/build_log_audit.py                      # newest build under tmp/awto_soc
    ./scripts/build_log_audit.py <path/to/top.rpt>
    ./scripts/build_log_audit.py --json

Exit 1 when something crosses a threshold or a real warning appears, so a build
step can gate on it.

## Why

The die is at ~65% LUT4 and **52 of 56 EBR**, and #432's closure gate fails on
routing congestion rather than logic depth (2.46 ns logic against 18.02 ns
routing). At that point the interesting facts are in the log, not the summary:

  * memory that fell back to `TRELLIS_DPR16X4` instead of a `DP16KD` -- LUTRAM
    costs LUT4s, and this design has 211 of them
  * `MULT18X18D` inferred where nobody asked for a multiplier
  * warnings that are not the ~1,700 benign `No latch inferred` notes

## What "benign" means here

Yosys prints `No latch inferred` for every combinational process it proves is
complete -- it is the ABSENCE of a latch. Reporting those buries the real ones.
`ABC: Warning: The network is combinational.` is likewise routine. Both are
filtered, and `--all-warnings` shows everything.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "awto_soc" / "build"

# LFE5U-25F, which is the die under the LFE5U-12F marking on this board.
# EBR and LUT4 are the two that bind; IO is the package's.
DEVICE = {"LUT4": 24288, "DP16KD": 56, "TRELLIS_IO": 197}

# Fraction of a resource at which to complain. EBR is tighter because there is
# no graceful degradation: a memory that does not fit becomes LUTRAM silently.
THRESHOLD = {"LUT4": 0.80, "DP16KD": 0.85, "TRELLIS_IO": 0.90}

# Yosys's own cell census, `N   CELLTYPE`, inside the `=== top ===` block.
CELL = re.compile(r"^\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
TOP = re.compile(r"^=== (\S+) ===\s*$")

# Warnings worth reading. Yosys prints one `No latch inferred` per complete
# combinational process, so those are the absence of the problem.
BENIGN = (
    "No latch inferred",
    "ABC: Warning: The network is combinational.",
    "AIG with boxes has internal fanout",
)
WARNING = re.compile(r"\b(Warning|WARNING|ERROR|Error):")

# Things that cost LUT4s or DSPs without anyone asking. A count, not a match:
# `MULT18X18D` is legitimate for an RV32IM core and a surprise anywhere else.
NOTABLE = ("TRELLIS_DPR16X4", "MULT18X18D", "L6MUX21", "PFUMX")


def newest_log():
    """The most recently written `top.rpt` under the build tree."""
    reports = sorted(BUILD.rglob("top.rpt"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise SystemExit(f"no top.rpt under {BUILD.relative_to(ROOT)} -- build first")
    return reports[-1]


def cells(text):
    """The `=== top ===` census. Local and submodule counts are summed.

    Yosys prints one block per module; only `top` totals the design, and its
    submodule counts are the ones that carry LUT4 and TRELLIS_FF.
    """
    found, inside = {}, False
    for line in text.splitlines():
        module = TOP.match(line)
        if module:
            inside = module.group(1) == "top"
            continue
        if not inside:
            continue
        cell = CELL.match(line)
        if cell:
            found[cell.group(2)] = found.get(cell.group(2), 0) + int(cell.group(1))
    return found


def warnings(text, keep_all=False):
    """Every warning line, benign ones dropped unless asked for."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not WARNING.search(line):
            continue
        if not keep_all and any(noise in line for noise in BENIGN):
            continue
        found.append((number, line.strip()))
    return found


def audit(path, keep_all=False):
    text = path.read_text(errors="replace")
    counted = cells(text)
    # CCU2C is two LUT4 positions; DPR16X4 occupies two more. Neither appears
    # in the LUT4 count, and both are why "LUT4" understates occupancy.
    lut4_eq = (counted.get("LUT4", 0) + 2 * counted.get("CCU2C", 0)
               + 2 * counted.get("TRELLIS_DPR16X4", 0))
    return {
        "log": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "cells": counted,
        "lut4_equivalent": lut4_eq,
        "usage": {name: {"used": counted.get(name, 0), "of": limit,
                         "fraction": round(counted.get(name, 0) / limit, 3)}
                  for name, limit in DEVICE.items()},
        "lut4_eq_fraction": round(lut4_eq / DEVICE["LUT4"], 3),
        "notable": {name: counted[name] for name in NOTABLE if counted.get(name)},
        "warnings": warnings(text, keep_all),
    }


def report(result):
    """Print it, and return the exit code."""
    print(f"  {result['log']}")

    over = []
    for name, limit in DEVICE.items():
        used = result["usage"][name]
        mark = "  "
        if used["fraction"] >= THRESHOLD[name]:
            mark, _ = "!!", over.append(f"{name} at {used['fraction']:.0%}")
        print(f"{mark}{name:<16}{used['used']:>7} of {used['of']:<6}"
              f"{used['fraction']:>7.1%}")

    eq = result["lut4_eq_fraction"]
    mark = "!!" if eq >= THRESHOLD["LUT4"] else "  "
    if eq >= THRESHOLD["LUT4"]:
        over.append(f"LUT4-equivalent at {eq:.0%}")
    print(f"{mark}{'LUT4 equivalent':<16}{result['lut4_equivalent']:>7} of "
          f"{DEVICE['LUT4']:<6}{eq:>7.1%}   LUT4 + 2xCCU2C + 2xDPR16X4")

    if result["notable"]:
        print("\n  costs that are not LUT4 and are not free:")
        for name, count in sorted(result["notable"].items()):
            print(f"    {name:<20}{count:>6}")
        if result["cells"].get("TRELLIS_DPR16X4"):
            print("    ^ DPR16X4 is LUTRAM: memory that did NOT get an EBR")

    if result["warnings"]:
        print(f"\n  {len(result['warnings'])} warning(s), benign ones filtered:")
        for number, line in result["warnings"][:40]:
            print(f"    {number}: {line[:150]}")
    else:
        print("\n  no warnings beyond the benign ones")

    if over:
        print(f"\n  OVER THRESHOLD: {', '.join(over)}")
    return 1 if over or result["warnings"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("log", nargs="?", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all-warnings", action="store_true",
                        help="include the benign No-latch-inferred notes")
    args = parser.parse_args()

    result = audit(args.log or newest_log(), args.all_warnings)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    return report(result)


if __name__ == "__main__":
    sys.exit(main())
