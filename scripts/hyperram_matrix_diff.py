#!/usr/bin/env python3
#
# Record a matrix run as a tracked artifact, and diff it against another. #226, #311.
# SPDX-License-Identifier: BSD-3-Clause

"""Run the 4096-cell matrix, save it under `results/hyperram/`, and diff runs.

    ./scripts/hyperram_matrix_diff.py --label baseline
    ./scripts/hyperram_matrix_diff.py --label ck-drive-16
    ./scripts/hyperram_matrix_diff.py --diff            # the last two
    ./scripts/hyperram_matrix_diff.py --diff a.json b.json
    ./scripts/hyperram_matrix_diff.py --list

## Why a diff and not a verdict

A cell that passes once is not a cell that passes. The audit records 128 clean
rows followed by a wedge on reconfigure, and a single run scores both the same.
**Two identical runs that disagree have found a marginal cell**, which is the
most interesting thing the matrix can produce and the one thing a single run
cannot report.

The same mechanism carries the #311 workflow: patch one pin attribute into a
BUILT bitstream (`hyperram_pin_patch.py`), reconfigure, rerun, diff. Cells that
move are that attribute's effect, isolated -- no rebuild, no confounded axes.

## Why these are committed

They are MEASUREMENTS, not derived artifacts: recreating one needs the board, a
build and the same part at the same temperature. Every file carries the commit,
the CK, the pass count and the timestamp, so a stale result is visibly stale
rather than quietly wrong.

Runs go to `results/hyperram/<YYYYMMDD-HHMMSS>-<label>.json`; the console log to
`tmp/logs/hyperram-matrix-diff.log`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

RESULTS = ROOT / "results" / "hyperram"
LOG = ROOT / "tmp" / "logs" / "hyperram-matrix-diff.log"

# `bist all` prints only FAILING cells, plus a summary. The log timestamp lands
# mid-row, between the mode and the drive code.
ROW = re.compile(r"^\s*(\d+)\s+(fix|var)\s+\S+\s+(\d+)\s+(dif|se)\s+(\d+)"
                 r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(PASS|fail)", re.M)
SUMMARY = re.compile(r"(\d+)\s+pass,\s*(\d+)\s+fail,\s*(\d+)\s+no result of\s*(\d+)")
RUNG = re.compile(r"^\s*rung\s+(\d+)\s+([\d.]+)\s+MHz(\s+<- live)?", re.M)


def emit(line=""):
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def git(*args):
    try:
        return subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None


class Board:
    def __init__(self):
        self.link = soc_shell.Link.open(None)
        self.link.write(b"\r")
        self.link.read_until_prompt(budget_s=3)

    def send(self, command, budget=8):
        self.link.write(command.encode() + b"\r")
        return self.link.read_until_prompt(budget_s=budget).decode("ascii", "replace")

    def close(self):
        self.link.close()


def record(label, passes, rung):
    """One matrix run, with everything needed to judge it later."""
    board = Board()
    try:
        ck = board.send(f"bist ck {rung}", 4)
        rungs = {int(n): float(mhz) for n, mhz, _ in RUNG.findall(ck)}
        if not rungs:
            raise SystemExit(f"no CK rungs reported; is `bist ck` in this build?\n{ck}")
        if "NOT LOCKED" in ck:
            raise SystemExit("PLL not locked -- no measurement here means anything")

        emit(f"running the matrix at {rungs.get(rung)} MHz, {passes} passes/cell")
        text = board.send(f"bist all {passes}", 300)
    finally:
        board.close()

    cells = {}
    for lat, mode, drive, clk, sel, errors, words, control, verdict in ROW.findall(text):
        # Only failures are printed, so the key set IS the failure set.
        cells[f"{lat},{mode},{drive},{clk},{sel}"] = [
            int(errors), int(words), int(control), verdict]

    totals = SUMMARY.search(text)
    if not totals:
        raise SystemExit(
            "no summary line -- the run did not finish, and a partial matrix "
            f"must not be saved as a complete one. Tail was:\n{text[-800:]}")
    passed, failed, noresult, total = (int(g) for g in totals.groups())
    if len(cells) != failed:
        emit(f"  WARNING: {failed} failures summarised but {len(cells)} parsed. "
             "Saving anyway; the diff will be wrong by the difference.")

    run = {
        "recorded": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "label": label,
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "ck_mhz": rungs.get(rung),
        "rung": rung,
        "rungs": rungs,
        "passes_per_cell": passes,
        "summary": {"pass": passed, "fail": failed,
                    "no_result": noresult, "total": total},
        # Failing cells only. A cell absent from here passed, which is why the
        # summary is stored beside it -- the complement is only trustworthy if
        # the totals agree.
        "failures": cells,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"{stamp}-{label}.json"
    path.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    emit(f"  {passed} pass, {failed} fail, {noresult} no result of {total}")
    emit(f"  -> {path.relative_to(ROOT)}")
    return path


def load(path):
    return json.loads(Path(path).read_text())


def diff(first, second):
    a, b = load(first), load(second)
    emit(f"\nA  {Path(first).name}")
    emit(f"   {a['recorded']}  CK {a['ck_mhz']} MHz  {a['passes_per_cell']} passes"
         f"  commit {(a['commit'] or '?')[:8]}{'  DIRTY' if a['dirty'] else ''}")
    emit(f"B  {Path(second).name}")
    emit(f"   {b['recorded']}  CK {b['ck_mhz']} MHz  {b['passes_per_cell']} passes"
         f"  commit {(b['commit'] or '?')[:8]}{'  DIRTY' if b['dirty'] else ''}")

    if a["ck_mhz"] != b["ck_mhz"] or a["passes_per_cell"] != b["passes_per_cell"]:
        emit("\n  NOTE: these runs differ in CK or pass count, so a moved cell is "
             "not necessarily marginal -- it may just be a different experiment.")

    fa, fb = set(a["failures"]), set(b["failures"])
    healed, broke = sorted(fa - fb), sorted(fb - fa)

    emit(f"\n  A fails {len(fa)}, B fails {len(b['failures'])}")
    if not healed and not broke:
        emit("  IDENTICAL failure sets -- nothing moved between these two runs")
        return 0

    emit(f"  {len(healed) + len(broke)} cell(s) changed verdict. Format: "
         "lat,mode,drive,clk,sel")
    for key in healed:
        emit(f"    fail -> PASS  {key}")
    for key in broke:
        emit(f"    PASS -> fail  {key}   errors {b['failures'][key][0]}")
    emit("\n  A cell that changed under identical conditions is MARGINAL, and a "
         "single run would have scored it as a verdict.")
    return len(healed) + len(broke)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run",
                        help="what this run is testing, e.g. `ck-drive-16`")
    parser.add_argument("--passes", type=int, default=8,
                        help="passes per cell (default 8; 4096 cells)")
    parser.add_argument("--rung", type=int, default=0, help="CK rung to select")
    parser.add_argument("--repeat", type=int, default=1,
                        help="record N runs back to back and diff them, which is "
                             "the marginality check")
    parser.add_argument("--diff", nargs="*", metavar="FILE",
                        help="diff two saved runs, or the last two if given none")
    parser.add_argument("--list", action="store_true", help="list saved runs")
    args = parser.parse_args()

    saved = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []

    if args.list:
        for path in saved:
            run = load(path)
            emit(f"{path.name:44s} CK {run['ck_mhz']!s:>8} MHz  "
                 f"{run['summary']['fail']:5d} fail  {run['recorded']}")
        return 0

    if args.diff is not None:
        pair = args.diff or [str(p) for p in saved[-2:]]
        if len(pair) != 2:
            raise SystemExit(f"need two runs to diff; found {len(pair)}")
        return 1 if diff(*pair) else 0

    written = [record(args.label, args.passes, args.rung)
               for _ in range(args.repeat)]
    if len(written) > 1:
        moved = 0
        for first, second in zip(written, written[1:]):
            moved += diff(str(first), str(second))
        emit(f"\n{moved} cell(s) moved across {len(written)} identical runs")
        # A MOVED CELL IS A FAILING GATE. This returned 0 unconditionally, so
        # `hyperram_verify.py` scored the step PASS and printed "no cell moved
        # between two identical runs" directly beneath 482 printed moved cells.
        # The recording succeeded; the measurement did not, and the exit code
        # reported the recording. (#351)
        return 1 if moved else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
