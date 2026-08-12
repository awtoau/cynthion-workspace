#!/usr/bin/env python3
#
# Report file references in open GitHub issue bodies that resolve to nothing.
# SPDX-License-Identifier: BSD-3-Clause

"""
The tracker accumulated 55 open issues whose bodies cite paths that the tree no
longer has -- `ecp5-test/` became `gateware/`, several documents were retired to
`debris/`, and `repos/luna` stopped being a submodule.  A reference that does not
resolve reads as checked when it is not, so this finds them mechanically rather
than by eye.

    ./scripts/audit_issue_refs.py                 # every open issue
    ./scripts/audit_issue_refs.py 97 110 202      # only these

Reads `tmp/issues-open.json` (refresh with
`gh issue list --state open --limit 300 --json number,title,body`), writes the
report to `tmp/logs/audit_issue_refs.log` as well as stdout.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "tmp" / "issues-open.json"
LOG = ROOT / "tmp" / "logs" / "audit_issue_refs.log"

# A path reference: a directory we own, a slash, and a file extension we use.
TOPS = ("ecp5-test", "gateware", "scripts", "firmware", "docs", "repos", "tests",
        "linux-on-cynthion", "debris", "venv", "luna")
EXTS = ("py", "md", "rs", "c", "h", "x", "toml", "json", "patch", "diff", "ys",
        "v", "sch", "cfg", "lds", "svd", "yml", "yaml")
PATH = re.compile(
    r"(?<![\w/.-])((?:%s)/[A-Za-z0-9_./+*-]*\.(?:%s))" % ("|".join(TOPS), "|".join(EXTS))
)
# Directory references, e.g. `ecp5-test/pins/` or `repos/luna/`.
DIRREF = re.compile(r"(?<![\w/.-])((?:%s)/[A-Za-z0-9_./+-]*/)(?![A-Za-z0-9_.+-])" % "|".join(TOPS))


def references(body):
    """Every distinct path-shaped token in an issue body, in first-seen order."""
    seen = {}
    for m in PATH.findall(body) + DIRREF.findall(body):
        seen.setdefault(m.rstrip(":,"), None)
    return list(seen)


def main():
    wanted = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    issues = json.loads(CACHE.read_text())

    lines = []
    total_missing = 0
    affected = 0
    for issue in sorted(issues, key=lambda i: i["number"]):
        if wanted and issue["number"] not in wanted:
            continue
        missing = [r for r in references(issue["body"] or "") if not (ROOT / r).exists()]
        if not missing:
            continue
        affected += 1
        total_missing += len(missing)
        lines.append(f"#{issue['number']:<4} {issue['title']}")
        for ref in missing:
            lines.append(f"        {ref}")

    lines.append(f"\n{affected} issues, {total_missing} references that resolve to nothing")
    report = "\n".join(lines)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(report + "\n")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
