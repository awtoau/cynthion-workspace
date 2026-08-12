# DPO2036 — CC/SBU over-voltage protection

Two on Cynthion r1.4, one per Type-C port: **`U13`** on TARGET, **`U14`** on AUX.
Schematic description *"4-CH OVER-VOLTAGE PROTECTION FOR CC/SBU PINS ON USB
TYPE-C"* (`repos/cynthion-hardware/type_c.kicad_sch`).

Datasheet `sources/DPO2036.pdf`, DS40644 Rev. 2-2, July 2020.

**Index:** [`../hardware.md`](../hardware.md) · interrupt design
[`../soc-interrupts.md`](../soc-interrupts.md)

## It is a series switch, not a clamp

Connector-side and system-side pins are separate — `CC1C/CC2C/SBU1C/SBU2C`
against `CC1S/CC2S/SBU1S/SBU2S` — so the signal **passes through** the part. A
clamp would shunt to ground and need one pin per line.

Twelve pins, **no bus interface**: no SCL, no SDA, no registers, nothing to
configure. `FAULTB` is the entire software-visible surface.

| pin | name | |
|---|---|---|
| 1–4 | `SBU1C`, `SBU2C`, `CC1C`, `CC2C` | connector side |
| 5 | `CB` | ESD support capacitor, 0.1 µF to ground |
| 6 | `FAULTB` | fault status, **active-low open-drain** |
| 7–10 | `CC2S`, `CC1S`, `SBU2S`, `SBU1S` | system side |
| 11–12 | `GND`, `VSYS` | 2.7–5.5 V |

## Thresholds and timing

| | min | typ | max | |
|---|---|---|---|---|
| `VTH_OVP_CCxC` | 5.6 | 6.0 | 6.25 | V — CC trip point |
| `VTH_OVP_SBUxC` | 4.15 | 4.5 | 4.75 | V — SBU trip point |
| `VTH_OVP_HYS_CCxC` | — | 140 | — | mV |
| `tFAULTB_ASSERTION` | — | — | 300 | µs |
| `tFAULTB_DEASSERTION` | — | 4 | — | ms |
| `tOVP_RESPONSE_CC_1` | 26 | 32 | 38 | ms — minimum before the CC FETs turn back on |
| `VUVLO` | 2.15 | 2.4 | 2.55 | V |
| `RDS(ON)` CC / SBU | — | 350 mΩ / 5 Ω | — | |

Short-to-VBUS tolerance on `CCxC` is 22 V, clamping 7 V on `CCxS`.
IEC61000-4-2 ESD protection on all four connector-side pins. Dead-battery
support: `CC1C`/`CC2C` carry the specification's `Rd` internally. Built-in
over-temperature protection.

## `FAULTB` is a level, and it can be missed

**Auto-recovery** — the features list says so. The part asserts `FAULTB` within
300 µs, holds the FETs off for **at least 26–38 ms**, then releases when the
over-voltage has gone. So the pin is low for roughly 30–42 ms per event.

`tFAULTB_DEASSERTION` typ 4 ms is a **propagation delay** from the condition
ending to the pin releasing — not a pulse width. Neither timing row carries a
test condition in the datasheet.

**A repeating fault is a train of ~30–42 ms assertions separated by the recovery
interval**, which aliases badly against any periodic sampler.

**Not established:** whether over-temperature protection also asserts `FAULTB`,
and whether the flag is shared across all four channels or reflects only the
faulting pair.

## What this board does with it

`U13` pin 6 → `R100` 10 kΩ pull-up → ECP5 ball **D4** → `i2c_mux.target_fault`,
a read-only CSR bit. `U14`'s is `i2c_mux.aux_fault`.

**Nothing latches it and nothing acts on it.** The only firmware reference is a
status line printed by the `typec` shell command. A level read at a poll answers
*"is it faulting now"*; the question is *"has it faulted"*, and only a capture
answers that.

The interrupt design makes both lines edge-captured sources —
[`../soc-interrupts.md`](../soc-interrupts.md). Issues:
[#506](https://github.com/awtoau/cynthion-workspace/issues/506) (the datasheet
reading), [#507](https://github.com/awtoau/cynthion-workspace/issues/507)
(nothing responds to the fault).
