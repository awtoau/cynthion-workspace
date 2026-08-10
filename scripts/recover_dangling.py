#!/usr/bin/env python3
#
# Rescue unreachable commits into branches before gc takes them. #379.
# SPDX-License-Identifier: BSD-3-Clause

"""Find dangling commits, and put a branch on every one worth keeping.

    ./scripts/recover_dangling.py                  # report, change nothing
    ./scripts/recover_dangling.py --since 2026-08-10
    ./scripts/recover_dangling.py --rescue         # create the branches
    ./scripts/recover_dangling.py --rescue --push  # ...and push them

## Why

A dangling commit is one no branch, tag or reflog entry reaches. `git gc` deletes
them, and nothing warns first. They arrive from dropped stashes, resets, amends,
and -- the case this was written for -- an agent worktree that was deleted while
its branch still held commits nobody had merged.

Found 2026-08-10: 131 dangling commits, of which one was a full-stack DQS
simulation against the vendor model that existed nowhere else (#186).

## What it does NOT decide

Whether a commit is worth keeping. It rescues everything in the window and
prints what each one is, because the alternative -- guessing -- is how the work
was lost in the first place. Triage after the branches exist, not before.

Branches are `rescued/<date>-<short>`; `git branch -D` them once merged or judged.
Log: `tmp/logs/recover-dangling.log`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "recover-dangling.log"


def emit(line=""):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def git(*args, check=True):
    done = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def dangling():
    """Every unreachable commit, newest first, with its date and subject."""
    # --no-reflogs, because a commit the reflog still reaches is not yet at risk
    # -- but the reflog expires, so those are worth seeing too. This reports the
    # ones already past that point.
    out = git("fsck", "--no-reflogs", check=False)
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "commit":
            sha = parts[2]
            info = git("log", "-1", "--format=%ad%x00%s%x00%an",
                       "--date=format:%Y-%m-%d %H:%M", sha, check=False)
            if info:
                date, subject, author = info.split("\0")
                found.append((date, sha, subject, author))
    return sorted(found, reverse=True)


def on_main(sha):
    """Is this commit's content already on main, by subject?

    By SUBJECT, not by patch-id: a rescued commit is usually an earlier draft of
    something that landed, so the patch differs while the work does not. A subject
    match is a hint to look, never a reason to skip the rescue.
    """
    subject = git("log", "-1", "--format=%s", sha, check=False)
    if not subject:
        return False
    return bool(git("log", "--oneline", "--fixed-strings", f"--grep={subject}",
                    "main", check=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None,
                        help="only commits dated on or after this (YYYY-MM-DD)")
    parser.add_argument("--rescue", action="store_true",
                        help="create a branch on each; without this, report only")
    parser.add_argument("--push", action="store_true",
                        help="push the rescued branches to origin")
    args = parser.parse_args()

    found = dangling()
    if args.since:
        found = [row for row in found if row[0][:10] >= args.since]

    emit(f"{len(found)} dangling commit(s)"
         + (f" dated {args.since} or later" if args.since else ""))

    rescued = []
    for date, sha, subject, author in found:
        # A stash's own commit says "WIP on" or "On <branch>:"; its content is
        # real but it was explicitly set aside, so it is flagged rather than
        # treated the same as a commit someone made deliberately.
        kind = "stash " if subject.startswith(("WIP on ", "On ")) else "commit"
        seen = " [subject also on main]" if on_main(sha) else ""
        emit(f"  {date}  {kind}  {sha[:12]}  {subject[:72]}{seen}")

        if args.rescue:
            name = f"rescued/{date[:10]}-{sha[:8]}"
            git("branch", "-f", name, sha)
            rescued.append(name)

    if not args.rescue:
        emit("\nreport only -- pass --rescue to put a branch on each")
        return 0

    emit(f"\n{len(rescued)} branch(es) created")
    if args.push and rescued:
        # One push, so a partial failure does not leave half of them local-only.
        git("push", "origin", *rescued)
        emit("pushed to origin -- they now survive this machine")
    elif rescued:
        emit("NOT pushed. A rescued branch that exists only here is one gc away "
             "from being lost again -- pass --push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
