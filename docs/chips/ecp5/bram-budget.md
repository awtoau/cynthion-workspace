# Block RAM on the ECP5: who actually uses it

Budget: **56 `DP16KD` blocks, 112 KiB** — the die's, see [`lfe5u-12f.md`](lfe5u-12f.md). Tightness depends on the design.

## Per design

All built for r1.4, same device, package and speed grade.

| Design | BRAM | LUT4 | What it is |
|---|---|---|---|
| USB analyzer | **9 / 56 (16%)** | 8191 | capture at line rate |
| Facedancer SoC | 45 / 56 (80%) | 12824 | soft CPU + firmware |
| RISC-V hello SoC | 41 / 56 (73%) | 6811 | soft CPU + firmware |

- Facedancer must be built with `domain="usb"` at 60 MHz: `top.py` reads `platform.DEFAULT_CLOCK_FREQUENCIES_MHZ`; the default `domain="sync"` places 60 MHz logic in a 120 MHz domain and fails timing.

## Current SoC count

- Counts recorded before [#441](https://github.com/awtoau/cynthion-workspace/issues/441) came from non-reproducible builds; see [`lfe5u-12f.md`](lfe5u-12f.md#block-ram).
- Count by mode, not one mode: `grep -cE "^enum: EBR[0-9]+\.MODE " top.config` (all modes) agrees with nextpnr's `N/56` line; a `DP16KD`-only grep undercounts `PDPW16KD` cells (details: [`lfe5u-12f.md`](lfe5u-12f.md#block-ram)).
- Recorded counts: 44 of 56 (42 `DP16KD` + 2 `PDPW16KD`; [`lfe5u-12f.md`](lfe5u-12f.md)); 48 of 56 with 8 KiB caches, 49 for the shipping geometry in the 2026-08-12 sweep below (those builds differ).
- USB host SIE tx/rx FIFOs (512x8 each) in block RAM, 2 blocks ([#619](https://github.com/awtoau/cynthion-workspace/issues/619)): `SyncFIFOBuffered` with the engine reading `r_level` (`docs/upstream-boundary.md`, GUH). Whole SoC with the earlier drop-in FIFO (`6465c5f`): metrics build 445, 2026-11-06, 33/56 BRAM, 19555 COMB (main before: 31, 20809).
- UNVERIFIED: the current count. No build report exists under `tmp/` in this worktree; run `./dev.py metrics report`.
- Consumers: main block RAM 64 KiB at address 0 in the recorded builds (32 blocks); `top.py` now has `RAM_SIZE = 32 * 1024`. USB buffers 3, BTB 2, ILA 1; I-cache and D-cache 5 blocks each at 8 KiB. Doubling both caches from 4 to 8 KiB cost 4 blocks, not 6 (tag and status RAM did not double).
- Spare went to the caches rather than RAM: `.bss` was 9,728 bytes and `.data` zero, so the rest of the RAM region was stack slack. Doubling the caches addresses the frontend-stall number [`../../rtic.md`](../../rtic.md) measured.

## Capture work barely touches block RAM

- Analyzer buffers into HyperRAM via `HyperRAMPacketFIFO`; block RAM holds only small FIFOs: `out_fifo_depth=128` out, a 4-deep async FIFO into the `usb` domain.
- USB caps at 48.5 MB/s, far below the HyperRAM bus ([`../hyperram/sequential-baseline.md`](../hyperram/sequential-baseline.md), [#581](https://github.com/awtoau/cynthion-workspace/issues/581)): no block RAM needed for bulk buffering.

## Firmware, not buffers, consumes it

- Both heavy designs are soft-CPU systems with block-RAM program memory: 64 KiB is 32 blocks before anything else.
- A 16 KiB-cache configuration reached 32/56 in the RISC-V sweep ("nearly full" only because 64 KiB of firmware is assumed alongside).
- The ceiling constrains CPU designs with block-RAM firmware, not the device. `.text` and `.rodata` now execute in place from flash through the XIP window (`peripherals/flash.py`), freeing block RAM.
- Cost of flash execution: flash fetch costs many cycles against block RAM's one, which the I-cache hides; with a too-small cache the stall is measurable: [`../../rtic.md`](../../rtic.md) ([#245](https://github.com/awtoau/cynthion-workspace/issues/245)): RTIC's extra 1,700 bytes of `.text` moved frontend stalls from 44 to **452 per 1,000 cycles** through a 4 KiB direct-mapped I-cache.

## Cache geometry

- Shipping geometry: `CACHE_SETS = 64`, `CACHE_WAYS = 2` in [`gateware/soc/top.py`](../../../gateware/soc/top.py) = 64 sets × 2 ways × 64 B = 8 KiB per cache. The literal defaults in `gateware/soc/cpu/cpu.py` (128×1) apply only when `top.py` does not pass the arguments. Canonical description: [`../vexiiriscv-cpu.md`](../vexiiriscv-cpu.md).
- `flash_cache_flush()` is in the generated C test firmware only; Rust firmware uses a real `fence.i`. Replacement is PLRU, so a sweep of the full cache size evicts every way. Sets and ways are both open axes.

Block RAM per geometry (`DP16KD` cells in each netlist's `top.json`, from the CPU matrix `cpu-l1-*` arms, [`scripts/soc/soc_cpu_arms.py`](../../../scripts/soc/soc_cpu_arms.py); 2026-08-12, shipping SoC):

| geometry | per cache | BRAM | outcome |
|---|---|---|---|
| 64×1 | 4 KiB direct | 43 | places |
| 128×1 | 8 KiB direct | 47 | places |
| 32×2 | 4 KiB 2-way | 47 | places |
| **64×2** | **8 KiB 2-way** | **49** | **what `top.py` ships** |
| fetch 64×2 / lsu 128×1 | mixed | 46 | places |
| fetch 128×1 / lsu 64×2 | mixed | 50 | places |
| 256×1 | 16 KiB direct | 55 | places |
| 128×2 | 16 KiB 2-way | 57 | **does not place** |
| 32×4 | 8 KiB 4-way | 57 | **does not place** |

- Two geometries need 57 blocks on a die with 56: nextpnr stops with "no BELs remaining to implement cell type 'DP16KD'" after ~2 s. `bankCount = wayCount`: each way brings its own bank, tag memory and wider PLRU state. Three ways does not exist (SpinalHDL's PLRU asserts `isPow2` on the way count).
- Decision on dropping a way: [#494](https://github.com/awtoau/cynthion-workspace/issues/494), blocked on the hit-rate half. Per-geometry timing is not tabled here: [#481](https://github.com/awtoau/cynthion-workspace/issues/481).
- What a geometry buys (hit rate, stalls) needs `STALLED_CYCLES_FRONTEND` on hardware under a preempting workload; the only such measurement is [`../../rtic.md`](../../rtic.md), predating every geometry above.
- Cheaper levers that remove work rather than spending block RAM: grouping the hot path so ~6.2 KB of handler code stops colliding with itself ([#284](https://github.com/awtoau/cynthion-workspace/issues/284)); backing off the 50 ms REFRESH poll now the ALERT works ([#285](https://github.com/awtoau/cynthion-workspace/issues/285)): that poll's task is 2,292 bytes of the hot set at 20 Hz.

## Files

| Path | What |
|---|---|
| `repos/cynthion/cynthion/python/src/gateware/analyzer/top.py` | the 9/56 design |
| `repos/cynthion/cynthion/python/src/gateware/facedancer/top.py` | the 45/56 design |
| `linux-on-cynthion/results/sweep_20260729.json` | 132 CPU configurations with BRAM per row (includes SoC glue, not a bare-core figure) |
