# Cynthion USB recovery without touching the cables

Script: [`scripts/board/usb_recover.py`](../scripts/board/usb_recover.py). Tests: [`tests/test_usb_recover.py`](../tests/test_usb_recover.py). Measured 2026-11-05, host `selwyn`, kernel 7.2.15.

## When
- `apollo` / the board arbiter fail with `USBTimeoutError` on `GET_ID` (Apollo enumerated but silent), or the kernel logs `device descriptor read/64, error -110` / `device not accepting address` for the Cynthion's port.
- Check first with `./scripts/board/usb_recover.py --status` (read-only).

## What the ladder does (stops at the first step after which Apollo answers)
1. Probe: one `GET_ID`, 500 ms.
2. Bootloader reset: vendor `0xED` sent raw (the `apollo` CLI gates on `GET_ID`), then `apollo exit-dfu`. A WDT reset of the SAMD11, no hub involved. On a hung Apollo the request times out (tested 2026-11-05): the control endpoint is not served.
3. Cut and restore power on the Cynthion's own hub ports: `sudo -n uhubctl -l <hub> -p <ports> -a cycle -d 5`. Skipped if the hub does not advertise per-port switching (`ppps`). A step that fails or hangs is logged as failed and the ladder goes on.
   - Duration on a dead board: ~92 s (measured 92, 93, 92 s), so the step limit is 120 s and the whole ladder ~2.5 min. After a power-on the kernel retries enumerating the dead device for ~92 s, and every request to that hub queues behind it (`uhubctl`'s status read, even `usbreset`: 0.1 s normally, timed out at 15 s inside that window). The healthy case was never measured.
   - Tried and dropped: terminating `uhubctl` after the power-on to save the wait. It runs as root under `sudo`, so it cannot be signalled (the step still took 92 s), and the hub is busy for those ~92 s regardless.
4. Reset only that hub: `sudo -n usbreset <bus>/<dev>`. Skipped, with the reason, if any process holds a device node (or serial port) of another device on that hub.
5. Still silent: it says so and exits 1.

## Safety
- Only the Cynthion's ports and its own hub. It never resets a whole USB controller: that drops every device, including other projects' serial adapters (a `switch_console_daemon.py` for another board was running during this work).
- Needs passwordless `sudo` for steps 2 and 3. Without it, the script reports the failure and stops.
- Port names are sysfs names (`1-1.3.1.3.3.3` = bus 1, hubs 1 -> 1.3 -> 1.3.1 -> 1.3.1.3 -> 1.3.1.3.3, port 3). Found by vendor/product when enumerated, else the last ones seen (`tmp/usb_recover_ports.txt`) or `--port`.

## Results (the hung state: Apollo enumerated but silent, then not enumerating)
| step | outcome |
|---|---|
| `usbreset` of the Cynthion's device | the device re-enumerated as "not accepting address", then vanished |
| `uhubctl` cycle of hub ports 3 and 4, 5 s off | the port disconnected and came back 6 s later in the same dead state |
| `usbreset` of the hub | the hub's other two devices re-enumerated cleanly; the Cynthion port still failed |
| unplugging every cable for a few seconds (by hand) | recovered, three times |

- So the software steps did not revive a hung Apollo here. The likely reason: the dock hub advertises `ppps` but does not actually remove VBUS from the port, so the board never lost power. This is not proven; the alternative is a host-side fault (the kernel also logs `invalid context state for evaluate context command`, and the Cynthion sits 5 hubs deep, the USB maximum).
- A hub that really switches VBUS (or a direct motherboard port that does) would make step 2 a true power cycle. Nothing here confirms either.
- What worked every time is removing all power by hand. SWD access to the SAMD11 (Tag-Connect TC2030-CTX, PA30/PA31, [`hardware.md`](hardware.md)) would also reset it without USB; not set up.

## Reset paths that exist without cables (documented elsewhere)
- `apollo enter-dfu` / `exit-dfu`: vendor `0xed`, WDT reset of the SAMD11 into the Saturn-V bootloader and back. [`programming-paths.md`](chips/ecp5/programming-paths.md), [`hardware.md`](hardware.md), [`upstream-bugs.md`](upstream-bugs.md) (#67).
- Vendor `0xec` emergency reset: control-plane state only, not an MCU reset.
- DTR/RTS: `tud_cdc_line_state_cb` only starts the UART; no reset.
- Tested 2026-11-05 on a healthy Apollo: `apollo enter-dfu` re-enumerated as "Cynthion Bootloader"; `exit-dfu` returned to the application as `1d50:615b` (FPGA analyzer owns CONTROL); `apollo force-offline` took it back as `1d50:615c`, GET_ID answers.
- All of these are control requests the firmware must serve. On a hung Apollo `0xED` timed out (2026-11-05, `tmp/isolate-B/top.bit` loaded through the arbiter): enumerated as `1d50:615c`, every control transfer times out, the AUX console gone.
- UART mode (`./scripts/apollo/adv_health.py --uart`, watchdog poll running) was selected before that load and did not prevent the hang. Control with `tmp/isolate-X/top.bit`: mode stayed `uart` through the configure, health `watchdog 0`. With no gateware loaded: `watchdog 1` (link dead), 5 timeouts.
- After `uhubctl` cycled port 3 alone (AUX was not enumerated, so it was not in the list) Apollo re-enumerated as `1d50:615c`, still silent.
- Apollo returns CONTROL to itself when the FPGA's advertisement stops (EIC edges or UART frame; [`cynone-sideband.md`](chips/cynone-sideband.md) §8, §14). PROGRAM held at RESET forces the FPGA offline ([`hardware.md`](hardware.md)); both are physical buttons.

## Apollo resets itself: main-loop watchdog (built, not flashed)
- `repos/apollo` `68388b9`: `hang_wd_enable()` in `main()`, `hang_wd_kick()` in the 20 ms housekeeping block. WDT period 512 clocks at 1.024 kHz = 500 ms; on expiry the SAMD11 resets and re-enumerates.
- Covers any hang where the main loop stops iterating (unbounded SYNCBUSY / `tx_bits_left` waits, fault handlers, a stalled tick). Does not cover a hang where the loop runs but USB does not answer.
- Timeout: longest stall from the code is 100 ms (`board_delay(100)` in `usb_switch.c`), not measured on a board; 500 ms is 5x. The kick spacing keeps the write-synchronised `WDT->CLEAR` off a busy sync.
- Flash 13736 of 14336 (ceiling 13748, 12 B headroom): `scripts/apollo/apollo_budget_check.py`, `verify_vectors.py` pass; `repos/apollo/firmware/test` `test_hang_wd` passes.
- To test, needs a live Apollo: flash via `apollo enter-dfu` + `dfu-util` ([`apollo-firmware.md`](apollo-firmware.md)), then reproduce the hang with `tmp/isolate-B/top.bit` and see whether Apollo re-enumerates and answers `GET_ID` by itself.
- Risk: the WDT is cleared by every reset (as `dfu.c` relies on), so a bad setting only reboot-loops the application; the Saturn-V bootloader still runs. Not verified.

## Not done
- A controller-wide reset (`xhci_hcd` unbind/bind): never run, because it drops every USB device on the host.
- Why the hang starts: see [`yosys-070-upgrade.md`](yosys-070-upgrade.md) (every job loading one particular bitstream killed Apollo; cause open).
