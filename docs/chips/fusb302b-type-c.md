# FUSB302B ×2 — the USB-C PD controllers

Two `FUSB302BMPX` USB Type-C / Power Delivery controllers on Cynthion r1.4,
refdes **U12** and **U14** (`repos/cynthion-hardware/type_c.kicad_sch`).

**Index:** [`../hardware.md`](../hardware.md)

## Both are at I2C address `0x22` — which is *why* there are two buses

| bus resource | device | address | SCL | SDA | `int` | `fault` | SBU1 / SBU2 |
|---|---|---|---|---|---|---|---|
| `target_type_c` 0 | FUSB302B | **`0x22`** | A4 | C4 | A3 (n) | D4 (n) | A2 / E4 |
| `aux_type_c` 0 | FUSB302B | **`0x22`** | H12 | G14 | H14 (n) | J14 (n) | H13 / K14 |

Two devices at the same fixed address cannot be distinguished on one bus, so the
board gives them separate pin-sets. **"Just put them on one bus" is not available
in hardware** — the mux is forced, not chosen. That constraint is what drives the
multiplexed-master design in
[`../gateware-architecture-plan.md`](../gateware-architecture-plan.md) (#98).

**Only TARGET and AUX have PD controllers.** CONTROL has a Type-C connector but no
FUSB302B (commit `0ff3b5d`).

Declared in `ecp5-test/cynthion_platform/cynthion_r1_4.py`. `scl` is `dir="o"` —
push-pull, no readback — so **clock stretching is impossible on this board**
(`ecp5-test/riscv/i2c_master.py`). `scl`/`sda` carry `PULLMODE="NONE"`; the
pull-ups are on the board.

## Measured on this board

Bus scan (commit `82b0f1e`, `ecp5-test/pins/i2c_scan.py`), 0x08–0x77 write-address
probe on all three buses:

```
power_monitor    0x10    PAC1954, the known-good control
target_type_c    0x22    FUSB302B
aux_type_c       0x22    FUSB302B
```

Register reads (commit `0ff3b5d`, `ecp5-test/pins/fusb302_id.py`):

| register | target | aux | reads | decode |
|---|---|---|---|---|
| `DEVICE_ID` `0x01` | **`0x91`** | **`0x91`** | 187 each | version 9 / revision 1 — **FUSB302B revision B**; both identical, as expected for two of the same part |
| `STATUS0` `0x40` | `0x01` | `0x01` | | **VBUSOK clear on both** — neither port powered at the time |

**The climbing read counts matter as much as the values.** A static value could be
a stuck bus; a count that advances proves transactions are completing.

`STATUS0` is read rather than `MEASURE` for VBUS: `MEASURE` (`0x04`) is a *write*
register that sets a comparator threshold, and the result appears in `STATUS0` as
`COMP` — so getting an actual voltage means binary-searching six bits. `VBUSOK` in
`STATUS0` bit 7 needs no configuration and answers "is this port powered" directly.

**Nothing in this tree configures these parts.** `int` and `fault` therefore stay
inactive no matter what is plugged in: until the CC pull-downs and measure block
are enabled in `SWITCHES0`, the controller performs no detection at all. Whether
the control registers still hold their reset defaults — which would settle whether
anything has *ever* configured them — has not been checked.

**Not measured:** anything requiring configuration — attach detection, CC
orientation, PD negotiation, per-port voltage.

## Register map

These registers are **not** in the SoC's memory map. The FUSB302B is an external
I2C device, so its map lives in this note rather than in the generated PAC — see
[Register reference](../hardware.md#register-reference) for where that boundary is.

Confirmed identical across the MIT and GPL sources below, which is a useful check:
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

### Where the map came from

The vendor datasheet is awkward to obtain — onsemi times out and every mirror
found is behind bot protection, including through a headed browser. It is not
needed: the part is well covered by open-source implementations.

| Source | Licence | Use here |
|---|---|---|
| **`manuelbl/zy12pdn-oss`** | **MIT** | **Best fit.** Complete register map plus a working driver, and MIT sits comfortably in a BSD-3 codebase |
| `apache/nuttx` `drivers/usbmisc/fusb302.c` | Apache-2.0 | Compatible; a different implementation to cross-check against |
| `espressif/esp-usb` | unclear | Cross-reference only, licence not declared |
| Linux `drivers/usb/typec/tcpm/fusb302_reg.h` | **GPL-2.0** | Authoritative, but **do not vendor** — read it, do not copy it |
| `zrna-research/akso` | GPL-3.0 | Same caution |

Register *addresses* are facts about the hardware and can be documented freely
regardless of where they were read. The *files* carry their licences, so a GPL
header must not be copied into this tree even though the numbers inside it may be
written down.

## The driver belongs in software, not gateware

The MIT reference implementation is 323 lines of C++ with 45 functions, and its
shape argues the point better than any principle would:

    poll()                   continuous, event driven
    check_for_interrupts()   respond to INT asynchronously
    check_for_msg()          parse PD messages out of a FIFO
    establish_retry_wait()   timers and retry state machines
    establish_usb_pd_wait()  multi step negotiation with timeouts

That is sequential, stateful, timer-driven logic with heavy branching — what a CPU
is good at and what a hand-written FSM is painful at.

**Gateware does the minimum to prove the bus works, and no more.** The precedent is
the [PAC1954](pac1954-power-monitor.md), whose sideband bitstream reads a single
register on a loop and blinks an LED when it reads the expected value.
`fusb302_id.py` is the same shape for these parts.

**Firmware on the RISC-V implements everything else**, as a port of the MIT driver:
configuration, interrupt handling, PD message parsing, retry timers, negotiation. A
USB-PD specification change then becomes a firmware edit rather than a bitstream
rebuild, and firmware can be debugged with a terminal.

The line is not a matter of taste. **Anything that needs a timer, a retry, or a
decision based on a previous message is software. Anything that is "put this byte
on the bus and tell me what came back" is gateware.** The one exception that might
later justify gateware is timestamping PD messages, for the analyser use case.

Initialisation, as the MIT driver does it — useful as a shape rather than something
to copy verbatim:

1. Write `RESET` with software reset and PD reset set — start from a known state.
2. Write `POWER` enabling all blocks except the internal oscillator, which is only
   needed for PD messaging.
3. Configure `CONTROL3` for automatic retries.
4. Enable the CC pull-downs and measurement block in `SWITCHES0` so attach
   detection works and `INT` begins asserting.

Step 4 is the one that matters for the state the board is in today.

## Interrupts

Each Type-C bus brings an `int` and a `fault` line, so six signals for two devices.
They do **not** need six PLIC sources — the `int` lines can be OR-ed into one.
With a multiplexed controller only one device can be talked to at a time, so
per-device sources buy nothing: the handler has to serialise its register reads
over the shared bus regardless, and the interrupt register has to be read to decode
*and clear* the cause either way.

**The trap, when this is built:** a shared line is level-sensitive, so the handler
must read and clear *every* asserted device before it returns. Missing one leaves
the line asserted, the interrupt re-fires immediately, and the result is a storm
that presents as a hung CPU — which on this project has repeatedly been mistaken
for dead gateware.

Keep `fault` distinct from `int`. It means something different, and it is the one
worth noticing unambiguously rather than after a register read.

Not urgent: PD negotiation is not on the critical path. The value of the interrupt
is that a state change can be looked into when it happens instead of polled.

## Code and scripts

| | |
|---|---|
| liveness gateware | `ecp5-test/pins/fusb302_id.py` — JTAG applet `0x46555342` "FUSB" |
| bus scanner | `ecp5-test/pins/i2c_scan.py` — applet `0x49324353` "I2CS" |
| multiplexed master — **simulation only, never on silicon** | `ecp5-test/i2c/multiplexed.py`, `test_multiplexed.py` |
| `DEVICE_ID` decode helper | `scripts/sideband_decoder.py` |

**There is no host-side script for either applet.** The values above were read ad
hoc and survive only in the commit messages cited — worth fixing if these parts are
picked up again.

The scanner and the ID reader are **separate bitstreams** because two I2C masters
cannot share a bus: LUNA's `I2CInitiator` and `I2CRegisterInterface` both drive
SDA, and instantiating both against the same pads is a `DriverConflict`.

Tracking: [#98](https://github.com/awtoau/cynthion-workspace/issues/98).
