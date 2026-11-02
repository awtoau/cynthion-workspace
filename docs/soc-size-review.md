# Where the SoC's size is

Measured reference: fabric (LUT/FF/BRAM) and firmware (`.text`/`.rodata`), plus the measurement hazards. Numbers carry their commit; actions are issues (see [README.md](README.md)).

## Tools (none touch the board)

| script | answers |
|---|---|
| [soc_peripheral_area.py](../scripts/soc_peripheral_area.py) | each peripheral synthesised alone (upper bound) |
| [soc_instrumentation_cost.py](../scripts/soc_instrumentation_cost.py) | the three instrumentation peripherals in context (delta) |
| [soc_upstream_copy_check.py](../scripts/soc_upstream_copy_check.py) | is our copy of luna_soc's SPI PHY still verbatim |
| [pnr_noise.py](../scripts/pnr_noise.py) | how much of an Fmax difference is placement luck |
| [soc_icache_model.py](../scripts/soc_icache_model.py) | I-cache misses of a `.text` layout |
| [soc_review.py](../scripts/soc_review.py) | what moved vs [soc-review-baseline.json](soc-review-baseline.json), what got duplicated; run after every change |
| [soc_map_audit.py](../scripts/soc_map_audit.py) | mapped fraction of decoded address space; unmapped-read behaviour |
| [soc_decoder_cost.py](../scripts/soc_decoder_cost.py) | which knob drives the Wishbone decoder's cell count |

## 0. Two budgets

- Fabric size = LUTs and FFs. Room exists: 58% LUT4, 30% DFF at `b403978`, on a die that is a 25F marked 12F.
- Firmware size = speed. I-cache is 8 KiB (64 sets x 2 ways x 64 B, `gateware/soc/top.py` `CACHE_SETS`/`CACHE_WAYS`; `cpu.py` literal defaults are 128x1, overridden; canonical [chips/vexiiriscv-cpu.md](chips/vexiiriscv-cpu.md)).
  - `firmware/cynthion-soc/Cargo.toml`: `opt-level` `z` -> `3` grew `.text` 79% and gave 5.4x IPC.
  - Hot footprint vs the cache: [rtic.md](rtic.md).
  - Every firmware change needs a `.text` delta beside it.
- BRAM: 49 of 56 recorded in [soc-review-baseline.json](soc-review-baseline.json) (`432d29b`); budget in [chips/ecp5/bram-budget.md](chips/ecp5/bram-budget.md).

## 1. Baseline `b403978`

| | |
|---|---|
| `.text` / `.rodata` | 41,400 / 17,056 B |
| flash image | 58,480 B |
| bootloader | 512 B (`.start` 12 + `.text` 500) |
| TRELLIS_COMB / FF | 14,178 / 7,479 of 24,288 (58% / 30%) |
| DP16KD | 44 of 56 |
| `./dev.py test` / `./scripts/soc_sims.py` | 97 checks / 531 checks, 15 simulations |

- Check counts are tree-state dependent (clean-tree check): uncommitted tree gives 96 / 530.

## 2. Measuring fabric area

- Out of context: one module alone; upper bound (alone it keeps logic neighbours would fold). `soc_peripheral_area.py`.
- In context: build the whole SoC twice, diff utilisation; the only number worth acting on. `soc_instrumentation_cost.py` subclasses the modules under test with an empty `elaborate`.
- DFF is exact in both; LUT4 is mapping and depends on neighbours. Read DFF first.
- A/B needs nothing pinned: identity is the ECP5 USERCODE plus `gateware/usercode_map.py` ([#447](https://github.com/awtoau/cynthion-workspace/issues/447), [#443](https://github.com/awtoau/cynthion-workspace/issues/443)). Gate: [soc_repro_check.py](../scripts/soc_repro_check.py) (two builds byte-compared; `check.py` `repro`).

### COMB delta under ~200 is the mapper

- Null control `432d29b`, `bist0-ck160-dqs1-merge0-sync60`, `--no-parallel-refine`, 2026-08-12: one 32-bit identity constant changed, no logic touched -> TRELLIS_COMB +194, TRELLIS_FF +1 ([soc_trim_delta.py `--trim null-constant`](../scripts/soc_trim_delta.py)).
- Global, not local folding (`gateware_id` block is 153 COMB; move is 194).
- A COMB delta counts only when clear of +-194 and an FF/BRAM/LUTRAM delta of the same sign supports it ([#454](https://github.com/awtoau/cynthion-workspace/issues/454)). `soc_review.py` enforces it.

### Fmax is placement luck

- 71.45-76.99 MHz across identical builds; 84.88 vs 73.69 MHz from two builds differing by 22 cells. No Fmax here is evidence; [pnr_noise.py](../scripts/pnr_noise.py) settles timing questions.

## 3. Per peripheral (out of context, upper bounds)

| module | LUT4 eq | DFF | DP16KD | |
|---|---:|---:|---:|---|
| `hyperram_probe` | 750 | 289 | - | instrumentation |
| `clint` | 400 | 137 | - | |
| `uart16550` | 231 | 118 | - | x2 in the SoC |
| `i2c_master` | 200 | 109 | - | |
| `flash_probe` | 183 | 72 | - | instrumentation |
| `serial_line` | 162 | 99 | - | |
| `vexii_irq` | 149 | 101 | - | in no SoC |
| `fabric_status` | 110 | 36 | - | |
| `ulpi_window` | 98 | 96 | - | |
| `stream_buffer` (64, sync) | 90 | 27 | - | |
| `flash_ila` | 89 | 108 | 1 | instrumentation |
| `sideband_csr` | 85 | 50 | - | |
| `stream_buffer` (16, CDC) | 46 | 57 | - | |
| `vbus_csr` | 23 | 24 | - | |
| `i2c_mux` | 9 | 20 | - | |
| `wishbone_pipe` | 9 | 34 | - | |

- `hyperram_probe` is the largest peripheral (instrumentation); `wishbone_pipe` recovered the timing margin for 9 LUT4 + 34 FF.

## 4. Instrumentation in context

`FlashILA`, `FlashPinProbe`, `HyperRAMProbe` with logic removed, CSR bridges kept:

| cell | with | without | delta | share |
|---|---:|---:|---:|---:|
| TRELLIS_COMB | 14,476 | 13,402 | 1,074 | 7.4% |
| TRELLIS_FF | 7,472 | 7,016 | 456 | 6.1% |
| DP16KD | 44 | 43 | 1 | |

- Understates a deletion: a real removal also recovers three bridges and three decoder windows.
- `peripherals/flash.py`: the ILA "was built to find the JEDEC read fault, which is fixed"; `flash_cdc.py`: after the PHY moves to its own domain ILA captures cross a clock boundary unsynchronised ("gross behaviour only").
- `ObservablePHY` (`peripherals/flash.py`) re-implements `luna_soc`'s `SPIPHYController.elaborate` statement for statement (130 lines). `soc_upstream_copy_check.py`: in sync with the installed `luna_soc` (83 upstream statements, 85 in the copy: instrumentation assignments + the `as fsm:` handle); validated against known-bad mutations both directions.

## 5. Firmware bytes (`llvm-nm --print-size`, `b403978`)

| | bytes | share |
|---|---:|---:|
| `cynthion_soc::run` (one function) | 17,044 | 41.2% |
| unattributed / small | 5,034 | 12.2% |
| `bench` (13 symbols) | 5,004 | 12.2% |
| `core::fmt`, `core::str`, `core::num` | 4,638 | 11.3% |
| `fusb302` | 1,746 | 4.2% |
| `board` | 1,002 | 2.4% |
| other | ~6,900 | 16.7% |

- `run` is a 25-arm `match` on the command word; `opt-level="z"` + `lto=true` still inlines single-call-site functions into it. 17,044 B = 266 cache lines vs 64 sets.
- Splitting costs 668 B: `#[inline(never)]` on ten handlers moves 7,000 B out of `run` (17,044 -> 10,044), `.text` 41,400 -> 42,068. Locality benefit unmeasured; `soc_icache_model.py` would settle it.
- `core::str` for four ASCII words cost 880 B: `vbus_command` used `core::str::from_utf8(rest).unwrap_or("").trim()`; `<str>::trim` pulls `core::unicode::unicode_data::white_space::WHITESPACE_MAP`. Taking bytes (`crate::trim`, 88 B, like `memory::Region::parse`, `gpio::led_by_name`, `fusb302`) and changing `vbus::Source::parse`'s signature: `.text` 41,400 -> 41,016 (-384), `.rodata` 17,056 -> 16,560 (-496), image 58,480 -> 57,600 (-880). `<str>::trim`, `str::validations`, `WHITESPACE_MAP` absent afterwards.
- After the changes in 7: COMB 14,025, FF 7,481, DP16KD 44, `$glbnet$clk` 73.05 MHz, 97 / 531 checks, `./dev.py lint` 8 passed.

## 6. Upstream vs hand-rolled (checked by running)

| we hand-roll | upstream | verdict |
|---|---|---|
| `StreamBuffer` FIFO<->stream wiring | `amaranth.lib.fifo` `w_stream`/`r_stream` | replaced; byte-identical Verilog (differences only `src` attributes and order of two `assign`s) |
| `blockram` (luna_soc) | `amaranth_soc.wishbone.sram.WishboneSRAM` | not a swap: no `cti`/`bte`/`err`, so cache-line refills lose registered-feedback bursting; same 2 cycles/beat |
| `flash_cdc.ClockCrossedPHY` | `luna_soc` `SPIControlPortCDC` | ours uses `AsyncFIFOBuffered` (upstream `AsyncFIFO` put the FIFO output mux and the PHY carry chain on one path); ours is the fixed one |
| `SplitRW` | nothing in `amaranth_soc.csr.action` | correct, shared by four peripherals |
| `SerialLine` | `amaranth_stdio.serial.AsyncSerial` | already a wrapper; the four things it adds are what `AsyncSerial` leaves out |

- Interrupt controller: `gateware/soc/cpu/intc.py` wraps `amaranth_soc.csr.event.EventMonitor` (see [soc-interrupts.md](soc-interrupts.md)); the PLIC was removed in `857c45d`.
- `luna_soc` surface is 1,125 lines, not 127: `core.blockram` 127 + `core.spiflash` 998 (`mmap.py` 207, `phy.py` 246, `port.py` 172, `controller.py` 164, `utils.py` 93, `__init__.py` 116), imported by `peripherals/flash.py` (`SPIFlashMemoryMap`, `SPIPHYController`, `SPIClockGenerator`, `SPIController`, `SPIControlPort`, `StreamCore2PHY`, `StreamPHY2Core`, `WaitTimer`, `PinSignature`), `top.py` (`ECP5ConfigurationFlashInterface`), `peripherals/flash_cdc.py` (`SPIControlPort`). [toolchain-simplification.md](toolchain-simplification.md) section 2 says "exactly two things" (predates the flash work).

## 7. Changes made

| change | `.text` | `.rodata` | fabric |
|---|---:|---:|---|
| `stream_buffer.py` uses `fifo.w_stream`/`r_stream` | - | - | byte-identical Verilog |
| `vbus_command`, `vbus::Source::parse` take bytes | -384 | -496 | - |
| `bench.rs` duplicate `#[inline(always)]` removed | 0 | 0 | - |

## 8. Wrong rather than large

- `peripherals/vbus_csr.py` `input` register drives nothing: `VbusControl` exposes `control_vbus_in_en`/`aux_vbus_in_en`, `top.py` deliberately does not request the pads. Writable, reads back, reaches no pin, while its docstring says "`vbus input both` restores the permissive state" and `vbus::inputs()` reports it as board state.
- `gateware/soc/cpu/irq.py`: 119 lines, 149 LUT4-eq, in no design. [upstream-boundary.md](upstream-boundary.md) keeps it so luna_soc's SVD generator finds the map; `repos/cynthion`'s facedancer top imports `InterruptController` from `luna_soc.gateware.cpu`, not here.
- `hello_soc.py` and `cpu_area.py` deleted (VexRiscv vs VexiiRiscv is settled).
- Doc references in `gateware/` to missing files: `docs/apollo_samd11_mcu/fpga-adv-sideband.md` (`sideband/sideband_gateware.py:5`), `docs/luna_ecp5_fpga/hyperram-speed.md` (`hyperram/hyperram_identify.py:11,80`, `hyperram/hyperram_fifo.py:109`), `docs/luna_ecp5_fpga/fast-bitstream-loading.md` (`loader/bitstream_sink.py:25`). `./dev.py docs` does not check source comments.
- `vbus::discharge()` has no caller (`firmware/cynthion-soc/src/vbus.rs:305`). `hyperram::seek_word`, `hyperram::write_word_pub` are called (eleven times from `bench.rs`); only ones checked against the whole tree are listed.

## 9. Claim that measurement did not support

- `WishboneSRAM` as a drop-in for `blockram`: constructs its Wishbone signature with no `features` (no `cti`/`bte`/`err`); cache-line refills are registered-feedback bursts; acknowledge also takes 2 cycles/beat (`bus/wishbone_pipe.py`), so no latency argument.
