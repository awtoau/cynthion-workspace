# Scenario 6: Self-test

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §6.

## What it is

Validate a board's own hardware — the debug connection, all three ULPI PHYs, and the
HyperRAM. Documented as the final step of bringing up a self-built Cynthion, and the only
scenario whose purpose is to answer "is this board good".

## What implements it upstream

- Gateware: `cynthion: cynthion/python/src/selftest/gateware.py::SelftestDevice`, built by
  `cynthion/python/src/gateware/selftest/top.py`. It exposes a **JTAG** register interface —
  applet ID `0x54455354` ("TEST"), a 6-bit LED register, ULPI register windows for
  `target_phy`, `aux_phy` and `control_phy`, and a HyperRAM register read path. Map in
  `cynthion/python/src/selftest/registers.py`.
- Firmware: **none** for the hardware tests. (The Moondancer firmware separately has a
  `selftest` GCP class with one verb, `0x10 test_error_return_code` — that is protocol
  plumbing, not a hardware test. Do not confuse them; the name collision is upstream's.)
- Host: `cynthion: cynthion/python/src/selftest/host.py::StandaloneTester` — cases
  `test_debug_connection`, `test_host_phy`, `test_target_phy`, `test_sideband_phy`,
  `test_hyperram`.

## Hardware it needs

CONTROL only. One cable, no second device, no target traffic. The cheapest hardware
requirement of anything in this set.

## What porting it would require

Structurally this is the closest match to work we have already done, because the tests are
**register reads over a debug transport**, not USB traffic.

- **The three PHY tests** read a USB3343's vendor/product ID registers over ULPI. Our
  `gateware/soc/ulpi_window.py` already does exactly this for `target_phy`, with a
  documented four-phase clock-domain handshake and a bounded wait. Extending it to
  `aux_phy` and `control_phy` is a resource-request change, and it is the only new gateware
  the PHY tests need.
- **The HyperRAM test** reads the identification registers. We have a HyperRAM controller,
  a probe peripheral (`hyperram_probe.py`) and an identify script
  (`scripts/hyperram_identify.py`). This is already covered by our own tooling, differently.
- **The debug-connection test** is an Apollo JTAG check that does not involve the FPGA
  design at all.
- **The transport is the real difference.** Upstream reaches the registers over a
  JTAG-tunnelled debug interface; we reach ours over the CPU's CSR bus, and we have a third
  option — the sideband link — that upstream does not use for this. We have
  `gateware/soc/jtag_stage.py` and a `soc_jtag_stage_sim`, so JTAG is not foreign here
  either.

The question this scenario really asks is not "can we port it" but **"do we want upstream's
self-test, or is our board test suite already it?"** We have `soc_hyperram_sim`,
`soc_i2c_owner_sim`, `soc_typec_sim`, `soc_board_sim` and `./dev.py test-board`. Upstream's
five cases may already be a subset of what we check. That comparison should be done before
any porting, because the likely outcome is "we already have this, under different names".

Taking `control_phy` off Apollo to test it requires the port request —
`gateware/sideband_advertise.py` — which is already built.

## How it would be tested

Self-test *is* a test, so the question is how we test the tester. A new diagnostic's first
run is the control, not the experiment: it must be run against a board already believed
good before its verdict on any other board means anything.

- **QEMU: no.** No firmware in upstream's version, and no PHYs in `-M virt`.
- **Simulation: yes for the register paths.** `ulpi_window`'s handshake is exactly the kind
  of thing that should have a pysim test and currently does not. That gap is worth closing
  regardless of whether this scenario is ever ported.
- **Hardware: yes, and it is the point.** A self-test that passes in simulation and has
  never been run against a known-bad board has proved nothing. The useful validation is
  running it against a board we already believe is good and confirming it says so.

## Verdict

**Portable**, and possibly redundant. Do the comparison against our existing board tests
first; the port may reduce to "add `aux_phy` and `control_phy` to the ULPI window".
