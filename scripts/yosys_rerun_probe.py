#!/usr/bin/env python3
#
# Re-run yosys on a build directory's own inputs, N times. #306.
# SPDX-License-Identifier: BSD-3-Clause

"""Is a failed synthesis the INPUTS or the TOOL?

    ./scripts/yosys_rerun_probe.py                       # this variant's build dir
    ./scripts/yosys_rerun_probe.py --runs 5 --dir tmp/awto_soc/build/<variant>

A build that fails with

    ERROR: Assert `memory_strings.count(...ID::MEMID...)' failed in kernel/rtlil.cc

says nothing about which. This re-runs the yosys step alone -- same `top.ys`, same
`top.il`, same `VexiiRiscv.v`, no elaboration, no place-and-route -- and reports
what happened each time.

  * fails every run -> the RTLIL is bad, and elaboration produced it
  * fails some runs -> yosys is not deterministic on this input, and a retry is a
    legitimate response rather than superstition

That distinction was worth a day the last time it came up: #306 attributed two
such asserts to a netlist written by a concurrent build, which is a different
fault with a different fix.

Outputs are written beside the inputs (`top.json` and `retry-N.rpt`), so a
successful run leaves the directory usable.

`--jobs N` runs N of them at once, each in its own `rerun-<n>/` copy of the
inputs, and keeps every `top.json` -- so the runs are independent and their
digests are comparable afterwards. Concurrency is the point: the assert is rare
enough that a serial probe spends most of an hour not reproducing it.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

# The same environment `soc_run.py` sources for a build. yosys is not on PATH
# without it, and a different yosys would answer a different question.
ENVIRONMENT = 'source "$HOME/opt/oss-cad-suite/environment"'

# yosys's own `stat` tally, `<count>   <CELL>`. LUT4 here is the pre-pack logic
# count -- what a netlist difference moves; nextpnr's TRELLIS_COMB is downstream.
CELL_RE = re.compile(r"^\s+(\d+)\s+(LUT4|CCU2C|TRELLIS_FF|DP16KD|MULT18X18D)\s*$",
                     re.M)


def cell_counts(report: Path) -> dict:
    """The last tally in the report -- `stat` runs per module and then for the top."""
    text = report.read_text(errors="replace") if report.exists() else ""
    return {name: int(used) for used, name in CELL_RE.findall(text)}


def one_run(build: Path, run: int, jobs: int) -> dict:
    """One yosys invocation, in its own directory when more than one is in flight."""
    work = build
    if jobs > 1:
        work = build / f"rerun-{run}"
        work.mkdir(parents=True, exist_ok=True)
        for name in ("top.ys", "top.il", "VexiiRiscv.v"):
            if (build / name).exists():
                shutil.copy2(build / name, work / name)
    report = work / f"retry-{run}.rpt"
    started = time.perf_counter()
    result = subprocess.run(
        f'{ENVIRONMENT} && yosys -q -l {report.name} top.ys',
        cwd=work, shell=True, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    produced = work / "top.json"
    digest = (hashlib.sha256(produced.read_bytes()).hexdigest()[:12]
              if result.returncode == 0 and produced.exists() else None)
    error = next((line for line in
                  ((result.stderr or "") + (result.stdout or "")).splitlines()
                  if line.startswith("ERROR")), "")
    return {"run": run, "rc": result.returncode, "seconds": elapsed,
            "digest": digest, "error": error, "cells": cell_counts(report)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=variant.build_dir(ROOT),
                        help="a build directory holding top.ys and its inputs")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=1,
                        help="runs in flight; each gets its own rerun-<n>/ copy. "
                             "yosys is single-threaded, so this is free parallelism")
    args = parser.parse_args()

    # A relative --dir is relative to the repo root, not to wherever this was
    # invoked from, so a path copied out of a build log works verbatim.
    build = args.dir if args.dir.is_absolute() else (ROOT / args.dir)
    if not (build / "top.ys").exists():
        emit(f"no top.ys in {build}; nothing to re-run")
        return 1

    inputs = {name: hashlib.sha256((build / name).read_bytes()).hexdigest()[:12]
              for name in ("top.ys", "top.il", "VexiiRiscv.v")
              if (build / name).exists()}
    emit(f"re-running yosys {args.runs}x in {build.relative_to(ROOT)}")
    for name, digest in inputs.items():
        emit(f"  {name} {digest}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = sorted(pool.map(lambda run: one_run(build, run, args.jobs),
                               range(1, args.runs + 1)),
                      key=lambda row: row["run"])

    for row in rows:
        emit(f"  run {row['run']}: rc={row['rc']} in {row['seconds']:.0f} s"
             + (f", top.json {row['digest']}" if row["digest"] else "")
             + (f", LUT4 {row['cells'].get('LUT4')}" if row["cells"] else "")
             + (f" -- {row['error'][:110]}" if row["error"] else ""))

    good = sum(row["rc"] == 0 for row in rows)
    digests = {row["digest"] for row in rows if row["digest"]}
    emit(f"{good}/{len(rows)} runs succeeded on identical inputs")
    if good in (0, len(rows)):
        emit("  deterministic in rc: the inputs decide, so a retry will not change it")
    else:
        emit("  NOT deterministic in rc: same inputs, different answers -- the tool, "
             "not the netlist")
    if len(digests) > 1:
        emit(f"  and the OUTPUT moved too: {len(digests)} distinct top.json -- "
             f"synthesis itself is a source of area/Fmax spread")
    elif digests:
        emit(f"  every successful run wrote the same top.json ({digests.pop()}): "
             f"area and timing spread come from downstream of yosys")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
