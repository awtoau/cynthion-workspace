#!/usr/bin/env python3
"""Characterise Diamond ECP5 device geometry / routing containers.

Trees: ep5c00 (ECP5U/UM) and ep5c00a (ECP5-5G) ONLY.

Several formats turn out to be a small container header followed by one or more
zlib streams (magic 78 9c).  Two container shapes seen:

  A) "[[\\xa6\\xa6\\xc4\\xc4r"  + u32be? + zlib stream        (.pkg .hrg .nph .bxg)
  B) " _$COMP\\0" + u32 + u32   + zlib stream                  (.tld)

This script:
  * finds every 78 9c offset in the head of each file,
  * tries to inflate from each, records how far it got,
  * for multi-stream files, walks the whole file inflating chunk after chunk,
  * dumps the inflated bytes and reports whether the result is ASCII-ish,
  * writes inflated output to tmp/diamond-mine/inflated/.
"""

from __future__ import annotations

import json
import logging
import string
import sys
import zlib
from pathlib import Path

WORKTREE = Path("/mnt/2tb/git/cynthion-workspace/.claude/worktrees/agent-a2366741da283904f")
OUT_DIR = WORKTREE / "tmp" / "diamond-mine" / "inflated"
LOG_DIR = WORKTREE / "tmp" / "logs"
DIAMOND = Path("/home/dan/lscc/diamond/3.14/ispfpga")

TARGETS = {
    "ep5c00": [
        "ep5c97x146.pkg", "ep5c97x146.tld", "ep5c97x146.hrg", "ep5c97x146.nph",
        "ep5c97x146.bxg", "ep5c97x146.bxd", "ep5c97x146.grf", "ep5c97x146.grd",
        "ep5c97x146.ddy", "ep5c00.bfd", "ep5c00.lmd", "cmodel.ncm", "neodata.etc",
    ],
    "ep5c00a": ["ec5a53x56.pkg", "ec5a53x56.hrg", "ep5c00a.bfd"],
}
TREE_DESC = {"ep5c00": "ECP5U/ECP5UM", "ep5c00a": "ECP5-5G"}
PRINTABLE = set(bytes(string.printable, "ascii"))


def inflate_all(buf: bytes, start: int) -> tuple[bytes, int]:
    """Inflate consecutive zlib streams beginning at `start`. Returns (data, streams)."""
    out = bytearray()
    pos = start
    streams = 0
    n = len(buf)
    while pos < n:
        idx = buf.find(b"\x78\x9c", pos)
        if idx < 0:
            break
        d = zlib.decompressobj()
        try:
            out += d.decompress(buf[idx:])
        except zlib.error:
            pos = idx + 2
            continue
        streams += 1
        if not d.eof:
            break
        consumed = len(buf) - idx - len(d.unused_data)
        pos = idx + consumed
    return bytes(out), streams


def ratio(b: bytes) -> float:
    if not b:
        return 0.0
    s = b[:200_000]
    return sum(1 for c in s if c in PRINTABLE) / len(s)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "diamond_geom_probe.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("geom")

    report = {}
    for tree, names in TARGETS.items():
        for nm in names:
            p = DIAMOND / tree / "data" / nm
            if not p.is_file():
                log.warning("missing %s", p)
                continue
            buf = p.read_bytes()
            first = buf.find(b"\x78\x9c", 0, 4096)
            log.info("=== %s [%s = %s] %d bytes, first zlib magic at %s",
                     nm, tree, TREE_DESC[tree], len(buf), first)
            log.info("    header hex  %s", buf[:32].hex())
            log.info("    header ascii %r", "".join(
                chr(b) if 32 <= b < 127 else "." for b in buf[:48]))

            entry = {
                "tree": tree, "tree_desc": TREE_DESC[tree], "path": str(p),
                "size": len(buf), "zlib_at": first,
                "header_hex": buf[:32].hex(),
                "raw_printable": round(ratio(buf), 3),
            }

            if first >= 0:
                data, streams = inflate_all(buf, first)
                entry.update(
                    compressed=True, streams=streams, inflated_size=len(data),
                    ratio=round(len(data) / max(1, len(buf)), 2),
                    inflated_printable=round(ratio(data), 3),
                    inflated_head=repr(data[:400]),
                )
                log.info("    INFLATED: %d streams -> %d bytes (x%.1f), printable=%.2f",
                         streams, len(data), len(data) / max(1, len(buf)), ratio(data))
                log.info("    inflated head: %r", data[:300])
                if data:
                    (OUT_DIR / f"{tree}__{nm}.inflated.bin").write_bytes(data)
            else:
                entry["compressed"] = False
                log.info("    not zlib; raw printable ratio %.2f", ratio(buf))
                log.info("    raw head: %r", buf[:300])
                entry["raw_head"] = repr(buf[:400])

            report[f"{tree}/{nm}"] = entry

    (WORKTREE / "tmp" / "diamond-mine" / "geometry_formats.json").write_text(
        json.dumps(report, indent=1))
    log.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
