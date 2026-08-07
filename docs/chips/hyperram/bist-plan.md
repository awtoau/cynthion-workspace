# The HyperRAM BIST: what to measure, and how, so the answer is admissible

The plan for characterising the W956A8 on this board. The part itself is
[w956a8.md](w956a8.md); this is the **method**, and it exists because the method
is what has been wrong every previous time.

## Why start again

Every HyperRAM figure this project has recorded was taken with at least one
broken instrument. Not a caveat — a dating exercise:

| defect | fixed |
|---|---|
| controller sampled a full CK early (`HIGH_LATENCY_CLOCKS` 5 → 6) | 2026-08-05 |
| `RECOVERY` fell through to `IDLE` with no tCSHI gap | 2026-08-05 |
| the negative control armed AFTER the engine started | 2026-08-06 |
| JTAG register readback slips below a sync/TCK ratio of ~4 | 2026-08-06 |
| the test pattern repeated every 256 words, so addressing above bit 7 was untested | 2026-08-06 |

Nothing measured before those dates discriminates. The two harnesses that exist
disagree with each other about BURSTDET — one reports 60,345 detections, the
other zero, on the same PHY — and that disagreement alone invalidates every
ceiling claim that rests on either.

So this is not another experiment on the pile. It is a rig whose output would be
admissible, and its job is to re-establish everything from zero.

## The shape: standard SoC, HyperRAM on its own clock

**The CPU is the standard one.** Not a special bitstream, not a cut-down
measurement variant — the shell that boots, prints, and can be talked to. A
measurement rig you cannot interrogate while it runs is how a wedged engine
becomes an hour of bisection.

**The HyperRAM gets its own clock domain, and the CPU controls it.** This is the
part that has never existed. Today CK is derived from `sync`, so moving CK moves
the CPU clock, the console divisor and the CLINT tick with it — no two rungs of
any ladder were ever comparable. With `hr` on the second PLL:

    usb    60.000 MHz   oscillator, fixed, the ULPI PHY's requirement
    sync   the CPU      pinned wherever it builds cleanly, never moved to test memory
    hr     the part     what is under test

**Runtime control, not rebuild-per-rung.** A ~90 second synthesis per cell is
what made a full matrix impractical and what pushed the last attempt into a
gateware sweep engine that died at cell 0 with no way to see why. The CPU must be
able to change the setting and re-measure without a rebuild. `DCSC` is a
glitchless clock mux and `EHXPLLL` exposes dynamic phase shift
(`PHASESEL`/`PHASEDIR`/`PHASESTEP`/`PHASELOADREG`) — neither has ever been
driven here. What is runtime-adjustable and what is not is the first thing to
establish, because it decides the shape of everything below.

## The matrix

Five axes. The fifth is the one that was missed every previous time.

| axis | where it lives | runtime? |
|---|---|---|
| **CK frequency** | PLL1 output | needs `DCSC` or a rebuild — unestablished |
| **READCLKSEL** | DQS PHY, read capture tap | yes, a CSR today |
| **Device drive strength** | W956A8 CR0[14:12] | yes, a register write to the part |
| **Differential vs single-ended clock** | W956A8 CR1[6] | yes |
| **FPGA pin drive strength** | ECP5 IO attributes | **NO — bitstream only** |

### The FPGA drive strength axis, which was forgotten

There are **two** drive strengths in this system and only one was ever swept.
CR0[14:12] sets how hard the *memory* drives DQ back at the FPGA. The ECP5's own
output buffers have an independent setting — `Attrs(IO_TYPE="LVCMOS33",
DRIVE=...)` and `SLEWRATE` — governing how hard the *FPGA* drives CK, CS# and DQ
on writes.

Every ceiling measured so far holds the FPGA side at whatever the platform
resource happens to declare, and reports the result as a property of the part. It
is not. A write failure at high CK is at least as likely to be the FPGA's drive
into the trace as the memory's drive back.

It is a bitstream attribute, so it cannot be swept at runtime like the others.
That makes it the **outer loop**: a small number of builds, each sweeping the
runtime axes fully. Ignoring it is not neutral — it silently fixes one variable
and attributes its effect to the others.

## The rule that makes a result admissible

**A pass requires a negative control that ran and failed.**

Zero errors and a comparator that never fired produce the same number, and this
project has recorded the second as the first more than once. Every cell reports
one of:

    Pass       errors == 0 AND the control ran AND the control failed
    Fail(n)    n errors
    NoResult   anything else -- including a timeout, which is NOT a pass

`NoResult` must be distinguishable by cause. "The engine never completed" and
"the control did not fire" want completely different next steps and must never
print the same text.

Alongside that, the pattern must use **every address bit** and be invertible, so
a displacement, a duplication or a stuck line is read off the failure rather than
inferred. The 256-word repeating pattern is exactly how addressing above bit 7
went untested while reporting clean.

## What must be true before any number is recorded

1. `soc_probe` passes 6/6 — the fabric is ours, the CPU runs, and the gateware
   and firmware are the same commit.
2. The clocks are **measured**, not declared. `ClockMonitor` counts against the
   oscillator; a PLL that never locked once reported its intended frequency from
   a constant while the domain was not oscillating at all.
3. The two existing harnesses agree about BURSTDET, or one is withdrawn. They
   currently do not (#213).
4. There is a validated known-good reference. `hyperram_stress.py` was it, and
   has not been re-run since its pattern was fixed (#214).

## Open questions this plan does not answer

- Whether PLL phase shift works on this part through this flow (#228). If it
  does, the phase axis stops needing rebuilds entirely.
- Whether a Wishbone crossing between `sync` and `hr` is sound. It is new — the
  two are the same domain today — and this codebase has been bitten twice by
  clock-domain crossings, both times with the same quiet signature of correct
  counters and dropped data.
- Whether the DQS path's one-word-late read is a read-late or a write-early
  fault (#186). It is upstream of everything here: a rig that measures a path
  with a known offset measures the offset.
