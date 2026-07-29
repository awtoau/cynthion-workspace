#!/usr/bin/env python3
"""Extract named Diamond webhelp pages to plain text for reading.

Usage:
  diamond_webhelp_extract.py <substring> [<substring> ...]

Matches page paths/titles in tmp/diamond-mine/webhelp_pages.jsonl and writes
each match to tmp/diamond-mine/pages/<slug>.txt (text + flattened tables).
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINE = ROOT / "tmp/diamond-mine"
LOGS = ROOT / "tmp/logs"


def setup_logging(name: str) -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGS / f"{name}.log", mode="a")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120]


def render(rec: dict) -> str:
    out = [f"# {rec['title']}", f"# path: {rec['path']}", ""]
    out.append(rec["text"])
    if rec["tables"]:
        out.append("\n\n===== TABLES =====")
        for i, tbl in enumerate(rec["tables"], 1):
            out.append(f"\n--- table {i} ---")
            for row in tbl:
                out.append(" | ".join(row))
    return "\n".join(out)


def main(argv: list[str]) -> int:
    log = setup_logging("diamond_webhelp_extract")
    if not argv:
        log.error("need at least one substring")
        return 2
    pat = [a.lower() for a in argv]
    src = MINE / "webhelp_pages.jsonl"
    dst = MINE / "pages"
    dst.mkdir(parents=True, exist_ok=True)

    n = 0
    with src.open() as fh:
        for line in fh:
            rec = json.loads(line)
            hay = (rec["path"] + " " + rec["title"]).lower()
            if any(p in hay for p in pat):
                path = dst / (slug(Path(rec["path"]).stem) + ".txt")
                path.write_text(render(rec))
                log.info("wrote %s (%d chars, %d tables)", path.name, len(rec["text"]), len(rec["tables"]))
                n += 1
    log.info("extracted %d pages for %s", n, argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
