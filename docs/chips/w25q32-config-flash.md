# Winbond W25Q32 — the configuration flash

The SPI NOR flash the ECP5 boots from on Cynthion r1.4. **Exactly 4 MiB**, and
unlike the [HyperRAM](hyperram/w956a8.md) on the same board it is exactly what its
marking says.

**Index:** [`../hardware.md`](../hardware.md)

## Performance

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md).
Datasheet references are **W25Q32JV Revision G, 27 March 2018**
(`sources/Winbond-W25Q32JV-32Mbit-SPI-Flash-RevG.pdf`, 80 pp), which is the
revision `bank8_configuration.kicad_sch:8011` names.

Two things this section exists to make obvious. **The clock pin, not the part, is
what bounds reads** — SCK can only leave this FPGA through `USRMCLK`, which has
no DDR output, so SCK can never exceed the fabric clock driving it. And **the
host→flash programming path is a different path from CPU reads and is ~20× slower
than the chip it is programming**, with SCK playing no part in that at all.

### 1. Theoretical maximum

**Reads.** §9.6 AC Electrical Characteristics (p. 63) gives three clock ratings,
and the board runs at 3.3 V so the first applies:

| symbol | applies to | max |
|---|---|---|
| `fC1` | everything except `0x03`, at VCC 3.0–3.6 V | **133 MHz** |
| `fC2` | the same, at VCC 2.7–3.0 V | 104 MHz |
| `fR` | `0x03` Read Data only | **50 MHz** |

Lane count multiplies directly, because every mode moves one bit per lane per
clock — there are no DTR opcodes on this die:

    0x03  1-1-1 @  50 MHz  =  1 x  50e6 / 8 =   6.25 MB/s
    0x0B  1-1-1 @ 133 MHz  =  1 x 133e6 / 8 =  16.6  MB/s
    0xBB  1-2-2 @ 133 MHz  =  2 x 133e6 / 8 =  33.3  MB/s
    0xEB  1-4-4 @ 133 MHz  =  4 x 133e6 / 8 =  66.5  MB/s   <- the part's ceiling

**Continuous Read is a per-transaction saving, not a bulk one.** It removes the
8-clock opcode phase and nothing else, so it is invisible on a large copy and
worth 5.4% on a 64-byte cache line. Clocks per 64-byte line, at SCK:

| mode | opcode | address | mode byte | dummy | data | total |
|---|---|---|---|---|---|---|
| `0x03` 1-1-1 | 8 | 24 | — | 0 | 512 | **544** |
| `0x0B` 1-1-1 | 8 | 24 | — | 8 | 512 | **552** |
| `0x6B` 1-1-4 | 8 | 24 | — | 8 | 128 | **168** |
| `0xEB` 1-4-4 | 8 | 6 | 2 | 4 | 128 | **148** |
| `0xEB` continuous | — | 6 | 2 | 4 | 128 | **140** |

**Writes and erases are three orders of magnitude away, and they are not clocked
at all** — they are self-timed, so SCK is irrelevant to them. §9.6, p. 64, with
the page at 256 bytes and the array at 16,384 pages (§6.1, p. 9):

| operation | payload | typ | max | typ throughput |
|---|---|---|---|---|
| page program `tPP` | 256 B | **0.4 ms** | 3 ms | **625 KiB/s** |
| sector erase `tSE` | 4 KiB | 45 ms | 400 ms | 88.9 KiB/s |
| block erase `tBE1` | 32 KiB | 120 ms | 1600 ms | 266.7 KiB/s |
| block erase `tBE2` | 64 KiB | 150 ms | 2000 ms | 426.7 KiB/s |
| chip erase `tCE` | 4 MiB | 10 s | 50 s | 419 KiB/s |
| **erase + program the whole part** | 4 MiB | 10 s + 6.55 s | — | **253 KiB/s** |

So the part's own ceiling is **66.5 MB/s reading and 253 KiB/s writing** — a
factor of 270. Erase dominates a small write and program dominates a large one.

### 2. Achievable on this board — the pin binds, and it is `USRMCLK`

**SCK is on N9 = MCLK/CCLK, and N9 has no alternate function.** The four data
lines do: T8/T7 are `PB11B`/`PB11A` and M7/N7 are `PB9B`/`PB9A`, ordinary bank-8
I/O as well as MSPI pins, which is why they keep working at full rate after
configuration. MCLK does not, and sysCONFIG FPGA-TN-02039 says why — *"The MCLK
is always reserved for use in MSPI mode, in most post-configuration
applications, as the reference clock for performing memory transactions with the
external SPI PROM."* On r1.4 the only copper to the flash clock is from N9.

**So SCK is not a pin that can be requested.** The platform's `qspi_flash`
resource has `dq` and `cs` and **no clock at all**
(`gateware/board/cynthion_r1_4.py:95-101`); `ECP5ConfigurationFlashInterface`
proxies every other signal through to the real pins and supplies `sck` as a plain
signal that the `USRMCLK` macro consumes (`gateware/soc/top.py:920-928`).

**And there is no DDR at that site, which is the whole constraint.** nextpnr
refuses an `ODDRX1F` whose `Q` drives anything but a top-level output, and
`USRMCLK` is not one — but underneath that, the CCLK site has **no
`DATAMUX_ODDR`/`IOLDO` mux** in the Trellis routing database, unlike every real
PIO, and `JA4`'s mux sources carry **no global-clock spine source**, so a global
clock cannot reach `USRMCLKI` without passing through a LUT or FF. There is no
software fix.

**The consequence, stated as the rule it is:**

> **SCK ≤ the fabric clock of the domain that generates it.** Never 2×.

| generator | SCK | note |
|---|---|---|
| luna_soc `SPIClockGenerator` | **domain / 2** | toggles SCK as a register — structurally halved |
| Glasgow `IOStreamer` | **domain / 1** | routes the domain clock itself; how 144 MHz was reached |

Reaching the part's in-spec 133 MHz therefore needs a **133 MHz fabric domain**
on an LFE5U-12F speed grade 8. Inside the full SoC it does not exist: nextpnr
reports the flash PHY's own domain — and the PHY is the only thing in it —
closing at **111–125 MHz** (`gateware/soc/top.py:590-594`, `fast` 144 → 124.77 MHz
FAIL, `fast` 120 → 111.26 MHz FAIL). So:

    in-SoC board max, quad  = 4 x 125e6 / 8 = 62.5 MB/s   (94% of the part)
    in-spec board max, quad = 4 x 133e6 / 8 = 66.5 MB/s   (needs a domain that does not close)

**What a board revision would buy, precisely.** If SCK were on an ordinary bank
pin, an `ODDRX1F` would give SCK = 2 × fabric, and the part's full 133 MHz would
come from a **66.5 MHz** domain — a clock this design already closes at
comfortably. That is the number: *the part's rated ceiling at half the fabric
clock, instead of a fabric clock the device cannot reach.* It would cost the
boot-from-flash path — the reservation is a convention rather than a hardware
rule, and outside MSPI mode even `CSSPIN` reverts to general-purpose I/O, so such
a board is possible — and a board that cannot configure itself from flash depends
entirely on the debug controller for recovery.

**As built today, the ceiling is 15.0 MB/s.** `SYNC_MHZ = 60`,
`FLASH_DIVISOR = 0`, `FLASH_PHY_FAST = False`, `FLASH_MODE = "quad"`
(`gateware/soc/top.py:513`, `:540`, `:567`, `:615`), so SCK is `60 / 2` = **30 MHz**
and quad gives `4 × 30e6 / 8` = 15.0 MB/s. Lane count is finished; only the clock
is left, and it is gated by the CPU rather than by the flash — `fast` must divide
the same VCO as `sync` by an integer.

**The host→flash programming path has a different bound entirely.** Apollo drives
the flash over its background SPI through USB vendor requests, and one
response-requiring transfer costs **3.00 ms** of USB round trip, measured
(`repos/apollo` `90c8b7b`). At 256 bytes a page, the pacing is round trips per
page — a write-enable and a completion poll — not SCK, not lane count, and not
anything the FPGA does.

### 3. Measured

| path | conditions | figure | source |
|---|---|---|---|
| bulk read, gateware harness | `0xEB` continuous, quad, **SCK 144 MHz**, `sync` 144, 1 KiB windows verified at bytes 0–15 **and** 1008–1023 | **71.70 MB/s** | `scripts/flash_ceiling.py --run`, 2026-08-03 |
| bulk read, single lane | `0x03`, SCK 144 MHz, same harness | 17.96 MB/s | as above |
| cache-line refill | `0xEB` continuous, SCK 144 MHz, 256 strided reads timed in the FPGA | **1028 ns / 64 B = 62.3 MB/s** | as above |
| cache-line refill | `0x03` single, SCK 144 MHz | 3833 ns / 64 B | as above |
| SoC memory-mapped read | `0xEB` **non**-continuous, quad, SCK 36 MHz, sequential | 11.27 MB/s, 5.68 µs / line | `scripts/soc_shell.py bench` |
| **host→flash program** | 58,940 B at offset `0xb0000`, `apollo flash-program` via Apollo background SPI, erase = 1 × 32 KiB block + 7 × 4 KiB sectors | **3.33 s = 17.3 KiB/s** | `repos/apollo` `90c8b7b`, driven by `scripts/soc_run.py`, 2026-08-06 |
| page program, sector erase, block erase on the part | — | **never measured** | #93 — every figure above is a read |

Three conditions worth attaching rather than assuming:

- **71.70 MB/s is out of spec.** SCK 144 MHz is 8% past `fC1` = 133 MHz, and
  `0x03` at 144 MHz is 2.9× past its own 50 MHz rating. Nothing failed at any of
  60 points, but the limit reached was the *test design's* fmax of 149 MHz, not
  the flash — so this says the part is comfortable, not that 144 MHz is a
  supported operating point.
- **The 11.27 MB/s row no longer describes the built SoC.** It was taken at
  `SYNC_MHZ = 72`, i.e. SCK 36 MHz. `SYNC_MHZ` is **60** today, so SCK is 30 MHz
  and the same firmware would be slower. Re-measure before quoting it.
- **17.3 KiB/s is after a fix, not before.** The same image took **4.71 s** until
  two redundant USB round trips per page were removed — a write-enable
  verification that re-checks a latch which either works on the first page or not
  at all, and a completion poll immediately followed by the next page's own wait.
  231 pages × 2 round trips × 3.00 ms predicted 1.4 s; 1.38 s was measured.

### 4. The gap, and what closes it

**The host→flash path is the whole story, and the gap is transport, not silicon.**
For that exact 58,940-byte image, from §9.6 typicals:

| | |
|---|---|
| erase — `0xb0000` is 64 KiB-aligned, so 1 × 32 KiB block + 7 × 4 KiB sectors | `120 + 7 × 45` = **435 ms** |
| program — 231 pages × `tPP` 0.4 ms | **92 ms** |
| **what the W25Q32JV itself needs** | **≈ 0.53 s** |
| **measured wall time** | **3.33 s** |
| **the part's share** | **16%** |

**2.8 seconds of that is USB round trips**, and SCK does not appear anywhere in
the arithmetic. Removing it needs the page loop to run on the MCU — host ships
bulk, the SAMD11 paces the flash at SPI speed — which is a firmware command that
does not exist, on a part at 94.4% of its flash budget. **#100** tracks it.

*(A note on provenance: `repos/apollo` `90c8b7b` and `scripts/soc_run.py:483`
give the program share as ~0.16 s. That uses `tPP` = 0.7 ms from the
**preliminary 2014** copy of the datasheet that was in `sources/`; Revision G
says 0.4 ms, which is 92 ms. The gap is wider than those comments claim, not
narrower.)*

Ranked, with what each is worth:

| rank | option | worth | effort |
|---|---|---|---|
| ✔ | `FLASH_MODE = "quad"` | **2.70×** on a 16 KiB random walk | done in `03482f4`, for **−261 LUTs** and no block RAM |
| 1 | **page loop on the SAMD11** (#100) | 3.33 s → ~0.6 s, **5.5×**, on every firmware iteration | a firmware command that does not exist, on a part at 94.4% of its budget |
| 2 | replace luna_soc's PHY — SCK is capped at `domain`/2 | **2×** on reads | the only read option not gated on the CPU clock |
| 3 | raise the flash domain | 2× at 120 MHz | `fast` must divide the same VCO as `sync`; the SoC's median Fmax is 75.0 MHz and the flash PHY's own is 111–125 |
| 4 | 128-byte I-cache line | +7.2% | a parameter, plus block RAM already at 75% |
| 5 | `0xEB` continuous read in the SoC | +5.1% per line | small, and the mode is sticky across an FPGA reconfiguration |
| 6 | `0x77` Set Burst with Wrap | 72% off the **stall**, 0% off throughput | needs a wrapped-read path and an I-cache that restarts on the critical word |
| — | **SCK on an ordinary bank pin** | the part's 133 MHz from a 66.5 MHz domain | **a board revision**, and it costs self-configuration |
| — | QPI, DTR, `0xC0` | **absent on this die** | — |

**Unknown:** about a third of the SoC's per-line cost. The model says 4.18 µs at
SCK 36 for `0xEB`; the board said 5.68 µs. The regime is right and the residue
has never been instrumented.

### Summary

| path | theoretical | board max | measured | % of board max | what closes the gap |
|---|---|---|---|---|---|
| bulk read, quad `0xEB` continuous | **66.5 MB/s** @ 133 MHz | 62.5 MB/s (fabric 125 MHz, the flash PHY's fmax) | **71.70 MB/s** @ SCK 144 — *out of spec* | >100% | nothing; the instrument ran out before the flash did |
| bulk read, quad, **as the SoC is built** | 66.5 MB/s | **15.0 MB/s** @ SCK 30 | 11.27 MB/s @ SCK 36 — **stale, `SYNC_MHZ` has moved** | ~75% | the PHY's /2, then the domain clock |
| bulk read, 1-lane `0x0B` | 16.6 MB/s @ 133 MHz | 15.6 MB/s | 17.95 MB/s @ SCK 144 | >100% | superseded by quad |
| bulk read, 1-lane `0x03` | 6.25 MB/s @ 50 MHz | 6.25 MB/s in spec | 17.96 MB/s @ SCK 144 | 287% | the opcode's rating is not a wall on this board |
| CPU cache-line refill | 62.3 MB/s @ SCK 144 | 12.8 MB/s @ SCK 30 (140-clock model) | 11.27 MB/s equivalent @ SCK 36 | — | continuous read, then the domain clock |
| page program, on the part | **625 KiB/s** (`tPP` 0.4 ms typ) | 625 KiB/s — the chip binds, the board does not | **never measured** (#93) | — | nothing on the board affects it |
| sector erase, on the part | 88.9 KiB/s (`tSE` 45 ms typ) | 88.9 KiB/s | **never measured** (#93) | — | pick the largest erase unit that fits |
| **host → flash programming** | ~109 KiB/s for this image, chip-bound | ~109 KiB/s — transport is the only variable | **17.3 KiB/s** (58,940 B / 3.33 s) | **16%** | **the page loop on the SAMD11 (#100)** |

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
[`../architecture.md`](../architecture.md).

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

Declared in `gateware/board/cynthion_r1_4.py`. All four quad data
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
below that. Two fixes moved it from 131 to 149 (`gateware/probes/qspi/qspi_gateware.py`);
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
this document.

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
| CPU, arbitrary commands | `HoldableSPIController` + `FairSPIControlPortCrossbar` in `gateware/soc/peripherals/flash.py` — **not** luna_soc's, which has two defects here ([`../upstream-boundary.md`](../upstream-boundary.md)) |
| sideband | `scripts/sideband_read.py` |

Boot-image selection, slot layout and the partition work:
[`../chips/ecp5/flash-partitioning.md`](../chips/ecp5/flash-partitioning.md).
Whether quad SPI speeds up configuration:
[`../chips/ecp5/qspi-boot-time.md`](../chips/ecp5/qspi-boot-time.md).

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
[`../architecture.md`](../architecture.md).

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

Ranked against the flash's other options in [`../architecture.md`](../architecture.md).

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
| *"QPI mode can address in as few as 8 clocks"* | **this part has no QPI mode.** The claim is true of the FV and of the JV-IM, not of what is fitted |

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
[`chips/ecp5/bram-budget.md`](../chips/ecp5/bram-budget.md) says is the
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

### What is left, ranked

**Flash — closed.**

| rank | option | worth | effort |
|---|---|---|---|
| ✔ | `FLASH_MODE = "quad"` | **2.70×** measured | done in `03482f4`, for −261 LUTs |
| 1 | replace luna_soc's PHY (`SCK` capped at `sync`/2) | 2× | large, and the only one not gated on the CPU clock |
| 2 | raise `SYNC_MHZ` | 2× at 120 MHz | **gated by the RISC-V Fmax of 75 MHz**, not by the flash |
| 3 | 128-byte I-cache line | +7.2% | a parameter, plus block RAM already at 75% |
| 4 | `0xEB` continuous read in the SoC | +5.1% | small, but the mode is sticky across reconfiguration |
| — | **QPI, DTR, `0xC0`** | **absent on this die** | — |

Bulk quad reads already run at 99.6% of the four-lane theoretical maximum. There
is no efficiency left; only SCK, and the instrument runs out before the flash does.

### Why the clock is on N9, and why that pin is the constrained one

| FPGA pin | function | flash |
|---|---|---|
| T8 | `D0/PICO/IO0/PB11B` | IO0 |
| T7 | `D1/POCI/IO1/PB11A` | IO1 |
| M7 | `D2/IO2/PB9B` | IO2 |
| N7 | `D3/IO3/PB9A` | IO3 |
| N8 | `CSSPI/PB15A` | CS |
| **N9** | **MCLK/CCLK** | **CLK** |

sysCONFIG: *"The MCLK is **always reserved** for use in MSPI mode, in most
post-configuration applications, as the reference clock for performing memory
transactions with the external SPI PROM."* If the FPGA is to configure itself from
this flash, the clock has to be on N9 — there is no alternative ball.

**The asymmetry is in the silicon, not the layout.** The data pins carry dual
designations (`PB11A`, `PB11B`, `PB9A`, `PB9B`) — ordinary bank-8 I/O as well as
MSPI pins — which is why they keep working at full speed after configuration.
MCLK has no alternate function, so it is reachable only through `USRMCLK`
([`ecp5/lfe5u-12f.md`](ecp5/lfe5u-12f.md)).

A board that never boots from flash escapes this entirely: the reservation applies
in *"most"* post-configuration applications, a convention rather than a hardware
rule, and outside MSPI mode even `CSSPIN` reverts to general-purpose I/O. Such a
board could put the flash clock on an ordinary bank pin. Not available here: on
r1.4 the only copper to the flash clock is from N9, so it is a PCB change for a
future revision, and it would cost the recovery path — a board that cannot
configure itself from flash depends entirely on the debug controller.

### Divisor 3 fails while 2 and 4 pass, and that is the build, not the part

The SCK divisor is a bitstream register rather than a build-time constant, so it
sweeps over JTAG without rebuilding — about 30 seconds against roughly five
minutes per point.

Sweeping it found a **non-monotonic** failure: at 120 MHz `sync`, divisor 3
(30 MHz) failed with 22 of 64 bytes wrong, repeatably across three runs, while
both faster divisors (1, 2) and every slower one (4, 5, 7) passed. At 60 MHz
`sync` a *different* divisor failed.

**A failure that moves with the build rather than with SCK is place-and-route
variation on the sample path, not the part.** Addressing it means constraining
that path or sweeping the sample offset per divisor — not clocking slower. Worth
knowing before anyone reads a single failing rung as a device limit.

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

Existing analysis: [`chips/ecp5/qspi-boot-time.md`](../chips/ecp5/qspi-boot-time.md).
