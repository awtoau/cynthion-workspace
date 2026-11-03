# SBU — the Type-C sideband

Four lines to the FPGA, no peripheral, three mutually exclusive protocols depending on what the far end negotiated. The analog front end decides what is reachable.

- Reference: <https://github.com/minoseigenheer/SWD-over-USB-C> — SWD in Debug Accessory Mode on this pin pair.
- Index: [`README.md`](README.md) · protection [`chips/dpo2036-cc-sbu-protection.md`](chips/dpo2036-cc-sbu-protection.md) · interrupts [`soc-interrupts.md`](soc-interrupts.md)

## What is wired

| ball | net | through |
|---|---|---|
| **A2** | `TARGET_C.SBU1S` | `U13.10` |
| **E4** | `TARGET_C.SBU2S` | `U13.9` |
| **H13** | `AUX_TYPE_C.SBU1S` | `U14.9` |
| **K14** | `AUX_TYPE_C.SBU2S` | `U14.10` |

- Plain bidirectional `LVCMOS33`, no pull mode, over-voltage protected by the DPO2036s, already claimed by the SoC top (a receiver needs no new `platform.request`).
- The FPGA is their only consumer: the FUSB302B has no SBU pins (`CC1`, `CC2`, `VBUS`, `VDD`, `INT_N`, `SCL`, `SDA`, `VCONN`, `GND`).
- [#97](https://github.com/awtoau/cynthion-workspace/issues/97): all four passed drive-and-readback (DC continuity, not bandwidth).
- Open: whether the DPO2036 passes a fast signal cleanly; whether the ports have opposite polarity — `TARGET_C.SBU1S` reaches `U13` pin 10 but `AUX_TYPE_C.SBU1S` reaches `U14` pin **9**, whose symbol name is `SBU2S` ([#517](https://github.com/awtoau/cynthion-workspace/issues/517) row 9).
- Orientation: SBU1/SBU2 swap with the cable, like CC1/CC2; a receiver reads orientation from the FUSB302B and swaps. A fixed per-port correction folds into the same place.

## Three protocols, one reachable as built

| negotiated | on the wire | electrical | reachable? |
|---|---|---|---|
| **DP Alt Mode** | AUX, 1 Mbit/s Manchester-II, half-duplex | AC-coupled **differential**, small swing | **no** |
| **Apple / Asahi debug** | UART, 115200 8N1 | single-ended, **1.2 V** logic | marginal |
| **ChromeOS CCD** | **USB 2.0 full-speed pair**, 12 Mbit/s | differential | **no** |
| **SWD** | two-wire debug | single-ended 3.3 V | **yes** |

- Pins are single-ended `LVCMOS33`. Real DP AUX readers put an `SN65MLVD200A` or `DS90LV048` differential receiver in front; none here.
- Apple's 1.2 V sits below an LVCMOS33 `VIH` of about 1.6 V.
- Reachable without new hardware: single-ended 3.3 V only (UART, SWD).

## SWD fits

- Two wires, single-ended, 3.3 V, **host-clocked** (no baud agreement, no oversampling). `SWCLK`/`SWDIO` map to `SBU1`/`SBU2`.
- No analog front end; no rate negotiation (far end sets speed).
- Hard part: `SWDIO` turnaround, same shape as [`gateware/probes/sideband/sideband_link.py`](../gateware/probes/sideband/sideband_link.py) on one bidirectional pin.
- Makes Cynthion a debug probe over USB-C.
- Precondition is CC-side entry into Debug Accessory Mode (FUSB302B presents the right resistors, far end accepts). The FUSB302B driver is measurement-only today (`POWER` without internal oscillator, `MASKA`/`MASKB` fully masked, `PDWN1`/`PDWN2` not enabled): nothing negotiates.

## DP AUX (for completeness)

- 1 Mbit/s Manchester-II, half-duplex, one differential pair. Transaction: SYNC preamble, 4-bit request header (native vs I2C-over-AUX, read/write, MOT), 20-bit address, length byte, payload, STOP. Replies ACK / NACK / DEFER.
- Edge rate up to 2 M transitions/s (mid-bit transition every 1 µs plus boundary transitions: minimum spacing 500 ns). At 60 MHz: 30 clocks per half-bit. Fine for a fabric receiver; **absurd for per-edge interrupts** (30 CPU cycles vs a ~180-instruction dispatch).
- Linux drives a hardware AUX controller; `drm_dp_helper.c` is a transaction layer only.
- `gatecat/scratching-post` `dp/dp_aux_sink/`: Amaranth, ~400 lines, PHY plus packet layer, tristate single-ended interface, parameterised by clock, golden bit-pattern test vector. **No licence file**, unmaintained, testbenches use the removed Amaranth simulator API; adopting means asking the author.
- `hamsternz/FPGA_DisplayPort`: MIT, VHDL with Xilinx primitives, hardcoded timing; sizing only (PHY 143 LUT / 163 FF).
- Moot until a differential front end exists.

## What a receiver plugs into

A data line is not an interrupt source; the receiver's interrupt is (as the console 16550 is the source for USB CDC bytes, not the ULPI pins). Two ports, two sources. Existing pattern `SerialLine` → `StreamBuffer` → `Uart16550` → source; `SerialLine` drops onto an SBU pad unchanged.

## The peripheral — `gateware/soc/peripherals/sbu.py` (branch `park/sbu-swd`, not on main)

One peripheral per port, one bidirectional pin pair, three modes selected by a register, sharing pads and tristate logic:

| mode | pin 0 | pin 1 | rate |
|---|---|---|---|
| **0 raw** (reset) | driven and sampled | driven and sampled | capture at `sync`, 16.7 ns |
| **1 serial** | transmit | receive | runtime baud, 10 Mbit/s exact, 12 Mbit/s ceiling |
| **2 swd** | `SWCLK`, always driven | `SWDIO`, turnaround per B4.2 | 52.5 MHz down to 25.6 kHz |

- **Raw** captures every transition with the levels after it and how long the previous levels held; the FIFO reports overflow rather than dropping quietly. It is the fallback for protocols with no engine here.
- **SWD** is the host side (Cynthion generates `SWCLK`), [#518](https://github.com/awtoau/cynthion-workspace/issues/518). Engine `peripherals/swd.py` (same branch), from ARM IHI 0074E chapter B4 (document: [`sources/README.md`](../sources/README.md)).
- **Speed** is an index into a fixed table (OpenOCD `stlink_khz_to_speed_map_swd[]` is twelve `{kHz, divisor}` pairs; ST-Link V2 tops out at 4 MHz). Index 0 is **52.5 MHz**.
- **`mode.swap`** corrects both reversal causes: flipped cable, and [#517](https://github.com/awtoau/cynthion-workspace/issues/517) row 9.

Clock:
- SWD runs in `swd`, 105 MHz: CLKOS2 of the PLL making `sync`, `CLKOS2_DIV = 4` of the 420 MHz VCO, exact; no second PLL bel. Clock plan: [`soc-clocking.md`](soc-clocking.md).
- CSRs stay in `sync`: pulses each way, request fields as data behind that handshake, ACK/RDATA captured on `done` (not synchronised per bit).

Bring-up:
- On-board SAMD11, not a cable. `J6` is the ARM 10-pin Cortex header carrying `MCU_SWCLK`/`MCU_SWDIO` to `U6` PA30/PA31. Jumper `user_pmod` 0 pins 1 and 2 (balls `C9`/`B9`) to it.
- **Read DPIDR and stop**: that SAMD11 is Apollo; halting it takes the board off USB.

Verification:
- Simulation only, no hardware yet: `scripts/soc/swd_host_sim.py` (37 checks; UNVERIFIED count) drives a spec-derived target model; `scripts/soc/sbu_port_sim.py` (29 checks; UNVERIFIED count) runs both clock domains through the CSR bus.
- Still to settle on a board: that the round trip through two DPO2036 switches and a cable fits the 9.5 ns half period at index 0 ([#97](https://github.com/awtoau/cynthion-workspace/issues/97) proves DC only); a slower speed index is the fallback.
