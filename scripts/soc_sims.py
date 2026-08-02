#!/usr/bin/env python3
#
# Run every SoC simulation and report each one's check count.
# SPDX-License-Identifier: BSD-3-Clause

"""
Runs the simulations under `scripts/` and prints how many checks each made.

    ./scripts/soc_sims.py            # every simulation
    ./scripts/soc_sims.py plic clint # only the ones whose name contains these
    ./scripts/soc_sims.py --list     # what is available

Exit status is 0 only if every simulation exited 0 and reported no FAIL, so this
works as a gate the same way `scripts/check.py` does.

## Why a runner rather than nine invocations

The count is the point, not just the exit status. These simulations are additive
-- a branch that adds behaviour adds checks -- so the number each one reports is a
measurement that only means something next to the last one. A count that goes DOWN
after a merge is the signal this script exists to make visible: it means an
assertion was lost in a conflict resolution and the suite still passed, which is
exactly the failure no exit status can show.

`scripts/check.py` deliberately does not run these. Its nine checks are the ones
that gate a commit; these elaborate Amaranth designs and take tens of seconds
each, which is a different budget and a different question.

## Counting

Every one of these prints `  PASS <what>` or `  FAIL <what>` per assertion, so the
count is those lines. A simulation that printed a summary and no per-check lines
would report zero here, which is why the count is checked against the exit status
rather than trusted on its own.

Output goes to the console and to tmp/logs/soc_sims.log; each simulation's own
full output lands in tmp/logs/<name>.log, written by the simulation itself.
"""

import argparse
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_sims.log"

# In the order a reader should meet them: the CPU's own peripherals first, then
# the board, then the two that drive the firmware rather than the gateware.
SIMS = [
    "soc_bus_sim",
    "uart16550_sim",
    "soc_serial_sim",
    "soc_plic_sim",
    "soc_clint_sim",
    "soc_i2c_owner_sim",
    "soc_hyperram_sim",
    "soc_jtag_stage_sim",
    "soc_board_sim",
    "soc_test",
]

RESET, BOLD, GREEN, RED, CYAN = (
    "\033[0m", "\033[1m", "\033[92m", "\033[91m", "\033[96m",
)

RESULT = re.compile(r"^\s*(PASS|FAIL)\b", re.M)


def run_one(name, python):
    """Run one simulation. Returns (name, ok, passed, failed, seconds, tail)."""
    started = time.monotonic()
    proc = subprocess.run(
        [python, str(ROOT / "scripts" / f"{name}.py")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    out = proc.stdout or ""
    results = RESULT.findall(out)
    passed = results.count("PASS")
    failed = results.count("FAIL")
    ok = proc.returncode == 0 and failed == 0 and passed > 0
    tail = out.strip().splitlines()[-12:] if not ok else []
    return name, ok, passed, failed, time.monotonic() - started, tail


def main():
    parser = argparse.ArgumentParser(
        description="Run the SoC simulations and report each one's check count.")
    parser.add_argument("only", nargs="*", metavar="NAME",
                        help="substrings; run only simulations matching one")
    parser.add_argument("--list", action="store_true", help="list and exit")
    parser.add_argument("--jobs", type=int, default=4,
                        help="how many to run at once (they are independent)")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter to run them with")
    args = parser.parse_args()

    if args.list:
        for name in SIMS:
            print(f"  {name}")
        return 0

    chosen = [n for n in SIMS
              if not args.only or any(word in n for word in args.only)]
    if not chosen:
        print(f"nothing matches {args.only}", file=sys.stderr)
        return 2

    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(text=""):
        print(text)
        lines.append(text)

    emit(f"\n{BOLD}SoC simulations{RESET}  ({len(chosen)} of {len(SIMS)})\n")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda n: run_one(n, args.python), chosen))

    # Back into the declared order, so two runs are comparable line by line.
    order = {name: index for index, name in enumerate(SIMS)}
    results.sort(key=lambda r: order[r[0]])

    total = 0
    bad = []
    for name, ok, passed, failed, seconds, tail in results:
        total += passed
        mark = f"{GREEN}ok{RESET}" if ok else f"{RED}FAILED{RESET}"
        note = f"  {RED}{failed} FAIL{RESET}" if failed else ""
        emit(f"  {name:<20} {passed:>4} checks  {seconds:5.1f}s  {mark}{note}")
        if not ok:
            bad.append(name)
            for line in tail:
                emit(f"      {line}")

    emit()
    emit(f"  {BOLD}{total} checks{RESET} across {len(results)} simulations "
         f"in {time.monotonic() - started:.1f}s")
    if bad:
        emit(f"  {RED}failed: {', '.join(bad)}{RESET}")
    emit(f"  log: {LOG.relative_to(ROOT)}")
    emit()

    LOG.write_text("\n".join(re.sub(r"\033\[[0-9;]*m", "", l) for l in lines))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
