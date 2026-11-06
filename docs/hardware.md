# Cynthion r1.4 hardware — the index

Every hardware fact has one home; find it here. If something here is wrong, fix it here.

- Board: **Cynthion r1.4** (`LFE5U-12F` marking, BG256), `cynthion_d11` Apollo build.
- The SoC needs the Apollo **fork** firmware: [`apollo-firmware.md`](apollo-firmware.md); see also [`chips/samd11-apollo.md`](chips/samd11-apollo.md).
- Measurements are from one board; a second has not been checked.
- Assertions trace to `repos/`, `gateware/`, a script's output, or a cited commit.

## The chips

| part | what it is | how software reaches it | note |
|---|---|---|---|
| **ECP5 `LFE5U-12F`** | the FPGA — **marked 12F, is a 25F die**; 20,143 LUT4 placed and verified on a 24,288-LUT die | JTAG (Apollo), config from flash | [`chips/ecp5/lfe5u-12f.md`](chips/ecp5/lfe5u-12f.md) |
| **Winbond W25Q32JV-IQ** | 4 MiB SPI config flash, bitstream at offset 0 | SPI/QSPI from the fabric; `apollo flash` | [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md) |
| **Winbond W956A8MBYA6I** | 8 MiB HyperRAM (64 Mbit ÷ 8) | register interface on the [#581](https://github.com/awtoau/cynthion-workspace/issues/581) core at CK 180, `0xf0000800` ([#588](https://github.com/awtoau/cynthion-workspace/issues/588)) | [`chips/hyperram/`](chips/hyperram/README.md) |
| **PAC1954-1** | 4-channel power monitor (`PRODUCT_ID` `0x7B`, `MANUFACTURER_ID` `0x54`) | I2C `0x10` on `power_monitor` | [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md) |
| **FUSB302B ×2** | USB-C PD controllers | I2C `0x22` on `target_type_c` and `aux_type_c` | [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) |
| **USB3343 ×3** | high-speed USB PHYs | **parallel ULPI, not I2C** | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| **ATSAMD11D14A** | the Apollo debug MCU | USB vendor requests on CONTROL | [`chips/samd11-apollo.md`](chips/samd11-apollo.md) |
| PI3USB102G | USB 2.0 DPDT mux (U16), CONTROL only | Apollo PA06 | [below](#the-control-port-mux) |

- Making the flash faster: [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md).
- Every bus rate against what its parts support (rated | board | actual): [`chips/bus-speed-audit.md`](chips/bus-speed-audit.md).

| soft core | what it is | note |
|---|---|---|
| **VexiiRiscv** | the SoC's RV32IMAC CPU, generated at elaboration | [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md); regenerating: [`riscv-core-build.md`](riscv-core-build.md) |
| **NS16550A ×2** | the console UARTs | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |

Pin map: [`gateware/board/cynthion_r1_4.py`](../gateware/board/cynthion_r1_4.py) (283 lines), vendored so reaching it does not drag in `LUNAApolloPlatform`, `LUNAPlatform` and a `luna-soc` fork pin. It is the authority on signal-to-ball; the tables below quote it.

## Register reference

- SoC peripheral registers come from the SoC's memory map, not prose. Do not look for offsets in gateware source or Apollo firmware; do not restate them in a document.
- `python3 scripts/soc/soc_generate_pac.py` elaborates the SoC far enough to read the memory map (no synthesis/PnR, no board) and emits [`firmware/cynthion-soc-pac/soc.svd`](../firmware/cynthion-soc-pac/soc.svd) (CMSIS-SVD) and the `cynthion-soc-pac` crate via `svd2rust` and `form`.
  - SVD currently has 21 `<peripheral>`, 146 `<register>`, 210 `<field>` elements (counted from the file).
  - `--svd-only` stops at the SVD; `--check` reports whether the committed one is current, writing nothing. Log: `tmp/logs/soc_generate_pac.log`.
- [`firmware/cynthion-soc/src/target.rs`](../firmware/cynthion-soc/src/target.rs) takes every address from `cynthion_soc_pac::base`: moving a peripheral moves the constant, renaming one is a compile error.
- The generator cross-checks the map against the `*_BASE` constants in `top.py` and the literals in `target.rs`; it writes nothing if they disagree.
- svd2rust accessors are **not used**: every CSR is behind an `amaranth_soc` multiplexer with 8-bit granularity (multi-byte register latches a shadow on the low byte, commits on the high byte); svd2rust emits one natural-width access, a different bus transaction. Drivers keep byte-level access; the crate supplies addresses only.
- The map carries no prose: `csr.Register` rewrites `__doc__` from a template. Bit-level meaning exists only where the gateware declares separate CSR fields. 16550 registers are each one 8-bit field (`LSR.DR`, `IER.ERBFI` in [`gateware/soc/peripherals/uart16550.py`](../gateware/soc/peripherals/uart16550.py)); standard offsets and read side effects are in [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md).
- External chip registers (PAC1954, FUSB302B on I2C; USB3343 behind a ULPI register window) are not in our map; their maps live in [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md), [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md), [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md).

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
| FPGA_ADV | single wire, half-duplex — [`chips/cynone-sideband.md`](chips/cynone-sideband.md) | Apollo ↔ ECP5 | MCU PA09 ↔ ECP5 T6 |
| `uart` | async serial | Apollo ↔ ECP5 | ECP5 R14 rx, T14 tx |

### Three I2C buses (forced by the parts)

- Both FUSB302Bs answer `0x22`, so each gets its own pin-set. "One bus" is not available in hardware.
- One I2C controller with a 2-bit bus select, not three replicated controllers: [`gateware-architecture-plan.md`](gateware-architecture-plan.md), [#98](https://github.com/awtoau/cynthion-workspace/issues/98).
- `scl` is `dir="o"` on all three (push-pull, no readback): **clock stretching is impossible**; anything needing it fails silently.
- Only TARGET and AUX have FUSB302Bs. CONTROL has a Type-C connector and none.

## Pin sharing

### MCU: PA10/11/14/15 shared three ways (JTAG / UART / SPI)

Canonical table, enforcement lock and limits: [`chips/samd11-apollo.md`](chips/samd11-apollo.md). Other MCU pins:

| MCU pin | function |
|---|---|
| PA02 | PROGRAM_BUTTON |
| PA03 | FPGA_INITN (r1.3+) |
| PA04 | FPGA_DONE |
| PA06 | USB_SWITCH → PI3USB102G |
| PA08 | FPGA_PROGRAM (PROGRAMN) |
| PA09 | FPGA_ADV (EIC EXTINT7) |
| PA16/17/22/23/27 | LED A–E |
| PA30/PA31 | SWCLK / SWDIO (Tag-Connect) |

- PA08/PA09 are not free, so the UART cannot be relocated on the d11 ([#65](https://github.com/awtoau/cynthion-workspace/issues/65)/#66).

### FPGA: R14/T14 are the same wires as JTAG TDI/TMS

- Platform file: UART pins R14 and T14 connect to JTAG R11 (TDI) and T11 (TMS); the MCU can use either function, not both. The constraint exists at both ends.
- Crossover: the FPGA's *rx* (R14) shares with TDI, the MCU's *TX* pin (PA14).

### Other shared pins

- **T8/T7/N8** — SPI and QSPI flash share them; QSPI adds M7/N7.
- **N4/P3** — target USB direct taps, declared three times (LVDS, LVCMOS33, LVCMOS12 for chirp detection).
- **Flash SCK has no ball**; reachable only via the `USRMCLK` macro: no DDR, no vendor fmax (the 62 MHz is fCCLK, not a USRMCLK limit; see [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md#usrmclk-sck)).

## Block diagram

```
HOST PC
├─ CONTROL USB (Type-C) ──► PI3USB102G mux ──┬──► Apollo SAMD11  (1d50:615c)
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

- USB ports: **three Type-C** (CONTROL, AUX, TARGET-C) plus **TARGET-A** (type-A, shares `target_phy` with TARGET-C).
- Buttons: PROGRAM (MCU, PA02), USER (FPGA, M14), RESET (resets MCU and, via the normal boot path, the FPGA).
- LEDs: six FPGA (E13 C13 B14 A15 D12 C11, active-low) and five MCU.
- Only **CONTROL** is muxed. AUX and TARGET are hardwired to their PHYs, hence `default_usb_connection = "aux_phy"`.

### The six FPGA LEDs

- Declared `LEDResources(pins="E13 C13 B14 A15 D12 C11", invert=True)`; gateware drives active-high.
- Colour order is the schematic's, not the platform's: `repos/cynthion-hardware/indicators_buttons.kicad_sch`, `Value` field of each diode ([#415](https://github.com/awtoau/cynthion-workspace/issues/415)). `led` prints ball and schematic reference beside each colour.

| # | ball | sch | colour | driven by | behaviour |
|---|---|---|---|---|---|
| 0 | E13 | D7 | violet | — | **unassigned**, dark |
| 1 | C13 | D6 | blue | `ever_console` | solid, latched |
| 2 | B14 | D5 | green | `serial.connect` | solid while USB is up |
| 3 | A15 | D4 | **yellow** | fabric counter | **flashing, 1 Hz** |
| 4 | D12 | D3 | **orange** | RTIC task, firmware | **flashing, 2 Hz** |
| 5 | C11 | D2 | **red** | `ever_errored` | latched — **off is good** |

| what you see | what it means |
|---|---|
| yellow **and** orange flashing, red dark | working normally |
| yellow flashing, **orange frozen** | FPGA runs; **OS stopped scheduling** |
| yellow frozen | no clock, or PLL lost lock |
| **red lit** | a bus error occurred, whatever else looks right |
| orange dark from boot | firmware never ran (fabric default is dark on purpose) |
| nothing at all | no bitstream, or no power |

- Yellow: fabric counter off the solved `sync` clock; alive with a dead CPU. Orange: periodic RTIC task at lowest priority; OS is scheduling with slack. Adjacent, 2:1 rate, checkable by eye.
- Latched lamp = "has this ever happened" (a bus error is one cycle). Flashing beats solid: a stuck-high pin looks like a design held in reset.
- No `ever_fetched` / `ever_io` lamp: both latch within microseconds of boot ([#411](https://github.com/awtoau/cynthion-workspace/issues/411)).
- Heartbeat divider is derived from `SYNC_MHZ` in `top.py`, as is `SidebandDebug`'s; a hardcoded one there gives a **dead** link ([`chips/cynone-sideband.md`](chips/cynone-sideband.md#the-bit-period-is-fixed-at-build-time-on-the-fpga-side)), [#111](https://github.com/awtoau/cynthion-workspace/issues/111).

## Identity — what enumerates as what

One device ID per personality, distinguished by interface subclass, **not PID**.

| Personality | VID:PID | bInterfaceClass | bInterfaceSubClass | Source |
|---|---|---|---|---|
| Apollo debug firmware | `1d50:615c` | 0xFF | — | `repos/apollo/firmware/src/boards/cynthion_d11/apollo_board.h:15-16` |
| Saturn-V DFU bootloader | `1d50:615c` | — | — | `repos/saturn-v/usb.c:33-34` |
| Gateware — Apollo stub / flash bridge | `1d50:615b` | 0xFF | `0x00` | `repos/cynthion/shared/usb.toml:72` |
| Gateware — analyzer | `1d50:615b` | 0xFF | `0x10` | `repos/cynthion/shared/usb.toml:73` |
| Gateware — moondancer/facedancer | `1d50:615b` | 0xFF | `0x20` | `repos/cynthion/shared/usb.toml:74` |

- `1d50:615b` means gateware is running (not "unready for DFU"); a `0xF0` advertiser-stop or `--force-offline` handoff recovers the port. DFU vs Apollo firmware needs the interface descriptor, not the PID.
- Apollo host-side also accepts `1209:0010` and pid.codes test PIDs (`1209:0001..0005`) as gateware IDs (`repos/apollo/apollo_fpga/__init__.py:50-59`).
- FlashBridge gateware is `1209:000f`; its bulk interface uses subclass `0x01`. `0x01..0x0f` is "Reserved" in `usb.toml` (Cynthion gateware); the namespaces do not collide.

## The CONTROL port mux

`U16` PI3USB102G (Diodes 10-TQFN) in `control_port` (`production/bom.csv`), driven by `CONTROL_SWITCH`, an output of the `debugger` sheet (Apollo **PA06**).

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

- Pre-r0.6 boards have no mux: `hand_off_usb()`/`take_over_usb()` tri-state PHY_RESET (PA09).
- Both transitions bracket the switch with a USB disconnect (host re-enumerates): `hand_off_usb()` calls `tud_disconnect()` **before** flipping `S`; `take_over_usb()` flips `S` **first**, then disconnect/reconnect. Both idempotent (`repos/apollo/firmware/src/usb_switch.c:29-74`).

### Who gets the port, and when

Ownership is driven continuously by the FPGA_ADV keepalive, not by configuration completion.

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

- `0xc2` (`allow_fpga_takeover_usb`) only sets a policy flag: an advertising FPGA does not get the port until the host sent it. `0xec` clears it. Boards without a USB switch have no advertising channel and hand off immediately.

### The keepalive has two encodings

Both drive ECP5 `int` (T6); Apollo's EIC edge counter reads only edge rate.

| Variant | Waveform | Used by |
|---|---|---|
| Upstream `ApolloAdvertiser` | 50 Hz square wave (toggles every 10 ms) | analyzer gateware |
| Local `PatternUartStreamer` | `C1 14 01 A5` 8-N-1 burst @ 1 Mbaud, every 100 ms | facedancer gateware |

- Detection window **200 ms**, threshold **>2 edges**. The `edge_counter` read/clear is wrapped in `NVIC_DisableIRQ`/`EnableIRQ` so an edge between the two statements is not dropped (`fpga_adv.c:682-697`).
- The same wire carries the sideband command protocol and a third (framed) encoding that Apollo's UART mode matches: [`chips/cynone-sideband.md`](chips/cynone-sideband.md#8-the-second-job-the-control-port-request), which also covers why `PatternUartStreamer`'s 1 Mbaud cannot be decoded by Apollo's 230400-baud receiver.

## The VBUS power tree

The board can power a target: a switch per port. It is a **passthrough, not a regulator**; TARGET A is powered from whichever upstream port feeds the board; nothing generates 5 V.

| resource | pin | direction | role |
|---|---|---|---|
| `control_vbus_in_en` | K13 | active low | power **input** shutoff, CONTROL |
| `aux_vbus_in_en` | L13 | active low | power **input** shutoff, AUX |
| `control_vbus_en` | L1 | | pass CONTROL's VBUS → TARGET A |
| `aux_vbus_en` | L2 | | pass AUX's VBUS → TARGET A |
| `target_c_vbus_en` | K5 | | pass TARGET-C's VBUS → TARGET A |
| `target_a_discharge` | K4 | | actively discharge TARGET A |

- Authority: `repos/cynthion/.../platform/cynthion_r1_4.py`: "VBUS on each of the Type-C ports can be connected to TARGET A through a bidirectional switch. If any of these switches is enabled, TARGET A is considered an output."
- **TARGET A is the common node.** Each `*_vbus_en` joins its port to that rail; closing two routes ONE supply to TWO destinations. Passing power from some other port *to* AUX needs `aux_vbus_en` plus `target_c_vbus_en` or `control_vbus_en` (mossmann, [cynthion#184](https://github.com/greatscottgadgets/cynthion/issues/184)).

### Input and passthrough are separate

From GSG's [Cynthion Hardware Design Update](https://www.crowdsupply.com/great-scott-gadgets/cynthion/updates/cynthion-hardware-design-update) (r0.6 redesign, inherited by r1.4):

- Passthrough up to **20 V** (top of PD SPR).
- A 5 V supply is still required on CONTROL or AUX to power Cynthion.
- Overvoltage protection shuts off either input above **5.5 V**; it does not affect passthrough. No diode drop on passthrough.
- All four ports have voltage and current monitoring (PAC1954) for flexible OVP/OCP/reverse-current protection of external hosts/devices, with slower response than the internal OVP.
- Threshold: `D17`, 5.6 V ±2% zener (5.49–5.71 V) in `repos/cynthion-hardware/power_distribution.kicad_sch`. No firmware involved.
- Each of CONTROL and AUX has two independent shutoffs: hardware OVP, and FPGA control of the active-low `*_vbus_in_en`.

### CC / SBU protection

- `DPO2036DBB-7` ("4-CH OVER-VOLTAGE PROTECTION FOR CC/SBU PINS ON USB TYPE-C", `type_c.kicad_sch`), two of them: **`U13`** on TARGET, **`U14`** on AUX (one sheet instantiated per port; refdes from the instance path). [#156](https://github.com/awtoau/cynthion-workspace/issues/156). Detail: [`chips/dpo2036-cc-sbu-protection.md`](chips/dpo2036-cc-sbu-protection.md).

| protection | guards | mechanism |
|---|---|---|
| `D17`, 5.6 V zener | VBUS inputs (CONTROL, AUX) | hardware shutoff above 5.5 V |
| **`U13`/`U14` DPO2036** | **CC1/CC2, SBU1/SBU2** | series pass, opens on over-voltage; `FAULTB` open-collector |
| PAC1954 | all four ports | firmware policy, slower |

- Why: VBUS may carry 20 V; a short of VBUS onto CC would take out the FUSB302B, and via SBU would drive FPGA I/O.
- [#97](https://github.com/awtoau/cynthion-workspace/issues/97): all four SBU pins passed drive-and-readback through the DPO2036 (DC continuity only).

### Firmware's role

The board defends itself; firmware defends the attached device. 20 V passthrough is designed behaviour. A USB-A peripheral on TARGET A cannot refuse 20 V, so a firmware guard there is user protection, built on the PAC1954 (slower than OVP, the only option for passthrough).

### Pass elements — open question

`repos/cynthion-hardware/power_distribution.kicad_sch` is the authority:

| what | count | designators |
|---|---|---|
| pass MOSFET, `SIA483ADJ-T1-GE3`, P-channel | **6** | Q1, Q2, Q4, Q5, Q6, Q7 |
| `BC847BS` dual NPN, gate drive | 6 | |
| `PAC195X-1-VQFN` monitor | 1 | same sheet |

- Six devices for six functions (3 passthroughs, 2 input shutoffs, discharge): one per function, not back-to-back pairs.
- **Open:** a single P-FET's body diode conducts regardless of gate, so if these are singles, "switch open" is one-direction-only, not isolation.
  - Symbol `Transistor_FET:SiA449DJ` has 3 pins (D, G, S): a single device.
  - Part number `SIA483ADJ-T1-GE3` differs from the symbol name; Vishay's `DJ` suffix is used on duals in this package.
  - The Vishay `SIA483ADJ` datasheet is not in `sources/`; it settles it.
- Until settled, treat an open switch as "not driven", not "isolated"; no firmware or self-test may rely on an open switch blocking current.

### What is safe to command

| situation | closing one passthrough | closing two |
|---|---|---|
| one port sourcing, others idle | **safe** | **safe** (what the second switch is for) |
| two ports both sourcing | safe, if it is the sourcing one | **NOT ESTABLISHED** — ties two supplies through the shared rail |
| source above 5.5 V (PD) | board fine; **attached USB-A device is not** | as left |
| any, with `target_a_discharge` closed | **NOT ESTABLISHED** — discharging a live rail | as left |

- [`firmware/cynthion-soc/src/vbus.rs`](../firmware/cynthion-soc/src/vbus.rs) replaces the whole register on every close, so exactly one source is connected (policy, not a hardware demand).

### PD on two ports

Only TARGET and AUX have FUSB302Bs; CONTROL cannot negotiate above 5 V (source sees no PD sink, stays at vSafe5V). Prefer CONTROL as passthrough source when either would do.

### The SoC drives the switches

- `repos/cynthion/.../gateware/analyzer/top.py` connects all four switches, each gated by `POWER_CONTROL_ENABLE`; `repos/packetry/src/backend/cynthion.rs` carries the command word (bits 3–7). Reset defaults: `target_c_vbus_en` **closed**, rest **open**.
- SoC peripheral: [`gateware/soc/peripherals/vbus_csr.py`](../gateware/soc/peripherals/vbus_csr.py). Firmware gates every close against a fresh PAC1954 reading.
- `vbus control` powers TARGET-A from CONTROL. `vbus charge` also routes it to TARGET-C and presents Rp there.
- TARGET-A charging needs only the switches (no CC line): close `control_vbus_en` or `aux_vbus_en`.
- TARGET-C as source also needs Rp: `vbus charge` writes both Rp enables, clears both Rd enables, starts the FUSB302B source-only toggle (CC1/CC2 orientation). Default current is the default; `vbus charge 1.5` / `vbus charge 3` advertise higher.
- Verify with PAC1954 current ([`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md)), not with a register that reads back.

## Datapaths

### Control plane — host → Apollo MCU

Needs CONTROL mux on **branch 2**. If the FPGA holds the port, first stop the keepalive (vendor `0xF0` to the stub interface).

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

### USB data paths

| # | Path | Port | Mux setting | PHY | Gateware | Host software |
|---|---|---|---|---|---|---|
| 22 | **Analyzer capture** | TARGET-C in / TARGET-A out | n/a | `target_phy` + direct taps N4/P3 | `analyzer/top.py`, subclass `0x10` | `cynthion` CLI / Packetry |
| 23 | **Facedancer emulation** | TARGET-C | n/a | `target_phy` | `facedancer/top.py`, subclass `0x20` | `facedancer` Python |
| 24 | **Gateware→host bulk** | CONTROL | **branch 1** | `control_phy` | analyzer or facedancer top | as above |
| 25 | **AUX port** | AUX | n/a — not muxed | `aux_phy` | `default_usb_connection` | as above |

## `flash --fast` — deliberate handoff to the FPGA

The one host operation that hands CONTROL to the FPGA on purpose (the target is FPGA gateware, not Apollo). Slow path bit-bangs every byte through Apollo's software JTAG TAP; fast path is host → USB bulk → ECP5 fabric → SPI flash, SAMD11 out of the data path. Speeds: [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md).

| Step | Action | Mux | Host talks to |
|---|---|---|---|
| 1 | build or fetch a `FlashBridge` bitstream (cached under `$XDG_CACHE_HOME/apollo/build/<plan-digest>`) | `S`=H | Apollo `1d50:615c` |
| 2 | `programmer.configure(...)` — load the bridge into FPGA **SRAM** over JTAG (volatile; does not flash) | `S`=H | Apollo |
| 3 | `allow_fpga_takeover_usb()` (`0xc2`) — bridge already advertising, next window hands over | **flips to `S`=L** | — (re-enumeration) |
| 4 | enumerate `1209:000f`, match class `0xFF` / subclass `0x01`, grab EP1 | `S`=L | **FPGA gateware** |
| 5 | stream the image over USB bulk; FPGA drives its own SPI flash pins | `S`=L | FPGA gateware |
| 6 | `__del__` → `request_handoff()` (`0xF0`) — stop advertising | back to `S`=H | Apollo |

- The bridge descriptor carries two interfaces: bulk flash (`0x01`) and the Apollo stub (`0x00`) that makes step 6 possible; without the stub, handing back needs a power cycle.
- Standalone `flash-fast` is deprecated: warns and forwards to `flash --fast`.

## Recovery ladder

Cheapest first; power cycle is the fallback.

1. Soft reset via Apollo (the `cyn` CLI is retired; use `./dev.py`, `./dev.py --help`; UNVERIFIED which subcommand resets).
2. Vendor `0xF0` to the stub interface (what `--force-offline` sends) — keepalive stops, Apollo reclaims the port.
3. Vendor `0xec` — emergency reset; the only legal preemption of an active JTAG session (`0xed` also permitted mid-programming).
4. Hold PROGRAM, press RESET — forces the FPGA offline at boot.
5. Power cycle ([#15](https://github.com/awtoau/cynthion-workspace/issues/15)).

## The SoC shell

The RISC-V SoC ([`gateware/soc/top.py`](../gateware/soc/top.py), firmware [`firmware/cynthion-soc/`](../firmware/cynthion-soc/)) answers a line shell on both consoles: USB CDC on AUX, and the Apollo-facing port on the shared JTAG pins.

```bash
python3 scripts/soc_run.py                       # build, load, read the console
python3 scripts/soc_shell.py help                # the USB console
python3 scripts/soc_shell.py --port /dev/serial/by-id/<apollo> help   # the Apollo console
```

- Address the Apollo console by `/dev/serial/by-id`, never `ttyACM<n>` (enumeration order moves).
- `help` is the authoritative command list; below is what each is for.

| command | what it reports | chip note |
|---|---|---|
| `check` | CPU arithmetic and two known flash words | — |
| `info` | image, CPU, bitstream identity | below |
| `selftest` | CPU, block RAM, each identity register | below |
| `flash id`, `flash read <hex>` | memory-mapped config flash | [`chips/w25q32-config-flash.md`](chips/w25q32-config-flash.md) |
| `bram read <hex>` | one word of block RAM | — |
| `ports` | which 16550s answer | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md) |
| `irq` | event-controller pending/enabled, per-source counts, deferred-log health, per-console `lost` | [`chips/ns16550a-console-uart.md`](chips/ns16550a-console-uart.md), [`soc-interrupts.md`](soc-interrupts.md) |
| `time` | 1 ms CLINT tick: uptime, `mtime`, handler cost | below |
| `cpu stats` | busy against idle, IPC, turn length, poll jitter | below |
| `log [n]` | push *n* deferred events as an interrupt handler would | below |
| `board` | every port as a tree: rail, PD controller, PHY | below |
| `led [colour on\|off\|fabric]` | the six LEDs, the button, PWRDN | — |
| `i2c [power\|target\|aux]` | scan one I2C bus and identify what answers | below |
| `power [floor <port> <mA>]` | the four rails, and change reporting | [`chips/pac1954-power-monitor.md`](chips/pac1954-power-monitor.md) |
| `phy` | TARGET USB3343 ULPI registers, walking-bit test | [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md) |
| `typec [init]` | both FUSB302B: CC and VBUS state, `int`/`fault` | [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md) |
| `sideband [hex]` | what the FPGA_ADV link reports | — |
| `load <hex>`, `reset` | stage an image and boot into it | — |

### `info`

- Eight lines: image (git hash, branch, dirty flag, build time); compiler and target triple; section sizes and what is left of the image region; boot status left by the bootloader; `misa` decoded; `mstatus`/`mtvec`; controller pending/enables; gateware.
- Image region and linker layout: [`firmware/cynthion-soc/memory.x`](../firmware/cynthion-soc/memory.x) (RAM 31 KiB at `0x400`, above the 492-byte bootloader; `.text`/`.rodata` in flash at `0x100B0000`). `.stack` is whatever RAM nothing else claimed; check headroom after any firmware change: `llvm-objdump -h firmware/cynthion-soc/target/riscv32imac-unknown-none-elf/release/cynthion-soc`.
- Firmware and bitstream are built separately (`load` replaces firmware without a bitstream rebuild). Which pair runs is the HOST's question: identity is the ECP5 USERCODE, readable only over JTAG.
  - [`gateware/build_helpers.py`](../gateware/build_helpers.py) stamps it at pack time (`ecppack --usercode`): short hash, bit 31 set for a dirty tree.
  - [`gateware/usercode_map.py`](../gateware/usercode_map.py) resolves the 32 bits to commit, variant, source digest, part, declared clocks, cache and ISA. One record per bitstream, one index across all.
  - `scripts/soc_confirm.py` reads USERCODE and IDCODE after configuring and names `stale-bitstream`, `wrong-part`, `declared-clock`; `scripts/soc/soc_probe.py` asserts the same two.
- The `fabric` line is what only the CPU sees: die temperature and bus fault counters. Its clock check is `sync` COUNTED against the 60 MHz oscillator against `target::TIME_HZ`; `CLOCK MISMATCH` means every derived interval is wrong by that ratio.
- `misa` reports `rv32im` though the core is generated `--with-rva --with-rvc`: VexiiRiscv hardwires the register. `selftest` runs C and A instructions as counter-evidence; `target::CPU_HAS_*` (generated into the PAC from `cpu/cpu.py` flags) is the other account.
- `die N C` is a DTR reading, not a measurement: 6-bit code, non-linear code-to-degrees table (FPGA-TN-02210 Table 4.3); the data sheet says "not specifically calibrated for absolute accuracy", no tolerance published.
- ECP5 gives the firmware no identity: USERCODE, IDCODE and 64-bit TraceID are JTAG-only (no primitive in Lattice `ecp5u.v`, yosys, prjtrellis or nextpnr). IDCODE distinguishes 12F (`0x21111043`) from 25F (`0x41111043`) and is Apollo's to read.
- Soft-error detection exists in silicon and prjtrellis, but yosys has no `SEDGA` cell and nextpnr never emits the SED config. PLL lock needs no register: `sync` is held in reset until lock.

### `selftest`

- One line per item plus a summary. Items: base integer set on non-foldable operands; `M` (signed division, divide by zero); `C` (three compressed instructions, checked to occupy six bytes); `A` (lr/sc, three `amo` forms); block RAM address/data walk between `.bss` and the stack; `rdtime` advances; each 16550 scratch register; fabric window DTR bit; flash known first word; TARGET PHY identity.
- `A` has its own item because atomics make the cached CPU configuration mandatory: the cacheless Wishbone bridge asserts `!withAmo`; symptom is an illegal instruction from a line that looks like arithmetic.
- Does not: touch I2C (`power`/`typec` report their cached values), write flash (**bitstream at offset 0**), read side-effect registers (would take an interrupt and never complete it), walk the PHY scratch register (`phy` does, and writes).
- Only write: block RAM between this image's `.bss` and its stack; never the bootloader's kilobyte at `0x0`. Items a target cannot answer report `skip`, counted apart from passes.

### `board`

One screen so relationships show: connector, rail, PD controller, PHY.

```
board  cynthion r1.4  three usb ports, four rails, three phys
power  pac1954 @10 on the power bus, polled every 50 ms  [sampled 61 ms ago]
|
+- CONTROL   the host port, and apollo's
|    rail       control   5.042 V  142.031 mA
|    connector  type-c, NO fusb302b -- vbus and cc are not visible from here
|    phy        usb3343, APOLLO'S -- no ulpi register window in this soc
|
+- AUX       the usb console; this text is leaving through it
|    rail       aux       5.136 V  167.968 mA
|    connector  type-c, fusb302b on the aux bus
|    typec      vbus present  nothing on CC              [confirmed 41 s ago]
|    phy        usb3343, THE CONSOLE'S -- no ulpi register window in this soc
|
+- TARGET    the port under test; nothing here drives it
     rail       target_c  5.156 V   12.207 mA
     rail       target_a    --  V      --  mA   off (12 mV)
     connector  type-c, fusb302b on the target bus
     typec      vbus absent   nothing on CC              [confirmed 2 s ago]
     phy        0424:0009 usb3343    linestate se0      [live]
```

- `typec` rows now carry a `cc` column (`cc none|cc1|cc2|both|?`); orientation is unvalidated on hardware: [`chips/fusb302b-type-c.md`](chips/fusb302b-type-c.md#orientation--read-and-not-yet-validated).
- Reads no bus: numbers come from caches the 50 ms poller / Type-C interrupt path filled (a live sweep is a dozen I2C transactions and risks the PAC1954 REFRESH window). One live read: TARGET's ULPI window `[live]` (single user, gateware-bounded at 68 µs).
- Ages: rails are polled, so an old sample means the poller stopped; controllers interrupt on change, so an old confirmation means the port has not moved.
- Absent is never a number: rail below vSafe0V (800 mV) prints `--` and `off` with the measured mV; current below the port floor prints `--` `no load`; unsampled prints `NOT SAMPLED`; absent PHY reports the gateware timeout. An unplugged rail measures 0.76–0.92 mA of ADC offset.
- CONTROL is a row shorter (no FUSB302B; PHY is Apollo's). `scripts/soc_test.py` drives `board` under QEMU, which works because it reads no bus.

### `power`

- Per port: bus volts, current in mA, above/below its own floor. Floor is set in mA, stored in µA; current is signed, the floor compares absolute magnitude.
- Background poll every 50 ms; prints only when a rail moves by **100 mA or more** or crosses its floor. Background lines go to the **USB console only** (the second port's TX pin is JTAG TMS; firmware never transmits there unbidden).
- Ports are named (`target_a`, `target_c`, `aux`, `control`), never numbered: PAC channel order is not port order.

### `irq` and `log`

- Lists pending/enabled words, a line per source and per Type-C source (per controller, so a climbing `target` with `aux` at zero names the connector). A source missing from `enabled` is masked by the handler and not yet serviced by the main loop.
- Ends `log  waiting N dropped M`: the deferred log. Handlers record a code and two words; the main loop formats and prints (a printing handler spins on a UART FIFO inside an interrupt: a hang on a level-sensitive source).
- `dropped`: the ring is bounded and push wait-free, so a storm loses lines rather than stalling. A climbing count means continuous loss; the shell prints `irq log: N event(s) LOST` on noticing.
- `log [n]` pushes *n* test records (fill/wrap/drop counting, driven under QEMU by `scripts/soc_test.py`); the ring holds 15 records, so `log 20` reports `pushed 15 of 20` (ring is 16 slots in [`events.rs`](../firmware/cynthion-soc/src/events.rs); 15 usable UNVERIFIED).
- Printing from a handler is a compile error: [`firmware/cynthion-soc/src/irq.rs`](../firmware/cynthion-soc/src/irq.rs) holds a `UartRx` with no transmit method and no `core::fmt::Write`. `scripts/soc/soc_irq_log_check.py` (the `irqlog` check in `scripts/check.py`) covers a sibling module naming `Uart`.

### `cpu stats`

What the machine is doing (three lines, from the CPU's own counters and one `rdtime` read):

```bash
python3 scripts/soc_shell.py "cpu stats"
```

| field | from | says |
|---|---|---|
| `window` | `mcycle` deltas | cycles the other figures are ratios of |
| `busy` | those deltas, split | fraction that did work rather than asked |
| `ipc` | `minstret` / `mcycle` | instructions per cycle on an in-order cached core |
| `mean`, `worst` | one turn of the main loop | how long a poller can be kept waiting |
| `worst gap` | `rdtime` at each poll | largest interval the 50 ms poll achieved |

- No scheduler, so usual utilisation reads 100%. Split here is useful work against spinning: a main-loop turn is **busy** if something called `metrics::busy`, else **idle** (it polled `irq::pop`, found nothing).
- Marked busy by: `irq::pop` (a byte arrived), the 50 ms power poll, `events::drain`, `typec::service`, `uart::report_errors`, the idle re-banner.
- The 1 ms tick is not marked busy: its cycles land in idle (the question is whether an RTOS would fit, and an RTOS brings its own tick). `time` prints the handler's worst cost and rate.
- The window is halved at 2^30 cycles (17.9 s at 60 MHz): ratios preserved, lifetime figure weighting the recent past.
- Core CSRs, checked against the generated Verilog (an undecoded CSR read traps):

| CSR | in this core | why |
|---|---|---|
| `mcycle`, `minstret`, and high halves | **yes** | `--with-rdtime` adds `zicntr`, which instantiates `PerformanceCounterPlugin` |
| `mcountinhibit` | yes | same plugin; unused here |
| `mhpmcounter3..31`, `mhpmevent3..31` | **decode, read hardwired zero** | WARL-zero dummies while `additionalCounterCount` is 0 |

- `--with-rdtime` in [`gateware/soc/cpu/cpu.py`](../gateware/soc/cpu/cpu.py) gates `rdtime` and these counters together; `info`'s `NO RDTIME` line warns for both.
- Cache miss counts are unavailable: they need `--performance-counters N` in `GENERATE_FLAGS`, a new bitstream, and a board. Not built (each a counter + CSR): Wishbone stall cycles, UART FIFO high-water marks, I2C bus occupancy, assert-to-handler-entry latency.

### `i2c <bus>`

Names the bus rather than remembering one: one controller, 2-bit select, three pin-sets; replies do not say which bus answered, so a stale select gives a plausible answer from the wrong chip. Select resets to the power monitor.

### `typec`

- Both controllers live: device id, VBUS, CC voltage band, raw `int`/`fault`, serviced-interrupt counts.
- State changes are interrupt-driven, not polled; **each `int` line has its own source**. The handler masks that source and records the port; the main loop clears that device (~1 ms of I2C, hence deferred) and re-enables the source. A `type-c <port>: vbus …, <cc>` line reports a change.
- `fault` is polled at 50 ms and outside the interrupt; it clears when the device's fault does.

### `phy`

TARGET PHY vendor/product IDs, function and OTG control, line state, walking-bit test over the scratch register. **`target_phy` only** (AUX carries the console; CONTROL is shared with Apollo). Absent PHY reports `no answer from the PHY` after a 68 µs gateware timeout.

## Also worth knowing

- **`int` (T6)** is the FPGA_ADV keepalive and sideband line, `PULLMODE="UP"` (ECP5 defaults pull-*down*, which fights Apollo's PA09 pull-up): [`chips/cynone-sideband.md`](chips/cynone-sideband.md#1-the-wire).
- **D+/D- are swapped on the board**, corrected inside the PHYs by a vendor register write the platform performs: [`chips/usb3343-ulpi-phy.md`](chips/usb3343-ulpi-phy.md).
- **UTi261M** — UNI-T thermal camera, `0bda:5830` (Realtek UVC), the facedancer proxy target: TARGET-C in, TARGET-A out.

## Where else to look

| topic | doc |
|---|---|
| alternatives weighed | [`architecture.md`](architecture.md) |
| FPGA_ADV wire | [`chips/cynone-sideband.md`](chips/cynone-sideband.md) |
| upstream vs replaced | [`upstream-boundary.md`](upstream-boundary.md) |
| reusable test gateware | [`gateware-architecture-plan.md`](gateware-architecture-plan.md) |
| clock plan | [`soc-clocking.md`](soc-clocking.md) |
| flash, USB, BRAM | [`chips/ecp5/`](chips/ecp5/) |
| Apollo firmware | [`apollo_samd11_mcu/`](apollo_samd11_mcu/) |
| soft CPU | [`chips/vexiiriscv-cpu.md`](chips/vexiiriscv-cpu.md) |
| toolchain | [`toolchain-versions.md`](toolchain-versions.md), [`toolchain-simplification.md`](toolchain-simplification.md) |
| workspace CLI | `./dev.py --help`, or `./dev.py describe` for JSON |
| Codex | [`codex-agent.md`](codex-agent.md) |
