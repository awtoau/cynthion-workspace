#!/usr/bin/env python3
"""Extract package pinout tables from Diamond ECP5 .pkg files.

Trees: ep5c00 (ECP5U/UM), ep5c00a (ECP5-5G).

.pkg container = 11-byte header ('[[\\xa6\\xa6\\xc4\\xc4r' + u32be) then N zlib
streams. Inflated payload begins with a 128-byte zeroed header, then:

    <u8 len> "FPBGA1156\\n"      package name   (newline-terminated, len-prefixed)
    <u8 len> "ep5c97x146\\n"     device name
    u32                          record count-ish
    then repeating: <u8 len><BALLNAME "\\n"> <u32be index>

So the pin table is <ball name, ordinal> pairs; the ordinal steps by 2 and is a
site index into the device's pad ordering.  Later streams hold the pad-function
side (PT/PB/PL/PR bank + pad names) which pairs with the ordinals.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import zlib
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
OUT_DIR = WORKTREE / "tmp" / "diamond-mine" / "pkg"
LOG_DIR = WORKTREE / "tmp" / "logs"
DIAMOND = Path.home() / "lscc" / "diamond" / "3.14" / "ispfpga"

PKG_FILES = {
    "ep5c00": ["ep5c97x146.pkg"],
    "ep5c00a": ["ec5a53x56.pkg", "ec5a71x74.pkg", "ec5a97x146.pkg", "ec5a124x182.pkg"],
}
TREE_DESC = {"ep5c00": "ECP5U/ECP5UM", "ep5c00a": "ECP5-5G"}

BALL_RE = re.compile(rb"[A-Z]{1,2}[0-9]{1,2}")
TOKEN_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_/\[\]\.\-]{1,40}")


def inflate_all(buf: bytes) -> bytes:
    out = bytearray()
    pos = 0
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
        if not d.eof:
            break
        pos = idx + (n - idx - len(d.unused_data))
    return bytes(out)


def parse_balls(data: bytes) -> tuple[str, str, list[dict]]:
    """Pull package name, device name and the <ball, ordinal> table."""
    pkg_name = dev_name = ""
    m = re.search(rb"([A-Z]{2,8}\d{2,4})\n", data[:400])
    if m:
        pkg_name = m.group(1).decode()
    m2 = re.search(rb"\n((?:ep5c|ec5a)[0-9x]+)\n", data[:400])
    if m2:
        dev_name = m2.group(1).decode()

    # Records are: <u8 tag=0x01> <BALLNAME> "\n" <u32be ordinal>
    # The tag byte is a constant 0x01, NOT a length; the name is newline-terminated.
    balls: list[dict] = []
    n = len(data)
    i = 0
    while i < n - 8:
        if data[i] != 0x01:
            i += 1
            continue
        nl = data.find(b"\n", i + 1, i + 8)
        if nl < 0:
            i += 1
            continue
        nm = data[i + 1 : nl]
        if not BALL_RE.fullmatch(nm):
            i += 1
            continue
        ordv = int.from_bytes(data[nl + 1 : nl + 5], "big")
        balls.append({"ball": nm.decode(), "ordinal": ordv, "off": i})
        i = nl + 5
    return pkg_name, dev_name, balls


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "diamond_pkg_extract.log", mode="w"),
                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("pkg")

    summary = {}
    for tree, names in PKG_FILES.items():
        for nm in names:
            p = DIAMOND / tree / "data" / nm
            if not p.is_file():
                log.warning("missing %s", p)
                continue
            data = inflate_all(p.read_bytes())
            pkg, dev, balls = parse_balls(data)
            # all package names present in the payload (a .pkg holds every package)
            pkgs = sorted(set(x.decode() for x in re.findall(
                rb"(?:FPBGA|CABGA|CSFBGA|QFN|TQFP|FTBGA|LFBGA|WLCSP)\d+", data)))
            log.info("=== %s [%s = %s]  inflated %d bytes", nm, tree, TREE_DESC[tree], len(data))
            log.info("    first package=%s device=%s", pkg, dev)
            log.info("    packages present: %s", pkgs)
            log.info("    ball records recovered: %d", len(balls))
            uniq = sorted(set(b["ball"] for b in balls))
            log.info("    distinct ball names: %d  e.g. %s", len(uniq), uniq[:20])

            stem = p.stem
            (OUT_DIR / f"{tree}__{stem}.pkg.json").write_text(json.dumps(
                {"tree": tree, "tree_desc": TREE_DESC[tree], "file": str(p),
                 "packages": pkgs, "device": dev, "balls": balls}, indent=1))
            (OUT_DIR / f"{tree}__{stem}.strings.txt").write_bytes(
                b"\n".join(TOKEN_RE.findall(data)))
            summary[f"{tree}/{nm}"] = {
                "tree_desc": TREE_DESC[tree], "inflated": len(data),
                "packages": pkgs, "ball_records": len(balls), "distinct_balls": len(uniq)}

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1))
    log.info("done -> %s", OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
