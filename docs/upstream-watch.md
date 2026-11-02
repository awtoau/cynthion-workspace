# Upstream watch: issues and PRs that matter to us

Scan of 2026-11-02 (Haiku/Sonnet agents, `gh`, read-only). Verdicts: **USE** adopt/lean on, **AVOID** do not repeat or rely on, **WATCH** track, **CONTRIBUTE** gap we could fill (see policy). Items marked UNSURE were not read in depth.

**Policy:** luna and cynthion maintainers refuse LLM-generated pull requests **and issues** (luna `CONTRIBUTING.md`, "AI policy"). Nothing from this workflow is filed or sent upstream; "CONTRIBUTE" means a human would do it. See [`upstream-boundary.md`](upstream-boundary.md).


**Terminology:** upstream's "Sideband" (PHY, port) is the old r0.x second USB port. It is unrelated to our FPGA_ADV one-wire UART, which this repo also calls "the sideband" ([`chips/cynone-sideband.md`](chips/cynone-sideband.md)).

## luna (greatscottgadgets/luna)
| item | state | why it matters | verdict |
|---|---|---|---|
| [#307](https://github.com/greatscottgadgets/luna/pull/307) deterministic endpoint/handler indexes (was `id()`) | merged 2026-10-28 | fixes non-deterministic netlists; output still depends on the yosys build, so pin oss-cad-suite | USE |
| [#298](https://github.com/greatscottgadgets/luna/pull/298) / [#294](https://github.com/greatscottgadgets/luna/issues/294) ACM interface association descriptor | merged / fixed in 0.2.3 | Windows refuses CDC-ACM without an IAD: matters for our composite device | USE |
| [#280](https://github.com/greatscottgadgets/luna/issues/280) ULPI-UTMI translator breaks when ULPI drives the USB domain | open | reset circuit deadlocks; direct hit on our ULPI wiring | AVOID: keep USB domain off the ULPI clock |
| [#281](https://github.com/greatscottgadgets/luna/issues/281) ULPI clock 60 MHz, usb_clock 120 MHz, 240 MHz only for HS | open | maintainer clock guidance | USE (reference) |
| [#276](https://github.com/greatscottgadgets/luna/issues/276) / [#277](https://github.com/greatscottgadgets/luna/pull/277) speed capped to FS with custom UTMI | open / rejected | maintainer wants a constructor parameter, not hardcoded HS | AVOID hardcoding |
| [#301](https://github.com/greatscottgadgets/luna/pull/301) packet.py fan-out direction fix (`USBInTransferManager`) | open PR | small real bug; files `usb2/packet.py`, `usb2/request.py` | adopt if we use the IN transfer manager |
| [#300](https://github.com/greatscottgadgets/luna/issues/300) `USBInterpacketTimer` uses HS timings for LS | open | copy-paste bug, no PR | only if LS is used |
| [#295](https://github.com/greatscottgadgets/luna/issues/295) iso stream IN `frame_finished` never fires | open | unfixed | AVOID iso stream IN |
| [#159](https://github.com/greatscottgadgets/luna/issues/159) ULPI D4/D7 spike on STP | open | ULPI IO not registered | WATCH |
| [#244](https://github.com/greatscottgadgets/luna/pull/244) HyperRAM via ECP5 DQS | merged 2024-05-02 | upstream has a DQS PHY (`psram.py`); UNSURE it fits the W956A8 | reference (DQS is parked) |
| [#278](https://github.com/greatscottgadgets/luna/pull/278) Amaranth 0.5 migration; [#297](https://github.com/greatscottgadgets/luna/issues/297) RFC 61 streams (0.6) | merged / open | migration reference; 0.6 removes `Record` | USE / WATCH |
| [#303](https://github.com/greatscottgadgets/luna/pull/303) USB3 compliance, de-emphasis on ECP5 | merged 2026-08-19 | USB3 only | WATCH |
| luna has **no USB host core** (only `ulpi.py`) | | host mode is on us: see [`aoa-adaptor.md`](aoa-adaptor.md) | |

## apollo, saturn-v, facedancer, luna-soc
| item | state | why it matters | verdict |
|---|---|---|---|
| apollo [PR#67](https://github.com/greatscottgadgets/apollo/pull/67) FPGA_ADV: UART RX replaced by edge counter | merged 2024-05-29 | SAMD11 has no crystal, UART baud drifts: conflicts with our 230400-baud sideband | USE (weighs toward shrinking the sideband) |
| apollo [#116](https://github.com/greatscottgadgets/apollo/issues/116) flash fails if the gateware takes the JTAG pins | open | our DMA JTAG / fast flash may hit it | WATCH |
| apollo [#29](https://github.com/greatscottgadgets/apollo/issues/29) `jtag_init` assumes RESET state | open | handled: `JTAGChain.__enter__` sends `GO_TO_STATE(RESET)` right after `JTAG_START` (`repos/apollo/apollo_fpga/jtag.py:254`), 5 TMS clocks unconditionally (`jtag_tap.c:293`); `tests/test_jtag_start_then_reset.py` | USE |
| apollo [#15](https://github.com/greatscottgadgets/apollo/issues/15) `flash_program` checks no part IDs | open | fixed here: `soc_confirm.part_gate` checks IDCODE and flash JEDEC (`ef4016`) before every configure and flash write; `tests/test_prewrite_part_checks.py` | USE |
| cynthion [#232](https://github.com/greatscottgadgets/cynthion/issues/232) custom bitstream needs a CPU reset delay | open | handled differently: R14/T14 `oe` is low until a byte is accepted and the firmware never speaks first (`serial_line.py`, `target.rs` `NEVER_SPEAKS_FIRST`); `soc_serial_sim.py` pins it | USE |
| cynthion [#185](https://github.com/greatscottgadgets/cynthion/issues/185) 25F/12F device string vs bitstream | closed | packed IDCODE is checked against the build record at build time; see [`lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md) | USE |
| apollo [#108](https://github.com/greatscottgadgets/apollo/issues/108) prefer `force_fpga_offline()` | open | we use it | USE |
| apollo [PR#109](https://github.com/greatscottgadgets/apollo/pull/109) r1.4 detection voltage | merged | board-rev detection | USE |
| saturn-v [#23](https://github.com/greatscottgadgets/saturn-v/issues/23) bootloader fits 2 KB only with the ARM GNU toolchain | open | pin the toolchain | USE |
| facedancer [#173](https://github.com/greatscottgadgets/facedancer/pull/173) Python 3.14 annotations | merged | free-threading not discussed (UNSURE) | USE |
| luna-soc [#48](https://github.com/greatscottgadgets/luna-soc/issues/48) `csr.Register` fails on Python 3.14 | fixed in amaranth-soc `d8b5892` | we need that amaranth-soc commit | USE |
| luna-soc [#17](https://github.com/greatscottgadgets/luna-soc/issues/17) PSRAM/HyperRAM peripheral | open | links Tiliqua's working `psram_peripheral.py` | reference |
| luna-soc [#47](https://github.com/greatscottgadgets/luna-soc/issues/47) outdated USB docs | open | maintainer points at moondancer/facedancer gateware as the reference | reference |

## cynthion, cynthion-hardware, packetry
| item | state | why it matters | verdict |
|---|---|---|---|
| cynthion [#147](https://github.com/greatscottgadgets/cynthion/issues/147) DQS for HyperRAM | open since 2024-07, empty | no upstream DQS result | |
| cynthion [#259](https://github.com/greatscottgadgets/cynthion/issues/259) speed grade 8 vs 6 | closed | parts are not binned: grade does not matter | USE |
| cynthion [#236](https://github.com/greatscottgadgets/cynthion/issues/236) part ID mismatch (25F fitted) | closed | upstream bitstreams are part-specific | AVOID mixing parts |
| cynthion [#251](https://github.com/greatscottgadgets/cynthion/issues/251) / [PR#255](https://github.com/greatscottgadgets/cynthion/pull/255) softcore takes the JTAG lines after reset | closed | reset-delay fix | USE |
| cynthion [#211](https://github.com/greatscottgadgets/cynthion/issues/211) CONTROL on the same hub as TARGET self-captures | open | maintainers: avoid that topology | AVOID (wiring) |
| cynthion [#230](https://github.com/greatscottgadgets/cynthion/issues/230) host mode | closed | only working host example is LUNA in Tiliqua | reference |
| cynthion [#231](https://github.com/greatscottgadgets/cynthion/issues/231) target power-cycling | open | no API; gateware approach endorsed | USE |
| cynthion [#243](https://github.com/greatscottgadgets/cynthion/pull/243) target VBUS control in the analyzer | merged 2025-07 | VBUS switch bits | USE |
| cynthion [#258](https://github.com/greatscottgadgets/cynthion/issues/258) analyzer fails to answer IN in status stage | open | real analyzer capture bug | AVOID until fixed |
| cynthion [#260](https://github.com/greatscottgadgets/cynthion/issues/260) auto speed selection gives invalid groups | open | speed-detection bug | WATCH |
| cynthion [#137](https://github.com/greatscottgadgets/cynthion/issues/137) LS analyzer holds data until 512 B | open | no live LS capture | AVOID relying on it |
| cynthion [#239](https://github.com/greatscottgadgets/cynthion/issues/239) FUSB302B support | closed | only a factory selftest, no driver | CONTRIBUTE (human) |
| cynthion [PR#265](https://github.com/greatscottgadgets/cynthion/pull/265) `cynthion reset` | closed, not merged | closed over LLM authorship | |
| hardware [#18](https://github.com/greatscottgadgets/cynthion-hardware/pull/18) r1.4.0 changes | merged 2024-08 | R110 5.1k pull-up, TARGET_C_VBUS default, second common-mode choke | diff our r1.4 against it |
| hardware [#15/#13/#12/#14](https://github.com/greatscottgadgets/cynthion-hardware/issues) r1.3 errata (1.1 V leak to TARGET A VBUS, PAC zone overlap ~25% error, INITN series R) | closed | PAC1954 and VBUS checklist; UNSURE r1.4 fixed #13 | USE as checklist |
| packetry [#250](https://github.com/greatscottgadgets/packetry/pull/250) device data intermingled on address reuse | merged 2025-08 | affects Android/DJI captures | USE (v0.5.0 has it) |
| packetry [#284](https://github.com/greatscottgadgets/packetry/issues/284) queue worker ENOMEM (usbfs 16 MB) | open | long captures | WATCH |
| packetry [#233](https://github.com/greatscottgadgets/packetry/issues/233) logging stops at saturated HS | closed | HyperRAM fills | AVOID saturated HS capture |
| packetry [#214](https://github.com/greatscottgadgets/packetry/issues/214) / [#226](https://github.com/greatscottgadgets/packetry/issues/226) class decoding / voltage capture | open | UVC/Android decoding; PAC1954 gateware | WATCH |

## Not upstream
- GUH (the vendored host SIE): [apfaudio/guh](https://github.com/apfaudio/guh), BSD-3-Clause, pushed 2026-10-26, 8 commits past our vendored `923c8490` (mostly mass-storage engines). Tiliqua: [apfaudio/tiliqua](https://github.com/apfaudio/tiliqua) (the only working LUNA-lineage host).
- LiteDRAM [`ecp5ddrphy.py`](https://github.com/enjoy-digital/litedram/blob/master/litedram/phy/ecp5ddrphy.py): per-group bitslip knob, DDRDLLA init order, IOLOGIC reset with ECLK stopped (DQS reference, parked).

## Second sweep: older items and open issues of any age (2026-11-02)
First sweep covered May 2025 on. This one read every open issue/PR of any age and sampled closed items before 2025-05 on our topics. Closed items were keyword-sampled, not exhaustively read; UNSURE = title only. Repos: luna, facedancer, cynthion, apollo, cynthion-hardware, saturn-v, luna-soc, packetry.

### luna: device-stack items that matter for the AOA test rig (ZLPs, stalls, descriptors)
| item | state | why it matters to us | verdict |
|---|---|---|---|
| [#127](https://github.com/greatscottgadgets/luna/issues/127) `USBInTransferManager` does not toggle DATA on a ZLP | closed 2021-08 (fix PR #128 closed unmerged) | ZLP DATA-toggle bug in the bulk IN path; mainline status UNSURE | USE: verify the toggle in our fork |
| [#137](https://github.com/greatscottgadgets/luna/issues/137) descriptor request of exactly N*MPS stalls instead of a ZLP | closed 2021-11 | direct ZLP test case; final fix UNSURE ([#264](https://github.com/greatscottgadgets/luna/pull/264) merged 2024-06 passes MPS to GetDescriptor) | USE: ZLP test |
| [#143](https://github.com/greatscottgadgets/luna/issues/143) `usb_device_connect=0` does not disconnect | closed 2024-06 (fixed by [#212](https://github.com/greatscottgadgets/luna/pull/212)) | soft-disconnect for re-enumeration | USE: confirm #212 in fork |
| [#166](https://github.com/greatscottgadgets/luna/issues/166) `CLEAR_FEATURE(ENDPOINT_HALT)` not implemented | closed 2024-07 ([#266](https://github.com/greatscottgadgets/luna/pull/266)) | stall recovery, toggle reset | USE: confirm #266 in fork |
| [#163](https://github.com/greatscottgadgets/luna/issues/163) ECP5 descriptors > 128 B truncated | closed 2024-11 | our composite descriptor may exceed 128 B; gateware fix UNSURE | WATCH |
| [#32](https://github.com/greatscottgadgets/luna/issues/32) wrong `bInterfaceProtocol` for ACM | closed 2020-07 | CDC descriptor pitfall; check our protocol byte | reference |
| [#125](https://github.com/greatscottgadgets/luna/issues/125) internal speed signal never updated in `USBDevice` | open 2021-07 | speed/chirp logic; check our fork | WATCH |
| [#145](https://github.com/greatscottgadgets/luna/issues/145) some ULPI register-write aborts not handled | closed 2021-11 | workaround was writing twice; fix UNSURE | WATCH |
| [#224](https://github.com/greatscottgadgets/luna/issues/224) CDC ACM orphaned packets in the analyzer | closed 2023-11 | resolution UNSURE | reference |
| [#281](https://github.com/greatscottgadgets/luna/issues/281) USB example without `submodules.car` / platform support | open 2024-11 | clock/platform requirements for a custom device | reference |
| [#141](https://github.com/greatscottgadgets/luna/issues/141) Microsoft OS descriptors for WinUSB ([#251](https://github.com/greatscottgadgets/luna/pull/251), [#256](https://github.com/greatscottgadgets/luna/pull/256)) | closed 2021-10 | WinUSB for our vendor bulk interface; UNSURE | USE |
| [#248](https://github.com/greatscottgadgets/luna/pull/248) ULPI waits min Tstart before bus use | merged 2024-05 | ULPI bring-up timing; UNSURE | WATCH |

### facedancer
| item | state | why it matters to us | verdict |
|---|---|---|---|
| [#152](https://github.com/greatscottgadgets/facedancer/issues/152) `USBConfiguration` parse fails with an IAD | open 2025-02 | composite CDC + vendor needs an IAD; BOS also unsupported | AVOID facedancer for IAD composite tests |
| [#157](https://github.com/greatscottgadgets/facedancer/issues/157) cancelled control transfer returns stale data | open 2025-04 | stale response can leak into the next control IN; Cynthion-specific per reporter | WATCH |
| [#114](https://github.com/greatscottgadgets/facedancer/issues/114) control IN limited to libgreat max command size | open 2024-08 | long control IN fails at 4096 B | WATCH |
| [#115](https://github.com/greatscottgadgets/facedancer/pull/115) ZLP bug on CONTROL IN | merged 2024-09 | reference for expected ZLP behaviour | reference |
| [#156](https://github.com/greatscottgadgets/facedancer/issues/156) `max_packet_size_ep0=32` ignored | closed 2025-05 | EP0 sizing | WATCH |
| [#74](https://github.com/greatscottgadgets/facedancer/issues/74) GreatFET One host mode: failed read on endpoint 1 | open 2023-10 | only host-mode reference; unresolved | WATCH |
| [#133](https://github.com/greatscottgadgets/facedancer/issues/133) descriptor redesign in 3.0.5 broke a keyboard | closed 2024-11 | pin versions if facedancer drives tests | WATCH |

### cynthion, apollo, saturn-v, hardware (older)
| item | state | why it matters to us | verdict |
|---|---|---|---|
| cynthion [#185](https://github.com/greatscottgadgets/cynthion/issues/185) LFE5U-25F instead of 12F on r1.4 | closed 2024-09 | changing the device in the platform did not change the bitstream part string | AVOID blind part swap |
| cynthion [#184](https://github.com/greatscottgadgets/cynthion/issues/184) `aux_vbus_en` power routing | closed 2024-08 | `aux_vbus_en` alone feeds TARGET A; AUX to another port needs a second switch | USE (VBUS tree) |
| cynthion [#216](https://github.com/greatscottgadgets/cynthion/issues/216) CLI soft reset | open 2025-03 | maintainer: `apollo reconfigure` does this | USE |
| apollo [#15](https://github.com/greatscottgadgets/apollo/issues/15) `flash_program` does not check part IDs | open 2023-02 | check the part ID ourselves before flashing | AVOID |
| apollo [#18](https://github.com/greatscottgadgets/apollo/pull/18) auto-convert 12F/25F bitstreams | open 2023-06 | bitstreams identical except VERIFY_ID; unmerged | USE manually |
| apollo [#21](https://github.com/greatscottgadgets/apollo/issues/21) "Sideband PHY" unusable on older Cynthions | closed 2024-05 | r0.1-r0.4 only: the old Host/Sideband ports. r1.x replaced them with Control/Aux and an MCU-controlled D+/D- switch for the handoff (martinling), so it does not apply to r1.4. Fixed for r0.3-r0.4 by [PR#66](https://github.com/greatscottgadgets/apollo/pull/66) | not relevant to r1.4 |
| saturn-v [#15](https://github.com/greatscottgadgets/saturn-v/issues/15) FPGA interferes with bootloader USB (older Cynthions) | open 2024-05 | bootloader/FPGA contention | WATCH |
| hardware [#15](https://github.com/greatscottgadgets/cynthion-hardware/issues/15) r1.3 leaks 1.1 V to TARGET A VBUS | closed 2023-12 | r1.3 only; fix is 0 ohm at R114 | reference |

### packetry (older), and capture size / compression
| item | state | why it matters to us | verdict |
|---|---|---|---|
| [#284](https://github.com/greatscottgadgets/packetry/issues/284) queue worker ENOMEM | open 2026-02 | long HS captures hit the usbfs 16 MB cap; raise `usbfs_memory_mb` (reporter got ~700 MB vs ~180 MB) | USE |
| [#175](https://github.com/greatscottgadgets/packetry/issues/175) record overwrites an unsaved capture without warning | open 2024-08 | save before re-recording | AVOID |
| [#174](https://github.com/greatscottgadgets/packetry/issues/174) low speed selected on a full-speed device panics | closed 2024-08 | choose the device's real speed | AVOID |
| [#114](https://github.com/greatscottgadgets/packetry/issues/114) gateware older than Packetry | closed 2024-07 | gateware protocol must match; check `cynthion info` and reflash | USE |
| [#154](https://github.com/greatscottgadgets/packetry/pull/154) `packetry-cli` | merged 2024-07 | headless capture path (`src/cli.rs`) | USE |
| [#185](https://github.com/greatscottgadgets/packetry/pull/185) PcapNG export | merged 2024-09 | one USB_2_0 interface, ns timestamps | USE |
| [#171](https://github.com/greatscottgadgets/packetry/pull/171) faster pcap save | merged 2024-08 | 371 MB: 4 s to 1.7 s | reference |
| [#136](https://github.com/greatscottgadgets/packetry/issues/136) / [#271](https://github.com/greatscottgadgets/packetry/issues/271) CLI capture / Wireshark extcap | open | no live pcap streaming; pcap written after capture | WATCH |

- **Compression: none anywhere between the analyzer and the file.** Analyzer to host: raw 16 KiB reads (`src/backend/cynthion.rs`, `READ_LEN=0x4000`, 4 transfers in flight); no compression in the host reader (gateware side not read: UNSURE, but `repos/cynthion` analyzer sources contain no compression or filtering code).
- Database: `src/database/compact_index.rs` delta-encodes the index with checkpoints; packet bytes are stored raw. pcap/pcapng export is uncompressed (`src/file.rs`).
- Limits in practice: usbfs memory (#284) and the analyzer buffer filling at a saturated bus ([#233](https://github.com/greatscottgadgets/packetry/issues/233)), plus disk space. No hard size cap seen in source (UNSURE).
