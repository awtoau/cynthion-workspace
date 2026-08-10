#!/usr/bin/env python3
#
# Decode a failing HyperRAM word back to the device addresses it actually came from. #349.
# SPDX-License-Identifier: BSD-3-Clause

"""The BIST pattern is invertible. Use that instead of describing the bits.

    ./scripts/hyperram_dqs_decode_word.py 0x3ffc003f 0x003f3ffe
    ./scripts/hyperram_dqs_decode_word.py --self-test

`HyperRAMCeiling.pattern` is `Cat(word_addr[:16], ~word_addr[16:22])`, so a
32-bit value carries the address it belongs to:

    value[15:0]  = addr[15:0]
    value[21:16] = ~addr[21:16]

and the 32-bit fabric word at device word address `A` holds `mem[A]` in the HIGH
half and `mem[A+1]` in the LOW half -- `docs/chips/hyperram/byte-order.md`, "the
lower device address is the more significant end of the fabric word".

Together those two facts turn any `compare actual/golden` pair into an ADDRESS,
which is the difference between "a rotation with a bit shifted" and "the read
returned the pair starting one device word early". The rig has been reading
these by eye and getting the second one wrong.

## Why the eye gets it wrong

`actual 0x3ffc003f` against `golden 0x003f3ffe` looks like the golden word
rotated 16 bits with one bit disturbed -- 0x3ffe against 0x3ffc. It is not. The
two halves come from two DIFFERENT device words, and the whole value is
accounted for with nothing left over once each half is decoded separately.
A rotation would leave that stray bit unexplained; a displacement does not leave
anything.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "hyperram_dqs_decode_word.log"

# `hyperram_ceiling_top.py`: ADDRESS_BITS = 22, and `pattern` splits at 16.
ADDRESS_BITS = 22
HALF = 16
UPPER_BITS = ADDRESS_BITS - HALF


def pattern(addr: int) -> int:
    """`HyperRAMCeiling.pattern`, in Python. One definition, restated once."""
    addr &= (1 << ADDRESS_BITS) - 1
    upper = (~(addr >> HALF)) & ((1 << UPPER_BITS) - 1)
    return (upper << HALF) | (addr & 0xFFFF)


def unpattern(word: int) -> int | None:
    """The address a 16-bit device word belongs to, or None if it is not one.

    A device word is HALF a pattern value, so on its own it is ambiguous -- the
    high half names `~addr[21:16]` and the low half names `addr[15:0]`. Both
    readings are returned by `sources_of` below; this one is for a full 32-bit
    fabric word.
    """
    upper = (word >> HALF) & 0xFFFF
    if upper >> UPPER_BITS:
        return None                       # more bits than the address has
    return (((~upper) & ((1 << UPPER_BITS) - 1)) << HALF) | (word & 0xFFFF)


def device_words(addr: int) -> tuple[int, int]:
    """The two device words the 32-bit beat at `addr` occupies, in wire order.

    Big-endian: `mem[addr]` is the HIGH half and `mem[addr + 1]` the LOW half.
    """
    value = pattern(addr)
    return (value >> 16) & 0xFFFF, value & 0xFFFF


def locate(half: int, near: int, span: int = 64) -> list[int]:
    """Which device word addresses near `near` hold the 16-bit value `half`.

    Searched rather than solved: a device word is half a pattern and the half it
    is depends on whether its address is even or odd within a beat, so inverting
    it directly needs the parity assumed. Searching assumes nothing and reports
    every hit, which is what makes "no candidate" a readable answer instead of a
    wrong one.
    """
    hits = []
    for addr in range(max(0, near - span), near + span):
        hi, lo = device_words(addr & ~1)
        mem = hi if (addr & 1) == 0 else lo
        if mem == half:
            hits.append(addr)
    return hits


def explain(actual: int, golden: int, log) -> int:
    due = unpattern(golden)
    if due is None:
        log.error("golden %#010x is not a pattern value -- %#x has more than "
                  "%d upper bits, so this pair cannot be decoded",
                  golden, (golden >> HALF) & 0xFFFF, UPPER_BITS)
        return 1

    hi_due, lo_due = device_words(due)
    log.info("golden %#010x  = pattern(%#07x)", golden, due)
    log.info("  the beat due here covers device words %#07x = %#06x and "
             "%#07x = %#06x", due, hi_due, due + 1, lo_due)
    log.info("")

    act_hi, act_lo = (actual >> 16) & 0xFFFF, actual & 0xFFFF
    log.info("actual %#010x  high half %#06x, low half %#06x",
             actual, act_hi, act_lo)

    hi_from = locate(act_hi, due)
    lo_from = locate(act_lo, due)
    log.info("  high half %#06x is the content of device word(s) %s",
             act_hi, ", ".join(f"{a:#07x}" for a in hi_from) or "none nearby")
    log.info("  low  half %#06x is the content of device word(s) %s",
             act_lo, ", ".join(f"{a:#07x}" for a in lo_from) or "none nearby")

    # A DISPLACEMENT is a pair of consecutive device words, both accounted for.
    pairs = [(h, l) for h in hi_from for l in lo_from if l == h + 1]
    log.info("")
    if not pairs:
        log.info("VERDICT: the two halves are not consecutive device words, so "
                 "this is NOT a whole-word displacement. Something is wrong "
                 "INSIDE a word -- a lane, a bit order, or noise.")
        return 0

    for start, _ in pairs:
        slip = start - due
        log.info("VERDICT: the read returned the pair starting at device word "
                 "%#07x where %#07x was due -- a displacement of %+d DEVICE "
                 "WORDS (%+d device edges, %s a fabric word).",
                 start, due, slip, 2 * slip,
                 "half" if abs(slip) == 1 else f"{abs(slip) / 2:g} x")
        log.info("  every bit of `actual` is accounted for by that "
                 "displacement. A rotation would leave some over; this does "
                 "not, so it is a GROUPING fault -- what `read_phase` and "
                 "READCLKSEL move -- and not a bit-level one.")
    return 0


# The board datum this was written for, and the arithmetic that decodes it.
# Recorded as a self-test so the decoder is checked against a known answer
# before it is trusted on a new one.
SELF_TEST = [
    # actual, golden, expected slip in device words
    (0x3FFC003F, 0x003F3FFE, -1),
    # An ALIGNED pair must decode to slip 0, or the decoder reports a fault in
    # every row including the good ones.
    (pattern(0x1000), pattern(0x1000), 0),
    # And two words further on must read +2, so the sign and the scale are both
    # checked rather than just the zero.
    (pattern(0x1002), pattern(0x1000), +2),
]


def self_test(log) -> int:
    bad = 0
    for actual, golden, want in SELF_TEST:
        due = unpattern(golden)
        act_hi, act_lo = (actual >> 16) & 0xFFFF, actual & 0xFFFF
        pairs = [(h, l) for h in locate(act_hi, due)
                 for l in locate(act_lo, due) if l == h + 1]
        got = (pairs[0][0] - due) if pairs else None
        ok = got == want
        bad += not ok
        log.info("  actual %#010x golden %#010x -> slip %s, wanted %+d  %s",
                 actual, golden, f"{got:+d}" if got is not None else "none",
                 want, "ok" if ok else "WRONG")
    if bad:
        log.error("%d self-test(s) wrong -- the decoder is not usable", bad)
    else:
        log.info("  the decoder reproduces a known displacement, a known "
                 "alignment and a known +2")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("actual", nargs="?", help="the word that came back")
    ap.add_argument("golden", nargs="?", help="the word that was due")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    if args.self_test or not (args.actual and args.golden):
        log.info("self-test:")
        rc = self_test(log)
        if args.self_test:
            return rc
        ap.error("give an actual and a golden word")
    return explain(int(args.actual, 0), int(args.golden, 0), log)


if __name__ == "__main__":
    sys.exit(main())
