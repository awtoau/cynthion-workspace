#!/usr/bin/env python3
#
# Why does `init` print `pac1954 WARN no acknowledge (address)`?
# SPDX-License-Identifier: BSD-3-Clause

"""Isolate the PAC1954 address NACK to the bus release in `init i2c`.

    ./scripts/soc_pac1954_nack.py            # the four-arm matrix, 10 trials
    ./scripts/soc_pac1954_nack.py --trials 3

## The claim under test

`init` reports `pac1954 WARN no acknowledge (address)` about 1 ms after
`init i2c` says `bus released with 9 clocks + stop`. "Power-up wait" is the
wrong diagnosis: the part has been powered for hours and answers `i2c power`
immediately before and after.

Each arm differs from the one above it in exactly ONE step, so a rate that
changes names the step that caused it. An arm that shows no effect proves
nothing on its own -- arm A is the control that shows the part answers at all.

    A  init pac1954 x2                       control: no `init i2c`
    B  init i2c ; init pac1954               the reported failure
    C  init i2c ; init fusb302b ; init pac1954   another bus transacts between
    D  init i2c ; i2c power ; init pac1954       the SAME bus transacts between

C and D are the discriminator. `recover()` clocks the bus the mux is pointed
at, which is bus 2 (power_monitor). If the damage were controller state, C's
FUSB302B transactions on buses 0/1 would clear it; if it is on the wire of bus
2, only a bus-2 transaction (D) can absorb it.

Transcript -> `tmp/logs/soc-pac1954-nack.log`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "soc-pac1954-nack.log"

# One `init <peripheral>` line is a handful of I2C transactions at ~1 MHz plus
# the console echo at 115200 baud: ~30 ms measured on this board. 4x that, so a
# reply that is merely slow is not read as a dead board; on expiry the arm is
# recorded as `no reply` rather than as a pass.
REPLY_S = 0.12

# `init i2c` never selects a bus: it clocks whichever one the mux was left
# pointed at by the previous transfer. So the arm that runs `init fusb302b`
# first aims the release at bus 0/1, and the arm that runs `init pac1954` first
# aims it at bus 2 -- the same command, a different victim.
ARMS = {
    "A control          ": (["init pac1954", "init pac1954"], "pac1954"),
    "B after init i2c   ": (["init pac1954", "init i2c", "init pac1954"], "pac1954"),
    "C other bus first  ": (["init pac1954", "init i2c", "init fusb302b",
                             "init pac1954"], "pac1954"),
    "D same bus first   ": (["init pac1954", "init i2c", "i2c power",
                             "init pac1954"], "pac1954"),
    "E release aimed off": (["init fusb302b", "init i2c", "init pac1954"], "pac1954"),
    "F fusb302b control ": (["init fusb302b", "init fusb302b"], "fusb302b"),
    "G fusb302b hit     ": (["init fusb302b", "init i2c", "init fusb302b"], "fusb302b"),
    "H fusb302b spared  ": (["init pac1954", "init i2c", "init fusb302b"], "fusb302b"),
}


def ask(link, command, budget=REPLY_S):
    """Send one shell line and return its reply."""
    link.write(command.encode() + b"\r")
    return link.read_until_prompt(budget_s=budget).decode("utf-8", "replace")


def verdict(reply, what):
    """What the arm's last `init <what>` said: ok, WARN, or nothing at all."""
    for line in reply.splitlines():
        if f"init  {what}" in line:
            return "WARN" if "WARN" in line else "ok"
    return "no reply"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("w")

    def say(text):
        print(text)
        log.write(text + "\n")
        log.flush()

    link = soc_shell.Link.open(args.port)
    link.settle(REPLY_S)
    say(f"console: {link.how}")

    # The build every row below came from. A reflash mid-run makes the rows
    # incomparable, so it is recorded rather than assumed.
    # `info` is ~25 lines at 115200 baud, ~220 ms. 2x that.
    banner = ask(link, "info", budget=0.5)
    for line in banner.splitlines():
        if line.strip().startswith(("image", "gateware")):
            say(f"build: {line.strip()}")

    tally = {name: {} for name in ARMS}
    for trial in range(args.trials):
        for name, (commands, what) in ARMS.items():
            for command in commands:
                reply = ask(link, command)
            outcome = verdict(reply, what)
            tally[name][outcome] = tally[name].get(outcome, 0) + 1
            log.write(f"trial {trial} {name} -> {outcome}\n")

    say("")
    say(f"{args.trials} trials, outcome of the final `init pac1954`")
    for name, outcomes in tally.items():
        counts = "  ".join(f"{k} {v}" for k, v in sorted(outcomes.items()))
        say(f"  {name}  {counts}")

    link.close()
    log.close()
    print(f"\ntranscript: {LOG}")


if __name__ == "__main__":
    main()
