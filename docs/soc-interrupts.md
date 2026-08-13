# SoC interrupts — the design

What can interrupt this CPU and what should. Seventeen sources, of which ten are
built. The "built today" column says which.

The controller is `amaranth_soc.csr.event.EventMonitor`, wrapped by
[`gateware/soc/cpu/intc.py`](../gateware/soc/cpu/intc.py) for the numbering:
pending bits, enables, W1C, and a trigger fixed per source at elaboration.
`scripts/soc_intc_sim.py` checks this table against what is wired.

**Index:** [`README.md`](README.md) · siblings
[`soc-clocking.md`](soc-clocking.md), [`soc-memory-bus.md`](soc-memory-bus.md)

## Four ways into the trap handler

RISC-V has three interrupt causes and one synchronous one, and all four arrive
through `mtvec`.

| cause | raised by | used for |
|---|---|---|
| **machine external** (MEI) | the interrupt controller | every board signal — the table below |
| **machine timer** (MTI) | the CLINT's `mtime` reaching `mtimecmp` | the 1 ms tick, and RTIC's monotonic |
| **machine software** (MSI) | the CLINT's `msip`, written by the CPU itself | RTIC pending a task — this is how `riscv-slic` dispatches |
| **exceptions** | the instruction being executed | faults: load/store access, illegal instruction, misaligned, `ecall`, breakpoint |

**Only the external one is a board signal.** The timer and software causes are
the CPU interrupting itself through the CLINT; exceptions are not interrupts at
all, they are the current instruction failing.

### Software interrupts are the dispatcher

`msip` is how a task gets released: RTIC writes it, the CPU takes MSI, and
`riscv-slic` decides which task runs. So software priority (below) is
implemented on this cause, not on the external one.

### Exceptions must not be silent

Without a handler, `riscv-rt` links `abort`, and a bus fault becomes an infinite
loop with no output — indistinguishable from the hang it replaced. So the design
carries an exception handler that reports cause, `pc` and faulting address.

A trap the firmware issues on purpose is also the DPO2036 test path: a load from
an unmapped address is how the trap-heavy load exercises this handler.

## Every board signal that can interrupt

| # | chip | signal | trigger | built today |
|---|---|---|---|---|
| 1 | — | console UART, 16550 in fabric | level | **yes** |
| 2 | — | Apollo link UART, 16550 in fabric | level | **yes** |
| 3 | — | I2C master, transaction complete | level | **yes** |
| 4 | **FUSB302B** `U2` | TARGET `int`, pin 5 | level | **yes** |
| 5 | **FUSB302B** `U12` | AUX `int`, pin 5 | level | **yes** |
| 6 | **PAC1954** `U1` | `GPIO/ALERT2`, pin 15 | **edge**, fall ([why](#the-pac1954-alert-is-not-a-level)) | **yes** |
| 7 | **PAC1954** `U1` | `SLOW/ALERT1`, pin 1 | edge | **no** — pin hard-driven as SLOW ([make it runtime-selectable](chips/pac1954-power-monitor.md)) |
| 8 | **DPO2036** `U13` | TARGET `FAULTB`, pin 6 | **edge**, rise ([why](chips/dpo2036-cc-sbu-protection.md)) | **yes** |
| 9 | **DPO2036** `U14` | AUX `FAULTB`, pin 6 | **edge**, rise | **yes** |
| 10 | **USB3343** TARGET | link event — `DIR` carries an RX CMD | edge | no |
| 11 | **USB3343** AUX | link event | edge | no |
| 12 | **USB3343** CONTROL | link event | edge | no |
| 13 | — | USER button, ball **M14** | **edge**, rise | **yes** |
| 14 | — | sideband byte, ball **T6** → **SAMD11** `U6` pin 8 | level | no — CSR count only (#509) |
| 15 | — | SBU peripheral, TARGET — balls **A2**, **E4** | level | no — no peripheral ([`sbu.md`](sbu.md), #518) |
| 16 | — | SBU peripheral, AUX — balls **H13**, **K14** | level | no — no peripheral |
| 17 | — | PLL loss of lock | **edge**, fall on `locked` | **yes** |

**Seventeen sources in the design, ten built.** The seven that are not need a
peripheral that does not exist; their numbers are left as gaps, tied low in
`intc.py`, so wiring one up later renumbers nothing.

Polarity is the pad's. `rise` and `fall` are the trigger as the controller sees
it, after the platform's `PinsN` has undone an active-low pin: `FAULTB` and the
button are `PinsN` and so assert as a rise, the PAC1954's ALERT is a raw `io`
pad and asserts as a fall.

Balls, pull-ups and every unused pin: [`chips/ecp5/pin-usage.md`](chips/ecp5/pin-usage.md).

## What the trigger actually decides

**Not whether a pulse is captured.** Every pending bit latches, level sources
included — `amaranth_soc.event.Monitor` sets it on the trigger and clears it
only on a W1C. A 5 µs pulse sets the bit either way.

What it decides is **when the bit can be acknowledged**:

| | level | edge |
|---|---|---|
| clear while the line is asserted | **ignored** — the set arm wins | takes |
| clear once the line is idle | takes | takes |
| line still asserted after a clear | fires again, immediately | silent until the next edge |

So the rule is the peripheral's, not the pin's:

* **The condition is a backlog the CPU drains** — a 16550's FIFO — → **level**.
  Draining is what clears it, and being re-entered while bytes remain is
  correct. An edge here would lose everything after the first burst.
* **The condition is an event the CPU cannot clear** — `FAULTB` held 30 ms, a
  button held down, a PLL that stays unlocked → **edge**. As a level it storms:
  the handler cannot acknowledge it, so it re-enters until the hardware
  releases, and the only defence is masking plus a poll to decide when to
  unmask — a handler and a poll instead of either.

`scripts/soc_intc_sim.py` asserts each row of that table.

## The PAC1954 alert is not a level

Threshold alerts latch low until an I2C read clears them at the part, which is
milliseconds away in task context: as a level source that is a storm for the
whole deferral. **Conversion-complete is different again** — a 5 µs pulse that
sets no status bit (DS20006539B §5.16.1), so there is nothing to read and
nothing to clear, and only the edge says it happened at all.

Edge, on the pad's falling edge. #514.

**Superseded:** this section used to say a level source would lose the pulse
outright. That was true of the PLIC, whose pending bit was combinational from
the line. It is not true of this controller.

## A data line is not an interrupt source

SBU carries DP AUX, a Debug Accessory UART, or nothing, depending on the mode
negotiated. So it gets a **receiver**, and the receiver's interrupt is the
source — exactly as the console's 16550 is the source for USB CDC bytes rather
than the ULPI pins being one.

Same for anything else that arrives as data rather than as an event.

## The button is not debounced in fabric

It bounces, and one press raises several interrupts. That is fine — a press is a
human event, so the handler has milliseconds of slack and software does the
debouncing.

Debouncing in gateware would be a timer per source for a thing the CPU can
settle in a few instructions.

## One source per device, never an OR

Three PHYs get three sources, two FUSB302Bs get two, and nothing is merged.

With one shared source the handler must interrogate every device to learn which
fired. For a PHY that means a ULPI register read — a bus transaction with a
timeout — three times, on every event. The saved source costs more than it saves.

This board has made the mistake once: the FUSB302B `int` lines were OR-ed onto a
single source and that was undone.

A source is a pending bit and an enable. Three cost essentially nothing over one.

## Priority is software only

**There is one priority in this design and it is RTIC's**, in `riscv-slic`.
Declared per task, it decides which task runs and which task can interrupt
which. That is where preemption comes from — see [`rtic.md`](rtic.md).

**The interrupt controller has none.** No priority registers, no threshold, no
claim/complete. Two registers, one bit per source:

    0x0  enable    RW   bit n: source n may raise the CPU line
    0x4  pending   RW   bit n: source n has triggered; write 1 to clear

Both are one CPU word wide and **the fourth byte access is the one that
commits** — `alignment=2` on the CSR multiplexer, the same shadow rule
`src/gpio.rs` documents. Three byte writes write nothing.

### Why the controller cannot preempt, whatever it offers

It gives the CPU **one** interrupt line. When that fires the CPU traps and
`mstatus.MIE` is cleared, per the privileged specification. Nothing further is
taken until software sets it again, so a source going pending mid-handler does
nothing until that handler finishes.

PLIC 1.0.0 line 93 says it from the other side: *"the PLIC provides no concept
of interrupt preemption or nesting"*.

### Deferring a source

A handler that cannot finish the work inline — the FUSB302B needs I2C, which
takes milliseconds — **masks** the source, hands off to a task, and the task
clears the device, acknowledges the bit and unmasks.

The clear works whether or not the source is enabled, so those last three have
no ordering requirement between them. **The ordering hazard belonged to
claim/complete**, where completing after masking stranded the claim: the source
could not go pending while a claim was outstanding, and a masked source had
nothing to complete against, so the line never fired again. It cost this board
one interrupt per Type-C port per boot. Removing claim/complete removed it.

The one order that does remain is the peripheral's: **clear the device, then
clear the bit.** For a level source the second is ignored while the first has
not happened.
