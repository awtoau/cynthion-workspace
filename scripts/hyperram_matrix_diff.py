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

import bist_rows  # noqa: E402
import hyperram_pin_patch  # noqa: E402
import soc_shell  # noqa: E402

RESULTS = ROOT / "results" / "hyperram"
LOG = ROOT / "tmp" / "logs" / "hyperram-matrix-diff.log"

# The row, summary, repeat and rung patterns live in `bist_rows` -- one copy for
# all seven scripts that read this output, because three private copies had
# already gone stale unnoticed.
RUNG = bist_rows.RUNG
# What the BOARD says it is running, from `info`. The host checkout's HEAD is a
# different fact and has been the wrong one -- see `board_identity`.
IMAGE = re.compile(r"^\s*image\s+(\S+)\s+(clean|dirty)", re.M)
GATEWARE = re.compile(r"^\s*gateware\s+(\S+)\s+(clean|dirty)", re.M)


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
    # REPO-RELATIVE, because `results/**` is tracked and this repo is public:
    # the absolute form named one account, one disk and one agent worktree in
    # every record, and `scripts/private_path_check.py` is red on all of them.
    # The sha256 is the identity; the path is only where it was found.
    try:
        where = str(path.relative_to(ROOT))
    except ValueError:
        where = path.name
    identity = {"path": where, "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data)}
    pins = hyperram_pin_patch.pin_state_of_bitstream(build_dir, path)
    emit(f"  bitstream {path.name}  sha256 {identity['sha256'][:12]}  "
         f"{len(pins)} PIOs recorded")
    return identity, pins


def board_identity(board):
    """The firmware and gateware commits the BOARD reports, from `info`.

    `git rev-parse HEAD` describes the HOST CHECKOUT, which is only the same
    thing when the board was flashed from it -- and a board left running an
    older image is the normal state during a build. A run stamped with the
    checkout's commit while the board ran another is a measurement filed under
    the wrong build, which is the one error a recorded artifact cannot survive.
    """
    text = board.send("info", 6)
    image, gateware = IMAGE.search(text), GATEWARE.search(text)
    if not image:
        raise SystemExit(
            "`info` did not report an image commit, so this run cannot be "
            f"attributed to a firmware build. Reply was:\n{text[-600:]}")
    return {"image": image.group(1), "image_dirty": image.group(2) == "dirty",
            "gateware": gateware.group(1) if gateware else None,
            "gateware_dirty": gateware.group(2) == "dirty" if gateware else None}


def record(label, passes, rung, bitstream=None, build_dir=None):
    """One matrix run, with everything needed to judge it later."""
    identity, pins = pin_provenance(bitstream, build_dir or hyperram_pin_patch.BUILD)
    board = Board()
    try:
        onboard = board_identity(board)
        head = git("rev-parse", "HEAD")
        if head and not head.startswith(onboard["image"]):
            emit(f"  BOARD RUNS {onboard['image']}, CHECKOUT IS AT {head[:8]} -- "
                 "this run measures the board's image, not the checkout")
        ck = board.send(f"bist ck {rung}", 4)
        rungs = {int(m["rung"]): float(m["mhz"]) for m in RUNG.finditer(ck)}
        if not rungs:
            raise SystemExit(f"no CK rungs reported; is `bist ck` in this build?\n{ck}")
        if "NOT LOCKED" in ck:
            raise SystemExit("PLL not locked -- no measurement here means anything")

        emit(f"running the matrix at {rungs.get(rung)} MHz, {passes} passes/cell")
        text = board.send(f"bist all {passes}", 300)
    finally:
        board.close()

    # A CLEAN run legitimately prints no rows -- `bist all` shows only what is
    # not a clean pass -- so this is the one caller that may not demand any.
    # THREE BUCKETS, because the firmware scores three. A NO RESULT is not a
    # pass and not a failure -- the control did not fire, so the cell says
    # nothing -- and dropping it made a PASS -> NO RESULT move invisible to the
    # diff that exists to find marginal cells (#422).
    cells, noresults = {}, {}
    for row in bist_rows.rows(text):
        key = "{lat},{mode},{drive},{clk},{sel}".format(**row)
        entry = [row["errors"], row["words"], row["control"], row["verdict"]]
        if row["verdict"].startswith("fail"):
            cells[key] = entry
        elif not row["verdict"].startswith("PASS"):
            noresults[key] = entry

    totals = bist_rows.require_summary(text, f"bist all {passes}")
    passed, failed = totals["passed"], totals["failed"]
    noresult, total = totals["no_result"], totals["total"]
    # A summary claiming failures beside a parse that found none is the silent
    # no-match, and it reads as a clean matrix. Not a warning.
    if failed and not cells:
        raise SystemExit(
            f"{failed} failures summarised and NOT ONE ROW PARSED. The parse is "
            "the suspect, not the board -- see scripts/bist_rows.py and "
            f"tests/test_bist_row_parsers.py. Tail was:\n{text[-800:]}")
    if len(cells) != failed:
        emit(f"  WARNING: {failed} failures summarised but {len(cells)} parsed. "
             "Saving anyway; the diff will be wrong by the difference.")

    spread = axis_spread({**cells, **noresults})
    live = liveness(spread)

    # STRUCTURE BEATS SPREAD. `liveness` reads the failure set, and a flat one is
    # equally consistent with "no effect" and "not connected". The engine says
    # which it is: on a non-DQS build `sel` is read by nothing (#343), so it is
    # dead here however the failures happened to fall.
    repeats = bist_rows.REPEATS.search(text)
    distinct, per_config = (int(repeats["distinct"]), int(repeats["repeats"])) \
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
        # What the BOARD said it was running. `commit` above is the host
        # checkout, which is a different fact -- see `board_identity`.
        "board": onboard,
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
        # Kept apart rather than merged: a cell that fails is evidence about the
        # part, one that returns NO RESULT is evidence about the rig.
        "no_results": noresults,
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

    ba, bb = a.get("board") or {}, b.get("board") or {}
    if ba.get("image") != bb.get("image"):
        emit(f"\n  DIFFERENT FIRMWARE: A ran {ba.get('image')}, B ran "
             f"{bb.get('image')} -- a moved cell is not marginality, it is the "
             "build change")

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

    return diff_runs(a, b)


def diff_runs(a, b):
    """The comparison itself, on two loaded runs. Split out so it is testable
    without a recorded file -- `tests/test_matrix_diff_buckets.py`."""
    # DIFF ON THE VERDICT CLASS, not on the failure set. A run records the cells
    # that failed and the cells that returned NO RESULT; anything in neither
    # passed. Comparing failure sets alone made PASS -> NO RESULT invisible --
    # and NO RESULT is exactly where a marginal cell lands, since the control
    # firing is the thing that goes intermittent (#422).
    def classes(run):
        seen = {key: "fail" for key in run["failures"]}
        seen.update({key: "no result" for key in run.get("no_results", {})})
        return seen

    ca, cb = classes(a), classes(b)
    moved = sorted(key for key in set(ca) | set(cb) if ca.get(key) != cb.get(key))

    emit(f"\n  A {len(a['failures'])} fail {len(a.get('no_results', {}))} no result"
         f"   B {len(b['failures'])} fail {len(b.get('no_results', {}))} no result")
    if not moved:
        emit("  IDENTICAL verdicts -- nothing moved between these two runs")
        return 0

    emit(f"  {len(moved)} cell(s) changed verdict. Format: lat,mode,drive,clk,sel")
    for key in moved:
        was, now = ca.get(key, "PASS"), cb.get(key, "PASS")
        extra = ""
        if now == "fail":
            extra = f"   errors {b['failures'][key][0]}"
        emit(f"    {was:9} -> {now:9}  {key}{extra}")
    emit("\n  A cell that changed under identical conditions is MARGINAL, and a "
         "single run would have scored it as a verdict.")
    return len(moved)


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
