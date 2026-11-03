# HyperRAM SoC peripheral (#588)

Source: [`gateware/soc/peripherals/hyperram.py`](../../../gateware/soc/peripherals/hyperram.py). Driver: [`firmware/cynthion-soc/src/hyperram.rs`](../../../firmware/cynthion-soc/src/hyperram.rs). Shell: `hr` ([`shell/hr.rs`](../../../firmware/cynthion-soc/src/shell/hr.rs)). Sim: [`scripts/soc/hyperram_peripheral_sim.py`](../../../scripts/soc/hyperram_peripheral_sim.py).

## Shape
- The core, PHY and PLL come from [`gateware/hyperram_baseline/`](../../../gateware/hyperram_baseline/README.md) (#581):
  - CK 180 MHz on `hr`/`hr90`, a second PLL off the 60 MHz `usb` net
  - core flags `reg_write` and `masked` are on
- No CPU address for HyperRAM. Two windows instead:
  - CSR registers at `0xf0000800` (0x80)
  - a 4 KiB data window at `0xf0010000`: dual-clock EBR, CPU port in `sync`, HyperRAM port in `hr`; no bus crosses a domain
- Commands are CSR writes, then poll `STATUS.busy` (no Wishbone wait states). IRQ 18 is level, gated by `IRQ_EN`.
- Crossings:
  - command: `sync` latches the operands, then toggles; the engine echoes the toggle
  - results: set one `hr` cycle before the done toggle, copied into the visible registers when it arrives, so they are one consistent snapshot
  - status levels: 2-FF synchronisers
  - stream: AsyncFIFO each way
- Clients of the core arbitrate one transaction at a time, round robin:
  - command ops
  - the stream ring (#580; tied off in `top.py` until then)
  - stager pairs (JTAG ER1, with the CPU in reset)
- `CTRL.abort` resets the engine and the core. RESET# runs again, then calibration.
- Calibration runs automatically after PLL lock + RESET#:
  - write the top 4 KiB of the array
  - for each of the 128 DELAYF taps, read and check it
  - apply the centre of the widest clean run
  - reads (COPY_IN, TEST read, REG) are refused (`UNCAL`) until it succeeds

## Registers (8-bit CSR; multi-byte low byte first; footprints are powers of two)
| off | name | acc | content |
|---|---|---|---|
| 0x00 | ID | r | `HRM3` 0x48524d33 |
| 0x04 | CK_KHZ | r | 180000 |
| 0x08 | STATUS | r | [0] busy [1] ready [2] locked [3] cal_valid [4] stream_on [5] hr_busy; [11:8] code [15:12] op |
| 0x0c | CMD | w | op: 1 COPY_OUT (window -> HR), 2 COPY_IN, 3 FILL, 4 TEST, 5 CALIBRATE, 6 REG_READ, 7 REG_WRITE (then read back), 8 SET_DELAY, 9 DEVICE_RESET |
| 0x0d | CTRL | w | [0] abort |
| 0x0e | EVENTS | rw1c | [0] done [1] error (any ERRORS bit) |
| 0x0f | IRQ_EN | rw | [0] done [1] error |
| 0x10 | ERRORS | rw1c | sticky bits, below |
| 0x14 | HR_ADDR | rw | [22:0] byte address |
| 0x18 | WIN_ADDR | rw | [11:0] window byte offset |
| 0x1c | LEN | rw | [23:0] bytes, >= 1 |
| 0x20 | FILL_VALUE | rw | constant (byte n = value byte n mod 4), ramp start [7:0] |
| 0x24 | MODE | rw | [1:0] fill 0 const 1 ramp 2 pattern; [3:2] test 1 write 2 read 3 write+read per burst; [4] inject_hang |
| 0x25 | REG_SEL | rw | 0 ID0 1 ID1 2 CR0 3 CR1 (reset 2) |
| 0x26 | LATENCY | rw | core wait, reset 13 (CR0 0x8F2F) |
| 0x27 | DELAY | rw | tap for SET_DELAY |
| 0x28 / 0x2a | SEED / XOR | rw | pattern seed; checker negative control |
| 0x2c | BURST | rw | words per transaction, 0 = max (668 at CK 180) |
| 0x2e / 0x30 | REG_WDATA / REG_RDATA | rw / r | register value out / in |
| 0x34 | CAL | r | [6:0] tap [15:8] widest clean run [16] valid |
| 0x38..0x47 | CAL_MAP0..3 | r | clean-tap bitmap |
| 0x48.. | CYCLES WORDS XACTS CHECKED ERRS BAD_INDEX BAD_GOT_WANT | r | last op; BAD_GOT_WANT = [15:0] want, [31:16] got |
| 0x64 / 0x66 | BAD_BITS / STATE | r | OR of got^want; at a watchdog: [3:0] engine, [7:4] core FSM, then source/sink |
| 0x68 / 0x6c | MISSING / CK_COUNT | r | tail-filled words; hr cycles per 2^19 of the 60 MHz oscillator (1572864 at 180) |
| 0x70..0x7c | STREAM_BASE / MASK / CTRL / LEVEL | rw / r | ring base word, size-1, enable; words held |

- Byte order: HyperRAM byte 2w is the first byte of word w (byte A). Little-endian words in the window read as written.
- Any address and length: masked head and tail words (RWDS), no read-modify-write. COPY handles an odd window/HyperRAM offset difference.
- Staging layout (bytes): magic @0, length @4, CRC @8, canary @16, stamp @20, image @32. Calibration uses the top 4 KiB.

## Codes and errors
- Codes:
  - 0 ok
  - 1 busy
  - 2 arg
  - 3 not-ready
  - 4 uncalibrated
  - 7 watchdog
  - 8 handshake
  - 9 reg-mismatch
  - 10 check (TEST with errors or missing)
  - 11 abort
  - 12 no-clock
  - 13 no-clean-tap
- ERRORS (sticky, rw1c):
  - 0 op
  - 1 wdog
  - 2 underflow (window prefetch)
  - 3 stream_ovf
  - 4 stream_unf
  - 5 handshake
  - 6 pll_lost
  - 7 pll_nolock
  - 8 ck_bad
  - 9 ck_dead
  - 10 cal_none
  - 11 reg_mismatch
  - 12 check

## Bounds (1.25x the derived expectation; the source has each one)
| wait | expected | expiry |
|---|---|---|
| one transaction | n + latency + 25 hr cycles (hyperbus.py guard/CA/tail/post/CS# high) | WDOG, core reset, STATE latched |
| DELAYF step | 8 + 8 x tap hr cycles | WDOG |
| RESET# | 2 x 2 us | NOT_READY |
| command handshake | 8 sync cycles | HANDSHAKE |
| PLL lock | tLOCK2 15 ms (DS-02012) | pll_nolock |
| CK meter update | 2^20 reference periods | ck_dead; an outstanding command ends NO_CLOCK |
| CK value | +/- 2 counts | ck_bad |
| firmware polls (`hyperram.rs`) | op bytes/2 + 38 per KiB hr cycles, /3, + 64; boot = tLOCK + RESET# + calibration | code `ff` |

## Firmware
- Boot: `cynthion-boot` waits for ready + calibrated, reads the header via the window and copies the image to block RAM; this build's image is in flash, so a verified image reports `FlashResident`.
- Staging: `load` streams into the window, one COPY_OUT per 4 KiB. The JTAG stager writes word pairs through the pair client.
- `hr status | calibrate | test [w|r|wr] [n] [a] [seed] | fill | read | write | reg | delay | errors | inject <hang|xor> | abort`.

## Clocks and cost
- Measured CK and figures: #588.
- Reference envelope: [`sequential-baseline.md`](sequential-baseline.md). Part facts: [`w956a8.md`](w956a8.md).
