#!/usr/bin/env python3
#
# One command that says whether the HyperRAM rig is trustworthy. #226.
# SPDX-License-Identifier: BSD-3-Clause

"""The whole BIST gate, in order, with one exit code.

    ./scripts/hyperram_verify.py                  # the gate
    ./scripts/hyperram_verify.py --quick          # skip the 4096-cell matrix
    ./scripts/hyperram_verify.py --label post-334 # names the saved matrix runs

## Why this exists

The steps below were being run by hand, in a different combination each time, as
each fix landed. That is how a step gets skipped without anyone noticing -- and
this rig's entire history is instruments that could not report their own failure.
A gate that is retyped every time is one of those.

Order matters and is not arbitrary:

1. **presence** -- the applet id. A peripheral that is not there reads as zeroes,
   and zero errors from a peripheral that does not exist is the most flattering
   wrong answer available.
2. **axis wiring** -- does each axis reach anything in the GATEWARE? No board,
   and it answers for `sel`, which no board test can: `READCLKSEL` has no
   read-back (#343).
3. **axis liveness** -- does each configuration axis reach the part? Run BEFORE
   any sweep, because "no effect" and "not connected" produce identical data, and
   a sweep over a dead axis produces rows that mean nothing (#334, #335).
4. **latency at every CK rung** -- the pass set, per rung (#331, #313).
5. **the matrix, twice** -- and DIFFED. A cell that passes once is not a cell
   that passes; two identical runs that disagree have found a marginal cell,
   which a single run scores as a verdict (#311's workflow too).

Each step's own script stays runnable on its own -- this composes them, it does
not replace them.

Exit code is the answer: 0 gate passed, 1 a step failed, 2 could not run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram-verify.log"

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
import variant  # noqa: E402

# The `.bit` this variant builds to. Passed to the matrix so its runs record
# what the HyperRAM pins were set to; without it every run the GATE records is
# saved with `pins: null` and cannot be compared against a pin patch (#311).
BITSTREAM = variant.build_dir(ROOT) / "top.bit"


def emit(line=""):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def step(name, argv, *, needed=True):
    """Run one step. Returns (ok, output); a failing step does not stop the gate.

    The gate reports EVERY step rather than stopping at the first failure: which
    combination fails is the diagnosis, and stopping early hides it. `needed`
    marks the ones whose failure makes later results meaningless.
    """
    emit(f"\n=== {name}")
    emit(f"    {' '.join(argv)}")
    try:
        done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    except OSError as exc:
        emit(f"    COULD NOT RUN: {exc}")
        return None, ""
    for line in done.stdout.splitlines():
        emit(f"    {line}")
    if done.returncode != 0:
        emit(f"    exit {done.returncode}"
             + ("  -- everything after this is unreliable" if needed else ""))
    return done.returncode == 0, done.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="skip the 4096-cell matrix, which is the slow part")
    parser.add_argument("--label", default="verify",
                        help="names the saved matrix runs")
    parser.add_argument("--passes", type=int, default=8)
    args = parser.parse_args()

    py = sys.executable
    s = ROOT / "scripts"
    results = {}

    # 0. SIMULATION FIRST, because it needs no hardware and costs 4 s against a
    # 3-8 minute bitstream. If the CA encoding or the latency arithmetic is
    # wrong, every board result below is noise -- and on hardware that noise is
    # indistinguishable from signal integrity, which is how #226 concluded every
    # earlier measurement was void.
    #
    # This one first of the two: a drift shows up here as a named beat count and
    # latency code, which reads faster than a testbench summary line.
    results["controller vs the independent model"] = step(
        "the controller's CA and latency arithmetic, against hyperram_model.v",
        [py, str(s / "hyperram_model_sim.py")])[0]

    results["the twin against the shared testbench"] = step(
        "the open twin, against the vendor model's own testbench",
        [py, str(s / "hyperram_vendor_model_sim.py"), "--sim", "icarus"])[0]

    results["controller vs our Python model"] = step(
        "the controller against our own model -- fault injection and the SoC above it",
        [py, str(s / "soc_hyperram_sim.py")])[0]

    # 2. Wiring, still without a board. `sel` has no device read-back, so this is
    # the ONLY thing that can tell a dead capture-phase axis from an inert one.
    results["axis wiring"] = step(
        "axis wiring -- does each axis reach anything in the gateware?",
        [py, str(s / "hyperram_axis_wiring.py")])[0]

    # 1-3. Liveness next. It also proves the link and the applet id, so a board
    # that is not answering fails here rather than halfway through a sweep.
    results["axis liveness"] = step(
        "axis liveness -- does each axis reach the part?",
        [py, str(s / "hyperram_axis_liveness.py")])[0]

    # 3. The latency pass set at every rung this bitstream carries.
    results["latency per CK rung"] = step(
        "latency, at every CK rung",
        [py, str(s / "hyperram_ck_sweep.py"), "--passes", str(args.passes)])[0]

    # 4. The matrix, twice, diffed.
    if args.quick:
        emit("\n=== matrix SKIPPED (--quick)")
        emit("    so nothing here says a cell is stable, only that it passed once")
    else:
        matrix = [py, str(s / "hyperram_matrix_diff.py"), "--label", args.label,
                  "--passes", str(args.passes), "--repeat", "2"]
        if BITSTREAM.exists():
            matrix += ["--bitstream", str(BITSTREAM)]
        else:
            emit(f"\n    NO BITSTREAM at {BITSTREAM.relative_to(ROOT)} -- the "
                 "matrix runs below will record `pins: null` (#311)")
        results["matrix, twice, diffed"] = step(
            "the 4096-cell matrix, twice, diffed", matrix)[0]

    emit("\n" + "=" * 60)
    for name, ok in results.items():
        emit(f"  {'PASS' if ok else 'FAIL' if ok is False else '????'}  {name}")

    if any(ok is None for ok in results.values()):
        emit("\nA step could not run, so the gate has no verdict.")
        return 2
    if not all(results.values()):
        emit("\nGATE FAILED. Read the failing step above rather than re-running "
             "the whole thing -- each script is runnable on its own.")
        return 1
    emit("\nGATE PASSED. Every axis reaches the part, the latency set is the "
         "same at every rung, and no cell moved between two identical runs.")
    emit("\nThis says the RIG is trustworthy. It says nothing about the part's "
         "limits: the non-DQS path is 1:1 geared, so CK is a fabric clock capped "
         "near 94 MHz against a part rated 166.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
