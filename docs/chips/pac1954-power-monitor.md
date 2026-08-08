# PAC1954-1 — the power monitor

The four-channel current/voltage monitor on Cynthion r1.4, refdes **U1**,
schematic part `PAC195X-1-VQFN`, order part `PAC1954T-E/4MX`
(`repos/cynthion-hardware/power_distribution.kicad_sch`).

**Index:** [`../hardware.md`](../hardware.md)

Everything below was measured on a Cynthion r1.4, not derived from documentation
alone. Tracking: [#82](https://github.com/awtoau/cynthion-workspace/issues/82)
(bring-up), [#84](https://github.com/awtoau/cynthion-workspace/issues/84)
(streaming over the sideband link).

## Performance

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md);
cross-cut against every other bus in [`bus-speed-audit.md`](bus-speed-audit.md).
Datasheet references are **DS20006539B**,
[`sources/PAC195X-Family-DS20006539B.pdf`](../../sources/PAC195X-Family-DS20006539B.pdf),
96 pp.

**Three rates and one resolution.** Two of the rates were wrong on the same day
and for the same reason — a figure nobody had checked against the part. The bus
went 80 kHz → 1 MHz
([#269](https://github.com/awtoau/cynthion-workspace/issues/269)); the sample
rate went 8 SPS → 1024 SPS
([#273](https://github.com/awtoau/cynthion-workspace/issues/273)). The current
LSB is still twice what it could be, and that one is open.

### 1. Theoretical maximum

**Bus.** AC Electrical Characteristics – I²C/SMBus Timing, p. 7:

| `fSMB` | range | mode |
|---|---|---|
| Fast Mode Plus | 0.010 – **1 MHz** | what this bus runs |
| High-Speed mode | 0.010 – **3.4 MHz** | unreachable here; see below |

So this is a **3.4 MHz part**, not a 1 MHz one. The bus is at 1 MHz because the
two FUSB302Bs sharing the controller stop there, and because Hs-mode is a
different protocol needing an unacknowledged master code, a current-source
pull-up on SCL and `t_r` under 40 ns — which no resistor delivers, and which is
why the specification mandates a current source.

**Sample rate.** §5.1 and CTRL `SAMPLE_MODE[3:0]` (Register 7-2, p. 45):

| mode | rate |
|---|---|
| `0b0000` **1024 SPS adaptive accumulation** | the register default |
| `0b0001` / `0b0010` / `0b0011` | 256 / 64 / 8 SPS adaptive |
| `0b0100`…`0b0111` | 1024 / 256 / 64 / 8 SPS |
| `0b1011` Burst mode | **5120 SPS**, one channel |

**Resolution.** Both ADCs are 17-bit two's complement internally with *"an
additional bit of resolution that is not accessible from the results register"*
(p. 27); 16 bits reach the host. Full scale is what `NEG_PWR_FSR`
(Register 7-11, p. 46) selects, per channel:

| field | option | full scale | LSB |
|---|---|---|---|
| `CFG_VBn` `00` | unipolar (default) | 0 … +32 V | **488.3 µV** |
| `CFG_VBn` `01` | bipolar | −32 … +32 V | 976.6 µV |
| `CFG_VBn` `10` | bipolar FSR/2 | −16 … +16 V | **488.3 µV** |
| `CFG_VSn` `00` | unipolar | 0 … +100 mV | 1.526 µV = 76.29 µA |
| `CFG_VSn` `01` | bipolar | ±100 mV | **3.052 µV = 152.588 µA** |
| `CFG_VSn` `10` | bipolar FSR/2 | ±50 mV | **1.526 µV = 76.294 µA** |

### 2. Achievable on this board

**The bus.** SCL is `Pins("D7", dir="o")` — a push-pull FPGA output with no `oe`
— so the pull-up does not bind SCL's rise time and **no device on this bus may
ever stretch the clock**. Only SDA rises through a resistor: 2.2k (R83/R84),
`t_r ≈ 0.8473·R·C`, which reaches the Fm+ 120 ns limit at `Cb` = 64 pF. The part
is alone on its mux segment, so `Cb` is one short trace and one load — but it has
never been measured. See [`bus-speed-audit.md`](bus-speed-audit.md).

**The sample rate was set by a pull-up resistor, and it is now driven.** Table
3-1, PAC195X-1 Pin Function Table, gives VQFN **pin 1 = `SLOW/ALERT1`**,
*"Default: SLOW Input pin. When high, all channels sample at 8 SPS"*; the
schematic part is `PAC195X-1-VQFN`, so that numbering applies. §5.7 then
describes this board exactly:

> *"If a pull-up resistor is attached to the SLOW/ALERT1 pin for ALERT1
> functionality, **the device will power-up in Slow mode because of being pulled
> up at power-up.**"*

`production/netlist.ipc` puts net `MON.SLOW` on **U1 pin 1**, ECP5 ball **C6**,
and **R85 pin 2**; R85 pin 1 is `+3V3` and `production/bom.csv` gives R85 as
**10k**. `power.rs` writes only `NEG_PWR_FSR` and never `CTRL`, so
`SLOW_ALERT1[1:0]` stays at its POR value of `11` = the SLOW function. **The part
converted at 8 SPS — 128× below its own default — for the life of the project**,
and every measurement further down this page was taken there.

Fixed in [`7d3b83c`](https://github.com/awtoau/cynthion-workspace/commit/7d3b83c):
`top.py` now drives C6 low (`slow.o = 0`, `slow.oe = 1`), which overrides the
10k with an ECP5 output sinking 0.33 mA. R85 is not a mistake — a 10k pull-up to
VDD is precisely what §3.8 asks for on an ALERT1 pin, and 8 SPS was the
documented cost of fitting it and never programming the mode.

**Resolution, as configured rather than as specified.** Firmware writes
`NEG_PWR_FSR = 0x5500`: every `CFG_VSn` = `01` (bipolar ±100 mV) and every
`CFG_VBn` = `00` (unipolar 0–32 V).

- **Current: half the available resolution is being spent on range.** `CFG_VSn`
  = `10` (±50 mV) would halve the LSB from **152.588 µA to 76.294 µA** at the
  cost of halving full scale from ±5 A to ±2.5 A. The measured offset noise on an
  unplugged rail is 0.69–0.92 mA — 4.5 to 6 codes — so the finer LSB would be
  real resolution rather than dither. Whether ±2.5 A is enough is a policy
  question about what this board is expected to pass through: USB-C without an
  e-marked cable is 3 A.
- **Voltage: there is no resolution to recover, and this is the opposite of what
  was assumed.** [`../plans/performance-sections.md`](../plans/performance-sections.md)
  names *"a 0–31 V range used to measure a 5 V rail"* as the case to fix. It
  cannot be fixed on this part. Register 7-11 offers exactly three VBUS options,
  and the only one below 32 V is bipolar ±16 V — which halves the range and
  halves the code count together, landing on **the same 488.3 µV/LSB**. A 5 V
  rail genuinely occupies 16% of the codes and no register changes that.

### 3. Measured

| axis | conditions | figure | source |
|---|---|---|---|
| bus rate | `PRER` 11, `sync` 60 MHz | **1.000 MHz**, three devices answering by identity | `d820d9e` |
| CPU spent on the poll | matched windows, 50 ms interval | 6.34% at 80 kHz → **2.70%** at 1 MHz | `d820d9e` |
| one measurement read | four channels after REFRESH | ~2 ms → **~160 µs** | `d820d9e` |
| **conversion rate, before** | 12 back-to-back reads of one live rail | **identical words repeating** — one conversion read three times | `7d3b83c` |
| **conversion rate, after** | 40 reads in 1.68 s | **32 of 39 transitions changed, 19.0 changes/s** — the 50 ms poll is now the binding constraint | `7d3b83c` |
| identity | `0xFE`/`0xFD`/`0xFF` | `0x54` / `0x7B` / `0x02`, by two independent paths | this file |
| **VPOWERn accumulator** | — | **never read** | 32-bit, and it is what catches an event between polls |
| **`VBUSn_AVG` / `VSENSEn_AVG`** | — | **never read** | 8× rolling average, Registers 7-7/7-8 |

**The bus is no longer where the poll's cost is, and the arithmetic says so.**
Pure bus time would have scaled with the 12.5× rate change:

    6.34% x (80 / 1000)  =  0.51%      what 1 MHz should have cost
    2.70% measured       -  0.51%      =  2.19 points that are not bus time

So **≈2.2 percentage points is per-transaction overhead no clock rate touches**,
and it is now 81% of what the poll costs. A further 3.4× on the bus — which is
not available anyway — would recover at most 0.36 points. The next win on this
path is the number of transactions, which is
[#267](https://github.com/awtoau/cynthion-workspace/issues/267).

### 4. The gap, and what closes it

| rank | option | worth | effort |
|---|---|---|---|
| ✔ | **drive SLOW low** | **8 → 1024 SPS, 128×** | done in `7d3b83c`, two lines |
| ✔ | I²C 80 kHz → 1 MHz | 6.34% → 2.70% CPU | done in `d820d9e`, one constant |
| 1 | **`CFG_VSn` = `10`, ±50 mV** | **2×** on current resolution, 152.588 → 76.294 µA/LSB | one write, to the register the firmware already sets. Costs half the current range |
| 2 | read `VPOWERn` or the `_AVG` registers | the accumulator integrates every conversion, so it is the only thing that can see an event between two 50 ms polls | a register the driver does not read yet |
| 3 | the four JTAG probe bitstreams, still at 100 kHz | 10× on the bring-up paths | `period_cyc = 600` in four files `d820d9e` did not touch |
| 4 | poll faster than 50 ms | up to 1024 SPS is now genuinely available | it was not worth asking before `7d3b83c`, because the converter was 2.5× slower than the poll |
| — | Hs-mode, 3.4 MHz | the part supports it | **unavailable** — needs a current-source pull-up and `t_r` < 40 ns |

**Unknown:** the SDA bus capacitance, which is what the whole rise-time margin
rests on. What would establish it: a scope on the 0.3–0.7 V<sub>DD</sub> edge.

## Identity, read from the part

| register | address | value | meaning |
|---|---|---|---|
| `MANUFACTURER_ID` | `0xFE` | **`0x54`** | Microchip |
| `PRODUCT_ID` | `0xFD` | **`0x7B`** | **PAC1954-1** — this is how the variant was established |
| `REVISION_ID` | `0xFF` | **`0x02`** | |

Read twice by independent paths, agreeing: over JTAG by `scripts/power_probe.py`
(`tmp/power_probe.log`, commit `a1b7ec0`), and from the RISC-V CPU over the SoC's
own I2C master (commit `6e3e0f1`).

Datasheet: [`sources/PAC195X-Family-DS20006539B.pdf`](../../sources/PAC195X-Family-DS20006539B.pdf).

## Wiring on r1.4

| resource | signal | ECP5 pin |
|---|---|---|
| `power_monitor` 0 | `scl` | D7 |
| | `sda` | C7 |
| | `pwrdn` (active low, `PinsN`) | D5 |
| | `slow` | C6 |
| | `gpio` | D6 |

**I2C address `0x10`**, set by a resistor from ADDRSEL to ground and latched at
power-up — it cannot be changed at runtime (DS20006539B Table 6-1: 0R/GND → `0x10`,
499R → `0x11`, … 226k → `0x1E`). Which resistor is fitted could not be determined
reliably from the KiCad schematic, so the bring-up **scans** the range and reports
what answers. Exactly one address responded; `0x11`–`0x1E` were silent.

**`PWRDN` is active-low and declared `PinsN`**, so driving `.o` low de-asserts
power-down. Getting this wrong produces NAKs on every address, which is
indistinguishable from a wrong address.

The resource carries `PULLMODE="UP"`; `scl` is `dir="o"`, so no clock stretching.

## Channel → port mapping

**This is not the intuitive ordering. Channel 1 is not CONTROL.**

| PAC channel | Physical port |
|---|---|
| 1 | TARGET_A |
| 2 | TARGET_C |
| 3 | AUX |
| 4 | CONTROL |

Derived from the **clean `r1.4.0` schematic tag** — not the working tree, which
sits on the Coppelia branch with in-progress edits and a KiCad 7→10 re-save — by
matching PAC `SENSEn` pin coordinates against the sheet's global labels. Channels 3
and 4 share a y-coordinate and are disambiguated by which side of the symbol they
sit on: AUX labels at x=92.71 match the left-side pins (x=95.25), CONTROL at
x=124.46 matches the right (x=123.19).

Then confirmed physically — see *Validation* below. Encoded in
`gateware/probes/power_monitor/registers.py`.

## Scaling

Sense resistors are **0.02 Ω ±1%** (R1, R2, R42, R59 on the r1.4.0 sheet), one per
channel, with 18.7 k ±1% dividers alongside.

| | Value |
|---|---|
| VBUS full scale | 0–32 V, 16-bit → **488.3 µV/LSB** |
| VSENSE full scale | **−100 to +100 mV**, signed 16-bit → 3.052 µV/LSB |
| Current per LSB | 3.052 µV ÷ 20 mΩ = **152.588 µA/LSB** |
| Current range | nominally **−5 A to +5 A** |

Firmware writes `NEG_PWR_FSR` (`0x1D`) as `0x5500`: every `CFG_VSn=01` selects
bipolar ±100 mV, while every VBUS field remains unipolar. DS20006539B section
5.9 and Table 5-2 (pages 25–26) specify two's-complement results with a 2¹⁵
denominator. Bipolar ±50 mV is the separate FSR/2 mode; that mode would halve
the range to ±2.5 A, but it is not selected. Register 7-11 (page 52) defines all
four channel fields.

This configuration is required by the bidirectional passthrough. With a phone
drawing about 430 mA from TARGET-C on 2026-08-03, CONTROL reported +472.946 mA
while TARGET-C clamped at 0.000 mA in unipolar mode. The repeatable bipolar check
is equal magnitudes with opposite signs on those two ports.

## Register map

These registers are **not** in the SoC's memory map — the PAC1954 is an external
I2C device, so its map lives in this note rather than in the generated PAC. See
[Register reference](../hardware.md#register-reference) for where that boundary is.

Measurement registers, all 16-bit, one per channel:

| Register | Address |
|---|---|
| `REFRESH` | `0x00` (Send Byte) |
| `VBUSn` | `0x07`–`0x0A` |
| `VSENSEn` | `0x0B`–`0x0E` |
| `VBUSn_AVG` | `0x0F`–`0x12` (8× averaged) |
| `VSENSEn_AVG` | `0x13`–`0x16` |
| `VPOWERn` | `0x17`–`0x1A` (32-bit) |

Issue `REFRESH` before reading: it latches VBUS, VSENSE and the accumulators
together, so all four channels come from one sample instant rather than whenever
each register happened to be read.

### Transfer size matters

The identification registers are single bytes; the measurement registers are
16-bit. The PAC195X **auto-increments its address pointer within a read**, so a
2-byte read of a 1-byte register returns that register *plus the next one*.

This bit during development: reading `MANUFACTURER_ID` with `size=2` returned
`0x02` (the revision) and `PRODUCT_ID` returned `0x54` (the manufacturer) — every
value shifted one register along. Here it failed loudly because the expected values
are known. In a streaming path a size mismatch would silently produce
plausible-looking wrong numbers, so the size is explicit in the API.

## Measured results

The measurements in this section predate bipolar configuration and are retained
as bring-up evidence.

Board attached via CONTROL and AUX, nothing on the TARGET ports:

```
measurements (after REFRESH):
  ch  port       VBUS raw     volts  VSENSE raw    current
  1   TARGET_A  0x0000      0.000 V     0x000A      0.76 mA
  2   TARGET_C  0x000D      0.006 V     0x000B      0.84 mA
  3   AUX       0x2951      5.165 V     0x01C3     34.41 mA
  4   CONTROL   0x2940      5.156 V     0x017E     29.14 mA
```

5.165 V and 5.156 V are USB VBUS well within the 4.75–5.25 V spec. The sub-1 mA
readings on the unpowered channels are ADC offset near zero.

### Validation: the cable-removal test

The strongest check available, because it cross-checks mapping, scaling and
plausibility at once. With AUX unplugged (`tmp/power_probe.log`):

| Port | Before | After |
|---|---|---|
| AUX | 5.165 V, 34.41 mA | **0.007 V, 0.92 mA** |
| CONTROL | 5.156 V, 29.14 mA | **5.151 V, 63.55 mA** |

Three things follow:

1. **The mapping is right.** AUX went dead when AUX was unplugged. Had channel 3
   actually been CONTROL, the wrong channel would have dropped.
2. **The scaling is right.** 34.41 + 29.14 = 63.55 mA before; 63.55 mA on CONTROL
   alone after. Total board draw is unchanged — it just all flows through one rail.
   Conservation of current, matching to within 2%.
3. **~65 mA at 5 V (≈325 mW) is the real idle draw.** It initially looked
   implausibly low for an ECP5 plus three PHYs, but that was because it was split
   across two rails, and the bring-up bitstream leaves most of the fabric unused
   with the PHYs not enumerating.

Ten consecutive samples spanned 28–36 mA, confirming the ADC is genuinely
converting rather than returning a latched value.

**Worth keeping as a regression: unplug a rail and confirm the current migrates
rather than vanishes.**

## How software reaches it

| path | how |
|---|---|
| host over JTAG | `gateware/probes/power_monitor/power_monitor_gateware.py` (applet `0x504D4F4E` "PMON") + `scripts/power_probe.py` |
| free-running poller | `gateware/probes/sideband/sideband_gateware.py` — reads one register on a loop, blinks an LED when it sees `0x54` |
| RISC-V CPU | `gateware/soc/peripherals/i2c_master.py` (OpenCores register map) wired in `gateware/soc/top.py`; driver `firmware/cynthion-soc/src/bus.rs` (which owns `bus/i2c.rs` and `bus/mux.rs`) and `src/power.rs`, shell commands `i2c` and `power` |

### From the SoC shell

```
> power
power @10  poll 50 ms  change 100 mA  sampled 63 ms ago
  target_a  0.000 V      0.686 mA  disconnected
  target_c  0.006 V      0.762 mA  disconnected
  aux       5.165 V     34.408 mA  connected
  control   5.156 V     29.144 mA  connected
  target_a floor 10.000 mA
  ...
> power floor aux 25          # in milliamps; stored as microamps
```

`power` **prints the background poll's cached sample and touches no bus**
(awtoau/cynthion-workspace#123). It still reports all four rails regardless of
the change threshold — the threshold keeps the log readable, and a command that
inherited it could not answer "what is it now" — but the numbers come from the
poller rather than from a read of this command's own.

That is ownership, not caching for speed. The part is unavailable for 1 ms after
a REFRESH and answers a read inside that window by acknowledging its address and
then NACKing the register pointer; the poll issues a REFRESH every 50 ms, so a
second caller reading on its own account landed in the window about one time in
fifty and reported "no acknowledge (register pointer)" on a bus that was working
perfectly. **One owner of the REFRESH cycle makes that impossible rather than
rare.** The earlier fix — wait 2 ms, try once more — is deleted: it removed the
symptom and left the structure, and a retry that can never fire is a claim about
the system that is not true.

**The age on the header line is load-bearing.** Worst-case staleness is 100 ms
(one interval for the sample to be fetched, plus however long ago the last poll
ran), which is imperceptible; but a poller that has *stopped* leaves four
voltages that are individually plausible and jointly a lie, and nothing in them
says so. The age is measured from the REFRESH that latched the values, not from
the read that fetched them, so it does not understate itself by an interval.
Before the first complete cycle it says `NO SAMPLE YET` rather than printing
rails; past 60 s it says `sampled OVER 60 s ago` rather than a number, because
the 32-bit `time` CSR wraps at 71.6 s and a wrapped age is small and plausible.

Checked in `scripts/soc_i2c_owner_sim.py`: a read driven into an open REFRESH
window, two owners interleaving on one device, and a mux select that is
remembered — each run against the real gateware and a model of this part, and
each asserting that the old arrangement fails.

The firmware also **polls every 50 ms in the background** and prints only when a
channel moves by **100 mA or more from the last value it announced**, or crosses
that channel's floor. Comparing against the last *announced* value rather than the
last *sample* matters: against the last sample, a rail ramping at 90 mA per poll
would never announce anything however far it travelled.

The floor exists because an unplugged rail measures 0.76–0.92 mA of ADC offset
here, and without it that noise walks across a threshold and emits change events
from a port with nothing in it. It is an **absolute-current magnitude**: both
+20 mA and −20 mA cross a 10 mA floor. Direction never determines whether a
real load is called connected.

**The conversions are exact integer rationals, not approximations.** VBUS
millivolts are `raw × 125 / 256`. Current decodes the raw word as two's
complement and scales its magnitude by `78125 / 512` µA per code, directly from
the datasheet's 5 A full scale and 2¹⁵ denominator. Magnitude-first arithmetic
makes equal positive and negative codes report equal magnitudes.

Background lines go to the **USB console only**. The Apollo-facing port's TX pin
is JTAG TMS, and a background monitor transmitting there unbidden is bus
contention (`target::ANNOUNCING`).

The I2C master is ours, written to the OpenCores "I2C-Master Core" register map
(Herveille, rev 0.9) — public, with a Linux driver (`i2c-ocores`), and **no read
with a side effect anywhere in it**. See
[`../upstream-boundary.md`](../upstream-boundary.md) for why it was not taken from
elsewhere.

```bash
apollo configure gateware/probes/power_monitor/build/top.bit
./scripts/power_probe.py                 # scan, identify, measure
./scripts/power_probe.py --address 0x10  # skip the scan
./scripts/power_probe.py --read 0xFE     # one register
```

Output goes to the console and to `tmp/power_probe.log`.

## Known limitations

- ~~Bus runs at 100 kHz; the part supports 400 kHz.~~ **Both numbers were wrong**
  and neither had a source. The SoC's bus ran at **80 kHz** and the part does
  **1 MHz** Fast-mode Plus and **3.4 MHz** High-Speed (DS20006539B p. 7) — so the
  gap understated was 12.5×, not 4×. The SoC path is at 1 MHz now
  ([#269](https://github.com/awtoau/cynthion-workspace/issues/269)). **The four
  JTAG probe bitstreams are still at 100 kHz** — `period_cyc = 600` in
  [`../../gateware/probes/pins/i2c_scan.py`](../../gateware/probes/pins/i2c_scan.py),
  [`../../gateware/probes/pins/fusb302_id.py`](../../gateware/probes/pins/fusb302_id.py),
  [`../../gateware/probes/power_monitor/power_monitor_gateware.py`](../../gateware/probes/power_monitor/power_monitor_gateware.py)
  and [`../../gateware/probes/sideband/sideband_gateware.py`](../../gateware/probes/sideband/sideband_gateware.py).
- ~~`SLOW` is driven low for the 1024 SPS default, but the actual sample timing
  has not been verified against the datasheet.~~ **It was not driven at all**, and
  R85 pulls it up, so the part ran at 8 SPS. Verified and fixed in
  [`7d3b83c`](https://github.com/awtoau/cynthion-workspace/commit/7d3b83c) — see
  the Performance section.
- `VPOWERn` (32-bit) has never been read — it is derivable from VBUS × VSENSE.
  Not equivalently, though: the accumulator integrates *every* conversion, so it
  is the only thing here that can see an event between two 50 ms polls.
- The JTAG path requires an Apollo debug session, so it is unsuitable for
  continuous monitoring. That is what #84 addresses; the CPU path is another route.
