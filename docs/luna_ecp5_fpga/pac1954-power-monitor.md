# PAC1954 power monitor — bring-up and measurement

Standalone gateware that reads the on-board PAC195X power monitor over I²C and
exposes it as JTAG registers, driven from the host over the Apollo debug link.

Everything below was measured on a Cynthion r1.4, not derived from
documentation alone. Tracking issues: [#82](https://github.com/awtoau/cynthion-workspace/issues/82)
(bring-up), [#84](https://github.com/awtoau/cynthion-workspace/issues/84)
(streaming over the sideband link).

## Why a standalone bitstream

The moondancer SoC has no I²C peripheral — its peripherals are `ADVERTISER`,
`GPIO0/1`, `INFO`, `LEDS`, `SPI0`, `TIMER0/1`, `UART0/1`, `USB0/1/2`. There is
no path from the RISC-V core to the device, and adding an I²C block plus a
driver is disproportionate for a part that only needs periodic polling.

This mirrors the existing LED and PHY selftests: elaborate → load over Apollo
JTAG → drive registers from a host script.

## Device facts

| | |
|---|---|
| Part | `PAC195X-1-VQFN` (`power_distribution` sheet) |
| Variant | PAC1954-1, confirmed by `PRODUCT_ID` = `0x7B` |
| I²C address | **`0x10`** |
| Datasheet | [`sources/PAC195X-Family-DS20006539B.pdf`](../../sources/PAC195X-Family-DS20006539B.pdf) |

The address is set by a resistor from ADDRSEL to ground and latched at
power-up — it cannot be changed at runtime (DS20006539B Table 6-1: 0R/GND →
`0x10`, 499R → `0x11`, … 226k → `0x1E`). Which resistor is fitted could not be
determined reliably from the KiCad schematic, so the bring-up **scans** the
range and reports what answers. Exactly one address responded.

`PWRDN` is active-low and declared `PinsN` in the platform, so driving `.o` low
de-asserts power-down. Getting this wrong produces NAKs on every address, which
is indistinguishable from a wrong address.

## Channel → port mapping

**This is not the intuitive ordering.** Channel 1 is not CONTROL.

| PAC channel | Physical port |
|---|---|
| 1 | TARGET_A |
| 2 | TARGET_C |
| 3 | AUX |
| 4 | CONTROL |

Derived from the **clean `r1.4.0` schematic tag** — not the working tree, which
sits on the Coppelia branch with in-progress edits and a KiCad 7→10 re-save —
by matching PAC `SENSEn` pin coordinates against the sheet's global labels.
Channels 3 and 4 share a y-coordinate and are disambiguated by which side of the
symbol they sit on: AUX labels at x=92.71 match the left-side pins (x=95.25),
CONTROL at x=124.46 matches the right (x=123.19).

Then confirmed physically — see *Validation* below.

## Scaling

Sense resistors are **0.02 Ω ±1%** (R1, R2, R42, R59 on the r1.4.0 sheet), one
per channel, with 18.7 k ±1% dividers alongside.

| | Value |
|---|---|
| VBUS full scale | 0–32 V, 16-bit → **488.3 µV/LSB** |
| VSENSE full scale | 0–100 mV, 16-bit → 1.526 µV/LSB |
| Current per LSB | 1.526 µV ÷ 20 mΩ = **76.3 µA/LSB** |
| Max current | 100 mV ÷ 20 mΩ = **5 A** |

All four channels read `NEG_PWR_FSR` (`1Dh`) = `0x0000`, i.e. unipolar — the POR
default. Bipolar mode would halve the effective range, so this is worth
re-checking if the configuration is ever changed.

## Registers

Measurement registers, all 16-bit, one per channel:

| Register | Address |
|---|---|
| `REFRESH` | `00h` (Send Byte) |
| `VBUSn` | `07h`–`0Ah` |
| `VSENSEn` | `0Bh`–`0Eh` |
| `VBUSn_AVG` | `0Fh`–`12h` (8× averaged) |
| `VSENSEn_AVG` | `13h`–`16h` |
| `VPOWERn` | `17h`–`1Ah` (32-bit) |

Issue `REFRESH` before reading: it latches VBUS, VSENSE and the accumulators
together, so all four channels come from one sample instant rather than
whenever each register happened to be read.

### Transfer size matters

The identification registers are single bytes; the measurement registers are
16-bit. The PAC195X **auto-increments its address pointer within a read**, so a
2-byte read of a 1-byte register returns that register *plus the next one*.

This bit during development: reading `MANUFACTURER_ID` with `size=2` returned
`0x02` (the revision) and `PRODUCT_ID` returned `0x54` (the manufacturer) —
every value shifted one register along. Here it failed loudly because the
expected values are known. In a streaming path a size mismatch would silently
produce plausible-looking wrong numbers, so the size is explicit in the API.

## Usage

```bash
# Build (needs the FPGA toolchain on PATH)
source ~/opt/oss-cad-suite/environment
python3 -c "
import sys; sys.path.insert(0,'ecp5-test')
from power_monitor.power_monitor_gateware import PowerMonitorTest
from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4
CynthionPlatformRev1D4().build(PowerMonitorTest(), do_program=False,
                               build_dir='ecp5-test/power_monitor/build')"

# Load and run
apollo configure ecp5-test/power_monitor/build/top.bit
./scripts/power_probe.py                 # scan, identify, measure
./scripts/power_probe.py --address 0x10  # skip the scan
./scripts/power_probe.py --read 0xFE     # one register
```

Output goes to the console and to `tmp/power_probe.log`.

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

## Validation: the cable-removal test

The strongest check available, because it cross-checks mapping, scaling and
plausibility at once. With AUX unplugged:

| Port | Before | After |
|---|---|---|
| AUX | 5.165 V, 34.41 mA | **0.007 V, 0.92 mA** |
| CONTROL | 5.156 V, 29.14 mA | **5.151 V, 63.55 mA** |

Three things follow:

1. **The mapping is right.** AUX went dead when AUX was unplugged. Had channel 3
   actually been CONTROL, the wrong channel would have dropped.
2. **The scaling is right.** 34.41 + 29.14 = 63.55 mA before; 63.55 mA on
   CONTROL alone after. Total board draw is unchanged — it just all flows
   through one rail. Conservation of current, matching to within 2%.
3. **~65 mA at 5 V (≈325 mW) is the real idle draw.** It initially looked
   implausibly low for an ECP5-12F plus three PHYs, but that was because it was
   split across two rails, and the bring-up bitstream leaves most of the fabric
   unused with the PHYs not enumerating.

Ten consecutive samples spanned 28–36 mA, confirming the ADC is genuinely
converting rather than returning a latched value.

This test is worth keeping as a regression: **unplug a rail and confirm the
current migrates rather than vanishes.**

## Known limitations

- Bus runs at 100 kHz; the part supports 400 kHz. Raise it now the link is known
  good.
- `SLOW` is driven low for the 1024 SPS default, but the actual sample timing
  has not been verified against the datasheet.
- `VPOWERn` (32-bit) is not read — it is derivable from VBUS × VSENSE.
- Requires an Apollo debug session, so it is unsuitable for continuous
  monitoring. That is what #84 addresses.
