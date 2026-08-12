# SoC interrupts — every source, and the chip that raises it

What is wired to the RISC-V SoC's interrupt controller, what each source means,
and what is deliberately not a source.

**Index:** [`README.md`](README.md) · sibling pages
[`soc-clocking.md`](soc-clocking.md), [`soc-memory-bus.md`](soc-memory-bus.md)

## Sources

Source 0 is reserved by the PLIC specification as "nothing pending", so real
sources start at 1. The gateware wiring is `gateware/soc/top.py`; the numbers
reach the firmware through `cynthion_soc_pac::base`, generated from
`AwtoSoc.interrupt_sources` by `scripts/soc_generate_pac.py`.

| # | firmware constant | chip | refdes | chip part | what raises it |
|---|---|---|---|---|---|
| 1 | `CONSOLE_IRQ` | — | — | 16550 in fabric | USB console UART, RX or TX-empty |
| 2 | `APOLLO_UART_IRQ` | — | — | 16550 in fabric | the Apollo link; the far end is the **SAMD11** `U6`, `ARM Cortex-M0+ MCU, 48MHz, 16KB Flash, 4KB RAM` |
| 3 | `BOARD_I2C_IRQ` | — | — | I2C master in fabric | transaction complete |
| 4 | `BOARD_I2C_MUX_TARGET_IRQ` | **FUSB302B** | `U2` | `FUSB302BMPX`, *IC USB TYPE C CTLR PROGR 14-MLP* | TARGET port CC/PD event, pin 5 `int` |
| 5 | `BOARD_I2C_MUX_AUX_IRQ` | **FUSB302B** | `U12` | as above | AUX port CC/PD event, pin 5 `int` |
| 6 | `BOARD_I2C_MUX_POWER_ALERT_IRQ` | **PAC1954** | `U1` | `PAC195X-1-VQFN`, four-channel current/voltage monitor | `ALERT` on GPIO/ALERT2, pin 15 |

Balls, pull-ups and every unused pin: [`chips/ecp5/pin-usage.md`](chips/ecp5/pin-usage.md).

**Source 6 is wired but not enabled.** `top.py` drives it —
`plic.sources[IRQ_POWER_ALERT].eq(~power_monitor.gpio.i)`, inverted because the
pin is open-drain — and `info` reports `enabled 0000003e`, bits 1–5. Whether
leaving it out of the mask is deliberate is not recorded.

## The PAC1954 has two alert outputs and only one is available

The part offers *"Two Independent ALERT/GPIO pins"* (DS20006539B). This board
wires both, and spends one:

| pin | function | what the gateware does |
|---|---|---|
| 1 `SLOW/ALERT1` | either a SLOW **input** or a second alert **output** | driven as an output, `slow.o = 0, oe = 1` — used for SLOW, so ALERT1 is not available |
| 15 `GPIO/ALERT2` | alert output, open-drain | read as an input → PLIC source 6 |

So a second power-alert source exists in the silicon and is unreachable while
`SLOW` is being driven. Freeing it means deciding SLOW is not needed — the ADC
rate it controls is the trade.

Every source is **level**-sensitive. That is required rather than incidental: a
16550's `irq` stays high while its FIFO holds a byte, so an edge-triggered input
loses an interrupt whenever a second byte arrives before the first is read. The
FUSB302B's interrupt registers are read-to-clear, so servicing the device is
what drops its line.

**One source per FUSB302B, not one OR-ed source.** `docs/architecture.md`
decision 8.

## Wired to a register, and to nothing else

| chip | refdes | chip part | pin | ECP5 ball | goes to |
|---|---|---|---|---|---|
| **DPO2036** | `U13` (TARGET) | *4-CH OVER-VOLTAGE PROTECTION FOR CC/SBU PINS ON USB TYPE-C* | 6 `FAULTB` | **D4**, `R100` 10 kΩ | `i2c_mux.target_fault` → CSR bit |
| **DPO2036** | `U14` (AUX) | as above | 6 `FAULTB` | — | `i2c_mux.aux_fault` → CSR bit |

`FAULTB` is active-low open-drain, asserted while the part is protecting and
through its 26–38 ms recovery, then released — `sources/DPO2036.pdf`, DS40644
Rev. 2-2. Nothing latches it and nothing acts on it: the only firmware reference
is a status line printed by the `typec` shell command. #506, #507.

The DPO2036 has no bus interface — twelve pins, no SCL, no SDA, no registers.
`FAULTB` is the entire software-visible surface.

## What the controller does and does not do

**No hardware preemption.** The PLIC specification 1.0.0 line 93: *"the PLIC
provides no concept of interrupt preemption or nesting"*. The privileged
specification clears `mstatus.MIE` on trap entry and VexiiRiscv's
`TrapPlugin.scala:869` does exactly that. So priority decides **which source a
claim returns when several are pending**, never whether a running handler is
interrupted.

**Preemption is delivered in software.** RTIC's RISC-V backend is `riscv-slic`,
a software interrupt controller. See [`rtic.md`](rtic.md).

**Priorities are compile-time constants** — `POWER_ALERT` 4, `CONSOLE` 3,
`TYPE_C` 2, `I2C` 1 — never written at runtime, and the threshold is 0
everywhere. Every claim site loops until `claim()` returns 0, so priority only
permutes the order of the handlers serviced inside one trap.

**Ordering that is load-bearing:** complete before disable. Completing after
disabling throws the completion away and gates the source off permanently,
because `pending[i] = sources[i] & ~claimed[i]`. `src/irq.rs`'s `defer_type_c`
carries the argument.

## Where the pieces are

| what | where |
|---|---|
| gateware controller | `gateware/soc/cpu/plic.py` |
| source wiring | `gateware/soc/top.py`, `AwtoSoc.interrupt_sources` |
| firmware driver | `firmware/cynthion-soc/src/plic.rs` |
| front end and deferral | `firmware/cynthion-soc/src/irq.rs` |
| source numbers | `cynthion_soc_pac::base`, generated by `scripts/soc_generate_pac.py` |
| host model | `scripts/soc_plic_sim.py` |
