# Every bus on r1.4, against what its parts actually support

**Index:** [`../hardware.md`](../hardware.md)

The I²C bus ran at 80 kHz for the life of the project. Its slot budget was
derived against **standard-mode** minima — 4.7 µs `t_LOW`, `t_SU;STA`, `t_BUF` —
on a bus with no standard-mode device on it. All three parts are Fast-mode Plus
1 MHz. It is at 1 MHz now, and the CPU spent on the power poll fell from 6.34%
to 2.70% ([`d820d9e`](https://github.com/awtoau/cynthion-workspace/commit/d820d9e),
[#269](https://github.com/awtoau/cynthion-workspace/issues/269)).

The reasoning was sound. Nobody had asked what the parts supported.

This page asks that question of **every** interface on the board, once, with a
citation per figure. Structure per
[`../plans/performance-sections.md`](../plans/performance-sections.md): three
numbers per interface — what the part supports, what this board allows, what we
configure — because the gap that matters is between the second and the third.

**The binding constraint is a pin or a primitive more often than it is the part.**
Four of the eleven interfaces below are bounded by something on the FPGA side,
one by an oscillator, and only one by the device on the far end.

---

## The table

| interface | the part supports | this board allows | we configure | gap |
|---|---|---|---|---|
| **I²C** ×3 buses | 1 MHz Fm+ (both parts); the PAC1954 also does 3.4 MHz Hs | measured: 3.33 MHz power, 2.5 MHz target/aux — see the sweep below | **1.000 MHz**, `PRER` derived from the COUNTED sync clock (#272) | **closed on the CPU path** at the parts' Fm+ rating. The JTAG probe bitstreams are still at 100 kHz, mostly for a stated reason |
| **SPI flash SCK** | 133 MHz `fC1` | `USRMCLK` has no DDR ⇒ SCK ≤ the fabric clock; luna_soc's clock generator halves it again ⇒ SCK = `sync`/2 | **30 MHz** (`sync` 60, `FLASH_DIVISOR = 0`) | **4.4×** to the part, 2× of which is the `/2` |
| **flash MCLK at boot** | 133 MHz on `0x0B` | `ecppack --freq` accepts only {2.4, 19.4, 38.8, 62.0}; ECP5 Table 4.7 stops at 62 | **38.8 MHz** | **1.6×** on configuration time |
| **HyperRAM CK** | 166 MHz (the `6I` bin) | **LVCMOS33 output max 150 MHz** — the FPGA pin, not the RAM. Non-DQS PHY makes CK = fabric clock | **60 MHz** | **2.5×** to the board's own ceiling |
| **ULPI** ×3 | 60 MHz, fixed by the ULPI 1.1 specification | the FPGA *sources* the clock (`clk_dir='o'`) from the A8 oscillator: exactly 60.000 MHz | **60 MHz** | **none, and it is not a parameter** |
| **JTAG TCK** | ECP5 `fMAX` 25 MHz | SAMD11 SERCOM: SCK = 48/(2·(BAUD+1)), so the only rungs are 24 and 12 MHz. 24 fails the TDO round trip | **12 MHz** (BAUD = 1) | **none — there is no legal rung in between** |
| **Apollo UART** | SERCOM USART off a 48 MHz core clock | R14/T14 *are* JTAG TDI/TMS. The cost is arbitration, not bit rate | **115200** | large in principle; the constraint is the sharing |
| **CynOne sideband** | — | one wire, open drain, half duplex | **230400** | see [`cynone-sideband.md`](cynone-sideband.md) §2 |
| **console 16550** | — | there is no baud generator; the transport is USB CDC | **n/a** | USB-bound, not baud-bound |
| **`sync`** | ECP5 `fOUT` 3.125–400 MHz | nextpnr median **71.81 MHz** on this netlist | **60 MHz** | ~20%, and unmeasured on silicon |
| **`usb`** | — | the A8 oscillator, straight through | **60.000 MHz** | none by construction |
| **`fast`** | — | the flash PHY's own fmax, 111–125 MHz | **not built** | gated on an ODDR that `USRMCLK` cannot have |

**One rate that is not a bus and was the largest error of the lot.** The
PAC1954's **conversion rate** ran at **8 SPS against a 1024 SPS default** — 128×
— because R85 pulls its `SLOW` pin high and `top.py` left that pin an input.
Found by the companion pin audit
([`ecp5/pin-usage.md`](ecp5/pin-usage.md)) and fixed in
[`7d3b83c`](https://github.com/awtoau/cynthion-workspace/commit/7d3b83c). It
belongs on this page because it is the same failure in a different unit: a rate
nobody had compared against the part. See
[`pac1954-power-monitor.md`](pac1954-power-monitor.md) §2.

**The two audits are complementary and neither finds the other's bugs.** This one
asks *how fast* every wired-up thing runs; the pin audit asks *whether each pin is
wired up at all*. The PAC1954 was wrong on both axes at once, and only the pin
question exposed the 128×.

---

## The gaps, ranked by what they are worth

1. **The flash's `/2`, worth 2× on every instruction fetch.** SCK is
   `sync`/2 because luna_soc's `SPIClockGenerator` toggles SCK as a register.
   Driving it through the domain clock directly makes SCK equal the domain rate.
   Detail and the ODDR obstacle: [`w25q32-config-flash.md`](w25q32-config-flash.md).
2. **The HyperRAM's uncoalesced cache-line refill, worth 6.4×** —
   12.0 → 76.8 MB/s. Not a clock decision at all;
   [`hyperram/w956a8.md`](hyperram/w956a8.md) §4 and
   [#185](https://github.com/awtoau/cynthion-workspace/issues/185).
3. **Boot MCLK 38.8 → 62 MHz, worth 1.6× on configuration time.** One
   `ecppack` flag, already investigated:
   [`ecp5/qspi-boot-time.md`](ecp5/qspi-boot-time.md).
4. **The four probe bitstreams still at 100 kHz I²C** — `period_cyc = 600` on a
   60 MHz domain, in
   [`../../gateware/probes/pins/i2c_scan.py`](../../gateware/probes/pins/i2c_scan.py),
   [`../../gateware/probes/pins/fusb302_id.py`](../../gateware/probes/pins/fusb302_id.py),
   [`../../gateware/probes/power_monitor/power_monitor_gateware.py`](../../gateware/probes/power_monitor/power_monitor_gateware.py)
   and [`../../gateware/probes/sideband/sideband_gateware.py`](../../gateware/probes/sideband/sideband_gateware.py).
   **This one is defensible and mostly should stay**: `i2c_scan.py` says why —
   *"an unconfigured device that is marginal at 400 kHz will still answer at
   100, and this only runs once"* — and a bring-up scan wants robustness, not
   throughput. Two things are still worth fixing. The **400 kHz** in that comment
   is the same unsourced figure this audit deleted from
   [`pac1954-power-monitor.md`](pac1954-power-monitor.md); the parts do 1 MHz.
   And the free-running **poller** in `power_monitor_gateware.py` is not a
   one-shot scan, so it has no such excuse.
5. **PAC1954 current resolution, worth 2× and never considered.** `CFG_VSn = 10`
   (±50 mV) halves the LSB from 152.588 µA to 76.294 µA, at the cost of halving
   full scale to ±2.5 A. See
   [`pac1954-power-monitor.md`](pac1954-power-monitor.md) — and note that the
   *voltage* range has **no such lever**, which is the opposite of what
   [`../plans/performance-sections.md`](../plans/performance-sections.md)
   assumed: Register 7-11's only sub-32 V option is bipolar ±16 V, which halves
   the range and the code count together and lands on the same 488.3 µV/LSB.
6. **`sync` 60 → ~72 MHz, worth 20% on everything derived from it** — CPU, flash
   SCK, HyperRAM CK and the I²C bus all move together. The CPU's real ceiling is
   unmeasured; [`../soc-clocking.md`](../soc-clocking.md) §2 withdraws the old
   result.
7. **Unknown:** the SDA bus capacitance. Everything about the I²C rise-time
   margin rests on an assumed 50 pF. See below.

**Three interfaces have no gap at all**, and saying so is the point of an audit:
ULPI is fixed by its own specification, JTAG has no legal rung between 12 and
24 MHz, and `usb` is an oscillator.

---

## Where the ceiling is a pin

### `USRMCLK` — the flash clock

SCK reaches the [W25Q32](w25q32-config-flash.md) only through the `USRMCLK`
macro. sysCONFIG FPGA-TN-02039-2.3 §4.6.5: *"The MCLK is always reserved for use
in MSPI mode, in most post-configuration applications, as the reference clock for
performing memory transactions with the external SPI PROM."* On r1.4 the only
copper to the flash clock is from N9, and N9 is that ball.

The CCLK site has no `DATAMUX_ODDR`/`IOLDO` mux in the Trellis routing database,
so **SCK can never exceed the fabric clock that generates it** — never 2×. That
is the sharpest pin constraint on the board and it is permanent.

### LVCMOS33 — the HyperRAM clock, and everything else fast

ECP5 datasheet FPGA-DS-02012-1.9 **Table 3.21, Maximum I/O Buffer Speed**:

| buffer | max input | max output |
|---|---|---|
| LVDS25, SSTL15/18, SSTL135, HSUL12 | 400 MHz | 400 MHz |
| **LVCMOS33 (all drives)** | **200 MHz** | **150 MHz** |

Every fast signal on this board is `IO_TYPE="LVCMOS33"`, because every part on
the far end is a 3.3 V CMOS device. **So the HyperRAM's rated 166 MHz CK is
already outside the FPGA's published output ceiling**, and the board maximum for
CK is 150 MHz rather than the part's 166 — a fact that
[`hyperram/w956a8.md`](hyperram/w956a8.md) §4 rank 3 did not account for.

Table 3.21 note 4 — *"All speeds are measured at fast slew"* — narrows it
further: only five resources in
[`../../gateware/board/cynthion_r1_4.py`](../../gateware/board/cynthion_r1_4.py)
carry `SLEWRATE="FAST"` (the three ULPI PHYs, the HyperRAM, the mezzanine
header). The QSPI flash pins do **not**, and the datasheet gives no figure at all
for LVCMOS33 at slow slew — so the 144 MHz SCK measured in
[`w25q32-config-flash.md`](w25q32-config-flash.md) has no published number to be
inside or outside of.

Fuller treatment in [`ecp5/lfe5u-12f.md`](ecp5/lfe5u-12f.md) §2.

### The SERCOM divider — JTAG TCK

The ECP5 accepts 25 MHz on TCK (Table 3.43, JTAG Port Timing Specifications,
`fMAX`). Apollo clocks it from a SAMD11 SERCOM in SPI master mode, where
`SCK = f_ref / (2 · (BAUD + 1))` with `f_ref` = 48 MHz
([`peripheral_clk_config.h`](../../repos/apollo/lib/tinyusb/hw/mcu/microchip/samd11/config/peripheral_clk_config.h)),
so the only candidates near the ceiling are **BAUD 0 → 24 MHz** and
**BAUD 1 → 12 MHz**. We use 1.

24 MHz does not work, and the arithmetic says so before anyone tries it. TDO
changes on the falling TCK edge and the SERCOM samples it half a period later:

| term | value | source |
|---|---|---|
| `tBTCO` — ECP5 TAP falling edge to valid output | 10 ns max | FPGA-DS-02012 Table 3.43 |
| `tMIS` — SAMD11 MISO setup to SCK, master | 21 ns typ | Atmel-42363H Table 35-50 |
| **required half period** | **≥ 31 ns** | |
| ⇒ **TCK ≤ 16.1 MHz** | | |

24 MHz is a 20.8 ns half period. The SAMD11's own `tSCK` master period figure —
84 ns typ, i.e. 11.9 MHz — points at the same place independently. **12 MHz is
the top legal rung and the gap is zero**, not because the parts are slow but
because the divider is an integer.

Caveat worth attaching: `tMIS` and `tSCK` are given in the **Typ.** column with
no min or max, so this is an argument rather than a guarantee. What would settle
it is the harness that already exists —
[`jtag.c`](../../repos/apollo/firmware/src/jtag.c) takes a SERCOM divider in the
high byte of `wIndex` and counts bytes that return exactly as the TAP should
have returned them. Running it at BAUD 0 costs one USB request.

---

## Where each I²C bus actually stops — measured 2026-08-10, board image `1e574f4`

Swept with [`../../scripts/i2c_rate_sweep.py`](../../scripts/i2c_rate_sweep.py),
1000 identity reads per rung, on the `bist1-ck120` variant (sync counted at
50 MHz). The identity is checked against the value the device is KNOWN to hold,
because `i2c soak` takes its expected value from its own first read and calls a
stable wrong answer CLEAN.

| bus | rated max | board allows | we run | why the gap |
|---|---|---|---|---|
| power (PAC1954) | **1 MHz** Fm+; 3.4 MHz only with `I2C_HISPEED` set | **3.33 MHz** clean, fails at 5 MHz | **1.000 MHz** | the part's Fm+ rating. 3.33 MHz is out of spec until `I2C_HISPEED` (reg `1Ch` bit 0) is set — see below |
| target (FUSB302B) | **1 MHz**, hard — no Hs mode | **2.5 MHz** clean, fails at 3.33 MHz | **1.000 MHz** | the part's rating. The bus goes faster than the part is specified for |
| aux (FUSB302B) | **1 MHz**, hard — no Hs mode | **2.5 MHz** clean, fails at 3.33 MHz | **1.000 MHz** | as target |

- The failure is not graceful and not the same on each bus. Target/aux at
  3.33 MHz return `0xff` with **zero** bus errors — an undriven bus, read as a
  stable wrong value. Power at 5 MHz returns 1000 bus errors.
- 1 MHz is now reached rather than claimed: PRER is derived from the clock the
  fabric counts, so the BIST variant's 50 MHz gives PRER 9 instead of the
  generated 11. It ran at **833 kHz** while reporting 1 MHz (#272).
- Both variants land on the same bit timing: 50 MHz/PRER 9 and 60 MHz/PRER 11
  are both a 200 ns slot, so the margins table below holds for either.

### The one rated headroom that exists

The PAC1954 is alone on the power bus and is a 3.4 MHz part. Microchip's Hs
entry is a **register bit, not the standard master-code handshake** —
DS20006539F §7, reg `1Ch` bit 0 `I2C_HISPEED`: *"Setting this bit enables the
3.4 MHz I2C operation by changing the pulse-width parameters of the Pulse
Gobbler."* Its stated master requirement is a **CMOS (push-pull) SCL driver**
(§3.4, p. 17), which this board already has — all three `scl` are `dir="o"`.

So the blocking conditions are two, and neither is the master code:
- **Bus capacitance ≤ 100 pF per line** at 3.4 MHz, against 550 pF at Fm+
  (DS20006539F p. 7). Unmeasured here — see the open item below.
- **One prescale, three buses.** The controller has a single PRER, so a per-bus
  rate needs it reprogrammed on every mux select. The FUSB302Bs cannot follow
  above 1 MHz.

That the sweep finds the power bus clean at 3.33 MHz and broken at 5 MHz, with
3.4 MHz the part's own Hs ceiling, is consistent with the part rather than the
copper being the limit — but it is consistency, not proof, until `Cb` is
measured.

## Verifying the I²C numbers

Asked for, so checked rather than restated. Recomputed from the datasheets in
[`../../sources/`](../../sources/README.md) rather than from the commit message.

**The arithmetic holds.** `f_SCL = f_sync / (5 · (PRER + 1))` = 60e6/(5·12) =
**1.000 MHz exactly**; the slot is 12/60e6 = 200 ns; `t_LOW` is 3 slots and
`t_HIGH` 2, and START/STOP get a sixth slot.

**Both parts really are Fast-mode Plus**, and both tables say so in the same
terms:

| parameter | ours at PRER 11 | Fm+ min | margin | source |
|---|---|---|---|---|
| `t_LOW` | 600 ns | 500 ns | **+20%** | FUSB302B Rev 1.3 p. 18; PAC195X DS20006539B p. 7 |
| `t_HIGH` | 400 ns | 260 ns | +54% | both |
| `t_SU;STA` | 400 ns | 260 ns | +54% | both |
| `t_HD;STA` | 400 ns | 260 ns | +54% | both |
| `t_SU;STO` | 400 ns | 260 ns | +54% | both |
| `t_BUF` | 1200 ns | 500 ns | +140% | both |
| `t_SU;DAT` | 200 ns | 50 ns | +300% | both |

`t_LOW` at +20% is the tightest, as claimed.

**Three things the commit did not say, none of which changes the answer:**

- **SCL is push-pull, so the pull-up does not bind it.** All three `scl`
  subsignals are `Pins(..., dir="o")` with no `oe`
  ([`cynthion_r1_4.py:160`](../../gateware/board/cynthion_r1_4.py), `:169`,
  `:196`), so only **SDA** rises through a resistor. The module docstring in
  [`../../gateware/soc/peripherals/i2c_master.py`](../../gateware/soc/peripherals/i2c_master.py)
  has this right; the commit message reads as though both lines were open drain.
  It also means no device on this bus may ever stretch the clock, which the same
  docstring records.
- **One Fm+ parameter was not checked, and it passes.** `t_VD;DAT` and
  `t_VD;ACK` — the slave's own SCL-low to SDA-valid time — are **450 ns max**
  (FUSB302B Rev 1.3 p. 18). The controller samples at the end of slot 3, which is
  1000 ns after SCL falls, for 550 ns of margin. This is the class of parameter
  that bites, because it is a *slave output* rather than a master obligation.
- **The PAC1954 is a 3.4 MHz part, not a 1 MHz one.** DS20006539B's I²C/SMBus
  timing table (p. 7) gives `fSMB` two rows: 0.010–1 MHz Fast Mode Plus and
  **0.010–3.4 MHz High-Speed mode**. The uniform-1-MHz claim is right for the
  bus as a whole and right as a design decision, and the reasons Hs-mode is
  unreachable here are already written down — an unacknowledged master code, a
  current-source pull-up, `t_r` under 40 ns. But "every device on the bus is a
  1 MHz part" understates one of them, and the segment it is on is its own.

**What is not established: the bus capacitance.** `t_r ≈ 0.8473·R·C` with
R = 2.2k reaches the Fm+ 120 ns limit at **`Cb` = 64 pF**, and 50 pF is an
assumption nobody has measured. The known contribution is small — the FUSB302B
gives `CI` = 5 pF typ per I/O pin — but the ECP5 pad, the mux fan-out and the
trace are not accounted for. What would establish it: a scope on SDA reading the
0.3–0.7 V<sub>DD</sub> edge, or a `Cb` extraction from
[`cynthion.kicad_pcb`](../../repos/cynthion-hardware/cynthion.kicad_pcb). Until
then the margin is an argument, not a measurement — and a bus marginal on rise
time **answers most of the time**, which is why the acceptance test is three
identity reads rather than an address scan.

**And 1 MHz is not where the CPU cost went.** From the matched windows in
`d820d9e`: 6.34% busy at 80 kHz, 2.70% at 1 MHz. Pure bus time would have scaled
by 12.5× to **0.51%**. It did not:

    6.34%  -  0.51%  =  5.83 points that were bus time
    2.70%  -  0.51%  =  2.19 points that are not

So **roughly 2.2 percentage points is per-transaction overhead that no bus rate
touches**, and it is now 81% of what the poll costs. The remaining rate-derived
share is 0.51 points, of which a further 3.4× would recover at most 0.36. The
next win on this path is the transaction count, not the clock —
[#267](https://github.com/awtoau/cynthion-workspace/issues/267).

---

## What this audit did not settle

- **The SDA bus capacitance** — above. The one open question on I²C.
- **LVCMOS33 at slow slew**, which is what the QSPI flash pins carry and what
  144 MHz SCK was measured through. No vendor figure exists.
- **Whether TCK 24 MHz actually fails.** The arithmetic says it will and the
  harness to prove it exists. One USB request.
- **The `sync` ceiling on silicon.** nextpnr's median is 71.81 MHz; the board has
  only ever been run at 60. Every rate on this page except ULPI, JTAG and boot
  MCLK moves with it.
