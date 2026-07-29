#!/usr/bin/env python3
#
# Shared build helpers for the test bitstreams.
# SPDX-License-Identifier: BSD-3-Clause

"""
Stamps each bitstream with an identifier so the board can say what it carries.

The ECP5 offers no configuration readback -- the JTAG command set writes
configuration and does not read it back -- so a running design cannot be
identified by inspecting it. USERCODE exists for exactly this: a 32-bit field
in the bitstream, readable over JTAG with the `READ_USERCODE` opcode whether or
not the design is doing anything useful.

Every build here left it at zero, which is why answering "what is running?"
meant comparing build timestamps and guessing.

The value is the short git hash of the working tree, with the top bit set when
the tree is dirty. A bitstream built from uncommitted work is exactly the one
whose provenance is least obvious later, so it is worth being able to tell.

    from build_helpers import ecppack_opts
    platform.build(design, **ecppack_opts())
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def usercode():
    """A 32-bit identifier for the current tree state."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        value = int(sha, 16) & 0x7fffffff
    except Exception:
        # Not a git tree, or git unavailable. Zero is the existing behaviour
        # and is better than refusing to build.
        return 0

    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        value |= 0x80000000
    return value


def ecppack_opts(extra=""):
    """Build kwargs that stamp the bitstream with its USERCODE.

    The frequency is repeated because passing ecppack_opts replaces the
    platform's own defaults rather than adding to them; dropping --freq would
    silently change the configuration clock.
    """
    code = usercode()
    return {"ecppack_opts": f"--compress --freq 38.8 --usercode {code:#010x} "
                            f"{extra}".strip()}


def describe(code):
    """Render a USERCODE read back from hardware."""
    if code == 0:
        return "unset -- built before USERCODE stamping"
    dirty = " (dirty tree)" if code & 0x80000000 else ""
    return f"git {code & 0x7fffffff:07x}{dirty}"


if __name__ == "__main__":
    print(f"usercode: {usercode():#010x}  -> {describe(usercode())}")
