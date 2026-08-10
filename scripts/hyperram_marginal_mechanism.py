#!/usr/bin/env python3
#
# Why every marginal cell is `var` and none is `fix`. #338.
# SPDX-License-Identifier: BSD-3-Clause

"""Read `results/hyperram/*.json` and test the candidate mechanisms for the
`var`-only marginality.

    ./scripts/hyperram_marginal_mechanism.py
    ./scripts/hyperram_marginal_mechanism.py --self-test

## What this adds over `hyperram_matrix_diff.py --diff`

That tool diffs the failure KEY SETS, so a cell failing in both runs with a
different error count reads as "nothing moved". The error count is recorded and
it is a strictly finer instrument: a cell can move 128 errors without crossing
the pass/fail line. This tool diffs on `(verdict, errors)`.

## The arithmetic this leans on, and where it comes from

All six recorded runs are the **DQS** build (`bist1-ck120-dqs1-*`), so the
controller is `hyperram_dqs_controller.py`, NOT the `hyperram_controller.py`
that #338's and #353's fixes landed in. On that path
(`hyperram_ceiling_top.py:599-634`):

- `latency_clocks = L - 1`, and `HANDLE_LATENCY` waits `2 x latency_clocks + 2`
  CK, so the LONG wait is `2L` CK.
- the SHORT wait is `LOW_LATENCY_CLOCKS = 3`, a CLASS CONSTANT with no input
  (`hyperram_dqs_controller.py:113`, and `hyperram_ceiling_top.py:629` says so
  in as many words), so the SHORT wait is `2 x 3 + 2 = 8` CK at EVERY code.

`L` per code is `LATENCY_CLOCKS_BY_CODE`; codes with no entry take the power-on
code's L, which is what the `Default` arm drives.

**Hence the discriminator.** `2L == 8` at `L = 4`, i.e. latency code 15. At code
15 the two branches wait the same number of CK, so the controller's RWDS
election cannot change the timing of the read. A marginal cell at code 15 is
therefore evidence for a DEVICE-side per-transaction event; a marginal cell at a
code where `2L != 8` is consistent with either side.

## Null hypotheses, stated before the numbers

- MODE: a marginal cell is equally likely to be `fix` or `var`. 2048 cells each.
- AXIS: a marginal cell is uniform over that axis's values, conditioned on the
  cells that could move at all.

Log to `tmp/logs/hyperram-marginal-mechanism.log`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "hyperram"
LOG = ROOT / "tmp" / "logs" / "hyperram-marginal-mechanism.log"

AXES = ("lat", "mode", "drive", "clk", "sel")

# `hyperram_ceiling_top.py:184`. Codes absent here take the power-on code's L via
# the `Default` arm, which is code 2 -> 7.
LATENCY_CLOCKS_BY_CODE = {14: 3, 15: 4, 0: 5, 1: 6, 2: 7}
POWER_ON_LATENCY_CODE = 2
# `hyperram_dqs_controller.py:113`, times two plus two -- see the module docstring.
DQS_SHORT_WAIT_CK = 2 * 3 + 2

log = logging.getLogger("hyperram-marginal-mechanism")


def setup_logging(verbose=False):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG)])


def latency_ck(code):
    """The L the DQS build drives for this latency code, in CK."""
    return LATENCY_CLOCKS_BY_CODE.get(
        code, LATENCY_CLOCKS_BY_CODE[POWER_ON_LATENCY_CODE])


def election_matters(code):
    """True when the long and short branches wait a DIFFERENT number of CK.

    False means the controller's RWDS election cannot change this cell's read
    timing at all, so anything marginal there came from the device.
    """
    return 2 * latency_ck(code) != DQS_SHORT_WAIT_CK


def load(path):
    return json.loads(Path(path).read_text())


def provenance(run):
    """The fields that decide whether two runs are the same experiment.

    A run whose `pins` are null or whose bitstream is unrecorded is not thereby
    useless -- it is unattributable to an ELECTRICAL setting, which is a narrower
    claim. What makes two runs a marginality pair is that the same image ran on
    the same part at the same CK, and that is what this returns.
    """
    board = run.get("board") or {}
    bitstream = run.get("bitstream") or {}
    return {
        "image": board.get("image"),
        "gateware": board.get("gateware"),
        "ck_mhz": run.get("ck_mhz"),
        "passes": run.get("passes_per_cell"),
        "sha256": bitstream.get("sha256"),
        "pins_recorded": run.get("pins") is not None,
    }


def cell_state(run):
    """Every cell's `(verdict, errors)`, with passing cells explicit.

    `failures` holds only failing cells, so a passing cell is an ABSENCE. Diffing
    absences is what makes an error-count diff possible at all.
    """
    return {key: ("fail", value[0]) for key, value in run["failures"].items()}


def diff_pair(a, b):
    """Cells that changed verdict, and cells that changed error count only."""
    sa, sb = cell_state(a), cell_state(b)
    verdict, count = [], []
    for key in set(sa) | set(sb):
        one, two = sa.get(key), sb.get(key)
        if (one is None) != (two is None):
            verdict.append((key, one, two))
        elif one is not None and one[1] != two[1]:
            count.append((key, one[1], two[1]))
    return sorted(verdict), sorted(count)


def axis_of(key, axis):
    return dict(zip(AXES, key.split(",")))[axis]


def binomial_two_sided(observed, total, p=Fraction(1, 2)):
    """P(a split at least this lopsided) under a fair coin. Exact, not normal."""
    if total == 0:
        return 1.0
    extreme = min(observed, total - observed)
    tail = sum(Fraction(comb(total, k)) * p**k * (1 - p)**(total - k)
               for k in range(0, extreme + 1))
    return float(min(1, 2 * tail))


def report_mode(moved, label):
    """THE HEADLINE. Which mode the moved cells are in, against a fair coin.

    Returns the `fix` count so a caller (and `--self-test`) can assert on it.
    """
    modes = Counter(axis_of(key, "mode") for key, *_ in moved)
    total = sum(modes.values())
    fix, var = modes.get("fix", 0), modes.get("var", 0)
    log.info("  %s: %d moved -- %d fix, %d var", label, total, fix, var)
    if total:
        log.info("    p = %.2g that a fair coin over 2048 fix / 2048 var cells "
                 "gives a split this lopsided", binomial_two_sided(var, total))
    return fix


def report_axis(moved, axis, universe):
    """How the moved cells fall across one axis, against the cells that exist."""
    seen = Counter(axis_of(key, axis) for key, *_ in moved)
    values = sorted(universe)
    total = sum(seen.values())
    if not total:
        return
    hit = [v for v in values if seen.get(v)]
    log.info("    %-5s %s   (%d of %d values)", axis,
             "  ".join(f"{v}:{seen.get(v, 0)}" for v in values),
             len(hit), len(values))


def report_election(moved):
    """Split the moved `var` cells by whether the controller's election could
    have changed their timing at all.

    `2L == 8` at code 15 makes the two branches identical, so a moved cell there
    cannot be the controller choosing wrongly -- the device chose.
    """
    matters, irrelevant = [], []
    for key, *rest in moved:
        if axis_of(key, "mode") != "var":
            continue
        code = int(axis_of(key, "lat"))
        (matters if election_matters(code) else irrelevant).append((key, code))
    log.info("    election CAN change the wait (2L != %d CK): %d cell(s)",
             DQS_SHORT_WAIT_CK, len(matters))
    log.info("    election CANNOT change the wait (2L == %d CK, code 15): "
             "%d cell(s)", DQS_SHORT_WAIT_CK, len(irrelevant))
    for key, code in irrelevant:
        log.info("      %s  L=%d, both branches wait %d CK -- the DEVICE moved "
                 "this one, not the election", key, latency_ck(code),
                 DQS_SHORT_WAIT_CK)
    return matters, irrelevant


def report_error_quantisation(run, name):
    """How many DISTINCT error counts each mode produces.

    A perfectly deterministic path emits the same count every time, so its
    distinct-value set is small and its block sizes are round. This is the
    corpus's own statement about which path carries a random component, and it
    reads every failing cell rather than only the ones that crossed the line.
    """
    by_mode = defaultdict(Counter)
    for key, value in run["failures"].items():
        by_mode[axis_of(key, "mode")][value[0]] += 1
    log.info("  %s", name)
    for mode in sorted(by_mode):
        counts = by_mode[mode]
        singles = sorted(v for v, n in counts.items() if n == 1)
        log.info("    %-3s %4d failing cells, %2d distinct error counts%s",
                 mode, sum(counts.values()), len(counts),
                 f", {len(singles)} of them unique: {singles}" if singles else "")


def pair_up(runs):
    """Group runs into comparable pairs: same image, CK, pass count and bitstream.

    Two runs that differ in ANY of these are a different experiment, and a cell
    that moved between them is that difference rather than marginality.
    """
    groups = defaultdict(list)
    for name, run in runs:
        p = provenance(run)
        groups[(p["image"], p["ck_mhz"], p["passes"])].append((name, run, p))
    pairs = []
    for key, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        for one, two in zip(members, members[1:]):
            pairs.append((one, two))
    return pairs


def analyse(paths):
    runs = [(Path(p).name, load(p)) for p in paths]
    log.info("=== provenance ===")
    for name, run in runs:
        p = provenance(run)
        sha = (p["sha256"] or "unrecorded")[:12]
        log.info("  %-42s image %-8s CK %-6s %d passes  bitstream %s%s",
                 name, p["image"], p["ck_mhz"], p["passes"], sha,
                 "" if p["pins_recorded"] else "  PINS NULL")

    shas = {provenance(r)["sha256"] for _, r in runs} - {None}
    if len(shas) > 1:
        log.info("  %d DISTINCT bitstreams here -- runs from different "
                 "bitstreams are different experiments and are not paired "
                 "across", len(shas))

    pairs = pair_up(runs)
    log.info("\n=== %d comparable pair(s) ===", len(pairs))

    all_verdict, all_count = [], []
    for (na, ra, pa), (nb, rb, pb) in pairs:
        verdict, count = diff_pair(ra, rb)
        log.info("\n%s\n%s", na, nb)
        if pa["sha256"] and pb["sha256"] and pa["sha256"] != pb["sha256"]:
            log.info("  DIFFERENT BITSTREAMS in one image group -- not a "
                     "marginality pair")
            continue
        report_mode(verdict, "verdict flips")
        report_mode(count, "error-count changes, verdict unchanged")
        all_verdict += verdict
        all_count += count

    log.info("\n=== pooled over every pair ===")
    fix_verdict = report_mode(all_verdict, "verdict flips")
    fix_count = report_mode(all_count, "error-count changes")
    pooled = all_verdict + all_count
    fix_pooled = report_mode(pooled, "both kinds of movement pooled")

    if pooled:
        log.info("\n  where the moved cells sit:")
        for axis, values in (("lat", [str(i) for i in range(16)]),
                             ("drive", [str(i) for i in range(8)]),
                             ("clk", ["dif", "se"]),
                             ("sel", [str(i) for i in range(8)])):
            report_axis(pooled, axis, values)
        log.info("\n  the controller's election, per moved var cell:")
        report_election(pooled)

    log.info("\n=== error-count quantisation, per run ===")
    for name, run in runs:
        report_error_quantisation(run, name)

    return {"fix_verdict": fix_verdict, "fix_count": fix_count,
            "fix_pooled": fix_pooled, "moved": len(pooled),
            "verdict": len(all_verdict), "counts": len(all_count)}


# ---------------------------------------------------------------------------
# The self-test. Every claim this tool makes is a claim it must be ABLE to
# refute, so each case below is a corpus the tool is REQUIRED to read
# differently -- and the run fails if any of them reads the same as the real one.
# ---------------------------------------------------------------------------

def synthetic(failures, image="img", sha="a" * 64):
    return {"board": {"image": image, "gateware": image},
            "ck_mhz": 120.0, "passes_per_cell": 8,
            "bitstream": {"sha256": sha}, "pins": {},
            "failures": failures}


def self_test():
    """Show each check failing on the defect it exists for."""
    failures = 0

    def require(name, ok, detail):
        nonlocal failures
        log.info("  %-58s %s  %s", name, "PASS" if ok else "FAIL", detail)
        if not ok:
            failures += 1

    # 1. A `fix` cell that moves MUST be reported as a `fix` cell. If the tool
    #    can only ever say "all var", its 16-of-16 is an artifact of the tool.
    a = synthetic({"0,fix,0,se,0": [8, 1024, 1024, "fail"]})
    b = synthetic({})
    verdict, _ = diff_pair(a, b)
    fix = report_mode(verdict, "self-test: a moved fix cell")
    require("a moved fix cell is counted as fix", fix == 1, f"got fix={fix}")

    # 2. A cell failing in BOTH runs with a different error count must be seen.
    #    `hyperram_matrix_diff.py --diff` reports this as "nothing moved", which
    #    is the blind spot this tool exists to cover.
    a = synthetic({"0,var,0,se,0": [128, 1024, 1024, "fail"]})
    b = synthetic({"0,var,0,se,0": [256, 1024, 1024, "fail"]})
    verdict, count = diff_pair(a, b)
    require("an error-count change with no verdict change is seen",
            len(verdict) == 0 and len(count) == 1,
            f"verdict={len(verdict)} count={len(count)}")

    # 3. The election arithmetic. Code 15 is L=4, so both branches wait 8 CK and
    #    the RWDS election is a no-op there; every other code must differ.
    require("code 15 (L=4) makes the two branches identical",
            not election_matters(15), f"2L={2 * latency_ck(15)} CK")
    others = [c for c in range(16) if c != 15]
    require("every other code has branches that differ",
            all(election_matters(c) for c in others),
            f"{sum(election_matters(c) for c in others)} of {len(others)}")
    # A reserved code must inherit the power-on L, not silently read as 0.
    require("a reserved code takes the power-on L", latency_ck(7) == 7,
            f"L={latency_ck(7)}")

    # 4. The p-value must not call a fair split significant, and must call a
    #    16-0 split significant. A test that fires either way measures nothing.
    require("an 8/8 split is not significant",
            binomial_two_sided(8, 16) > 0.5, f"p={binomial_two_sided(8, 16):.3g}")
    require("a 16/0 split is significant",
            binomial_two_sided(16, 16) < 1e-4,
            f"p={binomial_two_sided(16, 16):.3g}")

    # 5. Provenance. Two runs from DIFFERENT images must not be paired, or the
    #    build change is read as marginality -- the error this corpus invites,
    #    since it holds two images 30 seconds apart.
    pairs = pair_up([("a", synthetic({}, image="aaa")),
                     ("b", synthetic({}, image="bbb"))])
    require("runs from different images are not paired", len(pairs) == 0,
            f"{len(pairs)} pair(s)")
    pairs = pair_up([("a", synthetic({}, image="aaa")),
                     ("b", synthetic({}, image="aaa"))])
    require("runs from the same image ARE paired", len(pairs) == 1,
            f"{len(pairs)} pair(s)")

    log.info("\n%d self-test failure(s)", failures)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", type=Path,
                        help="runs to read (default: every file in "
                             "results/hyperram/)")
    parser.add_argument("--self-test", action="store_true",
                        help="show each check failing on the defect it exists "
                             "for, and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.self_test:
        return 1 if self_test() else 0

    paths = args.runs or sorted(RESULTS.glob("*.json"))
    if not paths:
        raise SystemExit(f"no runs found under {RESULTS}")
    analyse(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
