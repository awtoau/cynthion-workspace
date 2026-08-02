#!/usr/bin/env python3
#
# Refuse to publish one machine's filesystem layout.
# SPDX-License-Identifier: BSD-3-Clause

"""
Fails if a tracked file contains an absolute path that names one machine.

    python3 scripts/private_path_check.py
    python3 scripts/private_path_check.py -v      # list every file inspected

Exit status 0 if every tracked file is clean. Output goes to the terminal and
to `tmp/logs/private_path_check.log`. Run as the `paths` check in
`scripts/check.py`.

## Why

This repository is public. A path like a home directory or a local mount point
says which account and which disk the work was done on, is wrong for everyone
who clones it, and rots the moment the author moves the checkout -- which has
already happened here once. `$HOME`, `${REPOS_ROOT:-...}`, a repo-relative path
or a `<placeholder>` all say the same thing without any of that.

`$HOME` is not private: it is whoever is reading. `/home/<someone>` is.

## What counts as private

Absolute paths under the prefixes in ROOTS below -- the per-machine parts of
the filesystem. Everything else is left alone on purpose: `/dev/serial/by-id`,
`/sys`, `/proc`, `/usr` and `/opt` are the same on any machine that has them,
and this workspace names all of them legitimately.

A bare `/home` or `/mnt` with nothing after it is not a match, which is what
lets this file describe the rule it enforces.

## What it does not cover

Tracked files only, and text only: a path inside a bitstream, a `.bit` or a
vendor archive is invisible here. Binary build products should not be tracked
in the first place, which is the rule that actually prevents that -- see the
`*_build/` entries in `.gitignore`.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "private_path_check.log"

# Directory trees whose contents differ per machine and per account.
ROOTS = ["home", "Users", "mnt", "media"]

# One name component after the root: a user under /home, a disk under /mnt.
# The class
# deliberately excludes `<`, `$` and `{` so that `/home/<user>`,
# `$HOME/...` and `${REPOS_ROOT:-...}` are all fine.
PRIVATE = re.compile(r"/(?:" + "|".join(ROOTS) + r")/[A-Za-z0-9_][A-Za-z0-9_.-]*")

# Paths whose contents are not published or not ours to edit. `tmp/` is
# scratch, `.claude/` is per-machine agent state, and both are ignored by git
# anyway -- listed so that a future `git add -f` cannot smuggle one in.
SKIP_PREFIXES = ("tmp/", ".claude/")

# Files exempt from the rule, with the reason. Keep this empty if you can: an
# entry here is a path that ships to everyone who clones the repo.
EXEMPT: dict[str, str] = {}


def tracked_files() -> list[Path]:
    """Every tracked file, as paths relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True,
        stdout=subprocess.PIPE, text=True).stdout
    return [Path(name) for name in out.split("\0") if name]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every file inspected")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        problems = []
        inspected = 0

        for relative in tracked_files():
            name = relative.as_posix()
            if name.startswith(SKIP_PREFIXES) or name in EXEMPT:
                continue

            path = ROOT / relative
            # A submodule is a gitlink, and a file can be listed but absent in
            # a partial checkout. Neither is this check's business.
            if not path.is_file():
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary, or unreadable: nothing textual to check

            inspected += 1
            if args.verbose:
                emit(f"  {name}")

            for number, line in enumerate(text.splitlines(), 1):
                for match in PRIVATE.finditer(line):
                    problems.append(
                        f"{name}:{number}: {match.group(0)}\n"
                        f"      {line.strip()[:120]}")

        # A check that inspected nothing would pass forever. Assert the
        # discovery, not just the result.
        if inspected == 0:
            emit("no tracked text files found -- this check inspected nothing.")
            emit("Either git ls-files failed or the checkout is empty; fix "
                 "that rather than leaving a guard that looks nowhere.")
            return 1

        emit(f"{inspected} tracked text file(s) checked")

        if problems:
            emit()
            emit("This repository is public. An absolute path under "
                 f"{', '.join('/' + r for r in ROOTS)} names one machine and "
                 "one account.")
            emit("Use $HOME, ${REPOS_ROOT:-$HOME/git/awtoau}, a repo-relative "
                 "path, or a <placeholder> instead.")
            emit()
            for problem in problems:
                emit(f"  {problem}")
            emit()
            emit(f"{len(problems)} private path(s)")
            emit(f"log: {LOG.relative_to(ROOT)}")
            return 1

        emit("no private paths in tracked files")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
