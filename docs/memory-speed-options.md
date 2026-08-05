# Every remaining way to make the HyperRAM and the config flash faster

A survey, not an implementation. What is left on each part after the ceilings in
[`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) and
[`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md), what each option
is worth with the arithmetic, what it costs to try, and who has published a
number. **Nothing here was run on hardware** — the board was in use.

**Index:** [`hardware.md`](hardware.md)

## Three headlines

**The flash is finished.** Three of the four candidates the brief named —
QPI, DTR, and `0xC0` Set Read Parameters — **do not exist on this part**, and
that is now settled from the schematic's own part number rather than inferred
from a family page. Bulk quad reads already run at **99.6% of the four-lane
theoretical maximum**. There is no efficiency left to find; only SCK, and the
instrument runs out before the flash does. The fourth candidate, `0x77`, is real
but it is a latency feature, not a throughput one.

**The HyperRAM has about 12% left, and it is mostly burst length.** Today's
334.4 MB/s is 87.1% of theoretical, and the missing 12.9% is per-transaction
overhead on 128-word bursts. Longer bursts recover about 10.7% of it and
variable latency about 5%; together they reach **~375 MB/s, 97.7% of
theoretical**, at the clock already proven. Past that the only lever is the
clock, and the clock is where the CK 200 failure lives.

**Both numbers appear to be the fastest in the open record.** A survey of the
open FPGA ecosystem found nothing faster on an ECP5 for either part — the nearest
published HyperRAM figure is Tiliqua's 200 MB/s at CK 120, our own upstream's is
240 MB/s nominal, and no published ECP5 QSPI flash rate exceeds ~24 MB/s against
our 71.7. **So there is no recipe to copy for going faster.** What the survey did
find is that two projects have already solved calibration problems we are still
carrying — Tiliqua the `READCLKSEL`/`BURSTDET` loop, and Greg Davill's boards the
word-boundary slip — and both of those were solved *below* our operating point.

**Nothing here was run on hardware** — the board was in use. Everything is
datasheet arithmetic, code read in this workspace and upstream, or a published
figure with its source named.

---

## Flash — the part is a W25Q32JV-IQ, and that closes most of the list

### Identification, settled two ways

The board files name it outright. `repos/cynthion-hardware/bank8_configuration.kicad_sch:8011`
and `cynthion.kicad_pcb:7294`:

    (property "Part Number" "W25Q32JVSSIQ"
    (property "Datasheet" "http://www.winbond.com/resource-files/w25q32jv%20revg%2003272018%20plus.pdf"

`SS` = 8-pin SOIC 208-mil, `IQ` = industrial temperature with QE factory-set.
The datasheet is **W25Q32JV Revision G, 27 March 2018**.

Two things we read off the silicon corroborate it independently, which matters
because a schematic records what was ordered rather than what was fitted:

| we measured | what it proves |
|---|---|
| JEDEC `EF 40 16` | rules out JV-IM/JM (`70 16`) and both JW variants (`60 16`, `80 16`) |
| SR2 `0x02`, QE already set | §7.1: QE is *"factory fixed default for part numbers with ordering options `IQ` & `JQ`"*. QE=0 is the IM/JM default |
| SR3 exists and reads `0x60` | rules out **W25Q32BV**, which reads `40 16` too but has no Status Register-3 at all |

Only **W25Q32FV** and **W25Q32JV-IQ/JQ** survive `EF 40 16` + SR3 + QE=1, and
the schematic picks between them.

### Correction to what this workspace recorded

Two statements in the existing notes are wrong and are corrected here:

| was recorded | correct |
|---|---|
| SR3 `0x60`, **"ADS clear"** ([`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md)) | **there is no ADS bit on this part.** ADS/ADP are 4-byte-addressing bits and exist only on ≥256 Mbit parts. SR3 bit S23 is Reserved. `0x60` means DRV=25%, WPS=0, and nothing else |
| *"QPI mode can address in as few as 8 clocks"* ([`luna_ecp5_fpga/flash-detailed.md`](luna_ecp5_fpga/flash-detailed.md)) | **this part has no QPI mode.** The claim is true of the FV and of the JV-IM, not of what is fitted |

### QPI (`0x38`) — absent

The word "QPI" appears **three times** in the 80-page Rev G datasheet, and all
three are the same cross-reference: *"For DTR, QPI supporting, please refer to
W25Q32JV DTR datasheet."*

The absence is evidenced rather than assumed — the part's complete instruction
set is two tables, and neither contains `38h` or `FFh`:

- **Table 1, Standard SPI**: `06 50 04 AB 90 9F 4B 03 0B 02 20 52 D8 C7/60 05 01
  35 31 15 11 5A 44 42 48 7E 98 3D 36 39 75 7A B9 66 99`
- **Table 2, Dual/Quad SPI**: `3B BB 92 32 6B 94 EB 77`

That is all of it. No `38h`, no `C0h`, no `0Dh`/`BDh`/`EDh`, no 4-byte-address
opcodes.

**Even if it were present it would be worth roughly nothing here**, and this is
the more useful finding, because it applies to any future part choice. QPI's
advantage is that the *opcode* goes out on four lanes instead of one. But
`0xEB` continuous read — which this board already does — **deletes the opcode
entirely**. The two optimisations target the same eight clocks, and continuous
read gets there first:

| per 64-byte cache line, clocks at SCK | opcode | address | mode | dummy | data | total |
|---|---|---|---|---|---|---|
| `0xEB` quad I/O, SPI | 8 | 6 | 2 | 4 | 128 | **148** |
| `0xEB` continuous, SPI — *what we run* | — | 6 | 2 | 4 | 128 | **140** |
| QPI `0xEB` continuous, 2 dummy (hypothetical) | — | 6 | 2 | 2 | 128 | **138** |
| QPI `0x0B`, 8 dummy (what 133 MHz would need) | 2 | 6 | — | 8 | 128 | **144** |

**+1.4% at best, −2.8% at the clock we actually run.** The dummy-clock count in
QPI is not free: the DTR-part datasheet's `0xC0` table ties 2 dummy clocks to a
50 MHz ceiling and only 8 dummy clocks reach 133 MHz. At 144 MHz SCK the QPI
path is *slower* than what we have.

### DTR (`0xBD` / `0xED`) — absent, and it would be a downgrade anyway

Winbond **split the die** at the J generation. The FV had DTR and QPI in one
part; the JV bifurcated into **-IQ/JQ (`40 16`, plain SPI/Dual/Quad)** and
**-IM/JM (`70 16`, adds QPI and DTR)**, in two separate datasheets. Ours is the
stripped variant. This is the specific thing the brief asked to verify against
the variant rather than the family, and the family answer would have been wrong.

The arithmetic is worth doing anyway, because it decides whether fitting the
`-IM` part is ever worth a rework:

**DTR on the -IM is rated 66 MHz SCK at 3.0–3.6 V** (52 MHz below 3.0 V), against
133 MHz for its SDR instructions. Four lanes at double rate at 66 MHz is
4 × 2 × 66e6 / 8 = **66 MB/s**. We measure **71.7 MB/s** at 144 MHz SDR. Per
cache line, `0xED` costs 8 opcode + 3 address + 8 dummy + 64 data = 83 clocks at
66 MHz = **1258 ns**, against our 1028 ns.

**Double-rate at half the clock is not a win.** Fitting the DTR part would make
this board slower.

### `0xC0` Set Read Parameters — absent, and QPI-only regardless

Not in either instruction table. The DTR datasheet is explicit that it would not
help even if it were: *"In Standard SPI mode, the 'Set Read Parameters (C0h)'
instruction is not accepted. The dummy clocks for various Fast Read instructions
in Standard/Dual/Quad SPI mode are **fixed**."*

`0xEB`'s 4 dummy clocks in SPI mode are not adjustable on any W25Q32.

### `0x77` Set Burst with Wrap — present, and it is a latency feature not a throughput one

The one item on the brief's flash list that this part **does** have (§8.2.13).
`77h` + 24 dummy bits + one byte: `W6,W5` = 00/01/10/11 → 8/16/32/64-byte wrap,
`W4` = 0 enables it. Default `W4` = 1, i.e. **disabled**. It persists across
transactions and across `/CS`, and it applies **only to `0xEB`** — not `0x6B`,
not `0xBB`.

**It does not eliminate the address phase.** §8.2.12: output *"starts at the
initial address specified in the instruction, once it reaches the ending boundary
of the 8/16/32/64-byte section, the output will wrap around to the beginning
boundary automatically until /CS is pulled high"*. Every `0xEB` still carries its
full 24-bit address, and holding `/CS` low simply re-reads the same section
forever. The brief's hoped-for behaviour — successive wrapped bursts on one `/CS`
low without re-issuing an address — **is not what this instruction does**.

What it *is* worth is **critical-word-first**. Winbond says so directly: *"allows
applications that use cache to quickly fetch a critical address and then fill the
cache afterwards within a fixed length of data without issuing multiple read
commands."*

The arithmetic, for a 64-byte line of sixteen 4-byte CPU words at 144 MHz. A
4-byte word costs 8 clocks on four lanes:

| | clocks to the missed word | ns |
|---|---|---|
| no wrap, word at index *k* | 12 + 8(*k*+1) | — |
| no wrap, average (*k* = 7.5) | 72 | **500** |
| **wrap-64, always** | **20** | **139** |

**A 72% cut in the stall before the CPU can resume** — but the line still takes
the full 1028 ns to fill, so nothing about throughput changes, and it only pays
at all if the cache restarts on the critical word rather than waiting for the
line. VexiiRiscv's I-cache would have to be configured for it.

**Cost:** one `0x77` transaction at init, plus a wrapped-read path in the
controller, plus the cache configuration. Non-trivial, and it improves latency
under a miss rather than throughput.

### Drive strength — real, but there is no failure for it to fix

SR3 `DRV1,DRV0`: `00` = 100%, `01` = 75%, `10` = 50%, **`11` = 25%, the factory
default and what we read**. Writable volatile (`50h` then `11h`) and
non-volatile (`06h` then `11h`).

The datasheet gives **no ohms, no mA, and no tie to frequency or load** — the
percentages are unqualified, and §9.5 fixes the AC conditions at `CL` ≤ 30 pF
without reference to `DRV`. So there is no arithmetic to do; only an experiment.

Two things make it lower-value than it looks:

- **Nothing fails at 25% up to 144 MHz.** The ceiling reached was the test
  design's own fmax at 149 MHz, not the flash. A stronger driver has no failure
  to repair, and it cannot raise a limit that lives in the FPGA.
- **A volatile write has already been attempted and did not take** — SR3 read
  back unchanged at `0x60` through Apollo's background SPI.

Worth doing only as part of a design that has first got past 149 MHz. Note the
one paper argument in its favour: at 144 MHz the clock period is 6.94 ns and
`tCLQV` is specified **max 6 ns**, so the read is already relying on the real
part being far inside its guardband. If anything ever *does* go marginal at
speed, this is the first knob.

### What the flash arithmetic actually says

**Bulk reads are done.** 71.70 MB/s against a four-lane SDR theoretical of
4 × 144e6 / 8 = 72.0 MB/s is **99.58%**. There is no protocol overhead left to
remove; every remaining option moves the ~12-clock transaction preamble, which
is 0.4% of a bulk copy.

**Cache lines have 13.5% of overhead**, and that is where the options land:

| per 64-byte line at 144 MHz | clocks | ns | MB/s | vs today |
|---|---|---|---|---|
| `0xEB` continuous — **today** | 140 | 1028 (measured) | 62.3 | — |
| overhead magically zero (the bound) | 128 | 944 | 67.8 | +8.9% |
| QPI continuous, 2 dummy (not available) | 138 | 1014 | 63.1 | +1.4% |
| **128-byte cache line, same mode** | 268 | 1916 | 66.8 | **+7.2%** |

Measured points are within 5% of `clocks / 144 MHz + 55 ns`, so intermediate
values can be read off the model.

**Doubling the cache line to 128 bytes is the largest remaining flash win, and
it is a configuration parameter.** It halves the transaction count and amortises
the same 12 clocks over twice the data. It costs I-cache block RAM, which
[`luna_ecp5_fpga/bram-budget.md`](luna_ecp5_fpga/bram-budget.md) says is the
scarce resource, and it is only a win if locality holds.

**The largest item was taken while this survey was being written.**
`FLASH_MODE = "quad"` landed in `03482f4` — 2.70× on a 16 KiB random walk, for
**−261 LUTs and no block RAM**. What remains of the SoC gap is
[`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md)'s two rows,
`SYNC_MHZ` and the PHY, and they have a constraint that survey does not state:

**`SYNC_MHZ` is gated by the CPU, not the flash.** The SoC's median Fmax is
**75.0 MHz** across three place-and-route runs. Raising `sync` to 120 MHz for the
flash's sake requires the RISC-V core to close at 120 MHz first — see
[`riscv-clock-ceiling.md`](riscv-clock-ceiling.md). The flash's remaining 2× is
real but it is not the flash's to give.

One SoC-side step this survey adds: **`0xEB` continuous read is not adopted
either**, and it is worth 1083 → 1028 ns per line, **5.1%**, on top of quad. It
carries the sticky-state hazard already documented — the part remembers the mode
across an FPGA reconfiguration, and answers the next opcode as if it were an
address. Small win, real footgun; take it last if at all.

### One measurement to take that would settle the residue

**Read the full SFDP Basic Flash Parameter Table.** `scripts/flash_capacity_probe.py`
already issues `0x5A` but decodes only the density word. JESD216's BFPT declares,
from the die itself: which fast-read modes are supported, their dummy-cycle
counts, whether 4-4-4 (QPI) is available, and — DWORD 1 bit 19 — whether DTR
clocking is supported.

That matters because there is **one live contradiction** between the datasheet
and this board. Rev G documents no continuous-read mode at all; it says in four
places that `M7-0` *"should be set to Fxh"*, which is the **non**-continuous
encoding, and it has none of the FV's Figures 22b/24b describing opcode-skipping.
Yet we have measured `0xEB` continuous working byte-exact with the opcode
genuinely omitted, at 71.70 MB/s, and found the part *remembers the mode across
an FPGA reconfiguration*. **The die implements a feature its own datasheet
dropped.** SFDP would say whether anything else undocumented is there — and it is
a read-only probe on a board that is otherwise fully characterised.

---

## HyperRAM — about 12% left, and where it is

### The model these numbers come from

At CK 192 MHz the theoretical rate is 2 bytes/CK × 192e6 = **384 MB/s**.
Measured read is 334.4 = **87.08%**, so a 128-word transaction costs
128 / 0.8708 = **147 CK**, i.e. **19 CK of overhead**.

That matches the controller's own state machine, counted in `sync` cycles at
2 CK each: `LATCH_RWDS` 1 + `SHIFT_COMMAND0` 1 + `SHIFT_COMMAND1` 1 +
`HANDLE_LATENCY` 6 (loaded with `HIGH_LATENCY_CLOCKS = 5`, decrementing to 0) +
`RECOVERY` 1 = 10 sync = **20 CK**. The model is sound, so the table below can be
trusted arithmetically.

**A useful cross-check:** the *write* path measures 351.1 MB/s = 91.4%, which is
128/140, i.e. **12 CK of overhead**. Twelve clocks of overhead is therefore
demonstrated achievable on this board, not merely calculated. (The mechanism is
not established — writes do not wait on `DATAVALID` — so treat it as a bound, not
an explanation.)

### Option 1 — variable latency (`CR0[3] = 0`). +5%, and reading early is the failure mode

`CR0` reads `0x8f2f`, so `CR0[3] = 1` — **fixed latency**, the power-on default.
The part drives RWDS high during every CA period and always takes **2 × initial
latency**.

**Correcting a decode while we are here.** This workspace recorded `0x8f2f` as
*"latency 2"*. `CR0[7:4]` = `0010b`, and Table 10 reads that as
**"7 Clock Latency @ 200MHz Max Frequency (default)"** — 7 clocks, not 2. So
fixed latency costs **14 CK** on every transaction, and variable latency costs 7.

| `CR0[7:4]` | clocks | max CK |
|---|---|---|
| `1110b` | 3 | 83 MHz |
| `1111b` | 4 | 100 MHz |
| `0000b` | 5 | 133 MHz |
| `0001b` | 6 | 166 MHz |
| **`0010b`** | **7** | **200 MHz** — *what is set* |

At CK 192 MHz the 7-clock setting is the only legal one, so **shortening the
initial latency count is not available to us** (see Option 6). Variable latency
is a different mechanism: same 7-clock count, but taken **once** instead of
twice when no refresh is pending.

**Worth:** overhead 19 → 12 CK. 128/140 = 91.43% → **351.1 MB/s, +5.0%** —
exactly the write path's number, which is the corroboration above.

Refresh collisions give some of it back. Distributed refresh is 64 ms / 8192 rows
= **7.81 µs per row**; a 140 CK transaction at 192 MHz is 729 ns, so about
729/7810 = **9.3%** of transactions start with a refresh outstanding and pay the
long count anyway. Expected overhead 12 + 0.093 × 7 = 12.65 CK →
**349.5 MB/s, +4.5%**.

**What has to change together — and this is the part that bites.** Three things,
and doing any one alone reads early or reads nothing:

1. **`CR0[3]` must be cleared.** Nothing else works until the device stops
   asserting RWDS unconditionally.
2. **`HIGH_LATENCY_CLOCKS` must come down.** `READ_DATA` is the only state that
   raises `phy.read`, which is what opens `DQSBUFM`'s capture window. Entering it
   *late* loses the first words outright; entering it *early* is harmless because
   `DATAVALID` gates the latch. So the count must be set to the **short**
   value and left there. The counter is in `sync` cycles = 2 CK, so it cannot
   express 7 CK at all — 3 sync = 6 CK is the correct floor.
3. **RWDS must actually be sampled during the CA period — it is not today.**
   `LATCH_RWDS` reads `phy.rwds.i` in the state *before* `SHIFT_COMMAND0`, i.e.
   before the CA has been sent. Whatever `extra_latency` holds is not the
   device's answer about this transaction. Upstream masks it with
   `with m.If(extra_latency | 1)` and a `FIXME`, which is correct *only* while
   fixed latency is set. Clear `CR0[3]` without fixing this and the controller
   has no idea which latency applies.

   [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) already records that
   LUNA's `extra_latency | 1` is *"the correct behaviour here"* rather than the
   defect #90 called it. That stands — but it is correct only because of a
   register setting we would be changing.

**Cost:** a CR0 write in the bring-up sequence, two constants, and moving one
sample into the CA window. Then a full re-verification, because the failure mode
is silently-wrong data at full speed rather than an error — the same shape as
every other trap on this interface.

### Option 2 — longer bursts inside tCSM. +10.7%, and the cheapest thing on this page

`CR1` reads `0xffc1`, so `CR1[1:0]` = `01b` = **4 µs tCSM**, and the burst is
128 words.

**`CR1[1:0]` is read-only and `01b` is the only defined value.** Table 13 lists
`00b`, `10b` and `11b` as Reserved and the note says *"CR1[1:0] is read only."*
Table 14 has exactly one row: TCASE < 85 °C, 64 ms refresh interval, 8192 rows,
recommended tCSM 4 µs. **There is no longer tCSM to buy at any price** — that
answers the brief's question directly. The datasheet's remark that *"the array
refresh interval is longer at lower temperatures such that tCSM could be
increased"* is not actionable on a part whose register will not take the value.

At CK 192 MHz, 4 µs is **768 CK**. Subtracting 19 CK of overhead leaves room for
**749 words**. Our 128 is using **17% of the budget**:

| burst | +19 CK overhead | CK | µs at CK 192 | % of tCSM | efficiency | MB/s | vs today |
|---|---|---|---|---|---|---|---|
| **128 — today** | 147 | 147 | 0.77 | 19% | 87.1% | 334.4 | — |
| 256 | 275 | 275 | 1.43 | 36% | 93.1% | 357.5 | **+6.9%** |
| **512** | 531 | 531 | **2.77** | **69%** | 96.4% | **370.3** | **+10.7%** |
| 704 | 723 | 723 | 3.77 | 94% | 97.4% | 374.0 | +11.8% |

**512 words is the right answer**: it takes 10.7 of the available 11.8 points and
leaves 31% of tCSM as margin, where 704 leaves 6%.

**The catch, and it is the whole cost.** tCSM is **4 µs of wall-clock time at
every frequency** — the write timing table gives 4.0 µs at 200, 166, 133 and
100 MHz alike. So the legal burst in *words* scales with the clock:

| device CK | tCSM in CK | max legal words |
|---|---|---|
| 60 MHz (non-DQS, slowest rung) | 240 | ~221 |
| 120 MHz | 480 | ~461 |
| 192 MHz | 768 | ~749 |

**128 is a constant that is legal everywhere, which is why it is 128.** Raising
it to 512 means making `BURST_WORDS` a function of the built clock, or splitting
long transfers in the controller — which is what the datasheet says a host is
supposed to do: *"host memory controller logic splitting long transactions when
reaching the tCSM limit"*. That splitter does not exist in
`HyperRAMDQSInterface` and it is the piece of work this option really is.

**Violating tCSM does not fail visibly** — it fails by forgetting later. That is
how `hyperram_speed.py` (since retired) produced 220.2 MB/s from a 2048-word
/ 17 µs burst, a number
this workspace has already retired.

**Combined with Option 1** (12 CK overhead, 512 words): 512/524 = 97.71% →
**375.2 MB/s, +12.2%**, and 97.7% of everything CK 192 can deliver. That is the
realistic end of the road at this clock.

### Option 3 — hybrid burst and wrap for cache lines. Latency, not throughput

`CR0[2]` = 1 (legacy wrap) and `CR0[1:0]` = `11b` (32-byte wrap) today. Neither
does anything, because our transactions are linear — wrapped-vs-linear is
selected by a CA bit, not by CR0.

Setting **`CR0[2] = 0` (hybrid) and `CR0[1:0] = 01b` (64-byte)** would matter the
moment #90 puts a CPU on this bus. Hybrid burst is *"one wrapped burst followed
by linear burst"*, and §9.4.2 describes exactly the cache case: *"The first cache
line is filled starting at the critical word. Then the next sequential line in
memory can be read in to the cache while the first line is being processed."*

Note the feature summary marks hybrid burst **"64 Mbit only"** — it is available
on our density and not on the 128 Mbit sibling.

**Worth:** a 64-byte line is 32 device words, and a 4-byte CPU word is 2 CK. The
transaction is 19 + 32 = 51 CK = 266 ns.

| | CK to the missed word | ns at CK 192 |
|---|---|---|
| linear, word at index *k* | 19 + 2(*k*+1) | — |
| linear, average (*k* = 7.5) | 36 | **188** |
| **wrap-64, always** | **21** | **109** |

**A 42% cut in the stall**, plus the hybrid tail prefetching the next line for
free. Zero throughput change.

**Cost:** two CR0 fields, a wrapped-CA path in the controller, and a cache that
restarts on the critical word. Nothing to do until #90 lands; worth deciding
*before* it does, because it shapes the peripheral.

### Option 4 — drive strength (`CR0[14:12]`). Cheap to try, wrong shape for the failure

`CR0[14:12]` = `000b` = **34 Ω**, the reset default. The full ladder, from
Table 10 — note it is not monotonic in the code:

| code | impedance | | code | impedance |
|---|---|---|---|---|
| `001b` | 115 Ω | | `101b` | 27 Ω |
| `010b` | 67 Ω | | `110b` | 22 Ω |
| `011b` | 46 Ω | | `111b` | **19 Ω — strongest** |
| `000b` / `100b` | 34 Ω (default) | | | |

`000b` and `100b` are the same value; §9.4.5 calls the default *"the mid-point of
the available output impedance options"*, which is fair — 34 Ω sits fourth of
seven.

**It drives the right signals.** `CR0[14:12]` sets the impedance of **`DQ[7:0]`
and RWDS as the memory drives them**, which is precisely the read path that
fails at CK 200.

**It looks like the wrong shape for our failure** — signal-integrity faults give
scattered bit errors, and ours gives *transposed 16-bit halves borrowed from a
neighbouring word*, structurally related to what was written. And the eye looks
wide: at CK 200 the part's `tDV` min is **1.45 ns** against the ECP5's `tDWDQ`
requirement of **0.519 ns** at speed grade 8.

**But `tDV` is the wrong spec to check, and the right one is much tighter.** With
a DQS-strobed read what matters is data valid *relative to the strobe*, which is
`tDSS`/`tDSH` — and that is **±0.8 ns on our 166 MHz bin against ±0.4 ns on the
200 MHz bin**. See the `tDSS` discussion under [Published work](#the-tdss-reading-that-argues-the-other-way):
skew approaching half a UI moves the sample into the adjacent bit, and in a 4:1
gearbox an adjacent-bit sample *is* a half-word displacement. **The failure
signature does not distinguish the two hypotheses.**

**So try it** — three register writes (`101b`, `110b`, `111b`) on a rung that
already fails is among the cheapest experiments available, and on this reading it
has a real chance rather than a token one.

### Option 5 — differential clock (`CR1[6] = 0`). Untried, unremarked, and the board is already wired for it

**This is not on the brief's list and it should be.**

`CR1` reads `0xffc1`, so **`CR1[6] = 1` = "Single Ended - CK (default)"**. The
datasheet's pin description is blunt about what that means: *"Single Ended Clock:
**CK# is not used**, only a single ended CK is used."*

Meanwhile the board drives a genuine complementary pair —
`ecp5-test/cynthion_platform/cynthion_r1_4.py:206`:

    Subsignal("clk", DiffPairs("C3", "D3", dir="o"), Attrs(IO_TYPE="LVCMOS33D")),

**The FPGA has been driving CK# into a part that is configured to ignore it, on
every build this workspace has ever run.** In single-ended mode the part slices
CK against its own input threshold; in differential mode it clocks on *"the
crossing of the CK and CK# signals"*, which is what removes threshold and
common-mode error from the sampling instant. At CK 200 MHz the period is 5 ns and
a half-period 2.5 ns, so threshold error is a direct duty-cycle error on a DDR
bus — and duty-cycle error is exactly the class of fault that presents as a
half-word landing in the wrong slot.

The part specifies the differential mode properly (Table 25): `VID(AC)` min
0.6 × VCCQ, `VIX` between 0.4 and 0.6 × VCCQ, and *"CK and CK# input slew rate
must be ≥1 V/nS (2 V/nS if measured differentially)"*.

**The obvious objection does not apply here.** `LVCMOS33D` is pseudo-differential
— two complementary CMOS buffers rather than a true differential driver — and
badly-matched legs would put skew straight onto `VIX`, potentially making
differential mode *worse* than single-ended. But
[`luna_ecp5_fpga/hyperram-detailed.md`](luna_ecp5_fpga/hyperram-detailed.md)
already established how the pair is actually built:

> *"Amaranth drives an LVCMOS33D pair by driving the **true** pin only and
> letting the bank generate the complement, so the clock path is
> `ODDRX2F → DELAYG → OBZ → C3` and nothing else."*

**The complement is generated by the I/O bank at the buffer, downstream of the
`DELAYG` and of everything else.** Both legs share one source and one delay
element, so the P-to-N skew is the buffer pair's own mismatch and not an
accumulated routing difference. That is the good case for this option.

**Two things still to check:** `VCCQ` is 3.3 V, so `VID` is the full rail-to-rail
swing against a 3.6 V maximum — legal, with 0.3 V to spare. And `LVCMOS33D`
P-to-N skew is not specified in the ECP5 datasheet, so the argument above is
structural rather than numeric.

**Cost: one CR1 write in the bring-up sequence, no gateware change.** It is the
best return-per-effort experiment on the 200 MHz failure, ahead of drive
strength, and nothing in this workspace has tried it.

### Option 6 — a lower initial latency count. Do not

`CR0[7:4]` could be `0001b` (6 clocks) at CK ≤ 166 or `0000b` (5 clocks) at
CK ≤ 133. The trade is always bad:

At CK 166 the theoretical ceiling is 332 MB/s. Even at 97.7% efficiency — 512-word
bursts *and* variable latency *and* a 6-clock count — that is **324 MB/s**,
below today's 334.4. **Never trade clock for latency on this part.** Recorded so
nobody re-derives it.

### Option 7 — partial array refresh (`CR1[4:2]`). Speculative, and it costs memory

`CR1[4:2]` = `000b` = full array. Restricting to half the array halves the rows
needing refresh, which *could* halve the ~9.3% refresh-collision rate that Option
1 pays — worth about 0.4% of throughput, for 4 MiB of memory.

**And it may do nothing.** The datasheet describes partial array refresh only as
a standby-current feature and does not say the distributed refresh scheduler
slows down; the device may simply refresh the remaining rows twice as often.
tCSM certainly does not change — `CR1[1:0]` is read-only.

Listed for completeness. Bad trade at any reading.

---

## The FPGA side, which is one of the two places the CK 200 failure could live

### The clock structure is not the canonical ECP5 one, and that is the leading hypothesis

`hyperram_ceiling_top.py` takes `sync` from **`CLKOP`** and `fast` from
**`CLKOS2`** — two independent PLL outputs at a 2:1 ratio, each with its own
clock buffer. `CLKDIVF` usage in every built design is **0 of 4**.

The Lattice-canonical structure for a `DQSBUFM` interface is
**PLL → ECLK → `CLKDIVF` (÷2) → SCLK**, with `ECLKSYNCB` in the path. That
matters for two reasons:

- With `CLKDIVF`, SCLK is *derived from* ECLK, so their phase relationship is
  structural. With two PLL outputs it is only nominal — the PLL guarantees
  frequency, and each output has its own buffer and its own insertion delay.
- **`CLKDIVF` has an `ALIGNWD` port whose entire purpose is to slip the divided
  clock by one fast-clock cycle.** That is the standard mechanism for correcting
  exactly the fault we see: a word-boundary slip in 4:1 gearing, where the data
  is right but arrives in the wrong half.

Our CK 200 failure — *"the two 16-bit halves are transposed and one of them
belongs to a neighbouring word"* — is a textbook description of what `ALIGNWD`
exists to fix, and this design has no `ALIGNWD` because it has no `CLKDIVF`.

**There is no `ECLKSYNCB` either**, in ours or upstream's. The PLL's `CLKOS2`
goes straight to `ClockSignal("fast")` and nextpnr promotes it to an edge clock
by itself. `ECLKSYNCB` exists to gate ECLK so it *starts* in a known
relationship to SCLK; without it, the phase at which the two domains come out of
reset is whatever the PLL and the routing happen to give on that
place-and-route. Both `CPHASE` values are set to `div - 1` — the
no-shift convention — which makes them nominally aligned and specifies nothing
about the alignment that actually results.

**Upstream has the same structure**, so this is not a local mistake:
`repos/luna/luna/gateware/architecture/car.py` takes 240/120/60 MHz from
`CLKOP`/`CLKOS`/`CLKOS2` with hand-set `CPHASE` values of 1/3/7 and no
`CLKDIVF`. Upstream at least tuned those phases; ours are formulaic. **That is
the cheapest experiment in this section** — sweeping `CLKOS2_CPHASE` and
`CLKOS2_FPHASE` at CK 200 costs bitstreams and no design change, and if the
failure moves with phase it is confirmed as an alignment fault before anyone
commits to the `CLKDIVF` restructure.

**Cost:** restructuring the clock domain generator, which is not small, and it
changes every HyperRAM bitstream. **Value:** it is the only candidate on this
page that addresses the failure's actual signature rather than its
circumstances.

### `READCLKSEL` — and it does not do what this workspace assumed

`psram.py:779-782` hardcodes `READCLKSEL0=0, READCLKSEL1=1, READCLKSEL2=0` with
upstream's *"TODO: may need to tune at runtime by trying different values &
checking for BURSTDET high"*. It never was swept — but before anyone sweeps it,
the semantics matter, and they are not what "read clock select" suggests.

**Lattice FPGA-TN-02035-1.3, "ECP5 and ECP5-5G High-Speed I/O Interface", §6.2.4**
([mirror](https://0x04.net/~mwk/doc/lattice/ecp5/FPGA-TN-02035-1-3-ECP5-ECP5-5G-HighSpeed-IO-Interface.pdf)):

> Once READ1/0 positions the READ pulse, READCLKSEL2/1/0 can be used to **shift
> the READ pulse by 1/4T per step**. With the total eight possible combinations
> from 000 to 111, READCLKSEL2/1/0 covers the READ pulse shift up to a **whole 2T
> timing window**. If BURSTDET is asserted with a certain READCLKSEL2/1/0 value,
> it indicates that the READ pulse has been located to the optimal position. **If
> no BURSTDET is asserted during this step, the READ pulse needs to be moved to
> the next timing window.**

So `READCLKSEL` positions the **READ gating pulse**, not a phase-shifted capture
clock. The 90° shift comes from `DDRDLL`/`DDRDEL` into `DQSR90`; the fine delay
is a third mechanism (`RDMOVE`). Three separate knobs, and this workspace had
conflated the first two.

**Three consequences for the sweep:**

1. **Eight values span only 2T.** If the round-trip DQS delay lands outside that
   window, no `READCLKSEL` value works, and the fix is to move `READ1`/`READ0` to
   the next cycle — an *outer* loop the existing harness has no notion of. The
   same section warns that **moving `READ1` alone gives "two short pulses in wrong
   timing"**; both must move together.
2. **`PAUSE` is mandatory around the change**, not optional: *"the PAUSE input to
   DQSBUFM must be asserted before 4T of the change and remain asserted for
   another 4T after the change"*. LiteX hit this the hard way — litedram#103, two
   of six OrangeCrabs failing memtest after a successful read-levelling, fixed by
   strobing `PAUSE` afterwards.
3. **The ECP5 datasheet's port table is wrong.** It lists `READCLKSEL[1:0]` — two
   bits. The technical note, the primitive and prjtrellis all have three.

**Correction: the port table discrepancy is a datasheet error, not a silicon
limit.** An earlier draft of this page treated it as "8 values on paper, possibly
4 in silicon". TN-02035 settles it at eight.

### `BURSTDET` — four specific reasons ours may be staying low

TN-02035 §6.2.4 imposes conditions this design does not obviously meet:

> **A minimum burst length of eight on the memory bus must be used in the training
> process.** … The BURSTDET signal is asserted **after the last DQS transition is
> completed** … the memory controller should **wait until the DATAVALID signal
> from DQSBUFM is asserted and then sample the BURSTDET signal at the next
> cycle**. … It is recommended that **at least 128 read operations** be performed
> repetitively at a READ pulse position.

Table 6.3, for X2 gearing: initial `READ` assertion **"at least 5.5T before
preamble"**, `READ` width **"Total Burst Length / 4"** SCLK cycles.

Against that, in likely order:

1. **`READ0`/`READ1` windowing.** LUNA drives them straight from `phy.read`,
   raised in `READ_DATA`. If that is not ≥5.5T before the strobe and held for
   burst/4 SCLK cycles, `BURSTDET` cannot assert **at any `READCLKSEL` value** —
   which would make a sweep of the inner loop alone come back empty and prove
   nothing.
2. **Sampling method.** `burstdet_seen` is latched from a level. gram's commit
   `0cd69420` is titled *"Detect burstdet on rising edge, not by logic level"*,
   and exists because level-sampling misleads.
3. **`READCLKSEL` fixed at one point in a 2T span**, per above.
4. **RWDS is not DQS.** `DQSBUFM`'s burst detector is built for DDR3's
   preamble/postamble, which HyperBus does not have. Tiliqua's simulation path
   force-ties `burstdet.eq(1)` for want of a model — **but Tiliqua gets real
   assertions on hardware at CK 120**, so it is achievable on HyperRAM and this
   is the least likely of the four.

### `RDLOADN` / `RDMOVE` — tied off, and that is correct

`psram.py` sets `RDLOADN=0, RDMOVE=0, RDDIRECTION=1`. An earlier draft of this
page listed sweeping them as a cheap experiment. **That was wrong**, and the
correction is worth recording because it is the opposite of what the port names
suggest.

TN-02035 §8.10.1: *"If margin control is not used, then LOADN should be low to
**continuously get code from DDRDLL**."* `RDLOADN=0` is the setting that keeps the
delay line tracking process, voltage and temperature automatically. Raising it
hands **you** responsibility for tracking PVT via `DCNTL[7:0]`. LiteDRAM ties them
off identically.

So this is a real knob, but it is not free and it is not first: it converts an
automatic delay into a manual one. Do it only after `BURSTDET` works, when there
is something to centre against.

### The ECP5 is not the limit on paper — but nothing on paper covers this bus

Speed grade **8**, the fastest ECP5 bin. From the family datasheet:

| spec | -8 | our worst case |
|---|---|---|
| `fMAX_GDDRX2` (ECLK), generic DDR | 400 MHz | 200 MHz at CK 200 |
| `fMAX_DDR3` (ECLK) | 400 MHz | 200 MHz at CK 200 |
| `tDWDQ`, DQ input valid window required | 0.519 ns | part gives 1.45 ns of `tDV` |

**Twice the headroom on ECLK.** The ECP5's published DDR limits do not explain a
failure at CK 200.

**The eye comparison is not as comfortable as it looks**, though, and the first
draft of this page overstated it. `tDV` is data valid relative to *CK*;
`tDWDQ` is the window the FPGA needs relative to *DQS*. Closing the gap between
them costs `tDSS`/`tDSH` — **±0.8 ns on our bin** — so the honest figure is
1.45 − 2 × 0.8 = **−0.15 ns** in the worst case, i.e. the strobe-relative eye is
not guaranteed open at all at CK 200 on a 166 MHz part. That it works at CK 192
says the real part is well inside its guardband; it does not say there is
margin.

**Except that none of those numbers were characterised on our I/O standard.** The
datasheet's own notes: *"Generic DDR timing numbers based on LVDS I/O"*, *"DDR3
timing numbers based on SSTL15"*, *"General I/O timing numbers based on
LVCMOS 2.5, 12 mA, Fast Slew Rate, 0 pF load"*. **This bus is LVCMOS33** —
a higher-swing, higher-capacitance buffer that appears in none of them.

So the correct statement is not "the FPGA has margin" but **"no vendor number
covers 400 Mbps/pin DDR on LVCMOS33, in either direction"**. It is neither
endorsed nor excluded. Our 384 Mbps/pin at CK 192 is unpublished territory and
the measurement is the only authority.

### Two hardware options the board already anticipates

Both are in the schematic, which means someone considered them at design time.

**Fit the 200 MHz speed bin.** `repos/cynthion-hardware/ram.kicad_sch:2347`
lists an approved substitution:

    (property "Substitution" "W956A8MBYA5I, Infineon S27KL0642DPBHI020"

`W956A8MBYA**5I**` is the **200 MHz** grade of the identical die — same
datasheet, same registers, same package. Ours is the **6I**, 166 MHz. Section 2
of the datasheet lists both.

This reframes the ceiling honestly: **we are running a 166 MHz bin at 192 MHz,
15.7% past its grade, and the part that is specified to do 200 MHz is a different
order code on the same reel.** If the CK 200 failure survives every gateware fix
above, this is what the answer looks like — and if a 5I part *also* fails at 200
with transposed halves, that proves the fault is the gearing, not the memory. As
a diagnostic it is worth more than as an upgrade.

**Move the bus to 1.8 V.** `power_supplies.kicad_sch:7364`:

    VCCRAM is normally tied to +3V3 but regulator U15 may be populated
    instead of R54 to support 1.8 V HyperRAM.

`VCCRAM` appears throughout `bank6_7.kicad_sch`, so it feeds the FPGA bank as
well as the memory — the swap is coherent at board level rather than a partial
measure. The 1.8 V die (`W956D8MBYA5I`) has materially better AC specs at speed:

| at 200 MHz | 1.8 V | 3.0 V |
|---|---|---|
| `tCKD` max, CK to DQ valid | **5.0 ns** | 6.5 ns |
| `tCKDS` max, CK to RWDS valid | **5.0 ns** | 6.5 ns |
| `tDSS` / `tDSH`, RWDS-to-DQ skew, at 166 MHz | **±0.45 ns** | ±0.8 ns |

**Nearly half the RWDS-to-DQ skew** is the number that matters for a DQS-strobed
read. Against that: a different part, a regulator to populate, and a check that
nothing else in that bank needs 3.3 V — which has **not** been done and should be
before anyone takes this seriously.

---

## Published work

### Both of these numbers appear to be the fastest in the open record

A survey of GitHub, GitLab, Codeberg and the FPGA blogs found **nothing faster
than either of our figures on an ECP5**, and for the flash nothing within 3×.
That is worth stating carefully — it means there is no published recipe to copy
for going faster, and it also means the traps below were found by people working
*below* our operating point, so they are necessary rather than sufficient.

### HyperRAM on ECP5 — the scoreboard

| project | part | device CK | peak | published measurement | read capture |
|---|---|---|---|---|---|
| **this board** | Winbond W956A8, LFE5U-12F | **192 MHz** | 384 MB/s | **334.4 MB/s** | `DQSBUFM` 4:1 |
| DiVA, historic | LFE5U-25F-8, 1.8 V | 165 MHz | 330 MB/s | — | `IDDRX2F` + `DELAYF`, **no `DQSBUFM`** |
| orbtrace | LFE5U-25F | 150 MHz | 300 MB/s | — | `IDDRX2F` + `DELAYF` |
| DiVA, current | LFE5U-25F-8 | 150 MHz | 300 MB/s | ~194 MB/s sustained (inferred from its video load) | as above |
| boson-sd | LFE5U-25F-8 | 140 MHz | 280 MB/s | prints its own MB/s at boot | as above |
| **Tiliqua** | Cypress S27KL, LFE5U-45F | 120 MHz | 240 MB/s | *"tested up to 200 MB/sec"* | `DQSBUFM` 4:1 **+ READCLKSEL training** |
| **LUNA, pre-DQS** | — | 120 MHz | 240 MB/s | *"120 MHz DDR for a nominal rate of 1920 Mbit/s"* | `IDDRX1F` 2:1 |
| LiteX `hyperbus.py` | Certus-NX, **not ECP5** | 25 MHz | 50 MB/s | **46.7 MiB/s write, 22.7 read** | fabric SDR |

Our own upstream's published figure is the LUNA row — Great Scott Gadgets,
*"HyperRAM controller for USB analysis"*, 9 Feb 2022. **The DQS work has taken
that from 240 MB/s nominal to 334.4 MB/s measured.**

Two clean negatives, so nobody re-searches: **ULX3S / Radiona have no HyperRAM at
all** (SDRAM and DDR3 boards), and **1BitSquared published no HyperRAM gateware
or numbers**. The related FUSBee5 board says *"Hyperram is now fully connected…
but still needs testing"* and never followed up.

**No ECP5 board in `litex-boards` calls `add_hyperram`.** Upstream LiteX's
HyperRAM core has never been tuned on this part; its ECP5 lineage is the separate
`litex-hub/litehyperbus`, Greg Davill's `HyperRAMX2`.

That absence is the load-bearing fact in
[`linux-on-cynthion.md`](linux-on-cynthion.md): `linux-on-litex-vexriscv` runs
Linux on ECP5 today, but nobody has run it out of HyperRAM. What that document
needs from this one is not the burst figure but the **per-transaction 19 CK
overhead**, because a 64-byte cache line refilled one 32-bit word at a time pays
it sixteen times — 36.6 MB/s by arithmetic, against 241 if the Wishbone window
coalesced the CTI burst. Unmeasured.

### Tiliqua has already implemented LUNA's TODO, and it is a drop-in

`apfaudio/tiliqua` vendors LUNA's `psram.py` split across three files and changed
**exactly one thing that matters**. Where LUNA hardcodes `READCLKSEL = 0b010`,
Tiliqua drives it from a runtime register with the mandatory `PAUSE`-before /
`PAUSE`-after sequence, and runs a training FSM (`periph/psram.py:198-223`):

    with m.If(timeout == 127):
        m.d.sync += counter.eq(counter + 1)
        with m.If(counter == 127):
            m.next = "IDLE"
        with m.If(~psram.phy.burstdet):
            m.d.sync += readclksel.eq(readclksel + 1)
            m.d.sync += counter.eq(0)

Dummy read, wait, check `BURSTDET`; if low, increment `READCLKSEL` (wrapping
0→7) and restart. It requires **128 consecutive bursts with `BURSTDET` high**
before releasing — matching TN-02035's recommendation exactly. Commit
`37180a74`, September 2024.

**This is the single most reusable thing found.** Same file lineage, same
primitive, proven on real ECP5 HyperRAM silicon — and it establishes that
`BURSTDET` *does* assert on a HyperBus part, which was the open question.

Two caveats before copying it wholesale:

- **Tiliqua runs at CK 120 MHz**, 40% below where we already are, so it is not
  evidence about 192 or 200.
- **First-pass-wins is the wrong policy.** It stops at the first `READCLKSEL`
  that works, which may be the edge of the eye. `jeanthom/gram`
  (`libgram/src/calibration.c`) does it properly: sweep 0..7, find the **minimum**
  and **maximum** values that assert, and program the **midpoint**. LiteX's BIOS
  does the same with `delay_mid = (delay_min + delay_max) / 2` and a comment
  worth keeping — `delay_min = delay - 1; // delay on edges can be spotty`.

Everything else in Tiliqua is unchanged from LUNA, including
`LOW/HIGH_LATENCY_CLOCKS = 3/5`, the `extra_latency | 1`, and the tied-off margin
control. **Neither LUNA nor Tiliqua writes CR0 at all**, so every option in the
register sections above is unexplored by both.

### `ALIGNWD` is the published fix for our exact failure, and there is firmware to port

Lattice FPGA-TN-02200-1.3, the sysCLOCK PLL/DLL guide, Table 13.1:

> **ALIGNWD** | I | Signal is used for word alignment. **When enabled it slips the
> output one cycle relative to the input clock.** The ALIGNWD input is intended
> for use with high-speed data interfaces such as DDR or 7:1 LVDS Video.

The ecosystem splits two ways, and the split is informative:

- **LiteDRAM ties `ALIGNWD = 0` everywhere** and does word alignment in fabric
  with a soft `BitSlip(4)` on a CSR — deliberately separate from the
  `READCLKSEL` read delay.
- **Every Davill-lineage ECP5 HyperRAM controller drives `CLKDIVF.ALIGNWD` from a
  CSR.** `orbtrace/orbtrace/crg_ecp5.py:41-59`, and identically in `boson-sd` and
  `DiVA`:

      self._slip_hr2x   = CSRStorage()
      self._slip_hr2x90 = CSRStorage()
      ...
      i_ALIGNWD = self._slip_hr2x.storage,

**The firmware is the part to steal** —
`boson-sd/firmware/main_fw_bootstrap/hyperram.c:80-113`, two nested loops:

- **outer:** `clk_del` 0..3, the two `ALIGNWD` bits giving four coarse
  word-boundary positions, applied as **one-shot pulses, not levels**;
- **inner:** sweep the PLL's dynamic phase steps, memtest at each, look for a
  passing window of ≥5–6 steps, then centre by stepping up `window/2`;
- if no window at this slip, bump `clk_del` and slip again. It prints a
  pass/fail map as it goes.

That is Lattice's own documented 7:1-LVDS word-alignment pattern (TN-02035
§9.1.4: *"Each pulse on ALIGNWD rotates the 7-bit bus by 2 bits"*) applied to
HyperRAM. **It is the only published open-source fix for a word-boundary slip on
ECP5**, and its shape — a coarse word slip crossed with a fine phase sweep — is
what our CK 200 failure calls for.

**One gotcha:** attach `ALIGNWD` to **`CLKDIVF`**, not `IDDRX2F`. Glasgow leaves
`IDDRX2F.ALIGNWD` unconnected with a comment pointing at nextpnr#1749; `CLKDIVF`
is the well-trodden path. This is a second, independent reason the
`CLKDIVF` restructure is the right move — it is not only the canonical clock
structure, it is where the fix attaches.

**Probable root cause, from the same survey:** `ECLKSYNCB.STOP` → `CLKDIVF`/IDDR
reset skew gives a *random word-boundary phase at power-up*. Lattice's own answer
is the GDDRX_SYNC soft IP for deterministic ECLK restart, which TN-02035 requires
for every GDDRX2 interface. None of the open HyperRAM designs use it — which is
precisely why they all need a firmware slip sweep instead.

### The `tDSS` reading that argues the other way

One finding cuts against the alignment hypothesis and belongs on the record.

The **`tDSS`/`tDSH` spec — RWDS transition to DQ valid — is ±0.8 ns at 166 MHz
but ±0.4 ns at 200 MHz** in the 3.0 V column. The 200 MHz bin is screened to
**twice the strobe-to-data skew tightness**, and we hold the 166 MHz bin. At
CK 192 the UI is 2.6 ns, so ±0.8 ns of guaranteed skew is ±31% of it.

For a `DQSBUFM` read that is not a small effect: skew approaching half a UI moves
the sample into the adjacent bit, and in a 4:1 gearbox an adjacent-bit sample
*is* a half-word displacement. **So the transposed-halves signature does not by
itself distinguish a gearing fault from strobe skew**, and this page's earlier
claim that drive strength is "the wrong shape for the failure" was overconfident.

Both readings survive. The experiment that separates them is the phase/slip
sweep: an alignment fault moves in discrete steps as `ALIGNWD` or `CPHASE`
changes, and a skew fault narrows and widens continuously.

### Flash — the ECP5 scoreboard, and it is not close

| project | mode | SCK | implied |
|---|---|---|---|
| **this board** | `0xEB` 1-4-4 continuous, `USRMCLK` | **144 MHz** | **71.7 MB/s** |
| Hackaday Badge 2019 (LFE5U-45F) | **`0xEB` 1-4-4, `USRMCLK`** — the same approach | 48 MHz | ~24 MB/s |
| LiteX / litespi ECP5 boards | `0x6B` 1-1-4 | sys_clk/2, 25–30 MHz | ~12–15 MB/s |
| Microwatt on ECPIX-5 | 1-1-4, opcode+address always single-lane | 25 MHz | ~12.5 MB/s |
| SaxonSoc ULX3S XIP | `0x3B` 1-1-2 | 12.5 MHz | ~3 MB/s |
| Glasgow revD (LFE5U-25F) | quad, opt-in | 48 MHz max, 12 MHz default | ~24 / ~6 MB/s |

The closest peer uses **the identical instruction and the identical `USRMCLK`
path at a third of the clock.** The highest `USRMCLK` frequency reported anywhere
is NanoMig's **84 MHz**, driven from a phase-shifted PLL output (`CLKOS2` at
216°) — and its own comment says it is only used at power-up.

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

So [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md)'s *"132% past
the 62 MHz Lattice specifies for `MCLK`"* is comparing against the wrong number.
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

### The measured answer to the QPI question

`picosoc`'s `performance.py` commits raw cycle counts for exactly this
comparison — iCE40, 253,741 instructions:

| config | cycles | vs `0x03` |
|---|---|---|
| `0x03` 1-1-1 | 17,781,487 | 1.00× |
| quad 1-4-4 | 4,698,331 | 3.79× |
| **quad + continuous read** | **4,512,379** | **3.94×** |
| quad + DDR + continuous (`0xED`) | 2,308,609 | 7.70× |

Every adjacent step differs by exactly 23,244 cycles — the flash-restart count.
**Dropping the 8-clock opcode phase is worth +4.1% at quad SDR.** QPI recovers
only 6 of those 8 clocks, and only when continuous read is *not* already in use.
That is a measured bound on the entire question, and it agrees with the
arithmetic above.

ZipCPU's `qflexpress` writeup reaches the same conclusion analytically — 28
cycles/word plain 1-4-4, 20 with XIP — *"with the whole 8-cycle saving coming
from the mode bits, not from QPI"*. He deferred DDR flash modes and never
returned.

**Almost nobody implements QPI on flash in an FPGA.** `gh search code "EnterQPI"`
returns zero FPGA cores. LiteSPI has the 4-4-4 datapath but **never issues an
enter-QPI command** for Winbond, and no Winbond module in its ~9000-line device
database is declared QPI-capable. Its DTR opcodes (`0x0D`/`0xBD`/`0xED`) are
transcribed from JESD216B with empty descriptions and **both PHYs assert
`not flash.ddr`** — table entries, dead gateware. Sylvain Munaut's `no2qpimem`
is the one core that genuinely issues `0x38`, and it is iCE40.

**One warning from the only project to run the experiment.**
`hsk/tangnano20k_spi_flash_example` has parallel directories for `0xEB`
continuous, `0x38` QPI, and QPI continuous — and its README warns that **once the
part enters QPI mode it never exits, so the board cannot be reprogrammed until a
power cycle.** That is the same class of hazard as the continuous-read sticky
state already documented for this board, and a good reason to be glad the
question is moot here.

### Boot configuration, which is a separate lever

`ecppack --spimode qspi --freq 62.0` is documented and works. TN-02039 §6.1.4:
Master SPI has serial/dual/quad submodes, quad issuing `0xEB` at *"four times the
rate of standard SPI devices"*. The divider offers exactly
{2.4, 4.8, 9.7, 19.4, 38.8, 62} MHz, default **2.4**.

One published measurement: Dimitrios Kouzis-Loukas, 15 July 2025, on an
LFE5UM5G-85F — `MCCLK_FREQ=62` plus quad read, *"It takes just 60 ms to configure
the FPGA"*. He also reports the constraint that `0x03` works only to 38.8 MHz, so
62 MHz needs at least `0x0B`.

Three gotchas: **the ECP5 does not set the flash's QE bit** (neither technical
note mentions QE; DiVA disables qspi config on r0.1 boards for exactly this) —
ours is already set, so that one is free; `--spimode` breaks some programmers, and
prjtrellis itself strips `spimode`/`freq` when emitting SVF, so build two
bitstreams; and **SPI Quad is not supported on TQFP144**, a note added in
datasheet revision 3.3.

Existing analysis: [`luna_ecp5_fpga/qspi-boot-time.md`](luna_ecp5_fpga/qspi-boot-time.md).

---

## Ranking, by return per effort

**Flash**

| rank | option | worth | effort |
|---|---|---|---|
| ✔ | `FLASH_MODE = "quad"` | **2.70×** measured | **done in `03482f4`**, for −261 LUTs |
| 1 | replace luna_soc's PHY (`SCK` capped at `sync`/2) | 2× | large, and it is the only one not gated on the CPU clock |
| 2 | raise `SYNC_MHZ` | 2× at 120 MHz | **gated by the RISC-V Fmax of 75 MHz**, not by the flash |
| 3 | 128-byte I-cache line | +7.2% | a parameter, plus block RAM at 75% already |
| 4 | `0xEB` continuous read in the SoC | +5.1% | small, but the mode is sticky across reconfiguration |
| 5 | `0x77` wrap-64 + critical-word-first | −72% miss stall, 0% throughput | controller + cache work |
| 6 | SR3 drive strength | nothing today | one write, and it has already failed to take |
| — | **QPI, DTR, `0xC0`** | **absent on this part** | — |

**HyperRAM**

Split in two, because throughput at the proven clock and the 200 MHz question are
different projects with different evidence.

**Throughput at CK 192 MHz — bounded, and the bound is 97.7%**

| rank | option | worth | effort |
|---|---|---|---|
| 1 | 512-word bursts with a tCSM splitter | **+10.7%** | a splitter in the controller; the constant alone is unsafe below CK 133 |
| 2 | variable latency `CR0[3] = 0` | **+4.5%** | CR0 write + two constants + move the RWDS sample into the CA window |
| 3 | hybrid burst + wrap-64 | −42% miss stall, 0% throughput | decide before #90 lands |
| — | lower initial latency count | **negative** — 324 vs 334.4 MB/s | — |
| — | partial array refresh | ~0.4%, costs 4 MiB, and may do nothing | — |

Together, 1 and 2 give **375.2 MB/s — 97.7% of everything CK 192 can deliver**,
+12.2% on today. Neither is tried anywhere: **neither LUNA nor Tiliqua writes CR0
at all.** That is the whole of the throughput story; past this the clock is the
only lever.

**The CK 200 failure — cheap discriminators first**

| rank | option | what it establishes | effort |
|---|---|---|---|
| 1 | differential clock `CR1[6] = 0` | removes threshold error from the sampling instant; the board is already wired for it and nobody has tried it | **one register write** |
| 2 | `CLKOS2_CPHASE` / `FPHASE` sweep at CK 200 | alignment faults move in discrete steps, skew faults narrow continuously — **this is the discriminator** | bitstreams only |
| 3 | drive strength `CR0[14:12]` → `101`/`110`/`111` | the `tDSS` finding makes this more plausible than the first draft of this page allowed | three register writes |
| 4 | `READCLKSEL` training from Tiliqua | fixes `BURSTDET`, which converts "works, reason unknown" into a measured eye | drop-in from a common ancestor; use gram's midpoint policy, not first-pass-wins |
| 5 | `READ0`/`READ1` outer sweep | `READCLKSEL` spans only 2T; if the delay is outside it, item 4 finds nothing | harness change |
| 6 | `CLKDIVF` + `ECLKSYNCB` + `ALIGNWD`, with Davill's two-level firmware sweep | **the only published open-source fix for this exact failure on ECP5** | large, and it changes every HyperRAM bitstream |
| 7 | `RDMOVE` fine-delay sweep | centres the eye — but hands PVT tracking to you | after 4, not before |
| 8 | fit the 5I (200 MHz) part | if a 200 MHz-screened part *also* slips, the fault is the gearing, not the memory | rework; diagnostic first, upgrade second |
| 9 | 1.8 V rail | halves `tDSS`/`tDSH` | rework + a bank audit not yet done |

**Do 1 and 2 first.** Between them they cost no design work and they separate the
two live hypotheses — strobe skew against word-boundary alignment — which decides
whether the rest of the list is worth starting.
