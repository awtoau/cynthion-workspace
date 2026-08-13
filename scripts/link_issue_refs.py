#!/usr/bin/env python3
"""Turn bare `#123` issue references in Markdown into links.

Every issue reference must be clickable, in docs as well as in chat. GitHub
auto-links `#123` in its own web UI; nothing else does, so a doc read in an
editor or a rendered site leaves the reader to go and search for it.

Skips what must not be rewritten: fenced and inline code, headings, anchors in
URLs, `#L42` line references, and refs that are already links.

    ./scripts/link_issue_refs.py            # rewrite docs/**.md
    ./scripts/link_issue_refs.py --check    # exit 1 if any bare ref remains
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "awtoau/cynthion-workspace"
URL = f"https://github.com/{REPO}/issues"

# A bare ref: `#` + 2-4 digits. `[` and `](` catch an existing link, `/` and a
# word character catch a URL fragment, and `\b` catches `#L42`. A plain `(` must
# NOT be excluded or `(#509)` is missed -- only `](` means a link target.
BARE = re.compile(r"(?<![\w\[/])(?<!\]\()#(\d{2,4})\b")

# Fenced blocks, then inline code. Order matters: a fence may contain backticks.
FENCE = re.compile(r"^(?:\s*)(?:```|~~~)")


def _mask_inline_code(line: str) -> list[tuple[int, int]]:
    """Spans of `...` in one line, so a ref inside them is left alone."""
    return [m.span() for m in re.finditer(r"`[^`]*`", line)]


def rewrite(text: str) -> tuple[str, int]:
    out: list[str] = []
    in_fence = False
    changed = 0

    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        # A heading's `#` is at the line start and followed by a space, so BARE
        # already misses it -- but an indented code block is four spaces and has
        # no fence to toggle, so it is skipped explicitly.
        if in_fence or line.startswith("    ") or line.startswith("\t"):
            out.append(line)
            continue

        spans = _mask_inline_code(line)

        def repl(m: re.Match[str]) -> str:
            nonlocal changed
            if any(a <= m.start() < b for a, b in spans):
                return m.group(0)
            changed += 1
            return f"[#{m.group(1)}]({URL}/{m.group(1)})"

        out.append(BARE.sub(repl, line))

    return "".join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report bare refs and exit 1, writing nothing")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="files or directories; default docs/")
    args = ap.parse_args()

    targets: list[Path] = []
    for p in (args.paths or [ROOT / "docs"]):
        p = p if p.is_absolute() else ROOT / p
        targets.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])

    total = 0
    touched: list[tuple[Path, int]] = []
    for path in targets:
        text = path.read_text()
        new, n = rewrite(text)
        if not n:
            continue
        total += n
        touched.append((path, n))
        if not args.check:
            path.write_text(new)

    verb = "bare" if args.check else "linked"
    for path, n in touched:
        print(f"  {n:>4}  {path.relative_to(ROOT)}")
    print(f"{total} {verb} issue reference(s) across {len(touched)} file(s)")

    if args.check and total:
        print("\nRun ./scripts/link_issue_refs.py to fix.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
