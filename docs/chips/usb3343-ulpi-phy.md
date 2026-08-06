# USB3343 ×3 — the ULPI PHYs

Three Microchip `USB3343-CP` high-speed USB transceivers on Cynthion r1.4, refdes
**U8**, **U9** and **U11** (`repos/cynthion-hardware/cynthion.kicad_pcb`). One
reusable schematic sheet, `usb_phy.kicad_sch`, instantiated three times — once each
from `control_port`, `aux_port` and `target_port`.

**Index:** [`../hardware.md`](../hardware.md)

**These are parallel ULPI, not I2C.** They have no bus address; the FPGA drives an
8-bit data bus with `dir`/`nxt`/`stp` handshaking and reads PHY registers through
the ULPI register window, not over a shared serial bus. Nothing about the I2C
topology on this board applies to them.

## Wiring on r1.4

`ULPIResource` declarations in `gateware/board/cynthion_r1_4.py`, all
`IO_TYPE="LVCMOS33"`, `SLEWRATE="FAST"`:

| resource | `data[0..7]` | `clk` | `dir` | `nxt` | `stp` | `rst` |
|---|---|---|---|---|---|---|
| `control_phy` | N16 N14 P16 P15 R16 R15 T15 P14 | L14 | M16 | M15 | L15 | L16 |
| `aux_phy` | F16 G15 G16 H15 J15 J16 K15 K16 | D16 | E16 | F15 | E15 | J13 |
| `target_phy` | R2 R1 P2 P1 N3 N1 M2 M1 | T4 | R3 | T2 | T3 | R4 |

Two kwargs carry meaning that is easy to get backwards:

- **`clk_dir='o'`** — the **FPGA sources the ULPI clock**, 60 MHz. The PHY does
  not.
- **`rst_invert=True`** — the ECP5 pin is active-low in hardware and asserted-high
  in gateware. Amaranth generates `Pins(rst, dir="o", invert=True)`.

`default_usb_connection = "aux_phy"`, and `apollo_port_sharing =
{'control_phy': 'advertising'}` — only CONTROL is muxed with Apollo. See
[`../hardware.md`](../hardware.md) for the arbitration.

**D+/D- are swapped on the board** and corrected in the PHY, not in gateware. The
platform writes vendor register `0x39 = 0b000110` on every PHY via
`ulpi_extra_registers`, commented *"USB3343: swap D+ and D- to match the hardware
design"*. Anything that brings a PHY up without going through the platform's
`ulpi_extra_registers` will have the pair inverted.

## Measured on this board

`debris/scripts/phy_probe.py`, result in `tmp/phy_probe.log` (2026-07-23):

| | |
|---|---|
| PHY ID, all three | `0x24 0x04 0x09 0x00` — **vendor `2404`, product `0900`** |
| data lines | **all 8 ok on all three PHYs**, rounds 0–2 |

The probe walks a single bit across all eight data lines through the scratch
register (`0x16`) rather than reading a fixed value, so a stuck bus or a shorted
line fails rather than passing on a constant. Three physically present, fully wired
USB3343s.

The shipped selftest makes the equivalent assertion —
`repos/cynthion/.../selftest/host.py` checks `2404:0900` then scratch patterns
`0x00`, `0xff` and each single bit.

**Not measured:** eye quality, signal integrity, or anything analogue. Enumeration
at 480 Mbps is exercised indirectly by every gateware build that brings a port up;
CDC loopback on this path measures 195.4 Mbps
([`../usb-performance.md`](../usb-performance.md)).

## How software reaches them

The ULPI core is LUNA's, `repos/luna/luna/gateware/interface/ulpi.py`:
`ULPIInterface`, `ULPIRegisterWindow`, `ULPIRxEventDecoder`,
`ULPIControlTranslator`, `ULPITransmitTranslator`, `UTMITranslator`. `USBDevice`
auto-wraps a ULPI bus — *"if this looks more like a ULPI bus than a UTMI bus,
translate it"* — so most gateware never touches ULPI directly.

**This is luna, not luna_soc.** The USB stack has been solid; the defects recorded
in [`../upstream-boundary.md`](../upstream-boundary.md) are all in luna_soc.

| PHY | analyzer | facedancer SoC |
|---|---|---|
| `target_phy` | capture path via `UTMITranslator` | `usb0` |
| `aux_phy` | `AnalyzerTestDevice` | `usb1` (also named `host_phy`) |
| `control_phy` | USB uplink to host, via `platform.port_sharing()` | `usb2` (also named `sideband_phy`) |

Register access for bring-up and selftest goes through
`repos/cynthion/.../selftest/gateware.py`, which exposes a ULPI register window per
PHY at `REGISTER_TARGET_ADDR` / `AUX` / `CONTROL`.

### From the SoC shell — `phy`

The main SoC bitstream carries a ULPI register window on **`target_phy` only**
(`gateware/soc/ulpi_window.py`, driver `firmware/cynthion-soc/src/ulpi.rs`).
It is in the main bitstream deliberately: a standalone probe design evicts the
SoC, so it cannot answer questions about a running system.

**`aux_phy` is not touched, and must not be.** The USB console this shell answers
on runs over it, so a second master issuing register commands there would corrupt
the link the answer travels over. `control_phy` is shared with Apollo. TARGET is
the port nothing else drives.

```
> phy
ulpi  @f000061c  target_phy
  register         at  value
  vendor id low    00  24
  vendor id high   01  04
  product id low   02  09
  product id high  03  00
  function control 04  41
  otg control      0a  06
  debug            15  00
  vendor 0424 product 0009 USB3343 ok
  linestate dp 0 dm 0
  scratch walk ff  all 8 data lines ok
```

**How to tell a live PHY from an absent one.** The identity registers are
necessary and not sufficient — a stuck bus, a constant, or a PHY held in reset
can all produce something that looks like an answer, and `0424`/`0009` is only
four of the eight bytes the bus can carry. So `phy` also walks a single bit
across the scratch register (`0x16`): eight writes, eight read-backs, each value
seen once. `scratch walk ff` means all eight data lines drove and returned
independently. That is the same assertion `debris/scripts/phy_probe.py` and the shipped
`cynthion selftest` make.

A PHY that is genuinely absent does **not** read as zeros. It never releases
`dir`, so the gateware's 68 µs timeout fires and the command reports `no answer
from the PHY` for each register — a different message from "answered, wrongly".
Without that timeout one read of a missing PHY would leave the window busy for
the rest of the session and every later read would report "busy" instead of
"absent"; the recovery is covered in `scripts/soc_board_sim.py`.

`debug` (`0x15`) is read rather than `0x14` (Interrupt Latch), which is
**clear-on-read**: a diagnostic must not consume the event it reports on.
`linestate dp 0 dm 0` is SE0 — the expected reading with nothing plugged into
TARGET, not a fault.

Bringing the TARGET PHY out of reset costs about **22 mA on the AUX rail**,
measured with `power` before and after (145 mA → 167 mA).

## Registers

**PHY registers are ULPI registers, not SoC registers.** They are reached through
the ULPI register window and are not in our memory map, so they do not appear in
the generated PAC — see [Register reference](../hardware.md#register-reference).
The two used here are the vendor D+/D- swap register `0x39` and the scratch
register `0x16`.

## Scripts

| | |
|---|---|
| `debris/scripts/phy_probe.py` | ID plus a walking-bit test on all 8 data lines, all three PHYs |
| `cynthion selftest` | the shipped equivalent |
