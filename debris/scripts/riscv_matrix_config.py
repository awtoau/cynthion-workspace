#!/usr/bin/env python3
#
# Generate the VexiiRiscv sweep configuration for a bare-metal 32-bit target.
# SPDX-License-Identifier: BSD-3-Clause

"""
Writes the profile matrix for the cores that are actually candidates.

`riscv/scripts/62_generate_exhaustive_profile_matrix.py` produced the
archived sweep, and two of its defaults do not fit this target:

  --with-supervisor   defaults on. Supervisor mode exists to run Linux with an
                      MMU; moondancer is bare-metal firmware and will never
                      enter S-mode, so it is area spent on nothing.
  --xlen 64           defaults to 64-bit. Already ruled out -- no advantage on
                      this device -- so sweeping it spends most of the build
                      time informing a decision that is made.

It also fixes both caches at 64 sets x 1 way (4 KiB) and never varies them.
Cache size is the axis that matters most here, because block RAM is what the
12F is short of: 56 DP16KD blocks, 112 KiB, shared between the CPU, the
firmware it runs and the USB buffers. Doubling cache sets doubles the blocks
those caches consume, so the question "what fits" is answered by this sweep or
not at all.

Cache size cuts against Fmax as well: larger caches lengthen tag-compare paths.
And a miss here is cheap, since HyperRAM behind the core streams at 220 MB/s --
which weakens the case for large caches compared with a system whose backing
store is slow. The area and timing halves of that trade are measurable now; the
hit-rate half needs CoreMark, which needs CPU bring-up.

    ./scripts/riscv_matrix_config.py
    ./scripts/riscv_matrix_config.py --cache-sets 64 128
"""

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "riscv" / "config" / "profile_matrix_baremetal_x32.json"
LOG = ROOT / "tmp" / "riscv_matrix_config.log"

# Sets per way. The cache is sets x ways x 64-byte lines, so at one way these
# are 4, 8 and 16 KiB. 16 KiB is included to find the block-RAM wall by
# measurement rather than by estimate -- a configuration that does not leave
# room for firmware is a useful result, not a wasted build.
CACHE_SETS = [64, 128, 256]

# Bare-metal: RVM (multiply), RVC (compressed), rdtime. No supervisor mode.
# RVA (atomics) is swept as a base variant because moondancer uses it and the
# archived sweep showed it interacts with the LSU cache requirement.
BASE_FLAGS = ["--xlen", "32", "--with-rvm", "--with-rvc", "--with-rdtime"]


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def valid(flags):
    """Which feature combinations the generator will accept.

    Mirrors the constraints in the original generator: the data cache needs the
    instruction cache, branch prediction needs both, and gshare/ras are
    refinements of the BTB rather than alternatives to it.
    """
    if flags["lsu_l1"] and not flags["fetch_l1"]:
        return False
    advanced = flags["btb"] or flags["gshare"] or flags["ras"] or flags["dual"]
    if advanced and not (flags["fetch_l1"] and flags["lsu_l1"]):
        return False
    if (flags["gshare"] or flags["ras"]) and not flags["btb"]:
        return False
    return True


def combinations():
    """Every valid feature combination, ordered by how much is enabled."""
    keys = ["fetch_l1", "lsu_l1", "btb", "gshare", "ras", "dual"]
    found = []
    for bits in itertools.product([False, True], repeat=len(keys)):
        flags = dict(zip(keys, bits))
        if valid(flags):
            found.append(flags)
    return sorted(found, key=lambda f: (sum(f.values()),
                                        tuple(f[k] for k in keys)))


def tokens(flags, sets):
    """Feature tokens for the output name, cache size included.

    Size is in the name because two builds differing only in cache size are
    different builds, and the archived sweep's names could not express that --
    which is part of why its results could not be told apart.
    """
    parts = []
    kib = sets * 64 // 1024
    if flags["fetch_l1"]:
        parts.append(f"i{kib}k")
    if flags["lsu_l1"]:
        parts.append(f"d{kib}k")
    for key in ("btb", "gshare", "ras"):
        if flags[key]:
            parts.append(key)
    if flags["dual"]:
        parts.append("dual")
    return parts


def sbt_args(flags, sets, with_rva, is_soc):
    args = list(BASE_FLAGS)
    if with_rva:
        args.append("--with-rva")
    if flags["fetch_l1"]:
        args += ["--with-fetch-l1", "--fetch-l1-sets", str(sets),
                 "--fetch-l1-ways", "1"]
    if flags["lsu_l1"]:
        args += ["--with-lsu-l1", "--lsu-l1-sets", str(sets),
                 "--lsu-l1-ways", "1"]
    if flags["btb"]:
        args.append("--with-btb")
    if flags["gshare"]:
        args.append("--with-gshare")
    if flags["ras"]:
        args.append("--with-ras")
    if flags["dual"]:
        args.append("--dual-issue")
    if is_soc:
        args += ["--jtag-tap", "false"]
    return args


def build_profiles(cache_sets):
    """Every profile the sweep should build.

    Both core and SoC builds get an `output_prefix`. The archived sweep gave
    one only to SoC builds, which left core results with no way to recover
    their configuration from the filename -- every core row in the first
    attempt at a report came out labelled as having no features at all.
    """
    profiles = []
    index = 0

    for with_rva in (False, True):
        base = "rva" if with_rva else "im"
        for flags in combinations():
            cached = flags["fetch_l1"] or flags["lsu_l1"]
            # Cache size only means something when there is a cache; without
            # one the sweep would build the same design three times.
            for sets in (cache_sets if cached else [cache_sets[0]]):
                # MicroSoc's cacheless LSU cannot carry atomics, so RVA
                # requires the data cache.
                soc_ok = flags["lsu_l1"] or not with_rva

                index += 1
                names = tokens(flags, sets)
                suffix = "_".join(names) if names else "base"
                stem = f"x32_{base}"

                profiles.append({
                    "name": f"{stem}_core_{index:03d}",
                    "kind": "core_dev",
                    "sbt_main": "vexiiriscv.Generate",
                    "sbt_args": sbt_args(flags, sets, with_rva, False),
                    "output_prefix": f"{stem}_core_{index:03d}_{suffix}",
                    "tag": f"core_{stem}_{suffix}",
                    "cache_sets": sets if cached else 0,
                    "notes": f"core {base} " + (" + ".join(names) or "base"),
                })

                if soc_ok:
                    profiles.append({
                        "name": f"{stem}_soc_{index:03d}",
                        "kind": "microsoc_direct",
                        "sbt_main": "vexiiriscv.soc.micro.MicroSocGen",
                        "sbt_args": sbt_args(flags, sets, with_rva, True),
                        "top_module": "MicroSoc",
                        "output_prefix": f"{stem}_soc_{index:03d}_{suffix}",
                        "tag": f"soc_{stem}_{suffix}_clint_uart",
                        "cache_sets": sets if cached else 0,
                        "notes": f"soc {base} "
                                 + (" + ".join(names) or "base")
                                 + " + clint + uart",
                    })

    return profiles


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-sets", type=int, nargs="+",
                        default=CACHE_SETS,
                        help="sets per way to sweep (64 sets = 4 KiB)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    profiles = build_profiles(args.cache_sets)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"profiles": profiles}, indent=2) + "\n")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, f"wrote {args.out}")
        emit(handle)
        cores = [p for p in profiles if p["kind"] == "core_dev"]
        socs = [p for p in profiles if p["kind"] == "microsoc_direct"]
        emit(handle, f"  {len(profiles)} profiles: {len(cores)} core, "
                     f"{len(socs)} SoC")
        emit(handle, f"  cache sets swept: {args.cache_sets} "
                     f"({', '.join(str(s * 64 // 1024) + ' KiB' for s in args.cache_sets)})")
        emit(handle, f"  bases: im (no atomics), rva (atomics)")
        emit(handle, "  supervisor mode: never -- bare metal, no MMU")
        emit(handle, "  xlen: 32 only -- 64-bit ruled out for this device")
        emit(handle)
        emit(handle, "Every profile carries an output_prefix, core builds "
                     "included, so a")
        emit(handle, "result file can always be traced back to the "
                     "configuration that")
        emit(handle, "produced it.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
