# ECP5 test-bitstream inventory

This is the inventory of standalone FPGA experiments under `gateware/`.

- **Verified build** means synthesis, place-and-route and packing completed on
  2026-08-03; **syntax** means every Python file compiles but this design was
  not synthesized during the inventory.
- **Recorded pass** names existing silicon evidence; **hardware required** is
  deliberately not inferred from simulation or a successful build.
- Library blocks and simulations are listed after the bitstreams so they are
  not mistaken for loadable tests.

| bitstream | what it tests | build | silicon result | host driver | dependency movement / disposition |
|---|---|---|---|---|---|
| `fabric/fabric_gateware.py` | LUT/FF computation across the 12F-marked die's 25F fabric | **verified**, 20,476 LUT4, timing met | recorded clean runs and 1,575/1,575 control mismatches; harness refactor needs hardware confirmation | `scripts/fabric/fabric_build.py`, `fabric_run.py`, `fabric_negative_control.py`, `fabric_sweep.py` | retained; common BIST owns command, counters, sticky error and runtime control |
| `gateware/hyperram_baseline/top.py` | clean-room HyperRAM core, sequential throughput (#581) | built | [`docs/chips/hyperram/sequential-baseline.md`](../docs/chips/hyperram/sequential-baseline.md) | `scripts/hyperram/hyperram_baseline.py` | reference; the SoC peripheral is built on its core (#588) |
| `qspi/qspi_gateware.py` | quad-flash read modes, runtime divisor, byte capture and ceiling | syntax | recorded silicon sweep | `scripts/fabric/qspi_burst.py`, `qspi_ladder.py`, `flash_ceiling.py`, `flash_modes.py` | retained; current Apollo QSPI reader and variable clock replace rebuild-per-rate probes |
| `sideband/sideband_gateware.py` | full FPGA_ADV responder, flash paths and diagnostic state | syntax | recorded silicon protocol/soak results | `scripts/sideband_read.py`, `sideband_soak.py`, `sideband_speed_ladder.py`, `sideband_build.py` | retained as the diagnostic bitstream; shipping designs use the smaller `sideband_link.py` block |
| `adv_speed/adv_speed_gateware.py` | FPGA_ADV error rate versus baud and drive mode | syntax | recorded silicon ceiling | `scripts/sideband_speed_ladder.py` and capture tools | retained measurement bitstream; receiver baud is an Apollo firmware setting |
| `power_monitor/power_monitor_gateware.py` | PAC1954 address discovery and register reads | syntax | recorded silicon identification | `scripts/power_probe.py` | retained standalone diagnostic; the SoC has a multiplexed I2C owner and firmware driver |
| `pins/i2c_scan.py` | scan devices on each board I2C branch | syntax | hardware required | JTAG registers | diagnostic; board ownership now lives in `riscv/i2c_mux.py` |
| `pins/fusb302_id.py` | FUSB302 IDs and PAC1954 ID | syntax | recorded board-identification result | JTAG registers | diagnostic retained; firmware exposes richer device tests |
| `pins/pin_survey.py` | loopback, levels and edge counts on otherwise uncovered pins | syntax | hardware required | JTAG registers | retained board diagnostic; no CPU or USB dependency |
| `soc/top.py` | active Vexii SoC with flash, USB, JTAG and board I/O | syntax; firmware required to build | recorded silicon boot, console and board tests | `scripts/soc_test.py`, `soc_jtag_stage.py`, console scripts and firmware shell | retained active design; drivers, PAC and memory map are checked together |

## Components that are not bitstreams

These are reusable blocks or host-only checks even though they live in the test
tree.

| source | role | status |
|---|---|---|
| `bist.py` | shared JTAG BIST command/status and comparator | retained; simulated by `scripts/fabric/bist_sim.py` |
| `jtag_registers.py` | the JTAG register transport every applet reads through, clocked by TCK | replaces luna's `JTAGRegisterInterface` (#204); simulated by `scripts/soc/soc_jtag_registers_sim.py` |
| `riscv/*.py` except the four designs above | SoC peripherals, PHYs and CPU wrappers | covered by `scripts/soc_sims.py` and SoC integration |
| `sideband_advertise.py`, `sideband_link.py`, `sideband_debug.py` | drop-in FPGA_ADV blocks | covered by dedicated simulations and active SoC integration |
| `board/`, `build_helpers.py`, `usb_ids.py` | local platform and build/identity support | shared infrastructure, not loadable designs |
