#!/usr/bin/env python3
"""Same netlist, several nextpnr seeds, HyperRAM board test twice per seed (#628).

    ./scripts/hyperram/hr_seed_board.py --seeds default 2 3 --label fix2

- Per seed: `soc_run.py --skip-tests [--seed N]`, then `soc/hyperram_board_test.py` twice.
- Keeps each seed's `top.tim` / `top.json` under tmp/hr628/<label>-seed<N>/ for
  `hr_timing_paths.py`. One build at a time (the build dir is shared).
- Log: tmp/logs/hr_seed_board.log; per-step output in tmp/logs/hr_seed_board-<label>-*.log.
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "hr_seed_board.log"
BUILD = ROOT / "tmp" / "awto_soc" / "build" / "ck180-sync60"


def say(text):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def step(cmd, log):
    # soc_run and the board test carry their own bounds; this only records the outcome.
    with open(log, "w") as f:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
    return rc, Path(log).read_text(errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", nargs="+", default=["default", "2", "3"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in args.seeds:
        tag = f"{args.label}-seed{seed}"
        cmd = [sys.executable, "scripts/soc_run.py", "--skip-tests"]
        if seed != "default":
            cmd += ["--seed", seed]
        rc, text = step(cmd, LOG.parent / f"hr_seed_board-{tag}-build.log")
        fmax = re.findall(r"hr_clk': ([\d.]+) MHz", text)
        say(f"{tag}: build rc {rc}, nextpnr hr worst {fmax[-1] if fmax else '?'} MHz")
        keep = ROOT / "tmp" / "hr628" / tag
        keep.mkdir(parents=True, exist_ok=True)
        for name in ("top.tim", "top.json"):
            if (BUILD / name).exists():
                shutil.copy(BUILD / name, keep / name)
        results = []
        for run in range(1, args.runs + 1):
            if rc != 0:
                results.append("no build")
                continue
            _, out = step([sys.executable, "scripts/soc/hyperram_board_test.py"],
                          LOG.parent / f"hr_seed_board-{tag}-test{run}.log")
            m = re.search(r"(\d+)/(\d+) passed", out)
            results.append(m.group(0) if m else "no verdict")
            say(f"{tag}: board test {run}: {results[-1]}")
        rows.append((seed, fmax[-1] if fmax else "?", results))
    say("summary: " + "; ".join(f"seed {s}: {', '.join(r)} (hr STA {f} MHz)" for s, f, r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
