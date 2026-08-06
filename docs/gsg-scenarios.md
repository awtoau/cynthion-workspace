# Great Scott Gadgets' Cynthion scenarios

What upstream Cynthion officially does, and what implements it. This is the reference
half: **what each scenario is, and which bitstream, firmware and host tool provide it.**
Porting plans, status and verdicts live in the GitHub issues, not here — this file should
stay true whether or not we ever port anything.

Sources: the Cynthion documentation site (`cynthion.readthedocs.io`), the product page, and
the upstream repositories. Paths below are repo-relative and prefixed with the repo name.
Surveyed against `cynthion` at `dd2340e` (v0.2.5, 2026-05-22).

Where the website and the code disagree, both are recorded. See
[Website versus code](#website-versus-code).

## The board's ports, as the scenarios use them

| Port | Connector | Used for |
|------|-----------|----------|
| CONTROL | USB-C | Host control. Owned by the Apollo MCU at reset; the FPGA can take it over (see [port handover](#port-handover)). |
| AUX | USB-C | Second FPGA-driven port. Analyzer test device, firmware unit tests, user designs. |
| TARGET-C / TARGET-A | USB-C + USB-A | One shared port pair, passed through or driven. Capture and emulation both happen here. |
| USER PMOD A / B | 2x Pmod | 16 FPGA IOs. B doubles as the SoC's UART + JTAG. |

Three ULPI PHYs (`control_phy`, `aux_phy`, `target_phy`), six FPGA-driven USER LEDs, five
MCU-driven status LEDs, one USER button.

## Shipped artefacts

Three bitstreams, built from `cynthion` and shipped inside the `cynthion` Python wheel as
package data under `cynthion/python/assets/CynthionPlatformRev<M>D<m>/`. No `.bit` file is
committed to the repository and the CLI never downloads one — `make assets` builds them.

| Artefact | Built from | Kind |
|----------|-----------|------|
| `analyzer.bit` | `cynthion: cynthion/python/src/gateware/analyzer/top.py` | Pure gateware |
| `facedancer.bit` | `cynthion: cynthion/python/src/gateware/facedancer/top.py` | luna-soc SoC + VexRiscv |
| `selftest.bit` | `cynthion: cynthion/python/src/gateware/selftest/top.py` | Pure gateware |
| `moondancer.bin` | `cynthion: firmware/moondancer/` (Rust, `riscv32imac`) | SoC firmware, flashed at `0x000b_0000` |
| `apollo.bin` | prebuilt, committed at `cynthion: cynthion/python/assets/apollo.bin` | SAMD11 MCU firmware, flashed by DFU |

Two further bitstreams exist outside the wheel: `apollo: apollo_fpga/gateware/flash_bridge.py`
(used by `apollo flash-fast`) and `luna: luna/gateware/applets/speed_test.py`.

## Host tooling

`cynthion` CLI — `cynthion: cynthion/python/src/commands/cli.py`:
`run`, `flash`, `build`, `update`, `info`, `setup`.

`apollo` CLI — from the `apollo_fpga` package: `info`, `jtag-scan`, `flash-info`,
`flash-erase`, `flash-program`, `flash-fast`, `flash-read`, `svf`, `configure`,
`reconfigure`, `force-offline`, `spi`, `spi-inv`, `spi-reg`, `jtag-spi`, `jtag-reg`, `leds`.

`facedancer` and `packetry` are separate host applications; Cynthion is one backend for each.

## Port handover

Only one of the Apollo MCU and the FPGA can own the CONTROL port at a time. Gateware that
wants it instantiates `apollo: apollo_fpga/gateware/advertiser.py::ApolloAdvertiser`, which
toggles the `FPGA_ADV` pin on a 20 ms period; Apollo leaves the port switch on the FPGA for
as long as that advertisement continues, and hands it back when it stops. Vendor request
`0xF0` on the advertiser's interface stops it deliberately. `luna:
luna/gateware/board/core.py::port_sharing()` names this mechanism `"advertising"`.

Every scenario that presents a USB device on CONTROL depends on this. It is a hard
prerequisite, not a detail.

## The scenarios

### 1. USB protocol analyzer (Packetry)

Captures Low-, Full- and High-Speed USB 2.0 traffic passing through the TARGET port pair and
streams it to the host for analysis in Packetry. This is the factory-default bitstream and
the capability the product page leads with.

- Gateware: `cynthion: cynthion/python/src/gateware/analyzer/top.py::USBAnalyzerApplet`;
  capture engine `analyzer/analyzer.py::USBAnalyzer`; HyperRAM-backed packet FIFO
  `analyzer/fifo.py::HyperRAMPacketFIFO` (2^22 x 16-bit); speed detection
  `analyzer/speed_detection.py`; non-packet event detection `analyzer/event_detection.py`.
- Firmware: none. Pure gateware.
- Host: Packetry, `packetry: src/backend/cynthion.rs`.
- Ports: TARGET-C/A carries the traffic under observation, CONTROL carries the capture
  stream to the host.

Wire contract, as Packetry implements it:

| Item | Value |
|------|-------|
| VID:PID | `1d50:615b` |
| Interface | class `0xff`, subclass `0x10`, protocol `0x01` |
| Capture stream | bulk IN endpoint `0x81`, 16 KiB reads, 4 transfers in flight |
| Vendor requests | `0` get state, `1` set state, `2` get supported speeds, `3` set test-device config, `4` get protocol minor version |

The state byte carries the capture-enable bit and a 2-bit speed selector.

The applet also controls the VBUS distribution switches (`target_c_vbus_en`,
`control_vbus_en`, `aux_vbus_en`, `pass_through_vbus`, `power_a_port`) and drives the six
USER LEDs from capture state.

### 2. Analyzer built-in test device

`analyzer/top.py::AnalyzerTestDevice` is a second USB device instantiated on the **AUX** PHY
inside the analyzer bitstream, on board revisions >= r0.6. It presents an interrupt endpoint
at Low, Full or High speed under host control (vendor request `3`), giving the analyzer a
known traffic source to be tested against without a separate device. VID:PID `1209:000a`.

It is not a separate bitstream and is not mentioned in the user documentation.

### 3. Facedancer device emulation (Moondancer)

Emulates a real USB device on the TARGET port, with the device's behaviour written in Python
on the host. The FPGA runs a RISC-V SoC whose firmware ("Moondancer") exposes fine-grained
control of the target USB port over the libgreat command protocol; the Facedancer library's
Moondancer backend drives it.

- Gateware: `cynthion: cynthion/python/src/gateware/facedancer/top.py` — luna-soc SoC,
  VexRiscv CPU, 64 KiB block RAM at `0x0`, 4 MiB SPI flash at `0x1000_0000`, 8 MiB HyperRAM
  at `0x2000_0000`, CSRs at `0xf000_0000` (leds, gpio0/1, uart0/1, timer0/1, spi0, usb0/1/2
  each with `ep_control`/`ep_in`/`ep_out`, advertiser, info).
- Firmware: `cynthion: firmware/moondancer/src/bin/moondancer.rs`, with
  `firmware/moondancer-pac` (svd2rust), `firmware/lunasoc-hal`, `firmware/smolusb`,
  `firmware/libgreat`.
- Host: the `facedancer` package's Moondancer backend, over the `cynthion` Python package's
  board layer (`cynthion: cynthion/python/src/boards/cynthion_moondancer.py`, board ID `0x10`).
- Ports: CONTROL to the control host, TARGET-C/A to the host being fooled.

Wire contract (`cynthion: shared/libgreat.toml`): vendor request `0x65` carries commands;
bulk IN `0x81`, bulk OUT `0x02`. Interface subclass `0x20`, protocol `0x00`.

The firmware registers six libgreat/GCP classes: `core`, `firmware`, `selftest`, `gpio`,
`leds`, `moondancer`. The `moondancer` class is the USB device control surface —
`connect`, `disconnect`, `bus_reset`, `read_control`, `set_address`, `configure_endpoints`,
`stall_endpoint_in`, `stall_endpoint_out`, `clear_feature_endpoint_halt`, `read_endpoint`,
`ep_out_prime_receive`, `write_control_endpoint`, `write_endpoint`, `get_interrupt_events`,
`get_nak_status`, `ep_out_interface_enable`.

### 4. USB Proxy / MITM

Forwards USB transactions between a target host and a real device attached to the control
computer, with Python filters able to observe or rewrite them in flight. Documented by GSG
as a Cynthion scenario but implemented entirely in the host library: it is the Facedancer
emulation scenario with a proxy device class instead of an emulated one.

- Gateware and firmware: identical to scenario 3 — `facedancer.bit` + `moondancer.bin`.
- Host: `facedancer: facedancer/proxy.py::USBProxyDevice` and `facedancer/filters/`
  (`USBProxySetupFilters`, `USBProxyPrettyPrintFilter`). Example:
  `cynthion: cynthion/python/examples/facedancer-usbproxy.py`.
- Ports: CONTROL to the control computer, TARGET to the host being proxied, and the real
  device on a port of the control computer. On macOS this needs root to claim the device.

### 5. USER I/O from Facedancer

The six USER LEDs, the USER button and USER Pmod A pins are reachable from Python while a
Facedancer emulation is running, so an emulation can signal bus events on LEDs or gate its
behaviour on a button press. Pmod B is unavailable — it carries the SoC UART and JTAG.

- Gateware and firmware: the Facedancer bitstream's `leds` and `gpio0/1` peripherals; GCP
  classes `cynthion: firmware/moondancer/src/gcp/leds.rs` and `gpio.rs`.
- Host: `cynthion: cynthion/python/src/interfaces/led.py` and `interfaces/gpio.py`, reached
  via `cynthion.Cynthion()`. Pin map in `boards/cynthion_moondancer.py::GPIO_MAPPINGS`
  (PMOD A1–A10 plus `USER`).

### 6. Self-test

Validates a board's own hardware — the debug connection, all three ULPI PHYs and the
HyperRAM. Documented as the last step of bringing up a self-built board.

- Gateware: `cynthion: cynthion/python/src/selftest/gateware.py::SelftestDevice`, built by
  `cynthion/python/src/gateware/selftest/top.py`. Exposes a JTAG register interface: applet
  ID `0x54455354` ("TEST"), a 6-bit LED register, ULPI register windows for `target_phy`,
  `aux_phy` and `control_phy`, and a HyperRAM register read path. Map in
  `cynthion/python/src/selftest/registers.py`.
- Firmware: none for the gateware test. Separately, the Moondancer firmware has a `selftest`
  GCP class (`firmware/moondancer/src/gcp/selftest.rs`) with a single verb,
  `0x10 test_error_return_code` — that is a protocol plumbing check, not a hardware test.
- Host: `cynthion: cynthion/python/src/selftest/host.py::StandaloneTester` — cases
  `test_debug_connection`, `test_host_phy`, `test_target_phy`, `test_sideband_phy`,
  `test_hyperram`.
- Ports: CONTROL only. No second device, no target traffic.

### 7. FPGA configuration and flashing (Apollo)

Getting a bitstream onto the FPGA, either volatile over JTAG or persistently into the
configuration flash, plus the flash-inspection primitives around it.

- Firmware: the Apollo SAMD11 debug controller, `apollo` repo.
- Gateware: `apollo: apollo_fpga/gateware/flash_bridge.py` — a bitstream loaded into the
  FPGA purely so the FPGA can bridge the host to the SPI flash at speed. This is what
  `apollo flash-fast` uses; plain `flash-program` goes through the MCU.
- Host: the `apollo` CLI, and `cynthion run` / `cynthion flash` which wrap it.
- Ports: CONTROL only.

`cynthion run <name>` configures volatile SRAM; `cynthion flash <name>` writes the
configuration flash. For `facedancer`, both first place `moondancer.bin` at `0x000b_0000`.

### 8. Apollo MCU firmware update and recovery

- `cynthion update --mcu-firmware` flashes `apollo.bin` over DFU.
- Holding PROGRAM at power-on invokes the Saturn-V bootloader (separate repo,
  `greatscottgadgets/saturn-v`) on the CONTROL port, which is the unbrick path and the first
  step in self-built board bringup. DFU device `1d50:615c`, alt 0 `Flash`, alt 1 `SRAM`.
- Initial Saturn-V installation needs an SWD programmer on the `uC` header — Black Magic
  Probe, J-Link or OpenOCD-compatible.

### 9. Bulk throughput test

A bulk IN/OUT speed test used to characterise the USB data path.

- Gateware: `luna: luna/gateware/applets/speed_test.py`.
- Firmware alternative: `cynthion: firmware/moondancer/examples/bulk_speed_test.rs`, driven
  by `firmware/moondancer/scripts/bulk_speed_test.py`.
- Ports: CONTROL, or CONTROL plus AUX depending on the variant.

### 10. Custom gateware (the tutorials)

GSG documents Cynthion as an FPGA development platform, and the tutorial series is the
evidence: `Gateware Blinky` (Amaranth + toolchain), then `USB Gateware` parts 1–4 building a
USB device from luna's `USBDevice` — enumeration, WCID descriptors, control transfers, bulk
transfers. Sources at `cynthion: cynthion/python/examples/tutorials/`, each with a matching
`test-gateware-usb-device-0N.py` host script.

The platform files these build against are
`cynthion: cynthion/python/src/gateware/board/cynthion_r{0_1..0_7,1_0..1_4}.py` — 13 board
revisions, selected by the `LUNA_PLATFORM` environment variable.

### 11. Factory hardware validation

Not a user scenario, but it is where upstream's real hardware coverage lives:
`greatscottgadgets/cynthion-test`, driven from the `cynthion` repo's `Jenkinsfile`. It needs
a Tycho r2.0.0 test fixture, a Sasserides pin-bed stack, a GreatFET One, a Black Magic
Probe, a 24 V supply and switched USB hub power. It carries its own prebuilt `analyzer.bit`,
`selftest.bit`, `speedtest.bit` and `flashbridge.bit`.

## Website versus code

Recorded because each gap is a place where trusting one source alone would mislead.

| Claim | Source | Reality |
|-------|--------|---------|
| The `cynthion` CLI has five subcommands: `run`, `flash`, `update`, `info`, `setup` | docs site, "The cynthion command line interface" | Six. `build` exists in `cynthion/python/src/commands/cli.py` and is undocumented on that page. |
| "The Cynthion repository contains gateware for two designs" (analyzer, facedancer) | docs site, "Bitstream Generation" | Three top-levels build bitstreams; `selftest` is the third and the same site's CLI page lists it under `cynthion run`. |
| `cynthion selftest` | docs site, "Self-made Hardware Bringup" | No such subcommand. It is `cynthion run selftest`. |
| `cynthion flash <analyzer \| facedancer>` | docs site, CLI page | `cynthion_flash.py` also accepts `selftest`. |
| "Cynthion also includes the following user i/o ports" after "the four USB ports" | docs site, USER I/O page | Four *connectors*, three PHYs — TARGET-A and TARGET-C are one shared port. The Device Overview page on the same site says so. |
| The repository contains a `util/` directory | `cynthion` README.md | It does not exist at `dd2340e`. |

One upstream defect found while surveying, not a documentation gap:
`cynthion/python/src/commands/cynthion_build.py::_build_facedancer` calls bare
`flash_soc_firmware(...)` while the module only imports `from . import util`, so
`cynthion build facedancer` raises `NameError`. The same function hardcodes a relative
`cwd` of `../../firmware/moondancer/`.
