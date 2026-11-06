# USB MITM on the Cynthion: design

**Status:** design only, 2026-11-02. Nothing built, nothing run on a board, no SoC build. Every number is **UNSURE** unless tagged *measured* (yosys `synth_ecp5`, module alone, 2026-11-02, out-of-context upper bound). No Fmax/closure figures anywhere.
**Index:** [`aoa-adaptor.md`](aoa-adaptor.md) (topologies A/B/C) · [`capture-compression.md`](capture-compression.md) · [`usb-host-options.md`](usb-host-options.md) · [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) · plan [#557](https://github.com/awtoau/cynthion-workspace/issues/557) · capture timestamps [#563](https://github.com/awtoau/cynthion-workspace/issues/563)

## Goal
- Real host (DJI RC, PC) on one PHY, real device (phone) on another, third port taps the traffic.
- Tap = per-transaction ts16 records ([#563](https://github.com/awtoau/cynthion-workspace/issues/563)) through the LZ4 path.
- Split FIFO between the two directions for speed matching / elasticity.
- Scope: interoperability with an owned aircraft; observe and relay. No geofence defeat; no rewriting of flight-relevant payload (awto-dji CLAUDE.md, quoted in [`aoa-adaptor.md`](aoa-adaptor.md)).

## Verdict
- **Physically possible with three independent PHYs**: device-facing = `target_phy` (TARGET-A or C), host-facing = `control_phy` or `aux_phy`, tap = the other one.
- **TARGET-A and TARGET-C are one PHY on one bus**: they cannot be the two MITM sides. This is the constraint that shapes everything.
- **Byte-level cut-through relay works only at high speed (arithmetic below)**; full speed needs a transaction proxy that spoofs handshakes. Plan: tap first, proxy second, HS relay third.
- Cell budget is the hard limit: relay + tap + compressor is about the size of all remaining headroom (table below).

## What exists vs missing
| piece | state | where |
|---|---|---|
| Passive tap, stock analyzer: TARGET-C in / TARGET-A out joined on the one bus, `target_phy` listens, bulk to PC on CONTROL | upstream, separate bitstream, not our SoC | [`hardware.md`](hardware.md) data path 22 |
| Host engine (GUH SIE) on `target_phy`, roles off/regs/host/raw, 128-deep FIFOs, 2 x 512 B SIE FIFOs, CSR + firmware `Host` | I (sim, 50 + 21 checks); first board run enumerated the Pixel and did AOA | [`peripherals/usb_host.py`](../gateware/soc/peripherals/usb_host.py), [`firmware/cynthion-soc/src/usb_host.rs`](../firmware/cynthion-soc/src/usb_host.rs) |
| Device stack (luna_soc CDC-ACM console) | on `aux_phy` only | `gateware/soc/top.py` |
| `UsbPort` on `control_phy` / `aux_phy` | **missing** (takes pins as ports, only instanced on `target_phy`) | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| `device` / `relay` role | **missing** (roles stop at `raw`) | |
| AOA phone model (51/52 x6/53 then 2d00 bulk echo) | I | [`aoa_phone.py`](../gateware/probes/usb_host/aoa_phone.py) |
| UTMI wire model, LUNA device model | I | [`model.py`](../gateware/probes/usb_host/model.py) |
| "Real host" model | GUH SIE is the model; no scripted AOA-host test bench outside the CSR shim sim | [`usb_host_sie_sim.py`](../scripts/soc/usb_host_sie_sim.py) |
| Packet framer, ts16, counters, ring, collapser | **missing** (designed, [#563](https://github.com/awtoau/cynthion-workspace/issues/563)) | [`capture-compression.md`](capture-compression.md) |
| `UTMITranslator` behind `target_phy` in passive mode | **missing** ("register path only" today) | |
| LZ4 compressor / decompressor | I standalone, not in `top.py` | [`gateware/soc/capture/`](../gateware/soc/capture/) |
| HyperRAM block writer, index, CSRs | **missing** | |
| Hardware-timed turnaround data | **none** | |
| Upstream MITM | none (`greatscottgadgets/luna#302` is an open idea, no code); upstream has no host mode ([cynthion#230](https://github.com/greatscottgadgets/cynthion/issues/230)) | [`usb-host-options.md`](usb-host-options.md) section 8 |

## Ports and PHY sharing
| PHY | connectors | independent? | today |
|---|---|---|---|
| `target_phy` | TARGET-A + TARGET-C (one data bus, one PHY, one device at a time) | yes, vs the other two | `UsbPort` roles; host engine |
| `aux_phy` | AUX | yes | CDC-ACM console, `default_usb_connection` |
| `control_phy` | CONTROL | yes, but muxed with Apollo (`apollo_port_sharing`) | analyzer/facedancer bulk to PC; SoC does not instance a device on it |

- Each PHY has its own pins ([`cynthion_r1_4.py`](../gateware/board/cynthion_r1_4.py)); all three ID-read OK ([measured, `usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md)). Clock: FPGA sources each PHY's 60 MHz (`clk_dir='o'`).
- Two assignments:
| | device-facing | host-facing | tap + console | notes |
|---|---|---|---|---|
| **1 (recommended)** | TARGET-A (host role, VBUS out) | CONTROL | AUX (libusb bulk + CDC-ACM composite, already designed in [`aoa-adaptor.md`](aoa-adaptor.md)) | console stays; `vbus control` already passes CONTROL's VBUS to TARGET-A, so the RC powers the phone as with a plain cable; CONTROL is Apollo's (PROGRAM button / takeover evicts gateware; no programming over CONTROL while running) |
| 2 | TARGET-A | AUX (has FUSB302B) | CONTROL (stock analyzer path, Packetry) | console lost from AUX (fallback Apollo UART, conflicts with JTAG) |
- TARGET-C as the phone port works the same (native C cable, `vbus charge` gives Rp); A vs C is a cable choice, not a design change.
- Speeds: UNSURE what the RC runs at (HS or FS). Capture it passively first (step 0).

## Two data-path modes
### R: byte relay (cut-through, same speed both sides)
```
 host-facing PHY (device role)           device-facing PHY (host role)
  ULPI RX bytes --> [FIFO H->D] --> ULPI TX  ... phone
  ULPI TX  <-- [FIFO D->H] <-- ULPI RX  <--- phone
        \__ framer(side=H) __  __ framer(side=D) __/  -> tap
```
- One FIFO per direction, TX starts after a small prefill of the RX stream (cut-through); a packet is never held whole.
- Gateware owns: reset/chirp per side, speed clamp, attach/detach relay, SOF per side. CPU owns nothing in the data path.
- Elasticity: both PHY clocks come from the FPGA PLL (same domain assumed, UNSURE: check [`chips/ecp5/peripheral-clock-audit.md`](chips/ecp5/peripheral-clock-audit.md)). Residual slip: bit-stuffing differences and host crystal +-500 ppm over one packet. Prefill 4-16 B (UNSURE), FIFO 64 B per direction (LUTRAM or one shared DP16KD).
- **Turnaround arithmetic (spec values from memory, verify against USB 2.0 7.1.19 and 8.5; all UNSURE):**
| | bit time | one byte | device must answer in | host gives up after | relay adds (2 hops) |
|---|---|---|---|---|---|
| HS | 2.08 ns | 1 clock (16.7 ns) | 8-192 bit times (<= ~0.4 us) | 736-816 bit times (~1.5-1.7 us) | PHY latency + prefill, ~tens of clocks per hop (~0.3 us?) -> **fits** |
| FS | 83.3 ns | 40 clocks (667 ns) | 2-7.5 bit times (<= ~0.63 us) | 16-18 bit times (1.33-1.5 us) | >= 1 byte (667 ns) per hop -> 1.33 us+ -> **does not fit** |
- FS conclusion: a byte relay cannot be built from ULPI (the PHY hands over whole bytes, FS byte = 40 clocks). FS needs mode P. Real FS hubs repeat at bit level (~1-4 bit times); we cannot.
- Measure forwarding latency: sim cycle counts first, then a per-record `fwd_latency` byte (below) on hardware.

### P: transaction proxy (store-and-forward, handshake spoofing)
```
 host ---> [device-side engine on host-facing PHY] <== split FIFO (1 KiB / dir) ==> [GUH host engine on target_phy] ---> phone
              gateware: token decode, ACK/NAK/NYET                 CPU: control transfers, policy, AOA
```
- Host-facing PHY runs a device engine answering the real host from FIFO state **in gateware** (FS answer window ~37 clocks, CPU cannot).
- Device-facing PHY runs the existing host engine; a pump polls the phone's bulk IN continuously into FIFO D->H and drains FIFO H->D with bulk OUT.
- **Split FIFO:** one DP16KD (2 KiB) split 1 KiB per direction: holds one HS bulk packet (512) with double buffering, or one 1024 B HS packet single-buffered. Existing 2 x 512 B SIE FIFOs in `usb_host` may serve as the D-side half (saves BRAM; UNSURE).
- **What must be spoofed (proxy owns the handshake, not the phone):**
| host sees | proxy does |
|---|---|
| SETUP | always ACK (cannot NAK); CPU replays it on the phone |
| control data/status IN | NAK until CPU has the phone's reply |
| bulk IN | NAK until FIFO D->H has a packet; then data; retain until host ACK |
| bulk OUT | ACK only after the packet is queued; NAK when FIFO H->D full (or NYET after ACK in HS) |
| PING (HS bulk OUT) | ACK if a max packet fits, else NAK |
| data toggle | each side tracks its own (GUH takes the caller's toggle); proxy resyncs |
| SOF / uSOF | each side generates its own; frame numbers not locked; **isochronous not supported** |
| bus reset | host reset does **not** reset the phone after first enumeration: answer from cached descriptors |
| error | phone STALL/timeout after an early ACK -> STALL the next transaction on that endpoint (data already acked cannot be un-acked) |
- Pre-enumerate the phone before presenting to the host: avoids the host's SET_ADDRESS/recovery timing while the phone chirps (bus reset wait bound is 63 ms, [`aoa-adaptor.md`](aoa-adaptor.md)).

### Speed matching
- **Clamp, do not translate (first version):** operate both sides at the lowest common speed. Host-facing side attaches first and learns the host's speed (HS if it answers chirp); the device-facing side then chirps or does not, to match. Phone at FS behind an HS-capable RC is fine (host-facing side just presents FS).
- Result: always equal speeds; R for HS/HS, P for FS/FS. No packet splitting, no descriptor rewrite.
- True HS<->FS translation (keep HS on one side): rewrite bMaxPacketSize and bulk endpoint descriptors (512 vs 64), split/merge packets through the 1 KiB FIFOs. Later, mode P only; not planned. LS and hubs/split transactions: out of scope.

## Attach, detach and AOA
- AOA sequence from the RC: `51` GET_PROTOCOL (IN), `52` x6 SEND_STRING (OUT), `53` START (OUT); phone detaches, returns as `18d1:2d00/2d01`; RC re-enumerates ([`aoa-adaptor.md`](aoa-adaptor.md)).
- On `53` the proxy must: see device-side disconnect (host engine `HostDisconnect`/line state), drop the host-facing pull-up (soft disconnect), re-enumerate the phone, then re-attach host-side. Re-attach timeout the RC tolerates: UNSURE (the phone-side bound used in `aoa_probe.py` is 5 s).
- Mode R relays attach/detach/reset line states; the phone's detach must drive a host-side soft disconnect.
- **CPU involvement (mode P):**
  - EP0: SETUP bytes arrive from the host-facing engine; firmware runs the same transfer through `Host::control()`, returns the data stage. Descriptors cached after pre-enumeration.
  - `51`: forward, or answer locally (protocol 2) when no phone is attached. `52`: forward and record the strings. `53`: forward, then run the detach/re-attach sequence.
  - Bulk pump and FIFO moves: gateware (CPU only sets endpoint numbers and toggles).
  - Policy hooks (drop, delay, rewrite) default off; each marks the tap record `modified`.
- **CPU involvement (mode R):** enable/speed clamp/status only. Optional later: in-flight byte substitution on a descriptor IN packet (fixed offset, CRC16 recomputed in the relay path, ~40 LUT UNSURE).

## Tap record (per transaction, builds on [#563](https://github.com/awtoau/cynthion-workspace/issues/563))
- [#563](https://github.com/awtoau/cynthion-workspace/issues/563) owns the base record (type, length, status, ts16, rollover, overflow); this section adds only the MITM fields.
- One record per transaction (token + data + handshake), ts16 at token start, 60 MHz tick (wraps 1.09 ms; `NONE` rollover record as upstream).
```
 b0     type (txn / event 0xFF / repeat / drop-marker) | side (H or D) | speed(2)
 b1     handshake (ACK NAK STALL NYET none err) | data PID (DATA0/1/2/M) | spoofed | modified
 b2-3   addr(7) | ep(4) | dir(2) packed
 b4-5   ts16
 b6-7   payload length
 b8     fwd_latency: clocks from host-side packet end to device-side packet start (0 if spoofed)
 b9..   payload (control SETUP always; bulk payload truncated to N bytes, N CSR)
```
- Header ~9 B per transaction vs 4 B per packet upstream (3 packets = 12 B+); UNSURE overall win, depends on payload truncation. All field widths UNSURE until [#563](https://github.com/awtoau/cynthion-workspace/issues/563) fixes its record.
- Source: RX streams of both PHYs only (each packet exactly once: host->proxy on side H, phone->proxy on side D); the PHY's own TX is not re-tapped. In mode P, `spoofed` marks proxy-generated handshakes.
- Counters (NAK, SOF, per PID/ep) count every packet **before** the optional repeat collapser; a NAK storm on bulk IN while the pump waits is expected in mode P, so the collapser is more useful here than in [#563](https://github.com/awtoau/cynthion-workspace/issues/563)'s passive case.
- Path to PC: framer -> counters -> [collapse] -> ring (RV sample) -> LZ4 compressor -> 1 KiB blocks -> HyperRAM -> AUX bulk (PC decodes LZ4 in software; the gateware **decompressor is not needed** for the PC path, saves its cells).
- Full-buffer policy `stall | drop` as in [#563](https://github.com/awtoau/cynthion-workspace/issues/563); in relay mode `stall` would delay forwarding, so **only `drop` is allowed in mode R** (a tap must never throttle the link).

## Cell and BRAM estimate
Budget: 24288 LUT4-equivalent (COMB), 56 DP16KD. Baseline: brief says ~84% COMB (~3.9k free, UNSURE); [`aoa-adaptor.md`](aoa-adaptor.md) recorded 80% -> 91% after `usb_host` (~2.2k free). The two disagree; re-measure with `./dev.py metrics report` before committing to a design. Routing gets hard above ~75% logic.

| block | LUT4 | CCU2C | FF | DP16KD | source |
|---|---|---|---|---|---|
| `usb_host` (already in the SoC) | 1805 | 222 | 856 | 2 | *measured*, `soc_peripheral_area.py` |
| `ulpi_window` (scale) | 106 | 21 | 122 | 0 | *measured* |
| `stream_buffer_async` depth 16 | 39 | 6 | 57 | 0 | *measured* |
| LZ4 compressor, window 1024 | 712 | 196 | 621 | 4 | *measured*, `capture_codec_area.py` |
| LZ4 decompressor (not needed for PC path) | 512 | 118 | 124 | 1 | *measured* |
| second device controller (luna_soc eptri) on `control_phy` | 1500-2500? | | | ~3 | UNSURE; **measure first** |
| device engine (token decode, handshakes) for mode P, if not eptri | 400-900 | | | 0 | UNSURE |
| relay engine, 2 ports (mode R) | 600-1200 | | | 0 | UNSURE (aoa doc: 200-400 per PHY shim) |
| framer + ts16 + counters + 16-bit record packer | 300-600 | | | 0 | UNSURE |
| ring (32 x 16 B) + event log | 100-200 | | | 1 | UNSURE |
| split FIFO 2 KiB | ~100 | | | 1 | UNSURE |
| `UTMITranslator` passive on `target_phy` | 100-300 | | | 0 | UNSURE |
| HyperRAM block writer + index + CSRs | 300-600 | | | 0-1 | UNSURE |

- Compressor in COMB terms ~1100 (712 + 2 x 196, UNSURE mapping to COMB).
- Rough totals (UNSURE): tap without compressor 0.5-1.1k; + compressor ~1.6-2.2k; proxy path adds 1.9-3.4k; relay path adds 0.6-1.2k. Proxy + tap + compressor does **not** fit at 91% and is marginal at 84%.
- BRAM: ~36 placed now (UNSURE, [`aoa-adaptor.md`](aoa-adaptor.md)); + split FIFO 1 + ring 1 + compressor 4 + device controller ~3 -> ~45 of 56 (UNSURE).
- Levers, in order: ship tap uncompressed first; compress only (no decompressor); reuse SIE FIFOs as the D-side half of the split FIFO; drop the packet ring (counters + event log only); mode R instead of P when the link is HS; build a MITM-only bitstream (no flash/IO peripherals not needed), see [`soc-size-review.md`](soc-size-review.md).
- Measurement plan (no SoC build needed): run `scripts/soc/soc_peripheral_area.py --only <module>` for each new module as it lands; `scripts/soc/capture_codec_area.py` for codec variants; real delta only from the SoC with and without the block, one run each, `--no-parallel-refine` ([`aoa-adaptor.md`](aoa-adaptor.md) finding).

## Risks
- **FS cannot be relayed at byte level** (above): FS needs mode P, which is the bigger design.
- **Cell budget**: proxy + tap + compressor overflows at 91%. Decide the bitstream (MITM-only vs SoC) before building.
- **CONTROL is Apollo's**: PROGRAM/takeover evicts the host-facing side mid-session; no programming over CONTROL while MITM runs. `apollo_port_sharing='advertising'` must be honoured when the SoC owns the port.
- **Power**: the RC must feed the board and the phone (phone charge draw 450 mA at 5 V measured, [`aoa-adaptor.md`](aoa-adaptor.md)); PAC1954 gating on every close. RC current limit UNSURE.
- **RC behaviour unknown**: speed, re-attach timeout, strict turnaround, hub assumptions, whether it tolerates NAK on first IN. Capture it first (step 0).
- **Early-ACK semantics** change error behaviour (mode P): errors surface a transaction late.
- **PHY TX under-run** in mode R: NXT pacing + bit stuffing; prefill depth is a guess until simulated against NXT patterns and measured.
- **Isochronous, split transactions, hubs, LS**: not supported; DJI traffic class unknown (bulk assumed from AOA).
- **Clocking**: two PHY clocks from one PLL, but different pad phases; shared ts16 counter assumes one domain (UNSURE).
- **Tap bandwidth**: saturated HS bus is 40-50 MB/s of bytes ([`capture-compression.md`](capture-compression.md)); AUX CDC measured ~195 Mbps; LZ4 ratio on real traffic unmeasured; policy `drop` under load.
- **Router**: default parallel-refine router diverges at this utilisation ([`aoa-adaptor.md`](aoa-adaptor.md)); every added block risks the build.

## Test plan
- **Sim first, no hardware.** Harness style: `scripts/soc/*_sim.py`, registered in `scripts/soc_sims.py`.
- Bench: `[GUH host (RC model)] --UTMI wire-- [proxy host-facing] ... [proxy device-facing] --UTMI wire-- [aoa_phone]`; models from [`model.py`](../gateware/probes/usb_host/model.py) and [`aoa_phone.py`](../gateware/probes/usb_host/aoa_phone.py).
- **First run is the control:** the same checks on the direct host<->phone wire (no proxy) must pass before any proxy result counts.
- Checks:
  1. enumeration through the proxy: descriptors byte-equal to the direct run
  2. AOA: 51 -> protocol 2; 52 x6 string sums/lengths match; 53 -> phone detaches, host-side sees disconnect + reconnect as `18d1:2d00`
  3. bulk echo through the proxy: 31 B, 512 B, ZLP, both directions, toggle sequence
  4. speed clamp: FS-only host with HS-capable phone, and the reverse
  5. NAK path (mode P): host IN before data -> NAK, then data; OUT with full FIFO -> NAK/NYET
  6. IN/NAK storm: tap counters equal packet totals; ring not overrun
  7. tap records: one per transaction, ts monotonic mod 2^16, rollover record, LZ4 round trip bit-exact (reuse `capture_codec_ref.py`)
  8. mode R forwarding latency in clocks (token and data), against the arithmetic table; FS relay expected to **fail** the budget (confirms why P exists; a check of the new mode, not a negative control for an old one)
  9. reset: host bus reset after first enumeration does not reset the phone
- Then hardware (not now): passive tap of RC<->phone on the shared bus, compared with the stock analyzer + Packetry capture of the same session.

## Build order (smallest first)
0. **Passive tap on the shared bus**: `UTMITranslator` behind `target_phy`, framer, ts16, counters, CSR/RV readout, no compression. Observe RC<->phone on TARGET-C/A (A-to-C cable). Gives RC speed and timing. Sim: [#563](https://github.com/awtoau/cynthion-workspace/issues/563) checks.
1. Tap through compressor to AUX bulk (compress only), `drop` policy.
2. Measure a second device controller on `control_phy` (cells, BRAM) and instantiate `UsbPort` on `control_phy` with a `device` role. Decides whether mode P fits.
3. Mode P, same speed, EP0 + one bulk pair, CPU-assisted control passthrough, sim with AOA phone (checks 1-3, 9).
4. AOA detach/re-attach relay (check 2 full), pre-enumeration cache.
5. Speed clamp (check 4) and FS/FS via mode P.
6. Mode R (HS/HS) with latency measurement in sim, only if mode P's CPU path is too slow or too big.
7. Hardware bring-up: tap-only, then P with the Pixel as device and the PC (or RC) as host.
8. Later: HS<->FS translation, byte-substitution hook.

## Open questions
- Tracked in [#567](https://github.com/awtoau/cynthion-workspace/issues/567).
1. Assignment 1 (host on CONTROL, tap + console on AUX) acceptable, given CONTROL is Apollo-shared?
2. RC speed and enumeration timing: capture first (step 0), or already known?
3. Is a MITM-only bitstream acceptable (drops other SoC peripherals) if the budget requires it?
4. Is HS<->FS translation ever needed, or is clamping enough?
