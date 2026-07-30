# Does an LFE5U-12F's extra fabric work?

Test design, procedure and results for [awtoau/pluribus#98][issue].

[issue]: https://github.com/awtoau/pluribus/issues/98

## The question

LFE5U-12F and LFE5U-25F are the same die. The evidence for that is the vendor's
own, not an inference: byte-identical `.con` package files, identical
`frames × bits_per_frame` in `devices.json`, byte-identical Trellis
`tilegrid.json`, and IDCODEs differing only in the top nibble. nextpnr's chipdb
is per-die, so `nextpnr-ecp5 --12k` reports 24,288 LUTs and `ecppack` writes the
genuine 12F IDCODE `0x21111043`. **No patching is involved anywhere in this
test.**

That the tools *permit* it says nothing about whether the silicon *computes*
correctly. Three explanations remain:

1. **Market segmentation** — the die is whole and the extra fabric works.
2. **Binning / salvage** — 12F parts failed test in the extra region, so the
   extra LUTs are defective per-part.
3. **Independent fusing** — power or clocking limited separately from the
   IDCODE.

Case 2 is the dangerous one, and specifically because it predicts *intermittent*
wrongness rather than a dead region. A design that loads and blinks an LED
cannot distinguish any of the three.

## What the design is

`fabric_gateware.py`. 185 blocks, each holding 32 bits of state advanced every
cycle by:

1. a Galois LFSR step with a per-block polynomial, then
2. a fixed combinational mix — XOR of three rotations, then AND and OR of two
   more pairs, then a per-block constant.

Step 1 alone would be nearly free: 32 flops and a few LUTs. Step 2 is what costs
fabric. Seven state bits reach every output bit, which no single LUT4 can do, so
each bit becomes a small tree — about 104 LUT4s per block, measured.

`block_step()` in that module is simultaneously the specification, the Python
golden model, and a readable description of the hardware. There is no second
implementation of the recurrence to drift out of sync.

### Why nothing can be optimised away

| risk | what prevents it |
|---|---|
| yosys prunes a block | every block XORs into the signature, which is read over JTAG and drives the LEDs — nothing dangles |
| yosys shares logic between blocks | every block has a distinct polynomial **and** seed **and** mix constant, so no two compute the same function |
| states collapse to a common value | the round reload restores each block's own seed, not zero |
| the count silently shrinks | `fabric_build.py` parses nextpnr's LUT total and **fails the build** at or below 12,288 |

That last one is a hard failure rather than a warning. All of the first three
produce a bitstream that loads, runs, matches its golden value and proves
nothing — the most misleading outcome available.

### Rounds, and why the states restart

A round is 2^18 cycles, after which the XOR of all 185 states is latched as the
round's signature and every block reloads its seed, so the next round recomputes
the same function.

That reload is a deliberate compromise. The hardware advances 185 blocks at
60 MHz; the host's golden model manages about 86,000 cycles per second. The
hardware is roughly 700× faster, so a scheme where round *N* depends on rounds
1..*N*−1 gives a model that verifies a brief prefix and then falls permanently
behind — the part would run for hours unchecked, which is exactly the case this
test exists to catch.

With the reload, one golden constant covers every round forever. The gateware
checks itself, against a build-time constant, on every round with no host
attached, latching any mismatch stickily and counting it. The cost is that a
fault is caught in the round it occurs in rather than accumulated across rounds
— which was never the question.

## Guards against a false result

A false pass is worse than an inconclusive result, so each plausible route to
one has a specific check.

**The round timing could be wrong.** The signature is sampled through a
pipelined XOR tree at a round boundary while the blocks are reloading. An
off-by-one in the counter phase, the tree depth, or the reload's timing gives a
stable, repeatable, *wrong* signature — indistinguishable on hardware from
broken silicon, and it would be reported as broken silicon.
→ `fabric_sim.py` elaborates the same design with a small round and compares
against the model. Exact at (8 blocks, 2^7) and (13, 2^9).

**The vectorised golden model could be wrong.** It is a second implementation
and therefore a place to be wrong.
→ `fabric_golden.py` runs it against the scalar `block_step` cycle by cycle over
a 300-cycle prefix before trusting it, and fails rather than printing a number.

**The gateware could be built against a wrong constant**, in which case it
reports clean forever.
→ `fabric_run.py` recomputes the golden value from the specification and
**refuses to report anything** if it disagrees with what the gateware holds.
Verified by trying it: the `0xdeadbeef` build was refused.

**The detector might not fire at all.** A self-checking test that never reports
a failure is indistinguishable from one that cannot.
→ `fabric_control.py`, run against a `--golden 0xdeadbeef` build. Every round
must mismatch, and did: 1575/1575, sticky flag set on all 200 reads.

## Procedure

```bash
./scripts/fabric_sim.py                      # timing vs model, in simulation
./scripts/fabric_build.py                    # golden value, build, check LUTs
./scripts/fabric_placement.py                # where the logic actually landed
./scripts/fabric_run.py --rounds 20000       # load into SRAM, check

# negative control
./scripts/fabric_build.py --golden 0xdeadbeef
./scripts/fabric_run.py --rounds 100         # refuses, as designed
./scripts/fabric_control.py                  # detector must fire
./scripts/fabric_build.py                    # restore the real bitstream
```

**SRAM only. Nothing here writes flash.** Volatile configuration is undone by a
power cycle, so the board returns to its own boot gateware with no recovery
step. Writing flash would risk that gateware for no gain — the question is
whether the fabric computes correctly while running, and volatile configuration
answers it exactly as well.

No step sleeps or waits on a duration. The soak loop stops on a round count or a
poll cap, both counts. Wall-clock time is recorded only so a mismatch rate has a
denominator.

## Results

Cynthion r1.4, ECP5 `LFE5U-12F` CABGA256 speed 8, 2026-07-30, Apollo firmware
`v1.1.1-47-g19242e8-dirty`. Toolchain: oss-cad-suite, Yosys 0.65+57, nextpnr
0.10-74.

### Utilisation, from nextpnr's log

| | used | of | |
|---|---|---|---|
| Total LUT4s | **20,143** | 24,288 | **82.9%** |
| logic LUTs | 20,035 | 24,288 | 82.5% |
| carry LUTs | 108 | 24,288 | 0.4% |
| TRELLIS_FF | 8,151 | 24,288 | 33.6% |
| TRELLIS_IO | 58 | 197 | 29.4% |

**7,855 LUT4s beyond the 12,288 an LFE5U-12F advertises.**

Corroboration that nothing was pruned: yosys reports 20,035 `LUT4` and 8,151
`TRELLIS_FF`. 185 blocks × 32 bits is 5,920 state flops; the signature tree
(185 → 47 → 12 → 3 → 1, so 63 nodes × 32) is 2,016 more; 7,936 of 8,151
accounted for, the remainder being counters and host-visible registers.

`ecpunpack` reads **device ID `0x21111043`** out of the bitstream — the genuine
LFE5U-12F IDCODE, confirmed by a tool independent of the one that wrote it.

### Where the logic landed

A utilisation count is compatible with a placement that packs everything into
one dense corner, which would leave open the possibility that the design never
touched whatever region a 12F might have failed test in. So
`fabric_placement.py` parses `top.config` — the placement as the tools finally
committed it — and counts LUT `INIT` entries per tile row:

- **rows R2 to R48**, 44 of 47 carrying logic
- the three empty rows are R13, R25 and R37, which are EBR/DSP rows on this die
  rather than holes in the placement
- per-row occupancy 372 to 508 entries, mean 459
- **flatness (min/max row occupancy) 0.73**
- 69 columns, C2 to C70

So the logic is spread over every logic row and column of the die, not
concentrated. The design could not have confined itself to a 12k-sized subset.

### Timing

**86.43 MHz achieved against the 60 MHz constraint — met.** The 12F and 25F
share a speed grade, so this is not a case of the design only closing because
the extra fabric was clocked gently.

### Runs

| run | rounds | mismatched | sticky flag | host reads disagreeing |
|---|---|---|---|---|
| real bitstream, 8.9 s | 2,002 | 0 | never set | 0 / 65 |
| real bitstream, 87.6 s | 20,024 | 0 | never set | 0 / 643 |
| control, `0xdeadbeef` | 1,575 | **1,575** | **set, all 200 reads** | refused to score |

Each round is 262,144 cycles of all 185 blocks.

**No soak was run.** The sticky latching and the mismatch counter exist and are
demonstrated to work by the control, but this test is deliberately a single
load-and-check rather than an endurance run. The consequence for the conclusion
is spelled out below and it is not a small one: intermittent per-part defects are
**not** excluded by what was measured here.

The control's second finding is the stronger one and was not planned. It is a
separate build — 20,288 LUT4s rather than 20,143, independently placed and
routed, a different physical arrangement of logic across the die — and it
computed `0x26f028c8`, the same value the host model predicted and the same the
first build produced. Two different placements occupying ~83% of a die sold as
12,288 LUTs agreed with each other and with the specification.

The board's ADC read 3206 both before loading and while running, so there was no
measurable supply sag under the larger design.

## What this establishes, and what it does not

**Establishes**, for this one part at this one moment: a design occupying 20,143
of 24,288 LUTs placed, routed, closed timing at 60 MHz with 86.43 MHz of margin,
and computed the correct signature — with the logic spread evenly across every
logic row of the die, and with a detector demonstrated on the same silicon to
fire when the answer is wrong.

That kills the cheapest hypothesis: the extra fabric is not *plainly* dead and
not *plainly* unclocked on this part. It is evidence against case 3 as far as
logic and clocking go, since the extra region runs off the same global clock
network and computed correctly.

**Does not establish** anything about:

- **intermittent defects — the binning case is not excluded.** This is one pass
  at one moment. Case 2 predicts occasional wrongness, and a single check cannot
  see a rate. The sticky latch and the mismatch counter exist precisely to
  measure that, and were not used for it here. **A pass of this shape is
  entirely compatible with case 2.**
- **any other part.** Case 2 is a per-part claim. One passing device is
  consistent with a population where most extra regions are defective; the
  correct reading is "this die computed correctly once", not "12F dies are
  whole".
- **other conditions.** One temperature, one supply, one speed grade. Marginal
  logic often passes at room temperature and fails hot.
- **reliability.** Lattice does not test or warrant the extra region on a 12F, so
  nothing here makes it supported — only observed to work once.
- **the fabric comprehensively.** ~83% is a large fraction, not all of it, and
  the signature is an XOR over block state, so a fault in a bit that the mix
  happens not to propagate before the round ends could in principle be masked.
  The diffusion makes that unlikely, not impossible.

The unplanned corroboration is the control build: 20,288 LUT4s, independently
placed and routed, a physically different arrangement of logic, computing the
same `0x26f028c8`. Two distinct placements at ~83% agreed with each other and
with the specification. That is two samples of the placement, still one sample of
the part.

To move past this would take the thing deliberately not done here — a long run
for a mismatch *rate* — and, for any claim about LFE5U-12F as a family rather
than this die, more than one board.
