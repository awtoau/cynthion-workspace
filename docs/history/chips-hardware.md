# History: hardware / chips docs

Removed from the live docs (superseded, or journey rather than state). Agents do not read this file.

## docs/hardware.md, intro (removed 2026-11-02)

This board's facts have been re-derived — wrongly — more than once: a power monitor
called an INA when its silicon says PAC1954, an FPGA assumed to have 12,288 LUTs
when 20,143 have been placed and verified, a 64 Mbit HyperRAM read as 4 MiB. Each
of those was already written down when it was re-derived. If something here is
wrong, fix it here; do not work around it in a third file.

Bus-speed audit pointer: "Written after the I²C bus turned out to have run twelve times slower than every device on it for the life of the project."

## docs/hardware.md, "Claims that keep coming back wrong" (removed; each fact is stated in its live home)

| Claim | Actual |
|---|---|
| "The power monitor is an INA3221 or similar" | **PAC1954-1.** `PRODUCT_ID` reads `0x7B`, `MANUFACTURER_ID` `0x54`, read from silicon by two independent paths. |
| "The ECP5 has 12,288 LUT4s" | **20,143 have been placed, routed and verified** on a 24,288-LUT die. |
| "The HyperRAM is 4 MiB" (from "64 Mbit") | **8 MiB.** 64 Mbit ÷ 8 = 8 MiB. Three successive wrong explanations came from that one arithmetic slip. |
| "Both FUSB302Bs could share a bus" | They are both at `0x22`. They cannot. |
| "USB VID:PID `1d50:615b` for everything" | Apollo is **`1d50:615c`**; only gateware is `615b`. |
| "The Apollo bootloader is `1d50:60e6`" | Saturn-V uses **`1d50:615c`**, the same ID as Apollo firmware. `60e6` appears nowhere in the tree. |
| "`1d50:615b` means the board is not ready for DFU flashing" | It means gateware is running. A `0xF0` advertiser-stop or `--force-offline` handoff recovers the port. Distinguishing DFU from Apollo firmware needs the **interface descriptor**, not the PID. |
| "Subclass `0x10` = analyzer, `0x20` = moondancer" | Correct but incomplete — it omits **`0x00` = Apollo stub / flash bridge**, which is the subclass the handoff path actually matches on. |
| "At boot Apollo holds the port and the FPGA is in reset" | **Inverted.** Normal boot reconfigures the FPGA and hands it the port. Apollo only holds it when PROGRAM is pressed at boot. |
| "Configuration completing is what makes Apollo cede CONTROL" | Ownership is driven **continuously** by the FPGA_ADV keepalive, and ceding also requires the host to have sent `0xc2`. |
| "Hung firmware needs a power cycle" | See the recovery ladder. `0xec` and `0xed` remain permitted even mid-JTAG-programming. |
| "Apollo has a debug SPI to the FPGA" | The handlers exist but are gated on `_BOARD_HAS_DEBUG_SPI`, which `cynthion_d11` does not define. Those requests STALL. SPI goes via the JTAG ER1/ER2 tunnel. |
| "`spi.c` uses PA08/PA09/PA10" | Its **comment** says that. The code below muxes PA14/PA15/PA10. PA08 is FPGA_PROGRAM and PA09 is FPGA_ADV. |
| "ttyACM0/1/2 — three consoles" | Still a plan. `CFG_TUD_CDC` is **1**, so exactly one CDC interface exists. |

## docs/hardware.md, LED rationale (removed; the table and meanings stay)

Driven so that **a working board and a dead one do not look the same** — nothing
drove them before, which is a large part of why the silent SoC took days.

**The colour order is the schematic's, not the platform's.** `LEDResources` is
positional and carries no colour, so nothing in the gateware or firmware could
contradict a wrong name — and a reversed order survived in three places until
someone watched the board and saw the wrong lamp blinking ([#415](https://github.com/awtoau/cynthion-workspace/issues/415)).

**No `ever_fetched` or `ever_io` lamp.** Both latch within microseconds of any
boot and never distinguish anything again, so the board would carry two
permanently-lit lamps — the dead-instrument problem this section exists to
avoid ([#411](https://github.com/awtoau/cynthion-workspace/issues/411)).

The same correction made the sideband usable — its `state` was raw Wishbone `cyc`, high only during a transaction, so reading `0` was near-certain even on a busy CPU.

## docs/hardware.md, SoC register-offset incident (removed)

That is precisely how firmware came to send `0x9f << 24` on the strength of a comment asserting the PHY did not left-justify, when it does — the hardware and the comment disagreed and nothing could catch it.

## docs/hardware.md, `info` / memory budget paragraphs (superseded: image RAM is now 31 KiB, .text/.rodata in flash; see firmware/cynthion-soc/memory.x)

The shell is an image, not the resident half, and that is what its budget
rests on. `memory.x` puts it at `0x0400` with 63 KiB, above the 492-byte
bootloader that owns the reset vector.

With `board`, `info`, `selftest`, `time` and `stats` all present it measures
`.text` 25,996 + `.rodata` 9,976 + `.bss` 1,460 = 37,432 bytes, leaving **27,048
free** of 64,512. `stats` and the collection behind it account for 1,088 of
that — 772 of code, 260 of format strings and 48 of counters.
Under the previous 32 KiB split the same firmware was over by 3,748 bytes with no
stack at all and did not link. Two reclamations from that period stay because they
were genuine deletions of waste: `.eh_frame`, ~1800 bytes of unwind tables lld was
placing in RAM for a target that cannot unwind (`/DISCARD/` in both linker
scripts), and `opt-level = "z"` in place of `"s"`, another 1612.

Register-reference counts (superseded by the regenerated SVD, now 21 peripherals / 146 registers / 210 fields by element count): "`firmware/cynthion-soc-pac/soc.svd` (CMSIS-SVD: 12 peripherals, 55 registers, 96 fields)"; "`gateware/board/cynthion_r1_4.py` — 206 lines of pin declarations" (now 283 lines).

## docs/hardware.md, `cpu stats` (removed)

**Measured under QEMU: an untouched shell is 0.15% busy.** 99.85% of cycles are
`irq::pop` returning `None`. That is the figure to weigh against RTIC or Zephyr,
with the caveat that TCG is not the board — `mcycle` there advances once per
instruction, so IPC reads 1.000 and means nothing, and the board's cache misses
and Wishbone stalls are absent by construction.

Illustrative sample (none measured on the board):

```
cycles   window 1073741824  busy 0.15%  ipc 0.412
loop     turns 1874233  mean 572 cycles  worst 141202 cycles
poll     every 50 ms  polls 24019  worst gap 61 ms
```

Window rationale: the alternative to halving was a 64-bit divide, which on rv32 is `__udivdi3`: 912 bytes, and the same reason `time` prints a raw `mtime`.

## docs/sbu.md (removed)

"Proved in simulation, not on hardware. ... Both carry negative controls that are *run* — a turnaround one clock late, an inverted parity bit, an undefined acknowledge, a contending target — each asserting the corruption is reported."

"What a board still has to settle: that the 105 MHz domain closes timing" (timing claim dropped from the live doc).

## docs/chips/cynone-sideband.md (removed)

## 13. Where earlier documents were wrong

Recorded so a reader who finds the old text elsewhere — in git history, in a
docstring, in an issue — knows which way it was settled.

| claim | where it appeared | settled |
|---|---|---|
| Push-pull while driving; open drain rejected because the RC would set the rate ceiling | `debris/docs/fpga-adv-sideband.md` §1 and §11 | **Wrong.** Open drain shipped at both ends ([#88](https://github.com/awtoau/cynthion-workspace/issues/88)) and the soak refuted the RC argument. §1. |
| `ApolloAdvertiser` is a **25 Hz** square wave | `sideband_advertise.py` docstring, `architecture.md` 25, `upstream-boundary.md` | **50 Hz.** `repos/apollo/apollo_fpga/gateware/advertiser.py:42-48` sets `half_period` to 10 ms and toggles each time, so the period is 20 ms. The accompanying "low for half of every 20 ms" was always right, and so is `hardware.md`'s "50 Hz, toggles every 10 ms". |
| `fpga_adv_transceive()` at `fpga_adv.c:437` | `debris/docs/fpga-adv-sideband.md` §3.3, `architecture.md` 24 | The function is `fpga_adv_command()` at `:418`; `:437` is the mode guard. |
| `scripts/sideband_soak.py` and `sideband_contention_probe.py` are not in the tree | `debris/docs/sideband-review.md` §5 | They were retired to `debris/scripts/` in `25087b8`, not lost. The measurements they produced remain reproducible from there. |
| Baud is 115200 | [#209](https://github.com/awtoau/cynthion-workspace/issues/209), describing firmware `a7b8283` | Correct **for that firmware**. `b48d4bf` raised both ends to 230400; the two must move together. |

## docs/chips/w25q32-config-flash.md (removed)

### Correction to what this workspace recorded

Two statements in the existing notes are wrong and are corrected here:

| was recorded | correct |
|---|---|
| SR3 `0x60`, **"ADS clear"** (this document) | **there is no ADS bit on this part.** ADS/ADP are 4-byte-addressing bits and exist only on ≥256 Mbit parts. SR3 bit S23 is Reserved. `0x60` means DRV=25%, WPS=0, and nothing else |
| *"QPI mode can address in as few as 8 clocks"* | **this part has no QPI mode.** The claim is true of the FV and of the JV-IM, not of what is fitted |

## docs/chips/w25q32-config-flash.md (removed)

### Superseded

| was recorded | now |
|---|---|
| divisor 0 produces no clock; SCK capped at sync/2 | **wrong.** Divisor 0 reads byte-exact at every rung, at exactly half the cycle count of divisor 1 |
| 120 MHz FAIL, 160 MHz FAIL | both were divisor-0 points, disbelieved for the reason above. 120 MHz passes |
| 80 MHz is the fastest verified | 144 MHz, five modes |

## docs/chips/ecp5/bram-budget.md (removed; issue links inline)

Facedancer build gotcha, timing figure: "...fails timing at 72.68 MHz. Built the way its own build path does it, every clock passes."

"This section used to name two ways out as untried. One has since been taken: `.text` and `.rodata` execute in place from flash ... block RAM behind them freed exactly as predicted. The trade named here was right, and is the one this design now lives with."

How the 56 were spent (before/after doubling the caches; `./dev.py metrics report`; superseded, RAM region was 63 KiB):

| consumer | before | after | |
|---|---|---|---|
| main block RAM, the writable region | 32 | 32 | 64 KiB at address 0 |
| CPU I-cache | 2 | **5** | 4 KiB → 8 KiB |
| CPU D-cache | 4 | **5** | 4 KiB → 8 KiB |
| USB buffers | 3 | 3 | |
| CPU BTB | 2 | 2 | |
| ILA | 1 | 1 | |
| **total** | 44 | **48** | of 56 |
| **spare** | 12 | **8** | 16 KiB |

"Of the 63 KiB RAM region about 11 KiB is live and the entire remainder is stack slack — `.stack` is simply 'whatever is left'. Growing RAM would have enlarged slack and relieved nothing measurable."

"Timing held, and 'held' is the whole claim available from one build: `clk`'s distribution over 40 nextpnr seeds is 65.75–74.94 MHz, median 71.42, against a 60 MHz constraint that does not itself affect the result ([#467](https://github.com/awtoau/cynthion-workspace/issues/467), [#478](https://github.com/awtoau/cynthion-workspace/issues/478))."

"An earlier version of this section said sets rather than ways was forced, because `flash_cache_flush()` needs a direct-mapped cache. **That was wrong**, in the direction that made the easy choice look mandatory."

"The 52-versus-58 disagreement on 128×2 ([#287](https://github.com/awtoau/cynthion-workspace/issues/287)) does not reproduce. Two elaborations of that geometry, minutes apart, produce a byte-identical `top.json` and 57 blocks both times. Two things that were true when it was seen are not now: elaboration is reproducible ([#441](https://github.com/awtoau/cynthion-workspace/issues/441)), and every build no longer shares one generated `VexiiRiscv.v` path ([#306](https://github.com/awtoau/cynthion-workspace/issues/306)), which is exactly how one geometry's build could report another's memories."

Fmax per geometry (seed sweep, `soc_occupancy_timing.py`, `cpu-l1-*` arms, 40 seeds each, [#481](https://github.com/awtoau/cynthion-workspace/issues/481)):

| geometry | paired vs 64×2 | 95% CI | faster |
|---|---|---|---|
| **128×1** | **+3.50 MHz** | [+2.20, +4.80] | 34/40 |
| 256×1 | +2.25 | [+0.61, +3.88] | 29/40 |
| fetch 64×2 / lsu 128×1 | +2.16 | [+0.74, +3.58] | 25/40 |
| 64×1 | -0.74 | [-2.35, +0.86] | 17/40 |
| 32×2 | -1.91 | [-3.09, -0.72] | 12/40 |

"One build's Fmax is a property of that placement: the distribution at fixed occupancy is 9 MHz wide ([#467]), and the constraint the design is given does not change it ([#478]). Dropping the way pays only where the sets are: 64×1 is indistinguishable from 64×2."

Files table note: "That sweep's Fmax figures are withdrawn; its area and BRAM rows stand".

Geometry table sizes in the pre-fix doc were wrong for 2-way rows (listed 32×2 as 2 KiB, 64×2 as 4 KiB, 128×2 as 8 KiB, 32×4 as 2 KiB).

## docs/chips/vexiiriscv-cpu.md, header callout (removed)

> **Read every number here as provisional.**
>
> - **The core has changed repeatedly and most figures do not say which one they
>   were taken on.** The L1s have been 4 KiB direct-mapped, 8 KiB direct-mapped
>   and 8 KiB 2-way ([#283](https://github.com/awtoau/cynthion-workspace/issues/283), [#292](https://github.com/awtoau/cynthion-workspace/issues/292)); the dispatcher has been a superloop and RTIC
>   ([#245](https://github.com/awtoau/cynthion-workspace/issues/245)); `opt-level` was chosen against a cache that no longer exists ([#167](https://github.com/awtoau/cynthion-workspace/issues/167)).
> - **A measurement without its configuration cannot be judged**, only believed.
>   Where the configuration is stated, it is stated. Where it is not, assume the
>   figure predates the current core.
> - **Verifying any assertion here needs the full matrix**, not one build: cache
>   geometry x opt-level x dispatcher x clock. No such sweep has been run.
>   [#277](https://github.com/awtoau/cynthion-workspace/issues/277) is that work; [#291](https://github.com/awtoau/cynthion-workspace/issues/291) is making a regression explain itself.
> - **Fmax figures need repeats.** One identical design has closed at 62.81,
>   67.76, 75.30, 77.39, 78.32, 79.58 and 81.12 MHz across builds -- a ~15 MHz
>   placement spread, wider than most differences anyone wants to rank.
>
> Numbers with a stated date, commit and configuration are the trustworthy ones.
> Treat the rest as a starting point for a re-measurement.

## docs/chips/fusb302b-type-c.md (removed)

~~**Nothing in this tree configures these parts.**~~ **Superseded.** The RISC-V
SoC firmware now configures both at boot — see *From the SoC shell* below.

## docs/chips/pac1954-power-monitor.md, Performance intro (removed)

**Three rates and one resolution.** Two of the rates were wrong on the same day
and for the same reason — a figure nobody had checked against the part. The bus
went 80 kHz → 1 MHz
([#269](https://github.com/awtoau/cynthion-workspace/issues/269)); the sample
rate went 8 SPS → 1024 SPS
([#273](https://github.com/awtoau/cynthion-workspace/issues/273)). The current
LSB is still twice what it could be, and that one is open.

## docs/chips/pac1954-power-monitor.md, Known limitations (bus) (removed)

- ~~Bus runs at 100 kHz; the part supports 400 kHz.~~ **Both numbers were wrong**
  and neither had a source. The SoC's bus ran at **80 kHz** and the part does
  **1 MHz** Fast-mode Plus and **3.4 MHz** High-Speed (DS20006539B p. 7) — so the
  gap understated was 12.5×, not 4×. The SoC path is at 1 MHz now
  ([#269](https://github.com/awtoau/cynthion-workspace/issues/269)). **The four
  JTAG probe bitstreams are still at 100 kHz**

## docs/chips/pac1954-power-monitor.md, Known limitations (SLOW) (removed)

- ~~`SLOW` is driven low for the 1024 SPS default, but the actual sample timing
  has not been verified against the datasheet.~~ **It was not driven at all**, and
  R85 pulls it up, so the part ran at 8 SPS. Verified and fixed in
  [`7d3b83c`](https://github.com/awtoau/cynthion-workspace/commit/7d3b83c) — see
  the Performance section.

## docs/chips/ecp5/reconfigure-initn-gap.md (removed)

That claim originally rested on the open tooling's primitive list plus a
supporting argument: that MachXO2 has `PCNTR` for exactly this and the ECP5
does not, so the absence is deliberate rather than an oversight.

**The supporting argument was wrong.** Lattice Diamond 3.14 is installed
locally, and its simulation libraries are the vendor's own declaration of what
each family exposes. Comparing them:

## docs/chips/ecp5/peripheral-clock-audit.md intro (removed)

`usb` and `sync` used to come from one PLL and, in practice, ran at the same
rate. [`gateware/soc/clocks.py`](../../../gateware/soc/clocks.py) now takes `usb`
straight from the 60 MHz oscillator on A8, so `sync` has a PLL to itself and is a
free parameter. Three assumptions that were safe under the old topology are not
safe under this one:

## docs/chips/ecp5/peripheral-clock-audit.md intro 2 (removed)

This is the peripheral-by-peripheral review issue [#229](https://github.com/awtoau/cynthion-workspace/issues/229) asked for. Twenty-four
modules and boundaries, nine findings. A module that turns out to be sound is
recorded with the reason, because the point of the exercise is that none of them
had been looked at since the topology changed.

## docs/chips/ecp5/peripheral-clock-audit.md Defect 4 (removed)

**And the rate was wrong for a second reason this section did not catch.** The
argument below — that 100 kHz puts `t_SU;STA` inside a standard-mode minimum —
is correct and answers the wrong question. **Every device on that bus is a
Fast-mode Plus part**: two FUSB302Bs at 1 MHz and a PAC1954 at 1 MHz, which is in
fact a 3.4 MHz part. The bus is at **1 MHz** now

## docs/chips/ecp5/peripheral-clock-audit.md Defect 4 lead-in (removed)

The original finding, kept because the mechanism it describes is the one that was
fixed:
