# Connecting flash and HyperRAM to a RISC-V: the options

Written because this is where the HyperRAM work stalled, and because the
terminology hides what the actual choice is.

## The thing the jargon obscures

**Wishbone is not one of the options.** It is the bus standard that the options
use to reach the CPU. The real question is *what shape* the peripheral presents,
and there are three answers:

### 1. Memory-mapped

The device appears as an address range. The CPU issues an ordinary load and the
hardware turns it into a flash or RAM transaction. The CPU never knows the
peripheral exists.

- **For:** code can execute directly from it (XIP); no driver; this is how a
  RISC-V would run a program stored in flash.
- **Against:** every access stalls the CPU for the device's full latency; poor
  for bulk transfer.
- **Status:** `SPIFlashMemoryMap` already exists in luna_soc, described in its
  own docstring as a "Wishbone Memory-mapped SPI Flash controller".

### 2. Register-driven

The CPU writes a descriptor -- address, length, direction -- into control
registers, then polls or waits for an interrupt.

- **For:** efficient for bulk moves; the CPU can do other work meanwhile.
- **Against:** needs a driver; cannot execute code from it directly.
- **Status:** this is essentially what `QuadFlashReader` already is, minus a CPU
  to drive it.

### 3. DMA

A separate engine moves data between devices without the CPU touching it.

- **For:** fastest for bulk copy; CPU free throughout.
- **Against:** the most gateware, and area on an `LFE5U-12F` is not abundant.
- **Status:** not present.

These are layers rather than rivals. A working system plausibly wants
memory-mapped flash for execution *and* a register-driven path for bulk
transfers.

## Where the actual gap is

| | Flash | HyperRAM |
|---|---|---|
| Low-level driver | LUNA `ECP5ConfigurationFlashInterface`, Glasgow QSPI | LUNA `HyperRAMInterface` |
| Wishbone peripheral | **`SPIFlashMemoryMap`** | **none** |

That asymmetry is the whole reason the HyperRAM path felt stuck while the flash
path did not. Flash has a peripheral ready to attach to a CPU; HyperRAM has only
the raw interface, and something has to bridge it.

## What a HyperRAM Wishbone wrapper involves

`HyperRAMInterface` exposes a straightforward request/strobe interface:

    address[32], perform_write, register_space, single_page,
    start_transfer, final_word, write_data[16], read_data[16],
    idle, read_ready, write_ready

Translating Wishbone `cyc`/`stb`/`we`/`ack` onto those is a modest state
machine, and the sequencing is already proven -- `ecp5-test/hyperram/` drives
every one of these signals correctly across bulk, retention and random-access
tests with zero errors.

Two decisions inside it are not trivial:

**Width.** Wishbone to a RISC-V is 32 bits; HyperRAM is 16. Every CPU word is
two device transfers, so the wrapper must pair them and get the byte order
right, or every value comes back halved and swapped.

**Latency, which is the important one.** Measured on r1.4 at 120 MHz:

| Access pattern | Cost |
|---|---|
| Streaming, per 16-bit word | 1.01 cycles |
| Fixed overhead per transaction | ~23 cycles |
| **Single random 32-bit read** | **~27 cycles** |
| **32-bit read within a stream** | **~4 cycles** |

A single-word Wishbone read pays the command-and-latency phase *every time*, so
random access is roughly **7× worse than streaming**. That is a property of
HyperRAM, not of the wrapper, and no bus design removes it.

## The consequence

The bus wrapper is the easy part. What determines whether a CPU running from
HyperRAM is usable is **whether it has a data cache in front of it** -- a cache
turns scattered 27-cycle accesses into occasional line fills that amortise the
overhead across 8 or 16 words.

That is not a new question here: the RV32 equivalence report already names
`+i4k`/`+d4k` as toggles to measure one at a time, and found that varying them
together with other features is why its comparison was not conclusive. So the
cache decision is shared between the CPU work and the memory work, and should be
measured once for both.

## Suggested order

1. **HyperRAM Wishbone wrapper** -- unblocks CPU access to RAM at all, and the
   underlying sequencing is already verified.
2. **Attach `SPIFlashMemoryMap`** -- already written; gives execute-from-flash.
3. **Measure with and without a data cache** -- the single highest-value number,
   and it settles a question the CPU work also needs answered.
4. **DMA only if bulk copy proves to be the bottleneck**, which it may not.
