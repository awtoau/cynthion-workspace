# What we take from upstream, and what we have replaced

Which parts of LUNA, luna-soc and cynthion this workspace uses as-is, which it has diverged
from, and why. Written because the boundary was becoming emergent rather than decided —
things were being replaced one at a time, as each one broke, without anyone stating the
policy.

**The short version: `luna`'s USB gateware is good and we keep it. `luna_soc` is a
different matter — three defects have been traced to it — and the standing rule is now
that Great Scott Gadgets code is reserved for the genuinely Cynthion-specific.**

## The policy

**Reserve Great Scott Gadgets code for work that is genuinely Cynthion-specific** — the
r1.4 pin map, the USB stack we already depend on, gateware unique to this product. For
anything general (an interrupt controller, a timer, a UART, a bus primitive) look at
upstream `amaranth-soc` first, then the wider Amaranth ecosystem, then a published
standard register map, then write our own. **Writing our own is an acceptable outcome,
not a last resort.**

This is a change of direction, and it is worth being explicit about what changed. The
earlier framing here was "we expect to take more of their gateware later", with the
luna_soc peripheral set — timer, uart, gpio, spi — listed as things we would adopt rather
than write. That is no longer the plan.

**Distinguish `luna` from `luna_soc`.** They are not the same quality of code and should
not be treated as one decision:

- **`luna`** — the USB gateware. Good, widely exercised, and reimplementing it would be a
  large amount of work to arrive at something worse. **No defect has been found in it.**
- **`luna_soc`** — the SoC peripherals. **Three defects traced to it so far**, all three
  found on this board, all three in code paths their own firmware happens not to
  exercise: the SPI crossbar starvation, the CS-hold register type, and `oe = ~tx.rdy`
  releasing the UART's stop bit early. Each is documented below with a reproducer.

Three faults in three peripherals is a pattern rather than bad luck, and it is the reason
generic peripherals are now written here or taken from a published standard map instead.

**Replace what blocks us, and say so here.** Every divergence below exists because
something was measurably wrong or measurably limiting, not because we preferred our own
version. Each has a recorded reason and, where the fault is upstream's, a reproducer.

**Vendor the board definition rather than inheriting a stack for it.** The r1.4 pin map is
340 lines of hardware wiring; taking it via `cynthion` pulls in `LUNAApolloPlatform`,
`LUNAPlatform` and a `luna-soc` fork pin. Now vendored at
`ecp5-test/cynthion_platform/cynthion_r1_4.py`; see
[`hardware.md`](hardware.md).

## Used as-is, and we intend to keep using it

| what | from | why keep it |
|---|---|---|
| `USBDevice`, `USBSerialDevice` (CDC-ACM) | `luna.gateware.usb` | Measured at 195.4 Mbps loopback; CDC costs essentially nothing over raw bulk. This is the part of LUNA that is genuinely good. |
| `USBStreamInEndpoint`, `USBStreamOutEndpoint` | `luna.gateware.usb` | Same stack, same reasoning. |
| `StandardRequestHandler`, `ControlRequestHandler`, `SetupPacket` | `luna.gateware.usb.request` | USB protocol plumbing; no reason to touch it. |
| `IntegratedLogicAnalyzer` | `luna.gateware.debug.ila` | Works, fits in one DP16KD, found the CS bug. |
| `JTAGRegisterInterface` | `luna.gateware.interface.jtag` | Reliable, and the only debug path that does not depend on USB coming up. |
| `StreamSerializer` | `luna.gateware.stream` | Small, works. |
| `ULPIRegisterWindow` | `luna.gateware.interface.ulpi` | Reading a USB3343's registers means driving ULPI register transactions on the same 8-bit bus the link runs on -- it is part of the USB stack, not a generic peripheral, so it falls inside the exception rather than needing a case made for it. We already run `USBSerialDevice` over that layer on `aux_phy`. Writing our own would mean debugging a protocol FSM against a part we cannot probe, to arrive at the same thing. Use it on `target_phy`; `aux_phy`'s bus is owned by the console. |
| `blockram.Peripheral` | `luna_soc.gateware.core` | Works, and its `writable=True` default is what makes runtime firmware loading possible. |
| `SPIPHYController`, `ECP5ConfigurationFlashInterface` | `luna_soc.gateware.core.spiflash` | The PHY and the `USRMCLK` handling are correct. The bugs were a layer above. |
| `SPIFlashMemoryMap` | `luna_soc.gateware.core.spiflash` | Verified byte-exact against `apollo flash-read`. |
| `amaranth-soc` | **upstream**, not luna_soc's vendored copy | See below. |
| `amaranth_soc.gpio.Peripheral` | **upstream** `amaranth-soc` | The board's six LEDs, the power monitor's PWRDN and the USER button. Mode/Input/Output/SetClr, per-pin, with configurable input synchroniser stages -- everything a bespoke LED register would have had to grow, already documented and already tested. Its `INPUT_ONLY` reset mode is also what lets the fabric keep driving the LEDs until firmware asks for one; see the LED comment in `vexii_hello_soc.py`. The alternative was `luna_soc`'s gpio, which the policy reserves. |
| `AsyncSerial` | `amaranth-stdio` | The bit engine for the Apollo-facing UART. Wrapped, not replaced -- `ecp5-test/riscv/serial_line.py` adds the pad handling it deliberately leaves to the instantiator, and everything below the frame is upstream's. |

## Diverged, with reasons

Each row below is a divergence from upstream. **The technical detail — what upstream does,
what the measurement showed, what ours does instead — lives in
[`decisions.md`](decisions.md).** This table is the policy view: what we replaced, and
whether the reason was a defect, a limit, or a standard we preferred to adopt.

| what | ours | why it diverged | detail |
|---|---|---|---|
| Clock generation | `VariableClockDomainGenerator` (`repos/apollo/apollo_fpga/gateware/variable_clock.py`) | **limit.** Upstream offers 60/120/240 MHz only, which blocked the HyperRAM ceiling and the RISC-V clock sweep. Ours solves `sync` and `usb` together so `usb` lands on exactly 60 MHz. #111 | [3](decisions.md#3-clock-generation) |
| SPI crossbar | `FairSPIControlPortCrossbar` (`ecp5-test/riscv/vexii_flash.py`) | **upstream defect.** Re-arbitrates only when the grant-holder stops asserting `cs`, but `cs` is a hold, not a request. Reproducer `scripts/riscv_flash_crossbar_sim.py` | [11](decisions.md#11-spi-flash-crossbar) |
| SPI controller | `HoldableSPIController` (same file) | **upstream defect.** The CS field is `csr.action.W` — a one-cycle pulse — used as a latch. Reproducer: ILA capture | [12](decisions.md#12-spi-flash-chip-select-hold) |
| UART pad output enable | `SerialLine` (`ecp5-test/riscv/serial_line.py`) | **upstream defect, same shape as the last: a hold expressed as a ready.** `oe = ~tx.rdy` releases at the start of the stop bit. Reproducer `scripts/soc_serial_sim.py`. #113 | [13](decisions.md#13-uart-pad-output-enable) |
| Console peripheral | `Uart16550` (`ecp5-test/riscv/uart16550.py`) | **standard adopted.** A published register map that QEMU also models, so one driver serves the board and the test gate | [4](decisions.md#4-console-peripheral) |
| Interrupt controller | `Plic` (`ecp5-test/riscv/vexii_plic.py`) | **nothing usable upstream.** luna_soc's is reserved by policy and shaped for VexRiscv's in-CPU CSRs; `amaranth_soc`'s `EventMonitor` does not elaborate; VexiiRiscv's is Tilelink-only | [7](decisions.md#7-interrupt-controller) |
| I2C master | `I2CMaster` (`ecp5-test/riscv/i2c_master.py`) | **nothing upstream.** `amaranth-soc` has no I2C peripheral; `amaranth-stdio` is `serial.py` and nothing else. Register map is the OpenCores I2C-Master Core rev 0.9 | [10](decisions.md#10-i2c-register-map) |
| `amaranth_soc` | **upstream**, not luna_soc's vendored copy | **stale vendor.** The vendored tree reported version `unknown` and was four commits behind, including fixes it never had | [14](decisions.md#14-amaranth-soc-upstream-vs-vendored) |
| Board platform | vendored at `ecp5-test/cynthion_platform/` | **in progress.** `CynthionPlatformRev1D4` is 206 lines of pin declarations plus a 134-line base, but reaching it inherits `LUNAApolloPlatform` → `LUNAPlatform` and pins `luna-soc` to the `awtoau/awto-luna-soc` fork. Target: a self-contained platform depending only on `amaranth`, `amaranth.build` and `amaranth_boards.resources` | — |
| CONTROL port request | `SidebandAdvertiser` (`ecp5-test/sideband_advertise.py`) | **incompatible with the pin's other use.** `ApolloAdvertiser` drives FPGA_ADV as a 50 Hz square wave (20 ms period), which cannot share the wire with the sideband UART. Apollo's own `FPGA_ADV_MODE_UART` already defines the alternative — the frame `C1 14 01 A5` — and no gateware emitted it. #137 | [25](decisions.md#25-fpga_adv-advertisement-and-sideband-on-one-wire), [`chips/cynone-sideband.md`](chips/cynone-sideband.md#8-the-second-job-the-control-port-request) |

**Worth noting rather than using:** luna-soc's `InterruptController` exposes
`add(peripheral, name=, number=)` and `interrupts()`, which its SVD generator reads. Any
replacement that wants to keep that generator working has to keep those signatures —
`ecp5-test/riscv/vexii_irq.py` does, which is why it is still in the tree.

## Still expected from upstream — and it is Cynthion-specific work

**This is not an exit.** The policy reserves Great Scott Gadgets code for the genuinely
Cynthion-specific, and there is a lot of that: it is their product, and the parts of their
tree that are *about this board* are exactly the parts worth taking.

| what | where | likely use |
|---|---|---|
| USB analyzer | `cynthion` gateware | the product's actual purpose — capture and decode |
| Facedancer gateware | `cynthion/.../gateware/facedancer` | device emulation; also the SoC moondancer targets |
| `flash_bridge` | `apollo_fpga.gateware` | `flash --fast`, an FPGA-side flash writer |
| selftest bitstream | `cynthion.selftest` | already used by some scripts here |

Every row is board-specific. **The generic peripherals are no longer on this list.**

Moondancer is the case that used to argue the other way, and it is worth stating how it
now reads. It targets `riscv32imac` (which our CPU is), expects `blockram` at `0x00000000`
and `spiflash` at `0x100b0000` (which we now have), and needs leds, gpio0, gpio1, timer0,
timer1, spi0 and a USB peripheral. Those are luna_soc peripherals — but what moondancer
actually depends on is the **register interface**, not the implementation behind it. Three
of that peripheral set have now been reimplemented here after defects, and the compatible
thing to supply is the map, not the module.

So the rule is:

- **take what is about this board**, and most of that is good
- **write, or take from a published standard map, anything generic**
- **replace what is measurably broken or limiting**, with the reason recorded here
- **do not inherit a stack to get one file** — vendor the file

## Patches carried against vendored trees

Not gateware divergences — these are source patches applied to the vendored Python and
Rust dependencies, tracked in this repository's issues.

| Issue | Component | File | What |
|---|---|---|---|
| [#8](https://github.com/awtoau/cynthion-workspace/issues/8) | facedancer | `configuration.py` | skip pre-interface descriptors (e.g. IAD) before the first interface |
| [#9](https://github.com/awtoau/cynthion-workspace/issues/9) | facedancer | `backends/base.py` | downgrade a duplicate-endpoint-address exception to a warning (UVC alt settings) |
| [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | facedancer | `backends/moondancer.py` | deduplicate endpoints by address before `configure_endpoints` |
| [#43](https://github.com/awtoau/cynthion-workspace/issues/43) | moondancer | `gcp/moondancer.rs` | clamp endpoint `max_packet_size` to 512 (the HS limit) instead of rejecting SuperSpeed devices |
| [#65](https://github.com/awtoau/cynthion-workspace/issues/65) | apollo | `uart.c`, `console.c`, `vendor.c`, `apollo_mode.c` | JTAG/UART arbitration on the shared PA11/PA14 pins — see [`hardware.md`](hardware.md#pin-sharing--the-two-hazards) |

## Decided: HyperRAM splits at the PHY

**We keep upstream's controller and replace upstream's PHY.** The split is at the record
between them, and it falls out of the policy rather than being a compromise: `HyperBus` is
a published protocol and the layer that speaks it is generic; the layer below it is ECP5
I/O for r1.4's pin map, which is exactly the "genuinely Cynthion-specific" the policy
reserves — and reserving it means writing it, because nobody else has this board.

| layer | whose | why |
|---|---|---|
| `HyperRAMInterface`, `HyperRAMDQSInterface` | **upstream, unchanged** | command encoding, latency, burst. Verified: 220.2 MB/s on the non-DQS path |
| `HyperRAMPHY` (non-DQS) | **upstream, unchanged** | it elaborates here and it works |
| `HyperRAMDQSPHY` | **ours** (`ecp5-test/riscv/hyperram_dqs_phy.py`) | upstream's cannot be instantiated on r1.4 at all — see below |

**Wrapping upstream's DQS PHY was not an option.** It fails for three separate reasons,
none of them about DQS: it assigns `bus.clk` as a single net where the platform declares a
`DiffPairs`; it instantiates `BB` on `bus.dq.io`/`bus.rwds.io`, which exist only on a raw
request, while writing `bus.clk`/`bus.cs` as if buffered; and it never drives `bus.reset`,
leaving RESET# floating. There is no wrapper that fixes an assignment inside another
module's `elaborate`.

**And the board was wired for DQS all along.** `hyperram-detailed.md` recorded "unusable —
no DQS pin group"; the device database says RWDS is on `LDQS8` and every DQ line is in the
same group, and nextpnr agrees. `scripts/hyperram_dqs_pins.py` is the check.

Two upstream defects are **left in place, deliberately**, both in the controller:

- `RECOVERY` carries `# TODO: implement recovery` and falls through to `IDLE`, so nothing
  keeps CS# high for tCSHI (10 ns — longer than a 120 MHz cycle). The gap is held by the
  caller instead, where it can be counted. `scripts/soc_hyperram_sim.py` asserts
  back-to-back transactions violate it and that holding the gap fixes it.
- `with m.If(extra_latency | 1)` makes the low-latency branch dead, which #90 reports as
  costing ~30% of the fixed overhead. **It is correct for this part as configured**: CR0
  reads `0x8f2f`, which selects *fixed* latency, so the device takes the long count every
  time and RWDS says nothing about the transaction. Honouring RWDS pays only after CR0 is
  reprogrammed to variable latency — two changes, not one, and worth measuring rather than
  assuming.

**The Wishbone peripheral (#90) is workspace gateware.** `HyperRAMWishbone` wraps the
upstream controller with a 32-bit memory port: full stores and reads are two-word
HyperBus bursts, while partial stores read, merge and write because the upstream
controller exposes no RWDS mask input.

Three bugs were found in *our own* use of that interface, not in it: `final_word` must be
held rather than pulsed, `perform_write`/`write_data` must be held for the whole transfer,
and `CHID` is a single register window so channel setup is not re-entrant. All three
produced plausible wrong answers rather than failures. The first two are now asserted in
simulation rather than only written down.

## Vendored: the USB host engine

**`ecp5-test/usb_host/guh/` is `apfaudio/guh`'s `usbh` at commit `923c8490`, taken
verbatim** — `sie.py` (the transaction engine), `reset.py` (bus reset and the host
half of the high-speed chirp) and `types.py`. BSD-3-Clause; the licence sits beside
them.

This is the "do not inherit a stack to get one file" rule applied literally, and
the reasons are stronger here than usual: GUH has no releases, no tags, one
author, an explicit "interfaces will change" warning, and a stated policy of not
accepting contributions. It is also why the pin is that commit and not the one the
proposal was written against — at `fbd7077` three SoC-facing files carried
CERN-OHL-S-2.0, a copy-paste leftover reported as `apfaudio/guh#1` and fixed on
2026-07-30.

Deliberately **not** taken: `enumerator.py` and `descriptor.py` (they discard the
descriptors, hard-code the device address, and specialise the parser at synthesis
time — `docs/usb-host-options.md` §16), `engines/*`, `periph/*`.

The vendored files stay byte-identical so a pin bump is a diff. What we need and
they do not do goes beside the package: `ecp5-test/usb_host/model.py` is ours,
and `scripts/usb_host_sie_sim.py` asserts the engine's behaviour against LUNA's
own device stack — including the three interface traps a CPU-facing shim has to
handle, so a pin bump that changes any of them fails a test rather than the shim.

## Not yet decided

**`luna-soc` fork.** `cynthion` pins `luna-soc` to `awtoau/awto-luna-soc`. The fork existed
for an Amaranth API change and 40+ patched CSR classes; upstream is now **ahead**, so the
amaranth-soc reason is gone. Whether anything else still needs it has not been checked.

## Reporting upstream

**Three clean defects with standalone reproducers**, all in
`greatscottgadgets/luna-soc` — not in `luna`, whose USB gateware has been solid
throughout.

| defect | reproducer | why nobody upstream hit it |
|---|---|---|
| SPI crossbar starvation | `scripts/riscv_flash_crossbar_sim.py` | needs flash memory-mapped **and** arbitrary commands; upstream grants at cycle 0 when the map is idle and never in 600 cycles when it holds `cs` |
| CS hold as a one-cycle `csr.action.W` | ILA capture; CS fragmented into four 8-bit windows during a JEDEC read | moondancer's `read_flash_uuid` writes all 13 command bytes into the 16-deep FIFO before reading, so `r_rdy` never drops |
| `oe = ~tx.rdy` releasing the stop bit | `scripts/soc_serial_sim.py` | the released line RC-charges through a pull-up and usually arrives in time; it presents as intermittent corruption, not a dead link |

The first two are reachable only by memory-mapping flash *and* using arbitrary commands.
Cynthion's own SoC instantiates exactly that combination
(`with_controller=True, with_mmap=True`), so this is their code on their hardware.

The first two are worth reporting as one issue rather than two: the CS bug is the
interesting half, and the explanation of why their firmware survives it is what makes the
report actionable. The UART one stands alone.

Per the workspace rules, upstream repos need three checks before anything is filed. Draft
locally first.
