# One CSR bridge ([#619](https://github.com/awtoau/cynthion-workspace/issues/619))

## Before: seven Wishbone-to-CSR bridges

- Each CSR peripheral already owns a `csr.Bridge` (its register mux); that stays.
- What is replaced: the `WishboneCSRBridge` in front of each, plus its Wishbone decoder window.
- All CSR buses are 8 bits wide behind 32-bit Wishbone (granularity 8): a 32-bit access is 4 byte cycles + ack = 5 cycles.

| window | base (before) | size | registers | owner |
|---|---|---|---|---|
| console | `0xf0000000` | 8 B | 8 | `Uart16550` |
| apollo_uart | `0xf0000500` | 8 B | 8 | `Uart16550` |
| board (gpio, i2c, sideband, ulpi, i2c_mux, vbus, fabric, clocks) | `0xf0000600` | 128 B, own `csr.Decoder` | 4+5+4+4+2+1+2+2 | `gpio`, `I2CMaster`, `SidebandControl`, `UlpiRegisters`, `I2CBusMux`, `VbusControl`, `FabricStatus`, `ClockMonitor` |
| hyperram | `0xf0000800` | 128 B | 41 | `HyperRAM` |
| intc | `0xf0000c00` | 32 B | 2 | `Interrupts` |
| usb_host | `0xf0001000` | 32 B | 27 | `UsbPort` |
| clint | `0xf0800000` | 64 KiB | 5 | `Clint` |

- Native Wishbone windows (not CSR, not bridged): `ram`, `spiflash`, `spi0`, `spi0_buf`, `hyperram_window`.

## Cost of a bridge

`./scripts/soc/soc_csr_bridge_cost.py`: alone, not the whole SoC; `synth_ecp5`; upper bound.

- Each upstream bridge: 36 FF (3-bit cycle, 32-bit `dat_r`, ack) + its own address-decode window in the 30-bit Wishbone decoder.
- Four real peripherals (intc, 2x 16550, CLINT): own bridges 1440 COMB / 512 FF, one `csr.Decoder` + one bridge 729 COMB / 404 FF.
- Whole-design deltas do not follow isolated slopes ([#453](https://github.com/awtoau/cynthion-workspace/issues/453)); the saving in the SoC is an estimate.

## Design chosen

- One `csr.Decoder` (17-bit byte address, 8-bit data) holding every leaf peripheral directly (the nested `board` decoder is flattened; window names are unchanged, e.g. `board_gpio`).
- One `bus/csr_bridge.CsrBridge` in front of it: upstream `WishboneCSRBridge` behaviour (byte-stepped, registered read re-registered) plus ERR for a word no window overlaps.
- Why not upstream `WishboneCSRBridge`: a hole inside its window acks zero, and `BusFault` cannot see it. The hit test is one compare per window, on word-address high bits.
- Response pipeline: unchanged. The bridge still answers a mapped access in 5 cycles (inside `BUS_TIMEOUT_CYCLES`), a hole in 1 (ERR, no strobe).
- Bridge declares `cti`/`bte`/`err` so the Wishbone decoder builds no feature shim.

## Address map

- Kept: console, apollo_uart, board_*, hyperram, intc, usb_host.
- Moved: clint `0xf0800000` -> `0xf0010000` (CSR window must be contiguous and align to 128 KiB, so the CLINT's 64 KiB joins it).
- Moved (native windows that sat inside the new CSR window): hyperram_window `0xf0010000` -> `0xf0020000`, spi0 `0xf0000100` -> `0xf0021000`, spi0_buf `0xf0000200` -> `0xf0021100`.
- CPU PMA region (`CSR_REGION_SIZE`) untouched; all windows stay inside it.
- Firmware reads every base from the generated `cynthion-soc-pac` `base.rs`; the PAC and SVD are regenerated.

## Checks

- `./scripts/soc/soc_csr_map_sim.py` (88 checks, in `soc_sims.py`): behind the real CSR map, every window reads back at its address, strobes once per byte, answers in 5 cycles; every gap word answers ERR with no strobe; each peripheral sits at its documented `top.py` address.
- `./scripts/soc/soc_map_audit.py` counts the CSR window's unclaimed words as ERR; holes inside a peripheral's own window (CLINT's 64 KiB) are still SILENT.
- `cpu fault` reads `0xf0030000` (in the CPU's CSR region, in no window); the old `0xf0000700` is now inside the CSR window.

## Saving

- Estimate, not a build: 6 bridges removed = 216 FF; COMB from the isolated 4-peripheral model above is -711, whole-design slopes are noisier ([#453](https://github.com/awtoau/cynthion-workspace/issues/453)).
