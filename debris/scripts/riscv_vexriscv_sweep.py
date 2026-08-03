#!/usr/bin/env python3
#
# Measure VexRiscv the same way the VexiiRiscv sweep measured VexiiRiscv.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds every VexRiscv variant through the sweep's own flow, for comparison.

The VexRiscv figures quoted so far came from `ecp5-test/riscv/cpu_area.py`,
which is not the flow the VexiiRiscv sweep used. Three differences, pulling in
different directions:

  * cpu_area attaches block RAM to the instruction bus only, with no arbiter
    and no peripherals. The sweep's MicroSoC rows carry a CLINT and a UART.
    That flatters VexRiscv on area.
  * cpu_area takes whatever Fmax one routing pass reports. The sweep binary
    searches for the highest target that still closes. That understates
    VexRiscv on frequency.
  * The two use different top levels, so their critical paths are not
    necessarily comparable at all.

Comparing across them and calling it like-for-like is the same mistake the
archived RV32 report made -- its VexRiscv row included the whole USB fabric
while its VexiiRiscv rows did not, which is why that table could not be used to
choose a core.

So this builds VexRiscv with the same memory attached to both buses through the
same arbiter, and finds Fmax by the same search. The remaining difference is
that VexRiscv is a core plus memory while the sweep's SoC rows add CLINT and
UART, so VexRiscv should be compared against the sweep's `core_dev` rows.

    ./scripts/riscv_vexriscv_sweep.py
    ./scripts/riscv_vexriscv_sweep.py --variants cynthion imc
"""

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp" / "riscv_vexriscv"
LOG = ROOT / "tmp" / "logs" / "riscv_vexriscv_sweep.log"

VARIANTS = ["cynthion", "cynthion+jtag", "imac+dcache", "imac+litex", "imc"]

# Same bounds and resolution as riscv_sweep_run.py, so the numbers mean the
# same thing. 2 MHz is finer than run-to-run placement noise.
FMAX_LOW, FMAX_HIGH, FMAX_RESOLUTION = 20, 260, 2

PYTHON = str(Path.home() / "opt" / "cpython-315t" / "bin" / "python3.15t")

BUILD = """
import sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {ecp5!r})

import riscv.cpu_area as cpu_area
cpu_area.VARIANT = {variant!r}

from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4
CynthionPlatformRev1D4().build(cpu_area.CPUArea(), do_program=False,
                               build_dir={build_dir!r})
"""


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def clean_env():
    """Environment without the oss-cad-suite Python overrides.

    Yosys and nextpnr need that environment on PATH; a Python subprocess must
    not inherit its PYTHONHOME or it gets an interpreter with no standard
    library.
    """
    import os
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


def synthesise(variant, work):
    """Build the design to JSON via the Amaranth platform."""
    script = BUILD.format(root=str(ROOT), ecp5=str(ROOT / "ecp5-test"),
                          variant=variant, build_dir=str(work))
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     dir=ROOT / "tmp") as handle:
        handle.write(script)
        path = handle.name

    result = subprocess.run([PYTHON, path], cwd=ROOT, capture_output=True,
                            text=True, env=clean_env())
    Path(path).unlink(missing_ok=True)

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return None, (tail[-1][:80] if tail else "build failed")

    design = work / "top.json"
    return (design, None) if design.exists() else (None, "no top.json emitted")


def route(design, work, target_mhz):
    """Route at one target; returns (closed, area)."""
    result = subprocess.run([
        "nextpnr-ecp5", "--12k", "--package", "CABGA256", "--speed", "8",
        "--json", str(design), "--textcfg", str(work / "out.cfg"),
        "--timing-allow-fail", "--freq", str(target_mhz),
    ], capture_output=True, text=True, env=clean_env())

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, None

    def count(cell):
        found = re.search(rf"{cell}:\s+(\d+)/", output)
        return int(found.group(1)) if found else None

    area = {"lut": count("TRELLIS_COMB"), "ff": count("TRELLIS_FF"),
            "bram": count("DP16KD")}
    return "FAIL at" not in output, area


def find_fmax(design, work):
    """Highest target the design still closes at."""
    closed, area = route(design, work, FMAX_LOW)
    if not closed:
        return None, area, 1

    low, high, best, attempts = FMAX_LOW, FMAX_HIGH, FMAX_LOW, 1
    while high - low > FMAX_RESOLUTION:
        middle = (low + high) // 2
        closed, this_area = route(design, work, middle)
        attempts += 1
        if closed:
            low, best, area = middle, middle, this_area
        else:
            high = middle
    return best, area, attempts


def build(variant):
    work = OUT / variant.replace("+", "_")
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    design, error = synthesise(variant, work)
    if error:
        return variant, None, time.perf_counter() - started, error

    fmax, area, attempts = find_fmax(design, work)
    elapsed = time.perf_counter() - started
    if fmax is None:
        return variant, None, elapsed, f"does not close at {FMAX_LOW} MHz"

    result = {"variant": variant, "core": "VexRiscv", "fmax": fmax,
              "route_attempts": attempts, "seconds": elapsed, **(area or {})}
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return variant, result, elapsed, None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variants", nargs="+", default=VARIANTS)
    parser.add_argument("--jobs", type=int, default=5)
    args = parser.parse_args()

    for tool in ("yosys", "nextpnr-ecp5"):
        if shutil.which(tool) is None:
            print(f"{tool} not on PATH -- source the oss-cad-suite environment")
            return 1

    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        emit(handle, f"VexRiscv: {len(args.variants)} variants, core + 64 KiB "
                     f"block RAM")
        emit(handle, f"Fmax by binary search over {FMAX_LOW}-{FMAX_HIGH} MHz, "
                     f"as in the VexiiRiscv sweep")
        emit(handle)

        results, failures = {}, {}
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            futures = {pool.submit(build, v): v for v in args.variants}
            for future in concurrent.futures.as_completed(futures):
                variant, result, elapsed, error = future.result()
                if error:
                    failures[variant] = error
                    emit(handle, f"  {variant:<16} {elapsed:>6.1f}s  {error}")
                else:
                    results[variant] = result
                    emit(handle, f"  {variant:<16} {elapsed:>6.1f}s  "
                                 f"{result['fmax']:>4} MHz  {result['lut']:>6} "
                                 f"LUT  {result['bram']:>3} BRAM")

        emit(handle)
        emit(handle, "Compare against the sweep's core_dev rows, not its SoC "
                     "rows: these are")
        emit(handle, "a core plus memory, while the SoC rows add a CLINT and "
                     "a UART.")

        if failures:
            emit(handle)
            emit(handle, "Failures, reported rather than dropped:")
            for variant, error in sorted(failures.items()):
                emit(handle, f"    {variant:<16} {error}")

        emit(handle)
        emit(handle, f"results: {OUT}")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
