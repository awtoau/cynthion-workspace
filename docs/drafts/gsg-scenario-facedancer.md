# Scenario 3: Facedancer device emulation (Moondancer)

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §3.

## What it is

Emulate a real USB device on the TARGET port, with the device's behaviour written in Python
on the host. The FPGA runs a RISC-V SoC whose firmware ("Moondancer") gives the host
fine-grained control of the target USB port over the libgreat command protocol.

## What implements it upstream

- Gateware: `cynthion: cynthion/python/src/gateware/facedancer/top.py` — a luna-soc SoC
  with a VexRiscv CPU, 64 KiB block RAM at `0x0`, SPI flash at `0x1000_0000`, HyperRAM at
  `0x2000_0000`, CSRs at `0xf000_0000` (`leds`, `gpio0/1`, `uart0/1`, `timer0/1`, `spi0`,
  `usb0/1/2` each with `ep_control`/`ep_in`/`ep_out`, `advertiser`, `info`).
- Firmware: `cynthion: firmware/moondancer/src/bin/moondancer.rs`, plus `moondancer-pac`,
  `lunasoc-hal`, `smolusb`, `libgreat`.
- Host: the `facedancer` package's Moondancer backend via
  `cynthion: cynthion/python/src/boards/cynthion_moondancer.py` (board ID `0x10`).

## Hardware it needs

CONTROL to the control host, TARGET-C/A to the host being fooled. Two cables, two hosts
(or one host and two ports).

## What porting it would require

This is the deepest port, and it has two independent halves.

### Half one: the USB peripherals (gateware)

Moondancer's firmware addresses **three** USB device peripherals, each split into four CSR
blocks: `usb{0,1,2}` plus `ep_control`, `ep_in`, `ep_out`. We have **none** of them.
`target_ulpi` gives register access to the PHY and cannot move a packet.

So this needs P1 (the ULPI packet path) *and* a CPU-attached endpoint peripheral set on top
of it — a control-endpoint block, an IN block and an OUT block, each with a CSR interface,
each raising a PLIC interrupt. That is a genuine peripheral design, not an adaptation.

Note that the three USB peripherals correspond to the three PHYs, and only one of them
(`usb0`, TARGET) is needed for basic emulation. Building one set rather than three is a
legitimate reduction in scope, and it should be stated explicitly rather than discovered.

### Half two: the command surface (firmware)

The Moondancer firmware registers six libgreat/GCP classes — `core`, `firmware`,
`selftest`, `gpio`, `leds`, `moondancer`. The `moondancer` class is the USB device control
surface: `connect`, `disconnect`, `bus_reset`, `read_control`, `set_address`,
`configure_endpoints`, `stall_endpoint_in`, `stall_endpoint_out`,
`clear_feature_endpoint_halt`, `read_endpoint`, `ep_out_prime_receive`,
`write_control_endpoint`, `write_endpoint`, `get_interrupt_events`, `get_nak_status`,
`ep_out_interface_enable`.

We have no GCP dispatch in `firmware/cynthion-soc`. The verb table, the class registry and
the transport (vendor request `0x65`, bulk IN `0x81`, bulk OUT `0x02` — `cynthion:
shared/libgreat.toml`) all have to exist before any verb does anything.

**`smolusb::control::Control` is the exception, and it is a real one.** It is trait-based —
`dispatch_event` takes `&D where D: UsbDriver` — so the control-transfer state machine is
hardware-independent and ports verbatim against whatever driver we write. That is the one
part of this scenario that is portable today, and it is not a small part.

### Half three, implicitly: the host

Present the same descriptors upstream does — VID:PID `1d50:615b`, interface subclass
`0x20`, protocol `0x00` — and the unmodified `facedancer` package drives it. Deviating
means writing and maintaining a backend.

## How it would be tested

- **QEMU: partially, and more than it first appears.** `-M virt` has no USB, but the GCP
  dispatch layer, the verb table, descriptor construction, and `smolusb`'s control state
  machine driven against a mock `UsbDriver` are all pure firmware logic. That is a real
  fraction of half two and it belongs in `./dev.py test`.
- **Simulation: yes for the peripheral.** A CSR-driven endpoint block against a ULPI model
  is a pysim test. Needs P2.
- **Hardware: required for anything end-to-end.** "A host enumerates our emulated keyboard"
  cannot be simulated. Upstream's own answer is the same: their firmware unit tests
  (`cd firmware/moondancer/ && python -m unittest`) require CONTROL **and** AUX both cabled
  to the host. That test suite is worth reading as a specification of expected behaviour
  even before we can run it.

## Verdict

**Hard.** Two large pieces of new work — an endpoint peripheral family and a command
protocol — with only the control state machine coming for free. It is also the scenario
with the largest payoff, because scenarios 4 and 5 fall out of it at nearly zero extra
cost.
