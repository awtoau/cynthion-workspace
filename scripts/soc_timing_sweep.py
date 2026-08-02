#!/usr/bin/env python3
#
# Build the SoC repeatedly and report the spread of Fmax, not one number.
# SPDX-License-Identifier: BSD-3-Clause

"""
Repeated place-and-route runs of `ecp5-test/riscv/vexii_hello_soc.py`, reported
as min/median/max.

    ./scripts/soc_timing_sweep.py --label baseline --runs 3
    ./scripts/soc_timing_sweep.py --label registered --runs 3
    ./scripts/soc_timing_sweep.py --compare baseline registered

## Why repeated runs

nextpnr's placement is stochastic here, and not in a small way. The same netlist
built twice has been observed at 60.78 MHz and at 67.49 MHz -- an 11% spread on
a 60 MHz constraint, which is the difference between 1.3% margin and 12%. The
`--parallel-refine` pass is the reason: sixteen threads mutate a shared placement
and the interleaving is not reproducible, so the seed does not pin the result
the way it would for a single-threaded run.

A single build therefore cannot support a claim that a change helped. Two
changes measured one run each can easily be a full 7 MHz apart with nothing
between them but thread scheduling. This runs the same build N times and reports
the distribution, so an improvement has to move the whole distribution to count.

Runs are serial, not parallel: nextpnr is already given 31 threads, so two
concurrent builds would contend for the same cores and change the very thing
being measured.

## What is recorded

Per run: post-route Fmax on each clock, the critical path's start and end cell,
its logic/routing split, and cell utilisation. The path endpoints matter as much
as the frequency -- a change that improves Fmax while leaving the critical path
in the same place has bought placement luck, and a change that moves the path
somewhere else has actually altered the design.

Everything lands in `tmp/soc_timing_sweep/<label>.json`, appended, so a label
can be topped up with more runs later and `--compare` still sees all of them.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_timing_sweep.log"
RESULTS = ROOT / "tmp" / "soc_timing_sweep"
BUILD = ROOT / "tmp" / "vexii_hello" / "build"
SOC = ROOT / "ecp5-test" / "riscv" / "vexii_hello_soc.py"
BOOTLOADER = ROOT / "tmp" / "rust_boot.bin"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_timing import run_bounded

# The clock under test. nextpnr names it after the net driving the global, and
# `sync` is fed from the PLL's CLKOP through the plain `clk` net -- the USB
# domain appears separately as `aux_phy_0__clk__o`.
SYNC_CLOCK = "$glbnet$clk"

# The nextpnr invocation, passed through Amaranth's override environment.
#
# This is deliberately the same command a person building by hand would use
# (see the module docstring of vexii_hello_soc.py), because a sweep run under
# different options measures a different design. router2 is nextpnr's
# timing-driven router; the default router1 is faster and worse.
NEXTPNR_OPTS = "--parallel-refine --threads 31 --router router2"

# Which cell types are worth reporting. The rest of nextpnr's utilisation table
# is either fixed by the board (IO, PLL, JTAGG) or unused here.
UTIL_CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "TRELLIS_RAMW", "DP16KD",
              "MULT18X18D", "TRELLIS_IO")

_FMAX = re.compile(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz")
_UTIL = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)")
_PATH_HEAD = re.compile(r"Critical path report for clock '([^']+)'")
_SPLIT = re.compile(r"([\d.]+) ns logic, ([\d.]+) ns routing")


def log(line, handle):
    """Everything goes to both, because a sweep is watched live and read later."""
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def parse_tim(text):
    """Pull the post-route numbers out of a nextpnr log.

    nextpnr reports timing TWICE: once after placement as an estimate, and again
    after routing with real wire delays. Only the second is a fact about the
    bitstream -- the estimate has been seen 4 MHz low and 8 MHz high on the same
    run -- so every value here is taken from the LAST occurrence.
    """
    fmax = {}
    for clock, mhz in _FMAX.findall(text):
        fmax[clock] = float(mhz)          # later matches overwrite earlier ones

    util = {}
    for line in text.splitlines():
        found = _UTIL.match(line)
        if found and found.group(1) in UTIL_CELLS:
            util[found.group(1)] = int(found.group(2))

    # The critical path for the sync clock: split the log on the report headers
    # and keep the last section belonging to this clock.
    section = None
    current = None
    for line in text.splitlines():
        head = _PATH_HEAD.search(line)
        if head:
            current = head.group(1)
            if current == SYNC_CLOCK:
                section = []
            continue
        if section is not None and current == SYNC_CLOCK:
            if line.startswith("Info: Critical path report"):
                current = None
            else:
                section.append(line)

    path = {}
    if section:
        body = "\n".join(section)
        starts = re.search(r"clk-to-q\s+[\d.]+\s+[\d.]+ Source (\S+)", body)
        ends = re.findall(r"setup\s+[\d.]+\s+([\d.]+) Source (\S+)", body)
        split = _SPLIT.search(body)
        path = {
            "start": starts.group(1) if starts else None,
            "end": ends[-1][1] if ends else None,
            "total_ns": float(ends[-1][0]) if ends else None,
            "logic_ns": float(split.group(1)) if split else None,
            "routing_ns": float(split.group(2)) if split else None,
        }

    return {"fmax": fmax, "util": util, "path": path}


def build_once(firmware, handle):
    """One full synthesis + place-and-route. Returns the parsed report."""
    env = dict(os.environ, AMARANTH_nextpnr_opts=NEXTPNR_OPTS)
    # The bootloader too, so the bitstream measured is the bitstream that ships.
    #
    # It cannot move Fmax -- block RAM init is DP16KD INITVAL and no part of it is
    # timed -- but a sweep that built a different design from the one under discussion
    # would be a measurement of something nobody runs.
    command = [sys.executable, str(SOC), "--build", "--firmware", str(firmware),
               "--bootloader", str(BOOTLOADER)]

    result = run_bounded(command, family="vexii_hello_soc", cwd=ROOT, env=env)
    if result is None:
        log("  build overran its bound and was killed", handle)
        return None
    if result.returncode != 0:
        log(f"  build failed ({result.returncode})", handle)
        # The tail is where yosys and nextpnr put their errors; the head is
        # thousands of lines of elaboration.
        for line in (result.stderr or result.stdout).splitlines()[-25:]:
            log("    " + line, handle)
        return None

    return parse_tim((BUILD / "top.tim").read_text())


def sweep(label, runs, firmware):
    RESULTS.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    store = RESULTS / f"{label}.json"
    history = json.loads(store.read_text()) if store.exists() else []

    with LOG.open("a") as handle:
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        log(f"\n=== {label}: {runs} runs, {stamp} ===", handle)

        for index in range(runs):
            log(f"run {index + 1}/{runs}", handle)
            report = build_once(firmware, handle)
            if report is None:
                return 1

            report["label"] = label
            report["when"] = datetime.now(timezone.utc).astimezone().isoformat(
                timespec="seconds")
            history.append(report)
            store.write_text(json.dumps(history, indent=2))

            # Keep the log of the run itself: the frequency is a summary, and
            # the question after a surprising one is always "through what".
            shutil.copy(BUILD / "top.tim", RESULTS / f"{label}-{len(history)}.tim")

            sync = report["fmax"].get(SYNC_CLOCK)
            path = report["path"]
            log(f"  sync {sync:.2f} MHz  ({path['total_ns']:.2f} ns: "
                f"{path['logic_ns']} logic + {path['routing_ns']} routing)", handle)
            log(f"    {path['start']}", handle)
            log(f"    -> {path['end']}", handle)

        summarise([label], handle)
    return 0


def summarise(labels, handle):
    for label in labels:
        store = RESULTS / f"{label}.json"
        if not store.exists():
            log(f"{label}: nothing recorded", handle)
            continue
        history = json.loads(store.read_text())
        values = sorted(run["fmax"].get(SYNC_CLOCK, 0.0) for run in history)
        if not values:
            continue

        margin = (statistics.median(values) - 60.0) / 60.0 * 100
        log(f"\n{label}: {len(values)} runs, sync clock", handle)
        log(f"  min {values[0]:.2f}  median {statistics.median(values):.2f}  "
            f"max {values[-1]:.2f} MHz   (median margin over 60 MHz: {margin:+.1f}%)",
            handle)

        last = history[-1]
        util = ", ".join(f"{cell} {last['util'].get(cell, 0)}"
                         for cell in UTIL_CELLS if cell in last["util"])
        log(f"  utilisation (last run): {util}", handle)

        # Where the path lands is the structural result; if every run reports the
        # same endpoints, the spread is placement and not design.
        starts = {run["path"].get("start") for run in history}
        log(f"  critical path starts at: {', '.join(sorted(s or '?' for s in starts))}",
            handle)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", help="name this configuration")
    parser.add_argument("--runs", type=int, default=3,
                        help="how many builds; three is the minimum that says "
                             "anything about a distribution")
    parser.add_argument("--firmware", type=Path, default=ROOT / "tmp" / "rust_fw.bin")
    parser.add_argument("--compare", nargs="+", metavar="LABEL",
                        help="report labels already recorded, build nothing")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)

    if args.compare:
        with LOG.open("a") as handle:
            summarise(args.compare, handle)
        return 0

    if not args.label:
        parser.error("--label is required unless --compare is given")

    # Fail before the first build rather than after it. Amaranth reports a
    # missing nextpnr as a subprocess error several thousand lines into an
    # elaboration log, which is a slow way to learn that a shell was never
    # prepared.
    for tool in ("yosys", "nextpnr-ecp5", "ecppack"):
        if shutil.which(tool) is None:
            print(f"{tool} not on PATH -- source the oss-cad-suite environment:")
            print('  source "$HOME/opt/oss-cad-suite/environment"')
            return 1

    if not args.firmware.exists():
        print(f"no firmware at {args.firmware}; build it with ./scripts/riscv_firmware.py")
        return 1

    return sweep(args.label, args.runs, args.firmware)


if __name__ == "__main__":
    sys.exit(main())
