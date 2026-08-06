#!/usr/bin/env python3
#
# Build the sideband test bitstream at a chosen baud and drive style.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds `gateware/probes/sideband` for one (baud, drive style) pair.

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
BUILD_DIR = ROOT / "gateware" / "probes" / "sideband" / "build"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# The toolchain this project pins. Amaranth resolves yosys/nextpnr/ecppack from
# PATH, so prepending is enough -- no environment script to source.
OSS_CAD = Path.home() / "opt" / "oss-cad-suite" / "bin"

BUILD_SCRIPT = """
import sys
sys.path.insert(0, "gateware")
sys.path.insert(0, "repos/apollo")
from sideband.sideband_gateware import SidebandTest
from board.cynthion_r1_4 import CynthionPlatformRev1D4
CynthionPlatformRev1D4().build(SidebandTest(), do_program=False,
                               build_dir="gateware/probes/sideband/build")
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

    env = dict(os.environ)
    env["PATH"] = f"{OSS_CAD}:{env['PATH']}"

    # The script does not set baud or drive style: the soak runner has already
    # rewritten the sources. Reporting them here is a cross-check that what was
    # asked for is what the tree holds, not a second place to configure it.
    emit(f"building sideband bitstream"
         + (f", baud {args.baud}" if args.baud else "")
         + (f", {args.drive}" if args.drive else ""))

    # Captured, not streamed: the "BUILD OK" test and the failure tail below
    # both read this output.
    result = subprocess.run([sys.executable, "-c", BUILD_SCRIPT],
                            cwd=ROOT, env=env, capture_output=True,
                            text=True)

    if result.returncode != 0 or "BUILD OK" not in (result.stdout or ""):
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        emit(f"build failed: {tail[-1][:200] if tail else 'no output'}")
        return 1

    bitstream = BUILD_DIR / "top.bit"
    size = bitstream.stat().st_size if bitstream.exists() else 0
    emit(f"built {bitstream.relative_to(ROOT)}, {size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
