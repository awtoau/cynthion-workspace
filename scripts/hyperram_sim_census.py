#!/usr/bin/env python3
#
# Every assertion in `soc_hyperram_sim.py` is classified, and stays classified.
# SPDX-License-Identifier: BSD-3-Clause

"""The #346 split, enforced instead of described.

`docs/chips/hyperram/sim-audit.md` classifies every assertion in
`scripts/soc_hyperram_sim.py` as one of:

    caller     stays -- about the CONTROLLER or the SoC above it
    device     moved onto the twin, where the reference is not our own guess
    redundant  deleted, already checked elsewhere
    wrong      deleted, encoded a belief since refuted

An assertion with no home is what made the previous attempt useless, so this
checks the correspondence both ways:

  * every assertion in the file appears in the table, classified `caller`
  * every `caller` row still exists in the file
  * every `device`/`redundant`/`wrong` row is GONE from the file -- a re-added
    conformance check is caught here rather than read as coverage

    scripts/hyperram_sim_census.py            # exit 0 if the two agree
    scripts/hyperram_sim_census.py --list     # print the classification

Log: `tmp/logs/hyperram_sim_census.log`.
"""

from __future__ import annotations

import argparse
import ast
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "scripts" / "soc_hyperram_sim.py"
AUDIT = ROOT / "docs" / "chips" / "hyperram" / "sim-audit.md"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_sim_census.log"

KEPT = "caller"
GONE = ("device", "redundant", "wrong")

log = logging.getLogger("hyperram-sim-census")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"),
                  logging.StreamHandler(sys.stdout)],
    )


def assertions(path: Path) -> list[tuple[str, str]]:
    """(section, label) for every assertion site, in file order.

    `check_completed` is one assertion of its own -- it is the liveness check the
    file had none of before #316, and counting only `checks.check` would miss 15.
    """
    found = []
    for node in ast.parse(path.read_text()).body:
        if not (isinstance(node, ast.FunctionDef)
                and node.name.startswith("section_")):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            label = None
            if isinstance(sub.func, ast.Attribute) and sub.func.attr == "check":
                arg = sub.args[0]
                label = arg.value if isinstance(arg, ast.Constant) else "<f-string>"
            elif isinstance(sub.func, ast.Name) and sub.func.id == "check_completed":
                arg = sub.args[2]
                label = ("[completed] " + arg.value if isinstance(arg, ast.Constant)
                         else "<expr>")
            if label is not None:
                found.append((node.name, label, sub.lineno))
    found.sort(key=lambda row: row[2])
    return [(section, label) for section, label, _ in found]


def table(path: Path) -> list[tuple[str, str, str, str]]:
    """The classification table: (section, assertion, class, where)."""
    rows = []
    for line in path.read_text().splitlines():
        if not line.startswith("| `section_"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        if len(cells) != 4:
            raise SystemExit(f"{path.name}: not four columns: {line}")
        section, label, kind, where = cells
        rows.append((section.strip("`"), label, kind, where))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the classification")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)

    rows = table(AUDIT)
    if not rows:
        raise SystemExit(f"{AUDIT.name} carries no classification table -- fix "
                         f"this parser before believing anything it reports")
    in_file = assertions(SIM)

    counts = {}
    for _, _, kind, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    for kind in sorted(counts):
        log.info("%-10s %3d", kind, counts[kind])
    log.info("%-10s %3d in %s", "total", len(rows), AUDIT.name)
    log.info("%-10s %3d in %s", "live", len(in_file), SIM.name)

    if args.list:
        for section, label, kind, where in rows:
            log.info("  %-9s %-32s %s%s", kind, section, label,
                     "" if kind == KEPT else f"   -> {where}")

    unknown = {kind for _, _, kind, _ in rows} - {KEPT, *GONE}
    failures = [f"unknown class {kind!r} in the table" for kind in sorted(unknown)]

    classified = {(section, label) for section, label, _, _ in rows}
    kept = {(section, label) for section, label, kind, _ in rows if kind == KEPT}
    live = set(in_file)

    for section, label in sorted(live - classified):
        failures.append(f"UNCLASSIFIED: {section} {label!r} is asserted and appears "
                        f"in no row of {AUDIT.name}")
    for section, label in sorted(kept - live):
        failures.append(f"MISSING: {section} {label!r} is classified `caller` and "
                        f"no longer exists -- reclassify it or restore it")
    for section, label, kind, where in rows:
        if kind in GONE and (section, label) in live:
            failures.append(f"RESURRECTED: {section} {label!r} was classified "
                            f"`{kind}` ({where}) and is asserted again")

    if len(in_file) != len(live):
        failures.append(f"{len(in_file) - len(live)} duplicate assertion labels in "
                        f"{SIM.name}; the census cannot tell them apart")

    for failure in failures:
        log.error("%s", failure)
    if failures:
        log.error("FAIL -- %d assertion(s) out of step with the audit", len(failures))
        return 1
    log.info("PASS -- every assertion in %s is classified, and every classification "
             "matches what the file does", SIM.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
