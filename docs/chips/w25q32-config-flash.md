# Winbond W25Q32 — the configuration flash

The SPI NOR flash the ECP5 boots from on Cynthion r1.4. **Exactly 4 MiB**, and
unlike the [HyperRAM](w956a8-hyperram.md) on the same board it is exactly what its
marking says.

**Index:** [`../hardware.md`](../hardware.md)

## Identification, read from the part

| register | value | meaning | how |
|---|---|---|---|
| JEDEC ID | `EF 40 16` | Winbond, type `0x40`, capacity `0x16` = 2^22 | `scripts/flash_capacity_probe.py`, `apollo flash-info` |
| **SFDP density** | **4 MiB** | the die's own declaration, independent of the ID byte | `scripts/flash_capacity_probe.py` |
| unique ID | `355027cba3ac60de` | per-part | `apollo flash-info` |
| status register 2 | `0x02`, **QE set** | quad needs no register write | `scripts/flash_ceiling.py --status` |
| status register 3 | `0x60`, **DRV 25%**, WPS 0 | weakest output drive; BP-style protection, not block locks | `scripts/flash_ceiling.py --status` |

**The variant is `W25Q32JVSSIQ`** — named in
`repos/cynthion-hardware/bank8_configuration.kicad_sch:8011`, datasheet
**W25Q32JV Revision G, 27 March 2018**. Three measurements agree: `EF 40 16`
rules out JV-IM/JM (`70 16`) and both JW parts; SR2 QE=1 out of the box is the
`IQ`/`JQ` factory-fixed default (§7.1) and the *opposite* of the IM/JM default;
and SR3 existing at all rules out the W25Q32BV, which also reads `40 16` but has
no Status Register-3.

**This matters because the JV split the feature set.** The `-IM`/`-JM` part gets
QPI and DTR in a separate datasheet; the `-IQ` fitted here has **neither**, nor
`0xC0` Set Read Parameters. See
[`../decisions.md`](../decisions.md) 26.

**Correction: there is no ADS bit on this part.** This table previously read
SR3 `0x60` as "ADS clear". ADS/ADP are 4-byte-addressing bits and exist only on
≥256 Mbit parts; SR3 bit S23 here is Reserved. `0x60` is DRV=25% and WPS=0, and
nothing else. (S23 *is* `HOLD/RST` on the W25Q32FV — the JV dropped it.)

**Capacity confirmed three ways** — SFDP, the ID byte, and aliasing. Reads at 4, 8
and 12 MiB all return offset 0 exactly; reads past 16 MiB get no response.

The aliasing test is sound **because offset 0 holds real bitstream data** —
`Part: LFE5U-12` is legible in the hex — rather than erased `0xFF`. Two blank
regions would match trivially and prove nothing; that trap is why the probe
compares against live data.

Values are from one board. A second has not been checked.

## Wiring on r1.4

| resource | signal | ECP5 pin |
|---|---|---|
| `spi_flash` | `sdi` (MOSI) | T8 |
| | `sdo` (MISO) | T7 |
| | `cs` (active low) | N8 |
| `qspi_flash` | `dq[0..3]` | T8, T7, M7, N7 |
| | `cs` | N8 |
| clock | `SCK` | **no ball number** — reachable only through the `USRMCLK` macro |

Declared in `ecp5-test/cynthion_platform/cynthion_r1_4.py`. All four quad data
lines are wired, so quad mode is a gateware question, not rework.

## No read ceiling has been found (NEW, 2026-08-03)

**Nothing fails.** Every mode reads byte-exact at every rate reachable, up to
**144 MHz SCK** — **8% past this part's rating**, which is 133 MHz for everything
except `0x03` at VCC 3.0–3.6 V, and 2.9× past `0x03`'s own 50 MHz.

**Correction: there is no Lattice `MCLK` figure to be past.** This section
previously read *"132% past the 62 MHz Lattice specifies for `MCLK`"*. The 62 MHz
is `fCCLK` in the sysCONFIG port timing table — the **configuration engine's**
oscillator ceiling, which has nothing to do with user mode. The string `USRMCLK`
does not appear in the ECP5 datasheet at all; FPGA-TN-02039 §6.1.2, the only
`USRMCLK` documentation, gives no fmax, no setup/hold and no jitter; and
prjtrellis has **no timing entry for this path in any speed grade**, so a clean
nextpnr report says nothing about it. That statement is stronger than the
one it replaces: **user-mode `USRMCLK` is unmodelled by the vendor and by the
open toolchain**, and measurement is the only authority.

The limit reached is **the test design's own fmax**, not the flash and not the
pin. SCK is `sync / (divisor + 1)`, so the sync clock a bitstream is built at is
its top SCK; this design closes at 149 MHz and 144 is the fastest legal PLL rate
below that. Two fixes moved it from 131 to 149 (`ecp5-test/qspi/qspi_gateware.py`);
above that the critical path is inside Glasgow's own `IOStreamer`, which is the
part that has to run at SCK.

60 points, 5 modes × 4 divisors × 3 sync rates, all PASS:

| SCK | `0x03` | `0x0B` | `0x6B` | `0xEB` | `0xEB` continuous |
|---|---|---|---|---|---|
| **144 MHz** | 17.96 | 17.95 | 71.22 | 71.56 | **71.70 MB/s** |
| 130 MHz | 16.21 | 16.21 | 64.29 | 64.61 | 64.73 |
| 120 MHz | 14.97 | 14.96 | 59.35 | 59.64 | 59.75 |
| 72 MHz | 8.98 | 8.98 | 35.63 | 35.80 | 35.87 |
| 30 MHz | 3.74 | 3.74 | 14.85 | 14.92 | 14.95 |

`scripts/flash_ceiling.py --run`. Each point is verified against
`apollo flash-read` at **both ends** of a 1 KiB capture window — bytes 0-15 and
1008-1023 — because the first bytes of a read are the ones a marginal clock gets
right, so checking only the start checks the easiest part.

**`0x03` runs at 144 MHz**, which the datasheet rates at 50. The opcode's rating
is not a wall on this board.

### Superseded

| was recorded | now |
|---|---|
| divisor 0 produces no clock; SCK capped at sync/2 | **wrong.** Divisor 0 reads byte-exact at every rung, at exactly half the cycle count of divisor 1 |
| 120 MHz FAIL, 160 MHz FAIL | both were divisor-0 points, disbelieved for the reason above. 120 MHz passes |
| 80 MHz is the fastest verified | 144 MHz, five modes |

**No DDR, and fitting the part that has it would be slower.** The datasheet
contains no DTR opcodes; DDR reads belong to the `W25Q32JV-DTR` (`-IM`/`-JM`,
JEDEC `70 16`), which this is not. Its "equivalent 208/416 MHz" claim is lane
parallelism, not double-edge clocking.

Worth settling permanently, because "DDR would double it" is the obvious next
thought: **DTR on the `-IM` part is rated 66 MHz SCK** at VCC 3.0–3.6 V, against
133 MHz for its SDR instructions. Four lanes at double rate at 66 MHz is
4 × 2 × 66e6 / 8 = **66 MB/s** — below the **71.7 MB/s** this board already
measures. Per cache line `0xED` costs 8 + 3 + 8 + 64 = 83 clocks at 66 MHz =
**1258 ns**, against 1028 ns today. **Double rate at half the clock is a
downgrade.** Genuine DDR on this board is the HyperRAM.

Full speed table, read modes, clock domains and the bugs found getting there:
[`../luna_ecp5_fpga/flash-detailed.md`](../luna_ecp5_fpga/flash-detailed.md).

## Cache-line refill, which is what firmware execution pays (NEW, 2026-08-03)

A VexiiRiscv I-cache miss costs one transaction plus 64 bytes and nothing else,
so this is the number that decides whether flash is the bottleneck. Timed in the
FPGA across 256 reads at strided addresses, since one read is microseconds and
the host's JTAG access is not:

| mode | overhead | 144 MHz SCK | 36 MHz SCK |
|---|---|---|---|
| `0x03` single | 32 clocks | 3833 ns | 15181 ns |
| `0x6B` quad output | 40 | 1222 ns | 4736 ns |
| `0xEB` quad I/O | 20 | 1083 ns | 4181 ns |
| `0xEB` continuous | 12 | **1028 ns** | 3958 ns |

Measured within 5% of the clock count arithmetic throughout, so the model holds
and intermediate rates can be read off it.

- **Quad is the big win**: 3.1× on a cache line, from four lanes.
- **`0xEB` over `0x6B` is 11%** — it sends the address on four lanes too, halving
  transaction overhead from 40 clocks to 20. Worth 0.2% on a bulk copy; this is
  the case it exists for.
- **Continuous Read is a further 5%.** Verified byte-exact with the opcode
  genuinely omitted, not just faster.
- **The SoC is not single-lane and no longer at 30 MHz.** That claim was true
  when this table was written and is not now: `FLASH_MODE = "quad"` and
  `SYNC_MHZ = 72`, so SCK is 36 MHz and the mmap core issues `0xEB`. Measured on
  the board: **11.27 MB/s sequential, 5.68 µs per 64-byte line**, against the
  4.18 µs this table models for `0xEB` at 36 MHz — the right regime, with about a
  third of the overhead still unaccounted for.
- So **the mode work is finished and only SCK is left**. Continuous Read is worth
  a further 5% and is the last thing in the part's gift; everything beyond it is
  a clock-architecture question, not a flash one. `0xEB` continuous at 144 MHz
  would be 5.7× the current per-line time.

### Continuous Read is device state

`0xEB`'s mode byte `M5-4 = (1,0)` — `0xA0` — makes the *next* transaction omit
its opcode. `0xFF` leaves. The part **remembers this across an FPGA
reconfiguration**, and a part left in it answers an opcode with an address
phase. Worse, it does not return obvious nonsense: with no opcode sent it reads
the first eight DQ0 bits of the x4 address and mode byte *as* an opcode — for
address 0 and mode `0xFF` that spells `0x03` — and answers with real flash
contents from an unintended address. Anything that enters it must leave it on
every exit path.

## What is in it, and how software reaches it

The **bitstream lives at offset 0**. If the FPGA is configured over USB at startup
instead of from flash, the whole 4 MiB is free — which is what makes it usable as
RISC-V storage.

| path | how |
|---|---|
| host, slow | `apollo flash` — bit-banged through Apollo's software JTAG TAP |
| host, fast | `apollo flash --fast` — FlashBridge gateware in FPGA SRAM, USB bulk straight to the fabric, Apollo out of the data path |
| CPU, memory-mapped | `SPIFlashMemoryMap` window; see [Register reference](../hardware.md#register-reference) for the address |
| CPU, arbitrary commands | `HoldableSPIController` + `FairSPIControlPortCrossbar` in `ecp5-test/riscv/vexii_flash.py` — **not** luna_soc's, which has two defects here ([`../upstream-boundary.md`](../upstream-boundary.md)) |
| sideband | `scripts/sideband_read.py` |

Boot-image selection, slot layout and the partition work:
[`../luna_ecp5_fpga/flash-partitioning.md`](../luna_ecp5_fpga/flash-partitioning.md).
Whether quad SPI speeds up configuration:
[`../luna_ecp5_fpga/qspi-boot-time.md`](../luna_ecp5_fpga/qspi-boot-time.md).

## Registers that affect read speed (NEW, 2026-08-03)

- **QE (SR2 bit 1) is already set, and cannot be cleared.** §7.1 calls it the
  *"factory **fixed** default"* for `IQ`/`JQ` ordering options. So IO2 and IO3 are
  unconditionally data pins: there is no /WP, no /HOLD and **no /RESET** on this
  part, which is why Winbond supplies the `66h`/`99h` software reset instead
  (§8.2.35). Quad costs nothing here because the protection was never available.
- **Output drive is 25%, the default, and it does not need raising.** 100% is
  available and writable *volatile*, but nothing fails at 25% up to 144 MHz, so
  there is no failure for a stronger driver to fix. A volatile write attempted
  through Apollo's background SPI (`0x50` then `0x11`) **did not take** — SR3
  read back unchanged at `0x60`. The part was not modified.
- **No dummy-cycle register exists on this part.** `0xEB` in SPI mode is fixed at
  4 dummy clocks after the 2-clock mode byte. `0xC0` Set Read Parameters is a
  QPI-only command and **this part has no QPI mode** — the whole instruction set
  is 34 standard plus 8 dual/quad opcodes, and `38h`, `FFh` and `C0h` are in
  none of them. The DTR datasheet says it directly: *"In Standard SPI mode, the
  'Set Read Parameters (C0h)' instruction is not accepted. The dummy clocks …
  in Standard/Dual/Quad SPI mode are fixed."*
- **`0x77` Set Burst with Wrap is present**, and it is the only one of QPI /
  DTR / `0xC0` / `0x77` that this part has. It gives critical-word-first within
  an 8/16/32/64-byte section, persists across transactions, applies to `0xEB`
  only, and **does not remove the address phase**. Default `W4` = 1, disabled.

Every remaining speed option on this part, with the arithmetic:
[`../decisions.md`](../decisions.md) 26.

## What the SoC took, and what it bought (NEW, 2026-08-03)

`FLASH_MODE = "quad"` is **adopted**. One row per commit, measured with
`scripts/soc_shell.py bench` on the board; Fmax from three
`scripts/soc_timing_sweep.py` runs each, because a single place-and-route on
this design spreads 8 MHz on thread scheduling alone.

| commit | change | metric | before | after | factor |
|---|---|---|---|---|---|
| `quad` | `FLASH_MODE` `single` → `quad` (`0x03` → `0xEB`) | flash 16 KiB read rnd, cycles/access | 958.71 | 355.57 | **2.70×** |
| `quad` | — same commit | flash 16 KiB read seq, cycles/access | 93.67 | 50.11 | **1.87×** |
| `quad` | — same commit | flash 2 KiB read seq, cycles/access | 35.10 | 29.89 | 1.17× |
| `quad` | — same commit | flash 2 KiB read rnd, cycles/access | 62.08 | 63.43 | 0.98× (D-cache resident; no refill to speed up) |

Cost, over the same three-run sweeps:

| | LUT | FF | BRAM | Fmax min / median / max |
|---|---|---|---|---|
| single | 12769 | 6553 | 42 | 74.54 / 75.24 / 78.75 MHz |
| quad | 12508 | 6554 | 42 | 69.82 / 75.02 / 77.27 MHz |
| delta | **−261** | +1 | **0** | median −0.22 MHz |

**Quad is cheaper in LUTs, not dearer.** The FSM shifts four bits per clock
instead of one, so its bit counters are two bits narrower and the address
shift register is shorter. Nothing was spent to get 2.7×.

**The refill number predicted the bench number.** 3833 → 1083 ns is 3.54× on a
line; the 16 KiB random walk, which misses on essentially every access, moved
2.70×. The gap is the part of those 956 cycles that was never flash: ~14
instructions of xorshift and loop per access, and a fetch path with no branch
predictor (#140) charging four cycles an instruction. Flash stopped being the
whole cost, so it could not deliver the whole ratio.

**2 KiB random is the control.** It fits the 4 KiB D-cache, so it never refills
and it did not move — 62.08 → 63.43, inside run-to-run noise. A change that had
sped that row up would have been measuring something other than the flash.

Two things still stand between this and the ceiling in the table above:

| change | gets | cost |
|---|---|---|
| raise `SYNC_MHZ` | 2× at 120 MHz | the CPU clock moves with it; `usb` must stay exactly 60 MHz |
| replace luna_soc's PHY | 2× again | `SPIPHYController` toggles a flip-flop, so **SCK is structurally capped at sync/2**. Glasgow's controller reaches sync/1, which is how 144 MHz was measured |

`FLASH_DIVISOR` **cannot be made a CSR as written**: `SPIClockGenerator` uses it
to size its counter (`bits_for(div)`) at elaboration. Fixing the width and
comparing against a register would make it one. `FLASH_MODE` changes the FSM's
state list, so it is structural in a way the opcode and dummy value are not —
those two are already plain constants a register could hold.

## Not measured

**Write and erase timing.** Everything above is reads (#93).

**Anything above 144 MHz SCK.** The instrument runs out before the flash does.
Reaching further means either lifting the test design's fmax past 149 MHz — the
critical path is inside Glasgow's `IOStreamer` — or generating SCK in a 2× clock
domain so the fabric need not run at SCK.

**`ODDRX1F` cannot do it, and the reason is the silicon, not the tool.** nextpnr
refuses one whose `Q` drives anything but a top-level output, and `USRMCLK` is
not one — but underneath that, the CCLK site has **no `DATAMUX_ODDR`/`IOLDO` mux**
in the Trellis routing database, unlike every real PIO, and `JA4`'s mux sources
carry **no global-clock spine source**, so a global clock cannot reach `USRMCLKI`
without passing through a LUT or FF. There is no software fix. The two published
workarounds are hand-placed fabric DDR flip-flops next to the CCLK site
(`dan-rodrigues/icestation-32`), which reaches the fabric rate and does not
exceed it, and driving `USRMCLKI` from a phase-shifted PLL output (NanoMig, at
84 MHz). **This board is already past both.**

## Scripts

| | |
|---|---|
| `scripts/flash_capacity_probe.py` | JEDEC, SFDP, aliasing — read-only |
| `scripts/flash_backup.py` | full image backup |
| `scripts/flash_ceiling.py` | **the current one.** Bitstream ladder, verified SCK sweep, cache-line refill, status registers |
| `scripts/flash_speed_ladder.py`, `flash_modes.py`, `qspi_ladder.py` | earlier speed and mode characterisation |
| `scripts/test_flash_id.py` | JEDEC read |
| `apollo flash-info` | JEDEC and unique ID |

# Speed: every remaining option, and which are absent

Moved here from the dissolved `memory-speed-options.md`, which held only the
ranking and the parts that span both memories. These are properties of the
part, so they belong beside the part.

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
| SR3 `0x60`, **"ADS clear"** (this document) | **there is no ADS bit on this part.** ADS/ADP are 4-byte-addressing bits and exist only on ≥256 Mbit parts. SR3 bit S23 is Reserved. `0x60` means DRV=25%, WPS=0, and nothing else |
| *"QPI mode can address in as few as 8 clocks"* ([`luna_ecp5_fpga/flash-detailed.md`](../luna_ecp5_fpga/flash-detailed.md)) | **this part has no QPI mode.** The claim is true of the FV and of the JV-IM, not of what is fitted |

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
[`luna_ecp5_fpga/bram-budget.md`](../luna_ecp5_fpga/bram-budget.md) says is the
scarce resource, and it is only a win if locality holds.

**The largest item was taken while this survey was being written.**
`FLASH_MODE = "quad"` landed in `03482f4` — 2.70× on a 16 KiB random walk, for
**−261 LUTs and no block RAM**. What remains of the SoC gap is
this document's two rows,
`SYNC_MHZ` and the PHY, and they have a constraint that survey does not state:

**`SYNC_MHZ` is gated by the CPU, not the flash.** The SoC's median Fmax is
**75.0 MHz** across three place-and-route runs. Raising `sync` to 120 MHz for the
flash's sake requires the RISC-V core to close at 120 MHz first — see
[`soc-clocking.md`](../soc-clocking.md). The flash's remaining 2× is
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

Existing analysis: [`luna_ecp5_fpga/qspi-boot-time.md`](../luna_ecp5_fpga/qspi-boot-time.md).
