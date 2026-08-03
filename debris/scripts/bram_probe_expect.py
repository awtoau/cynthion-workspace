#!/usr/bin/env python3
#
# The expected decode of ecp5-test/bram_probe: ground truth, derived not guessed.
# SPDX-License-Identifier: BSD-3-Clause

"""
Say what a correct block RAM decode of `ecp5-test/bram_probe` must produce, and check it.

    ./scripts/bram_probe_expect.py                 # against the collected artefacts
    ./scripts/bram_probe_expect.py --build-dir tmp/bram_probe/build

The chain, every step from the build's own output and none of it from what the
contents look like:

| step               | source                                            |
|--------------------|---------------------------------------------------|
| instance -> BEL    | `top.pnr.json`, `NEXTPNR_BEL: X60/Y25/EBR0`       |
| BEL -> tile        | `MIB_R{Y}C{X}`, prefix `EBR{n}` from the bel name |
| tile -> WID        | `word: EBR{n}.WID`, 9 bits, **least significant first** |
| WID -> init block  | `.bram_init {WID}`                                |
| init -> words      | the packing below, confirmed against the contents |

**`EBR.WID` exists on ECP5.** It is in the EBR `.tile_group`, not documented as
prominently as on MachXO2, and the 9-bit word is written LSB first. On this design the
five WIDs decode to exactly the five `.bram_init` indices; on the SoC bitstream in
`tmp/vexii_hello/build` forty-two decode to exactly the forty-two present. That makes
the index-to-tile association a lookup rather than a guess about position order.

## The packing

An init block is 2048 words of 9 bits regardless of the port width, and the port width
decides how a logical word is spread across them.

- **width 9** (`ram_b`, four blocks): block *j* holds lanes `9j .. 9j+8` of every word,
  address *a* in init word *a*, bit *k-9j*. All nine bits carry data; the fourth block
  holds only five lanes because 32 does not divide by 9.
- **width 36** (`rom_a`, one block): logical word *a* occupies init words `4a .. 4a+3`,
  nine lanes each, lane *k* in init word `4a + k//9` bit `k%9`.

Both are checked below against the one-hot region, which is what makes them derived
rather than assumed: a wrong lane order does not reproduce an identity matrix.
"""

import argparse
import gzip
import json
import re
import sys
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "ecp5-test" / "bram_probe"
LOG = ROOT / "tmp" / "logs" / "bram_probe_expect.log"

sys.path.insert(0, str(PROBE))


def emit(text, handle):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def read_text(directory, name):
    """The artefacts are shipped gzipped; a build directory holds them plain."""
    plain = directory / name
    if plain.exists():
        return plain.read_text()
    return gzip.decompress((directory / (name + ".gz")).read_bytes()).decode()


def parse_config(text):
    """Return {wid: init words}, {(row, col, index): wid}."""
    inits = {}
    for match in re.finditer(r"^\.bram_init (\d+)\n((?:[0-9a-f ]+\n)+)", text, re.M):
        inits[int(match.group(1))] = [int(t, 16) for t in match.group(2).split()]

    tiles = {}
    for match in re.finditer(
            r"^\.tile_group ([^\n]*MIB_EBR[^\n]*)\n((?:\w+: [^\n]+\n)+)", text, re.M):
        first = match.group(1).split()[0]
        word = re.search(r"word: EBR(\d)\.WID (\d+)", match.group(2))
        if not word:
            continue
        row, col = re.match(r"MIB_R(\d+)C(\d+):", first).groups()
        # Least significant bit first.
        wid = sum(int(bit) << i for i, bit in enumerate(word.group(2)))
        tiles[(int(row), int(col), int(word.group(1)))] = wid
    return inits, tiles


def placement(text):
    """Instance name -> (row, col, ebr index), from nextpnr's post-route netlist."""
    design = json.loads(text)
    out = {}
    for name, cell in design["modules"]["top"]["cells"].items():
        bel = cell.get("attributes", {}).get("NEXTPNR_BEL", "")
        match = re.match(r"X(\d+)/Y(\d+)/EBR(\d)$", bel)
        if match:
            col, row, index = (int(g) for g in match.groups())
            out[name] = (row, col, index)
    return out


def read_lanes(blocks, depth, width=9):
    """`width` lanes per block, one init word per address. All nine bits are used."""
    mask = (1 << width) - 1
    words = []
    for address in range(depth):
        value = 0
        for index, block in enumerate(blocks):
            value |= (block[address] & mask) << (width * index)
        words.append(value & 0xFFFFFFFF)
    return words


def read_width36(block, depth):
    """Four 9-bit init words per address, nine lanes each."""
    words = []
    for address in range(depth):
        value = 0
        for part in range(4):
            value |= (block[4 * address + part] & 0x1FF) << (9 * part)
        words.append(value & 0xFFFFFFFF)
    return words


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, default=PROBE / "artefacts",
                        help="where top.config and top.pnr.json are")
    args = parser.parse_args()

    import bram_probe

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        inits, tiles = parse_config(read_text(args.build_dir, "top.config"))
        placed = placement(read_text(args.build_dir, "top.pnr.json"))

        emit(f"{len(inits)} block RAMs, {len(placed)} placed DP16KD instances", handle)
        emit("", handle)
        emit("instance      bel              tile          WID  nonzero init words", handle)
        for name in sorted(placed):
            row, col, index = placed[name]
            wid = tiles[(row, col, index)]
            nonzero = sum(1 for word in inits[wid] if word)
            emit(f"{name:<13} X{col}/Y{row}/EBR{index:<8} "
                 f"MIB_R{row}C{col:<8} {wid:>3}  {nonzero}", handle)
        emit("", handle)

        groups = {}
        for name, where in placed.items():
            groups.setdefault(name.split(".")[0], []).append((name, where))

        failures = 0
        for array, members in sorted(groups.items()):
            members.sort(key=lambda item: item[0])
            blocks = [inits[tiles[where]] for _, where in members]
            expected = bram_probe.ROM_A if array == "rom_a" else bram_probe.RAM_B
            depth = len(expected)
            got = (read_width36(blocks[0], depth) if len(blocks) == 1
                   else read_lanes(blocks, depth))
            ok = got == expected
            failures += not ok
            emit(f"{array}: {depth} x 32 from {len(blocks)} block(s) "
                 f"{[m[0] for m in members]}", handle)
            emit(f"  expected {expected[0]:#010x} {expected[1]:#010x} "
                 f"{expected[2]:#010x} ...", handle)
            emit(f"  decoded  {got[0]:#010x} {got[1]:#010x} {got[2]:#010x} ...", handle)
            emit(f"  {'MATCH' if ok else 'MISMATCH'} over all {depth} words", handle)
            if not ok:
                bad = next(i for i in range(depth) if got[i] != expected[i])
                emit(f"  first difference at {bad}: {got[bad]:#010x} != "
                     f"{expected[bad]:#010x}", handle)
            emit("", handle)

        # What makes this a test case rather than a demonstration: of every other way
        # the four `ram_b` blocks could be ordered and every other lane width they
        # could be read at, exactly none reproduces the contents.
        members = sorted(groups["ram_b"])
        blocks = [inits[tiles[where]] for _, where in sorted(groups["ram_b"])]
        expected = bram_probe.RAM_B
        tried = matched = 0
        for width in (8, 9):
            for order in permutations(range(len(blocks))):
                tried += 1
                got = read_lanes([blocks[j] for j in order], len(expected), width)
                matched += got == expected
        emit(f"falsification: {matched} of {tried} orderings of ram_b's blocks "
             f"reproduce the contents", handle)
        if matched != 1:
            emit("  a decoder could get this right by accident -- the test case is "
                 "not doing its job", handle)
            failures += 1

        emit("", handle)
        emit("the decode is correct" if not failures
             else f"{failures} check(s) failed", handle)
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
