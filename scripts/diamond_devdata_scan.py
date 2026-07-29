#!/usr/bin/env python3
"""Classify every file under Diamond's ECP5 device-data trees (ep5c00 = ECP5U/UM,
ep5c00a = ECP5-5G) as text / near-text / binary, and dump samples.

Diamond obfuscates several of its data files with a trivial +8 byte cipher
(plaintext = ciphertext - 8, mod 256).  We detect that too and report a file as
"ciphered text" when the decoded stream is mostly printable.

Findings land in tmp/diamond-mine/, log in tmp/logs/diamond_devdata_scan.log.
"""

from __future__ import annotations

import json
import logging
import os
import string
import sys
from pathlib import Path

WORKTREE = Path("/mnt/2tb/git/cynthion-workspace/.claude/worktrees/agent-a2366741da283904f")
OUT_DIR = WORKTREE / "tmp" / "diamond-mine"
LOG_DIR = WORKTREE / "tmp" / "logs"
DIAMOND = Path("/home/dan/lscc/diamond/3.14")

# DEVICE-TREE MAP, verified from data/DiamondDevFile.xml (<Family text=...> and
# every <Part ach=...>).  The ECP5 trees are the sa5p00 ones -- NOT ep5c00.
#   ECP5U    -> sa5p00    LFE5U-12F/25F/45F/85F   <-- Cynthion's part
#   ECP5UM   -> sa5p00m   LFE5UM-25F/45F/85F
#   ECP5UM5G -> sa5p00g   LFE5UM5G-25F/45F/85F
# ep5c00 / ep5c00a are LatticeECP3 and are scanned only as a labelled contrast.
ECP5_TREES = {
    "sa5p00": "ECP5U (LFE5U-12F/25F/45F/85F) <- Cynthion",
    "sa5p00m": "ECP5UM (LFE5UM-25F/45F/85F)",
    "sa5p00g": "ECP5UM5G (LFE5UM5G-25F/45F/85F)",
    "ep5c00": "LatticeECP3 -- NOT ECP5 (contrast)",
    "ep5c00a": "LatticeECP3 variant -- NOT ECP5 (contrast)",
}

PRINTABLE = set(bytes(string.printable, "ascii"))
SAMPLE_BYTES = 4096


def printable_ratio(buf: bytes) -> float:
    if not buf:
        return 0.0
    return sum(1 for b in buf if b in PRINTABLE) / len(buf)


def decipher(buf: bytes) -> bytes:
    """Diamond's +8 obfuscation: plaintext = ciphertext - 8 (mod 256)."""
    return bytes((b - 8) & 0xFF for b in buf)


def classify(path: Path) -> dict:
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(SAMPLE_BYTES)
        # sample from the middle too - some binaries have text headers
        if size > SAMPLE_BYTES * 3:
            fh.seek(size // 2)
            mid = fh.read(SAMPLE_BYTES)
        else:
            mid = b""

    raw_ratio = printable_ratio(head + mid)
    dec_ratio = printable_ratio(decipher(head + mid))

    if raw_ratio >= 0.90:
        kind = "text"
    elif dec_ratio >= 0.90:
        kind = "ciphered-text(+8)"
    elif raw_ratio >= 0.60:
        kind = "mixed"
    elif dec_ratio >= 0.60:
        kind = "ciphered-mixed(+8)"
    else:
        kind = "binary"

    return {
        "path": str(path),
        "name": path.name,
        "ext": path.suffix.lower(),
        "size": size,
        "mtime": path.stat().st_mtime,
        "kind": kind,
        "printable_ratio": round(raw_ratio, 4),
        "deciphered_ratio": round(dec_ratio, 4),
        "magic_hex": head[:32].hex(),
        "magic_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in head[:32]),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "diamond_devdata_scan.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("scan")

    records: list[dict] = []
    for tree, desc in ECP5_TREES.items():
        root = DIAMOND / "ispfpga" / tree
        if not root.is_dir():
            log.error("missing tree %s", root)
            continue
        log.info("scanning %s  (%s)", root, desc)
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                p = Path(dirpath) / fn
                if not p.is_file() or p.is_symlink():
                    continue
                try:
                    rec = classify(p)
                except OSError as exc:
                    log.warning("cannot read %s: %s", p, exc)
                    continue
                rec["tree"] = tree
                rec["tree_desc"] = desc
                records.append(rec)

    log.info("classified %d files", len(records))

    # per-extension rollup so format regularity is visible
    by_ext: dict[str, dict] = {}
    for r in records:
        e = by_ext.setdefault(
            r["ext"] or "(none)",
            {"count": 0, "total_size": 0, "kinds": {}, "trees": set(), "examples": []},
        )
        e["count"] += 1
        e["total_size"] += r["size"]
        e["kinds"][r["kind"]] = e["kinds"].get(r["kind"], 0) + 1
        e["trees"].add(r["tree"])
        if len(e["examples"]) < 3:
            e["examples"].append({"name": r["name"], "magic": r["magic_ascii"], "hex": r["magic_hex"]})
    for e in by_ext.values():
        e["trees"] = sorted(e["trees"])

    (OUT_DIR / "ecp5_file_inventory.json").write_text(json.dumps(records, indent=1))
    (OUT_DIR / "ecp5_ext_summary.json").write_text(json.dumps(by_ext, indent=1))

    log.info("=== extensions, by total bytes ===")
    for ext, e in sorted(by_ext.items(), key=lambda kv: -kv[1]["total_size"]):
        log.info(
            "%-10s n=%-4d %10.1f MB  kinds=%s trees=%s",
            ext, e["count"], e["total_size"] / 1e6, e["kinds"], e["trees"],
        )

    log.info("=== readable (text or ciphered-text) files ===")
    readable = [r for r in records if r["kind"] in ("text", "ciphered-text(+8)", "mixed", "ciphered-mixed(+8)")]
    readable.sort(key=lambda r: (r["tree"], r["name"]))
    for r in readable:
        log.info("%-9s %-24s %9d  %s", r["tree"], r["name"], r["size"], r["kind"])

    # dump deciphered copies of every ciphered file so they can be grepped
    dec_dir = OUT_DIR / "deciphered"
    dec_dir.mkdir(exist_ok=True)
    n = 0
    for r in records:
        if not r["kind"].startswith("ciphered"):
            continue
        if r["size"] > 8_000_000:
            continue
        src = Path(r["path"])
        dst = dec_dir / f"{r['tree']}__{r['name']}.txt"
        dst.write_bytes(decipher(src.read_bytes()))
        n += 1
    log.info("wrote %d deciphered files to %s", n, dec_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
