#!/usr/bin/env python3
#
# Watch the Type-C controllers across a physical attach or detach.
# SPDX-License-Identifier: BSD-3-Clause

"""
Polls `typec` and `irq` and reports only what CHANGED, so a cable event is visible.

## Why this exists

No interrupt caused by a port event has ever been recorded on this board. Every
capture reads `serviced 0` on both controllers (#150). The mask/service/re-enable
path was exercised, but nothing has ever made it fire for the reason it exists.

The observation needs a human to move a cable, and it needs the before and after
side by side -- a single reading after the fact cannot distinguish "the interrupt
fired and was serviced" from "nothing happened". So this samples continuously and
prints a line only when a field moves.

## What to expect, and why it may show nothing

**`PDWN1`/`PDWN2` are deliberately disabled** (`firmware/cynthion-soc/src/fusb302.rs`),
so the FUSB302Bs are not presenting Rd. Without pull-downs this is VBUSOK and
BC_LVL, not the Type-C sink attach state machine.

That means a device plugged into TARGET-C may produce NO change at all, and that
is a result rather than a failure -- it is the measurement that says how much of
attach detection the current configuration actually buys. Record it either way.

AUX reads `vRd-3.0A` because it is the port carrying this console. Do not unplug
it to generate an event: it is the wire these readings arrive on.

## The orientation columns are UNVALIDATED

`cc` and `bands` come from the driver reading both CC pins by moving the measure
select (#151). That code compiles and simulates; **it has never had a cable in a
connector.** The experiment it needs is exactly the one this tool is for: plug
TARGET-C one way, record `cc`, flip the plug, record it again. If `cc` swaps
between `cc1` and `cc2` while `bands` swaps with it, the derivation is right. If
it does not move, the derivation is wrong or the port is not being measured, and
the two are told apart by `bands` -- `-/-` means the sweep was skipped.

## Usage

    ./scripts/typec_watch.py                 # until interrupted
    ./scripts/typec_watch.py --polls 200     # a bounded run

Then plug or unplug a cable on TARGET-C and watch.

No sleeps and no timeouts: the loop is bounded by a poll count, and each poll
costs one console round trip, which is its own pacing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# The fields worth reporting a change in. `serviced` is the one this exists for:
# it is the count of interrupts the firmware actually handled, and it has never
# been anything but zero.
#
# `cc` and `bands` are the orientation columns: which pin the driver thinks the
# cable is on, and the two BC_LVL readings it derived that from. They are here
# because reversing a cable is the ONE experiment that can validate the
# derivation, and this is the tool that would be running while someone does it --
# a `cc` that moves cc1 -> cc2 when the plug is flipped is the evidence; a `cc`
# that does not move is equally a result.
PORT_RE = re.compile(
    r"^\s*(target|aux)\s+device\s+(\w+)\s+(.*?)\s+cc\s+(\S+)\s+(\S)/(\S)\s+"
    r"int\s+(\d+)\s+fault\s+(\d+)\s+serviced\s+(\d+)", re.M)
IRQ_RE = re.compile(r"type-c (target|aux)\s+src (\d+) pri (\d+) irqs (\d+)")


def sample(shell, emit=None):
    """One reading of both controllers, as {port: dict}."""
    text = shell("typec") + "\n" + shell("irq")
    state = {}
    for port, dev, status, cc, cc1, cc2, intr, fault, serviced in \
            PORT_RE.findall(text):
        state[port] = {"device": dev, "status": status.strip(),
                       "cc": cc, "bands": f"{cc1}/{cc2}",
                       "int": intr, "fault": fault, "serviced": serviced}
    for port, _src, _pri, irqs in IRQ_RE.findall(text):
        state.setdefault(port, {})["irqs"] = irqs
    # A parser that stops matching reports "nothing changed" forever, which is
    # indistinguishable from the result this tool exists to record. So it says so
    # instead: the reply had a port line in it and this could not read it.
    if emit is not None and not state and "device" in text:
        emit("  the `typec` reply did not parse -- the shell's format has moved "
             "ahead of PORT_RE")
    return state


def differences(old, new):
    """(port, field, was, now) for every field that moved."""
    for port in sorted(new):
        for field, now in sorted(new[port].items()):
            was = old.get(port, {}).get(field)
            if was is not None and was != now:
                yield port, field, was, now


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--polls", type=int, default=0,
                        help="stop after this many samples; 0 runs until Ctrl-C")
    parser.add_argument("--port", help="a tty node instead of the USB console")
    args = parser.parse_args()

    import soc_shell

    try:
        link = soc_shell.Link.open(args.port)
    except RuntimeError as error:
        emit(f"no console: {error}")
        return 1
    emit(f"console: {link.how}")

    def shell(command):
        """Issue one command and return its reply.

            The settle is soc_shell's REPLY_S, reused rather than reinvented: it is
            the round trip this shell takes to answer, established there. Draining
            until empty rather than reading a fixed count, because `typec` and `irq`
            differ in length and a short read would truncate mid-table.
            """
        link.write(command.encode() + b"\r")
        time.sleep(soc_shell.REPLY_S)
        reply = b""
        while True:
            chunk = link.read_available()
            if not chunk:
                break
            reply += chunk
        return reply.decode("ascii", "replace")

    emit("watching the Type-C controllers -- move a cable on TARGET-C")
    emit("PDWN1/2 are off, so no change is a RESULT, not a failure")
    emit()

    previous = sample(shell, emit)
    for port, fields in sorted(previous.items()):
        emit(f"  {port:7} {fields}")
    emit()

    polls = 0
    try:
        while args.polls == 0 or polls < args.polls:
            polls += 1
            # No `emit` here: the first sample above has already said if the
            # format moved, and repeating it once per poll would bury the
            # changes this prints.
            current = sample(shell)
            for port, field, was, now in differences(previous, current):
                emit(f"  CHANGED  {port:7} {field:9} {was} -> {now}")
            previous = current
    except KeyboardInterrupt:
        emit("\nstopped")

    emit()
    emit(f"{polls} samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
