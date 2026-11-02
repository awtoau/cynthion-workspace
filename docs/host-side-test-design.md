# Host-side test design: Cynthion as USB device facing a real host

**Status:** design only; nothing here built or run. Numbers are UNSURE unless a source is cited.
**Related:** [`aoa-adaptor.md`](aoa-adaptor.md) (topology B) · [`usb-host-options.md`](usb-host-options.md) · issues #557 (plan, fuzz/perf), #561 (app control port), #562 (app test streams), #564 (target-port harness, Cynthion as host).

## Terms
- "Target-side" (#564): Cynthion is HOST, DUT is a phone/device. Exists in sim; board-run for the Pixel.
- "Host-side" (this doc): Cynthion is DEVICE/accessory, a real host stack is the DUT or the oracle.
- Three peers in scope:
  - **PC** (Linux, libusb + usbmon): easiest, always available, best oracle.
  - **Pixel as host** (Android USB host API, bench app as client): real phone stack, scriptable via the app.
  - **DJI RC as host**: the real AOA accessory host; reachable only via capture, no scripting.
- Also in scope: phone-as-AOA-device facing the Cynthion host (topology A). That is #564 plus app streams; listed here only for the app-side needs.

## Behaviours to test (host-side)
Each row is one table entry; "oracle" column in the pass/fail section.

| # | area | cases |
|---|---|---|
| H1 | enumeration | attach at HS/FS; chirp; reset; GET_DESCRIPTOR device (len 8, 18, 64, 255), config (9, full, 65535), string, qualifier, BOS; SET_ADDRESS; SET_CONFIGURATION 0/1/bad |
| H2 | ID strings | manufacturer/product/serial; LANGID 0x0409; string index out of range -> STALL; odd lengths; long (253 B) strings; non-ASCII |
| H3 | AOA as device | 51 returns protocol; 52 x6 recorded (index 0..5, NUL-terminated, <=256 B, order, repeats); 53 -> detach, re-attach as `18d1:2d00` / `2d01`; 52 before 51; 53 with bad strings; wrong direction bits |
| H4 | bulk IN/OUT | sizes 0, 1, MPS-1, MPS, MPS+1, 2xMPS, large (64 KiB, 1 MiB); ZLP termination; short packet; HS 512 / FS 64; data toggle recovery; both directions concurrent |
| H5 | NAK/STALL | IN with no data -> NAK until data; OUT with FIFO full -> NAK; halted EP -> STALL; SET_FEATURE/CLEAR_FEATURE(ENDPOINT_HALT); STALL on unknown request; stall clears toggle |
| H6 | reset | reset mid-transfer; repeated resets (regression of #559 on the device side: second reset must be seen); reset during SET_ADDRESS |
| H7 | suspend/resume | host stops SOF 3 ms -> device suspends; remote wakeup (if advertised); resume signalling; state kept |
| H8 | power cycle | VBUS drop and return (host side); device-initiated disconnect (pull-up off) and re-attach; re-enumeration gives the same descriptors |
| H9 | re-enumeration | repeat 100x (UNSURE count); different speed each time; address reuse; AOA re-attach loop |
| H10 | control fuzz | wLength 0/1/7/8/63/64/65/512/65535 vs descriptor/vendor requests; unknown bRequest; short data stage; host aborts status stage |
| H11 | interrupt/iso | record unsupported if the device stack has none (findings, not failures) |

## What the app side needs (bench app, `au.awto.aoabench`)
Reuse, do not duplicate: #561 control port and #562 stream modes already cover most of it.
- Existing/planned modes: passive, echo, sink, source, duml (`bench_modes.dart`). Source/sink take pattern + seed (#562). Sizes 0/1/MPS/MPS+1/multiples/large are #562 scope.
- Direction matrix:

| direction | app role | what it adds |
|---|---|---|
| Cynthion host, phone device (today) | AOA accessory (device side) | nothing new; #561/#562 |
| Pixel host, Cynthion device | **Android USB host client** | NEW: app enumerates the Cynthion, opens bulk pair, runs the same modes against it |
- New app work for Pixel-as-host (not in #561/#562):
  - USB host client class: list `UsbManager` devices, request permission, open `UsbDeviceConnection`, claim interface, `bulkTransfer` loops.
  - Raw control: `controlTransfer(type, req, value, index, buf, len, timeout)` exposed over the control port, so the PC can drive any case from H1-H10.
  - Descriptor dump: return raw device/config/string descriptors as hex (oracle for H1/H2).
  - AOA from the app side: send 51/52/53 as the accessory host to the Cynthion (H3). Uses the same bench identity strings as `aoa_identity.dart`.
  - Reuse source/sink engines unchanged: the transport is the only swap (`UsbDeviceConnection` bulk instead of the AOA `ParcelFileDescriptor`).
- Control-port commands to add (names UNSURE):
  - `usb_list`, `usb_open{vid,pid}`, `usb_close`, `usb_ctrl{...}`, `usb_desc{type,index,len}`, `usb_bulk_in{ep,len,timeout}`, `usb_bulk_out{ep,hex}`, `usb_reset`, `usb_events` (attach/detach log with timestamps).
  - Mode set as in #561 (`mode:{"m":"source",...}`), with `transport: "aoa" | "usbhost"`.
- Wired control caveat (#561): when the phone's one USB port faces the Cynthion, the control port must be wifi, or adb-over-wifi. Document, do not work around.
- DJI RC as host: no app; passive capture (stock analyzer + Packetry, see [`aoa-adaptor.md`](aoa-adaptor.md)) and replay of its recorded 51/52/53 sequence against the Cynthion as device.

## What the Cynthion side needs
Today (see [`gateware/soc/peripherals/usb_host.py`](../gateware/soc/peripherals/usb_host.py)): `UsbPort` roles are `off`, `regs`, `host`, `raw` only. No `device` role on `target_phy`.
- **Device role on target_phy** (missing; the main gap):
  - Mux the existing LUNA device stack onto `target_phy` as a fifth role `device` (same hand-over rules as `host`, see [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md)).
  - Cannot be done by the `raw` role: device responses (handshake turnaround) are bit-times, the RV cannot meet them.
  - Reuse: [`gateware/probes/usb_host/aoa_phone.py`](../gateware/probes/usb_host/aoa_phone.py) already has LUNA devices answering 51/52/53 and a bulk echo behind one UTMI; lift it from a sim model to a synthesizable block behind the role mux.
  - Cost not measured. Logic cells are tight (see the in-SoC cost in [`aoa-adaptor.md`](aoa-adaptor.md#stage-1-cost)); a device stack may fit by sharing with `host` (only one role live at a time). UNSURE.
- **RV-programmable device behaviour** (so the test table, not a rebuild, picks the case):
  - Descriptor RAM: device/config/string descriptors loaded by CSR/FIFO from the RV; no reflash per case.
  - Knobs: force NAK N times, force STALL on EP/request, drop handshake (timeout), corrupt CRC/PID (only to test the host; UNSURE needed), pull-up on/off, speed select (HS/FS), remote wakeup, response delay.
  - Event log to the RV: SETUP (8 bytes), bulk byte counts, reset/suspend/resume, SOF count, with a timestamp per transaction (per #562 granularity).
  - Bulk endpoints: OUT 1 / IN 1 as RV-visible FIFOs (echo, sink, source with pattern+seed generated on the RV; same generator as the app's).
- **Firmware:** `Device` driver beside `Host` in [`usb_host.rs`](../firmware/cynthion-soc/src/usb_host.rs) (name UNSURE); drivers are the API, shell is one front end.
- **Shell verbs** (syntax only, `usb <port> ...`; names UNSURE):
  - `role device`, `dev desc <type> <idx> <hex>`, `dev pullup on|off`, `dev speed hs|fs`
  - `dev nak <ep> <n>`, `dev stall <ep>|clear`, `dev mode echo|sink|source <pattern> <seed>`
  - `dev log [n]` (events), `dev stats`, `dev aoa on|off` (AOA vendor handler + re-attach as 2d00/2d01)
- **Power:** device role needs VBUS from the host and `vbus` off; test H8 needs a way to drop the PC side. Options: PC-side `uhubctl`-style port power (needs a switchable hub, UNSURE available), unplug by pull-up off (cheaper, not the same as VBUS loss), or the Cynthion's own VBUS passthrough control (TARGET-C passthrough UNSURE).
- **AUX CDC device already exists** (`1d50:6180`): can serve as a zero-gateware first target for H1/H2/H4/H6 against a PC host.

## How the PC orchestrates
- One test table shared with #564 (same rows, same ids), with a `direction` field `cynthion_host | cynthion_device`, and a `peer` field `pc | pixel | rc_replay | sim`.
  - Table row: id, area (H1..H11 / target-side areas), peer, params (sizes, request tuples), expected, bound (with reason).
  - Backends: `sim` (LUNA host model against the device block), `board_pc` (libusb on the PC against the Cynthion), `board_pixel` (app control port), `board_rc` (replay + capture).
- Flow per case (board_pixel):
  1. PC selects the case, sets the Cynthion device behaviour over the shell (`scripts/soc_shell.py`).
  2. PC sets the app mode / issues the host-side action over the #561 control port.
  3. PC reads both ends: Cynthion `dev log`/`stats`, app result JSON (`bench_host.py` style), plus usbmon/analyzer capture where cabled.
  4. Pass/fail by the oracle below; logs in `tmp/logs/`.
- Seeds: one seed per run drives stream content (PRBS), sizes, and the order of fuzz steps; both ends regenerate the expected stream from it (#562). A failing case prints seed + case id + step; it joins the regression list.
- Replay: each case is a list of actions (`{target, verb, args}`); replay re-issues the list without randomness. Captured RC sessions convert to the same action list (51/52/53 with the recorded strings).
- Bounds: every wait has a stated limit and reason; stall timer (no progress for a fixed window) rather than a long total timeout; a hang is a failure.
- Layers: sim first, then PC-as-host on the board, then Pixel, then RC replay.

## Pass/fail oracles
| oracle | source | decides |
|---|---|---|
| host stack result | PC libusb return code/bytes, app `UsbDeviceConnection` result | descriptors read, STALL seen as STALL, NAK as timeout vs retry |
| expected descriptors | table row (golden bytes) | H1/H2 byte-exact compare |
| stream compare | seed regenerates stream (#562) | H4 first mismatch offset, byte/frame counts |
| device log | Cynthion `dev log` | request seen, order, toggle, count; cross-check with host view |
| capture ground truth | usbmon on PC; analyzer for phone/RC | packet-level disagreement between the two ends |
| enumeration identity | `lsusb`/`dumpsys usb` (`current_accessory`) | H3 end state `2d00/2d01` |
| stall timer | harness | any hang = fail, logs last event and silence |
- Disagreement between host view and device log is a finding in its own right (file an issue, per rules).
- Unsupported (iso, interrupt, split) = recorded finding, not a silent skip.

## Supported today vs missing
| piece | status |
|---|---|
| Host SIE engine, `host`/`raw` roles, AOA host sequence (sim + Pixel on board) | exists ([`aoa-adaptor.md`](aoa-adaptor.md)) |
| AOA phone/device model (51/52/53, detach, `2d00` echo) | sim only (`aoa_phone.py`) |
| LUNA device stack on AUX (CDC-ACM) | exists; usable as PC-host target now |
| `device` role on `target_phy` | missing |
| RV-programmable descriptors/NAK/STALL knobs, event log | missing |
| Device driver + shell verbs | missing |
| VBUS drop control toward a PC host | missing/UNSURE |
| App control port (#561), streams (#562) | issues open; bench modes passive/echo/sink/source/duml exist |
| App USB-host client + raw control commands | missing |
| Shared test table with `direction`/`peer` (#564) | in progress by another agent, sim-only |
| RC replay | missing; capture of the RC not taken |
| Interrupt/iso on the device stack | UNSURE; likely absent |

## Increment order (smallest first)
Each increment: a sim or PC check, then a bench check; one issue each.
1. **PC host vs AUX CDC device** (no gateware): libusb script runs H1, H2, H10, bulk sizes H4 and reset H6 against `1d50:6180`; usbmon as oracle. Proves the table format and oracles. Smallest.
2. **Table schema**: add `direction` + `peer` fields to the #564 table; no new tests yet. Coordinate with the #564 agent.
3. **App #561 + #562 land** (other session); verify the Cynthion-host direction with them (target-side), since host-side reuses the same plumbing.
4. **App raw USB-host client**: `usb_list/open/ctrl/desc/bulk_*` commands; test it first against a PC-attached known device (a Linux gadget or any stick), PC oracle via usbmon.
5. **Device role in sim**: lift `aoa_phone.py` into a role-muxed block, sim group with the LUNA host model; H1-H3, H6 first.
6. **Device role on board, PC host**: shell verbs `role device`, `dev desc`, `dev mode echo`; H1-H5 against the PC.
7. **Behaviour knobs + event log** (NAK/STALL/pull-up/speed); H5, H7, H8 (pull-up variant).
8. **AOA as device on board**: `dev aoa on`, PC runs `scripts/tools/aoa_probe.py` as the accessory host against the Cynthion; H3 end-to-end, re-attach as `2d00`.
9. **Pixel as host** against the Cynthion device: H1-H5 through the app control port; compare with the PC run (same table rows, `peer=pixel`).
10. **VBUS power-cycle (H8 full), suspend/resume (H7), re-enumeration loops (H9)**.
11. **RC replay and capture**: capture a real RC session (stock analyzer), convert to an action list, replay against the Cynthion device.
12. **Fuzz mode**: seeded random walk over the table with state resets; regression list from failing seeds.

## Open questions (to file as issues, not asked here)
- Does the device block fit alongside the host block in the 91%-COMB SoC (role-exclusive sharing)? Needs a cell-count run, not done.
- Switchable-hub or VBUS-drop option for H8 on the PC side.
- Is a Pixel host-mode client with the Cynthion as an AOA *device* meaningful (Android hosts do not normally speak AOA accessory mode to a device)? Likely only raw control + bulk; AOA H3 then targets the PC and the RC.
