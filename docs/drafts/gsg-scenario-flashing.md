# Scenario 7: Bitstream configuration and flashing

Child of the GSG scenarios master. Reference: `docs/gsg-scenarios.md` §7.

## What it is

Getting a bitstream onto the FPGA — volatile over JTAG, or persistently into the SPI
configuration flash — plus the flash inspection primitives around it.

## What implements it upstream

- Firmware: the Apollo SAMD11 debug controller (`apollo` repo).
- Gateware: `apollo: apollo_fpga/gateware/flash_bridge.py` — a bitstream loaded into the
  FPGA *purely so the FPGA can bridge the host to the SPI flash at speed*. This is what
  `apollo flash-fast` uses; plain `flash-program` goes through the MCU and is slower.
- Host: the `apollo` CLI — `configure`, `reconfigure`, `flash-program`, `flash-fast`,
  `flash-erase`, `flash-read`, `flash-info`, `svf`, `jtag-scan`, `force-offline`, the
  `spi`/`jtag-spi` register primitives, and `leds`.
- Wrapped by `cynthion run <name>` (volatile SRAM) and `cynthion flash <name>`
  (configuration flash). For `facedancer`, both first place `moondancer.bin` at
  `0x000b_0000`.

## Hardware it needs

CONTROL only.

## What porting it would require

**Nothing. We already do this, and in places we do it differently on purpose.**

- `./dev.py run` builds and configures the board; `./dev.py fw` writes flash and
  reconfigures without re-synthesising; `./dev.py stage` stages firmware over JTAG without
  rebuilding the bitstream; `./dev.py flash` reads and verifies the SPI flash.
- We have our own fast-loading path (`scripts/fast_loader.py`) and our own flash tooling
  (`gateware/soc/vexii_flash.py`, `scripts/flash_backup.py`).
- Our SoC maps SPI flash at `0x1000_0000`, matching upstream's choice, with a modal
  memory-map controller and a `spiflash` CSR block.

So this is **not a port**. What it is worth is an **audit**: three specific questions.

1. Does `apollo flash-fast`'s bridge bitstream do anything our `fast_loader.py` does not?
   Upstream's approach — load a bitstream whose only job is to be a fast SPI bridge — is a
   different design from ours and may be faster or slower. This is measurable.
2. Our firmware image lives where our linker puts it; upstream's Moondancer lives at
   `0x000b_0000`. If we ever want `cynthion flash --soc-firmware` to work against our
   bitstream, that offset is the contract.
3. `cynthion run <name>` and `cynthion flash <name>` are the commands a Cynthion user
   already knows. Whether our bitstreams should be reachable through them, or only through
   `./dev.py`, is a question about who the audience is — and it has not been asked.

## How it would be tested

- **QEMU: partially.** Flash *contents* handling — image layout, staging offsets, the
  bootloader's read path — is firmware logic. `./dev.py test` already boots the firmware
  under `-M virt`.
- **Simulation: yes, and already done.** `qspi_burst_sim` covers the QSPI burst path and
  `soc_bus_sim` the fabric. Both are in `./dev.py sim` today.
- **Hardware: yes, and already done.** `./dev.py flash` reads and verifies real flash;
  `run`, `console` and `flash` are all declared `needs_hardware`. Flash erase and program
  are authorised operations here — the board recovers over JTAG through Apollo.

This scenario has the best test coverage of any on the list, because it is the one we
have actually been living in.

## Verdict

**Already done, differently.** Convert this child into a short audit rather than a port:
measure `flash-fast` against `fast_loader.py`, and decide whether the `cynthion` CLI should
reach our bitstreams.
