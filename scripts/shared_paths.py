#!/usr/bin/env python3
#
# One answer to "where does this file live when I am in a worktree". See #365.
# SPDX-License-Identifier: BSD-3-Clause

"""
Resolve a path that a git worktree may not carry, without copying anything.

    from shared_paths import main_checkout, resolve_shared

    zip_ = resolve_shared(ROOT, Path("sources/models/x.zip"),
                          env_var="HYPERRAM_MODEL_ZIP")
    vexii = resolve_shared(ROOT, Path("repos/vexiiriscv"),
                           env_var="VEXII_ROOT", marker="build.sbt")

Returns the first candidate that exists, or None. Order is env override, then
this checkout, then the main checkout behind it.

## Why it is one function

`git worktree add` populates neither submodules nor gitignored trees, so a
worktree has no `repos/**` and no `sources/**`. Three scripts had each grown
their own fallback to the main checkout -- two of them the same twelve lines,
with a comment in one saying it was copied from the other on purpose. Three
copies of a rule is three places for it to drift, and the rule is one line.

`worktree_setup.py` fixes `repos/**` properly (a shared linked worktree per
submodule). This stays for `sources/**`, which is gitignored and so has no pin
to check out, and as the fallback for a checkout nobody has run setup in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Local git metadata read; measured at 20-40 ms. 10 s is ~250x and is a floor
# against a blocked index.lock, not a margin. On expiry: treated as "no main
# checkout" and the caller falls through to its own copy.
GIT_TIMEOUT_S = 10


def main_checkout(root: Path) -> Path | None:
    """The main checkout behind a linked worktree, or None if `root` is it."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    parent = Path(done.stdout.strip()).parent
    return None if parent == Path(root).resolve() else parent


def resolve_shared(root: Path, relative: Path | str, *,
                   env_var: str | None = None,
                   marker: str | None = None) -> Path | None:
    """First existing of: `$env_var`, `root/relative`, `main_checkout/relative`.

    `marker` names a file that must exist INSIDE the candidate, for candidates
    that are directories -- an empty submodule directory exists but is not a
    checkout.
    """
    relative = Path(relative)

    def usable(candidate: Path) -> bool:
        return (candidate / marker).is_file() if marker else candidate.exists()

    candidates: list[Path] = []
    if env_var and os.environ.get(env_var):
        candidates.append(Path(os.environ[env_var]))
    candidates.append(Path(root) / relative)
    main = main_checkout(Path(root))
    if main is not None:
        candidates.append(main / relative)

    for candidate in candidates:
        if usable(candidate):
            return candidate
    return None
