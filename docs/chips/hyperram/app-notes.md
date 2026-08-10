# Winbond's own application notes

Four documents, fetched from Winbond and verified against
[`../../../sources/README.md`](../../../sources/README.md) (page count + marker
string + md5). They answer four questions this tracker had open. Filed against
[#336](https://github.com/awtoau/cynthion-workspace/issues/336).

| short name used below | file | pp | rev |
|---|---|---|---|
| **AN 2025** | `sources/Winbond-AN-HyperRAM-20250528.pdf` | 53 | P01, 2025-05-28 |
| **Burst AN** | `sources/Winbond-AN-HyperRAM-Burst-Operation.pdf` | 4 | P01, 2023-09-25 |
| **Cycling AN** | `sources/Winbond-AN-pSRAM-data-cycling-effect-20220221.pdf` | 4 | P01-001, 2020-05-19 |
| **DDP AN** | `sources/Winbond-AN-SDP-DDP-128Mb-x8-HyperRAM.pdf` | 6 | A01, 2019-11-15 |
| **datasheet** | `sources/W956x8MBYA_A01-006.pdf` | 45 | A01-006, 2022-07-29 |

Our part is **HyperRAM 2.0, `W956x8Mxxxxx`, 64 Mb** — the row to read in every
per-generation table below.

## 1. The die is characterised to 250 MHz; the package is not

- **AN 2025 §6.4.9, p. 36**, CR0[7:4] for `W956x8Mxxxxx`:
  `0000b` 5 clk @133 · `0001b` 6 clk @166 · `0010b` 7 clk @200 (default) ·
  **`0010b` 7 clk "@ 250MHz Max Frequency"** · `0100b`–`1011b` Reserved ·
  `1110b` 3 clk @85 · `1111b` 4 clk @104.
- `0010b` is listed **twice**, at 200 and at 250. One encoding, two ceilings.
- Corroborated by `Config-AC.v` (plaintext, in the vendor Verilog zip): a 250 MHz
  AC block with `tACC` 28 ns = 7 × 4 ns, KGD default 250 MHz against a package
  default of 200 MHz.
- Datasheet has no 250 MHz column at all; grading is `6I` 166 MHz / `5I` 200 MHz.
- **Answer: yes for the die, no for the package.** Not permission to run the
  fitted `6I` there. What it does buy: the failure at 200 MHz is *not* the die
  running out of characterisation.

**The caveat this project did not have.** On every generation *after* ours,
250 MHz is a **different code with more latency** — `0101b` = **10 clocks** for
2.1 (`W957x8N`, `W956x8N`), 2.2 (`W958S8N`), 3.1 (`W957x6N`), 3.2 (`W958S6N`),
with `0010b` = 7 clocks capped at 200 MHz. Winbond re-priced 250 MHz at 10 clocks
on the parts that came after this one. Treat "7 clocks at 250 MHz" as the number
it printed, not as a margin anyone should lean on.

## 2. Differential clock is supported here, and the probe is self-reporting

- **AN 2025 §6.5.5.2, p. 41** — clock input by generation:

  | generation | product | clock input |
  |---|---|---|
  | 1.0-lite | `W955x8Mxxxxx` 32 Mb | single-ended **or** differential (note 1) |
  | **2.0** | **`W956x8Mxxxxx` 64 Mb — ours** | **"Single-Ended (CK) or Differential (CK/CK#)"** |
  | 2.0 | `W958D8Nxxxxx` 256 Mb | single-ended or differential |
  | 2.1 | `W957x8Nxxxxx` 128 Mb | **single-ended only** |
  | 2.1 | `W956x8Nxxxxx` 64 Mb | **single-ended only** |
  | 2.2 | `W958S8Nxxxxx` 256 Mb | **single-ended only** |
  | 3.0 | `W958D6Nxxxxx` 256 Mb | single-ended or differential |
  | 3.1 | `W957x6Nxxxxx` 128 Mb | **single-ended only** |
  | 3.2 | `W958S6Nxxxxx` 256 Mb | **single-ended only** |

  Note 1: the 32 Mb part supports single-ended *"although it is not disclosed by
  the datasheet"*.
- **AN 2025 §6.5.5.3, p. 41**: CR1[6] `1b` = Single-Ended (default), `0b` =
  Differential. Verbatim: *"For the HyperRAM device do not support differential
  clock inputs, write CR1 Bit[6] to 0 will have no effect on the CR1 Bit[6]."*
- **CONFIRMS the capability-bit reading.** Write 0, read back — `0` means the mode
  took, `1` means the part has no differential support and discarded the write.
  Safe on a part that lacks it. Every `dif`/`se` row of
  [`bist-plan.md`](bist-plan.md) rests on this and it holds.
- Not a defensive sentence: **five** current Winbond HyperRAM families in the
  table above are single-ended only, so the silent-discard path is real silicon.
- Independently agreed by the vendor Verilog model — `CR1[6] = 0` accepted, reads
  back 0 ([`models.md`](models.md)).
- **Trap.** AN 2025 Table 12 (§6.5, p. 39) lists CR1[6] with *only* the
  `1b - Single Ended - CK (default)` row. The `0b - Differential – CK#, CK`
  encoding appears **only** in §6.5.5.3. Read Table 12 alone and the bit looks
  like it has one value.
- **§7.4.5.2, p. 52**: where CK# is unused, *"it is recommended to short the CK#
  pin to ground"*. Not our case — r1.4 routes both halves (C3/D3, `LVCMOS33D`), so
  the experiment is wired for.
- **§7.4.5.3, p. 52**: during RESET# low and tVCS, CK/CK# are ineffective and may
  float, but *"it is recommended to keep the CK, CK# pin to be always HIGH or
  always LOW"*.

## 3. 4 µs is the only tCSM this part has — and it is read-only

- **AN 2025 §6.5.7, p. 44**, CR1[1:0] for `W956x8Mxxxxx`: `01b` = 4 µs;
  `00b`, `10b`, `11b` **all Reserved**. One value.
- **AN 2025 §6.5.3 Table 12 note 1** and **datasheet §9.5 Table 11 note 1**:
  *"CR1[1:0] is read only."* Not a knob — the device reports, the host obeys.
- 1 µs (`10b`) is legal on 2.0 `W958D8N` 256 Mb, and on 2.1 / 2.2 / 3.0 / 3.1 /
  3.2. Not here.
- **Confirms** the 4 µs constant already in both controllers. Nothing changes.

**Contradiction A — a generic table that does not apply to us.** AN 2025 §6.5.3
Table 13 lists `Tj < 85 → 4 µs / 01b` **and** `85 < Tj < 125 → 1 µs / 10b`. The
second row is not this part: §6.5.7 marks `10b` Reserved for `W956x8Mxxxxx`, and
datasheet Table 12 has exactly **one** row, `TCASE < 85 → 64 ms / 8192 rows /
4 µs / 01b`. The vendor model's `` `define LA_85C `` → tCSM 1000 ns is that
generic branch, not our part's spec. Note also the AN says **Tj** where the
datasheet says **TCASE** — different reference points for the same 85 °C number.

**Contradiction B — the Burst AN's own definition is wrong by 2x if taken
literally.** Burst AN §2: *"This limit is called the CS# low maximum time (tCSM)
and the tCSM will be equal to the maximum distributed refresh interval."* Read
straight, that is 64 ms / 8192 rows = **7.8 µs**, nearly double the real limit.
Datasheet §9.5.4 gives the derivation the number actually comes from: array
refresh interval ÷ rows, **then halved**, *"to ensure that a distributed refresh
interval cannot be entirely missed by a maximum length host access starting
immediately before a distributed refresh is needed"* → 7.8 / 2 → **4 µs**. Use
the datasheet.

**What the Burst AN does settle**: splitting long transactions is the *host's*
job — *"host memory controller logic splitting long transactions when reaching
the tCSM limit, or by host system hardware or software not performing a single
read or write transaction that would be longer than tCSM"*. The device does not
enforce it and does not report a violation.

## 4. Active Clock Stop — and the rule that replaced the datasheet's

**AN 2025 §7.2.2, p. 46:**

- entered **automatically** when the clock stays stable for **tACC + 30 ns**;
  current falls to `ICC6`
- read data is **latched and always driven** onto the data bus while stopped
- active current resumes *"once the data transfer is restarted with a toggling
  clock"*
- *"The Active Clock Stop state must not be used in violation of the tCSM limit.
  CS# must go High before tCSM is violated."*
- *"Note that it is recommended not to stop the clock during register access."*

**CONTRADICTS what #336's body says about this.** That sentence is a
**replacement, not an addition**. Datasheet §10.2.2 is the same paragraph word for
word up to the final sentence, where it says instead *"Note that it is recommended
to stop the clock when it is in Low state."* The app note **drops** the park-low
rule and substitutes the register-access rule. Both are in force; neither document
carries both, so neither alone is the complete set of rules for stopping the clock.

Where we stand against each:

- **Park CK low — already satisfied.** `clk_en = 0` feeds `ODDRX2F` `D0..D3` all
  zero (`gateware/soc/peripherals/hyperram_dqs_phy.py:288`), so CK parks LOW. Not
  a coincidence to rely on silently; it is now a stated requirement.
- **Do not stop during register access — a latent exposure.** `clk_en.eq(0)` in
  `CS_SETUP` is before the CA and is ordinary HyperBus, not a mid-transaction
  stop. The exposure is the coalescing master that stops the clock *mid*
  transaction (`sim-audit.md` §11 `section_clock_stop`), which shares the FSM with
  register access. Latent only because coalescing is off for correctness (#185) —
  the "wakes when a build flag moves" class of #240.
- Scale check before anyone treats this as a power feature: `ICC6` 5 mA typ /
  8 mA max against 8 mA typ / 30 mA max active — a clock gate worth ~3 mA typ
  ([`specifications.md`](specifications.md)).

## The two documents that answer none of the four

Recorded so nobody re-fetches them looking for an answer that is not there.

### DDP AN — a different part, but one useful table

- Subject is the **128 Mb x8** SDP/DDP pair, not our single-die 64 Mb part.
- **§4.9 ID0 bit assignments** give, for the 64 Mb die: `[12:8] = 01100b` –
  **13 row address bits**, `[7:4] = 1000b` – **9 column address bits**,
  `[3:0] = 0110b` – Winbond. Third independent statement of the
  **count-minus-one** convention (#109), this time in Winbond's own words.
- `[15:14]` MCP Die Address: `00b` Die 0, `01b` Die 1 — a single-die part always
  reads `00b`.
- Dual-die only, none of it ours: fixed latency **mandatory** (`CR0[3] = 1`),
  no burst across a die boundary, CR1[4:2] programmed per die, only one die may
  be in Hybrid Sleep or DPD, IO capacitance and standby current both 2x.
- **Answers none of the four.** No 250 MHz, no CR1[6], no tCSM value, no clock
  stop.

### Cycling AN — the mechanism behind refresh, with numbers for the wrong part

- Row-hammer in vendor words: repeated word-line toggling leaks charge into
  neighbouring rows; disturb risk is **worse at low temperature**, because the
  slower refresh permits more toggles per refresh period.
- **Its tREF-vs-temperature figures are the 32 Mb part**, explicitly: 819.2 ms
  below 28 °C, 327.7 ms 28–45 °C, 81.9 ms 45–85 °C, 20.5 ms above 85 °C. Our
  part's array refresh interval is **64 ms** (datasheet Table 12, TCASE < 85).
  Do not carry the 81.9 ms across.
- **Answers none of the four.** It explains why tCSM exists; it gives no value
  for this part.

## Summary

| # | question | settled by | answer |
|---|---|---|---|
| 1 | is the die characterised above 200 MHz? | AN 2025 §6.4.9 + `Config-AC.v` | yes to 250 MHz at 7 clocks — **die only**, package is graded 166/200. Later generations need 10 clocks for 250 |
| 2 | is differential clock real, and how do we probe it? | AN 2025 §6.5.5.2 / §6.5.5.3 | supported on this part; CR1[6] is a **capability bit** — write 0, read back, an unsupported part discards it. Confirms what the BIST matrix assumed |
| 3 | can tCSM be traded for efficiency? | AN 2025 §6.5.7 + §6.5.3 note 1 | no. `01b` = 4 µs is the only value **and the field is read-only**. Confirms the constant in both controllers |
| 4 | Active Clock Stop rules | AN 2025 §7.2.2 vs datasheet §10.2.2 | tACC + 30 ns entry, data stays driven, must not break tCSM. The AN's *"not during register access"* **replaces** the datasheet's *"stop the clock in the Low state"* — obey both |
