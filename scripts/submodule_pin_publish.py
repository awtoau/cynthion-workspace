#!/usr/bin/env python3
#
# A pin on no remote is a repo that only builds on one machine. See #373.
# SPDX-License-Identifier: BSD-3-Clause

"""Find submodule pins that exist on no remote, and push the commits that fix it.

    ./scripts/submodule_pin_publish.py                  # report; writes nothing
    ./scripts/submodule_pin_publish.py --json           # the same, machine-readable
    ./scripts/submodule_pin_publish.py --push repos/apollo   # publish that one

Progress and results -> `tmp/logs/dev.log`, like every tool here.

## The defect

`repos/apollo`'s pin `90c8b7b6` is on no remote (#373). `git fetch` of it answers
`upload-pack: not our ref`, `ls-remote` shows nothing at it, and the remote's
`main` is `69c6ba8e`. It is reachable only from this machine's superproject
object store:

- `git clone --recurse-submodules` of this repo FAILS at `repos/apollo`
- `git submodule update --init` cannot repair a checkout that lacks it
- a `git gc` that decided it was unreachable takes the only copy

Worktrees are unaffected because `worktree_setup.py` SHARES that store rather
than fetching, which hides the problem rather than fixing it. `./dev.py
worktree-check` reports it as a standing warning; this is the other half --
what to DO about it.

## Why publishing, and what was rejected

- **Move the pin back to the remote's tip.** Rejected: `90c8b7b6` is a measured
  flash-programming optimisation, 4.71 s -> 3.33 s. Reverting the pin throws it
  away and leaves the commit in exactly one place, so the next `gc` still loses
  it -- and the next local commit recreates the same defect.
- **Vendor the state as a bundle or a patch series in the superproject.**
  Rejected: a second distribution mechanism beside the one git already has, and
  it does not make `git clone --recurse-submodules` work, which is the property
  that is actually broken.
- **Push to a different fork.** Rejected: `awtoau/awto-apollo` IS the fork we
  control and is already the submodule URL. Another remote is another URL to
  keep in step for no gain.

So: push the commits to the fork the submodule already points at. Every pin here
is a fast-forward from its remote's tip, so nothing is rewritten and nothing is
forced.

## The scrub, and why it is not optional

These forks are PUBLIC. Anything pushed is published, so the diff about to go out
is checked for private filesystem paths and credential-shaped strings first, and
a hit REFUSES the push rather than warning about it. It is a check on content
that a human would otherwise have to remember to do, at the one moment where
forgetting cannot be undone.

Publishing is a deliberate act: `--push` names ONE submodule, and the tool prints
what it is about to send and to where before it sends it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

import private_path_check  # noqa: E402
import worktree_setup  # noqa: E402

# What must never be published, checked on ADDED lines only.
#
# The private-path pattern is imported, not restated: `private_path_check.py`
# already decides what a per-machine path is, and two definitions of that would
# disagree the first time one of them is edited.
FORBIDDEN = [
    (re.compile(r"^\+.*(" + private_path_check.PRIVATE.pattern + ")", re.M),
     "a private filesystem path"),
    (re.compile(r"^\+.*(api[_-]?key|secret|password|BEGIN [A-Z ]*PRIVATE KEY)",
                re.M | re.I), "a credential-shaped string"),
]


def git(args, work=None):
    return subprocess.run(["git", *args], cwd=work or ROOT,
                          capture_output=True, text=True)


def out(args, work=None):
    result = git(args, work)
    return result.stdout.strip() if result.returncode == 0 else ""


def unpublished():
    """Every submodule whose pin no remote-tracking ref contains.

    The question is asked LOCALLY -- `rev-list --not --remotes` -- so this needs
    no network and cannot be wrong about a remote that is merely unreachable.
    `worktree_setup` asks it the same way; this reuses that rather than carrying
    a second definition of "on a remote".
    """
    found = []
    for sub in worktree_setup.submodules():
        if not sub.pin or not sub.mod.exists():
            continue
        if worktree_setup.on_any_remote(sub.mod, sub.pin):
            continue
        found.append(sub)
    return found


def plan(sub):
    """What publishing this pin would send: commits, branch, fast-forward or not."""
    mod = ["--git-dir", str(sub.mod)]
    url = sub.url or out([*mod, "remote", "get-url", "origin"])
    # The branch that CONTAINS the pin, which is the one to push. A pin on a
    # detached commit contained by nothing is a different problem and is named
    # rather than guessed at.
    branches = [line.strip().lstrip("* ").strip() for line in
                out([*mod, "branch", "--contains", sub.pin]).splitlines()]
    branches = [b for b in branches if b and not b.startswith("(")]
    branch = "main" if "main" in branches else (branches[0] if branches else "")
    tip = out([*mod, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"])
    ahead = out([*mod, "log", "--oneline", f"origin/{branch}..{branch}"]).splitlines() \
        if tip else []
    fast_forward = bool(tip) and git([*mod, "merge-base", "--is-ancestor",
                                      f"origin/{branch}", branch]).returncode == 0
    return {
        "submodule": sub.label, "url": url, "pin": sub.pin,
        "branch": branch, "remote_tip": tip, "fast_forward": fast_forward,
        "commits": ahead,
    }


def scrub(sub, detail):
    """What in the outgoing diff must not be published. Empty is clean."""
    if not detail["branch"] or not detail["remote_tip"]:
        return ["cannot diff: no remote-tracking branch to compare against"]
    diff = out(["--git-dir", str(sub.mod), "diff",
                f"origin/{detail['branch']}..{detail['branch']}"])
    hits = []
    for pattern, what in FORBIDDEN:
        for line in pattern.findall(diff):
            hits.append(f"{what}: {''.join(line)[:80]}")
    return hits


def report(details):
    for detail in details:
        emit(f"{detail['submodule']}: pin {detail['pin'][:8]} is on no remote")
        emit(f"  url          {detail['url']}")
        emit(f"  branch       {detail['branch'] or '(the pin is on no local branch)'}")
        emit(f"  remote tip   {detail['remote_tip'][:8] or '(none)'}")
        emit(f"  fast-forward {'yes' if detail['fast_forward'] else 'NO -- refuses'}")
        for line in detail["commits"]:
            emit(f"    {line}")
        emit(f"  publish with: ./scripts/submodule_pin_publish.py "
             f"--push {detail['submodule']}")
    if not details:
        emit("every submodule pin is reachable from a remote: a fresh clone builds")


def publish(sub, detail):
    """Push the branch that carries the pin. Fast-forward only, scrubbed first."""
    if not detail["branch"]:
        log(f"{sub.label}: the pin is on no local branch; nothing to push",
            "ERROR")
        return 1
    if not detail["fast_forward"]:
        log(f"{sub.label}: {detail['branch']} is not a fast-forward of "
            f"origin/{detail['branch']} -- refusing. Publishing must not rewrite "
            f"anyone else's history; rebase or merge first.", "ERROR")
        return 1

    dirty = scrub(sub, detail)
    if dirty:
        log(f"{sub.label}: REFUSING to publish -- the outgoing diff contains:",
            "ERROR")
        for line in dirty:
            log(f"    {line}", "ERROR")
        log("These forks are public. Fix the content, not this check.", "ERROR")
        return 1

    emit(f"pushing {len(detail['commits'])} commit(s) to {detail['url']} "
         f"{detail['branch']}:")
    for line in detail["commits"]:
        emit(f"    {line}")
    result = git(["--git-dir", str(sub.mod), "push", "origin",
                  f"{detail['branch']}:{detail['branch']}"])
    if result.returncode != 0:
        log(f"push failed: {(result.stderr or result.stdout).strip()[-300:]}",
            "ERROR")
        return 1
    emit((result.stderr or result.stdout).strip())
    # The claim this whole script exists to make, verified against the remote
    # rather than assumed from a zero exit -- the same mistake as #360.
    git(["--git-dir", str(sub.mod), "fetch", "origin", "--quiet"])
    if worktree_setup.on_any_remote(sub.mod, detail["pin"]):
        log(f"{sub.label}: pin {detail['pin'][:8]} is now on a remote; a fresh "
            f"clone can build", "ALERT")
        return 0
    log(f"{sub.label}: the push returned 0 but the pin is STILL on no remote",
        "ERROR")
    return 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", metavar="PATH",
                        help="publish this submodule's branch (e.g. repos/apollo)")
    parser.add_argument("--json", action="store_true",
                        help="print the plan as one object; writes nothing")
    args = parser.parse_args()

    subs = unpublished()
    details = [plan(sub) for sub in subs]

    if args.json:
        print(json.dumps(details, indent=2))
        return 0 if not details else 1

    report(details)

    if not args.push:
        # Reporting only. Non-zero when there is something to publish, so a gate
        # can use it -- an unpublishable pin is a defect, not a note.
        return 1 if details else 0

    wanted = args.push.rstrip("/")
    for sub, detail in zip(subs, details):
        if sub.label == wanted:
            return publish(sub, detail)
    log(f"{wanted} is not a submodule with an unpublished pin. "
        f"Candidates: {[d['submodule'] for d in details] or 'none'}", "ERROR")
    return 1


if __name__ == "__main__":
    sys.exit(main())
