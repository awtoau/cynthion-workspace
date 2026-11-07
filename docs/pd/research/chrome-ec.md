<!-- Haiku research report, 2026-11-07, read-only survey. Leads, not verified facts: INFERRED marks are the agent's. Index: ../README.md -->


# ChromiumOS EC PD catalogue (coreboot/chrome-ec, default branch as fetched 2026-11-07)

Scope: paths below are repo-relative to coreboot/chrome-ec. Nothing was cloned or written. Files were read via `gh api ... -H "Accept: application/vnd.github.raw"` piped through grep/awk. No commit is pinned, so line numbers can drift. Not read in depth: common/usb_pd_host_cmd.c (150 lines), most of common/usbc/usb_pd_dpm.c, common/usb_pd_pdo.c, common/usb_pd_discovery.c, zephyr/drivers/usbc/* (PDC drivers), board-specific usb_pd_policy.c files (only board/host/board.h:76-77 checked). INFERRED marks items not directly confirmed.

## 1. MESSAGES

Type tables (include/usb_pd.h):
- Control (PD_CTRL_*), usb_pd.h:1335-1361: GOOD_CRC 1, GOTO_MIN 2, ACCEPT 3, REJECT 4, PING 5, PS_RDY 6, GET_SOURCE_CAP 7, GET_SINK_CAP 8, DR_SWAP 9, PR_SWAP 10, VCONN_SWAP 11, WAIT 12, SOFT_RESET 13, DATA_RESET 14, DATA_RESET_COMPLETE 15, NOT_SUPPORTED 16, GET_SOURCE_CAP_EXT 17, GET_STATUS 18, FR_SWAP 19, GET_PPS_STATUS 20, GET_COUNTRY_CODES 21, GET_SINK_CAP_EXT 22, GET_SOURCE_INFO 23, GET_REVISION 24.
- Data (PD_DATA_*), usb_pd.h:1512-1527: SOURCE_CAP 1, REQUEST 2, BIST 3, SINK_CAP 4, BATTERY_STATUS 5, ALERT 6, GET_COUNTRY_INFO 7, ENTER_USB 8, EPR_REQUEST 9, EPR_MODE 10, SOURCE_INFO 11, REVISION 12, VENDOR_DEF 15.
- Extended (PD_EXT_*), usb_pd.h:1464-1484: SOURCE_CAP 1, STATUS 2, GET_BATTERY_CAP 3, GET_BATTERY_STATUS 4, BATTERY_CAP 5, GET_MANUFACTURER_INFO 6, MANUFACTURER_INFO 7, SECURITY_REQ/RSP 8/9, FW_UPDATE_REQ/RSP 10/11, PPS_STATUS 12, COUNTRY_INFO 13, COUNTRY_CODES 14, SINK_CAP 15, CONTROL 16, EPR_SOURCE_CAP 17, EPR_SINK_CAP 18, VENDOR_DEF 30.
- EXT_CTRL types, usb_pd.h:704-707: EPR_GET_SOURCE_CAP 1, EPR_GET_SINK_CAP 2, EPR_KEEPALIVE 3, EPR_KEEPALIVE_ACK 4.

Who sends what and what is expected back (usb_pe_drp_sm.c):

| Msg | Sent by / where | Expected reply | Timer / notes |
|---|---|---|---|
| SOURCE_CAP (data) | SRC: PE_SRC_SEND_CAPABILITIES; send_source_cap() L1960-1976 | REQUEST (SNK) | SourceCapabilityTimer PD_T_SEND_SOURCE_CAP 100ms (L2508); CapsCounter N_CAPS_COUNT 25 (L126, L2529) |
| REQUEST (data) | SNK: pe_send_request_msg() L1983 | ACCEPT then PS_RDY; or REJECT/WAIT | SenderResponse via PE_TIMER_SENDER_RESPONSE; WAIT re-request after PD_T_SINK_REQUEST 100ms (L3879) |
| GET_SOURCE_CAP | SNK: L4292 / L7577 | SOURCE_CAP | sender response; REJECT/NOT_SUPPORTED handled L7632-7633 |
| GET_SINK_CAP | SRC: L7464 | SINK_CAP | L7512-7513 handles REJECT/NOT_SUPPORTED |
| PS_RDY | SRC after PD_T_SRC_TRANSITION: L2847-2859; PRS: L5171, L7288, L5490 | SNK waits in PE_SNK_TRANSITION_SINK | SNK PS_TRANSITION 500ms (usb_pd.h:272, L3721) |
| ACCEPT / GOTO_MIN | SRC_TRANSITION_SUPPLY: ACCEPT if PE_FLAGS_ACCEPT else GOTO_MIN (L2781-2786) | | GOTO_MIN without ACCEPT flag path INFERRED to be rare/PRS-only |
| DR_SWAP | DRS_SEND_SWAP | ACCEPT -> PE_DRS_CHANGE (L5022-5024); REJECT/WAIT/NOT_SUPPORTED -> ready (L5025-5029) | sender response |
| PR_SWAP | PRS_SNK_SRC_SEND_SWAP | ACCEPT -> TRANSITION_TO_OFF (L5587-5591); REJECT/WAIT -> SNK_READY (L5592-5599) | PRS_SRC_SNK: REJECT -> SRC_READY (L5281-5283); WAIT -> PE_TIMER_PR_SWAP_WAIT, counter < N_SNK_SRC_PR_SWAP_COUNT (L5284-5291) |
| VCONN_SWAP | VCS_SEND_SWAP | ACCEPT -> VCONN on/off; REJECT/WAIT -> ready; NOT_SUPPORTED -> VCS_FORCE_VCONN if not VCONN src, else ready (L7076-7094) | PE_TIMER_VCONN_ON PD_T_VCONN_SOURCE_ON 100ms (L7128); VCONN swap counter N_VCONN_SWAP_COUNT 3 (L157) |
| DATA_RESET | UDR/DDR (L7741, L7917) | DATA_RESET_COMPLETE or NOT_SUPPORTED (L7780, L7961) | PE_TIMER_DATA_RESET_FAIL 300ms (L7953, L8016); pe_should_send_data_reset() L1619 gates sending |
| VCONN_DISCHARGE / REAPPLIED | L8026, L8040 | | PD_T_VCONN_DISCHARGE 240ms; PD_T_VCONN_REAPPLIED 10ms |
| EPR_MODE | SNK EPR entry L8287 (ENTER); exit L8423 | ENTER_ACK -> wait; ENTER_SUCCESS/FAILED | PD_T_ENTER_EPR 500ms (L8295) |
| EXT CONTROL (EPR_KEEPALIVE) | SNK: L8228-8232 | KEEPALIVE_ACK -> SNK_READY (L8263-8265) | PD_T_SINK_EPR_KEEP_ALIVE 375ms (L3891); timeout -> hard reset (L8271-8272) |
| GET_STATUS / STATUS (ext) | PE_GIVE_STATUS L4745-4757 | (reply) | dpm_get_status_msg() failure -> ready with no reply (L4751-4753) |
| GET_SINK_CAP_EXT | answered PE_DR_SRC_GIVE_SINK_CAP_EXT (L3062-3064) | | |
| GET_SOURCE_INFO / SOURCE_INFO | PE_SRC_GIVE_SOURCE_INFO L8477-8498 | SOURCE_INFO (data, L8484) | |
| GET_REVISION / REVISION | PE_GET_REVISION L7670-7727 (only to partners with major rev 3.0); PE_GIVE_REVISION L8503-8514 | REVISION (data) | |
| ALERT (data) | sent L4857; received -> PE_ALERT_RECEIVED (L2986-2988, L3955-3958) | | |
| BIST | PE_BIST_TX; PRL: BIST_MODE_2 only (usb_pd_protocol.c L1681-1690) | | PD_T_BIST_CONT_MODE 55ms (L5745) |
| CHUNKING_NOT_SUPPORTED | | | PD_T_CHUNKING_NOT_SUPPORTED 45ms (L4484) |
| PING (ctrl) | PRL: no-op for SOP ping (usb_prl_sm.c L2271-2282, L2292-2297) | | |
| Ext requests (BATTERY_CAP/STATUS, MANUFACTURER, SECURITY, FW_UPDATE, COUNTRY_*) | BATTERY_CAP/STATUS only if CONFIG_BATTERY (L2944-2948); other ext -> extended_message_not_supported() (L2884-2899) -> NOT_SUPPORTED | | |

Extended-message dispatch: source L2941-2954, sink L3475-3487 and L3912-3930.

PRL-level collision/discard (usb_prl_sm.c):
- Receiving a message while a TX is pending discards the TX: L2271-2282.
- Duplicate MessageID is dropped: L2263-2264.
- Store MessageID: L2285.

## 2. VDMs

### 2a. Structured VDM (SVDM) handling
- Dispatch (usb_pd_policy.c L250-431):
  - INIT: identity, svids, modes, enter, DP status, DP config, exit, attention (L257-293).
  - Response ACK if rsize >= 1; NAK if 0; BUSY if negative (L298-305).
  - Attention: no reply, only GoodCRC (L282-288, `dfp_consume_attention`).
  - Unknown structured cmd: NAK (L291-297).
  - ACK/NAK/BUSY processing: L309-431. BUSY on ENTER is an error (L413-416). BUSY on discovery means resend (L407-411).
- Default SVDM responders are NULL (usb_pd_policy.c L78-82), so unimplemented commands get NAK.
- Partner VDM handling in PE: `PE_VDM_RESPONSE` for structured REQ (usb_pe_drp_sm.c L2971-2972).
- VDM initiation gating `pe_can_send_sop_vdm()` (L1139-1150):
  - VDMs only in explicit contract, except cable discovery.
  - PD2.0: only ATTENTION from UFP.
  - PD3.x: ENTER/EXIT only from DFP.
- Discovery:
  - Discover Identity (`CMD_DISCOVER_IDENT`) sent at L3587, L4223 (usb_pd_protocol.c).
  - Discover SVID/Modes: usb_pd_policy.c L162 (`VDO(USB_SID_PD,1,CMD_DISCOVER_SVID)`), L206.
  - Discover Identity count limits: N_DISCOVER_IDENTITY_COUNT 6 (L135) and PRECONTRACT limit 2 (L143); comment L129-134 says PRL retries 3 times, so 18 transmissions total.
  - Pre-contract retry interval PE_T_DISCOVER_IDENTITY_NO_CONTRACT 200ms (L149).
- Mode entry/exit/status/config (usb_pd_protocol.c): ENTER/EXIT CMD sends L2168-2174 (timeout), L2311 (exit), L837 (`VDO(..., CMD_ENTER_MODE|VDO_OPOS)` in usb_pd_policy.c).
- Host-initiated exit: `hc_remote_pd_set_amode` L548-577 (usb_pd_policy.c).
- Attention to DP: usb_pd_protocol.c L4877 (`VDO_OPOS(opos)|CMD_ATTENTION` with USB_SID_DISPLAYPORT).
- AP-directed VDM passthrough: common/usbc/ap_vdm_control.c (L113-140, L208-219 DP STATUS/CONFIG parsing; `ap_vdm_naked` L235).

### 2b. VDM timers (include/usb_pd.h, usb_pd_protocol.c)
- PD_T_VDM_BUSY 50ms (usb_pd.h:332), used on BUSY retry (usb_pe_drp_sm.c L5881-5882; usb_pd_protocol.c L1339).
- PD_T_VDM_SNDR_RSP 30ms (usb_pd.h:335), PE_TIMER_VDM_RESPONSE (L5959). Extended to 2x once (L5987-5989), then flagged as timeout (L5990-).
- PD_T_VDM_RCVR_RSP 15ms (usb_pd.h:334); PD_T_VDM_E_MODE 25ms (usb_pd.h:333); PD_T_VDM_WAIT_MODE_E 100ms (usb_pd.h:336). Chosen in `vdm_get_ready_timeout()` (usb_pd_protocol.c L2157-2180); unstructured VDM timeout 500ms (L2163-2164).
- PD_T_AME 1s, UFP attach to alt-mode entry (usb_pd.h:329).

### 2c. Vendor SIDs/VIDs named in code (include/usb_pd.h L1010-1035 unless noted)

| SID/VID | Value | Use in code |
|---|---|---|
| USB_SID_PD | 0xff00 (L1011) | PD SVDM (Discover SVID, etc.); usb_pd_policy.c L162; usb_pd_protocol.c; svdm_rsp_dfp_only.c; usb_pe_drp_sm.c |
| USB_SID_DISPLAYPORT | 0xff01 (L1012) | DP Status/Config/Attention: usb_pd_policy.c L981 (STATUS), L1041 (CONFIG); usb_pd_protocol.c L4877; ap_vdm_control.c L208-219; supported_modes[] L1203-1212 |
| USB_VID_GOOGLE | 0x18d1 (L1016) | Google vendor commands and GFU mode: see 2d |
| USB_VID_INTEL | 0x8087 (L1031) | Thunderbolt entry/exit: tbt_alt_mode.c L444-448 (mode check), L524-538 (EXIT), L566, L691 (ENTER); usb_pd_dpm.c L229, L451, L479, L1289 |
| USB_VID_APPLE | 0x05ac (L1019); PIDs 0x1012/0x1013 (L1020-1021) | Defined only; repo search finds no other reference |
| USB_VID_HP | 0x03F0 (L1023); PIDs L1024-1029 | Defined only; no other reference |
| USB_VID_FRAMEWORK | 0x32ac (L1033); PIDs 0x2/0x3 (L1034-1035) | board/banshee/usbc_config.c (only use found) |
| Samsung | none | No Samsung VID or quirk found in any PD/USBC file |

Mode-table placement: `supported_modes[]` (usb_pd_policy.c L1203-1224) lists DP (enter/status/config/post_config/attention/exit) and GFU (Google, L1215-1222). Mode VDO helper `VDO_MODE_GOOGLE(mode)` = mode & 0xff (usb_pd_vdo.h:864).

### 2d. Google vendor (unstructured) commands
Values come from `VDO_CMD_VENDOR(x) = ((10+x)&0x1f)` (include/usb_pd.h:589-603):
VERSION 10 (x=0), SEND_INFO 11, READ_INFO 12, REBOOT 15, FLASH_ERASE 16, FLASH_WRITE 17, ERASE_SIG 18, PING_ENABLE 20, CURRENT 21, FLIP 22, GET_LOG 23, CCD_EN 24.

Receiver (`pd_custom_vdm`, common/usb_pd_protocol.c L1264-1327), only when VID == Google (L1276-1277):
- VERSION (L1280-1284): prints version string, forces NUL terminator.
- SEND_INFO / READ_INFO (L1285-1311): stores RW hash via pd_dev_store_rw_hash; posts PD_EVENT_UPDATE_DEVICE host event if not known-latest RW (L1300-1301). cnt==6 is treated as "really old devices" with no status byte (L1306-1309).
- CURRENT (L1312-1314): prints mA.
- FLIP (L1315-1318): usb_mux_flip if CONFIG_USBC_SS_MUX.
- GET_LOG (L1319-1323): pd_log_recv_vdm if CONFIG_USB_PD_LOGGING.
- PING_ENABLE: no receiver case in this function (INFERRED: handled on partner side or elsewhere).
- Unknown Google cmd: no reply (L1326).

Responder for flash/reflash path (`pd_custom_flash_vdm`, usb_pd_policy.c L485-545):
- VERSION returns 24-byte version (L491-493).
- REBOOT: power supply reset, then system_reset(0) (L495-498).
- READ_INFO returns 7 words including RW hash and HW dev id (L500-503).
- FLASH_ERASE: only when running RO image (L505-516).
- FLASH_WRITE: only RO image and sequential offset (L517-527).
- ERASE_SIG: zeroes RSA signature area (L528-538).
- Unknown: no answer (L541-542).

Host-side senders (usb_pd_protocol.c):
- Flash/reboot/erase/write from console or host: L5005-5033 (cmd selection), L5326-5355.
- Console ping/curr/vers: L5194-5200.
- GET_LOG: L4896 (`pd_fetch_acc_log_entry`, 75ms wait L4897), L1114 in usb_tc_drp_acc_trysrc_sm.c.
- READ_INFO on GFU status: usb_pd_policy.c L1183-1190.

GFU mode (usb_pd_policy.c L1173-1191): enter always succeeds (L1173-1177); status sends READ_INFO (L1183-1190); config/attention are no-ops (L1193-1201).

## 3. QUIRKS / WORKAROUNDS

| Where (file:line) | Misbehaviour | Code does |
|---|---|---|
| usb_pe_drp_sm.c L2400-2410 (SRC), L3342-3352 (SNK) | Spec says cable needs no reset on PR swap; sending SOP' soft reset to cable "causes bad behavior with some devices" (b/179325862) | Only sends DPM_REQUEST_SOP_PRIME_SOFT_RESET_SEND after PR swap complete, not on initial connect or hard reset |
| usb_pe_drp_sm.c L3356-3363 | Some partners talk to cable after PR swap despite not being VCONN source | Disables SOP' RX if tc_is_vconn_src(); dpm_remove_sink |
| usb_pe_drp_sm.c L7130-7144 | Some partners send SOP' ~1ms after PS_RDY during VCONN swap (spec: source keeps VCONN up to tVCONNSourceOff 25ms) | SOP' RX disabled while waiting for PS_RDY; VCONN still sourced |
| usb_pe_drp_sm.c L1613-1617 | Some partners never answer Data_Reset (spec: Accept or Not_Supported), causing Error Recovery | Only starts Data Reset with partners that look like they support it (pe_should_send_data_reset) |
| usb_pe_drp_sm.c L5874-5884 | Partner BUSY on VDM | Retry discovery after tVDMBusy (50ms) |
| usb_pe_drp_sm.c L5975-5990 | Partners slow to answer VDM ("approximately 2x highest observed") | Extends VDM response timeout once, then flags timeout |
| usb_pe_drp_sm.c L5901-5905 | Partner NACKs discovery with NOT_SUPPORTED | Treated as NAK-equivalent (spec text says NAK, but NOT_SUPPORTED also accepted) |
| usb_pe_drp_sm.c L2255-2273 | Partners "chatty" with initial messages after contract; collisions | PD2.0 jitter: PE_TIMER_WAIT_AND_ADD_JITTER 167ms + up to 165ms (0xf*11ms). Applied on SRC_READY (L2919-2923) and SNK_READY (L3886) |
| usb_pd_protocol.c L282-286, L3502-3512, L4161-4168 | Partners chatty once SRC/SNK_READY; collisions | ready_state_holdoff_timer; no TX until expiry (TCPMv1) |
| usb_pd_protocol.c L1858-1860 | As above | SNK_READY_HOLD_OFF_US 200ms (usb_pd_protocol.c:122) + up to 180ms jitter |
| usb_pd_protocol.c L3676-3684 | Qualcomm-style stacks apply Rp once VBUS < ~3.67V (b/77827528) | Sets Rd before PR swap to sink |
| usb_pd_protocol.c L453 (cmt) | VCONN swap make-before-break (b/72961003, b/180973460) | Comment only |
| usb_pd_protocol.c L1306-1309 | Very old devices have no last byte in VDO_INFO | Accepts cnt==6 |
| usb_tc_drp_acc_trysrc_sm.c L143-146 | ANX7447 silicon: LPM needs debounce < ~39.7ms (b/77544959, b/149761477) | PD_LPM_DEBOUNCE_US = 10ms |
| usb_tc_drp_acc_trysrc_sm.c L148-156 | Some TCPCs slow to update CC_STATUS after LPM exit | Extra delay (TODO b/162347811) |
| usb_tc_drp_acc_trysrc_sm.c L661-671 | Enabling PD in RO on non-EFS2 device causes brownout on dead battery sysjump | Disables PD in that case |
| usb_tc_drp_acc_trysrc_sm.c L2490-2501 | Keep USB SS connected through PR swap (ADB) | usb_mux_set kept connected |
| usb_common.c L293-305 | Some TCPCs auto-toggle even in low-power mode; hub-DRP charging (b/72007056) | Briefly acts as sink (100ms) before going to LPM |
| usb_pe_drp_sm.c L4168-4171 | (brownout) Port disabled to avoid brownout; needs EC reboot | PE_SRC_DISABLED with CPRINTS message |
| usb_pe_drp_sm.c L4175-4190 | Recovery boot on chromeboxes (power_state:rec with cros_ec_softrec_power) | Preserves AP_OFF and STAY_IN_RO flags across PD reset (CONFIG_USB_PD_RESET_PRESERVE_RECOVERY_FLAGS) |
| usb_pe_drp_sm.c L8456-8466 | Source exits EPR without SPR contract (spec 8-22 violation) | Hard reset |
| usb_pe_drp_sm.c L2012-2024 (TODO b/386401791) | Charge manager sends identical REQUEST repeatedly | Suppresses duplicate REQUEST |
| usb_pe_drp_sm.c L2130 (TODO b/272827504) | Discovery not gated by DPM | Pending |
| usb_pe_drp_sm.c L1215 (TODO b/157228506) | Not all states check discard | Pending |
| usb_pd_dpm.c L1126-1130 (b/366241394) | Status SDB must be 7 bytes | Always sends 7-byte Status |
| usb_pd_policy.c L598 (TODO b/148528713) | SOP' storage shared with SOP | Pending |
| driver/tcpm/fusb302.c L416-417 (TODO crbug 791109) | VBUS notification cleanup | Pending |
| driver/tcpm/fusb302.c L23-26 | FUSB302 does not support AUTO_TOGGLE or DISCHARGE_TCPC | #error on those configs |
| driver/tcpm/fusb302.c L490-491 | Single pull-up toggles between CC1/CC2; no Ra | Documented limitation |

Apple, Samsung: no vendor-specific handling in code.

## 4. REPLIES / ERRORS

Not_Supported vs Reject by revision:
- pe_send_not_supported_entry (usb_pe_drp_sm.c L4444-4452): NOT_SUPPORTED if partner rev > PD_REV20, else REJECT.
- Unstructured VDM from partner (usb_pe_drp_sm.c L2973-2981 SRC, L3941-3950 SNK): PD3 -> NOT_SUPPORTED; PD2.0 -> ignored.
- Unknown control msg (L3074-3081, L4060-4066): NOT_SUPPORTED.
- Unknown extended msg (L2952, L3930): NOT_SUPPORTED (via extended_message_not_supported L2884-2899).
- VCONN swap disabled without CONFIG_USBC_VCONN (L3026-3031): NOT_SUPPORTED.
- PR swap rejected by DPM (pd_check_power_swap fails) -> REJECT (L5056-5058).
- VCONN swap rejected by DPM -> REJECT. WAIT is never sent: "PD_CTRL_WAIT is currently not implemented" (L6972-6981).
- Source capability response when request cannot be met -> REJECT always. Comment "Wait messaging should be implemented" (L3167). Contract kept if one exists (L3176-3200).
- Source with zero PDOs answers SOURCE_CAP request with REJECT (L1965-1969).
- Sink on REJECT/WAIT to REQUEST: if explicit contract -> SNK_READY, else SNK_WAIT_FOR_CAPABILITIES (L3661-3682). WAIT arms SinkRequestTimer (L3664, L3877-3879).

Soft reset triggers (usb_pe_drp_sm.c):
- Unexpected control message while in ready/waiting states: L3033-3045 (SRC), L3687-3690 (SNK), L7100 (VCS).
- Protocol error during non-interruptible AMS, or any TX error (ERR_TCH_XMIT): pe_report_error L1319-1324.
- Protocol error when not in explicit contract on SOP: L1322.
- Message discarded on outgoing: pe_check_outgoing_discard L1225-1243.
- Unexpected message during DATA_RESET/EPR: pe_send_soft_reset (L4768, L8262, L8337-8399).
- Protocol error while in PE_SEND_SOFT_RESET -> hard reset (L1264-1266).
- Partner DR_SWAP while in modal operation -> hard reset (L3017-3020, SRC READY; label inferred as PD_CTRL_DR_SWAP).

Hard reset triggers:
- SenderResponseTimer expiry (SRC L2731-2734; SNK L3704-3705; EPR KEEPALIVE L8271-8272).
- PS_TRANSITION timeout with counter <= N_HARD_RESET_COUNT (L3836-3840).
- NoResponseTimer with counter <= N_HARD_RESET_COUNT (L2718-2720).
- Protocol error in SEND_SOFT_RESET (L1264-1266).
- Data-role mismatch on SOP (L2227-2231) -> tc_start_error_recovery (not hard reset).
- EPR exit without SPR contract (L8462-8464).
- Hard reset counter N_HARD_RESET_COUNT = 2 (L123); if exceeded and PD_CONNECTION set -> PE_WAIT_FOR_ERROR_RECOVERY, else PE_SRC_DISABLED (L2719-2724).

Retries:
- PRL TX retries are hardware (TCPC): CONFIG_PD_RETRY_COUNT is 2 for REV30, 3 otherwise (include/config.h:6253-6257). pd_get_retry_count returns 2 or 3 (common/usb_common.c L239-243).
- Auto-retry enabled in fusb302 (driver/tcpm/fusb302.c L402-405).
- Retry-fail -> TX_COMPLETE_FAILED -> PRL TX error -> pe_report_error (usb_prl_sm.c L1164-1186).
- Comment on retries: usb_prl_sm.c L1115-1120 (large/extended messages should not be retried; not supported).

Swap handling:
- PR swap: SRC->SNK via PE_PRS_SRC_SNK_*; counter N_SNK_SRC_PR_SWAP_COUNT 5 (L165); WAIT -> PE_TIMER_PR_SWAP_WAIT 100ms (usb_pd.h:310). PR_SWAP counter reset on REJECT (L5282).
- DR swap: N_DR_SWAP_ATTEMPT_COUNT 5 (L174) for PD2.0 to become DFP before Discover Identity.
- VCONN swap: N_VCONN_SWAP_COUNT 3 (L157).
- FR swap: CONFIG_USB_PD_FRS; FRS_VBUS_DEBOUNCE 5ms (usb_pd.h:322); FR_SWAP ctrl msg chosen when in FRS mode (L5538-5540).
- PS_RDY collisions during swaps: PE_FLAGS_MSG_DISCARDED -> soft reset in TRANSITION_SUPPLY (L2868-2870).

PD revision negotiation:
- PD_REVISION = PD_REV30 when CONFIG_USB_PD_REV30 else PD_REV20 (include/usb_pd.h:806-808).
- SNK sets rev = MIN(PD_REVISION, header rev) (L3516-3518); SRC equivalent (L2653-2657).
- PD2.0 partner detected via prl_get_rev; cable revision logic L752-770.
- Get_Revision only to partners reporting major 3.0 (L2466-2470, L7670-7679).
- VDM SVDM version from vdo_ver[rev] (usb_pd_protocol.c L2135-2137).

Collision avoidance (usb_prl_sm.c L889-931): SRC AMS start sets PRL_FLAGS_SINK_NG and selects Rp for collision (typec_select_src_collision_rp SINK_TX_OK). SNK AMS start waits for SINK_TX_OK (PRL_FLAGS_WAIT_SINK_OK). PD_T_SINK_TX 18ms before SRC TX (usb_pd.h:243; usb_prl_sm.c L1202-1208).

## 5. FUSB302 DRIVER (driver/tcpm/fusb302.c)

- Build restrictions: CONFIG_USB_PD_DUAL_ROLE_AUTO_TOGGLE and CONFIG_USB_PD_DISCHARGE_TCPC unsupported (L23-26).
- State struct L31-39: cc_polarity, vconn_enabled, pulling_up, rx_enable, mdac_vnc, mdac_rd.
- Reset: fusb302_pd_reset (L47-50) on HARDRESET and on HARDSENT interrupt (L1007-1013, L1016-1022).
- Interrupts (L402-440): RETRYFAIL, HARDSENT, TX_SUCCESS, HARDRESET, GCRCSENT unmasked. BC_LVL masked during PD (L705-724).
- TX path:
  - fusb302_tcpm_transmit (L843-935): flush TX FIFO (L866), write ordered set (SOP L871-880, SOP' L883-892, SOP'' L895-904), then fusb302_send_message (L296-330) writes PACKSYM, header, data, JAMCRC.
  - Hard reset TX: set SEND_HARDRESET (L906-911).
  - BIST mode 2: BIST_MODE2 + TX_START, wait PD_T_BIST_TRANSMIT 50ms (L913-925).
  - TX result: TX_SUCCESS -> pd_transmit_complete(SUCCESS) (L998-999); RETRYFAIL -> FAILED (L1002-1004).
- RX path:
  - Burst read: 3-byte header read, then remaining len+4 bytes incl. CRC (L780-812).
  - Loops until non-GoodCRC packet or FIFO empty (L815-816); GoodCRC discarded (L818-821, L1-line macro L28-29).
  - Interrupt GCRCSENT (fires after the auto GoodCRC): drains FIFO into tcpm_enqueue_message if rx_enable else flush (L1025-1036).
  - "TODO: Check token to ensure valid packet" (L795).
- Auto-GoodCRC: fusb302_auto_goodcrc_enable (L76-85), enabled with PD comm (L752).
- Polarity: fusb302_tcpm_set_polarity (L558-604). PD comm enable refuses when polarity unknown (L705-708). Flushes RX FIFO on enable (L726-727).
- SOP' / SOP'' decode: CONFIG_USB_PD_DECODE_SOP; VCONN source only enables ENSOP1/2 (L738-749).
- Toggle: fusb302 toggles one pull-up (L490-491). Set CC modes L486-545 (Rp: pull-up on both CC lines, Rd: pull-down; toggle bit CONTROL2). Toggle enable sequence: low power (L1082-1084), sleep 250us (L1111), set TOGGLE (L1118). Mode selection L1088-1106.
- VBUS: MDAC-based compare; sleep 350us (L1137-1142). fusb302_get_vbus_voltage (L1157-1173).
- Retries: N_RETRIES from CONFIG_PD_RETRY_COUNT (L405). AUTO_RETRY enabled (L404).
- Hard reset generation via control register (L906-911); hard-reset receive resets chip and posts PD_EVENT_RX_HARD_RESET (L1016-1022).

## 6. SOURCE-ROLE SPECIFICS

- Source_Capabilities contents: built from dpm_get_source_pdo (usb_pe_drp_sm.c L1960-1976). Board-defined. Selection order: dpm_get_source_pdo (TCPMv2+PE), charge_manager_get_source_pdo (dynamic/max-current), or static pd_src_pdo (common/usb_common.c L161-177). Board policy files not read (INFERRED: board usb_pd_policy.c defines pd_src_pdo).
- Zero PDOs -> REJECT (L1965-1969).
- Request validation: pd_check_requested_voltage (common/usb_common.c L179-215):
  - idx == 0 or idx > pdo_cnt -> reject (L191-193).
  - Board hook pd_board_check_request (L196-197; default accepts L156-159).
  - op current > PDO max -> reject (L203-204).
  - max current > PDO max and not RDO_CAP_MISMATCH -> reject (L206-207).
  - Otherwise accept.
- PE path: pe_src_negotiate_capability_entry L2746-2770. Fail -> PE_SRC_CAPABILITY_RESPONSE -> REJECT (L3163-3169). Pass -> ACCEPT then transition.
- Capability mismatch (RDO bit 26 analogue): sink may set RDO_CAP_MISMATCH; source only relaxes max-current check (common/usb_common.c L206-207). Sink logs "Mismatch" (usb_pe_drp_sm.c L2010).
- Transition timing (usb_pe_drp_sm.c L2776-2859):
  - Send ACCEPT (or GOTO_MIN) (L2781-2786).
  - After TX complete, wait PD_T_SRC_TRANSITION 25ms (L2847-2848, usb_pd.h:306).
  - Then pd_transition_voltage, send PS_RDY, set PE_FLAGS_PS_READY (L2854-2859).
  - Explicit contract set on PS_RDY TX complete (L2829-2831).
  - PD2.0 first-message jitter applied on first contract in role (L2823-2826).
- Get_Source_Cap reply: PE_SRC_SEND_CAPABILITIES (L3004-3005).
- Get_Sink_Cap reply: PE_SNK_GIVE_SINK_CAP (L3007-3008).
- Get_Status reply: PE_GIVE_STATUS (L3059-3061) with EXT STATUS.
- Get_Source_Cap_Ext (17): no case in PE; falls to NOT_SUPPORTED (L3074-3081). INFERRED: no SRC extended source cap reply.
- Get_Source_Info (23): SOURCE_INFO reply (L3066-3069, L8477-8498).
- Get_Revision (24): REVISION reply (L3070-3072, L8503-8514).
- Get_PPS_Status (20): no case; NOT_SUPPORTED. No PPS source logic found in PE (only PDO_AUG macros, include/usb_pd.h:155-178).
- EPR on source: no EPR source PDO send path found; PD_EXT_EPR_SOURCE_CAP only handled on sink (L3478-3482, L3922). EPR_KEEPALIVE received by source falls to NOT_SUPPORTED (INFERRED from default ext branch L2941-2954).
- PS_RDY timing for PRS: source waits PD_T_SRC_TRANSITION before power-off (L5120-5121), turns off with PD_POWER_SUPPLY_TURN_OFF_DELAY (board value; e.g. board/host/board.h:77 = 20000us), PD_T_PS_SOURCE_OFF 835ms as timeout (usb_pe_drp_sm.c L5391), turn-on PD_T_PS_SOURCE_ON 435ms (L5182). PS_RDY send in wait-source-on (L5171).
- SourceCap holdoff/collision as in section 3.

## 7. TIMERS (include/usb_pd.h unless noted; "used" = referenced in the listed files)

| Timer | Value | Line | Meaning / used in |
|---|---|---|---|
| PD_T_SINK_TX | 18ms | 243 | SRC wait before TX (usb_prl_sm.c L1203) |
| PD_T_CHUNKING_NOT_SUPPORTED | 45ms | 244 | pe L4484 |
| PD_T_HARD_RESET_COMPLETE | 5ms | 245 | PRL hard reset complete (usb_pd_protocol.c) |
| PD_T_HARD_RESET_RETRY | 1ms | 246 | hard reset retry (usb_pd_protocol.c) |
| PD_T_SEND_SOURCE_CAP | 100ms | 247 | SourceCapabilityTimer (pe L2508) |
| PD_T_SINK_WAIT_CAP | 575ms | 248 | SinkWaitCapTimer (pe L3450) |
| PD_T_SINK_TRANSITION | 35ms | 249 | TCPMv1 only (usb_pd_protocol.c) |
| PD_T_SOURCE_ACTIVITY | 45ms | 250 | TCPMv1 |
| PD_T_ENTER_EPR | 500ms | 251 | pe L8295 |
| PD_T_SENDER_RESPONSE | 30ms | 262 | SenderResponseTimer (TCPMv1) |
| PD_T_PS_TRANSITION | 500ms | 272 | SNK PSTransitionTimer (pe L3721) |
| PD_T_PS_SOURCE_ON | 435ms | 276 | pe L5182 |
| PD_T_PS_SOURCE_OFF | 835ms | 277 | pe L5391 |
| PD_T_PS_HARD_RESET | 25ms | 278 | pe L3230, L3262 |
| PD_T_ERROR_RECOVERY | 240ms | 279 | error recovery (TCPMv1/TC) |
| PD_T_CC_DEBOUNCE | 100ms | 280 | TC |
| PD_T_DRP_SNK | 40ms | 282 | DRP toggle sink |
| PD_T_DRP_SRC | 30ms | 283 | DRP toggle source |
| PD_T_DEBOUNCE | 15ms | 284 | TC |
| PD_T_TRY_CC_DEBOUNCE | 15ms | 285 | TC |
| PD_T_SINK_ADJ | 55ms | 286 | TC/DPM |
| PD_T_SRC_RECOVER | 760ms | 287 | TC |
| PD_T_SRC_RECOVER_MAX | 1000ms | 288 | TC |
| PD_T_SRC_TURN_ON | 275ms | 289 | TC |
| PD_T_SAFE_0V | 650ms | 290 | TC |
| PD_T_NO_RESPONSE | 5.5s | 291 | NoResponseTimer (pe L3227, L3259) |
| PD_T_BIST_TRANSMIT | 50ms | 292 | fusb302 BIST |
| PD_T_BIST_RECEIVE | 60ms | 293 | TCPMv1 BIST |
| PD_T_BIST_CONT_MODE | 55ms | 294 | pe L5745 |
| PD_T_VCONN_SOURCE_ON | 100ms | 295 | pe L7128 |
| PD_T_DRP_TRY | 125ms | 296 | TC Try.SRC |
| PD_T_TRY_TIMEOUT | 550ms | 297 | TC Try |
| PD_T_TRY_WAIT | 600ms | 298 | TryWait.SNK |
| PD_T_SINK_REQUEST | 100ms | 299 | SinkRequestTimer (pe L3879) |
| PD_T_PD_DEBOUNCE | 15ms | 300 | TC |
| PD_T_CHUNK_SENDER_RESPONSE | 25ms | 301 | prl L1717 |
| PD_T_CHUNK_SENDER_REQUEST | 25ms | 302 | prl L2039 |
| PD_T_SWAP_SOURCE_START | 25ms | 303 | pe L2414 |
| PD_T_RP_VALUE_CHANGE | 20ms | 304 | TC |
| PD_T_SRC_DISCONNECT | 15ms | 305 | TC |
| PD_T_SRC_TRANSITION | 25ms | 306 | pe L2847, L5103 |
| PD_T_VCONN_STABLE | 50ms | 307 | TC |
| PD_T_DISCOVER_IDENTITY | 45ms | 308 | pe L1699, L6184 |
| PD_T_SYSJUMP | 1s | 309 | TC/system |
| PD_T_PR_SWAP_WAIT | 100ms | 310 | pe L5291 |
| PD_T_DATA_RESET | 225ms | 311 | DR reset |
| PD_T_DATA_RESET_FAIL | 300ms | 312 | pe L7953, L8016 |
| PD_T_VCONN_REAPPLIED | 10ms | 313 | pe L8040, L8126 |
| PD_T_VCONN_DISCHARGE | 240ms | 314 | pe L8026 |
| PD_T_SINK_EPR_KEEP_ALIVE | 375ms | 315 | pe L3891 |
| PD_T_FRS_VBUS_DEBOUNCE | 5ms | 322 | FRS TC |
| PD_T_AME | 1s | 329 | UFP attach -> alt mode entry |
| PD_T_VDM_BUSY | 50ms | 332 | pe L5882 |
| PD_T_VDM_E_MODE | 25ms | 333 | usb_pd_protocol.c L2175 |
| PD_T_VDM_RCVR_RSP | 15ms | 334 | usb_pd_protocol.c L2177 |
| PD_T_VDM_SNDR_RSP | 30ms | 335 | pe L5959; doubled L5988 |
| PD_T_VDM_WAIT_MODE_E | 100ms | 336 | usb_pd_protocol.c L2169 |
| PD_T_VPDDETACH | 20ms | 339 | VPD TC |
| PD_T_VPDCTDD | 4ms | 340 | VPD TC |
| PD_T_VPDDISABLE | 25ms | 341 | VPD TC |
| PD_T_TCPC_TX_TIMEOUT | 100ms | include/usb_pd_tcpm.h:23 | PRL TX timeout (usb_prl_sm.c L1133, L1164) |
| PE_T_DISCOVER_IDENTITY_NO_CONTRACT | 200ms | usb_pe_drp_sm.c:149 | cable discovery pre-contract |
| PD_LPM_DEBOUNCE_US | 10ms | usb_tc_drp_acc_trysrc_sm.c:146 | ANX7447 LPM |
| SNK_READY_HOLD_OFF_US | 200ms | usb_pd_protocol.c:122 | TCPMv1 holdoff + up to 180ms jitter (L1858-1860) |
| PE PD2.0 jitter | 167ms + up to 165ms | usb_pe_drp_sm.c:2267-2270 | WAIT_AND_ADD_JITTER |
| PD_POWER_SUPPLY_TURN_ON/OFF_DELAY | board-defined (board/host: 20ms) | board/host/board.h:76-77 | pe L5130, L5477 |

Counters (usb_pe_drp_sm.c L123-174): N_HARD_RESET_COUNT 2, N_CAPS_COUNT 25, N_DISCOVER_IDENTITY_COUNT 6, N_DISCOVER_IDENTITY_PRECONTRACT_LIMIT 2, N_VCONN_SWAP_COUNT 3, N_SNK_SRC_PR_SWAP_COUNT 5, N_DR_SWAP_ATTEMPT_COUNT 5. Retry count CONFIG_PD_RETRY_COUNT 2 (REV30) / 3 (include/config.h:6253-6257).

## Coverage gaps
- Not read: usb_pd_host_cmd.c, most of usb_pd_dpm.c, usb_pd_pdo.c, board PDO tables, zephyr PDC drivers (pdc_*.c, tps6699x), usb_pd_tcpc.c, usb_pe_ctvpd_sm.c, usb_tc_vpd/ctvpd, svdm_rsp_dfp_only.c internals.
- No Apple, Samsung, HP or Framework handling beyond definitions and one board usage (see 2c).
- No commit pin; fetched from default branch on 2026-11-07.
