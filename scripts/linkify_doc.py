#!/usr/bin/env python3
#
# Turn bare issue numbers and file paths in a Markdown doc into clickable links.
# SPDX-License-Identifier: BSD-3-Clause

"""Make a document's references clickable, in place.

## Why this is a script and not a one-off edit

An audit or a plan is mostly references — `#213`, `gateware/soc/clocks.py:44` —
and written as bare text every one of them is a copy-paste job for the reader.
The tracker audit alone had 88 issue references and no links.

Doing it by hand is both tedious and unreliable: the relative depth differs per
document, and a link that resolves from `docs/` does not resolve from
`docs/plans/`. The depth is computable, so compute it.

## What it does NOT touch

- **Fenced code blocks.** A link inside ``` renders as literal text, so
  rewriting there produces noise rather than a link.
- **Anything already a link.** Idempotent by construction, so it can be re-run
  after a document is edited.
- **Paths that do not exist.** A link to a moved file is worse than no link: it
  reads as verified when it is stale. Only real paths are linked, and
  `--report-missing` names the rest, which is itself a useful audit.

    ./scripts/linkify_doc.py docs/plans/issue-and-doc-audit.md
    ./scripts/linkify_doc.py --report-missing docs/plans/*.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

REPO = "https://github.com/awtoau/cynthion-workspace"

# `#123`, but not `#1` (too short to be an issue here) and not inside a word.
ISSUE = re.compile(r"(?<![\w/#])#(\d{2,4})\b")

# A repo-relative path, optionally with `:LINE`. Anchored on a known top-level
# directory so prose like "see the gateware" is not mistaken for a path.
TOPLEVEL = ("gateware", "firmware", "scripts", "docs", "tests", "sources",
            "debris", "linux-on-cynthion")
PATH = re.compile(
    r"`((?:" + "|".join(TOPLEVEL) + r")/[\w./-]+?\.(?:py|md|rs|toml|json|x|v))"
    r"(?::(\d+))?`")


def linkify(text, doc_path, *, missing=None):
    """Rewrite `text`, which lives at `doc_path`, with clickable references."""
    # How far up to reach the repo root from this document.
    depth = len(doc_path.relative_to(ROOT).parts) - 1
    up = "../" * depth

    out, in_fence = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        line = ISSUE.sub(lambda m: f"[#{m.group(1)}]({REPO}/issues/{m.group(1)})",
                         line)

        def path_link(m):
            rel, lineno = m.group(1), m.group(2)
            if not (ROOT / rel).exists():
                # A link to a file that is not there reads as verified when it
                # is stale, which is worse than leaving it plain.
                if missing is not None:
                    missing.add(rel)
                return m.group(0)
            shown = f"{rel}:{lineno}" if lineno else rel
            target = f"{up}{rel}" + (f"#L{lineno}" if lineno else "")
            return f"[`{shown}`]({target})"

        out.append(PATH.sub(path_link, line))
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("docs", nargs="+", type=Path)
    parser.add_argument("--report-missing", action="store_true",
                        help="name every referenced path that does not exist")
    args = parser.parse_args()

    for doc in args.docs:
        doc = doc.resolve()
        missing = set()
        before = doc.read_text()
        after = linkify(before, doc, missing=missing)
        doc.write_text(after)
        added = after.count("](") - before.count("](")
        emit(f"{doc.relative_to(ROOT)}: {added} links added")
        if missing and args.report_missing:
            emit(f"  {len(missing)} referenced paths DO NOT EXIST — left plain:")
            for path in sorted(missing):
                emit(f"    {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
