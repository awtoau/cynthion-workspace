# FUSB302B ×2 — the USB-C PD controllers

Two `FUSB302BMPX` USB Type-C / Power Delivery controllers on Cynthion r1.4,
refdes **U12** (AUX) and **U2** (TARGET) — `type_c.kicad_sch` is one sheet
instantiated once per port, so both parts live in the same file and the refdes
comes from the instance path, not the symbol. **U14 is not one of them**; it is
the AUX port's `DPO2036` over-voltage protection (TARGET's is `U13`), and an earlier
revision of this line named it as the second FUSB302B.

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
[`../gateware-architecture-plan.md`](../gateware-architecture-plan.md) ([#98](https://github.com/awtoau/cynthion-workspace/issues/98)).

**Only TARGET and AUX have PD controllers.** CONTROL has a Type-C connector but no
FUSB302B (commit `0ff3b5d`).

Declared in `gateware/board/cynthion_r1_4.py`. `scl` is `dir="o"` —
push-pull, no readback — so **clock stretching is impossible on this board**
(`gateware/soc/peripherals/i2c_master.py`). `scl`/`sda` carry `PULLMODE="NONE"`; the
pull-ups are on the board.

## Performance

Structure per [`../plans/performance-sections.md`](../plans/performance-sections.md);
cross-cut against every other bus in [`bus-speed-audit.md`](bus-speed-audit.md).
Datasheet references are **FUSB302B Rev 1.3**,
[`sources/FUSB302B-958669.pdf`](../../sources/FUSB302B-958669.pdf), 38 pp.

**This part is what caps the shared I²C bus at 1 MHz**, and it is the only device
on it that does. The [PAC1954](pac1954-power-monitor.md) will do 3.4 MHz; these
two will not, and one controller serves all three.

### 1. Theoretical maximum

The electrical table's own heading names the modes — *"I²C Interface Pins –
Standard, Fast, or Fast Mode Plus Speed Mode (SDA, SCL)"* (p. 17) — and stops
there. There is no High-Speed row. **"I²C Specifications Fast Mode Plus I²C
Specification"**, p. 18:

| symbol | parameter | min | max |
|---|---|---|---|
| `fSCL` | SCL clock frequency | 0 | **1000 kHz** |
| `t_LOW` | SCL low period | 0.5 µs | — |
| `t_HIGH` | SCL high period | 0.26 µs | — |
| `t_SU;STA`, `t_HD;STA`, `t_SU;STO` | condition setup / hold | 0.26 µs | — |
| `t_SU;DAT` | data setup | 50 ns | — |
| `t_BUF` | bus free, STOP to START | 0.5 µs | — |
| `t_r`, `t_f` | SDA and SCL rise / fall | — / 6 ns | **120 ns** |
| `t_SP` | spike width suppressed by the input filter | 0 | 50 ns |
| `t_VD;DAT`, `t_VD;ACK` | **the part's own** SCL-low to SDA-valid | 0 | **0.45 µs** |
| `Cb` | capacitive load per bus line | — | 550 pF |
| `CI` | capacitance per I/O pin | — | 5 pF typ |

At 1 MHz a byte plus its acknowledge is 9 clocks, so the byte ceiling is
**111.1 kB/s** — and every access here is one or two register bytes behind an
address, so the transaction count dominates and the byte rate never will.

Interrupt latency has one figure: `TINT_Mask`, *"Time from global interrupt mask
bit cleared to when INT_N goes LOW"*, **50 µs** (p. 17).

**PD message turnaround is not established.** Rev 1.3 describes the BMC physical
layer and a 48-byte packet FIFO but states no signalling rate; that number lives
in the USB PD specification, which is not in
[`../../sources/`](../../sources/README.md). What would establish it: the PD
spec's BMC bit rate and interframe timings, or a measurement on the CC line.

### 2. Achievable on this board

**Two devices at one fixed address is the binding constraint, and it is not a
rate.** They are on separate pin-sets behind an FPGA-side mux with a **single**
`I2CMaster`, so target and aux are serialised against each other *and* against
the power monitor. Raising `fSCL` shortens each transaction; it does not make
them concurrent. Three controllers would, at three times the logic.

**SCL is push-pull, so only SDA rises through a resistor.** Both `scl`
subsignals are `Pins(..., dir="o")` with no `oe`. The pull-ups are **2.2k** —
R97 on target, R33 on aux, from `production/bom.csv` — and `t_r ≈ 0.8473·R·C`
over the 0.3–0.7 V<sub>DD</sub> window gives:

    20 pF ->  37 ns        Fm+ allows 120 ns
    50 pF ->  93 ns
    64 pF -> 120 ns        the Fm+ edge, at this resistor
   100 pF -> 186 ns        Fm+ exceeded, Fast mode still fine

Each part is alone on its segment, and its own contribution is 5 pF typ. **`Cb`
has never been measured**, so the working assumption of 50 pF is an argument
rather than a number — see [`bus-speed-audit.md`](bus-speed-audit.md).

**One Fm+ parameter is a slave *output* and it is the one worth checking**, since
a master cannot fix it by slowing its own edges. `t_VD;DAT` and `t_VD;ACK` are
450 ns max. The controller drives SDA in slot 0 and samples at the end of slot 3,
which is **1000 ns** after SCL falls — 550 ns of margin.

**What we configure:** `I2C_SCL_HZ = 1_000_000` in
[`../../gateware/soc/top.py`](../../gateware/soc/top.py), giving `PRER` = 11 at
`sync` 60 MHz, a 200 ns slot and exactly 1.000 MHz. `t_LOW` at 600 ns is the
tightest parameter, 20% inside the minimum; everything else is above 50%. The
firmware constant is generated from the gateware's own `prescale_for`, so the two
cannot drift.

### 3. Measured

| axis | conditions | figure | source |
|---|---|---|---|
| identity, both parts | `PRER` 11, 1 MHz, one per mux segment | `0x22` manufacturer `01`, on **both** target and aux | `d820d9e` |
| bus rate | derived, not scoped | 1.000 MHz exactly | — |
| **SDA rise time** | — | **never measured** | the one open question on this bus |
| **PD message turnaround** | — | **never measured**, and no datasheet figure to compare against | — |
| interrupt path | level-sensitive source each | works; latency not timed | see *Interrupts* below |

### 4. The gap, and what closes it

| rank | option | worth | effort |
|---|---|---|---|
| — | raise `fSCL` above 1 MHz | **unavailable.** This part stops at Fm+, and the shared controller means the whole bus stops with it | — |
| 1 | fewer transactions per operation | the bus is no longer where the CPU time goes — see [`pac1954-power-monitor.md`](pac1954-power-monitor.md) §3 | [#267](https://github.com/awtoau/cynthion-workspace/issues/267) |
| 2 | a controller per segment | target, aux and the monitor stop serialising | three `I2CMaster` instances; nothing today needs the concurrency |
| 3 | measure `Cb` on SDA | converts the rise-time margin from an argument into a number | a scope on the 0.3–0.7 V<sub>DD</sub> edge |
| 4 | the probe bitstreams, still at 100 kHz | 10× on the bring-up paths | `period_cyc = 600` in [`../../gateware/probes/pins/fusb302_id.py`](../../gateware/probes/pins/fusb302_id.py) and [`../../gateware/probes/pins/i2c_scan.py`](../../gateware/probes/pins/i2c_scan.py) |

## Measured on this board

Bus scan (commit `82b0f1e`, `gateware/probes/pins/i2c_scan.py`), 0x08–0x77 write-address
probe on all three buses:

```
power_monitor    0x10    PAC1954, the known-good control
target_type_c    0x22    FUSB302B
aux_type_c       0x22    FUSB302B
```

Register reads (commit `0ff3b5d`, `gateware/probes/pins/fusb302_id.py`):

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
**Each `int` gets its own source** — TARGET on 4, AUX on 5. Each `fault` gets
one.

The `int` lines were OR-ed onto a single source until [#135](https://github.com/awtoau/cynthion-workspace/issues/135). The argument for the OR
was that with a multiplexed controller only one device can be talked to at a time,
so per-device sources buy nothing. Servicing does serialise, and always will, but
that is a fact about the bus rather than about which device the handler is told to
service. See [`../architecture.md`](../architecture.md) decision 8.

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
| liveness gateware | `gateware/probes/pins/fusb302_id.py` — JTAG applet `0x46555342` "FUSB" |
| bus scanner | `gateware/probes/pins/i2c_scan.py` — applet `0x49324353` "I2CS" |
| multiplexed master — superseded, and **it was never on silicon** | `gateware/probes/i2c/multiplexed.py`, `test_multiplexed.py` |
| the mux that *is* on silicon | `gateware/soc/peripherals/i2c_mux.py`, checked in `scripts/soc_board_sim.py` |
| firmware | `firmware/cynthion-soc/src/bus.rs` (owns the controller and the select), `fusb302.rs`, `typec.rs` |
| bus and device ownership | `scripts/soc_i2c_owner_sim.py` — a stale select is *answered* by the other port, not refused |
| orientation and interrupt decode | `scripts/soc_typec_sim.py` — both bands from one comparator, and the read-to-clear registers |
| the cable-reversal experiment | `scripts/typec_watch.py` — the only thing that can validate orientation |
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
(`gateware/soc/peripherals/i2c_mux.py`). That mux is what makes two devices at `0x22`
addressable at all, and **this is its first appearance on silicon** — the earlier
`gateware/probes/i2c/multiplexed.py` was only ever simulated. Confirmed working:

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

That capture is kept as it was read. The line has since gained a `cc` column —
the orientation and the two bands behind it — so a current reading of the same
port has `cc none 0/0` or `cc cc2 0/1` after the status text. TARGET's
`nothing on CC` above is the reading that motivated it.

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
| `SWITCHES0`, per reading | `MEAS_CC1`, then `MEAS_CC2`, then back | one comparator, two pins — see *Orientation* below |
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

### Orientation — read, and NOT yet validated

There is one comparator and two CC pins, and `SWITCHES0` decides which pin it
sees. The driver used to write `MEAS_CC1` once and never move it, so **a cable
whose CC landed on CC2 was indistinguishable from an empty port** — which is
exactly what TARGET reported above: `vbus present  nothing on CC`, with something
plugged in.

`fusb302::state` now reads both. It reads `SWITCHES0`, points the comparator at
CC1, reads `STATUS0`, points it at CC2, reads `STATUS0` again, and writes the
original value back byte for byte. Only the measure-select field moves, so the
pull enables — including the `PU_EN1`/`PU_EN2` a source role sets — survive a
reading, and `PDWN1`/`PDWN2` stay clear throughout. The comparator needs no
explicit settling delay: each write and the read after it are separate I2C
transactions, several hundred microseconds apart at 80 kHz.

While `CONTROL2.TOGGLE` is set the part's own polling owns `SWITCHES0`, so the
sweep is skipped entirely and orientation comes from `STATUS1A.TOGSS` instead. A
pin that was not measured reports as absent rather than as zero: `board` and
`typec` show `cc ?` with bands `-/-`, which is a different statement from `none`.

**None of this has been validated on hardware.** It is checked by
`scripts/soc_typec_sim.py` against a model whose `STATUS0` really does answer
about whichever pin the select reaches, and it is checked structurally against the
Rust — but the only test that can distinguish a correct reading from a plausible
one is a cable inserted one way and then the other. `scripts/typec_watch.py` is
the tool for that experiment and its `cc` column is what to watch. Until it has
been run, treat `cc1`/`cc2` as "the comparator saw a band on that pin".

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

Each `int` line has its own source. Clearing a controller means reading three
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

**The three registers say why, and that survives the read.** They are read-to-clear,
so the values the clear returns are the only record of the cause that will ever
exist — reading them again gives zero. `fusb302::clear` hands them back,
`typec::service` pushes them through the same deferred ring the handler's record
went into, and `events::report` prints the set bits by name with the raw bytes
beside them:

```
000012.481  type-c: int asserted, port 01
000012.483  type-c target: int I_BC_LVL I_VBUSOK (81 00 00)
```

The hex is not redundant: a reserved bit has no name, and the raw registers are
what a datasheet page can be checked against. `nothing latched` is a real answer —
a masked event still latches without raising the pin.

`fault` is kept out of the interrupt entirely. It means something different from
`int` and is meant to be distinguishable without a register read, and **nothing
here can clear it** — it drops when the device's fault does. It is reported in
`LINES` and polled at 50 ms alongside the rails; a change in either direction is
announced once.

Tracking: [#98](https://github.com/awtoau/cynthion-workspace/issues/98),
[#121](https://github.com/awtoau/cynthion-workspace/issues/121).
