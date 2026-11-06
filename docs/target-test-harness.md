# Target-port test harness

- Issue: [#557](https://github.com/awtoau/cynthion-workspace/issues/557) (fuzz strategy, perf tests). Engine: [`aoa-adaptor.md`](aoa-adaptor.md).
- One case table, two backends; same names, same expected results.

## Files
| file | role |
|---|---|
| `scripts/soc/usb_target_cases.py` | table, expectations, seeded fuzzer, runner, **board backend** (`--board`) |
| `scripts/soc/usb_target_sim.py` | **sim backend**: LUNA device models behind the real shim; registered in `soc_sims.py` |
| `gateware/probes/usb_host/target_device.py` | model device: EP0 + strings, bulk echo EP1, interrupt IN EP2 |
| `gateware/probes/usb_host/model.py` `WireFault` | device-to-host fault injector: drop, byte XOR at index (PID, CRC) |

## Use
- Sim: `./scripts/soc/usb_target_sim.py [group...] [--only name...] [--seed N --fuzz K] [-v]`.
- Board: `./scripts/soc/usb_target_cases.py --board --port target_a --profile generic|aoa_phone [--fuzz K --seed N] [--allow-vbus]`.
- Table: `./scripts/soc/usb_target_cases.py --list` (support per backend and the reason).

## Case
- `needs` = bus level before the case: none, default (addr 0), addressed (addr 1), configured.
- Steps = `(op, args, expected)`; ops are the same on both backends.
- Expected = spec-legal `kinds` set, optional length/data/info; `pin` = exact LUNA-model behaviour (sim profile only).
- Result kinds (driver level, what `usb <port> ...` prints): ack, nak (retry window spent), stall, timeout (handshake 4), crc (5), overflow (6), dropped, hang, noreset.
- Profiles: `model` (HS), `model_fs`, `aoa_phone` (pre-AOA 18d1:4ee2), `generic` (any device: structure only).
- `generic` (Dan, [#572](https://github.com/awtoau/cynthion-workspace/issues/572)): a `ctrl` step that may STALL also passes on NAK-until-limit; the case ends there and the bus is reset before the next (the Pixel's EP0 stays wedged until a reset). `nak_is_refusal` in the cases file.

## Board shell verbs used
- Existing: `role`, `reset`, `setup`, `in`, `out`, `addr`, `aoa`, `desc`.
- Added for this harness: `usb <port> ctrl <8 setup bytes> [out bytes]` (whole control transfer, `Host::control`) and `usb <port> enum <addr>` (`Host::enumerate`, prints `vid:pid`).
- `Host::control` now restarts both toggles after a successful SET_CONFIGURATION.
- Board limits: `out` takes up to 64 bytes, `ctrl` OUT data up to 56, `in` reads up to 64.

## Sim model quirks (handled in the sim backend, not the engine)
- LUNA restarts data toggles only on CLEAR_FEATURE(ENDPOINT_HALT), not on SET_CONFIGURATION or bus reset: the sim backend clears halt on EP 1 OUT/IN and EP 2 IN after each SET_CONFIGURATION.
- LUNA gives no status stage to an IN request with wLength 0: those cases are `UNSUPPORTED` in sim, board-only (and exercise the driver fix below).
- A second HS bus reset needs the device-side 3 ms HS-to-FS fallback scaled (`model.scale_device_hs_fallback_for_simulation`); each reset then costs ~12 s of simulation, so cases are ordered to minimise resets.
- `UlpiPhyModel.host_disconnect` (opt-in, `Bench(report_disconnect=True)`) emits RXCMD HostDisconnect; without it LINK.connected never clears.

## Driver/engine findings from this harness
- `Host::control` took the direction bit as the data stage: wLength 0 with bmRequestType IN sent an OUT status stage. Fixed (status is IN when wLength is 0).
- `Host::control` now restarts toggles after SET_CONFIGURATION and after CLEAR_FEATURE(ENDPOINT_HALT).
- OUT payload over 512 B ([#572](https://github.com/awtoau/cynthion-workspace/issues/572)): the shim's DRAIN waited for its TX FIFO to empty into a full SIE FIFO, forever; nothing but leaving host ended it. Fixed: refused at `start` (`tx_overflow` + `done`, both FIFOs emptied); a bus reset ends any transfer and empties TX. Cases `bulk_out_mps_plus_1`, `bulk_out_oversize_then_bus_reset`.
- OUT payloads up to 512 B pass (the 128-entry TX FIFO is not the limit in host role).
- IN toggle ([#627](https://github.com/awtoau/cynthion-workspace/issues/627)): the driver delivered a DATA PID it did not expect (a resend after a lost ACK). Fixed: `stage` checks `RXPID`, drops the packet (the SIE has ACKed it), keeps the toggle, asks again. Case `toggle_in_mismatch_discarded`.
- Stale RX ([#627](https://github.com/awtoau/cynthion-workspace/issues/627)): the driver popped RX only on ACK, so CRC-bad IN bytes were read by the next IN (and survive a bus reset). The sim model popped on every response and hid it. Fixed: `discard_rx` empties RX after every IN response it does not read; the model matches. Case `handshake_crc_then_clean_in`.
- Pixel EP0 wedge after NAKed requests ([#572](https://github.com/awtoau/cynthion-workspace/issues/572)): device-side. `ctrl_nak_forever_then_good` abandons four NAK-forever requests on the model (vendor 0x5A) and the next SETUPs are ACKed.

## Waits
- Sim: every wait is a poll count with a stated bound (`usb_target_sim.py` constants); a case over 60 s wall fails ("stall guard").
- Board: `soc_shell.Link.read_until_prompt` budgets per verb (`REPLY_BUDGET_S`, `RESET_BUDGET_S`, AOA 12 s); case limit 60 s.

## Fuzz
- `fuzz_cases(seed, n, profile)`: `random.Random(seed)`; case i is the same every run.
- Menu: descriptors (random wLength), strings, bulk echo, interrupt, garbage control requests, wrong address, resets.
- Oracle: per-step expectation where predictable, ANY otherwise; last step always `recover` (reset, enumerate, configure, GET_DESCRIPTOR returns 18 bytes).
- A failure prints `fuzz_seed<S>_<i>` and the replay command; fix = add it to the table.

## Regression cases (real-hardware facts)
- `reset_keeps_speed_after_second_reset`: speed stays HS (or FS on the FS model) at every reset; a later bus reset once left the PHY in FS.
- `reset_second_reset_drives_phy_reset_mode` (sim): the second reset writes PHY op_mode RAW_DRIVE (sie.py fix).

## Unsupported by the engine (recorded in the table, reported `UNSUPPORTED`)
- Isochronous: `EP.type` has SETUP/IN/OUT only; the SIE has no iso path.
- PING: not in the SIE; HS OUT flow control is NAK + retry.
- Suspend/resume: no control; SOFs stop only by leaving host role.
- LS: no LS device model; engine LINK has an LS code, SIE support unverified.
- SOF/uSOF gap injection; babble (RX_OVERFLOW path) needs an injector that appends bytes.
- Board-only gaps: handshake fault injection, toggle override, reset mid-transfer (single-threaded shell), unplug (use `vbus_cycle_reenumerate`; `vbus off` opens every switch).
