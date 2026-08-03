#!/usr/bin/env python3
#
# Is nextpnr's refusal above ~86 MHz a limit of the silicon or a policy?
# See awtoau/cynthion-workspace#110.
# SPDX-License-Identifier: BSD-3-Clause

"""
Place the RISC-V SoC above nextpnr's own timing constraint and test the silicon.

## The distinction this draws

Amaranth's generated build script runs nextpnr under `set -e` with no
`--timing-allow-fail`, so a design that misses its constraint stops the build
and emits no bitstream.  The brief for #110 records that as nextpnr having
"refused" at 90 and 100 MHz -- but a refusal to vouch for a placement is not the
same as an inability to produce one, and `--timing-allow-fail` is exactly the
flag that separates the two.

That matters because nextpnr's static timing analysis is a *conservative
bound*, computed from worst-case corner delays across every path.  Real silicon
at room temperature on a nominal part routinely beats it.  So "nextpnr says
86.1 MHz and refuses 90" leaves the interesting question untouched: does the
CPU actually compute correctly at the clock it was asked for?

This script answers that directly.  It re-places the *same* `top.json` the open
flow already synthesised, adding `--timing-allow-fail`, packs the result, and
puts it on the board.

## Why this runs against top.json rather than rebuilding

Amaranth regenerates `build_top.sh` on every build, so patching the flag into
that script is overwritten by the next run.  Driving nextpnr directly against
the emitted netlist avoids that, and has the stronger property that every point
on the ladder places *byte-identical* synthesis output -- so a difference
between two points is placement and nothing else.

The frequency constraint lives in the `.lpf`, and the design's PLL derives
`sync` from it, so the netlist is tied to the SYNC_MHZ it was elaborated at.
Each point therefore needs its own build; `--json` accepts one already made.

## What counts as passing

The same standard as every other ladder here, imported rather than
reimplemented from `riscv_clock_ladder`: the printed product must read
`369d0368` (0x12345678 * 3, so the CPU is multiplying rather than echoing a
stored constant) and the tick counter must advance between two separate reads.
A CPU with marginal timing computes the wrong answer rather than stopping,
which is precisely the failure mode a bitstream-built-successfully check would
miss.

**Verification must follow a reconfigure.**  The firmware prints its product
line once at boot, so reading a board that has been running prints only ticks
and the product check reports a false negative.  Configuring immediately before
reading is what makes the banner observable.

    ./scripts/nextpnr_allow_fail_ladder.py --frequencies 90 100 110
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "nextpnr_allow_fail_ladder.log"
RESULTS = ROOT / "tmp" / "nextpnr_allow_fail_ladder.json"
GATEWARE = ROOT / "ecp5-test" / "riscv" / "vexii_hello_soc.py"
BUILD = ROOT / "tmp" / "vexii_hello" / "build"
WORK = ROOT / "tmp" / "nextpnr_allow_fail"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))

from riscv_clock_ladder import verify, set_clock

ENV = 'source "$HOME/opt/oss-cad-suite/environment" && '


def sh(command, cwd=ROOT):
    return subprocess.run(["bash", "-c", command], cwd=str(cwd),
                          capture_output=True, text=True)


def synthesise(mhz):
    """Run the normal Amaranth build and keep whatever it produced.

    Expected to fail at the high end -- that failure is the phenomenon under
    test, not an error.  yosys runs before nextpnr, so `top.json` exists either
    way, and that netlist is what the rest of the ladder places.
    """
    result = sh(f'{ENV}python3.15t {GATEWARE} --build')
    achieved = None
    timing = BUILD / "top.tim"
    if timing.exists():
        for line in timing.read_text().splitlines():
            found = re.search(r"Max frequency for clock\s+'\$glbnet\$clk':"
                              r"\s*([\d.]+)\s*MHz", line)
            if found:
                achieved = float(found.group(1))
    return result.returncode == 0, achieved


def place_allow_fail(mhz):
    """Re-place the emitted netlist with the timing check downgraded.

    `--timing-allow-fail` turns nextpnr's hard stop into a warning: it still
    runs timing-driven placement against the constraint, still reports the
    frequency it reached, but writes the configuration instead of exiting.  The
    placement effort is therefore identical to the run that refused -- only the
    verdict changes.
    """
    out = WORK / str(mhz)
    out.mkdir(parents=True, exist_ok=True)
    cfg, tim, bit = out / "top.config", out / "top.tim", out / "top.bit"
    result = sh(f'{ENV}nextpnr-ecp5 --quiet --log {tim} --12k '
                f'--package CABGA256 --speed 8 --json {BUILD}/top.json '
                f'--lpf {BUILD}/top.lpf --textcfg {cfg} --timing-allow-fail')
    if result.returncode != 0 or not cfg.exists():
        text = (result.stdout + result.stderr).strip().splitlines()
        return None, None, (text[-1][:160] if text else "nextpnr failed")

    achieved = None
    if tim.exists():
        for line in tim.read_text().splitlines():
            found = re.search(r"Max frequency for clock\s+'\$glbnet\$clk':"
                              r"\s*([\d.]+)\s*MHz", line)
            if found:
                achieved = float(found.group(1))

    # --freq is the SPI configuration clock, unrelated to the design's own
    # clock; 38.8 matches what the Amaranth flow emits so the board is loaded
    # exactly as it would be normally.
    pack = sh(f'{ENV}ecppack --compress --freq 38.8 --input {cfg} --bit {bit}')
    if pack.returncode != 0 or not bit.exists():
        return None, achieved, "ecppack failed"
    return bit, achieved, None


def configure(bit):
    result = subprocess.run(
        [sys.executable,
         str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
         "configure", str(bit)],
        cwd=str(ROOT), capture_output=True, text=True)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frequencies", type=int, nargs="+",
                        default=[90, 100, 110])
    parser.add_argument("--restore", type=int, default=60)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    results = {}

    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("nextpnr above its own constraint  (#110)")
        emit("passing = printed product correct AND ticks advancing.")
        emit("verification follows a reconfigure so the boot banner is seen.")
        emit()

        try:
            for mhz in args.frequencies:
                emit(f"=== requested {mhz} MHz")
                set_clock(mhz)
                built, achieved = synthesise(mhz)
                shown = f"{achieved:.1f}" if achieved else "?"
                emit(f"  normal build: {'ok' if built else 'REFUSED'}, "
                     f"nextpnr achieved {shown} MHz")

                bit, achieved2, err = place_allow_fail(mhz)
                if not bit:
                    emit(f"  allow-fail place: FAILED -- {err}")
                    results[mhz] = {"normal_built": built,
                                    "achieved": achieved, "allow_fail": err}
                    continue
                shown2 = f"{achieved2:.1f}" if achieved2 else "?"
                emit(f"  allow-fail place: bitstream produced, "
                     f"achieved {shown2} MHz")

                if not configure(bit):
                    emit("  configure FAILED")
                    results[mhz] = {"normal_built": built, "achieved": achieved,
                                    "allow_fail_achieved": achieved2,
                                    "hardware": "configure failed"}
                    continue
                good, detail = verify()
                emit(f"  hardware: {'PASS' if good else '*** FAIL'}  {detail}")
                results[mhz] = {"normal_built": built, "achieved": achieved,
                                "allow_fail_achieved": achieved2,
                                "hardware_pass": good,
                                "hardware_detail": detail}
        finally:
            set_clock(args.restore)
            emit()
            emit(f"restored SYNC_MHZ = {args.restore}")

        RESULTS.write_text(json.dumps(results, indent=2) + "\n")
        passed = [m for m, r in results.items() if r.get("hardware_pass")]
        emit()
        emit(f"highest verified on hardware: {max(passed)} MHz" if passed
             else "nothing verified on hardware")
        emit(f"results {RESULTS}")
        emit(f"log {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
