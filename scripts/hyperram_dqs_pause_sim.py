#!/usr/bin/env python3
#
# Does DQSBUFM's read tap move only inside a PAUSE window, and only when the bus is idle? #349.
# SPDX-License-Identifier: BSD-3-Clause

"""The one rule Lattice states twice, checked against `ReadClkSelWindow`.

    ./scripts/hyperram_dqs_pause_sim.py
    ./scripts/hyperram_dqs_pause_sim.py --cycles 20000 --seed 7

## The rule

FPGA-TN-02035-1.3 s6.2.4 p.36:

    When any of READCLKSEL2/1/0 is changed at any time after a system reset, the
    PAUSE input to DQSBUFM must be asserted before 4T of the change and remain
    asserted for another 4T after the change to avoid glitches and malfunction.

and again on the port itself, Figure 6.7: *"can be changed only during PAUSE
assertion"*. T is one memory clock; this PHY emits 2 CK per `sync` cycle at 4:1
gearing, so 4T is **2 `sync` cycles** either side. `READCLKSEL_PAUSE_CYCLES` is 4.

## What it checks

1. `applied` changes only on a cycle with PAUSE held for the required run before
   AND after -- the rule as written, not a paraphrase of it.
2. `applied` never changes while the bus is not idle. PAUSE stops DQSW/DQSR90, so
   a change taken mid-burst corrupts the burst in flight rather than the next one.
3. Every commanded value eventually reaches `applied` once the bus goes idle.
   Without this the first two pass trivially on a tap that never moves.

## The negative control

The same three checks run against **the wiring as it was before #349** -- the
caller's value straight onto the primitive -- and must FAIL. A check that a
pass-through satisfies is not watching the thing it names. The run exits non-zero
if the control passes.

Log -> `tmp/logs/hyperram_dqs_pause_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from amaranth import Elaboratable, Module, Signal  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from peripherals.hyperram_dqs_phy import (  # noqa: E402
    READCLKSEL_PAUSE_CYCLES, ReadClkSelWindow)

LOG = ROOT / "tmp" / "logs" / "hyperram_dqs_pause_sim.log"

# 4T at 4:1 gearing, in `sync` cycles. Derived from the gearing, not from the
# number the PHY happens to hold: the check must fail if that number is lowered
# past the rule rather than move with it.
CK_PER_SYNC = 2
REQUIRED_SYNC = -(-4 // CK_PER_SYNC)      # ceil(4T / 2 CK per cycle) = 2

# The bus is idle about two thirds of the time in the stimulus, in runs long
# enough that a change can be held off across one. Not a timing figure -- it is
# a shape chosen so the "held off while busy" path is exercised at all.
BUSY_RUN = (3, 25)
IDLE_RUN = (3, 25)

# Cycles of held `want` and held idle at the end of every stimulus, so check 3 is
# `applied == want` and not a guess about whether there was time. One window is
# HOLD plus 2 x (READCLKSEL_PAUSE_CYCLES + 1) = 11 cycles; 32 is ~3x that.
QUIESCE = 32

# The value commanded at the start of the quiesce tail. Any value the frozen
# control does not hold; that control leaves `applied` at 0, so 0 is the one
# choice that would make check 3 unfalsifiable.
FINAL_WANT = 0b0101


class PassThrough(Elaboratable):
    """The wiring before #349: the caller's value straight onto the primitive.

    Same ports as `ReadClkSelWindow` so one checker can score both. This is the
    negative control, and it must fail.
    """

    def __init__(self):
        self.want = Signal(4)
        self.idle = Signal()
        self.ready = Signal()
        self.applied = Signal(4)
        self.pause = Signal()

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.applied.eq(self.want)
        # A `sync` register so the domain exists for the simulator. The control
        # has no sequential behaviour -- that is the point of it.
        m.d.sync += Signal(name="unused").eq(0)
        return m


class Frozen(PassThrough):
    """A tap that never moves. The control for check 3.

    Checks 1 and 2 are both about what happens WHEN the tap moves, so a tap that
    never moves satisfies them vacuously -- which is the failure a window held in
    permanent PAUSE would have. This must fail, and only on check 3.
    """

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.pause.eq(1)
        m.d.sync += Signal(name="unused").eq(0)
        return m


def trace(dut, cycles, seed):
    """Run the stimulus and return one record per cycle."""
    rows = []
    rng = random.Random(seed)

    async def stimulus(ctx):
        ctx.set(dut.ready, 1)
        want = 0b010
        busy_left, idle_left = 0, rng.randint(*IDLE_RUN)
        ctx.set(dut.want, want)
        for _ in range(cycles):
            if idle_left:
                idle_left -= 1
                idle = 1
                if not idle_left:
                    busy_left = rng.randint(*BUSY_RUN)
            else:
                busy_left -= 1
                idle = 0
                if not busy_left:
                    idle_left = rng.randint(*IDLE_RUN)
            ctx.set(dut.idle, idle)
            # A new tap about every twelfth cycle, deliberately without regard
            # to whether the bus is busy -- that is exactly what a CSR write
            # and a gateware sweep both do.
            if rng.random() < 1 / 12:
                want = rng.randrange(16)
                ctx.set(dut.want, want)
            rows.append(dict(want=ctx.get(dut.want), idle=ctx.get(dut.idle),
                             applied=ctx.get(dut.applied),
                             pause=ctx.get(dut.pause)))
            await ctx.tick()

        # The quiesce tail: one last value commanded, bus idle throughout.
        ctx.set(dut.idle, 1)
        ctx.set(dut.want, FINAL_WANT)
        for _ in range(QUIESCE):
            rows.append(dict(want=ctx.get(dut.want), idle=ctx.get(dut.idle),
                             applied=ctx.get(dut.applied),
                             pause=ctx.get(dut.pause)))
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(stimulus)
    sim.run()
    return rows


def score(rows, log, tag):
    """The three checks. Returns the list of violations, empty when clean."""
    bad = []
    changes = 0
    for i in range(1, len(rows)):
        if rows[i]["applied"] == rows[i - 1]["applied"]:
            continue
        changes += 1
        before = [r["pause"] for r in rows[max(0, i - REQUIRED_SYNC):i]]
        after = [r["pause"] for r in rows[i:i + REQUIRED_SYNC]]
        if len(before) < REQUIRED_SYNC or not all(before):
            bad.append(f"cycle {i}: tap moved to {rows[i]['applied']:#05b} with "
                       f"PAUSE {before} in the {REQUIRED_SYNC} cycles before it")
        elif not all(after) or len(after) < REQUIRED_SYNC:
            bad.append(f"cycle {i}: tap moved to {rows[i]['applied']:#05b} with "
                       f"PAUSE {after} in the {REQUIRED_SYNC} cycles after it")
        if not rows[i - 1]["idle"]:
            bad.append(f"cycle {i}: tap moved while the bus was NOT idle")

    # 3. It has to actually move, or the first two are satisfied by a wire that
    #    never changes. Scored at the end of the quiesce tail, where the value
    #    has been held and the bus idle for QUIESCE cycles.
    if rows[-1]["applied"] != rows[-1]["want"]:
        bad.append(f"the tap never reached the commanded value: want "
                   f"{rows[-1]['want']:#05b}, applied {rows[-1]['applied']:#05b} "
                   f"after {QUIESCE} idle cycles")

    log.info("  %-14s %5d cycles, %3d tap changes, %d violation(s)",
             tag, len(rows), changes, len(bad))
    for line in bad[:6]:
        log.info("      %s", line)
    if len(bad) > 6:
        log.info("      ... and %d more", len(bad) - 6)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cycles", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=8,
                    help="how many stimulus streams; one cannot separate a rule "
                         "that holds from one this stimulus never reached")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    log.info("PAUSE either side of a READCLKSEL change: %d `sync` cycles "
             "required (4T at %d CK per cycle), %d held",
             REQUIRED_SYNC, CK_PER_SYNC, READCLKSEL_PAUSE_CYCLES)
    log.info("")

    failures = []
    seeds = [args.seed + n for n in range(args.seeds)]
    for seed in seeds:
        if score(trace(ReadClkSelWindow(), args.cycles, seed), log,
                 f"window s{seed}"):
            failures.append(f"ReadClkSelWindow violated the rule at seed {seed}")

    # Both controls on ONE seed: they fail on every cycle they act, so a second
    # stream adds nothing.
    if not score(trace(PassThrough(), args.cycles, args.seed), log, "ctrl live"):
        failures.append("the LIVE-TAP CONTROL passed: the pre-#349 wiring -- the "
                        "caller's value straight onto DQSBUFM -- satisfied these "
                        "checks, so they are not watching the rule they name")
    if not score(trace(Frozen(), args.cycles, args.seed), log, "ctrl frozen"):
        failures.append("the FROZEN-TAP CONTROL passed: a tap that never moves "
                        "satisfied these checks, so a window stuck in PAUSE "
                        "would read as compliance")

    log.info("")
    log.info("%d stimulus stream(s) of %d cycles against the window; two "
             "controls, both of which must fail", len(seeds), args.cycles)
    for line in failures:
        log.error("FAIL %s", line)
    if not failures:
        log.info("the tap moves only inside a PAUSE window and only when idle, "
                 "and the pre-#349 wiring does not")
    log.info("log -> %s", LOG.relative_to(ROOT))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
