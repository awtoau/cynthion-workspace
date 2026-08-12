#!/usr/bin/env python3
#
# The highest `sync` a variant closes at on EVERY placer seed. #432.
# SPDX-License-Identifier: BSD-3-Clause

"""One variant, several `SYNC_MHZ` rungs, several `nextpnr --seed` values each.

    ./scripts/soc_sync_ladder.py --spec CYNTHION_HYPERRAM_DQS=1
    ./scripts/soc_sync_ladder.py --spec '' --sync 60 --seeds 1,2,3,4

## The question, and why seeds rather than repeats

"Does it close at 60" was answered from four builds at one default seed, and
those four were four different NETLISTS: elaboration is not reproducible (#441),
so the spread mixed placement with synthesis. `nextpnr-ecp5 --seed` varies the
placement of ONE netlist, which is the distribution a closure claim needs.

A rung counts as closed only when every seed clears it. One seed passing is the
same evidence as one build passing, and that is what #432 already refuted.

## What is checked before a number is believed

  * `solve_pll` must reach the rung exactly -- an unreachable rung is skipped
    with its reason, not built and rounded.
  * `build_top.sh` must carry `--seed` and must NOT carry `--parallel-refine`.
    The documented `AMARANTH_nextpnr_opts` override silently did nothing until
    #429, so the flags are read back from the script the build ran.
  * the nextpnr log must say `Program finished normally.` A build killed by its
    own bound leaves post-placement estimates that parse as a result (#440).

Rungs run in parallel -- one build directory per variant per rung, so they do
not collide. Seeds within a rung run in series, because they share one.
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp" / "sync-ladder"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit  # noqa: E402
from soc import clocks, variant  # noqa: E402

SOC_RUN = ROOT / "scripts" / "soc_run.py"

# The constraint as well as the achieved rate: which domain BINDS is the ratio of
# the two, and the achieved figure alone cannot say.
FMAX_RE = re.compile(
    r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz\s+"
    r"\((PASS|FAIL) at ([\d.]+) MHz\)")

# What a nextpnr log says when it reached the end -- present on a FAIL too. #440.
FINISHED = "Program finished normally."

# Seconds one build may take, before the concurrency term.
#
#   waits for   firmware, the CPU generator, yosys and nextpnr, for one variant
#   expected    256 s, the slowest COMPLETED merged build (#432); each further
#               build in flight adds ~70 s of lock wait (#351)
#   multiplier  1.25x
#   on expiry   the child is killed and the row is NO RESULT, naming the limit
BASE_SECONDS = 256
LOCKED_SECONDS = 70


def deadline(jobs):
    return 1.25 * (BASE_SECONDS + LOCKED_SECONDS * max(jobs - 1, 0))


def parse_spec(spec):
    out = {}
    for item in (s for s in spec.split(",") if s.strip()):
        name, _, value = item.partition("=")
        if name.strip() not in {n for n, _d, _t, _k in variant.VARIANT_ENV}:
            raise SystemExit(f"{name} is not a variant variable; it would not "
                             f"change the build directory")
        out[name.strip()] = value.strip()
    return out


def timing(text):
    """{clock: {mhz, target, status, margin}} from a COMPLETED nextpnr log."""
    if FINISHED not in text:
        return {}
    return {clock.split("$")[-1]: {
        "mhz": float(mhz), "target": float(target), "status": status,
        "margin": round(float(mhz) / float(target) - 1, 4)}
        for clock, mhz, status, target in FMAX_RE.findall(text)}


def pnr_flags(build):
    """The nextpnr command line the build ACTUALLY ran (#429)."""
    script = build / "build_top.sh"
    if not script.exists():
        return None
    for line in script.read_text().splitlines():
        if "NEXTPNR_ECP5" in line and "--json" in line:
            return line.strip()
    return None


def one_build(overlay, sync_mhz, seed, jobs):
    """One build of one variant at one rung on one seed. Returns the row."""
    env = {**os.environ, **overlay,
           "CYNTHION_SYNC_MHZ": f"{sync_mhz:g}",
           "CYNTHION_BUILD_JOBS": str(jobs),
           "CYNTHION_SYNTHESIS_FLOOR_SECONDS": str(int(deadline(jobs) * 0.9))}
    slug = variant.slug(env)
    build = variant.build_dir(ROOT, env)
    # Make this a synthesis rather than a file copy of the previous seed's.
    (build / "gateware-digest.txt").unlink(missing_ok=True)
    (build / "top.tim").unlink(missing_ok=True)

    limit = deadline(jobs)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, str(SOC_RUN), "--build-only", "--skip-tests",
             "--no-parallel-refine", "--seed", str(seed)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=limit)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        rc = None
        emit(f"{slug} seed {seed}: KILLED at the {limit:.0f} s limit")
    elapsed = time.perf_counter() - started

    tim = build / "top.tim"
    text = tim.read_text() if tim.exists() else ""
    keep = OUT / slug / f"seed{seed}"
    keep.mkdir(parents=True, exist_ok=True)
    if text:
        shutil.copy2(tim, keep / "top.tim")
    flags = pnr_flags(build)

    clocks_seen = timing(text)
    row = {"slug": slug, "sync_mhz": sync_mhz, "seed": seed, "rc": rc,
           "seconds": round(elapsed, 1), "clocks": clocks_seen,
           "nextpnr": flags,
           "seed_reached_nextpnr": bool(flags) and f"--seed {seed}" in flags,
           "deterministic": bool(flags) and "--parallel-refine" not in flags,
           "finished": FINISHED in text,
           "verdict": ("NO RESULT" if not clocks_seen else
                       "CLOSES" if all(c["status"] == "PASS"
                                       for c in clocks_seen.values())
                       else "MISSES")}
    row["binds"] = (min(clocks_seen, key=lambda n: clocks_seen[n]["margin"])
                    if clocks_seen else None)
    if not row["seed_reached_nextpnr"]:
        emit(f"  *** {slug} seed {seed}: --seed did NOT reach nextpnr (#429); "
             f"this row is not evidence")
    if not row["deterministic"]:
        emit(f"  *** {slug} seed {seed}: --parallel-refine PRESENT (#429)")
    emit(f"  {slug} seed {seed}: {row['verdict']:9s} {elapsed:.0f}s "
         f"binds={row['binds']}  "
         + "  ".join(f"{n}={c['mhz']:.2f}/{c['target']:.0f}"
                     for n, c in sorted(clocks_seen.items())))
    return row


def elaborate(overlay, sync_mhz):
    """Amaranth only. A rung the design refuses costs seconds here, minutes there."""
    env = {**os.environ, **overlay, "CYNTHION_SYNC_MHZ": f"{sync_mhz:g}"}
    code = ("import sys; sys.path[:0] = ['gateware', 'gateware/soc']\n"
            "from board.cynthion_r1_4 import CynthionPlatformRev1D4\n"
            "import top\n"
            "CynthionPlatformRev1D4().build(top.AwtoSoc(firmware=[0] * 16),\n"
            "                               do_build=False)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=300)
    ok = result.returncode == 0
    emit(f"  SYNC {sync_mhz:>4g}: {'elaborates' if ok else 'REFUSED'}"
         + ("" if ok else "  " + (result.stderr or "").strip().splitlines()[-1]))
    return ok


def rung(overlay, sync_mhz, seeds, jobs):
    """Every seed at one rung, in series -- they share a build directory."""
    return [one_build(overlay, sync_mhz, seed, jobs) for seed in seeds]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", default="",
                        help="variant overlay, KEY=VAL,KEY=VAL; empty = shipping")
    parser.add_argument("--sync", default="60,50,48,45,40,36,30",
                        help="the rungs to walk, highest first")
    parser.add_argument("--seeds", default="1,2,3,4",
                        help="nextpnr placer seeds; a rung closes only if all do")
    parser.add_argument("--jobs", type=int, default=0,
                        help="rungs in flight (default: all of them)")
    parser.add_argument("--label", default=None,
                        help="names this ladder's json; defaults to the spec")
    parser.add_argument("--elaborate", action="store_true",
                        help="preflight only: elaborate every rung and stop")
    args = parser.parse_args()

    overlay = parse_spec(args.spec)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    wanted = [float(s) for s in args.sync.split(",") if s.strip()]

    # REACHABLE, before anything is built. `solve_pll` refuses an inexact match,
    # so an unreachable rung is a refusal minutes in rather than a skip now.
    rungs, refused = [], []
    for mhz in wanted:
        if clocks.solve_pll(mhz, clocks.USB_PHY_MHZ) is None:
            refused.append(mhz)
        else:
            rungs.append(mhz)
    for mhz in refused:
        near = clocks.reachable(mhz - 8, mhz + 8, clocks.USB_PHY_MHZ)
        emit(f"SYNC_MHZ {mhz:g}: no exact PLL solution from the 60 MHz "
             f"oscillator. Reachable nearby: {near}")

    jobs = args.jobs or len(rungs)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.elaborate:
        emit(f"preflight: elaborating {args.spec or 'shipping'} at {rungs}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            ok = list(pool.map(lambda m: elaborate(overlay, m), rungs))
        return 0 if all(ok) else 1
    emit(f"ladder: spec={args.spec or 'shipping'} rungs={rungs} seeds={seeds} "
         f"jobs={jobs} limit={deadline(jobs):.0f}s/build")

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(rung, overlay, mhz, seeds, jobs) for mhz in rungs]
        for future in concurrent.futures.as_completed(futures):
            rows += future.result()

    emit("\n=== the ladder ===")
    ladder = {}
    for mhz in rungs:
        at = [r for r in rows if r["sync_mhz"] == mhz]
        clk = sorted(r["clocks"]["clk"]["mhz"] for r in at if "clk" in r["clocks"])
        closed = [r for r in at if r["verdict"] == "CLOSES"]
        ladder[f"{mhz:g}"] = {
            "seeds": len(at), "closes": len(closed),
            "clk_min": min(clk) if clk else None,
            "clk_median": clk[len(clk) // 2] if clk else None,
            "clk_max": max(clk) if clk else None,
            "binds": sorted({r["binds"] for r in at if r["binds"]})}
        emit(f"  SYNC {mhz:>4g}: {len(closed)}/{len(at)} seeds close  "
             + (f"clk {min(clk):.2f}/{clk[len(clk) // 2]:.2f}/{max(clk):.2f} "
                f"(min/med/max)  " if clk else "no clk report  ")
             + f"binds={','.join(ladder[f'{mhz:g}']['binds'])}")

    every = [mhz for mhz in rungs
             if ladder[f"{mhz:g}"]["closes"] == len(seeds)]
    emit(f"\nhighest rung closing on EVERY seed: "
         f"{max(every):g} MHz" if every else
         "\nno rung closed on every seed")

    label = args.label or (args.spec.replace("=", "").replace(",", "-")
                           or "shipping")
    record = OUT / f"{label}.json"
    record.write_text(json.dumps(
        {"spec": args.spec, "seeds": seeds, "refused": refused,
         "ladder": ladder, "rows": rows}, indent=2))
    emit(f"wrote {record.relative_to(ROOT)}")
    return 0 if every else 1


if __name__ == "__main__":
    sys.exit(main())
