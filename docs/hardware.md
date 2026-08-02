# Cynthion r1.4 hardware — the index

**Every hardware fact has one home. Find it here rather than re-deriving it.**

This board's facts have been re-derived — wrongly — more than once: a power monitor
called an INA when its silicon says PAC1954, an FPGA assumed to have 12,288 LUTs
when 20,143 have been placed and verified, a 64 Mbit HyperRAM read as 4 MiB. Each
of those was already written down when it was re-derived. If something here is
wrong, fix it here; do not work around it in a third file.

Board: **Cynthion r1.4** (`LFE5U-12F` marking, BG256) with the `cynthion_d11`
Apollo build. Measurements are from one board unless stated otherwise; a second has
not been checked. Every assertion is traceable to source in `repos/`,
`ecp5-test/`, a script's output, or a cited commit.

## The chips

| part | what it is | how software reaches it | note |
|---|---|---|---|
| **ECP5 `LFE5U-12F`** | the FPGA — **marked 12F, is a 25F die** | JTAG (Apollo), config from flash | [`chips/lfe5u-12f-ecp5.md`](chips/lfe5u-12f-ecp5.md) |
| **Winbond W25Q32** | 4 MiB SPI config flash, holds the bitstream at offset 0 | SPI/QSPI from the fabric; `apollo flash` | [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md) |
| **Winbond W956A8MBYA6I** | 8 MiB HyperRAM | HyperBus from the fabric; **no CPU path yet** (#90) | [`chips/w956a8-hyperram.md`](chips/w956a8-hyperram.md) |
| **PAC1954-1** | 4-channel power monitor | I2C `0x10` on `power_monitor` | [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md) |
| **FUSB302B ×2** | USB-C PD controllers | I2C `0x22` on `target_type_c` and `aux_type_c` | [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) |
| **USB3343 ×3** | high-speed USB PHYs | **parallel ULPI, not I2C** | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| **ATSAMD11D14A** | the Apollo debug MCU | USB vendor requests on CONTROL | [`chips/samd11-apollo.md`](chips/samd11-apollo.md) |
| TC7USB42MU | USB 2.0 DPDT mux (U16), CONTROL only | Apollo PA06 | [below](#the-control-port-mux) |

The pin map itself is **`ecp5-test/cynthion_platform/cynthion_r1_4.py`** — 206
lines of pin declarations, vendored so that reaching it does not drag in
`LUNAApolloPlatform`, `LUNAPlatform` and a `luna-soc` fork pin. That file is the
authority on which signal is on which ball; the tables below quote it, they do not
replace it.

## Register reference

**SoC peripheral registers come from the SoC's own memory map, not from prose.**

Run `./scripts/soc_generate_pac.py`. It elaborates the SoC far enough to read its
memory map — no synthesis, no place-and-route, no board touched — and emits
`firmware/cynthion-soc-pac/soc.svd` (CMSIS-SVD: 12 peripherals, 55 registers, 96
fields) and the `cynthion-soc-pac` crate from it, via `svd2rust` and `form`. Add
`--svd-only` to stop at the SVD, or `--check` to report whether the committed one
is current without writing anything
(`tmp/logs/soc_generate_pac.log`).

`firmware/cynthion-soc/src/target.rs` takes every hardware address from
`cynthion_soc_pac::base`, so moving a peripheral in the gateware moves the
firmware's constant with it and renaming one is a compile error.

The generator also cross-checks the map it read against the `*_BASE` constants in
`vexii_hello_soc.py` and against the literals in `target.rs`, and refuses to write
anything if they disagree. A disagreement is a defect in one of the three, not a
formatting difference.

**The svd2rust register accessors are not used, and that is on purpose.** Every
CSR here is behind an `amaranth_soc` multiplexer with a granularity of 8 bits,
where a multi-byte register latches a shadow from its low byte and commits on its
high byte; svd2rust emits one natural-width volatile access per register, which is
a different bus transaction. The drivers keep their own byte-level access. The
crate supplies addresses, which are what drifts.

What the memory map does **not** carry is prose: `csr.Register` rewrites
`__doc__` from a template, so every register in the design reports the docstring
"A CSR register" and the SVD's descriptions are mechanical. Bit-level meaning is
only present where the gateware declares separate CSR fields — the 16550's
registers are each one 8-bit field, so `LSR.DR` and `IER.ERBFI` live in
`ecp5-test/riscv/uart16550.py`, and the standard's own offsets and read side
effects are restated once, in
[`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) — they are the
NS16550A's rather than ours, and cannot drift with the memory map.

**Do not go looking for SoC register offsets in the gateware source or in Apollo's
firmware, and do not restate them in a document.** That is precisely how firmware
came to send `0x9f << 24` on the strength of a comment asserting the PHY did not
left-justify, when it does — the hardware and the comment disagreed and nothing
could catch it.

**External chip registers are a different case, and they do live in the chip
notes.** The PAC1954 and the FUSB302B are on I2C, and the USB3343's registers sit
behind a ULPI register window; none of them are in our memory map, so nothing
generates them. Their maps are in
[`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md),
[`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) and
[`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md).

## The buses

| bus | kind | devices | pins |
|---|---|---|---|
| `power_monitor` | I2C, 100 kHz | PAC1954-1 @ `0x10` | SCL D7, SDA C7 |
| `target_type_c` | I2C, 100 kHz | FUSB302B @ `0x22` | SCL A4, SDA C4 |
| `aux_type_c` | I2C, 100 kHz | FUSB302B @ `0x22` | SCL H12, SDA G14 |
| `control_phy` | ULPI, 8-bit, 60 MHz | USB3343 | data N16…P14, clk L14 |
| `aux_phy` | ULPI, 8-bit, 60 MHz | USB3343 | data F16…K16, clk D16 |
| `target_phy` | ULPI, 8-bit, 60 MHz | USB3343 | data R2…M1, clk T4 |
| `spi_flash` / `qspi_flash` | SPI / quad SPI | W25Q32 | T8 T7 (M7 N7), CS N8, SCK via `USRMCLK` |
| `hyper_ram` | HyperBus, DDR | W956A8MBYA6I | dq F2…G4, clk C3/D3, rwds D1, cs B2 |
| JTAG | 4-wire | ECP5 TAP | ECP5 R11 TDI, T11 TMS; MCU PA10/11/14/15 |
| FPGA_ADV | single wire, half-duplex, 230400 8-N-1 | Apollo ↔ ECP5 | MCU PA09 ↔ ECP5 T6 |
| `uart` | async serial | Apollo ↔ ECP5 | ECP5 R14 rx, T14 tx |

### Three I2C buses, and the reason is not a preference

**Both FUSB302Bs are at address `0x22`.** Two devices at the same fixed address
cannot be distinguished on one bus, so the board gives them separate pin-sets.
**"Just use one bus" is not available in hardware** — the mux is forced by the
parts, not chosen by the design. That is what motivates a single I2C controller
with a 2-bit bus select rather than three replicated controllers
([`gateware-architecture-plan.md`](gateware-architecture-plan.md), #98).

`scl` is declared `dir="o"` on all three buses — push-pull, no readback — so
**clock stretching is impossible on this board**. Anything that needs it will not
work, and will not fail visibly.

Only TARGET and AUX have PD controllers. CONTROL has a Type-C connector and no
FUSB302B.

## Pin sharing — the two hazards

### MCU side: PA10/11/14/15 are shared three ways

| MCU pin | JTAG | UART (SERCOM2) | SPI (SERCOM0) | Other |
|---|---|---|---|---|
| PA02 | — | — | — | PROGRAM_BUTTON |
| PA03 | — | — | — | FPGA_INITN (r1.3+) |
| PA04 | — | — | — | FPGA_DONE |
| PA06 | — | — | — | USB_SWITCH → TC7USB42MU |
| PA08 | — | — | — | FPGA_PROGRAM (PROGRAMN) |
| PA09 | — | — | — | FPGA_ADV (EIC EXTINT7) |
| **PA10** | **TDO** | — | **PAD2 (MISO)** | — |
| **PA11** | **TMS** | **RX (PAD3)** | — | — |
| **PA14** | **TDI** | **TX (PAD0)** | **PAD0 (MOSI)** | — |
| **PA15** | **TCK** | — | **PAD1 (SCK)** | — |
| PA16/17/22/23/27 | — | — | — | LED A–E |
| PA30/PA31 | — | — | — | SWCLK / SWDIO (Tag-Connect) |

JTAG and UART cannot both be active. It is enforced with a lock rather than left to
convention: `uart_configure_pinmux()` refuses while `apollo_mode_jtag_active()`, and
the CDC line-coding, line-state and rx-wanted callbacks are gated on the same flag,
because a host opening the console mid-flash would otherwise corrupt an in-flight
configure. **PA08/PA09 are not free, so the UART cannot be relocated on the d11**
(#65/#66).

### FPGA side: R14/T14 are the same wires as JTAG TDI/TMS

The platform file states it directly: *"UART pins R14 and T14 are connected to JTAG
pins R11 (TDI) and T11 (TMS) respectively, so the microcontroller can use either
function but not both simultaneously."* The constraint exists at **both** ends,
which is why it cannot be solved on the MCU alone.

Note the crossover — the FPGA's *rx* (R14) shares with TDI, which is the MCU's *TX*
pin (PA14). Reading one end's table alone tends to produce the wrong pairing.

Detail, and the limits of what has actually been confirmed:
[`chips/samd11-apollo.md`](chips/samd11-apollo.md).

### Other shared pins

- **T8/T7/N8** — SPI and QSPI flash share them; the QSPI resource adds M7/N7.
- **N4/P3** — target USB direct taps, declared three times over at LVDS, LVCMOS33
  and LVCMOS12 (the last for chirp detection).
- **Flash SCK has no ball number** and cannot be requested as a pin; it is reachable
  only through the `USRMCLK` macro, which is what caps flash clocking at a
  characterised 62 MHz.

## Block diagram

```
HOST PC
├─ CONTROL USB (Type-C) ──► TC7USB42MU mux ──┬──► Apollo SAMD11  (1d50:615c)
│                              ▲             └──► control_phy (USB3343) ──► ECP5
│                              │
│                    CONTROL_SWITCH (PA06, MCU output)
│                              │  ▲
│                              │  └── FPGA_ADV keepalive (ECP5 T6 → MCU PA09/EIC7)
│                              │
│                       Apollo ──JTAG (PA10/11/14/15)──► ECP5 TAP (R11/T11/…)
│                       Apollo ──UART (PA11/PA14)─────► ECP5 R14/T14 ──► soft CPU
│                                (same MCU pins as JTAG — mutually exclusive)
│
├─ AUX USB (Type-C) ──► aux_phy (USB3343) ──► ECP5
│
└─ TARGET-C / TARGET-A ──► target_phy (USB3343) + direct D+/D- taps ──► ECP5
                              (analyzer capture, or moondancer/facedancer emulation)
```

USB ports: **three Type-C** (CONTROL, AUX, TARGET-C) plus **TARGET-A** (a type-A
receptacle sharing `target_phy` with TARGET-C). Buttons: **two** — PROGRAM
(MCU-owned, PA02) and USER (FPGA-owned, M14) — plus RESET, which resets the MCU
and, via the normal boot path, the FPGA too. Six FPGA LEDs (E13 C13 B14 A15 D12
C11, active-low) and five MCU LEDs.

Only the **CONTROL** port is muxed. AUX and TARGET are hardwired to their PHYs,
which is why `default_usb_connection = "aux_phy"` — gateware that wants a host link
without fighting Apollo for the control port uses AUX.

## Identity — what enumerates as what

Cynthion enumerates as **one device ID per personality**, distinguished by
interface subclass, **not by PID**.

| Personality | VID:PID | bInterfaceClass | bInterfaceSubClass | Source |
|---|---|---|---|---|
| Apollo debug firmware | `1d50:615c` | 0xFF | — | `repos/apollo/firmware/src/boards/cynthion_d11/apollo_board.h:15-16` |
| Saturn-V DFU bootloader | `1d50:615c` | — | — | `repos/saturn-v/usb.c:33-34` |
| Gateware — Apollo stub / flash bridge | `1d50:615b` | 0xFF | `0x00` | `repos/cynthion/shared/usb.toml:72` |
| Gateware — analyzer | `1d50:615b` | 0xFF | `0x10` | `repos/cynthion/shared/usb.toml:73` |
| Gateware — moondancer/facedancer | `1d50:615b` | 0xFF | `0x20` | `repos/cynthion/shared/usb.toml:74` |

Apollo host-side also accepts `1209:0010` and a set of pid.codes test PIDs
(`1209:0001..0005`, `1209:000f` for the flash bridge) as gateware IDs
(`repos/apollo/apollo_fpga/__init__.py:50-59`). The FlashBridge gateware is a
separate identity, `1209:000f`, whose bulk interface uses subclass `0x01`.

## The CONTROL port mux

The physical mux is a **TC7USB42MU** USB 2.0 DPDT switch (`U16`) in `control_port`,
driven by the `CONTROL_SWITCH` net, which originates as an output from the
`debugger` sheet (Apollo **PA06**) and lands as an input on `control_port`.

| Mux pin | Net | Goes to |
|---|---|---|
| `D+` / `D-` (common) | `CONTROL_D+` / `CONTROL_D-` | CONTROL Type-C receptacle |
| `1D+` / `1D-` (branch 1) | local | USB3343 `control_phy` → ECP5 |
| `2D+` / `2D-` (branch 2) | `CONTROL_MCU_D+` / `-` | Apollo SAMD11 |
| `S` (select) | `CONTROL_SWITCH` | Apollo **PA06** |
| `~OE` | tied active | — (mux always enabled) |

| Mode | `switch_state` | PA06 | Mux `S` | Branch | Terminates at | Host sees | Pre-r0.6 equivalent |
|---|---|---|---|---|---|---|---|
| **Apollo owns CONTROL** | `SWITCH_MCU` (1) | **high**, output | `H` | **2** | Apollo SAMD11 | `1d50:615c`, CDC + vendor iface | PHY_RESET driven low (PHY held in reset) |
| **FPGA owns CONTROL** | `SWITCH_FPGA` (2) | **low**, output | `L` | **1** | `control_phy` → ECP5 | `1d50:615b`, subclass `0x00`/`0x10`/`0x20` | PHY_RESET input + pull-down released |
| **Pre-handoff / boot** | `SWITCH_UNKNOWN` (0) | not yet driven | undriven | indeterminate | — | — | — |

On pre-r0.6 boards there is no mux at all, so `hand_off_usb()`/`take_over_usb()`
tri-state PHY_RESET (PA09) instead.

Both transitions bracket the switch with a USB disconnect, so the host
re-enumerates rather than silently seeing a different device behind the same port:
`hand_off_usb()` calls `tud_disconnect()` **before** flipping `S`;
`take_over_usb()` flips `S` **first**, then disconnect/reconnect. Both are
idempotent (`repos/apollo/firmware/src/usb_switch.c:29-74`).

### Who gets the port, and when

Ownership is **not** driven by configuration completion. It is driven continuously
by the FPGA_ADV keepalive.

| Event | Trigger | Apollo action | Result |
|---|---|---|---|
| Normal boot | RESET, PROGRAM not pressed | `permit_fpga_configuration(true)`; `trigger_fpga_reconfiguration()`; `hand_off_usb()` | FPGA configures from flash and **owns** CONTROL |
| Interrupted boot | PROGRAM held at reset | `force_fpga_offline()`; `take_over_usb()` | Apollo **owns** CONTROL, FPGA held offline |
| Keepalive present **and** `0xc2` sent | >2 FPGA_ADV edges per 200 ms | `hand_off_usb()` | FPGA owns CONTROL |
| Keepalive absent | ≤2 edges in the window | `take_over_usb()` | Apollo reclaims CONTROL |
| Host sends `0xF0` to the stub iface | advertiser `stop` asserted | keepalive ceases | Apollo reclaims on the next window |
| JTAG programming in flight | first programming-class request | conflicting requests STALLed, UART repinmux refused | programming is uninterruptible |
| `0xec` emergency reset | — | `jtag_deinit()`, `allow_fpga_takeover_usb(false)` | control plane returns to HOLD, takeover now disallowed |
| `0xed` boot to DFU | WDT reset into Saturn-V | — | Saturn-V DFU on CONTROL |

`0xc2` (`allow_fpga_takeover_usb`) only sets a **policy** flag: an FPGA that is
happily advertising still will not get the port until the host has sent it.
Conversely `0xec` clears the flag, so after an emergency reset the FPGA cannot take
the port back until the host re-permits it. On boards without a USB switch there is
no advertising channel at all, so it hands off immediately rather than arming a
policy.

### The keepalive has two encodings

Both drive the same ECP5 pin (`int`, T6) into the same MCU edge counter, which only
cares about edge *rate*.

| Variant | Waveform | Used by |
|---|---|---|
| Upstream `ApolloAdvertiser` | 50 Hz square wave (toggles every 10 ms) | analyzer gateware |
| Local `PatternUartStreamer` | `C1 14 01 A5` 8-N-1 burst @ 1 Mbaud, every 100 ms | facedancer gateware |

Detection window **200 ms**, threshold **>2 edges**. The read/clear of
`edge_counter` is wrapped in `NVIC_DisableIRQ`/`EnableIRQ` so an edge landing
between the two statements is not dropped (`fpga_adv.c:94-97`).

## Datapaths

### Control plane — host → Apollo MCU

All require the CONTROL mux on **branch 2**. If the FPGA holds the port, the host
must first stop the keepalive (vendor `0xF0` to the stub interface).

| # | Path | Transport | MCU module | Physical link | Endpoint |
|---|---|---|---|---|---|
| 1 | **JTAG over USB** | vendor `0xb0`–`0xb7`, `0xbe`, `0xbf` | `jtag.c` / `jtag_tap.c`, bit-banged | PA15=TCK, PA10=TDO, PA14=TDI, PA11=TMS | ECP5 TAP |
| 2 | **UART / console** | CDC bulk (one interface) | `console.c` + `uart.c` (SERCOM2) | PA14=TX, PA11=RX → ECP5 R14/T14 | soft CPU |
| 3 | **FPGA reconfigure** | vendor `0xc0` | `fpga.c` | PA08 = FPGA_PROGRAM, open-drain | ECP5 PROGRAMN |
| 4 | **Force FPGA offline** | vendor `0xc1` | `fpga.c` | PA03 = FPGA_INITN (r1.3+) | ECP5 INITN |
| 5 | **Allow USB takeover** | vendor `0xc2` | `fpga_adv.c` → `usb_switch.c` | PA06 | CONTROL mux |
| 6 | **Emergency reset** | vendor `0xec` | `vendor.c` | — | control-plane state |
| 7 | **Boot to DFU** | vendor `0xed`, deferred to the ACK stage | `vendor.c` → TinyUSB DFU-runtime | WDT reset | Saturn-V |
| 8 | **LED pattern** | vendor `0xa1` | `led.c` | PA16/17/22/23/27 | 5 MCU LEDs |
| 9 | **ADC / rail voltage** | vendor `0xa4` | `board_rev.c` | rev-detect divider | board revision |
| 10 | **Microsoft WCID** | vendor `0xee` | `vendor.c` const descriptors | — | Windows WinUSB bind |

### FPGA-side and sideband

| # | Path | Transport | Physical link | Sink |
|---|---|---|---|---|
| 11 | **FPGA_ADV keepalive** | see above | ECP5 T6 → MCU PA09 (EIC EXTINT7) | `fpga_adv.c` edge counter |
| 12 | **Advertiser stop** | vendor `0xF0`, recipient=INTERFACE | — (asserts `stop`) | Apollo reclaims within one 200 ms window |
| 13 | **JTAG-tunnelled SPI** | JTAG opcodes `0x32` (ER1) / `0x38` (ER2) | same pins as path 1 | ECP5 user-fabric registers, ILA |
| 14 | **Config flash — slow** | JTAG, whole image bit-banged | T8=SDI, T7=SDO, N8=CS, SCK via `USRMCLK` | SPI config flash |
| 14b | **Config flash — `--fast`** | USB bulk to `1209:000f`, EP1, 512 B packets — **not** through Apollo | same flash pins | SPI config flash |
| 15 | **Self-reprogram** | GPIO assert | ECP5 T13 → PROGRAMN | ECP5 reconfigures itself |
| 16 | **USER button** | GPIO | ECP5 M14, active-low (`PinsN`) | gateware-defined |
| 17 | **PROGRAM button** | GPIO, sampled at boot and in `button_task()` | MCU PA02 (PA16, shared with LED_A, on < r0.6) | forces FPGA offline + takeover |
| 18 | **PMOD A** | 8 GPIO | C9 B9 D11 C12 / C8 D8 D9 C10 | external PMOD |
| 19 | **PMOD B** | 8 GPIO | B4 B5 B6 B7 / C5 A5 A6 A7 | external PMOD |
| 20 | **Mezzanine** | 22 GPIO, `SLEWRATE=FAST` | B8 A9 B10 A10 B11 D14 C14 F14 E14 G13 G12 C16 C15 B16 B15 A14 B13 A13 D13 A12 B12 A11 | mezzanine board |
| 21 | **SWD (MCU debug)** | SWD — **not** routed through USB | PA30=SWCLK, PA31=SWDIO, Tag-Connect TC2030-CTX | SAMD11 core |

### USB data paths — the instrument's actual job

| # | Path | Port | Mux setting | PHY | Gateware | Host software |
|---|---|---|---|---|---|---|
| 22 | **Analyzer capture** | TARGET-C in / TARGET-A out | n/a | `target_phy` + direct taps N4/P3 | `analyzer/top.py`, subclass `0x10` | `cynthion` CLI / Packetry |
| 23 | **Facedancer emulation** | TARGET-C | n/a | `target_phy` | `facedancer/top.py`, subclass `0x20` | `facedancer` Python |
| 24 | **Gateware→host bulk** | CONTROL | **branch 1** | `control_phy` | analyzer or facedancer top | as above |
| 25 | **AUX port** | AUX | n/a — not muxed | `aux_phy` | `default_usb_connection` | as above |

## `flash --fast` — the deliberate-handoff path

Every other host operation treats an FPGA-owned CONTROL port as an obstacle to be
cleared. This one **hands the port to the FPGA on purpose**, because the thing it
wants to talk to is FPGA gateware, not Apollo.

| Step | Action | Mux | Host talks to |
|---|---|---|---|
| 1 | build or fetch a `FlashBridge` bitstream (cached under `$XDG_CACHE_HOME/apollo/build/<plan-digest>`, so the Amaranth/nextpnr build is paid once) | `S`=H | Apollo `1d50:615c` |
| 2 | `programmer.configure(...)` — load the bridge into FPGA **SRAM** over JTAG. Volatile: this configures, it does not flash. | `S`=H | Apollo |
| 3 | `allow_fpga_takeover_usb()` (`0xc2`) — the bridge is already advertising, so the next window hands the port over | **flips to `S`=L** | — (re-enumeration) |
| 4 | enumerate `1209:000f`, match class `0xFF` / subclass `0x01`, grab EP1 | `S`=L | **FPGA gateware** |
| 5 | stream the image over USB bulk; the FPGA drives its own SPI flash pins | `S`=L | FPGA gateware |
| 6 | `__del__` → `request_handoff()` (`0xF0`) — stop advertising, give the port back | back to `S`=H | Apollo |

Why it is faster: in the slow path every flash byte is bit-banged through Apollo's
software JTAG TAP on a SAMD11. In the fast path the SAMD11 is not in the data path
at all — bytes go host → USB bulk → ECP5 fabric → SPI flash, at PHY speed.

The bridge descriptor carries **two** interfaces: the bulk flash interface (`0x01`)
and the Apollo stub (`0x00`) that makes step 6 possible. Without the stub, handing
the port back would need a power cycle. The `0x01..0x0f` range is "Reserved" in
`usb.toml`, which describes *Cynthion* gateware; the Apollo-side FlashBridge uses
`0x01` under its own `1209:000f` ID, so the two namespaces do not collide.

`flash-fast` as a standalone subcommand is **deprecated** — it warns and forwards
to `flash --fast`.

## Recovery ladder

Cheapest first. **Power cycle is the fallback, not the requirement.**

1. `cyn reset` — soft reset via Apollo.
2. Vendor `0xF0` to the stub interface (what `--force-offline` sends) — stops the
   keepalive, Apollo reclaims the port.
3. Vendor `0xec` — emergency reset; the only legal preemption of an active JTAG
   session.
4. Hold PROGRAM and press RESET — deterministic, forces the FPGA offline at boot.
5. Power cycle ([#15](https://github.com/awtoau/cynthion-workspace/issues/15)).

## Claims that keep coming back wrong

Each of these has been asserted in this repository and does not survive checking.
They are kept here because they recur.

| Claim | Actual |
|---|---|
| "The power monitor is an INA3221 or similar" | **PAC1954-1.** `PRODUCT_ID` reads `0x7B`, `MANUFACTURER_ID` `0x54`, read from silicon by two independent paths. |
| "The ECP5 has 12,288 LUT4s" | **20,143 have been placed, routed and verified** on a 24,288-LUT die. |
| "The HyperRAM is 4 MiB" (from "64 Mbit") | **8 MiB.** 64 Mbit ÷ 8 = 8 MiB. Three successive wrong explanations came from that one arithmetic slip. |
| "Both FUSB302Bs could share a bus" | They are both at `0x22`. They cannot. |
| "USB VID:PID `1d50:615b` for everything" | Apollo is **`1d50:615c`**; only gateware is `615b`. |
| "The Apollo bootloader is `1d50:60e6`" | Saturn-V uses **`1d50:615c`**, the same ID as Apollo firmware. `60e6` appears nowhere in the tree. |
| "`1d50:615b` means the board is not ready for DFU flashing" | It means gateware is running. A `0xF0` advertiser-stop or `--force-offline` handoff recovers the port. Distinguishing DFU from Apollo firmware needs the **interface descriptor**, not the PID. |
| "Subclass `0x10` = analyzer, `0x20` = moondancer" | Correct but incomplete — it omits **`0x00` = Apollo stub / flash bridge**, which is the subclass the handoff path actually matches on. |
| "At boot Apollo holds the port and the FPGA is in reset" | **Inverted.** Normal boot reconfigures the FPGA and hands it the port. Apollo only holds it when PROGRAM is pressed at boot. |
| "Configuration completing is what makes Apollo cede CONTROL" | Ownership is driven **continuously** by the FPGA_ADV keepalive, and ceding also requires the host to have sent `0xc2`. |
| "Hung firmware needs a power cycle" | See the recovery ladder. `0xec` and `0xed` remain permitted even mid-JTAG-programming. |
| "Apollo has a debug SPI to the FPGA" | The handlers exist but are gated on `_BOARD_HAS_DEBUG_SPI`, which `cynthion_d11` does not define. Those requests STALL. SPI goes via the JTAG ER1/ER2 tunnel. |
| "`spi.c` uses PA08/PA09/PA10" | Its **comment** says that. The code below muxes PA14/PA15/PA10. PA08 is FPGA_PROGRAM and PA09 is FPGA_ADV. |
| "ttyACM0/1/2 — three consoles" | Still a plan. `CFG_TUD_CDC` is **1**, so exactly one CDC interface exists. |

## The SoC shell — reaching this hardware from a prompt

The RISC-V SoC (`ecp5-test/riscv/vexii_hello_soc.py`, firmware
`firmware/cynthion-soc/`) answers a line-oriented shell on **both** its consoles:
the USB CDC node on AUX, and the Apollo-facing port on the shared JTAG pins.

```bash
python3 scripts/soc_run.py                       # build, load, read the console
python3 scripts/soc_shell.py help                # the USB console
python3 scripts/soc_shell.py --port /dev/ttyACM0 help   # the Apollo console
```

`help` in the shell is the authoritative list; the table below is what each
command is *for*. Anything hardware-specific is in that chip's note.

| command | what it reports | chip note |
|---|---|---|
| `check` | CPU arithmetic and two known flash words | — |
| `id`, `read <hex>` | the memory-mapped config flash | [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md) |
| `ports` | which 16550s answer | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |
| `irq` | PLIC pending/enabled, per-console interrupt counts, deferred-log health, per-console `lost` | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |
| `log [n]` | push *n* deferred events, as an interrupt handler would | below |
| `led [colour on\|off\|fabric]` | the six LEDs, the button, PWRDN | — |
| `i2c [power\|target\|aux]` | scan one of the three I2C buses and identify what answers | below |
| `power [floor <port> <mA>]` | the four rails, and the change reporting | [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md) |
| `phy` | the TARGET USB3343's ULPI registers, plus a walking-bit test | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| `typec [init]` | both FUSB302B controllers, their CC and VBUS state, `int`/`fault` | [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) |
| `sideband [hex]` | what the FPGA_ADV link reports | — |
| `load <hex>`, `go`, `reset` | stage and run a payload | — |

**`power`** reads all four rails on demand and prints, per port, bus volts,
current in milliamps, and whether that port is above its own floor. Units are
volts and milliamps throughout; the floor is set in milliamps and stored in
microamps.

Separately, the firmware **polls the monitor every 50 ms in the background** and
prints a line only when a rail moves by **100 mA or more**, or crosses its floor
in either direction. That threshold is why the console is not a wall of text at
twenty samples a second, and the floor is why an unplugged port — which measures
0.76–0.92 mA of ADC offset — does not emit events from noise. Background lines go
to the **USB console only**: the second port's TX pin is JTAG TMS and this
firmware never transmits there unbidden.

Ports are named — `target_a`, `target_c`, `aux`, `control` — and never numbered,
because the PAC's channel order is not the port order anyone would guess.

**`irq`** ends with `log  waiting N dropped M`. That is the deferred log an
interrupt handler writes to instead of printing — a handler that prints spins on
a UART FIFO inside an interrupt, which on a level-sensitive shared source is a
hang that presents as a dead CPU. Handlers record a code and two words; the main
loop formats and prints them.

**`dropped` is the number that matters.** The ring is bounded and the push is
wait-free, so a storm degrades to lost lines rather than a stalled handler — and
a queue that quietly discards under exactly the conditions you most want to see
is worse than no queue. A nonzero count is not by itself a fault (a burst outran
the shell once); a count that keeps climbing means events are being lost
continuously. The shell also prints `irq log: N event(s) LOST` when it notices,
so a loss does not wait for someone to type `irq`.

`log [n]` pushes *n* test records through the same path, which is how fill, wrap
and drop counting are exercised — including under QEMU, where
`scripts/soc_test.py` drives it. The ring holds 15 records, so `log 20` reports
`pushed 15 of 20`.

Printing from a handler is a **compile error**, not a convention:
`firmware/cynthion-soc/src/irq.rs` holds a `UartRx`, which has no transmit
method and no `core::fmt::Write`. `scripts/soc_irq_log_check.py` (the `irqlog`
check in `scripts/check.py`) covers what the compiler cannot — Rust's privacy
cannot stop a sibling module naming `Uart`.

**`i2c <bus>`** names the bus rather than remembering one. There is a single I2C
controller behind a two-bit select driving three pin-sets — forced by both
FUSB302Bs answering `0x22` — and nothing in a reply says which bus it came from,
so a stale select gives a plausible answer from the wrong chip rather than an
error. The select resets to the power monitor, so the rails are readable before
firmware writes anything.

**`typec`** reports both controllers live: device id, VBUS, the CC voltage band,
and the raw `int`/`fault` lines. A state change is *not* polled — the controllers
are configured at boot to interrupt, both `int` lines are OR-ed onto one PLIC
source, and the line is shared and level-sensitive. The handler masks the source
and records the event; the main loop clears **every** asserting device and
re-enables it. A `type-c <port>: vbus …, <cc>` line means that port's state
changed and what it changed to. `fault` is deliberately outside the interrupt and
polled at 50 ms, because it means something different and is worth telling apart
without a register read.

**`phy`** reports the TARGET PHY's vendor and product IDs, function and OTG
control, line state, and a walking-bit test across the scratch register. It reads
**`target_phy` only**: AUX carries the USB console the answer travels over and
CONTROL is shared with Apollo, so a register master on either would corrupt a
link something else is using. An absent PHY reports `no answer from the PHY`
after a 68 µs gateware timeout, not zeros.

## Also worth knowing

- **`int` (T6)** is the FPGA_ADV keepalive and sideband line, not a
  general-purpose interrupt. It is declared `PULLMODE="UP"` because the ECP5
  defaults to pull-*down*, which fights Apollo's PA09 pull-up.
- **D+/D- are swapped on the board** and corrected inside the PHYs by a vendor
  register write the platform performs. See
  [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md).
- **UTi261M** — UNI-T thermal imaging camera, `0bda:5830` (Realtek UVC), the target
  device used for facedancer proxy work: attached to TARGET-C, proxied out through
  TARGET-A.

## Where else to look

| topic | doc |
|---|---|
| every alternative weighed, and why, in tables | [`comparisons.md`](comparisons.md) |
| what we take from upstream and what we replaced | [`upstream-boundary.md`](upstream-boundary.md) |
| making the test gateware reusable by the CPU | [`gateware-architecture-plan.md`](gateware-architecture-plan.md) |
| how fast the soft CPU can be clocked on this part | [`riscv-clock-ceiling.md`](riscv-clock-ceiling.md) |
| flash, HyperRAM, USB and BRAM in depth | [`luna_ecp5_fpga/`](luna_ecp5_fpga/) |
| Apollo firmware — reviews, races, DFU, serial, configure speed | [`apollo_samd11_mcu/`](apollo_samd11_mcu/) |
| the soft CPU and moondancer | [`moondancer/`](moondancer/) |
| toolchain | [`toolchain-versions.md`](toolchain-versions.md), [`toolchain-simplification.md`](toolchain-simplification.md) |
| workspace CLI | [`cyn.md`](cyn.md) |
