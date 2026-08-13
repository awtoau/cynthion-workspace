# Block RAM on the ECP5: who actually uses it

The budget is **56 `DP16KD` blocks, 112 KiB** — the die's, settled in
[`lfe5u-12f.md`](lfe5u-12f.md). Whether that is tight depends entirely on what
the design is, and the split is sharper than expected.

## Measured

All built for r1.4, same device, package and speed grade.

| Design | BRAM | LUT4 | What it is |
|---|---|---|---|
| USB analyzer | **9 / 56 (16%)** | 8191 | capture at line rate |
| Facedancer SoC | 45 / 56 (80%) | 12824 | soft CPU + firmware |
| RISC-V hello SoC | 41 / 56 (73%) | 6811 | soft CPU + firmware |

All three build and produce bitstreams. **Facedancer must be built with
`domain="usb"` at 60 MHz** — `top.py` reads
`platform.DEFAULT_CLOCK_FREQUENCIES_MHZ`, and instantiating it with the default
`domain="sync"` places 60 MHz logic in a 120 MHz domain and fails timing at
72.68 MHz. Built the way its own build path does it, every clock passes.

## Capture work barely touches block RAM

- Analyzer buffers into HyperRAM through `HyperRAMPacketFIFO`, keeps only
  small block RAM FIFOs around it: `out_fifo_depth=128` on the way out, a
  4-deep async FIFO for the crossing into the `usb` domain.
- HyperRAM streams at 220 MB/s, USB caps at 48.5 MB/s -- no reason to spend
  block RAM on bulk buffering when the memory behind it is 4.5x faster than
  the link draining it.
- For the work this device is built for, block RAM is close to free.

## What consumes it is firmware, not buffers

- Both heavy designs are soft-CPU systems where block RAM is program memory,
  not buffering: 64 KiB of firmware for the CPU to execute from is 32 blocks
  before anything else is allocated.
- Worth separating from the caches: in the RISC-V sweep a configuration with
  16 KiB caches reached 32/56 blocks, which reads as "nearly full" only
  because 64 KiB of firmware is assumed to sit in block RAM alongside it.

## The consequence

- Block RAM ceiling constrains **CPU designs with block-RAM firmware**, not
  the device generally. This section used to name two ways out as untried.
  One has since been taken: **`.text` and `.rodata` execute in place from
  flash** through `SPIFlashMemoryMap`, block RAM behind them freed exactly as
  predicted. Putting firmware in HyperRAM remains untried and now unnecessary.
- The trade named here was right, and is the one this design now lives with:
  flash fetch costs many cycles against block RAM's one, which the
  instruction cache exists to hide -- and when the cache is too small to hide
  it, the stall is measurable, not theoretical. The matched superloop-vs-RTIC
  runs in [`../../rtic.md`](../../rtic.md) ([#245](https://github.com/awtoau/cynthion-workspace/issues/245)) found the RTIC dispatcher's
  extra 1,700 bytes of `.text` moving frontend stalls from 44 cycles per
  1,000 to **452 per 1,000** through a 4 KiB direct-mapped I-cache.

### How the 56 are spent now

Measured, both sides, from `./dev.py metrics report`:

| consumer | before | after | |
|---|---|---|---|
| main block RAM, the writable region | 32 | 32 | 64 KiB at address 0 |
| CPU I-cache | 2 | **5** | 4 KiB → 8 KiB |
| CPU D-cache | 4 | **5** | 4 KiB → 8 KiB |
| USB buffers | 3 | 3 | |
| CPU BTB | 2 | 2 | |
| ILA | 1 | 1 | |
| **total** | 44 | **48** | of 56 |
| **spare** | 12 | **8** | 16 KiB |

Doubling both caches cost **4 blocks, not the 6 a naive 4→8 KiB sum predicts** —
the caches were not storing only data, and the tag and status RAM did not double
with them.

The spare went to the caches rather than to RAM, and the firmware's own section
sizes are the reason: `.bss` is 9,728 bytes and `.data` is zero, so of the 63 KiB
RAM region about 11 KiB is live and **the entire remainder is stack slack** —
`.stack` is simply "whatever is left". Growing RAM would have enlarged slack and
relieved nothing measurable. Doubling the caches attacks the number
[`../../rtic.md`](../../rtic.md) actually measured.

Timing held, and "held" is the whole claim available from one build: `clk`'s
distribution over 40 nextpnr seeds is 65.75–74.94 MHz, median 71.42, against a
60 MHz constraint that does not itself affect the result ([#467](https://github.com/awtoau/cynthion-workspace/issues/467), [#478](https://github.com/awtoau/cynthion-workspace/issues/478)).

### Sets or ways — measured, and four ways is simply out of blocks

An earlier version of this section said sets rather than ways was forced, because
`flash_cache_flush()` needs a direct-mapped cache. **That was wrong**, in the
direction that made the easy choice look mandatory. The replacement policy is
PLRU rather than random, so a sweep of the full cache size still evicts every
way; and that flush is only in the generated C test firmware, while the Rust
firmware uses a real `fence.i`. The axis was never closed.

Block RAM per geometry, counted off each geometry's own netlist
(`DP16KD` cells in `top.json`) by the CPU matrix's `cpu-l1-*` arms
([`../../../scripts/soc_cpu_arms.py`](../../../scripts/soc_cpu_arms.py)),
measured 2026-08-12 on the shipping SoC:

| geometry | per cache | BRAM | outcome |
|---|---|---|---|
| 64×1 | 4 KiB direct | 43 | places |
| 128×1 | 8 KiB direct | 47 | places |
| 32×2 | 2 KiB 2-way | 47 | places |
| **64×2** | **4 KiB 2-way** | **49** | **what `top.py` ships** |
| fetch 64×2 / lsu 128×1 | mixed | 46 | places |
| fetch 128×1 / lsu 64×2 | mixed | 50 | places |
| 256×1 | 16 KiB direct | 55 | places |
| 128×2 | 8 KiB 2-way | 57 | **does not place** |
| 32×4 | 2 KiB 4-way | 57 | **does not place** |

**Two geometries are out of blocks**, 57 on a die with 56: nextpnr stops with
"no BELs remaining to implement cell type 'DP16KD'" after ~2 s, on whichever
memory it reaches last. `bankCount = wayCount`, so each way brings its own bank,
its own tag memory and a wider PLRU state. Three ways does not exist at all:
SpinalHDL's PLRU asserts `isPow2` on the way count.

**The 52-versus-58 disagreement on 128×2 ([#287](https://github.com/awtoau/cynthion-workspace/issues/287)) does not reproduce.** Two
elaborations of that geometry, minutes apart, produce a byte-identical `top.json`
and 57 blocks both times. Two things that were true when it was seen are not now:
elaboration is reproducible ([#441](https://github.com/awtoau/cynthion-workspace/issues/441)), and every build no longer shares one
generated `VexiiRiscv.v` path ([#306](https://github.com/awtoau/cynthion-workspace/issues/306)), which is exactly how one geometry's build
could report another's memories.

**Fmax is not in this table on purpose.** One build's Fmax is a property of that
placement: the distribution at fixed occupancy is 9 MHz wide ([#467](https://github.com/awtoau/cynthion-workspace/issues/467)), and the
constraint the design is given does not change it ([#478](https://github.com/awtoau/cynthion-workspace/issues/478)). Timing per geometry is
a seed sweep — `soc_occupancy_timing.py` with the `cpu-l1-*` arms, 40 seeds each,
in [#481](https://github.com/awtoau/cynthion-workspace/issues/481):

| geometry | paired vs 64×2 | 95% CI | faster |
|---|---|---|---|
| **128×1** | **+3.50 MHz** | [+2.20, +4.80] | 34/40 |
| 256×1 | +2.25 | [+0.61, +3.88] | 29/40 |
| fetch 64×2 / lsu 128×1 | +2.16 | [+0.74, +3.58] | 25/40 |
| 64×1 | -0.74 | [-2.35, +0.86] | 17/40 |
| 32×2 | -1.91 | [-3.09, -0.72] | 12/40 |

Dropping the way pays only where the sets are: 64×1 is indistinguishable from
64×2. [#494](https://github.com/awtoau/cynthion-workspace/issues/494) is the decision, and it is blocked on the hit-rate half.

### What none of this measures

What a geometry *buys*. Synthesis reports cost; hit rate and stalls need
`STALLED_CYCLES_FRONTEND` read on hardware under a workload that preempts, and
the only such measurement is in [`../../rtic.md`](../../rtic.md), which predates
every geometry above.

Two cheaper levers were identified alongside it and may beat it outright, since
both *remove* work rather than spending block RAM to absorb it: grouping the hot
path so ~6.2 KB of handler code stops colliding with itself ([#284](https://github.com/awtoau/cynthion-workspace/issues/284)), and backing
off the 50 ms REFRESH poll now that the ALERT does its job ([#285](https://github.com/awtoau/cynthion-workspace/issues/285)) — that poll's
task is 2,292 bytes of the hot set, running at 20 Hz.

## Files

| Path | What |
|---|---|
| `repos/cynthion/cynthion/python/src/gateware/analyzer/top.py` | the 9/56 design |
| `repos/cynthion/cynthion/python/src/gateware/facedancer/top.py` | the 45/56 design |
| `linux-on-cynthion/results/sweep_20260729.json` | 132 CPU configurations with BRAM per row. **That sweep's Fmax figures are withdrawn**; its area and BRAM rows stand but include SoC glue, so they are not a bare-core figure |
