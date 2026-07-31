#!/usr/bin/env python3
#
# Build the sideband test bitstream at a chosen baud and drive style.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds `ecp5-test/sideband` for one (baud, drive style) pair.

Exists so the soak runner does not have to shell out through a heredoc. The
earlier ladder script invoked the build with `bash -c` wrapping a Python
heredoc that sourced the oss-cad-suite environment; that works but it is a
shell-quoting hazard, and it hides build failures behind two levels of
interpretation.

The oss-cad-suite environment matters: yosys, nextpnr-ecp5 and ecppack must be
the versions this project pins, and Amaranth finds them on PATH. Rather than
source a shell script, the paths are added directly.

    ./scripts/sideband_build.py
    ./scripts/sideband_build.py --baud 460800 --drive open-drain
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "sideband_build.log"
BUILD_DIR = ROOT / "ecp5-test" / "sideband" / "build"

# The toolchain this project pins. Amaranth resolves yosys/nextpnr/ecppack from
# PATH, so prepending is enough -- no environment script to source.
OSS_CAD = Path.home() / "opt" / "oss-cad-suite" / "bin"

BUILD_SCRIPT = """
import sys
sys.path.insert(0, "ecp5-test")
sys.path.insert(0, "repos/apollo")
from sideband.sideband_gateware import SidebandTest
from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4
CynthionPlatformRev1D4().build(SidebandTest(), do_program=False,
                               build_dir="ecp5-test/sideband/build")
print("BUILD OK")
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baud", type=int, default=None,
                        help="only reported; the source is the source of truth")
    parser.add_argument("--drive", choices=["open-drain", "push-pull"],
                        default=None, help="only reported, as above")
    args = parser.parse_args()

    if not OSS_CAD.exists():
        print(f"no oss-cad-suite at {OSS_CAD}")
        return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{OSS_CAD}:{env['PATH']}"

    # The script does not set baud or drive style: the soak runner has already
    # rewritten the sources. Reporting them here is a cross-check that what was
    # asked for is what the tree holds, not a second place to configure it.
    with LOG.open("w") as handle:
        header = (f"building sideband bitstream"
                  + (f", baud {args.baud}" if args.baud else "")
                  + (f", {args.drive}" if args.drive else ""))
        print(header, flush=True)
        handle.write(header + "\n")

        result = subprocess.run([sys.executable, "-c", BUILD_SCRIPT],
                                cwd=ROOT, env=env, capture_output=True,
                                text=True)
        handle.write(result.stdout or "")
        handle.write(result.stderr or "")

        if result.returncode != 0 or "BUILD OK" not in (result.stdout or ""):
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            message = tail[-1][:200] if tail else "no output"
            print(f"build failed: {message}", flush=True)
            handle.write(f"build failed: {message}\n")
            return 1

        bitstream = BUILD_DIR / "top.bit"
        size = bitstream.stat().st_size if bitstream.exists() else 0
        print(f"built {bitstream.relative_to(ROOT)}, {size} bytes", flush=True)
        handle.write(f"built {bitstream}, {size} bytes\n")
        print(f"log: {LOG}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
