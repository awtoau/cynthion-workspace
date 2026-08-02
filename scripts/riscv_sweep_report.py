#!/usr/bin/env python3
#
# Turn the recovered VexiiRiscv sweep logs into a readable table.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds a feature-column table from the earlier VexiiRiscv sweep.

The sweep left 57 timing summaries and several hundred nextpnr logs, but no
consolidated report. Configuration lives in `riscv/config/*.json`, and the
build scripts name their outputs in three different ways depending on which
generator ran:

  microsoc_exh_09_i4k_d4k_btb   SoC builds carry an `output_prefix` that spells
                                the features out
  x32_core_exh_01_j0021         core builds have no `output_prefix` at all --
                                the features are only reachable by looking
                                `x32_core_exh_01` up in the config
  core_i4k_d4k_bpred_dual       an earlier scheme, before branch prediction was
                                split into btb/gshare/ras

So features are resolved from the config JSON by profile name, never guessed
from the filename. Guessing was the first attempt at this script and it was
wrong for 22 of 34 rows: the core builds have nothing to guess from, so they all
rendered as "no features enabled" -- 17 rows silently asserting a configuration
that was never built, sitting next to LUT counts that differed by 50% and gave
the lie away.

Each feature gets its own column and a tick. Reading down a column answers "what
does this feature cost", which is the question worth asking, and reading across
answers "what is in this build". Neither requires parsing anything.

The same rule applies to any future sweep: **one column per varied factor, not
a concatenated name.** A table whose rows differ in several dimensions at once
is only useful if the dimensions are separable at a glance. That includes the
base ISA -- rows are keyed on XLEN and the atomics/supervisor choice as well as
on the features, because merging across those compares cores that are not the
same core.

    ./scripts/riscv_sweep_report.py
    ./scripts/riscv_sweep_report.py --sort lut
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The sweep outputs are large and live outside the workspace on this machine;
# set RISCV_SWEEP_OUT to wherever 60_run_sweep.py wrote them.
SWEEP = Path(os.environ.get("RISCV_SWEEP_OUT",
                            ROOT / "riscv" / "out" / "sim"))
CONFIG = ROOT / "riscv" / "config"
LOG = ROOT / "tmp" / "riscv_sweep_report.log"

# The features the sweep varied. `i4k`/`d4k` are the instruction and data
# caches; `btb`, `gshare` and `ras` are branch prediction; `dual` is dual issue.
# `bpred` appears only in the earliest runs, before branch prediction was split
# into three separate switches -- it is kept as its own column rather than
# folded into btb, because a build that predates the split is not the same
# configuration as one that enables btb specifically.
FEATURES = ["i4k", "d4k", "btb", "gshare", "ras", "dual", "bpred"]

FEATURE_LABELS = {
    "i4k":    "I$",
    "d4k":    "D$",
    "btb":    "BTB",
    "gshare": "GShare",
    "ras":    "RAS",
    "dual":   "Dual",
    "bpred":  "BPred",
}

# Feature tokens sit between the base-ISA stem and any trailing `_clint_uart`,
# so the stem tokens have to be recognised to be skipped.
BASE_TOKENS = {"core", "soc", "x32", "x64", "m32", "sv", "rva", "rvm", "rvc",
               "rdtime", "clint", "uart", "base"}


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def load_profiles():
    """Map every profile name to its configuration.

    Most names carry an `x32`/`m32`/`x64` prefix identifying the run they
    belong to and are unique. The unprefixed `core_exh_NN` names in
    `profile_matrix_exhaustive.json` (x64) and `..._x32.json` (x32) are not:
    the same name means different things in each file.

    Ambiguous names are kept as a list of candidates rather than resolved by
    last-file-wins. A name with more than one candidate can only be reported if
    no result actually uses it -- which `collect` checks, instead of this
    function guessing.
    """
    profiles = {}

    for path in sorted(CONFIG.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            print(f"  skipping {path.name}: {error}")
            continue

        for profile in payload.get("profiles", []):
            name = profile.get("name")
            if not name:
                continue
            record = {
                "tag":    profile.get("tag", ""),
                "kind":   profile.get("kind", ""),
                "prefix": profile.get("output_prefix"),
                "args":   profile.get("sbt_args", []),
                "source": path.name,
            }
            candidates = profiles.setdefault(name, [])
            if not any(c["tag"] == record["tag"] for c in candidates):
                candidates.append(record)

    return profiles


def features_from_tag(tag):
    """Which features a tag encodes, and the base ISA it was built on.

    Tags look like `m32_core_x32_rva_rvm_rvc_rdtime_i4k_d4k_btb`. Everything
    that is not a known base token is a feature.
    """
    tokens = tag.split("_")
    features = {feature: False for feature in FEATURES}

    for token in tokens:
        if token in features:
            features[token] = True

    xlen = "64" if "x64" in tokens else ("32" if "x32" in tokens else "?")
    # The base profile is either supervisor-mode or atomics; the generator
    # never emitted both in this sweep.
    if "rva" in tokens:
        base = "rva"
    elif "sv" in tokens:
        base = "sv"
    else:
        base = "-"

    return features, xlen, base


def resolve(name, profiles):
    """Find the profile a result filename came from.

    Returns (tag, kind, stem, ambiguous). The job-id suffix is stripped first,
    so `microsoc_exh_12_..._j0030` and `microsoc_exh_12_...` resolve to the one
    configuration -- they are repeat builds of it, not two designs.

    `ambiguous` is set when the name matches several profiles with different
    tags, which happens for the unprefixed `core_exh_NN` names. Such a result
    is not placed in the table, because there is no way to tell which
    configuration produced it.
    """
    stem = re.sub(r"_j\d+$", "", name)

    # SoC builds: the filename is the output_prefix verbatim. This is the
    # stronger match -- the prefix spells the features out, so it identifies a
    # configuration even when several config files reuse the profile *name*
    # around it. Tried first and trusted when it hits.
    matches = [record
               for candidates in profiles.values()
               for record in candidates
               if record["prefix"] == stem]

    if matches:
        tags = {record["tag"] for record in matches}
        if len(tags) > 1:
            return None, None, stem, True
        return matches[0]["tag"], matches[0]["kind"], stem, False

    # Core builds: the filename is the profile name verbatim, and carries no
    # feature information of its own. Here a repeated name really is ambiguous.
    matches = profiles.get(stem, [])
    if not matches:
        return None, None, stem, False

    tags = {record["tag"] for record in matches}
    if len(tags) > 1:
        return None, None, stem, True

    return matches[0]["tag"], matches[0]["kind"], stem, False


def collect(profiles):
    """Gather every configuration with a timing result.

    Repeated builds of one configuration are collapsed to their best Fmax: the
    same profile was built several times and the spread between runs is
    place-and-route noise rather than a property of the design.
    """
    results = {}
    unresolved = []
    ambiguous = []

    for path in sorted(SWEEP.glob("*timing_summary.txt")):
        text = path.read_text(errors="replace")
        match = re.search(r"max_frequencies_mhz=([\d., ]+)", text)
        if not match:
            continue

        frequencies = [float(v) for v in match.group(1).split(",") if v.strip()]
        if not frequencies:
            continue

        name = path.name.replace("_timing_summary.txt", "")
        tag, kind, stem, is_ambiguous = resolve(name, profiles)

        if is_ambiguous:
            ambiguous.append(name)
            continue

        if tag is None:
            # An older naming scheme with the features in the filename, from
            # before the config-driven runs. Recognised explicitly so it is
            # reported as legacy rather than dropped or mistaken for a modern
            # row -- its `bpred` is not the same switch as `btb`.
            if stem.startswith("core_") or stem.startswith("microsoc_uart"):
                tag, kind = stem, "legacy"
            else:
                unresolved.append(name)
                continue

        best = max(frequencies)
        if tag not in results:
            results[tag] = {"fmax": best, "runs": 1, "name": name, "kind": kind}
        else:
            results[tag]["runs"] += 1
            if best > results[tag]["fmax"]:
                results[tag]["fmax"] = best
                results[tag]["name"] = name

    return results, unresolved, ambiguous


def area_for(name):
    """Area from the matching nextpnr log, if one exists.

    The timing summaries do not carry area, so this is a separate lookup and
    frequently misses -- reported as blank rather than guessed.
    """
    for candidate in sorted(SWEEP.glob(f"{name}*nextpnr.log")):
        text = candidate.read_text(errors="replace")
        lut = re.search(r"TRELLIS_COMB:\s+(\d+)/", text)
        if lut:
            ff = re.search(r"TRELLIS_FF:\s+(\d+)/", text)
            bram = re.search(r"DP16KD:\s+(\d+)/", text)
            return (int(lut.group(1)),
                    int(ff.group(1)) if ff else None,
                    int(bram.group(1)) if bram else None)
    return (None, None, None)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sort", choices=["fmax", "lut"], default="fmax")
    args = parser.parse_args()

    if not SWEEP.exists():
        print(f"sweep data not found at {SWEEP}")
        return 1
    if not CONFIG.exists():
        print(f"sweep configuration not found at {CONFIG}")
        return 1

    profiles = load_profiles()
    results, unresolved, ambiguous = collect(profiles)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        emit(handle, "VexiiRiscv feature sweep, recovered from the earlier work")
        emit(handle, f"{len(results)} configurations resolved against "
                     f"{len(profiles)} profiles in riscv/config")
        emit(handle)

        header = "  " + "".join(f"{FEATURE_LABELS[f]:>8}" for f in FEATURES)
        emit(handle, f"{header}{'XLEN':>6}{'base':>6}{'Fmax':>9}"
                     f"{'LUT4':>8}{'BRAM':>6}{'runs':>6}  kind")
        emit(handle, "  " + "-" * (8 * len(FEATURES) + 43))

        rows = []
        for tag, data in results.items():
            features, xlen, base = features_from_tag(tag)
            lut, _, bram = area_for(data["name"])
            rows.append((data["kind"], features, xlen, base,
                         data["fmax"], lut, bram, data["runs"]))

        # Sort by XLEN first: the sweep's largest effect by far is the ISA
        # width, so grouping on it keeps the feature comparisons within a
        # group where they mean something.
        if args.sort == "fmax":
            rows.sort(key=lambda r: (r[2], -r[4]))
        else:
            rows.sort(key=lambda r: (r[2], r[5] is None, r[5] or 0))

        for kind, features, xlen, base, fmax, lut, bram, runs in rows:
            ticks = "".join(f"{'*' if features[f] else '.':>8}"
                            for f in FEATURES)
            lut_text = f"{lut:>8}" if lut else f"{'--':>8}"
            bram_text = f"{bram:>6}" if bram is not None else f"{'--':>6}"
            emit(handle, f"  {ticks}{xlen:>6}{base:>6}{fmax:>8.1f}M"
                         f"{lut_text}{bram_text}{runs:>6}  {kind}")

        emit(handle)
        emit(handle, "* present, . absent. Fmax is the best of repeated runs: "
                     "the same")
        emit(handle, "configuration was built several times and the spread is "
                     "place-and-route")
        emit(handle, "noise rather than a property of the design.")
        emit(handle)
        emit(handle, "XLEN and base are columns because the sweep varied them "
                     "too. Rows are")
        emit(handle, "keyed on all three -- features, XLEN and base -- since "
                     "merging a 32-bit")
        emit(handle, "result with a 64-bit one compares cores that are not the "
                     "same core.")
        emit(handle)
        emit(handle, "BPred is the pre-split branch predictor from the earliest "
                     "runs, kept in")
        emit(handle, "its own column: those builds predate btb/gshare/ras being "
                     "separate")
        emit(handle, "switches, so their rows are not comparable with the "
                     "config-driven ones.")
        emit(handle)
        emit(handle, "LUT4 is blank where the sweep kept a timing summary but "
                     "no matching")
        emit(handle, "nextpnr log. Left blank rather than estimated.")

        if ambiguous:
            emit(handle)
            emit(handle, f"{len(ambiguous)} results matched several profiles "
                         f"with different")
            emit(handle, "configurations and are omitted -- the unprefixed "
                         "`core_exh_NN` names")
            emit(handle, "mean different things in the x32 and x64 config "
                         "files:")
            for name in ambiguous[:10]:
                emit(handle, f"    {name}")

        if unresolved:
            emit(handle)
            emit(handle, f"{len(unresolved)} results could not be tied to a "
                         f"configuration and are")
            emit(handle, "omitted rather than shown with guessed features:")
            for name in unresolved:
                emit(handle, f"    {name}")

        emit(handle)
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
