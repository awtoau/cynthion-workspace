<!-- Haiku research report, 2026-11-07, read-only survey. Leads, not verified facts: INFERRED marks are the agent's. Index: ../README.md -->

# USB PD R3.1 V1.6 (Oct 2022) message catalogue: extraction report

**Source:** `/home/dan/vendors/usb-if/sources/USB_PD_R3_1_V1.6_2022-10.pdf` (868 pages, SHA-256 32a894764b2687b8429384df7962591bcd85fb4b57b8f62e5ed2d9cd5e131fc6, matches manifest `/home/dan/vendors/usb-if/README.md`). PDF page index = printed page number.

**Method:** `pdftotext -layout -f N -l M <pdf> -` to stdout, read by page range. No output file was written, because the read-only rules bar creating files including in the scratchpad. The requested `scratchpad/pd-spec/` output file does not exist.

**Legend:** `p.N` = page. `INFERRED` = not stated in the spec text read. `NOT READ` = gap. N Normative, CN Conditional Normative, R Recommended, O Optional, NS Not_Supported response required, I Ignore, NA Not allowed, NK NAK required.

**Spec-wide caveat:** the spec does not state per message which PD revision introduced it. Explicit changes: Rev 3.1 adds EPR and AVS (p.56, 1.8); Rev 3.0 introduces SVDM Version 2.0 and VDO version fields (p.59, 2.3). The "Rev introduced" column is therefore INFERRED. PD 2.0 spec is not on disk.

---

## 1. CONTROL MESSAGES (Table 6-5, p.119-120)

| Bits 4..0 | Name | SOP valid | Sent by | Expected reply | Section | Rev |
|---|---|---|---|---|---|---|
| 00001 | GoodCRC | SOP* | Source, Sink, Cable Plug | none (is the ack) | 6.3.1 | INFERRED |
| 00010 | GotoMin | SOP only | Source only | Sink reduces to Min Operating Current within tSnkNewPower (6.3.2); follow-on NOT READ | 6.3.2 | INFERRED |
| 00011 | Accept | SOP* | Source, Sink, Cable Plug | none | 6.3.3 | INFERRED |
| 00100 | Reject | SOP* | Source, Sink, Cable Plug | none | 6.3.4 | INFERRED |
| 00101 | Ping | SOP only | Source only | none; Sink treats as I (Table 6-76, p.267) | 6.3.5 | INFERRED |
| 00110 | PS_RDY | SOP only | Source or Sink | none | 6.3.6 | INFERRED |
| 00111 | Get_Source_Cap | SOP only | Sink or DRP | Source_Capabilities (6.4.1.2) | 6.3.7 | INFERRED |
| 01000 | Get_Sink_Cap | SOP only | Source or DRP | Sink_Capabilities (6.4.1.3) | 6.3.8 | INFERRED |
| 01001 | DR_Swap | SOP only | Source or Sink | Accept, Reject or Wait (6.9) | 6.3.9 | INFERRED |
| 01010 | PR_Swap | SOP only | Source or Sink (see conflict, section 8) | Accept, Reject or Wait (6.9) | 6.3.10 | INFERRED |
| 01011 | VCONN_Swap | SOP only | Source or Sink | Accept, Reject or Wait (6.9) | 6.3.11 | INFERRED |
| 01100 | Wait | SOP only | Source or Sink | retry after tSinkRequest, tPRSwapWait, tDRSwapWait, tVCONNSwapWait or tEnterUSBWait (6.3.12) | 6.3.12 | INFERRED |
| 01101 | Soft_Reset | SOP* | Source or Sink | Accept (6.3.13, 6.8.1) | 6.3.13 | INFERRED |
| 01110 | Data_Reset | SOP only | Source or Sink | Accept, then Data_Reset_Complete (6.3.14) | 6.3.14 | INFERRED |
| 01111 | Data_Reset_Complete | SOP only | Source or Sink | none | 6.3.15 | INFERRED |
| 10000 | Not_Supported | SOP* | Source, Sink, Cable Plug | none | 6.3.16 | INFERRED |
| 10001 | Get_Source_Cap_Extended | SOP only | Sink or DRP | Source_Capabilities_Extended (6.5.1) | 6.3.17 | INFERRED |
| 10010 | Get_Status | SOP* | Source or Sink | Status (6.5.2) | 6.3.18 | INFERRED |
| 10011 | FR_Swap | SOP only | Sink (Rd asserted, note 1 p.120) | FR_Swap sequence (6.6.17; NOT READ in detail) | 6.3.19 | INFERRED |
| 10100 | Get_PPS_Status | SOP only | Sink | PPS_Status (6.5.10) | 6.3.20 | INFERRED |
| 10101 | Get_Country_Codes | SOP only | Source or Sink | Country_Codes (6.5.11) | 6.3.21 | INFERRED |
| 10110 | Get_Sink_Cap_Extended | SOP only | Source or DRP | Sink_Capabilities_Extended (6.5.13) | 6.3.22 | INFERRED |
| 10111 | Get_Source_Info | SOP only | Sink or DRP | Source_Info (6.4.11) | 6.3.23 | INFERRED |
| 11000 | Get_Revision | SOP* | Source or Sink (also Cable Plug via SOP'/SOP'') | Revision (6.4.12) | 6.3.24 | INFERRED |
| 11001-11111 | Reserved | | | | | |

---

## 2. DATA MESSAGES (Table 6-6, p.128-129)

| Bits 4..0 | Name | SOP valid | Sent by | Expected reply | Section | Rev |
|---|---|---|---|---|---|---|
| 00001 | Source_Capabilities | SOP only | Source or DRP | Request (Sink) | 6.4.1.2 (p.129-132) | INFERRED |
| 00010 | Request | SOP only | Sink only | Accept, Reject, Wait (6.9) | 6.4.2 (p.141-146) | INFERRED |
| 00011 | BIST | SOP* | Tester, Source or Sink | GoodCRC (Test Data); no GoodCRC in continuous mode | 6.4.3 (p.146-149) | INFERRED |
| 00100 | Sink_Capabilities | SOP only | Sink or DRP | none | 6.4.1.3 | INFERRED |
| 00101 | Battery_Status | SOP only | Source or Sink | none (answers Get_Battery_Status, 6.5.4) | 6.4.5 (p.178) | INFERRED |
| 00110 | Alert | SOP only | Source or Sink | Get_Battery_Status or Get_Status (6.4.6.1) | 6.4.6 (p.179-181) | INFERRED |
| 00111 | Get_Country_Info | SOP only | Source or Sink | Country_Info (6.5.12) | 6.4.7 (p.181-182) | INFERRED |
| 01000 | Enter_USB | SOP* | DFP | Accept, Wait, Reject (6.9) | 6.4.8 (p.182-184) | INFERRED |
| 01001 | EPR_Request | SOP only | Sink | Accept, Reject, Wait via EPR path (6.4.9) | 6.4.9 (p.184) | INFERRED |
| 01010 | EPR_Mode | SOP only | Source or Sink | EPR_Mode (Ack / Succeeded / Failed) (6.4.10) | 6.4.10 (p.184-189) | Rev 3.1 INFERRED |
| 01011 | Source_Info | SOP only | Source | none | 6.4.11 (p.189-191) | INFERRED |
| 01100 | Revision | SOP* | Source, Sink, Cable Plug | none | 6.4.12 (p.191) | INFERRED |
| 01111 | Vendor_Defined | SOP* | Source, Sink, Cable Plug | Structured: ACK / NAK / BUSY (Table 6-32). Unstructured: Table 6-73 | 6.4.4 (p.149-178) | INFERRED |
| others | Reserved | | | | | |

### 2.1 Power Data Objects (Table 6-7 p.130; bits 31..30 = type)

| Object | Bits 31..30 | Fields | Table |
|---|---|---|---|
| Source Fixed | 00 | 29 DRP; 28 USB Suspend Supported; 27 Unconstrained Power; 26 USB Comms Capable; 25 DR Data; 24 Unchunked Ext Msgs Supported; 23 EPR Mode Capable; 22 reserved (0); 21..20 Peak Current; 19..10 Voltage (50 mV); 9..0 Max Current (10 mA). Bits 29..23 used only on vSafe5V PDO (p.132) | 6-9 p.132-133; 6-10 p.134 |
| Source Variable (non-battery) | 10 | 29..20 Max V (50 mV); 19..10 Min V (50 mV); 9..0 Max I (10 mA) | 6-11 p.135 |
| Source Battery | 01 | 29..20 Max V (50 mV); 19..10 Min V (50 mV); 9..0 Max Power (250 mW) | 6-12 p.135 |
| Source SPR PPS APDO | 11, 29..28 = 00 | 27 PPS Power Limited; 26..25 reserved; 24..17 Max V (100 mV); 16 reserved; 15..8 Min V (100 mV); 7 reserved; 6..0 Max I (50 mA) | 6-13 p.136 |
| Source EPR AVS APDO | 11, 29..28 = 01 | 27..26 Peak Current; 25..17 Max V (100 mV); 16 reserved; 15..8 Min V (100 mV); 7..0 PDP (1 W) | 6-14 p.136; 6-15 p.137 |
| Sink Fixed | 00 | 29 DRP; 28 Higher Capability; 27 Unconstrained; 26 USB Comms; 25 DR Data; 24..23 FRS Type-C Current; 22..20 reserved; 19..10 Voltage (50 mV); 9..0 Operational Current (10 mA) | 6-16 p.138 |
| Sink Variable | 10 | 29..20 Max V; 19..10 Min V; 9..0 Operational Current (10 mA) | 6-17 p.139 |
| Sink Battery | 01 | 29..20 Max V; 19..10 Min V; 9..0 Operational Power (250 mW) | 6-18 p.140 |
| Sink SPR PPS APDO | 11, 29..28 = 00 | 27..25 reserved; 24..17 Max V (100 mV); 16 reserved; 15..8 Min V (100 mV); 7 reserved; 6..0 Max I (50 mA) | 6-19 p.140 |
| Sink EPR AVS APDO | 11, 29..28 = 01 | Bits 31..28 and 7..0 PDP (1 W) consistent with Source layout (INFERRED); full bit table NOT READ | 6-20 p.140-141 |

Object order (6.4.1, p.129): (1) vSafe5V Fixed first; (2) other Fixed in voltage order; (3) Battery in min-V order; (4) Variable in min-V order; (5) PPS in max-V order. Max 7 PDOs in SPR. EPR PDOs start at object position 8 (6.5.15.1, p.215).

### 2.2 Request Data Objects (Tables 6-21 to 6-26, p.141-143)

Bits 31..28 = object position (0000b and 1110b-1111b reserved). Common flags:

| Bit | Flag |
|---|---|
| 26 | Capability Mismatch |
| 25 | USB Communications Capable |
| 24 | No USB Suspend |
| 23 | Unchunked Extended Messages Supported |
| 22 | EPR Mode Capable |

| RDO | Bit 27 | Bits 19..10 | Bits 9..0 | Other | Table |
|---|---|---|---|---|---|
| Fixed / Variable | GiveBack = 0 | Operating Current (10 mA) | Max Operating Current (10 mA) | 21..20 reserved | 6-21 p.141 |
| Fixed / Variable with GiveBack | GiveBack = 1 | Operating Current (10 mA) | Min Operating Current (10 mA) | | 6-22 p.141-142 |
| Battery | GiveBack = 0 | Operating Power (250 mW) | Max Operating Power (250 mW) | | 6-23 p.142 |
| Battery with GiveBack | GiveBack = 1 | Operating Power (250 mW) | Min Operating Power (250 mW) | | 6-24 p.142 |
| PPS | 27 reserved (0) | 20..9 Output Voltage (20 mV) | 6..0 Operating Current (50 mA) | 21 reserved; 8..7 reserved | 6-25 p.142-143 |
| AVS | 27 reserved (0) | 20..9 Output Voltage (25 mV units; LSB 2 bits zero, so effective 100 mV) | 6..0 Operating Current (50 mA) | 21 reserved | 6-26 p.143 |

### 2.3 BIST Data Object (Table 6-27, p.147; 6.4.3 p.146-149)

| Bits 31..28 | Mode | Requirement | Behaviour |
|---|---|---|---|
| 0101b | BIST Carrier Mode | Mandatory | UUT sends continuous carrier; exit within tBISTContMode (6.4.3.1) |
| 1000b | BIST Test Data | Mandatory | Returns GoodCRC, then sends no further Messages except GoodCRC until Hard Reset (6.4.3.2) |
| 1001b | BIST Shared Test Mode Entry | Mandatory for shared-capacity UUTs | Offers full Source_Capabilities on every port in group within tBISTSharedTestMode (6.4.3.3.1) |
| 1010b | BIST Shared Test Mode Exit | Mandatory for shared-capacity UUTs | Exit shared capacity test mode (6.4.3.3.2) |
| other | Shall not be used | | |

Bits 27..0 Shall be zero. BIST only honoured at vSafe5V; ignored otherwise (p.146).

### 2.4 Alert Data Object (Table 6-47, p.179-180)

| Bits | Field | Notes |
|---|---|---|
| 31..24 | Type of Alert | bit 1 Battery Status Change; bit 2 OCP (Source only); bit 3 OTP; bit 4 Operating Condition Change; bit 5 Source Input Change; bit 6 OVP; bit 7 Extended Alert Event; bit 0 reserved |
| 23..20 | Fixed Batteries | B20 = Battery 0 ... B23 = Battery 3 (Table 6-47 and 6.4.6.2) |
| 19..16 | Hot Swappable Batteries | Table 6-47: B16 = Battery 4 ... B19 = Battery 7. 6.4.6.3 says B16 = Battery 0. **Conflict, see section 8.** |
| 15..4 | reserved | |
| 3..0 | Extended Alert Event Type | Valid only if bit 7 set (6.4.6.4) |

### 2.5 Source_Info Data Object (Table 6-51, p.190)

| Bits | Field |
|---|---|
| 31 | Port Type (0 = Managed Capability Port) |
| 30..24 | reserved |
| 23..16 | Port Maximum PDP |
| 15..8 | Port Present PDP |
| 7..0 | Port Reported PDP |

PDP units not stated in text read (NOT READ).

### 2.6 Revision Message Data Object (Table 6-52, p.191)

| Bits | Field |
|---|---|
| 31..28 | Revision.major |
| 27..24 | Revision.minor |
| 23..20 | Version.major |
| 19..16 | Version.minor |
| 15..0 | reserved (0) |

Example: Rev 3.1 Version 1.0 = Version.major 0001b, Version.minor 0000b. Spec Revision field values (6.2.1.1.5): 00b = 1.0, 01b = 2.0, 10b = 3.0, 11b = reserved.

### 2.7 Enter_USB Data Object (Table 6-49, p.182-183)

| Bits | Field | Values |
|---|---|---|
| 31 | reserved | |
| 30..28 | USB Mode | 000 USB 2.0; 001 USB 3.2; 010 USB4; others reserved |
| 27 | reserved | |
| 26 | USB4 DRD | 1 = capable of USB4 Device |
| 25 | USB3 DRD | 1 = capable of USB 3.2 Device |
| 24 | reserved | |
| 23..21 | Cable Speed | 000 USB2 only; 001 Gen1; 010 Gen2; 011 USB4 Gen3; 100 USB4 Gen4 |
| 20..19 | Cable Type | 00 Passive; 01 Active Re-timer; 10 Active Re-driver; 11 Optically Isolated |
| 18..17 | Cable Current | 00 VBUS not supported; 10 3A; 11 5A |
| 16 | PCIe Support | Host tunneling |
| 15 | DP Support | Host tunneling |
| 14 | TBT Support | Host Connection Manager |
| 13 | Host Present | 1 = host connected |
| 12..0 | reserved | |

Cable-field bits are ignored by Cable Plug (note 2).

### 2.8 EPR_Mode Data Object (Table 6-50, p.185-186)

| Bits 31..24 (Action) | Name | Bits 23..16 (Data) | Sent by |
|---|---|---|---|
| 0x00 | Reserved | | |
| 0x01 | Enter | Sink Operational PDP | Sink |
| 0x02 | Enter Acknowledged | 0 | Source |
| 0x03 | Enter Succeeded | 0 | Source |
| 0x04 | Enter Failed | 0x01 Cable not EPR capable; 0x03 RDO EPR bit not set; 0x04 Source unable to enter EPR at PDP; 0x05 5V fixed PDO EPR bit not set | Source |
| 0x05 | Exit | 0 | Source or Sink |
| 0x06-0xFF | Reserved | | |

Bits 15..0 reserved.

### 2.9 Battery_Status Data Object (Table 6-46, p.178)

| Bits | Field | Notes |
|---|---|---|
| 31..16 | Battery Present Capacity | SoC in 0.1 Wh; 0xFFFF = unknown |
| 15..8 | Battery Info | bit 0 Invalid Battery reference; bit 1 Battery Present; bits 3..2 charging status (00 charging, 01 discharging, 10 idle, 11 reserved); bits 7..4 reserved |
| 7..0 | reserved (0) | |

### 2.10 Vendor_Defined header (Tables 6-28 p.150, 6-29 p.151)

**Unstructured VDM header (Table 6-28):** bits 31..16 VID; bit 15 = 0; bits 14..0 vendor-defined.

**Structured VDM header (Table 6-29):**

| Bits | Field | Values |
|---|---|---|
| 31..16 | SVID | 16-bit SID or VID. PD SID = 0xFF00 (Table 6-31) |
| 15 | VDM Type | 1 = Structured |
| 14..13 | Structured VDM Version (Major) | 00 = 1.0 (shall not be used); 01 = 2.x; 10-11 reserved |
| 12..11 | Structured VDM Version (Minor) | 00 = 2.0 (pre Rev 3.1 V1.6 ports); 01 = 2.1 (Rev 3.1 V1.6 and later) |
| 10..8 | Object Position | Enter/Exit/Attention: 001-110 = index into mode VDO list; 111 = Exit all (Exit only); 000 reserved. Other commands: 000 |
| 7..6 | Command Type | 00 REQ; 01 ACK; 10 NAK; 11 BUSY |
| 5 | reserved | Shall be 0 and ignored |
| 4..0 | Command | 1 Discover Identity; 2 Discover SVIDs; 3 Discover Modes; 4 Enter Mode; 5 Exit Mode; 6 Attention; 7-15 reserved; 16-31 SVID-specific |

### 2.11 Structured VDO layouts (sent in Discover Identity ACK)

Numeric Product Type codes (Table 6-33 bits 29..27 / 25..23 and Tables 6-34 to 6-36 values) NOT captured; only names extracted.

| VDO (Table) | Page | Bits | Fields |
|---|---|---|---|
| ID Header (6-33) | p.155-156 | 31 | USB Comms as USB Host |
| | | 30 | USB Comms as USB Device |
| | | 29..27 | SOP Product Type (UFP): Undefined / PDUSB Hub / PDUSB Peripheral (names only; codes NOT READ) |
| | | 26 | Modal Operation Supported |
| | | 25..23 | SOP Product Type (DFP): Undefined / PDUSB Hub / PDUSB Host (names only; codes NOT READ) |
| | | 22..21 | Connector Type (00 legacy; 01 reserved; others not read) |
| | | 20..16 | reserved |
| | | 15..0 | USB Vendor ID |
| Cert Stat (6-37) | p.158 | 31..0 | XID (USB-IF assigned) |
| Product (6-38) | p.158 | 31..16 | USB Product ID |
| | | 15..0 | bcdDevice |
| UFP VDO (6-39) | p.158-159 | 31..29 | UFP VDO Version (011 = 1.3) |
| | | 28 | reserved |
| | | 27..24 | Device Capability bits (details NOT READ) |
| | | 23..22 | Connector Type (legacy) = 00 |
| | | 21..11 | reserved |
| | | 10..8 | VCONN Power (111 reserved) |
| | | 7 | VCONN Required |
| | | 6 | VBUS Required |
| | | 5..3 | Alternate Modes (bit 0 TBT3; bit 1 reconfigure; bit 2 no reconfigure) |
| | | 2..0 | USB Highest Speed (000 USB 2.0 only; 101-111 reserved) |
| DFP VDO (6-40) | p.160-161 | 31..29 | DFP VDO Version (010 = 1.2) |
| | | 28..27 | reserved |
| | | 26..24 | Host Capability bits (details NOT READ) |
| | | 23..22 | Connector Type (legacy) = 00 |
| | | 21..5 | reserved |
| | | 4..0 | Port Number |
| Passive Cable (6-41) | p.161-162 | 31..28 | HW Version (VID owner) |
| | | 27..24 | FW Version |
| | | 23..21 | VDO Version (000 = 1.0) |
| | | 20 | reserved |
| | | 19..18 | Plug type: 10 USB Type-C; 11 Captive |
| | | 17 | EPR Mode Capable |
| | | 16..13 | Cable Latency (0001 <10 ns ~1 m ... 1000 >70 ns) |
| | | 12..11 | Termination: 00 VCONN not required; 01 VCONN required |
| | | 10..9 | Max VBUS: 00 20 V; 01 30 V (deprecated); 10 40 V (deprecated); 11 50 V |
| | | 8..7 | reserved |
| | | 6..5 | VBUS Current: 01 3 A; 10 5 A |
| | | 4..3 | reserved |
| | | 2..0 | USB Highest Speed (000 USB 2.0; 001 Gen1; 010 Gen2; 011 Gen3; 100 Gen4) |
| Active Cable VDO1 (6-42) | p.163-164 | 31..28, 27..24 | HW, FW |
| | | 23..21 | VDO Version (011 = 1.3) |
| | | 19..18 | Plug type (10 USB-C; 11 Captive) |
| | | 17 | EPR Mode Capable |
| | | 16..13 | Cable Latency |
| | | 12..11 | Termination: 10 one end Active, one Passive, VCONN req; 11 both ends Active, VCONN req |
| | | 10..9 | Max VBUS (as Passive) |
| | | 8 | SBU Supported (0 = supported) |
| | | 7 | SBU Type |
| | | 6..5 | VBUS Current (00 default; 01 3 A; 10 5 A) |
| | | 4 | VBUS Through Cable |
| | | 3 | SOP'' Controller Present |
| | | 2..0 | USB Highest Speed (as Passive) |
| Active Cable VDO2 (6-43) | p.165 | 31..24 | Max Operating Temperature (°C) |
| | | 23..16 | Shutdown Temperature (°C) |
| | | 14..12 | U3/CLd Power (000 >10 mW ... 110 <50 µW) |
| | | 11 | U3 to U0 transition mode (0 direct; 1 via U3S) |
| | | 10 | Physical connection (0 Copper; 1 Optical) |
| | | 9 | Active element (0 Redriver; 1 Retimer) |
| | | 8 | USB4 Supported (0 = supported) |
| | | 7..6 | USB 2.0 Hub Hops Consumed |
| | | 5 | USB 2.0 Supported (0 = supported) |
| | | 4 | USB 3.2 Supported (0 = supported) |
| | | 3 | USB Lanes (0 one; 1 two) |
| | | 2 | Optically Isolated Active Cable |
| | | 1 | USB4 Asymmetric Mode |
| | | 0 | USB Gen (0 Gen1; 1 Gen2 or higher) |
| VPD VDO (6-44) | p.168 | 31..28, 27..24 | HW, FW |
| | | 23..21 | VDO Version (000 = 1.0) |
| | | 20..17 | reserved |
| | | 16..15 | Max VPD VBUS (00 20 V; 01/10/11 30/40/50 V deprecated) |
| | | 14 | Charge Through Current (1 = 5 A) |
| | | 13 | reserved |
| | | 12..7 | VBUS Impedance (2 mΩ steps, when Charge Through) |
| | | 6..1 | Ground Impedance (1 mΩ steps, when Charge Through) |
| | | 0 | Charge Through Support |
| Discover SVIDs ACK (6-45) | p.170 | 31..16 | SVID n |
| | | 15..0 | SVID n+1 (2 SVIDs per VDO) |

Discover Modes returns 0-6 mode VDOs (6.4.4.2.4). Mode-specific VDO content (DP, TBT, etc.) is not in this spec.

---

## 3. EXTENDED MESSAGES (Table 6-53, p.191-192; Section 6.5)

Extended header (Table 6-3, p.113): bit 15 Chunked; bits 14..11 Chunk Number (0-9); bit 10 Request Chunk; bit 9 reserved; bits 8..0 Data Size (max 260).

| Type (4..0) | Name | SOP valid | Sent by | Expected reply | Section | Data block | Rev |
|---|---|---|---|---|---|---|---|
| 00001 | Source_Capabilities_Extended | SOP only | Source or DRP | answers Get_Source_Cap_Extended | 6.5.1 | SCEDB 25 B (Table 6-54, p.192-194) | INFERRED |
| 00010 | Status | SOP* | Source, Sink, Cable Plug | answers Get_Status | 6.5.2 | SDB 7 B (Table 6-55, p.197-199); Cable Plug SDB 2 B (Table 6-56, p.201) | INFERRED |
| 00011 | Get_Battery_Cap | SOP only | Source or Sink | Battery_Capabilities | 6.5.3 | GBCDB 1 B (Table 6-57, p.201) | INFERRED |
| 00100 | Get_Battery_Status | SOP only | Source or Sink | Battery_Status (BSDO, 6.4.5) | 6.5.4 | GBSDB 1 B (Table 6-58, p.202) | INFERRED |
| 00101 | Battery_Capabilities | SOP only | Source or Sink | none | 6.5.5 | BCDB 9 B (Table 6-59, p.202-203) | INFERRED |
| 00110 | Get_Manufacturer_Info | SOP* | Source or Sink | Manufacturer_Info | 6.5.6 | GMIDB 2 B (Table 6-60, p.204) | INFERRED |
| 00111 | Manufacturer_Info | SOP* | Source, Sink, Cable Plug | none | 6.5.7 | MIDB 5-26 B (Table 6-61, p.204) | INFERRED |
| 01000 | Security_Request | SOP* | Source or Sink | Security_Response | 6.5.8.1 | SRQDB 4-260 B | INFERRED |
| 01001 | Security_Response | SOP* | Source, Sink, Cable Plug | none | 6.5.8.2 | SRPDB 4-260 B | INFERRED |
| 01010 | Firmware_Update_Request | SOP* | Source or Sink | Firmware_Update_Response | 6.5.9.1 | FRQDB 4-260 B (external USBPDFirmwareUpdate 1.0) | INFERRED |
| 01011 | Firmware_Update_Response | SOP* | Source, Sink, Cable Plug | none | 6.5.9.2 | FRPDB 4-260 B (external) | INFERRED |
| 01100 | PPS_Status | SOP only | Source | answers Get_PPS_Status | 6.5.10 | PPSSDB 4 B (Table 6-62, p.207) | INFERRED |
| 01101 | Country_Info | SOP only | Source or Sink | answers Get_Country_Info | 6.5.12 | CIDB 4-26 B (Table 6-64, p.209) | INFERRED |
| 01110 | Country_Codes | SOP only | Source or Sink | answers Get_Country_Codes | 6.5.11 | CCDB 4-26 B (Table 6-63, p.208) | INFERRED |
| 01111 | Sink_Capabilities_Extended | SOP only | Sink or DRP | answers Get_Sink_Cap_Extended | 6.5.13 | SKEDB 24 B (Table 6-65, p.209-211) | INFERRED |
| 10000 | Extended_Control | SOP only | Source or Sink | per ECDB type (6.5.14) | 6.5.14 | ECDB 2 B (Table 6-66, p.214) | Rev 3.1 INFERRED |
| 10001 | EPR_Source_Capabilities | SOP only | Source or DRP | EPR_Request | 6.5.15.2 | PDO list 4-260 B | Rev 3.1 INFERRED |
| 10010 | EPR_Sink_Capabilities | SOP only | Sink or DRP | none | 6.5.15.3 | PDO list 4-260 B | Rev 3.1 INFERRED |
| 11110 | Vendor_Defined_Extended | SOP* | Source, Sink, Cable Plug | per vendor | 6.5.16 | NOT READ | INFERRED |
| others | Reserved | | | | | | |

### 3.1 Data block layouts (key fields)

- **SCEDB (Table 6-54, 25 B):** 0-1 VID; 2-3 PID; 4-7 XID; 8 FW Version; 9 HW Version; 10 Voltage Regulation; 11 Holdup Time; 12 Compliance (bit 0 LPS, bit 1 PS1, bit 2 PS2); 13 Touch Current (bit 0 low-touch EPS, bit 1 ground pin, bit 2 PE); 14-15 Peak Current1; 16-17 Peak Current2; 18-19 Peak Current3 (2-byte width INFERRED from offsets); 20 Touch Temp; 21 Source Inputs; 22 upper nibble = Hot Swappable Battery Slots (0-4); 23 SPR Source PDP Rating (0-6); 24 EPR Source PDP Rating (0-7).
- **SDB, Status (Table 6-55, 7 B):** 0 Internal Temp (°C); 1 Present Input (bit 1 External Power, bit 2 AC/DC, bit 3 Battery power, bit 4 non-battery internal); 2 Present Battery Input; 3 Event Flags (bit 1 OCP, bit 2 OTP, bit 3 OVP, bit 4 CF/CV); 4 Temperature Status; 5 Power Status (bits 1-5: limited by cable current, insufficient power, insufficient external power, event flags, temperature); 6 Power State Change (1 S0, 2 Modern Standby, 3 S3, 4 S4, 5 S5, 6 G3); 7 LED.
- **SDB, Cable Plug (Table 6-56, 2 B):** 0 Internal Temp; 1 Flags (bit 0 Thermal Shutdown).
- **GBCDB (Table 6-57, 1 B):** 0 Battery Cap Ref (index from 0; 8-255 reserved).
- **GBSDB (Table 6-58, 1 B):** 0 Battery Status Ref (index from 0; 8-255 reserved).
- **BCDB (Table 6-59, 9 B):** 0-1 VID; 2-3 PID; 4-5 Design Capacity (0.1 Wh); 6-7 Last Full Charge (0.1 Wh); 8 Battery Type (bit 0 Invalid reference). 0xFFFF on unknown.
- **GMIDB (Table 6-60, 2 B):** 0 Target (0 = Port/Cable Plug; 1 = Battery); 1 Ref (battery index if target = battery).
- **MIDB (Table 6-61, 5-26 B):** 0-1 VID; 2-3 PID; 4.. Manufacturer String (null-terminated, 0-21 chars). Unsupported returns "Not Supported".
- **PPSSDB (Table 6-62, 4 B):** 0-1 Output Voltage (20 mV; 0xFFFF unsupported); 2 Output Current (50 mA; 0xFF unsupported); 3 Real Time Flags (bit 3 OMF).
- **CCDB (Table 6-63, 4-26 B):** 0 Length (count of codes); 1 reserved; then 2-byte codes.
- **CIDB (Table 6-64, 4-26 B):** 0 char1; 1 char2; 2-3 reserved; 4..25 Country Specific Data (0-22 B).
- **SKEDB (Table 6-65, 24 B):** 0-1 VID; 2-3 PID; 4-7 XID; 8 FW; 9 HW; 10 SKEDB Version (0 and 2-255 reserved); 11 Load Step; 12-13 Sink Load Characteristics; 14 Compliance (bit 0 LPS, bit 1 PS1, bit 2 PS2); 15 Touch Temp; 16 Battery Info (upper nibble = hot-swappable slots); 17 Sink Modes; 18 Sink Minimum PDP; 19 Sink Operational PDP; 20 Sink Maximum PDP; 21 EPR Sink Minimum PDP; 22 EPR Sink Operational PDP; 23 EPR Sink Maximum PDP.
- **ECDB (Table 6-66, 2 B):** 0 Type; 1 Data (zero if unused). Types (Table 6-67, p.214): 1 EPR_Get_Source_Cap (Sink or DRP; reply EPR_Source_Capabilities or Not_Supported); 2 EPR_Get_Sink_Cap (Source or DRP; reply EPR_Sink_Capabilities or Not_Supported); 3 EPR_KeepAlive (Sink; reply EPR_KeepAlive_Ack); 4 EPR_KeepAlive_Ack (Source). 0 and 5-255 reserved.

### 3.2 Chunking rules (6.2.1.2, p.112-115; Table 6-83 p.273)

| Parameter | Value |
|---|---|
| MaxExtendedMsgLen | 260 B |
| MaxExtendedMsgChunkLen | 26 B |
| MaxExtendedMsgLegacyLen | 26 B |

- Chunked = 1 for all Extended Messages when either port supports only chunked. Chunked = 0 for all when both support Unchunked (bit 24 of Source Fixed PDO, bit 23 of RDO) (6.2.1.2.1; Table 6-4 p.115).
- Data Size > 26 B: sent in 26 B chunks; last chunk carries remainder.
- Cable Plugs always send Data Size > 26 chunked. VCONN Source sends only chunked messages to Cable Plugs.
- Chunk Number starts at 0, increments by 1, max 9 (10 chunks). Requests carry the next chunk number wanted; responses carry the chunk returned.
- Request Chunk = 1 for request, 0 for response. A requested chunk (other than chunk 0) is returned only as a response to that request (6.2.1.2.3).
- Data Size is the total block size (INFERRED from Table 6-3 text).
- Retry (6.7.2): Extended <= 26 B retried. Extended > 26 B unchunked not retried. Chunked > 26 B: individual chunks retried. Cable Plugs do not retry.
- Chunk timers (Table 6-68): tChunkingNotSupported 40/45/50 ms; tChunkSenderRequest 24/27/30 ms; tChunkSenderResponse 24/27/30 ms; tChunkReceiverRequest 15 ms; tChunkReceiverResponse 15 ms.

---

## 4. STRUCTURED VDM

### 4.1 Command list (Tables 6-30, 6-32, 6-80; 6.4.4.3)

| Command | Num | SVID field | SOP* used | Initiator (Table 6-80) | Responder | Allowed responses (Table 6-32) | Notes |
|---|---|---|---|---|---|---|---|
| Discover Identity | 1 | PD SID 0xFF00 | SOP and SOP' (Table 6-30) | DFP CN1,6/R; UFP R (note 2) | UFP ACK/NAK/BUSY; Cable Plug ACK | ACK, NAK, BUSY | To SOP only with Explicit Contract (6.4.4.3.1). Source may send to SOP' before Explicit Contract (6.4.4.2). Request has 0 VDOs. ACK returns ID Header, Cert Stat, Product VDO, plus product-type VDO. |
| Discover SVIDs | 2 | PD SID 0xFF00 | SOP / SOP' | DFP O (CN1 when Modal Operation) | UFP ACK/NAK/BUSY | ACK, NAK, BUSY | Returns 2 SVIDs per VDO (6.4.4.3.2) |
| Discover Modes | 3 | any SVID | SOP / SOP' | DFP CN1/O | UFP ACK/NAK/BUSY | ACK, NAK, BUSY | Returns 0-6 mode VDOs (6.4.4.2.4) |
| Enter Mode | 4 | any SVID | SOP* | DFP only | UFP ACK/NAK | ACK, NAK (no BUSY) | Object Position = mode index 1-6. DFP only (6.4.4.2) |
| Exit Mode | 5 | any SVID | SOP* | DFP only | UFP ACK/NAK | ACK, NAK (no BUSY) | Object Position 1-6, or 7 = exit all |
| Attention | 6 | any SVID | SOP only | DFP O, UFP O | none | none | Responder ignores if unrecognised (6.4.4.2.6.2) |
| SVID-specific | 16-31 | any SVID | SOP* (per SVID) | per SVID | per SVID | per SVID | |

A NAK means do not retry (Table 6-32 note). BUSY means retry after tVDMBusy (50 ms).

### 4.2 Responses for unsupported or unknown VDM content

| Condition | Response |
|---|---|
| Unstructured VDM not supported or VID not recognised, DFP/UFP | Not_Supported (Table 6-73, p.237) |
| Unstructured VDM not supported or VID not recognised, Cable Plug | Ignored |
| Structured VDM not supported, DFP/UFP | Not_Supported (Table 6-73) |
| Structured VDM not supported, Cable Plug | Ignored |
| Structured VDM for unrecognised SVID (supports SVDM) | NAK (6.4.4.2, p.151) |
| Structured VDM unrecognised command | NAK (6.4.4.2.6.1) |

### 4.3 Unstructured VDM rules (6.4.4.1, p.150)

- Only with an Explicit Contract. Only DFP initiates. Only UFP or Cable Plug responds.
- Only after the UFP ACKs Enter Mode for the same SVID.
- Exit on Hard Reset.

### 4.4 VDM version rules (6.4.4.2.3, p.152)

- Major 01b = Version 2.x. Minor 00b = 2.0 (pre Rev 3.1 V1.6); 01b = 2.1 (Rev 3.1 V1.6+).
- Products shall support all versions from 1.0 upward for interop.
- A port receiving a higher version than it supports responds using its highest supported version.
- Version agreed from Discover Identity exchange; fixed until Detach.

### 4.5 VDM timers and counters

| Item | Value | Source |
|---|---|---|
| tVDMSenderResponse | 24 / 27 / 30 ms | Table 6-68 |
| tVDMReceiverResponse | 15 ms | Table 6-68 |
| tVDMBusy | 50 ms | Table 6-68 |
| tVDMEnterMode | 25 ms | Table 6-68 |
| tVDMExitMode | 25 ms | Table 6-68 |
| tVDMWaitModeEntry | 40 / 45 / 50 ms | Table 6-68 |
| tVDMWaitModeExit | 40 / 45 / 50 ms | Table 6-68 |
| nBusyCount | 5 (Table 6-70); 6.7.6 says Type-C 2.2 mode entry should use 1 | |
| nDiscoverIdentityCount | 20 (Table 6-70) | |

---

## 5. WHAT A SOURCE MAY SEND TO A SINK (and to a Cable Plug on SOP')

Source columns from Tables 6-76 (control), 6-77 (data), 6-78 (extended), 6-79 (ext-control), 6-80 (VDM), pp.266-271. Sink replies come from the Sink received column.

### 5.1 Control messages

| Message | Source transmit | Sink must reply | If Sink does not support | Notes |
|---|---|---|---|---|
| Get_Sink_Cap | R | Sink_Capabilities (N) | n/a | Note 4 p.268 |
| Get_Source_Cap | NA | n/a | n/a | Source cannot send |
| Get_Source_Cap_Extended | NA | n/a | n/a | |
| Get_Source_Info | NA | n/a | n/a | Sink sends to Source |
| Get_Revision | R | Revision (N) | | |
| Get_Status | R | Status (CN6 / CN15 conditional) | Not_Supported (NS) | Table 6-76 note 6 |
| Get_Country_Codes | CN10 (country authority) | Country_Codes (CN10) | Not_Supported (NS) | Note 10 |
| Get_PPS_Status | NA | n/a | n/a | Sink sends |
| VCONN_Swap | R | Accept / Reject / Wait (CN4 / NS) | Not_Supported (NS) | Note 4 |
| DR_Swap | O | Accept / Reject / Wait (O/NS) | Not_Supported (NS) | |
| PR_Swap | NA (Source-only) | n/a | Not_Supported (NS) | Dual-role only: N (section 8 conflict) |
| FR_Swap | NA | n/a | Not_Supported (NS) | Sink-only signal |
| Ping | O | Ignored (I) | n/a | Timing / keep-alive |
| GotoMin | CN1 / O | Sink reduces current to Min Operating Current (R3) | | Note 2 |
| Soft_Reset | N | Accept | n/a | Source must send on protocol error |
| Hard Reset | N (signalling) | none | n/a | Protocol error or non-response |
| Not_Supported | N | n/a | n/a | Sent in response to unknown messages in PE_SRC_Ready / PE_SNK_Ready (6.8.1) |
| Accept | N | n/a | n/a | |
| Reject | N | n/a | n/a | Source sends for invalid requests (6.9) |
| PS_RDY | CN4 | n/a | n/a | |
| Wait | CN8 / NS | | | |

### 5.2 Data messages

| Message | Source transmit | Sink reply | Notes |
|---|---|---|---|
| Source_Capabilities | N | Request (Sink) | Sent from PE_SRC_Ready; SourceCapabilityTimer (p.131) |
| Request | NA | n/a | Sink-only |
| BIST | N1 | per BIST mode (6.4.3) | Note 1 |
| Sink_Capabilities | NA | n/a | Sink-only |
| Battery_Status | CN2 | none | Note 2 |
| Alert | CN11 / R | Alert received: R / NS | Note 11: when SPR PPS supported |
| Get_Country_Info | CN5 / O | Country_Info (CN5) | Note 5: country authority |
| Enter_USB | CN7 / O | Accept / Wait / Reject (CN7 / NS) | Note 7: USB4 |
| EPR_Request | NA | n/a | Sink-only |
| EPR_Mode | CN9 | EPR_Mode Ack or Exit (CN9) | Note 9: Source operation in EPR |
| Source_Info | CN10 | not a reply | Table 6-76 |
| Revision | N | Revision | |
| Vendor_Defined | O | Structured ACK/NAK/BUSY; unstructured per 6.4.4.1 | |

### 5.3 Extended messages

| Message | Source transmit | Sink reply | Notes |
|---|---|---|---|
| Get_Battery_Cap | R | Battery_Capabilities (CN1) | Battery products |
| Get_Battery_Status | R | Battery_Status (CN1, BSDO) | |
| Get_Manufacturer_Info | R | Manufacturer_Info (R) | |
| Get_Country_Codes | CN10 / NA | Country_Codes (CN10) | |
| Country_Info | CN10 | n/a | |
| Security_Request | CN6 | Security_Response (CN6) | Note 6 |
| Firmware_Update_Request | CN7 | Firmware_Update_Response (CN7) | Note 7 |
| PPS_Status | CN8 (Source) | n/a | Note 8 |
| Status | CN15 / R | n/a | Note 15 |
| Source_Capabilities_Extended | R | none | |
| Sink_Capabilities_Extended | NA | n/a | Sink-only |
| EPR_Source_Capabilities | CN14 / NA | EPR_Request | Note 14 |
| Extended_Control | see 6.13.4 | see 5.4 | |
| Vendor_Defined_Extended | O | per vendor | |

### 5.4 Extended_Control (Table 6-79, p.270)

| Message | Source transmit | Sink transmit | Received by Sink |
|---|---|---|---|
| EPR_Get_Source_Cap | NA | CN1 | CN1 |
| EPR_Get_Sink_Cap | CN1 | NA | NS |
| EPR_KeepAlive | NA | CN1 | NS |
| EPR_KeepAlive_Ack | CN1 | NA | CN1 |

### 5.5 Structured VDM from a DFP (Table 6-80, p.271)

| Command | DFP transmit | UFP responds | Cable Plug (SOP') responds |
|---|---|---|---|
| Discover Identity | CN1,6 / R | R (note 2) | N (receive) |
| Discover SVIDs | CN1 / O | CN1 | NK (receive) |
| Discover Modes | CN1 / O | CN1 | NK (receive) |
| Enter Mode | CN1 / NA | NK (receive) | NK |
| Exit Mode | CN1 / NA | NK (receive) | NK |
| Attention | O | I | I |

### 5.6 Not_Supported and reject rules

| Rule | Source |
|---|---|
| Unrecognised or unsupported message in PE_SNK_Ready or PE_SRC_Ready: Not_Supported, not Soft_Reset | 6.8.1 (p.235) |
| Unexpected message during AMS: Protocol Error. Soft_Reset (non-interruptible AMS), or return to ready state and process (interruptible AMS) | 6.8.1 |
| Request in SPR that cannot be met: Reject. Sink may send a different Request but shall not repeat the same | 6.3.4 |
| Wait: Sink retries after tSinkRequest | 6.3.12.1 |
| NS in receive columns: Not_Supported response when received in ready states | p.265 |

### 5.7 PD 2.0 and 3.x compatibility

- Rev 3.x systems must support Rev 2.0 operation and revert to lowest common revision (2.3 p.59; 6.2.1.1.5 p.110-111).
- Specification Revision in each Message Header; GoodCRC sets revision 01b when responding to a 01b message.
- Cable Plug: Discover Identity sent at Rev 3.0 first; if no ACK or BUSY, VCONN Source may retry with Rev 2.0 (p.111-112).
- Chunking mandated so Rev 2.0 PHYs see messages <= 26 B unless both sides support unchunked (2.3).
- Rev 2.0 message set and per-message Not_Supported behaviour in PD 2.0 are NOT on disk. Any PD 2.0 statement here is INFERRED.

---

## 6. TIMERS AND COUNTERS

Source: Table 6-68 (p.230-231) and Table 6-69 (p.231-232). min / nom / max as printed. Single values are shown as one value. "Used by" from Table 6-69.

### 6.1 Timers

| Timer | Parameter | min / nom / max | Unit | Used by | Section |
|---|---|---|---|---|---|
| tACTempUpdate | | 500 | ms | Active Cable | 6.5.2.2.1 |
| BISTContModeTimer | tBISTContMode | 30 / 45 / 60 | ms | Policy Engine | 6.6.7.2 |
| | tBISTCarrierMode | 300 | ms | | 6.6.7.1 |
| | tBISTSharedTestMode | 1 | s | | 6.6.7.3 |
| | tCableMessage | 750 | µs | | 6.6.14 |
| ChunkingNotSupportedTimer | tChunkingNotSupported | 40 / 45 / 50 | ms | Policy Engine | 6.6.18.1 |
| ChunkReceiverRequest / Response | tChunkReceiverRequest / Response | 15 | ms | | 6.6.18.2 / 6.6.18.3 |
| ChunkSenderRequestTimer | tChunkSenderRequest | 24 / 27 / 30 | ms | Protocol | 6.6.18.2 |
| ChunkSenderResponseTimer | tChunkSenderResponse | 24 / 27 / 30 | ms | Protocol | 6.6.18.3 |
| CRCReceiveTimer | tReceive | 0.9 / 1.0 / 1.1 | ms | Protocol | 6.6.1 |
| | tRetry | 195 | µs | | 6.6.1 |
| | tTransmit | 195 | µs | | 6.6.1 |
| DataResetFailTimer | tDataResetFail | 300 / 400 (two values printed; column unclear) | ms | Policy Engine | 6.6.10.3 |
| DataResetFailUFPTimer | tDataResetFailUFP | 450 / 550 (two values printed) | ms | Policy Engine | 6.6.10.4 |
| | tDataReset | 200 / 225 / 250 | ms | | 6.6.10.2 |
| DiscoverIdentityTimer | tDiscoverIdentity | 40 / 50 (two values printed) | ms | Policy Engine | 6.6.15 |
| HardResetCompleteTimer | tHardResetComplete | 4 / 4.5 / 5 | ms | Protocol | 6.6.9 |
| | tHardReset | 5 | ms | | 6.3.13 |
| NoResponseTimer | tNoResponse | 4.5 / 5.0 / 5.5 | s | Policy Engine | 6.6.6 |
| PSHardResetTimer | tPSHardReset | 25 / 30 / 35 | ms | Policy Engine | 6.6.11.2 |
| PSSourceOffTimer | tPSSourceOff SPR | 750 / 835 / 920 | ms | Policy Engine | 6.6.5.2 |
| | tPSSourceOff EPR | 1120 / 1260 / 1400 | ms | | 6.6.5.2 |
| PSSourceOnTimer | tPSSourceOn SPR | 390 / 435 / 480 | ms | Policy Engine | 6.6.5.3 |
| PSTransitionTimer | tPSTransition SPR | 450 / 500 / 550 | ms | Policy Engine | 6.6.5.1 |
| | tPSTransition EPR | 830 / 925 / 1020 | ms | | 6.6.5.1 |
| SenderResponseTimer | tSenderResponse | 27 / 30 / 33 | ms | Policy Engine | 6.6.2 |
| | tReceiverResponse | 15 | ms | | 6.6.2 |
| SinkEPREnterTimer | tEnterEPR | 450 / 500 / 550 | ms | Policy Engine | 6.6.21.1 |
| SinkEPRKeepAliveTimer | tSinkEPRKeepAlive | 250 / 375 / 500 | ms | Policy Engine | 6.6.21.2 |
| SourceEPRKeepAliveTimer | tSourceEPRKeepAlive | 750 / 875 / 1000 | ms | Policy Engine | 6.6.21.3 |
| SinkPPSPeriodicTimer | tPPSRequest | 10 | s | Policy Engine | 6.6.19.1 |
| SourcePPSCommTimer | tPPSTimeout | 12.0 / 13.5 / 15.0 | s | Policy Engine | 6.6.19.2 |
| SinkRequestTimer | tSinkRequest | 100 | ms | Policy Engine | 6.6.4.1 |
| SinkTxTimer | tSinkTx | 16 / 18 / 20 | ms | Protocol | 6.6.16 |
| SinkWaitCapTimer | tTypeCSinkWaitCap | 310 / 465 / 620 | ms | Policy Engine | 6.6.3.2 |
| SourceCapabilityTimer | tTypeCSendSourceCap | 100 / 150 / 200 | ms | Policy Engine | 6.6.3.1 |
| SwapSourceStartTimer | tSwapSourceStart | 20 | ms | Policy Engine | 6.6.8.1 |
| | tSwapSinkReady | 15 | ms | | 6.6.8.1 |
| VCONNDischargeTimer | tVCONNSourceDischarge | 160 / 200 / 240 | ms | Policy Engine | 6.6.10.1 |
| VCONNOnTimer | tVCONNSourceTimeout | 100 / 150 / 200 | ms | Policy Engine | 6.6.13.1 |
| | tVCONNSourceOn | 50 | ms | | 6.3.11 |
| | tVCONNSourceOff | 25 | ms | | 6.6.13 |
| | tVCONNSwapWait | 100 | ms | | 6.6.4.4 |
| VDMModeEntryTimer | tVDMWaitModeEntry | 40 / 45 / 50 | ms | Policy Engine | 6.6.12.2 |
| VDMModeExitTimer | tVDMWaitModeExit | 40 / 45 / 50 | ms | Policy Engine | 6.6.12.3 |
| VDMResponseTimer | tVDMSenderResponse | 24 / 27 / 30 | ms | Policy Engine | 6.6.12.1 |
| | tVDMReceiverResponse | 15 | ms | | 6.6.12.1 |
| | tVDMBusy | 50 | ms | | 6.6.12.4 |
| | tVDMEnterMode | 25 | ms | | 6.6.12.2 |
| | tVDMExitMode | 25 | ms | | 6.6.12.3 |
| | tDRSwapWait | 100 | ms | | 6.6.4.3 |
| | tDRSwapHardReset | 15 | ms | | 6.6.11.3 |
| | tPRSwapWait | 100 | ms | | 6.6.4.2 |
| | tEnterUSB | 500 | ms | | 6.6.20 |
| | tEnterUSBWait | 100 | ms | | 6.6.4.5 |
| | tFirstSourceCap | 250 | ms | | 6.6.3.3 |
| | tFRSwap5V | 15 | ms | | 6.6.17.1 |
| | tFRSwapComplete | 15 | ms | | 6.6.17.2 |
| | tFRSwapInit | 15 | ms | | 6.6.17.3 |
| | tProtErrHardReset | 15 | ms | | 6.6.11.4 |
| | tProtErrSoftReset | 15 | ms | | 6.6.9.2 |
| | tSoftReset | 15 | ms | | 6.8.1 |
| | tSinkDelay | 5 | ms | | 5.7 |
| | tSrcHoldsBus | 50 | ms | | 8.3.3.2 |

Referenced in text but not in Table 6-68: tSnkNewPower (GotoMin, 6.3.2), NOT READ.

### 6.2 Counters (Tables 6-70, 6-71, section 6.7)

| Counter | Max / Parameter | Value | Section | Behaviour |
|---|---|---|---|---|
| MessageIDCounter | nMessageIDCount | 7 | 6.7.1 | Rolling 0-7. Reset to 0 on Hard Reset or Soft_Reset. |
| RetryCounter | nRetryCount | 2 | 6.7.2 | Retries on CRCReceiveTimer expiry. On failure, Soft Reset. |
| HardResetCounter | nHardResetCount | 2 | 6.7.3 | Hard Reset retries without response. Then assume non-responsive. |
| CapsCounter | nCapsCount | 50 | 6.7.4 | Optional. Source_Capabilities sent after power-up or Hard Reset. |
| DiscoverIdentityCounter | nDiscoverIdentityCount | 20 | 6.7.5 | Discover Identity to Cable Plug without GoodCRC. Reset by VCONN Swap. |
| VDMBusyCounter | nBusyCount | 5 | 6.7.6 | Responder BUSY count. Type-C 2.2 mode entry: use 1. |

---

## 7. DESTRUCTIVE AND DISRUPTIVE MESSAGES

Phone context: a phone is typically Sink / UFP. Effects are from the spec text.

| Message / signal | What it does (spec) | Effect on a connected phone | Section |
|---|---|---|---|
| Hard Reset (signalling) | Both sides return power supplies to default; protocol reset. VBUS to vSafe0V and back. Rp/Rd unchanged. Data role reverts to default per CC resistor (DFP if Rp, UFP if Rd). VCONN re-sourced by Rp port. EPR Mode and all Active Modes exited. Retried up to nHardResetCount. | VBUS drops to vSafe0V then renegotiates from 5 V. Sink must not treat as disconnect (p.237). Phone re-runs Source_Capabilities / Request. | 6.8.3 (p.237-238), 6.3.13, 7.1.5 |
| Soft_Reset | Resets protocol layer, MessageIDCounter, RetryCounter. Power supply shall not change. Explicit Contract renegotiated: Source resends Source_Capabilities (SPR) or EPR_Source_Capabilities (EPR). Failure leads to Hard Reset (SOP) or Cable Reset (SOP'/SOP''). | No power change. Phone renegotiates PD contract. | 6.8.1 (p.235-236), 6.3.13 |
| Data_Reset | Resets USB data. Exits all Alternate Modes both ends and in Cable Plugs. Power-cycles VCONN. DFP becomes VCONN Source. Does not change power or data roles or Explicit Contract. Failure enters ErrorRecovery. Sends Data_Reset_Complete. | VBUS kept. USB data re-enumerates. Alternate Mode exits. | 6.8.2 (p.237), 6.3.14 |
| PR_Swap | Power roles swap. Protocol layers reset. Source goes to Swap Standby. VBUS source changes. Rp/Rd swapped. Reject / Wait allowed. | Phone as Sink can become Source and supply VBUS (reverse charge). | 6.3.10, 7.3.9-7.3.10 |
| DR_Swap | Data roles swap (DFP/UFP). Treated as cable detach/re-attach for data path. Hard Reset if Cable Plug has Active Mode. | Phone (UFP) may become DFP. USB data path changes. | 6.3.9 |
| VCONN_Swap | VCONN source moves. Continuous VCONN during swap. PS_RDY within tVCONNSourceOn (50 ms). | Cable power moves. VBUS and data roles unchanged. | 6.3.11, 7.1.15 |
| FR_Swap | Fast role swap: Sink signals Source via FRS; sink takes over as Source with 5 V. Requires FRS-capable port pair. | Only for FR-capable pairs. | 6.3.19, 7.3.15, Table 6-82 |
| GotoMin | Source reduces Sink to Min Operating Current within tSnkNewPower, to reclaim power. GiveBack-capable Sinks only. | Phone with GiveBack current reduced temporarily. | 6.3.2 |
| Enter_USB | DFP tells UFP to enter USB 2.0 / 3.2 / USB4. Only DFP sends. | Phone (UFP) accepts, waits or rejects. A USB mode entry, not a power change. | 6.4.8 (p.182-184) |
| EPR_Mode Enter / Exit | Enter: Source moves to EPR; Source may go to 28-48 V. Exit: returns to SPR via PR/FR swap or new SPR contract. Sink receiving EPR_Source_Capabilities with an EPR PDO in object positions 1-7 must Hard Reset. | Non-EPR phone gets Hard Reset if it sees EPR PDOs in first 7 objects. EPR entry can raise VBUS to EPR levels. | 6.4.10 (p.184-189), 6.4.10.3 (p.189) |
| Request while in EPR | Source operating in EPR that receives a Request Message shall initiate Hard Reset | Phone sending plain Request in EPR contract causes Hard Reset. | 6.4.2 (p.141) |
| BIST Carrier Mode (0101b) | UUT sends continuous carrier; exits within tBISTContMode (30-60 ms). Only at vSafe5V. | PD messages suspended for tBISTContMode. | 6.4.3.1, 6.6.7.2 |
| BIST Test Data (1000b) | UUT returns GoodCRC, then sends no more Messages except GoodCRC until Hard Reset. | PD communication stops until Hard Reset. | 6.4.3.2 |
| BIST Shared Test Mode Entry (1001b) | UUT offers full Source_Capabilities on all ports of shared-capacity group within tBISTSharedTestMode (1 s). | Source offers full capacity. Phone may see changed caps. | 6.4.3.3.1 |
| BIST Shared Test Mode Exit (1010b) | Exit shared capacity test mode | | 6.4.3.3.2 |
| Firmware_Update_Request / Response | Content defined in external USBPDFirmwareUpdate 1.0 (not on disk). | Not specified here. | 6.5.9 |
| Cable Reset (signalling) | DFP only. Power-cycles Cable Plug VCONN. Exits Active Modes in cable. | Phone affected only if cable is active. | 6.8.4 |

---

## 8. GAPS, CONFLICTS AND NOT-READ ITEMS

| Item | Detail |
|---|---|
| PR_Swap sender | Table 6-5 (p.119) lists "Source or Sink". Table 6-76 (p.266) marks PR_Swap NA for Source-only and Sink-only ports, N only for Dual-Role Power. Treated as Dual-Role only. |
| Hot-swappable battery bits | Table 6-47 (p.179): B16 = Battery 4 ... B19 = Battery 7. 6.4.6.3 (p.181): B16 = Battery 0. Conflict. |
| Revision introduced per message | Not in spec. Only EPR/AVS (Rev 3.1) and SVDM Version 2.x (Rev 3.0) are explicitly versioned. All other entries INFERRED. |
| Product Type numeric codes | Table 6-33 bit values and Tables 6-34 to 6-36 numeric codes not captured. Names only. |
| Vendor_Defined_Extended | 6.5.16 (p.216) NOT READ. |
| EPR AVS Sink APDO | Table 6-20 full bit table NOT READ (pp.140-141). |
| Sink reply tables | AMS sequence tables Table 8-26 to 8-68 (8.3.2, pp.366-546) NOT READ. |
| GotoMin follow-on | Sequence after GotoMin NOT READ. |
| FR_Swap message | 6.3.19 (p.127) prose NOT READ; sending rule from Table 6-5 note 1 only. |
| Table 6-83 extended | Only the three extended-message constants read. Other 6.14 value parameters NOT READ. |
| PD 2.0 behaviour | PD 2.0 spec not on disk. All PD 2.0 claims INFERRED. |
| Mode-specific VDOs | DP, TBT and vendor mode VDO contents are outside this spec. |
| Timer ambiguities | Some timers print two values in pdftotext layout (tDataResetFail, tDataResetFailUFP, tDiscoverIdentity). Not resolved against page images. |
| Layout losses | pdftotext -layout dropped some NA/blank cells in Table 6-76 Accept/Reject rows; cable/VPD entries there are unreliable. |
| Manifest analysis file | `/home/dan/vendors/usb-if/analysis/pd31.txt` is listed in the README as a regenerable text dump. Not opened. |

---

## 9. RE-EXTRACTION COMMANDS (read-only, no file writes)

```
F=/home/dan/vendors/usb-if/sources/USB_PD_R3_1_V1.6_2022-10.pdf
pdftotext -layout -f 119 -l 120 "$F" -    # Table 6-5 control messages
pdftotext -layout -f 128 -l 129 "$F" -    # Table 6-6 data messages
pdftotext -layout -f 130 -l 146 "$F" -    # PDO and RDO layouts
pdftotext -layout -f 149 -l 178 "$F" -    # Vendor_Defined and VDOs
pdftotext -layout -f 178 -l 191 "$F" -    # Battery, Alert, Country, EPR, Source_Info, Revision
pdftotext -layout -f 191 -l 216 "$F" -    # Extended messages and data blocks
pdftotext -layout -f 229 -l 234 "$F" -    # Timers and counters
pdftotext -layout -f 265 -l 272 "$F" -    # Applicability matrices
```
