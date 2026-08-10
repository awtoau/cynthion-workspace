#!/usr/bin/env python3
#
# Make a git worktree buildable, and say whether one is. See #365.
# SPDX-License-Identifier: BSD-3-Clause

"""
`git worktree add` leaves this repo unbuildable. This fixes it and checks it.

    python3 scripts/worktree_setup.py check     # is THIS checkout buildable?
    python3 scripts/worktree_setup.py setup     # make it buildable
    python3 scripts/worktree_setup.py setup --prune   # also drop stale registrations

Both are reachable as `./dev.py worktree-check` and `./dev.py worktree-setup`.
`check` exits non-zero on anything a build would trip over, and names the fix.

## What breaks in a worktree

- `git worktree add` does NOT populate submodules, and four of them are needed.
  `repos/vexiiriscv` empty means the CPU cannot be regenerated at all.
- `repos/apollo`'s recorded pin is a commit that exists in NO remote. It is in
  the superproject's own `modules/repos/apollo` object store and nowhere else,
  so `git submodule update` cannot repair a worktree and cannot ever fix a fresh
  clone. This names that rather than surfacing git's "not our ref".
- `sources/**` is gitignored, so a worktree carries no vendor models.

## How it is fixed: share, never copy

Each submodule is materialised as a LINKED GIT WORKTREE of the superproject's
own `.git/modules/repos/<name>` -- the same object store the main checkout uses.
Only the checked-out files cost disk; the history (395 MB across the four) is
shared. Copying the trees instead, which is what has been improvised so far,
costs that per worktree and has already come one commit away from landing a
225 MB directory in git.

A linked worktree also gives the submodule its OWN HEAD and index, so two
worktrees can sit on different pins without fighting -- which a shared
`modules/` checkout cannot do.

## What it deliberately does not do

No network, unless a pin is genuinely absent from the local object store. The
pins are already there for every submodule this repo has; fetching would be
slower and, for `apollo`, impossible.
"""

from __future__ import annotations

import argparse
import configparser
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402
from shared_paths import main_checkout, resolve_shared  # noqa: E402

# One vendor file that is gitignored and that a simulation needs. Presence of
# this stands in for `sources/**` as a whole -- it is the only entry any script
# treats as required. `scripts/hyperram_vendor_model_sim.py` is the consumer.
VENDOR_MODEL = Path("sources/models/W956X8MBY_verilog_p.zip")

# Every git call here is local metadata against an existing object store, so the
# bound is process startup, not I/O: measured 20-40 ms per call on this machine.
# 30 s is ~750x that and is a floor, not a margin -- it exists so a git that
# blocks on an index.lock another agent holds is killed and named rather than
# hanging a check. On expiry the command, its limit and elapsed are logged.
GIT_TIMEOUT_S = 30

# A checkout of a submodule takes as long as writing its files. `vexiiriscv` is
# the largest at ~10 s cold on this machine; 120 s is ~12x that. Same expiry
# behaviour as above.
CHECKOUT_TIMEOUT_S = 120


def git(args: list[str], cwd: Path | None = None, *,
        timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """Run git and return the result. Never raises on a non-zero status."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT git {' '.join(args)}: limit {timeout:.0f}s", "ERROR")
        return subprocess.CompletedProcess(args, 124, "", f"timed out after {timeout:.0f}s")


def out(args: list[str], cwd: Path | None = None) -> str:
    return git(args, cwd).stdout.strip()


# ---------------------------------------------------------------------------
# What this checkout is
# ---------------------------------------------------------------------------

def common_dir() -> Path:
    """The shared `.git` -- the same path from the main checkout or any worktree."""
    return Path(out(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                    ROOT))


def is_linked_worktree() -> bool:
    """True in a `git worktree add` checkout, false in the main one."""
    return (ROOT / ".git").is_file()


# ---------------------------------------------------------------------------
# Submodules
# ---------------------------------------------------------------------------

class Sub:
    """One submodule, at any depth. `label` is its path from the repo root."""

    def __init__(self, label: str, work: Path, mod: Path, url: str, pin: str,
                 parent: Path, rel: str):
        self.label, self.work, self.mod, self.url, self.pin = \
            label, work, mod, url, pin
        self.parent, self.rel = parent, rel      # for `submodule update --init`


def _parse_gitmodules(text: str) -> list[tuple[str, str]]:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return [(parser[s]["path"], parser[s].get("url", ""))
            for s in parser.sections() if s.startswith("submodule ")]


def _gitmodules_of(work: Path, mod: Path, pin: str) -> str:
    """`.gitmodules` from the checkout if there is one, else from the pinned tree.

    Reading it from the object store is what lets `check` see nested submodules
    of a submodule that is not checked out yet -- otherwise an empty
    `repos/vexiiriscv` hides that its build needs `ext/SpinalHDL`.
    """
    if (work / ".gitmodules").is_file():
        return (work / ".gitmodules").read_text()
    if mod.exists() and pin:
        r = git(["--git-dir", str(mod), "show", f"{pin}:.gitmodules"])
        if r.returncode == 0:
            return r.stdout
    return ""


def _gitlink(work: Path, mod: Path, pin: str, path: str) -> str:
    """The commit a submodule is pinned to, from the index or the pinned tree."""
    if (work / ".git").exists():
        line = out(["ls-files", "-s", "--", path], work)
        if line:
            return line.split()[1]
    if mod.exists() and pin:
        line = out(["--git-dir", str(mod), "ls-tree", pin, "--", path])
        if line:
            return line.split()[2]
    return ""


def submodules(work: Path | None = None, mod_root: Path | None = None,
               prefix: str = "", pin: str = "") -> list[Sub]:
    """Every submodule, recursively, in `.gitmodules` order.

    Recursion is not optional: VexiiRiscv's `build.sbt` takes `ext/SpinalHDL` as
    a `ProjectRef`, so a `repos/vexiiriscv` without its own submodules cannot
    generate the CPU either.
    """
    work = ROOT if work is None else work
    mod_root = common_dir() if mod_root is None else mod_root
    found: list[Sub] = []
    for path, url in _parse_gitmodules(_gitmodules_of(work, mod_root, pin)):
        child = Sub(label=prefix + path, work=work / path,
                    mod=mod_root / "modules" / path, url=url,
                    pin=_gitlink(work, mod_root, pin, path),
                    parent=work, rel=path)
        found.append(child)
        found += submodules(child.work, child.mod, child.label + "/", child.pin)
    return found


def head_of(work: Path) -> str:
    return out(["rev-parse", "HEAD"], work) if (work / ".git").exists() else ""


def has_commit(mod: Path, sha: str) -> bool:
    return git(["--git-dir", str(mod), "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def on_any_remote(mod: Path, sha: str) -> bool:
    """True if a remote-tracking ref contains `sha` -- i.e. a fresh clone can get it."""
    r = git(["--git-dir", str(mod), "rev-list", "--count", "-1", sha,
             "--not", "--remotes"])
    return r.returncode == 0 and r.stdout.strip() == "0"


def unfetchable_report(name: str, url: str, mod: Path, pin: str) -> list[str]:
    """Name the cause when a pin exists nowhere a clone can reach.

    This is `repos/apollo` today: the pin is a local commit that was never
    pushed, so `git submodule update` fails with 'not our ref' and says nothing
    about why.
    """
    tip = out(["--git-dir", str(mod), "rev-parse", "--verify", "--quiet",
               "refs/remotes/origin/HEAD"]) or out(
        ["--git-dir", str(mod), "rev-parse", "--verify", "--quiet",
         "refs/remotes/origin/main"])
    return [
        f"{name}: pin {pin[:8]} is on NO remote of {url or 'its origin'}.",
        f"    origin/main is {tip[:8] or 'unknown'} -- the pin is a local-only commit.",
        "    `git submodule update` CANNOT repair this, and a fresh CLONE of this",
        "    repo cannot build. The only copy is the superproject's own object",
        f"    store ({mod.name}), which this setup shares rather than fetches.",
        "    Push the commit to fix it for everyone: awtoau/cynthion-workspace#365.",
    ]


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def check(verbose: bool = False) -> int:
    failures: list[str] = []
    warnings: list[str] = []

    kind = "linked worktree" if is_linked_worktree() else "main checkout"
    emit(f"checkout   : {ROOT.name}  ({kind})")
    emit(f"shared .git: {common_dir()}")
    emit("")

    subs = submodules()
    width = max([len(s.label) for s in subs] + [len(str(VENDOR_MODEL.parent))])
    for sub in subs:
        head = head_of(sub.work)

        if not sub.pin:
            failures.append(f"{sub.label}: no gitlink recorded -- .gitmodules and "
                            f"the tree disagree")
            emit(f"  {sub.label:<{width}}  FAIL  not a gitlink")
            continue

        if not head:
            failures.append(f"{sub.label}: empty -- run ./dev.py worktree-setup")
            emit(f"  {sub.label:<{width}}  FAIL  empty, pin {sub.pin[:8]} not "
                 f"checked out")
            continue

        if head != sub.pin:
            failures.append(f"{sub.label}: checked out {head[:8]}, pin is "
                            f"{sub.pin[:8]} -- run ./dev.py worktree-setup")
            emit(f"  {sub.label:<{width}}  FAIL  HEAD {head[:8]} != pin "
                 f"{sub.pin[:8]}")
            continue

        # Sharing rather than copying is the property that makes 30 worktrees
        # affordable, so it is checked rather than assumed.
        marker = sub.work / ".git"
        shared = marker.is_file() and str(sub.mod) in marker.read_text()
        if not shared and is_linked_worktree():
            warnings.append(f"{sub.label}: not sharing {sub.mod} -- this checkout "
                            f"has its own copy of the objects")
        emit(f"  {sub.label:<{width}}  ok    {sub.pin[:8]} "
             f"({'shared' if shared else 'OWN COPY'})")

        if sub.mod.exists() and not on_any_remote(sub.mod, sub.pin):
            warnings.append(f"{sub.label}: pin {sub.pin[:8]} is on no remote -- "
                            f"fine here, fatal for a fresh clone (#365)")

    model = resolve_shared(ROOT, VENDOR_MODEL)
    label = str(VENDOR_MODEL.parent)
    if model is None:
        failures.append(f"{VENDOR_MODEL} found in neither this checkout nor the "
                        f"main one -- the vendor-model simulations cannot run")
        emit(f"  {label:<{width}}  FAIL  {VENDOR_MODEL.name} not found")
    else:
        where = "here" if model.is_relative_to(ROOT) else "main checkout"
        emit(f"  {label:<{width}}  ok    {model.name} ({where})")

    emit("")
    for line in warnings:
        log(line, "WARN")
    if failures:
        for line in failures:
            log(line, "ERROR")
        log(f"NOT BUILDABLE - {len(failures)} problem(s). "
            f"Run: ./dev.py worktree-setup", "FATAL")
        return 1
    log("buildable", "ALERT")
    return 0


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def ensure_pin(name: str, url: str, mod: Path, pin: str) -> list[str] | None:
    """Make `pin` available locally. Returns a diagnosis on failure, None on success."""
    if has_commit(mod, pin):
        return None
    emit(f"  {name}: pin {pin[:8]} absent locally, fetching from {url}")
    if git(["--git-dir", str(mod), "fetch", "--quiet", "origin", pin],
           timeout=CHECKOUT_TIMEOUT_S).returncode == 0 and has_commit(mod, pin):
        return None
    git(["--git-dir", str(mod), "fetch", "--quiet", "--all"],
        timeout=CHECKOUT_TIMEOUT_S)
    if has_commit(mod, pin):
        return None
    return unfetchable_report(name, url, mod, pin)


def clone_module(sub: Sub) -> bool:
    """No object store yet -- let git create one the ordinary way (fresh clone)."""
    emit(f"  {sub.label}: no object store yet, running submodule update --init")
    r = git(["submodule", "update", "--init", "--", sub.rel], sub.parent,
            timeout=CHECKOUT_TIMEOUT_S * 4)
    if r.returncode != 0:
        log(f"{sub.label}: submodule update failed: {r.stderr.strip()}", "ERROR")
    return r.returncode == 0


def setup_one(sub: Sub, width: int, prune: bool) -> tuple[int, list[str]]:
    """Materialise one submodule. Returns (changes made, problems)."""
    if not sub.pin:
        return 0, [f"{sub.label}: no gitlink recorded; nothing to check out"]

    acted = 0
    if not sub.mod.exists():
        if not clone_module(sub):
            return 0, [f"{sub.label}: could not create an object store"]
        acted += 1

    if prune:
        git(["--git-dir", str(sub.mod), "worktree", "prune"])

    head = head_of(sub.work)
    if head == sub.pin:
        emit(f"  {sub.label:<{width}}  already at {sub.pin[:8]} - nothing to do")
        return acted, []

    diagnosis = ensure_pin(sub.label, sub.url, sub.mod, sub.pin)
    if diagnosis:
        emit(f"  {sub.label:<{width}}  FAILED - see below")
        return acted, diagnosis

    if head:
        r = git(["checkout", "--detach", sub.pin], sub.work,
                timeout=CHECKOUT_TIMEOUT_S)
        what = f"moved {head[:8]} -> {sub.pin[:8]}"
    else:
        # A registration left by a deleted worktree at this path makes
        # `worktree add` refuse; prune first so setup is re-runnable.
        git(["--git-dir", str(sub.mod), "worktree", "prune"])
        if sub.work.exists() and not any(sub.work.iterdir()):
            sub.work.rmdir()
        r = git(["--git-dir", str(sub.mod), "worktree", "add", "--detach",
                 str(sub.work), sub.pin], timeout=CHECKOUT_TIMEOUT_S)
        what = f"checked out {sub.pin[:8]} (objects shared, not copied)"

    if r.returncode != 0:
        emit(f"  {sub.label:<{width}}  FAILED - {r.stderr.strip()[:120]}")
        return acted, [f"{sub.label}: {r.stderr.strip() or r.stdout.strip()}"]
    emit(f"  {sub.label:<{width}}  {what}")
    return acted + 1, []


def setup(prune: bool = False) -> int:
    acted = 0
    problems: list[str] = []

    emit(f"preparing {ROOT}")
    emit(f"  sharing objects from {common_dir() / 'modules'}")
    emit("")

    # Re-listed after each level: a submodule's own `.gitmodules` gitlinks are
    # read from its index once it is checked out, which is more authoritative
    # than the pinned tree the first pass had to use.
    done: set[str] = set()
    while True:
        pending = [s for s in submodules() if s.label not in done]
        if not pending:
            break
        width = max(len(s.label) for s in pending)
        for sub in pending:
            done.add(sub.label)
            changes, trouble = setup_one(sub, width, prune)
            acted += changes
            problems += trouble

    emit("")
    model = resolve_shared(ROOT, VENDOR_MODEL)
    if model is None:
        problems.append(f"{VENDOR_MODEL} is in neither this checkout nor the main "
                        f"one. It is gitignored vendor IP -- see sources/README.md.")
    else:
        where = "here" if model.is_relative_to(ROOT) else f"shared from {main_checkout(ROOT)}"
        emit(f"  {'sources/models':<24} {model.name} resolved ({where})")

    emit("")
    if problems:
        for line in problems:
            log(line, "ERROR")
        log(f"setup INCOMPLETE - {acted} change(s) made, problems remain", "FATAL")
        return 1
    log(f"setup done - {acted} change(s) made" if acted
        else "setup done - already prepared, nothing changed", "ALERT")
    return check_after_setup()


def check_after_setup() -> int:
    """A setup that reports success without its own check is an assertion."""
    emit("")
    emit("verifying:")
    return check()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", default="check",
                        choices=("check", "setup"))
    parser.add_argument("--prune", action="store_true",
                        help="drop registrations left by deleted worktrees")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "setup":
        return setup(prune=args.prune)
    return check(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
