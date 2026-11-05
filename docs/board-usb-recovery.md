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

## Apollo resets itself: main-loop watchdog (flashed, tested 2026-11-05)
- `repos/apollo` `4ccf7e7`: `hang_wd_enable()` in `main()`, `hang_wd_kick()` in the 20 ms housekeeping block. WDT period 16384 clocks = 500 ms; on expiry the SAMD11 resets.
- Result: loading `tmp/isolate-B/top.bit` (hung Apollo 5 of 5 before) now ends with Apollo reset into the Saturn-V bootloader (`Cynthion Bootloader`, #67: a WDT reset stays in the bootloader). `apollo exit-dfu` then `apollo force-offline` brought it back with no cables. The hang is a stopped main loop.
- WDT clock is 32.768 kHz, not 1.024 kHz: `./scripts/apollo/wd_timing.py` (vendor `0xED`, firmware stops kicking) saw the device vanish 0.527 s after the request with 16384 clocks. The first build used 512 clocks (16 ms): shorter than boot, so the application reset-looped into the bootloader until reflashed.
- Timeout: longest stall from the code is 100 ms (`board_delay(100)` in `usb_switch.c`), not measured on a board; 500 ms is 5x.
- Flash 13736 of 14336 (ceiling 13748): `scripts/apollo/apollo_budget_check.py`, `verify_vectors.py`, `repos/apollo/firmware/test` `test_hang_wd` pass.
- `usb_recover.py` first rung: `leave bootloader` (`exit-dfu`, `force-offline`) when Apollo shows as the bootloader.
- Root cause (found 2026-11-05 with the experiment build `repos/apollo` branch `exp-hang-pc`: WDT early-warning and HardFault handlers saving the stacked PC, the request history and the bytes after `comms` into RAM above the stack): `jtag_scan_finish_step` stored the bit-banged tail's TDO byte at `jtag_in_buffer + slow_offset` even for scans that discard TDO. `jtag_in_buffer` is the second 512 bytes of the 1024-byte `comms` region and `slow_offset` reaches the chunk length, so a final chunk over 513 bytes wrote past the region.
  - `isolate B`'s final chunk is 692 bytes: the byte landed on the last byte of `_ctrl_xfer.complete_cb`. TDO was high (`0xFF`), giving `0xff003851`; the next control-transfer ACK called it: HardFault at `0xff003874`, the USB stack dead, the device still enumerated.
  - Why only some loads hung: the offset depends on the final chunk length (X's 818 bytes lands elsewhere) and on TDO (`X` padded to `B`'s length and the invalid stream read TDO low, writing `0x00`, a NULL callback that TinyUSB skips).
  - Fix `repos/apollo` `a476c69` (test `703e450`): the tail's destination is NULL for discard scans (`jtag_tail.h`), `jtag_tap_shift` skips a NULL output. Flash 13744 of 14336 (ceiling 13748). `isolate B`, `X`, `B` all load and pass on the flashed build.
- Why no test saw it:
  - The scan path had no test. Apollo's host tests covered `apollo_mode` and the FPGA_ADV watchdog logic only; the workspace sims model the gateware; `soc_test --board` and `hyperram_board_test` run through the SoC, after configure. Nothing varied the final chunk's length or looked at Apollo's RAM.
  - The `jtag.c` comments argued the property for the DMA path (`FLAG_DISCARD_TDO` stops the receive) and called a corrupted bitstream "structurally impossible"; the bit-banged tail was a second writer the argument never covered.
  - The write is one byte, and 512 of the 1024 possible final-chunk lengths (513 and up) go out of bounds, but where it lands depends on the length and on the RAM layout, which moves with every firmware change. Most landings hit data that is rewritten before use; `B`'s 692 bytes hit a live pointer, and only when TDO was high.
  - `stack_probe` checks the stack's high-water mark, not `.bss` neighbours.
  - Now: `repos/apollo/firmware/test/test_jtag_scan_tail.c` compiles the real `jtag.c` on a host (stub headers, fake SPI and TAP) and sweeps every bit count and flag combination plus the benchmark. Run against `a476c69^` it fails (512 final-chunk lengths out of bounds); it also failed on the first fix for 14 capturing scans (4097..4103 bits) and 8 benchmark chunk sizes, both fixed in `a16c6b8`.
- Review (Fable, read-only, 2026-11-05) found those two. Still open, not fixed:
  - `FORCE_BITBANG` with `wValue >= 256` shifts only `wValue & 0xFF` bits (`slow_bits` is a `uint8_t`); `ecp5.py` uses bitbang for background SPI transfers.
  - JTAG requests are accepted with no session: a `0xB1` without `0xBF` writes `comms.jtag_tx`, which is the live console ring (`jtag.c` comments call that impossible).
  - A benchmark at a very low SCK can run past the 500 ms watchdog (`jtag.c` assumes 12 MHz).
  - Its claim that the WDT clock is 1.024 kHz (a 16 s timeout) is contradicted by the measurement above: the device vanished 0.527 s after the last kick with 16384 clocks.
- The watchdog stays: it turns any other main-loop stop into a software-recoverable reset. A HardFault is also in `Dummy_Handler`'s spin, which the watchdog now ends.
- Instruments kept: [`wd_capture.py`](../scripts/apollo/wd_capture.py) (needs the experiment firmware), [`hang_bisect.py`](../scripts/apollo/hang_bisect.py), [`wd_timing.py`](../scripts/apollo/wd_timing.py), [`adv_health.py`](../scripts/apollo/adv_health.py).

## Not done
- A controller-wide reset (`xhci_hcd` unbind/bind): never run, because it drops every USB device on the host.
- Why the hang starts: see [`yosys-070-upgrade.md`](yosys-070-upgrade.md) (every job loading one particular bitstream killed Apollo; cause open).
