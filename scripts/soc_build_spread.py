#!/usr/bin/env python3
#
# Build the SoC N times and report the Fmax spread, because one build is not evidence.
# SPDX-License-Identifier: BSD-3-Clause

"""
Repeat `soc_run.py --build-only` and report the per-clock Fmax spread.

    ./scripts/soc_build_spread.py --runs 3 --label before
    ./scripts/soc_build_spread.py --runs 3 --label after --compare before

## Why repeats are mandatory here

`fast_build_env.NEXTPNR_OPTS` carries `--parallel-refine --threads 31`, so the
SA refinement pass is threaded and its result depends on thread interleaving.
Two runs of byte-identical sources give different placements and different Fmax:
this design has been recorded between 56.21 and 67.27 MHz on one netlist (#306).

A single before/after pair therefore cannot separate a change from the noise.
This runs each side several times and reports min/max/spread, so a claimed
difference can be compared against the width of the distribution it came from.

## What it defeats to get a real build

`soc_run.py` skips synthesis when `gateware-digest.txt` matches the tree, and
restores a cached bitstream from `tmp/bitcache/<digest>/` when one exists. Both
are right for normal work and both would turn runs 2..N into file copies of run
1, reporting a spread of zero. Each run deletes the digest and the cache entry
first.

`top.tim` accumulates across runs, so it is deleted too rather than parsed for
"the last block" -- the per-run copy under `tmp/build-spread/` is then this run's
report and nothing else's.

## The edge-clock evidence, from the bitstream

Each run also records whether nextpnr fell back to general routing for an edge
clock, and which PLL output arcs into the bank ECLK mux. That is a property of
the `.config`, not of the log, and it is the only place the answer is written
down: nextpnr announces the fallback as `log_info`, never as a warning (#314).
"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "awto_soc" / "build"
OUT = ROOT / "tmp" / "build-spread"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, spawn  # noqa: E402

FMAX_RE = re.compile(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz\s+\((\w+)")

# The ECLK input mux sources worth naming. `G_J{LL,UL}CPLL0CLK{OP,OS}` are the
# dedicated PLL taps; `*QECLKCIB*` is the fabric tap, which is what a design gets
# when the dedicated route was refused. See #314.
ECLK_SOURCE_RE = re.compile(r"^arc: \S*ECLKI\d+ (\S+)$", re.M)
FABRIC_ECLK = "general routing will be used"


def bust_cache():
    """Make the next `soc_run.py` synthesise rather than copy a previous result."""
    digest_file = BUILD / "gateware-digest.txt"
    digest = digest_file.read_text().strip() if digest_file.exists() else None
    digest_file.unlink(missing_ok=True)
    (BUILD / "top.tim").unlink(missing_ok=True)
    if digest:
        shutil.rmtree(ROOT / "tmp" / "bitcache" / digest, ignore_errors=True)


def frequencies(text):
    """{clock: (mhz, PASS/FAIL)} from a nextpnr log."""
    return {m.group(1): (float(m.group(2)), m.group(3))
            for m in FMAX_RE.finditer(text)}


def eclk_evidence(config_text, tim_text):
    """What the bitstream says about edge-clock routing.

    Reads the `.tile *:ECLK_L`/`ECLK_R` blocks, which are the bank edge-clock
    input muxes: an `arc:` into `*ECLKI*` names the source the mux selected.
    """
    sources = []
    for match in re.finditer(r"^\.tile (\S+:ECLK_[LR])\n((?:.+\n)*)", config_text,
                             re.M):
        for source in ECLK_SOURCE_RE.finditer(match.group(2)):
            sources.append(f"{match.group(1)} <- {source.group(1)}")
    return {"eclk_mux_sources": sorted(set(sources)),
            "fabric_eclk_lines": tim_text.count(FABRIC_ECLK)}


def one_run(label, index, extra):
    """One full build. Returns the row, or None if the build failed."""
    bust_cache()
    started = time.perf_counter()
    rc = spawn([sys.executable, str(ROOT / "scripts" / "soc_run.py"),
                "--build-only", "--skip-tests", *extra], quiet=True)
    elapsed = time.perf_counter() - started

    keep = OUT / label / str(index)
    keep.mkdir(parents=True, exist_ok=True)
    tim = BUILD / "top.tim"
    config = BUILD / "top.config"
    tim_text = tim.read_text() if tim.exists() else ""
    config_text = config.read_text() if config.exists() else ""
    if tim_text:
        shutil.copy2(tim, keep / "top.tim")
    if config_text:
        # 8 MB a run, and only the tile headers and their arcs are ever read
        # back. The `.bram_init` blocks are ~95% of it and say nothing here.
        (keep / "top.config.tiles").write_text(
            "\n".join(line for line in config_text.splitlines()
                      if not re.fullmatch(r"[0-9a-f ]+", line)))

    row = {"label": label, "run": index, "rc": rc,
           "seconds": round(elapsed, 1),
           "fmax": {k: v[0] for k, v in frequencies(tim_text).items()},
           "status": {k: v[1] for k, v in frequencies(tim_text).items()},
           **eclk_evidence(config_text, tim_text)}
    emit(f"  run {index}: rc={rc} {elapsed:.0f}s  "
         + "  ".join(f"{k.split('$')[-1]}={v:.2f}" for k, v in row["fmax"].items()))
    if row["fabric_eclk_lines"]:
        emit(f"    *** {row['fabric_eclk_lines']} edge clock(s) on GENERAL ROUTING")
    for source in row["eclk_mux_sources"]:
        emit(f"    ECLK mux: {source}")
    return row


def summarise(rows, title):
    emit(f"\n=== {title} ===")
    clocks = sorted({c for r in rows for c in r["fmax"]})
    out = {}
    for clock in clocks:
        values = [r["fmax"][clock] for r in rows if clock in r["fmax"]]
        if not values:
            continue
        spread = max(values) - min(values)
        out[clock] = dict(min=min(values), max=max(values),
                          spread=round(spread, 2), runs=len(values),
                          values=values)
        emit(f"{clock:32s} min={min(values):6.2f} max={max(values):6.2f} "
             f"spread={spread:5.2f} MHz  n={len(values)}")
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3,
                        help="builds to make; 3 is the floor for a spread")
    parser.add_argument("--label", required=True,
                        help="names this side of the comparison, e.g. before/after")
    parser.add_argument("--compare", default=None,
                        help="an earlier --label to print alongside")
    parser.add_argument("--append", action="store_true",
                        help="add to this label's existing runs rather than "
                             "replacing them. What makes INTERLEAVING possible: "
                             "on a shared machine, alternating the two sides one "
                             "build at a time keeps a change in background load "
                             "out of the difference between them.")
    parser.add_argument("soc_run_args", nargs="*",
                        help="extra arguments passed through to soc_run.py")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    record = OUT / f"{args.label}.json"
    rows = (json.loads(record.read_text())["rows"]
            if args.append and record.exists() else [])
    emit(f"{args.label}: {args.runs} builds of gateware/soc/top.py "
         f"(--build-only, no hardware){f', {len(rows)} already' if rows else ''}")
    for index in range(len(rows) + 1, len(rows) + args.runs + 1):
        row = one_run(args.label, index, args.soc_run_args)
        rows.append(row)
        if row["rc"] != 0:
            emit(f"  build {index} FAILED; see tmp/logs/synthesis.log")

    result = {"label": args.label, "rows": rows,
              "summary": summarise(rows, f"{args.label}: spread over "
                                         f"{len(rows)} builds")}
    (OUT / f"{args.label}.json").write_text(json.dumps(result, indent=2))
    emit(f"\nwrote {(OUT / f'{args.label}.json').relative_to(ROOT)}")

    if args.compare:
        other = OUT / f"{args.compare}.json"
        if not other.exists():
            emit(f"no earlier run labelled {args.compare} at "
                 f"{other.relative_to(ROOT)}")
            return 1
        previous = json.loads(other.read_text())
        summarise(previous["rows"], f"{args.compare}: spread over "
                                    f"{len(previous['rows'])} builds")
        emit("\nA difference smaller than the larger spread above is noise.")

    # A build that MISSES TIMING is a row, not an error: nextpnr returns non-zero
    # and still reports the Fmax it reached, which is the number this exists to
    # collect. Only a run that produced no report at all is a failure here.
    return 0 if all(r["fmax"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
