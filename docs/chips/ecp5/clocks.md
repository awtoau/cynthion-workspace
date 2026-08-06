# Clocks: the PLL, the three domains, and the four traps

The canonical file. Clocking has been re-derived in this project more than once,
so what is settled is settled here and nowhere else.

The part: [`lfe5u-12f.md`](lfe5u-12f.md) · the board:
[`../../hardware.md`](../../hardware.md)

## The three domains, and what each is for

| domain | source | frequency | who needs it |
|---|---|---|---|
| `usb` | **the oscillator, passed through** | exactly 60.000 MHz | the ULPI PHYs |
| `sync` | PLL0 | the CPU's, pinned | the SoC |
| `hr`, `hr_fast` | PLL1 | swept | the HyperRAM |

**They are independent, and that is recent.** They used not to be, and the
coupling cost real measurements — see "what changed" at the end.

The board's primary clock is a **discrete 60 MHz oscillator on A8**, and the FPGA
**sources** the ULPI clock (`clk_dir='o'` on all three PHY resources in
`gateware/board/cynthion_r1_4.py`). So `usb` is the oscillator itself. Nothing
divides it, nothing solves for it, and there is no tolerance left to check.

Both PLLs take that oscillator as their reference. Feeding one PLL from another's
output would multiply jitter and serialise lock for nothing.

## Trap 1: `CLKFB_DIV` counts OUTPUT periods, not VCO periods

With `FEEDBK_PATH="CLKOP"`:

    VCO  = input * CLKFB_DIV * CLKOP_DIV / CLKI_DIV
    sync = input * CLKFB_DIV / CLKI_DIV          (independent of CLKOP_DIV)

Treating `CLKFB_DIV` as a plain VCO multiplier is what `variable_clock.py`
records as **having produced clocks at twice the requested rate**. It is the
single most repeated mistake in this tree's clocking.

## Trap 2: the ULPI PHY has no tolerance at all

A source-synchronous parallel interface has no resynchronising start bit and
nothing to absorb a frequency error with. Measured, not inferred:

| build | usb | result |
|---|---|---|
| sync 90 MHz | 63.000 MHz (+5%) | placed, packed, configured cleanly — **never appeared on the USB bus** |
| sync 100 MHz | 60.000 MHz | enumerated at once |

A *higher* CPU clock with an exact PHY clock worked where a lower one with a 5%
error did not. The failure mode is a silently dead device, which is why builds
used to be refused rather than warned about.

With `usb` taken from the oscillator this cannot happen by construction.

## Trap 3: output dividers are configuration-time, and one did not behave

`qspi_gateware.py` records both halves:

> the ECP5's PLL output dividers are programmed during configuration and are
> **not writable afterwards**

and, from a hand-built `EHXPLLL`:

> **`CLKOS_DIV=2` measured 480 MHz on the sync domain rather than 240**

So a frequency sweep is **one bitstream per rung**, and a hand-computed divider
is checked on hardware before anything is built on top of it.

## Trap 4: nextpnr's Fmax is not a property of the design

Two separate ways to misread it:

* **It is a function of the constraint you gave.** Rerouting one configuration at
  a 200 MHz target gave **82.6 MHz** against the **146.4 MHz** the same path
  reported at `--freq 25.0 --timing-allow-fail`. An entire archived sweep's
  timing results were discarded over this.
* **It is printed twice.** The first `Max frequency for clock` is a
  post-placement estimate and is pessimistic by 15–25 MHz — the 150 MHz build
  reads **139 MHz FAIL** before routing and **166 MHz PASS** after. A log parser
  taking the first or the worst match reports a failure on a passing build.

Related: nextpnr's refusal to vouch for a placement that misses its constraint is
policy, not inability. Amaranth's generated `build_top.sh` runs it under `set -e`
with no `--timing-allow-fail`, and regenerates that script on every build — so
patching the flag in is overwritten by the next run.

## The CPU's ceiling is NOT known, and the measurement that claimed it is withdrawn

A ladder run with `--timing-allow-fail` put four builds on the board and reported
the CPU corrupting its output above 60 MHz:

| requested | achieved | usb | reported |
|---|---|---|---|
| 60 | 72.6 / 89.0 MHz | 60.000 | PASS — product `369d0368`, ticks 0 → 1 |
| 90 | 86.1 MHz | 63.000 | does not enumerate |
| 100 | 92.0 MHz | 60.000 | enumerates, **output corrupted** |
| 110 | 96.0 MHz | 61.111 | enumerates, **output corrupted** |

At 100 MHz the console printed `tick00001` / `tk00002` / `rck 000003`, and the
conclusion drawn was that the counter still increments so the CPU is executing
while characters are dropped around it — a marginal design computing wrongly
rather than halting.

**That conclusion does not follow, because the console is in the path and it had
a bug with exactly this signature.** `stream_buffer.py`:

> A `SyncFIFOBuffered` between `sync` at 80 MHz and `usb` at 60 worked perfectly
> while both were 60 MHz, then produced a stream with **correct counter VALUES
> and dropped CHARACTERS — `tic 00000`, `tck 000001`** — because bytes vanished
> in transit while the arithmetic that produced them was untouched. That is the
> signature of an unsynchronised crossing.

Same symptom, same arithmetic-intact-characters-missing shape, and the same
trigger: it works when `sync == usb` and fails when they differ. The ladder's own
table is that pattern — 60 passes, everything above corrupts — which is what an
unsynchronised FIFO does, not what a timing-marginal CPU does.

`StreamBuffer` now takes `i_domain` and `o_domain` explicitly and is a genuine
asynchronous FIFO when they differ. **The ladder has not been re-run since.**

So: the CPU's working ceiling is **unmeasured**. nextpnr achieved 92 MHz on the
100 MHz build and 96 on the 110 MHz one, and whether either runs correctly is
open. Any claim that the RISC-V "tops out around 75 MHz" — including ones made
recently in this repo's commit messages — rests on this withdrawn measurement.

Re-running it is cheap: the same script, the fixed `StreamBuffer`, and a readout
that is not the console.

### And it is not one number — it moves with the CPU

Even re-measured, "the CPU's ceiling" is a property of a *configuration*, not of
the part. Measured on this SoC, three builds per configuration:

| change | `sync` fmax (min / median / max) |
|---|---|
| no branch predictor | 74.54 / 75.24 / 78.75 MHz |
| **+ BTB, relaxed** (what ships) | 64.23 / **71.81** / 72.88 MHz |
| before `RegisteredResponse` | 62.4 – 71.7 MHz, spread 9.3 |
| after `RegisteredResponse` | **79.2 – 80.7 MHz**, spread **1.4** |

So adding the branch predictor cost ~3.4 MHz of median, and inserting one
pipeline register in the bus response path bought ~8 MHz *and* collapsed the
run-to-run spread. Caches, the PLIC, an MMU and the peripheral set all move it
the same way.

**Two consequences.** A single build's number is not the ceiling — `pnr_noise.py`
records the same netlist spreading 8 MHz between placements, so a configuration
needs several builds before it has a figure at all. And `sync` should be pinned
with margin below whatever the current configuration reaches, then re-checked
when the configuration changes — which is the reason it is pinned rather than
tracked to the HyperRAM.

## The HyperRAM ladder

CK is the **device** clock. The DQS PHY gears 4:1 off `hr_fast` and emits two CK
per `hr` cycle, so `hr = CK / 2` there and `hr = CK` on the non-DQS path. Getting
that factor of two wrong is a mistake every ladder in this tree has made at least
once, so `HyperRAMDomains` takes the device number as its argument.

15 rungs are reachable between CK 100 and 200:

    100  102.9  120  137.1  140  144  150  154.3  160  168  171.4  180  188.6  192  200

An unreachable CK raises with the rungs either side of it rather than rounding.

## What changed, and why the old ladder was only three rungs

`VariableClockDomainGenerator` solved `sync` **and** `usb` from one PLL. Since
every output of a PLL divides the same VCO, requiring `usb` to be exactly 60.000
constrained `sync` to values whose VCO is a whole multiple of 60 — leaving **only
60, 100 and 120 MHz** reachable in the 60–130 range.

Worse for measurement: the HyperRAM's CK was `2 × sync`, so a ladder rung moved
the CPU clock, the console divisor, the CLINT tick, the flash SCK divisor and the
cache refill timing **at the same time as CK**. No two rungs were a controlled
comparison, and the part's ceiling and the core's ceiling were answering for each
other.

`gateware/soc/hyperram_clocks.py` is the current generator. See #226.
