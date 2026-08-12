#!/usr/bin/env python3
"""What a float-High RATE does to the extra-latency election. (#400)

`hyperram_dqs_model_sim.py --stage config` settles the mechanism at 0% and 100%.
This puts a number between them: the fraction of `var` transactions that elect
the LONG count off a float the device never drove High, against the rate at
which a float reads High.

The corpus's headline is ~1 marginal cell in 128. This is the curve that says
which float-High rate that corresponds to, on the pre-#381 sample cycle -- and
that it is flat at zero on the shipped one.

    scripts/hyperram_rwds_float_rate.py                 # the default sweep
    scripts/hyperram_rwds_float_rate.py --seeds 64      # tighter error bars

Log: tmp/logs/hyperram_rwds_float_rate.log
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hyperram_dqs_model_sim as sim  # noqa: E402

log = logging.getLogger("float-rate")

# The shim the config stage runs at: the one combination the sweep at 57a9a99
# found the device decoding a CA in.
SHIM = ["+dq_pipe=0", "+ck_pipe=1", "+dq_ph=1", "+rd_slip=0"]

# `var`, and the device DECLINING -- the only shape in which a float read High
# invents a request nobody made. `refresh_every=100` is more transactions than
# any sequence runs, so the device never asks.
VAR = ["+dv_from_read=0", "+latency_mode=1", "+refresh_every=100"]

ROW = re.compile(r"txn=mem_rd .*elected=(\d) dev_long=(\d)")


def elections(lines: list[str]) -> tuple[int, int]:
    """`(transactions, spurious long elections)` over the memory reads."""
    seen = wrong = 0
    for line in lines:
        m = ROW.search(line)
        if m:
            seen += 1
            wrong += int(m.group(1)) != int(m.group(2))
    return seen, wrong


def sweep(image: str, pcts: list[int], seeds: int) -> dict[int, tuple[int, int]]:
    out = {}
    for pct in pcts:
        seen = wrong = 0
        for seed in range(1, seeds + 1):
            lines = sim.stage_config(
                SHIM + VAR + [f"+rwds_float_pct={pct}", f"+rwds_float_seed={seed}"],
                image)
            s, w = elections(lines)
            seen, wrong = seen + s, wrong + w
        out[pct] = (seen, wrong)
        log.info("  %3d%% float High -> %4d/%4d transactions elected long "
                 "spuriously (%5.1f%%)", pct, wrong, seen,
                 100.0 * wrong / seen if seen else 0.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=16,
                    help="float streams per rate; 4 transactions each")
    ap.add_argument("--pct", type=int, nargs="*",
                    default=[0, 1, 3, 6, 12, 25, 50, 100])
    args = ap.parse_args()

    logs = ROOT / "tmp" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(logs / "hyperram_rwds_float_rate.log", "w")])

    # Its OWN workdir: `hyperram_dqs_model_sim.py` clears its one at startup, so
    # sharing it means either run destroys the other's images mid-sweep.
    sim.WORKDIR = ROOT / "tmp" / "hyperram-rwds-float-rate"
    if sim.WORKDIR.exists():
        shutil.rmtree(sim.WORKDIR)
    sim.WORKDIR.mkdir(parents=True, exist_ok=True)

    pre = sim.build_config(round_trip=-1)
    now = sim.build_config()
    log.info("%d seeds x 4 codes = %d transactions per rate",
             args.seeds, 4 * args.seeds)

    log.info("pre-#381 build, sample cycle 2 -- before CS# has fallen:")
    before = sweep(pre, args.pct, args.seeds)
    log.info("shipped build, sample cycle %d -- inside the driven CA:",
             sim.DQS_ROUND_TRIP + 3)
    after = sweep(now, args.pct, args.seeds)

    log.info("")
    log.info("  %-10s %-22s %s", "float High", "pre-#381 (cycle 2)",
             f"shipped (cycle {sim.DQS_ROUND_TRIP + 3})")
    for pct in args.pct:
        b, a = before[pct], after[pct]
        log.info("  %8d%%  %5.1f%% (%3d/%3d)         %5.1f%% (%3d/%3d)", pct,
                 100.0 * b[1] / b[0] if b[0] else 0.0, b[1], b[0],
                 100.0 * a[1] / a[0] if a[0] else 0.0, a[1], a[0])

    bad = [p for p, (s, w) in after.items() if w]
    if bad:
        log.error("the SHIPPED sample cycle elected off a float at %s -- #381's "
                  "fix does not remove the mechanism", bad)
        return 1
    if not any(w for _, w in before.values()):
        log.error("no float-High rate moved an election even at the pre-#381 "
                  "sample cycle: the injection is inert and this proves nothing")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
