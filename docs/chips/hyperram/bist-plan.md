# HyperRAM BIST plan

How to characterise the W956A8 and get a number worth keeping.
The part itself: [w956a8.md](w956a8.md).

Absorbs #92, #148, #187, #188, #205, #210, #213, #214, #226.

## Shape

- **Standard SoC** — the shell that boots and can be interrogated.
- **HyperRAM on the second PLL**, CK controlled by the CPU.
- **Runtime, not rebuild-per-cell.** ~90 s synthesis per cell makes a full
  matrix impractical.

| domain | source | role |
|---|---|---|
| `usb` | oscillator | 60.000 MHz, ULPI's requirement |
| `sync` | PLL0 | the CPU — pinned, never moved to test memory |
| `hr` | PLL1 | the part, under test |

- CK must not derive from `sync`, or moving CK moves the CPU clock, console
  divisor and CLINT tick with it, and no two rungs are comparable.
- `READCLKSEL` + phase is already a CSR: 16 settings in milliseconds.
- CK and PLL phase joining it depends on `DCSC` and dynamic phase shift (#228).

## Matrix

| axis | where | runtime? |
|---|---|---|
| CK frequency | PLL1 | needs `DCSC` — unproven |
| READCLKSEL + read-window phase | DQS PHY, 16 combinations | yes, CSR |
| Device drive strength | W956A8 CR0[14:12] | yes |
| Differential vs single-ended clock | W956A8 CR1[6] | yes |
| FPGA pin drive strength | ECP5 `DRIVE`, `SLEWRATE` | no — bitstream |

**Two drive strengths, both in the matrix:**

- CR0[14:12] — how hard the **memory** drives DQ back.
- ECP5 output buffers — how hard the **FPGA** drives CK, CS# and DQ on writes.
- A write failure at high CK can be either one.
- The FPGA side is a bitstream attribute → **outer loop**: a few builds, each
  sweeping the runtime axes fully.

## Method

- **Sweep the capture phase at every rung.** The window moves with frequency, so
  a fixed phase finds where that phase stops, not where the part stops.
- **Centre in the window.** An edge pass survives neither temperature nor a
  rebuild.
- **Settle BURSTDET before sweeping.** It is the only alignment signal. Two
  harnesses on the same PHY currently disagree — one reports detections where the
  other reports none, on rungs the second scored clean. Leading theory: a
  word-boundary bit-slip, making "strobe found" and "data correct" independent.
- **Say whose limit it is.** Non-DQS clocks the fabric at CK, DQS at CK/2, so a
  non-DQS ceiling is usually ours. A rung with no bitstream is not a device
  result.

## Admissible results

- **A pass requires a negative control that ran AND failed.** Zero errors and a
  comparator that never fired produce the same number.

| verdict | condition |
|---|---|
| `Pass` | errors == 0 AND control ran AND control failed |
| `Fail(n)` | n errors |
| `NoResult` | anything else, including a timeout |

- A timeout is **not** a pass.
- `NoResult` must name its cause — "engine never completed" and "control did not
  fire" need different next steps and must not print the same text.
- The pattern must use **every address bit** and be invertible.
- **A HyperBus data phase cannot be stalled.** After latency the device clocks a
  word every CK regardless of listeners; a master that cannot keep up must not
  coalesce.

## Preconditions

| | |
|---|---|
| `soc_probe` 6/6 | fabric is ours, CPU runs, gateware and firmware match |
| Clocks measured, not declared | a dead PLL reports its intended rate from a constant |
| BURSTDET settled | one harness wrong, or the two measure different things |
| Known-good reference | `hyperram_stress.py` pattern is fixed but **not re-run** |
| #204 | JTAG readback slips a bit below sync/TCK ~4 — every applet reads through it |
| #215 | non-DQS controller vendored, tCSHI + latency — done 2026-08-07 |

## Open

- Does PLL phase shift work through this flow (#228)? If yes, the phase axis
  stops needing rebuilds.
- Is a Wishbone crossing between `sync` and `hr` sound? They are one domain
  today, and two CDC bugs here have already presented as correct counters with
  dropped data.
- Is the DQS one-word-late read a read-late or a write-early fault (#186)? A rig
  measuring a path with a known offset measures the offset.

## Why there are no old numbers here

Everything measured before August 2026 is deleted rather than marked unreliable.

- Five overlapping faults: reads landed a clock early; CS# re-asserted too soon;
  the pattern repeated every 256 words; the negative control armed after the
  engine started; JTAG readback dropped a bit at some clock ratios.
- Fixed at different times between 5 and 7 August 2026.
- Measurements span that whole period, so no figure can be matched to which
  faults were still present. Sorting good from bad afterwards is not possible.
- Deleted rather than annotated: a number restated as "withdrawn" is still a
  number, and one reached two draft submissions upstream before it was caught.
