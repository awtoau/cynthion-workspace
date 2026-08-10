#!/usr/bin/env python3
#
# Build the SoC at several L1 geometries and report what each one costs.
# SPDX-License-Identifier: BSD-3-Clause

"""
Synthesise the SoC once per cache geometry and report block RAM and Fmax.

    ./scripts/soc_cache_sweep.py                     # the default set
    ./scripts/soc_cache_sweep.py --geometry 64x2 128x1
    ./scripts/soc_cache_sweep.py --repeat 3          # see the placement spread

Output is mirrored to ./tmp/logs/soc_cache_sweep.log, and the raw rows land in
./tmp/soc_cache_sweep.json.

## The question this exists to settle

Whether to spend spare block RAM on **capacity** (more sets) or on
**associativity** (more ways). They fix different problems:

  * a **capacity** miss is a working set larger than the cache -- more sets
    help.
  * a **conflict** miss is two hot lines whose addresses land on the same
    index. In a direct-mapped cache they evict each other *no matter how big
    the cache is*. Only associativity helps.

- This SoC runs RTIC, which doesn't context-switch (preempts on a single
  stack under a stack-resource policy -- no thread contexts to save), but
  each handler is still its own instruction working set and they interleave:
  tick, I2C, console handlers and idle shell are distinct code streams, and
  more of them means more likely two hot ones collide on an index -- a
  conflict-miss profile, arguing for ways.
- Arguing for sets: `bankCount = wayCount` (`FetchL1Plugin.scala:128`), so
  each way is its own bank of DP16KDs on top of its own tag and PLRU memory
  -- ways cost block RAM faster than sets do, and there are only 8 blocks
  spare. A way also needs a way-select mux in the cache hit path, at an Fmax
  the BTB already had to be `--relaxed-btb` to meet.
- Neither argument settles it. Measuring does.

## Reading the output, and the trap in it

- **Fmax from a single build is not a property of the design.** Two builds
  of the identical 128x1 geometry closed `clk` at 78.04 and 67.76 MHz -- a
  spread wider than most of the differences this sweep is looking for.
  `--repeat` exists because of that: with one build per geometry, the
  ranking is placement noise wearing a result's clothes. Block RAM and cell
  counts ARE deterministic and can be trusted from a single build.

## What this does NOT measure

- Cache hit rate, stalls or IPC -- none of which synthesis can tell you.
  This reports what each geometry COSTS. What it BUYS needs the
  frontend-stall counter read on hardware under a workload that actually
  preempts; `docs/rtic.md` has the only such measurement so far, and it
  predates every geometry here.
- A geometry that is cheaper in block RAM and closes timing is not thereby
  better.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "soc_cache_sweep.log"
RESULTS = ROOT / "tmp" / "soc_cache_sweep.json"

# The stamp `soc_run` uses to decide synthesis can be skipped. Deleted before
# every build here -- see `build_once`, and #287 for what happened without it.
# In this variant's directory, as `soc_run` writes it (#351).
DIGEST = variant.build_dir(ROOT) / "gateware-digest.txt"

# sets x ways. 8 KiB is the geometry on `main`; the others hold the size roughly
# constant and move the associativity, which is the comparison that matters.
#
# 3 ways is deliberately absent and is not an oversight: SpinalHDL's PLRU asserts
# `isPow2` on the way count (`lib/misc/Plru.scala:15`), so the core cannot be
# generated with it.
DEFAULT_GEOMETRIES = [
    (128, 1),   # 8 KiB direct-mapped -- what main has
    (64, 2),    # 8 KiB 2-way          -- same size, associativity instead
    (128, 2),   # 16 KiB 2-way         -- both, if the blocks are there
    (32, 4),    # 8 KiB 4-way          -- associativity pushed as far as it goes
]

FMAX = re.compile(r"Max frequency for clock\s+'([^']+)':\s+([0-9.]+)\s+MHz")
METRICS = re.compile(r"metrics: recorded as build #(\d+) -- "
                     r"(\d+) COMB, (\d+) FF, (\d+) BRAM")


def geometry(text):
    """Parse `128x2` into `(128, 2)`, rejecting a way count PLRU cannot build."""
    match = re.fullmatch(r"(\d+)x(\d+)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a geometry; write it as SETSxWAYS, e.g. 128x2")
    sets, ways = int(match.group(1)), int(match.group(2))
    if ways & (ways - 1):
        raise argparse.ArgumentTypeError(
            f"{ways} ways is not a power of two, and SpinalHDL's PLRU asserts "
            f"isPow2 on it (lib/misc/Plru.scala:15). Use 1, 2 or 4.")
    return sets, ways


def build_once(sets, ways):
    """Build at one geometry. Returns a row, or one with `ok` false and why."""
    emit(f"building {sets} sets x {ways} ways "
         f"({sets * ways * 64 // 1024} KiB per cache)")

    # Patched in the file rather than passed as a flag: `top.py` is the source of
    # truth for CACHE_SETS/CACHE_WAYS and reports both to the firmware through
    # `gateware_id.py`. A flag that bypassed it would measure a core whose
    # geometry disagreed with what the SoC advertises -- which is exactly the
    # drift the constants exist to prevent.
    top = ROOT / "gateware" / "soc" / "top.py"
    original = top.read_text()
    patched, set_hits = re.subn(r"^CACHE_SETS = \d+", f"CACHE_SETS = {sets}",
                                original, count=1, flags=re.M)
    patched, way_hits = re.subn(r"^CACHE_WAYS = \d+", f"CACHE_WAYS = {ways}",
                                patched, count=1, flags=re.M)
    # Count the MATCHES, not whether the text changed. Comparing the text called
    # the one geometry already present in the tree a failure -- `patched ==
    # original` is exactly what a correct no-op substitution produces, so the
    # baseline row was the one row the sweep always dropped.
    missing = [name for name, hits in (("CACHE_SETS", set_hits),
                                       ("CACHE_WAYS", way_hits)) if not hits]
    if missing:
        return {"sets": sets, "ways": ways, "ok": False,
                "why": f"{' and '.join(missing)} not found in top.py at the "
                       f"start of a line -- renamed or moved"}

    # Captured rather than streamed through `devlog.spawn`, because the Fmax and
    # metrics lines have to be parsed out of it. The full text still reaches a
    # file -- per-geometry, so a failed build's output is not overwritten by the
    # next one.
    per_build = LOG.with_name(f"soc_cache_sweep-{sets}x{ways}.log")
    try:
        top.write_text(patched)
        # FORCE SYNTHESIS, and this is not an optimisation to skip.
        #
        # `soc_run.gateware_digest()` hashes the gateware sources and git HEAD,
        # and skips synthesis when the built bitstream already matches. That is
        # right for ordinary iteration and CATASTROPHIC here: it makes a sweep
        # report numbers for a bitstream it did not build.
        #
        # It already happened. The first sweep's 64x2 row logged "synthesis
        # SKIPPED: the bitstream was built from these exact sources" and then
        # published "50 BRAM" -- some previous build's figure, presented in a
        # results table as a measurement of 64x2.
        #
        # It is worse under `--repeat`. The second build of a geometry has
        # byte-identical sources and the same HEAD, so its digest ALWAYS matches
        # the first. Every repeat would skip synthesis and echo the first run's
        # numbers -- and the sweep would report perfect reproducibility as
        # evidence, which is the exact opposite of what --repeat is for.
        if DIGEST.exists():
            DIGEST.unlink()
        result = subprocess.run(
            [sys.executable, str(ROOT / "dev.py"), "build"],
            cwd=ROOT, capture_output=True, text=True, check=False)
    finally:
        # Always, including on an exception: leaving the tree patched would make
        # the next unrelated build silently carry this sweep's geometry.
        top.write_text(original)

    output = (result.stdout or "") + (result.stderr or "")
    per_build.write_text(output)

    if result.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-15:])
        emit(f"  FAILED rc={result.returncode} -- full output in {per_build}")
        for line in tail.splitlines():
            emit(f"    {line}")
        return {"sets": sets, "ways": ways, "ok": False,
                "why": f"build failed rc={result.returncode}",
                "log": str(per_build)}

    # BELT AND BRACES. Deleting the digest above should make this impossible, but
    # a row that was never synthesised is indistinguishable from a real one in
    # the output -- it has a build number, cell counts and a BRAM figure, all
    # belonging to some earlier bitstream. That is precisely how a stale "50
    # BRAM" reached a published table (#287). Refuse the row rather than trust
    # that the deletion worked.
    if "synthesis SKIPPED" in output:
        emit(f"  REFUSED: synthesis was skipped, so the metrics belong to some "
             f"other bitstream -- not to {sets}x{ways}")
        return {"sets": sets, "ways": ways, "ok": False,
                "why": "synthesis was SKIPPED -- the digest matched a previous "
                       "build, so any numbers here describe that one",
                "log": str(per_build)}

    row = {"sets": sets, "ways": ways, "ok": True,
           "log": str(per_build),
           "kib": sets * ways * 64 // 1024,
           "clocks": {name: float(mhz) for name, mhz in FMAX.findall(output)}}
    found = METRICS.search(output)
    if found:
        row.update(build=int(found.group(1)), comb=int(found.group(2)),
                   ff=int(found.group(3)), bram=int(found.group(4)))
    return row


def report(rows):
    emit("")
    emit(f"{'geometry':>10}  {'per cache':>9}  {'BRAM':>5}  {'COMB':>6}  "
         f"{'FF':>6}  {'clk MHz':>8}")
    for row in rows:
        name = f"{row['sets']}x{row['ways']}"
        if not row.get("ok"):
            emit(f"{name:>10}  {row.get('why', 'failed')}")
            continue
        clk = row.get("clocks", {}).get("$glbnet$clk")
        emit(f"{name:>10}  {row['kib']:>7} KiB  {row.get('bram', 0):>5}  "
             f"{row.get('comb', 0):>6}  {row.get('ff', 0):>6}  "
             f"{clk if clk else '--':>8}")

    ok = [r for r in rows if r.get("ok")]
    if len({(r["sets"], r["ways"]) for r in ok}) < len(ok):
        spread = {}
        for row in ok:
            spread.setdefault((row["sets"], row["ways"]), []).append(
                row.get("clocks", {}).get("$glbnet$clk"))
        emit("")
        emit("PLACEMENT SPREAD -- the reason --repeat exists")
        for (sets, ways), values in spread.items():
            got = [v for v in values if v]
            if len(got) > 1:
                emit(f"  {sets}x{ways}: {', '.join(f'{v:.2f}' for v in got)} MHz "
                     f"-- range {max(got) - min(got):.2f}")

    emit("")
    emit("Block RAM and cell counts are deterministic. Fmax from ONE build is a")
    emit("property of that placement, not of the design -- 128x1 has measured")
    emit("both 78.04 and 67.76 MHz. Do not rank geometries on a single number.")
    emit("")
    emit("This says what each geometry COSTS. What it BUYS is the frontend-stall")
    emit("counter under a workload that preempts, which synthesis cannot tell you.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geometry", nargs="*", type=geometry,
                        default=DEFAULT_GEOMETRIES,
                        help="SETSxWAYS, e.g. 128x1 64x2. Ways must be a power of two.")
    parser.add_argument("--repeat", type=int, default=1,
                        help="builds per geometry; >1 exposes the placement spread")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    emit(f"cache geometry sweep: {len(args.geometry)} geometries "
         f"x {args.repeat} build(s)")

    rows = []
    for sets, ways in args.geometry:
        for _ in range(args.repeat):
            rows.append(build_once(sets, ways))
            RESULTS.write_text(json.dumps(rows, indent=2))

    report(rows)
    emit(f"rows -> {RESULTS}")
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
