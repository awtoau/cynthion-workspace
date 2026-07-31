#!/usr/bin/env python3
"""Attribute yosys wall time per pass from a `yosys -t` log.

`yosys -t` prefixes every log line with `[SSSSS.uuuuuu]`. Each pass announces
itself with a `N.M. Executing FOO pass` header, so the delta between
consecutive headers is the wall time of the pass that preceded it.

The built-in "Time spent:" summary truncates to the top two entries, which is
not enough to decide what to optimise. This prints the full ranked list.

Usage:
    scripts/yosys_pass_profile.py <yosys -t logfile> [--top N]

Logs to ./tmp/logs/yosys_pass_profile.log as well as stdout.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

# `[00012.345678] 3.51. Executing CHECK pass (checking for obvious problems).`
LINE_RE = re.compile(r"^\[(\d+\.\d+)\]\s*(.*)$")
HEADER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.\s+Executing\s+(.+?)\s+pass\b")


def parse(path: Path) -> list[tuple[str, str, float, float]]:
    """Return (section, pass_name, start_ts, end_ts) for each pass in the log."""
    events: list[tuple[str, str, float]] = []
    last_ts = 0.0

    with path.open(errors="replace") as fh:
        for line in fh:
            m = LINE_RE.match(line)
            if not m:
                continue
            ts = float(m.group(1))
            last_ts = ts
            h = HEADER_RE.match(m.group(2))
            if h:
                events.append((h.group(1), h.group(2), ts))

    out = []
    for i, (section, name, start) in enumerate(events):
        end = events[i + 1][2] if i + 1 < len(events) else last_ts
        out.append((section, name, start, end))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logfile", type=Path)
    ap.add_argument("--top", type=int, default=25, help="rows to print (default 25)")
    args = ap.parse_args()

    logdir = Path("tmp/logs")
    logdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            logging.FileHandler(logdir / "yosys_pass_profile.log", mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("yosys_pass_profile")

    if not args.logfile.is_file():
        log.error("no such log file: %s", args.logfile)
        return 1

    passes = parse(args.logfile)
    if not passes:
        log.error("no `Executing ... pass` headers found -- was yosys run with -t?")
        return 1

    total = sum(end - start for _, _, start, end in passes)

    # Aggregate by pass name, since passes such as opt_clean run many times.
    agg: dict[str, tuple[int, float]] = {}
    for _, name, start, end in passes:
        count, elapsed = agg.get(name, (0, 0.0))
        agg[name] = (count + 1, elapsed + (end - start))

    log.info("%s", args.logfile)
    log.info("%d pass invocations, %d distinct passes, %.2fs attributed",
             len(passes), len(agg), total)
    log.info("")
    log.info("%-28s %6s %9s %7s", "pass", "calls", "seconds", "share")
    log.info("%s", "-" * 54)
    ranked = sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True)
    for name, (count, elapsed) in ranked[: args.top]:
        share = 100.0 * elapsed / total if total else 0.0
        log.info("%-28s %6d %9.2f %6.1f%%", name, count, elapsed, share)

    shown = sum(e for _, (_, e) in ranked[: args.top])
    if len(ranked) > args.top:
        log.info("%-28s %6s %9.2f %6.1f%%", f"({len(ranked) - args.top} others)", "",
                 total - shown, 100.0 * (total - shown) / total if total else 0.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
