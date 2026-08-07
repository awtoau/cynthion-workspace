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
| `RECOVERY` fell to `IDLE` with no tCSHI gap | 2026-08-05 |
| pattern used only the low address bits — repeated 64× across the part | 2026-08-06 |
| the negative control armed **after** the engine started | 2026-08-06 |
| JTAG readback slips a bit below a sync/TCK ratio of ~4 (#204) | 2026-08-06 |

Casualties include the eight-tap `READCLKSEL` conclusion ("every tap showed the
same skew" — a larger error swamped all eight), every MB/s figure from the CK
ladder, and **313.5 MB/s at CK 180**, which was in the part doc as the verified
baseline. Re-measured, CK 180 fails in bulk with 4.7 M errors. That figure is
**wrong, not merely unverified**.

## What survives

Measured CPU-free with instruments that had themselves been checked:

- **DQS reads and writes correctly at CK 120 and CK 140** — 3.5 M words, zero
  errors, live negative control. The only trustworthy DQS numbers here.
- **The capture window moves with frequency.** Phase 2 is correct at CK 140;
  at CK 180 the best is phase 3.

| CK | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| 140 | 10⁶ | 10⁶ | **0** | 10⁶ | **0** | 10⁶ | 3×10⁴ | 10⁶ |
| 180 | 10⁶ | 10⁶ | 10⁶ | **4×10⁴** | stalled | 10⁶ | 10⁶ | 10⁶ |

  A ladder holding one phase measures where **that phase** stops working, not
  where the part does — which is what every earlier ladder here did. Two taps
  read clean at CK 140, so the window may be two wide; centring is not done.
- **The SoC fault is DQS-specific.** Identical firmware moves a 256-byte ramp
  perfectly on non-DQS (256/256) and corrupts it on DQS — eliminating the memory
  window, the D-cache, writeback and the harness in one measurement.
- **Non-DQS stops at CK 140 because OUR fabric misses timing**, not the part:
  non-DQS clocks the fabric at CK, DQS at CK/2.

## The unresolved contradiction

**BURSTDET disagrees between two harnesses on the same PHY.** The SoC reports
16,678–60,345 bursts; the ceiling harness reports **zero on every rung**,
including rungs that verify 50 M words with zero errors. Both cannot be right.

Leading explanation: a word-boundary bit-slip, which would make "strobe found"
and "data correct" independent. Never written up.

BURSTDET is the ECP5's own report that the read window is aligned — the one
signal that would validate a sweep. Until this is settled, a sweep has no
alignment signal.

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
