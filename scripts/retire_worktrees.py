#!/usr/bin/env python3
#
# Retire spent agent worktrees to the wastebasket, never by deleting. #375.
# SPDX-License-Identifier: BSD-3-Clause

"""Move worktrees whose work is safely on `main` and on `origin`.

    ./scripts/retire_worktrees.py                 # report, move nothing
    ./scripts/retire_worktrees.py --move          # move the eligible ones
    ./scripts/retire_worktrees.py --move --prune  # ...and prune the registrations

## Why the wastebasket and not `rm`

A worktree is regenerable ONLY if its branch is merged and pushed. If either is
false, the directory is the last copy. 131 commits were found unreachable on
2026-08-10 because worktrees were removed while their branches still held
unmerged work, so the check matters more than the tidying.

Recoverable for a while, emptied by the user when confident. The wastebasket is
machine-wide and project-neutral -- cross-project junk must never be routed
through one project's data directory.

## Three conditions, all required

1. the branch is an ancestor of `main` -- its work is IN main, not merely similar
2. the branch exists on `origin` -- so the history survives this machine
3. no uncommitted changes to TRACKED files

Untracked files are reported but do not block: `sources/**` is gitignored by
design and every worktree that ran a simulation has a copy.

Nothing is deleted, ever. The move is the whole action, and `git worktree prune`
afterwards only clears the registration of a directory that is already gone.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "retire-worktrees.log"
WASTEBASKET = Path("/mnt/2tb/wastebasket")


def emit(line=""):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def git(*args, cwd=ROOT, check=True):
    done = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def worktrees():
    """Every worktree except the main checkout, with its branch."""
    found, path = [], None
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line.split(" ", 1)[1])
        elif line.startswith("branch ") and path is not None:
            branch = line.split(" ", 1)[1].replace("refs/heads/", "")
            if path != ROOT:
                found.append((path, branch))
            path = None
    return found


def remote_heads():
    """Every branch on origin, in ONE round trip.

    Asked per branch this was 43 network calls and outran a two-minute bound --
    the moves themselves are renames on one filesystem and cost nothing.
    """
    out = git("ls-remote", "--heads", "origin", check=False)
    return {line.split("refs/heads/", 1)[1]
            for line in out.splitlines() if "refs/heads/" in line}


ORIGIN_HEADS = None


def on_origin(branch):
    global ORIGIN_HEADS
    if ORIGIN_HEADS is None:
        ORIGIN_HEADS = remote_heads()
    return branch in ORIGIN_HEADS


def judge(path, branch):
    """(eligible, reason). The reason is printed either way, so a KEEP says why."""
    if not path.exists():
        return False, "directory already gone -- prune will clear it"
    merged = subprocess.run(("git", "merge-base", "--is-ancestor", branch, "main"),
                            cwd=ROOT, capture_output=True).returncode == 0
    if not merged:
        ahead = git("log", "--oneline", f"main..{branch}", check=False)
        return False, f"NOT MERGED -- {len(ahead.splitlines())} commit(s) not on main"
    if not on_origin(branch):
        return False, "NOT ON ORIGIN -- moving it would leave one copy"
    tracked = git("status", "--porcelain", "--untracked-files=no",
                  cwd=path, check=False)
    if tracked:
        return False, f"{len(tracked.splitlines())} uncommitted change(s) to tracked files"
    untracked = git("status", "--porcelain", cwd=path, check=False)
    note = f" ({len(untracked.splitlines())} untracked, ignored)" if untracked else ""
    return True, f"merged and pushed{note}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--move", action="store_true",
                        help="move the eligible ones; without this, report only")
    parser.add_argument("--prune", action="store_true",
                        help="git worktree prune afterwards, clearing stale registrations")
    parser.add_argument("--stamp", default=None,
                        help="wastebasket slot suffix (default: now, YYYYmmdd-HHMMSS)")
    args = parser.parse_args()

    found = worktrees()
    eligible, kept = [], []
    for path, branch in found:
        ok, reason = judge(path, branch)
        (eligible if ok else kept).append((path, branch, reason))

    emit(f"{len(found)} worktree(s) besides the main checkout")
    emit(f"\nKEEPING {len(kept)}:")
    for path, branch, reason in kept:
        emit(f"  {branch:44s} {reason}")
    emit(f"\nELIGIBLE {len(eligible)}: merged into main AND on origin, no tracked changes")

    if not args.move:
        emit("\nreport only -- pass --move to retire the eligible ones")
        return 0

    stamp = args.stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    slot = WASTEBASKET / f"cynthion-worktrees-{stamp}"
    slot.mkdir(parents=True, exist_ok=True)
    emit(f"\nmoving to {slot}")

    for path, branch, _reason in eligible:
        shutil.move(str(path), str(slot / path.name))
        emit(f"  moved {path.name}")
    emit(f"{len(eligible)} moved. Nothing deleted -- empty the wastebasket yourself "
         "once you are satisfied.")

    if args.prune:
        git("worktree", "prune")
        emit("registrations pruned")
    else:
        emit("registrations NOT pruned; pass --prune, or run `git worktree prune`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
