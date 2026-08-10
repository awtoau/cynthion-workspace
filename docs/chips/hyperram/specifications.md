# W956A8MBYA6I specifications

Every number, per speed grade, with its source. A reference sheet — the analysis
lives in [`w956a8.md`](w956a8.md), the simulation in [`models.md`](models.md).

**Sources.** `sources/W956x8MBYA_A01-006.pdf` (rev A01-006, 2022-07-29) unless a
row says otherwise. Rows marked *(model)* come from `Config-AC.v`, the plaintext
half of Winbond's simulation model, and rows marked *(AN 2025)* from
`sources/Winbond-AN-HyperRAM-20250528.pdf`. Both are in
[`../../../sources/README.md`](../../../sources/README.md).

**The fitted part is the `6I`: 3.0 V, 166 MHz.** The 166 MHz column is the one
this board is graded to; 200 MHz is the `5I` the schematic approves as a
substitution, and 250 MHz appears only in the model and the 2025 app note.

## Identity

| | |
|---|---|
| order code | `W956A8MBYA6I` — `A` = 3.0 V, `6` = 166 MHz, `I` = industrial |
| density | 64 Mbit = **8 MiB** |
| organisation | 4 M words x 16 bit, 8 DQ, DDR |
| addressing | 13 row + 9 column bits, 8192 rows x 512 words |
| package | 24-ball TFBGA, 6 x 8 mm |
| ID0 / ID1 | `0x0c86` / `0x0001` |
| CR0 / CR1 at POR | `0x8f2f` / `0xffc1` |
| generation | HyperRAM 2.0 *(AN 2025)* |

Register bit meanings are in [`w956a8.md`](w956a8.md); the values above are
confirmed three ways — board, datasheet, and Winbond's own model.

## Supply and DC

| parameter | min | max | unit |
|---|---|---|---|
| `VCC`, `VCCQ` (3.0 V device) | 2.7 | 3.6 | V |
| `VIL` | -0.15 x VCC | 0.3 x VCC | V |
| `VIH` | 0.7 x VCC | 1.15 x VCC | V |
| `VOL` (IOL = 100 µA) | — | 0.2 | V |
| `VOH` (IOH = 100 µA) | VCCQ - 0.2 | — | V |
| `TCASE` operating | -40 | +85 | °C |

**Absolute maxima** — stress ratings, not operating conditions: VCC/VCCQ and any
ball -0.5 V to VCC + 0.5 V; storage -65 to +150 °C; soldering +260 °C for 10 s;
output short-circuit current 100 mA, one output at a time, under one second.

**Capacitance** (3.0 V, Table 19): `CI` (CK, CK#, CS#) 3.0 pF max, `CO` (RWDS)
3.0 pF max, `CIO` (DQ) 3.0 pF max, deltas 0.25 pF. Die only — package not
included. The datasheet asks for CK/CK#/RWDS/DQ capacitance to be *matched*,
which is a layout constraint rather than a number to check.

## Current — nothing else in this repo carries these

| symbol | what | typ | max | conditions |
|---|---|---|---|---|
| `ICC1` | active **read** | 8 mA | **30 mA** | CS# low, 200 MHz, VCC 3.6 V |
| `ICC2` | active **write** | 8 mA | **30 mA** | CS# low, 200 MHz, VCC 3.6 V |
| `ICC6` | **Active Clock Stop** | 5 mA | 8 mA | CS# low, clock halted, -40 to +85 °C |
| `ICC4` | standby, full array | 52 µA | 250 µA | CS# high, VCC 3.6 V |
| `ICC5` | reset | — | 1 mA | CS# high, RESET# low |
| `ICC7` | power-up | — | 35 mA | inrush |
| `IDPD` | deep power down | — | 12 µA | VCC 3.6 V, TCASE 85 °C |
| `ILI` | input leakage | — | 2 µA | 15 µA while RESET# is low |

Three things follow that are worth knowing before anyone budgets power:

- **Active is ~8 mA typical and 30 mA worst case**, so the part is a rounding
  error next to the FPGA — but the 35 mA `ICC7` inrush at power-up is the largest
  single number here and it lands at the same moment everything else starts.
- **Active Clock Stop saves ~3 mA typ, not orders of magnitude.** It is a clock
  gate, not a sleep state; the array stays refreshed and the read data stays
  driven. Deep power down is the ~1000x saving, and it loses the contents.
- **Partial-array standby is worth 22 µA typ at most** (52 → 30 µA going from
  full array to 1/8). That is the whole prize for CR1[4:2], and it explains why
  option 7 in [`w956a8.md`](w956a8.md) is judged speculative.

Measurable on this board with the PAC1954 (#82, #84) if the rail is separable.

## Refresh and tCSM

| | |
|---|---|
| array refresh interval | 64 ms, TCASE < 85 °C |
| array rows | 8192 |
| **tCSM** (max CS# low) | **4 µs**, `CR1[1:0] = 01b` |
| `CR1[1:0]` legal values | **`01b` only** on this part *(AN 2025 §6.5.7)* |
| tCSM, above 85 °C | 1 µs *(model, `` `define LA_85C ``)* |

The 4 µs is derived, not arbitrary: 64 ms / 8192 rows = 7.8 µs per row, **halved**
so a maximum-length access starting immediately before a refresh cannot make the
device miss one entirely. The datasheet says so in as many words, and the vendor's
*Burst Wrapped Operation* app note puts the obligation on the host: split long
transactions, the device will not do it for you.

## AC timing, per grade

3.0 V columns. The 250 MHz column is **not in the datasheet** — it is what
Winbond's own model uses for a KGD die, and the 2025 app note lists the 7-clock
latency code as legal there.

| parameter | 100 MHz | 133 MHz | 166 MHz | 200 MHz | 250 MHz *(model)* |
|---|---|---|---|---|---|
| `tCK` min | 10 | 7.5 | **6** | 5 | 4 |
| `tCK` max | 100 | 100 | 100 | 100 | — |
| `tCKHP` duty | 0.45–0.55 tCK | ← | ← | ← | ← |
| `tCSHI` CS# high between transactions | 10 | 7.5 | **6** | 6 | 6 |
| `tRWR` read-write recovery | 40 | 37.5 | **36** | 35 | 28 |
| `tACC` initial access | 40 | 37.5 | **36** | 35 | 28 |
| `tCSS` CS# setup to CK | 3 | 3 | **3** | 4 | 4 |
| `tDSV` data strobe valid | 12 | 12 | **12** | 6.5 | 5 |
| `tIS` input setup | 1.0 | 0.8 | **0.6** | 0.5 | 0.5 |
| `tIH` input hold | 1.0 | 0.8 | **0.6** | 0.5 | 0.5 |
| `tCKD` CK to DQ valid, max | 7 | 7 | **7** | 6.5 | 5.0 |
| `tCKDI` CK to DQ invalid, max | 5.2 | 5.5 | **5.6** | 5.7 | 4.2 |
| `tCKDS` CK to RWDS valid, max | 7 | 7 | **7** | 6.5 | 5.0 |
| `tDV` data valid, min | 2.7 | 1.875 | **1.3** | 1.45 | 0.8 |
| `tDQLZ` CK to DQ low-Z | 0 | 0 | 0 | 0 | — |

All in ns. Clock jitter of ±5% is permitted. Minimum frequency is not a fixed
number — `tCK` max of 100 ns is bounded in practice by tCSM, the initial latency
and the burst length together.

**`tDV` is the number that runs out first.** At 166 MHz the data eye is 1.3 ns
against a 6 ns period; at 200 MHz it is 1.45 ns against 5 ns. That is the budget
the capture phase has to land inside, and it is why the failure at 200 MHz is
plausibly analogue rather than protocol — see [`w956a8.md`](w956a8.md).

Note `tCKDI` **rises** from 5.2 to 5.7 ns as the grade gets faster while `tCKD`
falls: the valid window is squeezed from both ends, not just shortened.

## Initial latency

`CR0[7:4]`, and only five of sixteen codes are legal. `clocks = 5 + sext4(code)`.

| code | clocks | max frequency |
|---|---|---|
| `1110b` | 3 | 85 MHz *(AN 2025; the datasheet says 83)* |
| `1111b` | 4 | 104 MHz |
| `0000b` | 5 | 133 MHz |
| `0001b` | 6 | 166 MHz |
| `0010b` | 7 | 200 MHz **(POR default)**, and 250 MHz *(AN 2025)* |
| `0011b`–`1101b` | reserved | — |

`CR0[3] = 1` (POR) doubles the count — 7 becomes 14 CK before data. More latency
is always safe at a lower clock; the frequencies are ceilings on the code, not
requirements.

## Checking a number

The datasheet copy is verified by page count and revision string:

    pdfinfo sources/W956x8MBYA_A01-006.pdf | grep Pages          # 45
    pdftotext -layout sources/W956x8MBYA_A01-006.pdf - | grep -c 'A01-006'

A 10-page copy of this document is in circulation and is abridged — it is missing
every section above. [`../../../sources/README.md`](../../../sources/README.md)
has the detail.
