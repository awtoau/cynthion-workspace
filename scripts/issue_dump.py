#!/usr/bin/env python3
"""Dump every open issue (body + comments) into ./tmp/issues/<n>.md for triage.

Why: `gh issue view` one at a time is slow and noisy in an agent transcript.
This writes one file per issue plus a combined index, and logs to
./tmp/logs/issue_dump.log as the repo rules require.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "awtoau/cynthion-workspace"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp" / "issues"
LOGS = ROOT / "tmp" / "logs"

FIELDS = "number,title,state,body,labels,createdAt,updatedAt,comments,url"


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOGS / "issue_dump.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def fetch(number: int) -> dict:
    out = subprocess.run(
        ["gh", "issue", "view", str(number), "--repo", REPO, "--json", FIELDS],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def render(d: dict) -> str:
    labels = ",".join(l["name"] for l in d.get("labels") or []) or "-"
    parts = [
        f"# #{d['number']} {d['title']}",
        f"url: {d['url']}",
        f"labels: {labels}",
        f"created: {d['createdAt']}  updated: {d['updatedAt']}",
        "",
        "## BODY",
        (d.get("body") or "").strip() or "(empty)",
    ]
    for c in d.get("comments") or []:
        author = (c.get("author") or {}).get("login", "?")
        parts += ["", f"## COMMENT by {author} at {c.get('createdAt')}",
                  (c.get("body") or "").strip()]
    return "\n".join(parts) + "\n"


def main() -> int:
    setup_logging()
    OUT.mkdir(parents=True, exist_ok=True)
    numbers = [int(n) for n in (OUT / "open.txt").read_text().split()]
    logging.info("fetching %d issues", len(numbers))

    def one(n: int):
        try:
            d = fetch(n)
        except subprocess.CalledProcessError as e:
            logging.error("issue %d failed: %s", n, e.stderr.strip())
            return None
        (OUT / f"{n}.md").write_text(render(d))
        return d

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for r in pool.map(one, numbers) if r]

    logging.info("wrote %d issue files to %s", len(results), OUT)
    combined = OUT / "ALL.md"
    combined.write_text(
        "\n\n---\n\n".join(
            (OUT / f"{d['number']}.md").read_text()
            for d in sorted(results, key=lambda d: d["number"])
        )
    )
    logging.info("combined index: %s (%d bytes)", combined, combined.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
