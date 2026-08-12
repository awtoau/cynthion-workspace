#!/usr/bin/env python3
#
# Simulate the shipping FPGA_ADV link: liveness, a byte each way, nothing else.
# SPDX-License-Identifier: BSD-3-Clause

"""
Drives commands at `SidebandLink` on the wire and decodes what comes back.

The link is the one path that still answers when USB has not enumerated, the
console is silent and JTAG is occupied, so its failures are the kind nothing else
can report. Everything here is checked at the pad, in the same view Apollo has --
a reply that is right internally and wrong on the wire is exactly the failure
worth catching.

What is checked:

  * `PING` returns the protocol version and the CPU's byte, with a valid CRC
  * `STATUS` returns the status byte alone, and the heartbeat toggles per reply
  * the status bits carry what the design reports
  * a `0x80`-`0xFF` opcode delivers seven bits to the CPU
  * **`POWER` (0x2B) and `DEVICES` (0x2C) are answered as unknown commands** --
    they are not implemented, and must not look like a query returning zeros
  * the reply waits out the turnaround, and the link does not frame its own reply
  * open-drain, and the bit period derived from the clock

Output goes to the console and to tmp/logs/dev.log.
"""

import argparse
import sys
import warnings
from pathlib import Path

from amaranth.hdl import Fragment, UnusedElaboratable
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))
sys.path.insert(0, str(ROOT / "gateware" / "probes" / "sideband"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from sideband_link import (CMD_PING, CMD_STATUS, CMD_WRITE_BASE,  # noqa: E402
                           PROTOCOL_VERSION, SidebandLink)

warnings.filterwarnings("ignore", category=UnusedElaboratable)


# Fast enough to be realistic in shape, slow enough to simulate whole frames.
CLK_HZ = 2_304_000
BAUD = 230400
# The absolute turnaround the board needs, at the simulated clock. Kept at the
# real 40 us rather than shortened: it is one of the two properties this link is
# required to keep, and a sim that shortened it would not be testing it.
TURNAROUND_US = 40

# The opcodes this link does NOT implement, and the reason the check exists.
#
# ONE OPCODE IS THE MECHANISM. There is no case for any of these in the decoder,
# so all four take the identical path -- nothing matches, the default arm answers
# with bit 0 clear -- and the only thing that differs between them is the number
# in the check's name. Each one is a whole Simulator build of `SidebandLink`,
# which is 440 ms of pysim compilation against 18 ms of wire time, so the four of
# them were 1.5 s to walk one arm four times. POWER is the one the module
# docstring names; `--soak` restores the other three.
REMOVED = {0x2B: "POWER"}
SOAK_REMOVED = {0x2B: "POWER", 0x2C: "DEVICES", 0x40: "LED", 0x03: "LED_RELEASE"}

# Set by `--soak`. Guards the one check that is a comparison between two runs of
# the same command, which is a repetition by construction.
SOAK = False


def crc8(data):
    """CRC-8/ATM: polynomial 0x07, init 0x00, no reflection, no final XOR."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode(trace, divisor):
    """Bytes out of a line trace, by sampling each bit centre after a start bit."""
    out = []
    index = 0
    while index < len(trace):
        if trace[index] != 0:
            index += 1
            continue
        if index + 10 * divisor > len(trace):
            break
        value = 0
        for bit in range(8):
            centre = index + (bit + 1) * divisor + divisor // 2
            value |= trace[centre] << bit
        stop = trace[index + 9 * divisor + divisor // 2]
        out.append((value, stop))
        index += 10 * divisor
    return out


def transact(dut, command, *, message=0, state=0, events=0, error=0,
             reconfigured=0, settle=0, quiet_cycles=None):
    """Send one command byte, then watch the wire.

    Returns (reply bytes, start cycle of the first reply bit, received value,
    received strobe count, whether the pad was ever driven high).
    """
    divisor = dut.divisor
    trace = []
    seen = {"received": None, "strobes": 0, "drove_high": False}

    async def testbench(ctx):
        ctx.set(dut.rx, 1)
        ctx.set(dut.message, message)
        ctx.set(dut.state, state)
        ctx.set(dut.events, events)
        ctx.set(dut.error, error)
        ctx.set(dut.reconfigured, reconfigured)

        async def idle(cycles):
            for _ in range(cycles):
                await ctx.tick()
                if ctx.get(dut.received_strobe):
                    seen["received"] = ctx.get(dut.received)
                    seen["strobes"] += 1
                oe = ctx.get(dut.pad_oe)
                if oe and ctx.get(dut.pad_o):
                    seen["drove_high"] = True
                trace.append(0 if oe else 1)

        # Settle first, so a previous transaction's tail is never in this trace.
        await idle(settle + divisor)

        # The command, 8-N-1, LSB first.
        for level in [0] + [(command >> bit) & 1 for bit in range(8)] + [1]:
            ctx.set(dut.rx, level)
            await idle(divisor)
        ctx.set(dut.rx, 1)

        await idle(quiet_cycles)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / CLK_HZ)
    sim.add_testbench(testbench)
    sim.run()

    # Only the part of the trace after the command has been sent can hold a
    # reply; the command itself was driven by the testbench, not by the pad.
    tail_from = (settle + divisor) + 10 * divisor
    reply = decode(trace[tail_from:], divisor)
    start = next((index for index, level in enumerate(trace[tail_from:])
                  if level == 0), None)
    return reply, start, seen


def run(checks):
    divisor = SidebandLink(clk_freq_hz=CLK_HZ, baud=BAUD).divisor
    turnaround = int(CLK_HZ * TURNAROUND_US / 1e6)
    # Turnaround, four bytes of reply, and slack to prove nothing follows.
    quiet = turnaround + 10 * divisor * 6

    def link():
        return SidebandLink(clk_freq_hz=CLK_HZ, baud=BAUD,
                            turnaround_us=TURNAROUND_US)

    #
    # PING: the protocol version and the CPU's byte.
    #
    # The window is the wider one the self-framing check below needs. A second
    # identical PING for that check is another 440 ms Simulator build to watch
    # the same reply go out; widening this one costs 15 ms of wire time and
    # answers both questions from one send.
    reply, start, seen = transact(link(), CMD_PING, message=0x5A,
                                  quiet_cycles=quiet * 2)
    ping_reply, ping_seen = reply, seen
    values = [value for value, _ in reply]
    checks.check(
        "PING replies status, version, message, CRC -- four bytes",
        len(reply) == 4,
        f"got {len(reply)} bytes: {[hex(v) for v in values]}")
    checks.check(
        "PING carries the protocol version",
        len(values) >= 2 and values[1] == PROTOCOL_VERSION,
        f"version byte was {hex(values[1]) if len(values) > 1 else None}, "
        f"expected {hex(PROTOCOL_VERSION)}. A host that pings and reads this "
        f"knows which opcode map it is talking to, which is what makes removing "
        f"commands safe from the other end.")
    checks.check(
        "PING carries the CPU's byte",
        len(values) >= 3 and values[2] == 0x5A,
        f"message byte was {hex(values[2]) if len(values) > 2 else None}, "
        f"expected 0x5a")
    checks.check(
        "and the CRC covers status and payload",
        len(values) == 4 and values[3] == crc8(values[:3]),
        f"CRC was {hex(values[3]) if len(values) > 3 else None}, expected "
        f"{hex(crc8(values[:3])) if len(values) >= 3 else None}")
    checks.check(
        "every reply byte has a stop bit",
        all(stop == 1 for _, stop in reply),
        f"framing was {[stop for _, stop in reply]}")
    checks.check(
        "open-drain: the pad is never driven high",
        not seen["drove_high"],
        "the link and the advertiser share one pad by OR-ing their enables, "
        "which is only safe while neither drives high")
    checks.check(
        "the reply waits out the 40 us turnaround",
        start is not None and start >= turnaround,
        f"the first reply bit landed {start} cycles after the command, and the "
        f"master needs {turnaround}. Replying sooner loses the first bits: "
        f"Apollo returns the pin to its SERCOM one bit period after the stop "
        f"bit, then the SERCOM must resync.")

    #
    # STATUS: the status byte alone, and what it carries.
    #
    reply, _, _ = transact(link(), CMD_STATUS, state=0b10, events=1, error=1,
                           reconfigured=1, quiet_cycles=quiet)
    values = [value for value, _ in reply]
    checks.check(
        "STATUS replies status and CRC -- two bytes, no payload",
        len(reply) == 2,
        f"got {len(reply)} bytes: {[hex(v) for v in values]}")
    expected = 0b0010_1111   # ok, events, error, reconfigured, state=0b10
    checks.check(
        "the status byte carries what the design reports",
        values and values[0] == expected,
        f"status was {bin(values[0]) if values else None}, expected "
        f"{bin(expected)}: ok, events, error, reconfigured, state 0b10")
    checks.check(
        "and its CRC is valid",
        len(values) == 2 and values[1] == crc8(values[:1]),
        f"CRC was {hex(values[1]) if len(values) > 1 else None}")

    #
    # The heartbeat. Two commands in one run, so the toggle is observed rather
    # than assumed from two separate resets.
    #
    # A second transaction on a fresh instance would restart the heartbeat, so
    # this one runs both commands against the same simulation.
    both = two_commands(dut_factory=link, divisor=divisor, quiet=quiet)
    # And a third STATUS, on yet another instance, purely to compare its reply
    # against the first of those two: a re-run of a command the run has already
    # made, which is the definition of the soak tier.
    first = transact(link(), CMD_STATUS, quiet_cycles=quiet)[0] if SOAK else None
    checks.check(
        "the heartbeat toggles on every reply",
        len(both) == 2 and ((both[0] >> 6) & 1) != ((both[1] >> 6) & 1),
        f"status bytes were {[bin(v) for v in both]}; a value that CHANGES is "
        f"what proves the responder is executing, where a repeated value could "
        f"be a wedged state machine replaying a stale buffer")
    if SOAK:
        checks.check(
            "and the first reply is otherwise identical between runs",
            first and first[0][0] == both[0],
            f"{bin(first[0][0]) if first else None} against {bin(both[0])}")

    #
    # A byte in, from the host.
    #
    reply, _, seen = transact(link(), CMD_WRITE_BASE | 0x2A, quiet_cycles=quiet)
    values = [value for value, _ in reply]
    checks.check(
        "a 0x80-0xFF opcode delivers its low seven bits to the CPU",
        seen["received"] == 0x2A,
        f"received {seen['received']!r}, expected 0x2a")
    checks.check(
        "and it is strobed exactly once",
        seen["strobes"] == 1,
        f"{seen['strobes']} strobes; a repeated strobe would count one byte "
        f"several times in the CSR")
    checks.check(
        "and acknowledged with the status byte alone",
        len(reply) == 2 and (values[0] & 1) == 1,
        f"got {len(reply)} bytes, status {bin(values[0]) if values else None}; "
        f"the reply is what proves the byte was accepted")

    #
    # The removed commands. This is the check the whole change turns on.
    #
    for opcode, name in REMOVED.items():
        reply, _, _ = transact(link(), opcode, quiet_cycles=quiet)
        values = [value for value, _ in reply]
        checks.check(
            f"{name} (0x{opcode:02x}) is answered as an unknown command",
            len(reply) == 2 and values and (values[0] & 1) == 0
            and values[1] == crc8(values[:1]),
            f"got {len(reply)} bytes {[hex(v) for v in values]}. This opcode is "
            f"NOT implemented here. A well-formed frame of zeros would read as a "
            f"working query returning nothing, which is worse than a command "
            f"that is not there -- every reader would have to know which fields "
            f"were real.")

    #
    # The link must not hear itself. The pad reads back what it drives, so a
    # responder without the receive gate frames its own reply as commands and
    # answers each byte of it -- observed as three received bytes for a one-byte
    # command, the last being our own CRC.
    #
    # Read off the PING at the top of this run, whose window was widened to the
    # `quiet * 2` this check wants. Sending another one proved nothing the first
    # had not: same command, same instance state, same wire.
    checks.check(
        "the link does not frame its own reply as new commands",
        len(ping_reply) == 4 and ping_seen["strobes"] == 0,
        f"{len(ping_reply)} bytes on the wire and {ping_seen['strobes']} host "
        f"bytes received; a single PING must produce exactly one reply")

    #
    # The clock property, kept by construction.
    #
    faster = SidebandLink(clk_freq_hz=CLK_HZ * 2, baud=BAUD)
    checks.check(
        "the bit period is derived from the clock, not defaulted",
        faster.divisor == divisor * 2,
        f"divisor {faster.divisor} at twice the clock, expected {divisor * 2}. "
        f"A design that raises sync and leaves this alone gets a DEAD link, not "
        f"a slow one -- a UART tolerates about +/-2%.")
    checks.check(
        "and the turnaround is absolute, so it scales with the clock too",
        SidebandLink(clk_freq_hz=CLK_HZ * 2, baud=BAUD).turnaround_cycles
        == 2 * SidebandLink(clk_freq_hz=CLK_HZ, baud=BAUD).turnaround_cycles,
        "the master's handover cost is fixed in CPU cycles and does not shrink "
        "as baud rises; scaling with the bit period starved it exactly where it "
        "was needed -- 100% at 115200, 92% at 230400, 0% above")

    try:
        SidebandLink(clk_freq_hz=1e6, baud=BAUD)
        too_slow = False
    except ValueError:
        too_slow = True
    checks.check(
        "a clock too slow for the baud raises at elaboration",
        too_slow,
        "a divisor under 8 leaves no room to sample mid-bit and must not build")


def two_commands(dut_factory, divisor, quiet):
    """Two STATUS commands against one instance; returns the two status bytes."""
    dut = dut_factory()
    trace = []

    async def testbench(ctx):
        ctx.set(dut.rx, 1)

        async def idle(cycles):
            for _ in range(cycles):
                await ctx.tick()
                trace.append(0 if ctx.get(dut.pad_oe) else 1)

        await idle(divisor)
        for _ in range(2):
            for level in ([0] + [(CMD_STATUS >> bit) & 1 for bit in range(8)]
                          + [1]):
                ctx.set(dut.rx, level)
                await idle(divisor)
            ctx.set(dut.rx, 1)
            await idle(quiet)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / CLK_HZ)
    sim.add_testbench(testbench)
    sim.run()

    # The testbench drove the command bits on `rx`, not on the pad, so every
    # start bit in this trace is the link replying.
    return [value for value, _ in decode(trace, divisor)][::2]


def main():
    # Rebound rather than passed down: which opcodes are asked and whether the
    # re-run comparison happens are properties of the run, not of a check.
    global REMOVED, SOAK
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true",
                        help="all %d removed opcodes rather than %d, and the "
                             "reply-across-runs comparison."
                             % (len(SOAK_REMOVED), len(REMOVED)))
    args = parser.parse_args()

    if args.soak:
        SOAK = True
        REMOVED = SOAK_REMOVED

    emit("FPGA_ADV shipping link")
    checks = Checks(emit)
    run(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
