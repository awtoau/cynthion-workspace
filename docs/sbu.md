# SBU — the Type-C sideband, and what can be done with it

Four lines to the FPGA, no peripheral, and three mutually exclusive protocols
depending on what the far end negotiated. **The gateware is the easy part; the
analog front end decides what is reachable.**

**Reference implementation:**
<https://github.com/minoseigenheer/SWD-over-USB-C> — SWD in Debug Accessory Mode
on this exact pin pair.

**Index:** [`README.md`](README.md) · protection
[`chips/dpo2036-cc-sbu-protection.md`](chips/dpo2036-cc-sbu-protection.md) ·
interrupt design [`soc-interrupts.md`](soc-interrupts.md)

## What is wired

| ball | net | through |
|---|---|---|
| **A2** | `TARGET_C.SBU1S` | `U13.10` |
| **E4** | `TARGET_C.SBU2S` | `U13.9` |
| **H13** | `AUX_TYPE_C.SBU1S` | `U14.9` |
| **K14** | `AUX_TYPE_C.SBU2S` | `U14.10` |

Declared as plain bidirectional `LVCMOS33` with no pull mode, over-voltage
protected by the DPO2036s, and already claimed by the SoC top — so a receiver
needs no new `platform.request`.

**The FPGA is their only consumer.** The FUSB302B has no SBU pins; its pinout is
`CC1`, `CC2`, `VBUS`, `VDD`, `INT_N`, `SCL`, `SDA`, `VCONN`, `GND`.

**Proven alive** under #97 — all four passed drive-and-readback. That is DC
continuity, not bandwidth.

**Two things to settle while building, neither a blocker:** whether the DPO2036
passes a signal cleanly enough for a fast link, and whether the two ports are
wired with opposite polarity — `TARGET_C.SBU1S` reaches `U13` pin 10 while
`AUX_TYPE_C.SBU1S` reaches `U14` pin **9**, whose symbol name is `SBU2S`.
#517 row 9.

Orientation has to be handled anyway: SBU1/SBU2 swap when the cable is flipped,
like CC1/CC2, so a receiver reads orientation from the FUSB302B and swaps. A
fixed per-port correction folds into the same place.

## Three protocols, and only one is reachable as built

| negotiated | on the wire | electrical | reachable? |
|---|---|---|---|
| **DP Alt Mode** | AUX, 1 Mbit/s Manchester-II, half-duplex | AC-coupled **differential**, small swing | **no** |
| **Apple / Asahi debug** | UART, 115200 8N1 | single-ended, **1.2 V** logic | marginal |
| **ChromeOS CCD** | **USB 2.0 full-speed pair**, 12 Mbit/s | differential | **no** |
| **SWD** | two-wire debug | single-ended 3.3 V | **yes** |

Our pins are single-ended `LVCMOS33`. Every project that reads real DP AUX puts
an `SN65MLVD200A` or `DS90LV048` differential receiver in front; we have none.
Apple's 1.2 V logic sits below an LVCMOS33 `VIH` of roughly 1.6 V.

**So the protocols reachable without new hardware are the single-ended 3.3 V
ones: a UART, or SWD.**

## SWD is the option that fits

Two wires, single-ended, 3.3 V, and **clocked by the host** — so there is no
baud rate to agree and no oversampling requirement. `SWCLK` and `SWDIO` map onto
`SBU1`/`SBU2` directly.

It is an established use of these pins: `minoseigenheer/SWD-over-USB-C` puts SWD
in Debug Accessory Mode on exactly this pair.

Why it suits this board better than the alternatives:

- **no analog front end** — single-ended 3.3 V is what the pins already are
- **no rate negotiation** — SWD is host-clocked, so the far end sets the speed
  and the receiver follows
- **the direction change is the only hard part** — `SWDIO` is bidirectional with
  a turnaround phase, which is the same shape as
  `gateware/probes/sideband/sideband_link.py` already handles on a single
  bidirectional pin
- **it makes Cynthion a debug probe over USB-C**, which is a capability rather
  than an observation

**Entry into Debug Accessory Mode is the precondition**, and that is CC-side
work: the FUSB302B has to present the right resistors and the far end has to
accept. Our FUSB302B driver is measurement-only today — `POWER` without the
internal oscillator, `MASKA`/`MASKB` fully masked, `PDWN1`/`PDWN2` not enabled —
so nothing negotiates anything yet.

## DP AUX, for completeness

1 Mbit/s Manchester-II, half-duplex, on one differential pair. Every transaction
opens with a SYNC preamble and closes with a STOP; the request header is 4 bits
(native vs I2C-over-AUX, read/write, MOT), then a 20-bit address, a length byte
and payload. Replies are ACK / NACK / DEFER.

**Edge rate is up to 2 M transitions/s** — Manchester guarantees a mid-bit
transition every 1 µs and consecutive identical bits add a boundary one, so the
minimum edge spacing is 500 ns. At 60 MHz that is 30 clocks per half-bit:
comfortable for a fabric receiver, and **absurd for per-edge interrupts**, which
would leave 30 CPU cycles against a ~180-instruction dispatch.

Linux never bit-bangs it either — every kernel path drives a hardware AUX
controller, and `drm_dp_helper.c` is a transaction layer only.

**One adoptable implementation exists**: `gatecat/scratching-post`,
`dp/dp_aux_sink/` — Amaranth, ~400 lines, PHY plus packet layer, tristate
single-ended interface, parameterised by clock frequency, with a golden bit
pattern as a test vector. **It has no licence file**, is unmaintained, and its
testbenches use the removed Amaranth simulator API. Adopting it means asking the
author. `hamsternz/FPGA_DisplayPort` is MIT but VHDL with Xilinx primitives and
hardcoded timing; useful for sizing — 143 LUT / 163 FF for the PHY.

None of that matters until the differential front end exists.

## What a receiver plugs into

A data line is not an interrupt source. Whatever receiver goes on these lines,
**its** interrupt is the source — as the console's 16550 is the source for USB
CDC bytes rather than the ULPI pins being one. Two ports, two sources.

The existing pattern is `SerialLine` → `StreamBuffer` → `Uart16550` → source,
and `SerialLine` drops onto an SBU pad unchanged.

## The peripheral that was built — [`gateware/soc/peripherals/sbu.py`](../gateware/soc/peripherals/sbu.py)

**One peripheral per port, one bidirectional pin pair, three modes**, selected by
a register and sharing the pads and the tristate logic:

| mode | pin 0 | pin 1 | rate |
|---|---|---|---|
| **0 raw** (reset) | driven and sampled | driven and sampled | capture at `sync`, 16.7 ns |
| **1 serial** | transmit | receive | runtime baud, 10 Mbit/s exact, 12 Mbit/s ceiling |
| **2 swd** | `SWCLK`, always driven | `SWDIO`, turnaround per B4.2 | 52.5 MHz down to 25.6 kHz |

- **Raw is the fallback and it is why the peripheral is not just an SWD block.**
  Every transition is captured with the levels after it and how long the previous
  levels held, so a sideband protocol with no engine here is readable rather than
  guessable. The FIFO reports overflow instead of dropping quietly.
- **SWD is the host side** — Cynthion generates `SWCLK`, per #518. The engine is
  [`peripherals/swd.py`](../gateware/soc/peripherals/swd.py), built from ARM
  IHI 0074E chapter B4; `sources/README.md` has the document.
- **Speed is an index into a fixed table**, the shape real probes ship: OpenOCD's
  `stlink_khz_to_speed_map_swd[]` is twelve `{kHz, divisor}` pairs and an ST-Link
  V2 tops out at 4 MHz. Index 0 here is **52.5 MHz**.
- **`mode.swap` is the one correction** for both reasons the pair arrives
  reversed — a flipped cable, and #517 row 9.

**The clock.** SWD runs in `swd`, a 105 MHz domain that is CLKOS2 of the PLL
already making `sync`: `CLKOS2_DIV = 4` of the 420 MHz VCO, exactly, so nothing
else moves and no second PLL bel is taken. The CSRs stay in `sync`, so the
crossing is real and is handled as one — pulses each way, request fields as data
behind that handshake, and ACK/RDATA captured on `done` rather than synchronised
per bit.

**Bring-up is the on-board SAMD11, not a cable.** `J6` is the ARM 10-pin Cortex
header carrying `MCU_SWCLK`/`MCU_SWDIO` to `U6` PA30/PA31. Jumper `user_pmod` 0
pins 1 and 2 — balls `C9`/`B9` — to it and the target is a real SW-DP two inches
away. **Read DPIDR and stop**: that SAMD11 is Apollo, and halting it takes the
board off USB.

**Proved in simulation, not on hardware.** `scripts/swd_host_sim.py` (37 checks)
drives a target model written from the specification; `scripts/sbu_port_sim.py`
(29 checks) runs both clock domains through the CSR bus. Both carry negative
controls that are *run* — a turnaround one clock late, an inverted parity bit, an
undefined acknowledge, a contending target — each asserting the corruption is
reported.

**What a board still has to settle:** that the 105 MHz domain closes timing, and
that the round trip through two DPO2036 switches and a cable fits the 9.5 ns half
period at index 0. #97 proves DC continuity and nothing about a fast edge; a
slower speed index is the answer if it does not.
