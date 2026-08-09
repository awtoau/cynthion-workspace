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

The analyzer buffers into HyperRAM through `HyperRAMPacketFIFO` and keeps only
small block RAM FIFOs around it: `out_fifo_depth=128` on the way out, and a
4-deep async FIFO for the crossing into the `usb` domain. With HyperRAM
streaming at 220 MB/s and USB capped at 48.5 MB/s, there is no reason to spend
block RAM on bulk buffering — the memory behind it is 4.5x faster than the link
draining it.

So for the work this device is built for, block RAM is close to free.

## What consumes it is firmware, not buffers

Both heavy designs are soft-CPU systems, and in both the block RAM is program
memory rather than buffering: 64 KiB of firmware for the CPU to execute from is
32 blocks before anything else is allocated.

That is worth separating from the caches. In the RISC-V sweep a configuration
with 16 KiB caches reached 32/56 blocks, which reads as "nearly full" only
because 64 KiB of firmware is assumed to sit in block RAM alongside it.

## The consequence

The block RAM ceiling constrains **CPU designs with block-RAM firmware**, not
the device generally. This section used to name two ways out as untried. One of
them has since been taken: **`.text` and `.rodata` execute in place from flash**
through `SPIFlashMemoryMap`, and the block RAM behind them was freed exactly as
predicted. Putting firmware in HyperRAM remains untried and is now unnecessary.

The trade named here was right, and it is the one this design now lives with:
flash fetch costs many cycles against block RAM's one, which is what the
instruction cache exists to hide — and when the cache is too small to hide it,
the stall is measurable rather than theoretical. The matched superloop-vs-RTIC
runs in [`../../rtic.md`](../../rtic.md) (#245) found the RTIC
dispatcher's extra 1,700 bytes of `.text` moving frontend stalls from 44 cycles
per 1,000 to **452 per 1,000** through a 4 KiB direct-mapped I-cache.

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

Timing held: `clk` closes at **78.04 MHz** against a 60 MHz constraint, and the
USB domain at 90.30 MHz.

**Sets, not ways.** `flash_cache_flush()` in
[`../../../scripts/riscv_firmware.py`](../../../scripts/riscv_firmware.py)
displaces the D-cache by reading its size at line stride, which touches every
set exactly once *only while the cache is direct-mapped*. A second way puts a
replacement policy in charge of what survives and the flush stops being a proof
— while still running, still passing, and proving nothing. Doubling the sets
keeps it correct by construction, and leaves the hit path free of a way-select
mux, which matters against an Fmax ceiling the BTB already had to be relaxed to
meet.

## Files

| Path | What |
|---|---|
| `repos/cynthion/cynthion/python/src/gateware/analyzer/top.py` | the 9/56 design |
| `repos/cynthion/cynthion/python/src/gateware/facedancer/top.py` | the 45/56 design |
| `linux-on-cynthion/results/sweep_20260729.json` | 132 CPU configurations with BRAM per row. **That sweep's Fmax figures are withdrawn**; its area and BRAM rows stand but include SoC glue, so they are not a bare-core figure |
