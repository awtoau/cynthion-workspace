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
| status register 3 | `0x60`, ADS clear, **DRV 25%** | no 4-byte addressing; weakest output drive | `scripts/flash_ceiling.py --status` |

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
**144 MHz SCK** — 38% past the part's 104 MHz rating and 132% past the 62 MHz
Lattice specifies for `MCLK`. Stating that once: the top of this table is past
both ratings and there is no margin figure to reason from.

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

**No DDR.** The datasheet contains no DTR opcodes; DDR reads belong to the W25Q-DTR
family, which this is not. Its "equivalent 208/416 MHz" claim is lane parallelism,
not double-edge clocking. Genuine DDR on this board is the HyperRAM.

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
- Against the SoC's current 30 MHz single-lane, 18.1 µs per line, `0xEB`
  continuous at 144 MHz is **17.6× faster**.

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

- **QE (SR2 bit 1) is already set.** Quad needs no write to this part, and
  setting it would have cost hardware write protection by repurposing /WP and
  /HOLD as IO2 and IO3.
- **Output drive is 25%, the default, and it does not need raising.** 100% is
  available and writable *volatile*, but nothing fails at 25% up to 144 MHz, so
  there is no failure for a stronger driver to fix. A volatile write attempted
  through Apollo's background SPI (`0x50` then `0x11`) **did not take** — SR3
  read back unchanged at `0x60`. The part was not modified.
- **No dummy-cycle register applies.** `0xEB` in SPI mode is fixed at 4 dummy
  clocks after the 2-clock mode byte. The configurable count (`0xC0` Set Read
  Parameters) is a QPI-mode command, and this design does not use QPI.

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
domain so the fabric need not run at SCK. An `ODDRX1F` cannot do it: nextpnr
refuses one whose `Q` drives anything but a top-level output, and `USRMCLK` is
not one.

## Scripts

| | |
|---|---|
| `scripts/flash_capacity_probe.py` | JEDEC, SFDP, aliasing — read-only |
| `scripts/flash_backup.py` | full image backup |
| `scripts/flash_ceiling.py` | **the current one.** Bitstream ladder, verified SCK sweep, cache-line refill, status registers |
| `scripts/flash_speed_ladder.py`, `flash_modes.py`, `qspi_ladder.py` | earlier speed and mode characterisation |
| `scripts/test_flash_id.py` | JEDEC read |
| `apollo flash-info` | JEDEC and unique ID |
