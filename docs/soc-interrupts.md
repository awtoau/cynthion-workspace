# SoC interrupts — the design

Every interrupt-capable signal on the board, what should raise an interrupt, and
what is still undecided. **This is the design, not a description of the build.**
Where the two differ, the "built" column says so.

**Index:** [`README.md`](README.md) · siblings
[`soc-clocking.md`](soc-clocking.md), [`soc-memory-bus.md`](soc-memory-bus.md)

## Every interrupt-capable signal

| chip | refdes | chip part | signal | trigger | built | design |
|---|---|---|---|---|---|---|
| — | — | 16550 in fabric | console UART | level | source 1 | keep |
| — | — | 16550 in fabric | Apollo link UART | level | source 2 | keep |
| — | — | I2C master in fabric | transaction complete | level | source 3 | keep |
| **FUSB302B** | `U2` | *IC USB TYPE C CTLR PROGR 14-MLP* | TARGET `int`, pin 5 | level | source 4 | keep |
| **FUSB302B** | `U12` | as above | AUX `int`, pin 5 | level | source 5 | keep |
| **PAC1954** | `U1` | `PAC195X-1-VQFN`, four-channel current/voltage monitor | `GPIO/ALERT2`, pin 15 | level | source 6, **not enabled** | enable it, or record why not |
| **PAC1954** | `U1` | as above | `SLOW/ALERT1`, pin 1 | level | **spent** — hard-driven as SLOW output | **runtime-selectable**: SLOW output or ALERT1 source |
| — | — | `SidebandDebug` in fabric | FPGA_ADV byte arrived, ball **T6** → **SAMD11** `U6` pin 8 | level | CSR count only | **make it a source** |
| **DPO2036** | `U13` | *4-CH OVER-VOLTAGE PROTECTION FOR CC/SBU PINS ON USB TYPE-C* | TARGET `FAULTB`, pin 6 | **edge** ([why](chips/dpo2036-cc-sbu-protection.md)) | CSR bit only | **make it a source** |
| **DPO2036** | `U14` | as above | AUX `FAULTB`, pin 6 | **edge** | CSR bit only | **make it a source** |

Balls, pull-ups and every unused pin: [`chips/ecp5/pin-usage.md`](chips/ecp5/pin-usage.md).

## Why the sideband must interrupt

The sideband exposes `rx`, the last byte Apollo sent, and `rxcnt`, how many have
arrived. The firmware polls and compares the count against its own copy.

**`rx` holds one byte.** Two arrivals between polls and the first is
unrecoverable — `rxcnt` reports that it happened and cannot give it back. An
interrupt per arrival makes it one byte per interrupt.

The count stays: it is how a repeat is told from a silence, and it is not
read-to-clear, so reading has no side effect.
[`chips/cynone-sideband.md`](chips/cynone-sideband.md).

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
