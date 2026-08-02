# PAC1954-1 — the power monitor

The four-channel current/voltage monitor on Cynthion r1.4, refdes **U1**,
schematic part `PAC195X-1-VQFN`, order part `PAC1954T-E/4MX`
(`repos/cynthion-hardware/power_distribution.kicad_sch`).

**Index:** [`../hardware.md`](../hardware.md)

Everything below was measured on a Cynthion r1.4, not derived from documentation
alone. Tracking: [#82](https://github.com/awtoau/cynthion-workspace/issues/82)
(bring-up), [#84](https://github.com/awtoau/cynthion-workspace/issues/84)
(streaming over the sideband link).

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
`ecp5-test/power_monitor/registers.py`.

## Scaling

Sense resistors are **0.02 Ω ±1%** (R1, R2, R42, R59 on the r1.4.0 sheet), one per
channel, with 18.7 k ±1% dividers alongside.

| | Value |
|---|---|
| VBUS full scale | 0–32 V, 16-bit → **488.3 µV/LSB** |
| VSENSE full scale | 0–100 mV, 16-bit → 1.526 µV/LSB |
| Current per LSB | 1.526 µV ÷ 20 mΩ = **76.3 µA/LSB** |
| Max current | 100 mV ÷ 20 mΩ = **5 A** |

All four channels read `NEG_PWR_FSR` (`0x1D`) = `0x0000`, i.e. unipolar — the POR
default. Bipolar mode would halve the effective range, so this is worth re-checking
if the configuration is ever changed.

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
| host over JTAG | `ecp5-test/power_monitor/power_monitor_gateware.py` (applet `0x504D4F4E` "PMON") + `scripts/power_probe.py` |
| free-running poller | `ecp5-test/sideband/sideband_gateware.py` — reads one register on a loop, blinks an LED when it sees `0x54` |
| RISC-V CPU | `ecp5-test/riscv/i2c_master.py` (OpenCores register map) wired in `ecp5-test/riscv/vexii_hello_soc.py`; driver `firmware/cynthion-soc/src/bus.rs` (which owns `bus/i2c.rs` and `bus/mux.rs`) and `src/power.rs`, shell commands `i2c` and `power` |

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
from a port with nothing in it. 10 mA by default — an order of magnitude above the
offset, a factor of three below the smallest real draw measured (29 mA).

**The conversions are exact integer rationals, not approximations.** VBUS
millivolts are `raw × 125 / 256` (32 V / 65536 = 488.28125 µV/LSB) and current in
microamps is `raw × 78125 / 1024` (0.1 V / 65536 / 20 mΩ = 76.2939453125 µA/LSB).
No floating point is linked into a 64 KiB block RAM, and there is no rounding
constant to drift. The current multiply is done in `u64` because 65535 × 78125
overflows 32 bits — at high current, which is exactly when a wrong number matters.

Background lines go to the **USB console only**. The Apollo-facing port's TX pin
is JTAG TMS, and a background monitor transmitting there unbidden is bus
contention (`target::ANNOUNCING`).

The I2C master is ours, written to the OpenCores "I2C-Master Core" register map
(Herveille, rev 0.9) — public, with a Linux driver (`i2c-ocores`), and **no read
with a side effect anywhere in it**. See
[`../upstream-boundary.md`](../upstream-boundary.md) for why it was not taken from
elsewhere.

```bash
apollo configure ecp5-test/power_monitor/build/top.bit
./scripts/power_probe.py                 # scan, identify, measure
./scripts/power_probe.py --address 0x10  # skip the scan
./scripts/power_probe.py --read 0xFE     # one register
```

Output goes to the console and to `tmp/power_probe.log`.

## Known limitations

- Bus runs at 100 kHz; the part supports 400 kHz. Raise it now the link is known
  good.
- `SLOW` is driven low for the 1024 SPS default, but the actual sample timing has
  not been verified against the datasheet.
- `VPOWERn` (32-bit) has never been read — it is derivable from VBUS × VSENSE.
- The JTAG path requires an Apollo debug session, so it is unsuitable for
  continuous monitoring. That is what #84 addresses; the CPU path is another route.
