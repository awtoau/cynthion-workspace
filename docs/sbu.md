# SBU — the Type-C sideband

Four lines to the FPGA, no peripheral, three mutually exclusive protocols depending on what the far end negotiated. The analog front end decides what is reachable.

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

## DP AUX (for completeness)

- 1 Mbit/s Manchester-II, half-duplex, one differential pair. Transaction: SYNC preamble, 4-bit request header (native vs I2C-over-AUX, read/write, MOT), 20-bit address, length byte, payload, STOP. Replies ACK / NACK / DEFER.
- Edge rate up to 2 M transitions/s (mid-bit transition every 1 µs plus boundary transitions: minimum spacing 500 ns). At 60 MHz: 30 clocks per half-bit. Fine for a fabric receiver; **absurd for per-edge interrupts** (30 CPU cycles vs a ~180-instruction dispatch).
- Linux drives a hardware AUX controller; `drm_dp_helper.c` is a transaction layer only.
- `gatecat/scratching-post` `dp/dp_aux_sink/`: Amaranth, ~400 lines, PHY plus packet layer, tristate single-ended interface, parameterised by clock, golden bit-pattern test vector. **No licence file**, unmaintained, testbenches use the removed Amaranth simulator API; adopting means asking the author.
- `hamsternz/FPGA_DisplayPort`: MIT, VHDL with Xilinx primitives, hardcoded timing; sizing only (PHY 143 LUT / 163 FF).
- Moot until a differential front end exists.

## What a receiver plugs into

A data line is not an interrupt source; the receiver's interrupt is (as the console 16550 is the source for USB CDC bytes, not the ULPI pins). Two ports, two sources. Existing pattern `SerialLine` → `StreamBuffer` → `Uart16550` → source; `SerialLine` drops onto an SBU pad unchanged.

## The peripheral

- `sbu.py`/`swd.py` and their sims are parked on branch `park/sbu-swd` (Dan, 2026-11-03; [#591](https://github.com/awtoau/cynthion-workspace/issues/591)).

