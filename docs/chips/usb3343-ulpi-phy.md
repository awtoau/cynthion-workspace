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

## Performance

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md);
cross-cut against every other bus in [`bus-speed-audit.md`](bus-speed-audit.md).
Datasheet references are **SMSC USB334x Revision 1.2 (02-08-13)**,
[`sources/334x.pdf`](../../sources/334x.pdf), 92 pp.

**This is the one interface on the board with no rate decision in it**, and that
is worth stating rather than assuming. The 60 MHz is not a choice anyone made,
cannot be raised, and is exactly right.

### 1. Theoretical maximum

**The ULPI clock is 60 MHz, fixed by the interface specification.** The part
implements *"UTMI+ Low Pin Interface (ULPI) Specification, Revision 1.1"*
(p. 1), whose whole arithmetic is that an 8-bit bus at 60 MHz carries USB high
speed exactly:

    8 bits x 60 MHz = 480 Mb/s = 60 MB/s     the ULPI byte lane
    USB 2.0 high speed                        480 Mb/s = 60 MB/s

Those are the same number by construction. A faster ULPI clock would carry
nothing, because there is no faster USB 2.0.

| line rate | Mb/s | MB/s | ULPI clocks per byte |
|---|---|---|---|
| high speed | 480 | 60.0 | 1 |
| full speed | 12 | 1.5 | 40 |
| low speed | 1.5 | 0.1875 | 320 |

The datasheet's note 0.1 (p. 2) settles the direction: *"All versions support
ULPI Clock In Mode (60MHz input at REFCLK)"*, and Table 4.3 (p. 19) rates
`FREFCLK`, the REFCLK frequency accuracy, at **±500 ppm** with `DCREFCLK`, the
duty cycle, at **20–80%**.

### 2. Achievable on this board — the FPGA is the clock source, so there is nothing to raise

**`clk_dir='o'`.** All three `ULPIResource` declarations put the clock pin in
output mode, so these PHYs run in **ULPI Clock Input Mode**: the FPGA drives
60 MHz into REFCLK and the PHY uses it directly as the interface clock. It is not
a rate we ask a part for; it is a rate we supply.

Which makes the oscillator the specification, and it clears the requirement by an
order of magnitude. **Y1 is `SIT1602BC-23-33E-60.000000E`** — a SiTime SiT1602B
MEMS oscillator on ball A8 through series resistor R108
(`production/netlist.ipc`, `clock_misc.kicad_sch`). Its part number decodes to
**±50 ppm total stability**, *"inclusive of initial tolerance at 25 °C, 1st year
aging at 25 °C, and variations over operating temperature, rated power supply
voltage and load"* (SiT1602B rev 1.08 Table 1), with a 45–55% duty cycle:

| requirement | USB3343 needs | Y1 delivers | margin |
|---|---|---|---|
| REFCLK accuracy | ±500 ppm | **±50 ppm** | **10×** |
| REFCLK duty cycle | 20–80% | 45–55% | wide |
| USB 2.0 high-speed device clock | ±500 ppm | ±50 ppm | 10× |

`usb` is that oscillator passed straight through with no PLL in the path
([`../../gateware/soc/clocks.py`](../../gateware/soc/clocks.py) — `usb` is the
one domain the PLL does not touch), so the 60.000 MHz in the design is exact
rather than solved. That file's own docstring records what the alternative cost:
a `sync` = 90 MHz build put `usb` at 63.000 MHz, placed and configured cleanly,
and never appeared on the USB bus.

**The pins are not the constraint either, for once.** `IO_TYPE="LVCMOS33"`,
`SLEWRATE="FAST"` — so ECP5 Table 3.21 applies at its published figures, 150 MHz
output and 200 MHz input. 60 MHz is **40%** of the output ceiling, the widest
margin any fast interface on this board has. See
[`ecp5/lfe5u-12f.md`](ecp5/lfe5u-12f.md) §2 for what that table does to the
others.

**The interface timing budget, at the 16.67 ns period** (Table 4.4, p. 20–21,
the *"60MHz ULPI Input Clock"* rows, `CLoad` = 10 pF):

| direction | parameter | value | what is left of the period |
|---|---|---|---|
| FPGA → PHY | `TSC`, `TSD` setup | 3 ns min | 13.67 ns for the FPGA to produce it |
| FPGA → PHY | `THC`, `THD` hold | 0 ns min | no hold obligation at all |
| PHY → FPGA | `TDC`, `TDD` output delay | 0.5–6.0 ns | 10.67 ns for the FPGA to capture it |

Note 4.4 adds that *"REFCLK does not need to be aligned in any way to the ULPI
signals"*, which is why a single clock net feeding both the PHY and the fabric is
legal here.

### 3. Measured

| path | conditions | figure | source |
|---|---|---|---|
| PHY identity, all three | ULPI register window over JTAG | `0x24 0x04 0x09 0x00` — vendor `2404`, product `0900` | `debris/scripts/phy_probe.py`, 2026-07-23 |
| data lines, all three | walking bit through scratch register `0x16`, rounds 0–2 | 8/8 on each PHY | as above |
| **bulk IN, direct root port** | high speed, 512-byte packets, 284,306 transactions | **388.0 Mbps = 48.5 MB/s** | [`../usb-performance.md`](../usb-performance.md) |
| bulk OUT, direct root port | as above | 338.8 Mbps = 42.3 MB/s | as above |
| CDC-ACM loopback, combinational | as above | 195.4 Mbps = 24.4 MB/s | as above |
| **the ULPI clock itself** | — | **never measured** | it has never needed to be; see below |

388 Mbps is **81% of the 60 MB/s ULPI byte lane** and **91% of the 426 Mbps USB
protocol maximum**. The device contributes 0.16% of the transaction budget: 1.0000
ACKs per IN token, 512.0 bytes per token, one clock cycle from token to first data
byte. **Nothing in the measured shortfall is the PHY or the clock.**

**Not measured:** eye quality, signal integrity, or anything analogue on D+/D−.
The 60 MHz on the clock pin has never been put on a scope either — what stands in
for it is that `ClockMonitor`
([`../../gateware/soc/peripherals/clock_monitor.py`](../../gateware/soc/peripherals/clock_monitor.py))
counts `sync` against a 60,000-cycle `usb` window and the CPU reports a mismatch
if the two disagree, so a wrong oscillator would be visible as a wrong `sync`.

### 4. The gap, and what closes it

**There is no gap on this interface, and nothing should be spent looking for
one.** The three numbers agree: the specification says 60 MHz, the board supplies
60.000 MHz at ±50 ppm, and the design configures 60 MHz.

What is left is throughput, and none of it is here:

| rank | option | worth | where it lives |
|---|---|---|---|
| — | raise the ULPI clock | **nothing.** There is no faster USB 2.0 | — |
| 1 | close the 91% → 100% protocol gap | 388 → 426 Mbps | host and topology, not the device — [`../usb-performance.md`](../usb-performance.md) |
| 2 | CDC-ACM path, 195.4 → 388 Mbps | **2×** | the combinational loopback harness, not the PHY |
| — | a second PHY carrying traffic at once | 60 → 120 MB/s aggregate | three PHYs exist; no design drives two at line rate |

**Two timing constants worth checking against this section rather than
re-deriving.** Both are `usb` cycle counts, and both are exact rather than
approximate now that `usb` is an oscillator: `PHY_PAD_RESET_CYCLES = 128` is
2.133 µs against §5.6.2's *"minimum of 1 microsecond"*, and
`PHY_PREP_CYCLES = 72_000` is 1.200 ms against `TPREP` 1.0–1.2 ms (Table 4.3,
LPM disabled).

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
(`gateware/soc/peripherals/ulpi_window.py`, driver `firmware/cynthion-soc/src/ulpi.rs`).
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
"absent"; the recovery is covered in `scripts/soc/soc_board_sim.py`.

`debug` (`0x15`) is read rather than `0x14`, the Interrupt Latch, which is
**clear-on-read**: a diagnostic must not consume the event it reports on. See
[the interrupt block](#the-interrupt-block).
`linestate dp 0 dm 0` is SE0 — the expected reading with nothing plugged into
TARGET, not a fault.

Bringing the TARGET PHY out of reset costs about **22 mA on the AUX rail**,
measured with `power` before and after (145 mA → 167 mA).

## RESETB, and how to know it is connected

`RESETB` is the only reset these parts have, and all three ULPI resources declare
`rst_invert=True`, so the pad is active low. Two of the three are driven — TARGET
from [`gateware/soc/top.py`](../../gateware/soc/top.py), AUX from LUNA's
`UTMITranslator` — and both take `ResetSignal("usb")`.

Two durations, both from the datasheet (Rev 1.2), and both counted in gateware
against the 60.000 MHz oscillator so they are exact rather than approximate:

| | | |
|---|---|---|
| RESETB low | **≥ 1 µs** | §5.6.2. Resets the ULPI registers to their defaults and every internal state machine. We use 128 cycles, 2.133 µs. |
| TPREP | **1.0–1.2 ms** | Table 4.3, 60 MHz REFCLK, LPM disabled. From RESETB valid to the PHY de-asserting DIR; the bus is not the link's to drive before it. We use 72000 cycles, 1.200 ms — the maximum, not the typical. |

At **cold power-up none of this shows**: the part's own POR runs when VDD18 comes
up, long before FPGA configuration. What needs the pad is **warm
reconfiguration** — reflashing or `trigger_fpga_reconfiguration()` does not
power-cycle the PHY, so one carrying a stuck bus turn from the previous bitstream
keeps it.

That is why a pad tied de-asserted was invisible for as long as it was
([#241](https://github.com/awtoau/cynthion-workspace/issues/241)): the PHY
answers its identity registers either way.

**`phy reset` is the check that is not fooled by that.** Scratch (`0x16`) is
specified to return to `00h` on a RESETB cycle (Table 7.1), so the command writes
`0x5a`, *verifies it took*, resets, and reads back:

    phy reset  target_phy
      scratch set     5a
      resetb          low 2.133 us, then 1.200 ms tprep
      scratch now     00  RESET REACHED THE PHY (vendor 24)

`00` means the pad moved and the part saw it; `5a` means it did not. Verified on
hardware 2026-08-07.

## Role mux and byte stream on `target_phy` (`usb_host` window)

Legend: **I** implemented and simulated ([`usb_host_shim_sim.py`](../../scripts/soc/usb_host_shim_sim.py)); **U** not proven on a board. Nothing here has run on hardware: every row is U for the board; I marks what the simulation covers.

Source: [`peripherals/usb_host.py`](../../gateware/soc/peripherals/usb_host.py), class `UsbPort`. Window `usb_host` at `0xf0001000`, 0x20 bytes, 8-bit CSRs (4 per word), IRQ 10 (level). Design: [`../aoa-adaptor.md`](../aoa-adaptor.md), [`../usb-host-options.md`](../usb-host-options.md).

### Roles (`ROLE`, reset `off`)
| role | pins belong to | status |
|---|---|---|
| `off` (0) | `UlpiRegisters`; same wire behaviour as `regs` | I (cycle-identical to bare `UlpiRegisters`), U |
| `regs` (1) | `UlpiRegisters` (`ulpi` window, `phy` shell command) | I, U |
| `host` (2) | vendored GUH SIE via `UTMITranslator` | I (HS and FS enumeration, 512 B bulk IN vs LUNA device), U |
| `raw` (3) | CPU: TXCMD/bytes out, RXCMD/bytes in | I (SETUP+DATA0 vs LUNA device, register read/write), U |

- Switch (usb domain) only when `DIR` has been low 8 cycles AND the outgoing owner is idle (regs window and PHY reset done; host translator not busy; raw TX engine idle). I
- Wait bound `SWITCH_LIMIT` = 1.25 x (`PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES`) usb cycles; expiry: role unchanged, `ROLE_STATUS.timeout`, `EV_TIMEOUT`. I
- Entering `host` first writes function control `0x04 = 0x41` and OTG control `0x0a = 0x06` (the translator's shadow of them); 32-cycle bound per write; failure leaves role `regs` + timeout. I
- The translator also writes the board's swap register `0x39` (`ulpi_extra_registers`). `raw` does not: firmware writes `0x39 = 0x06` itself via `TX_DATA`/`TX_LAST` (`0xB9`, `0x06`). The sim runs with the swap register but has no D+/D- to swap. I/U
- While `UlpiRegisters` is not selected it sees `DIR=1`: a `ulpi` read times out as "PHY absent". I
- `usb3343 reset` (`phy_rst`) is independent of the role; resetting under `host` is not coordinated (SIE keeps its state). U
- VBUS: not touched here; `vbus` command only. Host pull-downs come from the SIE's OTG-control writes.
- Not instanced on aux/control: `UsbPort(name=...)` takes the pins and the `regs_*` hand-off as ports. U

### Registers (byte offsets; side effects sit alone in a word)
| off | name | acc | contents |
|---|---|---|---|
| 0x00 | `ROLE` | rw | [1:0] requested role; write while a switch is pending is dropped (`BUSY_DROP`) |
| 0x01 | `CTRL` | w | 0 `bus_reset` (host) 1 `start` (host) 2 `clr_tx` 3 `clr_rx` |
| 0x02 | `IRQ_EN` | rw | 0 rx_avail 1 done 2 line 3 errors (`EVENTS` bits 2-7) |
| 0x04 | `ROLE_STATUS` | r | [1:0] current role 2 switching 3 timeout (sticky) |
| 0x05 | `LINK` | r | [1:0] line state (host: translator; raw: last RXCMD) [3:2] speed 0 HS 1 FS 2 LS 3 unknown 4 connected 5 reset_active 6 idle 7 reset_done |
| 0x06 | `EVENTS` | rw1c | 0 done 1 line 2 rx_overflow 3 tx_overflow 4 rx_underflow 5 tx_abort 6 busy_drop 7 timeout |
| 0x07 | `RXPID` | r | first PID byte received in the last transfer |
| 0x08 | `ADDR` | rw | [6:0] device address |
| 0x09 | `EP` | rw | [3:0] endpoint [5:4] type (0 SETUP 1 IN 2 OUT) 6 data_pid (caller's toggle) |
| 0x0a | `RESP` | r | [2:0] response (1 ACK 2 NAK 3 STALL 4 TIMEOUT 5 CRC 6 RX_OVERFLOW) 3 `nyet` |
| 0x0c/0x0d | `RXLEN` | r | 16-bit payload bytes of the last IN |
| 0x10 | `RX_DATA` | r | **pops** the RX FIFO; empty returns 0 + `rx_underflow` |
| 0x11 | `RX_TAG` | r | 0 tag of head 1 head valid; no side effect (read before `RX_DATA`) |
| 0x12 | `RX_LEVEL` | r | RX entries (0..128) |
| 0x14 | `TX_DATA` | w | push a byte |
| 0x15 | `TX_LAST` | w | push the last byte of a packet; raw ends it with STP |
| 0x16 | `TX_CMD` | w | push the last byte of a command with no STP (ULPI register read) |
| 0x17 | `TX_LEVEL` | r | TX entries (0..128) |

- Use byte accesses: a 32-bit store strobes every register in the word.

### FIFOs
- Both 128 deep, async (`sync` <-> `usb`), LUTRAM. TX 10 bit `{tag[1:0], byte}`: tag 0 data, 1 last+STP, 2 last without STP (`host` ignores tags). RX 9 bit `{tag, byte}`: tag 0 data byte, **tag 1 RXCMD**.
- `raw` RX also synthesises an RXCMD when DIR+NXT start a receive (RxActive set) and when DIR falls with RxActive set (cleared). A register read comes back as a tag-1 entry. I
- `raw` TX: a packet leaves only after its last byte is queued (marker count crossed to usb, 4-cycle settle). Packets > 128 bytes cannot be sent; > 128 RX entries overflow (oldest kept, tail dropped, `rx_overflow`). I
- A packet is a ULPI transmit: `[0x40|PID, byte..., last]` (TXCMD, then bytes, STP after the last); the CPU computes CRC5/CRC16. Register write: `[0x80|addr, value(last)]`; register read: `[0xC0|addr (TX_CMD)]` then the RX entry. I
- `host`: TX bytes flow into the SIE's own 512 B FIFO as written (`clr_tx` clears only the shim FIFO); RX entries are payload bytes, tag 0. I

### SIE traps (7.3 of [`../usb-host-options.md`](../usb-host-options.md))
| trap | handling |
|---|---|
| 8-bit `rx_len` wraps at 512 | `RXLEN` counts UTMI receive bytes less PID and CRC16; sim: RXLEN 512 while SIE `rx_len` reads 0. I |
| data toggle | caller's (`EP.data_pid`); no auto toggle. I |
| no completion strobe | driver FSM edge-detects `idle`; `EV_DONE` + IRQ. I |
| NYET read as ACK | flagged only: `RESP.nyet` when `RXPID == 0x96`; PING not issued; the sim cannot produce NYET (LUNA does not). U |
| `start` while SIE not idle / in reset | dropped, `BUSY_DROP`, `done` raised with resp NONE. I |
| SIE never finishes | watchdog `XFER_LIMIT` = 76000 usb cycles (1.25 x one FS frame + IPD): SIE reset (bus re-resets), `EV_TIMEOUT`. U (no sim; the bound is arithmetic) |

### Cost
- Standalone synthesis, [`soc_peripheral_area.py`](../../scripts/soc/soc_peripheral_area.py): `usb_host` 2279 LUT4-equivalent / 856 DFF (includes the GUH SIE and its two 512x8 FIFOs); `ulpi_window` 148 / 122 for scale. Out-of-context upper bound. In-SoC delta: see [`../aoa-adaptor.md`](../aoa-adaptor.md).

## Registers

**PHY registers are ULPI registers, not SoC registers.** They are reached through
the ULPI register window and are not in our memory map, so they do not appear in
the generated PAC — see [Register reference](../hardware.md#register-reference).
The two used here are the vendor D+/D- swap register `0x39` and the scratch
register `0x16`.

### The interrupt block

The part has no interrupt pin. It reports events **in band**: it asserts `DIR`,
takes the ULPI bus, and sends an RX CMD. Which transitions are worth an RX CMD is
programmed per source and per edge:

| register | address | reset |
|---|---|---|
| USB Interrupt Enable Rising | `0Dh` — set `0Eh`, clear `0Fh` | `1Fh` |
| USB Interrupt Enable Falling | `10h` — set `11h`, clear `12h` | `1Fh` |
| USB Interrupt Status | `13h` | `00h` |
| USB Interrupt Latch | `14h` | `00h` |

All four share one bit layout — five sources:

| bit | field | |
|---|---|---|
| 0 | `HostDisconnect` | UTMI+ HS Hostdisconnect. **Host mode only** |
| 1 | `VbusValid` | UTMI+ Vbusvalid |
| 2 | `SessValid` | UTMI+ SessValid |
| 3 | `SessEnd` | UTMI+ SessEnd |
| 4 | `IdGnd` | UTMI+ IdGnd |
| 7:5 | Reserved | read only, 0 |

`1Fh` is bits 0-4, so **all five are enabled on both edges out of reset** and
every PHY is generating RX CMDs for them now. Nothing here programs the enables
or reads the latch.

**`13h` is not a substitute for `14h`.** Two of the five read 0 in the status
register when both their edge enables are set — which is the reset state:

> *`VbusValid`: "If VbusValid Rise and VbusValid Fall are set this register will
> read 0."*
> *`SessEnd`: "If SessEnd Rise and SessEnd Fall are set this register will read 0."*

`SessValid` is explicitly exempt — it *"will always read the current status of
the Session Valid comparator regardless of the SessValid Rise and SessValid Fall
settings."* So in the default configuration a `VbusValid` or `SessEnd`
transition appears **only** in the latch.

`LineState` and `RxActive` are not in this block. They ride the RX CMD directly,
and `RxActive` changes every packet — which is why the five above are the
interrupt sources and those two are not.

**A transmit delays the RX CMD and staleness its payload.** s6.3.1:

> *"If an RXCMD event occurs during a Hi-Speed USB transmit, the RXCMD is blocked
> until STP deasserts at the end of the transmit. The RXCMD contains the status
> that is current at the time the RXCMD is sent."*

Blocked, not dropped — the RX CMD arrives once the transmit ends. What is lost is
only its **payload's** value for a transition that reverted meanwhile: the bits
describe the world at send time, not at event time.

**The event itself is still captured**, in the latch `14h`, which is set by the
transition and cleared by a read. So the rule is the same one `13h` against `14h`
already sets: **read the latch to learn what happened; do not read the RX CMD's
status bits as if they were the event.** Under bulk load on TARGET the delay is
ordinary, so this is the normal path rather than a corner.

During a receive there is no blocking — RX CMDs go out whenever `NXT = 0` and
`DIR = 1`.

## Scripts

| | |
|---|---|
| `debris/scripts/phy_probe.py` | ID plus a walking-bit test on all 8 data lines, all three PHYs |
| `cynthion selftest` | the shipped equivalent |
