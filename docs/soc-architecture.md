# The SoC: what it is made of, and where each piece came from

Still open: [`decisions.md`](decisions.md). Board:
[`hardware.md`](hardware.md). Silicon: [`chips/`](chips/).

Provenance — **written** from a spec · **generated** at elaboration · **vendored**
so it could be fixed · **upstream** unmodified.

## Core

| | what | provenance | why it is this way |
|---|---|---|---|
| CPU | VexiiRiscv, RV32IMAC + `rdtime` | **generated** from SpinalHDL at elaboration, in seconds | no vendored netlist to drift |
| caches | I and D, 64 sets × 1 way × 64 B = **4 KiB each** | generated with the core | **atomics force caches**: `LsuCachelessBridge.scala:203` asserts `!withAmo`, and the firmware is `riscv32imac` |
| buses | three Wishbone masters — `ibus`, `dbus` (cached), `iobus` (uncached) | generated | `--fetch-wishbone --lsu-wishbone --lsu-l1-wishbone`, all three needed; the third reaches every CSR |
| branch prediction | `BtbPlugin`, 512 sets, relaxed | generated | no `GSharePlugin`, no `RasPlugin` |
| clocks | `VariableClockDomainGenerator` | **written** | solves `sync` **and** `usb` together so `usb` lands on exactly 60 MHz; upstream's generator offers only hardcoded taps and a 3.7% error does not enumerate the ULPI PHY (#111) |

## Interrupts and time

| | what | provenance | why it is this way |
|---|---|---|---|
| PLIC | RISC-V PLIC, 31 sources, 5 used | **written** from the spec | upstream's is broken |
| sources | **per device** — each FUSB302B `int` line has its own | written | the claim identifies the device; an OR-ed source must clear *every* asserting device before re-enabling or the level re-fires forever (#135) |
| CLINT | `mtime`/`mtimecmp`, machine timer and software interrupt | **written** | |
| concurrency | **RTIC 2.3**, `riscv-clint-backend` | **upstream** crates | fixes the unbounded main-loop turn: 1,220 µs → 274 µs, zero deadline misses ([`rtic.md`](rtic.md)) |
| — and the PLIC survives it | `src/irq.rs` stays, SLIC in series behind it | | RTIC's `binds =` names a SLIC source, so no task can consume the PLIC front end |
| monotonic | CLINT-backed, five methods, ~60 lines | **written** | `rtic-monotonics` 2.2.1 has two RISC-V backends and no CLINT one. Costs the whole of `mtimecmp` — `src/timer.rs` cannot share the binary |
| logging from handlers | deferred ring | written | a handler that formats is a handler that blocks (#122/#124) |

## Peripherals

| | what | provenance | why it is this way |
|---|---|---|---|
| console ×2 | NS16550A, 16-byte FIFO | **written** from the spec, checked line-by-line against OpenCores' `uart_regs.v` | one `src/uart.rs` serves board and QEMU; the bespoke predecessor had a poll hazard where reading `rx_data` popped the FIFO. [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |
| buffering | `StreamBuffer` **at the transport**, not inside the UART | written | sized for the transport's stall, packet size and clock; overflow is dropped at the producer by backpressure |
| I2C | one controller plus a mux | **written**, OpenCores "I2C-Master Core" rev 0.9 register map | **forced by the board** — the parts share one bus |
| device protocols | one owner, cached reads | written | the PAC1954 NACKs for ~1 ms after REFRESH, so a state machine spans transactions (#123) |
| SPI flash | crossbar and chip-select hold, quad `0xEB` | **written** | three upstream defects, all the same shape — a hold expressed as a ready |
| QSPI burst | an FSM with an arming cycle | written | same shape again, ours this time (#89) |
| HyperRAM | `HyperRAMWishbone` at `0x2000_0000`, 8 MiB, `main=1 exe=1` | **vendored** controller (`hyperram_dqs_controller.py`) | vendored so `RECOVERY` could enforce tCSHI and the latency branch could stop being hardcoded (#90) |
| sideband | FPGA_ADV, one wire, three commands | **written** | the pin's upstream job is port takeover; this shares it (#137). [`chips/cynone-sideband.md`](chips/cynone-sideband.md) |
| GPIO, VBUS, `gateware_id`, ULPI window, I2C mux | | written | |

## Memory and boot

| | what | provenance | why it is this way |
|---|---|---|---|
| at `0x0` | a **492-byte bootloader**; the shell is an image | **written** | the shell outgrew block RAM at 36,514 bytes against 32,768 (#138) |
| firmware load | JTAG stream, and USB bulk | written | ~60 s by JTAG (#132/#114) |
| memory map | emitted from the design | **generated** — `scripts/soc_generate_pac.py` | 12 peripherals, 55 registers, 96 fields; cross-checked against `vexii_hello_soc.py`'s `*_BASE` constants and `target.rs`'s literals, and refuses to write on disagreement |
| register access | generated PAC, **addresses only** | generated | svd2rust emits one natural-width access per register; every CSR here is behind an `amaranth_soc` multiplexer at granularity 8, where a multi-byte register latches a shadow on its low byte and commits on its high byte. Drivers keep byte-level access |

## Dependencies and verification

| | what | provenance | why it is this way |
|---|---|---|---|
| `amaranth-soc` | upstream package, from git | **upstream** | PyPI's is a placeholder — version "0", no modules |
| emulation | QEMU `-M virt` | upstream | tests firmware logic that touches no PHY; it cannot test a single USB peripheral |
| simulation | 16 Amaranth pysim simulations, 543 checks | **written** | the only tier that can test a gateware path without a board |
| unit tests | 62, in `tests/` | written | |
