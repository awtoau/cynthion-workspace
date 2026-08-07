# The HyperRAM BIST: what is known, and how to measure the rest

The method for characterising the W956A8 on this board. The part is
[w956a8.md](w956a8.md); this is how to get a number worth keeping.

Absorbs #92, #148, #187, #188, #205, #210, #213, #214 and #226.

## The shared constraint

**A HyperBus data phase cannot be stalled.** Once latency expires the device
clocks a word every CK, and the controller asserts `write_ready`/`read_ready`
every cycle regardless of listeners. Holding a transaction open promises a word
per cycle; a master that cannot keep that promise must not coalesce. Most of what
follows is downstream of this.

## Every measurement before 2026-08-06 is void

A dating exercise, not a caveat:

| defect | fixed |
|---|---|
| controller sampled a full CK early (`HIGH_LATENCY_CLOCKS` 5 → 6) | 2026-08-05 |
| `RECOVERY` fell to `IDLE` with no tCSHI gap — **DQS path only** | 2026-08-05 |
| the same, on the **non-DQS** path: the baseline, and what the SoC ships (#215) | 2026-08-07 |
| pattern used only the low address bits — repeated 64× across the part | 2026-08-06 |
| the negative control armed **after** the engine started | 2026-08-06 |
| JTAG readback slips a bit below a sync/TCK ratio of ~4 (#204) | 2026-08-06 |

Casualties include the eight-tap `READCLKSEL` conclusion ("every tap showed the
same skew" — a larger error swamped all eight) and **every MB/s figure from the
CK ladder**, including the one that was in the part doc as the verified
baseline. Re-measured, its best rung fails in bulk with 4.7 M errors.

Those numbers are **deleted, not annotated**. A figure restated as "withdrawn"
is still a figure, and stays quotable by anyone skimming — this one had already
reached two draft submissions upstream before it was caught.

## No measurement is carried forward

**Every figure this project has recorded for this part is void**, and none is
restated here. Not the throughput ladder, not the per-phase error counts, not the
BURSTDET totals, not the CK ceilings. The apparatus had up to five faults at once
and the numbers were produced across that whole period; separating a good rung
from a bad one after the fact is not possible.

A number restated as "withdrawn" is still a number and stays quotable by anyone
skimming. One of them had already reached two draft submissions upstream before
it was caught. So they are deleted.

What survives is not data but **three things the failures taught about method**:

**Do not hold the capture phase fixed.** The read window moves with frequency, so
a ladder at one phase measures where that phase stops working, not where the part
does. Every ladder here did exactly that. The phase must be swept at each rung,
and the result centred in the window rather than taken at the first setting that
passes — an edge pass survives neither temperature nor a rebuild.

**BURSTDET is contested and must be settled first.** Two harnesses on the same
PHY disagree about it, one reporting detections where the other reports none,
including on rungs the second scored clean. Both cannot be right. The leading
explanation is a word-boundary bit-slip, which would make "strobe found" and
"data correct" independent — never written up. BURSTDET is the ECP5's own report
that the read window is aligned, so until this is resolved a sweep has no signal
to centre against.

**Separate our limit from the part's.** The non-DQS path caps out because *our
fabric* misses timing, not because the device does — it clocks the fabric at CK
while DQS clocks it at CK/2. Any ceiling must state which of the two it found,
and a rung that produced no bitstream is not a device result.

## The shape

**Standard SoC.** The shell that boots, prints and can be interrogated. Not a
cut-down measurement bitstream: a rig you cannot talk to while it runs is how a
wedged engine becomes an hour of bisection. The previous attempt was a gateware
sweep FSM that died at cell 0 with the controller in IDLE and the engine in
`READ_RECOVER`; nine handshake defects were found in it and a tenth was not.

**HyperRAM on its own clock, controlled by the CPU.** Today CK derives from
`sync`, so moving CK moves the CPU clock, console divisor and CLINT tick — no two
rungs were ever comparable. With `hr` on the second PLL:

    usb    60.000 MHz   oscillator, the ULPI PHY's requirement
    sync   the CPU      pinned, never moved to test memory
    hr     the part     under test

**Runtime, not rebuild-per-cell.** ~90 s of synthesis per cell is what made a
full matrix impractical. `READCLKSEL` is already a runtime CSR — 16 settings in
milliseconds instead of 16 builds. `DCSC` (glitchless clock mux) and `EHXPLLL`
dynamic phase shift are unproven here (#228) and decide whether CK and phase can
join it.

## The matrix

| axis | where | runtime? |
|---|---|---|
| CK frequency | PLL1 | needs `DCSC` — unproven |
| READCLKSEL + read-window phase | DQS PHY, 16 combinations | **yes, CSR today** |
| Device drive strength | W956A8 CR0[14:12] | yes |
| Differential vs single-ended clock | W956A8 CR1[6] | yes |
| **FPGA pin drive strength** | ECP5 `DRIVE`, `SLEWRATE` | **no — bitstream** |

### The axis that was missed

Two drive strengths exist and only one was swept. CR0[14:12] sets how hard the
**memory** drives DQ back; the ECP5's output buffers set how hard the **FPGA**
drives CK, CS# and DQ on writes. Every ceiling so far holds the FPGA side at
whatever the platform resource declares and reports the result as a property of
the part. A write failure at high CK is at least as likely to be the FPGA's drive
into the trace.

It is a bitstream attribute, so it is the **outer loop**: a few builds, each
sweeping the runtime axes fully. Ignoring it fixes one variable silently and
attributes its effect to the others.

## What makes a result admissible

**A pass requires a negative control that ran and failed.** Zero errors and a
comparator that never fired produce the same number, and this project has
recorded the second as the first more than once.

    Pass       errors == 0 AND the control ran AND the control failed
    Fail(n)    n errors
    NoResult   anything else — including a timeout, which is NOT a pass

`NoResult` must be distinguishable by cause: "engine never completed" and
"control did not fire" need different next steps and must not print the same
text.

The pattern must use **every address bit** and be invertible, so a displacement
or a stuck line is read off the failure rather than inferred.

## Preconditions

1. `soc_probe` 6/6 — fabric is ours, CPU runs, gateware and firmware match.
2. Clocks **measured**, not declared. A PLL that never locked once reported its
   intended frequency from a constant while the domain was not oscillating.
3. BURSTDET settled — one harness shown wrong, or the two shown to measure
   different things.
4. A validated known-good reference. `hyperram_stress.py` was it; its pattern
   used an 8-bit address and repeated every 256 words, and it **has not been
   re-run since the fix**.
5. The non-DQS controller vendored with the tCSHI and latency fixes (#215) — it
   is the baseline, and `bootram.py` ships it.

## Open

- Does PLL phase shift work through this flow (#228)? If so the phase axis stops
  needing rebuilds.
- Is a Wishbone crossing between `sync` and `hr` sound? New — they are one domain
  today — and this codebase has been bitten twice by CDC, both times with correct
  counters and dropped data.
- Is the DQS one-word-late read a read-late or a write-early fault (#186)? A rig
  measuring a path with a known offset measures the offset.
