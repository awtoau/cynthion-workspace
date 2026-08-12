#!/usr/bin/env python3
#
# Raise the SoC clock until the board stops computing correctly. #110, #439.
# SPDX-License-Identifier: BSD-3-Clause

"""
Build, load and CHECK THE ARITHMETIC at a series of `sync` frequencies.

    ./scripts/riscv_clock_ladder.py --sync 60 70 80 90 100
    ./scripts/riscv_clock_ladder.py --spec CYNTHION_HYPERRAM_MERGED=1 --sync 60

## What counts as passing

Not "the bitstream built", not "the tty appeared". A CPU with marginal timing
does not stop; it computes the wrong answer.

  * `cpu check` -- the LIGHT load. Volatile add, multiply and two memory-mapped
    flash reads; every line must read `ok`.
  * `cpu stress` -- the HARD load, aimed at the measured critical paths rather
    than at a score: cache-missing arithmetic checksummed against a value the
    host compiler computed, 64 deliberate exceptions a pass through `TrapPlugin`
    (#468), and the timer tick. Every one of the three is verified.
  * `cpu stats` read twice -- the counters must advance.
  * `info` -- `measured sync` is COUNTED in fabric against the 60 MHz
    oscillator, and must match the rung. This is the check #439 asked for: the
    board says what it is running at, rather than the script assuming. It is
    read straight after the stress load, so the die temperature is the hot one.

A rung that passes the light load and fails the hard one is `LIGHT ONLY`, and
that is the interesting row: it would mean every previous "it runs at N MHz"
here was measured with the wrong workload.

## The rung is an environment variable

`CYNTHION_SYNC_MHZ` is in `variant.VARIANT_ENV`, so a rung is one build
directory and one cache key. It replaces the `SYNC_MHZ = \\d+` rewrite of
`top.py`, which since f7bdb18 hit the BIST arm of a ternary and left the
shipping arm at 60 -- every rung the old script reported was the same 60 MHz
design under a different name (#439). All results from before that are void.

`solve_pll` must reach the rung exactly; an unreachable one is skipped with the
reachable neighbours named, never built and rounded.

## The board

Through `scripts/board.py` only -- one shared, stateful resource, and a
transcript with the bitstream digest that produced it. Never the tty directly.

Logs to ./tmp/logs/riscv_clock_ladder.log, results to
./tmp/riscv_clock_ladder.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "tmp" / "riscv_clock_ladder.json"
LOG = ROOT / "tmp" / "logs" / "riscv_clock_ladder.log"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import clocks, variant  # noqa: E402
from soc_sync_ladder import parse_spec, timing  # noqa: E402

SOC_RUN = ROOT / "scripts" / "soc_run.py"
BOARD = ROOT / "scripts" / "board.py"

# The shell commands, in order.
#
# Two loads, not one. `cpu check` is liveness -- one multiply and two flash
# reads, which pass at rungs where real code fails. `cpu stress` drives the
# measured critical paths: cache-missing arithmetic against a host-computed
# checksum, 64 deliberate exceptions a pass through `TrapPlugin` (#468), and the
# timer tick. `info` follows it so the die temperature is read HOT.
COMMANDS = ["cpu check", "cpu stats", "cpu stress 500", "info", "cpu stats"]

# `cpu check`'s four answers. The firmware prints the verdict; this asserts that
# all four are present, because a truncated reply with no BAD in it also has no
# ok in it and would otherwise read as a pass.
CHECKS = ("sum", "prod", "@0", "@40")

# Seconds one build may take.
#
#   waits for   cargo, the CPU generator, yosys and nextpnr for one variant
#   expected    256 s, the slowest completed merged build (#432)
#   multiplier  1.25x
#   on expiry   the child is killed and the rung is BUILD TIMEOUT, named as such
BUILD_SECONDS = 320

OUTPUT = []


def say(line=""):
    emit(line)
    OUTPUT.append(line)


def flush():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(OUTPUT) + "\n")


def build(overlay, mhz, seed):
    """Build one rung. Returns (built, build_dir, slug, detail)."""
    env = {**os.environ, **overlay, "CYNTHION_SYNC_MHZ": f"{mhz:g}",
           # The ceiling refuses a rung the fabric is not expected to close.
           # Whether the DIE agrees with that expectation is the question.
           "CYNTHION_SYNC_CEILING_MHZ": f"{max(mhz, 90):g}"}
    slug = variant.slug(env)
    build_dir = variant.build_dir(ROOT, env)
    # `--allow-timing-fail` is the point of the ladder: a rung nextpnr says
    # misses is exactly the one worth loading, because STA's verdict on this die
    # has never been checked against the die (#470).
    command = [sys.executable, str(SOC_RUN), "--build-only", "--skip-tests",
               "--no-parallel-refine", "--allow-timing-fail"]
    if seed is not None:
        command += ["--seed", str(seed)]
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=BUILD_SECONDS)
    except subprocess.TimeoutExpired:
        return False, build_dir, slug, f"killed at the {BUILD_SECONDS} s limit"
    if not (build_dir / "top.bit").exists():
        tail = (done.stderr or done.stdout).strip().splitlines()
        return False, build_dir, slug, tail[-1][:110] if tail else "no top.bit"
    return True, build_dir, slug, None


def predicted(build_dir):
    """What nextpnr says about this build: {clock: {mhz, target, status}}."""
    report = build_dir / "top.tim"
    return timing(report.read_text()) if report.exists() else {}


def on_board(bitstream, budget, label):
    """Run COMMANDS through the arbiter. Returns (record, [(command, reply)]).

    By path, not by slug: one arbiter serves the whole machine and it resolves a
    slug against ITS checkout's build directory, which is another worktree's.
    """
    done = subprocess.run(
        [sys.executable, str(BOARD), "--json", "run",
         "--bitstream", str(bitstream),
         "--budget", str(budget), "--label", label, *COMMANDS],
        cwd=ROOT, capture_output=True, text=True)
    try:
        record = json.loads(done.stdout[done.stdout.index("{"):])
    except ValueError:
        record = {"status": "no record",
                  "error": (done.stderr or done.stdout)[-400:]}
    replies = [(step.get("command", ""), step.get("reply", ""))
               for step in record.get("transcript") or []]
    return record, replies


def arithmetic(replies):
    """(ok, detail) from `cpu check` -- the whole point of the ladder."""
    said = "\n".join(reply for command, reply in replies
                     if command.startswith("cpu check"))
    if not said.strip():
        return False, "cpu check returned nothing"
    if "BAD" in said:
        bad = [line.strip() for line in said.splitlines() if "BAD" in line]
        return False, "WRONG ANSWER: " + "; ".join(bad)
    missing = [name for name in CHECKS
               if not re.search(rf"^{re.escape(name)}\s+[0-9a-f]{{8}} ok",
                                said, re.M)]
    if missing:
        return False, f"no ok line for {', '.join(missing)}"
    return True, "sum, prod and both flash words ok"


def advanced(replies):
    """(ok, detail) -- did the counters move between the two `cpu stats`."""
    reads = [reply for command, reply in replies
             if command.strip() == "cpu stats"]
    if len(reads) < 2:
        return False, f"only {len(reads)} cpu stats reply(s)"
    if reads[0].strip() == reads[1].strip():
        return False, "cpu stats identical twice -- not executing"
    return True, "counters advanced"


def measured(replies, mhz):
    """(ok, khz, detail) -- what the FABRIC counts, against the rung asked for."""
    said = "\n".join(reply for command, reply in replies
                     if command.strip() == "info")
    if "CLOCK MISMATCH" in said:
        return False, None, "firmware reports CLOCK MISMATCH"
    found = re.search(r"measured sync (\d+) kHz, pll (\w+)", said)
    if not found:
        return False, None, "no `measured sync` line in `info`"
    khz, lock = int(found.group(1)), found.group(2)
    off = abs(khz - mhz * 1000) / (mhz * 1000)
    if off > 0.01:
        return False, khz, (f"fabric counts {khz} kHz, the rung asked for "
                            f"{mhz:g} MHz -- this build is not the rung (#439)")
    if lock != "locked":
        return False, khz, f"PLL {lock}"
    return True, khz, f"{khz} kHz, pll locked"


def stressed(replies):
    """(ok, detail) from `cpu stress` -- the load aimed at what actually binds."""
    said = "\n".join(reply for command, reply in replies
                     if command.startswith("cpu stress"))
    if not said.strip():
        return False, "cpu stress returned nothing"
    found = re.search(r"verdict\s+(PASS|FAIL)", said)
    if not found:
        return False, "cpu stress did not reach its verdict -- it did not finish"
    if found.group(1) != "PASS":
        broke = [line.strip() for line in said.splitlines()
                 if "BAD" in line or "WRONG" in line]
        return False, "UNDER LOAD: " + "; ".join(broke)
    passes = re.search(r"passes\s+(\d+)", said)
    return True, f"stress ok ({passes.group(1) if passes else '?'} passes)"


def die_celsius(record, replies):
    """Junction temperature -- the axis nobody has varied.

    From `info`, which the ladder runs immediately after the stress load, so it
    is the HOT reading. The arbiter's own is taken at configure time and is the
    fallback."""
    said = "\n".join(reply for command, reply in replies
                     if command.strip() == "info")
    found = re.search(r"die ([+-]?)(\d+) C", said)
    if found:
        return int(found.group(1) + found.group(2))
    return ((record.get("provenance") or {}).get("board") or {}).get("die_c")


def rung(overlay, mhz, seed, budget):
    row = {"sync_mhz": mhz, "seed": seed}
    built, build_dir, slug, detail = build(overlay, mhz, seed)
    row["slug"] = slug
    if not built:
        row["verdict"] = "NO BUILD"
        row["detail"] = detail
        say(f"  SYNC {mhz:>4g}  NO BUILD  {detail}")
        return row

    row["predicted"] = predicted(build_dir)
    clk = row["predicted"].get("clk")
    forecast = ("nextpnr clk {mhz:.2f}/{target:.0f} {status}".format(**clk)
                if clk else "no timing report")

    record, replies = on_board(build_dir / "top.bit", budget,
                               f"clock ladder sync {mhz:g}")
    row["board_status"] = record.get("status")
    row["bitstream_sha256"] = (
        ((record.get("provenance") or {}).get("bitstream") or {}).get("sha256"))
    row["transcript"] = [{"command": c, "reply": r} for c, r in replies]
    row["die_celsius"] = die_celsius(record, replies)

    ok_clock, khz, clock_detail = measured(replies, mhz)
    ok_math, math_detail = arithmetic(replies)
    ok_move, move_detail = advanced(replies)
    ok_load, load_detail = stressed(replies)
    row.update(measured_khz=khz, clock_ok=ok_clock, arithmetic_ok=ok_math,
               advancing=ok_move, stress_ok=ok_load)
    row["verdict"] = ("PASS" if (ok_clock and ok_math and ok_move and ok_load)
                      else "LIGHT ONLY" if (ok_clock and ok_math and ok_move)
                      else "FAIL")
    row["detail"] = "; ".join(
        part for part, ok in ((clock_detail, ok_clock), (math_detail, ok_math),
                              (move_detail, ok_move), (load_detail, ok_load))
        if not ok) or load_detail

    heat = f"{row['die_celsius']} C" if row["die_celsius"] is not None else "no DTR"
    say(f"  SYNC {mhz:>4g}  {row['verdict']:10s}  {forecast:34s}  die {heat:6s}  "
        f"{row['detail']}")
    return row


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sync", type=float, nargs="+",
                        default=[60, 70, 72, 75, 80, 84, 90, 96, 100],
                        help="the rungs to walk, lowest first")
    parser.add_argument("--spec", default="",
                        help="variant overlay, KEY=VAL,KEY=VAL; empty = shipping")
    parser.add_argument("--seed", type=int, default=1,
                        help="nextpnr placer seed, so a rung is reproducible")
    parser.add_argument("--budget", type=float, default=6.0,
                        help="seconds per shell command; `info` is the slow one")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    overlay = parse_spec(args.spec)

    rungs, refused = [], []
    for mhz in args.sync:
        (rungs if clocks.solve_pll(mhz, clocks.USB_PHY_MHZ) else refused
         ).append(mhz)
    for mhz in refused:
        near = clocks.reachable(mhz - 8, mhz + 8, clocks.USB_PHY_MHZ)
        say(f"SYNC {mhz:g}: no exact PLL solution from the 60 MHz oscillator. "
            f"Reachable nearby: {near}")

    say(f"clock ladder: spec={args.spec or 'shipping'} seed={args.seed} "
        f"rungs={[f'{m:g}' for m in rungs]}")
    say("passing means `cpu check` is all ok, the counters advance, and the "
        "fabric counts the rung it was asked for")
    say()

    rows = [rung(overlay, mhz, args.seed, args.budget) for mhz in rungs]

    passed = [r["sync_mhz"] for r in rows if r["verdict"] == "PASS"]
    light = [r["sync_mhz"] for r in rows if r["verdict"] != "FAIL"]
    say()
    say(f"highest rung correct UNDER LOAD: {max(passed):g} MHz"
        if passed else "nothing verified under load")
    if light and (not passed or max(light) > max(passed)):
        say(f"highest rung correct on the LIGHT load only: {max(light):g} MHz "
            f"-- the gap between these two is what a liveness test would have "
            f"reported as the limit")
    for row in rows:
        if row["verdict"] == "PASS":
            continue
        clk = (row.get("predicted") or {}).get("clk")
        say(f"first rung that broke: {row['sync_mhz']:g} MHz "
            f"({row['verdict']}) -- {row['detail']}"
            + (f"; nextpnr predicted {clk['mhz']:.2f} MHz against "
               f"{clk['target']:.0f}" if clk else ""))
        break

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(
        {"spec": args.spec, "seed": args.seed, "refused": refused,
         "rows": rows}, indent=2) + "\n")
    say(f"wrote {RESULTS.relative_to(ROOT)}")
    flush()
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
