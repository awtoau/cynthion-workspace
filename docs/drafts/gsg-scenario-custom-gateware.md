# Scenario 10: Custom gateware (the tutorial series)

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §10.

## What it is

GSG documents Cynthion as an FPGA development platform, not only as an instrument, and the
tutorial series is what backs that claim: `Gateware Blinky` (Amaranth plus toolchain), then
`USB Gateware` parts 1–4 building a USB device from luna's `USBDevice` — enumeration, WCID
descriptors, control transfers, bulk transfers.

This is a scenario in the sense that matters: it is a documented promise about what a user
can do with the board, and it is the one that says our platform must stay compatible.

## What implements it upstream

- Sources: `cynthion: cynthion/python/examples/tutorials/`, each with a matching
  `test-gateware-usb-device-0N.py` host script.
- Platform files: `cynthion: cynthion/python/src/gateware/board/cynthion_r{0_1..0_7,1_0..1_4}.py`
  — 13 board revisions, selected by the `LUNA_PLATFORM` environment variable.
- Entry point: `luna.top_level_cli(Top)`, which synthesises and uploads in one step.
- Toolchain: the tutorials recommend YoWASP (`yowasp-yosys`, `yowasp-nextpnr-ecp5`) rather
  than OSS CAD Suite, for portability.

## Hardware it needs

CONTROL for the blinky tutorial. CONTROL plus TARGET for the USB device tutorials — the
device under construction appears on TARGET.

## What porting it would require

Nothing needs porting. What needs *checking* is compatibility, and there is a real question
here.

**We have our own platform file**, `gateware/board/cynthion_r1_4.py`, not
luna's. Any tutorial example a user runs against our repo resolves `platform.request("led", n)`,
`platform.request("target_phy")` and `apollo_port_sharing` through ours. So the concrete
question is: **does our platform present the same resource names, at the same indices, with
the same semantics, as upstream's `cynthion_r1_4.py`?** If it does, every tutorial works
unchanged. If it silently differs — an LED index, a Pmod pin order, an attribute name — a
tutorial fails in a way that reads as the user's mistake.

That comparison is mechanical and has not been done. It is the entire content of this
child.

Two known-good signs: `gateware/board/core.py` already reports
`control_phy: advertising` through `port_sharing()`, matching luna's interface, and
`usb_serial.py` already builds a luna `USBDevice` against our platform successfully — which
is tutorial parts 1–4's exact dependency.

Also worth noting: LED indices on this board are colour-ordered
(0 red, 1 orange, 2 yellow, 3 green, 4 blue, 5 violet). A tutorial that says "LED 3" and
our platform that says "LED 3" must mean the same lamp.

## How it would be tested

This is the scenario with the **cleanest** test story of any here, because a tutorial has
a defined pass condition and upstream ships the checker.

- **QEMU: no.** Gateware, not firmware.
- **Simulation: yes, for the elaboration half.** Every tutorial example can be *elaborated*
  against our platform in pysim without a board — that alone catches every resource-name
  mismatch, which is the failure this child exists to prevent. It is fast enough for the
  `gate` tier and needs no ULPI model.
- **Hardware: for the USB tutorials' final check**, using upstream's own
  `test-gateware-usb-device-0N.py` scripts as the oracle. That is a ready-made acceptance
  suite we did not have to write.

An "elaborate every upstream tutorial against our platform" sim would be a genuinely cheap,
genuinely useful addition to `scripts/soc_sims.py`.

## Verdict

**Portable** — and mostly a compatibility audit rather than a port. Small, cheap, testable
without hardware, and it protects a promise upstream has made publicly.
