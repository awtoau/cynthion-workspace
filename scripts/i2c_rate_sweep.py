#!/usr/bin/env python3
#
# How fast does this I2C bus ACTUALLY go? Sweep it. See #272.
# SPDX-License-Identifier: BSD-3-Clause

"""Walk the prescale ladder on all three buses and record where each stops.

    ./scripts/i2c_rate_sweep.py                 # the full ladder, 1000 reads
    ./scripts/i2c_rate_sweep.py --reads 5000

## Why the board and not arithmetic

The rate ceiling cannot be computed. It depends on SDA's rise time, which
depends on bus capacitance, which is a property of the copper and is in no
datasheet. Arithmetic says whether a rate is in spec; only the board says
whether it works.

`i2c soak` reads a device's IDENTITY every pass, not its address. An address ACK
is one bit and a marginal bus gets it right by luck; an identity is eight bits
that must be exactly right, fetched through a repeated START -- the tightest
setup interval in the protocol.

**One failure in a thousand is a failure.** That is the rate at which a marginal
bus works, and "mostly" is its signature.

## The check `i2c soak` cannot make on its own

`soak` takes the FIRST successful read at a rate as the expected value. If a rate
is so marginal that the first read is already wrong, every later read matches it
and the run reports CLEAN. So this asserts the identity against the value the
device is KNOWN to hold -- 0x54 for the PAC1954's manufacturer id -- and calls a
self-consistent wrong answer a failure.

Transcript -> `tmp/logs/i2c-rate-sweep.log`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_shell  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "i2c-rate-sweep.log"

# f_SCL = f_sync / (5 * (PRER + 1)). 11 is what the build configures.
LADDER = [11, 5, 3, 2, 1, 0]
BUSES = ["power", "target", "aux"]

# What each bus's device is known to answer, so a self-consistent wrong value
# cannot pass. PAC1954 manufacturer id 0xfe; FUSB302B device id 0x01, whose
# value is a revision and so is only checked for consistency.
KNOWN = {"power": 0x54}

RESULT = re.compile(
    r"(\d+) reads\s+(\d+) bus errors\s+(\d+) wrong values\s+expected ([0-9a-f]{2})")
VERDICT = re.compile(r"(CLEAN|FAILED) at (\S+)")

# One soak of N reads at the SLOWEST rung: ~200 us per identity read at 1 MHz,
# so 1000 reads is ~0.2 s, plus the console echo. 4x that, and a rung that does
# not answer inside it is recorded as `no reply` rather than as a pass.
BUDGET_S = 1.0


def ask(link, command, budget=BUDGET_S):
    link.write(command.encode() + b"\r")
    return link.read_until_prompt(budget_s=budget).decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reads", type=int, default=1000)
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("w")

    def say(text):
        print(text)
        log.write(text + "\n")
        log.flush()

    link = soc_shell.Link.open(args.port)
    link.settle(0.2)
    say(f"console: {link.how}")

    # The build, and the SYNC RATE, because `i2c soak` computes the rate it
    # prints from `target::TIME_HZ` rather than from the counted clock -- so on
    # a bitstream whose sync differs, every rate it prints is wrong by that
    # ratio. Recorded so the rows can be corrected rather than believed.
    for line in ask(link, "info", budget=1.0).splitlines():
        if line.strip().startswith(("image", "gateware")) or "sync" in line:
            say(f"  {line.strip()}")

    say("")
    say(f"{args.reads} reads per rung")
    say(f"{'bus':8} {'prer':>4} {'rate as printed':>16} {'errors':>7} {'wrong':>6} "
        f"{'id':>4} {'verdict':>8}")

    for which in BUSES:
        for prer in LADDER:
            reply = ask(link, f"i2c soak {which} {prer} {args.reads}")
            found = RESULT.search(reply)
            verdict = VERDICT.search(reply)
            if not found or not verdict:
                say(f"{which:8} {prer:>4} {'-':>16} {'-':>7} {'-':>6} {'-':>4} "
                    f"{'no reply':>8}")
                continue
            _, errors, wrong, ident = found.groups()
            state, rate = verdict.groups()
            # The board's own verdict, overruled when the identity is known and
            # wrong: `soak` cannot tell a wrong-but-stable read from a right one.
            want = KNOWN.get(which)
            if want is not None and int(ident, 16) != want:
                state = "WRONGID"
            say(f"{which:8} {prer:>4} {rate:>16} {errors:>7} {wrong:>6} "
                f"{ident:>4} {state:>8}")

    link.close()
    log.close()
    print(f"\ntranscript: {LOG}")


if __name__ == "__main__":
    main()
