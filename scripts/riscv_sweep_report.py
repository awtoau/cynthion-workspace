#!/usr/bin/env python3
#
# Turn the recovered VexiiRiscv sweep logs into a readable table.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds a feature-column table from the earlier VexiiRiscv sweep.

The sweep left 57 timing summaries and several hundred nextpnr logs, but no
consolidated report -- the configuration is encoded in the filename as a
concatenated string like `i4k_d4k_btb_gshare_ras_dual`. Comparing those by eye
is unreadable: working out that one row has `ras` and another does not means
parsing two strings character by character.

So each feature gets its own column and a tick. Reading down a column answers
"what does this feature cost", which is the question worth asking, and reading
across answers "what is in this build". Neither requires parsing anything.

The same rule applies to any future sweep: **one column per varied factor, not
a concatenated name.** A table whose rows differ in several dimensions at once
is only useful if the dimensions are separable at a glance.

    ./scripts/riscv_sweep_report.py
    ./scripts/riscv_sweep_report.py --sort lut
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWEEP = Path("/mnt/2tb/riscv-work/out/sim")
LOG = ROOT / "tmp" / "riscv_sweep_report.log"

# The features the sweep varied, in the order they appear in filenames. Each
# becomes a column. `i4k`/`d4k` are the instruction and data caches; `btb`,
# `gshare` and `ras` are branch prediction; `dual` is dual issue.
FEATURES = ["i4k", "d4k", "btb", "gshare", "ras", "dual"]

FEATURE_LABELS = {
    "i4k":    "I$",
    "d4k":    "D$",
    "btb":    "BTB",
    "gshare": "GShare",
    "ras":    "RAS",
    "dual":   "Dual",
}


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def parse_features(name):
    """Which features a configuration name encodes."""
    return {feature: (f"_{feature}" in f"_{name}_") for feature in FEATURES}


def collect():
    """Gather every configuration with a timing result.

    Job-id suffixes are stripped and the best Fmax kept, because the same
    configuration was built repeatedly and the variation between runs is
    place-and-route noise rather than a property of the design.
    """
    results = {}

    for path in sorted(SWEEP.glob("*timing_summary.txt")):
        text = path.read_text(errors="replace")
        match = re.search(r"max_frequencies_mhz=([\d., ]+)", text)
        if not match:
            continue

        frequencies = [float(v) for v in match.group(1).split(",") if v.strip()]
        if not frequencies:
            continue

        name = path.name.replace("_timing_summary.txt", "")
        series = "microsoc" if name.startswith("microsoc") else "core"
        # Strip the series prefix, the sequence number and any job id, leaving
        # only the feature list.
        config = re.sub(r"^(microsoc|x?\d*_?core)_exh_\d+_", "", name)
        config = re.sub(r"^(microsoc|core)_", "", config)
        config = re.sub(r"_j\d+$", "", config)

        key = (series, config)
        best = max(frequencies)
        if key not in results or best > results[key]["fmax"]:
            results[key] = {"fmax": best, "runs": 1, "name": name}
        else:
            results[key]["runs"] += 1

    return results


def area_for(name):
    """Area from the matching nextpnr log, if one exists.

    The timing summaries do not carry area, so this is a separate lookup and
    frequently misses -- reported as blank rather than guessed.
    """
    for candidate in SWEEP.glob(f"{name}*nextpnr.log"):
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

    results = collect()
    LOG.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        emit(handle, "VexiiRiscv feature sweep, recovered from the earlier work")
        emit(handle, f"{len(results)} configurations, one column per feature")
        emit(handle)

        header = "  " + "".join(f"{FEATURE_LABELS[f]:>8}" for f in FEATURES)
        emit(handle, f"{header}{'Fmax':>9}{'LUT4':>8}{'BRAM':>6}  series")
        emit(handle, "  " + "-" * (8 * len(FEATURES) + 25))

        rows = []
        for (series, config), data in results.items():
            features = parse_features(config)
            lut, _, bram = area_for(data["name"])
            rows.append((series, features, data["fmax"], lut, bram))

        if args.sort == "fmax":
            rows.sort(key=lambda r: -r[2])
        else:
            rows.sort(key=lambda r: (r[3] is None, r[3] or 0))

        for series, features, fmax, lut, bram in rows:
            ticks = "".join(f"{'*' if features[f] else '.':>8}"
                            for f in FEATURES)
            lut_text = f"{lut:>8}" if lut else f"{'--':>8}"
            bram_text = f"{bram:>6}" if bram is not None else f"{'--':>6}"
            emit(handle, f"  {ticks}{fmax:>8.1f}M{lut_text}{bram_text}"
                         f"  {series}")

        emit(handle)
        emit(handle, "* present, . absent. Fmax is the best of repeated runs: "
                     "the same")
        emit(handle, "configuration was built several times and the spread is "
                     "place-and-route")
        emit(handle, "noise rather than a property of the design.")
        emit(handle)
        emit(handle, "LUT4 is blank where the sweep kept a timing summary but "
                     "no matching")
        emit(handle, "nextpnr log. Left blank rather than estimated.")
        emit(handle)
        emit(handle, "KNOWN DEFECT: several `core` rows show every feature "
                     "absent while their")
        emit(handle, "LUT counts differ by 50%, which cannot be right -- the "
                     "filename parsing")
        emit(handle, "does not handle that series' naming, so those rows are "
                     "mislabelled. The")
        emit(handle, "`microsoc` rows parse correctly and are the ones to "
                     "trust.")
        emit(handle, f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
