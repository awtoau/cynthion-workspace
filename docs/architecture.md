# Architecture: what this is made of, and where each piece came from

Still open: [`architecture.md`](architecture.md). Board: [`hardware.md`](hardware.md).
Silicon: [`chips/`](chips/). Why we diverge from upstream:
[`upstream-boundary.md`](upstream-boundary.md).

Provenance — **written** from a spec · **generated** at elaboration · **vendored**
so it could be fixed · **upstream** unmodified.

## Three processors, and what each owns

| | part | runs | owns |
|---|---|---|---|
| **Apollo** | ATSAMD11D14A | bare metal, one `while(1)` over TinyUSB | JTAG, the debug SPI, FPGA configuration, the CONTROL port mux, FPGA_ADV |
| **the FPGA** | ECP5 `LFE5U-12F` — a 25F die | one bitstream at a time | every peripheral, the ULPI PHYs, the memories |
| **the SoC** | VexiiRiscv, inside that bitstream | `cynthion-soc`, Rust, `riscv32imac` | the command shell, the drivers, the workload |

Apollo is bare metal by construction: no RTOS, no threads, so the only preemption
is three ISRs — [`chips/samd11-apollo.md`](chips/samd11-apollo.md). The FPGA part
is in [`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md). Everything below is
the SoC.

## SoC core

| | what | provenance | detail |
|---|---|---|---|
| CPU | VexiiRiscv, RV32IMAC + `rdtime` | generated | [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md) |
| caches | I and D, 64 sets × 1 way × 64 B = 4 KiB each | generated | same — cached is forced by atomics |
| buses | three Wishbone masters: `ibus`, `dbus` cached, `iobus` uncached | generated | same |
| branch prediction | `BtbPlugin`, 512 sets, relaxed | generated | same |
| clocks | `VariableClockDomainGenerator` | written | [`chips/ecp5/clocks.md`](chips/ecp5/clocks.md), #111 |

## Interrupts and time

| | what | provenance | detail |
|---|---|---|---|
| PLIC | RISC-V PLIC, one source per device | written | [`hardware.md`](hardware.md#register-reference), #135 |
| CLINT | `mtime`/`mtimecmp` | written | same |
| concurrency | RTIC 2.3, `riscv-clint-backend` | upstream | [`rtic.md`](rtic.md) |
| monotonic | CLINT-backed, ~60 lines | written | same — `rtic-monotonics` has no CLINT backend |
| logging from handlers | deferred ring | written | #122, #124 |

`src/irq.rs`'s PLIC claim loop survives RTIC adoption, with the SLIC in series
behind it — RTIC's `binds =` names a SLIC source, not a hardware interrupt.

## Peripherals

| | what | provenance | detail |
|---|---|---|---|
| console ×2 | NS16550A, 16-byte FIFO | written | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |
| buffering | `StreamBuffer` at the transport, not in the UART | written | sized per transport; overflow dropped at the producer |
| I2C | one controller plus a mux, OpenCores rev 0.9 map | written | forced — the parts share one bus |
| device protocols | one owner, cached reads | written | [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md), #123 |
| SPI flash | crossbar, chip-select hold, quad `0xEB` | written | [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md), #89 |
| HyperRAM | `HyperRAMWishbone` at `0x2000_0000`, 8 MiB, `main=1 exe=1` | vendored | [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md), [`soc-memory-bus.md`](soc-memory-bus.md), #90 |
| sideband | FPGA_ADV, one wire, three commands | written | [`chips/cynone-sideband.md`](chips/cynone-sideband.md), #137 |
| ULPI window | register access on `target_phy`, no packet path | written | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| GPIO, VBUS, `gateware_id`, I2C mux | | written | [`hardware.md`](hardware.md#register-reference) |

The three flash and UART peripherals are ours because upstream's have defects of
one shape — a hold expressed as a ready. Each is named with its reproducer in
[`upstream-boundary.md`](upstream-boundary.md).

## Memory and boot

| | what | provenance | detail |
|---|---|---|---|
| at `0x0` | **the bootloader** — `firmware/cynthion-boot`, 492 bytes | written | [`hardware.md`](hardware.md), #138 |
| the firmware | `firmware/cynthion-soc` — an **image** the bootloader loads | written | too large to be resident |
| firmware load | JTAG stream, and USB bulk | written | #132, #114 |
| memory map and PAC | generated from the design; **addresses only** | generated | `scripts/soc_generate_pac.py`, [`hardware.md`](hardware.md#register-reference) |

## Dependencies and verification

| | what | provenance | detail |
|---|---|---|---|
| `amaranth-soc` | upstream package, from git | upstream | [`toolchain-versions.md`](toolchain-versions.md) — PyPI's is a placeholder |
| emulation | QEMU `-M virt` | upstream | cannot reach a PHY or any of our peripherals |
| simulation | Amaranth pysim | written | `scripts/soc_sims.py` — the only tier that tests gateware without a board |
| unit tests | pytest, in `tests/` | written | |
