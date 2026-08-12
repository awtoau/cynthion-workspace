#!/usr/bin/env python3
#
# What a USERCODE means: the 32 bits on the part, resolved to the build. #447.
# SPDX-License-Identifier: BSD-3-Clause

"""
The host's side of the build identity, now that the fabric carries none of it.

USERCODE is 32 bits and the questions asked of a bitstream are not: which commit,
which variant, what `sync` was declared at, what part it was built for. So the
bits are the KEY and this file is the table.

    from usercode_map import record_for_build, write, lookup, clock_problems

## Where the files live, and why there

    <build_dir>/usercode.json    one record, beside the .bit it describes
    tmp/usercode-map.json        every record ever built, keyed by usercode

Two files because they answer two questions:

  * The per-build record travels WITH the bitstream. `soc_run.bitcache_put`
    copies it into the cache beside `top.bit`, so a cached bitstream restored
    months later still carries its own identity. A record that lived only in a
    central index would be separated from its bitstream by the first cache hit.
  * The index answers a bare 32 bits read off a board nobody can attribute --
    the case where the build directory is unknown, or is another variant's.

Both are derived: delete either and the next build rewrites its own row.

## What is in a record

Every build fact the host has any use for:

    usercode        the 32 bits ecppack stamped -- the key
    commit, dirty   HEAD at build time, and whether the tree was clean
    variant         the `variant.slug()` tag, so two variants never collide
    source_digest   `build_helpers.source_digest()` -- WHICH gateware
    gateware_digest `soc_run.gateware_digest()`'s cache key, when the caller has it
    build_dir       relative to the workspace root
    device, speed   the platform's part and speed grade
    idcode          what that part must answer on JTAG (prjtrellis' own table)
    sync_hz, usb_hz what the PLL solver landed on, not what was requested
    cache_sets/ways, isa    how `cpu/cpu.py` generated the core
    built_at        ISO 8601 with offset

A timestamp is fine in a JSON file beside the bitstream: it moves nothing that
gets synthesised.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The per-build record's name, beside `top.bit`. `soc_run.bitcache_put` copies
# this list of names, so adding it there is what keeps a cached bitstream
# attributable.
RECORD_NAME = "usercode.json"

# The index. Under tmp/ with the bitcache, because it is derived from the builds
# and any build rewrites its own row.
INDEX = Path("tmp") / "usercode-map.json"


def trellis_devices(path=None):
    """prjtrellis' own device table, or None if the database is not installed.

    THE PART'S IDCODE IS NOT OURS TO DECLARE. `ecppack` and the silicon agree on
    it through this file; a table typed here would be a third opinion.
    """
    if path is None:
        env = os.environ.get("TRELLIS_DB")
        candidates = []
        if env:
            candidates.append(Path(env) / "devices.json")
        packer = shutil.which("ecppack")
        if packer:
            candidates.append(Path(packer).resolve().parent.parent
                              / "share" / "trellis" / "database" / "devices.json")
        candidates.append(Path.home() / "opt" / "oss-cad-suite" / "share"
                          / "trellis" / "database" / "devices.json")
        path = next((c for c in candidates if c.exists()), None)
    if path is None or not Path(path).exists():
        return None
    return json.loads(Path(path).read_text())


def idcode_for(device, path=None):
    """The JTAG IDCODE `device` must answer with, from prjtrellis. None if unknown.

    `device` is the platform's own string, e.g. `LFE5U-12F`.
    """
    table = trellis_devices(path)
    if table is None:
        return None
    for family in table.get("families", {}).values():
        entry = family.get("devices", {}).get(device)
        if entry and "idcode" in entry:
            return int(entry["idcode"], 16)
    return None


def git_head(root=ROOT):
    """(commit, dirty) for the working tree, or (None, None) outside git."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True,
                                check=True).stdout.strip()
    except Exception:
        return None, None
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                           capture_output=True, text=True).stdout.strip()
    return commit, bool(dirty)


def record_for_build(*, usercode, build_dir, variant_slug, source_digest,
                     device, speed, sync_hz, usb_hz, cache_sets, cache_ways,
                     isa, gateware_digest=None, root=ROOT, devices_path=None):
    """One row of the table. Pure apart from reading git and the device table."""
    commit, dirty = git_head(root)
    try:
        where = str(Path(build_dir).resolve().relative_to(root))
    except ValueError:
        where = str(build_dir)
    return {
        "usercode": int(usercode),
        "usercode_hex": f"{int(usercode):#010x}",
        "commit": commit,
        "dirty": dirty,
        "variant": variant_slug,
        "source_digest": source_digest,
        "gateware_digest": gateware_digest,
        "build_dir": where,
        "device": device,
        "speed": str(speed),
        "idcode": idcode_for(device, devices_path),
        "sync_hz": int(sync_hz),
        "usb_hz": int(usb_hz),
        "cache_sets": int(cache_sets),
        "cache_ways": int(cache_ways),
        "isa": dict(isa),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write(record, build_dir, root=ROOT):
    """Write the per-build record and add it to the index. Returns both paths."""
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    beside = build_dir / RECORD_NAME
    beside.write_text(json.dumps(record, indent=2) + "\n")

    index = Path(root) / INDEX
    index.parent.mkdir(parents=True, exist_ok=True)
    try:
        table = json.loads(index.read_text())
    except Exception:
        # A missing or corrupt index is rebuilt rather than fatal: it is derived,
        # and refusing a build over it would trade a bitstream for a cache file.
        table = {}
    table[f"{record['usercode']:#010x}"] = record
    index.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    return beside, index


def read_build_record(build_dir):
    """The record beside a bitstream, or None."""
    path = Path(build_dir) / RECORD_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def lookup(usercode, root=ROOT, index=None):
    """The record for a USERCODE read off a part, or None.

    Answers "which build is this?" when the bare 32 bits do not.
    """
    path = Path(index) if index else Path(root) / INDEX
    if not path.exists():
        return None
    try:
        table = json.loads(path.read_text())
    except Exception:
        return None
    return table.get(f"{int(usercode):#010x}")


def describe(usercode, record=None):
    """One line for a USERCODE, using the record when there is one."""
    code = int(usercode)
    if code == 0:
        return "0x00000000 -- unset; built before USERCODE stamping"
    if record is None:
        dirty = " (dirty tree)" if code & 0x8000_0000 else ""
        return f"{code:#010x} -- git {code & 0x7fff_ffff:07x}{dirty}, no record"
    return (f"{code:#010x} -- {(record.get('commit') or '?')[:7]}"
            f"{' dirty' if record.get('dirty') else ''} "
            f"{record.get('variant')} src {(record.get('source_digest') or '?')[:8]}")


# ---------------------------------------------------------------------------
# What a record implies, checked on the host
# ---------------------------------------------------------------------------


def clock_problems(record, ceiling_mhz=None, limits=None):
    """Declared clocks this die cannot deliver. A list of lines; empty is a pass.

    The host's half of the clock check. The CPU measures `sync` against the
    oscillator and cannot see the part; this sees the part and cannot count a
    clock, so the two are not redundant.

    Sources, none of them typed twice:
      * `soc/clocks.py` -- the EHXPLLL VCO window, the fPFD floor, the ULPI's
        exact 60 MHz, and the fabric ceiling this design has been observed to
        close under.
      * the record's own `device`/`speed`, so a bitstream built for one part and
        loaded onto another is what fails rather than the number.
    """
    import sys

    if limits is None:
        sys.path.insert(0, str(ROOT / "gateware" / "soc"))
        import clocks as limits

    problems = []
    sync_hz = record.get("sync_hz")
    usb_hz = record.get("usb_hz")
    ceiling = ceiling_mhz if ceiling_mhz is not None else limits.SYNC_CEILING_MHZ

    if usb_hz is not None and abs(usb_hz - limits.USB_PHY_MHZ * 1e6) > 1:
        problems.append(
            f"usb declared {usb_hz / 1e6:.3f} MHz; the ULPI PHY needs exactly "
            f"{limits.USB_PHY_MHZ:g} MHz and everything USB is derived from it")
    if sync_hz is None:
        return problems
    sync_mhz = sync_hz / 1e6
    if sync_mhz > ceiling:
        problems.append(
            f"sync declared {sync_mhz:.3f} MHz, above the {ceiling:g} MHz this "
            f"design has been observed to close at on a {record.get('device')} "
            f"-{record.get('speed')}")
    if limits.solve_pll(sync_mhz, limits.USB_PHY_MHZ) is None:
        problems.append(
            f"sync declared {sync_mhz:.3f} MHz, which no EHXPLLL divider set "
            f"reaches from {limits.USB_PHY_MHZ:g} MHz "
            f"(VCO {limits.VCO_MIN_MHZ:g}..{limits.VCO_MAX_MHZ:g} MHz, "
            f"fPFD >= {limits.PFD_MIN_MHZ:g} MHz)")
    return problems


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        code = int(sys.argv[1], 0)
        found = lookup(code)
        print(describe(code, found))
        if found:
            print(json.dumps(found, indent=2))
    else:
        table = json.loads((ROOT / INDEX).read_text()) if (ROOT / INDEX).exists() else {}
        for key, row in sorted(table.items()):
            print(f"{key}  {describe(int(key, 16), row)}")
