# What we take from upstream, and what we have replaced

Which parts of LUNA, luna-soc and cynthion this workspace uses as-is, which it has diverged from, and why.

- `luna`: the USB device stack, ULPI and packet layers are kept (production AUX console and USB host port). Defects found and replaced: `JTAGRegisterInterface` ([#204](https://github.com/awtoau/cynthion-workspace/issues/204)), the non-DQS HyperRAM `psram` controller ([#215](https://github.com/awtoau/cynthion-workspace/issues/215)). Endpoint submodule names came from `id()`, which made builds non-reproducible; fixed upstream (luna PR [#307](https://github.com/awtoau/cynthion-workspace/issues/307)).
- `luna_soc`: three defects traced to it. Great Scott Gadgets (GSG) code is reserved for the genuinely Cynthion-specific.

## No upstreaming

- `luna` and `cynthion` maintainers refuse LLM-generated pull requests and issues (luna `CONTRIBUTING.md`, "AI policy"). Nothing is upstreamed from this workflow.
- The patch plan, validation process and defect-report drafts are withdrawn: [`history/upstream-toolchain.md`](history/upstream-toolchain.md).
- Divergences stay in local forks; this file records what and why.
- Upstream is still read: `scripts/tools/upstream_ci.py` and the build traps below measure upstream's own tree as a baseline.

## The policy

- **Reserve GSG code for the genuinely Cynthion-specific**: r1.4 pin map, the USB stack we depend on, gateware unique to the product.
- For anything general (interrupt controller, timer, UART, bus primitive), in order: upstream `amaranth-soc`, wider Amaranth ecosystem, a published standard register map, write our own. Writing our own is an acceptable outcome.
- The luna_soc peripheral set (timer, uart, gpio, spi) is not on the adoption list.
- **`luna`**: USB gateware. Reimplementing it is a large cost for a worse result.
- **`luna_soc`**: SoC peripherals. Three defects, all found on this board, all in paths luna_soc's own firmware does not exercise:
  - SPI crossbar starvation.
  - CS-hold register type.
  - `oe = ~tx.rdy` releasing the UART stop bit early.
  - Three faults in three peripherals is a pattern: generic peripherals are written here or taken from a published map.
- **Replace what blocks us, and say so here.** Every divergence has a recorded reason and, where upstream's fault, a reproducer.
- **Vendor the file, do not inherit the stack.** Board definition: `gateware/board/cynthion_r1_4.py`, see [`hardware.md`](hardware.md).

## luna-soc fork pin

- `repos/cynthion` pins `luna-soc @ git+https://github.com/awtoau/awto-luna-soc.git@main`.
- Fork contents: upstream luna-soc 0.3.2, amaranth-soc `d8b5892` backport (Python 3.14 CSR annotations), five reverted `__init__` workarounds (94 lines, 8 files).
- The fork existed for an Amaranth API change and 40+ patched CSR classes; upstream is now ahead, so the amaranth-soc reason is gone.
- TODO: check whether anything else still needs the fork; if not, pin upstream `luna-soc~=0.3.2`.
- Fork tags use the PEP 440 local form: [`toolchain-versions.md`](toolchain-versions.md#constraints-that-must-hold).
- `import cynthion` hard-requires `luna_soc` (top-level import in `cynthion/python/src/__init__.py`).
- `gateware/` imports nothing from `luna_soc` since [#593](https://github.com/awtoau/cynthion-workspace/issues/593) (the config flash is the [#590](https://github.com/awtoau/cynthion-workspace/issues/590) core: [`chips/flash-soc-peripheral.md`](chips/flash-soc-peripheral.md)).

## Used as-is

| what | from | why keep it |
|---|---|---|
| `USBSerialDevice` (CDC-ACM) | `luna.gateware.usb.devices.acm` | Production AUX console on `aux_phy` (`top.py`). Pulls in luna's USB device stack, ULPI/UTMI and packet layers (44 files, 7817 code lines, `scripts/tools/luna_closure.py --only soc`). 195.4 Mbps loopback was measured on probes deleted in `cf62c6d` ([`usb-performance.md`](usb-performance.md)); not re-measurable today |
| `ULPIRegisterWindow` | `luna.gateware.interface.ulpi` | Register access on both PHYs: `peripherals/ulpi_window.py`, inside `UsbPort` (`peripherals/usb_host.py`) and inside our ULPI translator (`gateware/usbcore/ulpi.py`). Imported for `usbcore` and `usb_host.py` from one place, `gateware/usbcore/ulpi_regwin.py`; replacement is a later stage of [#612](https://github.com/awtoau/cynthion-workspace/issues/612). `aux_phy`'s bus is owned by the console |
| `USBDevice`, stream endpoints, `ControlRequestHandler`, `StreamSerializer` | `luna.gateware.usb`, `luna.gateware.stream` | Simulation models only (`gateware/probes/usb_host/{aoa_phone,target_device,model}.py`), reference devices for the host engine. Not in the production SoC |
| `JTAGRegisterInterface` | `luna.gateware.interface.jtag` | Replaced by our wire-compatible `gateware/probes/jtag_registers.py` ([#204](https://github.com/awtoau/cynthion-workspace/issues/204)). Both baselines (`flash_baseline`, `hyperram_baseline`) switched to it 2026-11-06. Luna's remains only in one deliberate negative control (`scripts/soc/soc_jtag_registers_sim.py`) and upstream's selftest in `repos/cynthion` |
| `amaranth_soc.gpio.Peripheral` | upstream `amaranth-soc` | Six LEDs, power monitor PWRDN, USER button. `INPUT_ONLY` reset mode lets fabric drive the LEDs until firmware asks (LED comment in `top.py`) |
| `AsyncSerial` | `amaranth-stdio` | Bit engine for the Apollo-facing UART; `gateware/soc/peripherals/serial_line.py` adds the pad handling |

## Diverged, with reasons

Technical detail is in [`architecture.md`](architecture.md); this is the policy view (defect, limit, or standard adopted).

| what | ours | why it diverged | detail |
|---|---|---|---|
| Clock generation | `SocClocks` (`gateware/soc/clocks.py`) | **limit.** Upstream: 60/120/240 MHz only, blocked the RISC-V clock sweep. Ours solves `sync` and `usb` together so `usb` is exactly 60 MHz. [#111](https://github.com/awtoau/cynthion-workspace/issues/111) | [architecture](architecture.md#soc-core) |
| Config flash | `Flash` (`gateware/soc/peripherals/flash.py`) on the [#590](https://github.com/awtoau/cynthion-workspace/issues/590) core | **upstream defects + rate.** luna_soc's crossbar starved its controller (CS is a hold, not a request) and the controller's CS was a one-cycle pulse; SCK was `sync`/2. The clean-room core holds /CS per transaction by construction and runs SCK 180 MHz. [#593](https://github.com/awtoau/cynthion-workspace/issues/593) | [flash-soc-peripheral](chips/flash-soc-peripheral.md) |
| UART pad output enable | `SerialLine` (`gateware/soc/peripherals/serial_line.py`) | **upstream defect, a hold expressed as a ready.** `oe = ~tx.rdy` releases at the start of the stop bit. Reproducer `scripts/soc/soc_serial_sim.py`. [#113](https://github.com/awtoau/cynthion-workspace/issues/113) | [architecture](architecture.md#peripherals) |
| Console peripheral | `Uart16550` (`gateware/soc/peripherals/uart16550.py`) | **standard adopted.** Published register map that QEMU also models: one driver for board and test gate | [architecture](architecture.md#peripherals) |
| Interrupt controller | `amaranth_soc.csr.event.EventMonitor`, wrapped by [`gateware/soc/cpu/intc.py`](../gateware/soc/cpu/intc.py) | **upstream, adopted.** luna_soc's is reserved by policy and shaped for VexRiscv's in-CPU CSRs; VexiiRiscv's is Tilelink-only | [architecture](architecture.md#interrupts-and-time) |
| I2C master | `I2CMaster` (`gateware/soc/peripherals/i2c_master.py`) | **nothing upstream.** `amaranth-soc` has no I2C; `amaranth-stdio` is `serial.py` only. Register map: OpenCores I2C-Master Core rev 0.9 | [architecture](architecture.md#peripherals) |
| `amaranth_soc` | upstream, not luna_soc's vendored copy | **stale vendor.** Vendored tree reported version `unknown`, four commits behind | [architecture](architecture.md#dependencies-and-verification) |
| Board platform | vendored `gateware/board/` | **limit.** Upstream's route inherits `LUNAApolloPlatform` -> `LUNAPlatform` and the luna-soc fork pin. Imports only `amaranth` and `amaranth.build`. All of `gateware/` uses it (`soc/top.py` since [#416](https://github.com/awtoau/cynthion-workspace/issues/416)) | below |
| CONTROL port request | `SidebandAdvertiser` (`gateware/probes/sideband/sideband_advertise.py`) | **incompatible with the pin's other use.** `ApolloAdvertiser` drives FPGA_ADV as a 50 Hz square wave (20 ms), which cannot share the wire with the sideband UART. Apollo's `FPGA_ADV_MODE_UART` already defines the alternative, frame `C1 14 01 A5`; no gateware emitted it. [#137](https://github.com/awtoau/cynthion-workspace/issues/137) | [architecture](architecture.md#peripherals), [`chips/cynone-sideband.md`](chips/cynone-sideband.md#8-the-second-job-the-control-port-request) |

A replacement keeping luna-soc's SVD generator working must keep `InterruptController.add(peripheral, name=, number=)` and `interrupts()`; retired example: `debris/code/gateware/soc_cpu_irq.py`.

### Vendored board platform

- Files: `cynthion_r1_4.py` (pin map, byte-identical to upstream from the class declaration down), `core.py`, `resources.py`, `__init__.py`.
- Package is `board`, not `platform`: `gateware/` is on `sys.path` and `platform` would shadow the stdlib module (`amaranth/tracer.py` imports it). Do not rename.
- `amaranth_boards` is not installed; upstream's copy lives inside the `cynthion` package and is injected into `sys.modules` from its `__init__`. `LEDResources` and `ULPIResource` are copied verbatim into `resources.py` (a tidied version could change the pin records).
- Dropped as dead:
  - `toolchain_program`/`toolchain_flash`/`toolchain_erase`/`_ensure_unconfigured` (every `build()` passes `do_program=False`).
  - All of `LUNAPlatform`; `LUNAApolloPlatform.apollo_gateware_phy`; the `LunaECP5DomainGenerator` default.
- Load-bearing, kept:
  - `pseudo_power_supply_fragment` (with `prepare()`): r1.4 strands I/O balls to VCCIO/GND that must be driven; Amaranth leaves unrequested pins undriven; no build error otherwise.
  - `toolchain_prepare` sets `--freq 38.8`, the SPI configuration clock the board reads its bitstream at on power-on.
  - `DEFAULT_CLOCK_FREQUENCIES_MHZ`, read by `gateware/probes/adv_speed/adv_speed_gateware.py` to size a UART divisor.
- Equivalence was checked by running nextpnr both ways (utilisation and per-signal balls identical on `blinky`, 59 pins; `wide`, 105 pins): `platform_vendor_compare.py`, now in `debris/scripts/`.

## Still taken from upstream: Cynthion-specific work

| what | where | likely use |
|---|---|---|
| USB analyzer | `cynthion` gateware | capture and decode |
| Facedancer gateware | `cynthion/.../gateware/facedancer` | device emulation; also the SoC moondancer targets |
| `flash_bridge` | `apollo_fpga.gateware` | `flash --fast`, FPGA-side flash writer |
| selftest bitstream | `cynthion.selftest` | used by some scripts |

- Generic peripherals are not on this list.
- Moondancer targets `riscv32imac`, expects `blockram` at `0x00000000` and `spiflash` at `0x100b0000` (both present), and needs leds, gpio0, gpio1, timer0, timer1, spi0 and a USB peripheral. It depends on the register interface, not the implementation: supply the map, not the module.
- Rule: take what is about this board; write or take from a published standard map anything generic; replace what is measurably broken or limiting; vendor the file, not the stack.

## Patches carried against dependency trees

Source patches, tracked in this repository's issues. `facedancer` and `luna` are not submodules here (submodules: `repos/apollo`, `repos/cynthion`, `repos/cynthion-hardware`, `repos/vexiiriscv`); the facedancer patches apply to a facedancer checkout outside this tree.

| Issue | Component | File | What |
|---|---|---|---|
| [#8](https://github.com/awtoau/cynthion-workspace/issues/8) | facedancer | `configuration.py` | skip pre-interface descriptors (e.g. IAD) before the first interface |
| [#9](https://github.com/awtoau/cynthion-workspace/issues/9) | facedancer | `backends/base.py` | duplicate-endpoint-address exception downgraded to a warning (UVC alt settings) |
| [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | facedancer | `backends/moondancer.py` | deduplicate endpoints by address before `configure_endpoints` |
| [#43](https://github.com/awtoau/cynthion-workspace/issues/43) | moondancer | `gcp/moondancer.rs` | clamp endpoint `max_packet_size` to 512 (HS limit) instead of rejecting SuperSpeed devices |
| [#65](https://github.com/awtoau/cynthion-workspace/issues/65) | apollo | `uart.c`, `console.c`, `vendor.c`, `apollo_mode.c` | JTAG/UART arbitration on shared PA11/PA14: [`hardware.md`](hardware.md#pin-sharing--the-two-hazards) |

## HyperRAM: written, not taken

- Clean-room core (`gateware/hyperram_baseline/`, [#581](https://github.com/awtoau/cynthion-workspace/issues/581)) from the datasheet and public designs; nothing from luna's `psram.py`.
- HyperRAM: register interface on the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core at CK 180, see [docs/chips/hyperram/soc-peripheral.md](chips/hyperram/soc-peripheral.md) ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)).

## Vendored: the USB host engine

- `gateware/probes/usb_host/guh/` is `apfaudio/guh`'s `usbh` at commit `923c8490`, verbatim: `sie.py` (transaction engine), `reset.py` (bus reset, host half of the HS chirp), `types.py`. BSD-3-Clause, licence beside them.
- GUH: no releases or tags, one author, "interfaces will change" warning, does not accept contributions.
- Pin is `923c8490`, not `fbd7077`: three SoC-facing files at `fbd7077` carried CERN-OHL-S-2.0 (copy-paste leftover, `apfaudio/guh#1`, fixed 2026-07-30).
- Not taken: `enumerator.py` and `descriptor.py` (discard descriptors, hard-code the device address, specialise the parser at synthesis time: [`usb-host-options.md`](usb-host-options.md) section 16), `engines/*`, `periph/*`.
- Files stay byte-identical so a pin bump is a diff. Additions go beside the package: `gateware/probes/usb_host/model.py` is ours.
- Exception, the import lines ([#612](https://github.com/awtoau/cynthion-workspace/issues/612) S1): `sie.py`, `reset.py`, `types.py` import their lower layers from `gateware/probes/usb_host/guh_lower.py` instead of luna. `guh_lower.py` gives luna's names (`UTMITranslator`, `UTMIInterface`, `USBDataPacketReceiver`, ...) on top of our `gateware/usbcore/`; a pin bump re-applies those three lines.
- Exception, the SIE FIFOs ([#619](https://github.com/awtoau/cynthion-workspace/issues/619)): `sie.py` drops `fifo` from its `amaranth.lib` import and adds `from ..guh_lower import fifo`, so `fifo.SyncFIFO` (tx_fifo, rx_fifo) is `gateware/usbcore/fifo.py`: block RAM (DP16KD) instead of distributed RAM, same cycle behaviour as amaranth's `SyncFIFO` (bypass register for a write into an empty FIFO). No engine logic changed; a pin bump re-applies those two lines. Proof: `scripts/soc/sie_fifo_equiv_sim.py` (FIFO alone in lockstep at six depths; whole engine on the LUNA bench, per-cycle trace identical; negative controls).
- `scripts/soc/usbcore_equiv_sim.py` runs each `guh_lower` class in lockstep with luna's (every output, every cycle) and cross-decodes packets between ours, luna's and a Python reference.
- `scripts/soc/usb_host_sie_sim.py` asserts the engine against LUNA's device stack, including the three interface traps a CPU-facing shim must handle.

## The three luna-soc defects

All in `greatscottgadgets/luna-soc`, none in `luna`. Cynthion's own SoC instantiates the trigger combination (`with_controller=True, with_mmap=True`): their code on their hardware. Not reported (see [No upstreaming](#no-upstreaming)).

| defect | reproducer | why upstream never hit it |
|---|---|---|
| SPI crossbar starvation | `riscv_flash_crossbar_sim.py` (now in `debris/scripts/`) | needs flash memory-mapped and arbitrary commands; upstream grants at cycle 0 when the map is idle and never in 600 cycles when it holds `cs` |
| CS hold as one-cycle `csr.action.W` | ILA capture: CS fragmented into four 8-bit windows during a JEDEC read | moondancer's `read_flash_uuid` writes all 13 command bytes into the 16-deep FIFO before reading, so `r_rdy` never drops |
| `oe = ~tx.rdy` releasing the stop bit | `scripts/soc/soc_serial_sim.py` | released line RC-charges through a pull-up and usually arrives in time; presents as intermittent corruption, not a dead link |

## Carried commits in the forks

- Apollo fork vs upstream: [`apollo-firmware.md`](apollo-firmware.md) owns the description. This section keeps the per-commit reasons.
- Counts go stale; compute: `git -C repos/apollo log --oneline upstream/main..HEAD | wc -l` (same for `repos/cynthion`).
- Hashes read from `git log` on 2026-07-30; local history can be rebased, confirm with `git show`.
- `luna` is installed from upstream main (`352cdbf`, 0.2.3.post15) with no commits of ours; `patches/luna-amaranth-0.6.patch` (a copy of amaranth 0.5.10's `Record`, a copied `Encoder`, public imports) is applied only by `scripts/tools/amaranth_main_probe.py`. `luna_soc` is our `awto-luna-soc` fork. `facedancer` sat on upstream tag 3.1.3 when last checked.

### Apollo firmware fixes (d11)

| Commit | What |
|---|---|
| `39a2213` | Sticky JTAG/UART pin lock. r1.4 console UART and JTAG share PA10/11/14/15 with no arbitration; a CDC line-coding callback during JTAG re-pinmuxes PA14. `apollo_mode` HOLD<->JTAG_PROGRAM lock gates three CDC callbacks and `uart_configure_pinmux()`. Tests: `firmware/test/test_apollo_mode.c`, `JTAGUARTExclusionHILTest` |
| `df4a93b` | Boot-to-DFU vendor request `0xed` + host `boot_to_dfu()`. Reboot deferred to the ACK stage: `tud_dfu_runtime_reboot_to_dfu_cb()` arms the WDT and hangs, so doing it in the setup handler makes success look like a USB failure. Test: `BootToDFUHILTest` (`APOLLO_TEST_ALLOW_REBOOT=1`) |
| `6cc219e` | Vendor-request gating during JTAG programming, two-stage: `JTAG_START` takes the pin lock, the first programming-class request escalates to `MODE_JTAG_PROGRAMMING`, then reconfigure / force-offline / takeover / debug-SPI are refused. `EMERGENCY_RESET` (`0xec`) is the one preemption path. `test_apollo_mode.c` has 31 checks |
| `da564f8` | ISR/main-loop races: `fpga_online` not `volatile`; `edge_counter` read-then-clear in `fpga_adv_task()` not atomic against `EIC_Handler`. No dedicated test |
| `973fa78` | `CFG_TUD_VENDOR=0` on d11 (vendor class driver unused; Apollo vendor requests are control transfers via `tud_vendor_control_xfer_cb()`). -520 B ROM, -680 B RAM. Scoped to `cynthion_d11`; d21/qtpy/daisho/xplained/pico untested |
| `651b027` | `msft_10_compat_id` / `msft_10_ext_props` `const` (in `.rodata`). -176 B RAM |
| `01ae228` | `static const` on `board_rev` threshold table, LED pin table, `jtag_deinit()` `gpio_pins[]`; internal linkage for 13 `vendor.c` handlers and 2 `jtag_tap` helpers. -276 B |
| `4bf7691` | LTO (`firmware/Makefile`: `-flto=auto -flto-partition=one -Wl,--entry=Reset_Handler -Wl,-u,exception_table`) + `fpga_adv.c` UART timeout folded to a constant (removes runtime `__udivsi3`, 266 B) + sideband `0xFFFE` sub-command in `vendor.c` (ours) |

### Apollo host Python

| Commit | What |
|---|---|
| `8054f62` | `FlashBridgeConnection` is a context manager. Handing the port back is a USB transfer; from `__del__` it ran at GC/shutdown with libusb context gone: segfault, port left with the FPGA, Apollo appeared to vanish. [#75](https://github.com/awtoau/cynthion-workspace/issues/75). Test: `test_4_flash_fast_is_not_broken` (`APOLLO_TEST_ALLOW_FLASH_FAST=1`) |
| `6f16848` | `apollo install-udev`. Shipped rules cover `1209:000a` and `000e`, not `000f` (the flash bridge), so `flash-fast` failed EACCES and wedged USB. No test (writes `/etc/udev/rules.d`) |
| `12e6d97` | `apollo exit-dfu`. No test (see below) |
| `e034daa` | ECP5 SRAM configuration 1.53x faster: host `ignore_response` through the bitstream burst (write-only burst was doing a `GET_IN_BUFFER` per chunk, 739 of 2575 ms) + firmware SERCOM SPI pipelining (+28 B). An earlier racy pipelining silently corrupted TDO and gave an all-zero status register: assert status is non-zero and DONE, never just "no exception" |
| `24b3b7a`, `daecad7`, `9c72930` | Test suite: CLI smoke tests, command responses and flash write paths (`APOLLO_TEST_ALLOW_FLASH_WRITE`), PAC1954 HIL tests. Upstream has no Python test suite |

### Apollo, ours (investigation, not fixes)

- Sideband / FPGA_ADV command link: `4a46b24`, `a7b8283`, `b48d4bf`, `ca32e5b`, `9ec249a`, `6cdc7c3`, `0b1529a` (+ feature hunks of `4bf7691`).
- Quad-SPI flash: `9b8ffb8`, `2418362`, `f42433d`, `1e90e5c`, `ace1fd8`, `c3c1911`, `4ead7df`, `42bffe6`, `7a8e304`.
- PLL (`ecppll` variable clock): `0ab00de`, `c3c5fbe`.
- `74db0e6`: synthetic JTAG benchmark on vendor request `0xb9` (moved there to clear a collision).
- Vendor request numbers allocated locally: `0xed` boot-to-DFU, `0xec` emergency reset, `0xb9` JTAG benchmark, `0xFFFE` `FPGA_ADV_MODE` sub-command. Upstream owns that number space; local allocations have collided once.

### Cynthion (`repos/cynthion`)

| Commit | What |
|---|---|
| `c0144f4` | wrong function name in a `write_endpoint` log message |
| `56a64c2` | duplicate `ep_iso_in` removed from the facedancer module hierarchy (elaboration bug) |
| `fc81a06` | `make clippy` passes ([#35](https://github.com/awtoau/cynthion-workspace/issues/35)) |
| `7378797` | event-queue overflow drops the event instead of spinning |
| `6fa3e0a` | clamp endpoint `max_packet_size` to `EP_MAX_PACKET_SIZE` |
| `edf35c9` | udev rule for the Apollo flash bridge (`1209:000f`); same bug as apollo `6f16848` |
| `482bcf2` | integer overflow checks in all profiles |
| `53d3ea4` | `__init__` methods on CSR Register classes for Amaranth 0.5.x: redundant now (the amaranth-soc fix is the right layer; see fork pin above); revert locally |

Ours: `d036598`, `b0c06d7` (rebuilt `moondancer.bin`, `make bin`); `4d8688c`, `131927a` (stack canary), `482bcf2` (fault injection / hardening); `4ab5361` (iso IN skeleton, incomplete); `ef9addb`, `7fa0c6a` (FPGA_ADV UART sideband streamer, r1.4 bidirectional-with-pull-up platform change); `4fd1f7a`, `5848814`, `efd5009`, `593cf4a` (`apollo-mux.py`, `apollod.py`, `reset-cynthion.sh`); `5745112`, `6863fd6`, `1d24249` (`awto.md` docs); `24af0a4`, `1d08505`, `c8b591e` (`.gitignore`).

- `593cf4a` and `efd5009` add a `.sh` file, which violates the workspace no-shell-scripts rule: local cleanup.

### Apollo ROM budget and LTO

- Application region: 14336 B = 16 KB flash less `BOOTLOADER_SIZE = 0x800` (linker `--defsym` from `board.mk`; Saturn-V uses 2016 of 2048 B).
- Upstream `upstream/main` d11 built at 13768 B (96.04%, 568 B free); LTO switch alone: 10800 B (75.33%, 3536 B free). Measured 2026-07-30 with `apollo_rom_sizing.py` (now in `debris/scripts/`).
- **LTO silent failure:** a naive enable links and yields a 4-byte binary. The SAMD11 linker script has no `ENTRY()` and builds the vector table from weak aliases, so the plugin prunes everything. Roots must be named: `-Wl,--entry=Reset_Handler -Wl,-u,exception_table`. Guard: `scripts/apollo/verify_vectors.py` asserts Reset, SysTick, USB, EIC, SERCOM1, TC1 are real code, not `Dummy_Handler`.
- `--gc-sections` hides missing features: absence of a symbol can mean "never called". Under LTO it may mean inlined; confirm in disassembly.
- Ordering constraint: `01ae228` conflicts if applied before `39a2213`, `df4a93b`, `6cc219e` (they restructure `jtag_deinit()` and the `vendor.c` dispatch tables). `651b027` conflicts with those if reversed.
- Semantic conflict: 39a2213 + df4a93b + 6cc219e cost +336 B; without LTO that leaves 232 B free (98.38%).

### `exit-dfu` (`12e6d97`)

- The Saturn-V bootloader cannot reset itself out: it stays when the reset cause is the watchdog (`saturn-v/main.c:157`), and arming the watchdog is the only reset Apollo can perform. `PM_RCAUSE_POR` needs power removed.
- Way out: zero-length `DFU_DNLOAD` is "download complete"; `dfu.c:12` turns it into `dfuMANIFEST_SYNC` without writing firmware; `dfu_cb_manifest()` sets `exit_and_jump`; main loop calls `NVIC_SystemReset()` (`PM_RCAUSE_SYST`, so the next boot runs the application).
- Must be `DFU_DNLOAD`, not `DFU_DETACH` (the bootloader has no detach case).
- The interface must be claimed, or the transfer stalls `LIBUSB_ERROR_PIPE` (reads as a refused request).
- Verified on hardware: `boot-to-dfu` then `exit-dfu` returns a working debugger with no replug.

### INITN gap (diagnosed, unfixed)

- `trigger_fpga_reconfiguration()` (`boards/cynthion_d11/fpga.c:52-77`) pulses PROGRAMN and calls `fpga_set_online(true)` but never releases INITN (open drain, must be high). r1.4 has a pull-down and no pull-up; the only caller of `permit_fpga_configuration(true)` is MCU startup (`main.c:74`, `:81`).
- Evidence: status register Fail with BSE error 0; same image loads over JTAG; every host trigger fails with `INITN=0`.
- Candidate fix: call `permit_fpga_configuration(true)` before pulsing PROGRAMN. Not attempted. See [`chips/ecp5/reconfigure-initn-gap.md`](chips/ecp5/reconfigure-initn-gap.md).

## Upstream CI

`scripts/tools/upstream_ci.py` clones a GSG repo into `tmp/`, checks out the named ref and runs the commands its own workflows run. The clone is destroyed and re-made every run, so it cannot touch `repos/*`.

    ./scripts/upstream_ci.py luna                       # upstream main
    ./scripts/upstream_ci.py luna --pr 301              # someone else's PR head
    ./scripts/upstream_ci.py apollo --pick 0e9bfb1      # our commit on clean upstream
    ./scripts/upstream_ci.py apollo --job host-tests    # one job
    ./scripts/upstream_ci.py luna --list                # show jobs, run nothing

- `--pick` cherry-picks from `repos/<repo>` onto clean upstream.
- Full history is cloned only with `--pick`/`--patch`; `git apply -3` and `git cherry-pick` fail on `--depth 1` ("repository lacks the necessary blob").
- Pico firmware job fails locally (Fedora GCC 15.2.0 vs pico-sdk; upstream pins an older compiler via `carlosperate/arm-none-eabi-gcc-action`). Run the control (same job, no patch) before attributing a failure to a patch.

| repo | upstream automated checks on a PR |
|---|---|
| `luna` | GH Actions `simulations`: `unittest discover -t . -s tests`, Python 3.9-3.13. Jenkins HIL job (`ERROR` on every open PR, logs unreadable to us) |
| `apollo` | GH Actions `python` (`unittest discover -p jtag_svf.py`) and `Firmware` (6-board matrix, `firmware.yml`) |
| `cynthion` | GH Actions `Python` (`python.yml`): unit tests + analyzer elaboration, 3 OSes x 5 Pythons, weekly |
| `saturn-v` | `build.yml`: bootloader on 2 platforms, weekly |
| `packetry` | 2 workflows: build + AppImage |
| `facedancer`, `luna-soc` | none |

- Upstream CI is weak signal: LUNA's Actions gate is unit tests only (a PR fixing an inverted fanout in `HandshakeExchangeInterface` passes identically with and without the fix).
- First-time-contributor fork PRs do not run Actions until a maintainer approves.
- Workflows in the forks are kept (deleting them diverges the forks and conflicts on sync). None build FPGA bitstreams, moondancer firmware, or analyzer/facedancer gateware.
- This workspace runs nothing on GitHub: [`../README.md`](../README.md#checks-run-locally-not-on-github).

## Building upstream's code, unmodified

Rule: a number from our tree is not a number about upstream. Our forks carry our commits, pins and fixes; comparisons must come from their code built their way.

- Use a container, not a venv. `repos/cynthion/BUILD.md`'s install step is `pip uninstall -y -r <(pip freeze)` against the active environment, which here is the 3.15t with the whole workspace; a venv does not protect against it. Upstream pins Python 3.11 (host: 3.15t) and ships no Dockerfile/nix/devcontainer.
- The tool `scripts/upstream_build/build.py` described in earlier docs is not in the tree (UNVERIFIED where it went, [#200](https://github.com/awtoau/cynthion-workspace/issues/200) tracks the container).
- Traps, each a hard failure or a plausible wrong number:
  1. `bash -lc` re-reads `/etc/profile`, resets `PATH`, drops the venv; `pip` becomes the system pip (PEP 668 refusal). Use `bash -c`, absolute interpreter paths.
  2. `repos/cynthion` is OUR fork (`awtoau/awto-cynthion`, `pyproject.toml:47` points luna-soc at the fork). Building it measures our variant. Tell in pip output: `luna-soc-0.3.2+awto.1` is ours, `luna-soc==0.3.2` is upstream's. Clone `greatscottgadgets/cynthion` at a pinned tag (`--ref`, default `0.2.5`) and print installed `luna`/`amaranth`/`cynthion` versions.
  3. `cynthion/python/src/shared` is a symlink to `../../../shared/`; copying only `cynthion/python` leaves it dangling (`ImportError: cannot import name 'usb' from 'cynthion.shared'`). Build in place in the clone.
  4. `Makefile:1` is `SHELL := /bin/zsh`; the image needs `zsh`.
  5. Amaranth builds in a temp dir (`Build directory: /tmp/tmpXXXXXXXX`); only the `.bit` is copied out, so `top.tim`/`top.rpt` vanish. Tell the build to keep its files.
- Pin the OSS CAD Suite by date, not `latest`, and the upstream ref.
- Isolation: fresh clone into a tmpfs (never a bind mount of ours); only `tmp/upstream-build/<target>/` writable; it is gitignored.
- Patch check: build at the pinned ref (before), apply, rebuild (after), compare utilisation and behaviour.
