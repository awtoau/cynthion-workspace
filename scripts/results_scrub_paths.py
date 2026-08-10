#!/usr/bin/env python3
#
# Rewrite absolute paths in tracked results/*.json to repo-relative.
# SPDX-License-Identifier: BSD-3-Clause

"""
`results/**` is tracked and this repo is public, so an absolute path in a
recorded artifact names one account, one disk and one agent worktree --
`scripts/private_path_check.py` fails on exactly that.

    ./scripts/results_scrub_paths.py            # report what would change
    ./scripts/results_scrub_paths.py --write    # rewrite them

Only the leading checkout prefix is removed, so `.../tmp/awto_soc/build/x.bit`
becomes `tmp/awto_soc/build/x.bit`. A path under some other checkout (an agent
worktree under `.claude/worktrees/<id>/`) loses that prefix too: the sha256
beside it is the identity, and the directory it was read from is not
reproducible anywhere else anyway.

The producer is fixed in `scripts/hyperram_matrix_diff.py:pin_provenance`;
this is for the records already committed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# Any checkout of this workspace, with or without an agent worktree under it.
# Anchored on the repo name so a path naming some unrelated tree is left alone
# and reported rather than silently truncated.
CHECKOUT = re.compile(
    r"/[^\s\"]*?cynthion-workspace(?:/\.claude/worktrees/[^/\"]+)?/")


def scrub(value: str) -> str:
    return CHECKOUT.sub("", value)


def walk(node):
    """Every string in a JSON tree, rewritten. Returns (node, changes)."""
    if isinstance(node, dict):
        changes = 0
        for key, value in node.items():
            node[key], delta = walk(value)
            changes += delta
        return node, changes
    if isinstance(node, list):
        changes = 0
        for index, value in enumerate(node):
            node[index], delta = walk(value)
            changes += delta
        return node, changes
    if isinstance(node, str):
        scrubbed = scrub(node)
        return scrubbed, int(scrubbed != node)
    return node, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    touched = 0
    for path in sorted((ROOT / "results").rglob("*.json")):
        original = path.read_text()
        data, changes = walk(json.loads(original))
        if not changes:
            continue
        touched += 1
        emit(f"{path.relative_to(ROOT)}: {changes} absolute path(s)")
        if args.write:
            path.write_text(json.dumps(data, indent=2) + "\n")
    emit(f"{touched} file(s) {'rewritten' if args.write else 'would change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
