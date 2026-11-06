# Config flash: the SoC peripheral ([#593](https://github.com/awtoau/cynthion-workspace/issues/593))

The W25Q32JV config flash on the SoC bus, built on the clean-room [#590](https://github.com/awtoau/cynthion-workspace/issues/590) core. Issue: [#593](https://github.com/awtoau/cynthion-workspace/issues/593). Part facts: [w25q32-config-flash.md](w25q32-config-flash.md); core and board results: [flash-baseline.md](flash-baseline.md).

## Files

| file | what |
|---|---|
| [`gateware/soc/peripherals/flash.py`](../../gateware/soc/peripherals/flash.py) | `Flash`: CSRs, XIP line buffer, command engine, calibration |
| [`gateware/flash_baseline/spiflash.py`](../../gateware/flash_baseline/spiflash.py) | the core: `TxEngine`, `FlashPHY`, `FlashPins`, `desc()` (imported, unchanged) |
| [`gateware/soc/clocks.py`](../../gateware/soc/clocks.py) | `SocClocks(flash_mhz=)`: `fsck`, `fcap`/`fcapn`, MCLK on PLL0 |
| [`firmware/cynthion-soc/src/flash.rs`](../../firmware/cynthion-soc/src/flash.rs) | driver: JEDEC, SR1, SFDP, 0Bh word, sector erase, page program, calibrate |
| [`scripts/soc/soc_flash_sim.py`](../../scripts/soc/soc_flash_sim.py) | sim against the [#590](https://github.com/awtoau/cynthion-workspace/issues/590) datasheet model |

## Clocks

- SCK **180 MHz**, not the 190 of [#590](https://github.com/awtoau/cynthion-workspace/issues/590): two PLLs on the die (HyperRAM owns one), so the flash shares PLL0's VCO with `sync` 60. LCM(60, 190) = 1140 MHz > VCO max 800; 190 with in-spec fPFD needs `sync` off 60 (CLKOP = 190 as feedback, `sync` = 57 on CLKOS). 180: VCO 720, div 4, 32 PHASESTEPs per period. [#590](https://github.com/awtoau/cynthion-workspace/issues/590) climb at 180: EBh 0 errors, eye 9 points vs 8 at 190.
- PLL0: CLKOP `sync`, CLKOS `fcap` (PHASESEL 00, delay), CLKOS2 `fsck`, CLKOS3 MCLK +180 degrees. DPHASE_SOURCE enabled.
- `fsck` reset: `~LOCK` through a `ResetSynchronizer`. PHASESTEP registered in `fsck`, low 4 / high 4 cycles.
- MCLK: CLKOS3 -> DCCA (CE = registered `ck_en`, changes on `fsck` edges = MCLK falling) -> USRMCLK.
- Pins: DRIVE 16, SLEWRATE FAST, PULLMODE NONE ([`cynthion_r1_4.py`](../../gateware/board/cynthion_r1_4.py)); the variant axes `CYNTHION_FLASH_DRIVE`/`_SLEWRATE` are gone.

## Windows

| window | base | size | what |
|---|---|---|---|
| `spiflash` | `0x10000000` | 4 MiB | XIP, read-only, cached by the CPU (`main=1 exe=1`) |
| `spi0` | `0xf0000100` | 64 B | command CSRs (below) |
| `spi0_buf` | `0xf0000200` | 256 B | command data: reads = received bytes, writes = bytes to send; little-endian words |

## XIP

- Miss: one EBh 1-4-4 transaction (mode byte F0h, no continuous read) for the aligned 64 B line; words acked as they land (16-word line buffer in `sync`).
- Hit: next cycle. Any command's done invalidates the line (deferred past a fill in flight).
- ERR, at once, with a sticky bit: stores (`xip_write`); loads while a write-class command (WREN or BUSY poll) or calibration runs, or with no valid calibration (`xip_blocked`); a fill past its watchdog (`xip_fill`).
- Worst legitimate wait: one maximal command transaction then the line, `xip_worst_sync_cycles` = 787 sync cycles at 60/180. Fill watchdog 1.25x = 984; `BUS_TIMEOUT_CYCLES` = 1.25 x (787 + 4) = 989, so the peripheral's own error lands first.
- CPU held in reset (`boot_ready`) until the first calibration finishes: `.text` executes from the window.

## Command port

- `desc` is the core's transaction descriptor (`desc()` in spiflash.py: opcode, address bits 0/24/32, widths, dummy, read, tail). One command = [WREN] + one transaction + [BUSY poll].
- `flags.wren`: WREN first. `flags.wait`: poll SR1 after (ends on two consecutive ready bytes), bounded by `wait_us`; `poll_us` and `status.saw_busy` report it.
- /CS high after a transaction: 10 ns after reads, 50 ns after writes (tSHSL1/2), in `fsck` cycles.
- Transaction watchdog: `TX_LIMIT` = 1.25 x the longest non-poll transaction (2155 cycles) = 2694 `fsck` cycles.
- Sync-side backstop: `wait_us` x 1.25 + 64 us; CALIBRATE uses the scan's own bound (`cal_us`).

## Registers (`spi0`, byte offsets)

| off | name | access | fields |
|---|---|---|---|
| 0x00 | id | R32 | `FLS1` = 0x464C5331 |
| 0x04 | sck_khz | R32 | 180000 |
| 0x08 | status | R16 | busy 0, ready 1, locked 2, cal_valid 3, cal_x4 4, saw_busy 5, code 11:8, op 15:12 |
| 0x0c | cmd | W8 | op: 1 RUN, 2 CALIBRATE |
| 0x0d | ctrl | W8 | abort 0 |
| 0x0e | events | RW1C | done 0, error 1 |
| 0x0f | irq_en | RW | per event; IRQ 19, level |
| 0x10 | errors | RW1C | see below |
| 0x14 | desc | RW32 | transaction descriptor |
| 0x18 | addr | RW24 | flash byte address |
| 0x1c | len | RW9 | data bytes, <= 256 |
| 0x1e | flags | RW | wren 0, wait 1 |
| 0x20 | wait_us | RW32 | BUSY poll bound; 0 with `wait` is ARG |
| 0x24 | poll_us | R32 | last poll, us |
| 0x28 | cal | R32 | step 7:0, lat 11:8, xfer 12, width_x1 23:16, width_x4 31:24 |
| 0x2c | steps | R8 | PHASESTEPs per period |
| 0x30 | seq | R16 | commands finished (refusals not counted) |
| 0x32 | state | R16 | engine state + core phase at the last watchdog/abort |
| 0x34 | xip_fills | R32 | line fills issued |
| 0x38 | cal_us | R16 | calibration bound, us |

- Codes: 0 OK, 1 BUSY, 2 ARG, 3 NOT_READY, 7 WDOG, 8 HANDSHAKE, 11 ABORT, 13 CAL_NONE.
- Errors: 0 op (any non-OK), 1 wdog, 2 cal, 3 xip_blocked, 4 xip_write, 5 xip_fill, 6 pll_lost, 7 handshake.
- Multi-byte CSRs: low byte first (amaranth-soc shadow latch/commit).

## Calibration

- After lock, and on CALIBRATE. Scan order: xfer (outer), lat, phase step (inner, a full turn per lat row); `key = lat x N + step` is contiguous capture time within an xfer.
- x1: 9Fh = EF4016 at lat 0..7 x 2 xfer x N steps. Centre of the widest run (>= 2 points), placed by stepping, verified once.
- Reference: 64 B 0Bh (x1, IO1 only) at `0x0B0000` (the firmware image) at the x1 point.
- x4: EBh of the same 64 B against the reference, lat x1 +- 1 x 2 xfer x N. Centre placed, verified. The final point serves XIP and commands.
- A blank or constant reference (AND != 00 or OR != FF over the 64 bytes) cannot tell lines apart: x1 placement kept, `cal_x4` 0.
- Bound: `cal_cycles(32)` = 68317 `fsck` cycles = 380 us at 180 MHz.

## Simulation (`soc_flash_sim.py`)

- Model: `flash_baseline_sim.W25Q32JV`; data invalid tCLQX 1.5 ns .. tCLQV 5 ns after MCLK falls; fsck 90 MHz, N = 16, fcap delayed T/N per PHASESTEP falling edge.
- Covers: boot calibration (x1 + x4, PHASESTEPs seen = placed step), JEDEC/SR1, XIP byte order and one EBh per line, hits, line invalidation, erase/program (WREN, op, poll each one /CS window; poll time vs model), 0Bh readback, store/blocked/ARG/BUSY refusals, poll watchdog, abort, IRQ + W1C, recalibration, XIP and commands interleaved, no contention, /CS setup cycle, /CS high.
- Negative controls: silent part (no eye, XIP ERR, CALIBRATE recovers), blank reference (x4 off), wrong bench expectation.
- Says nothing about pad timing, the real eye at 180 MHz or the PLL's phase step size.

## Old controller (removed)

- luna_soc `spiflash` subclasses (memory map, PHY copy, controller with CS hold, fair crossbar, pin probe), `flash_cdc.py`, `flash_sck_full.py`. Defects and evidence: [flash-lessons.md](flash-lessons.md), [upstream-bugs.md](../upstream-bugs.md).
- Their behaviours here: CS hold = one descriptor per /CS window; modal read = `desc`; fair crossbar = round robin between XIP and command in the engine; pin probe = sim counters (no hardware counterpart).
