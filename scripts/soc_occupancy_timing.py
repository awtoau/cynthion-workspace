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

import soc_cpu_arms  # noqa: E402
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

ARMS = {
    # The shipping design, untouched.
    "base": "",
    # The same design asked for `sync` 60 rather than its own 50. An arm is a
    # netlist and the environment selects it -- set before `import top`, which
    # is where it is read, rather than by the caller's shell.
    "sync60": 'os.environ["CYNTHION_SYNC_MHZ"] = "60"\n',
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

# The CPU's own configuration, which is regenerated from Scala on every build
# and so is an axis rather than a fixed input. `soc_cpu_arms.base` is the same
# design as `base` above, generated through the same path; both are kept so the
# two entry points can be compared.
ARMS.update({f"cpu-{name}": text
             for name, text in soc_cpu_arms.snippets().items()})

# THE CONSTRAINT IS NOT AN ARM, and #478 is why: the same netlist asked for 80
# MHz instead of 60 places and routes to a bit-identical result on every clock,
# 12 seeds for 12. `constrain` is kept as the way to re-check that on a new
# netlist; an arm elaborated at another SYNC_MHZ would measure the netlist
# change (PLL, baud divisors, ~250 cells), not the ask.

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

# This arm's OWN generated core. `top.py` puts it in the variant build
# directory, which every arm shares -- fine while no arm changed the CPU, and a
# silent swap of one arm's netlist for another's now that they do (#306).
soc_top.BUILD_DIR = Path({str(build_dir)!r})

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


CLOCK_NET = "car.clk_sync"
CONSTRAINED = re.compile(r"^(?P<name>[\w.-]+)-at(?P<mhz>\d+)$")


def constrain(arm, mhz):
    """Clone an arm's netlist under a different `clk` constraint.

    THE SAME top.json, so this is the one comparison that changes the constraint
    and nothing else: elaborating at another SYNC_MHZ moves the PLL, the baud
    divisors and a few hundred cells with it. Rewriting the LPF moves only what
    nextpnr is asked for.

    Every arm here is constrained at 60 and closes 65-75, i.e. the tool stops
    working as soon as it passes. Whether the reported Fmax is the netlist's
    ceiling or the slack the router happened to leave is what this measures.
    """
    source, target = arm_dir(arm) / "synth", arm_dir(f"{arm}-at{mhz}") / "synth"
    if not (source / "top.json").exists():
        raise SystemExit(f"{arm}: no netlist -- run `synth` first")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("top.json", "build_top.sh"):
        (target / name).write_bytes((source / name).read_bytes())
    lines, hits = [], 0
    for line in (source / "top.lpf").read_text().splitlines():
        if line.startswith(f'FREQUENCY NET "{CLOCK_NET}"'):
            line = f'FREQUENCY NET "{CLOCK_NET}" {mhz * 1e6:.1f} HZ;'
            hits += 1
        lines.append(line)
    if hits != 1:
        raise SystemExit(f"{arm}: {hits} constraints on {CLOCK_NET} in top.lpf, "
                         f"expected 1 -- the clock was renamed")
    (target / "top.lpf").write_text("\n".join(lines) + "\n")
    emit(f"{arm}-at{mhz}: {arm}'s netlist, {CLOCK_NET} constrained to {mhz} MHz")
    return f"{arm}-at{mhz}"


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
        # The tool's own first ERROR, because "no Program finished normally" is
        # the same string for a design that wants 57 block RAMs on a die with 56
        # as for one that missed its constraint.
        why = next((line.strip() for line in text.splitlines()
                    if line.startswith("ERROR:")), "")
        return {"arm": arm, "seed": seed, "ok": False,
                "seconds": round(elapsed, 1),
                "why": f"rc={proc.returncode} {why or f'no {FINISHED!r}'}"[:160]}
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
            "fmax": {clock: values[-1] for clock, values in fmax.items()},
            # Which plugin owns each clock's critical path, and its logic/routing
            # split -- a per-sample property, so it needs the seed set too.
            "paths": {clock: critical_path(text, clock) for clock in fmax}}


PATH_HEAD = re.compile(r"Critical path report for clock\s+'(\S+)'")
# nextpnr's own summary, and the section terminator. Taken over anything this
# accumulates: the cross-domain reports follow immediately, and a parser that
# runs into them reports an 86 ns path on a 14 ns design.
PATH_TOTAL = re.compile(r"Info:\s+([\d.]+) ns logic,\s+([\d.]+) ns routing")
PATH_STEP = re.compile(r"Info:\s+(clk-to-q|logic|routing|setup)\s+([\d.]+)\s+"
                       r"([\d.]+)\s+(Source|Net|Sink)?\s*(\S+)?")
PATH_TILE = re.compile(r"\((\d+),(\d+)\)\s*->\s*\((\d+),(\d+)\)")
# `TrapPlugin_logic_...`, `PerformanceCounterPlugin_logic_...`: SpinalHDL names
# every net after the plugin that made it, which is what makes a path
# attributable at all.
PLUGIN = re.compile(r"([A-Z][A-Za-z0-9]*Plugin)")


def critical_path(text, clock):
    """Summarise one clock's critical path out of a nextpnr log.

    Returns the logic/routing split, the hop count, the tile bounding box and
    which plugin owns the most nets on it -- the question `--parallel-refine`
    and single-seed builds made unanswerable (#429, #467).
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        found = PATH_HEAD.search(line)
        if found and found.group(1) == clock:
            start = index + 1
            break
    if start is None:
        return None

    logic = routing = 0.0
    hops = 0
    source = sink = None
    xs, ys = [], []
    plugins = {}
    for line in lines[start:]:
        total = PATH_TOTAL.match(line.rstrip())
        if total:
            logic, routing = float(total.group(1)), float(total.group(2))
            break
        if "Critical path report" in line or "Max frequency" in line:
            break
        step = PATH_STEP.match(line.rstrip())
        if step:
            kind, _delay, _total, _role, name = step.groups()
            if kind == "logic":
                hops += 1
            elif kind == "clk-to-q":
                source = name
        if " Sink " in line:
            sink = line.split(" Sink ", 1)[1].strip()
        tile = PATH_TILE.search(line)
        if tile:
            xs += [int(tile.group(1)), int(tile.group(3))]
            ys += [int(tile.group(2)), int(tile.group(4))]
        for name in PLUGIN.findall(line):
            plugins[name] = plugins.get(name, 0) + 1

    def owner_of(cell):
        """The plugin that named the cell, else the module it sits in.

        Falling back to the hierarchy matters: half the endpoints are not
        plugin-named, and "which module" is still the question being asked."""
        found = PLUGIN.search(cell or "")
        if found:
            return found.group(1)
        parts = (cell or "").split(".")
        return ".".join(parts[:2]) if len(parts) > 2 else (cell or "?")

    return {"logic_ns": logic, "routing_ns": routing, "hops": hops,
            # Start and end separately: the path runs BETWEEN plugins, and one
            # majority label hides which end moved.
            "from": owner_of(source), "to": owner_of(sink),
            "busiest": max(plugins, key=plugins.get) if plugins else "(none)",
            "source": source, "sink": sink,
            "bbox": [min(xs), min(ys), max(xs), max(ys)] if xs else None,
            "plugins": plugins}


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
            "TRELLIS_FF": counts.get("TRELLIS_FF", 0),
            # Block RAM, which is what decides whether a cache geometry exists
            # at all: 56 on this die, and 4 ways wants 58.
            "DP16KD": counts.get("DP16KD", 0)}


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


def _betacf(a, b, x):
    """Modified Lentz for the beta continued fraction. Converges for
    x < (a+1)/(a+b+2); `_betainc` reflects the rest."""
    tiny, epsilon = 1e-30, 1e-12
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for num in (m * (b - m) * x / ((a + m2 - 1) * (a + m2)),
                    -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1))):
            d = 1 + num * d
            if abs(d) < tiny:
                d = tiny
            d = 1 / d
            c = 1 + num / c
            if abs(c) < tiny:
                c = tiny
            h *= d * c
        if abs(d * c - 1) < epsilon:
            break
    return h


def _betainc(a, b, x):
    """Regularised incomplete beta.

    The p in every table here goes through this. The previous continued fraction
    returned 0 below `(a+1)/(a+b+2)` and 1 above it -- a step function at the
    reflection point, so every p ever printed by this script was 0.0 or 1.0 and
    the value carried no information beyond the sign of the difference.
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1 - x))
    if x > (a + 1) / (a + b + 2):
        return 1 - _betainc(b, a, 1 - x)
    return front * _betacf(a, b, x) / a


def t_critical(dof, confidence=0.95):
    """Two-sided t* by bisection on the same p the tests use. No SciPy here."""
    target = 1 - confidence
    low, high = 0.0, 100.0
    for _ in range(80):
        mid = (low + high) / 2
        p = _betainc(dof / 2, 0.5, dof / (dof + mid * mid))
        low, high = (mid, high) if p > target else (low, mid)
    return (low + high) / 2


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
    """Paired stats over the seeds both arms have. Same seed, two netlists.

    PAIRING BUYS NOTHING HERE, and that is measured: the same seed on two
    netlists correlates at r = -0.09, +0.05, +0.09 over three arms, so the paired
    and unpaired standard errors agree to 0.03 MHz. A seed is not a difficulty
    both arms inherit -- a different netlist re-rolls the placement outright.

    Kept because it is still the right test for "same seed, two netlists", and
    because the CI it reports is the rig's sensitivity: +/-1.3 MHz at n=40.
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
    half = t_critical(n - 1) * sd / math.sqrt(n) if sd else 0.0
    wins = sum(1 for d in diffs if d > 0)
    # Sign test: how surprising is `wins` out of n if the trim did nothing.
    sign_p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(wins, n - wins) + 1))
                 / 2 ** n)
    return {"n": n, "mean": mean, "sd": sd, "min": min(diffs), "max": max(diffs),
            "median": statistics.median(diffs), "p": p, "wins": wins,
            "sign_p": sign_p, "common": common, "diffs": diffs,
            "lo": mean - half, "hi": mean + half}


def report(arms, clock):
    emit(f"clock under test: {clock}")
    emit()
    emit(f"  {'arm':<24} {'LUT4-eq':>8} {'LUT4':>7} {'CCU2C':>6} {'DPR':>4} "
         f"{'FF':>6} {'BRAM':>5}")
    emit("  " + "-" * 68)
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
             f"{cells['CCU2C']:>6} {cells['DPR16X4']:>4} {cells['TRELLIS_FF']:>6} "
             f"{cells['DP16KD']:>5}")

    emit()
    emit("  Fmax over seeds, MHz")
    for arm, values in series.items():
        emit(f"    {arm:<24} {describe(values)}")

    emit()
    emit("  BEST-OF-N, MHz -- what picking the fastest seed is worth")
    emit(f"    {'arm':<24} {'median':>7} {'best':>7} {'gain':>7} {'best seed':>10}")
    for arm, values in series.items():
        best = max(values)
        seed = max(by_seed[arm], key=lambda s: by_seed[arm][s])
        emit(f"    {arm:<24} {statistics.median(values):>7.2f} {best:>7.2f} "
             f"{best - statistics.median(values):>+7.2f} {seed:>10}")

    # The pairing, visible -- but only while it fits on a line. Past a handful of
    # arms it is a 300-column wall that nobody reads; the JSON is the record.
    if len(series) <= 6:
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
        emit(f"  {'arm':<24} {'n':>3} {'mean d':>8} {'95% CI':>17} {'sd':>6} "
             f"{'paired p':>9} {'faster':>8} {'sign p':>8}")
        emit("  " + "-" * 90)
        for arm in series:
            if arm == "base":
                continue
            got = paired(by_seed["base"], by_seed[arm])
            if not got:
                continue
            interval = f"[{got['lo']:+.2f}, {got['hi']:+.2f}]"
            emit(f"  {arm:<24} {got['n']:>3} {got['mean']:>+8.2f} "
                 f"{interval:>17} {got['sd']:>6.2f} "
                 f"{got['p']:>9.4f} {got['wins']:>3}/{got['n']:<4} "
                 f"{got['sign_p']:>8.4f}")


def sample_paths(arm, clock):
    """Every sampled seed's critical path, re-read from its own nextpnr log.

    From the log rather than from `samples.json`, so a fix to the parser applies
    to samples already taken.
    """
    found = []
    for row in load(arm):
        if not row.get("ok"):
            continue
        log = arm_dir(arm) / f"seed{row['seed']:03d}" / "top.tim"
        if not log.exists():
            continue
        path = critical_path(log.read_text(), clock)
        if path:
            found.append((row["seed"], row["fmax"].get(clock), path))
    return found


def path_report(arms, clock):
    """What the critical path IS, over the seed set, per arm."""
    emit(f"critical path on {clock}, over seeds")
    emit()
    emit(f"  {'arm':<24} {'n':>3} {'logic':>6} {'route':>6} {'route %':>7} "
         f"{'hops':>5} {'span':>5} {'in CPU':>7} {'ends':>5}  "
         f"commonest start -> end")
    emit("  " + "-" * 120)
    for arm in arms:
        found = sample_paths(arm, clock)
        if not found:
            continue
        paths = [path for _seed, _fmax, path in found]
        ends = {}
        for path in paths:
            key = f"{path['from']} -> {path['to']}"
            ends[key] = ends.get(key, 0) + 1
        span = [max(p["bbox"][2] - p["bbox"][0], p["bbox"][3] - p["bbox"][1])
                for p in paths if p["bbox"]]
        logic = statistics.mean(p["logic_ns"] for p in paths)
        route = statistics.mean(p["routing_ns"] for p in paths)
        # Both ends inside the CPU instance. If this is not most of them, the
        # CPU is not what binds the clock, whatever one build said.
        inside = sum(1 for p in paths
                     if (p["source"] or "").startswith("cpu.cpu.")
                     and (p["sink"] or "").startswith("cpu.cpu."))
        emit(f"  {arm:<24} {len(paths):>3} {logic:>6.2f} {route:>6.2f} "
             f"{100 * route / (logic + route):>6.1f}% "
             f"{statistics.mean(p['hops'] for p in paths):>5.1f} "
             f"{statistics.mean(span) if span else 0:>5.1f} "
             f"{100 * inside / len(paths):>6.0f}% {len(ends):>5}  "
             + ", ".join(f"{name} ({count})" for name, count in
                         sorted(ends.items(), key=lambda kv: -kv[1])[:2]))
    emit()
    emit("  logic + routing is the period, so those two columns ARE the Fmax.")
    emit("  `span` is the longer side of the path's tile bounding box, `ends`")
    emit("  the number of DISTINCT start->end pairs the seeds produced: the")
    emit("  critical path's identity is a draw, not a property of the design.")


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
                                          "paths", "constrain", "determinism",
                                          "list"))
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
    parser.add_argument("--mhz", type=int, default=80,
                        help="`constrain` clocks the clone's `clk` at this")
    args = parser.parse_args()

    if args.stage == "list":
        for name in ARMS:
            print(f"  {name}")
        return 0

    arms = args.arm or list(ARMS)
    for arm in arms:
        # `<arm>-at<mhz>` is a `constrain` clone: it has a directory rather than
        # an entry, because its netlist is another arm's.
        found = CONSTRAINED.match(arm)
        if found and found.group("name") in ARMS:
            if not (arm_dir(arm) / "synth" / "top.json").exists():
                raise SystemExit(f"{arm}: run `constrain --arm "
                                 f"{found.group('name')} --mhz {found.group('mhz')}`")
            continue
        if arm not in ARMS:
            raise SystemExit(f"no such arm: {arm}; `list` shows them")

    if args.stage == "synth":
        # Elaboration is Python and yosys is pinned to one thread, so arms
        # synthesise concurrently without contending -- up to `--jobs`, which
        # matters once a matrix is 17 arms on a machine with other work on it.
        # One arm that cannot elaborate must not cost the other nineteen their
        # netlists: a failure is a row, not the end of the batch.
        failed = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(arms), args.jobs)) as pool:
            futures = {pool.submit(synth, arm): arm for arm in arms}
            for future in concurrent.futures.as_completed(futures):
                arm = futures[future]
                try:
                    seconds = future.result()
                except BaseException as error:
                    failed.append(arm)
                    emit(f"{arm}: FAILED -- {str(error).splitlines()[0][:120]}")
                    continue
                cells = occupancy(arm)
                emit(f"{arm}: {seconds:.0f}s  LUT4-eq {cells['lut4_equiv']}  "
                     f"FF {cells['TRELLIS_FF']}  BRAM {cells['DP16KD']}")
                nextpnr_command(arm)  # asserts --parallel-refine is absent
        emit("build_top.sh checked for --parallel-refine on every arm: absent")
        if failed:
            emit(f"{len(failed)} arm(s) have no netlist: {', '.join(failed)}")
        flush_log("synth")
        return 1 if failed else 0

    if args.stage == "constrain":
        for arm in arms:
            constrain(arm, args.mhz)
        flush_log("constrain")
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

    if args.stage == "paths":
        path_report(arms, clock)
        flush_log("paths")
        return 0

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
