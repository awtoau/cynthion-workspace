#!/usr/bin/env python3
"""Move stale scratch files out of tmp/ into the machine-wide wastebasket.

tmp/ accumulates the residue of finished work: probe captures from a closed
issue, drafts of comments already posted, .bin fixtures from a flash session
that ended weeks ago. All of it is regenerable or already recorded elsewhere,
but `rm` is the wrong verb for a judgement call made by a script -- so this
moves rather than deletes, into /mnt/2tb/wastebasket/<slot>-<timestamp>/,
where it stays recoverable until the user empties it.

Two things are never moved:
  * git-tracked files. tmp/ is gitignored as a whole, but tmp/drafts/ was
    force-added, and a tracked file under tmp/ is content someone chose to
    keep.
  * anything modified on or after --before. That cutoff is the caller's
    judgement about where finished work ends and current work begins; there
    is no defensible default, so the flag is required.

Usage:
    scripts/clean_tmp.py --before 2026-08-01 --dry-run
    scripts/clean_tmp.py --before 2026-08-01
    scripts/clean_tmp.py --before 2026-08-01 --dir tmp/logs --slot cynthion-logs
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "tmp" / "logs"
WASTEBASKET = Path("/mnt/2tb/wastebasket")

log = logging.getLogger("clean_tmp")


def setup_logging(verbose: bool) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)
    to_file = logging.FileHandler(LOGS / "clean_tmp.log")
    to_file.setLevel(logging.DEBUG)
    to_file.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%S%z")
    )
    log.addHandler(to_file)


def tracked_files(directory: Path) -> set[Path]:
    """Paths under `directory` that git tracks, absolute and resolved."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", str(directory.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return {(ROOT / p).resolve() for p in out.split("\0") if p}


def stale_files(directory: Path, cutoff: dt.datetime,
                keep: set[Path]) -> list[Path]:
    """Files directly in `directory` last modified before `cutoff`.

    Not recursive: a subdirectory of tmp/ is usually a build tree or a
    still-live working set, and deciding about one of those as a unit is a
    separate call from sweeping loose files.
    """
    stale = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        if path.resolve() in keep:
            log.debug("keep (tracked): %s", path.name)
            continue
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
        if mtime >= cutoff:
            log.debug("keep (recent):  %s", path.name)
            continue
        stale.append(path)
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True, metavar="YYYY-MM-DD",
                    help="move files last modified before this local date")
    ap.add_argument("--dir", default="tmp", type=Path,
                    help="directory to sweep, workspace-relative (default: tmp)")
    ap.add_argument("--slot", default="cynthion-tmp",
                    help="wastebasket slot prefix (default: cynthion-tmp)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would move, touch nothing")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also log every file that is kept, and why")
    args = ap.parse_args()

    setup_logging(args.verbose)

    try:
        cutoff = dt.datetime.strptime(args.before, "%Y-%m-%d")
    except ValueError:
        log.error("--before wants YYYY-MM-DD, got %r", args.before)
        return 2

    directory = (ROOT / args.dir).resolve()
    if not directory.is_dir():
        log.error("not a directory: %s", directory)
        return 2
    if ROOT not in directory.parents and directory != ROOT:
        log.error("refusing to sweep outside the workspace: %s", directory)
        return 2

    stale = stale_files(directory, cutoff, tracked_files(directory))
    if not stale:
        log.info("nothing older than %s in %s", args.before, args.dir)
        return 0

    total = sum(p.stat().st_size for p in stale)
    log.info("%d files, %.1f KB, older than %s in %s",
             len(stale), total / 1024, args.before, args.dir)

    if args.dry_run:
        for path in stale:
            log.info("  would move: %s", path.name)
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = WASTEBASKET / f"{args.slot}-{stamp}" / directory.name
    dest.mkdir(parents=True)

    for path in stale:
        shutil.move(str(path), str(dest / path.name))
        log.debug("moved: %s", path.name)

    log.info("moved %d files to %s", len(stale), dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
