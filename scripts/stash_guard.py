#!/usr/bin/env python3
#
# Claude Code PreToolUse hook: stop one worktree popping another's stash. See #369.
# SPDX-License-Identifier: BSD-3-Clause

"""
`git stash` is one stack shared by every worktree. This denies the commands that
can consume another worktree's entry. Registered in `.claude/settings.json`.

Denied:
- `git stash clear`
- `git stash pop|apply|drop|branch` with no ref (defaults to `stash@{0}`)
- the same with a ref whose message does not say `On <this branch>`

Allowed: `push`, `list`, `show`, and pop/apply/drop of your own branch's entry.
Better still: commit WIP to your own branch.

    python3 scripts/stash_guard.py < hook-json     # exit 2 + stderr = denied
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

GIT_TIMEOUT_S = 5  # local rev-parse/stash list; on expiry the hook errors (non-2), no deny
CONSUMERS = {"pop", "apply", "drop", "branch"}
SEPARATORS = re.compile(r"&&|\|\||[;|\n]")


def _git(args: list[str], cwd: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       timeout=GIT_TIMEOUT_S)
    return r.stdout.strip() if r.returncode == 0 else ""


def stash_entries(cwd: str) -> list[tuple[str, str, str]]:
    """-> [(stash@{n}, sha, subject)] top first."""
    rows = _git(["stash", "list", "--format=%gd %H %gs"], cwd)
    return [tuple(line.split(" ", 2)) for line in rows.splitlines()]  # type: ignore[misc]


def owner_of(subject: str) -> str:
    """'On br: msg' / 'WIP on br: sha msg' -> 'br'."""
    m = re.match(r"(?:WIP )?[Oo]n (.*?): ", subject)
    return m.group(1) if m else ""


def _segments(command: str):
    """Yield (cwd override, stash args) for each `git [-C d] stash ...` segment."""
    for seg in SEPARATORS.split(command):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if "git" not in words:
            continue
        i, cwd = words.index("git") + 1, None
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                cwd = words[i + 1]
                i += 1
            elif words[i] in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                i += 1
            i += 1
        if i < len(words) and words[i] == "stash":
            yield cwd, words[i + 1:]


def decide(command: str, cwd: str) -> str | None:
    """-> reason to deny, or None."""
    for override, args in _segments(command):
        where = str(Path(cwd, override)) if override else cwd
        pos = [a for a in args if not a.startswith("-")]
        sub = pos[0] if pos else "push"
        if sub == "clear":
            return "git stash clear wipes every worktree's stashes (#369)"
        if sub not in CONSUMERS:
            continue
        refs = pos[2:] if sub == "branch" else pos[1:]  # branch: `<name> [<stash>]`
        ref = refs[0] if refs else "stash@{0}"
        explicit = bool(refs)
        if not explicit:  # even a top entry that is yours can change before git runs
            return (f"`git stash {sub}` with no ref takes stash@{{0}}, which may be "
                    f"another worktree's (#369). Name your own entry, or commit WIP.")
        branch = _git(["branch", "--show-current"], where) or "(no branch)"
        for name, sha, subject in stash_entries(where):
            if ref in (name, sha) or (len(ref) >= 7 and sha.startswith(ref)):
                mine = owner_of(subject)
                if mine == branch:
                    return None
                return (f"{ref} belongs to '{mine}', you are on '{branch}' (#369). "
                        f"Commit WIP to your own branch instead of stashing.")
        return f"{ref} is not a stash entry of this repo (#369)"
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    if "stash" not in command:
        return 0
    reason = decide(command, event.get("cwd") or ".")
    if reason:
        print(f"stash_guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
