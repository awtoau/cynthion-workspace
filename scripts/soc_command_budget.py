#!/usr/bin/env python3
#
# Measure each shell command's round trip, and derive a per-command timeout.
# SPDX-License-Identifier: BSD-3-Clause

"""
Time every shell command on the board and emit the per-command timeout table.

    ./scripts/soc_command_budget.py                 # every command
    ./scripts/soc_command_budget.py --repeat 5      # 5 samples each
    ./scripts/soc_command_budget.py bench info      # just these

Output is mirrored to ./tmp/logs/soc_command_budget.log; raw samples land in
./tmp/soc_command_budget.json.

## Why

`soc_shell.py` used one global `REPLY_S = 2.0` for every command, described in
its own comment as "generous purely so a slow command is not truncated".
`soc_test.py` had measured the slowest command at 47 ms and set 0.25 s. The
measurement existed and never propagated (#295).

One global number has to cover the slowest command, so it is wrong for the other
thirty-four -- and it is wrong in the expensive direction, because the wait is
only paid when something has ALREADY gone wrong. A `bench hyperram` that needs
seconds forces every `info` to wait seconds before reporting a dead board.

## The rule this implements

**1.25x the measured worst case** (`docs/agents/agent-rules.md`). A timeout too
low names itself in the log; a timeout too long is indistinguishable from a hang.

Two departures from a bare 1.25x, both stated rather than folded in:

  * a **floor**, because a command measured at 3 ms would otherwise get 3.75 ms
    and fail on a scheduling hiccup on the host. The floor is host jitter, not
    board behaviour.
  * the multiplier applies to the **worst** sample, not the mean. The worst is
    what the timeout has to survive.

## What this does NOT do

Run on QEMU. Emulated timings are not board timings and a budget derived from
TCG would be wrong in both directions -- `soc_test.py` keeps its own constants
for that reason.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
import soc_shell  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "soc_command_budget.log"
RESULTS = ROOT / "tmp" / "soc_command_budget.json"

# Floor for any derived budget, in seconds.
#
# Host-side jitter, not board behaviour: a Python round trip through a socket can
# lose tens of milliseconds to scheduling on a loaded machine, and a budget below
# that fails for reasons nothing on the board caused.
FLOOR_S = 0.10

# What the rule asks for, over the WORST sample rather than the mean.
MULTIPLIER = 1.25

# Every command, with arguments that make it do its normal work.
#
# Read-only. Nothing here erases flash, writes a rail, resets the board or
# changes a limit -- a calibration run that altered the thing it measures would
# be the instrument reporting its own load.
COMMANDS = [
    "help", "?", "info", "time", "rtic", "board", "selftest", "sideband",
    "cpu stats", "cpu check", "cpu irq", "cpu log",
    "info map", "info pmod", "info ports", "info button",
    "usb3343 status", "vbus status", "fusb302b", "i2c status",
    "pac1954 status", "pac1954 alert", "pac1954 rate",
    "hyperram status", "flash id", "hyperram id",
    "bram read 0", "flash read 0",
    "bram bench", "flash bench",
]


def measure(link, command, repeat, ceiling):
    """Round-trip `command` `repeat` times. Returns (samples, last reply)."""
    samples = []
    reply = b""
    for _ in range(repeat):
        started = time.perf_counter()
        link.write(command.encode() + b"\r")
        reply = link.read_until_prompt(budget_s=ceiling)
        samples.append(time.perf_counter() - started)
    return samples, reply


def budget(worst):
    """The timeout for a command whose worst observed round trip was `worst`."""
    return max(FLOOR_S, round(worst * MULTIPLIER, 3))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("commands", nargs="*", default=None,
                        help="commands to time; default is all of them")
    parser.add_argument("--repeat", type=int, default=3,
                        help="samples per command (default 3)")
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    commands = args.commands or COMMANDS
    LOG.parent.mkdir(parents=True, exist_ok=True)
    emit(f"timing {len(commands)} command(s) x {args.repeat} on the board")

    # A generous ceiling DURING CALIBRATION, and only here: the point is to find
    # out how long things take, so a budget derived under a tight one would be
    # measuring the budget rather than the command.
    ceiling = 10.0
    try:
        link = soc_shell.Link.open(args.port)
    except RuntimeError as error:
        emit(f"could not reach the console: {error}")
        return 1
    emit(f"console: {link.how}")

    # A bare Enter first, for the reason `soc_shell.main` gives: it lands at a
    # clean prompt whatever was half-typed, and it is not timed -- the first
    # round trip after opening carries the open's own cost.
    link.write(b"\r")
    link.read_until_prompt(budget_s=ceiling)

    rows = []
    try:
        for command in commands:
            samples, reply = measure(link, command, args.repeat, ceiling)
            worst = max(samples)
            rows.append({
                "command": command,
                "worst_ms": round(worst * 1000, 1),
                "mean_ms": round(statistics.mean(samples) * 1000, 1),
                "budget_s": budget(worst),
                "reply_bytes": len(reply),
                "samples_ms": [round(s * 1000, 1) for s in samples],
            })
            RESULTS.write_text(json.dumps(rows, indent=2))
    finally:
        link.close()

    rows.sort(key=lambda r: r["worst_ms"], reverse=True)

    emit("")
    emit(f"{'command':<16} {'worst':>9} {'mean':>9} {'budget':>9}  bytes")
    for row in rows:
        emit(f"{row['command']:<16} {row['worst_ms']:>7.1f}ms "
             f"{row['mean_ms']:>7.1f}ms {row['budget_s']:>8.3f}s "
             f"{row['reply_bytes']:>6}")

    slowest = rows[0]
    emit("")
    emit(f"slowest: {slowest['command']} at {slowest['worst_ms']} ms")
    emit(f"a single global budget would have to be {slowest['budget_s']} s, "
         f"and every other command would wait that long to report a dead board.")
    emit("")
    emit("Paste into soc_shell.py's BUDGET_S, keeping the measurement date:")
    for row in rows:
        if row["budget_s"] > FLOOR_S:
            emit(f'    "{row["command"]}": {row["budget_s"]},'
                 f'   # worst {row["worst_ms"]} ms')
    emit(f"rows -> {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
