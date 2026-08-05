#!/usr/bin/env python3
#
# The same fabric test, N placements, one aggregate verdict. See #116.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds the fabric test N times with different nextpnr placer seeds, loads each
into SRAM, tests it, and reports what the set of them covered.

Why more than one configuration
-------------------------------

The single fabric run occupied ~82% of the die's LUTs and found it clean. That
is one placement, and a placement can only reach a small share of the
interconnect however much logic it holds: a routing arc is a mux selection, so
per destination wire exactly one source can be driving at a time. Everything
else that wire could have been driven from is untested by that build and can
only be reached by building again with the logic somewhere else.

So this loops. Each configuration is the same source, the same golden constant
and the same self-check, placed differently -- and the aggregate is the union of
what they exercised.

What it does not do
-------------------

It does not borrow a golden value. `fabric_build.py` computes its own for every
configuration, from this project's model of this project's recurrence. Handing
it a value from another generator whose recurrence differs would make every
round mismatch, and a design that mismatches every round is indistinguishable
from a dead one -- the failure mode most likely to be reported as a discovery.

It does not assume the seed worked. `fabric_placement.py` compares the occupied
LUT sites between builds, and `fabric_arcs.py` measures the routing each one
switched on against the Trellis database. If nextpnr converged on similar
placements, the coverage number says so and the extra builds bought nothing.

It does not inherit the negative control. A different placement is a different
design, so one configuration is rebuilt with a deliberately wrong golden
constant and `fabric_negative_control.py` must see every round mismatch. Without that,
the clean runs are silence rather than evidence.

Timing
------

Nothing here sleeps. Builds run to completion; each hardware test stops on a
count of rounds, not a duration. Wall-clock figures are recorded because a
mismatch rate needs a denominator, not because anything waits on them.

    ./scripts/fabric_sweep.py --configs 8
    ./scripts/fabric_sweep.py --configs 24 --jobs 8
    ./scripts/fabric_sweep.py --configs 24 --skip-build   # re-test what is built
"""

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
WORK = ROOT / "tmp" / "fabric-coverage"

sys.path.insert(0, str(SCRIPTS))

from devlog import emit  # noqa: E402

from fabric_build import parse_utilisation, parse_timing          # noqa: E402
import fabric_arcs                                                # noqa: E402
import fabric_placement                                           # noqa: E402

# The wrong constant the negative control is built against. Any value the design
# cannot produce will do; this one is recognisable in a register dump, which
# matters when the question is "is this the control or the real thing?".
WRONG_GOLDEN = "0xdeadbeef"


def build(seed, directory, blocks, round_bits, min_luts, golden=None):
    """One configuration. Returns (ok, seconds, log path)."""
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "build.log"
    command = [sys.executable, str(SCRIPTS / "fabric_build.py"),
               "--seed", str(seed),
               "--build-dir", str(directory),
               "--log", str(log),
               "--round-bits", str(round_bits),
               "--min-luts", str(min_luts)]
    if blocks is not None:
        command += ["--blocks", str(blocks)]
    if golden is not None:
        command += ["--golden", golden]
    start = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0, time.perf_counter() - start, log


def measure(directory):
    """LUT utilisation and timing, from nextpnr's own log for this build."""
    tim = directory / "top.tim"
    if not tim.exists():
        return {}
    text = tim.read_text()
    utilisation = parse_utilisation(text)
    luts, available = utilisation.get("Total LUT4s", (0, 0))
    clocks = parse_timing(text)
    entry = {"luts": luts, "luts_available": available,
             "lut_share": luts / available if available else 0.0}
    if clocks:
        # The slowest clock in the report, not the first. nextpnr prints one
        # line per clock domain and the design is only as fast as its worst,
        # so picking whichever came first would flatter half the builds.
        clock, achieved, constraint, _ = min(clocks, key=lambda c: c[1])
        entry.update(clock=clock, mhz=achieved, mhz_constraint=constraint,
                     timing_met=all(c[3] for c in clocks))
    return entry


def test(directory, rounds, emit):
    """Load and run one configuration. Returns the result dict."""
    result_path = directory / "result.json"
    command = [sys.executable, str(SCRIPTS / "fabric_run.py"),
               "--bitstream", str(directory / "top.bit"),
               "--rounds", str(rounds),
               "--result-json", str(result_path),
               "--log", str(directory / "run.log")]
    outcome = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result_path.exists():
        return json.loads(result_path.read_text())
    emit("  no result.json; the run did not get far enough to write one")
    for line in ((outcome.stdout or "") + (outcome.stderr or "")).splitlines()[-8:]:
        emit(f"    {line}")
    return {"verdict": "run failed"}


def die(entry):
    """The die temperature code from a run result, or None if there is none."""
    for key in ("die_after", "die_before"):
        value = entry.get(key)
        if value and value & (1 << 8):
            return value & 0x3F
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", type=int, default=8,
                        help="how many placements; 8 shakes out the loop, 24 "
                             "is the figure #116 asks for")
    parser.add_argument("--first-seed", type=int, default=1,
                        help="seeds are this and up, so a rerun with the same "
                             "value rebuilds the same placements")
    parser.add_argument("--rounds", type=int, default=5000,
                        help="rounds each configuration must complete. A "
                             "count, not a duration: at 2**18 cycles and 60 "
                             "MHz the gateware finishes about 229 a second, so "
                             "5000 is roughly 22 seconds of checking per "
                             "placement. Depth per placement is the soak's "
                             "job; this sweep's axis is breadth")
    parser.add_argument("--jobs", type=int, default=6,
                        help="parallel builds. Placement is single-threaded "
                             "per build, so the wall time is otherwise N times "
                             "one build")
    parser.add_argument("--blocks", type=int, default=None)
    parser.add_argument("--round-bits", type=int, default=18)
    parser.add_argument("--min-luts", type=int, default=12288)
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--skip-build", action="store_true",
                        help="test bitstreams already in the work directory")
    parser.add_argument("--no-control", action="store_true",
                        help="skip the negative control. The clean runs are "
                             "then not evidence, so this exists for a rerun "
                             "of the hardware half, not for a real result")
    args = parser.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)

    seeds = list(range(args.first_seed, args.first_seed + args.configs))
    directories = {seed: args.work / f"seed-{seed:03d}" for seed in seeds}

    emit(f"=== fabric sweep, {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===")
    emit(f"{args.configs} configurations, seeds {seeds[0]}..{seeds[-1]}, "
         f"{args.rounds} rounds each")
    emit(f"work: {args.work}")
    emit()

    #
    # Build. In parallel, because each nextpnr is single-threaded and the
    # board is not involved yet -- the hardware half below is strictly
    # serial and cannot overlap with itself.
    #
    builds = {}
    if args.skip_build:
        emit("--skip-build: using the bitstreams already present")
        for seed, directory in directories.items():
            builds[seed] = (directory / "top.bit").exists()
    else:
        emit(f"building {args.configs} configurations, {args.jobs} at a "
             f"time")
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            futures = {
                pool.submit(build, seed, directories[seed], args.blocks,
                            args.round_bits, args.min_luts): seed
                for seed in seeds}
            for future in concurrent.futures.as_completed(futures):
                seed = futures[future]
                ok, seconds, log = future.result()
                builds[seed] = ok
                emit(f"  seed {seed:>3}: "
                     f"{'built' if ok else 'BUILD FAILED'} in "
                     f"{seconds:.0f}s"
                     + ("" if ok else f" -- see {log}"))
        emit(f"builds finished in {time.perf_counter() - start:.0f}s")
    emit()

    good = [seed for seed in seeds if builds.get(seed)]
    if not good:
        emit("no configuration built -- nothing to test")
        return 1

    #
    # Test. Serial: there is one board, and a configuration under test owns
    # it completely.
    #
    emit(f"testing {len(good)} configurations on the board, in order")
    emit()
    results = {}
    for index, seed in enumerate(good, 1):
        directory = directories[seed]
        stats = measure(directory)
        emit(f"[{index}/{len(good)}] seed {seed}: "
             f"{stats.get('luts', 0):,} LUT4s "
             f"({100 * stats.get('lut_share', 0):.1f}%), "
             f"{stats.get('mhz', 0):.2f} MHz "
             f"{'MET' if stats.get('timing_met') else 'NOT MET'}")
        outcome = test(directory, args.rounds, emit)
        outcome.update(stats)
        outcome["seed"] = seed
        results[seed] = outcome
        temperature = die(outcome)
        emit(f"          {outcome.get('rounds', 0):,} rounds, "
             f"{outcome.get('mismatches', 0)} mismatched, sticky "
             f"{'SET' if outcome.get('sticky') else 'clear'}, "
             f"die {temperature if temperature is not None else 'n/a'} "
             f"-- {outcome['verdict'].upper()}")
    emit()

    #
    # The negative control, rebuilt for this sweep rather than inherited.
    #
    control = None
    if not args.no_control:
        seed = good[0]
        directory = args.work / f"control-seed-{seed:03d}"
        emit(f"negative control: seed {seed} rebuilt against "
             f"{WRONG_GOLDEN}, which the design cannot produce")
        ok, seconds, log = build(seed, directory, args.blocks,
                                 args.round_bits, args.min_luts,
                                 golden=WRONG_GOLDEN)
        if not ok:
            emit(f"  control build failed in {seconds:.0f}s -- see {log}")
        else:
            # fabric_run.py loads it and then refuses to score it, because
            # its constant disagrees with the host model. That refusal is
            # the correct behaviour and is itself part of the control.
            loaded = test(directory, args.rounds, emit)
            emit(f"  loaded; fabric_run.py's verdict: {loaded['verdict']}")
            outcome = subprocess.run(
                [sys.executable, str(SCRIPTS / "fabric_negative_control.py")],
                cwd=ROOT, capture_output=True, text=True)
            for line in (outcome.stdout or "").splitlines():
                emit(f"  {line}")
            control = outcome.returncode == 0
        emit()

    #
    # What the set of them covered.
    #
    configs = [directories[seed] / "top.config" for seed in good
               if (directories[seed] / "top.config").exists()]
    emit("=== placement: did the seed actually move the logic? ===")
    placement = fabric_placement.compare(configs, emit)
    emit()
    emit("=== routing arcs exercised, measured against the Trellis "
         "database ===")
    arcs = fabric_arcs.coverage(configs, "LFE5U-12F")
    fabric_arcs.report(arcs, emit)
    emit()

    #
    # The verdict.
    #
    passed = [s for s in good if results[s].get("verdict") == "pass"]
    # The headline quotes the per-instance share. "Routing arcs" plainly
    # means the arcs on this die, and that is the smaller, harder number;
    # the per-tile-type share is always larger and is stated immediately
    # below rather than in the headline, so neither can be read alone.
    emit(f"=== {len(passed)}/{args.configs} passed "
         f"-- {100 * arcs['instance_share']:.1f}% of routing arcs "
         f"exercised ===")
    emit(f"  per instance:  {arcs['covered_instance_arcs']:,} of "
         f"{arcs['total_instance_arcs']:,} arcs on the die")
    emit(f"  per tile type: {arcs['covered_type_arcs']:,} of "
         f"{arcs['total_type_arcs']:,} distinct arcs "
         f"-- {100 * arcs['type_share']:.1f}%")
    rounds = sum(results[s].get("rounds", 0) for s in good)
    mismatches = sum(results[s].get("mismatches", 0) for s in good)
    emit(f"  {rounds:,} gateware-checked rounds across all "
         f"configurations, {mismatches} mismatched")
    temperatures = [t for t in (die(results[s]) for s in good)
                    if t is not None]
    if temperatures:
        emit(f"  die temperature code {min(temperatures)}..."
             f"{max(temperatures)} across the sweep -- the operating point "
             f"is now recorded per configuration rather than assumed "
             f"constant")
    else:
        emit("  no die temperature: the bitstreams carry no DTR, so the "
             "operating point remains an untested assumption")
    if control is None:
        emit("  NO NEGATIVE CONTROL was run, so these clean results are "
             "not evidence that the detector can report a wrong answer")
    elif control:
        emit("  negative control passed on one of these placements: the "
             "detector fires on a wrong answer, so a clean run is a "
             "measurement")
    else:
        emit("  NEGATIVE CONTROL FAILED -- the detector did not reliably "
             "report a wrong answer, so nothing above is evidence about "
             "the silicon")

    summary = {
        "configs": args.configs,
        "seeds": seeds,
        "passed": passed,
        "rounds": rounds,
        "mismatches": mismatches,
        "control": control,
        "placement": placement,
        "arcs": {k: v for k, v in arcs.items() if k != "steps"},
        "arc_steps": arcs["steps"],
        "results": {str(s): results[s] for s in good},
    }
    (args.work / "sweep.json").write_text(json.dumps(summary, indent=2))
    emit(f"summary: {args.work / 'sweep.json'}")

    if len(passed) != args.configs or control is not True:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
