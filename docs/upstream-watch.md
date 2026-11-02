# Upstream watch: issues and PRs that matter to us

Scan of 2026-11-02 (Haiku/Sonnet agents, `gh`, read-only). Verdicts: **USE** adopt/lean on, **AVOID** do not repeat or rely on, **WATCH** track, **CONTRIBUTE** gap we could fill (see policy). Items marked UNSURE were not read in depth.

**Policy:** luna and cynthion maintainers refuse LLM-generated pull requests **and issues** (luna `CONTRIBUTING.md`, "AI policy"). Nothing from this workflow is filed or sent upstream; "CONTRIBUTE" means a human would do it. See [`upstream-boundary.md`](upstream-boundary.md).

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
| apollo [#29](https://github.com/greatscottgadgets/apollo/issues/29) `jtag_init` assumes RESET state | open | relevant to DMA JTAG | WATCH |
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
