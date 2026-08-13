# SoC interrupts — the design

What can interrupt this CPU and what should. Seventeen sources, ten built. The
controller is `amaranth_soc.csr.event.EventMonitor` — pending bits, enables, W1C,
a trigger fixed per source at elaboration — wrapped by
[`gateware/soc/cpu/intc.py`](../gateware/soc/cpu/intc.py) for the numbering.
`scripts/soc_intc_sim.py` checks this file's table against what is wired.

**Index:** [`README.md`](README.md) · siblings
[`soc-clocking.md`](soc-clocking.md), [`soc-memory-bus.md`](soc-memory-bus.md)

## Four ways into the trap handler

Three interrupt causes and one synchronous one, all four through `mtvec`.

| cause | raised by | used for |
|---|---|---|
| **machine external** (MEI) | the interrupt controller | every board signal — the table below |
| **machine timer** (MTI) | the CLINT's `mtime` reaching `mtimecmp` | the 1 ms tick, and RTIC's monotonic |
| **machine software** (MSI) | the CLINT's `msip`, written by the CPU itself | RTIC pending a task — this is how `riscv-slic` dispatches |
| **exceptions** | the instruction being executed | faults: load/store access, illegal instruction, misaligned, `ecall`, breakpoint |

**Only the external one is a board signal.** Timer and software are the CPU
interrupting itself through the CLINT; an exception is the current instruction
failing.

### Software interrupts are the dispatcher

`msip` releases a task: RTIC writes it, the CPU takes MSI, `riscv-slic` decides
which task runs. Software priority (below) is on this cause, not the external one.

### Exceptions must not be silent

Without a handler `riscv-rt` links `abort`, so a bus fault is an infinite loop
with no output. The design carries an exception handler reporting cause, `pc` and
faulting address; a load from an unmapped address is how the trap-heavy load
exercises it.

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
| 10 | **USB3343** TARGET | link event — enables in `0Dh`/`10h`, delivered as an RX CMD on `DIR` ([and a transmit swallows transients](chips/usb3343-ulpi-phy.md)) | edge | no |
| 11 | **USB3343** AUX | link event | edge | no |
| 12 | **USB3343** CONTROL | link event | edge | no |
| 13 | — | USER button, ball **M14** | **edge**, rise | **yes** |
| 14 | — | sideband byte, ball **T6** → **SAMD11** `U6` pin 8 | **edge** — `received_strobe` is one cycle | no — CSR count only ([#509](https://github.com/awtoau/cynthion-workspace/issues/509)) |
| 15 | — | SBU peripheral, TARGET — balls **A2**, **E4** | level | no — no peripheral ([`sbu.md`](sbu.md), [#518](https://github.com/awtoau/cynthion-workspace/issues/518)) |
| 16 | — | SBU peripheral, AUX — balls **H13**, **K14** | level | no — no peripheral |
| 17 | — | PLL loss of lock | **edge**, fall on `locked` | **yes** |

* The seven not built need a peripheral that does not exist. Their numbers are
  gaps, tied low in `intc.py`, so wiring one up renumbers nothing.
* `rise`/`fall` is the trigger as the controller sees it, after the platform's
  `PinsN` has undone an active-low pin: `FAULTB` and the button are `PinsN` and
  assert as a rise; the PAC1954's ALERT is a raw `io` pad and asserts as a fall.

Balls, pull-ups and every unused pin: [`chips/ecp5/pin-usage.md`](chips/ecp5/pin-usage.md).

## What the trigger actually decides

**Not whether a pulse is captured.** Every pending bit latches, level sources
included — `amaranth_soc.event.Monitor` sets it on the trigger and clears it only
on a W1C, so a 5 µs pulse sets the bit either way. What it decides is **when the
bit can be acknowledged**:

| | level | edge |
|---|---|---|
| clear while the line is asserted | **ignored** — the set arm wins | takes |
| clear once the line is idle | takes | takes |
| line still asserted after a clear | fires again, immediately | silent until the next edge |

So the rule is the peripheral's, not the pin's:

* **A backlog the CPU drains** — a 16550's FIFO → **level**. Draining clears
  it; being re-entered while bytes remain is correct. An edge loses everything
  after the first burst.
* **An event the CPU cannot clear** — `FAULTB` held 30 ms, a button held down,
  a PLL that stays unlocked → **edge**. As a level it storms: unacknowledgeable
  until the hardware releases, so the only defence is a mask plus a poll to
  decide when to unmask — a handler and a poll instead of either.

`scripts/soc_intc_sim.py` asserts each row of that table.

## The PAC1954 alert is not a level

* Threshold alerts latch low until an I2C read clears them at the part —
  milliseconds away in task context, so a level source storms for the whole
  deferral.
* Conversion-complete is a **5 µs pulse that sets no status bit**
  (DS20006539B §5.16.1): nothing to read, nothing to clear, and only the edge
  says it happened.
* So: edge, on the pad's falling edge. [#514](https://github.com/awtoau/cynthion-workspace/issues/514).

## A data line is not an interrupt source

SBU carries DP AUX, a Debug Accessory UART, or nothing, depending on the mode
negotiated. So it gets a **receiver**, and the receiver's interrupt is the
source — as the console's 16550 is the source for USB CDC bytes rather than the
ULPI pins being one. Same for anything arriving as data rather than as an event.

## The button is not debounced in fabric

It bounces, and one press raises several interrupts. A press is a human event, so
the handler has milliseconds of slack and software debounces — in gateware it
would be a timer per source for a thing the CPU settles in a few instructions.

## One source per device, never an OR

Three PHYs get three sources, two FUSB302Bs get two, and nothing is merged.

With one shared source the handler must interrogate every device to learn which
fired — for a PHY, a ULPI register read with a timeout, three times, on every
event. A source is a pending bit and an enable: three cost essentially nothing
over one, and the saved source costs more than it saves.

## Priority is software only

**There is one priority in this design and it is RTIC's**, in `riscv-slic`:
declared per task, deciding which task runs and which can interrupt which, and
the only source of preemption — see [`rtic.md`](rtic.md).

**The interrupt controller has none.** No priority registers, no threshold, no
claim/complete. Two registers, one bit per source:

    0x0  enable    RW   bit n: source n may raise the CPU line
    0x4  pending   RW   bit n: source n has triggered; write 1 to clear

Both are one CPU word wide and **the fourth byte access commits** (`alignment=2`
on the CSR multiplexer, the shadow rule `src/gpio.rs` documents): three byte
writes write nothing.

### Why the controller cannot preempt, whatever it offers

It gives the CPU **one** line. When that fires the CPU traps and `mstatus.MIE`
is cleared, per the privileged specification, so a source going pending
mid-handler does nothing until that handler finishes. PLIC 1.0.0 line 93 says it
from the other side: *"the PLIC provides no concept of interrupt preemption or
nesting"*.

### Deferring a source

A handler that cannot finish the work inline — the FUSB302B needs I2C, which
takes milliseconds — **masks** the source and hands off to a task, which clears
the device, acknowledges the bit and unmasks.

* Acknowledging works whether or not the source is enabled, so those three have
  no ordering requirement between them.
* The one order that does bind is the peripheral's: **clear the device, then
  clear the bit.** For a level source the second is ignored while the first has
  not happened.
* Edge sources need no mask at all: the handler acknowledges and is not
  re-entered until the next edge.
