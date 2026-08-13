# SoC interrupts — the design

What can interrupt this CPU and what should. **This is the design, not a
description of the build.** Where the two differ, the "built" column says so.

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

| chip | refdes | chip part | signal | trigger | built | design |
|---|---|---|---|---|---|---|
| — | — | 16550 in fabric | console UART | level | source 1 | keep |
| — | — | 16550 in fabric | Apollo link UART | level | source 2 | keep |
| — | — | I2C master in fabric | transaction complete | level | source 3 | keep |
| **FUSB302B** | `U2` | *IC USB TYPE C CTLR PROGR 14-MLP* | TARGET `int`, pin 5 | level | source 4 | keep |
| **FUSB302B** | `U12` | as above | AUX `int`, pin 5 | level | source 5 | keep |
| **PAC1954** | `U1` | `PAC195X-1-VQFN`, four-channel current/voltage monitor | `GPIO/ALERT2`, pin 15 | level | source 6, **not enabled** | enable it, or record why not |
| **PAC1954** | `U1` | as above | `SLOW/ALERT1`, pin 1 | level | **spent** — hard-driven as SLOW output | **runtime-selectable**: SLOW output or ALERT1 source |
| — | — | sideband in fabric | FPGA_ADV byte arrived, ball **T6** → **SAMD11** `U6` pin 8 | level | CSR count only | **make it a source** (#509) |
| **USB3343** | TARGET | *hi-speed USB ULPI transceiver* | link event — `DIR` carries an RX CMD | edge | none | **one source** |
| **USB3343** | AUX | as above | link event | edge | none | **one source** |
| **USB3343** | CONTROL | as above | link event | edge | none | **one source** |
| **DPO2036** | `U13` | *4-CH OVER-VOLTAGE PROTECTION FOR CC/SBU PINS ON USB TYPE-C* | TARGET `FAULTB`, pin 6 | **edge** ([why](chips/dpo2036-cc-sbu-protection.md)) | CSR bit only | **make it a source** |
| **DPO2036** | `U14` | as above | AUX `FAULTB`, pin 6 | **edge** | CSR bit only | **make it a source** |

Balls, pull-ups and every unused pin: [`chips/ecp5/pin-usage.md`](chips/ecp5/pin-usage.md).

## One source per device, never an OR

Three PHYs get three sources, three FUSB302Bs get two, and nothing is merged.

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
claim/complete. Pending bits and enables.

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
unmasks when it is done.

With pending bits and enables that is the whole of it. **The ordering hazard
belongs to claim/complete**, where completing after masking strands the claim:
the source cannot go pending while a claim is outstanding, and a masked source
has nothing to complete against, so the line never fires again. Removing
claim/complete removes the hazard.
