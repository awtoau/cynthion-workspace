#!/usr/bin/env python3
#
# Sweep the HyperRAM DQS capture phase at one device clock.
# SPDX-License-Identifier: BSD-3-Clause

"""Record and analyse the full READCLKSEL capture sweep.

The default device CK is 160 MHz, safely below the known-clean 192 MHz point.
A near-ceiling sweep can make a valid harness look broken by producing a narrow
or empty window, so 192 MHz is deliberately not the procedural starting point.

The window is a physical property of a particular board, package and part. This
script supplies the procedure; it does not imply that any window or centred
setting has been measured until ``--run`` completes on hardware.

    ./scripts/hyperram_readclksel_sweep.py --build
    ./scripts/hyperram_readclksel_sweep.py --run
    ./scripts/hyperram_readclksel_sweep.py

``--build`` makes the single DQS bitstream without programming a board. ``--run``
uses that bitstream, walks all eight settings, and gets each pass/fail verdict
from the existing shared BIST and its runtime negative control. With neither
option, the script builds and then runs, matching the other sweep scripts.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
RESULTS = ROOT / "tmp" / "hyperram-readclksel"
SETTINGS = tuple(range(8))
DEFAULT_CK_MHZ = 160.0
DEFAULT_WORDS = 10_000_000


@dataclass(frozen=True)
class WindowAnalysis:
    """A verdict that never invents one window from incompatible evidence."""

    kind: str
    ranges: tuple[tuple[int, int], ...]
    widths: tuple[int, ...]
    width: int | None = None
    centre: int | None = None
    centre_candidates: tuple[int, ...] = ()
    clipped_start: bool = False
    clipped_end: bool = False
    width_is_lower_bound: bool = False
    centre_bound: str | None = None
    alarming: bool = False


def analyse_window(table):
    """Analyse consecutive integer ``(setting, passed)`` rows.

    A centre exists only when the table demonstrates exactly one non-total
    passing range. For an even width, the lower of the two equally central
    settings is selected deterministically and both candidates are retained.
    """
    rows = list(table.items()) if isinstance(table, dict) else list(table)
    if not rows:
        raise ValueError("pass/fail table is empty")

    rows = sorted(rows)
    settings = [setting for setting, _ in rows]
    if any(not isinstance(setting, int) for setting in settings):
        raise ValueError("settings must be integers")
    if len(set(settings)) != len(settings):
        raise ValueError("settings must be unique")
    expected = list(range(settings[0], settings[-1] + 1))
    if settings != expected:
        raise ValueError("settings must cover one consecutive range")
    if any(type(passed) is not bool for _, passed in rows):
        raise ValueError("verdicts must be bool values")

    passing = [setting for setting, passed in rows if passed]
    if not passing:
        return WindowAnalysis(kind="no_pass", ranges=(), widths=())
    if len(passing) == len(rows):
        return WindowAnalysis(
            kind="all_pass", ranges=((settings[0], settings[-1]),),
            widths=(len(rows),), clipped_start=True, clipped_end=True,
            width_is_lower_bound=True)

    ranges = []
    start = end = passing[0]
    for setting in passing[1:]:
        if setting == end + 1:
            end = setting
        else:
            ranges.append((start, end))
            start = end = setting
    ranges.append((start, end))
    ranges = tuple(ranges)
    widths = tuple(end - start + 1 for start, end in ranges)

    if len(ranges) != 1:
        return WindowAnalysis(kind="disjoint", ranges=ranges, widths=widths)

    start, end = ranges[0]
    width = widths[0]
    clipped_start = start == settings[0]
    clipped_end = end == settings[-1]
    low = (start + end) // 2
    high = (start + end + 1) // 2
    candidates = (low,) if low == high else (low, high)
    if clipped_start and clipped_end:
        centre_bound = "indeterminate"
    elif clipped_start:
        centre_bound = "upper"
    elif clipped_end:
        centre_bound = "lower"
    else:
        centre_bound = None

    return WindowAnalysis(
        kind="single_window", ranges=ranges, widths=widths, width=width,
        centre=low, centre_candidates=candidates,
        clipped_start=clipped_start, clipped_end=clipped_end,
        width_is_lower_bound=clipped_start or clipped_end,
        centre_bound=centre_bound, alarming=width == 1)


def analysis_lines(analysis):
    """Render consequences explicitly so edge cases cannot look routine."""
    if analysis.kind == "no_pass":
        return ["*** NO PASSING SETTING: no capture window or centre exists"]
    if analysis.kind == "all_pass":
        return [
            "*** INVALID CAPTURE TEST: EVERY SETTING PASSED",
            "*** This test is measuring something other than the capture window; "
            "no width or centre is reported",
        ]
    if analysis.kind == "disjoint":
        spans = ", ".join(
            f"{start}..{end} (width {width})"
            for (start, end), width in zip(analysis.ranges, analysis.widths))
        return [
            f"*** DISJOINT PASSING RANGES: {spans}",
            "*** The single-window assumption failed; no centre is reported",
        ]

    start, end = analysis.ranges[0]
    width = f">= {analysis.width}" if analysis.width_is_lower_bound else str(
        analysis.width)
    lines = [f"passing range {start}..{end}; observed width {width}"]
    if analysis.alarming:
        lines.append("*** ALARM: WIDTH 1 -- capture margin is one setting")
    if analysis.clipped_start:
        lines.append("*** Range reaches the sweep start; true width may extend below it")
    if analysis.clipped_end:
        lines.append("*** Range reaches the sweep end; true width may extend above it")
    if analysis.centre_bound == "lower":
        lines.append(f"observed centre {analysis.centre} is a LOWER BOUND on the "
                     "true centred setting")
    elif analysis.centre_bound == "upper":
        lines.append(f"observed centre {analysis.centre} is an UPPER BOUND on the "
                     "true centred setting")
    else:
        lines.append(f"centred setting {analysis.centre}")
    if len(analysis.centre_candidates) == 2:
        lines.append(f"even width has two central settings "
                     f"{analysis.centre_candidates}; selecting the lower by rule")
    return lines


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="build only; no board")
    parser.add_argument("--run", action="store_true",
                        help="run only; use the existing bitstream")
    parser.add_argument("--ck", type=float, default=DEFAULT_CK_MHZ,
                        help="fixed device CK in MHz (default: 160, below 192)")
    parser.add_argument("--words", type=int, default=DEFAULT_WORDS,
                        help="checked words per setting before its verdict")
    args = parser.parse_args()

    if args.ck <= 0:
        parser.error("--ck must be positive")
    if args.words <= 0:
        parser.error("--words must be positive")

    do_build = args.build or not args.run
    do_run = args.run or not args.build
    sync_mhz = args.ck / 2

    # Hardware dependencies stay outside analysis imports so synthetic tests do
    # not require a toolchain, Apollo checkout, debugger, or board.
    from hyperram_ceiling import build_one, run_one, solve_pll, timing_of

    if solve_pll(sync_mhz, fast_ratio=2) is None:
        parser.error(f"device CK {args.ck:g} MHz is not reachable by the DQS clocks")

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone()
    result_path = RESULTS / f"{stamp.strftime('%Y%m%d-%H%M%S')}.json"

    emit(f"\n=== HyperRAM READCLKSEL sweep, "
         f"{stamp.isoformat(timespec='seconds')} ===")
    emit(f"fixed device CK {args.ck:g} MHz; DQS sync {sync_mhz:g} MHz; "
         f"settings 0..7; {args.words:,} checked words per setting")

    if do_build:
        emit("\nbuilding one runtime-selectable DQS bitstream; "
             "the board is not involved")
        ok, build_dir, _ = build_one(args.ck, True, sync_mhz)
        timing = timing_of(build_dir)
        note = ""
        if timing:
            note = (f"; timing {'MET' if timing[0] else 'FAILED'} "
                    f"{timing[1]:.1f}/{timing[2]:.1f} MHz on {timing[3]}")
        emit(f"  {'built' if ok else 'BUILD FAILED'}{note}")
        if not ok:
            return 1

    if not do_run:
        return 0

    emit("\nrunning all READCLKSEL settings; each verdict comes from "
         "the shared BIST and its negative control")
    results = []
    for setting in SETTINGS:
        measured = run_one(args.ck, True, sync_mhz, args.words,
                           readclksel=setting)
        passed = measured.get("verdict") == "pass"
        results.append({"setting": setting, "passed": passed,
                        "measurement": measured})

    analysis = analyse_window(
        (row["setting"], row["passed"]) for row in results)
    emit("\npass/fail table")
    for row in results:
        emit(f"  READCLKSEL {row['setting']}: "
             f"{'PASS' if row['passed'] else 'FAIL'}")
    emit("\nwindow analysis")
    for line in analysis_lines(analysis):
        emit("  " + line)

    payload = {
        "timestamp": stamp.isoformat(timespec="seconds"),
        "ck_mhz": args.ck, "sync_mhz": sync_mhz,
        "words_per_setting": args.words, "results": results,
        "analysis": asdict(analysis),
    }
    result_path.write_text(json.dumps(payload, indent=2) + "\n")
    emit(f"results: {result_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
