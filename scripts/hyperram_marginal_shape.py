#!/usr/bin/env python3
#
# What a marginal HyperRAM cell actually lost, and how that pins the mechanism.
# SPDX-License-Identifier: BSD-3-Clause

"""The words a variable-latency burst loses are exactly L, the latency code.

`hyperram_matrix_diff.py` records one row per failing cell: errors, words,
control, verdict. A cell that loses ~one burst of `BURST_WORDS` is *marginal* --
it worked for every other burst, so the failure is a rate and not a setting.

This groups the marginal cells by latency code and reports the error count. The
result is the mechanism:

    errors == BURST_WORDS - L   for the code's own L, cell by cell

which is what a host that waits **2L against a device that took L** produces. It
enters the data phase L clocks in, so it never sees the first L words; the
comparator makes `BURST_WORDS - L` comparisons and every one is shifted. A host
that waits L against a device on 2L cannot produce this -- it idles until the
strobe and reads the burst correctly. See #338, and the negative control in
`gateware/probes/hyperram/vendor_model_tb.sv`.

Usage:

    scripts/hyperram_marginal_shape.py                       # every run in results/hyperram
    scripts/hyperram_marginal_shape.py results/hyperram/*.json

Log: `tmp/logs/hyperram_marginal_shape.log`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "hyperram"
LOGFILE = ROOT / "tmp" / "logs" / "hyperram_marginal_shape.log"

# CR0[7:4] -> initial latency L in CK. Sparse: 3..13 are reserved and the part
# keeps the power-on code, which is why a reserved code can still work.
LATENCY_CK_BY_CODE = {14: 3, 15: 4, 0: 5, 1: 6, 2: 7}
POWER_ON_CODE = 2

# `hyperram_ceiling_top.BURST_WORDS`. Set by tCSM, not by preference.
BURST_WORDS = 128

# A cell is MARGINAL when it lost of the order of one burst and no more. The
# window is one burst either side: below it the cell is clean, above it the cell
# failed for a reason that is not a lost burst (16378 and 16384 are the two
# whole-cell failures this rig produces).
MARGINAL_LO = BURST_WORDS // 2
MARGINAL_HI = BURST_WORDS * 2

log = logging.getLogger("marginal-shape")


def setup_logging() -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def latency_of(code: int) -> int:
    """The L the PART uses. A reserved code keeps the power-on one."""
    return LATENCY_CK_BY_CODE.get(code, LATENCY_CK_BY_CODE[POWER_ON_CODE])


def report(paths: list[Path]) -> int:
    shape: Counter = Counter()
    per_run = []
    for path in paths:
        run = json.loads(path.read_text())
        failures = run["failures"]
        marginal = {k: v for k, v in failures.items()
                    if MARGINAL_LO < v[0] < MARGINAL_HI}
        modes = Counter(k.split(",")[1] for k in marginal)
        per_run.append((path.name, run["passes_per_cell"], run["ck_mhz"],
                        len(marginal), modes["fix"]))
        for key, value in marginal.items():
            code = int(key.split(",")[0])
            shape[(code, latency_of(code), value[0])] += 1

    log.info("%-44s %6s %8s %9s %10s", "run", "passes", "CK MHz", "marginal", "fix cells")
    for name, passes, ck, n, fix in per_run:
        log.info("%-44s %6d %8s %9d %10d", name, passes, ck, n, fix)

    log.info("")
    log.info("%-6s %4s %8s %6s   %s", "code", "L", "errors", "cells", "errors == words - L ?")
    bad = 0
    for (code, lat, errors), n in sorted(shape.items()):
        matches = errors == BURST_WORDS - lat
        whole = errors == BURST_WORDS
        verdict = "yes" if matches else ("whole burst" if whole else "NO")
        if not (matches or whole):
            bad += 1
        log.info("%-6d %4d %8d %6d   %s", code, lat, errors, n, verdict)

    if bad:
        log.error("%d error counts fit neither `words - L` nor a whole burst -- the "
                  "mechanism below does not explain them", bad)
        return 1
    log.info("")
    log.info("Every marginal cell lost either the whole burst or exactly `words - L` "
             "of it. That is a host on 2L against a device on L: it enters the data "
             "phase L clocks in and never sees the first L words. The other "
             "direction cannot produce it -- see #338.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", type=Path,
                    help="matrix JSON files (default: every one in results/hyperram)")
    args = ap.parse_args()

    setup_logging()
    paths = args.runs or sorted(RESULTS.glob("*.json"))
    if not paths:
        raise SystemExit(f"no matrix runs found under {RESULTS}")
    return report(paths)


if __name__ == "__main__":
    sys.exit(main())
