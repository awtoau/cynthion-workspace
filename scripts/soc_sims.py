#!/usr/bin/env python3
#
# Run every SoC simulation and report each one's check count.
# SPDX-License-Identifier: BSD-3-Clause

"""
Runs the simulations under `scripts/` and prints how many checks each made.

    ./scripts/soc_sims.py            # every simulation, with its repetition
    ./scripts/soc_sims.py plic clint # only the ones whose name contains these
    ./scripts/soc_sims.py --tier once # every simulation, one cycle each
    ./scripts/soc_sims.py --list     # what is available

## The two tiers

`once` and `soak` differ in SCOPE, not in which simulations run -- both run all
fifteen. `once` exercises each mechanism a single time; `soak` passes `--soak` to
the simulations that have repetition to restore, and they put back their burst
counts, parameter sweeps, long payloads and re-runs. `fast` and `all` still work
as names for them.

Exit status is 0 only if every simulation exited 0 and reported no FAIL, so this
works as a gate the same way `scripts/check.py` does.

## Why a runner rather than one invocation each

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

Output goes to the console and to tmp/logs/dev.log. Each simulation's own full
output lands in the same file, tagged with the module and function that wrote
it -- this reports only the tail of a failing one.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from subprocess_timeout_from_history import limit_for, run_bounded  # noqa: E402

# In the order a reader should meet them: the CPU's own peripherals first, then
# the board, then the two that drive the firmware rather than the gateware, then
# the gateware that is not part of the SoC at all.
SIMS = [
    "soc_bus_sim",
    "uart16550_sim",
    "soc_serial_sim",
    "soc_stream_buffer_sim",
    "soc_plic_sim",
    "soc_clint_sim",
    "soc_i2c_owner_sim",
    "soc_typec_sim",
    "soc_hyperram_sim",
    "soc_bist_cdc_sim",
    "soc_jtag_stage_sim",
    "soc_jtag_registers_sim",
    "soc_board_sim",
    "sideband_link_sim",
    "sideband_advertise_sim",
    "soc_test",
    "qspi_burst_sim",
    "bist_sim",
    "usb_host_sie_sim",
]

# The tiers, and the axis they are split on.
#
# THE AXIS IS SCOPE, NOT COST. `once` runs EVERY simulation, each exercising its
# mechanism exactly one time. `soak` runs every simulation too, and adds the
# repetition: burst counts above one, parameter sweeps, the long payloads, the
# re-runs that compare one run against another.
#
# The first attempt at this split ran nine of the fifteen simulations in the fast
# tier and skipped six. That is tiering on cost, and it gets both ends wrong: it
# demoted a one-cycle test for being expensive, and left cheap repetition in the
# path that gates a commit. A test should not test the same thing more than once,
# and repeating an operation in the hope it glitches is a soak. See #175.
#
# So there is no list of names here. The difference between the tiers is one
# flag, passed to the simulations that have repetition to restore.
TIERS = {"once": SIMS, "soak": SIMS}

# What the tiers used to be called, kept working. `fast` meant "the cheap
# simulations" and now means "one cycle of every simulation"; `all` meant "every
# simulation" and now means "every simulation, with its repetition".
TIER_ALIASES = {"fast": "once", "all": "soak"}

RESET, BOLD, GREEN, RED, CYAN = (
    "\033[0m", "\033[1m", "\033[92m", "\033[91m", "\033[96m",
)

RESULT = re.compile(r"^\s*(PASS|FAIL)\b", re.M)


def takes_soak(name):
    """Whether this simulation has a `--soak` flag, read off its source.

    Asked of the file rather than kept in a list here, because a list is a
    second place to remember: a simulation that grows repetition and puts it
    behind `--soak` would be run without the flag by a soak tier that had not
    been told about it, and nothing would say so -- the count would not move and
    the run would still pass. The declaration lives in exactly one place.
    """
    source = (ROOT / "scripts" / f"{name}.py").read_text()
    return '"--soak"' in source


def run_one(name, python, soak=False):
    """Run one simulation. Returns (name, ok, passed, failed, seconds, tail)."""
    started = time.monotonic()
    command = [python, str(ROOT / "scripts" / f"{name}.py")]
    if soak and takes_soak(name):
        command.append("--soak")
    # Bounded by this sim's own history. A looping simulation used to wedge its
    # pool worker silently: the run never finished and nothing named which sim
    # (#295). Per-sim family, because these differ by two orders of magnitude.
    proc = run_bounded(command, cwd=ROOT, merge_stderr=True,
                       family=f"sim:{name}{'-soak' if soak else ''}")
    if proc is None:
        bound, reason = limit_for(f"sim:{name}{'-soak' if soak else ''}")
        return (name, False, 0, 1, time.monotonic() - started,
                [f"TIMED OUT after {bound:.0f}s ({reason}); no result"])
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
    parser.add_argument("--tier", choices=sorted(TIERS) + sorted(TIER_ALIASES),
                        default="soak",
                        help="`once` runs every simulation one cycle each and "
                             "gates a commit; `soak` adds the repetition. "
                             "`fast` and `all` are the old names for them")
    # Two cores back, so a full-tilt run leaves the machine usable.
    parser.add_argument("--jobs", type=int, default=max(1, os.cpu_count() - 2),
                        help="how many to run at once (they are independent)")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter to run them with")
    args = parser.parse_args()
    tier = TIER_ALIASES.get(args.tier, args.tier)
    soak = tier == "soak"

    if args.list:
        for name in TIERS[tier]:
            print(f"  {name}{'  --soak' if soak and takes_soak(name) else ''}")
        return 0

    chosen = [n for n in TIERS[tier]
              if not args.only or any(word in n for word in args.only)]
    if not chosen:
        print(f"nothing matches {args.only}", file=sys.stderr)
        return 2

    emit(f"\n{BOLD}SoC simulations{RESET}  ({len(chosen)} of {len(SIMS)}, "
         f"tier {tier})\n")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda n: run_one(n, args.python, soak), chosen))

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
    emit()

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
