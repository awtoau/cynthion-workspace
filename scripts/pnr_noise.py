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
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGDIR = ROOT / "tmp" / "logs"

UTIL_RE = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+\d+%")
FMAX_RE = re.compile(r"^Info: Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz")


def log(msg, handle):
    """Write to terminal and to the run log, so a long sweep is inspectable."""
    print(msg, flush=True)
    handle.write(msg + "\n")
    handle.flush()


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


def run_once(nextpnr, json_path, lpf, outdir, seed, freq, handle):
    """One nextpnr invocation. Returns (util, fmax, seconds) or None on failure."""
    outdir.mkdir(parents=True, exist_ok=True)
    tim = outdir / "top.tim"
    cmd = [
        nextpnr, "--quiet", "--log", str(tim),
        "--12k", "--package", "CABGA256", "--speed", "8",
        "--json", str(json_path), "--lpf", str(lpf),
        "--textcfg", str(outdir / "top.config"),
        "--seed", str(seed),
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
        log(f"  seed {seed}: no utilisation parsed (rc={proc.returncode})", handle)
        log(proc.stderr[-2000:], handle)
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
    ap.add_argument("--name", default="pnr_noise")
    args = ap.parse_args()

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logpath = LOGDIR / f"{args.name}.log"
    results = []
    with open(logpath, "w") as handle:
        log(f"netlist: {args.json}", handle)
        log(f"runs: {args.runs}  target freq: {args.freq}", handle)
        for seed in range(1, args.runs + 1):
            log(f"run seed={seed} ...", handle)
            got = run_once(args.nextpnr, args.json, args.lpf,
                           args.outdir / f"seed{seed}", seed, args.freq, handle)
            if got is None:
                continue
            util, fmax, elapsed = got
            results.append({"seed": seed, "util": util, "fmax": fmax,
                            "seconds": round(elapsed, 1)})
            log(f"  LUT={util.get('TRELLIS_COMB')} FF={util.get('TRELLIS_FF')} "
                f"BRAM={util.get('DP16KD')} DSP={util.get('MULT18X18D')} "
                f"fmax={fmax} {elapsed:.1f}s", handle)

        # The summary is the point: spread, not individual runs.
        log("\n=== spread across runs ===", handle)
        for key in ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "MULT18X18D"):
            vals = [r["util"].get(key) for r in results if key in r["util"]]
            if vals:
                log(f"{key:14s} min={min(vals)} max={max(vals)} "
                    f"spread={max(vals) - min(vals)}", handle)
        clocks = {c for r in results for c in r["fmax"]}
        for clock in sorted(clocks):
            vals = [r["fmax"][clock] for r in results if clock in r["fmax"]]
            if vals:
                spread = max(vals) - min(vals)
                pct = 100.0 * spread / min(vals) if min(vals) else 0.0
                log(f"fmax {clock:28s} min={min(vals):.2f} max={max(vals):.2f} "
                    f"spread={spread:.2f} MHz ({pct:.1f}%)", handle)
        times = [r["seconds"] for r in results]
        if times:
            log(f"runtime  min={min(times):.1f}s max={max(times):.1f}s", handle)

        out = args.outdir / f"{args.name}.json"
        out.write_text(json.dumps(results, indent=2))
        log(f"\nwrote {out}\nlog {logpath}", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
