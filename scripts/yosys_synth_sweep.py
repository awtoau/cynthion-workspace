#!/usr/bin/env python3
# Runs option sets of yosys synth_ecp5 on one top.il side by side and compares the netlists.
# SPDX-License-Identifier: BSD-3-Clause

"""
Size and packability of a synth_ecp5 option set, several at once.

    ./scripts/yosys_synth_sweep.py --yosys <yosys> --src <dir with top.il, VexiiRiscv.v> \\
        [--nextpnr <nextpnr-ecp5> --lpf <top.lpf>] [--jobs 6] [--only base nowidelut]

- Each set: `synth_ecp5 -run :check <opts>` then the check label's passes without `autoname`
  (fast_build_env.py), one yosys per set, YOSYS_MAX_THREADS=1 (yosys#6418).
- Columns: cell counts from top.json; `comb~` = LUT4 + 2*CCU2C + PFUMX + L6MUX21, which is
  400 below nextpnr's TRELLIS_COMB on the 0.65+57 netlist (21473 vs 21876).
- With --nextpnr: `--pack-only`; `pack` is ok or REJECTED, and the utilisation nextpnr reports.
- Log: ./tmp/logs/yosys_synth_sweep.log; work dirs: ./tmp/yosys-sweep/<name>/
"""

import argparse
import collections
import concurrent.futures
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "tmp" / "yosys-sweep"
LOG = ROOT / "tmp" / "logs" / "yosys_synth_sweep.log"

# Without M (trellis_ff_without_m.v): the unused M input abc9 ties to 0 in yosys 0.70.
FIX_M = f"techmap -max_iter 1 -map {ROOT / 'scripts' / 'yosys' / 'trellis_ff_without_m.v'}"

# (synth_ecp5 options, commands after synth). `-widelut` is the ECP5 default, so no set for it.
SETS = {
    "base": ("", ""),
    "nowidelut": ("-nowidelut", ""),
    "cmp2softlogic": ("-cmp2softlogic", ""),
    "base+fixm": ("", FIX_M),
    "nowidelut+fixm": ("-nowidelut", FIX_M),
    "cmp2softlogic+fixm": ("-cmp2softlogic", FIX_M),
    "both": ("-nowidelut -cmp2softlogic", ""),
    "both+fixm": ("-nowidelut -cmp2softlogic", FIX_M),
}

# yosys on this design: 36-104 s single-threaded (measured 2026-11-05, 0.70+329 / 0.65+57).
# Limit = 3x the slowest, on expiry: the set is reported TIMEOUT.
YOSYS_LIMIT_S = 320
# nextpnr --pack-only: seconds; a rejected netlist fails in about 10 s, an accepted one packs in ~15 s.
PACK_LIMIT_S = 120


def script(opts, post):
    return "\n".join([
        "read_verilog VexiiRiscv.v", "read_rtlil top.il",
        f"synth_ecp5 -run :check {opts} -top top",
        "hierarchy -check", "check -noinit", "blackbox =A:whitebox", post,
        "write_json top.json", ""])


def counts(path):
    cells = json.loads(path.read_text())["modules"]["top"]["cells"]
    return collections.Counter(c["type"] for c in cells.values())


def run_set(name, args):
    opts, post = SETS[name]
    work = WORK / name
    work.mkdir(parents=True, exist_ok=True)
    for source in ("top.il", "VexiiRiscv.v"):
        link = work / source
        if not link.exists():
            link.symlink_to(Path(args.src).resolve() / source)
    (work / "top.ys").write_text(script(opts, post))
    env = dict(os.environ, YOSYS_MAX_THREADS="1")
    started = time.perf_counter()
    try:
        done = subprocess.run([args.yosys, "-q", "-l", "top.rpt", "top.ys"], cwd=work, env=env,
                              capture_output=True, text=True, timeout=YOSYS_LIMIT_S)
    except subprocess.TimeoutExpired:
        return dict(name=name, error=f"yosys TIMEOUT after {YOSYS_LIMIT_S} s")
    seconds = time.perf_counter() - started
    if done.returncode != 0:
        return dict(name=name, error=f"yosys rc={done.returncode}: {(done.stdout + done.stderr)[-300:]}")
    c = counts(work / "top.json")
    row = dict(name=name, seconds=seconds, lut4=c["LUT4"], pfumx=c["PFUMX"], l6=c["L6MUX21"],
               ccu2c=c["CCU2C"], ff=c["TRELLIS_FF"], cells=sum(c.values()))
    row["comb"] = row["lut4"] + 2 * row["ccu2c"] + row["pfumx"] + row["l6"]
    if args.nextpnr:
        try:
            pack = subprocess.run(
                [args.nextpnr, "--quiet", "--pack-only", "--12k", "--package", "CABGA256",
                 "--speed", "8", "--json", "top.json", "--lpf", str(Path(args.lpf).resolve()),
                 "--log", "pack.log"], cwd=work, capture_output=True, text=True,
                timeout=PACK_LIMIT_S)
            text = (work / "pack.log").read_text(errors="replace") if (work / "pack.log").exists() else ""
            row["pack"] = "ok" if pack.returncode == 0 else "REJECTED"
            used = re.search(r"TRELLIS_COMB:\s+(\d+)/", text)
            row["pnr_comb"] = int(used.group(1)) if used else None
        except subprocess.TimeoutExpired:
            row["pack"] = f"TIMEOUT {PACK_LIMIT_S} s"
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--src", required=True, help="directory with top.il and VexiiRiscv.v")
    parser.add_argument("--nextpnr", help="nextpnr-ecp5, to try --pack-only on each netlist")
    parser.add_argument("--lpf", help="top.lpf (needed with --nextpnr)")
    parser.add_argument("--jobs", type=int, default=6, help="yosys runs at once (one core each)")
    parser.add_argument("--only", nargs="+", choices=sorted(SETS), default=sorted(SETS))
    args = parser.parse_args()
    if args.nextpnr and not args.lpf:
        parser.error("--nextpnr needs --lpf")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    version = subprocess.run([args.yosys, "-V"], capture_output=True, text=True,
                             timeout=30).stdout.strip()
    logging.info("yosys: %s  sets=%s jobs=%d", version, " ".join(args.only), args.jobs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(lambda name: run_set(name, args), args.only))

    logging.info("%-14s %6s %7s %6s %5s %6s %6s %7s %7s  %s", "set", "sec", "LUT4", "PFUMX",
                 "L6MUX", "CCU2C", "FF", "comb~", "pnrCOMB", "pack")
    for row in rows:
        if "error" in row:
            logging.info("%-14s %s", row["name"], row["error"])
            continue
        logging.info("%-14s %6.1f %7d %6d %5d %6d %6d %7d %7s  %s", row["name"], row["seconds"],
                     row["lut4"], row["pfumx"], row["l6"], row["ccu2c"], row["ff"], row["comb"],
                     row.get("pnr_comb") or "-", row.get("pack", "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
