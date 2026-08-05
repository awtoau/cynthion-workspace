# HyperRAM burst writes corrupt every odd word (halves swapped)

A 32-bit Wishbone burst write to the HyperRAM window stores the two 16-bit
halves of every **odd-indexed** beat in the wrong order. Even beats are correct.

Present on the **non-DQS** PHY, which is the configuration in use.

## Reproduction

`hr cross` in the SoC shell (`bench::hyper_line_write_check`): write sixteen
consecutive `u32` through the memory window, evict the D-cache, read them back.

    line write: 8/16 correct, bad 1010101010101010 want 200f0e0d got 0e0d200f

The bitmap is bit-per-index, LSB = word 0. Odd indices fail; each failing word
has its halves transposed, not shifted -- the data is all present, in the wrong
order within the beat.

## Why it was never seen

* `bench hyperram` only reads.
* `hrtest` and `hr read` move one word at a time through the staging CSR, not
  the window.
* `soc_hyperram_sim.py`'s checks cover burst READS ("a 16-beat incrementing
  burst issues ONE HyperBus transaction") and single writes. There is no
  burst-write case.
* Since `.text` moved to flash, firmware never writes a cache line to HyperRAM
  in normal operation.

## Where to look

`HyperRAMWishbone` and `BootRAM` in `ecp5-test/riscv/vexii_bootram.py` each keep
their own `second_word` register tracking which half of the 32-bit beat is in
flight -- the window resets its copy on every beat, `BootRAM` free-runs its copy
across the whole burst. Two trackers of one fact is the obvious suspect, but
this has NOT been confirmed in simulation yet and should be before anything is
changed.

## Fix order

1. Add a burst-write case to `scripts/soc_hyperram_sim.py` and reproduce there.
2. Fix, and keep the case.
3. Re-run `hr cross` on the board for `16/16`.

Blocks the DQS work (#92), which was compounding this with a separate one-word
read skew.
