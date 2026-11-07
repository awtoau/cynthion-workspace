<!-- Haiku research report, 2026-11-07, read-only survey. Leads, not verified facts: INFERRED marks are the agent's. Index: ../README.md -->


# USB-PD library survey (read-only, 2026-11-07)

## 0. Method, gaps, disclosures

- No `git clone` (the task asked for one into the scratchpad, but read-only mode forbids creating files). All reads were `gh api` contents/trees.
- GitHub code search returned HTTP 403 (rate limit) on every attempt, so the quirk-word search covers only files I actually fetched, not whole repos. Coverage gaps are listed in section 7.
- Citations are `repo:path:line` as read. "INFERRED" marks conclusions not directly stated in code or spec text.

---

## 1. elagil/usbpd: public API and coverage

### 1.1 Crates and toolchain
- `usbpd` 3.1.0, `usbpd-traits` 2.0.0. Default feature `epr`; optional `defmt`, `log`, `serde` (`elagil/usbpd:usbpd/Cargo.toml:31-36`).
- Deps: `usbpd-traits`, `proc-bitfield`, `byteorder` (no default features), `heapless`, `uom` (uom-ng 0.39 with si,u32), `embassy-futures` 0.1.1, `thiserror` 2 (no default features). `embassy-time` is NOT a dependency (`usbpd/Cargo.toml:14-25`).
- Workspace: edition 2024, rust-version 1.88, resolver 3 (`elagil/usbpd:Cargo.toml:2,8-9`).
- Both crates are `no_std` unless test (`usbpd-traits/src/lib.rs:4`, `usbpd/src/lib.rs:23`).

### 1.2 Traits (required and default methods)

| Trait (file:line) | Required (no default body) | Defaults / notes |
|---|---|---|
| `usbpd_traits::Driver` (`usbpd-traits/src/lib.rs:31-54`) | `wait_for_vbus(&mut self)` :44; `receive(&mut self, &mut [u8]) -> Result<usize, DriverRxError>` :47; `transmit(&mut self, &[u8]) -> Result<(), DriverTxError>` :50; `transmit_hard_reset(&mut self) -> Result<(), DriverTxError>` :53 | `const HAS_AUTO_GOOD_CRC: bool = false` :34 (PHY sends GoodCRC; PRL skips its own, `usbpd/src/protocol_layer/mod.rs:464`). `const HAS_AUTO_RETRY: bool = false` :41 (PHY retries; PRL skips loop, `protocol_layer/mod.rs:375`). |
| `DriverRxError` / `DriverTxError` (`usbpd-traits/src/lib.rs:11-28`) | n/a | RX: `Discarded`, `HardReset`. TX: `Discarded` (concurrent RX/noise), `HardReset`. |
| `usbpd::timers::Timer` (`usbpd/src/timers/mod.rs:4-6`) | `fn after_millis(milliseconds: u64) -> impl Future<Output=()>` (associated fn, no `self`) | none |
| `DevicePolicyManager` (source) (`usbpd/src/source/device_policy_manager.rs:82-141`) | none | `source_capabilities()` :86 (default 5V @3A); `evaluate_request()` :93 (default **Reject**); `transition_power()` :100; `hard_reset()` :107; `evaluate_vconn_swap_request()` :112 (false); `drive_vconn()` :117; `get_event()` :131 (default never resolves; must be cancel-safe); `inform(Info)` :138 |
| `DrpDevicePolicyManager` (source) (`source/device_policy_manager.rs:146-193`) | none | `sink_capabilities()` :150; `evaluate_swap_request(SwapType)` :155; `fr_swap_signaled()` :166; `discharge_vbus()` :171; `disable()` :178; `cc_sink()` :185; `swap_data_role()` :190 |
| `SourceDpm` (`source/device_policy_manager.rs:72,77`) | supertrait bound: `DevicePolicyManager + DrpDevicePolicyManager` (+ `EprDevicePolicyManager` with `epr`) | empty `impl SourceDpm for X {}` compiles |
| `DevicePolicyManager` (sink) (`usbpd/src/sink/device_policy_manager.rs:86-172`) | none | `request(&SourceCapabilities)` :99 (default 5V, Highest current); `transition_power(&PowerSource)` :116; `hard_reset()` :129; `sink_capabilities()` :141 (5V@100mA); `evaluate_vconn_swap_request` :147; `drive_vconn` :152; `get_event(&SourceCapabilities)` :166 (default never resolves); `inform(&SourceCapabilities, Info)` :88 |
| `DrpDevicePolicyManager` (sink) (`sink/device_policy_manager.rs:177-217`) | none | `evaluate_swap_request` :179; `set_fr_swap_detect(_enable)` :188 (no `self`); `disable` :195; `cc_source` :202; `source_on` :209; `swap_data_role` :214 |
| `SinkDpm` (`sink/device_policy_manager.rs:76,81`) | supertrait bound as above | `examples/embassy-nucleo-g474re-sink/src/power.rs:173-178` uses `impl SinkDpm for Device {}` |
| `EprDevicePolicyManager` (sink) (`sink/epr_device_policy_manager.rs:12-26`) | none | `epr_mode_entry_failed(reason)` :25 |
| `EprDevicePolicyManager` (source) (`source/epr_device_policy_manager.rs:10-27`) | none | `epr_capable()` :14 (false); `epr_cable_good()` :19 (false); `epr_source_capabilities()` :24 (vSafe5V, 50) |

### 1.3 Entry points

| Item | Location |
|---|---|
| `Sink<DRIVER, TIMER, DPM>::new(driver, dpm)` | `usbpd/src/sink/policy_engine/mod.rs:198` |
| `Sink::new_dual_role(driver, dpm)` | `sink/policy_engine/mod.rs:215` |
| `Sink::re_attach(driver)` / `deconstruct()` | `sink/policy_engine/mod.rs:232` / `:237` |
| `Sink::run(&mut self) -> Result<RunResult, Error>` (loops until exit; only returns on unrecoverable error or swap) | `sink/policy_engine/mod.rs:372` |
| `Source<DRIVER, TIMER, DPM>::new(driver, dpm, role_swap: bool)` | `usbpd/src/source/policy_engine/mod.rs:217` |
| `Source::new_dual_role(driver, dpm, role_swapped: bool)` | `source/policy_engine/mod.rs:236` |
| `Source::re_attach` / `deconstruct` / `run` | `source/policy_engine/mod.rs:264` / `:259` / `:371` |
| `sink::Error` / `source::Error` = `{PortPartnerUnresponsive, ReconnectionRequired, Protocol(ProtocolError)}` | `sink/policy_engine/mod.rs:175-182`; `source/policy_engine/mod.rs:195-202` |
| `RunResult { SwapToSource, SwapToSink }` | `usbpd/src/lib.rs:197-203` |
| Default header template: Source uses `SpecificationRevision::R3_X` | `source/policy_engine/mod.rs:~206` (see `Header::new_template`) |

DPM enums:
- Source `Event`: `None`, `UpdatedSourceCapabilities`, `RequestSinkCapabilities`, `RequestSourceCapabilities`, `RequestVconnSwap`, `RequestEprSourceCapabilities` (epr), `ExitEprMode` (epr), `RequestDataRoleSwap`, `RequestPowerRoleSwap` (`source/device_policy_manager.rs:14-35`).
- Source `Info`: `NotSupportedReceived`, `SprSinkCapabilities`, `EprSinkCapabilities` (epr), `RemoteSourceCapabilities` (:39-49). `CapabilityResponse`: `Reject`, `Wait`, `Accept` (:54-61).
- Sink `Event`: `None`, `RequestSprSourceCapabilities`, `RequestEprSourceCapabilities` (epr), `EnterEprMode(Power)` (epr), `ExitEprMode` (epr), `RequestPower(PowerSource)`, `RequestVconnSwap`, `RequestDataRoleSwap`, `RequestPowerRoleSwap`, `FastPowerRoleSwap` (`sink/device_policy_manager.rs:16-56`).
- Sink `Info` (`#[non_exhaustive]`, :61-70): `NotSupportedReceived`, `GetSourceCapTimeout`, `None`.

### 1.4 Coverage matrix (what the policy engines actually send, parse and answer)

Legend: PE = policy engine, PRL = protocol layer (`usbpd/src/protocol_layer/mod.rs`). Send sites use the file names `sink/policy_engine/mod.rs` (S) and `source/policy_engine/mod.rs` (R).

**Control messages.** Enum: `protocol_layer/message/header.rs:186-241`.

| Message | Send (site) | Receive (site) | Auto-reply on RX | Status |
|---|---|---|---|---|
| GoodCRC | PRL software ack, skipped if `HAS_AUTO_GOOD_CRC` (`protocol_layer/mod.rs:464-470`); revision copied from received msg (`:287-300`) | consumed in `wait_for_good_crc` (`:243-285`) | yes (PRL) | covered |
| GotoMin | none | none | falls to NotSupported (S `:627`, R `:1344`) | **not covered** |
| Accept | R `:476,791,891,952,1033,1127`; S `:706,920,990,1176` | S `:445` (wait for Accept/Wait/Reject); R `:1259`-area SoftReset path | n/a | covered |
| Reject | R `:934,1014,1144`; CapabilityResponse::Reject `:593`; S `:970,1065,1189` | S `:446,461-464` | n/a | covered |
| Wait | R `:594` (CapabilityResponse::Wait); S receive `:446,465` | S `:446,465`; R `:1005` | n/a | covered; README:26 says "Wait handling incomplete"; FIXME `sink/policy_engine/mod.rs:1157` |
| Ping | none | none | **NotSupported** (fallback R `:1344`, S `:627`) | not covered (answers NotSupported) |
| PS_RDY | R `:487,969,1053,1189`; S `:1019,1122,1222` | S `:476` (wait for PsRdy) | n/a | covered |
| GetSourceCap | S `:799` (`GetSourceCap` state) | R `:1261` → SendCapabilities (SPR) / GiveSourceCap (EPR) | n/a | covered |
| GetSinkCap | R `:657` (DRP GetSinkCap state) | S `:599` → GiveSinkCap(SPR); R `:1307` → DrpGiveSinkCap only if `dual_role`, else NotSupported | n/a | covered (DRP) |
| DR_Swap | R `:904`; S `:936` | S `:594`; R `:1272` (NotSupported if `!dual_role`) | NotSupported if not DRP | covered (DRP) |
| PR_Swap | R `:984`; S `:1026` | S `:590`; R `:1290` (NotSupported if `!dual_role`) | NotSupported if not DRP | covered (DRP) |
| VCONN_Swap | R `:1074`; S `:1135` | S `:600`; R `:1267` | n/a | covered |
| Soft_Reset | S `:689`; R `:774` | S `:259` (RxError::SoftReset → SoftReset state); R `:1259`; PRL `protocol_layer/mod.rs:623-630` | Accept (S `:706`, R `:791`) | covered |
| Not_Supported | S `:668`; R `:801` | R `:1315` (NotSupportedReceived) | n/a | covered |
| FR_Swap | S `:1079` | R `:1299` → FastPowerRoleSwap evaluate (DRP) else NotSupported | n/a | covered (DRP); sink-side FRS detect is TODO (S `:123`, `:492`) |
| Data_Reset / Data_Reset_Complete | none | none | NotSupported | **not covered** |
| Get_Source_Cap_Extended, Get_Status, Get_PPS_Status, Get_Country_Codes, Get_Sink_Cap_Extended, Get_Source_Info, Get_Revision | none | none | NotSupported | **not covered** |
| Reserved control type | n/a | PRL returns `UnsupportedMessage` (`protocol_layer/mod.rs:493-496`) | n/a | n/a |

**Data messages.** Enum: `protocol_layer/message/data/mod.rs:49-67` (`// TODO: Add missing types`, :48).

| Message | Send | Receive | Auto-reply on RX | Status |
|---|---|---|---|---|
| Source_Capabilities | R `:420` (`transmit_source_capabilities`), `:756` | S `:541-583` (EvaluateCapabilities) | Request is S's reply | covered |
| Request (SPR, PPS, AVS, battery, unknown) | S via PRL `protocol_layer/mod.rs:949` | R `:1220-1231` (NegotiateCapability; HardReset if mode mismatch) | n/a | covered. `Data::to_bytes` `unimplemented!()` for Battery/Unknown (`data/mod.rs:202`) |
| Sink_Capabilities | S `:774` (SPR), `:778` (EPR) | R `:673` (after GetSinkCap) | n/a | covered |
| BIST | none | none | NotSupported | **not covered** (BISTContMode timer defined, unused) |
| Battery_Status, Alert | none | none | NotSupported | **not covered** |
| Get_Country_Info, Enter_USB, Source_Info, Revision | none | none | NotSupported | **not covered** |
| EPR_Request (epr) | S via `sink/policy_engine/epr.rs`; PRL `:949` area | R `:1232` | n/a | covered (epr feature, default on) |
| EPR_Mode (epr) | S `sink/policy_engine/epr.rs:70-126`; R `:1244` | S `:586`; R `:1244` | n/a | covered (epr) |
| Vendor_Defined (VDM) | **none** (`Data::to_bytes` for VendorDefined = `unimplemented!()`, `data/mod.rs:208`) | parsed to `Data::VendorDefined` (`data/mod.rs:139-181`); no PE arm | NotSupported (fallback) | **parse-only** (see section 4) |

**Extended messages.** Enum: `protocol_layer/message/extended/mod.rs:24-37`.

| Message | Send | Receive | Auto-reply on RX | Status |
|---|---|---|---|---|
| ExtendedControl: EPR_Get_Source_Cap | S via `transmit_extended_control_message` (`protocol_layer/mod.rs:775`) | R `:1323` | n/a | covered (epr) |
| ExtendedControl: EPR_Get_Sink_Cap | R `:663` | S `:612`; R `:1328` | n/a | covered (epr) |
| ExtendedControl: EPR_KeepAlive | S `sink/policy_engine/epr.rs:196` | R `:1333` → EprKeepAlive state | n/a | covered (epr) |
| ExtendedControl: EPR_KeepAlive_Ack | R `:746` | S `sink/policy_engine/epr.rs:210` | source receiving Ack → NotSupported (R `:1337`) | covered (epr) |
| ExtendedControl: other values | none | `try_message_type()` Err → NotSupported (R `:1317-1337`, `:112`) | NotSupported | not covered |
| EPR_Source_Capabilities | TX `protocol_layer/epr.rs:~33-40` (single frame, see 3.9) | S `:566`; R `:682`-area | n/a | covered (epr) |
| EPR_Sink_Capabilities | S `:778` | R `:682` (after GetSinkCap) | n/a | covered (epr) |
| Source_Capabilities_Extended, Status, Get_Battery_Cap, Get_Battery_Status, Battery_Capabilities, Get_Manufacturer_Info, Manufacturer_Info, Security_Request/Response, Firmware_Update_Request/Response, PPS_Status, Country_Info, Country_Codes, Sink_Capabilities_Extended, Vendor_Defined_Extended | none | parsed as `Extended::Unknown` (`protocol_layer/message/mod.rs:153,243`) | NotSupported | **not covered** (`SourceCapabilitiesExtended::to_bytes` = `unimplemented!()`, `extended/mod.rs:57`) |

### 1.5 Not covered (what to add)
- Control: GotoMin, Ping (answered NotSupported instead of ignored), Data_Reset / Data_Reset_Complete, GetSourceCapExtended, GetStatus, GetPpsStatus, GetCountryCodes, GetSinkCapExtended, GetSourceInfo, GetRevision. These are not in any PE state.
- Data: BIST (incl. BIST Carrier/Test modes), Battery_Status, Alert, Get_Country_Info, Enter_USB, Source_Info, Revision.
- Extended: all status/battery/manufacturer/security/firmware/PPS-status/country/sink-caps-extended types.
- VDM: no TX, no PE handling (section 4).
- Chunked TX of anything >26 bytes (section 3.9). Chunked RX reassembly exists.
- Protocol: PD 3.x Hard Reset and Soft Reset are covered; Fast Role Swap is a stub for the sink (TODO at `sink/policy_engine/mod.rs:123`, `:492`).

---

## 2. Message types and replies per library (differences from elagil)

| Behaviour | elagil | Zephyr | fmckeogh usb-pd-rs (sink) | okhsunrog fusb302b | IvanLi fusb302 | Other |
|---|---|---|---|---|---|---|
| Ping received | NotSupported (`source/policy_engine/mod.rs:1344` fallback; `sink/policy_engine/mod.rs:627`) | **ignored** (`usbc_pe_snk_states.c:469-471`; `usbc_pe_src_states.c:454-455`) | ignored (`usb-pd/src/sink.rs:313` `Control(_) => None`) | n/a (PHY) | n/a | — |
| VDM received, PD 2.0 | NotSupported | **ignored** (`usbc_pe_snk_states.c:450-458`; `usbc_pe_src_states.c:429-436`) | UNHANDLED log, event emitted (`sink.rs:315-335`) | n/a | n/a | — |
| VDM received, PD 3.0 | NotSupported | NotSupported (same lines) | as above | n/a | n/a | — |
| Unknown control / reserved / not-supported-for-rev | NotSupported | NotSupported when rev > 2.0, else **Reject** (`usbc_pe_common.c:1120-1133`) | ignored (`sink.rs:313`) | n/a | n/a | — |
| Soft_Reset received | SoftReset state, Accept (`sink/policy_engine/mod.rs:259`; `source :1259`, `:791`) | PRL resets; Accept | **"UNHANDLED: Soft RESET request." warn, no reply** (`sink.rs:309-312`) | n/a | n/a | — |
| Unexpected Accept/Reject/Wait/PS_RDY in Ready | NotSupported-path / ignored per state (`sink/policy_engine/mod.rs:445-465` context-dependent) | **Soft_Reset** (`usbc_pe_snk_states.c:486-497`; spec 6.8.1 comment at :488-496) | ignored | n/a | n/a | — |
| Extended message received (not Chunked>26) | NotSupported (`source :1317-1337`) | NotSupported (`usbc_pe_common.c:549-560`) | not parsed (Unknown) | n/a | n/a | — |
| Chunked extended >26 bytes received | reassembled (`protocol_layer/mod.rs:481-611`) | **Chunk_Received state, wait ChunkingNotSupported (45 ms) then NotSupported; no reassembly** (`usbc_pe_common.c:553-554`, `:1153-1182`) | not parsed | n/a | n/a | — |
| GET_SINK_CAP | GiveSinkCap (`sink :599`) | GIVE_SINK_CAP (`snk :472-474`) | n/a (sink only) | n/a | n/a | — |
| DR_SWAP | DRP swap (`:590-594` sink; `:1272` source) | `PE_DRS_EVALUATE_SWAP` (`snk :475-476`; `src :~456`) | n/a | n/a | n/a | — |
| PR_SWAP, VCONN_SWAP, FR_SWAP | states present (see 1.4) | **no PE states found** in `subsys/usb/usb_c` file list (INFERRED: unimplemented); PD_CTRL names only (`usbc_prl.c:118-191`) | n/a | n/a | n/a | — |
| Sink Get_Source_Cap reply on timeout | DPM inform `GetSourceCapTimeout` (`sink :830`) | `pd_t_wait_to_resend` (`usbc_pe_snk_states.c:33`) | n/a | n/a | n/a | — |
| Source requests Wait + explicit contract | `Ready(true)` + SinkRequestTimer (`sink :465-470`) | Ready + `pd_t_wait_to_resend` 100 ms (`snk :295-313`) | n/a | n/a | n/a | — |
| Reject with no contract (sink) | WaitForCapabilities (`sink :461-462`) | WaitForCapabilities (`snk :320`) | n/a | n/a | n/a | — |
| Protocol error during transition | HardReset (`sink :321`) | — | — | — | — | — |
| GoodCRC consumed by PHY | n/a (software) | n/a (hardware GoodCRC; `usbc_prl.c:634-641`) | not sent in software (see 3.1) | RX-side GoodCRC **dropped** before delivery (`src/lib.rs:545-552`) | `receive()` does **not** filter GoodCRC (`driver.rs:408-426`) | — |

Other message-level differences:
- fmckeogh sink sends VDMs (REQ/ACK DiscoverIdentity, REQ DiscoverSVIDS) with PD SID `0xff00` (`usb-pd/src/sink.rs:194-268`). It also sends fixed and PPS requests (`sink.rs:145-180`, `:341-378`). It sends no Accept or Reject; it only consumes Accept/Reject/PsRdy events (`sink.rs:296-308`).
- fmckeogh `Message::Unknown => unimplemented!()` (`sink.rs:337`). Any data type other than SourceCap/Request/VDM is parsed to `Unknown` (`messages/mod.rs:138-140`), so a received Alert or BIST panics the sink.
- fmckeogh has no source role: `usb-pd/src/source.rs` is empty (1 line, verified).
- Zephyr PD 2.0 vs 3.0 revision split is applied to Not_Supported vs Reject as shown above.
- okhsunrog, IvanLi: PHY only. Neither implements message semantics. okhsunrog only drops GoodCRC and keeps the event state (section 3.2).

---

## 3. Quirks and workarounds

### 3.1 FUSB302B GoodCRC spec-revision limit
- Spec: `elagil/usbpd:USB_PD_R3_2 V1.1 2024-10.md:4218` (PD 3.2 section 6.2.1.1.5): "The sender of a GoodCRC Message **Shall** set the Specification Revision field to 01b (Revision 2.0) when responding to a Message that contains 01b ... **May** set the Specification Revision field to 01b or 10b when responding to a Message that contains 10b (Revision 3.x)."
- FUSB302B datasheet: `okhsunrog/fusb302b:FUSB302B-D.md:882-886` — SPECREV `00`=Rev 1.0, `01`=Rev 2.0, **`10` and `11` = "Do Not Use"**.
- okhsunrog keeps the reset-default Rev 2.0 GoodCRC and comments on the rule: `src/lib.rs:339-341`; design note `docs/design.md:59-62`; reset value `device.ddsl:84` (`0b00100000`, SPECREV field at `:96`).
- IvanLi: default `PdRevision::Rev20` (`src/types.rs:241`). `Rev30` (SPECREV=10) is an explicit BMPX opt-in only (`docs/adr/0006-explicit-bmpx-pd3-goodcrc.md`; `docs/specs/fusb302-phy/SPEC.md:71-73`).
- fmckeogh: `Switches1 .with_specrev(R2_0)` (`fusb302b/src/lib.rs:448`). The field is a single bit at 5 (`fusb302b/src/registers.rs:251`), so Rev 3.x cannot be encoded.
- elagil: PRL copies the received message's revision into its GoodCRC (`protocol_layer/mod.rs:292-300`). The doc comment says "Shall match", which is stronger than the spec's "may". The behaviour is still allowed.

### 3.2 FIFO flush ordering and transmit/receive sequencing (okhsunrog, the most detailed)
- Interrupt snapshot: one 7-byte burst STATUS0A..INTERRUPT (`src/lib.rs:36-37, 373-389`); read-to-clear events kept in `Events` across cancellation (`src/lib.rs:373-374`; `docs/design.md:8-24`).
- `transmit()` order: read events; if HARD_RST pending, `reset_pd_and_fifos` and return HardReset (`src/lib.rs:468-476`). Clear stale `tx_sent`, `retry_fail`, `collision` (`:477-480`). **TX_FLUSH** (`:481-485`). FIFO write of Sync-1 x3, Sync-2, PACKSYM, msg, JAM_CRC, EOP, TXOFF, **TXON in the same I2C write** (`:486-490`; `docs/design.md:28-30`). Completion deadline 15 ms (`:493`).
- `receive()`: one FIFO prefix read (token+header+CRC = 7 bytes, `src/lib.rs:568-580`), second read for data tail, stopping before the next SOP (`:612-623`). RX_FLUSH on bad SOP (`:580-586`) or oversize (`:596-603`). GoodCRC filtered (`:545-552`).
- Init order: reset; power; masks; CONTROL3 (AUTO_RETRY, N_RETRIES=2, auto soft/hard reset **off**) (`:281-290`); RX_FLUSH (`:292-296`); CC detect; PD_RESET (`:302-305`); AUTO_CRC enabled last (`:309-312`). Comment: "Never reset/flush RX after enabling automatic acknowledgements" (`:307-308`; `docs/design.md:55-57`).
- IvanLi: `init()` = SW reset then flush both FIFOs (`driver.rs:93-96`). `transmit()` does **not** flush TX; it returns `TransmitFifoBusy` if TX FIFO is non-empty (`driver.rs:367-368, 474-480`). `receive()` does not flush on bad SOP and returns the raw token error (`driver.rs:419-426`). TX/RX-FIFO state after a hard reset is the caller's job (INFERRED; `transmit_hard_reset` does no flush, `:394-405`).
- fmckeogh: `send_message` does not flush TX (`fusb302b/src/lib.rs:207-244`). RX flush on non-SOP only (`:474-478`, with `warn!`). When non-SOP, `header` stays 0 and `check_for_msg` still runs on it (INFERRED: parses a Reserved message; see 3.4).

### 3.3 Reset cleanup
- okhsunrog `reset_pd_and_fifos()` clears TX and RX FIFO, PD_RESET, and clears all `Events` (`src/lib.rs:391-406`). `transmit_hard_reset()` calls it first (`:438-441`). Hard reset from TX/RX also calls it (`:472, :499, :532`).
- elagil PRL: `reset()` clears counters and chunk state (`protocol_layer/mod.rs:195-199`); `hard_reset()` resets counters and retry counter, clears chunk RX, and sends Hard Reset signaling (`:736-756`); parked RX messages discarded on layer reset (`:155`).
- IvanLi: `pd_reset()` (`driver.rs:89-91`) only; caller must call it.
- fmckeogh: any HARD_RST interrupt does a full `init()` and enters RetryWait 500 ms (`fusb302b/src/lib.rs:314-318, 375-382`). Measured wait 10 ms (`:289`), Ready timeout 300 ms (`:457`). `init()` busy-waits 1,000,000 fence iterations (`:87-89`; blocking).
- Zephyr: TC/PRL reset sets all msg_id counters to 0 and clears RX msg IDs (`usbc_prl.c:696-705`). Soft-reset-only layer reset clears one SOP's counter (`:824-838`).

### 3.4 Other FUSB302B-specific issues
- **SEND_HARD_RESET**: datasheet `FUSB302B-D.md:1024-1025` says writing 1 sends a hard reset ("highest priority"). fmckeogh writes `SEND_HARD_RESET=1` in `init()` (`fusb302b/src/lib.rs:145`) and in `establish_usb_pd_wait()` (`:404`). INFERRED: this transmits a Hard Reset on attach and each time it enters the PD-wait state.
- **N_RETRIES**: encoding `FUSB302B-D.md:1030-1033`: `10`=2 retries, `11`=3 retries (4 sends total). PD nRetryCount is 2 (`elagil/usbpd:usbpd/src/counters.rs:47`). fmckeogh uses `with_n_retries(3)` (`fusb302b/src/lib.rs:149`, `:408`), exceeding nRetryCount. okhsunrog uses `TwoRetries` (`src/lib.rs:286`). IvanLi: `Two=0b100` (`src/types.rs:185-212`).
- **AUTO_SOFTRESET / AUTO_HARDRESET**: fmckeogh sets both on (`fusb302b/src/lib.rs:146-147`, `:405-406`). okhsunrog sets both off and leaves it to the PE (`src/lib.rs:287-288`). IvanLi defaults off (`src/types.rs:246-247`).
- **AUTO_CRC bit**: SWITCHES1 bit 2 is AUTO_CRC (`okhsunrog/fusb302b:device.ddsl:87-88` `field auto_crc 2`; `IvanLi-CN/fusb302-rs:src/registers.rs:160`). fmckeogh names the same bit `auto_src` (`fusb302b/src/registers.rs:257`) and sets it in `establish_usb_pd_wait()` (`fusb302b/src/lib.rs:447`). So fmckeogh does enable GoodCRC, under a misleading name.
- **Collisions**: okhsunrog maps I_COLLISION to `Discarded` on TX (`src/lib.rs:387, 511-513`). No collision/Rp logic in elagil, IvanLi, or fmckeogh (grep of fetched files). Zephyr does collision Rp handling: `SINK_TX_NG` on Src_Tx (`usbc_prl.c:916-926`), `SINK_TX_OK` on exit (`:745-760`), and discards pending TX when an RX SOP message arrives, except Ping (`usbc_prl.c:1372-1393`).
- **Stale TX completion**: okhsunrog clears TXSENT/RETRYFAIL/COLLISION before each TX (`src/lib.rs:477-480`; design note "Completion from a previous cancelled/failed transmission is stale").
- **Pending message overwrite**: fmckeogh `panic!("pending message already set")` if a second message arrives before `receive_message` takes the first (`fusb302b/src/lib.rs:368-370`).
- **Interrupt pin**: fmckeogh says INT is shared with SWDIO, so it polls CC manually (`fusb302b/src/lib.rs:261-265`).

### 3.5 Missing VBUS sense
- elagil requires `Driver::wait_for_vbus` (`usbpd-traits/src/lib.rs:43-44`). There is no default and no built-in detect.
- okhsunrog built-in: polls STATUS0.VBUSOK until ~4.0 V, draining events between polls (`src/lib.rs:419-436`). External: `VbusDetect` trait (`:74-80`). `()` bypass returns immediately, with no measurement and no detach detection (`:83-85`; `docs/design.md:77-79`; `README.md:88-105`).
- IvanLi: `configure_vbus_measurement` plus `read_vbus_comparator` (MDAC, 420 mV steps; `driver.rs:205-231`; `README.md:29-31`). The caller must poll.
- fmckeogh: no VBUS check (`fusb302b/src/lib.rs:251-306` measures CC only).
- elagil TCPP03 example: `VBUS_OK` only meaningful in Hibernate/LowPower, not Normal (`examples/usbpd-tcpp03-m20/src/lib.rs:11-12, 98-104`). VBUS is measured by ADC instead.
- Zephyr: VBUS through a DT-described device with `usbc_vbus_check_level` (`usbc_tc_snk_states.c:200`, `usbc_stack.c:84`).

### 3.6 Charger, phone and board-specific behaviour
- Searched fetched files for `workaround`, `quirk`, `hack`, `broken`, `Apple`, `Samsung`, `some chargers`, `charger`. **No device-specific workaround found** in the fetched elagil, okhsunrog, IvanLi, fmckeogh or Zephyr usb_c files. Repo-wide code search was rate-limited and not run.
- Board facts only: Pinecil leaves FUSB302 VBUS pin disconnected (`okhsunrog/fusb302b:README.md:29-33`). ZY12PDN board (`fmckeogh/usb-pd-rs:README.md:11`).
- Zephyr's comment on PD 2.0 VDMs ("PD2.0 ignores and PD3.0 reply with not supported", `usbc_pe_snk_states.c:452-455`) is the only "compat" rule found.
- elagil `Wait` handling incomplete (`usbpd/README.md:26`); FIXME for VCONN Wait (`sink/policy_engine/mod.rs:1157`). CapsCounter increment in Discovery is "not to spec" (`source/policy_engine/mod.rs:403-405`).
- IvanLi: a toggle result is not an attach until TOGDONE is consumed (`driver.rs:297-320`).

### 3.7 Other defects or spec deviations found
- elagil docs say chunked messages are unsupported (`protocol_layer/message/extended/mod.rs:70`), but RX reassembly exists (`protocol_layer/mod.rs:481-611`).
- elagil `VdmCommandType::from(u8)` and `VdmCommand::from(u8)` `panic!` on unknown values (`protocol_layer/message/data/vendor_defined.rs:66, 112`). Only reachable if the accessor is called on wire data (INFERRED).
- elagil `Message::to_bytes` for extended messages always emits a chunked header with chunk 0 and a single payload (see 3.9).
- fmckeogh `SpecificationRevision::from(u8)` `unimplemented!()` for `0b11` (`usb-pd/src/header.rs:54`).
- fmckeogh `TimerTypes` `todo!()` for SourcePPSComm, SinkTx, SwapSourceStart, VCONNDischarge, VCONNOn, VDM* (`usb-pd/src/timers/mod.rs:63-70`). Timers are unusable for those.
- fmckeogh `Request::RequestPPS` asserts index range (`sink.rs:159`).
- fmckeogh `send_message` panics unless state is `Connected` (`fusb302b/src/lib.rs:192-194`).
- fmckeogh error types are empty enums with `FIXME: Implement error handling` (`fusb302b/src/lib.rs:67-75`).

### 3.8 Hard reset / protocol-error paths (elagil, for reference)
- Sink: RX or TX HardReset → HardReset state; SoftReset → SoftReset; TransmitRetriesExceeded in SoftReset/SendSoftReset → HardReset (`sink/policy_engine/mod.rs:254-264`). Protocol error during TransitionSink → HardReset (`:321`). Sink caps timeout → HardReset (`:308-310`).
- Source: PSTransition/hard-reset counter limit via `HardResetCounter` max 3 (`counters.rs:45`).

### 3.9 Chunking and EPR cap size on the wire
- Chunk RX: assembled per `ChunkedMessageAssembler` (`protocol_layer/message/extended/chunked.rs`, `MAX_EXTENDED_MSG_CHUNK_LEN=26` at `:17`).
- Chunk TX: `ChunkedMessageSender` is defined (`chunked.rs:274-355`) but **not referenced** in the PRL or PE files fetched (grep of `protocol_layer/mod.rs`, `sink/...`, `source/...`, `protocol_layer/epr.rs`).
- `Message::to_bytes` extended path writes one chunk-0 header with `data_size` = full payload, no split (`protocol_layer/message/mod.rs:110-117`). EPR_Source_Capabilities can carry up to 16 PDOs = 64 bytes (`protocol_layer/epr.rs` `heapless::Vec<_,16>`; `extended/mod.rs:31`). INFERRED: this exceeds the 26-byte chunk limit on the wire and will fail with a compliant port partner.
- Zephyr: chunked RX not assembled (3.7 above).

### 3.10 Other
- Zephyr: TX hardware retries are used and software retry is explicitly rejected for timing (`usbc_prl.c:634-641`: "software cannot handle the required timing ~ 1ms (tReceive + tRetry)").
- Zephyr: duplicate detection by msg_id per SOP (`usbc_prl.c:1378-1381`).
- Zephyr: Message ID modulo 8 (`usbc_prl.c:571-572`; `PD_MESSAGE_ID_COUNT 7` at `usbc_prl.h:23`).
- elagil: MessageId counter max 7 (`counters.rs:46`), Retry 2 (`:47`), Caps 50 (`:39`), HardReset 3 (`:45`, nHardResetCount 2).
- fmckeogh MessageId: `% 8` (`fusb302b/src/lib.rs:501`).

---

## 4. Vendor messages (SVIDs, VDMs)

| Library | What it does | Location |
|---|---|---|
| elagil | Parses VDM header and data objects; no TX; no SVID handling; VDM TX `unimplemented!()`. `// TODO: Add DevicePolicyManager callback` (`data/mod.rs:60-63`). VDM command enum: Discover Identity (0x1), Discover SVIDs (0x2), Discover Modes (0x3), Enter (0x4), Exit (0x5), Attention (0x6), DP Status (0x10), DP Config (0x11). 0x12-0x1F TODO (`vendor_defined.rs:74-83, 111`). `VdmHeaderRaw.standard_or_vid` at bits 16-31 (`vendor_defined.rs:181-183`). DisplayPort caps structure at `:574`. | `usbpd/src/protocol_layer/message/data/vendor_defined.rs:7-200, 574`; `data/mod.rs:139-181, 208` |
| elagil | No SVID values hard-coded. No DPM hook for VDMs. | — |
| fmckeogh | Structured header with **`standard_or_vid = 0xff00`** (PD SID) for DiscoverIdentity REQ, DiscoverSVIDS REQ, and DiscoverIdentity ACK (`usb-pd/src/sink.rs:205, 224-242, 239, 265`). ACK sends 5 VDOs: header, ID header, cert stat, product, UFP product type (`sink.rs:194-223`). Received VDMs (structured or unstructured) are logged "UNHANDLED" and emitted as `Event::VDMReceived` (`sink.rs:315-335`). No SVID-specific handling. | `usb-pd/src/sink.rs:194-268, 315-335`; `usb-pd/src/messages/mod.rs:36` ("TODO: Incomplete"), `:99-136` |
| Zephyr | VDM symbol names only (`usbc_prl.c:163`, `:191`). PE: ignored on PD 2.0, NotSupported on PD 3.0 (`usbc_pe_snk_states.c:450-458`; `usbc_pe_src_states.c:429-436`). No SVIDs. | as listed |
| okhsunrog fusb302b | none (PHY) | — |
| IvanLi fusb302 | none (PHY) | — |

Unstructured VDM: elagil parses `VdmHeaderUnstructured` (`vendor_defined.rs:286-297`); fmckeogh logs it only.

---

## 5. Timers (values, locations)

Units ms unless noted. "—" = not present in that library's read files.

| Timer | elagil `usbpd/src/timers/mod.rs` (value) | Zephyr (`include/zephyr/drivers/usb_c/usbc_pd.h` / `usbc_tc.h`) | fmckeogh `usb-pd/src/timers/mod.rs` | Note |
|---|---|---|---|---|
| tNoResponse | 5000 (:103) | 4500–5500 (:89,:96) | 5000 (:151) | |
| tPSHardReset | 30 (:104) | 25–35 (:104,:112) | 30 (:157) | |
| tSinkTx | 18 (:124) | 16–20 (:119,:126) | 18 (:169) | fmck `todo!()` in `From` (:64) |
| tTypeCSendSourceCap | 150 (:120) | 100–200 (:135,:144) | 150 (:175) | |
| tTypeCSinkWaitCap | 465 (:119) | 310–620 (:154,:162) | 465 (:176) | |
| tSenderResponse | 30 (:112) | 24–30, nom 27 (:213,:219,:225) | 30 (:166) | |
| tPSSourceOffSpr | 835 (:105) | — | 835 (:158) | |
| tPSSourceOffEpr | 1260 (:107) | — | 1260 (:159, unused) | epr |
| tPSSourceOnSpr | 435 (:108) | — | 435 (:160) | |
| tPSTransitionSpr | 500 (:109) | 450–550 (:231,:237,:243) | 500 (:161) | |
| tPSTransitionEpr | 925 (:111) | 830–1020 (:249,:255,:261) | 925 (:162, unused) | epr |
| tReceive (CRCReceive) | 1 (:98) | — | 1 (:163) | |
| tSinkRequest | 100 (:118) | min 100 (:267) | 100 (:168) | |
| tSinkEPREnter | 500 (:114) | — | 500 (:141) | epr |
| tSinkEPRKeepAlive | 375 (:116) | — | 375 (:150) | epr |
| tSourceEPRKeepAlive | 875 (:122) | — | 875 (:149) | epr |
| tSinkPPSPeriodic (tPPSRequest) | 5000 (:117) | — | 5000 (:152) | spec max 10 s |
| tSourcePPSComm (tPPSTimeout) | 13500 (:123) | — | 13000 (:153, unused) | |
| tSwapSourceStart | 20 (:125) | — | 20 (:173) | |
| tVCONNOn (tVCONNSourceTimeout) | 150 (:127) | — | 150 (:180) | |
| tVCONNDischarge | 200 (:126) | — | 200 (:177) | |
| tChunkingNotSupported | 45 (:95) | 40–50, nom 45 (:274,:281,:288) | — | |
| tChunkSenderRequest / Response | 27 (:96,:97) | — | 27 (:131,:132) | |
| tDiscoverIdentity | 45 (:101) | — | 45 (:136) | |
| tVDMModeEntry / Exit / Response | 45, 45, 27 (:128-130) | — | 45 (:187), 45 (:188), 27 (:186) | |
| tDataResetFail / FailUFP | 350, 500 (:99,:100) | — | 350, 500 (:134,:135) | |
| tBISTContMode | 45 (:94) | — | 45 (:124) | |
| tHardResetComplete | 5 (:102) | 4–5 (:201,:207) | 5 (:148) | |
| tTxTimeout (PRL) | — | 100 (:195) | — | Zephyr only |
| tSafe0V / tSafe5V | — | max 650 (:183) / 275 (:190) | — | |
| tRetry | — | — | 195 µs (:165, unused) | |
| tRetryWait (fmck FUSB302 ) | — | — | 500 ms (`fusb302b/src/lib.rs:381`) | driver-level |
| fmck Ready timeout | — | — | 300 ms (`fusb302b/src/lib.rs:457`) | driver-level |
| okhsunrog RESET_SETTLE | — | — | — | 10 ms (`src/lib.rs:30`) |
| okhsunrog CC_MEASUREMENT_SETTLE | — | — | — | 10 ms (`src/lib.rs:31`) |
| okhsunrog IRQ_POLL_INTERVAL | — | — | — | 1 ms (`src/lib.rs:32`) |
| okhsunrog TX_COMPLETION_TIMEOUT | — | — | — | 15 ms (`src/lib.rs:33`) |
| okhsunrog HARD_RESET_TIMEOUT | — | — | — | 5 ms (`src/lib.rs:34`) |
| okhsunrog RX_POLL_TIMEOUT | — | — | — | 20 ms (`src/lib.rs:35`) |
| IvanLi | no timers in driver | — | — | — |

Other timing facts:
- elagil timer usage in fetched PE/PRL files: referenced: CRCReceive, PSHardReset, PSSourceOff{Spr,Epr}, PSSourceOn{Spr}, PSTransition{Spr,Epr}, SenderResponse, SinkEPREnter, SinkEPRKeepAlive, SinkPPSPeriodic, SinkRequest, SinkWaitCap, SourceCapability, SourceEPRKeepAlive, SourcePPSComm, SwapSourceStart, VCONNOn. Not referenced in those files: BISTContMode, ChunkingNotSupported, ChunkSender*, DataResetFail*, DiscoverIdentity, HardResetComplete, NoResponse, SinkTx, VCONNDischarge, VDM*.
- Zephyr: `usbc_timer.c` uses k_timer with an atomic expired flag (`usbc_timer.c:31-36, 44-55`).
- Zephyr TC timers (values in `include/zephyr/drivers/usb_c/usbc_tc.h`): CC debounce 100–200 (:180,:186); PD debounce 10–20 (:193,:200); DRP 50–100 (:90,:96); DRP PW 15–70 (:103,:110); VBUS_ON max 275 (:50); VBUS_OFF max 650 (:57); VCONN_OFF max 35 (:77); RP value change 10–20 (:234,:241); try timeout 550–1100 (:154,:160); error recovery source min 240 (:227).

---

## 6. What an integrator must supply: elagil/usbpd + fusb302b on no-OS riscv32imac RTIC

### 6.1 Toolchain and target
- elagil: rust-version 1.88, edition 2024 (`elagil/usbpd:Cargo.toml:8-9`). fusb302b: edition 2024 with let-chains (`okhsunrog/fusb302b:src/lib.rs:424-425`), so Rust 1.88+ (INFERRED).
- riscv32imac has the A extension, so atomics exist (INFERRED). fusb302b CI checks `riscv32imc-unknown-none-elf` (`fusb302b:README.md:126-129`; `.github/workflows/ci.yml`). elagil CI builds only `thumbv7em-none-eabihf` and `thumbv8m.main-none-eabihf` (`elagil/usbpd:.github/ci/build.sh:3,87`; `rust-toolchain.toml:3`). **No repo in the fetched set builds on riscv32imac.**

### 6.2 Executor and tasks
- elagil has no executor. `Sink::run()` / `Source::run()` are plain async loops (`sink/policy_engine/mod.rs:372`; `source/policy_engine/mod.rs:371`). It uses `embassy_futures::select` (`usbpd/Cargo.toml:21`), so the executor is free. RTIC async task holding the `Sink` (`run` future) is the expected shape (INFERRED).
- `DevicePolicyManager::get_event()` is `select`ed against the receive and timer futures (`sink/policy_engine/mod.rs:~560-600`), so every DPM async method must be cancel-safe (documented at `sink/device_policy_manager.rs:156-165`; `source/device_policy_manager.rs:121-130`).

### 6.3 Timer
- Implement `usbpd::timers::Timer::after_millis` (`usbpd/src/timers/mod.rs:4-6`) on an RTIC monotonic (e.g., rtic-monotonics/SysTick/CLINT). Need ≤1 ms resolution for CRCReceive (1 ms, `timers/mod.rs:98`).
- fusb302b does NOT take a timer parameter. It calls `embassy_time::{Timer, Instant, with_deadline}` directly (`okhsunrog/fusb302b:src/lib.rs:26, 117, 240, 321, 448, 462`). Therefore it needs a global embassy-time time driver (embassy-time-driver implementation) and a critical-section implementation for riscv (INFERRED from `embassy-time 0.5.1`, `Cargo.toml` deps; dev-deps only enable `critical-section` std). An RTIC monotonic alone does not satisfy this (INFERRED). Options: an embassy-time-driver shim over the same hardware timer, or a fork of fusb302b that takes a timer trait.

### 6.4 Interrupts / wake-up
- fusb302b `InterruptPin` (`src/lib.rs:108-112`) is implemented for `GpioInterrupt<P>` where `P: embedded_hal_async::digital::Wait` (`:121-131`). INT_N active-low, needs an async wait for low, which means an EXTI interrupt with a waker. RTIC needs an async GPIO-wait shim (INFERRED).
- Without a pin, `()` polls every 1 ms (`src/lib.rs:114-119`). That generates I2C traffic and CPU wakeups (INFERRED cost).

### 6.5 I2C trait requirements
- fusb302b: `I2CBus: embedded_hal_async::i2c::I2c<Error = E> + 'static`, `E: core::fmt::Debug` (`src/lib.rs:163-166, 174-179, ...`; `src/interface.rs:29-40`). The bus must be `'static` (no borrowed bus). `embedded-hal-async = "1.0.0"` (`Cargo.toml`).
- Address `0x22` (`src/lib.rs:44`). Only one I2C address is hard-coded in the constructor path (`FUSB302B_I2C_ADDRESS`).
- A blocking bus cannot be used directly. Wrapping a blocking HAL would block the executor (INFERRED).
- IvanLi: `Fusb302<I2C>` with `embedded-hal` 1.0 (sync) or `embedded-hal-async` behind `async` feature (`docs/specs/fusb302-phy/SPEC.md:17-18, 60-61`; `README`). Owns the bus; `release()` returns it (`src/driver.rs:64-66`).
- fmckeogh: `embedded_hal_async::i2c::I2c` (`fusb302b/src/lib.rs:13`); `Registers::new(i2c)`.

### 6.6 VBUS and power
- Required: `Driver::wait_for_vbus` (`usbpd-traits/src/lib.rs:43-44`). With okhsunrog, pick the internal VBUSOK poll (`src/lib.rs:419-436`) only if the sense pin is wired; otherwise `init_with_vbus_and_interrupt` plus an external `VbusDetect`, or `()` bypass (`README.md:88-105`).
- elagil also needs `DevicePolicyManager::transition_power` / `hard_reset` to drive actual VBUS. Defaults are no-ops (`sink/device_policy_manager.rs:116,129`; `source/device_policy_manager.rs:100,107`), so a real power path must override them.

### 6.7 Blocking points
- fusb302b: no synchronous waits. All waits are async with deadlines: init 10 ms settle x2 (`src/lib.rs:240, 321`), TX 15 ms, RX 20 ms, hard reset 5 ms (`:33-35`). An indefinitely stalled I2C needs an external timeout (`docs/design.md:74-75`).
- fmckeogh: `init()` busy-waits 1,000,000 iterations (`fusb302b/src/lib.rs:87-89`). Blocking on RTIC; do not use.
- elagil: non-blocking. Stack use: `get_message_buffer()` returns `[u8; 272]` by value (`protocol_layer/mod.rs:37, 213-215`). `heapless::Vec<_,16>` for EPR caps. INFERRED stack use: several hundred bytes per TX path.

### 6.8 Interfaces to write
- `usbpd_traits::Driver` adapter for fusb302b: fusb302b already implements it (`src/lib.rs:409-561`). Set `HAS_AUTO_GOOD_CRC=true`, `HAS_AUTO_RETRY=true` (`:416-417`). Driver errors: Discarded/HardReset only.
- `usbpd::timers::Timer` adapter (6.3).
- `SinkDpm` impl: all methods default; must still `impl DrpDevicePolicyManager` and `impl EprDevicePolicyManager` (empty impls OK). Override `request()`, `transition_power()`, `hard_reset()`, `get_event()` for the real device.
- IvanLi: no `usbpd_traits::Driver` impl. Would need an adapter (`docs/specs/fusb302-phy/SPEC.md:85-86`: adapter crate "future").

### 6.9 Things that block a clean RTIC port
1. embassy-time global time driver required by fusb302b (section 6.3).
2. Async GPIO wait for INT_N required for the interrupt path (6.4).
3. No riscv32imac build in any fetched repo; ci coverage stops at thumbv7em/thumbv8m (elagil) and riscv32imc (fusb302b).
4. elagil `Timer` must be implemented by the integrator; no default.

---

## 7. Gaps and unverified items
- Not read in full: elagil `protocol_layer/message/extended/chunked.rs` body, `sink/policy_engine/epr.rs` and `source/policy_engine/epr.rs` (only grep sites), most of `protocol_layer/mod.rs` (targeted ranges), elagil examples other than G474 sink and TCPP03, elagil tests. Zephyr: `usbc_tc_*` (only VBUS and timer grep), `usbc_stack.c` (grep only), `include/zephyr/drivers/usb_c/usbc_tc.h` (grep only), Zephyr TCPC driver layer (not fetched).
- Repo-wide keyword search: not performed (rate-limited). Quirk findings are limited to fetched files.
- fmckeogh `source.rs` is one line (verified). Its `Driver::send_message` is never exercised by a source role.
- IvanLi: `tests/`, `docs/specs/fusb302-phy/SPEC.md` (read), CONTEXT.md (not read).
- okhsunrog: `tests/driver.rs` not read.
- The FUSB302B register map was verified only for SPECREV, SEND_HARD_RESET, N_RETRIES, and the Control3 layout (`FUSB302B-D.md:786, 882-886, 1020-1036`).
