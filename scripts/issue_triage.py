#!/usr/bin/env python3
"""Cross-reference open issues against the tree, to find ones already done.

The recurring failure here is not an unfixed bug, it is a FIXED bug whose issue
stayed open -- six of them in one day. That makes the priority queue lie about
what is urgent, which is worse than a long queue.

Three signals, none conclusive on its own:

  commits   the issue number appears in a commit message on main
  gone      a path the issue names no longer exists (deleted, or renamed)
  present   every path it names still exists

Output is a ranked candidate list for a human to read. It closes nothing.

    ./scripts/issue_triage.py                 # all open issues
    ./scripts/issue_triage.py --label p0      # one label
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "awtoau/cynthion-workspace"
LOG = ROOT / "tmp" / "logs" / "issue-triage.log"

# A path mentioned in an issue body: backticked, with a directory separator and
# a plausible extension. Deliberately narrow -- a false path costs a wrong
# verdict, and this exists to rank, not to decide.
PATH = re.compile(r"`([\w./-]+/[\w.-]+\.(?:py|rs|md|toml|v|json|sh))`")


def _roots() -> list[Path]:
    """The tree, plus each submodule -- an issue names `apollo_fpga/ecp5.py`
    without the `repos/apollo/` prefix, and calling that deleted is a lie."""
    return [ROOT] + sorted(p.parent for p in ROOT.glob("repos/*/.git"))


ROOTS = _roots()


def _anywhere(rel: str) -> bool:
    return any((r / rel).exists() for r in ROOTS)


def sh(args: list[str], cwd: Path = ROOT) -> str:
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return out.stdout


def issues(label: str | None) -> list[dict]:
    cmd = ["gh", "issue", "list", "--repo", REPO, "--state", "open",
           "--limit", "400", "--json", "number,title,body,labels,updatedAt"]
    if label:
        cmd += ["--label", label]
    return json.loads(sh(cmd) or "[]")


def commit_mentions() -> dict[int, list[str]]:
    """Issue number -> commit subjects on main that name it."""
    text = sh(["git", "log", "--format=%h\t%s%n%b", "--no-merges", "-2000"])
    found: dict[int, list[str]] = {}
    subject = ""
    for line in text.splitlines():
        if "\t" in line and len(line.split("\t", 1)[0]) in (7, 8, 9, 10):
            subject = line.split("\t", 1)[1]
        for n in re.findall(r"#(\d{2,4})\b", line):
            found.setdefault(int(n), [])
            if subject and subject not in found[int(n)]:
                found[int(n)].append(subject)
    return found


def classify(issue: dict, mentions: dict[int, list[str]]) -> dict:
    body = issue.get("body") or ""
    paths = sorted(set(PATH.findall(body)))
    gone = [p for p in paths if not _anywhere(p)]
    subjects = mentions.get(issue["number"], [])

    # Rank: a commit naming it AND a path it names now missing is the strongest
    # signal that the work landed and the file moved or was deleted with it.
    score = (2 if subjects else 0) + (2 if gone else 0) + (1 if paths and not gone else 0)
    return {
        "number": issue["number"],
        "title": issue["title"],
        "labels": [l["name"] for l in issue["labels"]],
        "commits": subjects[:3],
        "paths_gone": gone[:4],
        "paths_seen": len(paths),
        "score": score,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", help="restrict to one label")
    ap.add_argument("--min-score", type=int, default=1)
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    mentions = commit_mentions()
    rows = [classify(i, mentions) for i in issues(args.label)]
    rows = [r for r in rows if r["score"] >= args.min_score]
    rows.sort(key=lambda r: (-r["score"], r["number"]))

    lines = [f"{len(rows)} candidate(s), ranked. This closes nothing.\n"]
    for r in rows:
        lines.append(f"#{r['number']}  score {r['score']}  "
                     f"[{','.join(r['labels']) or '-'}]  {r['title'][:88]}")
        for s in r["commits"]:
            lines.append(f"      commit: {s[:96]}")
        for p in r["paths_gone"]:
            lines.append(f"      gone:   {p}")
    text = "\n".join(lines)
    print(text)
    LOG.write_text(text + "\n")
    print(f"\n-> {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
