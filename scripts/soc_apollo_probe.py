#!/usr/bin/env python3
#
# Measure how reliably the Apollo-facing console carries characters.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sends a known string to the second console and counts how often it comes back wrong.

    ./scripts/soc_apollo_probe.py                 # 10 trials on /dev/ttyACM0
    ./scripts/soc_apollo_probe.py --trials 40
    ./scripts/soc_apollo_probe.py --port /dev/ttyACM3

Exit status 0 if every trial echoed exactly. Output goes to the terminal and to
`tmp/logs/soc_apollo_probe.log`.

## Why this exists

The SoC's second 16550 talks to the SAMD11 over R14/T14, **which are the same
nets as JTAG TDI and TMS** (see the comment on `APOLLO_UART_BASE` in
`ecp5-test/riscv/vexii_hello_soc.py`). Nothing tells the FPGA when a JTAG session
is in progress, so this line is only as good as the last thing that touched those
pins, and its quality varies from minute to minute.

That matters because this port is the fallback when something else owns the USB
console -- which happens often enough that `scripts/soc_shell.py --port` exists
for it. A garbled reply there reads exactly like broken firmware, and it has
already sent one investigation after an interrupt handler that was working
perfectly. **Before blaming the firmware for a bad reply on this port, run this
and find out what the port was doing.**

## What a result means

The probe sends a 36-character line and reads back the shell's echo of it. Echo
is used rather than a command's output because it isolates transport from
everything else: the shell echoes each printable byte as it arrives, so a
mismatch is a character that was corrupted or lost between here and the line
editor, and nothing else in the firmware is involved.

  * **Substituted or inserted characters** (`abcUe+KkW.]]Xdef`) are a bad bit
    reaching the receiver -- a metastable sample, or a frame framed out of noise.
  * **A single character missing** (`abcdefghiklmn`) is a frame lost or rejected.
  * **A repeated block** (`abcdefghijabcdefghijabc`) is not a line fault at all.
    A UART cannot invent a replay of bytes it never saw; something with a buffer
    sent them twice. See the pacing experiment below.
  * **Truncation** (`abcdefghi`) is the rest of the line not arriving.
  * **A clean run** says the line was quiet, and any misbehaviour seen at the
    same time is genuinely above the transport.

The number to keep is the ratio, not a pass.

## `--pace`, and what it separated

`--pace MS` sends the line one character at a time with a gap instead of as a
back-to-back burst. That is the experiment that tells a bad line from a bad
buffer: **corruption on a wire does not care how far apart the characters are**,
while a producer that re-sends part of its buffer only does so when there is a
buffer's worth in flight.

Measured on r1.4, 20 trials per cell, board left alone, same firmware
throughout, before and after the gateware fix in `ecp5-test/riscv/serial_line.py`
(issue #113):

    mismatches / 20     back-to-back        --pace 3

    before the fix      15   garbage,       14   one character missing
                             substitutions,      per line, nothing else
                             and replays

    after the fix       11   almost all      2   one spurious leading byte,
                             block replays        twice

Two separate faults, and the table separates them.

**The per-character loss is the line, and it is fixed.** 14/20 down to 2/20 once
burst pressure is removed. That is the metastability and the missing framing
check in `serial_line.py`.

**The block replay is not the line, and it is not fixed.** It is unchanged by
the gateware, it vanishes when the characters are spaced out, and the FPGA's own
transmit direction is clean over hundreds of bytes at the same moment (a `help`
listing comes back perfect). What is left is the host-to-FPGA path above the
FPGA: the SAMD11's `console_task()` moving CDC bytes into SERCOM2, in
`repos/apollo/firmware/src/console.c`. That is a submodule and not this
workspace's to change.

## An A/B this was written for

When the console was made interrupt-driven, this port started answering
erratically and the interrupt path was the obvious suspect. Running this against
a build of the *same* firmware with the interrupt path disabled and the shell
polling, on the *same* bitstream, gave 9/10 mismatches -- against 10/10 for the
interrupt build. The transport was at fault in both, and the change was
exonerated by measurement rather than by argument. That is what this is for.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_apollo_probe.log"

# The Apollo debugger's own CDC-ACM node (`1d50:615c`), which is where the
# SAMD11 forwards SERCOM2 -- and SERCOM2 is the other end of this UART.
DEFAULT_PORT = "/dev/ttyACM0"

# A line the shell will echo character by character and then reject.
#
# All printable ASCII, because the line editor drops anything outside 0x20..0x7e
# without echoing it, and a probe whose characters can be legitimately swallowed
# cannot tell "dropped by the line editor" from "lost on the wire". No spaces
# either: `run()` splits on the first one, so a corrupted space would change how
# the reply is shaped as well as what it says.
PROBE = "abcdefghijklmnopqrstuvwxyz0123456789"

# How long to wait for one echo before calling the trial incomplete.
#
# 36 characters echoed at 115200 is ~3 ms, plus the rejection message and prompt,
# so under 10 ms of wire time. Two seconds is two orders of magnitude of headroom
# and exists so that a slow trial is reported as corruption -- which is what it
# is -- rather than as a timeout that might have been the probe's own fault.
REPLY_S = 2.0


# What the shell answers a line it does not recognise with. The echo is
# whatever precedes it on the same line.
REJECTION = b"\r\nunknown command"


def extract_echo(reply):
    """Pull the shell's echo of the probe line out of everything that arrived.

    Not `reply.split(b"\\r\\n")[0]`, which is what this used to be and which is
    wrong whenever a trial's reply is late: the next trial then opens with the
    tail of the previous one (`\\r\\n> `), the split returns the empty string
    before it, and the trial is scored as a total loss no matter what the
    transport actually did. That turned a run with four clean echoes out of six
    into a reported 20/20 failure, which is a diagnostic lying in the direction
    that makes the hardware look worse than it is -- the one direction a
    diagnostic must never lie in.

    So: find the rejection message, take what is in front of it, and drop any
    prompt that got in first.
    """
    end = reply.find(REJECTION)
    line = reply if end < 0 else reply[:end]
    # A prompt may precede the echo, either from this trial or the previous one.
    if b"> " in line:
        line = line.rsplit(b"> ", 1)[-1]
    return line.decode("ascii", "replace")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=DEFAULT_PORT,
                        help=f"tty node to probe (default {DEFAULT_PORT})")
    parser.add_argument("--trials", type=int, default=10,
                        help="how many lines to send")
    parser.add_argument("--pace", type=float, default=0.0, metavar="MS",
                        help="milliseconds between characters instead of a "
                             "back-to-back burst; see the docstring")
    args = parser.parse_args()

    import serial

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        emit(f"probing {args.port} at 115200, {args.trials} trials")
        emit()

        try:
            port = serial.Serial(args.port, 115200, timeout=REPLY_S)
        except Exception as error:
            emit(f"could not open {args.port}: {error}")
            return 1

        mismatches = 0
        try:
            for trial in range(args.trials):
                # Discard anything left over. Each trial is independent, and a
                # previous trial's late reply arriving now would be counted
                # against this one.
                port.reset_input_buffer()
                if args.pace:
                    # One character at a time, with a gap.
                    #
                    # This is the experiment that separates a bad line from a
                    # bad buffer. Corruption on the wire does not care how far
                    # apart the characters are; a producer that re-sends part of
                    # its buffer only does it when there is a buffer's worth in
                    # flight. `--pace 5` sends slower than any queue can fill.
                    #
                    # The duration is not a timeout and nothing is being waited
                    # for: it is the inter-character gap the measurement is
                    # varying, and it is the independent variable of the test.
                    for character in PROBE + "\r":
                        port.write(character.encode())
                        port.flush()
                        time.sleep(args.pace / 1000.0)
                else:
                    port.write(PROBE.encode() + b"\r")
                    port.flush()

                # Read until the shell's rejection of the line appears, which is
                # how we know the whole echo has been sent.
                deadline = time.monotonic() + REPLY_S
                reply = b""
                while time.monotonic() < deadline and b"help`" not in reply:
                    reply += port.read(port.in_waiting or 1)

                echo = extract_echo(reply)
                if echo != PROBE:
                    mismatches += 1
                    emit(f"  trial {trial}: {echo!r}")
        finally:
            port.close()

        emit()
        emit(f"{mismatches}/{args.trials} echoes differed from what was sent")
        if mismatches:
            emit("This port shares R14/T14 with JTAG TDI/TMS. A run of `apollo")
            emit("configure` or `apollo jtag-scan` immediately before this will")
            emit("produce exactly these results on firmware that is working.")
            emit("Leave the board alone and probe again before concluding")
            emit("anything about the SoC.")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
