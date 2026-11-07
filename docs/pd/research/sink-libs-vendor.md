<!-- Haiku research report, 2026-11-07, read-only survey. Leads, not verified facts: INFERRED marks are the agent's. Index: ../README.md -->

# USB PD sink quirks and vendor charging protocols: read-only survey

Method: `gh api` contents/readme reads and `gh search code|issues`. No clones were made, because the read-only constraint forbids writing files. Repo prefixes below are GitHub `owner/repo`. Issue URLs were read directly.

## 0. Coverage and gaps

| Item | Status |
|---|---|
| pdsink/pdsink `docs/` | Only `docs/README.md` (a usage guide) and `images/`. No compliance notes or PPS/EPR quirk docs exist here. |
| Ralim/IronOS PD source (`source/Core/Drivers/USBPD.cpp`) | Not read. Only issue [#1599](https://github.com/awtoau/cynthion-workspace/issues/1599) and [#1904](https://github.com/awtoau/cynthion-workspace/issues/1904) bodies and comments were read. |
| puzrin/reflow_micro PD | Only README.md:25 ("Powered from a 100-140 W USB PD charger"). `doc/` was listed but not grepped. |
| Apple 2.4A/1.0A divider levels, Apple charger ID | Not found in any repo read. |
| Samsung Galaxy S-series phone PPS/APDO behaviour | Not found. Only Samsung charger models appear (EP-TA845, EP-T6530, a 25 W unit). |
| Oppo VOOC/SuperVOOC, MediaTek PE, QC4 | Not found (`gh search code "VOOC"` and `"MediaTek PE"` returned unrelated repos). |
| Samsung PD VDM / SVID 0x04E8, Google 0x18D1 | Not found. 0x04E8 and 0x18D1 hits are USB VIDs in unrelated udev/doc files. |
| Real raw PD captures or phone Get_Sink_Cap responses | None quoted in any repo read. |
| Repo-wide `gh search issues` in pdsink, kws, manuelbl, Ralim/usb-pd | Only unrelated titles returned. No phone issues. |

## 1. Phone and charger quirks

| Device | Symptom | Workaround / fix seen | Source |
|---|---|---|---|
| Samsung EP-TA845 (45 W PPS, Galaxy S20 Ultra bundle) | PPS VPDO at PDO index 7 disabled the PPS timer. Charger cut power or hard-reset after ~10-30 s. | Fixed in firmware: `PPSTimerEnabled` is set from the selected APDO type. Ralim/usb-pd#17 (closed) and https://github.com/Ralim/IronOS/issues/1599 (comment by Samuelrmlink) | https://github.com/Ralim/usb-pd/issues/17 ; Ralim/usb-pd:src/policy_engine_states.cpp:110-129, :593 |
| Samsung EP-T6530 (65 W) | Pinecil v2 / TS101 reboots once tip heats above ~100 C. Stable in DFU mode. | Open. Reported as fixed by "PD tweaks" in dev builds; not confirmed. | https://github.com/Ralim/IronOS/issues/1668 ; https://github.com/Ralim/IronOS/issues/2104 (comment by CakeIsYummy21) |
| Samsung 25 W PD charger (bundled with TS80P) | Charger picks QC instead of PD. TC66C charger reboots the iron every few seconds. | Open. | https://github.com/Ralim/IronOS/issues/1009 (comment by DjordjeMandic) |
| Anker 735 GaNPrime 65 W; Anker 140 W power bank | TS101 on v2.23 RC3 reboots permanently. Reboots right after PD debug "state 6". v2.22 works. | Open. Ralim: "Anker chargers have had other issues before." | https://github.com/Ralim/IronOS/issues/2104 |
| Apple A1947 61 W | Advertises 20.3V3A / 15V3A / 9V3A / 5.2V3A. Works with a Pinecil v2 that overshoots to 20 V on a 3 A charger when the tip is 6.2 ohm. | Open (no firmware workaround in thread). | https://github.com/Ralim/IronOS/issues/1904 ; [#1668](https://github.com/awtoau/cynthion-workspace/issues/1668) (Apple charger "works good") |
| Apple charger (unspecified) | Works with the same Samsung-reboot setup in [#1668](https://github.com/awtoau/cynthion-workspace/issues/1668). | None needed. | https://github.com/Ralim/IronOS/issues/1668 |
| MacBook M2 Pro | TS101 cycles PD debug states 3 then 6, then reboots. | Open. | https://github.com/Ralim/IronOS/issues/1887 |
| Baseus CCGAN10CUS 100 W | PD intermittently stays at 5 V with the Pinecil v2. | Tested a PPS-fix firmware (see Samsung EP-TA845 row). | https://github.com/Ralim/IronOS/issues/1599 |
| Nedis 65 W travel charger | v2.22 firmware reaches only 16 V; v2.21 reached 20 V. | Open. | https://github.com/Ralim/IronOS/issues/1810 |
| Lenovo 65 W and UGREEN 100 W | "DC Low" warning. PD debug shows state 25; working chargers show state 12. | Open. | https://github.com/Ralim/IronOS/issues/2050 |
| HP GaN 65 W (claims PD/PPS/QC5.0/AFC) | Reported incompatible with Pinecil v2. | Issue body only; no resolution in body. | https://github.com/Ralim/IronOS/issues/2109 |
| Chargers "picky" about cables | Samsung EP-T6530 and Orico/Toocki 240 W cables fail; Samsung DX510 cable works. | Cable choice. | https://github.com/Ralim/IronOS/issues/1668 (comment by StevenSeifried) |
| Laptop (unnamed) | PD completes: 20 V request, ACCEPT, PS_RDY, DR swap, Discover Identity, no resets. Draws 0.00 A. D+/D- rest near 0 V with a 0.85 V blip every second. Plain inline meter gets power. | Cause unknown. Stock firmware behaves the same. | kralonur/kws-x1-standalone-rs:README.md:1362-1372 |
| Phone sink in port check | Phone sees meter's sink pull-down and switches to sourcing ("as when it charges earbuds"). | Used by PD Session port check. | kws README.md:~865-873 |
| Phone CC at 5.1 kohm via A-to-C cable | Reads 0.46 V in passthrough (56 kohm pull-up to VBUS path). | Used to infer meter Rd (~2.9 kohm). | kws README.md:179-183 |
| Meter's own Rd | 0.92 V on CC vs 1.68 V expected for 3 A. | Not explained. Overview corrects TYPE-C current. | kws README.md:170-172 |
| Galaxy S-series / Pixel / iPhone as PPS sink | Nothing found. | n/a | none |
| Samsung Galaxy phone bundled in cable tests | Not found. | n/a | none |

Note: every Samsung and Apple entry above is a charger, not a phone. None of these repos documents phone-as-sink PPS behaviour.

## 2. Vendor protocols on D+/D- and in PD VDMs

Role in these repos: kws-x1 is a **sink/master** for AFC, SCP, FCP, QC. A Cynthion source would need the **slave/adapter** side (INFERRED from afc.rs:1-12 and kws README:1447-1487, which say the meter sends the pings and checks for four slave pings).

### 2a. Protocol table

| Protocol | Source advertises | Sink sends / expects | Voltages / timings (as cited) | Relevance to a source with CC, VBUS, PHY on D+/D- |
|---|---|---|---|---|
| BC1.2 (SDP/CDP/DCP) | Dedicated charger = D+ shorted to D- (kws README:435-436, "`BC1.2` ... `5V shorted`"). | Sink holds D+ at 0.6 V until charger opens its short and D- falls (embedded `docs/single-wire.md`:12-13). | 0.6 V D+ hold. | Source side must short D+/D- for DCP and release it for SDP/CDP (INFERRED: no source-side BC1.2 code found). CDP and SDP detection not documented in these repos. |
| Apple "divider" (TI TPS2514, DIVIDER) | Fixed D+/D- levels by current class: 5 W = D+ 2 V / D- 2.7 V (1.00 A); 10 W = D+ 2.7 V / D- 2 V (2.00 A); 12 W = 2.7 V on both (2.40 A) (kws README:429-432, cites TPS2514 section 8.3.3). | Sink reads only (kws Lines screen, README:462-466). | Above levels. | Source can drive these with a PHY. Apple 2.4A/1.0A names are NOT in these repos. |
| 1.2 V DCP | 1.2 V on both D+ and D- (kws README:433-434, TPS2514 section 8.3.4). No rating published. | Reads only. | 1.2 V. | Drivable with a PHY. |
| Samsung AFC | Adapter answers the phone's master pings with 4 slave pings (afc.rs:1-12 and kws README:1447-1487; in this repo the meter is the master). Request bytes: 0x08 = 5 V, 0x46 = 9 V (kws README:1465; afc.rs:60-63). | Master ping then 5 V or 9 V request. Detection needs all four slave pings. | D+ held at 0.6 V after BC1.2 entry (single-wire.md:16-17). MPING 16 UI; RESPONSE_UI 7; slave pings 5-20 UI; response-data skip 2 ms (not decoded); final gap 200 us; reset = 100 UI D- high (16 ms) (afc.rs:17-33). UI = 160 us. Up to 5 request groups, >=300 ms apart (kws README:1466-1468). | Relevant. Source = slave, so it needs D- sampling and reply pings, not requests. Reference timings are community-hosted (gta7lite/android_kernel_samsung_mt6765), not a public spec. Not verified on hardware beyond one charger (kws README:1487). |
| Qualcomm QC 2.0 | Charger has D+/D- short for BC1.2 entry. Sink holds D+ at 0.6 V until D- released (kws README:1488-1500). | Sink requests 5/9/12/20 V by D+/D- pulse patterns. Charger returns to plain USB 5 V on OFF. | 5, 9, 12, 20 V (kws README:1515-1519). Charger ignores QC under a PD contract (README:1494-1498). | Source-side pattern and voltages needed. Qualcomm spec not public (embedded README.md:110-112). |
| Qualcomm QC 3.0 | Continuous mode, then 200 mV steps (kws README:1502-1510). | Sink steps up/down in 0.2 V. | 3.6 V to 12 V (20 V Class B). Step each 100 ms scan (README:1507-1510). | As QC 2.0. |
| QC output check | Charger gives no ACK. Sink watches VBUS. | Request not visible on VBUS after 610 ms = 60 ms filter + 550 ms PS transition: IGNORED (README:1531-1533). | 610 ms. | Source must reach target within this window if it wants the sink to report OK. |
| QC 4 | Not found. | | | |
| Huawei SCP | Adapter type read via register 0x7E (fallback 0x80, 0x81). Rating registers read-only. | Sink checks D+ at 2.6-3.0 V, then reads registers. D- single wire. | D+ 2.6-3.0 V for detection (single-wire.md:13-14; Table B.2). Tvdp_src_on 40-80 ms (single-wire.md:14-15). Watchdog 2 s (README:1386-1400). | Source = responder on D- single-wire (single-wire-protocols "over a caller-owned single-wire PHY on D-", single-wire.md:3-4). No authentication (README:136-138). |
| Huawei FCP | Capability register 0x21; power 0x22; voltage list 0x30+; fault flags 0x28; voltage apply 0x2B; voltage write 0x2C (README:1425-1437). Lowest listed voltage usually 5.2 V. | Clears fault flags, writes 0x2C, reads back, applies 0x2B. | Same D- single-wire entry as SCP. | As SCP. Not run on hardware (README:1445). |
| Oppo VOOC / SuperVOOC, MediaTek PE | Not found. | | | |
| Samsung PD vendor VDM / SVID 0x04E8 | Not found. | | | |
| Apple SVID 0x05AC | Only as test fixtures: kws `firmware-core/examples/screens.rs:981` (BatteryCapabilities vid 0x05ac) and embedded `pd-protocol/tests/pd_spr.rs` (unstructured VDM 0x05ac_0000). Not a real capture. | | | |
| Google 0x18D1 | Not found in these repos. | | | |
| USB-IF SVID 0xFF00 (Discover Identity) | Discover Identity request header 0xff00_a801 (PD3) and 0xff00_8001 (PD2) (embedded `pd-protocol/src/pd_discovery.rs:37-38`). | | | Standard. |

### 2b. Notes

- AFC is the only protocol here that uses an electrical scheme on D+ and D- that a source might copy; SCP and FCP use a D- single-wire PHY (single-wire.md:3-4).
- No protocol in these repos documents how a source "advertises" it beyond the line levels above (INFERRED: detection is by sink probing, not source advertisement).

## 3. PD compliance pitfalls

### 3a. Timers

| Timer | Value used | Spec range given | Source |
|---|---|---|---|
| tTypeCSinkWaitCap | 465 ms | 310-620 ms | pdsink/pdsink:src/pd/timers.h:63 |
| tSenderResponse | 30 ms | 27-50 ms | pdsink:src/pd/timers.h:64 |
| tSinkRequest | 100 ms | (repeat) | pdsink:src/pd/timers.h:65 |
| tPPSRequest | 5000 ms | 10 s max (Table 6.68 in rev 3.2 v1.1; omitted from v1.2) | pdsink:src/pd/timers.h:66-67 |
| tPSTransition SPR | 500 ms | 450-550 ms | pdsink:src/pd/timers.h:69 |
| tPSTransition EPR | 925 ms | 830-1020 ms | pdsink:src/pd/timers.h:70 |
| tSinkEPRKeepAlive | 375 ms | 250-500 ms | pdsink:src/pd/timers.h:71 |
| tEnterEPR | 500 ms | 450-550 ms | pdsink:src/pd/timers.h:72 |
| tBISTContMode | 45 ms | 30-60 ms | pdsink:src/pd/timers.h (near :73) |
| tHardResetComplete | 5 ms | 4-5 ms | pdsink:src/pd/timers.h |
| tCCDebounce / tPDDebounce | 100 / 10 ms | 100-200 / 10-20 ms | pdsink:src/pd/timers.h |
| tActiveCcPollingDebounce | 20 ms | custom | pdsink:src/pd/timers.h |
| GoodCRC wait | 120 ms | not stated | Ralim/usb-pd:src/policy_engine_states.cpp:575 |
| Sender response (Ralim) | PD_T_SENDER_RESPONSE macro | n/a | Ralim/usb-pd:src/policy_engine_states.cpp:151, :186, :404 |
| PS transition (Ralim) | PD_T_PS_TRANSITION macro | n/a | Ralim/usb-pd:src/policy_engine_states.cpp:168 |
| VDM response (Discover Identity) | 50 ms max of tVDMSenderResponse | Table 7.9 | embedded `docs/policy.md:24-26` |
| PPS re-request (manuelbl) | 8 s | "at least every 10 s" | manuelbl/usb-pd-arduino:src/PDSink.cpp:182-185 |
| PPS retry after failed send (manuelbl) | 100 ms | n/a | manuelbl/usb-pd-arduino:src/PDSink.cpp:198 |
| PPS re-request (zy12pdn) | 8000 ms | n/a | manuelbl/zy12pdn-oss:src/pd_sink.cpp:216; check at :61-62 |
| PPS periodic (Ralim) | Notifications::PPS_REQUEST | n/a | Ralim/usb-pd:src/policy_engine_states.cpp:223 |
| EPR keepalive (Ralim) | EPR_KEEPALIVE event | n/a | Ralim/usb-pd:src/policy_engine_states.cpp:244, :616, :625 |
| Passthrough QC timing | 610 ms | 60 ms filter + 550 ms | kws README:1531-1533 |

### 3b. Retry, message ID, reset

| Pitfall | Detail | Source |
|---|---|---|
| Retry count depends on revision | `max_retries()` returns nRetryCount for rev > 2.0, else nRetryCount_REV20. | pdsink:src/pd/port.h:92; PRL check at pdsink:src/pd/prl.cpp:966 |
| PRL Tx retry | Retry counter is checked per transmission; GoodCRC retry handled by TCPC. | pdsink:src/pd/prl.cpp:959-966 |
| Message ID reset | TX ID reset to 0 on soft reset (pdsink:prl.cpp:826) and hard reset (pdsink:prl.cpp:1277, :1477). | pdsink |
| Soft reset | Uses MessageID 0; Accept consumes TX ID 0; renegotiation starts at ID 1. Unacked reset or no Accept requires mandatory Hard Reset. | embedded `docs/PD-SOFT-RESET.md:8-15` |
| Hard reset triggers (mandatory) | SenderResponse timeout on Request; PSTransition timeout or unexpected message; failed Soft_Reset (no Accept or GoodCRC); protocol error during recovery. Optional SinkWaitCap reset blocked. | embedded `docs/PD-HARD-RESET.md:5-10` |
| Hard reset recovery | Caller owns PE_SNK_Transition_to_default, discharge and VBUS sequencing. Driver must not silently retry. | embedded `docs/PD-HARD-RESET.md:17-22` |
| Revision | Negotiated revision survives Soft_Reset; Hard Reset restarts detection. | embedded `docs/usbpd-review.md:31` |
| VDM timing | Discover Identity timeout after ACK is not proof of "unsupported". No automatic retry or role change. | embedded `docs/policy.md:32-34` |
| Structured VDM in Ready | NAK at received version or SVDM 2.1 if higher. Unstructured VDMs: Not_Supported. | embedded `docs/policy.md:37-42` |
| Deferred wakeup bug | `request_wakeup()` re-called instead of waking the port. | pdsink/pdsink issue #2 (https://github.com/pdsink/pdsink/issues/2) |
| PPS 7th-PDO bug | Disabled PPS timer causes hard reset (see section 1). | https://github.com/Ralim/usb-pd/issues/17 |
| 32-bit timestamp wrap | Wraps at 49.7 days in IronOS timestamp path. | https://github.com/Ralim/usb-pd/issues/14 |
| Interrupt latency | Must respond to IRQ or poll under 10 ms. | Ralim/usb-pd:README.md (Design requirements) |
| Cable query window | Charger asks the cable question only in first moments after attach; gives up after ~20 unanswered tries. | kws README:1600-1612 (section "Full charger offer and 5 A without a cable") |
| Cable reading needs PD 3.0 | "The cable can only be asked under a PD 3.0 contract." `NEEDS A PD 3.0 PORT`. | kws README:1101-1188 (Cable section) |
| VCONN pin detection | Pin above 2.1 V is VCONN; charger talks on the other CC. | kws README:1608-1612 |
| Release Rd | Releasing Rd instead of hard reset: charger keeps VBUS, next boot gets no Source_Capabilities (ERR:0028). | kws README:1593-1598 |
| 5 A cable test non-standard | Meter answers SOP' itself; only a certified e-marker should. | kws README:1535-1650 |
| 5 A test broken on v0.12.0 | Open. Second hold does not restart the test. | https://github.com/kralonur/kws-x1-standalone-rs/issues/25 |
| Passthrough stops | Stops listening after Hard Reset, RX overflow or malformed frame. | kws README:52-56 |
| Sniff stop reasons | CC?, GUARD, VERIFY, DRIFT, FLUSH ST, BUS, HARD RESET, RX OVERFLOW, BAD FRAME, TX FLAG, PHY FAULT, HALTED. | kws README:1330-1360 |

### 3c. Cable e-marker and VCONN for >3 A and EPR

| Rule | Detail | Source |
|---|---|---|
| 5 A requires e-marker | Charger caps all objects at 3 A without a 5 A Passive Cable VDO. | kws README:1555-1565 |
| Passive cable current codes | 1 = 3 A, 2 = 5 A (`CURRENT_3A_CODE`, `CURRENT_5A_CODE`). | embedded `pd-protocol/src/pd_cable.rs:41-44` |
| EPR bit | Bit 17 in modern cable VDOs. Never inferred from voltage. | embedded `pd_cable.rs:51`, :105 |
| Reserved current encoding | v1.2 passive reserved encodings need a 3 A default. | embedded `pd_cable.rs:97` |
| SOP'' controller presence | Defined by layout; not inferred from reserved bits. | embedded `pd_cable.rs:119-134` |
| EPR cable requirement | Kws: "A real EPR cable is required; the fake marker is unchanged." | kws README:1647-1650 region (EPR objects paragraph) |
| EPR entry failure text | `EPR: cable not capable`, `EPR: VCONN failed`, `EPR: source unavailable`. | kws README:601-604 |
| Discovery needs VCONN | `NO E-MARKER OR NO VCONN`. | kws README:1121 |
| Capability 28 V+ only on request | EPR_Get_Source_Cap is sent by the sink only after EPR Mode Capable bit (kws README:1638-1641). Query alone does not enter EPR. | kws README:1636-1641 |

## 4. What a source can query, and phone answers

| Query | Sent by | Answer / reference | Notes |
|---|---|---|---|
| Get_Sink_Cap | Source to sink | pdsink answers at `src/pd/pe.cpp:628` (`PD_CTRL_MSGT::Get_Sink_Cap`) and `:631` (`Get_Sink_Cap_Extended`). EPR_Get_Sink_Cap at `pe.cpp:539`. | Real phone answer not captured in these repos. |
| Get_Source_Cap / Get_Source_Cap_EPR | Sink to source | kws README:1347 (passthrough message names: GET_SRC_CAP, GET_SRC_CEX). | No phone capture. |
| Get_Status | Source or sink | Header 0x0092 and second request [0x92, 2] (embedded `pd_discovery.rs:65-67`). Named as GET_STATUS in kws README:1347. | No phone response quoted. |
| Get_Manufacturer_Info | Source or sink | Header 0x9086 with body [2, 0x80, 0, 0] (embedded `pd_discovery.rs:61-63`). Cable MAKER row from it (kws README:1150). Charger PEAKS AND MAKER (kws README:1061). | No captured response. |
| Discover Identity SOP | Source | Header from VDM 0xff00_a801 (PD3) / 0xff00_8001 (PD2) (embedded `pd_discovery.rs:37-38`). Phone result shows outcomes BLOCKED, RESET, NO REPLY (kws README:~880-900). UFP VDO parsed only for known layouts (embedded `docs/policy.md:29-31`). | No phone VDO captured in repos. |
| Discover Identity SOP' | Source to cable | Cable reply: Passive Cable VDO for 5 A, 20 V, USB 2.0 (kws README:1617-1620, `EMARKER`). ID header Cable Plug flag set. | Only the meter's own emulation is shown. |
| Cable VDO fields | Read by cable page | VDO VERSION, PLUG CODE, EPR BIT, LATENCY CODE, SBU, VBUS THROUGH, SOP2 PRESENT, MAX TEMP C, SHUTDOWN TEMP C, U3 CLD POWER CODE (kws README:1150-1170, cable details). Summary: USB speed, RATING, LENGTH from signal delay, VID/PID, CERT ID, MODES (TBT 8087, DP FF01). | Decode logic at embedded `pd_cable.rs`. |
| EPR_Get_Source_Cap | Sink | Sent once charger reports EPR Mode Capable (kws README:1636-1641). | |
| Charger battery info | Sink reads source | Phone/power bank battery capability (kws README:1085-1087, battery_capabilities; fixture VID 0x05AC at screens.rs:981). | Fixture, not capture. |

### Captures and logs quoted

- IronOS PD debug states: "reboots right after the state 6" (https://github.com/Ralim/IronOS/issues/2104, comment by thomasoteixeira). Working chargers show state 12; failing show state 25 (https://github.com/Ralim/IronOS/issues/2050).
- No raw PD frames or phone captures appear in any repo read.
- Samsung AFC: kws README:1447-1487 says detection tested on one charger only.

## 5. Open items

1. Samsung Galaxy S-series, Pixel and iPhone PPS/APDO behaviour: not found in any repo read.
2. Apple divider 2.4A/1.0A and charger ID: not found.
3. Oppo VOOC, MediaTek PE, QC4, Samsung PD VDM: not found.
4. pdsink docs/ (compliance notes) and IronOS PD source: not read or not present.
5. Real capture logs for phones: none in these repos.
