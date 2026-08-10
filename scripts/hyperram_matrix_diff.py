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

## Why `--bitstream` is not optional in practice

A pin-attribute run and its control are the SAME commit, the same firmware and
the same CK -- everything this file used to record is identical between them, and
the one thing that differs was written nowhere. `--bitstream` unpacks the file
that was configured and stores what its HyperRAM PIOs are actually set to, so a
saved run says which electrical settings produced it. Without it the run is
saved with `pins: null` and a warning, because an unattributable measurement
should be visibly unattributable rather than quietly indistinguishable.

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
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hyperram_pin_patch  # noqa: E402
import soc_shell  # noqa: E402

RESULTS = ROOT / "results" / "hyperram"
LOG = ROOT / "tmp" / "logs" / "hyperram-matrix-diff.log"

# `bist all` prints only FAILING cells, plus a summary. The log timestamp lands
# mid-row, between the mode and the drive code.
ROW = re.compile(r"^\s*(\d+)\s+(fix|var)\s+\S+\s+(\d+)\s+(dif|se)\s+(\d+)"
                 r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(PASS|fail)", re.M)
SUMMARY = re.compile(r"(\d+)\s+pass,\s*(\d+)\s+fail,\s*(\d+)\s+no result of\s*(\d+)")
# `bist all` prints this only when its cell count overstates what it varied.
REPEATS = re.compile(r"(\d+)\s+DISTINCT configurations x (\d+) repeats")
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


AXES = ("lat", "mode", "drive", "clk", "sel")


def axis_spread(cells):
    """How the failures fall across each axis: `{axis: {value: count}}`.

    **A matrix whose failures are spread perfectly evenly over `sel` is not
    measuring a read window.** `sel` is the capture phase; if every phase scores
    the same, nothing is being captured and the verdict comes from somewhere
    other than the data path.

    That is not hypothetical. A whole sweep of pin patches was recorded against a
    build whose pass set was uniform 112/112 across `sel`, `drive` and `clk` --
    and whose failure set was IDENTICAL at 80 MHz and at 90 MHz. Every point
    scored "no effect", which was true of the rig and said nothing about the
    pins. Recorded per run so the next such corpus is visibly void on its face
    rather than after a day of it (#311).
    """
    spread = {axis: {} for axis in AXES}
    for key in cells:
        for axis, value in zip(AXES, key.split(",")):
            spread[axis][value] = spread[axis].get(value, 0) + 1
    return spread


def liveness(spread):
    """Which axes influenced the outcome, and which are flat. `{axis: bool}`."""
    return {axis: len(set(counts.values())) > 1 for axis, counts in spread.items()}


def pin_provenance(bitstream, build_dir):
    """What the configured bitstream sets the HyperRAM PIOs to, plus its identity.

    Read from the `.bit` rather than from the patch command that produced it: the
    file that reached the board is the only thing that cannot be out of step with
    what the board ran.
    """
    if bitstream is None:
        emit("  WARNING: no --bitstream, so this run does NOT record what the "
             "HyperRAM pins were set to. It cannot be compared against a pin "
             "patch. (#311)")
        return None, None
    path = Path(bitstream).resolve()
    data = path.read_bytes()
    identity = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data)}
    pins = hyperram_pin_patch.pin_state_of_bitstream(build_dir, path)
    emit(f"  bitstream {path.name}  sha256 {identity['sha256'][:12]}  "
         f"{len(pins)} PIOs recorded")
    return identity, pins


def record(label, passes, rung, bitstream=None, build_dir=None):
    """One matrix run, with everything needed to judge it later."""
    identity, pins = pin_provenance(bitstream, build_dir or hyperram_pin_patch.BUILD)
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

    spread = axis_spread(cells)
    live = liveness(spread)

    # STRUCTURE BEATS SPREAD. `liveness` reads the failure set, and a flat one is
    # equally consistent with "no effect" and "not connected". The engine says
    # which it is: on a non-DQS build `sel` is read by nothing (#343), so it is
    # dead here however the failures happened to fall.
    repeats = REPEATS.search(text)
    distinct, per_config = (int(repeats.group(1)), int(repeats.group(2))) \
        if repeats else (total, 1)
    if per_config > 1:
        live["sel"] = False
        emit(f"  {distinct} DISTINCT configurations x {per_config} repeats -- "
             "`sel` is UNWIRED on this build, not merely inert")

    dead = [axis for axis, moving in live.items() if not moving]
    if dead:
        emit(f"  INERT AXES: {', '.join(dead)} -- the failures fall evenly across "
             f"every value, so this run measured nothing on {'them' if len(dead) > 1 else 'it'}")

    run = {
        "recorded": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "label": label,
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "ck_mhz": rungs.get(rung),
        "rung": rung,
        "rungs": rungs,
        "passes_per_cell": passes,
        # What the run MEASURED, against the cells it printed. Equal on a DQS
        # build; 512 x 8 on a non-DQS one (#343).
        "distinct_configurations": distinct,
        "repeats_per_configuration": per_config,
        # The electrical state this run was taken at. `null` means unrecorded,
        # NOT "the defaults" -- see `pin_provenance`.
        "bitstream": identity,
        "pins": pins,
        # Whether this matrix measured anything at all -- see `axis_spread`.
        "axis_fail_counts": spread,
        "axes_live": live,
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


def pin_delta(a, b):
    """Print how the two runs' HyperRAM pin settings differ, or that nobody knows.

    This is the line that decides what a moved cell MEANS. Same pins -> the noise
    floor. One attribute apart -> that attribute's effect. Unrecorded -> neither,
    and it says so.
    """
    pa, pb = a.get("pins"), b.get("pins")
    if pa is None or pb is None:
        emit("\n  PINS UNRECORDED on " + ("A" if pa is None else "") +
             ("B" if pb is None else "") + " -- a moved cell here cannot be "
             "attributed to an attribute OR to noise")
        return
    moved = []
    for key in sorted(set(pa) | set(pb)):
        one, two = pa.get(key, {}), pb.get(key, {})
        for attr in sorted(set(one) | set(two)):
            if one.get(attr) != two.get(attr):
                moved.append(f"    {key} {attr}: {one.get(attr)} -> {two.get(attr)}")
    if not moved:
        emit("\n  pins IDENTICAL between these runs (the noise-floor case)")
    else:
        emit(f"\n  {len(moved)} pin setting(s) differ:")
        for line in moved:
            emit(line)


def diff(first, second):
    a, b = load(first), load(second)
    emit(f"\nA  {Path(first).name}")
    emit(f"   {a['recorded']}  CK {a['ck_mhz']} MHz  {a['passes_per_cell']} passes"
         f"  commit {(a['commit'] or '?')[:8]}{'  DIRTY' if a['dirty'] else ''}")
    emit(f"B  {Path(second).name}")
    emit(f"   {b['recorded']}  CK {b['ck_mhz']} MHz  {b['passes_per_cell']} passes"
         f"  commit {(b['commit'] or '?')[:8]}{'  DIRTY' if b['dirty'] else ''}")

    pin_delta(a, b)

    # An inert axis on either side makes "nothing moved" meaningless: the rig,
    # not the pins, is what did not move.
    for name, run in (("A", a), ("B", b)):
        per_config = run.get("repeats_per_configuration") or 1
        if per_config > 1:
            emit(f"  {name} measured {run['distinct_configurations']} distinct "
                 f"configurations, each {per_config} times -- its cell count is "
                 f"{per_config}x what it varied (#343)")
        dead = [axis for axis, moving in (run.get("axes_live") or {}).items()
                if not moving]
        if dead:
            emit(f"  {name} has INERT axes ({', '.join(dead)}) -- an unchanged "
                 f"failure set here is not evidence about anything")

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
    parser.add_argument("--bitstream", type=Path, default=None,
                        help="the .bit that was configured; its HyperRAM pin "
                             "settings are unpacked and stored with the run")
    parser.add_argument("--build-dir", type=Path,
                        default=hyperram_pin_patch.BUILD,
                        help="the build the bitstream came from, for its LPF")
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

    written = [record(args.label, args.passes, args.rung,
                      args.bitstream, args.build_dir)
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
