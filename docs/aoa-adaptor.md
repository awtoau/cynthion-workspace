# AOA adaptor on Cynthion — design for review

**Status:** proposal, nothing built. Source: read-only study 2026-11-02 (Sonnet agent; claims below UNSURE where marked).
**Index:** [`usb-host-options.md`](usb-host-options.md) (host-core study) · [`hardware.md`](hardware.md)

## Goal
- Cynthion bridges an Android Open Accessory (AOA) link to a Linux PC.
- The RV does the protocol and a diagnostics side channel (power, port analysis, console). Gateware stays minimal.
- The PC needs no custom driver.

## AOA in one page (spec: <https://source.android.com/docs/core/interaction/accessories/aoa>, AOA 1.0)
- Accessory = USB **host**; powers the bus (500 mA, 5 V). Android phone = USB **device**.
- Already in accessory mode if VID `0x18D1`, PID `0x2D00` (AOA) or `0x2D01` (AOA + ADB).
- Otherwise, vendor control requests:
  1. `51` GET_PROTOCOL (IN, 16-bit LE version; non-zero = supported)
  2. `52` SEND_STRING (OUT, once per string; wIndex 0 manufacturer, 1 model, 2 description, 3 version, 4 URI, 5 serial; NUL-terminated, ≤256 B)
  3. `53` START (OUT, no data)
- Phone drops off the bus, re-enumerates as `18d1:2d00/2d01`: bus reset, SET_ADDRESS, SET_CONFIGURATION 1, find bulk IN + OUT.
- Send all header strings (Android ≤10 can reboot on a version mismatch). AOA and MTP do not coexist. AOA 2.0 (HID, audio) not studied.

## What exists in this repo
- Host SIE (GUH) vendored at [`gateware/probes/usb_host/guh/`](../gateware/probes/usb_host/guh); 21 simulated checks ([`usb_host_sie_sim.py`](../scripts/usb_host_sie_sim.py)): chirp, CRC, GET_DESCRIPTOR, SET_ADDRESS, SET_CONFIGURATION, 512 B bulk IN.
- Cost: +2080 LUT, 0 BRAM (SIE); +2910 LUT with enumerator ([`usb-host-options.md`](usb-host-options.md) §12, §22).
- **Not done:** no CSR/FIFO shim to the SoC; `target_phy` still owned by `UlpiRegisters` (#120, #125); no firmware; no VBUS drive for host mode (UNSURE: [`vbus_csr.py`](../gateware/soc/peripherals/vbus_csr.py) postdates the study); never run on hardware.
- Known SIE traps: 8-bit `rx_len` wraps at 512 B; caller tracks data toggle; no completion strobe; NYET read as ACK; no split transactions.
- Device side: CDC-ACM on `aux_phy` (`1d50:6180`), ~195 Mbps loopback ([`usb-performance.md`](usb-performance.md)).

## Topologies
| | Cynthion is | Port facing the Android side | Gateware | RV | Risk |
|---|---|---|---|---|---|
| A | accessory host | TARGET-A (Type-A, the only VBUS-out port) | host SIE + CSR/FIFO shim + VBUS CSR; CDC to PC exists | enumerate, 51/52×6/53, wait re-attach, enumerate, bulk pump + toggles | medium |
| B | the phone, facing an existing host (e.g. DJI RC) | TARGET-C | device stack only, no host core | answer 51, record 52 strings, on 53 detach/re-attach as `18d1:2d00`, bridge bulk to PC | low gateware; Type-C role handling with a DJI RC UNSURE |
| C | passthrough tap that also speaks AOA | both | host + device on two PHYs + analyzer | as A and B | high (~18k LUT projected, not measured); AUX loses the console |

## Linux side (no custom driver)
- Linux can already be an AOA accessory with libusb; no kernel driver. Stage 0 needs only a PC and a phone.
- Recommended: composite device with **two stock CDC-ACM functions**, `cdc_acm` binds both.
  - ACM0 = AOA data pipe (transparent).
  - ACM1 = diagnostics from the RV (power, ports, console).
  - Needs 6 endpoints beyond EP0 + IAD descriptors; a second ACM is a LUNA descriptor change (effort UNSURE).
- Alternatives: one ACM with tag-byte/COBS framing (PC demux needed); CDC-NCM network (more gateware/CPU); vendor bulk + libusb (udev rule). FunctionFS does not apply (gadget side).
- New PID in [`usb_ids.py`](../gateware/usb_ids.py); not `0x615b`/`0x615c`.

## Size and risk (topology A)
- Smallest host ≈ +2.3-2.5k LUT → fabric ~62-64%, BRAM unchanged (study §12.2; figures not measured in-situ).
- VBUS: closing `control_vbus_en`/`aux_vbus_en` powers TARGET-A; whether CONTROL's switch reaches it is UNSURE (§19). Gate on a fresh PAC1954 reading.
- Hot-plug needs `target_a_discharge` and `HostDisconnect`. USB3343 host pulldown must be enabled (LUNA disables it).
- Phone is high-speed; the host half of the chirp is simulation only. Fall back to full speed if needed.
- After `53` the phone vanishes and returns: firmware needs a disconnect timeout/watchdog; GUH has no error recovery.
- `sync` domain is the tight one (71.6 MHz vs 60); put the host in `usb`.
- Upstream: no host mode in Cynthion ([cynthion#230](https://github.com/greatscottgadgets/cynthion/issues/230)); only reference is LUNA host in Tiliqua. Maintainers refuse LLM-generated contributions: this stays in the fork.

## Plan
1. **Stage 0, PC only:** libusb script as accessory against a phone: 51, 52×6, 53, wait for `18d1:2d00`, bulk echo with an Android test app. Capture with usbmon. Pin the DJI RC's exact behaviour first (Q1).
2. **Stage 1, simulation:** AOA model device on `model.py`'s `USBDevice` in `usb_host_sie_sim.py`; build the shim; keep `soc_sims.py` green.
3. **Stage 2, board:** re-parent `target_phy` → VBUS to TARGET-A → print a phone's device descriptor → run the sequence → bridge to ACM. Pass: PC reads the test app's bytes from `/dev/ttyACM*`, SoC log shows `18d1:2d00`.

## Questions for Dan (answer in a few words)
1. Is the AOA peer a phone (Cynthion hosts → A) or an existing AOA host such as the DJI RC (Cynthion plays the phone → B)?
2. Which port does the Android device use: TARGET-A or TARGET-C?
3. Full speed enough, or high speed needed?
4. PC: stock `cdc_acm` only, or libusb tool allowed?
5. One data stream + one diagnostics ACM acceptable?
6. Console stays on AUX (`1d50:6180`) or may move to CONTROL?
7. A-to-C cable acceptable on the Android side?
8. Dedicated bitstream, or must it also pass the stock analyzer?
9. PC link on AUX or CONTROL (CONTROL is shared with Apollo)?
10. Is the DJI RC's AOA behaviour (strings, version) known, or does it need a capture?
