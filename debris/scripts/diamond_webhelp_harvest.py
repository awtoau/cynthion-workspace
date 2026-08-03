#!/usr/bin/env python3
"""Harvest Lattice Diamond webhelp HTML into structured JSON for mining.

Diamond ships 2092 HTML pages under docs/webhelp/eng/. This converts every page
to plain text plus extracted tables, so later passes can grep/cross-reference
mechanically instead of browsing.

Output:
  tmp/diamond-mine/webhelp_pages.jsonl   one record per page
  tmp/logs/diamond_webhelp_harvest.log
"""
from __future__ import annotations

import html
import json
import logging
import re
import sys
from pathlib import Path

DIAMOND = Path.home() / "lscc/diamond/3.14"
WEBHELP = DIAMOND / "docs/webhelp/eng"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp/diamond-mine"
LOGS = ROOT / "tmp/logs"


def setup_logging(name: str) -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOGS / f"{name}.log", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TABLE_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)
ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)


def detag(s: str) -> str:
    s = SCRIPT_RE.sub(" ", s)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s)


def page_text(raw: str) -> str:
    body = SCRIPT_RE.sub(" ", raw)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", body, flags=re.I)
    txt = detag(body)
    lines = [ln.strip() for ln in txt.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_tables(raw: str) -> list[list[list[str]]]:
    tables = []
    for tbl in TABLE_RE.findall(raw):
        rows = []
        for row in ROW_RE.findall(tbl):
            cells = [detag(c).strip() for c in CELL_RE.findall(row)]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def main() -> int:
    log = setup_logging("diamond_webhelp_harvest")
    OUT.mkdir(parents=True, exist_ok=True)
    if not WEBHELP.is_dir():
        log.error("webhelp not found: %s", WEBHELP)
        return 1

    pages = sorted(p for p in WEBHELP.rglob("*") if p.suffix.lower() in (".htm", ".html"))
    log.info("found %d html pages under %s", len(pages), WEBHELP)

    out_path = OUT / "webhelp_pages.jsonl"
    n_tables = 0
    with out_path.open("w") as fh:
        for i, p in enumerate(pages, 1):
            try:
                raw = p.read_text(errors="replace")
            except OSError as exc:
                log.warning("unreadable %s: %s", p, exc)
                continue
            m = TITLE_RE.search(raw)
            title = detag(m.group(1)).strip() if m else ""
            tables = extract_tables(raw)
            n_tables += len(tables)
            rec = {
                "path": str(p.relative_to(WEBHELP)),
                "section": p.relative_to(WEBHELP).parts[0],
                "title": title,
                "text": page_text(raw),
                "tables": tables,
                "bytes": p.stat().st_size,
            }
            fh.write(json.dumps(rec) + "\n")
            if i % 250 == 0:
                log.info("  %d/%d pages", i, len(pages))

    log.info("wrote %s (%d pages, %d tables)", out_path, len(pages), n_tables)

    sections: dict[str, int] = {}
    for p in pages:
        sections[p.relative_to(WEBHELP).parts[0]] = sections.get(p.relative_to(WEBHELP).parts[0], 0) + 1
    for sec, cnt in sorted(sections.items(), key=lambda kv: -kv[1]):
        log.info("  section %-40s %4d pages", sec, cnt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
