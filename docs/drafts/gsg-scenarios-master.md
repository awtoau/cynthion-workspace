# Master: port Great Scott Gadgets' Cynthion scenarios to our SoC

Upstream ships three bitstreams and documents about a dozen distinct things a Cynthion
can do. We have replaced luna-soc with our own VexiiRiscv SoC. This issue is the survey
of what upstream actually does, and the index of one child issue per scenario.

The reference half — what each scenario is and what implements it upstream, with the
wire contracts — is in [`docs/gsg-scenarios.md`](https://github.com/awtoau/cynthion-workspace/blob/main/docs/gsg-scenarios.md).
This issue holds the part that is not durable: what porting each one costs and how it
would be tested.

## The one fact that shapes every child

**This SoC has no USB device controller and no USB packet path of any kind on the
TARGET port.**

The peripherals are: `ram`, two 16550 consoles, `gpio`, `i2c`, `sideband`, `target_ulpi`,
`i2c_mux`, `vbus`, `gateware_id`, `plic`, `clint`, `spiflash`, `bootram`, `hyperram`.
`gateware/soc/peripherals/ulpi_window.py` reads and writes a USB3343's PHY registers over
`target_phy` and cannot send or receive a single packet. Moondancer's firmware expects
three sets of `usb{0,1,2}` + `ep_control` + `ep_in` + `ep_out` CSR peripherals; none of
them exist here.

So for every scenario, "what would porting it require" begins at the same place: build
or adopt a USB packet path. The children differ in how much they need on top of that.

## What we do have, and it is more than it sounds

| Asset | Where | Why it matters |
|---|---|---|
| Our own board platform | `gateware/board/cynthion_r1_4.py` | `control_phy`, `aux_phy`, `target_phy` resources and `apollo_port_sharing` already declared |
| A working luna USB device on AUX | `gateware/probes/usb_serial/usb_serial.py`, instantiated in the SoC at `gateware/soc/top.py` | `USBSerialDevice` enumerates at high speed and loops back at 195.4 Mbps. luna's USB device gateware **builds and runs on our platform today** — the question is never "can luna's USB stack work here", only "is it wired to the right PHY with the right endpoints" |
| CONTROL port handover | `gateware/probes/sideband/sideband_advertise.py`, `docs/sideband.md`, frame-exact sim `scripts/sideband_advertise_sim.py` | Upstream's `ApolloAdvertiser` prerequisite is already solved on our side |
| VBUS switch control | `gateware/soc/peripherals/vbus_csr.py` | The analyzer's pass-through and port-power control has a CPU-visible home already |
| HyperRAM | `gateware/soc/peripherals/hyperram_dqs_phy.py`, mapped at `0x2000_0000` | The analyzer's 8 MiB capture buffer needs exactly this |
| PAC generated from the SoC's own map | `./dev.py pac` | A new peripheral gets Rust accessors without hand-transcription |
| `smolusb::control::Control` | upstream, trait-based over `D: UsbDriver` | The control-transfer state machine is hardware-independent and ports verbatim. It is the one part that is portable today |

## The testing constraint, which is the real one

Our three test tiers do not overlap the way the scenarios need.

* **`./dev.py test` runs `qemu-system-riscv32 -M virt`.** That machine has a 16550, a
  PLIC and a CLINT and *nothing else of ours* — no ULPI, no USB, no HyperRAM, no
  `gateware_id`. QEMU can test firmware logic that does not touch a PHY: protocol
  parsers, command dispatch, descriptor construction, the `smolusb` control state
  machine driven by a mock. It cannot test a single USB peripheral.
* **`./dev.py sim` runs 15 Amaranth pysim simulations** (`scripts/soc_sims.py`), 9 of
  them sub-second and in the pre-commit gate. There is currently **no USB simulation and
  no ULPI simulation**. Adding one is the highest-leverage thing on this whole list,
  because it is the only tier that can test a packet path without a board.
* **`./dev.py test-board`** runs the shell suite on real hardware. `run`, `console` and
  `flash` are declared `needs_hardware` and are deliberately excluded from `gate` and
  `ci`.

A scenario whose only test is "plug it in and look" is a scenario that will silently rot.
Each child states its test tier explicitly, and where the answer is "hardware only", say
so rather than pretending.

## The children

| # | Scenario | Bitstream / firmware upstream | Verdict |
|---|---|---|---|
| 1 | [USB protocol analyzer](gsg-scenario-analyzer.md) | `analyzer.bit`, no firmware | Hard — biggest gateware build, but no firmware and a fully specified wire contract |
| 2 | [Analyzer built-in test device](gsg-scenario-analyzer-test-device.md) | inside `analyzer.bit`, AUX PHY | Portable — closest to what already runs on AUX |
| 3 | [Facedancer device emulation](gsg-scenario-facedancer.md) | `facedancer.bit` + `moondancer.bin` | Hard — needs the USB peripheral set *and* the libgreat command surface |
| 4 | [USB proxy / MITM](gsg-scenario-usb-proxy.md) | same as 3 | Free once 3 lands — it is host-side only |
| 5 | [USER I/O from Facedancer](gsg-scenario-user-io.md) | same as 3 | Portable — our `gpio` and LED peripherals already exist |
| 6 | [Self-test](gsg-scenario-selftest.md) | `selftest.bit`, no firmware | Portable — JTAG register reads, and we already have ULPI register access |
| 7 | [Bitstream configuration and flashing](gsg-scenario-flashing.md) | Apollo MCU + `flash_bridge.py` | Already done, differently — worth an audit, not a port |
| 8 | [MCU firmware update and recovery](gsg-scenario-mcu-firmware.md) | Apollo + Saturn-V, DFU | Already done — no SoC involvement at all |
| 9 | [Bulk throughput test](gsg-scenario-speed-test.md) | `luna/gateware/applets/speed_test.py` | Portable — we have measured 195.4 Mbps on AUX already |
| 10 | [Custom gateware tutorials](gsg-scenario-custom-gateware.md) | tutorial sources, luna `USBDevice` | Portable — a platform-compatibility question, not a SoC one |
| 11 | [Factory hardware validation](gsg-scenario-factory-hil.md) | `cynthion-test` + Tycho | Needs hardware we lack — Tycho, Sasserides, GreatFET One, Black Magic Probe |

## Shared prerequisites

Three pieces of work are prerequisites for several children at once. They are called out
here so they are not re-argued in each.

**P1. A ULPI packet path on `target_phy`.** Today `target_phy` reaches only
`ulpi_window.UlpiRegisters`. Scenarios 1, 3 and 4 all need `luna.gateware.interface.ulpi`'s
full PHY plus a UTMI-level consumer. The `USBSerialDevice` on `aux_phy` proves the layer
works on this platform; the work is instantiating it on the right PHY without disturbing
the register window that the board tests depend on.

**P2. A ULPI simulation.** Neither QEMU nor the current sim set can see a USB packet.
Until a bus-functional ULPI model exists in `scripts/`, every USB scenario is
hardware-only, which makes all of them untestable in `gate` and `ci`. This should
probably be done before scenario 1, not during it.

**P3. A host-side board layer.** Upstream's `cynthion` Python package speaks to a board
identified by VID:PID `1d50:615b` with interface subclass `0x10` (analyzer) or `0x20`
(moondancer). Anything we build either matches those descriptors — and then Packetry and
Facedancer work unmodified — or does not, and needs its own host tooling. **Match them.**
The descriptors are the cheapest part of any of this and they are what buys us upstream's
entire host stack for free.

## Website versus code

Six discrepancies found between what greatscottgadgets.com documents and what the
repositories contain, plus one upstream defect. They are recorded in
[`docs/gsg-scenarios.md`](https://github.com/awtoau/cynthion-workspace/blob/main/docs/gsg-scenarios.md#website-versus-code) rather
than here, because they stay true. The defect —
`cynthion build facedancer` raising `NameError` — is worth reporting upstream.

## Scope

This issue does not commit us to porting anything. It exists so that the decision about
each scenario is made against what upstream actually does rather than against a memory of
it.
