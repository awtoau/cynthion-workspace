# USB Host Mode on Cynthion at 480 Mbps — Proposal

**Status:** proposal, no hardware exercised
**Date:** 2026-07-30T19:30:00+10:00
**Consolidates:** awtoau/cynthion-workspace#101, #105
**Target:** LFE5U-12F (24288 LUT, 56 BRAM), Cynthion r1.4

---

## 0. Executive summary

Four findings drive everything below.

1. **LUNA has no host stack.** Not a partial one — the USB 2.0 protocol layer is
   structurally device-only. The ULPI/PHY layer, however, already exposes every
   host control signal it would need.
2. **The board can host.** Cynthion r1.4 can source VBUS to TARGET A and has
   current/voltage monitoring plus Type-C CC control. This is not a board blocker.
3. **A working LUNA-based host exists in another project** — Tiliqua's
   `usb_host.py` (merged, 691 lines) — but it is **full-speed (12 Mbps) only**,
   and a GSG maintainer has pointed at it as *the* starting point for Cynthion.
4. **480 Mbps is where the cost concentrates.** Getting to 12 Mbps host is a
   port-and-adapt job. Getting from 12 to 480 Mbps requires the host side of the
   high-speed chirp handshake, which nobody in this ecosystem has written.

The distance from "no host" to "12 Mbps host" is far shorter than the distance
from "12 Mbps host" to "480 Mbps host". Recommendation is in section 6.

---

## 1. What LUNA has today

### 1.1 It is device-only, by construction

`repos/luna/luna/gateware/usb/usb2/` contains `device.py`, `control.py`,
`endpoint.py`, `request.py`, `transfer.py`, `descriptor.py`, `reset.py`,
`packet.py`. There is no `host.py`, no root-hub model, no SOF generator, no
transaction scheduler, no pipe management, no enumeration driver.

The decisive line is `luna/gateware/usb/usb2/device.py:382`:

```python
# Disable our host-mode pulldowns; as we're a device.
self.utmi.dm_pulldown.eq(0),
self.utmi.dp_pulldown.eq(0),
```

The device stack does not merely omit host support; it actively drives the
host-mode pulldowns off. Host mode is a deliberate non-goal of `USBDevice`.

### 1.2 The asymmetry is in the packet layer

`packet.py` names look symmetric but are not. Each block is one direction only:

| Block | Role | Reusable for host? |
|---|---|---|
| `USBTokenDetector` | **receives** SETUP/IN/OUT/SOF tokens | No — host must *generate* tokens |
| `USBHandshakeDetector` | receives ACK/NAK/STALL | Yes, directly |
| `USBHandshakeGenerator` | sends ACK/NAK/STALL as a device | Partly — host only ever sends ACK |
| `USBDataPacketReceiver` / `Deserializer` | receives DATA0/1 | Yes, directly |
| `USBDataPacketGenerator` | sends DATA0/1, does CRC-16 | Yes, directly |
| `USBDataPacketCRC` | CRC-16 both directions | Yes, directly |
| `USBInterpacketTimer` | bus turnaround timing | Yes — already has HS/FS/LS constants |

So roughly the lower half of the packet layer is direction-agnostic and reusable.
The missing pieces are a **token generator** and everything above it.

`USBInterpacketTimer` is worth calling out as a genuine asset: it already encodes
HS, FS and LS rx-to-tx delays and tx-to-rx timeouts from the USB 2.0 and ULPI 1.1
specs (`_HS_RX_TO_TX_DELAY`, `_HS_TX_TO_RX_TIMEOUT = 92` ULPI cycles, etc.).
Bus turnaround timing at high speed is a classic source of silent failure, and it
is already done and in use.

### 1.3 The HS chirp handshake is device-side only

`reset.py`'s `USBResetSequencer` implements the **device** half of the high-speed
detection handshake: states `DEVICE_CHIRP` (drive chirp K ~2 ms), then
`AWAIT_HOST_K` / `IN_HOST_K` / `AWAIT_HOST_J` / `IN_HOST_J`, counting three valid
K/J pairs before declaring `IS_HIGH_SPEED`.

The host half is the mirror image and **does not exist**: assert SE0, watch for a
device chirp K, then drive at least three alternating K/J pairs, then switch to HS
terminations. This is the single most important gap for a 480 Mbps host, and it is
discussed in section 3.2.

### 1.4 The ULPI layer, by contrast, is already host-capable

`luna/gateware/interface/ulpi.py` exposes the full UTMI+ control surface, and the
docstrings explicitly anticipate host use:

```
I: dp_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
I: dm_pulldown -- when set, enables a 15kR pull-down on D+; intended for host mode
```

Also present: `op_mode` (including `RAW_DRIVE` for direct bus driving),
`xcvr_select`, `term_select`, `chrg_vbus` / `dischrg_vbus`, and the OTG control
register (0x0A) written with `id_pullup`, `dp_pulldown`, `dm_pulldown`,
`dischrg_vbus`, `chrg_vbus`, `use_external_vbus_indicator`.

**Conclusion for section 1:** the PHY abstraction needs no work for host mode. The
protocol layer above it needs to be written. This is a favourable split — the
fiddly, timing-critical, vendor-specific layer is the part already done.

---

## 2. What the board supports

From `repos/cynthion/cynthion/python/src/gateware/platform/cynthion_r1_4.py`.

### 2.1 Three full ULPI PHYs

`control_phy`, `aux_phy`, `target_phy` are each declared as a complete
`ULPIResource` — 8-bit data, clk (FPGA-driven, `clk_dir='o'`), dir, nxt, stp,
rst. All three are USB 2.0 HS-capable PHYs with bidirectional ULPI. Nothing about
the ULPI wiring restricts direction of the USB role; ULPI `dir` is bus turnaround,
not host/device role.

### 2.2 VBUS sourcing exists — this is the board fact that matters

```python
# VBUS on each of the Type-C ports can be connected to TARGET A through
# a bidirectional switch. If any of these switches is enabled, TARGET A
# is considered an output.
Resource("target_c_vbus_en",   0, Pins("K5", ...))
Resource("control_vbus_en",    0, Pins("L1", ...))
Resource("aux_vbus_en",        0, Pins("L2", ...))
Resource("target_a_discharge", 0, Pins("K4", ...))
```

Plus `control_vbus_in_en` / `aux_vbus_in_en` for input shutoff.

A host must source VBUS to the device it hosts. Cynthion can: VBUS from CONTROL or
AUX can be switched through to TARGET A, and TARGET A can be actively discharged.
`target_a_discharge` matters more than it looks — safe VBUS sequencing on
disconnect needs it, and it is present.

Supporting infrastructure is also there:

- `power_monitor` — I2C voltage/current monitor, so overcurrent detection and the
  spec-required port power management are achievable.
- `target_type_c` / `aux_type_c` — I2C Type-C controllers with `int` and `fault`
  pins, giving CC line control (Rp/Rd role advertisement) and fault reporting.

### 2.3 No ID pin, and none needed

There is no mini/micro-B `ID` pin, because r1.4 is Type-C. Role is established by
CC line resistors via the Type-C controllers, not by an ID pin. The ULPI
`id_pullup` bit exists in the OTG register but is irrelevant here. This is a
non-issue, noted only because "check the ID pin" was raised as a question.

### 2.4 Board verdict

**The board can act as a USB host.** VBUS sourcing, discharge, current monitoring,
CC role control and an HS-capable PHY are all present on TARGET. There is no
hardware blocker. Everything remaining is gateware plus firmware.

Caveat not verified here: whether the VBUS switch path and TARGET A current limit
meet the 500 mA / 900 mA a spec-compliant host should offer. That is a schematic
and datasheet question, and no hardware was exercised for this proposal. For
hosting a single low-power device it is very unlikely to be a problem.
