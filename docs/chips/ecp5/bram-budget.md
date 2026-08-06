**The 12F is a 25F die** ([`lfe5u-12f.md`](lfe5u-12f.md)), so the datasheet says
32 `DP16KD` and the toolchain says 56.

prjtrellis's chipdb for `LFE5U-12F` holds **127 EBR tiles and 24,288 LUT4** —
the die's, not the marking's — so `nextpnr-ecp5 --12k` offers 56 blocks to every
design built for this part. Nobody opts into the extended space; the tool never
mentions the smaller number.

So percentages against 32 do not measure a limit anyone is exceeding — the open
flow enforces no such limit. Whether **Lattice** guarantees the extended space is
the real question, it is empirical, and #116 is where it is being answered.

Source for 32: the ECP5 family datasheet's `LFE5U12` column, "sysMEM Blocks
(18 Kb)". The same column gives **2/2 PLLs/DLLs**, which is what the second PLL
in `hyperram_clocks.py` relies on.

How much a design needs depends entirely on what the design is, and the split is
sharper than expected.

## Measured

All built for r1.4, same device, package and speed grade.

Both counts given, since the datasheet's and the toolchain's disagree.

| Design | `DP16KD` | of the datasheet's 32 | of the chipdb's 56 | LUT4 | What it is |
|---|---|---|---|---|---|
| USB analyzer | **9** | 28% | 16% | 8191 | capture at line rate |
| the SoC | **41** | 128% | 73% | 6811 | soft CPU + firmware |
| Facedancer SoC | **45** | 141% | 80% | 12824 | soft CPU + firmware |

**Facedancer is upstream's own design and it is in the third row.** Great Scott
Gadgets ship it on boards marked 12F and it works, which is the practical
evidence that the die really does carry the extra memory — they are not opting
into anything, they are building with the same chipdb.

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
