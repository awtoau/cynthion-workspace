# The HyperRAM BIST: what is known, and how to measure the rest

The method for characterising the W956A8 on this board. The part is
[w956a8.md](w956a8.md); this is how to get a number worth keeping.

Absorbs #92, #148, #187, #188, #205, #210, #213, #214 and #226.

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

## Three things the earlier failures actually taught

Not numbers — these change how you measure, and each one invalidated a whole
class of result.

**Do not hold the capture phase fixed.** The read window moves with frequency, so
a ladder run at one phase finds where *that phase* stops working, not where the
part stops. Every ladder here did exactly that. Sweep the phase at each rung and
centre in the window rather than taking the first setting that passes — an edge
pass survives neither temperature nor a rebuild.

**BURSTDET is contested and must be settled first.** Two harnesses on the same
PHY disagree about it, one reporting detections where the other reports none,
including on rungs the second scored clean. Both cannot be right. The leading
explanation is a word-boundary bit-slip, which would make "strobe found" and
"data correct" independent — never written up. BURSTDET is the ECP5's own report
that the read window is aligned, so until this is resolved a sweep has no signal
to centre against.

**Separate our limit from the part's.** The non-DQS path caps out because *our
fabric* misses timing, not because the device does — it clocks the fabric at CK
while DQS clocks it at CK/2. Any ceiling must say which of the two it found, and
a rung that produced no bitstream is not a device result.

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

And the device's own rule, which constrains any master that drives it: **a
HyperBus data phase cannot be stalled.** Once latency expires the device clocks a
word every CK and the controller asserts `write_ready`/`read_ready` every cycle
regardless of listeners, so holding a transaction open is a promise to supply or
consume a word per cycle. A master that cannot keep it must not coalesce.

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

## Why there are no old numbers here

Everything measured before August 2026 has been deleted rather than marked
unreliable. The reason is not one mistake but several, overlapping:

- reads landed a full clock early, so data was captured before the part
  presented it
- CS# was re-asserted sooner than the part allows after a transaction
- the test pattern repeated every 256 words, so most of the address space was
  never actually checked — a fault that displaced data further than that scored
  as correct
- the negative control was armed after the engine had already started, so a
  clean result was not evidence of anything
- the JTAG readback dropped a bit at some clock ratios, so the numbers read back
  were not always the numbers the gateware held

These were fixed at different times between 5 and 7 August 2026. That spread is
the problem: the measurements were taken across the whole period, so there is no
way to tell which figure was taken with which fault still present. Sorting the
good from the bad after the fact is not possible, so none of it is kept.

They are deleted rather than annotated because a number restated as "withdrawn"
is still a number, and stays quotable by anyone skimming. One of them had already
reached two draft submissions to upstream before it was caught.
