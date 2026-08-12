#!/usr/bin/env python3
#
# Does freeing die area improve timing on this design? No -- #467.
# SPDX-License-Identifier: BSD-3-Clause

"""Sample the Fmax DISTRIBUTION at several occupancy levels and compare them.

The claim under test: the shipping SoC's critical path is 2.23 ns logic against
12.01 ns routing at 64.8% LUT4, so routing dominates, and routing is what
congestion degrades -- therefore removing logic should buy Fmax. Every previous
attempt compared ONE build a side, which cannot see past the spread.

## The design

- One arm = one netlist. `synth` elaborates and synthesises it once; #441 makes
  that byte-reproducible, so the netlist is a fixed property of the arm.
- One sample = one place-and-route of that netlist at a given `--seed`. With
  `--parallel-refine` OFF each (netlist, seed) is deterministic, so the sample
  set is a reproducible sample of the placer's outcome distribution.

Seeds, not source perturbations, are what re-roll a sample here. A perturbation
re-rolls the netlist AND its occupancy by a few cells, confounding the very axis
being measured; `--seed` holds occupancy EXACTLY fixed within an arm, so the only
difference between arms is the one under test. The two questions then separate:

- vary the seed, one netlist -> the placement distribution at fixed occupancy,
  which is the noise floor, measured rather than inferred
- hold the seed set, change the netlist -> the trim's effect over the same
  placements, compared PAIRED by seed

Nothing in this project has ever passed `--seed`, so every Fmax in its history is
one draw from the default placement.

Answered in #467: at 56% occupancy, -1315 LUT4-equivalents buys +0.19 MHz, 95% CI
[-0.96, +1.35] -- nothing, on a harness that resolves the -6.45 MHz control at
p < 0.0001. The placement distribution at FIXED occupancy is 9 MHz wide, which is
what every single-build comparison here was reading as signal.

    ./scripts/soc_occupancy_timing.py synth --arm base --arm hyperram-probe
    ./scripts/soc_occupancy_timing.py sweep --arm base --seeds 20 --jobs 8
    ./scripts/soc_occupancy_timing.py report

#440: a run killed by its own bound leaves a parseable `top.tim`, so
`Program finished normally.` is required of every sample.
#429: `--parallel-refine` is checked by READING the generated `build_top.sh`,
not by assuming the environment took.

Logs to ./tmp/logs/soc_occupancy_timing.log, results to ./tmp/occupancy/.
"""

import argparse
import concurrent.futures
import json
import math
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_occupancy_timing.log"
OUT = ROOT / "tmp" / "occupancy"

sys.path.insert(0, str(ROOT / "scripts"))

from soc_trim_delta import TRIMS  # noqa: E402

FINISHED = "Program finished normally."
UTIL_RE = re.compile(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'(\S+)':\s+([\d.]+) MHz")

# One LUT4 of die area. CCU2C and DPR16X4 each occupy two of the eight LUT4
# positions in a slice, so they cost two; TRELLIS_FF rides along in the same
# slice and costs none. This is the x-axis.
LUT4_EQUIV = {"LUT4": 1, "CCU2C": 2, "DPR16X4": 2}

# THE HARNESS CONTROL. A chain of 32-bit adders in `sync`, `keep`-marked so
# neither opt_clean nor ABC can shorten it, driven by and driving a register.
# Occupancy moves by ~32 LUT4-equivalents per stage; the critical path moves by
# a whole adder per stage. If the arms do not separate on this, they cannot
# separate on anything and no other row here means anything.
_CHAIN = '''
import top as _top
from amaranth import Module, Signal

_DEPTH = {depth}
_real_elaborate = _top.AwtoSoc.elaborate

def _elaborate(self, platform):
    m = _real_elaborate(self, platform)
    keep = {{"keep": "true"}}
    head = Signal(32, name="chain_head", attrs=keep)
    node = head
    for index in range(_DEPTH):
        nxt = Signal(32, name=f"chain{{index}}", attrs=keep)
        m.d.comb += nxt.eq(node + (index | 1))
        node = nxt
    m.d.sync += head.eq(node)
    return m

_top.AwtoSoc.elaborate = _elaborate
'''

# The merged one-gateware design at `sync` 60 (#432). An arm is a netlist, and
# the environment is what selects this one -- read at `import top`, so it is set
# before that import rather than by the caller's shell.
_MERGED = 'os.environ["CYNTHION_HYPERRAM_MERGED"] = "1"\n' \
          'os.environ["CYNTHION_SYNC_MHZ"] = "60"\n'

ARMS = {
    # The shipping design, untouched.
    "base": "",
    "merged60": _MERGED,
    # Removals, smallest first. `hyperram-probe` is the largest single stub
    # available; stacking `window-spi0` on it is the largest reachable.
    "hyperram-probe": TRIMS["hyperram-probe"],
    "window-spi0": TRIMS["window-spi0"],
    "trim-both": TRIMS["hyperram-probe"] + TRIMS["window-spi0"],
    # An addition, to test whether the relationship is monotonic or one-sided.
    "plus3-bridged-windows": TRIMS["plus3-bridged-windows"],
    # Controls.
    "chain8": _CHAIN.format(depth=8),
    "chain32": _CHAIN.format(depth=32),
}

OUTPUT = []


def emit(line=""):
    print(line, flush=True)
    OUTPUT.append(line)


def flush_log(name):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    path = LOG.with_name(f"soc_occupancy_timing-{name}.log")
    path.write_text("\n".join(OUTPUT) + "\n")
    emit(f"(log written to {path})")
    LOG.write_text("\n".join(OUTPUT) + "\n")


def arm_dir(arm):
    return OUT / arm


def synth(arm):
    """Elaborate and synthesise one arm. Leaves top.json, top.lpf, build_top.sh."""
    build_dir = arm_dir(arm) / "synth"
    script = f"""
import os, sys
from pathlib import Path
ROOT = Path({str(ROOT)!r})
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

import fast_build_env
# Determinism over wall clock: every sample in this experiment must be a
# function of (netlist, seed) alone (#306, #441).
os.environ["AMARANTH_nextpnr_opts"] = "--threads 31 --router router2"

{ARMS[arm]}

import top as soc_top
from board.cynthion_r1_4 import CynthionPlatformRev1D4

CynthionPlatformRev1D4().build(
    soc_top.AwtoSoc(firmware=[0] * (soc_top.RAM_SIZE // 4)),
    do_program=False, build_dir={str(build_dir)!r})
"""
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, cwd=ROOT)
    # nextpnr exits non-zero when a clock misses its constraint, having placed
    # and routed anyway. That is a result, not a failure -- an arm heavy enough
    # to miss is exactly one this wants to sample. The netlist is what `sweep`
    # needs, and #440's check runs per sample.
    if not (build_dir / "top.json").exists():
        raise SystemExit(f"{arm}: no netlist\n"
                         + proc.stdout[-4000:] + "\n" + proc.stderr[-4000:])
    return time.monotonic() - started


def nextpnr_command(arm):
    """The arm's own nextpnr command line, read back from its build script.

    Read rather than reconstructed: #429 was an override that never reached the
    tool. The `--parallel-refine` check has to interrogate what will actually
    run.
    """
    script = arm_dir(arm) / "synth" / "build_top.sh"
    if not script.exists():
        raise SystemExit(f"{arm}: no build_top.sh -- run `synth` first")
    for line in script.read_text().splitlines():
        # The invocation, not the `: ${NEXTPNR_ECP5:=...}` default above it.
        if "NEXTPNR_ECP5" in line and "--lpf" in line:
            words = shlex.split(line)
            if "--parallel-refine" in words:
                raise SystemExit(
                    f"{arm}: build_top.sh passes --parallel-refine; every Fmax "
                    f"from it would be thread-interleaving noise (#429, #306)")
            return words
    raise SystemExit(f"{arm}: no nextpnr-ecp5 line in build_top.sh")


def sample(arm, seed, threads=None, tag=None):
    """One place-and-route at `seed`. Returns the parsed report or raises."""
    out = arm_dir(arm) / (tag or f"seed{seed:03d}")
    out.mkdir(parents=True, exist_ok=True)
    tim = out / "top.tim"
    cmd, synth_dir = [], arm_dir(arm) / "synth"
    for word in nextpnr_command(arm):
        # Redirect every output of the shared build script into this seed's own
        # directory; the inputs (top.json, top.lpf) stay shared.
        if word.endswith(("top.tim", "top.config")):
            word = str(out / Path(word).name)
        # The generated script names the tool through a shell variable.
        if word == "$NEXTPNR_ECP5":
            word = "nextpnr-ecp5"
        cmd.append(word)
    cmd += ["--seed", str(seed)]
    if threads is not None:
        # Overwrite the build script's own `--threads`, not append to it.
        at = cmd.index("--threads")
        cmd[at + 1] = str(threads)

    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=synth_dir)
    (out / "argv").write_text(" ".join(cmd) + "\n")
    elapsed = time.monotonic() - started
    text = tim.read_text() if tim.exists() else proc.stdout + proc.stderr
    if FINISHED not in text:
        return {"arm": arm, "seed": seed, "ok": False, "seconds": elapsed,
                "why": f"no '{FINISHED}' (rc={proc.returncode})"}
    fmax, util = {}, {}
    for line in text.splitlines():
        found = UTIL_RE.match(line.strip())
        if found:
            util[found.group(1)] = int(found.group(2))
        found = FMAX_RE.search(line)
        if found:
            fmax.setdefault(found.group(1), []).append(float(found.group(2)))
    return {"arm": arm, "seed": seed, "ok": True, "seconds": round(elapsed, 1),
            "util": util,
            # nextpnr prints the table twice; the last describes the bitstream.
            "fmax": {clock: values[-1] for clock, values in fmax.items()}}


def occupancy(arm):
    """LUT4-equivalents and FF count, counted off the arm's own netlist."""
    path = arm_dir(arm) / "synth" / "top.json"
    if not path.exists():
        return None
    design = json.loads(path.read_text())
    counts = {}
    for module in design.get("modules", {}).values():
        for cell in module.get("cells", {}).values():
            counts[cell["type"]] = counts.get(cell["type"], 0) + 1
    lut4e = sum(counts.get(kind, 0) * weight for kind, weight in LUT4_EQUIV.items())
    return {"lut4_equiv": lut4e,
            **{kind: counts.get(kind, 0) for kind in LUT4_EQUIV},
            "TRELLIS_FF": counts.get("TRELLIS_FF", 0)}


def results_path(arm):
    return arm_dir(arm) / "samples.json"


def load(arm):
    path = results_path(arm)
    return json.loads(path.read_text()) if path.exists() else []


def sweep(arm, seeds, jobs, threads=None):
    """Place and route the arm at seeds 1..N, skipping ones already sampled."""
    have = {row["seed"] for row in load(arm) if row.get("ok")}
    todo = [seed for seed in range(1, seeds + 1) if seed not in have]
    emit(f"{arm}: {len(have)} sampled, {len(todo)} to run, {jobs} at a time")
    rows = load(arm)
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for row in pool.map(lambda seed: sample(arm, seed, threads), todo):
                clock = row.get("fmax", {})
                emit(f"  seed {row['seed']:>3}: "
                     + (", ".join(f"{name} {value:.2f}"
                                  for name, value in sorted(clock.items()))
                        if row["ok"] else f"FAILED {row['why']}")
                     + f"  [{row['seconds']}s]")
                rows = [old for old in rows if old["seed"] != row["seed"]] + [row]
                results_path(arm).write_text(json.dumps(rows, indent=1))
    return rows


def determinism(arm, seed, repeats, threads):
    """Repeat ONE (netlist, seed) serially. Anything but one value is a defect.

    Serial on purpose: if the tool is thread-interleaving dependent, running the
    repeats concurrently would hide it behind a different load each time.
    """
    emit(f"{arm} seed {seed}, --threads {threads}, {repeats} serial repeats")
    values = []
    for index in range(repeats):
        row = sample(arm, seed, threads=threads,
                     tag=f"determinism-t{threads}-s{seed}-{index}")
        if not row["ok"]:
            emit(f"  repeat {index}: FAILED {row['why']}")
            continue
        values.append(row["fmax"]["$glbnet$clk"])
        emit(f"  repeat {index}: clk {values[-1]:.2f} MHz  [{row['seconds']}s]")
    emit(f"  -> {len(set(values))} distinct value(s) in {len(values)}: "
         f"{sorted(set(values))}")
    return values


def welch(left, right):
    """(t, dof, two-sided p) for two samples of unequal variance."""
    if len(left) < 2 or len(right) < 2:
        return None
    m1, m2 = statistics.mean(left), statistics.mean(right)
    v1, v2 = statistics.variance(left), statistics.variance(right)
    n1, n2 = len(left), len(right)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return (math.inf, math.inf, 0.0)
    t = (m2 - m1) / se
    dof = se ** 4 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    # Two-sided p from the t distribution's CDF, via the incomplete beta.
    x = dof / (dof + t * t)
    return t, dof, _betainc(dof / 2, 0.5, x)


def _betainc(a, b, x):
    """Regularised incomplete beta, continued fraction (Lentz). Enough for a p."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1 - x))
    if x > (a + 1) / (a + b + 2):
        return 1 - _betainc(b, a, 1 - x)
    tiny, c, d = 1e-30, 1.0, 1.0
    f = 1.0
    for i in range(300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1 / (max(abs(1 + num * d), tiny) * (1 if 1 + num * d >= 0 else -1))
        c = 1 + num / c if abs(1 + num / c) > tiny else tiny
        f *= c * d
        if abs(1 - c * d) < 1e-10:
            break
    return front * (f - 1) / a


def mann_whitney(left, right):
    """(U, two-sided p by normal approximation). Free of any distribution shape."""
    n1, n2 = len(left), len(right)
    if not n1 or not n2:
        return None
    merged = sorted([(v, 0) for v in left] + [(v, 1) for v in right])
    ranks, index = {}, 0
    while index < len(merged):
        stop = index
        while stop + 1 < len(merged) and merged[stop + 1][0] == merged[index][0]:
            stop += 1
        shared = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[position] = shared
        index = stop + 1
    r1 = sum(ranks[i] for i, (_, side) in enumerate(merged) if side == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mean = n1 * n2 / 2
    sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sd == 0:
        return u1, 1.0
    z = (abs(u1 - mean) - 0.5) / sd
    return u1, math.erfc(z / math.sqrt(2))


def describe(values):
    if not values:
        return "--"
    if len(values) == 1:
        return f"{values[0]:.2f} (n=1)"
    return (f"min {min(values):6.2f}  med {statistics.median(values):6.2f}  "
            f"max {max(values):6.2f}  mean {statistics.mean(values):6.2f}  "
            f"sd {statistics.stdev(values):5.2f}  n={len(values)}")


def paired(ref, arm):
    """Paired stats over the seeds both arms have. Same placement, two netlists.

    Pairing removes the seed-to-seed variation, which is the dominant term here,
    so it sees a shift a two-sample test of the same size cannot.
    """
    common = sorted(set(ref) & set(arm))
    diffs = [arm[seed] - ref[seed] for seed in common]
    if len(diffs) < 2:
        return None
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs)
    n = len(diffs)
    t = mean / (sd / math.sqrt(n)) if sd else math.inf
    p = (_betainc((n - 1) / 2, 0.5, (n - 1) / (n - 1 + t * t)) if sd else 0.0)
    wins = sum(1 for d in diffs if d > 0)
    # Sign test: how surprising is `wins` out of n if the trim did nothing.
    sign_p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(wins, n - wins) + 1))
                 / 2 ** n)
    return {"n": n, "mean": mean, "sd": sd, "min": min(diffs), "max": max(diffs),
            "median": statistics.median(diffs), "p": p, "wins": wins,
            "sign_p": sign_p, "common": common, "diffs": diffs}


def report(arms, clock):
    emit(f"clock under test: {clock}")
    emit()
    emit(f"  {'arm':<24} {'LUT4-eq':>8} {'LUT4':>7} {'CCU2C':>6} {'DPR':>4} "
         f"{'FF':>6}")
    emit("  " + "-" * 62)
    series, area, by_seed = {}, {}, {}
    for arm in arms:
        rows = [row for row in load(arm) if row.get("ok")]
        by_seed[arm] = {row["seed"]: row["fmax"][clock] for row in rows
                        if clock in row["fmax"]}
        values = sorted(by_seed[arm].values())
        if not values:
            continue
        series[arm] = values
        cells = occupancy(arm)
        area[arm] = cells
        emit(f"  {arm:<24} {cells['lut4_equiv']:>8} {cells['LUT4']:>7} "
             f"{cells['CCU2C']:>6} {cells['DPR16X4']:>4} {cells['TRELLIS_FF']:>6}")

    emit()
    emit("  Fmax over seeds, MHz")
    for arm, values in series.items():
        emit(f"    {arm:<24} {describe(values)}")

    emit()
    emit("  per seed, MHz -- the pairing, visible")
    every = sorted({seed for arm in series for seed in by_seed[arm]})
    emit("    seed  " + "".join(f"{arm[:14]:>15}" for arm in series))
    for seed in every:
        emit(f"    {seed:>4}  " + "".join(
            f"{by_seed[arm].get(seed, float('nan')):>15.2f}" for arm in series))

    if "base" in series:
        emit()
        emit(f"  {'arm':<24} {'d(LUT4-eq)':>11} {'d(median)':>10} "
             f"{'d(mean)':>9} {'Welch p':>9} {'MWU p':>8} {'overlap':>9}")
        emit("  " + "-" * 85)
        ref = series["base"]
        for arm, values in series.items():
            if arm == "base":
                continue
            t = welch(ref, values)
            u = mann_whitney(ref, values)
            # Ranges that do not touch is the strongest statement available
            # from a sample this size, and needs no distributional assumption.
            disjoint = min(values) > max(ref) or max(values) < min(ref)
            emit(f"  {arm:<24} "
                 f"{area[arm]['lut4_equiv'] - area['base']['lut4_equiv']:>+11} "
                 f"{statistics.median(values) - statistics.median(ref):>+10.2f} "
                 f"{statistics.mean(values) - statistics.mean(ref):>+9.2f} "
                 f"{t[2]:>9.4f} {u[1]:>8.4f} "
                 f"{'disjoint' if disjoint else 'overlap':>9}")

        emit()
        emit("  PAIRED by seed -- same placement, two netlists")
        emit(f"  {'arm':<24} {'n':>3} {'mean d':>8} {'sd':>6} {'min d':>7} "
             f"{'max d':>7} {'paired p':>9} {'faster':>8} {'sign p':>8}")
        emit("  " + "-" * 87)
        for arm in series:
            if arm == "base":
                continue
            got = paired(by_seed["base"], by_seed[arm])
            if not got:
                continue
            emit(f"  {arm:<24} {got['n']:>3} {got['mean']:>+8.2f} "
                 f"{got['sd']:>6.2f} {got['min']:>+7.2f} {got['max']:>+7.2f} "
                 f"{got['p']:>9.4f} {got['wins']:>3}/{got['n']:<4} "
                 f"{got['sign_p']:>8.4f}")


def needed_n(values, delta):
    """Two-sample n a side for 80% power at alpha 0.05 to see a shift of `delta`."""
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    return math.ceil(2 * (1.96 + 0.84) ** 2 * sd ** 2 / delta ** 2)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("synth", "sweep", "report", "power",
                                          "determinism", "list"))
    parser.add_argument("--arm", action="append", default=[])
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--threads", type=int, default=None,
                        help="override the build script's nextpnr --threads")
    parser.add_argument("--repeats", type=int, default=3,
                        help="`determinism` repeats of one seed")
    parser.add_argument("--seed", type=int, default=1,
                        help="`determinism` seed")
    parser.add_argument("--clock", default=None,
                        help="which clock's Fmax to report; the slowest by "
                             "default")
    parser.add_argument("--delta", type=float, default=2.0,
                        help="MHz the `power` stage sizes n for")
    args = parser.parse_args()

    if args.stage == "list":
        for name in ARMS:
            print(f"  {name}")
        return 0

    arms = args.arm or list(ARMS)
    for arm in arms:
        if arm not in ARMS:
            raise SystemExit(f"no such arm: {arm}; `list` shows them")

    if args.stage == "synth":
        # Elaboration is Python and yosys is pinned to one thread, so arms
        # synthesise concurrently without contending.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(arms)) as pool:
            for arm, seconds in zip(arms, pool.map(synth, arms)):
                cells = occupancy(arm)
                emit(f"{arm}: {seconds:.0f}s  LUT4-eq {cells['lut4_equiv']}  "
                     f"FF {cells['TRELLIS_FF']}")
                nextpnr_command(arm)  # asserts --parallel-refine is absent
        emit("build_top.sh checked for --parallel-refine on every arm: absent")
        flush_log("synth")
        return 0

    if args.stage == "determinism":
        for arm in arms:
            determinism(arm, args.seed, args.repeats, args.threads)
        flush_log("determinism")
        return 0

    if args.stage == "sweep":
        for arm in arms:
            sweep(arm, args.seeds, args.jobs, args.threads)
        flush_log("sweep")
        return 0

    clock = args.clock
    if clock is None:
        rows = [row for arm in arms for row in load(arm) if row.get("ok")]
        if not rows:
            raise SystemExit("no samples yet -- run `sweep`")
        slowest = min(rows[0]["fmax"], key=lambda name: rows[0]["fmax"][name])
        clock = slowest

    if args.stage == "power":
        for arm in arms:
            values = sorted(row["fmax"][clock] for row in load(arm)
                            if row.get("ok") and clock in row["fmax"])
            if len(values) > 1:
                emit(f"{arm}: sd {statistics.stdev(values):.2f} MHz over "
                     f"n={len(values)}; n a side for {args.delta} MHz at 80% "
                     f"power = {needed_n(values, args.delta)}")
        flush_log("power")
        return 0

    report(arms, clock)
    flush_log("report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
