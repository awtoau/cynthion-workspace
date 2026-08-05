#!/usr/bin/env python3
"""Post one triage status comment per issue, from `tmp/comments/<n>.md`.

Companion to `issue_dump.py`, which pulls every open issue into
`tmp/issues/<n>.md`. Triage writes its findings back as one file per issue and
this posts them, so the drafts are reviewable as a set before anything reaches
the tracker.

    ./scripts/issue_comment_post.py --dry-run     # list what would be posted
    ./scripts/issue_comment_post.py               # post them

Comments only. This script cannot close, retitle or relabel anything, and that
is deliberate: closure is the repo owner's decision.

Logs to `tmp/logs/issue_comment_post.log` alongside the console, per the repo's
logging rule -- a post that fails halfway needs a record of which issues already
have the comment.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = "awtoau/cynthion-workspace"
ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "tmp" / "comments"
LOGS = ROOT / "tmp" / "logs"

# Refuse to post anything carrying a private path or a credential-shaped word.
# The repo is public; a scrub that runs is worth more than one that is
# remembered.
FORBIDDEN = ("/mnt/", "/home/", "password", "api_key", "api-key", "secret")


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOGS / "issue_comment_post.log", mode="a"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def scrub(path: Path) -> list[str]:
    text = path.read_text().lower()
    return [bad for bad in FORBIDDEN if bad in text]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", type=int, nargs="*",
                    help="post only these issue numbers")
    args = ap.parse_args()
    setup_logging()

    drafts = sorted(DRAFTS.glob("*.md"), key=lambda p: int(p.stem))
    if args.only:
        drafts = [p for p in drafts if int(p.stem) in args.only]

    failed = []
    for path in drafts:
        number = int(path.stem)
        bad = scrub(path)
        if bad:
            logging.error("#%d REFUSED -- contains %s", number, ", ".join(bad))
            failed.append(number)
            continue
        if args.dry_run:
            logging.info("#%d would post %d bytes", number, path.stat().st_size)
            continue
        out = subprocess.run(
            ["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body-file", str(path)],
            capture_output=True, text=True,
        )
        if out.returncode:
            logging.error("#%d FAILED: %s", number, out.stderr.strip())
            failed.append(number)
        else:
            logging.info("#%d posted: %s", number, out.stdout.strip())

    logging.info("%d drafts, %d failed", len(drafts), len(failed))
    if failed:
        logging.error("failed: %s", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
