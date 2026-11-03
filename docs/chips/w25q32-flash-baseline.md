# Config flash baseline (W25Q32JV, gateware only)

Issue: [#590](https://github.com/awtoau/cynthion-workspace/issues/590). Reference test gateware, not product gateware. Fixed reference for [#589](https://github.com/awtoau/cynthion-workspace/issues/589) (flash XIP).

**Rule (Dan, 2026-11-03):** clean room. Designed from the flash datasheets and Lattice ECP5 docs only. From the repo: pinout, the 60 MHz oscillator pin, bank voltage. No repo or upstream SPI/QSPI code was read.

## Sources

| source | used for |
|---|---|
| `~/vendors/winbond/w25q32jv_revg_20180327.pdf` (W25Q32JV rev G) | instruction tables 1/2, Figs 16-24 bit order, AC 9.6, tPP/tSE/tBE, IQ = QE fixed 1, JEDEC EF 4016 |
| `~/vendors/winbond/W25Q32JV_DTR_RevH_20190107.pdf` (W25Q32JV-DTR rev H) | QPI table 3, C0h read parameters, QPI EBh dummies, DTR table 4 (EDh, 66 MHz) |
| `~/vendors/lattice/FPGA-TN-02039-2-3-ECP5-sysCONFIG.pdf` | USRMCLK (6.1.2), MASTER_SPI_PORT persistence default DISABLE, background SPI lost once USRMCLK is used |
| `~/vendors/lattice/FPGA-TN-02200-ECP5-sysCLOCK-PLL-DLL.pdf` | EHXPLLL dynamic phase: PHASESEL/DIR/STEP, step = 1/(8 x DIV) period (18.4.1) |
| `~/vendors/lattice/Lattice-ECP5-HighSpeed-IO-FPGA-TN-02035-1.3.pdf` | bottom side (bank 8) has no DDR registers (5.12.4) |
| r1.4 KiCad schematic, `scripts/flash/flash_board_nets.py` | U7 W25Q32JVSSIQ; IO0..IO3 = T8 T7 M7 N7, /CS = N8 (5.1k pull-up), CLK = N9 MCLK (5.1k pull-up) |

## Wiring and Apollo

- All four IO, /CS and CLK go only to the FPGA (plus pull-ups / test points). Wired modes: x1, dual, quad, QPI.
- Apollo (SAMD11) is on none of the flash nets. Its only path to the flash is ECP5 JTAG background SPI. With USRMCLK instantiated the config engine can no longer use the SPI port (TN-02039 6.1.2), so during the test Apollo cannot drive the bus; the harness is its only JTAG client and uses only the ER1/ER2 register path.

## Design

- Files: `gateware/flash_baseline/{spiflash,top}.py`, pins in `gateware/hyperram_baseline/board_r14.py`; harness `scripts/flash/flash_baseline.py`; sim `scripts/flash/flash_baseline_sim.py`.
- One EHXPLLL from the 60 MHz pin: CLKOP = `sck` (engine + pin registers), CLKOS2 = +180 degrees into USRMCLK (MCLK), CLKOS = read capture with runtime PHASESTEP (delay only). Largest output divider, so a step is 1/(8 x DIV) of a period.
- MCLK is gated by a DCCA (CE from an `sck` register, so it changes while CLKOS2 is low): /CS falls one cycle before the first edge and rises one cycle after the last (tSLCH/tCHSH get a full period, not T/2). Outputs change on `sck` edges = MCLK falling.
- Pins: board_r14 sets LVCMOS33 16 mA FAST (design choice; GSG declares IO_TYPE only); `io8slow` builds 8 mA SLOW for comparison.
- Fabric registers on the pins (bank 8 has no IOLOGIC DDR). Inputs: `cap` flop, `capn` flop, then `sck`; `xfer` picks the path, probed per eye row. `lat` delays the per-cycle sample token by 0..15 cycles.
- Capture placement is probed, not counted: after the eye, step forward probing until half the clean run is seen (`seek`); the eye probe is repeated after placing.
- PHASESTEP is a registered pulse (the PLL latches on its falling edge). LOCK is synchronised before it resets `sck`; STATUS[3] latches a lock loss. ABORT resets the engine after a hang. SEQ counts ops for freshness. BUSY polls end on two consecutive ready bytes.
- Transaction descriptor: opcode (1 or 4 lines), address 24 or 32 bits (address + M7-0 = Fxh), dummy cycles, data width, read/write, length, tail. /CS high >= 10 ns after reads, >= 50 ns after writes, and >= the token line.
- Ops: RAW (one transaction), READ (bursts, seeded checker, 4 KiB-sector non-blank bitmap), WLOOP (per unit: WREN, op, BUSY poll timed in `sck` cycles).
- Pattern: each byte folds every address bit; low lines toggle byte to byte; a 32-bit seed xors each byte; a new seed differs from the last in every byte.

## Simulation

- Model in the sim script, from the datasheets: bit-level per transaction, tCLQV 5 ns, WEL/BUSY with scaled times, page wrap, program ANDs, commands on /CS high, DTR EDh, gated MCLK. QPI is not modelled (38h ignored as rev G says); the board part answers QPI, covered only by the board's seeded check.
- Passes: capture calibration at 4 phases, JEDEC/SR2, erase + blank, x1 and quad program, every read mode (counts, cycles = data + overhead), 32-byte bursts, DTR (lat, pairing), BUSY vs model, no contention, /CS high, a stopped-clock setup cycle on every transaction, bitmap.
- Negative controls fail as required: stale seed (every byte), xor (every byte), one corrupted model byte (1 error at its index, got/want differ by the flipped bit).
- Found by the sim: BBh turnaround contention, traced to the sim clock phases (Simulator default phase), not the gateware.

## How to run

- `./scripts/flash/flash_baseline.py sweep [--sck ...]`: builds what the cache lacks, then per SCK: configure, JEDEC eye (lat), EBh seeded eye, every mode over the 1 MiB test region in one transaction and in 32-byte transactions, controls, DTR eye + read. The first SCK also does ID/QPI/DTR probe, the whole-part scan, and erase + blank check + program with a fresh seed (device-limited: once per run).
- `climb [--start 100]`: 10 MHz steps up, reads only on the programmed region, until two points fail; drive (SR3 DRV) and 8 mA SLOW checks at the limit.
- `diag --sck F [--drv]`: register read-back, op freshness (SEQ), PHASESTEP drift (2 single-write turns + 1 batch turn), SR3 drive eye width.
- `scan --sck 20`: read-only ID + map.
- Log `tmp/logs/flash_baseline.log`; rows `tmp/flash-baseline/results.jsonl`.

## Flash map (read 2026-11-03, before any write)

- Non-blank 4 KiB sectors: 0x000000-0x03d000, 0x0b0000-0x0db000, 0x180000-0x199000, 0x201000-0x263000, 0x3ff000-0x400000.
- **Test region: 0x280000-0x37ffff** (1 MiB, all blank when found). Erase/program touch nothing else.

## Results

Conditions: Cynthion r1.4, U7 W25Q32JVSSIQ (JEDEC EF 4016, SR2 02 = QE, SR3 60 = DRV 25% default, SFDP header ok), bench room temperature, 2026-11-03, one pass per case. Gateware 545fe06 (gated MCLK, registered PHASESTEP, two-byte BUSY poll), pins 16 mA FAST. SCK measured against the 60 MHz pin (checks the PLL ratio only). Region = 1 MiB test region in one transaction; burst32 = 64 KiB in 32-byte transactions. MB/s = bytes / (engine cycles / measured SCK).

Climb (runs 20261103-172252, -175223; `climb-table`). Every row: SEQ fresh, every byte checked; stale-seed, xor and QPI stale-seed controls failed every byte (1048576/1048576):

| SCK MHz | EBh eye (pts) | EBh 1-4-4 MB/s / err | EBh burst32 | 0Bh 1-1-1 | QPI 4-4-4 | lock lost |
|---|---|---|---|---|---|---|
| 100 | 15 | 50.00 / 0 | 33.68 / 0 | 12.50 / 0 | 50.00 / 0 | no |
| 110 | 14 | 55.00 / 0 | 37.05 / 0 | 13.75 / 0 | 55.00 / 0 | no |
| 120 | 13 | 60.00 / 0 | 40.42 / 0 | 15.00 / 0 | 60.00 / 0 | no |
| 130 | 14 | 65.00 / 0 | 43.79 / 0 | 16.25 / 0 | 65.00 / 0 | no |
| 140 | 17 | 70.00 / 0 | 47.16 / 0 | 17.50 / 0 | 70.00 / 0 | no |
| 150 | 15 | 75.00 / 0 | 50.53 / 0 | 18.75 / 0 | 75.00 / 0 | no |
| 160 | 15 | 80.00 / 0 | 53.89 / 0 | 20.00 / 0 | 80.00 / 0 | no |
| 170 | 11 | 85.00 / 0 | 57.26 / 0 | 21.25 / 0 | 85.00 / 0 | no |
| 180 | 9 | 90.00 / 0 | 60.00 / 0 | 22.50 / 0 | 90.00 / 0 | no |
| **190** | 8 | **95.00 / 0** | 63.33 / 0 | 23.75 / 0 | 95.00 / 0 | no |
| 200-260 | 0 | no JEDEC eye at any phase/lat/xfer | - | - | - | no |

- EBh eye = clean points of 32 (16 phase columns x 2 XFER rows) at the chosen lat row group; it narrows from 15-17 (100-160) to 8 (190).
- Earlier sweeps (20-80 MHz, gateware 50d95a9, same reads): all modes 100% of pin rate, 0 errors; 3Bh/BBh/6Bh match 0Bh/EBh scaling.
- Burst32 throughput = 64 data cycles + 20 (EBh) or 14 (QPI) opcode/address/dummy + 2 SETUP/HOLD + /CS-high gap per 32 bytes.
- DTR (EDh, rated 66 MHz): 0 errors / 1048576 at 20, 50, 80 MHz (stale control fails every byte); no clean capture at >= 100 MHz, EDh returns constant 0x00 there. MB/s for DTR is the engine's own cycle count; only the error count is evidence.

### Where it stops (200 MHz and up)

- PLL locks at every point (lock lost never set); no op hangs (STATE 0, every op done); SEQ fresh.
- `probe --sck 200`: 9Fh returns the same bit stream shifted one bit per lat (0xf02000, 0xe04000, 0xc08000, ...), never EF4016 at any phase. The capture works (consistent shifts); **the flash does not decode the command**: write-side timing (data vs MCLK at the flash, fixed at +180 degrees) fails, not read capture, not the fabric.
- Drive: at 190, SR3 DRV 100% (volatile) eye 9 vs 8 at 25%, region read 0 errors either way; at 150, 36 vs 34 of 40 steps. At 200 no eye at either drive. 8 mA SLOW pins at 190: pass, eye 8 (same as 16 mA FAST).
- Next lever (not done): MCLK phase adjustable (CLKOS2 on PHASESEL 01, or static per build) to re-centre the flash's input window above 190 MHz.

### Program / erase (device-limited; measured once per run at the first SCK)

| op | n | min ms | avg ms | max ms | datasheet typ / max |
|---|---|---|---|---|---|
| page program 02h (256 B) | 2048 | 0.345 | 0.352 | 0.362 | 0.4 / 3 |
| quad page program 32h | 2048 | 0.346 | 0.352 | 0.362 | 0.4 / 3 |
| sector erase 20h (4 KiB) | 8 | 52.4 | 54.2 | 56.1 | 45 / 400 |
| block erase 52h (32 KiB) | 1 | 156.5 | 156.5 | 156.5 | 120 / 1600 |
| block erase D8h (64 KiB) | 15 | 219.0 | 238.1 | 250.5 | 150 / 2000 |

- Run 20261103-170624 at 100 MHz; earlier runs at 20-160 MHz agree within a few %. tBE1/tBE2 are 1.3x/1.6x datasheet typical; tSE 1.2x; tPP 0.9x. All far under max.

### Bugs found and fixed on the way (details on #590)

- GO used a rising-edge detector while the harness toggled it: every other op never ran.
- Capture phase was counted, not measured; PHASESTEP was combinational and glitched, so the count drifted ~6-8 steps per 64-step turn at div 8. Placement after a wrap was a cycle off (the 100/132 MHz "failures"). Fixed: registered PHASESTEP (drift 0 at div 5/6/10), probed placement and XFER.
- /CS setup/hold was T/2 with a free-running MCLK; now a gated MCLK gives a full cycle.
- A hung BUSY poll held the engine for the rest of the load (now ABORT); a single misread ready byte could end a poll (now two); LOCK reached the reset unsynchronised.

## Gap vs rated

| gap | size | reason |
|---|---|---|
| quad 133 MHz rated -> 190 measured | +43% | overclock; limit is the fixed MCLK phase at the flash's inputs |
| 32-byte bursts | 66-71% of pin at 190 | 20-22 cycles of opcode/address/dummy + 2 SETUP/HOLD + /CS gap per 64 data cycles; continuous read (M5-4 = 10) would drop the 8-cycle opcode |
| DTR | stops between 80 and 100 MHz | rated 66 MHz; at 66 MHz DTR = 66 MB/s = SDR quad at 133 MHz |

## Notes

- Not measured: repeats beyond 2, temperature, other boards, MCLK phase other than 180 degrees, continuous read mode, DTR above 80 MHz with a finer eye.
- The JTAG register path (LUNA `JTAGRegisterInterface`, #204) read back clean in diag (0/300 and 0/100 per SCK); it is still to be replaced (#591).
