# RV32 Equivalence and Variation Report (2026-07-22)

## Scope
This report separates two questions that were previously mixed:

1. CPU benchmark equivalence between two new RV32 builds.
2. Full-system equivalence versus legacy VexRiscv + USB fabric.

The key conclusion is that the existing runtime benchmark comparison is valid only for CPU-centric comparison of the two new builds, not for legacy full-system equivalence.

## Version-Pinned Evidence

### Benchmark run identities (new builds)
- Stripped run ID: `VexiiRiscv_1964590524`
- Moondancer-like run ID: `VexiiRiscv_964903203`
- Harness: `vexiiriscv.tester.RegressionSingle` (benchmark paths)
- Benchmarks: `dhrystone_vexii`, `coremark_vexii`
- Benchmark seed: `2` (printed in run banners)
- CoreMark compile string (both): `GCC11.1.0`, `-march=rv32imac -mabi=ilp32` with identical optimization flags

### CPU-equivalence audit (new vs new)
Checked items:
- Core family is the same in both runs: both are `VexiiRiscv_*` regression artifacts.
- Benchmark ELF path is identical per benchmark type in both runs:
  - Dhrystone: `.../baremetal/dhrystone_vexii/build/rv32imac/dhrystone_vexii.elf`
  - CoreMark: `.../baremetal/coremark_vexii/build/rv32imac/coremark_vexii.elf`
- ELF ISA/ABI target is identical by path and compiler flags: `rv32imac`, `ilp32`.
- Testbench stress knobs are identical in both benchmark args:
  - `--ibus-ready-factor 2.0`
  - `--dbus-ready-factor 2.0`
  - `--fail-after 600000000`

Not identical (intentional config differences):
- Privilege/plugin and memory hierarchy options differ (supervisor, RVA, fetch/LSU L1 options).
- Therefore this is a same-core-family comparison with different feature mixes, not a bit-for-bit same CPU configuration.

### Area/timing run identities
From `riscv-64/metrics/ecp5_usage_history.csv`, commit `6930b8e` rows:
- `soc_x32_sv_rvm_rvc_rdtime_clint_uart`
- `soc_x32_rva_rvm_rvc_rdtime_i4k_d4k_clint_uart`
- `soc_x32_legacy_vexriscv_usb_facedancer`

Target for all rows:
- Device: `LFE5U-12F`
- Package: `BG256`
- Speed grade: `8`

## Exact Config Delta (New vs New)

### Stripped RV32 config
`--xlen=32 --with-supervisor --with-rvm --with-rvc --with-rdtime`

### Moondancer-like RV32 config
`--xlen=32 --with-rvm --with-rvc --with-rva --with-rdtime --with-fetch-l1 --fetch-l1-sets=64 --fetch-l1-ways=1 --with-lsu-l1 --lsu-l1-sets=64 --lsu-l1-ways=1`

### Non-equivalent features in that comparison
- `with-supervisor` present only in stripped variant.
- `with-rva` present only in moondancer-like variant.
- I-cache and D-cache (i4k/d4k style L1) present only in moondancer-like variant.

So this is not a single-factor A/B test; it is a multi-factor config delta.

## Table A: CPU-Centric Benchmark Comparison (Valid and fair for new-vs-new)

| Metric | Stripped RV32 | Moondancer-like RV32 | Delta (Moondancer-like - Stripped) |
|---|---:|---:|---:|
| Dhrystone us/run | 64 | 74 | +10 (+15.6%) |
| Dhrystones/sec | 15623 | 13415 | -2208 (-14.1%) |
| Dhrystone DMIPS/MHz | 0.74 | 0.63 | -0.11 (-14.9%) |
| CoreMark total ticks | 6,133,969 | 6,361,949 | +227,980 (+3.7%) |
| CoreMark/MHz | 1.63 | 1.57 | -0.06 (-3.7%) |

Interpretation:
- On these CPU-oriented workloads, the stripped build is more efficient per cycle/MHz.
- The largest measured runtime delta is Dhrystone (-14.1%).

## Table A2: Throughput-at-Implemented-Clock (derived)

This is not a measured board runtime; it is a normalized estimate using
`CoreMark/MHz * route Fmax (MHz)` to illustrate efficiency-vs-frequency tradeoff.

| Metric | Stripped RV32 | Moondancer-like RV32 | Delta |
|---|---:|---:|---:|
| CoreMark/MHz | 1.63 | 1.57 | -3.7% |
| Fmax (MHz) | 73.37 | 146.37 | +99.5% |
| Estimated CoreMark/s at Fmax | 119.59 | 229.80 | +92.2% |

Interpretation:
- Per-MHz efficiency favors stripped.
- Frequency headroom strongly favors moondancer-like.
- Which one is "faster" in practice depends on the clock you can and will run in the real system context.

## Table B: Area/Timing Comparison (fair for implementation cost, not runtime equivalence)

| Metric | Stripped RV32 | Moondancer-like RV32 | Legacy VexRiscv + USB fabric |
|---|---:|---:|---:|
| LUT4 | 6592 | 6876 | 12646 |
| FF | 2695 | 3756 | 6005 |
| BRAM | 8 | 14 | 45 |
| Fmax (MHz) | 73.37 | 146.37 | 67.47 |

Derived deltas:
- Moondancer-like vs stripped:
  - LUT4: +4.3%
  - FF: +39.4%
  - BRAM: +75.0%
  - Fmax: +99.5%
- Legacy vs stripped:
  - LUT4: +91.8%
  - FF: +122.8%
  - BRAM: +462.5%
  - Fmax: -8.0%

## Equivalence Matrix

| Comparison | Equivalent for CPU benchmark? | Equivalent for full-system cost? | Why |
|---|---|---|---|
| Stripped vs moondancer-like (new vs new) | Partially | Yes (as new implementations) | Same harness/workloads, but CPU features differ in multiple dimensions |
| Legacy vs stripped | No | Partially | Legacy includes full USB fabric; stripped row is CPU+SoC baseline without equivalent USB fabric |
| Legacy vs moondancer-like | No | Partially | Same issue: legacy has USB fabric included; benchmark harness not aligned |

## Fairness labels by question

| Question | Current status | Why |
|---|---|---|
| Are we comparing like CPU binaries? | Yes | Same `rv32imac` benchmark ELFs and same harness knobs |
| Are we comparing like CPU microarchitecture settings? | Partly | Feature mix differs (supervisor/RVA/cache knobs) |
| Are we comparing like full systems versus legacy USB fabric? | No | Legacy row includes USB fabric; new benchmarked rows do not include equivalent USB subsystem |

## Why the variation is likely large
Ranked likely contributors:

1. Multi-factor config change in new-vs-new benchmark
- This is not one variable at a time; supervisor, RVA, and both L1 caches differ simultaneously.

2. Cache architecture tradeoff on small synthetic workloads
- Dhrystone/CoreMark working sets and access patterns may not strongly benefit these specific cache parameters; cache pipeline overhead can dominate.

3. Feature/plugin timing tradeoff
- The moondancer-like configuration appears optimized for much higher route Fmax, while losing some per-MHz efficiency.

4. Full-system mismatch with legacy row
- Legacy row includes USB fabric and substantial non-CPU logic not present in the compared new rows.

## What is equivalent right now
Equivalent today:
- Toolchain inside benchmark binaries (`GCC11.1.0`, `rv32imac`, same flags)
- Workloads (same Dhrystone and CoreMark sources/binaries)
- Benchmark harness type and pass criteria
- Device family and implementation target for area/timing rows

Not equivalent today:
- CPU feature set between the two new configs is not single-factor matched.
- Legacy includes USB fabric while new rows used for runtime comparison do not include equivalent USB subsystem.

## Recommended next measurements for a true root-cause split

1. Single-factor CPU A/B matrix (one toggle at a time)
- Baseline: stripped
- +RVA only
- +I-cache only
- +D-cache only
- +I-cache + D-cache
- +Supervisor (if absent in baseline variant)

Suggested minimum matrix to isolate the biggest variation quickly:
- `base`: xlen32 + rvm + rvc + rdtime
- `base + supervisor`
- `base + rva`
- `base + i4k+d4k`
- `base + rva + i4k+d4k` (current moondancer-like closest)

2. Full-system parity run
- Build a new profile that includes USB/fabric blocks as close as possible to legacy composition.
- Record area/timing and, if possible, a USB-active workload metric.

3. Throughput-at-clock normalization
- Keep CoreMark/MHz as efficiency metric.
- Also report estimated absolute throughput at achieved Fmax for each build.

## Bottom line
- Your fairness concern is correct.
- Current benchmark numbers are meaningful for new-vs-new CPU efficiency, but not for legacy full-system equivalence.
- The large variation likely comes from comparing multiple architectural changes at once, plus a separate system-composition mismatch when legacy USB fabric is involved.
