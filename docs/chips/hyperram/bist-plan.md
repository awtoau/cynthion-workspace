# HyperRAM BIST plan

> **Audit 2026-08-10: [2026-08-10-audit.md](2026-08-10-audit.md)** — six faults
> found in our own instruments, three fixed. No measurement of the part is valid.

How to characterise the W956A8 and get a number worth keeping.
The part itself: [w956a8.md](w956a8.md).
What nineteen other controllers do, and the bounds ours is missing:
[survey.md](survey.md).

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

## Built, and where it is

The rig is on `main`, off by default:

    CYNTHION_HYPERRAM_BIST=1 ./scripts/soc_run.py

| | |
|---|---|
| engine | `gateware/probes/hyperram/hyperram_ceiling_top.py` — unchanged, shared with the JTAG applet |
| transport | `gateware/soc/peripherals/bist_csr.py` — CSR, parameters at +0x000, results at +0x100 |
| peripheral | `gateware/soc/peripherals/hyperram_bist.py` — engine in `hr`, bridge in `sync` |
| driver | `firmware/cynthion-soc/src/bist.rs` |
| commands | `bist status｜smoke｜cell｜sweep｜trace` |

Gates, both in seconds — the point is knowing in seconds whether the rig works:

| | | |
|---|---|---|
| `scripts/soc_bist_transport_sim.py` | 1.7 s | can the CPU read an engine-driven register across the domain boundary |
| `tests/test_bist_constants.py` | 0.01 s | gateware/firmware agree on base, window, burst, register numbers |
| `bist smoke` | seconds, on the board | four cells: can the rig both pass **and** detect a fault |

Both simulation gates are confirmed to FAIL on the corresponding known-bad, not
merely to pass.

## Isolation: the engine must not displace anything

> **SUPERSEDED for the first build.** Exclusive pins were chosen deliberately —
> get a number out of a measurement-only bitstream first, then do the mux. The
> requirement below stands as the target, and the failure chain it describes is
> still what makes an unbootable bitstream expensive to debug. See #226.

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
- **Four cells**, one CK, four values of an axis that is WIRED on this build —
  capture phase on a DQS build, latency code on a non-DQS one (#343). Four
  repeats can only meet the criteria below by being marginal.
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
| Known-good reference | **First one taken 2026-08-10**: non-DQS, CK 80, drive 3, phase 0, CR0 latency 2 and 6 pass on both fixed and variable, control fired 512/512. One run — see the repeatability caveat below. |
| #204 | **cleared 2026-08-07** — `jtag_registers.py` is TCK-clocked, so no ratio applies; `jtck` closes at 295.68 MHz. The fault was luna's, not JTAG's. |
| **#314** | **DQS edge clock ran on general fabric — closed 2026-08-10, and now checked on every build.** Fix was `CLKOS2` → `CLKOS` plus `a_BEL="X2/Y49/EHXPLL_LL"`; zero `ECLKBRIDGECS`, which sits downstream of the mux that rejected the source. Evidence is `arc: S1W2_ECLKI0 G_JLLCPLL0CLKOS` in `top.config` and zero `general routing will be used`. `soc_run.py` fails the build on either, via `soc_eclk_check.audit`. Results published before 7d88981 are still void. |
| **Repeatability** | The engine wedges on some reconfigures: one 128-cell sweep clean, the next producing nothing, same bitstream. **A result is not a reference until it reproduces.** |
| #215 | non-DQS controller vendored, tCSHI + latency — done 2026-08-07 |

## Open

- Does PLL phase shift work through this flow (#228)? If yes, the phase axis
  stops needing rebuilds.
- Is a Wishbone crossing between `sync` and `hr` sound? They are one domain
  today, and two CDC bugs here have already presented as correct counters with
  dropped data.
- Is the DQS one-word-late read a read-late or a write-early fault (#186)? A rig
  measuring a path with a known offset measures the offset. **The CA/latency
  parity explanation is refuted** (`57a9a99`): the data phase starts at edge
  `4 + 2 × L_ck`, which is a multiple of 4 at every legal fixed-latency code, so
  DQS aligns. The fault is above the PHY and #186 is still open.
- `READ_DATA` is unbounded, so a run that drops one beat parks the controller
  and the rig reports nothing (#316). Every cell below is measured through it.
  No implementation surveyed calibrates the read window, so the phase axis has
  no prior art to shortcut it — [survey.md](survey.md).

## The matrix

### Axes

| axis | values | count | runtime? |
|---|---|---|---|
| CK frequency | the in-spec PLL rungs at or under the fabric ceiling: DQS 100–180 is `100 120 140 144 150 160 168 180`, non-DQS 60–84 is `60 70 72 75 80 84` | **8** DQS / **6** non-DQS | one build per rung; `DCSC` carries two per build on non-DQS only |
| READCLKSEL tap | 0–7 | 8 | yes, CSR — **DQS builds only** |
| Read-window phase | 0–1 | 2 | yes, CSR |
| Device drive strength | CR0[14:12], 0–7 | 8 | yes, register write |
| Clock mode | CR1[6] differential / single-ended | 2 | yes, register write |
| FPGA pin drive | ECP5 `DRIVE` 4/8/12/16 mA | 4 | no — but **patchable**, see below |

**READCLKSEL is a DQSBUFM input, so it exists only where a DQSBUFM does.**
`HyperRAMPHY` (non-DQS) captures on `IDDRX1F` clocked by `sync` and takes no
phase argument; `hyperram_ceiling_top.py` passes the register to the PHY inside
`if self.dqs:` and nowhere else. On a non-DQS build the CSR is written and read
by nothing, so the 4096-cell matrix is **512 configurations run 8 times**
([#343](https://github.com/awtoau/cynthion-workspace/issues/343)).

- `./scripts/hyperram_axis_wiring.py` decides this from the elaborated design,
  needs no board, and is the only thing that can: `READCLKSEL` reaches the FPGA,
  not the part, so there is no read-back for `hyperram_axis_liveness.py` to use.
- `drive` (CR0[14:12]) and `clk` (CR1[6]) are wired on both builds — they reach
  the part through the engine's `CONFIG_CR0`/`CONFIG_CR1` writes, and the CR0/CR1
  read-back proves whether the part stored them.

### FPGA pin attributes are bitstream bits, not a rebuild

`DRIVE`, `PULLMODE`, `SLEWRATE`, `HYSTERESIS` and `OPENDRAIN` are `.config_enum`
entries in the Trellis PIO tile database — a handful of configuration frame bits
each, e.g. `PIOA.DRIVE` encoding 4/8/12/16 across `F4B1..F8B1`. No fabric-visible
register, so a running design cannot change them; but a **built** bitstream can
be patched and reconfigured with no resynthesis, the same trick
`scripts/bram_patch.py` uses for block RAM.

So the axis costs ~1 s per setting, not ~90 s, and four attributes that were
never in the matrix become affordable. Starting values and the open electrical
questions: **#311**.

Full cross product on the DQS path is **8 × 8 × 2 × 8 × 2 × 4 = 16,384 cells**,
so it is not run flat:

- FPGA drive is the outer loop — 4 bitstream patches, each sweeping everything
  runtime. Patches, not builds, per the section above.
- CK is a BUILD, one per rung, because `hr_fast` is an edge clock and the bank
  ECLK mux takes CLKOP/CLKOS only — 8 builds, then 8 × 2 × 8 × 2 = **512 cells**
  swept at runtime inside each.
- Coarse-then-fine does not apply: the rungs are 20 MHz apart at the bottom of
  the range and there is nothing between them to refine to (#313, #428).
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

## What the rig itself got wrong, and why it matters to read results carefully

Eight faults found on 2026-08-10, none a property of the part. The two that
would have silently corrupted a matrix:

- **The device was never configured.** `CONFIG_CR0` was reachable only from
  `RESET`, which runs once at power-up before any firmware exists. ~160 device
  configurations produced byte-identical results, because none was applied. An
  axis that does *nothing* is indistinguishable from an axis that does not
  *matter*, and the second reads as a finding.
- **The DQS edge clock was on general fabric** (#314), announced by nextpnr as
  `log_info` rather than a warning.

Both were invisible in the output. That is the standing lesson: **almost every
fault here was an instrument that could not report its own failure** — an
unbound register reading zero, a `done` that could not assert, a config path
never reached, plus three diagnostics that crashed formatting `None` instead of
printing what they had found.

Read a clean sweep with that in mind. `128 PASS` at one rung means *no axis
discriminates there*, not *everything is good*.

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
