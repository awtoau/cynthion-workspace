#!/usr/bin/env python3
#
# Drive a SW-DP written from the specification at the SWD host, and check the wire.
# SPDX-License-Identifier: BSD-3-Clause

"""
`SwdHost` against a target model built from ARM IHI 0074E (ADIv6.0) chapter B4.

The model is written from the specification's own rules rather than from the
design under test:

  * it samples SWDIO on the RISING edge of SWCLK and changes what it drives on
    the RISING edge -- B4.3.1, the only edge that document names
  * it counts an eight-bit packet request, `turnaround` clocks of nobody driving,
    three ACK bits, and a data phase whose position differs between a read and a
    write -- B4.2
  * it checks the request's even parity over APnDP, RnW and A[2:3] itself, so a
    host that computed parity over the whole eight bits is caught by the target
    rather than by an expectation written next to the host

What is checked:

  * a read returns RDATA and OK, and the parity bit covers the 32 data bits alone
  * a write puts WDATA on the wire, after the turnaround a write has and a read
    does not
  * ACK is LSB-first: FAULT reads 0b100, which is the check that catches a
    decoder copied from table B4-1's transmission-order digits
  * WAIT, FAULT and an undefined acknowledge end the transaction with no data
    phase
  * host and target never drive together, in any transaction
  * a line reset is 50 clocks high followed by idle cycles low
  * the request's parity bit takes both values, so a constant cannot pass

## The negative controls

Four mutations, each RUN, each asserting that the corruption is REPORTED:

  * **turnaround** -- a target with `DLCR.TURNROUND` at 2 against a host at 1.
    The acknowledge is no longer OK, which is the whole point: a turnaround
    disagreement is a shifted stream, not a fault line.
  * **parity** -- the target inverts the RDATA parity bit. `parity_error` rises
    and `rdata` is still delivered.
  * **acknowledge** -- the target answers 0b011, which no response uses. The host
    reports exactly that and runs no data phase.
  * **contention** -- the target holds the line one clock past its last ACK bit.
    The monitor that reports zero contention in every other run reports it here.

Output goes to the console and to tmp/logs/dev.log.
"""

import argparse
import sys
import warnings
from pathlib import Path

from amaranth.hdl import Fragment, UnusedElaboratable
from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from peripherals.swd import (ACK_FAULT, ACK_OK, ACK_WAIT,  # noqa: E402
                             DATA_BITS, IDLE_CYCLES, LINE_RESET_CLOCKS,
                             MIN_DIVISOR, REQUEST_BITS, SPEED_DIVISORS,
                             SwdHost)

warnings.filterwarnings("ignore", category=UnusedElaboratable)


# The `swd` domain, and speed index 0 -- divisor 2, SWCLK 52.5 MHz. Simulating at
# the top of the table is the point: it is the case with no cycle to spare
# between the last ACK bit and the first data bit, and a slower index would hide
# a decision state that does not fit.
CLK_HZ = 105_000_000
DIVISOR = SPEED_DIVISORS[0]

def cycle_limit(divisor):
    """Domain cycles one transaction may take: the longest is a line reset, 50
    clocks plus 8 idle, and 1.25x of that names a wedged FSM rather than hanging
    the simulation."""
    return int((LINE_RESET_CLOCKS + IDLE_CYCLES) * divisor * 1.25)

# Set by `--soak`: the request-parity sweep over every A[3:2] rather than the
# two values that make the parity bit take both states.
SOAK = False


def even_parity(value, width):
    return bin(value & ((1 << width) - 1)).count("1") & 1


class Target:
    """A SW-DP on the wire, from IHI 0074E B4. Rising edges only.

    Parameters
    ----------
    turnaround : int
        The target's own `DLCR.TURNROUND`. A run where this differs from the
        host's is the turnaround negative control.
    ack : int
        The acknowledge to send, ACK[0] in bit 0.
    rdata : int
        The read payload.
    flip_data_parity : bool
        Invert the RDATA parity bit -- the parity negative control.
    extra_drive : int
        Keep driving for this many clocks past the last bit the protocol gives
        the target -- the contention negative control.
    """

    def __init__(self, *, turnaround=1, ack=ACK_OK, rdata=0,
                 flip_data_parity=False, extra_drive=0):
        self.turnaround = turnaround
        self.ack = ack
        self.rdata = rdata
        self.flip_data_parity = flip_data_parity
        self.extra_drive = extra_drive

        # What the model observed, for the checks to read.
        self.request = []
        self.wdata_bits = []
        self.request_ok = None
        self.fields = {}

        self._drive = False
        self._value = 0
        self._schedule = {}
        self._sample_edges = set()

    def rising(self, edge, wire):
        """One rising edge of SWCLK: sample, then update what is driven."""
        if edge <= REQUEST_BITS:
            self.request.append(wire)
            if edge == REQUEST_BITS:
                self._parse_request()
        elif edge in self._sample_edges:
            self.wdata_bits.append(wire)

        if edge in self._schedule:
            self._drive, self._value = self._schedule[edge]

    def output(self):
        """What the target is driving right now: (driving, value)."""
        return self._drive, self._value

    def _parse_request(self):
        start, apndp, rnw, a2, a3, parity, stop, park = self.request
        self.fields = dict(start=start, apndp=apndp, rnw=rnw, a2=a2, a3=a3,
                           parity=parity, stop=stop, park=park)
        # B4.2.5: a protocol error is a bad Start, Stop, Park, or a parity bit
        # that does not match the four payload bits.
        self.request_ok = (start == 1 and stop == 0 and park == 1
                           and parity == (apndp ^ rnw ^ a2 ^ a3))

        # B4.2: the acknowledge follows one turnaround period; a read's data
        # phase follows the acknowledge with no turnaround, a write's with one.
        last = REQUEST_BITS + self.turnaround
        for index in range(3):
            self._schedule[last + 1 + index] = (True, (self.ack >> index) & 1)
        released = last + 4

        if self.ack == ACK_OK and rnw:
            bits = [(self.rdata >> index) & 1 for index in range(32)]
            parity_bit = even_parity(self.rdata, 32) ^ self.flip_data_parity
            for index, bit in enumerate(bits + [parity_bit]):
                self._schedule[last + 4 + index] = (True, bit)
            released = last + 4 + DATA_BITS
        elif self.ack == ACK_OK and not rnw:
            # The turnaround at `released`, then 33 host-driven bits.
            self._sample_edges = set(range(released + 1,
                                           released + 1 + DATA_BITS))

        self._schedule[released + self.extra_drive] = (False, 0)


def run_transaction(dut, target, *, apndp=0, rnw=0, addr=0, wdata=0,
                    line_reset=False):
    """One transaction on the wire. Returns everything the checks look at."""
    seen = {"contention": 0, "edges": 0, "wire": [], "host_drove": [],
            "done": False, "ack": None, "rdata": None, "parity_error": None,
            "idle_swclk_low": True, "edge_cycles": []}

    async def bench(ctx):
        wire = 1
        ctx.set(dut.swdio_i, wire)
        ctx.set(dut.apndp, apndp)
        ctx.set(dut.rnw, rnw)
        ctx.set(dut.addr, addr)
        ctx.set(dut.wdata, wdata)
        ctx.set(dut.line_reset if line_reset else dut.start, 1)
        await ctx.tick()
        ctx.set(dut.start, 0)
        ctx.set(dut.line_reset, 0)

        previous_clock = 0
        edge = 0
        for cycle in range(cycle_limit(max(dut.speeds))):
            await ctx.tick()
            clock = ctx.get(dut.swclk_o)
            host_oe = ctx.get(dut.swdio_oe)
            host_o = ctx.get(dut.swdio_o)

            if clock and not previous_clock:
                edge += 1
                target.rising(edge, wire)
                seen["wire"].append(wire)
                seen["host_drove"].append(host_oe)
                seen["edge_cycles"].append(cycle)
            previous_clock = clock

            driving, value = target.output()
            if driving and host_oe:
                seen["contention"] += 1
            # Neither side driving holds the last level: the 100k pull-up of
            # B4.3.2 "can only be relied on to maintain the state of the wire".
            wire = host_o if host_oe else (value if driving else wire)
            ctx.set(dut.swdio_i, wire)

            if ctx.get(dut.done):
                seen.update(done=True, edges=edge,
                            ack=ctx.get(dut.ack),
                            rdata=ctx.get(dut.rdata),
                            parity_error=ctx.get(dut.parity_error))
                break

        # The clock parks low between transactions, which is what makes stopping
        # it legal after the idle cycles (B4.1.1).
        await ctx.tick()
        seen["idle_swclk_low"] = ctx.get(dut.swclk_o) == 0

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1 / CLK_HZ)
    sim.add_testbench(bench)
    sim.run()
    return seen


def host(turnaround=1, divisor=DIVISOR):
    return SwdHost(speeds=(divisor,), turnaround=turnaround)


def edges_for(*, data_phase, turnaround=1):
    """SWCLK cycles a transaction takes, counted from the phases of B4.2."""
    total = REQUEST_BITS + turnaround + 3 + IDLE_CYCLES
    return total + (DATA_BITS + turnaround if data_phase else turnaround)


def run(checks):
    #
    # A read that acknowledges OK.
    #
    payload = 0xDEADBEEF
    target = Target(ack=ACK_OK, rdata=payload)
    read = run_transaction(host(), target, rnw=1, apndp=1, addr=0b01)

    checks.check("a read completes and reports done", read["done"],
                 "the FSM never reached its idle cycles")
    checks.check("the target accepted the packet request", target.request_ok,
                 f"Start/Stop/Park or parity wrong: {target.fields}")
    checks.check(
        "the request carries APnDP, RnW and A[3:2] in wire order",
        (target.fields.get("apndp"), target.fields.get("rnw"),
         target.fields.get("a2"), target.fields.get("a3")) == (1, 1, 1, 0),
        f"A[2] goes on the wire before A[3]; got {target.fields}")
    checks.check("the read returns RDATA", read["rdata"] == payload,
                 f"got {read['rdata']:#010x}, expected {payload:#010x}")
    checks.check("OK is 0b001 as received", read["ack"] == ACK_OK,
                 f"got {read['ack']:#05b}")
    checks.check("the read payload's parity checks out",
                 read["parity_error"] == 0,
                 "even parity over the 32 data bits alone -- ACK is never "
                 "covered (B4.1.6)")
    checks.check(
        "a read is request, Trn, ACK, RDATA, Trn, idle -- and no other clocks",
        read["edges"] == edges_for(data_phase=True),
        f"{read['edges']} SWCLK cycles, expected "
        f"{edges_for(data_phase=True)}. A read has NO turnaround between ACK "
        f"and RDATA (B4.2.2) and one after it.")
    checks.check("host and target never drive together on a read",
                 read["contention"] == 0,
                 f"{read['contention']} cycles with both drivers on")
    checks.check("SWCLK parks low between transactions",
                 read["idle_swclk_low"])
    checks.check(
        "the idle cycles are clocked with the line low",
        all(bit == 0 for bit in read["wire"][-IDLE_CYCLES:]),
        f"last {IDLE_CYCLES} clocks were {read['wire'][-IDLE_CYCLES:]}; "
        f"B4.1.4 idles LOW")

    #
    # A write that acknowledges OK. The data phase is the host's, and it sits
    # one turnaround after the acknowledge -- the read's does not.
    #
    written = 0x0BADC0DE
    target = Target(ack=ACK_OK)
    write = run_transaction(host(), target, rnw=0, addr=0b10, wdata=written)
    bits = target.wdata_bits
    value = sum(bit << index for index, bit in enumerate(bits[:32]))

    checks.check("a write completes", write["done"])
    checks.check("the target received WDATA", value == written,
                 f"got {value:#010x}, expected {written:#010x} from {len(bits)} "
                 f"bits. A missing turnaround before WDATA shifts every bit.")
    checks.check("WDATA carries its even parity bit",
                 len(bits) == DATA_BITS and bits[32] == even_parity(written, 32),
                 f"{len(bits)} bits, parity {bits[32:]}")
    checks.check(
        "a write is request, Trn, ACK, Trn, WDATA, idle",
        write["edges"] == edges_for(data_phase=True),
        f"{write['edges']} SWCLK cycles, expected {edges_for(data_phase=True)}")
    checks.check("host and target never drive together on a write",
                 write["contention"] == 0,
                 f"{write['contention']} cycles with both drivers on")

    #
    # The request parity bit takes both values. A constant, or a parity taken
    # over all eight request bits, passes one of these and fails the other.
    #
    sweep = [(0, 0, 0b00), (1, 0, 0b00)] if not SOAK else [
        (apndp, rnw, addr)
        for apndp in (0, 1) for rnw in (0, 1) for addr in range(4)]
    for apndp, rnw, addr in sweep:
        probe = Target(ack=ACK_FAULT)
        run_transaction(host(), probe, apndp=apndp, rnw=rnw, addr=addr)
        expected = apndp ^ rnw ^ (addr & 1) ^ ((addr >> 1) & 1)
        checks.check(
            f"request parity is even over the four payload bits "
            f"(APnDP={apndp} RnW={rnw} A[3:2]={addr:#04b})",
            probe.fields.get("parity") == expected and probe.request_ok,
            f"parity bit {probe.fields.get('parity')}, expected {expected}")

    #
    # WAIT and FAULT: reported, and no data phase, because overrun detection is
    # not enabled from here (B4.2.3, B4.2.4).
    #
    for name, code in (("WAIT", ACK_WAIT), ("FAULT", ACK_FAULT)):
        target = Target(ack=code, rdata=0xFFFFFFFF)
        result = run_transaction(host(), target, rnw=1)
        checks.check(
            f"{name} is reported as {code:#05b}, LSB-first",
            result["ack"] == code,
            f"got {result['ack']:#05b}. Table B4-1 prints these in "
            f"transmission order, so a decoder copying its digits swaps OK "
            f"and FAULT.")
        checks.check(
            f"{name} ends the transaction with no data phase",
            result["edges"] == edges_for(data_phase=False),
            f"{result['edges']} SWCLK cycles, expected "
            f"{edges_for(data_phase=False)}")
        checks.check(f"no contention on a {name} response",
                     result["contention"] == 0)

    #
    # A line reset: 50 clocks high, then idle cycles low (B4.3.3).
    #
    target = Target()
    reset = run_transaction(host(), target, line_reset=True)
    high = reset["wire"][:LINE_RESET_CLOCKS]
    checks.check(
        "a line reset holds SWDIO high for 50 clocks then idles low",
        (reset["done"] and len(high) == LINE_RESET_CLOCKS and all(high)
         and all(bit == 0 for bit in reset["wire"][LINE_RESET_CLOCKS:])),
        f"{reset['edges']} clocks, "
        f"{sum(reset['wire'][:LINE_RESET_CLOCKS])} of the first 50 high")
    checks.check("the host drives the whole line reset",
                 all(reset["host_drove"]),
                 "a released line during a reset relies on the pull-up, which "
                 "B4.3.2 says takes many clock cycles to pull up")

    #
    # ---- the negative controls, each one run ----------------------------
    #
    # Turnaround: the target's DLCR.TURNROUND is 2 and the host's is 1, so every
    # target-driven bit lands one clock late.
    #
    target = Target(turnaround=2, ack=ACK_OK, rdata=payload)
    skewed = run_transaction(host(turnaround=1), target, rnw=1)
    checks.check(
        "NEGATIVE CONTROL: a turnaround disagreement is caught",
        skewed["ack"] != ACK_OK or skewed["rdata"] != payload,
        f"a target at TURNROUND=2 against a host at 1 returned ack "
        f"{skewed['ack']:#05b} and rdata {skewed['rdata']:#010x} -- if that "
        f"reads as a clean OK the checks above cannot see a shifted stream")

    #
    # Parity: the RDATA parity bit inverted, everything else correct.
    #
    target = Target(ack=ACK_OK, rdata=payload, flip_data_parity=True)
    bad_parity = run_transaction(host(), target, rnw=1)
    checks.check(
        "NEGATIVE CONTROL: an inverted RDATA parity bit is reported",
        bad_parity["parity_error"] == 1,
        "the payload was delivered with a wrong parity bit and nothing said so")
    checks.check(
        "and the corrupted read still delivers its data",
        bad_parity["rdata"] == payload and bad_parity["ack"] == ACK_OK,
        f"got {bad_parity['rdata']:#010x} ack {bad_parity['ack']:#05b}; "
        f"B4.1.6 leaves the retry to the debugger, so the data is reported "
        f"alongside the error rather than instead of it")

    #
    # Acknowledge: 0b011 is not a response any DP gives.
    #
    target = Target(ack=0b011, rdata=payload)
    undefined = run_transaction(host(), target, rnw=1)
    checks.check(
        "NEGATIVE CONTROL: an undefined acknowledge is reported verbatim",
        undefined["ack"] == 0b011,
        f"got {undefined['ack']:#05b}; a decoder that folds unknown responses "
        f"into OK or FAULT hides a mis-synchronised link")
    checks.check(
        "and an undefined acknowledge runs no data phase",
        undefined["edges"] == edges_for(data_phase=False),
        f"{undefined['edges']} SWCLK cycles, expected "
        f"{edges_for(data_phase=False)}")

    #
    # Contention: the target holds the line one clock past its last ACK bit,
    # into the turnaround the host is about to drive.
    #
    target = Target(ack=ACK_WAIT, extra_drive=2)
    clashing = run_transaction(host(), target, rnw=1)
    checks.check(
        "NEGATIVE CONTROL: a target that overstays is seen as contention",
        clashing["contention"] > 0,
        "the monitor that reports zero on every clean run reported zero here "
        "too, so it proves nothing about the turnaround")

    #
    # The speed table: an index picks the divisor at run time, and the period on
    # the wire is that divisor. A table that only ever built at one rate would
    # pass every check above.
    #
    for index, divisor in ((0, SPEED_DIVISORS[0]), (1, SPEED_DIVISORS[1])):
        target = Target(ack=ACK_OK, rdata=payload)
        timed = run_transaction(host(divisor=divisor), target, rnw=1)
        periods = {b - a for a, b in zip(timed["edge_cycles"],
                                         timed["edge_cycles"][1:])}
        checks.check(
            f"speed index {index} clocks SWCLK at domain/{divisor}",
            periods == {divisor} and timed["rdata"] == payload,
            f"SWCLK periods {sorted(periods)} domain cycles, expected "
            f"{{{divisor}}}, and rdata {timed['rdata']:#010x}",
            measurement=f"{CLK_HZ / divisor / 1e6:.3f} MHz SWCLK")

    checks.check(
        "the top of the table is at least the 50 MHz asked for",
        CLK_HZ / SPEED_DIVISORS[0] >= 50e6,
        f"index 0 is {CLK_HZ / SPEED_DIVISORS[0] / 1e6:.3f} MHz")
    checks.check(
        "and the table is strictly slower down the index",
        all(a < b for a, b in zip(SPEED_DIVISORS, SPEED_DIVISORS[1:])),
        f"{SPEED_DIVISORS}: an index that is not monotonic makes a firmware "
        f"speed search oscillate")

    #
    # The constructor's two refusals.
    #
    too_fast = False
    try:
        SwdHost(speeds=(MIN_DIVISOR - 1,))
    except ValueError:
        too_fast = True
    checks.check(
        "a divisor below two raises at elaboration",
        too_fast,
        "a register-generated clock needs a cycle high and a cycle low")

    bad_turnaround = False
    try:
        SwdHost(speeds=(DIVISOR,), turnaround=5)
    except ValueError:
        bad_turnaround = True
    checks.check(
        "a turnaround DLCR.TURNROUND cannot encode raises at elaboration",
        bad_turnaround,
        "table B2-5 encodes 1 to 4 clocks and nothing else")


def main():
    global SOAK
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--soak", action="store_true",
                        help="sweep every APnDP/RnW/A[3:2] request rather than "
                             "the two that make the parity bit take both values")
    args = parser.parse_args()
    SOAK = args.soak

    emit("SWD host on the Type-C sideband")
    checks = Checks(emit)
    run(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
