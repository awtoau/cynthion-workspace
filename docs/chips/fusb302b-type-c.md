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

~~**Nothing in this tree configures these parts.**~~ **Superseded.** The RISC-V
SoC firmware now configures both at boot — see *From the SoC shell* below.

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

Each Type-C bus brings an `int` and a `fault` line, so four signals for two devices.
**Each `int` gets its own PLIC source** — TARGET on 4, AUX on 5. Neither `fault` gets
one.

The `int` lines were OR-ed onto a single source until #135. The argument for the OR
was that with a multiplexed controller only one device can be talked to at a time,
so per-device sources buy nothing. Servicing does serialise, and always will, but
that is a fact about the bus rather than about which device the handler is told to
service. See [`../decisions.md`](../decisions.md) decision 8.

**The trap the OR carried:** a shared line is level-sensitive, so the handler must
read and clear *every* asserted device before the source is re-enabled. Missing one
leaves the line asserted, the interrupt re-fires immediately, and the result is a
storm that presents as a hung CPU — which on this project has repeatedly been
mistaken for dead gateware. One source per device makes that unmissable by
construction: there is only ever one device behind the level being cleared.

`fault` stays outside the interrupt, and not only because it means something
different from `int`. **Nothing in the firmware can clear it** — it drops when the
device's fault does — so an interrupt on it would have to stay masked until a poll
saw the level go away. That would add a handler and keep the poll.

Not urgent: PD negotiation is not on the critical path. The value of the interrupt
is that a state change can be looked into when it happens instead of polled.

## Code and scripts

| | |
|---|---|
| liveness gateware | `ecp5-test/pins/fusb302_id.py` — JTAG applet `0x46555342` "FUSB" |
| bus scanner | `ecp5-test/pins/i2c_scan.py` — applet `0x49324353` "I2CS" |
| multiplexed master — superseded, and **it was never on silicon** | `ecp5-test/i2c/multiplexed.py`, `test_multiplexed.py` |
| the mux that *is* on silicon | `ecp5-test/riscv/i2c_mux.py`, checked in `scripts/soc_board_sim.py` |
| firmware | `firmware/cynthion-soc/src/bus.rs` (owns the controller and the select), `fusb302.rs`, `typec.rs` |
| bus and device ownership | `scripts/soc_i2c_owner_sim.py` — a stale select is *answered* by the other port, not refused |
| `DEVICE_ID` decode helper | `scripts/sideband_decoder.py` |

**There is no host-side script for either applet.** The values above were read ad
hoc and survive only in the commit messages cited — worth fixing if these parts are
picked up again.

The scanner and the ID reader are **separate bitstreams** because two I2C masters
cannot share a bus: LUNA's `I2CInitiator` and `I2CRegisterInterface` both drive
SDA, and instantiating both against the same pads is a `DriverConflict`.

## From the SoC shell — `typec` and `i2c <bus>`

Both controllers are reached from the RISC-V SoC over **one** I2C controller whose
clock and data fan out to three pin-sets under a two-bit select
(`ecp5-test/riscv/i2c_mux.py`). That mux is what makes two devices at `0x22`
addressable at all, and **this is its first appearance on silicon** — the earlier
`ecp5-test/i2c/multiplexed.py` was only ever simulated. Confirmed working:

```
> i2c target
i2c   @f0000610 prescale 149 bus 0 (target_type_c)
  22 answers
> i2c aux
i2c   @f0000610 prescale 149 bus 1 (aux_type_c)
  22 answers
> i2c power
i2c   @f0000610 prescale 149 bus 2 (power_monitor)
  10 PAC1954-1 manufacturer 54 revision 02

> typec
type-c @f0000620  lines 00  irq serviced 0  configured
  target device 91  vbus present  nothing on CC           int 0  fault 0
  aux    device 91  vbus present  vRd-3.0A                int 0  fault 0
```

`device 91` is version 9 revision 1 — FUSB302B revision B — on both, matching
what `fusb302_id.py` read over JTAG. `typec init` re-runs the configuration.

**The select is written before every transaction and never remembered.** Nothing
in a reply says which bus it came from: both controllers answer `0x22` and both
report `0x91`, so a stale select does not produce an error, it produces a
plausible answer from the wrong chip. The gateware also holds a select written
mid-transfer until the controller is idle, so a firmware mistake cannot become a
bus-level one — switching pin-sets between a START and its STOP would leave one
bus half-driven and put an edge on another that every device on it reads as a
START. The mux **resets** to the power monitor, so the rails are readable before
any firmware writes that register.

### What is configured, and the one thing that is not

Enough to interrupt on a state change, and nothing that changes what a port
presents to whatever is plugged into it:

| register | value | why |
|---|---|---|
| `RESET` | `SW_RES` | start from documented values, not from what a previous bitstream left |
| `POWER` | bandgap+wake, measure, receiver | not the internal oscillator — that is only needed to *send* PD messages |
| `SWITCHES0` | `MEAS_CC1` | routes CC1 to the comparator; **drives nothing** |
| `MASK` | `I_BC_LVL` and `I_VBUSOK` unmasked | CC level and VBUS: the two state changes worth an interrupt |
| `MASKA`, `MASKB` | all masked | PD and hard-reset events, which nothing acts on yet — an unmasked interrupt with no handler is a storm on a shared level line |
| `CONTROL0` | `INT_MASK` cleared | the switch that lets the `INT` pin assert at all. Its reset value is *masked*, which is why these parts never interrupted before |

**The CC pull-downs (`PDWN1`/`PDWN2`) are deliberately NOT enabled.** They would
give full attach detection and they are the only bit in that sequence that
changes a port electrically: presenting Rd tells a source this port is a sink.
One of these two controllers is on **AUX — the port carrying the USB console the
shell answers on** — so the cost of getting it wrong is losing the console, on a
board whose CC lines nothing in this tree has ever driven. `MEAS_CC1` still gives
`BC_LVL` (the voltage a source's Rp puts on CC) and `VBUSOK`, which is a state
change on both ports obtained without asserting anything onto a connector.
Enabling them is a one-line change with a known consequence.

### TARGET-C as a source

`vbus charge` changes **TARGET-C only** to the opposite role and leaves AUX
measure-only. It writes:

| register | value | effect |
|---|---:|---|
| `SWITCHES0` `0x02` | `0xc0` | `PU_EN1`/`PU_EN2`; `PDWN1`/`PDWN2` clear |
| `CONTROL0` `0x06` | `0x04` / `0x08` / `0x0c` | Default / 1.5 A / 3 A advertisement |
| `CONTROL2` `0x08` | `0x07` | source-only autonomous CC1/CC2 polling |

The register definitions are on FUSB302B datasheet pages 21, 23 and 24.
`STATUS1A.TOGSS` on page 28 reports source on CC1 (`001`) or CC2 (`010`), so
cable orientation is detected rather than assumed. Bare `vbus charge` uses
Default current; the higher levels require explicit arguments.

### The interrupt, and where the level-sensitive trap is actually handled

Each `int` line has its own PLIC source. Clearing a controller means reading three
read-to-clear registers over I2C — about a millisecond at 80 kHz, on the same
controller the power monitor's 50 ms poll uses. So the handler does **not** do it:

| context | what it does |
|---|---|
| interrupt handler (`src/irq.rs`) | masks **that** source, records **which** port, returns |
| main loop (`src/typec.rs`) | clears that one device, re-enables that one source |

The deferral is about the I2C, not about the sharing, which is why it survived the
split. What the split changed is the obligation: with one source the loop had to
clear *every* asserting device before re-enabling, and missing one left the shared
line high. Now there is one device behind each source and nothing to miss. The mask
is per device too, so a TARGET awaiting its clear no longer blinds AUX.

A device still asserting when its source comes back re-fires immediately, which is
correct — there is still work — and the handler masks it again, so it is a loop with
the CPU making progress between passes rather than a storm.

The handler **records rather than prints**: printing from a handler spins on a
UART FIFO inside an interrupt. See `firmware/cynthion-soc/src/events.rs` and
`docs/hardware.md`. The record now carries the port, which the shared source could
not supply without a register read.

`fault` is kept out of the interrupt entirely. It means something different from
`int` and is meant to be distinguishable without a register read, and **nothing
here can clear it** — it drops when the device's fault does. It is reported in
`LINES` and polled at 50 ms alongside the rails; a change in either direction is
announced once.

Tracking: [#98](https://github.com/awtoau/cynthion-workspace/issues/98),
[#121](https://github.com/awtoau/cynthion-workspace/issues/121).
