# USB host mode on Cynthion at 480 Mbps

- Status: proposal; first increment built (engine vendored, enumerates in simulation). No hardware exercised.
- Issues: [#105](https://github.com/awtoau/cynthion-workspace/issues/105) (keep open), [#101](https://github.com/awtoau/cynthion-workspace/issues/101) (duplicate of #105), [#120](https://github.com/awtoau/cynthion-workspace/issues/120), [#125](https://github.com/awtoau/cynthion-workspace/issues/125), [#115](https://github.com/awtoau/cynthion-workspace/issues/115).
- Target: LFE5U-12F (24288 LUT, 56 BRAM), Cynthion r1.4.
- Linux needs a driver for any bespoke 480 Mbps interface; the full-speed OHCI route is in [../linux-on-cynthion/ANALYSIS.md](../linux-on-cynthion/ANALYSIS.md).
- Superseded text from earlier drafts: [history/soc-usb.md](history/soc-usb.md).

## 1. Findings

- LUNA has no host stack (device-only by construction). The ULPI/PHY layer already exposes every host control signal.
- The board can host (VBUS sourcing, discharge, current monitor, CC control, HS PHY).
- `apfaudio/guh` ("Gateware USB Host", BSD-3-Clause, Amaranth, builds on LUNA) is the only credible base: enumerates HS (thumbdrives) and FS (MIDI/HID) in pure gateware.
- Host side of the HS chirp handshake is the hard part; GUH `reset.py` has it.

## 2. LUNA

- `repos/luna/luna/gateware/usb/usb2/`: `device.py`, `control.py`, `endpoint.py`, `request.py`, `transfer.py`, `descriptor.py`, `reset.py`, `packet.py`. No host, root hub, SOF generator, scheduler, pipe or enumeration code.
- `device.py:382` drives `dm_pulldown`/`dp_pulldown` to 0: host mode is a deliberate non-goal.
- `packet.py` reuse for host:
  - Yes: `USBHandshakeDetector`, `USBDataPacketReceiver`/`Deserializer`, `USBDataPacketGenerator`, `USBDataPacketCRC`, `USBInterpacketTimer` (HS/FS/LS turnaround constants, e.g. `_HS_TX_TO_RX_TIMEOUT = 92` ULPI cycles).
  - Partly: `USBHandshakeGenerator` (host only sends ACK).
  - No: `USBTokenDetector` (host must generate tokens).
- `reset.py` `USBResetSequencer` is device-side chirp only (`DEVICE_CHIRP`, `AWAIT_HOST_K`/`IN_HOST_K`/`AWAIT_HOST_J`/`IN_HOST_J`, 3 K/J pairs).
- `luna/gateware/interface/ulpi.py`: `op_mode` (incl. `RAW_DRIVE`), `xcvr_select`, `term_select`, `chrg_vbus`/`dischrg_vbus`, OTG register 0x0A (`id_pullup`, `dp_pulldown`, `dm_pulldown`, `dischrg_vbus`, `chrg_vbus`, `use_external_vbus_indicator`). PHY layer needs no work.
- `import luna` resolves to the interpreter's `site-packages/luna`, not `repos/luna`; both device-only.

## 3. Board (`repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`)

- `control_phy`, `aux_phy`, `target_phy`: full ULPI, 8-bit data, FPGA-driven clk. `dir` is turnaround, not role.
- VBUS to TARGET-A: `target_c_vbus_en` (K5), `control_vbus_en` (L1), `aux_vbus_en` (L2) each switch that port's VBUS through; any one makes TARGET-A a source. `target_a_discharge` (K4). `control_vbus_in_en` / `aux_vbus_in_en` for input shutoff.
- `power_monitor` (I2C V/I); `target_type_c` / `aux_type_c` = FUSB302B (not TUSB322I) with `int`/`fault`.
- No ID pin (Type-C; role via CC).
- Unverified: 500/900 mA availability; whether CONTROL VBUS switch delivers to TARGET-A (`control_vbus_in_en` is `PinsN`, post-configuration state not traced).
- No `*_vbus_en` pin is driven anywhere in the SoC today.

## 4. GUH

- Pinned `923c8490` (fixes `apfaudio/guh#1`: earlier `guh/periph/msc.py`, `dma.py`, `util/gearbox.py` carried CERN-OHL-S-2.0). Vendored at `gateware/probes/usb_host/guh/` (`sie.py`, `reset.py`, `types.py`, byte-identical, licence beside them).
- No releases, one author, "interfaces will change", declines contributions: vendor, do not depend.
- Examples build for `CynthionPlatformRev1D4` in GUH's CI (`midi_host`, `keyboard_host`, `msc_host`); they hard-wire `control_vbus_en` high, which is the correct pin on r1.4.
- 27 GUH tests pass locally incl. host-vs-simulated-device asserting `detected_speed == HIGH`.

| Module | Lines | Take? |
|---|---|---|
| `usbh/sie.py` | 673 | yes: transaction engine |
| `usbh/reset.py` | 215 | yes: host half of HS chirp |
| `usbh/types.py` | - | yes |
| `usbh/enumerator.py` | 479 | no: discards descriptors (keeps `bMaxPacketSize0`, one endpoint number, one `wMaxPacketSize`), parser specialised to one interface class at synthesis, address `0x12` / config `1` hard-coded (`enumerator.py:64, 108, 122-129, 441-446`); +830 LUT |
| `usbh/descriptor.py` | 224 | no |
| `engines/*` (msc 743, keyboard 163, midi 133) | - | no: fixed-function classes |
| `periph/msc.py` | - | no: block device (`cmd_lba`, `cmd_blocks`, `cmd_dir`, `capacity`, `block_size`), not a transfer interface |
| `periph/dma.py` | - | later: tier two, 4.4 |

- ~1200 lines Amaranth taken. Vendoring per [upstream-boundary.md](upstream-boundary.md).
- Gaps: hubs enumerate but not devices behind them; no split transactions; error handling incomplete (watchdog recommended, babble/STALL/retry/toggle resync); three class engines only.
- Other prior art: GSG `cynthion#230` / `#174` closed as "deferred", pointing at Tiliqua PR #65 (FS-only ancestor); no `uhc_*` driver for any FPGA soft core in Zephyr (`uhc_dwc2.c`, `uhc_max3421e.c`, `uhc_mcux_*`, `uhc_virtual.c` = `native_sim` loopback); daisho archived, SuperSpeed; LUNA forks all device-only. SpinalHDL OHCI (4.7) is FS/LS only.

## 5. Area and timing (synthesis + PnR only)

`scripts/usb_host_area.py` ([usb_host_area.py](../scripts/soc/usb_host_area.py)): LFSR-driven scaffold, `SocClocks(sync_mhz=60)`, `fifo_depth=512` (a 512 B HS bulk packet needs it; GUH default is 64), baseline (33 LUT, 24 FF) subtracted.

| build | LUT | FF | BRAM | LUTRAM | fmax (ULPI clk) |
|---|---|---|---|---|---|
| baseline (CRG + LED) | 33 | 24 | 0 | 0 | 398.72 MHz |
| `USBSIE(bus=target_phy, fifo_depth=512)` | 2113 | 458 | 0 | 96 | 125.55 MHz |
| + `USBHostEnumerator` + parser | 2943 | 552 | 0 | 128 | 129.22 MHz |

- Delta: SIE 2080 LUT / 434 FF / 0 BRAM / 96 LUTRAM; SIE + enumerator 2910 LUT.
- Both 512x8 FIFOs land in LUT RAM (96 of 3036 `TRELLIS_RAMW`).
- Whole GUH examples for comparison: `keyboard_host` 2184 LUT, `midi_host` 2244, `msc_host` (HS) 4103 LUT / 1 BRAM.
- SoC today (`top.tim`, LFE5U-12F, CABGA256, speed 8): 12201 LUT4 (50%), 159 LUTRAM. BRAM: see [chips/ecp5/bram-budget.md](chips/ecp5/bram-budget.md).
  - BRAM: 49 blocks for the current 64x2 cache geometry per the `gateware/soc/top.py` comment above `CACHE_SETS` (43 for 64x1, 47 for 128x1), and `DP16KD` 49 in [soc-review-baseline.json](soc-review-baseline.json) (`432d29b`). No recent build report under `tmp/`: current figure UNVERIFIED.
  - Cache geometry: 64 sets x 2 ways x 64 B = 8 KiB per cache in `top.py` `CACHE_SETS`/`CACHE_WAYS` (applies); `cpu.py` literal defaults are 128x1. Canonical: [chips/vexiiriscv-cpu.md](chips/vexiiriscv-cpu.md).
  - + SIE: ~14300 LUT (59%); + SIE and enumerator ~15100 (62%); + 200-400 LUT CSR/FIFO shim. BRAM untouched.
- fmax (SoC): `sync` 71.58 MHz (constraint 60), `usb` 94.30 MHz (60), `jtck` 161.08 MHz (20). Host belongs in `usb`. Clock plan: [soc-clocking.md](soc-clocking.md).
  - Standalone fmax is not in-situ: at ~60% occupancy routing dominates (`18c1fa5`: 13.64 of 16.45 ns), and placement varies ~9 MHz ([debris/scripts/soc_timing_sweep.py](../debris/scripts/soc_timing_sweep.py)). In-situ number is the first next step.
  - After `18c1fa5` the Wishbone round trip terminates at `RegisteredResponse` (`gateware/soc/bus/wishbone_pipe.py:96`): another subordinate adds one comparator and one ACK-gather leaf, provided its ACK is combinationally cheap.
- Projected host+device in one bitstream: ~18k LUT / ~45 BRAM = addition, not synthesis.

## 6. Options and verdict

| option | what | verdict |
|---|---|---|
| A | vendor GUH, gateware class engine streams to UART/CPU/PSRAM | proves fabric works, leads nowhere |
| B | GUH SIE behind a CSR/FIFO peripheral; firmware issues transfers | **chosen**; new work, not a port |
| C | Zephyr UHC driver on the RISC-V core over B | superset of B, later; needs Zephyr bring-up on VexiiRiscv, RAM, three-layer debugging |

- Do not implement xHCI; a purpose-built register interface is cheaper (GUH says "host engine", not "controller").
- Walk away if the need is: spec-compliant general host (hubs, splits, isochronous, error recovery: use a Pi), USB 3.x, guaranteed sustained streaming (480 Mbps line rate is not payload; measure), no tolerance for a single-maintainer dependency, or capture only (analyzer is better).
- Sweet spot: one known device at 480 Mbps on owned hardware, stack visible; MITM with a real host on one port and a real device on the other.

## 7. Attaching to the SoC

### 7.1 `target_phy` ownership ([#120](https://github.com/awtoau/cynthion-workspace/issues/120), [#125](https://github.com/awtoau/cynthion-workspace/issues/125))

- `target_phy` is the only candidate: `aux_phy` carries the CDC-ACM console, `control_phy` is shared with Apollo.
- `platform.request("target_phy")` may be called once; `gateware/soc/top.py` calls it and drives `clk`, `rst`, `stp`, `data.o`, `data.oe` from `UlpiRegisters`. No mux point.
- `ulpi_window.py:250` hard-wires `data_oe = ~dir_i`; a host receiving holds `dir` high, so the window's 4096-cycle (68 us) timeout fires (working system reports broken PHY).
- `target_phy.rst` is tied to `ResetSignal("usb")`: a host stack cannot re-initialise the PHY.
- Fix is re-parenting, not arbitration. `UTMITranslator` (instantiated by `USBSIE`, `guh/usbh/sie.py:346`) already holds a `ULPIRegisterWindow` and `ULPIRxEventDecoder` (`ulpi.py:805-808`).
  - Its docstring promises `address`, `read_data`, `write_data`, `manual_read`, `manual_write` (`ulpi.py:683-687`); `__init__` (`ulpi.py:718`) does not create them.
  - `ULPIControlTranslator` ties `register_window.read_request` to 0 (`ulpi.py:524`) with a free `m.Else()` at `ulpi.py:519` for manual access.
  - Implementing the documented ports makes `UlpiRegisters` a client; candidate for [upstreamable-patches.md](upstream-boundary.md).
- #125: the RX-CMD status peripheral taps the translator outputs (`line_state`, `vbus_valid`, `session_valid`, `session_end`, `rx_error`, `host_disconnect`, `id_digital`; `ulpi.py:694-697`), one per translator, no bus access.
- Host and analyzer share one translator: analyzer attaches a bare `UTMITranslator` in non-driving mode (`repos/cynthion/.../analyzer/top.py:277-278, 391-394`); `USBSIE` exposes `self.utmi`, `USBAnalyzer` consumes UTMI.

### 7.2 Clocking

- GUH is single-domain (`guh/periph/msc.py:62` warning); no `AsyncFIFO` in GUH.
- `sync` and `usb` are both 60 MHz from different PLL outputs (CLKOP, CLKOS; `repos/apollo/apollo_fpga/gateware/variable_clock.py:295-330`), so nextpnr treats them as unrelated; `usb -> <async>` is the worst cross-domain delay at 11.83 ns.
- Chosen: host in `usb`; four-phase handshake (`ulpi_window.py:235-311`) for transfer registers, `StreamBuffer` over `AsyncFIFOBuffered` (`gateware/soc/peripherals/stream_buffer.py:96`) per byte stream.
- Rejected: `DomainRenamer({"usb": "sync"})` (GUH tests do this, `tests/test_integration.py:39-40`): +2100 LUT in the domain with 19% margin that carries the CPU.

### 7.3 Register interface

Transfer register plus FIFO port, matching `USBSIEInterface` (`guh/usbh/sie.py:47-72`: `dev_addr`/`ep_addr`/`type`/`data_pid`, strobe `start`, poll `idle`, two 8-bit streams). Not a descriptor ring.

BUILT (simulation only), differently from the table below: byte map, FIFO tags, roles and trap handling are in [chips/usb3343-ulpi-phy.md](chips/usb3343-ulpi-phy.md#role-mux-and-byte-stream-on-target_phy-usb_host-window); window is 0x20 bytes at 0xf0001000, IRQ 10, `peripherals/usb_host.py`. The table is the original proposal.

Proposed window `usb_host` at 0xf0001000, size 0x100 (free, aligned, inside the uncached PMA region `base=f0000000 size=10000000 main=0`, `gateware/soc/cpu/cpu.py:110-113`; INTC is at 0xf0000c00). Own `decoder.add()` (needs a 32-bit port, not the 8-bit `board` CSR decoder).

| off | name | acc | contents |
|---|---|---|---|
| 0x00 | `CTRL` | rw | `enable`, `bus_reset` (strobe), `sof_enable` |
| 0x01 | `ADDR` | rw | `dev_addr[6:0]` |
| 0x02 | `EP` | rw | `ep_addr[3:0]`, `type[1:0]` (SETUP/IN/OUT), `data_pid` |
| 0x03 | `START` | w | strobe: latch and run one transaction |
| 0x04 | `STATUS` | r | `idle`, `reset_active`, `disconnected`, `speed[1:0]` |
| 0x05 | `RESP` | r | NONE/ACK/NAK/STALL/TIMEOUT/CRC_ERROR/RX_OVERFLOW |
| 0x06-07 | `RXLEN` | r | 16-bit, counted by the shim |
| 0x08-09 | `FRAME` | r | `sof_frame[10:0]` |
| 0x0c | `FIFO8` | rw | write pushes TX byte, read pops RX byte |
| 0x10-13 | `FIFO32` | rw | same FIFOs, four bytes per access |
| 0x14 | `FIFO_STATUS` | r | `tx_full`, `tx_empty`, `rx_valid`, `rx_full` |
| 0x15 | `IRQ_EN` | rw | `xfer_done`, `disconnect`, `sof` |
| 0x16 | `IRQ_PEND` | r/w1c | same bits |

- Interrupt: a source in the event controller (`gateware/soc/cpu/intc.py`, pending bits latch, write 1 to clear; numbering in [soc-interrupts.md](soc-interrupts.md)). `scripts/soc_generate_pac.py` reads `decoder.bus.memory_map` and `interrupt_sources`, so the PAC symbols appear automatically.
- GUH interface traps:
  - `status.rx_len` is 8 bits (`sie.py:616`), wraps on a 512 B packet; GUH MSC counts itself (`msc.py`, `rx_byte_idx = Signal(10)`). Shim counts FIFO drains.
  - Data toggle is the caller's: `xfer.data_pid` goes straight to the transmitter. Feature for fuzzing/MITM.
  - No completion strobe, poll-only: shim synthesises the interrupt from the rising edge of `status.idle` and holds it level-high (the event controller latches level sources; clear the peripheral, then the bit).
  - NYET reported as ACK (`sie.py:643-645`), no PING: HS bulk OUT flow control approximate.

### 7.4 Throughput

| | ceiling | basis |
|---|---|---|
| USB HS bulk | 53.2 MB/s | 13 x 512 B per 125 us microframe |
| USB FS bulk | 1.22 MB/s | 19 x 64 B per 1 ms frame |
| Wishbone 32-bit, 60 MHz | 120 MB/s | 2 cycles/transfer after `18c1fa5` |
| `FIFO8` port | ~4-8 MB/s (estimate) | one uncached byte access, 8-15 CPU cycles |
| `FIFO32` port | ~10-20 MB/s (estimate) | four bytes per access |

- Estimates unmeasured (VexiiRiscv stall on an uncached `iobus` load unknown; firmware loop against the CLINT tick, needs the board).
- Register port fits control transfers, enumeration, HID/interrupt endpoints, all FS. Sustained HS bulk reaches about a third of line rate.
- Tier two: `guh/periph/dma.py` (Wishbone initiator, `cti`/`bte` bursts, BSD-3 since `923c8490`) as a fourth arbiter master into the 64 KiB block RAM at 0x0. Staging FIFO `_DATA_FIFO_WORDS` default 8 KiB ~ 4 BRAM (tunable). Build only after a measured PIO number proves it necessary.

### 7.5 Enumeration in firmware

- Five control transfers: GET_DESCRIPTOR, SET_ADDRESS, GET_DESCRIPTOR at new address, SET_CONFIGURATION, bulk IN. ~400 lines `no_std` Rust over the shim, using the deferred event ring (`firmware/cynthion-soc/src/events.rs`). Raw descriptors, any class, can retry.
- VBUS/CC: add `control_vbus_en`, `target_c_vbus_en`, `aux_vbus_en`, `target_a_discharge` to the `gpio` peripheral; present Rp on TARGET via `SWITCHES0` in `firmware/cynthion-soc/src/fusb302.rs` (`fusb302.rs:26-38` documents why pull-downs are off) and `typec.rs`.

## 8. Jobs it serves

| job | gives | does not give |
|---|---|---|
| Analysis | known stimulus, deterministic replay, deliberate violations | capture fidelity (analyzer already better) |
| Emulation | the missing half: drive a device under test, fuzz, replay a capture | general host: no hubs past tier one, splits, isochronous |
| Passthrough / MITM | real host on TARGET + real device on another PHY, traffic modifiable; `greatscottgadgets/luna#302` | needs both stacks and a spare PHY (`aux_phy` is the console) |

Not served: USB 3.x, spec compliance, multi-tier hubs, guaranteed throughput.

## 9. What is built

- `gateware/probes/usb_host/model.py`: wire (two UTMI interfaces facing each other with the chirp line-state priority) and a model device (LUNA `USBDevice` with control endpoint and 512 B bulk IN). Both ends upstream gateware written independently.
- `scripts/usb_host_sie_sim.py` ([usb_host_sie_sim.py](../scripts/soc/usb_host_sie_sim.py)), in `scripts/soc_sims.py` (541 checks, 8.1 s); 21 assertions:
  - bus comes up both ways (HS detected HIGH; FS-only falls back to FULL);
  - wire checked independently (CRC5/CRC16 recomputed in Python);
  - five control transfers enumerate a device incl. a predicted 512 B bulk IN; negative control: transfer to the old address times out.
- Three of the four GUH traps are assertions: `rx_len` reads 0 after 512 B; `idle` is a level held 11450 cycles; DATA PID on the wire is the caller's. NYET needs a device that emits it (LUNA's does not).
- Not done: nothing attached to the SoC; no firmware; no CDC (bench runs in `sync`); no VBUS/CC; nothing below UTMI (no bit stuffing/NRZI/EOP, no ULPI PHY); no hardware.

## 10. Driver requirements (any scheduling model; see [rtic.md](rtic.md), [#115](https://github.com/awtoau/cynthion-workspace/issues/115))

1. Shim synthesises the completion edge from `status.idle` and holds it.
2. Latch `response` and RX byte count at completion; they hold only until the next `start`.
3. One owner: no queue, `start` ignored unless idle; shim raises a bit when that happens.
4. No deadline for correctness: the engine holds state and generates SOF itself. Late service costs throughput, not correctness, so host mode is not an argument for or against a scheduler.
5. Bounded drain loop (one HS packet = 512 FIFO accesses), not a byte per turn.

## 11. Next, in order

1. Bump/keep GUH pin `923c8490`; build the `sie` configuration of `usb_host_area.py` inside `AwtoSoc`; read in-situ LUT and `sync`/`usb` fmax (with the timing sweep).
2. Re-parent `target_phy` behind `UTMITranslator` (7.1); unblocks #120 and #125.
3. VBUS/CC (7.5).
4. CSR/FIFO shim + interrupt (7.3, section 10).
5. Firmware enumeration; milestone: print a device descriptor.
6. Measure PIO byte rate; then decide on tier two.

- Acceptance loopback (AUX to TARGET, proposed on #105) needs `aux_phy`, which is the console: move the console to `control_phy` behind `ApolloAdvertiser` for that test, or use a third-party device for first light.

## 12. Alternative: OHCI (full speed only)

- SpinalHDL ships OHCI (`lib/src/main/scala/spinal/lib/com/usb/ohci/`, 7 files incl. `UsbOhciWishbone.scala`): standard register map, existing Linux/BSD/Zephyr driver, DMA descriptors, Wishbone wrapper.
- FS/LS only (`UsbOhci.scala`: `lowSpeed` and an `fs` timer constant). No open EHCI found. Licence unconfirmed (`NOASSERTION`).
- Choice: 12 Mbps with a standard register map, or 480 Mbps bespoke. If the requirement softens to FS, OHCI is the better engineering.

## 13. Artifacts

- [scripts/usb_host_area.py](../scripts/soc/usb_host_area.py), `tmp/usb-host-area/area-results.json`.
- Original measurements retired to [debris/scripts/](../debris/scripts/) (`usb-host-core-area.py`, `usb-host-area.py`).
