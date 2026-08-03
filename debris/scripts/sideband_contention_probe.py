#!/usr/bin/env python3
#
# Probe the FPGA_ADV sideband responder's line-ownership timing, for issue #88.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures when the responder claims and releases the shared FPGA_ADV wire, and
how long a push-pull driver conflict lasts when the master overlaps it.

Issue #88 says both ends drive push-pull and collision is avoided only by
timing. That can only be settled by knowing exactly which cycles the pad's
output enable is asserted for, relative to the bits on the line. This script
reports those boundaries and the measured conflict durations rather than
asserting anything, so the numbers in the report come from the simulator and not
from reading the FSM.

Probes `SidebandResponder` -- the test bitstream's responder, whose 18-byte POWER
reply is the longest ownership window in the protocol and therefore the worst
case. `ecp5-test/sideband_link.py` shares the pad discipline (open-drain, `oe`
tracking the bit) and holds the line for at most four bytes, so this bounds it
too.

Findings go to ./tmp/logs/sideband_contention_probe.log as well as stdout.
"""

import logging
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "repos" / "apollo"))

from amaranth.hdl import Module                      # noqa: E402
from amaranth.sim import Simulator                   # noqa: E402

from apollo_fpga.gateware.sideband import (          # noqa: E402
    SidebandResponder, CMD_STATUS, CMD_POWER,
)

# A 1 MHz clock with an exact divisor of 16 keeps the traces short enough to
# read and makes every boundary land on a whole cycle, so the reported numbers
# are exact rather than rounded.
CLK_HZ = 1e6
BAUD = 62500
DIVISOR = int(CLK_HZ // BAUD)

# Real-world scale, for converting cycle counts into microseconds. The link
# ships at 230400 baud (firmware ADV_UART_BAUD, and the sideband test
# bitstream's responder), so one bit is 1e6/230400 us.
SHIPPING_BAUD = 230400
US_PER_BIT_SHIPPING = 1e6 / SHIPPING_BAUD

LOG_PATH = WORKSPACE / "tmp" / "logs" / "sideband_contention_probe.log"


def configure_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="w"),
                  logging.StreamHandler(sys.stdout)],
    )


def trace(command, turnaround_us=40, open_drain=False,
          cycles_after=DIVISOR * 10 * 26):
    """Send one command; return a per-cycle trace of (pad_o, pad_oe)."""
    dut = SidebandResponder(clk_freq_hz=CLK_HZ, baud=BAUD,
                            turnaround_us=turnaround_us,
                            open_drain=open_drain)
    m = Module()
    m.submodules.dut = dut

    sim = Simulator(m)
    sim.add_clock(1 / CLK_HZ)

    samples = []
    marks = {}

    async def drive(ctx):
        ctx.set(dut.rx, 1)
        for _ in range(DIVISOR * 2):
            await ctx.tick()
            samples.append((ctx.get(dut.pad_o), ctx.get(dut.pad_oe)))

        marks["command_start"] = len(samples)
        for bit in [0] + [(command >> i) & 1 for i in range(8)] + [1]:
            ctx.set(dut.rx, bit)
            for _ in range(DIVISOR):
                await ctx.tick()
                samples.append((ctx.get(dut.pad_o), ctx.get(dut.pad_oe)))
        # End of the command's stop bit: everything after this is the
        # responder's turnaround plus its reply.
        marks["command_end"] = len(samples)

        ctx.set(dut.rx, 1)
        for _ in range(cycles_after):
            await ctx.tick()
            samples.append((ctx.get(dut.pad_o), ctx.get(dut.pad_oe)))

    sim.add_testbench(drive)
    sim.run()
    return samples, marks


def spans(flags):
    """Contiguous runs of truth in `flags`, as (start, end) cycle pairs."""
    out = []
    start = None
    for cycle, flag in enumerate(flags):
        if flag and start is None:
            start = cycle
        elif not flag and start is not None:
            out.append((start, cycle))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def report_ownership(name, command, turnaround_us=40, open_drain=False):
    """How long the pad's driver is enabled, and what it drives while enabled."""
    samples, marks = trace(command, turnaround_us=turnaround_us,
                           open_drain=open_drain)
    end = marks["command_end"]

    driven = spans([oe for _, oe in samples])
    low_on_wire = spans([(not o) and oe for o, oe in samples])
    driven_high = spans([o and oe for o, oe in samples])

    logging.info("--- %s (turnaround %d us, %s) ---", name, turnaround_us,
                 "open-drain" if open_drain else "push-pull")
    logging.info("command stop bit ends at cycle %d", end)

    if not driven:
        logging.info("pad driver never enabled")
        return

    total_driven = sum(e - s for s, e in driven)
    total_high = sum(e - s for s, e in driven_high)
    logging.info("driver enabled for %d cycles total across %d span(s)",
                 total_driven, len(driven))
    logging.info("of which DRIVEN HIGH for %d cycles (%.2f bit times)",
                 total_high, total_high / DIVISOR)
    if total_high == 0:
        logging.info("driver only ever pulls LOW -- two such drivers cannot "
                     "contend")

    first = driven[0]
    logging.info("driver first enabled at cycle %d (+%d cycles = %.2f bit "
                 "times after the command's stop bit)",
                 first[0], first[0] - end, (first[0] - end) / DIVISOR)

    if low_on_wire:
        first_low = low_on_wire[0][0]
        logging.info("first LOW on the wire at cycle %d (+%d cycles)",
                     first_low, first_low - end)
        lead = first_low - first[0]
        logging.info("driver enabled %d cycles (%.2f bit times) BEFORE the "
                     "first low -- driving HIGH for that whole window",
                     lead, lead / DIVISOR)
        if lead > 0 and not open_drain:
            logging.warning("push-pull: %.2f bit times = %.1f us at %d baud of "
                            "driven-high before the start bit, during which "
                            "the master's own driver may still be enabled",
                            lead / DIVISOR,
                            (lead / DIVISOR) * US_PER_BIT_SHIPPING,
                            SHIPPING_BAUD)

        last_low_end = low_on_wire[-1][1]
        stop_end = last_low_end + DIVISOR
        release = driven[-1][1]
        logging.info("final stop bit ends at cycle %d; driver released at %d "
                     "(%+d cycles)", stop_end, release, release - stop_end)
        if release < stop_end and not open_drain:
            logging.warning("driver released BEFORE the final stop bit was "
                            "held for its full period -- the reply is "
                            "truncated on the wire")
        elif release < stop_end:
            logging.info("open-drain: releasing before the stop bit's end is "
                         "correct -- a high bit IS the released state, and the "
                         "pull-ups hold the line for its full period")


def measure_conflict(overlap_bits, turnaround_us=40, open_drain=False,
                     master_open_drain=False):
    """Overlap a master transmission with the reply; count shorted cycles.

    Models the shared wire as it is: two drivers, each with its own value, its
    own output enable, and its own drive style.

    The damaging condition is specifically one end driving HIGH into the other
    end driving LOW -- that is the low-impedance path through two output stages.
    Two ends pulling LOW together is just a low and harms nothing, which is the
    whole argument for open-drain. So the count that matters is not "both
    enabled" but "both enabled AND one of them is sourcing current into the
    other's sink".

    `overlap_bits` is how many bit times after its own command's stop bit the
    master re-enables its driver -- i.e. how early it gives up waiting and
    retries, which is the #99 timeout-retry case.
    """
    dut = SidebandResponder(clk_freq_hz=CLK_HZ, baud=BAUD,
                            turnaround_us=turnaround_us,
                            open_drain=open_drain)
    m = Module()
    m.submodules.dut = dut

    sim = Simulator(m)
    sim.add_clock(1 / CLK_HZ)

    result = {"both_enabled": 0, "shorted": 0}

    async def drive(ctx):
        def account(master_bit):
            """One cycle of the shared wire, with both drivers considered."""
            fpga_oe = ctx.get(dut.pad_oe)
            fpga_o = ctx.get(dut.pad_o)

            # The master's driver, in the style being modelled. Open-drain means
            # it is only enabled for a low bit.
            master_oe = (master_bit == 0) if master_open_drain else True
            master_o = master_bit

            if not (fpga_oe and master_oe):
                return
            result["both_enabled"] += 1
            # A short needs a source and a sink: one end high, the other low.
            if fpga_o != master_o:
                result["shorted"] += 1

        # Master idle, driver released.
        ctx.set(dut.rx, 1)
        for _ in range(DIVISOR * 2):
            await ctx.tick()

        # Master transmits the command. Apollo bit-bangs PA09 as a plain GPIO
        # output, so today its driver is enabled for the whole frame.
        for bit in [0] + [(CMD_STATUS >> i) & 1 for i in range(8)] + [1]:
            ctx.set(dut.rx, bit)
            for _ in range(DIVISOR):
                await ctx.tick()
        ctx.set(dut.rx, 1)

        # Master releases, waits `overlap_bits`, then retries -- landing on top
        # of a reply that is still going out.
        for _ in range(int(overlap_bits * DIVISOR)):
            await ctx.tick()

        # The retry, with the master driving and the responder possibly also
        # driving.
        for bit in [0] + [(CMD_STATUS >> i) & 1 for i in range(8)] + [1]:
            ctx.set(dut.rx, bit)
            for _ in range(DIVISOR):
                await ctx.tick()
                account(bit)

    sim.add_testbench(drive)
    sim.run()
    return result


def report_conflicts(open_drain=False, master_open_drain=False):
    logging.info("--- retry overlap: FPGA %s, master %s ---",
                 "open-drain" if open_drain else "push-pull",
                 "open-drain" if master_open_drain else "push-pull")
    worst = 0
    for overlap_bits in (1, 2, 4, 8, 12):
        r = measure_conflict(overlap_bits, open_drain=open_drain,
                             master_open_drain=master_open_drain)
        worst = max(worst, r["shorted"])
        logging.info("retry %2d bit times after the command: %3d cycles with "
                     "both drivers enabled, %3d SHORTED high-into-low "
                     "(%.1f us at %d baud)",
                     overlap_bits, r["both_enabled"], r["shorted"],
                     (r["shorted"] / DIVISOR) * US_PER_BIT_SHIPPING,
                     SHIPPING_BAUD)
    if worst == 0:
        logging.info("no shorted cycles in any overlap tested -- contention "
                     "impossible in this combination")
    else:
        logging.warning("worst case %d shorted cycles (%.1f us at %d baud) of "
                        "a driven high into a driven low", worst,
                        (worst / DIVISOR) * US_PER_BIT_SHIPPING, SHIPPING_BAUD)


def main():
    configure_logging()
    logging.info("FPGA_ADV line-ownership probe: clk %.0f Hz, baud %d, "
                 "divisor %d", CLK_HZ, BAUD, DIVISOR)
    logging.info("cycle counts scale to the shipping link by treating "
                 "%d cycles as one bit time (%.2f us at %d baud)",
                 DIVISOR, US_PER_BIT_SHIPPING, SHIPPING_BAUD)

    report_ownership("CMD_STATUS (2-byte reply)", CMD_STATUS)
    report_ownership("CMD_POWER (18-byte reply)", CMD_POWER)
    # 25 us is the measured failure floor recorded in the responder's comments;
    # trace it so the pre-start-bit driven-high window is visible at the
    # tightest setting anyone has run.
    report_ownership("CMD_STATUS, short turnaround", CMD_STATUS,
                     turnaround_us=25)
    report_ownership("CMD_STATUS, open-drain", CMD_STATUS, open_drain=True)

    # All four drive-style combinations, because the answer to #88 depends on
    # both ends and fixing only one end does not remove the short.
    report_conflicts(open_drain=False, master_open_drain=False)
    report_conflicts(open_drain=True, master_open_drain=False)
    report_conflicts(open_drain=False, master_open_drain=True)
    report_conflicts(open_drain=True, master_open_drain=True)

    logging.info("log written to %s", LOG_PATH)


if __name__ == "__main__":
    main()
