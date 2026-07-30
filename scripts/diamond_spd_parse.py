#!/usr/bin/env python3
"""Parse Lattice Diamond .spd (speed/timing) files for the ECP5 trees.

IMPORTANT DEVICE-TREE CORRECTION
--------------------------------
ep5c00 / ep5c00a are *LatticeECP3*, NOT ECP5.  The real ECP5 trees are the
sa5p00 family.  Verified from data/DiamondDevFile.xml, which maps every part
number to its arch tree via the `ach` attribute:

    ECP5U    -> sa5p00    LFE5U-12F / -25F / -45F / -85F   <-- Cynthion
    ECP5UM   -> sa5p00m   LFE5UM-25F / -45F / -85F
    ECP5UM5G -> sa5p00g   LFE5UM5G-25F / -45F / -85F
    LatticeECP3 -> ep5c00 / ep5c00a

Corroborating evidence: ispfpga/sa5p00/data/ contains LFE5U-12F_CABGA256.con,
LFE5U-12F_TQFP144.svg etc, and sa5p00.bfd names SLICE_A/SLICE_B/SLICE_C.
ispfpga/ep5c00/data/ has no LFE5U anything.

The naming is a trap: "ep5c" *looks* like ECP5 but expands to ECP-3 internally,
while "sa5p" is the ECP5 codename.

Observed layout (little-endian header, big-endian payload ints):

  offset 0    : u32 = 2            (format version?)
  offset 4..  : mostly zero padding
  ~0x87       : 0xAA 0xAA marker, then a Pascal string "Final 35.22 "
                (length-prefixed: one byte len, then chars)
  then        : "M", newline, Pascal string of the device name "ep5c97x146"

  After the header the file is a flat sequence of DELAY RECORDS.  Each record is:

     <variable binary prefix: selector / pin-index / mux bytes>
     4 x int32be   delay values in picoseconds, for four corners
     u8 len, name  ASCII delay-arc name   e.g. "C2OUT_DEL", "GSRREC_HLD"
     u8 len, cond  ASCII condition string e.g.
                   "CLKMUX:CLK:::CLK=#INV INA:INA MODE:IDDR_ODDR"
     0xFF          record terminator

The four int32be values repeat as (v, v, v, v) for corner-independent arcs and
differ for real min/max data, which is consistent with a
(min-rise, min-fall, max-rise, max-fall) or (best/typ/worst) corner tuple.

Strategy: rather than fully reversing the binary prefix, scan for the
name/condition string pairs (which are unambiguous, being length-prefixed ASCII
terminated by NUL/0xFF) and take the 16 bytes immediately preceding as the
delay quad.  That recovers name + condition + 4 delays for every arc, which is
the actually-useful content.

Output: tmp/diamond-mine/spd/<device>.arcs.json + a summary table.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import struct
import sys
from pathlib import Path

WORKTREE = Path("/mnt/2tb/git/cynthion-workspace/.claude/worktrees/agent-a2366741da283904f")
OUT_DIR = WORKTREE / "tmp" / "diamond-mine" / "spd"
LOG_DIR = WORKTREE / "tmp" / "logs"
DIAMOND = Path("/home/dan/lscc/diamond/3.14/ispfpga")

# DEVICE TREE MAP -- verified against /home/dan/lscc/diamond/3.14/data/DiamondDevFile.xml
# <Family name="..." text="<tree>"> and every <Part ... ach="<tree>">:
#
#     ECP5U     -> sa5p00    (LFE5U-12F/25F/45F/85F)   <-- the Cynthion part
#     ECP5UM    -> sa5p00m   (LFE5UM-25F/45F/85F)
#     ECP5UM5G  -> sa5p00g   (LFE5UM5G-25F/45F/85F)
#     LatticeECP3 -> ep5c00  (NOT ECP5!)
#     LatticeECP3 5G variant -> ep5c00a (NOT ECP5!)
#
# ep5c00 / ep5c00a are LatticeECP3, a different family entirely.  They are kept
# here only as an explicit CONTRAST set so the distinction stays visible.
SPD_FILES = {
    "sa5p00": ["sa5p25.spd", "sa5p45.spd", "sa5p85.spd"],
    "sa5p00m": None,   # filled by discovery below
    "sa5p00g": None,
    # contrast only - NOT ECP5:
    "ep5c00": ["ep5c97x146.spd"],
}
TREE_DESC = {
    "sa5p00": "ECP5U  (LFE5U-12F/25F/45F/85F) <- Cynthion",
    "sa5p00m": "ECP5UM (LFE5UM-25F/45F/85F)",
    "sa5p00g": "ECP5UM5G (LFE5UM5G-25F/45F/85F)",
    "ep5c00": "LatticeECP3 -- NOT ECP5, contrast only",
}


def discover(tree: str) -> list[str]:
    d = DIAMOND / tree / "data"
    return sorted(p.name for p in d.glob("*.spd")) if d.is_dir() else []

# A delay-arc name: uppercase ASCII, digits, underscore.  Length-prefixed.
NAME_RE = re.compile(rb"[A-Z][A-Z0-9_]{2,63}")
# inline delay payloads inside condition strings, e.g. "; #VO:762,762,763,763"
EMBED_RE = re.compile(r"#([A-Z]{2,6}):([-0-9][-0-9,]*)")


def parse_header(buf: bytes) -> dict:
    hdr = {"format_u32": struct.unpack_from("<I", buf, 0)[0]}
    marker = buf.find(b"\xaa\xaa")
    hdr["aa_marker_at"] = marker
    if marker >= 0:
        # Pascal strings follow: <len><chars>
        p = marker + 2
        strs = []
        for _ in range(6):
            if p >= len(buf):
                break
            ln = buf[p]
            if 0 < ln < 64 and all(9 <= c < 127 for c in buf[p + 1 : p + 1 + ln]):
                strs.append(buf[p + 1 : p + 1 + ln].decode("ascii", "replace"))
                p += 1 + ln
            else:
                p += 1
        hdr["header_strings"] = strs
    return hdr


def find_sections(buf: bytes) -> list[dict]:
    """A .spd holds SEVERAL speed-grade sections, each starting with an
    0xAA 0xAA marker followed by a version tag like 'Final 35.22 ' and the
    device name.  Return the section boundaries."""
    # 0xAA 0xAA also occurs incidentally inside delay data, so a section only
    # counts when the marker is followed (within a few bytes) by a version tag
    # Pascal string such as "Final 35.22 " and then the device name.
    secs = []
    pos = 0
    while True:
        m = buf.find(b"\xaa\xaa", pos)
        if m < 0:
            break
        pos = m + 2
        tag = ""
        for probe in range(m + 2, min(m + 14, len(buf))):
            ln = buf[probe]
            if 4 <= ln <= 40:
                cand = buf[probe + 1 : probe + 1 + ln]
                if cand.startswith((b"Final", b"Prelim", b"Advanc")):
                    tag = cand.decode("ascii", "replace").strip()
                    break
        if tag:
            secs.append({"start": m, "tag": tag})
    for i, s in enumerate(secs):
        s["end"] = secs[i + 1]["start"] if i + 1 < len(secs) else len(buf)
    return secs


def extract_arcs(buf: bytes, base: int = 0) -> list[dict]:
    """Records look like:

        ... <16 bytes: 4 x int32be delay> <u8 nlen><NAME>\\0 <u8 clen><COND>\\0 0xFF

    i.e. name and condition are BOTH length-prefixed AND NUL-terminated, so the
    condition-length byte sits at name_end+1, not name_end.
    """
    arcs = []
    n = len(buf)
    i = 0
    while i < n - 4:
        ln = buf[i]
        if not (3 <= ln <= 63):
            i += 1
            continue
        name_b = buf[i + 1 : i + 1 + ln]
        if len(name_b) != ln or not NAME_RE.fullmatch(name_b):
            i += 1
            continue
        j = i + 1 + ln
        if j >= n or buf[j] != 0:  # NUL terminator after the name
            i += 1
            continue
        # After "<nlen><NAME>\0" comes a u16be condition length, then the
        # condition text, then its own NUL.  Zero-length means unconditional.
        if j + 3 > n:
            break
        clen = int.from_bytes(buf[j + 1 : j + 3], "big")
        cond_b = b""
        nxt = j + 3
        if 0 < clen <= 512:
            cand = buf[j + 3 : j + 3 + clen]
            if len(cand) == clen and all(9 <= c < 127 for c in cand):
                cond_b = cand
                nxt = j + 3 + clen
        if i < 16:
            i = nxt
            continue
        quad = struct.unpack(">4i", buf[i - 16 : i])
        cond = cond_b.decode("ascii", "replace")
        rec = {
            "name": name_b.decode("ascii"),
            "cond": cond,
            # raw values are picoseconds * 1024
            "raw": list(quad),
            "delays_ps": [round(v / 1024.0, 3) for v in quad],
            "offset": base + i - 16,
        }
        # Some arcs (notably the IO buffer ones) carry a zero delay quad and put
        # the real numbers inline in the condition text as "#VO:762,762,763,763"
        # -- these are already plain picoseconds, NOT scaled by 1024.
        emb = EMBED_RE.findall(cond)
        if emb:
            rec["embedded_ps"] = {
                tag: [int(v) for v in vals.split(",") if v.strip("-").isdigit()]
                for tag, vals in emb
            }
        arcs.append(rec)
        i = nxt
    return arcs


def plausible(a: dict) -> bool:
    """Filter obvious false positives: delays should be sane picosecond values."""
    d = a["raw"]
    return all(-100_000 <= v <= 100_000_000 for v in d)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "diamond_spd_parse.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("spd")

    overview = {}
    for tree, names in SPD_FILES.items():
        for nm in (names if names is not None else discover(tree)):
            path = DIAMOND / tree / "data" / nm
            if not path.is_file():
                log.error("missing %s", path)
                continue
            buf = path.read_bytes()
            hdr = parse_header(buf[:512])
            secs = find_sections(buf)
            log.info("=== %s  [tree %s = %s]  %d bytes", nm, tree, TREE_DESC[tree], len(buf))
            log.info("    header: %s", hdr)
            log.info("    %d speed-grade sections: %s",
                     len(secs), [(s["tag"], s["start"], s["end"] - s["start"]) for s in secs])

            sections_out = []
            for si, s in enumerate(secs):
                sub = buf[s["start"]:s["end"]]
                arcs = [a for a in extract_arcs(sub, s["start"]) if plausible(a)]
                by_name = collections.Counter(a["name"] for a in arcs)
                with_cond = sum(1 for a in arcs if a["cond"])
                log.info("    section %d tag=%-14s arcs=%-7d names=%-4d with_cond=%d",
                         si, s["tag"], len(arcs), len(by_name), with_cond)
                sections_out.append({
                    "index": si, "tag": s["tag"], "start": s["start"],
                    "arc_count": len(arcs), "distinct_names": len(by_name),
                    "arcs_with_condition": with_cond, "arcs": arcs,
                })

            all_names = collections.Counter(
                a["name"] for sec in sections_out for a in sec["arcs"])
            log.info("    %d distinct arc names overall; top 20:", len(all_names))
            for nmx, cnt in all_names.most_common(20):
                log.info("        %-28s %6d", nmx, cnt)

            stem = path.stem
            (OUT_DIR / f"{tree}__{stem}.arcs.json").write_text(
                json.dumps({"tree": tree, "tree_desc": TREE_DESC[tree],
                            "file": str(path), "header": hdr,
                            "note": "raw delay units are picoseconds*1024",
                            "sections": sections_out}, indent=1)
            )
            overview[f"{tree}/{nm}"] = {
                "tree_desc": TREE_DESC[tree],
                "bytes": len(buf),
                "header": hdr,
                "sections": [{k: v for k, v in s.items() if k != "arcs"}
                             for s in sections_out],
                "total_arcs": sum(s["arc_count"] for s in sections_out),
                "distinct_names": len(all_names),
                "top_names": all_names.most_common(40),
            }

    (OUT_DIR / "overview.json").write_text(json.dumps(overview, indent=1))
    log.info("wrote %s", OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
