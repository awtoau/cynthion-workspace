**Settled in pluribus, not here.** `pluribus: docs/fabric-test.md` — *"`LFE5U-12F`
and `-25F` are the same die, and the open flow gives a 12F all 24,288 LUTs
unpatched. That extra fabric worked on the one part tested, but binning/salvage
is only bounded, not excluded."*

So the toolchain reports 56 `DP16KD` where the datasheet advertises 32, nobody
opts in, and whether a given die is sound is an empirical question with no tool
to answer it. That is pluribus's subject. **This file is only what our designs
actually use.**

How much a design needs depends entirely on what the design is, and the split is
sharper than expected.

## Measured

All built for r1.4, same device, package and speed grade.

| Design | `DP16KD` of 56 | LUT4 | What it is |
|---|---|---|---|
| USB analyzer | **9** (16%) | 8191 | capture at line rate |
| the SoC | **41** (73%) | 6811 | soft CPU + firmware |
| Facedancer SoC | **45** (80%) | 12824 | soft CPU + firmware |

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
the device generally. Two ways out, neither yet tried:

- execute in place from flash (`SPIFlashMemoryMap` exists in luna_soc and is
  memory-mapped), or
- put firmware in HyperRAM.

Either frees roughly 26 blocks, which is more than the entire analyzer uses and
enough to make 16 KiB caches affordable. The trade is fetch latency: HyperRAM
costs 20-26 cycles per transaction against a single cycle for block RAM, which
is exactly what an instruction cache exists to hide.

## Files

| Path | What |
|---|---|
| `repos/cynthion/cynthion/python/src/gateware/analyzer/top.py` | the 9/56 design |
| `repos/cynthion/cynthion/python/src/gateware/facedancer/top.py` | the 45/56 design |
| `linux-on-cynthion/results/sweep_20260729.json` | 132 CPU configurations with BRAM per row. **That sweep's Fmax figures are withdrawn**; its area and BRAM rows stand but include SoC glue, so they are not a bare-core figure |
