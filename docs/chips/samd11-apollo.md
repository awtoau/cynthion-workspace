# ATSAMD11D14A — the Apollo debug microcontroller

The Cortex-M0+ that owns the CONTROL port, programs the FPGA over JTAG and carries
the console. Order part **`ATSAMD11D14A-MUT`** (substitution `-MNT`),
`repos/cynthion-hardware/debugger.kicad_sch`. Apollo builds it as board
`cynthion_d11`; the linker script calls it `SAMD11D14AM`.

**Index:** [`../hardware.md`](../hardware.md)

## Performance — the clock rates this MCU sets for the whole board

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md);
cross-cut against every other bus in [`bus-speed-audit.md`](bus-speed-audit.md).
Datasheet references are **Atmel-42363H-SAM-D11, 09/2016**,
[`sources/Atmel-42363-SAM-D11_Datasheet.pdf`](../../sources/Atmel-42363-SAM-D11_Datasheet.pdf).

Two rates leave this part and land on the FPGA: **JTAG TCK** and the **console
UART**. Neither is bounded by the ECP5, and one of them has no room to move at
all — which is the useful finding, because it is not obvious from either
datasheet on its own.

### 1. Theoretical maximum

**The SERCOM is the whole story.** Every serial rate here comes from a SERCOM off
GCLK0, and `CONF_CPU_FREQUENCY` is **48,000,000**
([`peripheral_clk_config.h`](../../repos/apollo/lib/tinyusb/hw/mcu/microchip/samd11/config/peripheral_clk_config.h)),
which is also the datasheet ceiling — **Table 35-6, Maximum Peripheral Clock
Frequencies** (and its Table 40-6 twin for the other operating-condition set)
gives `fGCLK_SERCOM0_CORE` a maximum of **48 MHz**, and the part's own overview
states *"The SAM D11 devices operate at a maximum frequency of 48MHz"*.

In SPI master mode the SERCOM divides that by an integer:

    SCK = 48 MHz / (2 x (BAUD + 1))

    BAUD  0 -> 24.0 MHz
    BAUD  1 -> 12.0 MHz      <- what JTAG uses
    BAUD  2 ->  8.0 MHz
    BAUD  3 ->  6.0 MHz

**There is no rung between 24 and 12.** That single fact decides the JTAG
section below, and it is a property of the divider rather than of either part.

The far end is more generous. ECP5 datasheet FPGA-DS-02012-1.9 **Table 3.43,
JTAG Port Timing Specifications** gives `fMAX`, TCK clock frequency, as
**25 MHz** — so the FPGA would accept BAUD 0.

### 2. Achievable on this board — the round trip binds, not either endpoint

**JTAG.** Apollo clocks TCK from SERCOM0 in SPI master mode with `BAUD = 1`
([`boards/cynthion_d11/jtag.c`](../../repos/apollo/firmware/src/boards/cynthion_d11/jtag.c)),
so **TCK = 12 MHz**. The obvious question is why not 24, given the ECP5 accepts
25 — and the answer is a two-datasheet sum that neither document makes on its
own.

TDO changes on the falling TCK edge; the SERCOM (`CPOL = 1`, `CPHA = 1`) samples
MISO on the rising edge, half a period later. So:

| term | value | source |
|---|---|---|
| `tBTCO` — ECP5 TAP falling clock edge to valid output | **10 ns max** | FPGA-DS-02012 Table 3.43 |
| `tMIS` — SAMD11 MISO setup to SCK, master | **21 ns typ** | Atmel-42363H §35.15.2, Table 35-50 |
| board and pad delay | not accounted | — |
| **required half period** | **≥ 31 ns** | |
| **⇒ TCK ≤ 16.1 MHz** | | |

24 MHz gives a 20.8 ns half period, 10 ns short. The SAMD11's own figure for
master mode — **`tSCK` 84 ns typ, i.e. 11.9 MHz**, same table — arrives at the
same place by a different route, and 12 MHz is one hair inside it.

**So the gap between what the ECP5 allows (25 MHz) and what we run (12 MHz) is
not slack. It is the divider having no rung in the window between 12.0 and
16.1 MHz.** The binding constraint is the SAMD11's SERCOM, and it is already at
its ceiling.

Two caveats attached rather than buried. `tMIS` and `tSCK` are given in the
**Typ.** column with no Min or Max, so the arithmetic above is an argument and
not a guarantee; and it says nothing about *configuration* over JTAG, where a
bit slipping is caught by the ECP5's own CRC rather than by a readback.

**The UART, and it is not a rate question.** The Apollo serial line runs at
**115200 8N1** on both sides — `uart_initialize(true, 115200)` in
[`console.c`](../../repos/apollo/firmware/src/console.c), and
`APOLLO_UART_BAUD = 115200` in
[`../../gateware/soc/top.py`](../../gateware/soc/top.py) — which is 11.52 kB/s
next to a 60 MHz CPU. A SERCOM off 48 MHz would reach several megabaud. What
stops it is **not** the baud generator: R14/T14 on the FPGA are the ECP5's JTAG
TDI/TMS pads, and PA10/11/14/15 on this part are shared three ways between JTAG,
SERCOM0 SPI and SERCOM2 USART. Both ends of this link are pins that JTAG also
needs, so the mitigation is a policy of never transmitting unbidden, not a
faster wire. See the pin-sharing section below.

The one-wire sideband link exists precisely because that policy leaves nothing
able to speak first; it runs at 230400 and has its own analysis in
[`cynone-sideband.md`](cynone-sideband.md) §2.

**The USB round trip is the real ceiling on everything this part does for the
host.** One response-requiring vendor transfer costs **3.00 ms** measured, and
that is what paces `apollo flash-program`: 58,940 bytes took 3.33 s of which the
flash itself needed ≈0.53 s. TCK does not appear in that arithmetic at all —
see [`w25q32-config-flash.md`](w25q32-config-flash.md) §4.

### 3. Measured

| path | conditions | figure | source |
|---|---|---|---|
| JTAG shift | TCK 12 MHz, DMA-driven SPI | **750 kword/s** | [`../../gateware/soc/bus/jtag_stage.py`](../../gateware/soc/bus/jtag_stage.py) |
| JTAG shift | polled path, 1024 bytes | ~700 µs, during which `tud_task()` cannot run | this file, *Links to the FPGA* |
| USB vendor round trip | one response-requiring transfer | **3.00 ms** | `repos/apollo` `90c8b7b` |
| host → flash program | 58,940 B at `0xb0000` | 3.33 s = 17.3 KiB/s | `scripts/soc_run.py`, 2026-08-06 |
| **TCK at BAUD 0 (24 MHz)** | — | **never run** | the harness exists; see below |

### 4. The gap, and what closes it

| rank | option | worth | effort |
|---|---|---|---|
| 1 | **page loop on this MCU** ([#100](https://github.com/awtoau/cynthion-workspace/issues/100)) | 3.33 s → ~0.6 s on every firmware iteration, **5.5×** | a command that does not exist, on a part at 94.9% of its flash budget |
| 2 | fewer USB round trips per operation | 3.00 ms each, and they dominate every host-driven path | already done once — two redundant trips per page removed, 4.71 s → 3.33 s |
| — | **TCK 12 → 24 MHz** | **unavailable.** 24 MHz is 10 ns short on the TDO round trip and past the ECP5's 25 MHz `fMAX` in any case | — |
| — | a faster Apollo UART | **wrong lever.** The pins are JTAG's; the cost is arbitration | — |

**Unknown, and cheap to settle:** whether TCK 24 MHz fails the way the
arithmetic says. `jtag.c` already takes a SERCOM divider in the high byte of
`wIndex` and counts bytes that return exactly as the TAP should have returned
them — a benchmark written for this question, with the standard clocking
restored afterwards so an exotic rate cannot leave the chain misconfigured.
Running it at BAUD 0 costs one USB request and would convert a typical-column
argument into a result.

**Also unknown, and worth naming:** `apollo_fpga/jtag.py` advertises
`max_frequency=405e3` and its `set_frequency()` is `pass` with a `# FIXME`, so
every SVF `FREQUENCY` command is logged and discarded. Nothing is broken by it —
the real rate is the firmware's 12 MHz — but anything reading that constant is
being told something untrue.

## Memory budget — the binding constraint on this board

| | measured | of | | ceiling |
|---|---|---|---|---|
| flash | **13,688 B** | 14,336 B | **95.48%** | 95.9% = 13,748 B — 60 B under |
| RAM | **3,552 B** | 4,096 B | **86.72%** | 87.5% = 3,584 B — 32 B under |
| `.text` / `.relocate` / `.bss` / stack | 13,608 / 80 / 2,768 / 704 | | | |
| stack high-water mark | **344 B** of 704 reserved | | | |

`arm-none-eabi-gcc` 15.2.0, `APOLLO_BOARD=cynthion` at apollo `90c8b7b6`,
2026-08-10. Checked by `scripts/apollo_budget_check.py`; `firmware.bin` is
13,688 bytes, which is the same number arrived at independently.

**There is no `.data`.** The linker script routes `*(.data .data.*)` into
`.relocate` — VMA in RAM, LMA in flash — so it costs 80 bytes of each. The
figures above superseded a check that summed by section name and reported
94.92% / 84.77%, both under their ceilings, while both were over ([#199](https://github.com/awtoau/cynthion-workspace/issues/199)).

**The ceilings are derived, not chosen** ([#404](https://github.com/awtoau/cynthion-workspace/issues/404)). 95% / 85% were computed against
those 80-byte-low totals, and the firmware they shipped with was already at
95.48% / 86.33%. Each now sits under one 64-byte object — the smallest
`apollo_memory_report.py` flags — above the measured image, so the next notable
object trips the guard and byte drift does not. Flash has nothing left to give:
588 B free is what upstream runs with, and the levers that would buy more are
costed in `scripts/apollo_budget_levers.py` ([#496](https://github.com/awtoau/cynthion-workspace/issues/496) is the largest, 336 B).

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

Reports: `scripts/apollo_memory_report.py`. (`apollo_rom_sizing.py` and
`apollo_stack_measure.py` are retired to `debris/scripts/`.)

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
move off the shared pins on the d11. Issues [#65](https://github.com/awtoau/cynthion-workspace/issues/65)/#66.

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
| PA06 | USB_SWITCH → PI3USB102G |
| PA08 | FPGA_PROGRAM (PROGRAMN) |
| PA09 | FPGA_ADV (EIC EXTINT7) |
| PA16/17/22/23/27 | LED A–E |
| PA30 / PA31 | SWCLK / SWDIO, Tag-Connect TC2030-CTX — **not** routed through USB |

On r0.6 and earlier (`BOARD_HAS_SHARED_BUTTON`) there is no USB_SWITCH: PA09 is
PHY_RESET instead of FPGA_ADV, and PROGRAM_BUTTON shares PA16 with LED_A — which is
why `button.c` saves the pin level, flips direction to read it, and restores it.

## Concurrency: two contexts, and one of them is three ISRs

**The firmware is bare-metal.** No RTOS, no scheduler, no threads —
`grep -rl "FreeRTOS\|pthread\|xTaskCreate\|osThread" firmware/src` returns
nothing, and `main.c:109` is a single `while (1)` calling `tud_task()`. Every USB
vendor request is dispatched from inside that call, on that one stack.

So **any claim of a race must name an ISR**, because nothing else preempts. There
are three, all in `boards/cynthion_d11/fpga_adv.c`:

| handler | state it shares with the main loop |
|---|---|
| `EIC_Handler` | `edge_counter` |
| `SERCOM1_Handler` | `pattern_position`, `last_heartbeat`, `response`, `response_len`, `response_want` |
| `TC1_Handler` | `tx_bits_left` |

Every one of those is `volatile`. The other boards' handlers (`boards/*/uart.c`)
are UART plumbing and touch no FPGA state.

**The one race this shape allows was found and fixed.** `fpga_adv_task()` reads
and clears the edge counter as a pair, and an edge landing between the two
statements would be dropped — under-counting the window that feeds
`fpga_requesting_port()`, whose threshold is `> 2`, and potentially missing an
FPGA USB-takeover request. It is masked:

    NVIC_DisableIRQ(EIC_IRQn);
    window_edges = edge_counter;
    edge_counter = 0;
    NVIC_EnableIRQ(EIC_IRQn);

A 2026-05 review instead reported three races in `vendor.c` and `fpga.c` and
recommended mutexes. All three were refuted against the source — see [#61](https://github.com/awtoau/cynthion-workspace/issues/61).

## Links to the FPGA

| link | pins | notes |
|---|---|---|
| **JTAG** | PA15 TCK, PA14 TDI, PA11 TMS, PA10 TDO | bit-banged, or SPI-accelerated via SERCOM0 with DMA. The polled path costs ~700 µs per 1024 bytes, during which `tud_task()` cannot run. |
| **FPGA_ADV sideband** | PA09 ↔ ECP5 **T6** | single wire, half-duplex, Apollo is master. SERCOM1 PAD3 receives in hardware; transmit is bit-banged from TC1 because `TXPO` cannot reach PAD3. Carries the port-ownership signal *and* the command protocol. Everything else: [`../sideband.md`](../chips/cynone-sideband.md). |
| **UART** | PA14 TX, PA11 RX ↔ ECP5 T14/R14 | one CDC-ACM interface (`CFG_TUD_CDC` is 1). Mutually exclusive with JTAG. |
| **CONTROL USB mux** | PA06 | PI3USB102G DPDT switch; see [`../hardware.md`](../hardware.md) |
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
| `scripts/apollo_memory_report.py` | where the bytes go |
| `scripts/apollo_budget_levers.py` | what each way back under budget costs, built not estimated |
| `scripts/verify_vectors.py` | the LTO vector-table guard; in `check.py` since [#199](https://github.com/awtoau/cynthion-workspace/issues/199) |
| `scripts/apollo_reflash.py` | reflash over DFU |
| [`../apollo_samd11_mcu/`](../apollo_samd11_mcu/) | code review, race conditions, DFU buffers, serial architecture, the configure-speed investigation |

**On JTAG configuration speed there is exactly one document**, and it is
[`../apollo_samd11_mcu/apollo-configure-speed-investigation.md`](../apollo_samd11_mcu/apollo-configure-speed-investigation.md).
Four accumulated before; three had to be retired, two of them stating conclusions
that were the opposite of the truth. **Do not start a second — add to its table.**
