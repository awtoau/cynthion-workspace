#!/usr/bin/env python3
#
# A two-memory ECP5 design whose block RAM contents name their own lane order.
# SPDX-License-Identifier: BSD-3-Clause

"""
A test case for block RAM decoding: two memories, known contents, known answer.

    python3 gateware/probes/bram_probe/bram_probe.py --build

Built for `CynthionPlatformRev1D4` (LFE5U-12F-8CABGA256). It exists to be *decoded*,
not run -- the artefacts in `artefacts/` are the deliverable and the board is never
configured.

## Why two memories and not one

A single memory cannot expose a grouping error: every block belongs to the one array,
so any grouping looks right. Two of different depths, different port counts and
mutually exclusive contents can.

| memory  | depth | width | ports            | first word   |
|---------|-------|-------|------------------|--------------|
| `rom_a` |   512 |    32 | read only        | `0x00000001` |
| `ram_b` |  2048 |    32 | read + write     | `0xfffffffe` |

## Why the contents are shaped like this

The first 32 words of each memory are a **one-hot identity matrix**: word *a* has
exactly bit *a* set. That is the whole point of the design.

    rom_a[0] = 0x00000001    rom_a[1] = 0x00000002    rom_a[2] = 0x00000004
    ram_b[0] = 0xfffffffe    ram_b[1] = 0xfffffffd    ram_b[2] = 0xfffffffb

A decoder that gets the lane order wrong does not produce *plausible* output here; it
produces a permutation matrix that is visibly not the identity, and reading off where
the ones landed gives the permutation directly. Bit-level interleaving, lane order and
block order are all read off the same 32 words.

`0xAA` in one memory and `0x55` in the other is not enough. A constant fills every lane
with a constant column, and a permutation of equal columns is invisible; `0xAA` and
`0x55` are also each other's complement, so a swap of the two memories flips a byte
that a decoder has no way to call wrong. Here **every column is distinct and no column
of one memory equals a column of the other** -- asserted below, not assumed.

Words from 32 upward are `a * 0x9e3779b1` (complemented in `ram_b`). That fills the
rest densely, so the depth of each array is confirmed rather than inferred from where
the zeroes start, and neither memory is the mostly-zero image that defeats a
value-based search.

## What a correct decode produces

- Two arrays, one 512 x 32 and one 2048 x 32.
- The 512-deep one reads `0x00000001, 0x00000002, 0x00000004, ...` at addresses 0..31.
- The 2048-deep one reads the complement, `0xfffffffe, 0xfffffffd, 0xfffffffb, ...`.
- Every word from address 32 on satisfies `word == (a * 0x9e3779b1) & 0xffffffff` in
  `rom_a` and its complement in `ram_b`.

`artefacts/rom_a.hex` and `artefacts/ram_b.hex` hold the full expected contents, one
big-endian word per line, so a decode can be diffed rather than eyeballed.

## The role question

`ram_b` has a write port and `rom_a` has none. A decoder that infers a block's role
from fabric wire names -- `J[AB]n` as write, `J[CD]n` as read -- must therefore report
no write port on the two-or-so blocks that hold `rom_a`. If it reports one, the naming
convention is not pin semantics.
"""

import argparse
import sys
from pathlib import Path

from amaranth import Cat, Elaboratable, Module, Signal
from amaranth.lib.memory import Memory

ROOT = Path(__file__).resolve().parent.parent.parent
ARTEFACTS = Path(__file__).resolve().parent / "artefacts"

ROM_A_DEPTH = 512
RAM_B_DEPTH = 2048
WIDTH = 32

# Knuth's 32-bit golden-ratio multiplier. Odd, so multiplication by it is a bijection
# on 32-bit words and no column of the filled region is constant.
MIX = 0x9E3779B1


def contents(depth, invert):
    """One-hot for the first 32 words, then a dense mix; complemented for `ram_b`."""
    words = []
    for address in range(depth):
        word = (1 << address) if address < WIDTH else (address * MIX) & 0xFFFFFFFF
        words.append(word ^ 0xFFFFFFFF if invert else word)
    return words


ROM_A = contents(ROM_A_DEPTH, invert=False)
RAM_B = contents(RAM_B_DEPTH, invert=True)


def columns(words):
    """The per-lane bit column, as an int, for each of the 32 lanes."""
    return [sum(((word >> lane) & 1) << index for index, word in enumerate(words))
            for lane in range(WIDTH)]


def check_distinguishable():
    """The property the whole test case rests on. Asserted, so it cannot rot."""
    a = columns(ROM_A)
    b = columns(RAM_B)
    assert len(set(a)) == WIDTH, "two lanes of rom_a carry the same column"
    assert len(set(b)) == WIDTH, "two lanes of ram_b carry the same column"
    # Compare over the shared address range: a block of one memory read as though it
    # belonged to the other must not agree.
    shared = min(ROM_A_DEPTH, RAM_B_DEPTH)
    mask = (1 << shared) - 1
    assert not (set(x & mask for x in a) & set(y & mask for y in b)), \
        "a lane of rom_a matches a lane of ram_b"


class BramProbe(Elaboratable):
    """Two initialised memories, read continuously so nothing is trimmed.

    Every bit of every read word folds into an LED, because a memory whose output is
    partly unused is a memory yosys is entitled to narrow -- and a narrowed memory has
    a different lane order than the one this design claims to test.
    """

    def elaborate(self, platform):
        m = Module()

        m.submodules.rom_a = rom_a = Memory(shape=WIDTH, depth=ROM_A_DEPTH, init=ROM_A)
        m.submodules.ram_b = ram_b = Memory(shape=WIDTH, depth=RAM_B_DEPTH, init=RAM_B)

        counter = Signal(28)
        m.d.sync += counter.eq(counter + 1)

        read_a = rom_a.read_port()
        read_b = ram_b.read_port()
        m.d.comb += [
            read_a.addr.eq(counter[:9]),
            read_b.addr.eq(counter[:11]),
            read_a.en.eq(1),
            read_b.en.eq(1),
        ]

        # A write port on one memory and not the other, so "which blocks have a write
        # port" is a question with a known answer. The button gates it: nothing in the
        # tree can prove the enable is constant, so the port survives synthesis.
        button = platform.request("button_user", 0)
        write_b = ram_b.write_port()
        m.d.comb += [
            write_b.addr.eq(counter[11:22]),
            write_b.data.eq(counter[:WIDTH]),
            write_b.en.eq(~button.i & counter[27]),
        ]

        # Fold all 64 read bits into 6 LEDs. Nothing is discardable.
        fold = Signal(6)
        for index in range(6):
            bits = [read_a.data[bit] for bit in range(index, WIDTH, 6)]
            bits += [read_b.data[bit] for bit in range(index, WIDTH, 6)]
            m.d.comb += fold[index].eq(Cat(*bits).xor())

        leds = Cat(platform.request("led", index).o for index in range(6))
        m.d.comb += leds.eq(fold)

        return m


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true",
                        help="synthesise and collect the artefacts")
    args = parser.parse_args()

    check_distinguishable()
    print(f"rom_a: {ROM_A_DEPTH} x {WIDTH}, first word {ROM_A[0]:#010x}")
    print(f"ram_b: {RAM_B_DEPTH} x {WIDTH}, first word {RAM_B[0]:#010x}")
    print("all 64 lane columns distinct, and no lane of one matches a lane of the other")

    ARTEFACTS.mkdir(parents=True, exist_ok=True)
    (ARTEFACTS / "rom_a.hex").write_text("".join(f"{w:08x}\n" for w in ROM_A))
    (ARTEFACTS / "ram_b.hex").write_text("".join(f"{w:08x}\n" for w in RAM_B))

    if not args.build:
        print("expected contents written; pass --build to synthesise")
        return 0

    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4

    build_dir = ROOT / "tmp" / "bram_probe" / "build"
    CynthionPlatformRev1D4().build(BramProbe(), do_program=False,
                                   build_dir=str(build_dir))
    print(f"built into {build_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
