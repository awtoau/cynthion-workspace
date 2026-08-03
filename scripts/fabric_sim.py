#!/usr/bin/env python3
#
# Simulate the fabric test's round timing against the golden model.
# See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks that the gateware's signature is the golden model's value, in simulation,
before anything is built or loaded.

This exists to remove one specific way the hardware experiment could produce a
false negative. The design samples the XOR of 100 block states through a
pipelined tree, at a round boundary, while the blocks are simultaneously being
reloaded with their seeds. An off-by-one anywhere in that -- the counter's
phase, the tree's depth, the reload's timing relative to the sample -- gives a
stable, repeatable, *wrong* signature. On hardware that is indistinguishable
from broken silicon, and it would be reported as broken silicon.

So the same design is elaborated with a small round and a small block count and
run in Amaranth's simulator, and its signature is compared against
`fabric_golden.golden()` for the same parameters. A small round is legitimate
here because the property under test is the timing relationship, which does not
depend on how many cycles the round contains.

If this disagrees, the bug is in the gateware or the model, and the hardware
result would have been meaningless. It runs in seconds.

    ./scripts/fabric_sim.py
    ./scripts/fabric_sim.py --round-bits 8 --blocks 7
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_sim.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from amaranth.sim import Simulator

from fabric.fabric_gateware import FabricTest, REG_SIGNATURE
from fabric_golden import golden


def run(blocks, round_bits, rounds, expected):
    """Returns the list of signatures the simulated design produced.

    `platform is None` suppresses the LED block; the JTAG register interface
    elaborates without requested pins because it hangs off the JTAG primitive.
    What the simulator needs beyond that is a handle on the signature signal,
    which is reachable through the register map rather than through JTAG
    shifting -- far less machinery for the same observation.
    """
    dut = FabricTest(blocks=blocks, round_bits=round_bits, golden=expected,
                     simulate=True)
    module = dut.elaborate(None)
    # Amaranth warns when an Elaboratable is constructed and never handed to a
    # Fragment. Here `elaborate` was called directly so the signals could be
    # reached; marking it used says so rather than leaving a warning that looks
    # like a bug in the design.
    dut._MustUse__used = True

    # One round is `1 << round_bits` clocks; the tree adds a few cycles of
    # latency before the value lands. Running `rounds + 1` full rounds plus a
    # margin guarantees every requested signature has been latched. Both are
    # cycle counts, not wall-clock waits.
    cycles = (rounds + 1) * (1 << round_bits) + 64

    # One entry per round the design *counted*, not per distinct value. Every
    # round produces the same signature by design, so deduplicating would hide
    # the very thing being checked: that later rounds recompute it rather than
    # the counter advancing while the signature register sits stale.
    seen = []

    async def testbench(ctx):
        previous = 0
        for _ in range(cycles):
            await ctx.tick()
            count = ctx.get(dut.rounds)
            if count != previous:
                seen.append(ctx.get(dut.signature))
                previous = count

    sim = Simulator(module)
    sim.add_clock(1e-6, domain="sync")
    sim.add_testbench(testbench)
    sim.run()
    return seen


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blocks", type=int, default=8,
                        help="small by default; the property under test is the "
                             "timing, which does not depend on the count")
    parser.add_argument("--round-bits", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        cycles = 1 << args.round_bits
        emit(f"simulating {args.blocks} blocks, round 2**{args.round_bits} "
             f"= {cycles} cycles, {args.rounds} rounds")

        expected = golden(args.blocks, cycles)
        emit(f"golden model: {expected:#010x}")

        seen = run(args.blocks, args.round_bits, args.rounds, expected)
        emit(f"simulated signatures: "
             f"{', '.join(f'{v:#010x}' for v in seen) or 'none'}")

        if len(seen) < args.rounds:
            emit(f"FAIL -- only {len(seen)} rounds completed, wanted "
                 f"{args.rounds}; the round counter is not advancing")
            return 1

        # Every round must give the same value, because each round restarts
        # from the seeds. A drifting value means the reload is not working and
        # the hardware self-check would compare against a constant that only
        # ever matched once.
        if len(set(seen)) != 1:
            emit(f"FAIL -- signatures differ between rounds: "
                 f"{len(set(seen))} distinct values, so the seed reload is "
                 f"not restoring state")
            return 1

        if seen[0] != expected:
            emit(f"FAIL -- hardware {seen[0]:#010x} != model {expected:#010x}")
            emit("  the round timing and the model disagree; a hardware run "
                 "would report broken silicon that is not broken")
            return 1

        emit(f"PASS -- {len(seen)} rounds, all {expected:#010x}")
        emit(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
