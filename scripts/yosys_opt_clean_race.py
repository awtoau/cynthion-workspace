#!/usr/bin/env python3
# Hunts the yosys opt_clean memory race (YosysHQ/yosys#6418): N memories, forced threads; a patch's test case.
# SPDX-License-Identifier: BSD-3-Clause

"""
One module with N unpacked-array delay chains (N memories), run through `prep` with
opt_clean forced multi-threaded; counts the outcomes. Design and knobs are the issue's.

    ./scripts/yosys_opt_clean_race.py --yosys <path> --chains 12 --runs 30
    ./scripts/yosys_opt_clean_race.py --yosys <path> --chains 11 13 12 30 51 --runs 20 --jobs 8

- Armed counts (hashlib growth rule): 12, 30, 51, 106, 216. 11 and 13 never fail.
- Exit 1 if any run is not `ok`.
- Runs are independent processes, `--jobs` at a time (threads; the wait releases the GIL).
- Log: ./tmp/logs/yosys_opt_clean_race.log
"""

import argparse
import collections
import concurrent.futures
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "yosys_opt_clean_race.log"

# A clean run is ~0.3 s (measured on 0.65+57 and 0.70+329, 28 threads); a hang is a
# self-looping bucket chain and never returns. 15 s = 50x, on expiry: counted as `hang`.
RUN_LIMIT_S = 15


def design(chains, width, depth):
    """Verilog-2005 flat ports (read_verilog has no unpacked-array ports)."""
    n, w, d = chains, width, depth
    out = [f"module top (input clk, input en, input [{n * w}-1:0] a_flat, input [{n * w}-1:0] b_flat);",
           f"  reg [{d}-1:0] filled = 0;"]
    out += [f"  reg [{w}-1:0] d{i} [{d}];" for i in range(n)]
    out += ["  always @(posedge clk) begin", "    if (en) begin",
            f"      filled <= {{filled[{d}-2:0], 1'b1}};"]
    for i in range(n):
        out.append(f"      d{i}[0] <= a_flat[{i * w} +: {w}];")
        out += [f"      d{i}[{k}] <= d{i}[{k - 1}];" for k in range(1, d)]
    out += ["    end", "  end", "  always @(posedge clk) begin"]
    out += [f"    if (filled[{d}-1]) assert (b_flat[{i * w} +: {w}] == d{i}[{d}-1]);" for i in range(n)]
    out += ["  end", "endmodule", ""]
    return "\n".join(out)


def classify(returncode, text):
    if returncode is None:
        return "hang"
    if "memory_strings.count" in text:
        return "memid_assert"
    if returncode != 0:
        return "crash"
    return "ok"


def run_once(yosys, source, threads, extra_env):
    env = dict(os.environ, YOSYS_WORK_UNITS_PER_THREAD="1", YOSYS_MAX_THREADS=str(threads),
               **extra_env)
    script = f"read_verilog -sv -formal -nomem2reg {source}; prep -top top"
    try:
        done = subprocess.run([yosys, "-QT", "-p", script], env=env, capture_output=True,
                              text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired as expired:
        return "hang", f"no exit in {RUN_LIMIT_S} s: {(expired.stdout or b'')[-200:]!r}"
    text = done.stdout + done.stderr
    return classify(done.returncode, text), f"rc={done.returncode} {text[-300:]!r}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yosys", required=True, help="yosys binary to test")
    parser.add_argument("--chains", type=int, nargs="+", default=[12])
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--threads", type=int, default=16, help="YOSYS_MAX_THREADS per run")
    parser.add_argument("--jobs", type=int, default=4, help="runs at once")
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--env", nargs="*", default=[], metavar="K=V",
                        help="extra environment, e.g. YOSYS_MAX_THREADS=1 to override")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    version = subprocess.run([args.yosys, "-V"], capture_output=True, text=True,
                             timeout=RUN_LIMIT_S).stdout.strip()
    extra_env = dict(item.split("=", 1) for item in args.env)
    logging.info("yosys: %s  threads=%d runs=%d jobs=%d env=%s", version, args.threads,
                 args.runs, args.jobs, extra_env or "-")

    bad = 0
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as scratch:
        for chains in args.chains:
            source = Path(scratch) / f"chains{chains}.v"
            source.write_text(design(chains, args.width, args.depth))
            counts = collections.Counter()
            first_bad = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                results = list(pool.map(
                    lambda _: run_once(args.yosys, source, args.threads, extra_env),
                    range(args.runs)))
            for outcome, detail in results:
                counts[outcome] += 1
                if outcome != "ok" and first_bad is None:
                    first_bad = (outcome, re.sub(r"\s+", " ", detail))
            bad += args.runs - counts["ok"]
            logging.info("chains=%-3d  ok=%d memid_assert=%d crash=%d hang=%d",
                         chains, counts["ok"], counts["memid_assert"], counts["crash"],
                         counts["hang"])
            if first_bad:
                logging.info("  first failure: %s %s", *first_bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
