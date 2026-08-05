# Scenario 2: Analyzer built-in test device

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §2.

## What it is

A second USB device instantiated on the **AUX** PHY inside the analyzer bitstream, on board
revisions ≥ r0.6. It presents an interrupt endpoint at Low, Full or High speed under host
control, giving the analyzer a known traffic source to be validated against without needing
a separate device plugged in. VID:PID `1209:000a`.

Not documented on the website at all. Found only in the code.

## What implements it upstream

- Gateware: `cynthion: cynthion/python/src/gateware/analyzer/top.py::AnalyzerTestDevice`.
- Host control: analyzer vendor request `3` (set test config), and
  `packetry: src/backend/cynthion.rs::configure_test_device`.
- Firmware: none.

## Hardware it needs

A loopback cable from AUX to TARGET, and nothing else. That is the entire point of it.

## What porting it would require

This is the smallest port on the list and the one closest to what already runs.

- `USBSerialDevice` on `aux_phy` in `vexii_hello_soc.py` is already a luna `USBDevice` on
  the same PHY, enumerating at high speed. Swapping a CDC-ACM function for an
  interrupt-endpoint function is a descriptor and endpoint-handler change, not new
  infrastructure.
- The three-speed switching (Low/Full/High selected at runtime) is the only genuinely new
  part; upstream builds separate descriptor sets per speed and adds a standard request
  handler for each.
- No CPU involvement, no HyperRAM, no libgreat.

## How it would be tested

- **QEMU: no.** No firmware.
- **Simulation: yes.** A luna `USBDevice` responding to a driven host model is exactly the
  kind of thing pysim handles, and upstream luna has test infrastructure for it. This
  could land as a sub-second sim in the fast tier.
- **Hardware: yes, and cheaply.** AUX enumerating on the host as `1209:000a` is a
  one-cable check. `./dev.py test-board` territory.

## Why it might be worth doing first

It is the only scenario that produces a **test fixture** rather than a feature: once this
exists, scenario 1 has a traffic source that does not depend on borrowing a real USB device,
and the analyzer can be validated on a bench with one loopback cable. Building the thing
that tests the hard scenario before building the hard scenario is the right order.

Against that: it tests the analyzer, and we have no analyzer yet. On its own it proves only
that we can put a chosen device on AUX — which `USBSerialDevice` already proves.

## Verdict

**Portable.** Nearest neighbour to code we already run. Small, and its value is almost
entirely as scaffolding for scenario 1.
