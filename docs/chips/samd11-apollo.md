# ATSAMD11D14A — the Apollo debug microcontroller

The Cortex-M0+ that owns the CONTROL port, programs the FPGA over JTAG and carries
the console. Order part **`ATSAMD11D14A-MUT`** (substitution `-MNT`),
`repos/cynthion-hardware/debugger.kicad_sch`. Apollo builds it as board
`cynthion_d11`; the linker script calls it `SAMD11D14AM`.

**Index:** [`../hardware.md`](../hardware.md)

## Memory budget — the binding constraint on this board

| | measured | of | |
|---|---|---|---|
| flash | **13,608 B** | 14,336 B | **94.92%** |
| RAM | **3,472 B** | 4,096 B | **84.77%** |
| `.text` / `.data` / `.bss` / stack | 13,608 / 0 / 2,768 / 704 | | |
| stack high-water mark | **344 B** of 704 reserved | | |

`tmp/logs/apollo_budget_check.log`, 2026-08-02. Checked by
`scripts/apollo_budget_check.py` (`ROM_CEILING = 0.95`).

**14 KB, not 16.** The part has 16 KiB of flash; `BOOTLOADER_SIZE := 0x800`
reserves 2 KiB at the bottom for the Saturn-V DFU bootloader, leaving
`0x4000 - 0x800 = 14,336 B` for the application. RAM is a flat 4,096 B at
`0x20000000`.

**LTO is load-bearing, not an optimisation.** `-flto=auto -flto-partition=one` in
`repos/apollo/firmware/Makefile`; it reclaims 2,968 bytes. Upstream without it sits
at 96.04% with 568 bytes free. The stack reservation is also cut deliberately —
`-Wl,--defsym=STACK_SIZE=0x2C0` (704 B) against the linker default of 1,024 B,
justified by the measured 344-byte high-water mark, reclaiming 320 bytes.

**LTO has a specific hazard here**, which is why `scripts/verify_vectors.py`
exists: weak-alias vector tables plus `-flto` can silently resolve interrupt
vectors to `Dummy_Handler`. The script checks the linked ELF's vector slots. That
is a silent failure — the firmware links, flashes and mostly works.

Reports: `scripts/apollo_memory_report.py`, `apollo_rom_sizing.py`,
`apollo_stack_measure.py`.

## Pin sharing — PA10, PA11, PA14, PA15 are shared three ways

| MCU pin | JTAG | UART (SERCOM2) | SPI (SERCOM0) |
|---|---|---|---|
| **PA10** | **TDO** | — | **PAD2 (MISO)** |
| **PA11** | **TMS** | **RX (PAD3)** | — |
| **PA14** | **TDI** | **TX (PAD0)** | **PAD0 (MOSI)** |
| **PA15** | **TCK** | — | **PAD1 (SCK)** |

Sources: `apollo_board.h` for the JTAG assignment, `uart.c` for
`MUX_PA11D_SERCOM2_PAD3` / `MUX_PA14D_SERCOM2_PAD0`, `spi.c` for the
`SERCOM0_PAD0/1/2` muxing used by SPI-accelerated JTAG — all under
`repos/apollo/firmware/src/boards/cynthion_d11/`.

**Enforced in firmware, not by convention.** `uart_configure_pinmux()` hard-refuses
to repinmux while `apollo_mode_jtag_active()`, and the CDC line-coding, line-state
and rx-wanted callbacks are gated on the same flag — a host opening the console
mid-flash would otherwise corrupt an in-flight configure. `jtag.c` calls
`uart_release_pinmux()` on init and `uart_configure_pinmux()` on deinit.

**Stale upstream comment:** `spi.c` says "PA08 (TDI), PA09 (TCK), PA10 (TDO)" but
the code below it muxes PA14/PA15/PA10. PA08 is FPGA_PROGRAM and PA09 is FPGA_ADV.
The comment is wrong, not the code.

**The relocation question is settled: PA08/PA09 are not free**, so the UART cannot
move off the shared pins on the d11. Issues #65/#66.

### The FPGA-side half: R14/T14 versus JTAG TDI/TMS

The same conflict exists at the other end of the wires, which is why it cannot be
solved on the MCU alone. From the platform file's own comment:

> UART pins R14 and T14 are connected to JTAG pins R11 (TDI) and T11 (TMS)
> respectively, so the microcontroller can use either function but not both
> simultaneously.

So **ECP5 R14** (UART RX) is tied to **R11 = JTAG TDI**, and **T14** (UART TX) to
**T11 = JTAG TMS**. On the Apollo side those same two nets land on PA14 (TDI /
UART TX) and PA11 (TMS / UART RX).

Note the crossover: the FPGA's *RX* shares with TDI, which is the MCU's *TX* pin.
That is consistent — one wire, one MCU pin, one FPGA pin — but it is why reading
either end's table alone tends to produce a wrong pairing.

This is asserted by the platform comment and by the Apollo pin assignments, both
cited above. A net-level confirmation from the KiCad schematic has **not** been
extracted; the relevant nets (`FPGA_JTAG.TDI`, `FPGA_JTAG.TMS`) exist in
`bank8_configuration.kicad_sch` and `debugger.kicad_sch`, but pin→net binding needs
a generated netlist.

### Other d11 pins

| pin | function |
|---|---|
| PA02 | PROGRAM_BUTTON |
| PA03 | FPGA_INITN (r1.3+) |
| PA04 | FPGA_DONE |
| PA06 | USB_SWITCH → TC7USB42MU |
| PA08 | FPGA_PROGRAM (PROGRAMN) |
| PA09 | FPGA_ADV (EIC EXTINT7) |
| PA16/17/22/23/27 | LED A–E |
| PA30 / PA31 | SWCLK / SWDIO, Tag-Connect TC2030-CTX — **not** routed through USB |

On r0.6 and earlier (`BOARD_HAS_SHARED_BUTTON`) there is no USB_SWITCH: PA09 is
PHY_RESET instead of FPGA_ADV, and PROGRAM_BUTTON shares PA16 with LED_A — which is
why `button.c` saves the pin level, flips direction to read it, and restores it.

## Links to the FPGA

| link | pins | notes |
|---|---|---|
| **JTAG** | PA15 TCK, PA14 TDI, PA11 TMS, PA10 TDO | bit-banged, or SPI-accelerated via SERCOM0 with DMA. The polled path costs ~700 µs per 1024 bytes, during which `tud_task()` cannot run. |
| **FPGA_ADV sideband** | PA09 ↔ ECP5 **T6** | single wire, half-duplex, Apollo is master. SERCOM1 PAD3 receives in hardware; transmit is bit-banged from TC1 because `TXPO` cannot reach PAD3. Carries the port-ownership signal *and* the command protocol. Everything else: [`../sideband.md`](../chips/cynone-sideband.md). |
| **UART** | PA14 TX, PA11 RX ↔ ECP5 T14/R14 | one CDC-ACM interface (`CFG_TUD_CDC` is 1). Mutually exclusive with JTAG. |
| **CONTROL USB mux** | PA06 | TC7USB42MU DPDT switch; see [`../hardware.md`](../hardware.md) |
| dedicated debug SPI | — | **not enabled on this board.** Handlers exist (`debug_spi.c`, vendor requests `0x50`–`0x54`) but are gated on `_BOARD_HAS_DEBUG_SPI`, which `cynthion_d11` does not define. Those requests STALL. SPI to the FPGA goes via the JTAG ER1/ER2 tunnel instead. |

The ECP5 `int` resource that carries FPGA_ADV is declared `PULLMODE="UP"` because the
ECP5 defaults to pull-*down*, which fights Apollo's PA09 pull-up — see
[`../sideband.md`](../chips/cynone-sideband.md#1-the-wire) for what that costs if it is ever
dropped.

## Identity

| | |
|---|---|
| Apollo firmware and Saturn-V bootloader | `1d50:615c` |
| firmware version at last measurement | `v1.1.1-58-g6520707` |

Apollo and Saturn-V share a PID. **Distinguishing DFU from Apollo firmware needs
the interface descriptor, not the PID** — a mistake that has been made here before.

## Registers

**Not applicable.** The SAMD11's own peripheral registers come from Microchip's
CMSIS headers in the Apollo tree. Nothing here restates them, and the SoC's
generated PAC covers the *FPGA's* registers, not this part's — see
[Register reference](../hardware.md#register-reference).

## Scripts and further reading

| | |
|---|---|
| `scripts/apollo_budget_check.py` | flash/RAM against the ceiling |
| `scripts/apollo_memory_report.py`, `apollo_rom_sizing.py`, `apollo_stack_measure.py` | where the bytes go |
| `scripts/verify_vectors.py` | the LTO vector-table guard |
| `scripts/apollo_reflash.py` | reflash over DFU |
| [`../apollo_samd11_mcu/`](../apollo_samd11_mcu/) | code review, race conditions, DFU buffers, serial architecture, the configure-speed investigation |

**On JTAG configuration speed there is exactly one document**, and it is
[`../apollo_samd11_mcu/apollo-configure-speed-investigation.md`](../apollo_samd11_mcu/apollo-configure-speed-investigation.md).
Four accumulated before; three had to be retired, two of them stating conclusions
that were the opposite of the truth. **Do not start a second — add to its table.**
