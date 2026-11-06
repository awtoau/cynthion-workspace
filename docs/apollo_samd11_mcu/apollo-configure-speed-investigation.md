# `apollo configure`: speed

Host -> SAMD11 (USB control transfers) -> SERCOM SPI -> ECP5 JTAG TAP. Everything reached over that path benefits, including `apollo flash-program` / `flash-erase` (same `device.jtag` session via `create_jtag_programmer()`).

- `apollo flash --fast` bypasses it: loads a bridge on the FPGA, programs flash through the FPGA's own USB.
- W25Q32 read modes / QSPI: [../chips/w25q32-config-flash.md](../chips/w25q32-config-flash.md).
- SAMD11 flash/RAM budget: [../chips/samd11-apollo.md](../chips/samd11-apollo.md) § Memory budget.
- Investigation story (every attempt, every retracted figure): [../history/soc-usb.md](../history/soc-usb.md).

## Result

- **713.9 -> 322.2 ms at 1024 B chunks, 2.22x vs stock `v1.1.1`**; 122880-byte fixed payload, direct USB port.
- Shipped `6520707` (`awtoau/awto-apollo`) within the flash/RAM ceilings (ceilings never raised).
- Verified on hardware: IDCODE `0x21111043`, configure `rc=0`, `check.py` 6 passed.
- Real configure (in-process, same bitstream): 693 ms (direct port, 512 B era).

| change | worth | how |
|---|---|---|
| DMA-clocked JTAG (`d43f765`) | -85 ms | CPU not spinning on SERCOM flags, so `tud_task()` runs during the shift |
| USB topology | -46 ms | off a 4-hub chain, no code |
| 512 then 1024 B chunks | -109 ms | fewer transactions |
| pipelined `spi_send` + LTO | -147 ms | shifter kept fed; LTO reclaimed 2968 B |
| housekeeping (LED, button) on a 20 ms tick (`7060149`, `6520707`) | -6 ms | |

Each figure is a difference of two separately measured rows, not an isolated A/B (sum 393 vs actual 392).

### Commit ladder (122880 B, ms; % = wire time / measured)

| commit | what | 256 B | 512 B | 1024 B |
|---|---|---|---|---|
| `v1.1.1` | stock | 713.9 | n/a | n/a |
| `4bf7691` | LTO | 639.1 | n/a | n/a |
| `e034daa` | pipelined `spi_send` + suppress TDO readback | 566.9 | n/a | n/a |
| `bb82d39` | 512 B buffers + `GET_INFO` | 555.4 | 488.9 | n/a |
| `19242e8` | two reported limits + 1024 B writes | 564.0 | 489.5 | 457.6 |
| `cd4a85c` | double-buffered staging | - | 469.1 | 455.6 |
| direct USB port | no code | - | 425.2 | 409.1 |
| `d43f765` | clock by DMA | - | 343.3 | 324.4 |
| `7060149` / `6520707` | 5 ms then 20 ms housekeeping tick | - | 341.2 | 322.2 |
| `c8b9c1f` | SETUP dispatched from USB ISR; unshipped, stalls on chunk-size change, 246 B over budget ([#100](https://github.com/awtoau/cynthion-workspace/issues/100)) | - | 328.3 | 314.4 |
| theoretical wire | 12 MHz SCK, 1 bit/clock | 81.9 | 81.9 | 81.9 |

- `n/a`: pre-`bb82d39` firmware declares `jtag_out_buffer[256]` and stalls larger requests; the benchmark refuses to label a 256 B run as 512.
- Rows above `cd4a85c` were taken on the hub chain (~10% pessimistic); quote the direct-port and later rows.
- `0e9bfb1` (pure refactor) moves 4.8 ms = noise floor.
- `e034daa` is 1.13x on identical work.

## Where the remaining 240 ms goes

- Wire time 81.9 ms; transport costs 3.9x that. USB, not JTAG.
- Per-chunk USB cost is bus efficiency: fit **144.7 us fixed + 2.717 us/byte** (direct port: 122.7 us + 2.267 us/byte, 3.53 Mbit/s = 29% of bus) vs 0.667 us/byte wire. [`usb_transfer_cost.py`](../../debris/scripts/usb_transfer_cost.py).
- Payload term is ~334 ms regardless of chunk size. Doubling chunks only halves the fixed term (35 ms of 457 at 1024 B).
- SPI clocking marginal cost 0.663-0.715 us/byte vs 0.667 wire floor (2-3% overhead); the rest of the no-USB gap is per-chunk setup.
- Full-speed bulk is 64 B/packet like control. Packets per 1 ms frame differ: bulk 19 (0.822 us/byte), control measured 5.8 (2.717 us/byte).
- **Next lever: dedicated bulk endpoint, ~3.3x on the payload term** (334 ms -> 101 ms theoretical, 202 ms at 50%). Needs own framing and error handling; bulk streaming (Apollo EP 0x02 OUT / 0x83 IN, CDC's, idle during JTAG lock) once measured 1703 vs 1683 ms for control, cause unexplained.
- Ruled out: collapsing `SET_OUT_BUFFER` + `SCAN` (~17 ms at 1024 B); SETUP-from-ISR (7.8 ms, 3.2% of gap); isochronous (1023 B/frame, no retry, needs integrity layer); Linux-side tuning (xHCI, no knobs).
- Bootloader (Saturn-V) flashes over EP0 at the same 5.8 packets/frame; bulk idea parked (2 KB flash budget, `repos/saturn-v`).

### Endpoints

| interface | class | endpoints |
|---|---|---|
| 0 | CDC control | EP 0x81 IN, interrupt, 8 B |
| 1 | CDC data | EP 0x02 OUT, 0x83 IN, bulk, 64 B |
| 2 | DFU runtime | none (EP0) |

- All JTAG traffic is EP0 vendor control requests. SAMD11 descriptor table `sram_registers[8][2]` = 8 endpoints.
- SAMD USB is itself a DMA engine (`dcd_samd.c:276` `bank->ADDR.reg = (uint32_t) buffer`): zero copy, nothing to remove on the USB side.

## Facts to keep

### SCK
- `f_SCK = 48 MHz / (2 x (BAUD+1))`: BAUD 0 = 24 MHz, **1 = 12 MHz (current)**, 2 = 8 MHz. No setting between.
- Datasheet Table 35-50 `tSCK` min 84 ns = 11.9 MHz rated; BAUD=1 is already marginally past rated, BAUD=0 2x past.
- `tMIS` 21 ns typ vs 20.8 ns half-period at 24 MHz: duplex readback could not validate 24 MHz.

### DMA
- Must be asynchronous: arm channels and return, poll completion from `jtag_scan_task()`. Spinning on `TCMPL` blocks `tud_task()` and is polling plus setup cost.
- Completion polled, not ISR: `CHID` is a single register window, channel setup is not re-entrant.
- ~64 B of its flash cost is mandatory (DMAC descriptor + write-back sections).
- Second buffer (`jtag_tx_alt`, 512 B) is not redundant: removing it costs +74.1 ms (+22.8%). [`jtag_single_buffer_probe.py`](../../debris/scripts/jtag_single_buffer_probe.py).
- TX-only DMA / dropping TDO: ~2 ms of 950, silent-failure surface. ECP5 self-validates via `LSC_RESET_CRC`; BSE code 3 = CRC failed. Every configure decodes `DONE`, `FAIL` and BSE code explicitly.

### RAM (JTAG buffers)
- Stack sized from measurement: `STACK_SIZE=0x2C0` (704 B) vs 344 B measured high-water (lower bound; 2x margin); `-Wl,--defsym=STACK_SIZE=...`. No MPU: overflow is silent `.bss` corruption.
- Console ring shares the JTAG union (exclusive via the JTAG lock `jtag_init()` .. `jtag_deinit()`; `console_task()` returns while held). `jtag_in_buffer`/`jtag_out_buffer` stay separate (`spi_send()` takes both).
- Buffers are pointers, not arrays: `sizeof()` yields 4. Bounds use `JTAG_BUFFER_SIZE`.
- `spi_send()` accepts NULL receive; `DATA` must still be read (`RXC` stays set; skipping the read hangs). `FLAG_DISCARD_TDO` = SCAN flags bit 2; older firmware ignores unknown bits.
- `JTAG_BUFFER_SIZE` 1024 is rejected: doubling costs both halves of the pair (1024 B); union cap ~1634 B, i.e. 2 x ~817. 512 B stands. Re-carve (transmit takes whole region, receive its second half) reached 457.5 ms at 1024 B but needs `GET_INFO` to report **two limits** (write limit 1024, capture-read limit 512; `GET_INFO` has 4 spare bytes) or capturing scans >512 B are refused (`_execute_command()` in `ecp5.py` defaults `ignore_response=False`).

### Standard test (the only figure to quote)
- [`jtag_fixed_benchmark.py`](../../debris/scripts/jtag_fixed_benchmark.py), committed payload 122880 B: fixed so a smaller bitstream cannot read as "faster"; real bitstreams 103364-136184 B; divides evenly by 128..1024.
- Shift path only (no `ISC_ENABLE`, CRC, DONE poll; TAP left in SHIFT-DR): FPGA ignores data, safe to repeat. Spread 1.8-3.3 ms.
- [`jtag_speed_bisect.py`](../../debris/scripts/jtag_speed_bisect.py) reflashes historical firmware (checks out `firmware/` only; full checkouts remove our `boot_to_dfu()`). `SOURCES` is a wildcard: `stack_probe.c/h` and `apollo_mode.c/h` must be set aside for stock builds (stock builds 94.17% ROM / 86.52% RAM).
- Time in-process, not `subprocess.run(python cli.py ...)` (~80 ms interpreter startup).
- Do not compare figures from different bitstreams/firmware/scopes.

### `0xb9` synthetic benchmark (`handle_jtag_benchmark`)
- Generates `jtag_out_buffer[i] = i*7+1` in firmware, clocks, verifies TDO, returns elapsed ms; accepts a SERCOM divider.
- `blocks` in the reply is in 256 B units (`jtag.c:337`).
- Mismatch counter is only meaningful in BYPASS (TDO = TDI delayed one bit).
- Chunk size in `wIndex` must not truncate: 512 once truncated to 0 ("full buffer"), clocked 245760 B in one loop and dropped the device off the bus. Now 64 B units + 65536 B work bound; bound is off by one (1024 x 64 = 65536 passes). Bound must be on time, covering the whole control transfer. 1024 B no-USB figure unavailable until fixed.
- No-USB clocking: 275 ms @256 B, 137 ms @512 B (60% of wire).

### USB topology
- 4 hubs deep on a shared full-speed segment: 144.7 us + 2.717 us/B; 1 level, alone: 122.7 us + 2.267 us/B. Quote topology with any figure.

## Negative results (not to retry)

| attempt | outcome |
|---|---|
| SCK 12 -> 24 MHz | unsafe, see SCK |
| `JTAG_BUFFER_SIZE` 1024 | 95.12% RAM, rejected by `check.py` |
| dropping `button_task()` alone / masking EIC during JTAG | no gain in isolation (inside 1.6 / 4.0 ms spread); LED + button cut together from ~200 kHz to 200 Hz gave 9.8 ms |
| `-fstack-usage` | wrong tool (LTO inlines across units) |
| paint-and-measure stack, word-wise scan | LTO resolves `&_sstack` per inlined copy; a coincidental `0xDEADBEEF` truncates the scan |

## ECP5 cannot load itself ([#108](https://github.com/awtoau/cynthion-workspace/issues/108))

- No ICAP-equivalent. Config/misc primitives: `JTAGG`, `OSCG`, `SEDGA`, `DTR`, `USRMCLK`, `GSR` (`prjtrellis/libtrellis/src/Chip.cpp:261-268`, `nextpnr/ecp5/arch.cc:1035`, Yosys ECP5 blackbox); none accepts config data.
- `JTAGG`: only `ER1`/`ER2` user registers; `TCK/TMS/TDI` are external pads; config opcodes decoded behind the TAP IR.
- `USRMCLK`: MCLK pin for soft SPI to flash only. `SEDGA`: read-only CRC. `PCNTR`: MachXO2/XO3 only (`Chip.cpp:472`).
- `--background` (`ecppack.cpp:158-165`, `Bitstream.cpp:34,917-921`) keeps pins live for an external master doing partial reconfig over JTAG; FPGA-TN-02039 §6.2: Background mode supports only read commands.
- Exits from user mode (FPGA-TN-02039 §5.5): PROGRAMN (input only), REFRESH on JTAG / slave SPI, power cycle. `BOOTADDR` fixed at pack time (`ecppack.cpp:167-194`).
- Only route from fabric: soft SPI master writes flash, something external reboots; that is the existing `FlashBridge` / `flash-fast`.
- FPGA-side transfer is not the cost: a bulk-OUT sink bitstream (TARGET-C, 110 MHz vs 60 MHz constraint, `1209:000e`) took 304726 B in 7.89 ms (38.6 MB/s) vs 2746 ms over the JTAG path.

### Early decomposition (304726 B image, stock-era firmware)
- Apollo enumerates 12 Mbps full-speed, EP0 64 B (`CFG_TUD_ENDPOINT0_SIZE 64`); raw ceiling 1.5 MB/s. The 388 Mbps in [#100](https://github.com/awtoau/cynthion-workspace/issues/100) is the FPGA PHY on another port.
- `SET_OUT_BUFFER`: 204 us + 2.53 us/B; `SCAN`: 1.40 us/B marginal. Getting a byte into the SAMD11 costs ~2x clocking it.
- Floors: 100% of full-speed wire 203 ms (floor for any SAMD11 path); [#100](https://github.com/awtoau/cynthion-workspace/issues/100)'s 6 ms target is not reachable through this MCU.

## Clean state between runs

- A configured FPGA drives the shared lines and will not re-enter ISC: back-to-back configures fail with "Failed to enter ISC" and all-zero status. [`verify_configure.py`](../../debris/scripts/verify_configure.py) issues before each run:
  - `0xec` `EMERGENCY_RESET`: breaks `MODE_JTAG_PROGRAMMING` latch (pipe error otherwise).
  - `0xbe` `JTAG_STOP`: closes dangling session.
  - `0xc0` `TRIGGER_RECONFIGURATION`: pulses PROGRAMN.
- `force_fpga_offline()` unusable here (pipe error once offline).
- After configure the FPGA may take the USB port: "No such device", re-run.
- In Saturn-V: `dfu-util -d 1d50:615c -a 0 -D <firmware.bin> -R`; `apollo enter-dfu`; [`apollo_reflash.py`](../../debris/scripts/apollo_reflash.py).
