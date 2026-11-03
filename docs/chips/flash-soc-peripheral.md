# Config flash SoC peripheral (#593)

Source: [`gateware/soc/peripherals/flash.py`](../../gateware/soc/peripherals/flash.py). Driver: [`firmware/cynthion-soc/src/flash.rs`](../../firmware/cynthion-soc/src/flash.rs). Shell: `flash` ([`shell/memory.rs`](../../firmware/cynthion-soc/src/shell/memory.rs)). Sim: [`scripts/soc/flash_peripheral_sim.py`](../../scripts/soc/flash_peripheral_sim.py).

## Shape
- Core, PHY: the #590 clean-room baseline ([`gateware/flash_baseline/`](../../gateware/flash_baseline/spiflash.py), [flash-baseline.md](flash-baseline.md)): `TxEngine` and `FlashPHY` used as they are.
- SCK 190 MHz (#590 decision). PLL B (the die has two, [lfe5u-12f.md](ecp5/lfe5u-12f.md)): CLKOP `flash` (engine, pin registers), CLKOS `flash_cap` (capture, runtime PHASESTEP, 32 steps per period), CLKOS2 +180 degrees through a DCCA gate into USRMCLK.
- PLL A now carries `hr`, `hr90` and `sync` (`clocks.py`): 190 shares no 400-800 MHz VCO with 60 or 180. `sync` must divide PLL A's VCO (720 MHz at CK 180).
- Pins: `qspi_flash` io0-io3 (one OE each) + /CS, LVCMOS33 16 mA FAST, PULLMODE NONE.
- Windows:
  - XIP at `0x10000000`, 4 MiB, read-only, `main=1,exe=1` PMA
  - CSRs at `0xf0000100` (0x80)
  - command buffer at `0xf0011000`, 4 KiB: dual-clock EBR, CPU port `sync`, engine byte port `flash`
- IRQ 19, level, gated by `IRQ_EN`.
- Engine serves one request at a time: an XIP fill, then a command.

## XIP
- A VexiiRiscv L1 refill is 16 beats, line-aligned, in order, `cyc` held (FetchL1Bus / LsuL1Bus `toWishbone`).
- Beat 0 of a line not streaming: one EBh 1-4-4 transaction (M = F0h, no continuous read) of 64 B. Words cross by a 16-deep AsyncFIFO; each beat ACKs (combinationally) when its word is there.
- A beat ahead of the stream skips words; a beat for another line drains the rest of the current fill first. No line is kept after its 16th word: every refill reads the part.
- ERR (and a sticky bit) instead of a wait: not calibrated (`xip_uncal`), a command pending (`xip_busy`), a store (`xip_write`), a fill whose transaction hit its watchdog (`xip_fill`).
- Code that runs while a command is pending must not be in flash: the driver's wait loop is `.data`, interrupts masked.

## Command port
- Write operands, write CMD, poll `STATUS.busy` (no Wishbone wait states).
- Ops:
  - 1 READ: EBh `LEN` (1..4096) bytes at `ADDR` into the buffer at `BUF_ADDR`
  - 2 PROGRAM: WREN, SR1.WEL check, 02h `LEN` (1..256, within one page) from the buffer, BUSY poll
  - 3 ERASE_4K (20h), 4 ERASE_64K (D8h): WREN, WEL check, erase, BUSY poll
  - 5 JEDEC (9Fh, RESULT[23:0]); 6 STATUS (RESULT = SR3 << 16 | SR2 << 8 | SR1); 7 WREN (+ WEL check)
  - 8 CALIBRATE; 9 RESET (reset sequence below, tRST)
- BUSY poll ends on two consecutive ready bytes; it must see BUSY set at least once (`not-started` otherwise).
- Erase/program span must lie in `[WRITE_LO, WRITE_HI)` (reset 0/0: nothing writable). The driver sets the #590 test region `0x280000-0x37ffff`.
- `CTRL.abort` resets the engine (not the PHASESTEP counter): reset sequence, then calibration.

## Reset sequence and calibration
- After PLL lock (synchronised, sticky lock loss), and after abort or a watchdog:
  - 8 CLK of 1s on 4 lines (ends EBh continuous read), FFh 4-wide (QPI exit), 66h+99h 4-wide, 66h+99h 1-wide, wait tRST
- Calibration, automatic after that, rerun by CALIBRATE:
  - pass 1: every (xfer 0/1, lat 0..15, phase 0..31) probed with 9Fh == EF4016; row masks (32 bits per (xfer, lat), x 16 + lat) to buffer 0x000
  - pass 2: 0Bh 1-1-1 read of 64 B at `CAL_ADDR` (reset 0, the bitstream header) at the 9Fh centre = reference (buffer 0x100); every line must show both levels (`cal-data` otherwise); every point probed with EBh against it; row masks to buffer 0x080
  - each pass: widest clean run on the axis `lat x 32 + phase` per xfer, centre placed, then 4 verify probes (pass 2 alternates EBh and 9Fh)
- PHASESTEP: a registered pulse (4 low, 4 high, 8 settle `flash` cycles), delay direction only; a seek is a count of steps from a reset-less position counter, and every placement is verified by probing.

## Registers (8-bit CSR; multi-byte low byte first; footprints powers of two)
| off | name | acc | content |
|---|---|---|---|
| 0x00 | ID | r | `FLS2` 0x464c5332 |
| 0x08 | STATUS | r | [0] busy [1] ready (calibrated + locked) [2] locked [3] init (reset done) [4] xip_active [5] eng_busy; [11:8] code [15:12] op |
| 0x0c | CMD | w | op |
| 0x0d | CTRL | w | [0] abort |
| 0x0e | EVENTS | rw1c | [0] done [1] error |
| 0x0f | IRQ_EN | rw | [0] done [1] error |
| 0x10 | ERRORS | rw1c | sticky bits, below |
| 0x14 / 0x18 / 0x1a | ADDR / BUF_ADDR / LEN | rw | [21:0] / [11:0] / [12:0] |
| 0x1c | RESULT | r | JEDEC, STATUS, or the last poll byte |
| 0x20 / 0x22 | WRITE_LO / WRITE_HI | rw | erase/program window, 4 KiB sectors |
| 0x28 | CAL_ADDR | rw | calibration reference address |
| 0x2c | CAL | r | EBh placement: [8:0] centre (lat x 32 + phase) [23:16] eye width [24] xfer [31] valid |
| 0x38 | BUSY_CYCLES | r | `flash` cycles of the last BUSY poll |
| 0x3c / 0x3e | STATE / SEQ | r | engine state at a watchdog / commands finished |
| 0x40 | CK_COUNT | r | `flash` cycles per 2^19 periods of the 60 MHz oscillator (1660245 at 190); read, not checked, in gateware |
| 0x48 / 0x4a | FILL_LAST / FILL_MAX | r | sync cycles from an XIP fill's request to its 16th word |

## Codes and errors
- Codes: 0 ok, 1 busy, 2 arg, 3 not-ready, 4 uncalibrated, 5 protect, 6 wel, 7 watchdog, 8 handshake, 9 not-started, 10 cal-none, 11 abort, 12 cal-data, 13 cal-verify.
- ERRORS: 0 op, 1 wdog, 2 xip_uncal, 3 xip_busy, 4 xip_write, 5 handshake, 6 pll_lost, 7 pll_nolock, 8 cal, 9 not_started, 10 xip_fill.

## Bounds (1.25x the derived expectation; the source has each)
| wait | expected | expiry |
|---|---|---|
| one transaction | data cycles + 64 (TxEngine worst overhead) | WDOG: tx reset, STATE latched, reset sequence + recalibration |
| BUSY poll | tPP 3 ms / tSE 400 ms / tBE2 2 s (rev G 9.6 max) | WDOG |
| reset | tRST 30 us | (fixed wait) |
| XIP beat (bus) | drain of a fill + a new fill + crossings (`xip_wait_bound`, 175 at sync 60) | BusFault ERR (`top.BUS_TIMEOUT_CYCLES`) |
| command handshake | 8 sync cycles | HANDSHAKE |
| PLL lock | tLOCK2 15 ms (DS-02012) | pll_nolock |
| firmware polls (`flash.rs`) | the gateware bound + 64 us | `TIMEOUT` |
| boot (`cynthion-boot`) | tLOCK2 + tRST + calibration | jumps anyway |

## Firmware
- `cynthion-boot` waits for `STATUS.ready` before jumping to the image in flash.
- `flash status|read|regs|wren|erase <a> [64k]|program <a> <seed>|test [a] [seed]|cal|reset`. `flash test` erases one 64 KiB block of the test region, checks blank, programs 256 pages, verifies by command READ and by XIP (after evicting the D-cache), and runs a stale-seed control.
- `bench flash` prints the D-cache refill cost from the CPU's counters (waiting cycles per load miss).

## Results
- Measured figures: #593.
