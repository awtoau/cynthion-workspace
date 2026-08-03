#!/usr/bin/env python3
#
# Build the fabric test bitstream and verify it really occupies the fabric.
# See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds `ecp5-test/fabric` for a `--12k` target and refuses to produce a
bitstream that does not actually use the fabric beyond 12,288 LUTs.

That refusal is the point. The whole experiment rests on the design occupying
logic an LFE5U-12F does not advertise, and every step between the source and
the silicon can quietly undo that:

  * yosys can prune a block whose output does not reach a pin
  * yosys can share logic between blocks that compute the same function
  * a mistake in `BLOCKS` can drop the count below the threshold

All three produce a bitstream that loads, runs, matches its golden value, and
proves nothing at all -- the most misleading outcome available. So this script
parses the LUT count out of nextpnr's own log and fails the build if it is below
`--min-luts`. "It used the extra fabric" is then a measured number from the
tool, not an intention from the source.

The golden value is computed first, by `fabric_golden.py`, and baked into the
gateware as a constant so the design can check itself with no host attached.

The oss-cad-suite environment matters: yosys, nextpnr-ecp5 and ecppack must be
the versions this project pins, and Amaranth finds them on PATH. Rather than
source a shell script, the paths are added directly.

    ./scripts/fabric_build.py
    ./scripts/fabric_build.py --blocks 120 --min-luts 20000
    ./scripts/fabric_build.py --seed 7 --build-dir tmp/fabric-coverage/seed-007

`--seed` is what makes more than one configuration possible. The same source
placed differently routes differently, and routing is the only way to reach
interconnect a single build cannot: an arc is a mux selection, so one
configuration drives each destination wire from exactly one source and leaves
every other source for that wire untested. `fabric_sweep.py` loops it.

This script does not program anything. Loading is a separate, deliberate step:
see `fabric_run.py`.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_build.log"
BUILD_DIR = ROOT / "ecp5-test" / "fabric" / "build"

# The toolchain this project pins. Amaranth resolves yosys/nextpnr/ecppack from
# PATH, so prepending is enough -- no environment script to source.
OSS_CAD = Path.home() / "opt" / "oss-cad-suite" / "bin"

# What an LFE5U-12F advertises. The die carries 24,288. A build at or below this
# would sit entirely inside the advertised region and could not distinguish any
# of the three explanations, so it is a hard floor rather than a warning.
ADVERTISED_LUTS = 12288

BUILD_SCRIPT = """
import sys
sys.path.insert(0, "ecp5-test")
sys.path.insert(0, "repos/apollo")
from fabric.fabric_gateware import FabricTest
from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4

design = FabricTest(blocks={blocks}, round_bits={round_bits}, golden={golden})
CynthionPlatformRev1D4().build(design, do_program=False,
                               build_dir={build_dir!r})
print("BUILD OK")
"""

# The platform's `toolchain_prepare` hardcodes ecppack_opts, so the USERCODE
# stamping in build_helpers cannot be applied here without overriding the
# platform. It is not needed: this design answers "which bitstream is running?"
# through REG_ID and REG_GOLDEN, which say more than a git hash would -- they
# say which golden constant the running gateware is checking itself against.


def parse_utilisation(text):
    """Pull nextpnr's own utilisation figures out of its log.

    Returns a dict of category -> (used, available). Reported rather than
    recomputed: the number that matters is the tool's, because the tool is what
    decided where the logic went.
    """
    found = {}
    # "Info:     Total LUT4s:  19812/24288    81%" and the TRELLIS_* lines.
    pattern = re.compile(r"^Info:\s+(.+?):\s+(\d+)/\s*(\d+)\s+\d+%\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            found[match.group(1).strip()] = (int(match.group(2)),
                                             int(match.group(3)))
    return found


def parse_timing(text):
    """Returns (achieved_mhz, constraint_mhz, met) for the sync domain, or None.

    nextpnr reports either "Max frequency for clock ...: 71.53 MHz (PASS at
    60.00 MHz)" or the same with FAIL. Both are parsed, because a build that
    failed timing is still a result -- it just is not a result about the fabric
    working, and reporting a pass in that case would be the worst outcome.
    """
    pattern = re.compile(
        r"Max frequency for clock\s+'?(?P<clock>[^']+?)'?:\s+"
        r"(?P<achieved>[\d.]+)\s+MHz\s+\((?P<verdict>PASS|FAIL)\s+at\s+"
        r"(?P<constraint>[\d.]+)\s+MHz\)")
    results = []
    for match in pattern.finditer(text):
        results.append((match.group("clock"),
                        float(match.group("achieved")),
                        float(match.group("constraint")),
                        match.group("verdict") == "PASS"))
    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blocks", type=int, default=None,
                        help="override the gateware's BLOCKS")
    parser.add_argument("--round-bits", type=int, default=None,
                        help="override the gateware's ROUND_BITS")
    parser.add_argument("--min-luts", type=int, default=None,
                        help=f"fail the build below this LUT4 count; default "
                             f"is the {ADVERTISED_LUTS} an LFE5U-12F "
                             f"advertises, since anything at or below it "
                             f"cannot answer the question")
    parser.add_argument("--golden", default=None,
                        help="hex golden value, if already computed; otherwise "
                             "it is computed here")
    parser.add_argument("--seed", type=int, default=None,
                        help="nextpnr placer seed. The same source with a "
                             "different seed places and routes differently, "
                             "which is the only way to reach interconnect a "
                             "single configuration cannot: an arc is a mux "
                             "selection, so one build can drive each "
                             "destination wire from exactly one source")
    parser.add_argument("--build-dir", type=Path, default=BUILD_DIR,
                        help="where the tools write; a sweep needs one "
                             "directory per configuration or each build "
                             "destroys the evidence from the last")
    parser.add_argument("--log", type=Path, default=LOG,
                        help="where this script's transcript goes")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "ecp5-test"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fabric.fabric_gateware import BLOCKS, ROUND_BITS

    blocks = args.blocks if args.blocks is not None else BLOCKS
    round_bits = (args.round_bits if args.round_bits is not None
                  else ROUND_BITS)
    min_luts = args.min_luts if args.min_luts is not None else ADVERTISED_LUTS

    if not OSS_CAD.exists():
        print(f"no oss-cad-suite at {OSS_CAD}")
        return 1

    args.log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{OSS_CAD}:{env['PATH']}"

    # Amaranth reads its tool overrides from AMARANTH_<name> before it looks at
    # the build kwargs, and this platform's `toolchain_prepare` already passes
    # `ecppack_opts` that way -- so the environment is the one route that adds
    # to nextpnr's command line without overriding the platform.
    if args.seed is not None:
        env["AMARANTH_nextpnr_opts"] = f"--seed {args.seed}"

    with args.log.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        emit(f"fabric test: {blocks} blocks, round 2**{round_bits} cycles")
        if args.seed is not None:
            emit(f"nextpnr placer seed: {args.seed}")
        emit(f"build directory: {args.build_dir}")

        if args.golden:
            golden = int(args.golden, 16)
            emit(f"golden signature (given): {golden:#010x}")
        else:
            from fabric_golden import golden as compute_golden, \
                verify_vector_model
            checked = verify_vector_model(blocks, 300)
            emit(f"golden model cross-checked against the scalar "
                 f"specification over {checked} cycles")
            golden = compute_golden(blocks, 1 << round_bits)
            emit(f"golden signature: {golden:#010x}")

        script = BUILD_SCRIPT.format(blocks=blocks, round_bits=round_bits,
                                     golden=hex(golden),
                                     build_dir=str(args.build_dir))
        emit("running yosys, nextpnr-ecp5 and ecppack")
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                env=env, capture_output=True, text=True)
        combined = (result.stdout or "") + (result.stderr or "")
        handle.write(combined)
        handle.flush()

        if result.returncode != 0 or "BUILD OK" not in (result.stdout or ""):
            tail = combined.strip().splitlines()
            for line in tail[-15:]:
                emit(f"  {line}")
            emit("BUILD FAILED")
            return 1

        timing_log = args.build_dir / "top.tim"
        if not timing_log.exists():
            emit(f"no nextpnr log at {timing_log} -- cannot report utilisation")
            return 1
        tim = timing_log.read_text()

        utilisation = parse_utilisation(tim)
        emit()
        emit("utilisation, as reported by nextpnr:")
        for name in ("Total LUT4s", "logic LUTs", "carry LUTs", "TRELLIS_FF",
                     "TRELLIS_COMB", "TRELLIS_IO"):
            if name in utilisation:
                used, avail = utilisation[name]
                emit(f"  {name:<14} {used:>6}/{avail:<6} "
                     f"{100.0 * used / avail:5.1f}%")

        if "Total LUT4s" not in utilisation:
            emit("nextpnr did not report a LUT4 total -- refusing to claim "
                 "anything about utilisation")
            return 1
        luts, available = utilisation["Total LUT4s"]

        emit()
        for clock, achieved, constraint, met in parse_timing(tim):
            emit(f"timing: {clock} {achieved:.2f} MHz achieved against a "
                 f"{constraint:.2f} MHz constraint -- "
                 f"{'MET' if met else 'NOT MET'}")

        emit()
        emit(f"LUT4s {luts} of {available} on the die; an LFE5U-12F "
             f"advertises {ADVERTISED_LUTS}")
        if luts <= min_luts:
            emit(f"REFUSING: {luts} LUT4s is not above the {min_luts} floor.")
            emit("  A design inside the advertised region cannot distinguish "
                 "whole-die from salvage, so this bitstream would prove "
                 "nothing. Raise --blocks.")
            return 1
        emit(f"  {luts - ADVERTISED_LUTS} LUT4s beyond what the part "
             f"advertises")

        bitstream = args.build_dir / "top.bit"
        size = bitstream.stat().st_size if bitstream.exists() else 0
        emit(f"built {bitstream}, {size} bytes")
        emit(f"log: {args.log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
