# Scenario 8: MCU firmware update and recovery

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §8.

## What it is

Updating the Apollo debug controller's firmware, and recovering a board whose Apollo
firmware is broken.

- `cynthion update --mcu-firmware` flashes `apollo.bin` over DFU. Unlike the bitstreams,
  `apollo.bin` is **committed prebuilt** at
  `cynthion: cynthion/python/assets/apollo.bin` — it is not built from source by the
  Cynthion toolchain.
- Holding PROGRAM at power-on invokes the Saturn-V bootloader
  (`greatscottgadgets/saturn-v`, a separate repo) on the CONTROL port. DFU device
  `1d50:615c`, alt 0 `Flash`, alt 1 `SRAM`. This is the unbrick path.
- Installing Saturn-V in the first place needs an **SWD programmer** on the `uC` header —
  Black Magic Probe, J-Link, or an OpenOCD-compatible adapter — plus an
  `arm-none-eabi` toolchain. That is the first step of self-built-board bringup.

## Hardware it needs

CONTROL only for DFU. An SWD programmer and a 10-pin cable for the initial bootloader
install or for a board with no working bootloader.

## What porting it would require

**Nothing.** This is entirely the SAMD11 and its host tooling. Our SoC is not involved at
any point — the FPGA is held unconfigured for most of it.

We already build Apollo firmware from source rather than shipping a prebuilt binary
(`repos/apollo`, with `scripts/apollo_budget_check.py` and
`scripts/apollo_memory_report.py` guarding the flash budget, and
`docs/apollo_samd11_mcu/` documenting it). That is strictly more than upstream's user-facing
path offers, which ships a blob.

Two things worth recording rather than porting:

1. **The prebuilt-blob discrepancy.** A user running `cynthion update --mcu-firmware` gets
   a binary they cannot reproduce from the repo they cloned. We build ours. If we ever ship
   to anyone else, that difference is a feature and should be stated.
2. **The DFU contract is fixed** — `1d50:615c`, alt 0/1 — and any Apollo build of ours must
   keep it, or `dfu-util` and `cynthion update` stop working against our boards.

## How it would be tested

- **QEMU: no.** Wrong architecture, wrong chip; this is Cortex-M0 firmware.
- **Simulation: no.**
- **Hardware: the only tier.** Flashing Apollo and confirming the board re-enumerates is
  inherently a hardware operation. It is also low-risk here: Apollo is recoverable over
  SWD, and the bootloader region can be locked.

Our existing guards — the flash-budget check and the vector-table verification — are
static analysis, not runtime tests, and they catch the specific failure that matters
(an image that links but does not boot).

## Verdict

**Already done, and better than upstream's user path.** No SoC work. Keep this child open
only to record the DFU contract and the prebuilt-blob difference; close it after that.
