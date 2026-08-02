#!/usr/bin/env python3
"""Extract the PLAIN-TEXT ECP5 device/pinout tables from Diamond.

These live in the real ECP5 trees (sa5p00 / sa5p00m / sa5p00g), NOT in ep5c00
(which is LatticeECP3 -- see diamond_spd_parse.py for the tree-map evidence).

Three plain-text families, no reverse engineering needed:

  *.con  tab/space separated attribute records, one per site:
           IO       ID X Y SITE PIN_NAME BANK SIDE DIFF_PAIR TRUE_LVDS MIPI DQS
           IOLOGIC  ID X Y SITE IO DQS DQS_GROUP
         plus PLL/DCU/EBR/DSP/config site rows.  This is a full package pinout
         WITH die coordinates, bank numbers, LVDS pairing and DQS grouping.

  *.fil  per-package filter/annotation list.
  *.csv  SERDES/DCU attribute + pin tables (ecp4u_*.csv, despite the name these
         sit in the ECP5 tree and describe the ECP5 DCU).

Output: tmp/diamond-mine/con/<part>_<pkg>.json + a coverage summary.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
OUT_DIR = WORKTREE / "tmp" / "diamond-mine" / "con"
LOG_DIR = WORKTREE / "tmp" / "logs"
DIAMOND = Path.home() / "lscc" / "diamond" / "3.14" / "ispfpga"
TREES = {
    "sa5p00": "ECP5U (LFE5U-*) <- Cynthion",
    "sa5p00m": "ECP5UM (LFE5UM-*)",
    "sa5p00g": "ECP5UM5G (LFE5UM5G-*)",
}

ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_con(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        kind = line.split(None, 1)[0]
        attrs = dict(ATTR_RE.findall(line))
        if attrs:
            attrs["_kind"] = kind
            rows.append(attrs)
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "diamond_ecp5_con_extract.log", mode="w"),
                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("con")

    summary = {}
    for tree, desc in TREES.items():
        d = DIAMOND / tree / "data"
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.con")):
            rows = parse_con(p)
            kinds = collections.Counter(r["_kind"] for r in rows)
            ios = [r for r in rows if r["_kind"] == "IO"]
            banks = sorted(set(r.get("BANK", "?") for r in ios), key=str)
            lvds = sum(1 for r in ios if r.get("TRUE_LVDS") == "TRUE")
            mipi = sum(1 for r in ios if r.get("MIPI") == "true")
            dqs = sorted(set(r.get("DQS_GROUP") for r in rows if r.get("DQS_GROUP")))
            log.info("%-9s %-32s rows=%-5d IO=%-4d banks=%-14s trueLVDS=%-4d MIPI=%-4d dqsgrp=%d",
                     tree, p.name, len(rows), len(ios), ",".join(banks), lvds, mipi, len(dqs))
            (OUT_DIR / f"{p.stem}.json").write_text(json.dumps(
                {"tree": tree, "tree_desc": desc, "file": str(p),
                 "row_kinds": dict(kinds), "rows": rows}, indent=1))
            summary[p.stem] = {
                "tree": tree, "tree_desc": desc,
                "rows": len(rows), "row_kinds": dict(kinds),
                "io_count": len(ios), "banks": banks,
                "true_lvds_pairs": lvds, "mipi_capable": mipi,
                "dqs_groups": len(dqs),
                "attributes_seen": sorted({k for r in rows for k in r if k != "_kind"}),
            }

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1))
    log.info("")
    log.info("extracted %d packages -> %s", len(summary), OUT_DIR)
    if summary:
        any_key = next(iter(summary))
        log.info("attributes available per row: %s", summary[any_key]["attributes_seen"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
