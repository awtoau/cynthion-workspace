#!/usr/bin/env python3
#
# When Apollo's flash and RAM figures moved, by building every pin. See #404.
# SPDX-License-Identifier: BSD-3-Clause

"""Walks the apollo submodule pins and measures each, so growth has a commit.

`apollo_budget_check.py` catches a step change but cannot say which change it
was. This builds the firmware at every pin the workspace has held and accounts
for each with the same by-address arithmetic, so flash and RAM carry a delta and
a commit rather than a percentage.

- oldest pin first, deltas against the previous row
- the pinned SHA is restored and rebuilt last, so the tree holds the real
  firmware rather than the last row measured
- `VERSION_STRING` is not pinned here: it is `git describe --always` of the
  commit being measured, seven hex digits wide at every one of them

    ./scripts/apollo_budget_history.py
    ./scripts/apollo_budget_history.py --since d61f6d5

Output also goes to `tmp/logs/dev.log`.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import apollo_budget_check  # noqa: E402
from devlog import emit, log  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
ELF = FIRMWARE / "_build" / "cynthion_d11" / "firmware.elf"

# A clean serial build of this firmware is 0.61 s (`apollo_budget_levers.py`).
# 5 s is ~8x that: a loaded machine does not flake, and a wedged link is named in
# seconds. On expiry the pin, the limit and the command are logged and the sweep
# stops -- a half-built tree must not be measured.
BUILD_LIMIT_S = 5.0

# The commit that introduced the ceilings this sweep is about (#404).
DEFAULT_SINCE = "d61f6d5"


def git(args, cwd=ROOT):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


def pins(since):
    """`[(workspace sha, date, subject, apollo sha)]`, oldest first."""
    span = f"{since}~1..HEAD" if since else "HEAD"
    listed = git(["log", "--format=%h|%ad|%s", "--date=short", span,
                  "--", "repos/apollo"]).splitlines()
    found = []
    for line in reversed(listed):
        sha, date, subject = line.split("|", 2)
        entry = git(["ls-tree", sha, "repos/apollo"])
        found.append((sha, date, subject, entry.split()[2]))
    return found


def build(apollo_sha):
    """Check out that pin, build clean, and account for the result."""
    git(["checkout", "--quiet", apollo_sha], cwd=APOLLO)
    described = git(["describe", "--abbrev=7", "--always", "--tags"], cwd=APOLLO)
    subprocess.run(["rm", "-rf", str(FIRMWARE / "_build")], check=True)
    command = ["make", "APOLLO_BOARD=cynthion", "-j8",
               f"VERSION_STRING={described}"]
    try:
        result = subprocess.run(command, cwd=FIRMWARE, capture_output=True,
                                text=True, timeout=BUILD_LIMIT_S)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT build {apollo_sha}: killed at {BUILD_LIMIT_S}s -- "
            f"{' '.join(command)}", "ERROR")
        return None
    if result.returncode != 0:
        log(f"build failed at {apollo_sha}: "
            f"{(result.stderr or result.stdout)[-400:]}", "ERROR")
        return None
    return apollo_budget_check.account(ELF)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default=DEFAULT_SINCE,
                        help="oldest workspace commit to include")
    args = parser.parse_args()

    pinned = git(["ls-tree", "HEAD", "repos/apollo"]).split()[2]
    rows = pins(args.since)
    if not rows:
        print(f"no repos/apollo pin changes since {args.since}")
        return 1

    emit("Apollo budget history -- every pin built, not estimated")
    emit()
    emit(f"  {'workspace':<9} {'date':<11} {'apollo':<8} "
         f"{'flash':>6} {'d':>6}  {'RAM':>5} {'d':>6}  subject")
    emit("  " + "-" * 78)

    previous = None
    failed = False
    try:
        for sha, date, subject, apollo_sha in rows:
            book = build(apollo_sha)
            if book is None:
                emit(f"  {sha:<9} {date:<11} {apollo_sha[:7]:<8} BUILD FAILED")
                failed = True
                continue
            rom_d = "" if previous is None else f"{book['rom_used'] - previous[0]:+d}"
            ram_d = "" if previous is None else f"{book['ram_used'] - previous[1]:+d}"
            emit(f"  {sha:<9} {date:<11} {apollo_sha[:7]:<8} "
                 f"{book['rom_used']:>6} {rom_d:>6}  {book['ram_used']:>5} "
                 f"{ram_d:>6}  {subject[:40]}")
            previous = (book["rom_used"], book["ram_used"])
    finally:
        emit()
        if build(pinned) is None:
            emit("  PINNED REBUILD FAILED -- the tree is not holding the "
                 "pinned firmware")
            return 1
        emit(f"  pin {pinned[:7]} restored and rebuilt")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
