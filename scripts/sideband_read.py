#!/usr/bin/env python3
#
# Read sideband values back on the host: every command, CRC checked.
# SPDX-License-Identifier: BSD-3-Clause

"""
Queries the FPGA_ADV sideband from the host and validates every reply.

This exists because the reply framing is easy to get wrong, and getting it wrong
looks exactly like a corrupted link. A reply is:

    status byte | payload | CRC-8

so the length to request is **payload + 2**, not the payload length. Asking for
the payload length alone truncates the reply before its CRC byte, and the check
then fails on a perfectly good link. That mistake was made twice from the command
line before this script existed, and both times it read as "CRC BAD" on hardware
that was working.

The payload lengths are taken from the gateware's own `PAYLOAD_SIZE` table
rather than restated here, so they cannot drift apart.

Two independent verdicts are reported per command, and they are not redundant:

  * the host recomputes CRC-8/ATM over status+payload and compares
  * the firmware's own counters (`ok`, `crc_fail`, `timeout`) are read

The firmware counters are authoritative for the link itself -- they count what
the microcontroller saw, not what the host's arithmetic reconstructed. If the two
disagree, the host's framing is wrong, which is precisely the failure this script
is meant to make impossible to repeat.

    ./scripts/sideband_read.py
    ./scripts/sideband_read.py --soak 5000
    ./scripts/sideband_read.py --command POWER
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "sideband_read.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Framing, decoding and command encoding all live in sideband_decoder so this
# script and any other consumer cannot disagree about them.
from sideband_decoder import (
    check, decode, commands, reply_length,
    encode_leds, encode_led_colours, encode_led_release, LED_COLOURS,
)

# Vendor request 0xc3 overloads wValue. Any value that is NOT one of these SETS
# the mode -- so reading with wValue=0 selects EIC rather than reporting
# anything, which is a trap worth naming in code rather than in a comment
# somewhere else.
REQ = 0xC3
W_MODE_READ = 0xFFFF
W_COMMAND = 0xFFFE
W_HEALTH = 0xFFFC
MODE_UART = 1

IN = 0xC0
OUT = 0x40


def query(dev, opcode):
    """One command. Returns (reply, verdict)."""
    want = reply_length(opcode)
    try:
        reply = bytes(dev.ctrl_transfer(IN, REQ, W_COMMAND,
                                        (want << 8) | opcode, want))
    except Exception as error:
        return b"", f"FAILED ({type(error).__name__})"
    ok, reason = check(reply, opcode)
    return reply, reason


def send_leds(dev, spec):
    """Drive the FPGA's LEDs over the sideband. Returns a description.

    These are the FPGA's six colour LEDs, not Apollo's own -- `apollo leds`
    (vendor request 0xa1) sets the microcontroller's, over USB, and never
    touches the FPGA. Before the sideband existed there was no host path to
    these at all.

    Sending expects no reply, so there is nothing to CRC-check. It goes as an
    OUT transfer rather than an IN of length zero: the firmware skips its receive
    loop when the wanted length is 0, but a zero-length IN control transfer still
    waits on a data stage that never comes and fails with a libusb timeout. The
    direction bit is what distinguishes "send this" from "send this and reply".

    Whether it took is confirmed by looking at the board, which is the point of
    an LED.
    """
    if spec.strip().lower() in ("release", "off-override"):
        opcode = encode_led_release()
        described = "released -- LEDs back to whatever the design drives"
    elif spec.strip().isdigit():
        opcode = encode_leds(int(spec))
        described = f"pattern {int(spec):#08b}"
    else:
        colours = [c.strip() for c in spec.split(",") if c.strip()]
        opcode = encode_led_colours(*colours)
        described = "lit: " + ", ".join(colours)

    dev.ctrl_transfer(OUT, REQ, W_COMMAND, (0 << 8) | opcode, None)
    return f"0x{opcode:02x}  {described}"


def health(dev):
    """[ok, crc_fail, timeout]. Each saturates at 255 and clears on read."""
    try:
        return list(dev.ctrl_transfer(IN, REQ, W_HEALTH, 0, 3))
    except Exception:
        # Absent on firmware older than b48d4bf. Reporting zeros there would
        # claim a clean link from a measurement that cannot see one.
        return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--command", help="only this one, by name")
    parser.add_argument("--soak", type=int, default=0, metavar="N",
                        help="then run N POWER transactions and score them")
    parser.add_argument("--leds", metavar="SPEC",
                        help="drive the FPGA's LEDs over the sideband: a "
                             "0-63 pattern, comma-separated colour names "
                             "(red,orange,yellow,green,blue,violet), or "
                             "'release' to hand them back. Note these are the "
                             "FPGA's six LEDs -- `apollo leds` sets Apollo's "
                             "own, over USB, which is a different set.")
    args = parser.parse_args()

    from apollo_fpga import ApolloDebugger
    debugger = ApolloDebugger()
    dev = debugger.device

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        emit(f"firmware: {debugger.get_firmware_version()}")

        # UART mode is not the power-on default: EIC is, so that a host which
        # never chooses behaves like older firmware. Commands only work in UART.
        dev.ctrl_transfer(OUT, REQ, MODE_UART, 0, None)
        mode = list(dev.ctrl_transfer(IN, REQ, W_MODE_READ, 0, 1))
        if mode != [MODE_UART]:
            emit(f"mode did not take: {mode} -- expected [{MODE_UART}]")
            return 1
        emit(f"mode: UART ({mode[0]})")
        emit()

        health(dev)          # clear, so the counts below cover this run only

        if args.leds:
            emit(f"LED command sent: {send_leds(dev, args.leds)}")
            emit()

        selected = commands()
        if args.command:
            selected = [c for c in selected
                        if c[0].upper() == args.command.upper()]
            if not selected:
                emit(f"no such command: {args.command}")
                return 1

        failures = 0
        for label, opcode, payload_len in selected:
            reply, verdict = query(dev, opcode)
            if verdict != "CRC OK":
                failures += 1
            emit(f"  {label:<8} 0x{opcode:02x}  {verdict:<12} {reply.hex()}")
            if verdict == "CRC OK":
                emit(f"           {decode(opcode, reply)}")

        counters = health(dev)
        emit()
        if counters is None:
            emit("health counters absent -- firmware predates b48d4bf")
        else:
            emit(f"firmware health: ok={counters[0]} crc_fail={counters[1]} "
                 f"timeout={counters[2]}  (each saturates at 255)")
            if counters[1] or counters[2]:
                emit("  the firmware saw errors the host check may not have")
                failures += 1

        if args.soak:
            emit()
            emit(f"soaking {args.soak} POWER transactions")
            from apollo_fpga.gateware import sideband as gw
            power_len = gw.PAYLOAD_SIZE[gw.CMD_POWER]
            health(dev)
            good = short = bad = 0
            for _ in range(args.soak):
                _, verdict = query(dev, gw.CMD_POWER)
                if verdict == "CRC OK":
                    good += 1
                elif verdict == "CRC BAD":
                    bad += 1
                else:
                    short += 1
            counters = health(dev)
            rate = good / args.soak * 100
            emit(f"  good {good}  short {short}  crc_bad {bad}   {rate:.2f}%")
            if counters is not None:
                emit(f"  firmware health: ok={counters[0]} "
                     f"crc_fail={counters[1]} timeout={counters[2]}")
                if counters[1] or counters[2]:
                    failures += 1
            if rate < 100.0:
                failures += 1

        emit()
        emit("PASS" if failures == 0 else f"FAIL ({failures} problem(s))")
        emit(f"log: {LOG}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
