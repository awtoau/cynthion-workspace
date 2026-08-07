# HyperRAM BIST plan

How to characterise the W956A8 and get a number worth keeping.
The part itself: [w956a8.md](w956a8.md).

The work: #230.

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

## Driving it: the CPU, from the console

**No sweep FSM in gateware.** The CPU sets the parameters, starts the pass,
polls, reads the counters and prints the row. Gateware runs one cell; the CPU
decides what the next one is.

- **One cell is one console command.** A single setting can be tried by hand,
  in isolation, without a sweep and without a rebuild.
- A sweep is the same command in a loop, so nothing is exercised in a sweep that
  was not first exercised alone.
- The last attempt put the loop in gateware. It died at cell 0 and there was no
  way to ask it anything.

### Every access is logged

Each register write and read goes to the console — address, value, and what it
means:

    hr cell ck=120 tap=2 phase=0 drive=3 mode=diff
      w CR0        f0000710 <- 0001b32f   drive 3, fixed latency
      w READCLKSEL f0000704 <- 00000002   tap 2, phase 0
      w PASSES     f0000708 <- 00000100
      w CONTROL    f000070c <- 00000001   go
      r STATUS     f0000700 -> 00000003   busy
      r STATUS     f0000700 -> 00000002   done
      r ERRORS     f0000714 -> 00000000
      r WORDS      f0000718 -> 00000080
      ... control pass ...
      PASS  0 errors / 128 words, control fired 128/128

- A hang then names the last access, rather than leaving a blank terminal.
- A register that reads back wrong is visible at the point it happens, not
  inferred from a bad result three steps later.
- Verbosity is the default. A quiet mode exists for long sweeps, never for
  bring-up.

### Time estimated before it runs

Print the estimate before starting, from cells × passes × words and the measured
rate:

    hr sweep ck=100..200 tap=0..7 phase=0..1
      26112 cells x 256 passes x 128 words @ ~120 MB/s
      estimated 41 min, plus 51 rebuilds if CK is not runtime-settable

- A sweep whose cost is only discovered by running it gets abandoned halfway,
  and half a sweep is not a result.
- If the estimate is hours, that is the signal to cut an axis or go coarse
  first — before spending them.

## Isolation: the engine must not displace anything

**Hard requirement: adding the test engine must not remove a single thing the
SoC has today.** Not the HyperRAM window, not BootRAM, not the staging path, not
the bootloader's behaviour. The SoC must boot, print and answer with the engine
present and idle.

The constraint that forces this:

- The HyperRAM pins take **one driver**. `platform.request("ram")` succeeds once.
- So the engine and the SoC's memory window cannot both own them by construction.

The way that went wrong before, and must not repeat:

- engine claims the pins → SoC's HyperRAM window must go
- BootRAM stages through HyperRAM → BootRAM must go
- no BootRAM → the bootloader's staging probe reads a window that is now absent
- VexiiRiscv traps an access to an address in no declared region → **silent board
  from reset**, no banner, no console

Each removal was forced by the one before it. The result was a bitstream that
could not be talked to, so a wedged engine was indistinguishable from a dead CPU.

**Resolution: share the pins, do not reassign them.** One requester, with a mux
selecting who drives:

- default — the SoC's controller, exactly as today
- test mode — the engine, selected by a CSR
- the window, BootRAM, staging and the bootloader are all **unchanged and still
  built**; they are simply not in use while a measurement runs

Acceptance for this stage, before any measurement is attempted:

- the SoC boots and `soc_probe` passes 6/6 with the engine present
- `hr` runs at a frequency unrelated to `sync`, confirmed by measurement
- the engine is idle and has moved nothing

## First test: four cells, 256 bytes

Before the matrix, prove the process. This is a test **of the rig**, not of the
part.

- **256 bytes** — 128 16-bit words. Milliseconds to run.
- **Four cells**, one CK, four capture phases. Everything else held.
- **Data derived from the address**, so a displacement is read off the dump
  rather than inferred. Byte at offset `i` encodes `i`.

The result that validates the rig is **not** four passes:

| outcome | what it means |
|---|---|
| at least one `Pass` | the rig can report success |
| at least one `Fail` | the rig can **detect** failure — without this, a pass is worthless |
| control fired in all four | zero errors means something in every cell |
| a `Fail` names an address | the pattern is doing its job |

- **Four passes is a failed smoke test.** It means either every phase is genuinely
  good — implausible — or the rig cannot see a fault. Widen the phase spread
  until one fails.
- **Four `NoResult` is a wedged engine**, not a bad part. Fix the rig.
- Pick the phase spread to straddle a suspected edge, so a pass and a fail are
  both likely. Prior settings are a hint for where to look, never a result.

Only once this behaves does the matrix mean anything.

## Method

- **Sweep the capture phase at every rung.** The window moves with frequency, so
  a fixed phase finds where that phase stops, not where the part stops.
- **Centre in the window.** An edge pass survives neither temperature nor a
  rebuild.
- **Do not trust BURSTDET yet.** It is meant to say the read window is aligned,
  so it should assert exactly where the data is correct. Observed, it does the
  opposite:
  - in the ceiling harness it asserts **only at the one phase that fails in
    bulk**, and reads zero at every phase that verifies clean
  - the SoC, on the same PHY, reports tens of thousands of detections
  - so it is currently anti-correlated with correctness in one harness and
    uncorrelated in the other
  - settle it before using it to centre a window, or it will centre on the wrong
    one
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

## The matrix

### Axes

| axis | values | count | runtime? |
|---|---|---|---|
| CK frequency | every even MHz 100–200 that the PLL reaches with `hr_fast = 2·hr` | **51** | needs `DCSC` (#228) |
| READCLKSEL tap | 0–7 | 8 | yes, CSR |
| Read-window phase | 0–1 | 2 | yes, CSR |
| Device drive strength | CR0[14:12], 0–7 | 8 | yes, register write |
| Clock mode | CR1[6] differential / single-ended | 2 | yes, register write |
| FPGA pin drive | ECP5 `DRIVE` 4/8/12/16 mA | 4 | **no — bitstream** |

Full cross product is **51 × 8 × 2 × 8 × 2 × 4 = 104,448 cells**, so it is not
run flat:

- FPGA drive is the outer loop — 4 builds, each sweeping everything runtime.
- Inner sweep per build is 51 × 8 × 2 × 8 × 2 = **26,112 cells**.
- Coarse-then-fine: walk CK in 10 MHz steps first, then refine around the edge.
- Drive and clock mode are likely separable — hold them while finding the CK/phase
  surface, then vary them at the edge. That is an assumption to test, not a given.

### Recorded per cell

| column | why it is there |
|---|---|
| `ck_mhz`, `readclksel`, `phase`, `drive`, `clk_mode`, `fpga_drive` | the cell's coordinates |
| `verdict` | `Pass` / `Fail(n)` / `NoResult` |
| `errors`, `words` | `words == 0` is NoResult, never a pass |
| `control_errors`, `control_words` | the control must have run **and** failed |
| `first_bad_addr`, `expected`, `got` | one bad word in ten million and ten million bad words are different faults |
| `burstdet` | the ECP5's own alignment report — contested, see Method |
| `dll_locked`, `dll_ready` | a rung that ran without a locked DLL is not a result |
| `die_temp_before`, `die_temp_after` | a rung that failed hot is not a clock limit |
| `read_cycles`, `write_cycles` | throughput, derived — not the headline |
| `timed_out` | distinguishes a wedged engine from a fired control |

### Reading the surface

- The pass region is a **surface**, not a ceiling. Report its shape.
- Per CK, report the **width** of the passing phase window, not just that one
  phase passed. Width is margin.
- A CK where the window is one tap wide is not the same result as one where it is
  three, even if both pass.
- The edge of the surface is where the interesting failure is: characterise it
  rather than just recording where passing stopped.

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
