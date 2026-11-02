# Apollo change tracking

For Apollo firmware work on USB mode, CDC/UART behaviour and issue verification. Design reference: [apollo_serial_interface_and_mode_exclusivity_design.md](apollo_serial_interface_and_mode_exclusivity_design.md).

- Apollo tree: `repos/apollo` (submodule, `awtoau/awto-apollo`). Evidence: this repo's `tmp/`. Tracking: GitHub issue comments (e.g. [#22](https://github.com/awtoau/cynthion-workspace/issues/22)).
- Patches are commits in the submodule; no loose `patches/apollo/` copy (recover with `git log` there).

## Apollo commands

- Host CLI: `info`, `jtag-scan`, `flash-info`; `flash-erase`, `flash-program`, `flash-fast`, `flash-read`; `configure`, `reconfigure`, `force-offline`; `spi`, `spi-inv`, `spi-reg`, `jtag-spi`, `jtag-reg`, `svf`; `leds`.
- Device dispatch `firmware/src/vendor.c`: JTAG (start/stop, set/get buffers, scan, run clock, goto state, get state, bulk scan); control (trigger reconfiguration, force FPGA offline, allow FPGA takeover USB); SPI (debug SPI send/read, flash SPI send, take/release flash lines).

## UART vs JTAG on cynthion_d11

- UART (PA11/PA14) shares pins with JTAG TMS/TDI, so they are mutually exclusive during programming (pin table: [../chips/samd11-apollo.md](../chips/samd11-apollo.md); decision: the mode design doc).
- `apollo flash-program` and `apollo configure` need JTAG ownership of those pins. Any process opening the Apollo CDC ACM port can trigger UART callbacks mid-transaction.
- Before flash/program/configure: stop `ttyACM*` readers and serial monitors.
- Symptom of contention: `usb.core.USBError: [Errno 32] Pipe error`, or an ECP5/JTAG timeout in `apollo flash-program`.

## Per change set

1. Intent note (hypothesis, target files, verification plan), usually `firmware/src/console.c`, `firmware/src/main.c`.
2. Build: `cd repos/apollo/firmware && make APOLLO_BOARD=cynthion`. Flash: `make APOLLO_BOARD=cynthion dfu`.
3. Runtime evidence in `tmp/` with date stamps: `lsusb` mode polling (615c vs 615b), tty discovery from sysfs, serial capture/probe output.
4. Commit in the submodule.
5. One issue comment: summary, build/flash/runtime outcome, artifact filenames, pass/fail against close criteria.

## Close criteria (verification issues)

1. Expected USB mode stable over the test window.
2. Data-plane behaviour shown by runtime evidence.
3. Reproduction steps documented.
4. Commit referenced.
5. Final comment references all evidence files.

## Log entry template

```
## YYYY-MM-DDTHH:MM:SS+10:00 - <short title>
- Hypothesis:
- Files changed:
- Build / Flash / Runtime result:
- Artifacts: tmp/<file>, <submodule commit>
- Issue update: <issue URL>
- Status: pass | fail | partial
```
