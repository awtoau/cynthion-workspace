**Settled, and older than this project.** David Shah, `YosysHQ/prjtrellis#55`,
January 2019 — *"There isn't really any 12F silicon"*, and asked directly whether
all 25K LUTs are usable on a 12F: *"Yes, you can, however **I'd rather not have
this a highly advertised 'feature' of the Trellis flow at this point in time.**"*
That sentence is why it reads as folklore rather than documentation.

Great Scott Gadgets know too. Martin Ling, `greatscottgadgets/cynthion#185`:

> The 12F and 25F parts are actually the same die. They both have all 25K LUTs.
> The only thing that differs between them is the IDCODE… In *theory* the
> manufacturer could be binning the parts, with those marked 12F being ones that
> failed tests in one half of the LUTs. But in practice nobody has ever detected
> any difference that I know of.

`pluribus: docs/fabric-test.md` is where the consequence lives: the extra fabric
worked on the one part tested, but binning is bounded rather than excluded, so
"does my die work?" has no tool to answer it (#116).

**There is nothing to override, and nextpnr does enforce a limit — it enforces
the chipdb's.** From a build's own `top.tim`, invoked as plain
`nextpnr-ecp5 --12k --package CABGA256 --speed 8`:

    Info: 	                DCCA:       1/     56     1%
    Info: 	              DP16KD:       5/     56     8%
    Info: 	          MULT18X18D:       4/     28    14%
    Info: 	             EHXPLLL:       1/      2    50%

56 block RAMs, not the datasheet's 32, with no flag asking for it — and the
build would fail at 57. So "no limit" was the wrong way to put it: the limit is
the die's, because the chipdb is per-die, and a design cannot exceed it.

(`MULT18X18D: 4/28` in that same report is where the 32 was once mis-stated as
28. The multiplier count sits directly under the memory count.)

Whether a given die is *sound* is the separate, empirical question, and it is
pluribus's. **This file is only what our designs actually use.**

How much a design needs depends entirely on what the design is, and the split is
sharper than expected.

## Diamond enforces the marking; the open flow does not

Same device string, two different ceilings:

| flow | told | allows |
|---|---|---|
| `nextpnr-ecp5 --12k` | `LFE5U-12F` | **24,288 LUT4 / 56 EBR** — the die |
| Diamond | `LFE5U-12F` | **12,288 LUT4 / 32 EBR** — the marking |

Martin Ling, in `greatscottgadgets/cynthion#185`: *"If you use the proprietary
toolchain, it will enforce a limit of 12K LUTs when you select the 12F part."*

`scripts/diamond/flow.py` sets `DEVICE = "LFE5U-12F"`, which is correct and is
not bypassed. Our SoC is 7,249 LUT4 and fits either ceiling, so no Diamond run
here has met the limit.

**But it bounds what Diamond can be used to check.** Any design over 12,288 LUT4
cannot be built in Diamond for this part at all, so it cannot be cross-checked
against the open flow — and upstream's own facedancer, at ~12.5K LUT4 and 44
block RAMs, is already past both of Diamond's limits.

So a design that fits the die but not the marking cannot be built in Diamond by
asking for the part that is on the board — which is where a Diamond-as-oracle
comparison would otherwise stop.

### The workaround: target the 25F, write it to the 12F

Diamond's refusal is a per-device rule in the tool, not anything about the
silicon. Ask it for an `LFE5U-25F` and it places into all 24,288 LUT4 and 56 EBR,
because that is one die and Trellis's own database says so:

    LFE5U-12F    idcode 0x21111043   frames 7562  bits/frame 592  max_row 50  max_col 72
    LFE5U-25F    idcode 0x41111043   frames 7562  bits/frame 592  max_row 50  max_col 72

**Identical frame count, identical frame width, identical grid.** The
configuration is the same size and the same shape; the only difference in the
whole record is the top nibble of the IDCODE, `2` against `4`.

So the bitstream a 25F build produces is loadable on the 12F-marked part. What
stops it is the loader's IDCODE check, not the device: the part reports
`0x21111043` and the bitstream asks for `0x41111043`. Reconcile those — patch the
top nibble, or configure with a loader that does not verify it — and it runs.

**Two things this is not.** It is not a way to get more silicon: the fabric is
the same either way, and the open flow already reaches all of it without any of
this. And it is not free of the binning question — a 25F-targeted build placed
into the upper half is exactly the region a salvage die would have failed on
(#116).

What it *is*: the way to build a design in Diamond that the open flow will build
and Diamond otherwise refuses, so the two can be compared at all above 12,288
LUT4.

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
