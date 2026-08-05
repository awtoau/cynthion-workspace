#!/usr/bin/env python3
#
# Simulate the Apollo-facing serial line, and the three ways a pad can be mis-wired.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks `ecp5-test/riscv/serial_line.py` against issue #113.

    python3 scripts/soc_serial_sim.py
    python3 scripts/soc_serial_sim.py -v      # print every byte and edge

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## What this is evidence for

Issue #113 is the Apollo console corrupting and truncating characters, worst
right after `apollo configure`, and losing the first line after the tty is
opened. Three faults were found, all in how the pad was wired rather than in
`amaranth_stdio`. Each has a check here that FAILS against the old wiring and
passes against `SerialLine`:

  * **the stop bit was never driven.** `oe` came off `~tx.rdy`, which falls on
    the same cycle the transmitter shifts the stop bit out. The check measures
    `tx_oe` against `tx_o` across a whole character and requires the enable to
    outlast the last bit. A companion check drives `AsyncSerialTX` directly and
    demonstrates the old expression failing, so the fix is not being compared
    against an assumption about the PHY.

  * **framing errors arrived as characters.** The receiver is fed a frame with a
    space where its stop bit belongs, and must not emit a byte.

  * **noise was framed into bytes.** The receiver is fed a burst at roughly the
    rate JTAG toggles TDI -- which is the same net as this pin -- and must emit
    nothing, then must recover and receive correctly once the line goes quiet.

The fourth fault, the missing synchroniser on the receive pad, is structural
rather than behavioural: a simulation has no metastability to reproduce. It is
checked by construction instead -- that `SerialLine` puts an `FFSynchronizer`
between `rx_i` and everything else, and that no design hands a raw pad to
`AsyncSerialRX`.

## Why a check here costs milliseconds, and why that is not a poll

#175 read this harness's ~5 ms per check off `dev.log` and grouped it with
`soc_test`'s 20 ms as an interval to remove. It is not one: nothing here waits on
wall time at all. Every check is preceded by its own elaboration and simulation,
and a profile counts 10,276 `pysim` deltas across the eleven runs below with the
whole cost inside `Simulator.advance`. Milliseconds per check IS the simulator's
rate, and the only way to lower it is fewer simulated cycles -- which would mean
testing less. Recorded here so the next person reading a flat per-check figure
does not go looking for a `sleep` that was never there.

## What the line reports losing

A separate group, and not about #113. `source.valid` is one cycle and does not
wait for `source.ready`, so a byte offered to a full sink is destroyed here and
the 16550 downstream cannot see it -- its own `sink` backpressures, so a full
FIFO there is a stall rather than a loss. `overrun` and `frame_error` are the
only record that either happened, and they become LSR.OE and LSR.FE.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth.hdl import Fragment
from amaranth.sim import Simulator
from amaranth_stdio.serial import AsyncSerialTX

from serial_line import SerialLine


# Small enough that a character is a few hundred simulated cycles rather than
# five thousand, large enough to clear AsyncSerialRX's floor of 5 and to leave
# the sampler unambiguously in the middle of a bit. The real design runs 520;
# nothing here depends on the value, which is the point of testing at 8.
DIVISOR = 8

# One character, 8N1: start, eight data, stop.
FRAME_BITS = 10


def run(dut, testbench):
    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


async def drive_frame(ctx, pin, bits, divisor=DIVISOR):
    """Put a list of bit values on `pin`, one bit period each."""
    for bit in bits:
        ctx.set(pin, bit)
        await ctx.tick().repeat(divisor)


def frame_bits(byte, stop=1):
    """A start bit, eight data bits LSB first, and whatever stop bit is asked for."""
    return [0] + [(byte >> n) & 1 for n in range(8)] + [stop]


async def idle(ctx, pin, bit_periods, divisor=DIVISOR):
    ctx.set(pin, 1)
    await ctx.tick().repeat(bit_periods * divisor)


# ---------------------------------------------------------------------------
# The transmitter: the stop bit has to be driven.
# ---------------------------------------------------------------------------

def run_tx_checks(checks, verbose):
    # First, the PHY on its own, to establish what the old expression did rather
    # than assert it. If amaranth_stdio ever changes when `rdy` rises, this check
    # is what says so -- and the `frame_cycles` constant in serial_line.py is
    # derived from the same behaviour, so it must not drift silently.
    phy = AsyncSerialTX(divisor=DIVISOR, data_bits=8, parity="none")
    seen = {}

    async def phy_testbench(ctx):
        ctx.set(phy.data, 0x61)     # 'a' -- bit 7 low, like every ASCII character
        ctx.set(phy.ack, 1)
        await ctx.tick()
        ctx.set(phy.ack, 0)
        trace = []
        for cycle in range(DIVISOR * (FRAME_BITS + 4)):
            trace.append((ctx.get(phy.o), ctx.get(phy.rdy)))
            await ctx.tick()
        seen["trace"] = trace

    run(phy, phy_testbench)

    trace = seen["trace"]
    rdy_rise = next(i for i, (_, rdy) in enumerate(trace) if rdy)
    # The frame is one leading mark period, then start, data, stop. So the stop
    # bit occupies the last `DIVISOR` cycles of an 11-period span.
    stop_begins = (1 + FRAME_BITS - 1) * DIVISOR
    stop_ends = (1 + FRAME_BITS) * DIVISOR - 1

    checks.check(
        "AsyncSerialTX raises rdy at the START of the stop bit",
        rdy_rise == stop_begins,
        f"rdy rose at cycle {rdy_rise}, stop bit spans {stop_begins}..{stop_ends}.\n"
        f"If this moved, serial_line.py's frame_cycles is now wrong.")

    checks.check(
        "so `oe = ~tx.rdy` releases the pad for the whole stop bit  (the #113 bug)",
        rdy_rise <= stop_begins,
        f"rdy rose at {rdy_rise}; the stop bit needs the pad driven "
        f"through {stop_ends}.")

    # Now the wrapper, which has to hold the pad past the end of the frame.
    dut = SerialLine(divisor=DIVISOR, data_bits=8)
    seen = {}

    async def line_testbench(ctx):
        ctx.set(dut.rx_i, 1)
        ctx.set(dut.sink.payload, 0x61)
        ctx.set(dut.sink.valid, 1)
        # Wait for the transmitter to take it.
        while not ctx.get(dut.sink.ready):
            await ctx.tick()
        await ctx.tick()
        ctx.set(dut.sink.valid, 0)

        trace = []
        for _ in range(DIVISOR * (FRAME_BITS + 4)):
            trace.append((ctx.get(dut.tx_o), ctx.get(dut.tx_oe)))
            await ctx.tick()
        seen["trace"] = trace

    run(dut, line_testbench)
    trace = seen["trace"]

    if verbose:
        for cycle, (o, oe) in enumerate(trace):
            checks.note(f"tx cycle {cycle:4d}  o={o} oe={oe}")

    driven = [i for i, (_, oe) in enumerate(trace) if oe]
    last_driven = driven[-1] if driven else -1

    checks.check(
        "SerialLine drives the pad from the first bit to the last",
        driven and driven[0] == 0 and last_driven >= stop_ends,
        f"pad driven for cycles {driven[0] if driven else None}..{last_driven}; "
        f"the frame occupies 0..{stop_ends}.")

    checks.check(
        "the pad is driven continuously -- no gap inside a character",
        driven == list(range(driven[0], last_driven + 1)),
        f"output enable had holes in it: driven cycles {driven}")

    stop_levels = {trace[i][0] for i in range(stop_begins, stop_ends + 1)}
    checks.check(
        "and the stop bit it drives is a mark",
        stop_levels == {1},
        f"levels seen across the stop bit: {sorted(stop_levels)}")

    checks.check(
        "the pad is released once the character is over",
        not trace[-1][1],
        "tx_oe was still asserted well after the frame; the line would be held "
        "against JTAG.")


def run_tx_burst_check(checks, verbose):
    """Back-to-back characters must not blink the output enable between them."""
    dut = SerialLine(divisor=DIVISOR, data_bits=8)
    seen = {}
    payload = [0x41, 0x42, 0x43]

    async def testbench(ctx):
        ctx.set(dut.rx_i, 1)
        trace = []
        remaining = list(payload)
        started = False
        ctx.set(dut.sink.payload, remaining[0])
        ctx.set(dut.sink.valid, 1)
        for _ in range(DIVISOR * (FRAME_BITS + 2) * (len(payload) + 1)):
            if ctx.get(dut.sink.valid) and ctx.get(dut.sink.ready):
                started = True
                remaining.pop(0)
                if remaining:
                    ctx.set(dut.sink.payload, remaining[0])
                else:
                    ctx.set(dut.sink.valid, 0)
            if started:
                trace.append(ctx.get(dut.tx_oe))
            await ctx.tick()
        seen["trace"] = trace

    run(dut, testbench)
    trace = seen["trace"]
    driven = [i for i, oe in enumerate(trace) if oe]

    checks.check(
        "three characters back to back hold the pad without releasing it",
        driven == list(range(driven[0], driven[-1] + 1)),
        f"output enable dropped inside the burst; asserted cycles {driven}")


# ---------------------------------------------------------------------------
# The receiver: what it must refuse.
# ---------------------------------------------------------------------------

def receive(bits_before, frames, bits_after=40, divisor=DIVISOR):
    """Drive a bit pattern into a SerialLine and collect the bytes it emits.

    `frames` is a list of bit lists. `bits_before` is the idle mark ahead of
    them, in bit periods -- the receiver has to see enough of it to arm.
    """
    dut = SerialLine(divisor=divisor, data_bits=8)
    got = []
    seen = {}

    async def testbench(ctx):
        ctx.set(dut.source.ready, 1)
        await idle(ctx, dut.rx_i, bits_before, divisor)
        seen["armed_after_idle"] = ctx.get(dut.armed)
        for frame in frames:
            await drive_frame(ctx, dut.rx_i, frame, divisor)
            # `source.valid` is a single cycle; sample it while driving rather
            # than after, which is why the collection is inside the loop below.
        ctx.set(dut.rx_i, 1)
        await ctx.tick().repeat(bits_after * divisor)
        seen["armed_at_end"] = ctx.get(dut.armed)
        seen["frame_errors"] = ctx.get(dut.frame_errors)

    # Collecting bytes needs a second testbench sampling every cycle; amaranth
    # runs testbenches cooperatively, so a sampler that only ever awaits ticks
    # interleaves cleanly with the driver above.
    async def sampler(ctx):
        while True:
            if ctx.get(dut.source.valid) and ctx.get(dut.source.ready):
                got.append(ctx.get(dut.source.payload))
            await ctx.tick()

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(sampler, background=True)
    sim.add_testbench(testbench)
    sim.run()
    return got, seen


def run_rx_checks(checks, verbose):
    # The ordinary case first: if this fails nothing else here means anything.
    got, seen = receive(20, [frame_bits(b) for b in b"hi"])
    if verbose:
        checks.note(f"clean line received {got!r}")
    checks.check(
        "a quiet line delivers the characters sent on it",
        got == list(b"hi"),
        f"expected {list(b'hi')}, received {got}")

    checks.check(
        "the receiver arms itself once the line has been idle",
        seen.get("armed_after_idle") == 1,
        "armed was still low after 20 bit periods of mark; the qualifier is "
        "not releasing and nothing will ever be received.")

    # A frame with a space where the stop bit belongs. This is what a bad bit on
    # the wire produces, and delivering it as a character is the #113 fault.
    got, seen = receive(20, [frame_bits(0x5a, stop=0)])
    checks.check(
        "a frame with a bad stop bit is dropped, not delivered  (the #113 bug)",
        got == [],
        f"the receiver emitted {got} for a frame that was never valid.")
    checks.check(
        "and it is counted",
        seen.get("frame_errors") == 1,
        f"frame_errors read {seen.get('frame_errors')}, expected 1")

    # The receiver must come back afterwards, or one glitch kills the port until
    # the next bitstream.
    got, _ = receive(20, [frame_bits(0x5a, stop=0),
                          [1] * 20,
                          frame_bits(ord("k"))])
    checks.check(
        "after a framing error the line recovers once it goes quiet",
        got == [ord("k")],
        f"expected the character after the quiet period, received {got}")

    # And it must NOT come back before then, or the JTAG case is unfixed.
    got, _ = receive(20, [frame_bits(0x5a, stop=0), frame_bits(ord("k"))])
    checks.check(
        "but not while the line is still busy",
        got == [],
        f"the receiver re-armed inside a burst and delivered {got}; a burst "
        f"containing a framing error is not a burst worth reassembling.")


def run_jtag_noise_check(checks, verbose):
    """A burst at roughly the rate JTAG drives TDI must not become characters.

    R14 is the same net as JTAG TDI. During `apollo configure` the SAMD11 drives
    it from SPI at megahertz rates, which is far faster than a bit period -- so
    to a receiver with no idle qualifier it is an endless supply of start bits.
    """
    # Toggling every other cycle: with a divisor of 8 that is 4x the bit rate,
    # the same order of magnitude of over-rate as SPI-accelerated JTAG against
    # 115200. Deliberately not a clean divisor of the bit period, so the frames
    # it would otherwise produce are not all identical.
    noise = []
    level = 1
    for n in range(DIVISOR * 40):
        if n % 3 == 0:
            level ^= 1
        noise.append(level)

    dut = SerialLine(divisor=DIVISOR, data_bits=8)
    got = []

    async def sampler(ctx):
        while True:
            if ctx.get(dut.source.valid) and ctx.get(dut.source.ready):
                got.append(ctx.get(dut.source.payload))
            await ctx.tick()

    async def testbench(ctx):
        ctx.set(dut.source.ready, 1)
        await idle(ctx, dut.rx_i, 20)
        for bit in noise:
            ctx.set(dut.rx_i, bit)
            await ctx.tick()
        during = len(got)
        # Then the SAMD11 restores the pinmux and the line goes idle again.
        await idle(ctx, dut.rx_i, 20)
        await drive_frame(ctx, dut.rx_i, frame_bits(ord("z")))
        await idle(ctx, dut.rx_i, 4)

        checks.check(
            "a JTAG-rate burst on the shared pin produces no characters  (#113)",
            during == 0,
            f"the receiver framed {during} byte(s) out of line noise: "
            f"{got[:during]}")
        checks.check(
            "and the port works again as soon as the line is quiet",
            got[during:] == [ord("z")],
            f"expected the character sent after the burst, received "
            f"{got[during:]}")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(sampler, background=True)
    sim.add_testbench(testbench)
    sim.run()


def count_pulses(frames, *, ready, bits_before=20, divisor=DIVISOR):
    """Drive frames into a SerialLine and count what it reports losing.

    `ready` is what the downstream sink says: 1 for a sink with room, 0 for one
    that is full, which is the condition that destroys a byte here.
    """
    dut = SerialLine(divisor=divisor, data_bits=8)
    seen = {"overrun": 0, "frame_error": 0}

    async def sampler(ctx):
        while True:
            seen["overrun"] += ctx.get(dut.overrun)
            seen["frame_error"] += ctx.get(dut.frame_error)
            await ctx.tick()

    async def testbench(ctx):
        ctx.set(dut.source.ready, ready)
        await idle(ctx, dut.rx_i, bits_before, divisor)
        for frame in frames:
            await drive_frame(ctx, dut.rx_i, frame, divisor)
        ctx.set(dut.rx_i, 1)
        await ctx.tick().repeat(4 * divisor)
        seen["frame_errors"] = ctx.get(dut.frame_errors)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(sampler, background=True)
    sim.add_testbench(testbench)
    sim.run()
    return seen


def run_error_report_checks(checks, verbose):
    """What the line loses has to reach the 16550, or LSR is a comfortable lie.

    `source.valid` is one cycle and does not wait for `source.ready`, so a byte
    offered to a full sink is destroyed HERE. The 16550 downstream cannot see it:
    its own `sink` backpressures, so a full FIFO there is a stall and not a loss.
    These two outputs are the only record, and they become LSR.OE and LSR.FE.
    """
    seen = count_pulses([frame_bits(b) for b in b"abc"], ready=0)
    checks.check(
        "a byte delivered to a sink with no room is reported as an overrun",
        seen["overrun"] == 3,
        f"three characters were destroyed and {seen['overrun']} were reported. "
        f"Unreported, they are bytes that nothing anywhere ever mentions.")

    seen = count_pulses([frame_bits(b) for b in b"abc"], ready=1)
    checks.check(
        "and a sink that takes them reports none",
        seen["overrun"] == 0,
        f"{seen['overrun']} overrun(s) reported for a receiver that lost "
        f"nothing; LSR.OE would cry wolf on every busy moment.")

    seen = count_pulses([frame_bits(0x5a, stop=0)], ready=1)
    checks.check(
        "a framing error is reported once, alongside the count it increments",
        seen["frame_error"] == 1 and seen["frame_errors"] == 1,
        f"frame_error pulsed {seen['frame_error']} time(s), the counter reads "
        f"{seen.get('frame_errors')}")


def run_structural_checks(checks, verbose):
    """The synchroniser is a structural property; simulation cannot see it."""
    import inspect
    from amaranth.lib.cdc import FFSynchronizer

    source = inspect.getsource(SerialLine.elaborate)
    checks.check(
        "SerialLine puts an FFSynchronizer between the pad and the receiver",
        "FFSynchronizer(self.rx_i" in source,
        "the receive pad reaches AsyncSerialRX without a synchroniser, which "
        "is the metastability half of #113. AsyncSerialRX only supplies one "
        "when constructed with a `pins` argument, and this design does not "
        "pass one.")

    soc = (ROOT / "ecp5-test" / "riscv" / "vexii_hello_soc.py").read_text()
    checks.check(
        "and the SoC does not hand a raw pad to an AsyncSerial",
        "rx.i.eq(apollo_pins.rx.i)" not in soc,
        "vexii_hello_soc.py still wires apollo_pins.rx.i straight into a PHY.")

    # A configuration that would let a frame cut short by disarming survive into
    # the next arming must be refused rather than accepted and silently wrong.
    #
    # The constructor is expected to raise, which leaves a half-built
    # Elaboratable that is never elaborated -- so amaranth's use-tracking warns
    # at collection time about a design bug that is deliberate here. Silencing
    # it for the duration of the call keeps the warning working everywhere else.
    import gc
    from amaranth.hdl import Elaboratable
    Elaboratable._MustUse__silence = True
    try:
        SerialLine(divisor=DIVISOR, data_bits=8, idle_bits=8)
        rejected = False
    except ValueError:
        rejected = True
    finally:
        # Collect while still silenced. The warning fires from __del__, and the
        # traceback of the exception we wanted keeps the half-built object alive
        # past the end of this block otherwise -- so it would surface at
        # interpreter shutdown, long after the flag was put back.
        gc.collect()
        Elaboratable._MustUse__silence = False
    checks.check(
        "an idle_bits shorter than a frame is refused",
        rejected,
        "SerialLine accepted idle_bits=8 against a 10-bit frame; a truncated "
        "garbage frame would then be delivered on the next arming.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every simulated byte and pad edge")
    args = parser.parse_args()

    checks = Checks(emit)

    emit("serial_line.SerialLine -- transmit")
    run_tx_checks(checks, args.verbose)
    run_tx_burst_check(checks, args.verbose)
    emit()

    emit("serial_line.SerialLine -- receive")
    run_rx_checks(checks, args.verbose)
    run_jtag_noise_check(checks, args.verbose)
    emit()

    emit("serial_line.SerialLine -- what it reports losing")
    run_error_report_checks(checks, args.verbose)
    emit()

    emit("serial_line.SerialLine -- structure")
    run_structural_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
