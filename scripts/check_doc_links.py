#!/usr/bin/env python3
#
# Verify that every relative Markdown link in the workspace docs resolves.
# SPDX-License-Identifier: BSD-3-Clause

"""
Walks the tracked Markdown files and checks each relative link target exists.

Written because the hardware documentation is now a hub-and-spoke set -- one index
plus a note per chip -- and the whole point of that shape is that following a link
beats re-deriving a fact. A dead link sends the reader back to the pin dump, which
is exactly the failure the structure exists to prevent.

Skips `repos/` (submodules, not ours) and `debris/` (archived by definition, and its
links point at things that were deliberately retired). Anchors are checked only for
existence of the file, not the heading.

    ./scripts/check_doc_links.py            # whole workspace
    ./scripts/check_doc_links.py docs       # one subtree
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from shared_paths import resolve_shared  # noqa: E402

SKIP_PREFIXES = ("repos/", "debris/", "tmp/")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


# Only `sources/` falls back to the main checkout. Widening it to any path would
# let a doc deleted on this branch pass because main still has it.
SHARED_PREFIX = "sources"


def resolves(candidate: Path) -> bool:
    """Exists here, or -- under `sources/` only -- in the main checkout.

    `sources/**` is gitignored, so every datasheet link was broken in every
    worktree and this whole check was red there: 20+ failures, none real (#365).
    """
    if candidate.exists():
        return True
    try:
        relative = candidate.resolve().relative_to(ROOT)
    except ValueError:
        return False
    if relative.parts[:1] != (SHARED_PREFIX,):
        return False
    return resolve_shared(ROOT, relative) is not None


def tracked_markdown(subtree):
    """Ask git for the Markdown files, so untracked scratch is never scanned."""
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith(SKIP_PREFIXES):
            continue
        if subtree and not line.startswith(subtree):
            continue
        yield line


def main():
    subtree = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else None

    broken = []
    checked = 0

    for rel in tracked_markdown(subtree):
        path = ROOT / rel
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            # External links and in-page anchors are not ours to check.
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # Strip the anchor: the file must exist, the heading is not verified.
            target = target.split("#", 1)[0]
            if not target:
                continue
            checked += 1
            if not resolves(path.parent / target):
                broken.append((rel, target))

    emit(f"checked {checked} relative links")
    if broken:
        emit(f"{len(broken)} broken:")
        for source, target in broken:
            emit(f"  {source} -> {target}")
        return 1
    emit("all resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
