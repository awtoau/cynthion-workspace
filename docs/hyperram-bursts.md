# HyperRAM bursts: the data phase cannot be stalled

**Index:** [`README.md`](README.md) · window and ports:
[`hyperram-next-step.md`](../debris/docs/hyperram-next-step.md) · rates and options:
[`memory-speed-options.md`](memory-speed-options.md) · decisions:
[`decisions.md`](decisions.md)

## The constraint

Once a HyperBus transaction's latency has expired, the device clocks a word
every CK. It does not wait. luna's `HyperRAMInterface` matches that by asserting
`write_ready` / `read_ready` on **every** cycle of `WRITE_DATA` / `READ_DATA`,
whether or not anything is listening.

**Holding a transaction open is therefore a promise to supply or consume a word
every cycle.** A master that cannot keep that promise must not coalesce.

This is a property of the controller's FSM and of the part, not of Wishbone. It
is the reason `HyperRAMWishbone` takes a `sustained` parameter rather than
always bursting.

## What breaks when the promise is broken

This SoC cannot keep it. `RegisteredResponse`
(`ecp5-test/riscv/wishbone_pipe.py`) withholds STB for one cycle after each
acknowledgement, so the CPU delivers **a beat every three cycles** against a
device consuming **two words every two**.

The deficit does not appear as a stall — nothing in the path can stall — it
appears as *extra words*. Per beat, from the cycle trace:

    cycle 20   beat 0 low half
    cycle 21   beat 0 high half + ack
    cycle 22   beat 0 low half AGAIN   <- STB withheld; req_data falls back to
                                          a dat_w the CPU has not advanced
    cycle 23   beat 1 high half        <- now one word out of step
    cycle 24   beat 1 low half

The duplicate is written to a real address. It also leaves `BootRAM`'s
`second_word`, which free-runs with the device, one ahead of the window's, which
resets per beat — so every following beat goes out high-half-first.

**A 64-byte line write emitted 48 device words instead of 32, clobbering 96
bytes of HyperRAM.** Reads were worse: 1 beat in 16 correct, each advancing
three words instead of two.

Measured identically in simulation and on the board:

    line write: 8/16 correct, bad 1010101010101010  want 200f0e0d got 0e0d200f

Even beats correct, odd beats with their two 16-bit halves transposed.

## Why it survived so long

Every path that could have caught it was blind to it:

* `bench hyperram` only **reads**.
* `hr test` and `hr read` move one 16-bit word at a time through the staging
  CSR, which never opens a multi-word transaction.
* `soc_hyperram_sim.py` covered burst **reads** and **single** writes. There was
  no burst-write case at all.
* A cross-port check writes and reads through the same path, so the read and
  write skews largely cancel — which is why a *total* read fault presented as a
  *half* write fault.
* Since `.text` moved to flash, firmware never writes a cache line to HyperRAM
  in normal operation.

The board had already measured it. `7351eb9` recorded `words 10848 against 3616
beats, exactly 3.0 per 32-bit beat`, flagged as not understood and explicitly
not to be quoted. Three words per beat is this fault, in the units it happens
in.

## What a simulation of this must include

A harness that drives `mmap.bus` directly models a master that replaces a beat
on the acknowledging edge — which this SoC is not. Such a harness reports
**16/16 correct** and the bug disappears.

`RegisteredResponse` must be in the simulated path. That single insertion is the
difference between a model that agrees with the board bit-for-bit and one that
contradicts it.

Related trap: `ModelHyperRAM16`'s data phase must enter when the controller's
does. It counted `HIGH_LATENCY_CLOCKS` where the controller loads
`HIGH_LATENCY_CLOCKS - 2`, and recorded **zero** words for a single write.
Reads never exposed it, because RWDS gates the controller's sampling and the
model simply waited; a write is not strobed, so the words went past while the
model was still counting.

## The cost of correctness, and where it goes back

`sustained=False` disables coalescing: every 32-bit beat becomes its own
transaction, and the burst path folds away at elaboration. Measured on the board
at `SYNC_MHZ = 60`, non-DQS PHY:

| | coalesced (corrupt) | one beat per transaction (correct) |
|---|---|---|
| window, 16 KiB sequential read | ~11 MB/s | 5.43 MB/s |
| four loads deep, 16 KiB sequential | 20.71 MB/s | 6.98 MB/s |
| beats per HyperBus transaction | 16 | 1.00 |
| 64-byte line write | 8/16 words | 16/16 |

Coalescing is worth 2–3x and is not available to a master that bubbles.
Recovering it needs one of: a master that never bubbles, a prefetch/FIFO deep
enough to source or sink a word per cycle for a whole line, or a controller that
accepts backpressure — which is only possible if CK may legally be gated
mid-burst.

`sustained` is a statement about the **master**, which is why it is a parameter
of the window rather than of the controller.

## Transaction overhead, in CK

Counted off the controller's states — 3 command words, 13 latency, 1 recovery —
not fitted to a measurement:

    one 64-byte line, coalesced      49 CK
    sixteen separate transfers      304 CK

**These are not the `19 CK` in [`memory-speed-options.md`](memory-speed-options.md).**
That is a board measurement of the *DQS* engine at 4:1 gearing, a different
quantity. Its former agreement with a `51 CK` figure here was a coincidence of
the two-cycle model error described above, and reading the two as the same
number is a mistake this paragraph exists to prevent.
