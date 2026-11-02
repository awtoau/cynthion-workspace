#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Tabulate what nextpnr did, from every `top.tim` under a directory. No Fmax.

    ./scripts/soc/timing_survey.py tmp/awto_soc/build tmp/timing/seeds

Per log: name, finished, flags from `build_top.sh` beside it (seed, refine,
router), cells (COMB/FF/RAMW/DP16KD), per-clock PASS/FAIL, router iterations,
converged, longest plateau (iterations with no fall in overuse before the end),
HeAP / SA / router seconds. Writes the table as JSON next to the log in
`tmp/logs/timing_survey.json`. docs/timing-variability.md reads it.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "soc"))

from devlog import emit  # noqa: E402
from pnr_noise import parse_report  # noqa: E402

ITER = re.compile(r"iter=(\d+)\s+wires=(\d+)\s+overused=(\d+)")
PHASE = {
    "heap_s": re.compile(r"HeAP Placer Time: ([\d.]+)s"),
    "sa_s": re.compile(r"SA placement time ([\d.]+)s"),
    "route_s": re.compile(r"Router2 time ([\d.]+)s|Router1 time ([\d.]+)s"),
}
SHORT = {"$glbnet$clk": "clk", "$glbnet$hr_clk": "hr", "$glbnet$aux_phy_0__clk__o": "usb",
         "$glbnet$jtag_tck": "jtag"}


def plateau(overuse):
    """Longest run of iterations at the end where overuse did not fall below its running best."""
    best, run, longest = None, 0, 0
    for o in overuse:
        if best is None or o < best:
            best, run = o, 0
        else:
            run += 1
            longest = max(longest, run)
    return longest


def flags(tim):
    sh = tim.parent / "build_top.sh"
    text = sh.read_text() if sh.exists() else ""
    seed = re.search(r"--seed (\d+)", text)
    return {"refine": "--parallel-refine" in text,
            "router": "router2" if "router2" in text else ("router1" if text else None),
            "seed": int(seed.group(1)) if seed else None}


def survey(tim):
    text = tim.read_text(errors="replace")
    util, _, verdict = parse_report(text)
    iters = [(int(i), int(w), int(o)) for i, w, o in ITER.findall(text)]
    row = {"log": str(tim), "name": tim.parent.name,
           "finished": "Program finished normally." in text,
           **flags(tim),
           "COMB": util.get("TRELLIS_COMB"), "FF": util.get("TRELLIS_FF"),
           "RAMW": util.get("TRELLIS_RAMW"), "DP16KD": util.get("DP16KD"),
           "verdict": {SHORT.get(k, k): v for k, v in verdict.items()},
           "iterations": iters[-1][0] if iters else None,
           # router1 prints no overuse; it either finishes or is still going.
           "converged": iters[-1][2] == 0 if iters else "Routing complete." in text,
           "plateau": plateau([o for _, _, o in iters]) if iters else None,
           "first_overuse": iters[0][2] if iters else None,
           "wires": iters[-1][1] if iters else None}
    for key, rx in PHASE.items():
        m = rx.search(text)
        row[key] = float(next(g for g in m.groups() if g)) if m else None
    if row["seed"] is None:
        m = re.search(r"seed(\d+)", tim.parent.name)
        row["seed"] = int(m.group(1)) if m else None
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "tmp" / "logs" / "timing_survey.json")
    args = ap.parse_args()

    rows = [survey(t) for d in args.dirs for t in sorted(d.glob("**/top.tim"))]
    emit(f"{'name':44s} fin seed ref rtr   COMB    FF RAMW  iters conv plat  verdict")
    for r in rows:
        v = " ".join(f"{k}:{val[0]}" for k, val in sorted(r["verdict"].items()))
        emit(f"{r['name'][:44]:44s} {str(r['finished'])[0]:>3s} {str(r['seed']):>4s} "
             f"{str(r['refine'])[0]:>3s} {str(r['router'])[-1:]:>3s} "
             f"{str(r['COMB']):>6s} {str(r['FF']):>5s} {str(r['RAMW']):>4s}  "
             f"{str(r['iterations']):>5s} {str(r['converged'])[0]:>4s} {str(r['plateau']):>4s}  {v}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1))
    emit(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
