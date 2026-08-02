# bram_probe — a block RAM decoding test case with a known answer

Two memories on an ECP5, contents chosen so a wrong decode is visibly wrong rather than
plausible. Built for `CynthionPlatformRev1D4` (LFE5U-12F-8CABGA256). Nothing here is
meant to run on hardware; the artefacts are the deliverable.

Regenerate with:

    AMARANTH_nextpnr_opts="--write top.pnr.json" \
      python3 ecp5-test/bram_probe/bram_probe.py --build

Check a decode against ground truth with:

    ./scripts/bram_probe_expect.py

## The artefacts

| file | what it is |
|------|------------|
| `bram_probe.py` | the Amaranth source, including the content generator |
| `artefacts/top.bit` | the built bitstream — the thing to decode |
| `artefacts/top.config.gz` | the same, as prjtrellis text; the reference answer |
| `artefacts/top.json.gz` | post-synthesis yosys netlist: **instance names**, cell types, widths |
| `artefacts/top.pnr.json.gz` | post-route nextpnr netlist: `NEXTPNR_BEL` per instance |
| `artefacts/rom_a.hex` | expected contents of `rom_a`, one big-endian word per line |
| `artefacts/ram_b.hex` | expected contents of `ram_b` |

Built with Yosys 0.65+57, nextpnr-0.10-74, Project Trellis 1.4-79, Amaranth 0.5.9. The
flow Amaranth generated, which is what produced the artefacts:

    yosys -q -l top.rpt top.ys
    nextpnr-ecp5 --quiet --write top.pnr.json --log top.tim --12k --package CABGA256 \
        --speed 8 --json top.json --lpf top.lpf --textcfg top.config
    ecppack --compress --freq 38.8 --input top.config --bit top.bit --svf top.svf

`--write top.pnr.json` is the only addition to the platform's default flow, and it is
what carries the instance names through to the placement.

## The two memories

| instance | depth | width | ports | primitives | `DATA_WIDTH` |
|----------|-------|-------|-------|------------|--------------|
| `rom_a` | 512 | 32 | read only | 1 × DP16KD | 36 |
| `ram_b` | 2048 | 32 | read + write | 4 × DP16KD | 9 |

Different depths, different groupings, different port widths and different roles, so a
grouping error, a width error and a role error each have somewhere to show.

## The contents, and why they are shaped this way

The first 32 words of each memory are a **one-hot identity matrix** — word *a* has
exactly bit *a* set:

    rom_a[0] = 0x00000001   rom_a[1] = 0x00000002   rom_a[2] = 0x00000004  ...
    ram_b[0] = 0xfffffffe   ram_b[1] = 0xfffffffd   ram_b[2] = 0xfffffffb  ...

`ram_b` is the bitwise complement of `rom_a` at the same address. Words from 32 upward
are `a * 0x9e3779b1` (complemented in `ram_b`), which fills the rest densely so the
depth of each array is confirmed rather than inferred from where the zeroes start.

A wrong lane order does not produce plausible output here. It produces a permutation
matrix that is visibly not the identity, and reading off where the ones landed gives
the permutation. That is the whole point.

**All `0xAA` in one and all `0x55` in the other would not do this.** A constant fills
every lane with a constant column, and a permutation of equal columns is invisible;
`0xAA` and `0x55` are also each other's complement, so swapping the two memories flips
a byte a decoder has no way to call wrong. Here every one of the 64 lane columns is
distinct and no column of one memory equals a column of the other — `bram_probe.py`
asserts both at generation, so the property cannot rot.

## The expected answer

    rom_a  512 × 32   one DP16KD at MIB_R25C60, bel X60/Y25/EBR0, WID 3
                      DATA_WIDTH 36; logical word a occupies init words 4a..4a+3,
                      nine lanes each: lane k in init word 4a + k//9, bit k%9
                      rom_a[0..2] = 00000001 00000002 00000004

    ram_b  2048 × 32  four DP16KD, DATA_WIDTH 9; block j holds lanes 9j..9j+8,
                      address a in init word a, bit k-9j
                      lane group 0 -> MIB_R25C62, bel X62/Y25/EBR1, WID 7
                      lane group 1 -> MIB_R25C57, bel X57/Y25/EBR3, WID 6
                      lane group 2 -> MIB_R25C64, bel X64/Y25/EBR2, WID 5
                      lane group 3 -> MIB_R25C66, bel X66/Y25/EBR3, WID 4
                      ram_b[0..2] = fffffffe fffffffd fffffffb

`rom_a` is read-only. Any decoder that reports a write port on `MIB_R25C60` has
inferred one from something that is not pin semantics.

## Three traps this case is built to catch

**Position order is not lane order.** Sorted by tile column, `ram_b`'s blocks are C57,
C62, C64, C66 — which is lane groups 1, 0, 2, 3. A decoder that orders blocks by
position gets the identity matrix wrong at the first word. Guessing position order
would have produced a plausible answer on a design where placement happened to be
monotonic; it does not here.

**Nine lanes per block, not eight.** `DATA_WIDTH 9` uses all nine bits of each init
word, so 32 lanes across four blocks is 9+9+9+5 and not 8+8+8+8. Reading eight bits per
block reproduces `ram_b[0]` as `0x1ffffffe` instead of `0xfffffffe`, and every later
word is shifted rather than merely truncated. On dense contents the first word looks
close enough to pass; on the one-hot region nothing looks close.

**A single block can hold a 32-bit memory.** `rom_a` is one DP16KD in `DATA_WIDTH 36`
mode, so its lane order is entirely internal interleaving with no routing to group. A
decoder that only understands multi-block arrays has nothing to say about it.

`scripts/bram_probe_expect.py` reports how many of the 48 possible orderings of
`ram_b`'s blocks and lane widths reproduce the contents. The answer is 1.

## `EBR.WID` does exist on ECP5

The obstacle raised in `awtoau/pluribus#102` — that ECP5 has no `WID` word, so the
`.bram_init` index cannot be tied to a tile — does not hold. The word is in the EBR
`.tile_group`, prefixed by the bel's own EBR index, and written **least significant bit
first**:

    .tile_group MIB_R25C64:MIB_EBR4 MIB_R25C65:MIB_EBR5 MIB_R25C66:MIB_EBR6
    word: EBR2.WID 101000000        <- read LSB first: 0b000000101 = 5
    enum: EBR2.MODE DP16KD
    enum: EBR2.DP16KD.DATA_WIDTH_A 9

and `.bram_init 5` is indeed `ram_b`'s lane group 2. Read the same bits most
significant first and you get 320, which is not the index of anything.

On this design the five WIDs decode to exactly the five `.bram_init` indices present.
On a 42-block SoC bitstream from the same flow, the forty-two WIDs decode to exactly
the forty-two indices present — a bijection in both cases, which is what a coincidence
would not give. So the index-to-tile association is a lookup, not a guess about
position order.

Note the two indices in a tile group differ and both matter: the *tile type* suffix
(`MIB_EBR4`) is the tile's position in the row segment, while the *word prefix*
(`EBR2`) matches the EBR index in nextpnr's bel name (`X64/Y25/EBR2`). Pair on the bel
index and the first tile's column, not on the tile type.

## What routing still has to supply

The chain above gets from an instance name to an init block. It does **not** answer
which lane group an instance holds without the instance name itself — `ram_b.0.0`
through `ram_b.0.3` are named by yosys and carry the lane order in the suffix, and that
name reaches the bitstream only through `top.json` / `top.pnr.json`. Recovering the
lane order from the bitstream alone is what the data nets are for, and this case gives
a known answer to check that against.
