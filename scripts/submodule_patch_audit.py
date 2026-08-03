#!/usr/bin/env python3
#
# Refuse to remove a submodule that still holds work nobody else has.
# See awtoau/cynthion-workspace#169.
# SPDX-License-Identifier: BSD-3-Clause

"""
Fails if any submodule holds a commit, a stash or an edit that is not on a remote.

    python3 scripts/submodule_patch_audit.py
    python3 scripts/submodule_patch_audit.py --fetch    # refresh remotes first
    python3 scripts/submodule_patch_audit.py -v         # list every branch

Exit status 0 only if every submodule could be deleted today without losing
anything. Output goes to the terminal and to
`tmp/logs/submodule_patch_audit.log`.

## Why this exists

#169 ends with the submodules under `repos/` being removed. `git rm` of a
submodule deletes its working tree, and a submodule is the easiest place in a
repository to leave work that exists in exactly one place:

- Submodules sit at a **detached HEAD** by default, so a commit made in one is on
  no branch at all. It survives only in the reflog of a directory that is about
  to be deleted.
- `git status` in the superproject shows a dirty submodule as one modified line.
  Nine local branches and a stash look identical to a whitespace change.
- The forks are `awtoau/awto-*`. Work pushed to the *upstream* remote, or to no
  remote, is not covered by "I pushed it".

`repos/apollo` currently carries nine local branches and a stash, which is what
prompted this. Losing one is silent and permanent, so the check runs before the
removal rather than after.

## What "safe" means here

A commit is safe if it is reachable from a **remote-tracking ref** -- any
`refs/remotes/*`, not just `origin/main`. That is the closest thing git can
answer locally to "somebody else has this".

Remote-tracking refs go stale without a fetch, so this can be wrong in one
direction: a branch pushed from another machine looks unpushed here. It cannot be
wrong in the direction that matters -- it will never call something safe that is
not -- because a stale ref can only omit commits, never invent them. Pass
`--fetch` to refresh first, which needs network.

Stashes are always reported as unsafe. A stash commit is unreachable from any
ref by construction, so no amount of pushing makes one safe; it has to be
applied or dropped deliberately.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "submodule_patch_audit.log"


def git(args, cwd, check=False):
    """Run git, returning stdout. Never raises for an expected empty result."""
    done = subprocess.run(["git", *args], cwd=cwd,
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def submodules():
    """(prefix, sha, path) per submodule, from the superproject's own view.

    The prefix is git's: '-' not initialised, '+' the checkout does not match
    the commit the superproject records, ' ' in step. The '+' case matters here
    -- it means a fresh clone gets different code than this machine has.
    """
    out = git(["submodule", "status"], ROOT)
    for line in out.splitlines():
        if not line.strip():
            continue
        prefix, rest = line[0], line[1:]
        sha, path = rest.split()[0], rest.split()[1]
        yield prefix, sha, path


def unreachable(sha, cwd):
    """True if `sha` is on no remote-tracking ref."""
    count = git(["rev-list", "--count", sha, "--not", "--remotes"], cwd)
    return count not in ("", "0")


def audit(path, prefix, gitlink, verbose, emit):
    """Report one submodule. Returns a list of findings; empty means safe."""
    work = ROOT / path
    findings = []

    if prefix == "-" or not (work / ".git").exists():
        emit(f"  {path:<26} not initialised -- nothing on disk to lose")
        return findings

    # An edit nobody committed. Split from untracked because the fixes differ:
    # one is `git commit`, the other is often `rm`.
    status = git(["status", "--porcelain"], work).splitlines()
    modified = [s for s in status if not s.startswith("??")]
    untracked = [s for s in status if s.startswith("??")]
    if modified:
        findings.append(f"{len(modified)} uncommitted change(s)")
    if untracked:
        findings.append(f"{len(untracked)} untracked file(s)")

    # The superproject records a commit a fresh clone must be able to fetch. If
    # the checkout has drifted from it, both SHAs have to be accounted for.
    head = git(["rev-parse", "HEAD"], work)
    if prefix == "+":
        findings.append(f"checkout {head[:8]} != recorded {gitlink[:8]}")
    if unreachable(gitlink, work):
        findings.append(f"recorded commit {gitlink[:8]} is on no remote")

    # Every local branch, not just HEAD. This is the one that catches a detached
    # experiment somebody branched and walked away from.
    for branch in git(["branch", "--format=%(refname:short)"], work).splitlines():
        branch = branch.strip()
        if not branch or branch.startswith("("):
            continue                    # "(HEAD detached at ...)" is not a ref
        ahead = git(["rev-list", "--count", branch, "--not", "--remotes"], work)
        if ahead not in ("", "0"):
            findings.append(f"branch {branch}: {ahead} commit(s) on no remote")
        elif verbose:
            emit(f"      {branch} -- pushed")

    # A stash is unreachable from any ref by construction, so it can never be
    # made safe by pushing. Always a finding.
    for entry in git(["stash", "list"], work).splitlines():
        findings.append(f"stash: {entry.strip()}")

    if findings:
        emit(f"  {path:<26} WOULD LOSE WORK")
        for finding in findings:
            emit(f"      {finding}")
    else:
        emit(f"  {path:<26} safe -- every commit is on a remote")

    return findings


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true",
                        help="refresh remote-tracking refs first (needs network)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every branch, including the pushed ones")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        entries = list(submodules())

        # A check that inspects nothing must say so rather than pass. Once the
        # submodules are gone this is the expected state, but it has to be
        # visible, not a silent green.
        if not entries:
            emit("no submodules -- nothing to audit.")
            emit("If that is a surprise, .gitmodules is empty or this is not "
                 "the superproject.")
            return 0

        if args.fetch:
            emit(f"fetching {len(entries)} submodule remote(s)...")
            for _, _, path in entries:
                out = git(["fetch", "--all", "--quiet"], ROOT / path)
                if out:
                    emit(f"  {path}: {out}")
            emit()

        emit(f"{len(entries)} submodule(s), against every remote-tracking ref")
        emit()

        unsafe = {}
        for prefix, gitlink, path in entries:
            findings = audit(path, prefix, gitlink, args.verbose, emit)
            if findings:
                unsafe[path] = findings

        emit()
        if unsafe:
            total = sum(len(f) for f in unsafe.values())
            emit(f"{len(unsafe)} submodule(s) hold {total} piece(s) of work "
                 f"that exist only here.")
            emit("Push the branch, apply or drop the stash, commit or discard "
                 "the edit -- then remove the submodule.")
        else:
            emit("Every submodule is reproducible from its remotes. "
                 "Safe to remove.")

        if not args.fetch:
            emit()
            emit("Remote-tracking refs were not refreshed; re-run with --fetch "
                 "to rule out a stale ref.")

        emit()
        emit(f"log: {LOG}")
        return 1 if unsafe else 0


if __name__ == "__main__":
    sys.exit(main())
