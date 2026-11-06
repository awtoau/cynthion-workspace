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

- SCK **120 MHz** (2 x `sync`, in spec; part rated 133). 180 and 190 fail on the board ([#593](https://github.com/awtoau/cynthion-workspace/issues/593)). Two PLLs on the die (HyperRAM owns one), so the flash shares PLL0's VCO with `sync` 60. LCM(60, 190) = 1140 MHz > VCO max 800; 190 with in-spec fPFD needs `sync` off 60 (CLKOP = 190 as feedback, `sync` = 57 on CLKOS). 180: VCO 720, div 4, 32 PHASESTEPs per period. [#590](https://github.com/awtoau/cynthion-workspace/issues/590) climb at 180: EBh 0 errors, eye 9 points vs 8 at 190.
- PLL0: CLKOP `sync`, CLKOS `fcap` (PHASESEL 00, delay), CLKOS2 `fsck`, CLKOS3 MCLK +180 degrees. DPHASE_SOURCE enabled.
- `fsck` reset: `~LOCK` through a `ResetSynchronizer`. PHASESTEP registered in `fsck`, low 4 / high 4 cycles.
- MCLK: CLKOS3 -> DCCA (CE = registered `ck_en`, changes on `fsck` edges = MCLK falling) -> USRMCLK.
- Pins: DRIVE 16, SLEWRATE FAST, PULLMODE NONE ([`cynthion_r1_4.py`](../../gateware/board/cynthion_r1_4.py)); the variant axes `CYNTHION_FLASH_DRIVE`/`_SLEWRATE` are gone.

## Windows

| window | base | size | what |
|---|---|---|---|
| `spiflash` | `0x10000000` | 4 MiB | XIP, read-only, cached by the CPU (`main=1 exe=1`) |
| `spi0` | `0xf0021000` | 64 B | command registers (below) |
| `spi0_buf` | `0xf0021100` | 256 B | command data: reads = received bytes, writes = bytes to send; little-endian words |

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
- CALIBRATE is bounded by the scan itself; `cal_us` reports the bound for the CPU.

## Registers (`spi0`, 32-bit words, byte offsets)

Plain Wishbone words (`REG_*` in flash.py), not amaranth-soc CSRs: the byte-wide CSR multiplexer cost more cells than the registers.

| off | name | access | fields |
|---|---|---|---|
| 0x00 | status | R | busy 0, ready 1, locked 2, cal_valid 3, cal_x4 4, saw_busy 5, code 11:8, op 15:12, events 17:16 (done, error), errors 30:24 |
| 0x04 | cmd | W | op 3:0: 1 RUN, 2 CALIBRATE |
| 0x08 | ctrl | W | abort 0; write 1s to bits 17:16 to clear events, 30:24 to clear errors |
| 0x0c | irq_en | RW | per event 1:0; IRQ 19, level |
| 0x10 | desc | RW | transaction descriptor; bits 9, 16, 23 forced 0 (no QPI opcode, dual or DTR) |
| 0x14 | addr | RW | flash byte address 23:0 |
| 0x18 | len | RW | data bytes 8:0 (<= 256), wren 16, wait 17 |
| 0x1c | wait_us | RW | BUSY poll bound 23:0; 0 with `wait` is ARG |
| 0x20 | poll_us | R | last poll, us |
| 0x24 | cal | R | step 7:0, lat 11:8, xfer 12, width_x1 23:16, width_x4 31:24 |
| 0x28 | cal_us | R | calibration bound, us |

- Codes: 0 OK, 1 BUSY, 2 ARG, 3 NOT_READY, 7 WDOG, 11 ABORT, 13 CAL_NONE.
- Errors: 0 op (any non-OK), 1 wdog, 2 cal, 3 xip_blocked, 4 xip_write, 5 xip_fill, 6 pll_lost.
- No sync-side backstop: the engine bounds every wait; the CPU's own bound in `flash.rs` covers an fsck clock that stops.

## Calibration

- After lock, and on CALIBRATE. Scan order: xfer (outer), lat, phase step (inner, a full turn per lat row); `key = lat x N + step` is contiguous capture time within an xfer.
- x1: 9Fh = EF4016 at lat 0..7 x 2 xfer x N steps. Centre of the widest run (>= 2 points), placed by stepping, verified once.
- Reference: 64 B 0Bh (x1, IO1 only) at `0x0B0000` (the firmware image) at the x1 point.
- x4: EBh of the same 64 B against the reference, lat x1 +- 1 x 2 xfer x N. Centre placed, verified. The final point serves XIP and commands.
- A blank or constant reference (AND != 00 or OR != FF over the 64 bytes) cannot tell lines apart: x1 placement kept, `cal_x4` 0.
- Bound: `cal_cycles(32)` = 68317 `fsck` cycles.

## Simulation (`soc_flash_sim.py`)

- Model: `flash_baseline_sim.W25Q32JV`; data invalid tCLQX 1.5 ns .. tCLQV 5 ns after MCLK falls; fsck 90 MHz, N = 16, fcap delayed T/N per PHASESTEP falling edge.
- Covers: boot calibration (x1 + x4, PHASESTEPs seen = placed step), JEDEC/SR1, XIP byte order and one EBh per line, hits, line invalidation, erase/program (WREN, op, poll each one /CS window; poll time vs model), 0Bh readback, store/blocked/ARG/BUSY refusals, poll watchdog, abort, IRQ + W1C, recalibration, XIP and commands interleaved, no contention, /CS setup cycle, /CS high.
- Negative controls: silent part (no eye, XIP ERR, CALIBRATE recovers), blank reference (x4 off), wrong bench expectation.
- Says nothing about pad timing, the real eye at 180 MHz or the PLL's phase step size.

## Old controller (removed)

- luna_soc `spiflash` subclasses (memory map, PHY copy, controller with CS hold, fair crossbar, pin probe), `flash_cdc.py`, `flash_sck_full.py`. Defects and evidence: [flash-lessons.md](flash-lessons.md), [upstream-bugs.md](../upstream-bugs.md).
- Their behaviours here: CS hold = one descriptor per /CS window; modal read = `desc`; fair crossbar = round robin between XIP and command in the engine; pin probe = sim counters (no hardware counterpart).

## Board status (2026-11-06)

- Flash side works on the board (builds [#438](https://github.com/awtoau/cynthion-workspace/issues/438)-[#442](https://github.com/awtoau/cynthion-workspace/issues/442)): 1-line eye 16-18 steps, 4-line eye 19-20 of 32 per lat, placed lat 4 xfer 1, JEDEC EF4016; `flash status` answers when the CPU runs.
- **HyperRAM fails with this peripheral in the SoC**: auto-calibration finds no clean tap of 128 (once also an underflow, once an XFER watchdog) on all 4 builds where the CPU ran. The previous design (0cd05bd, 20781 COMB) passes HyperRAM 13/13 and soc_test 132/132 on the same board the same day.
- COMB 21878 (was 20781); the CPU is silent on some placements. Not yet separated: logic-cell pressure vs the extra 180 MHz clocks and 16 mA FAST flash pins. Open on [#593](https://github.com/awtoau/cynthion-workspace/issues/593).
