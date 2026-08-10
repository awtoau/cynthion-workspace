# `Config-AC.v`, transcribed

Winbond's own AC numbers and register defaults, from the plaintext half of their
simulation model. The file is vendor IP and cannot be committed; this page is the
tracked copy of what it says. [#345](https://github.com/awtoau/cynthion-workspace/issues/345).

**Provenance.** `sources/models/W956X8MBY_verilog_p.zip` → nested
`W956A8MBYA_verilog_p.zip` → `Config-AC.v`, 11 KB, unpacked to `tmp/questa-vp/`
and read **2026-08-10**. No revision string, no date and no part number inside the
file — the enclosing zip is the only version marker. Fetch route in
[`../../../sources/README.md`](../../../sources/README.md).

**What it is.** A `parameter` include and nothing else, pulled in by
`W956A8MBYA.modelsim.vp` whose body is AES-encrypted. No behaviour, no
assertions, no prose. Every figure below is a literal in that file; anything
derived is marked as such.

**Why it counts.** It is the vendor's own simulation constants rather than a
reading of the datasheet's prose, and it carries a **250 MHz** column and a
**temperature-dependent tCSM** the datasheet does not.

## The configuration it ships with

| define | shipped as | the alternative |
|---|---|---|
| `X8` | set | bus width |
| `SDP` | set — single die | `DDP`, dual die |
| `POWER3V` | set — 3.0 V | `POWER1P8V` |
| `PKG` | set — packaged part | `KGD`, known good die |
| `LA_85C` | **commented out** | uncomment for use above 85 °C |
| `STOP_ON_ERROR` | `0` | `1` halts the sim on a violation |
| `TEST_DELAY` | `0.1` ns | — |

- The shipped set is exactly the part on this board, **except `LA_85C`**.
- `LA_85C` is a user edit, described in the file's first line verbatim, typos
  included: *"If you uase the part the temparature range is over 85 degree c, You
  can uncomment the following line"*. A default run therefore uses the **loose**
  tCSM (4 µs) and never the hot one, silently.
- `SDP` sets `MEM_BITS`/`ADDR_BITS` = 22 (A0–A21, 4 M words = 8 MiB);
  `DDP` sets 23.

## Grade selection, and the trap in it

`tCK` per grade, ns: `T250` 4.0, `T200` 5.0, `T166` 6.0, `T133` 7.5, `T104` 9.6,
`T100` 10.0, `T85` 11.76. Duty `tCH`/`tCL` 0.45–0.55 tCK.

- With **no `T*` define**, `tCK` falls back to `clk_period` = **5.0 ns (200 MHz)
  under `PKG`**, 4.0 ns (250 MHz) under `KGD`. The file states in its own defaults
  what [#336](https://github.com/awtoau/cynthion-workspace/issues/336) recorded:
  **die characterised to 250, package to 200.**
- **The AC-parameter chain has branches for `T100`/`T133`/`T166`/`T200`/`T250`
  only, and no default branch.** `T85` and `T104` set `tCK` and get **no AC
  parameters at all** — the same broken elaboration as passing no grade.
  Measured 2026-08-10, Questa Lattice OEM 2024.2 `vlog -sv` on
  `W956A8MBYA.modelsim.vp`: `+define+T166` compiles clean, `+define+T104` and
  `+define+T85` each give **7 × `(vlog-2730) Undefined variable: '<protected>'`**.
  `scripts/hyperram_vendor_model_sim.py` offered all seven grades; now five.

## AC parameters, 3.0 V, per grade

All ns. `T250` is the KGD block — **it does not describe the packaged part on this
board**.

| parameter | what | T100 | T133 | **T166** | T200 | T250 |
|---|---|---|---|---|---|---|
| `tCSHI` | CS# high between transactions | 10 | 7.5 | **6** | 6 | 6 |
| `tRWR` | read-write recovery | 40 | 37.5 | **36** | 35 | 28 |
| `tACC` | initial access | 40 | 37.5 | **36** | 35 | 28 |
| `tCSS` | CS# setup to CK | 3 | 3 | **3** | 4 | 4 |
| `tDSV` | CS# Low to RWDS valid | 12 | 12 | **12** | 6.5 | 5 |
| `tIS` | input setup | 1.0 | 0.8 | **0.6** | 0.5 | 0.5 |
| `tIH` | input hold | 1.0 | 0.8 | **0.6** | 0.5 | 0.5 |
| `tCKD_max` | CK to DQ valid | 7.0 | 7.0 | **7.0** | 6.5 | 5.0 |
| `tCKDI_max` | CK to DQ invalid | 5.2 | 5.5 | **5.6** | 5.7 | 4.2 |
| `tDV` | data valid, min | 2.7 | 1.875 | **1.3** | 1.45 | 0.8 |
| `tCKDS_max` | CK to RWDS valid | 7.0 | 7.0 | **7.0** | 6.5 | 5.0 |
| `tCKDSR_max` | CK to RWDS, read | 7.0 | 7.0 | **7.0** | 7.0 | 5.5 |
| `tDSS_min/max` | RWDS to CK skew | ±0.80 | ±0.8 | **±0.8** | ±0.4 | ±0.4 |
| `tDSH_min/max` | RWDS hold skew | ±0.80 | ±0.8 | **±0.8** | ±0.4 | ±0.4 |
| `tDSZ` | RWDS to Hi-Z | 7 | 7 | **7** | 6.5 | 5 |
| `tOZ` | output to Hi-Z | 7 | 7 | **7** | 6.5 | 5 |

Grade-independent floors: `tCKD_min` = `tCKDS_min` = `tCKDSR_min` = **1 ns**,
`tCSH_MIN` = `tCSH_MAX` = **0.0 ns** (no CS# hold after the last clock).

**1.8 V differs and this board is 3.0 V**, so only the deltas, T100/T133/T166/T200:
`tCKD_max` 5.5/5.5/5.5/5.0, `tCKDI_max` 4.3/4.5/4.6/4.2, `tDV` 3.3/2.375/1.8/1.45,
`tCKDS_max` 5.5 (5.0 at T200), `tCKDSR_max` 5.5, `tDSZ` = `tOZ` 6 (5 at T200),
`tDSV` 5 at T200, and tighter `tDSS`/`tDSH` at T133 (±0.6) and T166 (±0.45).
`T250` has no voltage split; its values are the 1.8 V shape throughout.

### Three identities the table settles

Derived here, not stated in the file:

- **`tRWR` == `tACC` at every grade.** One number, two names; no separate
  read-after-write penalty to budget beyond the initial latency.
- **`ceil(tACC / tCK)` = 4, 5, 6, 7, 7** for T100…T250 — *exactly* the datasheet's
  minimum latency code per frequency. The latency-code table is not an independent
  spec; it is `tACC` divided by the period. At CK 192 MHz that is
  `ceil(35/5.208)` = **7**, which is why LC7 is the only legal code there.
- **`tDSV` spans 1.2 / 1.6 / 2.0 / 1.3 / 1.25 CK.** The CA is 3 CK, so the
  fraction of the command period in which RWDS is a **float** is grade-dependent,
  and worst at **T166: two whole clocks of the three**. See question 1.

## Timings that are not per grade

| parameter | value | note |
|---|---|---|
| `tCSM` | **4000 ns**, or **1000 ns** with `LA_85C` | the max CS# Low; the only temperature-dependent number in the file |
| `tRP` | 200 ns | RESET# pulse |
| `tRH` | 200 ns | RESET# high |
| `tRPH` | 400 ns | RESET# to first access |
| `tVCS` | 150 000 ns = 150 µs | power-up to ready |
| `tDMV` | 0 ns | data mask valid |
| `refresh_cycle` | `4e3` — **commented *"optional value"*** | see question 2 |

**Hybrid sleep:** `tHSIN` 3000, `tHSMXC_min` 60, `tHSMX_max` 70 000,
`tCSHS_min` 60, `tCSHS_max` 3000, `tEXTHS_max` 100 000 ns.

**Deep power down:** `tDPDIN` 3000 (3 µs), `tDPDCSL` 200, `tDPDOUT_MAX` 150 000
(150 µs), `tCSDPD_min` 200, `tCSDPD_max` 3000, `tEXTDPD` 150 000 ns.

## Register map

POR values, given as bit fields *and* hex in the file itself:

| register | POR | fields as written |
|---|---|---|
| ID0 | `0x0c86` | `00_0_01100_1000_0110` — 13 row bits, 9 column bits |
| ID0, die 1 (DDP) | `0x4c86` | `01_0_…` |
| ID1 | `0x0001` | HyperRAM 2.0 |
| CR0 | `0x8f2f` | `1_000_1111_0010_1_1_11` |
| CR1 | `0xffc1` | `11111111_1_1_0_000_01` |
| CR1, **`LA_85C`** | **`0xffc2`** | `11111111_1_1_0_000_10` — only `CR1[1:0]` moves |

### CA constants — they confirm our address mapping exactly

| constant | value |
|---|---|
| `ID0_READ` / `ID1_READ` | `48'hE0_00_00_00_00_00` / `…_01` |
| `CR0_READ` / `CR0_WRITE` | `48'hE0_00_01_00_00_00` / `48'h60_…` |
| `CR1_READ` / `CR1_WRITE` | `48'hE0_00_01_00_00_01` / `48'h60_…` |
| die 1 (DDP) | the same `\| (1'b1 << 35)`, commented *"A[22]→CA[35]"* |

- `E0` = CA[47:45] `111` — read, register space, **linear**; `60` = `011` — write,
  register space, linear. **A register access sets CA[45] even though burst type is
  meaningless there**, which is what
  [#320](https://github.com/awtoau/cynthion-workspace/issues/320) changed
  `hyperram_controller.py` to do. Winbond's own constants agree.
- `A[22] → CA[35]` fixes the mapping as CA[44:16] = A[31:3], CA[15:3] = 0,
  CA[2:0] = A[2:0] — bit for bit what `ca.eq(Cat(...))` builds.
- CR0 differs from ID0 by CA[24] alone → **word address 0x800**, CR1 `0x801`,
  ID0 `0x0`, ID1 `0x1`. The addresses `scripts/hyperram_regfuzz.py` already uses.

### CR0 fields

- `[15]` `0` = enter deep power down, `1` = normal.
- `[14:12]` drive strength: `000` 34 Ω *(default)*, `001` 115, `010` 67, `011` 46,
  `100` 34 (second entry, named `drive_34_2_ohms`), `101` 27, `110` 22, `111` 19.
- `[7:4]` initial latency: `1110` LC3, `1111` LC4, `0000` LC5, `0001` LC6,
  `0010` LC7, **`0011` LC8, `0100` LC9**.
- `[3]` `0` variable, `1` fixed *(default)*.
- `[2]` `0` hybrid wrap, `1` legacy wrap *(POR, per `0x8f2f`)*.
- `[1:0]` burst length: `00` 128 B, `01` 64 B, `10` 16 B, `11` 32 B *(default)*.

### CR1 fields

- `[15:12]` = `1010` is the **software reset** (`CR1_SOFT_RESET`); POR is `1111`.
- `[6]` `0` differential clock, `1` single-ended *(POR)*.
- `[5]` `1` enters half sleep.
- `[4:2]` partial array refresh: `000` full, `001` bottom ½, `010` bottom ¼,
  `011` bottom ⅛, `100` none, `101` top ½, `110` top ¼, `111` top ⅛.
- `[1:0]` distributed refresh, named in the file: `00` `2_TIMES`, `01` `4_TIMES`,
  `10` `TBD_TIMES`, `11` `1P5_TIMES`. POR `01` below 85 °C, **`10` with `LA_85C`**.

## The three questions

### 1. RWDS during the CA period — it does not say, but it bounds when

Nothing in the file describes RWDS's *meaning*; that lives in the encrypted body,
and [`models.md`](models.md) has it from the model's behaviour. What the file does
give is **`tDSV`, CS# Low to RWDS valid, and it is 12 ns at T166** — the whole
answer to *when* the level may be believed:

- 12 ns at CK 166 MHz = **2 CK of a 3 CK command period**. Only the last clock of
  the CA carries a guaranteed-driven RWDS. At T200 it is 6.5 ns = 1.3 CK, at T250
  5 ns = 1.25 CK — better, but never under one clock at any grade this board runs.
- **This contradicts `hyperram_controller.py`.** The derivation at
  [`hyperram_controller.py:182`](../../../gateware/soc/peripherals/hyperram_controller.py)
  reads *"the window opens one cycle after CS# because tDSV (12 ns) is under one
  sync cycle but not zero"*. 12 ns is under one sync cycle only at **sync ≤ 83 MHz**.
  At 166 MHz it is two.
- Consequence, using that comment's own pin arithmetic (CS# falls at pin cycle
  `1+P`, request valid `2+P .. 4+P`, sample cycle `R+2`): the sample lands on pin
  cycle `2+P`, **one CK after CS# falls — inside tDSV**. The first
  guaranteed-valid pin cycle at 166 MHz is `3+P`, i.e. sample cycle **`R+3`**.
  `R+3` also satisfies T200 and T250 (`ceil(tDSV/tCK)` = 2 at all three), and the
  window still closes at `R+4`.
- So the sample instant is **one cycle early at every grade above 83 MHz**, in the
  unsafe direction, and it is early by exactly the amount that makes it read a
  floating bus. That is a live candidate for
  [#338](https://github.com/awtoau/cynthion-workspace/issues/338)'s
  variable-latency failures, and it is a *different* claim from
  [#321](https://github.com/awtoau/cynthion-workspace/issues/321), which fixed
  staleness rather than the instant.
- Not changed here: the fix belongs with #338's build matrix, and a float reads as
  1 or 0 by chance, so the rate it produces has to be measured rather than assumed.

### 2. Refresh interval per grade — no, and not per temperature either

- **One parameter, `refresh_cycle = 4e3`, commented *"optional value"***. Not per
  grade, not per temperature, and the comment disclaims it.
- 4e3 ns equals `tCSM` below 85 °C, which may be coincidence — the file does not
  connect them.
- The observed model behaviour contradicts a 4 µs period being used as one:
  [`models.md`](models.md) records refresh elections **100 CS# assertions apart
  regardless of wall time** (21.07 µs for 100 single-word transactions, 148.07 µs
  for 100 × 128-word). A 4 µs timer would have fired ~5 times inside the first.
- The 64 ms / 8192 rows array figure is **not in this file** at all.
- So it neither supports nor refutes a periodic ~1-in-128 rate. The temperature
  dependence the file *does* carry is `tCSM` and the `CR1[1:0]` POR value, below.

### 3. AC parameters at the fitted grade — the `T166` block, and two of ours are wrong

The board carries a **`6I`: packaged, 3.0 V, 166 MHz** → the `T166` + `POWER3V`
column above, bolded. `T200` is the `5I` the schematic allows as a substitution;
**`T250` is `KGD` and does not apply to a packaged part.**

- **`tCSHI` is 6 ns at T166, not 10.** `T_CSHI_NS = 10.0` in both
  [`hyperram_controller.py`](../../../gateware/soc/peripherals/hyperram_controller.py)
  and [`hyperram_dqs_controller.py`](../../../gateware/soc/peripherals/hyperram_dqs_controller.py)
  is the **T100** figure applied to a 166 MHz part. Safe (too long), and costs one
  recovery cycle per transaction at sync 166 MHz — `ceil(10 × 0.166)` = 2 against
  `ceil(6 × 0.166)` = 1. At sync 192 both round to 2, so nothing is lost there.
  It is a gap with no recorded reason, which is
  [#341](https://github.com/awtoau/cynthion-workspace/issues/341)'s subject.
- **`tRWR` is 36 ns at T166 and nothing implements it.** `RECOVERY` in both
  controllers counts `tCSHI` only; there is no `T_RWR` constant anywhere in the
  repo. Since `tRWR` == `tACC` and both are satisfied by the initial latency
  (`ceil(36/6)` = 6 CK ≤ the 7 CK LC7 selects), the omission is currently covered
  — **by the latency code, not by anything that knows why**.
- `tCSS` 3 ns, `tIS`/`tIH` 0.6 ns, `tDV` 1.3 ns and `tDSS`/`tDSH` ±0.8 ns are the
  T166 numbers the capture phase has to live inside.

## What it contradicts, and what it confirms

**Contradicts:**

| claim, and where | `Config-AC.v` |
|---|---|
| *"`CR0[7:4]` = `0011b`–`1101b` reserved"*, [`specifications.md`](specifications.md) | names **`0011` = LC8 and `0100` = LC9**. Seven codes, not five, and `clocks = 5 + sext4(code)` holds for all seven |
| *"`CR1[1:0]`: `00b`, `10b`, `11b` Reserved, `01b` the only defined value"*, [`w956a8.md`](w956a8.md) | names all four, and makes **`10b` the POR value under `LA_85C`** |
| *"tDSV (12 ns) is under one sync cycle"*, `hyperram_controller.py:182` | 12 ns is 2 CK at 166 MHz — see question 1 |
| *"`tCSHI`, 10 ns of CS# high"*, [`w956a8.md`](w956a8.md) | 10 ns is the T100 value; **6 ns** at T166 |
| `--grade T85` / `T104` are usable, `hyperram_vendor_model_sim.py` | no AC block exists for either |

**Confirms:** ID0/ID1/CR0/CR1 POR values; the CA bit layout and the register word
addresses; `CR0[3]` fixed as POR; `CR0[2]` legacy wrap as POR; CA[45] set on
register access ([#320](https://github.com/awtoau/cynthion-workspace/issues/320));
8 MiB from 22 address bits; the AC columns already in
[`specifications.md`](specifications.md); and `tCSM` 4 µs / 1 µs
([#317](https://github.com/awtoau/cynthion-workspace/issues/317)).

**The `LA_85C` pairing is the finding with a use.** Above 85 °C the file changes
*two* things together: `tCSM` 4000 → 1000 ns, and CR1's POR `0xffc1` → `0xffc2`,
i.e. `CR1[1:0]` `01` → `10`. If real silicon reports `10b` in the hot regime, then
**`CR1[1:0]` is an in-band read of which tCSM applies** and firmware can pick the
CS# cap without a temperature sensor. The file does **not** say whether that is a
runtime function of die temperature or a build-time modelling choice for a
differently-ordered part — it is a compile-time `define`. Testable on the board:
read CR1 hot and cold. Until then the 4 µs cap stays 4× too loose when hot.

## What it does not say

- No behaviour of any kind — no protocol, no state machine, no RWDS semantics.
- No refresh period, no row count, no 64 ms.
- No tCSM per grade (tCSM is the same at every frequency).
- No revision, date or part number inside the file.
- Nothing about burst geometry beyond the `CR0[1:0]` byte counts.
- No `T85`/`T104` AC parameters, despite `tCK` entries for both.
