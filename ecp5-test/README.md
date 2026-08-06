# ECP5 test-bitstream inventory

This is the inventory of standalone FPGA experiments under `ecp5-test/`.

- **Verified build** means synthesis, place-and-route and packing completed on
  2026-08-03; **syntax** means every Python file compiles but this design was
  not synthesized during the inventory.
- **Recorded pass** names existing silicon evidence; **hardware required** is
  deliberately not inferred from simulation or a successful build.
- Library blocks and simulations are listed after the bitstreams so they are
  not mistaken for loadable tests.
- The HyperRAM rows have a shorter door: `./dev.py hyperram` names every harness,
  its runner, and whether anyone has ever put its result on silicon (#189).

| bitstream | what it tests | build | silicon result | host driver | dependency movement / disposition |
|---|---|---|---|---|---|
| `fabric/fabric_gateware.py` | LUT/FF computation across the 12F-marked die's 25F fabric | **verified**, 20,476 LUT4, timing met | recorded clean runs and 1,575/1,575 control mismatches; harness refactor needs hardware confirmation | `scripts/fabric_build.py`, `fabric_run.py`, `fabric_negative_control.py`, `fabric_sweep.py` | retained; common BIST owns command, counters, sticky error and runtime control |
| `hyperram/hyperram_ceiling_top.py` | legal-tCSM sustained HyperRAM verify, DQS/non-DQS clock ceiling, READCLKSEL and BURSTDET | **verified** for both PHYs at 60 MHz sync | recorded 334.4 MB/s at CK 192 MHz; BURSTDET stayed clear; harness/phase parameter needs hardware confirmation | `scripts/hyperram_ceiling.py` | retained; current DQS PHY is local and the controller's recovery gap remains caller-owned |
| `hyperram/hyperram_fifo.py` | FIFO-fed burst throughput and data capture | syntax | recorded hardware measurements; not rerun | `scripts/hyperram_fifo.py` | keep as historical throughput probe; ceiling test applies tCSM and address-derived checking |
| `hyperram/hyperram_stress.py` | bulk, retention and random-access fill/verify | syntax | **passed on r1.4 at 120 MHz** — 0/16384 bulk, 0/16384 after ~6 ms retention, 0/4096 random, 119.8 MB/s (`3f436d4`) | JTAG registers; no dedicated current runner | predates the DQS PHY, legal burst cap and shared control measurement. This row read "hardware required" until #189; the file has not changed since the run |
| `hyperram/hyperram_identify.py` | ID registers, density and bank aliasing | syntax | recorded identification result | `scripts/hyperram_identify.py` | diagnostic retained; uses the non-DQS LUNA interface |
| `hyperram/hyperram_regfuzz.py` | configuration-register write/read behaviour | syntax | recorded probe result | `scripts/hyperram_regfuzz.py` | diagnostic retained; register semantics remain device-specific |
| `qspi/qspi_gateware.py` | quad-flash read modes, runtime divisor, byte capture and ceiling | syntax | recorded silicon sweep | `scripts/qspi_burst.py`, `qspi_ladder.py`, `flash_ceiling.py`, `flash_modes.py` | retained; current Apollo QSPI reader and variable clock replace rebuild-per-rate probes |
| `sideband/sideband_gateware.py` | full FPGA_ADV responder, flash paths and diagnostic state | syntax | recorded silicon protocol/soak results | `scripts/sideband_read.py`, `sideband_soak.py`, `sideband_speed_ladder.py`, `sideband_build.py` | retained as the diagnostic bitstream; shipping designs use the smaller `sideband_link.py` block |
| `adv_uart/adv_uart_gateware.py` | Apollo UART advertisement heartbeat and release | syntax | recorded silicon result | JTAG stop register plus Apollo observation | retained narrow end-to-end test; advertiser implementation lives in Apollo FPGA |
| `adv_speed/adv_speed_gateware.py` | FPGA_ADV error rate versus baud and drive mode | syntax | recorded silicon ceiling | `scripts/sideband_speed_ladder.py` and capture tools | retained measurement bitstream; receiver baud is an Apollo firmware setting |
| `power_monitor/power_monitor_gateware.py` | PAC1954 address discovery and register reads | syntax | recorded silicon identification | `scripts/power_probe.py` | retained standalone diagnostic; the SoC has a multiplexed I2C owner and firmware driver |
| `pins/i2c_scan.py` | scan devices on each board I2C branch | syntax | hardware required | JTAG registers | diagnostic; board ownership now lives in `riscv/i2c_mux.py` |
| `pins/fusb302_id.py` | FUSB302 IDs and PAC1954 ID | syntax | recorded board-identification result | JTAG registers | diagnostic retained; firmware exposes richer device tests |
| `pins/pin_survey.py` | loopback, levels and edge counts on otherwise uncovered pins | syntax | hardware required | JTAG registers | retained board diagnostic; no CPU or USB dependency |
| `bram_probe/bram_probe.py` | EBR lane/address decode against known contents | archived `top.bit`; source syntax clean | hardware result recorded in `bram_probe/README.md` | `scripts/bram_probe_expect.py` plus physical pin decode | retained forensic fixture; committed artefacts preserve the exact routed case |
| `loader/bitstream_sink.py` | USB bulk sink and transfer counters for fast-loader work | syntax | recorded USB throughput result | `scripts/bitstream_load_time_probe.py` | retained transport fixture; product ID comes from `usb_ids.py` |
| `usb_bulk/usb_bulk.py` | USB bulk loopback, direct and FIFO-buffered | syntax | recorded throughput result | host USB benchmark tools | retained; current endpoint APIs and unique test VID/PID are in use |
| `usb_bulk/usb_oneway.py` | one-way USB IN streaming ceiling | syntax | recorded throughput result | `scripts/usb_oneway_speed.py` | retained throughput fixture; unique product ID is in `usb_ids.py` |
| `usb_bulk/usb_timing.py` | USB transaction timing instrumentation | syntax | recorded timing result | host USB timing probe | retained diagnostic; unique product ID is in `usb_ids.py` |
| `usb_serial/usb_serial.py` | CDC serial loopback and counters | syntax | recorded 195.4 Mbps loopback | serial/USB host tools | retained reference path; current SoCs reuse its transport shape |
| `led_patterns_simple.py` | LEDs and user-button pattern selection | syntax; archived `led_patterns.bit` exists | manual visual test only | button and LEDs | historical board bring-up; legacy Amaranth CLI surface |
| `led_pattern_gateware_hello_world.py` | USB-controlled LED patterns | syntax | manual visual test only | `led_pattern_hello_world.py` | historical USB bring-up; PID allocation is centralized in `usb_ids.py` |
| `riscv/vexii_bench_soc.py` | generated MicroSoc CoreMark console bridge | syntax; generated `MicroSoc.v` required | hardware result depends on generated image | generated firmware plus USB host | measurement fixture; the Scala/sbt generator is an external prerequisite |
| `riscv/vexii_hello_soc.py` | active Vexii SoC with HyperRAM, flash, USB, JTAG and board I/O | syntax; firmware required to build | recorded silicon boot, console and board tests | `scripts/soc_test.py`, `soc_jtag_stage.py`, console scripts and firmware shell | retained active design; drivers, PAC and memory map are checked together |

## Components that are not bitstreams

These are reusable blocks or host-only checks even though they live in the test
tree.

| source | role | status |
|---|---|---|
| `bist.py` | shared JTAG BIST command/status and comparator | retained; simulated by `scripts/bist_sim.py` |
| `i2c/multiplexed.py` | CSR peripheral prototype for a three-way I2C mux | superseded by `riscv/i2c_mux.py`, which has silicon evidence; its six simulations pass |
| `riscv/*.py` except the four designs above | SoC peripherals, PHYs and CPU wrappers | covered by `scripts/soc_sims.py` and SoC integration |
| `sideband_advertise.py`, `sideband_link.py`, `sideband_debug.py` | drop-in FPGA_ADV blocks | covered by dedicated simulations and active SoC integration |
| `cynthion_platform/`, `build_helpers.py`, `usb_ids.py` | local platform and build/identity support | shared infrastructure, not loadable designs |
