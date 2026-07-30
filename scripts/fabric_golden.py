#!/usr/bin/env python3
#
# Golden-value model for the fabric test. See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Computes the expected round signature for `ecp5-test/fabric` on the host.

The value this prints is what the gateware is built to compare against, so it
has to be right for the wrong reason to be impossible. Two independent things
guard that:

  * The recurrence is not restated here. `block_step()` in the gateware module
    is the single definition, and it is the same function the Amaranth
    description mirrors. This script only arranges to run it fast.

  * "Fast" means a numpy version advancing all blocks at once, which *is* a
    second implementation and therefore a place to be wrong. So it is checked
    against the scalar `block_step` cycle by cycle over a prefix before it is
    trusted for the full round. If they disagree, this script fails rather than
    printing a number.

Speed matters because the hardware is about 700 times faster than the model: at
60 MHz it finishes a 2**18-cycle round in 4.4 ms, and the model needs about 3
seconds. That asymmetry is why the gateware restarts each round from its seeds
and checks itself against one constant, rather than the host trying to keep up.

    ./scripts/fabric_golden.py
    ./scripts/fabric_golden.py --check-cycles 500
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_golden.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))

from fabric.fabric_gateware import (BLOCKS, ROUND_BITS, ROUND_CYCLES,
                                    block_params, block_step)

U32 = np.uint32


def _tables(blocks):
    polys = np.zeros(blocks, dtype=U32)
    seeds = np.zeros(blocks, dtype=U32)
    mixes = np.zeros(blocks, dtype=U32)
    for index in range(blocks):
        poly, seed, mix = block_params(index)
        polys[index] = poly
        seeds[index] = seed
        mixes[index] = mix
    return polys, seeds, mixes


def _rotl(value, amount):
    amount &= 31
    if amount == 0:
        return value
    return (value << U32(amount)) | (value >> U32(32 - amount))


def _vector_step(states, polys, mixes):
    """All blocks, one cycle. Must agree with `block_step` exactly."""
    lsb = states & U32(1)
    shifted = (states >> U32(1)) ^ (polys * lsb)
    mixed = _rotl(shifted, 7) ^ _rotl(shifted, 13) ^ _rotl(shifted, 23)
    mixed ^= _rotl(shifted, 3) & _rotl(shifted, 17)
    mixed ^= _rotl(shifted, 11) | _rotl(shifted, 29)
    return shifted ^ mixed ^ mixes


def verify_vector_model(blocks, cycles):
    """Run scalar and vector side by side. Raises on the first disagreement.

    `cycles` is a cycle count, not a duration. A few hundred is enough: the mix
    diffuses across the whole word within about three cycles, so any indexing or
    width error in the vector version shows up almost immediately, and running
    longer only costs scalar time without testing anything new.
    """
    polys, seeds, mixes = _tables(blocks)
    vector = seeds.copy()
    scalar = [int(seeds[i]) for i in range(blocks)]

    for cycle in range(cycles):
        vector = _vector_step(vector, polys, mixes)
        for i in range(blocks):
            poly, _seed, mix = block_params(i)
            scalar[i] = block_step(scalar[i], poly, mix)
        if [int(x) for x in vector] != scalar:
            bad = next(i for i in range(blocks)
                       if int(vector[i]) != scalar[i])
            raise AssertionError(
                f"vector model diverged at cycle {cycle}, block {bad}: "
                f"{int(vector[bad]):#010x} != {scalar[bad]:#010x}")
    return cycles


def golden(blocks=BLOCKS, cycles=ROUND_CYCLES):
    """The XOR of all block states after one round.

    The gateware advances every block from its seed and reloads the seed on the
    final cycle of the round, so the signature is taken after `cycles - 1`
    advances -- see the round-timing comment in the gateware. Off by one here
    would be a mismatch caused by this script rather than by the silicon.
    """
    polys, seeds, mixes = _tables(blocks)
    states = seeds.copy()
    for _ in range(cycles - 1):
        states = _vector_step(states, polys, mixes)
    signature = U32(0)
    for state in states:
        signature ^= state
    return int(signature)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blocks", type=int, default=BLOCKS)
    parser.add_argument("--round-bits", type=int, default=ROUND_BITS)
    parser.add_argument("--check-cycles", type=int, default=300,
                        help="cycles to cross-check vector against scalar")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the hex value, for use in a build")
    args = parser.parse_args()

    cycles = 1 << args.round_bits

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            if not args.quiet:
                print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        emit(f"blocks {args.blocks}, round 2**{args.round_bits} = {cycles} cycles")

        checked = verify_vector_model(args.blocks, args.check_cycles)
        emit(f"vector model agrees with the scalar specification over "
             f"{checked} cycles, all {args.blocks} blocks")

        start = time.perf_counter()
        value = golden(args.blocks, cycles)
        elapsed = time.perf_counter() - start
        emit(f"golden signature: {value:#010x}  "
             f"({elapsed:.1f}s for {cycles - 1} advances)")
        emit(f"log: {LOG}")

    if args.quiet:
        print(f"{value:#010x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
