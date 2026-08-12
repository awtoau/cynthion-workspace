#!/usr/bin/env python3
#
# Build several variants at once and collect them. #351.
# SPDX-License-Identifier: BSD-3-Clause

"""Fan N gateware builds out across the cores, then say what came back.

    ./scripts/soc_build_fanout.py --ck 80,85,90,95            # a BIST CK ladder
    ./scripts/soc_build_fanout.py --ck 80,85 --jobs 1         # the same, in series
    ./scripts/soc_build_fanout.py CYNTHION_HYPERRAM_BIST=1 ''  # BIST and shipping

Each build is `soc_run.py --build-only`, which touches no hardware -- so a whole
ladder can be built while the board is busy, and the board is then needed only
for the ~10 s of flash-and-measure per rung.

## Why this is possible at all

Every artifact of a build lives under `tmp/awto_soc/build/<variant>/` (#351) and
the CPU generator writes into it under a lock (#306). Before that, one fixed
directory meant a second build overwrote the first's bitstream, firmware and
netlist while both reported success.

Two things are still serialised, by design, and both are announced in the log
when a build waits on them:

  * the firmware toolchain -- one cargo target directory and one ELF path
  * the CPU generator -- sbt's cwd must be the project root

Synthesis, which is the whole cost, is not.

## Re-running the same set retries only what failed

`soc_run.py` skips synthesis when the digest beside a bitstream matches the tree,
so a second run of the same specs costs seconds per rung that already built. That
is the answer to a spurious failure -- and there is one to be spurious about:
yosys asserts in `rtlil.cc` occasionally, and `yosys_rerun_probe.py` shows the
same inputs then passing (#306). Nothing here retries automatically: a build that
fails twice is a build that is failing.

## What it does NOT do

Nothing here touches the board, so nothing here measures anything. It produces
bitstreams; `hyperram_ck_sweep.py` and friends do the measuring.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
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

SOC_RUN = ROOT / "scripts" / "soc_run.py"

# How long one build may take before it is killed and reported.
#
# Waits on: firmware, the CPU generator and synthesis, for one variant.
# One build alone is ~230 s worst case, and each other build in flight adds ~70 s
# of lock wait to the last one -- both measured, and the workings are in #351.
# 1.25x that. On expiry the child is killed and the row names the variant, the
# limit and the elapsed, so an overrun cannot look like a hang.
BASE_SECONDS = 230
LOCKED_SECONDS = 70


def deadline(jobs: int) -> float:
    return 1.25 * (BASE_SECONDS + LOCKED_SECONDS * max(jobs - 1, 0))


def parse_spec(spec: str) -> dict:
    """`KEY=VAL,KEY=VAL` -> an environment overlay. Empty means the default."""
    out = {}
    for item in (s for s in spec.split(",") if s.strip()):
        name, _, value = item.partition("=")
        name = name.strip()
        if name not in {n for n, _d, _t, _k in variant.VARIANT_ENV}:
            raise SystemExit(
                f"{name} is not a variant variable. It would change nothing "
                f"about the build directory, so a sweep over it would build the "
                f"same bitstream N times. Known: "
                f"{', '.join(n for n, _d, _t, _k in variant.VARIANT_ENV)}")
        out[name] = value.strip()
    return out


def one_build(overlay: dict, jobs: int, extra: list[str]) -> dict:
    """Run one `soc_run.py --build-only`. Returns a row describing it."""
    # How many builds share the machine, so the child can size its own synthesis
    # bound. Without it soc_run would use a limit measured on builds that ran
    # alone and kill this one for being concurrent (#295).
    env = {**os.environ, **overlay, "CYNTHION_BUILD_JOBS": str(jobs)}
    slug = variant.slug(env)
    build = variant.build_dir(ROOT, env)
    limit = deadline(jobs)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, str(SOC_RUN), "--build-only", *extra],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=limit)
        rc, output = result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        rc, output = None, ""
        emit(f"{slug}: KILLED at {limit:.0f} s (the limit), having produced "
             f"{'a bitstream' if (build / 'top.bit').exists() else 'none'}")
    elapsed = time.perf_counter() - started

    row = {
        "slug": slug,
        "rc": rc,
        "seconds": elapsed,
        "build_dir": str(build.relative_to(ROOT)),
        "bitstream": None,
        "digest": None,
        "fmax": [],
        "bytes": directory_bytes(build),
    }
    if (build / "top.bit").exists():
        row["bitstream"] = (build / "top.bit").stat().st_size
    if (build / "gateware-digest.txt").exists():
        row["digest"] = (build / "gateware-digest.txt").read_text().strip()
    for line in output.splitlines():
        if "Max frequency for clock" in line:
            row["fmax"].append(line.strip())
        if line.startswith("ERROR:"):
            row.setdefault("errors", []).append(line.strip())
    return row


def directory_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) \
        if path.is_dir() else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("specs", nargs="*", metavar="KEY=VAL,KEY=VAL",
                        help="one variant per argument; empty string = default")
    parser.add_argument("--ck", default="",
                        help="shorthand for a BIST CK ladder: one build per "
                             "comma-separated rung, CYNTHION_HYPERRAM_BIST=1")
    parser.add_argument("--jobs", type=int, default=0,
                        help="builds in flight (default: all of them). --jobs 1 "
                             "is the sequential baseline")
    parser.add_argument("--skip-tests", action="store_true",
                        help="pass --skip-tests to every build. The QEMU gate is "
                             "about the firmware, which a ladder does not vary, "
                             "so it is worth running once and not N times")
    parser.add_argument("--no-parallel-refine", action="store_true",
                        help="pass it to every build. A ladder whose Fmax column "
                             "is the result needs it: --parallel-refine is the "
                             "whole of the spread (#361), so without it a rung "
                             "that closes cannot be told from a lucky placement")
    args = parser.parse_args()

    overlays = [parse_spec(spec) for spec in args.specs]
    overlays += [{"CYNTHION_HYPERRAM_BIST": "1", "CYNTHION_HYPERRAM_CK_MHZ": ck}
                 for ck in (c for c in args.ck.split(",") if c.strip())]
    if not overlays:
        parser.error("nothing to build: give a variant spec or --ck")

    # Distinct variants only. Two identical specs would be one build refusing
    # the other's directory lock, which is a confusing way to say "duplicate".
    seen, unique = set(), []
    for overlay in overlays:
        slug = variant.slug({**os.environ, **overlay})
        if slug in seen:
            emit(f"duplicate variant {slug} ignored")
            continue
        seen.add(slug)
        unique.append(overlay)

    jobs = args.jobs or len(unique)
    extra = (["--skip-tests"] if args.skip_tests else []) + \
            (["--no-parallel-refine"] if args.no_parallel_refine else [])
    emit(f"{len(unique)} build(s), {jobs} at a time, "
         f"{deadline(jobs):.0f} s each before they are killed")
    for overlay in unique:
        emit(f"  {variant.slug({**os.environ, **overlay})}")

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(lambda o: one_build(o, jobs, extra), unique))
    wall = time.perf_counter() - started

    emit("")
    ok = 0
    for row in sorted(rows, key=lambda r: r["slug"]):
        state = "ok" if row["rc"] == 0 else f"FAILED rc={row['rc']}"
        ok += row["rc"] == 0
        emit(f"{row['slug']}: {state} in {row['seconds']:.0f} s, "
             f"{row['bytes'] / 1e6:.0f} MB in {row['build_dir']}")
        if row["digest"]:
            emit(f"  digest {row['digest']}")
        for line in row["fmax"]:
            emit(f"  {line}")
        for line in row.get("errors", [])[-3:]:
            emit(f"  {line}")

    total = sum(row["bytes"] for row in rows)
    emit("")
    emit(f"{ok}/{len(rows)} built in {wall:.0f} s wall clock "
         f"({sum(r['seconds'] for r in rows):.0f} s of build time), "
         f"{total / 1e9:.2f} GB on disk")
    free = shutil.disk_usage(ROOT).free
    emit(f"  {free / 1e9:.0f} GB free; at {total / max(len(rows), 1) / 1e6:.0f} "
         f"MB per variant a 20-rung ladder is "
         f"{20 * total / max(len(rows), 1) / 1e9:.1f} GB")
    emit("  nothing prunes these; scripts/clean_tmp.py --before <date> is the "
         "sweeper, and it moves rather than deletes")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
