#!/usr/bin/env python3
#
# Run the HyperRAM checks and benchmarks on the board and keep the numbers. See #92.
# SPDX-License-Identifier: BSD-3-Clause

"""
Drives the shell's HyperRAM commands over the console and records what came back.

    ./scripts/hyperram_measure.py
    ./scripts/hyperram_measure.py --note "DQS, CK 120"

## Correctness first, then speed -- in that order and not the other

`hrcross` runs before any benchmark, and a failure stops the run. A throughput
number taken from a path that returns the wrong bytes is worse than no number:
it is a number that will be quoted. The recorded history of this interface is
faults that produce *plausible wrong answers* rather than failures -- a half-word
slip reads back as data, a fixed-latency count that lands outside the window
reads back as data -- so "it printed a rate" is not evidence that it worked.

## It does not configure the board

`./dev.py run` builds, configures and flashes; this only talks to what is already
running. Keeping them apart means a measurement cannot silently be taken from a
bitstream other than the one just built -- `soc_run.py` owns the staleness check
and there is no second copy of it here to disagree.

## The counters are read in the same breath as the rate

`bench hyperram` gives MB/s; `cpu stats` gives where the cycles went. Reading
them in one session against one configuration is the difference between "20.71
MB/s" and "20.71 MB/s, of which 294 cycles per line were backend stall" -- the
first is a score and the second says what to change next.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "tmp" / "hyperram-measure.json"

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

from soc_test import BoardSession  # noqa: E402

# What to ask, in order. `hrcross` is first because it gates the rest.
#
# Each entry is (command, the substring that means the command finished). The
# terminator is a piece of the command's OWN last line rather than the prompt:
# the prompt is echoed on connect and by every prior command, so matching it
# reads the wrong one and returns before the output exists.
CHECKS = [
    ("hr cross", ("hyperram ports agree", "hyperram ports DISAGREE")),
    ("hr test", ("hyperram write+read ok", "round-trip BAD")),
]

BENCHES = [
    ("hr bench", ("MB/s", "did not answer")),
    ("cpu stats", ("busy", "ipc")),
]

# How long one command may take before the run is called failed.
#
# The board answers in MILLISECONDS. `bench hyperram` is the slowest command
# here and it walks 16 KiB a few ways -- tens of ms at the rates measured, and
# the whole shell tick loop is 1 s. One second is therefore already two orders
# of magnitude of headroom over the thing being waited for.
#
# It was 30 s, which is not a bound on anything real: a board that has stopped
# answering is indistinguishable from one that answered at 100 ms, so the only
# effect of a large value is that a dead board takes half a minute per command
# to say so.
BUDGET = 1.0


def ask(session, command, needles):
    """Send one command; return its output, or None if nothing matched."""
    start = len(session.snapshot())
    session.send(command.encode() + b"\r")
    for needle in needles:
        found = session.expect(needle.encode(), BUDGET, since=start)
        if found is not None and found >= 0:
            # Let the rest of the line and any trailer arrive before slicing.
            time.sleep(0.05)
            text = session.snapshot()[start:].decode("utf-8", "replace")
            return text.replace("\r", "").strip()
    return None


def run(args):
    if args.command:
        session = BoardSession(lambda text="": emit(text))
        for command in args.command:
            start = len(session.snapshot())
            session.send(command.encode() + b"\r")
            # Wait for the prompt that FOLLOWS the reply, not a fixed
            # settle: `hr sweep` runs eight line-writes with a cache
            # eviction each and takes far longer than a one-line reply, and
            # a fixed settle either truncates it or pads every other
            # command. Falls back to BUDGET if no prompt arrives, which is
            # what a hung board looks like.
            session.expect(b"\n> ", args.budget, since=start)
            text = session.snapshot()[start:].decode("utf-8", "replace")
            emit(f"$ {command}")
            for line in text.replace("\r", "").splitlines():
                emit(f"  {line}")
        return 0
    emit(f"hyperram measurement{': ' + args.note if args.note else ''}")
    emit()

    session = BoardSession(lambda text="": emit(text))
    record = {"note": args.note, "checks": {}, "benches": {}}

    emit("correctness")
    ok = True
    for command, needles in CHECKS:
        out = ask(session, command, needles)
        record["checks"][command] = out
        if out is None:
            # Show what DID arrive. "NO ANSWER" alone cannot distinguish a
            # dead board from a command the firmware does not have from a
            # needle that never matched, and those need different fixes.
            tail = session.snapshot()[-400:].decode("utf-8", "replace")
            emit(f"  {command}: NO ANSWER; last bytes seen:")
            for line in tail.replace("\r", "").splitlines()[-6:]:
                emit(f"    | {line}")
            ok = False
            continue
        for line in out.splitlines():
            if line.strip() and not line.strip().startswith(command):
                emit(f"  {line.strip()}")
        if "DISAGREE" in out or "BAD" in out:
            ok = False

    if not ok:
        emit()
        emit("REFUSING to benchmark: the path does not return the "
             "right bytes, so a rate from it would be meaningless.")
        RESULTS.write_text(json.dumps(record, indent=2))
        return 1

    emit()
    emit("throughput")
    for command, needles in BENCHES:
        out = ask(session, command, needles)
        record["benches"][command] = out
        if out is None:
            tail = session.snapshot()[-400:].decode("utf-8", "replace")
            emit(f"  {command}: NO ANSWER; last bytes seen:")
            for line in tail.replace("\r", "").splitlines()[-6:]:
                emit(f"    | {line}")
            continue
        for line in out.splitlines():
            if line.strip() and not line.strip().startswith(command):
                emit(f"  {line.strip()}")

    RESULTS.write_text(json.dumps(record, indent=2))
    emit()
    emit(f"results: {RESULTS}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--note", default="",
                        help="what configuration this is, kept with the numbers")
    parser.add_argument("--budget", type=float, default=BUDGET,
                        help="seconds to wait for a reply. The default is a "
                             "bound on 'the board stopped answering'; raise it "
                             "only to tell a SLOW command from a hung one.")
    parser.add_argument("--command", action="append", default=[],
                        help="send this instead of the usual set, and print the "
                             "raw reply; repeatable. For asking the board what "
                             "firmware it is actually running.")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
