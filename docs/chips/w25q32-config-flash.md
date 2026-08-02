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
| status register 3 | `0x60`, ADS clear | no 4-byte addressing | `scripts/flash_capacity_probe.py` |

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

## The measured ceiling is the ECP5 pin, not the flash

The flash is rated to **104 MHz**. The ECP5 path driving it is specified only to
**62 MHz**, because `MCLK` never stops being a configuration pin — it is tristated
into the configuration block on entering user mode, and the only route to it is
`USRMCLK`. Measured:

| SCK | vs the 62 MHz spec | result |
|---|---|---|
| 40 MHz | within | PASS |
| 53.3 MHz | within | PASS, 26.53 MB/s |
| 80 MHz | +29% over | PASS, three runs, byte-exact |
| 120 MHz | +93% over | FAIL |
| 160 MHz | +158% over | FAIL |

**No DDR.** The datasheet contains no DTR opcodes; DDR reads belong to the W25Q-DTR
family, which this is not. Its "equivalent 208/416 MHz" claim is lane parallelism,
not double-edge clocking. Genuine DDR on this board is the HyperRAM.

Full speed table, read modes, clock domains and the bugs found getting there:
[`../luna_ecp5_fpga/flash-detailed.md`](../luna_ecp5_fpga/flash-detailed.md).

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

## Not measured

**Write and erase timing.** Everything above is reads (#93).

## Scripts

| | |
|---|---|
| `scripts/flash_capacity_probe.py` | JEDEC, SFDP, aliasing — read-only |
| `scripts/flash_backup.py` | full image backup |
| `scripts/flash_speed_ladder.py`, `flash_modes.py`, `qspi_ladder.py` | speed and mode characterisation |
| `scripts/test_flash_id.py` | JEDEC read |
| `apollo flash-info` | JEDEC and unique ID |
