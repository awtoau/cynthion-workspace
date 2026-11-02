#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Event stream for a batch of nextpnr runs (pnr_noise.py, soc_occupancy_timing.py).

    ./scripts/tools/watch_pnr_runs.py --log tmp/logs/seeds8.log --runs tmp/timing/seeds

One line per completed run (copied from the batch log), `STALL` when no `top.tim`
under `--runs` has been written for `--stall` seconds (60 s: nextpnr logs every
router iteration, which is seconds apart), the summary block when the log says
`wrote`, and a non-zero exit on a traceback. Polls every 15 s; exits on finish.
"""

import argparse
import re
import sys
import time
from pathlib import Path

RUN_LINE = re.compile(r"^\s+run \d+ seed=")
SUMMARY = re.compile(r"^(fmax|router|runtime|wrote|TRELLIS|DP16KD)")
FAILURE = re.compile(r"Traceback|no utilisation parsed")


def newest_mtime(runs):
    tims = list(runs.glob("*/top.tim")) if runs.is_dir() else []
    return max((t.stat().st_mtime for t in tims), default=None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", required=True, type=Path)
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--stall", type=float, default=60.0)
    ap.add_argument("--poll", type=float, default=15.0)
    args = ap.parse_args()

    seen = 0
    stalled = False
    while True:
        lines = args.log.read_text(errors="replace").splitlines() if args.log.exists() else []
        done = [l for l in lines if RUN_LINE.match(l)]
        for line in done[seen:]:
            print(line.strip(), flush=True)
        seen = len(done)
        if any(FAILURE.search(l) for l in lines):
            print("FAILED:", *lines[-6:], sep="\n  ", flush=True)
            return 1
        if any(l.startswith("wrote ") for l in lines):
            for line in lines:
                if SUMMARY.match(line):
                    print(line, flush=True)
            return 0
        mtime = newest_mtime(args.runs)
        if mtime is not None:
            age = time.time() - mtime
            if age > args.stall and not stalled:
                load = Path("/proc/loadavg").read_text().split()[0]
                print(f"STALL: no top.tim written for {age:.0f}s (limit {args.stall:.0f}s), "
                      f"loadavg {load}", flush=True)
                stalled = True
            elif age <= args.stall:
                stalled = False
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
