# Bugs we found in upstream code

One list of defects found in code or tools this repo does not own. Policy and what we replaced: [`upstream-boundary.md`](upstream-boundary.md). Bugs other people found: [`upstream-watch.md`](upstream-watch.md).

- Reporting upstream needs Dan's approval (public, third-party repos); upstream does not accept AI-written contributions. "Reported" below is therefore "no" unless stated.
- Only upstream origin is listed. Our own bugs are not.
- UNSURE = the issue does not settle whether the fault is upstream's, or whether it is a bug rather than documented behaviour.
- State: **fixed upstream** / **patched** (in our fork, vendored file or local binary) / **worked around** / **open**.
- Issue links: `#N` = [awtoau/cynthion-workspace](https://github.com/awtoau/cynthion-workspace/issues).

## cynthion (Python host library)

| component / file | bug | our issue | state | reported |
|---|---|---|---|---|
| `python/src/commands/cynthion_setup.py` | udev rules read with bare `open(...).readlines()`; file handles leak | [#1](https://github.com/awtoau/cynthion-workspace/issues/1) | open | no |
| `python/src/commands/cynthion_setup.py` | `cmd.split()` builds the `cp` argv; paths with spaces split wrongly | [#2](https://github.com/awtoau/cynthion-workspace/issues/2) | open | no |
| `python/src/commands/cynthion_build.py` | `cargo objcopy` return code unchecked; flash step runs after a failed build | [#3](https://github.com/awtoau/cynthion-workspace/issues/3) | open | no |
| `python/src/cynthion.py` | placeholder serial key `"TODO"` in `active_connections`; devices without serials alias to one connection | [#4](https://github.com/awtoau/cynthion-workspace/issues/4) | open | no |
| `python/src/selftest/host.py` | two identical consecutive PHY register writes (copy/paste) | [#5](https://github.com/awtoau/cynthion-workspace/issues/5) | open | no |
| platform `cynthion_r1_4.py`, `ram` resource | HyperRAM pins carry no `DRIVE` and a `clock_skew=127` TODO; electrical settings are an inherited default (UNSURE: a gap, not a defect) | [#311](https://github.com/awtoau/cynthion-workspace/issues/311), [#416](https://github.com/awtoau/cynthion-workspace/issues/416), [#459](https://github.com/awtoau/cynthion-workspace/issues/459) | worked around (vendored `gateware/board/`) | no |
| platform `cynthion_r1_4.py` | build pins speed grade 8 (`ECP5_SPEED_GRADE`); board part is `-6`. UNSURE: [cynthion#259](https://github.com/greatscottgadgets/cynthion/issues/259) says grade does not matter | [#474](https://github.com/awtoau/cynthion-workspace/issues/474) | open | no |

## moondancer firmware and facedancer

| component / file | bug | our issue | state | reported |
|---|---|---|---|---|
| moondancer `gcp/moondancer.rs` `read_endpoint` | no buffered packet returns a zero-length packet (a `TODO`), indistinguishable from a real ZLP | [#23](https://github.com/awtoau/cynthion-workspace/issues/23) | open | no |
| moondancer `bin/moondancer.rs` | event-queue overflow spins forever; a recoverable overload becomes a hang | [#14](https://github.com/awtoau/cynthion-workspace/issues/14) | patched (`7378797`: drop the event) | no |
| moondancer `panic_log.rs` | panic handler spins with no host notification; looks like a USB hang | [#13](https://github.com/awtoau/cynthion-workspace/issues/13) | open (UNSURE: missing feature, not a defect) | no |
| moondancer firmware | endpoint `max_packet_size` above 512 returns EINVAL (rejects SuperSpeed descriptors at HS) | [#43](https://github.com/awtoau/cynthion-workspace/issues/43), [#38](https://github.com/awtoau/cynthion-workspace/issues/38) | patched (`6fa3e0a`, clamp to 512) | no |
| moondancer `write_endpoint` | log message names the wrong function | [#38](https://github.com/awtoau/cynthion-workspace/issues/38) | patched (`c0144f4`) | no |
| cynthion facedancer gateware | duplicate `ep_iso_in` in the module hierarchy (elaboration bug); no issue, commit `56a64c2` | none | patched | no |
| moondancer firmware | isochronous endpoints unsupported (treated as bulk/interrupt); proxying a UVC camera crashes with `LIBUSB_ERROR_PIPE` and leaves the device needing a power cycle. UNSURE: missing feature; reporter on [cynthion#220](https://github.com/greatscottgadgets/cynthion/issues/220) | [#6](https://github.com/awtoau/cynthion-workspace/issues/6), [#11](https://github.com/awtoau/cynthion-workspace/issues/11) | open (skeleton only, `4ab5361`) | no (upstream issue exists from someone else) |
| facedancer `configuration.py` | `AttributeError` on an IAD before the first interface descriptor (`last_interface` is `None`) | [#8](https://github.com/awtoau/cynthion-workspace/issues/8) | patched (checkout outside this tree) | no |
| facedancer `backends/base.py` | `validate_configuration` raises on a repeated endpoint address across alternate settings (valid for UVC) | [#9](https://github.com/awtoau/cynthion-workspace/issues/9) | patched (same) | no |
| facedancer `backends/moondancer.py` | `configure_endpoints` sends duplicate triplets and oversize `max_packet_size`; firmware answers EINVAL | [#10](https://github.com/awtoau/cynthion-workspace/issues/10) | patched (same) | no |

## luna

No defect found in the USB device stack ([`upstream-boundary.md`](upstream-boundary.md)). Present-fix audit: [#570](https://github.com/awtoau/cynthion-workspace/issues/570).

| component / file | bug | our issue | state | reported |
|---|---|---|---|---|
| `gateware/interface/jtag.py` `JTAGRegisterInterface` | (1) shifts on a `sync`-sampled TCK edge: readback slips one bit below ~5x oversampling; (2) undecoded addresses return `0xDEADBEEF`; (3) a register at address 29 silently dropped while 28 worked | [#204](https://github.com/awtoau/cynthion-workspace/issues/204) | worked around (replaced by `gateware/probes/jtag_registers.py`) | no |
| `gateware/interface/psram.py` `HyperRAMDQSPHY` | does not elaborate on Amaranth 0.5 (`bus.dq.io` on a non-raw request, `o_Z` drives pads inside `DELAYG`, `bus.reset` never driven); no clock generator makes its `fast` domain | [#200](https://github.com/awtoau/cynthion-workspace/issues/200) | worked around (DQS parked, [#587](https://github.com/awtoau/cynthion-workspace/issues/587)) | no (cynthion#147 open upstream, no comments) |
| `psram.py` HyperRAM controller | `RECOVERY` falls through to `IDLE` with no tCSHI gap; `extra_latency \| 1` makes the low-latency branch dead | [#215](https://github.com/awtoau/cynthion-workspace/issues/215) | patched in the vendored DQS controller; non-DQS stack retired ([#584](https://github.com/awtoau/cynthion-workspace/issues/584)) | no |
| `psram.py` / DQS PHY | `phy.rwds.i` never driven, so variable latency cannot work | [#534](https://github.com/awtoau/cynthion-workspace/issues/534), [#540](https://github.com/awtoau/cynthion-workspace/issues/540), [#543](https://github.com/awtoau/cynthion-workspace/issues/543) | worked around (fixed latency); DQS parked | no |
| `psram.py` controller | `LATCH_RWDS` samples RWDS before the CA is sent; READ_DATA unbounded; no tCSM bound. UNSURE: controllers were vendored and edited, origin of each fault not separated | [#321](https://github.com/awtoau/cynthion-workspace/issues/321), [#316](https://github.com/awtoau/cynthion-workspace/issues/316), [#317](https://github.com/awtoau/cynthion-workspace/issues/317) | patched in our copy; superseded by the clean-room core ([#581](https://github.com/awtoau/cynthion-workspace/issues/581)) | no |
| `psram.py` `HyperRAMPHY` | all IO delays constant (`DELAYF` `DEL_VALUE=0`, CK offset 80 taps): read sample point and CK phase cannot be centred | [#582](https://github.com/awtoau/cynthion-workspace/issues/582), [#585](https://github.com/awtoau/cynthion-workspace/issues/585) | worked around (clean-room core) | no |
| `DQS` edge clock | routed through fabric; no `ECLKBRIDGECS` instantiated. UNSURE: reported against a build, confirmed in ours | [#314](https://github.com/awtoau/cynthion-workspace/issues/314) | open upstream; ours fixed `7d88981` | no |

## luna-soc and amaranth-soc

| component / file | bug | our issue | state | reported |
|---|---|---|---|---|
| luna-soc `FairSPIControlPortCrossbar` | re-arbitrates when the holder stops asserting `cs`, but `cs` is a hold: starvation, and a held CS can be cut mid-command | [#460](https://github.com/awtoau/cynthion-workspace/issues/460), [#442](https://github.com/awtoau/cynthion-workspace/issues/442) | patched (`HoldableSPIController` / fair crossbar in `gateware/soc/peripherals/flash.py`); [#460](https://github.com/awtoau/cynthion-workspace/issues/460) still open for the I-cache-miss case | no |
| luna-soc SPI controller | CS field is `csr.action.W` (one-cycle pulse) used as a latch; CS fragments into 8-bit windows | [#442](https://github.com/awtoau/cynthion-workspace/issues/442) (UNSURE: no dedicated issue) | patched (`HoldableSPIController`) | no |
| luna-soc UART | `oe = ~tx.rdy` releases the pad at the start of the stop bit; intermittent corruption | [#113](https://github.com/awtoau/cynthion-workspace/issues/113) (UNSURE: cited by the boundary doc, title is about Apollo UART) | patched (`SerialLine`) | no |
| luna-soc vendored `amaranth_soc` | stale vendored copy (2025-01-07) silently used when `amaranth-soc` is not installed; different gateware, no error | [#190](https://github.com/awtoau/cynthion-workspace/issues/190) | worked around (declare the dependency) | no |
| amaranth-soc `csr.Register` | fails on Python 3.14 (annotations); [luna-soc#48](https://github.com/greatscottgadgets/luna-soc/issues/48) | [#192](https://github.com/awtoau/cynthion-workspace/issues/192), [#194](https://github.com/awtoau/cynthion-workspace/issues/194) | fixed upstream (amaranth-soc `d8b5892`) | upstream issue exists |

## apollo (SAMD11 firmware and Python host)

| component / file | bug | our issue | state | reported |
|---|---|---|---|---|
| firmware `fpga.c` | `fpga_online` not `volatile`; USB ISR writes, main loop reads | [#70](https://github.com/awtoau/cynthion-workspace/issues/70) | patched (`da564f8`) | no |
| firmware `fpga_adv.c` | `edge_counter` read-then-clear races `EIC_Handler`; can drop a takeover request | [#71](https://github.com/awtoau/cynthion-workspace/issues/71) | patched (`da564f8`) | no |
| firmware, pins PA10/11/14/15 | JTAG(USB), JTAG(SPI) and UART share pins with no arbitration; a CDC line-coding callback re-pinmuxes PA14 during JTAG | [#65](https://github.com/awtoau/cynthion-workspace/issues/65) | patched (`39a2213`, `6cc219e`) | no |
| host `FlashBridgeConnection` | port handed back from `__del__`; `flash-fast` segfaults and wedges Apollo's USB until physical reset | [#75](https://github.com/awtoau/cynthion-workspace/issues/75) | patched (`8054f62`) | no |
| udev rules | cover `1209:000a`/`000e`, not `000f` (flash bridge); `flash-fast` fails EACCES. No dedicated issue, see [#75](https://github.com/awtoau/cynthion-workspace/issues/75) | [#75](https://github.com/awtoau/cynthion-workspace/issues/75) | patched (`6f16848`) | no |
| `apollo_fpga/gateware/advertiser.py` | `ApolloAdvertiser` drives `.o` never `.oe` on a `dir="io"` pin; EIC-mode advertisement may never reach the wire (read from source, not confirmed on hardware) | [#181](https://github.com/awtoau/cynthion-workspace/issues/181) | open | no |
| `configure` / `trigger_fpga_reconfiguration()` | exits 0 with no design running; INITN never released after the PROGRAMN pulse ([`chips/ecp5/reconfigure-initn-gap.md`](chips/ecp5/reconfigure-initn-gap.md)). UNSURE: link between the two not proven | [#360](https://github.com/awtoau/cynthion-workspace/issues/360) | open | no |
| firmware CDC / UART | first line after a tty open answers `unknown command`; suspected lazy `uart_configure_pinmux()` glitching RX. UNSURE | [#113](https://github.com/awtoau/cynthion-workspace/issues/113) | worked around (send a bare CR) | no |
| `saturn-v` bootloader | cannot reset itself out of DFU (stays on watchdog reset cause) | [#67](https://github.com/awtoau/cynthion-workspace/issues/67) | worked around (`exit-dfu`, `12e6d97`) | no |
| TinyUSB DFU runtime | `tud_dfu_runtime_reboot_to_dfu_cb()` arms the WDT and hangs; reboot must be deferred to the ACK stage. UNSURE: TinyUSB or Apollo's use of it | [#67](https://github.com/awtoau/cynthion-workspace/issues/67) | worked around (`df4a93b`) | no |

## yosys, nextpnr, prjtrellis

| tool / component | bug | our issue | state | reported |
|---|---|---|---|---|
| yosys `write_edif` | emits a dangling `cellRef` for `$scopeinfo`: skipped in the library loop, not in instance emission | [#197](https://github.com/awtoau/cynthion-workspace/issues/197) | open (still unfixed upstream when checked) | no (no upstream issue exists) |
| yosys `opt_clean` memory analysis (`cells_all.cc`, `fixup_unused_cells_and_mems`) | parallel workers call non-const `dict::operator[]` / `at()`, which rehash lazily: MEMID assert at `rtlil.cc:2631` (our August aborts), segfault, hang; armed at 12 / 30 / 51 / 106 / 216 memories in one module. Present in 0.65+57 and 0.70+329. The August silent-different-netlist run is not explained by it | [#356](https://github.com/awtoau/cynthion-workspace/issues/356), [#306](https://github.com/awtoau/cynthion-workspace/issues/306); patch + tests: [`yosys-opt-clean-race.md`](yosys-opt-clean-race.md) | worked around (`YOSYS_MAX_THREADS=1`); patch ready, not posted | yes, by others: [YosysHQ/yosys#6418](https://github.com/YosysHQ/yosys/issues/6418) (open; candidate diff in the thread, no PR) |
| yosys 0.70 `abc9` (`abc_ops_reintegrate`) | ties the unused `M` of every async `TRELLIS_FF` to 0 (box input pins come back as constant 0); nextpnr 0.10-74 and 0.11.1 reject the netlist in packing. Not in 0.65+57 | [#602](https://github.com/awtoau/cynthion-workspace/issues/602); [`yosys-070-upgrade.md`](yosys-070-upgrade.md) | worked around (`scripts/yosys/trellis_ff_without_m.v`); not adopted | no (draft ready, needs Dan's go) |
| nextpnr `ecp5/pack.cc` `pack_dqsbuf` | requires the DQSI net to have a single sink; the routing database and Diamond accept a fabric tap | [#543](https://github.com/awtoau/cynthion-workspace/issues/543), [#540](https://github.com/awtoau/cynthion-workspace/issues/540) | patched locally; patch dropped with DQS (`f3ffdda`); decision on where the fix lives still open | no |
| nextpnr / prjtrellis `--12k` | chipdb is the 25F die (24,288 LUT4, 56 EBR); Diamond enforces the 12F marking. Undocumented upstream? (UNSURE: a documentation gap, not a code bug) | [#499](https://github.com/awtoau/cynthion-workspace/issues/499) | open | no |
| nextpnr `--parallel-refine` | Fmax spread 14.34 MHz on one fixed netlist and seed; still 12.80 MHz across three builds without it. UNSURE: nondeterminism may be known behaviour | [#361](https://github.com/awtoau/cynthion-workspace/issues/361), [#441](https://github.com/awtoau/cynthion-workspace/issues/441), [#407](https://github.com/awtoau/cynthion-workspace/issues/407) | worked around (no-parallel-refine plus seed spread) | no |
| nextpnr timing constraint | 60 and 80 MHz requests give bit-identical Fmax on 12/12 seeds; achieved frequency does not track the request. UNSURE: tool behaviour | [#478](https://github.com/awtoau/cynthion-workspace/issues/478), [#482](https://github.com/awtoau/cynthion-workspace/issues/482) | worked around (do not use Fmax as evidence) | no |

## amaranth

No defect found in Amaranth itself. Related, not bugs: Amaranth 0.6 removes `Record` ([#83](https://github.com/awtoau/cynthion-workspace/issues/83)); Amaranth leaves unrequested pins undriven ([`upstream-boundary.md`](upstream-boundary.md#vendored-board-platform)).

## VexiiRiscv

| component | bug | our issue | state | reported |
|---|---|---|---|---|
| `Param.scala` | `PerformanceCounterPlugin` cannot be dropped while `rdtime` is kept; `--performance-counters` dropped generates a byte-identical core. UNSURE: design choice, not a defect | [#471](https://github.com/awtoau/cynthion-workspace/issues/471) | worked around | no |

## GUH (apfaudio/guh, vendored host SIE)

| component | bug | our issue | state | reported |
|---|---|---|---|---|
| `guh/sie.py` | SIE FSM applies the reset controller's PHY mode only in `RESET` (power-up); later bus resets never reach the PHY | [#559](https://github.com/awtoau/cynthion-workspace/issues/559), [#571](https://github.com/awtoau/cynthion-workspace/issues/571) | patched (`sie.py` drives PHY mode whenever `reset_active`; the vendored file is no longer byte-identical); [#571](https://github.com/awtoau/cynthion-workspace/issues/571) (alternating reset failures) closed | no (GUH does not accept contributions) |

## Android `f_accessory`

| behaviour | bug | our issue | state | reported |
|---|---|---|---|---|
| accessory read ([`aoa-adaptor.md`](aoa-adaptor.md)) | a transfer of exactly one packet (512 B at HS) with no ZLP is ACKed and never delivered to the app. UNSURE: by design (short packet or full buffer completes a read) | [#575](https://github.com/awtoau/cynthion-workspace/issues/575) | worked around (host sends ZLP / format change) | no |

## Issues this list covers

#1 #2 #3 #4 #5 #6 #8 #9 [#10](https://github.com/awtoau/cynthion-workspace/issues/10) [#11](https://github.com/awtoau/cynthion-workspace/issues/11) [#13](https://github.com/awtoau/cynthion-workspace/issues/13) [#14](https://github.com/awtoau/cynthion-workspace/issues/14) [#23](https://github.com/awtoau/cynthion-workspace/issues/23) [#38](https://github.com/awtoau/cynthion-workspace/issues/38) [#43](https://github.com/awtoau/cynthion-workspace/issues/43) [#65](https://github.com/awtoau/cynthion-workspace/issues/65) [#67](https://github.com/awtoau/cynthion-workspace/issues/67) [#70](https://github.com/awtoau/cynthion-workspace/issues/70) [#71](https://github.com/awtoau/cynthion-workspace/issues/71) [#75](https://github.com/awtoau/cynthion-workspace/issues/75) [#83](https://github.com/awtoau/cynthion-workspace/issues/83) [#113](https://github.com/awtoau/cynthion-workspace/issues/113) [#181](https://github.com/awtoau/cynthion-workspace/issues/181) [#190](https://github.com/awtoau/cynthion-workspace/issues/190) [#192](https://github.com/awtoau/cynthion-workspace/issues/192) [#194](https://github.com/awtoau/cynthion-workspace/issues/194) [#197](https://github.com/awtoau/cynthion-workspace/issues/197) [#200](https://github.com/awtoau/cynthion-workspace/issues/200) [#204](https://github.com/awtoau/cynthion-workspace/issues/204) [#215](https://github.com/awtoau/cynthion-workspace/issues/215) [#306](https://github.com/awtoau/cynthion-workspace/issues/306) [#311](https://github.com/awtoau/cynthion-workspace/issues/311) [#314](https://github.com/awtoau/cynthion-workspace/issues/314) [#316](https://github.com/awtoau/cynthion-workspace/issues/316) [#317](https://github.com/awtoau/cynthion-workspace/issues/317) [#321](https://github.com/awtoau/cynthion-workspace/issues/321) [#356](https://github.com/awtoau/cynthion-workspace/issues/356) [#360](https://github.com/awtoau/cynthion-workspace/issues/360) [#361](https://github.com/awtoau/cynthion-workspace/issues/361) [#407](https://github.com/awtoau/cynthion-workspace/issues/407) [#416](https://github.com/awtoau/cynthion-workspace/issues/416) [#441](https://github.com/awtoau/cynthion-workspace/issues/441) [#442](https://github.com/awtoau/cynthion-workspace/issues/442) [#459](https://github.com/awtoau/cynthion-workspace/issues/459) [#460](https://github.com/awtoau/cynthion-workspace/issues/460) [#471](https://github.com/awtoau/cynthion-workspace/issues/471) [#474](https://github.com/awtoau/cynthion-workspace/issues/474) [#478](https://github.com/awtoau/cynthion-workspace/issues/478) [#482](https://github.com/awtoau/cynthion-workspace/issues/482) [#499](https://github.com/awtoau/cynthion-workspace/issues/499) [#534](https://github.com/awtoau/cynthion-workspace/issues/534) [#540](https://github.com/awtoau/cynthion-workspace/issues/540) [#543](https://github.com/awtoau/cynthion-workspace/issues/543) [#559](https://github.com/awtoau/cynthion-workspace/issues/559) [#571](https://github.com/awtoau/cynthion-workspace/issues/571) [#575](https://github.com/awtoau/cynthion-workspace/issues/575) [#582](https://github.com/awtoau/cynthion-workspace/issues/582) [#585](https://github.com/awtoau/cynthion-workspace/issues/585)
