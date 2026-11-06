# ECP5 `LFE5U-12F` — the FPGA, and it is a 25F die

The main programmable device on Cynthion r1.4. Lattice ECP5, marked `LFE5U-12F`,
CABGA256.

**We build for the 25F die at speed grade 8, and that is settled.** The marking
understates the die (below), and the part's own grade marking is 6. Both are
known. The flow targets 25F/-8 and every number here is in those terms; divide
by ~1.3 for a board-relative figure. Revisit only if a measured failure points
at the grade -- not otherwise, and not as a caveat on individual results.

**Index:** [`../hardware.md`](../../hardware.md)

## The headline: the part marked 12F is a 25F die

**The marking understates the die by 1.98× in LUT4 and 1.75× in block RAM.**
That is the durable fact about this part, and it does not move when anything is
rebuilt.

| what | by the marking (`LFE5U-12`) | on the die (`LFE5U-25`) | ratio |
|---|---|---|---|
| LUT4 | 12,288 | **24,288** | 1.98× |
| sysMEM `DP16KD` blocks | 32 (576 Kb) | **56** (1,008 Kb) | 1.75× |
| 18×18 multipliers | 28 | 28 | — |
| PLLs | 2 | 2 | — |

Marking figures from the family datasheet, **FPGA-DS-02012-1.9 Table 1.1**
(`sources/Lattice-ECP5-Family-DataSheet-FPGA-DS-02012.pdf`). Die figures from
`nextpnr-ecp5 --12k`, whose chipdb is per-die, and confirmed on the part itself:
IDCODE `0x21111043` from `apollo jtag-scan` and read back out of the bitstream by
`ecpunpack`, with the part still reporting as `LFE5U-12F`.

**The extra capacity is in LUT and block RAM only.** DSP and PLL counts are
identical on the two dies, so nothing on this page about multipliers or clocking
gains from the die being larger than the marking.

**No patching is involved.** `ecppack` writes the genuine 12F IDCODE. The
vendor's own files say the dies are the same: byte-identical `.con` package
files, identical `frames × bits_per_frame`, byte-identical Trellis
`tilegrid.json`, IDCODEs differing only in the top nibble.

- **Trap: the packed IDCODE follows the platform's `device` string, nothing else** ([cynthion#185](https://github.com/greatscottgadgets/cynthion/issues/185)). `LFE5U-12F` packs `0x21111043` (this board), `LFE5U-25F` packs `0x41111043`; the `.bit` is the same size either way, and an installed non-editable package keeps the old string. `usercode_map.bitstream_problems` compares the packed `.bit` with the build record at build time, and `soc_confirm.part_gate` checks both against the board's IDCODE before any configure or flash write.

## Performance

Structure and rules: [`../../plans/performance-sections.md`](../../plans/performance-sections.md).

**This part is what binds almost everything else on the board**, so the pin-level
section below is the useful half. The FPGA is rarely the thing that runs out —
the pin it has to leave through usually is.

### 1. Theoretical maximum

Speed grade **8**, the fastest ECP5 bin, and 12F and 25F share it. All from
FPGA-DS-02012-1.9.

| axis | −8 figure | table |
|---|---|---|
| register-to-register, 16-bit adder / 4:1 mux | 441 MHz | 3.20 |
| register-to-register, 16-bit counter | 384 MHz | 3.20 |
| register-to-register, 64-bit counter | 263 MHz | 3.20 |
| `DP16KD` true-dual-port with output registers | 272 MHz | 3.20 |
| `DP16KD` read-before-write | 214 MHz | 3.20 |
| 18×18 multiplier, all registers | 225 MHz | 3.20 |
| primary clock tree `fMAX_PRI` | 370 MHz | 3.22 |
| edge clock tree `fMAX_EDGE` | 400 MHz | 3.22 |
| I/O and PFU register `fMAX_IO` | 400 MHz | 3.22 |
| `fMAX_GDDRX2` (ECLK), `fDATA_GDDRX2` | 400 MHz, **800 Mb/s per pin** | 3.22 |
| PLL `fVCO` | 400–800 MHz | 3.23 |
| PLL `fOUT` (CLKOP, CLKOS) | 3.125–400 MHz | 3.23 |
| PLL `fPFD`, phase detector input | **10**–400 MHz | 3.23 |
| PLL `fIN` (CLKI, CLKFB) | 8–400 MHz | 3.23 |

**Table 3.20 is itself the answer to "what is the fmax".** 441 MHz for a 16-bit
adder and 263 MHz for a 64-bit counter, same silicon, same bin — a 1.68× spread
from the design alone, before a single net is routed. There is no one number, and
any page that quotes one has picked a design without saying so.

### 2. Achievable on this board — and it is a pin, five times out of six

**The I/O standard is the binding constraint on every fast signal here, and it
is not a choice the gateware can revisit.** Table 3.21, Maximum I/O Buffer Speed:

| buffer | max input | max output |
|---|---|---|
| LVDS25, SSTL15/18, SSTL135, HSUL12 | 400 MHz | 400 MHz |
| **LVCMOS33 (for all drives)** | **200 MHz** | **150 MHz** |
| LVDS25E, LVPECL33, BLVDS25 — *emulated* | — | 150 MHz |

Table 3.21 note 6: *"Maximum data rate equals 2 times the clock rate when
utilizing DDR."* So on LVCMOS33 the published per-pin DDR rate is

    output:  150 MHz × 2 = 300 Mb/s per pin
    input:   200 MHz × 2 = 400 Mb/s per pin

against 800 Mb/s for GDDRX2 on the standards the DDR tables were characterised
on. **The board is 2.7× down on output before any gateware exists**, because
`cynthion_r1_4.py` declares `IO_TYPE="LVCMOS33"` on every resource that matters —
and it has to, because the parts on the other end are 3.3 V CMOS devices.

Two more notes on that table that bear directly on what we run:

- **Note 4: "All speeds are measured at fast slew."** So the 150/200 MHz figures
  apply only to pins carrying `SLEWRATE="FAST"`. In `cynthion_r1_4.py` that is
  five declarations and no more — the three ULPI PHYs, the HyperRAM, and the
  mezzanine header. The PMODs, the LEDs, the UART and the I²C buses are default
  slew, and the datasheet gives **no number at all** for them.
- **Note 2: for emulated differential outputs the maximum speed "depends on the
  layout."** The HyperRAM clock is `LVCMOS33D` on `C3`/`D3` — *emulated*
  differential through external resistors, not a true LVDS driver. The datasheet
  refers LVCMOS33D back to the LVCMOS33 DC specifications and declines to give it
  a frequency, so **the CK pair has no vendor ceiling, only the single-ended
  150 MHz to reason from.**

#### The pins that bind, named

**`USRMCLK` — the flash clock, and it is the sharpest case on the board.**
SCK reaches the [W25Q32](../w25q32-config-flash.md) only through the `USRMCLK`
macro; the ball is not a PIO and there is no alternative route. Two consequences,
both permanent:

- **No hard DDR register.** nextpnr refuses an `ODDRX1F` whose `Q` does not drive
  a `TRELLIS_IO`, and underneath that refusal the CCLK site has no
  `DATAMUX_ODDR`/`IOLDO` mux in the Trellis routing database and `JA4` carries no
  `G_HPBX` global-clock source. This is architecturally impossible, not a tool
  limitation — see the section further down. So **SCK is a fabric-rate signal,
  and the flash's rate is bounded by the SoC's own fmax rather than by the
  flash.**
- **No vendor number.** `USRMCLK` does not appear in the datasheet. `fCCLK`
  62 MHz is the *configuration engine's* master-clock output in the sysCONFIG
  port timing table, a different path — see the section further down, which
  retires that figure.

**Hard DDR registers exist for the HyperRAM and are used.** The [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core
(`gateware/hyperram_baseline/hyperbus.py`) gears 2:1 through `ODDRX1F`/`IDDRX1F`,
read data through `DELAYF`. `USRMCLK` above is the one place where DDR cannot be used.

**R11/T11 and R14/T14 are the same wires, so the console's rate is arbitration.**
The ECP5's JTAG TDI/TMS pads are the Apollo UART's rx/tx pads, and on the MCU
side PA10/11/14/15 are shared three ways (JTAG, SPI, UART). Nothing about the
fabric sets the console rate: `APOLLO_UART_BAUD` is 115200, which is **11.52 kB/s
beside a 60 MHz CPU**, and the mitigation for the sharing is a policy of never
transmitting unbidden rather than a faster link. See the pin-sharing section of
[`../../hardware.md`](../../hardware.md).

#### The fabric limits, after the pins

**Two PLLs on this die, not four** (Table 1.1, and the 25F die does not add any).
One is `SocClocks` in `gateware/soc/clocks.py`, giving `sync` and `fast`; the
second clocks the HyperRAM core (`hr`, `hr90`, CK 180, [#588](https://github.com/awtoau/cynthion-workspace/issues/588)). There is no third, so any future independent domain
displaces one of those two.

**The PLL's reachable set is wider than this tree says, and out of spec for most
of it.** `solve_pll` searches CLKI_DIV and CLKFB_DIV to 128 rather than taking
`ecppll`'s first answer, and from the board's 60 MHz oscillator it reaches
**every integer MHz from 63 to 130 exactly** — 68 of 68, and 68 of 68 again with
`fast` pinned at 2× `sync`.

**But 55 of those 68 put the phase detector below the datasheet's 10 MHz `fPFD`
minimum**, as low as **1.0 MHz** at `sync` = 67 MHz, and Table 3.23 note 3 states
that the period and cycle-to-cycle jitter figures are not guaranteed below
10 MHz. `solve_pll` checks the VCO window and not `fPFD`. Since `fPFD` is
`60 / CLKI_DIV`, the thirteen in-spec points are those reachable with
CLKI_DIV ≤ 6:

    70  72  75  80  84  90  96  100  105  108  110  120  130

`sync` = 60 MHz, as shipped, runs the phase detector at the full 60 MHz.

### 3. Measured

**Utilisation and timing** — one SoC build, `tmp/awto_soc/build/top.tim` and
`top.config`, `sync` constrained to 60 MHz:

| resource | used | of | % |
|---|---|---|---|
| `TRELLIS_COMB` (LUT4) | 14,755 | 24,288 | 60.7% |
| `TRELLIS_FF` | 7,669 | 24,288 | 31.6% |
| **block RAM (`DP16KD` + `PDPW16KD`)** | **44** | **56** | **78.6%** |
| `MULT18X18D` | 4 | 28 | 14.3% |
| `EHXPLLL` | 1 | 2 | 50% |

**Count the block RAMs by mode, not by one mode.** Two of the 44 are
`PDPW16KD`, so `grep -cE "^enum: EBR[0-9]+\.MODE DP16KD" top.config` returns 42
and undercounts by two. `grep -cE "^enum: EBR[0-9]+\.MODE " top.config` is the
figure, and it agrees with nextpnr's own `44/56` line.

**Fmax** is a distribution, not a number. `scripts/soc_timing_sweep.py` runs
place-and-route three times per configuration because `--parallel-refine` mutates
a shared placement across sixteen threads and the same netlist has spread 8 MHz
between runs:

| configuration | LUT | FF | BRAM | fmax min / median / max |
|---|---|---|---|---|
| SoC, no branch predictor | 12,508 | 6,554 | 42 | 74.54 / 75.24 / 78.75 MHz |
| SoC, BTB relaxed + relaxed branch (ships) | 12,903 | 6,942 | 44 | 64.23 / **71.81** / 72.88 MHz |
| fabric test, 20,143 LUT4 | — | — | — | 86.43 MHz, one run |

**HyperRAM DQ rate:** the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) reference measurement is
[`../hyperram/sequential-baseline.md`](../hyperram/sequential-baseline.md).

**Flash SCK: 144 MHz measured**, `0xEB` continuous, 71.70 MB/s — through
`USRMCLK`, with the design closing at 149 MHz. The instrument ran out before the
flash did.

**Never measured:** the DSP blocks, and LVCMOS33 at *slow* slew, which is what
most pins carry.

### 4. The gap, and what closes it

1. **Nothing closes the LVCMOS33 gap except a board revision.** 300 Mb/s per pin
   out against 800 for GDDRX2 is the I/O standard, and the standard is set by the
   3.3 V parts on the other end. Worth 2.7× on paper and unavailable in practice.
2. **Block RAM is the binding resource, at 78.6%.** Moving the 64 KiB of firmware
   out of block RAM — execute in place from flash — frees about
   32 blocks, which is more than the entire USB analyzer design uses. Worth: it
   is the only thing that makes 16 KiB caches fit. Cost: fetch latency, which is
   what the I-cache exists to hide. See
   [`bram-budget.md`](bram-budget.md).
3. **Clock: about 20% is sitting in the placement statistic.** nextpnr's median
   is 71.81 MHz against the 60 MHz constraint the design ships at, and the CPU's
   real ceiling is **unmeasured** — [`../../soc-clocking.md`](../../soc-clocking.md)
   §2 withdraws the "corrupts above 60 MHz" result. Re-running the ladder is
   cheap.
4. **`fPFD` below 10 MHz is an unpriced risk, not a gap.** Any `sync` outside the
   thirteen in-spec points runs the phase detector out of spec. Worth: unknown —
   nothing has been measured that would show it, and jitter is exactly the kind
   of fault that presents as something else.
5. **LUT is not a gap.** 60.7% used, and the die carries twice what the marking
   promises. Nothing on this board is LUT-bound.

### Summary

| path | theoretical | board max | measured | % of board max | what closes the gap |
|---|---|---|---|---|---|
| LUT4 | 24,288 on the die (12,288 by the marking) | 24,288 — the marking is enforced nowhere in the flow | 14,755 in the SoC; 20,143 placed and verified once | 61% / 83% | nothing; not the binding resource |
| block RAM | 56 × 18 Kb = 1,008 Kb (32 by the marking) | 56 | **44** (`top.config`, all EBR modes) | 79% | firmware out of block RAM: ~32 blocks |
| fabric fmax | 441 MHz, 16-bit adder (Table 3.20) | 370 MHz, primary clock tree | 71.81 MHz median, 3 P&R runs, SoC netlist | 19% | design, not placement — see the CPU page |
| DDR, DQ pin | 800 Mb/s (`fDATA_GDDRX2`, −8) | **300 Mb/s out / 400 in** (LVCMOS33, Table 3.21 + note 6) | [`sequential-baseline.md`](../hyperram/sequential-baseline.md) ([#581](https://github.com/awtoau/cynthion-workspace/issues/581)) | — | an I/O standard the DDR tables cover — board revision |
| flash SCK | no vendor figure for user-mode `USRMCLK` | the design's own fmax, because SCK is fabric-rate | 144 MHz, `0xEB` continuous | — | raise design fmax, or SCK from a 2× domain |
| PLL `sync` | 400 MHz `fOUT` | 130 MHz solved; **13 of 68** integer points keep `fPFD` ≥ 10 MHz | 60 MHz | 46% of the solved range | measure the CPU's real ceiling |
| console | — | 115200 baud on pins shared with JTAG | 11.52 kB/s | 100% | a different transport, not a faster pin |
| DSP | 28 × 18×18 at 225 MHz | 28 — the 25F die adds none | 4 used | 14% | nothing |

## How the 25F die was established

One experiment, run once, in July 2026
([#116](https://github.com/awtoau/cynthion-workspace/issues/116)). These are that
run's outputs, not current figures — a rebuild moves all of them.

| what the run did | result |
|---|---|
| LUT4s placed, routed and self-checked | **20,143** — 82.9% of the die, 7,855 past the marking |
| rounds run | 22,026, across two runs (2,002 + 20,024) |
| mismatches | **zero** |
| timing | 86.43 MHz achieved against a 60 MHz constraint |
| negative control (`--golden 0xdeadbeef`) | 1,575 of 1,575 rounds mismatched, sticky flag set on all 200 reads |

**The control is why the zero counts.** A self-checking test that never reports a
failure is indistinguishable from one that cannot fail. The `0xdeadbeef` build
proves the detector fires.

**The timing rules out a gentle clock.** 12F and 25F share a speed grade, so
86.43 MHz against a 60 MHz constraint is margin, not a design that only closed
because the extra fabric was clocked slowly.

**The placement rules out a 12k-sized subset.** `fabric_placement.py` parses
`top.config` and finds logic in **44 of 47 tile rows** (R2–R48; the three empty
rows are EBR/DSP rows on this die, not holes) across 69 columns, flatness 0.73.

### Placement sweep: 24 seeds

Measured 2026-08-03 (commit date) on the r1.4, commit
[`2b4fb76`](https://github.com/awtoau/cynthion-workspace/commit/2b4fb76fc831b415b0c241d91fe066a513cc221e)
(`fabric_build.py --seed N` looped by `fabric_sweep.py`; arcs counted by
`fabric_arcs.py` from `top.config` `arc:` lines against the Trellis database mux list).
- 24 configurations passed: 20,336 LUT4 each, 120,439 gateware-checked rounds, zero mismatches, no sticky flag.
- Negative control rebuilt for the sweep (a different placement is a different design): 1,573 of 1,573 rounds mismatched, flag latched on all 200 reads.
- Interconnect coverage: **35.8%** of 7,804,818 arcs, not the 97% [#116](https://github.com/awtoau/cynthion-workspace/issues/116) predicts for 24 configurations.
- Why: a placer seed moves tile instances only; the design is 185 structurally identical blocks, so every build needs the same connection kinds and per-tile-type share saturates near 9.6%.
- Marginal arcs per build fell 213,663 to 68,298, still falling; hundreds of seeds would be needed.
- Site overlap 0.757 over all 276 pairs vs density floor 0.709: the seed moved about 83% of the movable sites.
- Conclusion: seed variation cannot reach the coverage [#116](https://github.com/awtoau/cynthion-workspace/issues/116) assumes; that needs configurations built to select different sources per destination (a different generator).
- Same result, pluribus side: its `docs/fabric-test.md:52-56`.

### What this does *not* establish

**Intermittent defects.** This is one part and a single load-and-check, not a
soak. Binning for occasional wrongness is not excluded by a passing run. Treat
the extra fabric as usable, not as guaranteed across parts.

## Block RAM

LUT4 / `TRELLIS_COMB` counts recorded before [#441](https://github.com/awtoau/cynthion-workspace/issues/441) are not comparable across builds:
- A build-time `datetime.now()` constant was folded by synthesis; four builds of identical source spanned 451 `TRELLIS_COMB` (commit [`04518d7`](https://github.com/awtoau/cynthion-workspace/commit/04518d74d7afa31b7aaab89526f6e42e494964b8)); the instrumentation figure read 714 unpinned vs 1,074 pinned.
- Fixed: two elaborations now give one RTLIL digest (`scripts/soc/soc_repro_check.py`, [`check.py:328-343`](../../../scripts/check.py#L328-L343)); counts from builds after that are comparable. Also applies to [`bram-budget.md`](bram-budget.md).

nextpnr reports **56 DP16KD** — the 25F figure, not the 12F's 32. The SoC build
places **44** of them (42 `DP16KD` plus 2 `PDPW16KD`), carrying the CPU's
I-cache, D-cache, 64 KiB of program memory, the BTB and the console FIFO. Counted
from the placed bitstream, not estimated — see the Performance section for the
`top.config` command and why counting one mode undercounts by two.

**Block RAM has not been walked** the way the fabric was. It is nonetheless taken
as working on the strength of ordinary use: the CPU fetches from block RAM,
executes and computes correctly while the FIFO carries characters uncorrupted.
Marginal block RAM surfaces as garbage instructions or dropped bytes, not as
something subtle. Worth revisiting only if a fault appears that smells like memory
corruption.

Who actually consumes it: [`../chips/ecp5/bram-budget.md`](../ecp5/bram-budget.md).

## DSP blocks

The ECP5 has DSP blocks, so a hardware multiplier is cheap. Measured at **16
cycles** for an integer multiply against 123 for a soft-float single-precision
multiply — which is why `rv32im` earns its area on this part. CPU configuration
is in [`vexiiriscv-cpu.md`](../vexiiriscv-cpu.md).

## Clocking

One discrete **60 MHz oscillator on pin A8**. `usb` is that oscillator passed
straight through — the FPGA *sources* the ULPI clock (`clk_dir='o'` on all three
PHY resources), so `usb` is exactly 60.000 MHz by construction with no tolerance
left to check. Only `sync` and `fast` come from a PLL.

The generator is `SocClocks` in `gateware/soc/clocks.py`. It replaced
`VariableClockDomainGenerator`
(`repos/apollo/apollo_fpga/gateware/variable_clock.py`), which solved `sync` and
`usb` from one VCO and so admitted only 60, 100 and 120 MHz in the 60–130 range;
that in turn replaced upstream's `LunaECP5DomainGenerator` and its hardcoded
60/120/240 MHz taps. A `usb` clock 3.7% out does not enumerate the ULPI PHY,
which is what made the coupling expensive. See
[`../upstream-boundary.md`](../../upstream-boundary.md) and [#111](https://github.com/awtoau/cynthion-workspace/issues/111).

**`sync` is now free, within the `fPFD` caveat in the Performance section above.**

How fast the soft CPU can actually be clocked on this part is a separate and
still-open question:

How fast the soft CPU can be clocked on this part:
[`../soc-clocking.md`](../../soc-clocking.md).

## How software reaches it

| path | mechanism |
|---|---|
| configuration over JTAG | Apollo bit-bangs the TAP; `apollo` CLI |
| configuration from flash | at power-on, from the [W25Q32](../w25q32-config-flash.md) at offset 0 |
| debug registers / ILA | JTAG ER1 (`0x32`) / ER2 (`0x38`) tunnel, via `JTAGRegisterInterface` |
| reconfigure | Apollo drives PROGRAMN (MCU PA08); the fabric can also self-trigger via `self_program` (T13) |

JTAG pins on the FPGA side are **R11 (TDI)** and **T11 (TMS)**, which are wired to
the UART pins R14/T14 — see the pin-sharing section of
[`../hardware.md`](../../hardware.md).

## Registers

**SoC peripheral registers are not documented here.** The SoC's own memory map is
the authority — see [Register reference](../../hardware.md#register-reference) in the
board index. This note covers the silicon, not the gateware running on it.

## Code and scripts

| | |
|---|---|
| pin map (vendored) | `gateware/board/cynthion_r1_4.py` |
| fabric test gateware | `gateware/probes/fabric/fabric_gateware.py` |
| build / run / control | `scripts/fabric/fabric_build.py`, `fabric_run.py`, `fabric_negative_control.py`, `fabric_placement.py`, `fabric_sim.py`, `fabric_golden.py` |
| flashing and configuration | [`../chips/ecp5/flashing.md`](../ecp5/flashing.md) |
| live opcode sweep | [`../chips/ecp5/config-engine-probe.md`](../ecp5/config-engine-probe.md), `scripts/ecp5_cmd_probe.py` |

Generic ECP5/toolchain findings live in pluribus (`docs/ecp5/`), not here — the
test is whether a finding would be useful to someone with a different ECP5 board.

## Flash clock (`USRMCLK`)

### There is no `USRMCLK` maximum to violate

This is the cleanest result of the flash survey, and it retires a number this
workspace has been quoting.

- **The string `USRMCLK` does not appear in the ECP5 datasheet.** The 62 MHz
  figure is `fCCLK`, *"max selected CCLK output frequency"*, in the sysCONFIG
  port timing table — the **configuration engine's** oscillator ceiling. Applying
  it to the user-mode mux path is an extrapolation, not a spec.
- **FPGA-TN-02039 §6.1.2 is the only `USRMCLK` documentation**, and it gives
  instantiation templates and three functional notes — **no fmax, no setup/hold,
  no skew, no jitter**.
- **The open toolchain models nothing either.** The bel has three pins and three
  fixed connections; `getCellDelay` has no `USRMCLK` case; there is **no
  CCLK/USRMCLK/MCLK entry in any speed grade** of prjtrellis's timing database.
  A clean nextpnr report says nothing whatever about this path.

So reading a flash SCK as *"132% past the 62 MHz Lattice specifies for `MCLK`"*
compares against the wrong number ([W25Q32](../w25q32-config-flash.md#usrmclk-sck)).
**There is no vendor figure for user-mode `USRMCLK` at all** — we are in
unmodelled territory, and measurement is the only authority. That is a stronger
statement than the one it replaces, not a weaker one.

### `ODDR` into `USRMCLK` is architecturally impossible, not merely refused

The existing note records that nextpnr refuses an `ODDRX1F` whose `Q` does not
drive a top-level output. That is true, and it is the lesser reason:

- nextpnr's check is `is_trellis_io`, i.e. `cell->type == id_TRELLIS_IO`;
  `USRMCLK` is a different cell type, so the packer hard-errors.
- **But the CCLK site has no `DATAMUX_ODDR`/`IOLDO` mux** in the Trellis routing
  database, unlike every real PIO — and `JA4`'s mux sources carry **no
  `G_HPBX` global-clock spine source**, so a global clock cannot reach
  `USRMCLKI` without passing through a LUT or FF.

**There is no software fix.** The two things people actually do are
hand-placed fabric DDR (`dan-rodrigues/icestation-32` places two `TRELLIS_SLICE`
FFs on opposite edges next to the CCLK site) — which reaches the fabric rate and
does not exceed it — or NanoMig's phase-shifted PLL straight into `USRMCLKI`.
**We are already at 144 MHz, past both.**
