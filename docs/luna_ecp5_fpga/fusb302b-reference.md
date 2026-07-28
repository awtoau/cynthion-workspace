# FUSB302B: sources for register definitions and driver logic

The vendor datasheet is awkward to obtain — onsemi times out, and every mirror
found is behind bot protection, including through a headed browser. It is not
needed: the part is well covered by open-source implementations, several under
licences compatible with this repository.

## Sources, by usefulness

| Source | Licence | Use here |
|---|---|---|
| **`manuelbl/zy12pdn-oss`** | **MIT** | **Best fit.** Complete register map plus a working driver, and MIT sits comfortably in a BSD-3 codebase |
| `apache/nuttx` `drivers/usbmisc/fusb302.c` | Apache-2.0 | Compatible; a different implementation to cross-check against |
| `espressif/esp-usb` | unclear | Cross-reference only, licence not declared |
| Linux `drivers/usb/typec/tcpm/fusb302_reg.h` | **GPL-2.0** | Authoritative, but **do not vendor** — read it, do not copy it |
| `zrna-research/akso` | GPL-3.0 | Same caution |

A note on what is and is not copyrightable: register *addresses* are facts about
the hardware and can be documented freely regardless of where they were read.
The *files* carry their licences, so a GPL header must not be copied into this
tree even though the numbers inside it may be written down.

## Register map

Confirmed identical across the MIT and GPL sources, which is a useful check —
two independent implementations agreeing makes a transcription error unlikely.

| Address | Register | Notes |
|---|---|---|
| `0x01` | `DEVICE_ID` | identity and silicon revision |
| `0x02` | `SWITCHES0` | CC pull-up/pull-down enables, VCONN, measure select |
| `0x03` | `SWITCHES1` | transmit config, auto-CRC, data role |
| `0x04` | `MEASURE` | comparator threshold |
| `0x05` | `SLICE` | receive threshold |
| `0x06`–`0x09` | `CONTROL0`–`CONTROL3` | interrupt masking, transmit, toggle, retries |
| `0x0A` | `MASK` | interrupt mask |
| `0x0B` | `POWER` | per-block power enables |
| `0x0C` | `RESET` | software and PD reset |
| `0x0D` | `OCPREG` | over-current threshold |
| `0x0E`–`0x0F` | `MASKA`, `MASKB` | further interrupt masks |
| `0x3C`–`0x3F` | `STATUS0A`, `STATUS1A`, `INTERRUPTA`, `INTERRUPTB` | |
| `0x40`–`0x42` | `STATUS0`, `STATUS1`, `INTERRUPT` | |
| `0x43` | `FIFOS` | PD message FIFO |

I2C address is **`0x22`**, confirmed empirically by the bus scan rather than
taken from a document — both controllers acknowledge there.

## Initialisation, as the MIT driver does it

Useful as a shape rather than something to copy verbatim:

1. Write `RESET` with software reset and PD reset set — start from a known state.
2. Write `POWER` enabling all blocks except the internal oscillator, which is
   only needed for PD messaging.
3. Configure `CONTROL3` for automatic retries.
4. Enable the CC pull-downs and measurement block in `SWITCHES0` so attach
   detection works and `INT` begins asserting.

Step 4 is the one that matters for the immediate question here: until the pull-
downs and measure block are enabled, the controller performs no detection, and
`int` and `fault` correctly stay inactive no matter what is plugged in.

## The driver belongs in software, not gateware

The MIT reference implementation is 323 lines of C++ with 45 functions, and its
shape argues the point better than any principle would:

    poll()                   continuous, event driven
    check_for_interrupts()   respond to INT asynchronously
    check_for_msg()          parse PD messages out of a FIFO
    establish_retry_wait()   timers and retry state machines
    establish_usb_pd_wait()  multi step negotiation with timeouts

That is sequential, stateful, timer-driven logic with heavy branching — what a
CPU is good at and what a hand-written FSM is painful at. USB-PD in particular
is a protocol with message types, negotiation phases and timeouts; expressing it
in gateware means a large state machine that is hard to read and harder to
change.

So the split should be:

**Gateware does the minimum to prove the bus works, and no more.** The
precedent is the PAC1954, where the sideband bitstream uses LUNA's
`I2CRegisterInterface` to read a single register on a loop and blink an LED
when it reads the expected value. No state machine, no protocol, no branching —
a liveness check.

The equivalent here is reading `DEVICE_ID` (0x01) on each of the two Type-C
buses and reporting the result. That answers "is the chip there and does the
bus work" and nothing else, which is exactly what a test bitstream should
answer.

**Firmware on the RISC-V implements everything else**, as a port of the MIT
driver: the register-by-register configuration, interrupt handling, PD message
parsing, retry timers and negotiation state. None of that belongs in an FPGA.
A USB-PD specification change then becomes a firmware edit rather than a
bitstream rebuild, and firmware can be debugged with a terminal, which gateware
cannot.

The line between the two is not a matter of taste. Anything that needs a timer,
a retry, or a decision based on a previous message is software. Anything that is
"put this byte on the bus and tell me what came back" is gateware.

The one exception that might later justify gateware is timestamping: if PD
messages need capturing with precise timing for analysis, a small capture block
next to the I2C master beats a CPU polling. That is a refinement for the
analyser use case, not part of getting a port working.

## What this confirms about the board

The bus scan found both controllers at `0x22`, so they are powered, clocked and
listening. Combined with the register map above, the next step is cheap: read
`DEVICE_ID` to confirm identity and revision, then read `SWITCHES0` and
`CONTROL0` and compare against their reset values.

That last comparison is the one that settles whether anything has ever
configured these parts. A code search across the tree found nothing that drives
them, but absence of evidence in source is weaker than reading the silicon: if
the control registers hold reset defaults, nothing has touched them since power-
on.
