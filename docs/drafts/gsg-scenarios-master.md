# Port Great Scott Gadgets' Cynthion scenarios to our SoC (draft issue, not filed)

- Upstream ships three bitstreams and documents about a dozen scenarios; we replaced luna-soc with our own VexiiRiscv SoC.
- Reference half (what each scenario is, what implements it, wire contracts, website-vs-code gaps): [../gsg-scenarios.md](../gsg-scenarios.md). This file: porting cost and test tier per scenario. Commits us to nothing.
- Per-scenario child drafts were merged into the section below; originals in [../history/soc-usb.md](../history/soc-usb.md).

## The fact that shapes every child

This SoC has no USB device controller and no USB packet path on the TARGET port.

- Peripherals: `ram`, two 16550 consoles, `gpio`, `i2c`, `sideband`, `target_ulpi`, `i2c_mux`, `vbus`, `fabric_status`, interrupt controller (`gateware/soc/cpu/intc.py`), `clint`, `spiflash`; HyperRAM register peripheral at `0xf0000800` ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)).
- `gateware/soc/peripherals/ulpi_window.py` reads/writes USB3343 registers over `target_phy`; it cannot send or receive a packet.
- Moondancer expects `usb{0,1,2}` + `ep_control` + `ep_in` + `ep_out` CSR peripherals; none exist here.

## What exists

| asset | where | why |
|---|---|---|
| own board platform | `gateware/board/cynthion_r1_4.py` | `control_phy`, `aux_phy`, `target_phy`, `apollo_port_sharing` declared |
| luna USB device on AUX | `gateware/probes/usb_serial/usb_serial.py`, in `gateware/soc/top.py` | `USBSerialDevice` enumerates at high speed; luna's USB gateware builds and runs here ([usb-performance.md](../usb-performance.md)) |
| CONTROL port handover | `gateware/probes/sideband/sideband_advertise.py`, [sideband](../chips/cynone-sideband.md), `scripts/sideband_advertise_sim.py` | upstream's `ApolloAdvertiser` prerequisite solved |
| VBUS switch control | `gateware/soc/peripherals/vbus_csr.py` | CPU-visible home for pass-through / port power |
| HyperRAM | register interface on the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core at CK 180, see [docs/chips/hyperram/soc-peripheral.md](../chips/hyperram/soc-peripheral.md) ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)) | analyzer's 8 MiB capture buffer |
| PAC from the SoC's own map | `./dev.py pac` | new peripherals get Rust accessors |
| `smolusb::control::Control` | upstream, trait-based over `D: UsbDriver` | hardware-independent control-transfer state machine; ports verbatim |

## Test tiers

- `./dev.py test` runs `qemu-system-riscv32 -M virt` (16550, interrupt/CLINT model, nothing of ours: no ULPI, USB, HyperRAM, `fabric_status`). Tests firmware logic not touching a PHY: parsers, dispatch, descriptor construction, `smolusb` control state machine against a mock.
- `./dev.py sim` runs 15 Amaranth pysim simulations (`scripts/soc_sims.py`), 9 sub-second and in the pre-commit gate. No USB or ULPI simulation exists; adding one is the highest-leverage item (the only tier that tests a packet path without a board).
- `./dev.py test-board` runs the shell suite on hardware. `run`, `console`, `flash` are `needs_hardware`, excluded from `gate` and `ci`.
- Each scenario states its tier; "hardware only" is said as such.

## Shared prerequisites

- **P1. ULPI packet path on `target_phy`.** Today it reaches only `ulpi_window.UlpiRegisters`. Scenarios 1, 3, 4 need `luna.gateware.interface.ulpi`'s full PHY plus a UTMI consumer. Same ownership problem as [usb-host-options.md](../usb-host-options.md) section 7.1.
- **P2. ULPI simulation.** Bus-functional ULPI model in `scripts/`; before scenario 1.
- **P3. Host-side board layer.** Upstream matches VID:PID `1d50:615b` with interface subclass `0x10` (analyzer) or `0x20` (moondancer). Match the descriptors and Packetry/Facedancer work unmodified.

## Scenarios

| # | scenario | upstream | verdict |
|---|---|---|---|
| 1 | USB protocol analyzer | `analyzer.bit`, no firmware | Hard |
| 2 | Analyzer built-in test device | in `analyzer.bit`, AUX PHY | Portable |
| 3 | Facedancer device emulation | `facedancer.bit` + `moondancer.bin` | Hard |
| 4 | USB proxy / MITM | same as 3 | Free once 3 lands |
| 5 | USER I/O from Facedancer | same as 3 | Portable |
| 6 | Self-test | `selftest.bit` | Portable, possibly redundant |
| 7 | Bitstream configuration and flashing | Apollo + `flash_bridge.py` | Already done, differently |
| 8 | MCU firmware update and recovery | Apollo + Saturn-V DFU | Already done |
| 9 | Bulk throughput test | `luna/.../speed_test.py` | Portable |
| 10 | Custom gateware tutorials | tutorials, luna `USBDevice` | Portable (compatibility audit) |
| 11 | Factory hardware validation | `cynthion-test` + Tycho | Needs hardware we lack |

### 1. Analyzer (Hard)

Ports: TARGET carries the traffic, CONTROL carries the stream (two cables). No firmware, PAC or libgreat; pure gateware.

- Needed: P1; the capture engine (`USBAnalyzer` ~400 lines Amaranth, no luna-soc dependency: take or rewrite); a HyperRAM stream port; a bulk IN endpoint on CONTROL with the five vendor requests and descriptors; port request (solved); VBUS switching driven from gateware state (analyzer has no CPU; `vbus_csr.py` is CPU-driven).
- Upstream's `HyperRAMPacketFIFO` instantiates luna's `HyperRAMInterface`/`HyperRAMPHY`; ours is the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core behind a register interface ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)), so the stream port is new work on that core.
- Wire contract: VID:PID `1d50:615b`; interface class `0xff`, subclass `0x10`, protocol `0x01`; capture stream bulk IN `0x81`, 16 KiB reads, 4 in flight; vendor requests `0` get state, `1` set state, `2` get speeds, `3` set test-device config, `4` get protocol minor version; state byte = capture-enable bit + 2-bit speed selector.
- Tests: QEMU no. Sim yes (capture engine + FIFO in `gate`, needs P2). Hardware required for Packetry interop and real-chirp speed detection (`test-board`, not `gate`).

### 2. Analyzer test device (Portable)

AUX-to-TARGET loopback cable; VID:PID `1209:000a`; interrupt endpoint at Low/Full/High under vendor request `3`; not documented on the website.

- Smallest port: `USBSerialDevice` on `aux_phy` is already a luna `USBDevice`; swap CDC-ACM for an interrupt-endpoint function (descriptor + endpoint handler). Three-speed switching (separate descriptor sets per speed, standard request handler each) is the only new part. No CPU, HyperRAM, libgreat.
- Tests: sim yes (luna `USBDevice` against a host model, fast tier); hardware cheap (AUX enumerates as `1209:000a`, `test-board`).
- Value is as a test fixture for scenario 1; alone it proves only what `USBSerialDevice` already proves.

### 3. Facedancer (Hard)

Ports: CONTROL to the control host, TARGET-C/A to the host being fooled. Largest payoff: 4 and 5 follow at near-zero cost.

- Half one, gateware: three USB device peripherals upstream (`usb{0,1,2}` x `ep_control`/`ep_in`/`ep_out`); only `usb0` (TARGET) is needed for basic emulation (state that scope reduction explicitly). Needs P1 and a CPU-attached endpoint peripheral set raising interrupts through the event controller ([soc-interrupts.md](../soc-interrupts.md)).
- Half two, firmware: six libgreat/GCP classes (`core`, `firmware`, `selftest`, `gpio`, `leds`, `moondancer`); verb table, class registry, transport (vendor request `0x65`, bulk IN `0x81`, bulk OUT `0x02`, `cynthion: shared/libgreat.toml`) must exist; no GCP dispatch in `firmware/cynthion-soc` today. `smolusb::control::Control` ports verbatim.
- Host: descriptors VID:PID `1d50:615b`, subclass `0x20`, protocol `0x00` drive the unmodified `facedancer` package.
- Tests: QEMU partially (GCP dispatch, verb table, descriptor construction, `smolusb` against a mock `UsbDriver`, in `./dev.py test`). Sim yes for the endpoint block (needs P2). Hardware for end-to-end; upstream's firmware unit tests (`cd firmware/moondancer/ && python -m unittest`) need CONTROL and AUX both cabled and are worth reading as spec.

### 4. USB proxy (free after 3)

Nothing on the device: same bitstream/firmware as 3. Host side `facedancer: facedancer/proxy.py::USBProxyDevice`, `filters/`. Needs a third connection (real device on the control computer's port); macOS needs root.

- Proxying is latency-sensitive (host timeouts not adjustable): doubles as the performance regression test for 3.
- Tests: hardware only (two hosts, a third device); manual acceptance check after 3, not CI.

### 5. USER I/O (Portable)

- Present: six USER LEDs from `amaranth_soc.gpio` at `GPIO_BASE` in `top.py`; USER button and Pmod A pins declared; per-pin direction control.
- Missing: host-to-register path. Upstream uses GCP (inherits 3's transport). Alternative: `leds` and `gpio` commands on the console shell over the AUX CDC-ACM tty; not compatible with `cynthion.Cynthion()`. Near-free later if GCP is built for 3.
- Tests: QEMU yes for the command layer; sim yes (extend `soc_board_sim`); hardware for the final claim.

### 6. Self-test (Portable, possibly redundant)

CONTROL only, one cable. Tests are register reads over a debug transport.

- PHY tests read USB3343 ID registers: `ulpi_window.py` does this for `target_phy` (four-phase handshake, bounded wait); extending to `aux_phy` and `control_phy` is a resource-request change (taking `control_phy` off Apollo needs the port request, built). HyperRAM test: ID registers. Debug-connection test: Apollo JTAG, no FPGA design.
- Transport differs: upstream JTAG-tunnelled; ours CPU CSR bus (plus sideband; `gateware/soc/bus/jtag_stage.py`, `soc_jtag_stage_sim`).
- Compare first: `soc_i2c_owner_sim`, `soc_typec_sim`, `soc_board_sim`, `./dev.py test-board` may already cover upstream's five cases (`test_debug_connection`, `test_host_phy`, `test_target_phy`, `test_sideband_phy`, `test_hyperram`).
- Tests: QEMU no. Sim yes for register paths (`ulpi_window` handshake has no pysim test; worth closing regardless). Hardware: run against a board believed good first; a diagnostic's first run is the control.

### 7. Flashing (done, differently; audit)

Ours: `./dev.py run` (build + configure), `fw` (flash + reconfigure, no resynthesis), `stage` (firmware over JTAG), `flash` (read + verify SPI flash); `scripts/fast_loader.py`; `gateware/soc/peripherals/flash.py`; `scripts/flash_backup.py`. SPI flash at `0x1000_0000` like upstream.

- Audit: (1) does `apollo flash-fast`'s bridge bitstream beat `fast_loader.py` (measurable); (2) Moondancer lives at `0x000b_0000`: that offset is the contract if `cynthion flash --soc-firmware` should work against our bitstream; (3) should `cynthion run|flash <name>` reach our bitstreams, or only `./dev.py` (audience not asked).
- Tests: QEMU partially (flash layout/staging); sim done (`qspi_burst_sim`, `soc_bus_sim`); hardware done (`./dev.py flash`). Flash erase/program are authorised; Apollo recovers over JTAG.

### 8. MCU firmware (done; no SoC involvement)

- `apollo.bin` is committed prebuilt upstream; we build from `repos/apollo`, guarded by `scripts/apollo_budget_check.py` and `scripts/apollo_memory_report.py` ([apollo_samd11_mcu/](../apollo_samd11_mcu/apollo_change_process.md)).
- Keep the DFU contract: `1d50:615c`, alt 0 `Flash`, alt 1 `SRAM`, or `dfu-util` and `cynthion update` stop working. Initial Saturn-V install needs an SWD programmer (Black Magic Probe, J-Link, OpenOCD) on the `uC` header.
- Tests: hardware only; flash-budget and vector-table checks are static and catch "links but does not boot".

### 9. Bulk throughput (Portable, largely achieved)

- Gateware `luna: luna/gateware/applets/speed_test.py` (product "Bulk Speed Test", manufacturer "Luna Project", `cynthion: shared/usb.toml`); firmware variant `firmware/moondancer/examples/bulk_speed_test.rs` measures USB path plus CPU feed (needs 3's peripherals; skip).
- Measured on this platform: [usb-performance.md](../usb-performance.md). Carry over: `max_packet_size` defaults to 64 (FS bulk limit) and enumerates at high speed at ~1/8 the achievable rate.
- Tests: sim cannot produce a meaningful throughput number (sim time is not wall time). Hardware only, recorded with packet size, negotiated speed, and host.

### 10. Custom gateware tutorials (Portable; compatibility audit)

- Tutorials `cynthion: cynthion/python/examples/tutorials/` with `test-gateware-usb-device-0N.py` (Blinky, USB Gateware parts 1-4); 13 platform files `cynthion_r{0_1..0_7,1_0..1_4}.py` via `LUNA_PLATFORM`; entry `luna.top_level_cli(Top)`; tutorials recommend YoWASP.
- The question: does `gateware/board/cynthion_r1_4.py` present the same resource names, indices and semantics as upstream's (`led`, `target_phy`, `apollo_port_sharing`, Pmod pin order)? Mechanical, not done. LED indices are colour-ordered (0 red, 1 orange, 2 yellow, 3 green, 4 blue, 5 violet).
- Good signs: `gateware/board/core.py` reports `control_phy: advertising` via `port_sharing()`; `usb_serial.py` builds a luna `USBDevice` against our platform.
- Tests: elaborate every tutorial against our platform in pysim (fast tier, no ULPI model); hardware with upstream's `test-gateware-usb-device-0N.py` as oracle.

### 11. Factory hardware validation (do not port)

`greatscottgadgets/cynthion-test` (`cynthion-test.py`, `selftest.py`, `speedtest.py`, `calibrate.py`, `check.py`, `ranges.py`, `eut.py`; prebuilt `analyzer.bit`, `selftest.bit`, `speedtest.bit`, `flashbridge.bit`), driven from the `cynthion` `Jenkinsfile`.

- Fixture: Cynthion r1.4.0 UUT, Tycho r2.0.0, Sasserides r1.0.0 (A and B stack), GreatFET One, Black Magic Probe, 24 V supply (Mean Well GST25B24-P1J), 3 x ADT-Link UT6A-UT6B-NC cables (U1 pins 1 and 8 connected), 10-pin ARM debug cable, 34-pin ribbon, PASS/FAIL buttons, switched hub, mechanical hold-down. We have none; Tycho and Sasserides are GSG designs.
- Mine for specification: `ranges.py` (expected voltage/current of a good board), `selftest.py`/`speedtest.py` sequencing (scenarios 6, 9), the `Jenkinsfile` per-commit vs per-board split against `./dev.py ci`.
- Our hardware confidence comes from pysim (`./dev.py sim`) and `test-board`: why P2 is a prerequisite.
