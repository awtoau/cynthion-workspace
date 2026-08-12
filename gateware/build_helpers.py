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

The value is the short git hash of the working tree, with the top bit set when
the tree is dirty. A bitstream built from uncommitted work is exactly the one
whose provenance is least obvious later, so it is worth being able to tell.

32 bits is not enough to answer "which build is this?" on its own, so the SoC
build writes a record keyed by them: `gateware/usercode_map.py`.

    from build_helpers import ecppack_opts
    platform.build(design, **ecppack_opts())        # a kwarg, where one works
    os.environ["AMARANTH_ecppack_opts"] = ecppack_opts()["ecppack_opts"]

The environment form is the one the SoC uses: `CynthionPlatform.toolchain_prepare`
passes its own `ecppack_opts` before `**kwargs`, so a kwarg is a duplicate
keyword there, and Amaranth's `_extract_override` reads the environment first.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWARE_SOC = ROOT / "gateware" / "soc"


def source_digest():
    """A hash of every gateware source, and of the variant that selects among them.

    The one definition of "which gateware is this". `soc_run.gateware_digest`
    folds HEAD and the placer flags into it for the bitstream cache;
    `peripherals/gateware_id.py` puts its low 32 bits in a register.
    """
    if str(GATEWARE_SOC) not in sys.path:
        sys.path.insert(0, str(GATEWARE_SOC))
    import variant

    digest = hashlib.sha256()
    for source in sorted(GATEWARE_SOC.rglob("*.py")):
        digest.update(source.relative_to(ROOT).as_posix().encode())
        digest.update(source.read_bytes())
    for setting in variant.settings():
        digest.update(setting.encode())
    return digest.hexdigest()


def source_id():
    """`source_digest` as the 32 bits `gateware_id`'s `built` register holds."""
    return int(source_digest()[:8], 16)


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


def ecppack_opts(extra="", code=None):
    """Build kwargs that stamp the bitstream with its USERCODE.

    Pass `code` when the caller has already computed it -- `soc/top.py` does,
    and uses the same value for `usercode_map`'s record, so the bitstream and
    the record cannot disagree about a tree that changed mid-build.

    The frequency is repeated because passing ecppack_opts replaces the
    platform's own defaults rather than adding to them; dropping --freq would
    silently change the configuration clock.
    """
    code = usercode() if code is None else code
    # DECIMAL. ecppack parses --usercode with a plain integer reader and rejects
    # `0x...` outright ("the argument ... is invalid"), so the hex this used to
    # emit failed every build that asked for a USERCODE -- the stamp could not
    # have been reaching any bitstream. Verified against ecppack 1.4-79 for
    # values above 2**31 too, which is where a dirty tree's top bit puts it.
    return {"ecppack_opts": f"--compress --freq 38.8 --usercode {code:d} "
                            f"{extra}".strip()}


def describe(code):
    """Render a USERCODE read back from hardware."""
    if code == 0:
        return "unset -- built before USERCODE stamping"
    dirty = " (dirty tree)" if code & 0x80000000 else ""
    return f"git {code & 0x7fffffff:07x}{dirty}"


if __name__ == "__main__":
    print(f"usercode: {usercode():#010x}  -> {describe(usercode())}")
    print(f"source:   {source_id():#010x}  -> {source_digest()}")
