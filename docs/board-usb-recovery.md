# Cynthion USB recovery without touching the cables

Script: [`scripts/board/usb_recover.py`](../scripts/board/usb_recover.py). Tests: [`tests/test_usb_recover.py`](../tests/test_usb_recover.py). Measured 2026-11-05, host `selwyn`, kernel 7.2.15.

## When
- `apollo` / the board arbiter fail with `USBTimeoutError` on `GET_ID` (Apollo enumerated but silent), or the kernel logs `device descriptor read/64, error -110` / `device not accepting address` for the Cynthion's port.
- Check first with `./scripts/board/usb_recover.py --status` (read-only).

## What the ladder does (stops at the first step after which Apollo answers)
1. Probe: one `GET_ID`, 500 ms.
2. Cut and restore power on the Cynthion's own hub ports: `sudo -n uhubctl -l <hub> -p <ports> -a cycle -d 5`. Skipped if the hub does not advertise per-port switching (`ppps`). A step that fails or hangs is logged as failed and the ladder goes on.
   - Duration on a dead board: ~92 s (measured 92, 93, 92 s), so the step limit is 120 s and the whole ladder ~2.5 min. After a power-on the kernel retries enumerating the dead device for ~92 s, and every request to that hub queues behind it (`uhubctl`'s status read, even `usbreset`: 0.1 s normally, timed out at 15 s inside that window). The healthy case was never measured.
   - Tried and dropped: terminating `uhubctl` after the power-on to save the wait. It runs as root under `sudo`, so it cannot be signalled (the step still took 92 s), and the hub is busy for those ~92 s regardless.
3. Reset only that hub: `sudo -n usbreset <bus>/<dev>`. Skipped, with the reason, if any process holds a device node (or serial port) of another device on that hub.
4. Still silent: it says so and exits 1.

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
- All of these are control requests the firmware must serve. Not tried on a hung Apollo (the board was not enumerating).

## Not done
- A controller-wide reset (`xhci_hcd` unbind/bind): never run, because it drops every USB device on the host.
- Why the hang starts: see [`yosys-070-upgrade.md`](yosys-070-upgrade.md) (every job loading one particular bitstream killed Apollo; cause open).
