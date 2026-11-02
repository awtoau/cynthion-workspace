# AOA adaptor on Cynthion — design for review

**Status:** proposal, nothing built. Source: read-only study 2026-11-02 (Sonnet agent; claims below UNSURE where marked).
**Index:** [`usb-host-options.md`](usb-host-options.md) (host-core study) · [`hardware.md`](hardware.md)

## Goal
- Cynthion bridges an Android Open Accessory (AOA) link to a Linux PC.
- The RV does the protocol and a diagnostics side channel (power, port analysis, console). Gateware stays minimal.
- The PC needs no custom driver.

## Decisions (Dan, 2026-11-02)
| # | question | answer |
|---|---|---|
| 1 | AOA peer | **A** (Cynthion hosts a phone). C (tap) later, built on A. |
| 2 | phone port | **either**: A and C share one data bus and `target_phy`. Native C cable works: `vbus charge` powers TARGET-A and C and presents Rp on C. A-to-C cable on A also fine (accepted). |
| 3 | speed | **high speed preferred**; full speed is the fallback. |
| 4 | PC driver | **libusb** (Dan, 2026-11-02, after the trade-off below). The AOA mirror is a vendor bulk interface read through libusb. A stock CDC-ACM console stays alongside it as a convenience unless dropped. |
| 5-6 | streams / console | **one USB port to the PC carries everything**: the AOA mirror, diagnostics and console. Simpler cabling; the link never runs at 100% so there is room. |
| 7 | A-to-C cable | yes. |
| 8 | bitstream | **dedicated** SoC bitstream (below). |
| 9 | PC link | **AUX**; CONTROL stays for programming, test and diagnostics (shared with Apollo). |
| 10 | RC behaviour | handshake strings already known (V, awto-dji live tests). **Build the AOA test app first** (awto-dji bench app); capture the real RC only if byte-level timing is needed. |

### PC side: stock cdc_acm vs libusb
| | stock `cdc_acm` only | libusb interface (+ ACM for console) |
|---|---|---|
| PC setup | none; `/dev/ttyACM*` on Linux/Windows/macOS | udev rule (Linux) or WinUSB/Zadig (Windows); our tool |
| data model | byte stream: packet boundaries, zero-length packets and exact sizes are lost | true packets: sizes, ZLPs and boundaries visible |
| fits "mirror AOA" | only with our own framing | 1:1: PC bulk IN/OUT = the phone's AOA bulk IN/OUT |
| fits the test rig (size sweep, ZLP, fuzz) | weak: cannot see a ZLP bug | strong |
| speed | fine; line settings meaningless | full bulk rate |
- Chosen: **composite** = interface 0 vendor bulk pair (the AOA mirror, libusb) + CDC-ACM interfaces for console/diagnostics (stock, no PC setup). Both on AUX (`1d50:6180` family, new PID in `usb_ids.py`).
- Cost: LUNA descriptor work for the composite device; effort UNSURE.

### Bitstream: dedicated vs also passing the analyzer
- **Dedicated SoC bitstream (chosen).** One design with the role-per-PHY engine (host/device/raw). Topology A needs no analyzer: the Cynthion is the host, so the RV already sees every byte it sends and receives.
- The stock analyzer stays a separate bitstream: `cynthion run analyzer` loads it from the wheel, and it is in FPGA flash (replug boots it). Use it to capture the **real** RC to phone link (a tap, not our host) with Packetry.
- Combining capture and host in one bitstream is topology C: a second PHY and an analyzer core (~18k LUT projected, not measured). Defer until A works.

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
- Host SIE (GUH) vendored at [`gateware/probes/usb_host/guh/`](../gateware/probes/usb_host/guh); 21 simulated checks ([`usb_host_sie_sim.py`](../scripts/soc/usb_host_sie_sim.py)): chirp, CRC, GET_DESCRIPTOR, SET_ADDRESS, SET_CONFIGURATION, 512 B bulk IN.
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

### Diagram

```
 A. Cynthion = accessory HOST            Android phone is the DEVICE
 ┌──────────┐ ACM0 data  ┌─────────────────────────────┐  TARGET-A   ┌─────────┐
 │ Linux PC │◄──────────►│ CONTROL/AUX   FPGA          │◄───────────►│ Android │
 │ cdc_acm  │ ACM1 diag  │  CDC-ACM x2   host SIE+shim │ VBUS out    │ phone   │
 └──────────┘◄───────────│               RV: 51/52/53, │ AOA bulk    │ (device)│
                         │               pump, diag    │             └─────────┘
                         └─────────────────────────────┘

 B. Cynthion = the PHONE                 existing accessory is the HOST
 ┌──────────┐ ACM0 data  ┌─────────────────────────────┐  TARGET-C   ┌─────────┐
 │ Linux PC │◄──────────►│ CONTROL/AUX   FPGA          │◄───────────►│ DJI RC  │
 │ cdc_acm  │ ACM1 diag  │  CDC-ACM x2   device stack  │ no VBUS out │ (host)  │
 └──────────┘◄───────────│               RV: answer 51,│ AOA bulk    │ accessory
                         │               log 52, on 53 │             └─────────┘
                         │               re-attach 2d00│
                         └─────────────────────────────┘

 C. Cynthion = tap (host + device + analyzer)       needs two PHYs, AUX loses console
 ┌─────────┐ TARGET-C ┌───────────────────────────────┐ TARGET-A ┌─────────┐
 │ DJI RC  │◄────────►│ device stack ◄─RV─► host SIE  │◄────────►│ phone   │
 │ (host)  │          │      └── analyzer ──┘         │ VBUS out │ (device)│
 └─────────┘          └───────────┬───────────────────┘          └─────────┘
                          CONTROL │ capture + diag
                                  ▼
                              Linux PC
```

- Arrows are data; A and B put the AOA bulk pipe on ACM0 and RV diagnostics on ACM1.
- Host SIE exists (simulated, +2.1k LUT); device stack exists (LUNA); the CSR/FIFO shim and `target_phy` hand-over are the missing pieces.

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

## Bring-up console: drive USB by hand from the RV shell
- Goal: type host transactions on the AUX console to explore a device before any gateware host core exists. The RV is the software SIE.
- Why it fits: ULPI is a byte stream at 60 MHz; the RV cannot bit-bang it live, but it can fill a TX FIFO and drain an RX FIFO through CSRs. That FIFO shim is the same shim topology A needs.
- **Port-generic, not host-only.** One ULPI stream engine per PHY, and a runtime **role** per PHY: `device` (today's CDC on AUX), `host`, `raw` (RV bit-bangs), `analyzer`, `off`. Any PHY can be used for anything; the verbs hang off the existing `usb <port>` command, not a new `usbh`.
- Board limits to respect: three PHYs for four connectors. `target_phy` serves the TARGET-C/A pair (passthrough); `control_phy` is shared with Apollo; `aux_phy` carries the console. `usb3343` register access exists only on `target_phy` today. The console must stay reachable while AUX is re-roled (fallback: the Apollo UART path, which conflicts with JTAG).
- Missing piece (gateware): per PHY, a mux between the LUNA device stack / host SIE / FIFO shim, plus TX FIFO (TXCMD + bytes) and RX FIFO (RXCMD + bytes + line-state events). ~200-400 LUT per PHY, 0 BRAM (LUTRAM FIFOs). `target_phy` first (hand-over from `UlpiRegisters`).
- Proposed verbs (firmware on top of the shim; `<port>` = `aux|control|target_a|target_c`):
  - `usb <port> role device|host|raw|off`, `usb <port> power on|off` (VBUS via the existing gated `vbus`), `usb <port> reset` (bus reset + speed), `usb <port>` (status: role, line state, speed, VBUS mA; exists in part)
  - `usb <port> tx <hex..>` / `rx` (raw packets), `setup <8 bytes>` + `in <ep> <len>` (control transfer by hand)
  - `usb <port> desc` (GET_DESCRIPTOR), `usb <port> aoa` (51, 52×6, 53, wait re-attach)
- Exists today: `usb3343 status` (PHY registers, line state), `usb target_a` (rail), `vbus`, `pac1954`. No raw TX/RX, no role switch.
- Prior art: LUNA has no host gateware (only `ulpi.py`). GSG's RV-plus-serial tool is Moondancer (GCP over USB), device-side emulation only. Only host reference: Tiliqua's USB MIDI host (apfaudio/tiliqua PR 65, closed 2024-11-05). Vendored GUH SIE is the sim-proven core.

## Pixel on TARGET-A, 2026-11-02
- Power: TARGET-A has no switch of its own; `vbus control` passes CONTROL's VBUS to it. The firmware refuses unless a power sample is ≤200 ms old: run `pac1954 rate 50` first, `pac1954 rate 0` after (else `no: stale`).
- Result: draw climbed 85 → 455 mA in ~100 ms and held ~450 mA at 5.0 V (a device is there and charging).
- PHY line state stayed `dp 0 dm 0` (host pulldowns on, OTG control `06`): no D+ pull-up, so the phone is not attaching for data. Likely locked screen / charge-only; retry unlocked with USB set to File transfer (PTP/MTP) after `usbh`-level tooling exists, or first from the PC as Stage 0.
- Console bug found on the way: `usb target_a` said "nothing drawing" while 450 mA flowed (signed compare) — [#551](https://github.com/awtoau/cynthion-workspace/issues/551), fixed in firmware, not yet flashed.

## The phone side already exists: awto-dji
- `/mnt/2tb/git/awto-dji` (Flutter app `au.awto.dji.awto_dji_app`, installed on the Pixel 3 XL; wireless adb at the Pixel's current `adb devices -l` address). It is the AOA **device**; the DJI RC is the accessory **host** (docs: `aoa-live-test-2026-10-23.md`, `-26.md`, `phone-bridge-operator-guide.md`).
- Accessory filter ([`accessory_filter.xml`]): manufacturer `DJI`, model `T600`. The RC also sends description `DJI`, version `v1.2.3.4`, uri `www.dji.com`, serial `12345678` (read off a live phone with `dumpsys usb`). Source: `lib/transport/aoa_identity.dart`.
- Driven over adb with no screen: `adb shell am start -a au.awto.dji.action.CONNECT --es relay <host>:<port>`; `adb shell dumpsys usb` shows `current_functions=ACCESSORY`, `current_accessory=...`; `adb logcat` shows `[transport:android-accessory] state=...`, `[link] rx`, `[link] packet`.
- **Hazard:** a `CONNECT` intent does not carry the auto-grant. `AoaShim` waits on Android's "Allow AWTO DJI to access ...?" dialog; with nobody to answer it, it sat in `connecting` (60 s timeout added in `7385613`, unverified on hardware). Accept it with `adb shell input tap` (resolve coordinates from `uiautomator dump`, not hardcoded) or replug so `USB_ACCESSORY_ATTACHED` launches it with the grant.
- So with Cynthion as accessory host (topology A) and this app on the Pixel, one session controls both ends: the RV sends 51/52×6/53 and then DUML-style bytes; adb reads what the phone saw. The Pixel being wireless-adb means its USB port is free for the Cynthion.
- Scope (awto-dji CLAUDE.md): interoperability with an owned aircraft; no geofence defeat. Test traffic here is handshake plus loopback, not flight commands.

## Plan
0. **Stage 0 tool:** [`scripts/tools/aoa_probe.py`](../scripts/tools/aoa_probe.py) plays the accessory from the PC with the DJI identity above, then lists the `18d1:2d00/2d01` endpoints. Needs the Pixel on a PC USB port (not the Cynthion) and the permission dialog answered. Its `usbmon` capture is the byte-exact target for the RV.
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
