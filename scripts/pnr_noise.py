#!/usr/bin/env python3
"""Measure nextpnr-ecp5 run-to-run variation on a fixed netlist.

Placement is stochastic: nextpnr seeds its placer, so two runs of the *same*
netlist through the *same* tool do not give identical results. Before any
cross-toolchain difference can be called a finding, we need to know how big a
difference the tool produces against itself.

Utilisation (LUT/FF/BRAM counts) is decided by packing, which is deterministic
given a netlist, so it should not move at all. Fmax is decided by place and
route, which is seeded, so it will. This script separates the two by running
the same JSON through nextpnr several times with different seeds and reporting
the spread of each.

    ./scripts/pnr_noise.py --json tmp/noise/top.json --lpf tmp/noise/top.lpf \
        --runs 4 --freq 120

Anything smaller than the spread reported here is noise, not a result.

## Seeded spread versus the build's own spread

`--seed` is a knob nothing in this project turns: `build_top.sh` passes no seed,
so every real build runs the same one. `--repeat-seed` therefore holds the seed
fixed and varies nothing at all, which is the only way to measure what a REBUILD
spreads by -- and with `--opts "$(fast_build_env.NEXTPNR_OPTS)"`, which this
defaults to, that is `--parallel-refine`'s thread interleaving and nothing else.

`--jobs` runs several at once. Each nextpnr is itself threaded, so this
oversubscribes deliberately: the question is what a rebuild does, and rebuilds on
this machine now happen several at a time (#351).
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from fast_build_env import NEXTPNR_OPTS  # noqa: E402

UTIL_RE = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+\d+%")
FMAX_RE = re.compile(r"^Info: Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz")


def parse_report(text):
    """Pull utilisation and per-clock Fmax out of a nextpnr log."""
    util, fmax = {}, {}
    for line in text.splitlines():
        m = UTIL_RE.match(line)
        if m:
            util[m.group(1)] = int(m.group(2))
        m = FMAX_RE.match(line)
        if m:
            # A clock can be reported more than once (before and after
            # routing). The last value is the post-route one, which is the
            # only one that describes the actual bitstream.
            fmax[m.group(1)] = float(m.group(2))
    return util, fmax


def run_once(nextpnr, json_path, lpf, outdir, seed, freq, opts=(), label=None):
    """One nextpnr invocation. Returns (util, fmax, seconds) or None on failure."""
    outdir.mkdir(parents=True, exist_ok=True)
    tim = outdir / "top.tim"
    cmd = [
        nextpnr, "--quiet", "--log", str(tim),
        "--12k", "--package", "CABGA256", "--speed", "8",
        "--json", str(json_path), "--lpf", str(lpf),
        "--textcfg", str(outdir / "top.config"),
        "--seed", str(seed), *opts,
    ]
    if freq:
        cmd += ["--freq", str(freq)]

    start = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - start

    # nextpnr returns non-zero when it cannot meet the requested frequency,
    # but it still placed and routed the design and still reports a real Fmax.
    # That is exactly the case a binary search needs to read, so a failing
    # exit code is only fatal if no report was produced.
    text = tim.read_text() if tim.exists() else (proc.stdout + proc.stderr)
    util, fmax = parse_report(text)
    if not util:
        emit(f"  seed {seed}: no utilisation parsed (rc={proc.returncode})")
        emit(proc.stderr[-2000:])
        return None
    return util, fmax, elapsed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--lpf", required=True, type=Path)
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--freq", type=float, default=None,
                    help="target frequency; nextpnr optimises toward it")
    ap.add_argument("--outdir", type=Path, default=ROOT / "tmp" / "noise")
    ap.add_argument("--nextpnr", default="nextpnr-ecp5")
    ap.add_argument("--name", default="pnr_noise",
                    help="label for this run in the shared log")
    ap.add_argument("--repeat-seed", type=int, default=None, metavar="SEED",
                    help="hold the seed at SEED for every run: what a plain "
                         "rebuild varies by, since no build passes a seed")
    ap.add_argument("--opts", default=NEXTPNR_OPTS,
                    help="extra nextpnr flags; defaults to the ones every build "
                         "uses. Pass '' for a single-threaded control")
    ap.add_argument("--jobs", type=int, default=1,
                    help="runs in flight")
    args = ap.parse_args()

    opts = args.opts.split()
    seeds = ([args.repeat_seed] * args.runs if args.repeat_seed is not None
             else list(range(1, args.runs + 1)))
    emit(f"{args.name}: netlist {args.json}")
    emit(f"runs: {args.runs}  target freq: {args.freq}  jobs: {args.jobs}")
    emit(f"seeds: {seeds}  opts: {' '.join(opts) or '(none)'}")

    def attempt(indexed):
        index, seed = indexed
        got = run_once(args.nextpnr, args.json, args.lpf,
                       args.outdir / f"run{index}-seed{seed}", seed, args.freq, opts)
        return index, seed, got

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for index, seed, got in sorted(pool.map(attempt, enumerate(seeds, 1)),
                                       key=lambda row: row[0]):
            if got is None:
                continue
            util, fmax, elapsed = got
            results.append({"run": index, "seed": seed, "util": util, "fmax": fmax,
                            "seconds": round(elapsed, 1)})
            emit(f"  run {index} seed={seed}: "
                 f"COMB={util.get('TRELLIS_COMB')} FF={util.get('TRELLIS_FF')} "
                 f"BRAM={util.get('DP16KD')} fmax={fmax} {elapsed:.1f}s")

    # The summary is the point: spread, not individual runs.
    emit("\n=== spread across runs ===")
    for key in ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "MULT18X18D"):
        vals = [r["util"].get(key) for r in results if key in r["util"]]
        if vals:
            emit(f"{key:14s} min={min(vals)} max={max(vals)} "
                f"spread={max(vals) - min(vals)}")
    clocks = {c for r in results for c in r["fmax"]}
    for clock in sorted(clocks):
        vals = [r["fmax"][clock] for r in results if clock in r["fmax"]]
        if vals:
            spread = max(vals) - min(vals)
            pct = 100.0 * spread / min(vals) if min(vals) else 0.0
            emit(f"fmax {clock:28s} min={min(vals):.2f} max={max(vals):.2f} "
                f"spread={spread:.2f} MHz ({pct:.1f}%)")
    times = [r["seconds"] for r in results]
    if times:
        emit(f"runtime  min={min(times):.1f}s max={max(times):.1f}s")

    out = args.outdir / f"{args.name}.json"
    out.write_text(json.dumps(results, indent=2))
    emit(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
