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
"""

from __future__ import annotations

import argparse
import hashlib
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=variant.build_dir(ROOT),
                        help="a build directory holding top.ys and its inputs")
    parser.add_argument("--runs", type=int, default=3)
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

    outcomes = []
    for run in range(1, args.runs + 1):
        started = time.perf_counter()
        result = subprocess.run(
            f'{ENVIRONMENT} && yosys -q -l retry-{run}.rpt top.ys',
            cwd=build, shell=True, capture_output=True, text=True)
        elapsed = time.perf_counter() - started
        digest = (hashlib.sha256((build / "top.json").read_bytes()).hexdigest()[:12]
                  if result.returncode == 0 and (build / "top.json").exists()
                  else None)
        outcomes.append(result.returncode)
        error = next((line for line in
                      ((result.stderr or "") + (result.stdout or "")).splitlines()
                      if line.startswith("ERROR")), "")
        emit(f"  run {run}: rc={result.returncode} in {elapsed:.0f} s"
             + (f", top.json {digest}" if digest else "")
             + (f" -- {error[:110]}" if error else ""))

    good = outcomes.count(0)
    emit(f"{good}/{len(outcomes)} runs succeeded on identical inputs")
    if good in (0, len(outcomes)):
        emit("  deterministic: the inputs decide, so a retry will not change it")
    else:
        emit("  NOT deterministic: same inputs, different answers -- the tool, "
             "not the netlist")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
