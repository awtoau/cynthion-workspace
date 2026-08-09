#!/usr/bin/env python3
#
# Package the SoC for someone working on synthesis time, without the repo.
# SPDX-License-Identifier: BSD-3-Clause

"""Assemble a self-contained synthesis-optimisation package from the last build.

    ./scripts/pluribus_handover.py            # from tmp/awto_soc/build
    ./scripts/pluribus_handover.py --netlist  # include the 35 MB post-synth JSON

Output goes to `tmp/pluribus-handover/`, log to `tmp/logs/pluribus_handover.log`.

## Why a package and not "clone the repo"

The design is Amaranth, generated from Python, and reproducing it needs the whole
toolchain plus the RISC-V core generator plus a firmware build. None of that is
interesting to someone optimising place-and-route. What IS interesting is the
RTLIL, the constraints and the exact yosys/nextpnr invocation -- all of which the
Amaranth build already writes out, so this copies rather than regenerates.

`emit_verilog.py` converts the same RTLIL to Verilog for a vendor flow. It is not
run here: RTLIL is what the open flow actually consumes, and converting adds a
translation step that would be blamed for any difference.

## The variant matters

`CYNTHION_HYPERRAM_BIST` selects a materially different design, so the package
records which one it holds. Mixing the two would compare a ~15,300-cell SoC with
an ~18,000-cell one and attribute the difference to a synthesis option.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

BUILD = ROOT / "tmp" / "awto_soc" / "build"
OUT = ROOT / "tmp" / "pluribus-handover"
SYNTH_LOG = ROOT / "tmp" / "logs" / "synthesis.log"

# What the open flow consumes, and nothing else. Sizes are from build #91.
WANTED = [
    ("top.il", "the design, as RTLIL -- what yosys actually reads"),
    ("top.lpf", "pin assignment and clock constraints"),
    ("top.ys", "the yosys script the flow runs"),
    ("build_top.sh", "the exact yosys + nextpnr + ecppack invocation"),
    ("VexiiRiscv.v", "the CPU, a generated Verilog input read by top.ys"),
]
OPTIONAL = [
    ("top.json", "post-synthesis netlist, for nextpnr-only work"),
    ("top.rpt", "nextpnr's own report"),
    ("top.tim", "the timing report"),
]


def measured_times() -> dict:
    """Wall-clock split, from the synthesis log if it is still there."""
    out = {}
    if not SYNTH_LOG.exists():
        return out
    text = SYNTH_LOG.read_text(errors="replace")
    for label, pattern in (
            ("yosys", r"End of script.*?CPU: user ([\d.]+)s"),
            ("nextpnr_place", r"Placement.*?time ([\d.]+)s"),
            ("nextpnr_route", r"Routing.*?time ([\d.]+)s")):
        found = re.search(pattern, text, re.S)
        if found:
            out[label] = float(found.group(1))
    return out


def head() -> str:
    got = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True)
    return got.stdout.strip()


def variant() -> str:
    """Which design the build directory holds, from the peripheral list."""
    config = BUILD / "top.config"
    if not config.exists():
        return "unknown"
    # The BIST variant is the one with a second PLL, so it has an extra
    # EHXPLLL. Cheaper than re-elaborating, and it reads the artifact rather
    # than the environment -- which is the point, since the environment is
    # exactly what went wrong before.
    plls = config.read_text(errors="replace").count("EHXPLLL")
    return "HYPERRAM_BIST (measurement)" if plls >= 2 else "shipping SoC"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--netlist", action="store_true",
                        help="also copy the 35 MB post-synthesis JSON and reports")
    args = parser.parse_args()

    if not (BUILD / "top.il").exists():
        emit(f"no build in {BUILD.relative_to(ROOT)} -- run soc_run.py first")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    files = list(WANTED) + (list(OPTIONAL) if args.netlist else [])

    emit(f"packaging {variant()} from {BUILD.relative_to(ROOT)}")
    copied = []
    for name, why in files:
        source = BUILD / name
        if not source.exists():
            emit(f"  MISSING {name} -- {why}")
            continue
        shutil.copy2(source, OUT / name)
        size = source.stat().st_size
        copied.append((name, size, why))
        emit(f"  {name:16} {size / 1e6:7.2f} MB  {why}")

    times = measured_times()
    manifest = {
        "commit": head(),
        "variant": variant(),
        "files": [{"name": n, "bytes": s, "what": w} for n, s, w in copied],
        "measured_seconds": times,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    write_readme(copied, times)
    total = sum(s for _, s, _ in copied)
    emit(f"{len(copied)} files, {total / 1e6:.1f} MB -> {OUT.relative_to(ROOT)}")
    emit(f"open with: code-insiders {OUT / 'README.md'}")
    return 0


def write_readme(copied, times):
    rows = "\n".join(f"| `{n}` | {s / 1e6:.2f} MB | {w} |" for n, s, w in copied)
    timing = ("\n".join(f"- {k}: {v:.1f} s" for k, v in times.items())
              or "- not captured; `tmp/logs/synthesis.log` had been rotated")
    (OUT / "README.md").write_text(f"""\
# Cynthion SoC — synthesis-time package

ECP5 `LFE5U-25F` die (marked 12F), open flow: yosys + nextpnr-ecp5 + ecppack.

- commit `{head()}`
- variant **{variant()}**
- full build ~130 s wall clock, which is the number worth attacking

## Files

| file | size | what |
|---|---|---|
{rows}

`top.il` is the design as RTLIL — what yosys reads. Verilog is NOT included on
purpose: RTLIL is what the open flow consumes, and converting adds a translation
step that would get blamed for any difference. `scripts/emit_verilog.py` in the
source repo does that conversion if a vendor flow needs it.

`build_top.sh` is the exact invocation, including the `NEXTPNR_OPTS` this project
already tuned (`--parallel-refine` plus `--router router2`; measured 64 s → 59 s
with no change to utilisation, and `--threads` alone does nothing because
nextpnr's SA refinement is serial without `--parallel-refine`).

## Measured

{timing}

## The more interesting problem: run-to-run variance

**Identical source, same commit, same flags**, across one session:

| | range |
|---|---|
| `clk` Fmax | **56.21 – 67.27 MHz** against a 60 MHz constraint |
| COMB cells | **16,090 – 18,908** |

One build failed timing outright and the next passed. At a 60 MHz constraint that
is enough to fail by luck, and it makes "did my change help?" unanswerable from a
single build — so the real cost of a 130 s synthesis is 130 s × however many
repeats it takes to see past the noise.

Worth knowing before optimising wall-clock time: **reducing the variance may be
worth more than reducing the mean.**

## Constraints that are not negotiable

- `clk` (CPU) at 60 MHz, `usb` at 60 MHz from the oscillator — ULPI requires it.
- The BIST variant adds `hr`/`hr_fast` off a second PLL, deliberately unrelated
  to `clk`.
- 56 EBR and 24,288 LUT4 available; the design currently sits at 52 BRAM, so
  block RAM is the tighter of the two.
""")


if __name__ == "__main__":
    raise SystemExit(main())
