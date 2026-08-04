#!/usr/bin/env python3
#
# Simulate the FPGA_ADV port-request advertisement.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks the frame Apollo's pattern matcher is looking for, and the rules that keep it
off the wire at the wrong moment.

Apollo grants the CONTROL port on a complete `C1 14 01 A5` inside 300 ms and on
nothing else, so an advertisement that is one byte wrong, one bit period wrong, or
transmitted over a sideband reply is indistinguishable from no advertisement at all.
None of those failures raise anything on either end -- the port is simply never
granted -- which is why they are simulated rather than left to the board.

What is checked:

  * the four bytes, in order, 8-N-1, LSB first
  * every bit exactly one divisor wide, and the line released between frames
  * the repeat interval, and that three frames fit inside Apollo's timeout
  * `enable` off means the pad is never driven -- the reset state of the SoC
  * `hold` and a low line both stop a frame starting, and the guard is honoured
  * the bit period tracks `clk_freq_hz`, so a faster domain does not kill the link
  * open-drain: `pad_o` is never 1, which is what lets this share the pad

Output goes to the console and to tmp/logs/dev.log.
"""

import sys
import warnings
from pathlib import Path

from amaranth.hdl import Fragment, UnusedElaboratable
from amaranth.sim import Simulator

# Three checks below construct a module purely to assert on what `__init__`
# computes -- a divisor, an interval, a rejection. Those are never elaborated on
# purpose, and Amaranth's warning about it would be the only noise in the output.
warnings.filterwarnings("ignore", category=UnusedElaboratable)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

from sideband_advertise import (APOLLO_TIMEOUT_MS, PATTERN,  # noqa: E402
                                SidebandAdvertiser)


# Small enough to simulate whole frames in seconds, large enough that the divisor is
# realistic. 1.152 MHz / 230400 = a divisor of 5 would fail the responder's own
# minimum, so the sim uses a rate that keeps the same shape as the board.
CLK_HZ = 2_304_000
BAUD = 230400
# 1 ms here stands in for the 100 ms on the board: the same three-frames-per-timeout
# relationship, at a length a simulation can run.
INTERVAL_MS = 1


class Checks:
    def __init__(self):
        self.passed = self.failed = 0

    def check(self, what, ok, detail=""):
        line = f"  {'PASS' if ok else 'FAIL'} {what}"
        if not ok and detail:
            line += f"\n       {detail}"
        emit(line)
        if ok:
            self.passed += 1
        else:
            self.failed += 1


def capture(dut, cycles, *, enable=1, hold_until=0, rx_low_until=0):
    """Run the advertiser and return the line level per cycle.

    Open-drain with a pull-up, so the wire is low only while `pad_oe` is asserted.
    Sampling the pad rather than internal state is deliberate: it is the same view
    Apollo has, and a frame that is correct internally and wrong on the pad is
    exactly the failure worth catching.
    """
    trace = []
    drove_high = []

    async def testbench(ctx):
        for cycle in range(cycles):
            ctx.set(dut.enable, enable)
            ctx.set(dut.hold, 1 if cycle < hold_until else 0)
            ctx.set(dut.rx, 0 if cycle < rx_low_until else 1)
            await ctx.tick()
            oe = ctx.get(dut.pad_oe)
            trace.append(0 if oe else 1)
            if oe and ctx.get(dut.pad_o):
                drove_high.append(cycle)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / CLK_HZ)
    sim.add_testbench(testbench)
    sim.run()
    return trace, drove_high


def frames(trace, divisor, bits_per_frame=10 * len(PATTERN)):
    """Decode the trace into (start_cycle, [bytes]) by sampling each bit centre."""
    out = []
    index = 0
    while index < len(trace):
        if trace[index] != 0:
            index += 1
            continue
        start = index
        bits = []
        for bit in range(bits_per_frame):
            centre = start + bit * divisor + divisor // 2
            if centre >= len(trace):
                return out
            bits.append(trace[centre])
        decoded = []
        for byte_index in range(len(PATTERN)):
            window = bits[byte_index * 10:(byte_index + 1) * 10]
            value = 0
            for position, bit in enumerate(window[1:9]):
                value |= bit << position
            decoded.append((window[0], value, window[9]))
        out.append((start, decoded))
        index = start + bits_per_frame * divisor
    return out


def run(checks):
    dut = SidebandAdvertiser(clk_freq_hz=CLK_HZ, baud=BAUD,
                             interval_ms=INTERVAL_MS)
    divisor = dut.divisor
    interval = dut.interval_cycles
    frame_cycles = 10 * len(PATTERN) * divisor

    # Long enough for three frames plus the guard before the first.
    trace, drove_high = capture(dut, interval * 3 + frame_cycles * 2)
    seen = frames(trace, divisor)

    checks.check(
        "the advertisement is the exact pattern Apollo matches",
        seen and [value for _, value, _ in seen[0][1]] == list(PATTERN),
        f"decoded {[hex(v) for _, v, _ in seen[0][1]] if seen else None}, "
        f"expected {[hex(b) for b in PATTERN]}. Apollo grants the port on this "
        f"frame and on nothing else.")

    checks.check(
        "every byte is framed 8-N-1: start low, stop high",
        seen and all(start == 0 and stop == 1 for start, _, stop in seen[0][1]),
        f"framing bits were {[(s, p) for s, _, p in seen[0][1]] if seen else None}")

    checks.check(
        "the frame is 40 bit periods and no more",
        seen and len(seen) >= 2
        and all(trace[seen[0][0] + frame_cycles + slack] == 1
                for slack in range(divisor)),
        "the line is still driven after the fourth stop bit, so the next frame "
        "would run into this one")

    checks.check(
        "the line is released between frames",
        seen and len(seen) >= 2
        and all(level == 1
                for level in trace[seen[0][0] + frame_cycles:seen[1][0]]),
        "something drives the wire between advertisements, which would block "
        "Apollo's commands for the whole interval")

    gaps = [b[0] - a[0] for a, b in zip(seen, seen[1:])]
    checks.check(
        "frames repeat at the requested interval, start to start",
        gaps and all(abs(gap - interval) <= divisor for gap in gaps),
        f"gaps were {gaps}, expected ~{interval} cycles")

    checks.check(
        f"three frames fit inside Apollo's {APOLLO_TIMEOUT_MS} ms timeout",
        gaps and max(gaps) * 3 <= APOLLO_TIMEOUT_MS * CLK_HZ / 1000,
        f"largest gap {max(gaps) if gaps else None} cycles; two lost frames must "
        f"not surrender the port")

    checks.check(
        "open-drain: the pad is never driven high",
        not drove_high,
        f"pad_o was 1 while pad_oe was asserted at cycles {drove_high[:4]}; the "
        f"responder and this module share one pad by OR-ing their enables, which "
        f"is only safe while neither drives high")

    #
    # Off at reset. The SoC configures with `enable` low and must not touch the
    # wire until firmware asks -- a bitstream that seized CONTROL on configuration
    # would take the port from the debug interface used to recover the board.
    #
    quiet = SidebandAdvertiser(clk_freq_hz=CLK_HZ, baud=BAUD,
                               interval_ms=INTERVAL_MS)
    quiet_trace, _ = capture(quiet, interval * 2 + frame_cycles, enable=0)
    checks.check(
        "disabled, the pad is never driven at all",
        all(level == 1 for level in quiet_trace),
        f"{sum(1 for level in quiet_trace if level == 0)} cycles driven with "
        f"enable low")

    #
    # Holding off. `hold` is the responder transmitting; a low line is the other
    # end transmitting. Either starting a frame is a collision the FPGA could have
    # avoided knowing what it knew.
    #
    held = SidebandAdvertiser(clk_freq_hz=CLK_HZ, baud=BAUD,
                              interval_ms=INTERVAL_MS)
    hold_for = interval + frame_cycles
    held_trace, _ = capture(held, hold_for + frame_cycles * 2,
                            hold_until=hold_for)
    held_frames = frames(held_trace, divisor)
    checks.check(
        "no frame starts while the responder is transmitting",
        all(start >= hold_for for start, _ in held_frames),
        f"a frame started at {held_frames[0][0] if held_frames else None} with "
        f"hold asserted until {hold_for}")

    checks.check(
        "and the guard delays the frame past the end of the hold",
        held_frames
        and held_frames[0][0] >= hold_for + held.guard_bits * divisor,
        f"started at {held_frames[0][0] if held_frames else None}, earliest "
        f"allowed {hold_for + held.guard_bits * divisor}; the guard is what stops "
        f"a frame landing in the 40 us turnaround between a command and its reply")

    driven = SidebandAdvertiser(clk_freq_hz=CLK_HZ, baud=BAUD,
                                interval_ms=INTERVAL_MS)
    low_for = interval + frame_cycles
    driven_trace, _ = capture(driven, low_for + frame_cycles * 2,
                              rx_low_until=low_for)
    # The forced-low cycles are the other end driving, not us; only look after.
    driven_frames = [f for f in frames(driven_trace[low_for:], divisor)]
    checks.check(
        "no frame starts while the other end is driving the line low",
        all(level == 1 or index < low_for
            for index, level in enumerate(driven_trace[:low_for])),
        "the advertiser drove the wire while Apollo held it low")
    checks.check(
        "and it advertises again once the line comes back",
        driven_frames
        and [value for _, value, _ in driven_frames[0][1]] == list(PATTERN),
        "the advertiser never recovered after the line was held low, which would "
        "lose the port permanently after one busy period")

    #
    # The clock property, the one the issue asks to keep by construction.
    #
    faster = SidebandAdvertiser(clk_freq_hz=CLK_HZ * 2, baud=BAUD,
                                interval_ms=INTERVAL_MS)
    checks.check(
        "the bit period is derived from the clock, not defaulted",
        faster.divisor == divisor * 2,
        f"divisor {faster.divisor} at twice the clock, expected {divisor * 2}. A "
        f"design that raises sync and leaves this alone gets a DEAD link.")
    checks.check(
        "and the repeat interval scales with it too",
        faster.interval_cycles == interval * 2,
        f"{faster.interval_cycles} cycles, expected {interval * 2}")

    try:
        SidebandAdvertiser(clk_freq_hz=1e6, baud=BAUD)
        too_slow = False
    except ValueError:
        too_slow = True
    checks.check(
        "a clock too slow for the baud raises at elaboration",
        too_slow,
        "a divisor under 8 leaves no room to sample mid-bit and must not build")

    try:
        SidebandAdvertiser(clk_freq_hz=CLK_HZ, baud=BAUD,
                           interval_ms=APOLLO_TIMEOUT_MS)
        too_sparse = False
    except AssertionError:
        too_sparse = True
    checks.check(
        "an interval that fits fewer than three frames per timeout raises",
        too_sparse,
        f"one frame per {APOLLO_TIMEOUT_MS} ms surrenders the port on the first "
        f"lost frame")


def main():
    emit("FPGA_ADV advertisement")
    checks = Checks()
    run(checks)
    emit()
    emit(f"{checks.passed} passed, {checks.failed} failed")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
