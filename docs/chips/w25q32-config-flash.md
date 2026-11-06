# Winbond W25Q32JVSSIQ: the configuration flash

SPI NOR flash the ECP5 boots from on Cynthion r1.4, U7. 4 MiB. Index: [`../hardware.md`](../hardware.md).

- Evidence trail, old bugs and test methods: [flash-lessons.md](flash-lessons.md).
- Clean-room baseline (no CPU): [#590](https://github.com/awtoau/cynthion-workspace/issues/590); test image: [#591](https://github.com/awtoau/cynthion-workspace/issues/591); SoC path speed: [#100](https://github.com/awtoau/cynthion-workspace/issues/100).
- Datasheets: W25Q32JV Rev G, 2018-03-27 (`~/vendors/winbond/w25q32jv_revg_20180327.pdf`, the revision `repos/cynthion-hardware/bank8_configuration.kicad_sch:8011` names); W25Q32JV-DTR Rev H (`~/vendors/winbond/`) for QPI/DTR timing.

## Part

| item | value | how |
|---|---|---|
| part number | W25Q32JVSSIQ: SOIC-8 208-mil, industrial, QE factory-set | `bank8_configuration.kicad_sch:8011`, `cynthion.kicad_pcb:7294` |
| JEDEC ID | `EF 40 16` (rules out JV-IM/JM `70 16`, JW `60 16` / `80 16`) | `apollo flash-info`; baseline `50d95a9` |
| SR1 / SR2 / SR3 | `00` / `02` (QE = 1) / `60` (DRV 25%, WPS 0) | baseline `50d95a9` (2026-11-03); Aug reads agree |
| unique ID | `355027cba3ac60de` | `apollo flash-info` |
| capacity | 4 MiB: SFDP density, ID byte 0x16, aliasing at 4/8/12 MiB (compared against live bitstream bytes), nothing past 16 MiB | `debris/scripts/flash_capacity_probe.py` |

- Variant check: `EF 40 16` + SR3 present (W25Q32BV has none) + QE = 1 at power-up (IQ/JQ default, §7.1) leaves W25Q32FV or JV-IQ/JQ; the schematic picks JV-IQ.
- QE fixed: IO2/IO3 are always data; no /WP, /HOLD or /RESET; software reset is 66h + 99h (§8.2.35).
- SR3 has no ADS bit (≥256 Mbit only); S23 reserved. DRV: `00` 100%, `01` 75%, `10` 50%, `11` 25% (default); datasheet gives no ohms. Volatile write (50h + 11h) via Apollo background SPI did not take (read back `60`); never tried from fabric.
- One board read. A second has not been checked.

## Wiring on r1.4

| flash pin | ECP5 ball | function | board parts |
|---|---|---|---|
| 1 /CS | N8 | CSSPIN / PB15A | 5.1k pull-up R110, TP11 |
| 5 IO0 | T8 | D0 / PB11B | none |
| 2 IO1 | T7 | D1 / PB11A | none |
| 3 IO2 | M7 | D2 / PB9B | none |
| 7 IO3 | N7 | D3 / PB9A | none |
| 6 CLK | N9 | MCLK / CCLK | 5.1k pull-up R109, TP10 |

- Source: [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 1 (`scripts/flash/flash_board_nets.py` on the baseline branch). No series parts on any net; no pull-ups on IO0-3.
- SoC platform resources (`spi_flash`, `qspi_flash`) in [`cynthion_r1_4.py`](../../gateware/board/cynthion_r1_4.py); `qspi_flash` has no clock: SCK goes through the `USRMCLK` macro.
- N9 has no alternate function (sysCONFIG FPGA-TN-02039: MCLK "always reserved" for MSPI). Data pins are dual-purpose bank-8 I/O. Booting from flash needs CLK on N9; a board that never boots from flash could move it (costs the recovery path).
- Apollo (U6) is on none of these nets. Its only route is ECP5 JTAG background SPI, which is gone once a design instantiates USRMCLK (TN-02039 §6.1.2).
- Bank 8 is the bottom side: no IOLOGIC DDR registers (TN-02035 §5.12.4); pads are fabric-registered.

## USRMCLK (SCK)

- No DDR at CCLK: nextpnr and Diamond 3.14 map both refuse ODDRX1F -> USRMCLK; the site has no DATAMUX_ODDR/IOLDO mux; JA4 has no global-spine source, so a global clock reaches USRMCLKI only through a LUT or FF ([`usrmclk_oddr`](../../scripts/diamond/designs/usrmclk_oddr), `906f0bf`).
- So SCK ≤ the fabric clock that generates it, and the CLK route is fabric, not constrained against /CS or IO.
- User-mode USRMCLK has no fmax, setup/hold or jitter figure (TN-02039 §6.1.2 is the only documentation) and no prjtrellis timing in any grade: only measurement counts. The 62 MHz in the sysCONFIG table is fCCLK (config engine), not a USRMCLK limit.

## FPGA pad attributes

| design | DRIVE | SLEWRATE | PULLMODE | HYSTERESIS | source |
|---|---|---|---|---|---|
| SoC | 16 | FAST | NONE | ON (DQ), unset (CS#) | `FLASH_DRIVE` / `FLASH_SLEWRATE` / `FLASH_PULLMODE` in [`cynthion_r1_4.py`](../../gateware/board/cynthion_r1_4.py) ([#459](https://github.com/awtoau/cynthion-workspace/issues/459)); the baseline values since [#593](https://github.com/awtoau/cynthion-workspace/issues/593) (was 8 SLOW) |
| flash baseline | 16 | FAST | unset | not checked | baseline `gateware/hyperram_baseline/board_r14.py` (design choice, not a board fact) |

- Rated: DRIVE 4/8/12/16 mA in the Trellis PIC bits; 24 mA in the Lattice datasheet, UNVERIFIED.
- Gate: [`test_flash_pin_electricals.py`](../../tests/test_flash_pin_electricals.py).
- DRIVE x SLEWRATE: no build axis since [#593](https://github.com/awtoau/cynthion-workspace/issues/593); the [#590](https://github.com/awtoau/cynthion-workspace/issues/590) baseline compared 16 FAST and 8 SLOW at 190 MHz (eye 8 both, [flash-baseline.md](flash-baseline.md)). A full 4/8/12/16 x SLOW/FAST sweep was never run.

## Datasheet ratings (Rev G §9.6, VCC 3.0-3.6 V on this board)

| item | value |
|---|---|
| fC1, all but 03h | 133 MHz (fC2 104 MHz at 2.7-3.0 V) |
| fR, 03h | 50 MHz |
| tCLQV / tCLQX | 6 ns max / 1.5 ns |
| tSLCH, tCHSL, tCHSH, tSHCH | 3 ns |
| tSHSL1 (after read) / tSHSL2 | 10 ns / 50 ns |
| tPP 256 B | 0.4 typ / 3 max ms |
| tSE 4 KiB | 45 / 400 ms |
| tBE1 32 KiB | 120 / 1600 ms |
| tBE2 64 KiB | 150 / 2000 ms |
| tCE 4 MiB | 10 / 50 s |

- Pin rates at 133 MHz (SDR, one bit per lane per clock): 0Bh 16.6 MB/s, BBh 33.3, EBh 66.5; 03h at 50 MHz 6.25.
- DTR (per the DTR datasheet, for the -IM part): rated 66 MHz SCK; 4 x 2 x 66e6 / 8 = 66 MB/s.
- tPP 0.7 ms (in [#100](https://github.com/awtoau/cynthion-workspace/issues/100)'s body and `repos/apollo` `90c8b7b`) is from the preliminary 2014 datasheet; Rev G says 0.4 ms.

## Read modes and commands

| mode | opcode | address | mode byte | dummy clocks | data |
|---|---|---|---|---|---|
| Read | 03h | 1 line | - | 0 | 1 line (fR 50 MHz) |
| Fast Read | 0Bh | 1 | - | 8 | 1 |
| Dual Output | 3Bh | 1 | - | 8 | 2 |
| Dual I/O | BBh | 2 | M on 2 lines | 0 | 2 |
| Quad Output | 6Bh | 1 | - | 8 | 4 |
| Quad I/O | EBh | 4 (6 clocks) | M, 2 clocks | 4 | 4 |
| DTR Quad I/O | EDh | 4, DDR (4 clocks) | M | 7 (DTR Rev H Table 4) | 4, DDR (a byte per clock) |

- SPI-mode dummy counts are fixed; C0h Set Read Parameters is QPI-only.
- Per 64-byte line at SCK: 03h 544 clocks, 0Bh 552, 6Bh 168, EBh 148, EBh continuous 140.
- **Continuous Read** (EBh M5-4 = 10, 0xA0) works on this die though Rev G documents only M = Fxh. 0xFF leaves it. It survives an FPGA reconfigure; a part left in it reads the next opcode's bits as address/mode and returns real data from the wrong address.
- **QPI and DTR answer on this die** although Rev G (IQ) lists neither (Tables 1/2 have no 38h/FFh/C0h/0Dh/BDh/EDh): after 38h a 4-wide 9Fh returns EF4016 and FFh returns to SPI; EDh read the 1 MiB test region with 0 errors / 1048576 against a fresh seed, stale-seed control 1048576 errors (baseline `50d95a9`, 20 MHz, 2026-11-03). Negative controls and an SFDP read still to do ([flash-lessons.md](flash-lessons.md#items-that-may-explain-the-590-symptoms)).
- 0x77 Set Burst with Wrap: present, default off (W4 = 1); 8/16/32/64-byte wrap, EBh only, persists across /CS; gives critical-word-first, keeps the address phase.

## Device-state hazards

- Continuous Read, QPI, C0h parameters and 0x77 wrap all persist across /CS and FPGA reconfigure.
- A part left in QPI stops the FPGA booting from flash (config engine reads SPI); clear with FFh in QPI or a power cycle.
- Anything that enters a mode leaves it on every exit path: 4-wide FFh, 1-wide FFh with 16 clocks of IO0 high, 66h + 99h, then a 1-wide JEDEC read.

## Flash map

Whole-part EBh scan, 4 KiB sectors holding any non-0xFF byte (baseline, 20 MHz, gateware `73debf2-dirty`, 2026-11-03T15:42+11:00, [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 2): 228 sectors, 885709 non-FF bytes of 4194304.

| range | contents |
|---|---|
| 0x000000-0x03d000 | bitstream (boot slot) |
| 0x0b0000-0x0db000 | SoC firmware (mmap `0x100b0000`) |
| 0x180000-0x199000 | test bitstream, slot 3 (`2b2ada5`) |
| 0x201000-0x263000 | test bitstream, slot 4 (`2b2ada5`) |
| 0x280000-0x37ffff | baseline test region (1 MiB, erased and programmed by the baseline) |
| 0x3ff000-0x400000 | partition table (`2b2ada5`) |

- The baseline test region overlaps planned slots 5/6 in [flash-partitioning.md](ecp5/flash-partitioning.md).
- If the FPGA is configured over USB instead of from flash, the whole 4 MiB is free.

## Measured on the part

Baseline (`50d95a9`, SCK 20 MHz, one pass, 2026-11-03, [#590](https://github.com/awtoau/cynthion-workspace/issues/590) comment 3):

| op | measured | datasheet typ / max |
|---|---|---|
| tPP (02h and 32h, 2048 pages each) | 0.348-0.364 ms | 0.4 / 3 ms |
| tSE | 50-53 ms | 45 / 400 ms |
| tBE1 | 172 ms | 120 / 1600 ms |
| tBE2 | 239-245 ms | 150 / 2000 ms |

- Reads: scan at 10.00 MB/s = 100% of the x4 pin rate at 20 MHz. MB/s is engine bytes / engine cycles, so only the error counts are evidence about the part.
- SCK sweep 20-240 MHz: results go in [#590](https://github.com/awtoau/cynthion-workspace/issues/590). Old probe results (byte-exact to 144 MHz SCK, 2026-08-03) are in [flash-lessons.md](flash-lessons.md#board--part-facts) as a cross-check.

## Paths to the flash

| path | how | state |
|---|---|---|
| host, slow | `apollo flash`: Apollo software JTAG TAP, background SPI | only with no USRMCLK design loaded; `apollo flash-read` of all 4 MiB in one call corrupts past ~1.9 MB (`2b2ada5`) |
| host, fast | `apollo flash --fast`: FlashBridge gateware in SRAM, USB bulk to the fabric | |
| host program cost | 58,940 B at `0xb0000`: 3.33 s = 17.3 KiB/s (`repos/apollo` `90c8b7b`, 2026-08-06); one USB round trip 3.00 ms; part needs ~0.53 s of it | page loop on the SAMD11: [#100](https://github.com/awtoau/cynthion-workspace/issues/100) |
| SoC, memory-mapped | XIP window of [`flash.py`](../../gateware/soc/peripherals/flash.py) on the [#590](https://github.com/awtoau/cynthion-workspace/issues/590) core ([flash-soc-peripheral.md](flash-soc-peripheral.md)) | EBh 1-4-4 per 64 B line at SCK 180 MHz: 148 clocks + setup/hold/gap per line, pin ceiling 90 MB/s; UNVERIFIED on the board ([#593](https://github.com/awtoau/cynthion-workspace/issues/593)) |
| SoC, commands | the same peripheral's command port: WREN + one descriptor transaction + BUSY poll | erase/program/ID/SR/SFDP from `firmware/cynthion-soc/src/flash.rs` |
| sideband | `scripts/sideband_read.py` | |

- Boot slots and partitions: [flash-partitioning.md](ecp5/flash-partitioning.md). Programming paths: [programming-paths.md](ecp5/programming-paths.md).

## Boot configuration

- `ecppack --spimode qspi --freq 62.0` documented; MSPI quad issues EBh (TN-02039 §6.1.4). Divider set {2.4, 4.8, 9.7, 19.4, 38.8, 62} MHz, default 2.4.
- ECP5 does not set QE (ours is factory-set). prjtrellis strips `spimode`/`freq` from SVF: build two bitstreams. SPI Quad not supported on TQFP144 (datasheet rev 3.3).
- External figure: 60 ms configure with `MCCLK_FREQ=62` + quad on an LFE5UM5G-85F (Kouzis-Loukas, 2025-07-15); there 03h worked only to 38.8 MHz.
- Analysis: [qspi-boot-time.md](ecp5/qspi-boot-time.md).

## Not measured / open

- SFDP Basic Flash Parameter Table (5Ah): settles QPI / DTR / 4-4-4 from the die.
- QPI/DTR negative controls on hardware; 0xC0 in QPI.
- DRV write from fabric; FPGA DRIVE x SLEWRATE sweep.
- A second board.
