# FPGA_ADV sideband link

Half-duplex request/response between Apollo (ATSAMD11) and the ECP5 over the
single existing FPGA_ADV wire. No PCB change; native JTAG stays enabled.

Apollo is always the master; the FPGA only speaks when asked.

```
Apollo → FPGA:  [CMD]
FPGA  → Apollo: [STATUS][payload ...][CRC8]
```

Implementations: [sideband.py](../../repos/apollo/apollo_fpga/gateware/sideband.py)
(responder), [fpga_adv.c](../../repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c)
(master), [sideband_gateware.py](../../ecp5-test/sideband/sideband_gateware.py)
(test bitstream).

Tracking: [#68](https://github.com/awtoau/cynthion-workspace/issues/68),
[#84](https://github.com/awtoau/cynthion-workspace/issues/84),
[#85](https://github.com/awtoau/cynthion-workspace/issues/85).

## 1. Physical layer

| | |
|---|---|
| Pin | PA09 (SAMD11) ↔ T6 (ECP5) |
| Rate | 230400 baud, 8-N-1, LSB first |
| Idle | High, on internal pull-ups only |
| Logic | 3.3 V LVCMOS, push-pull while driving |
| Flow control | None |

Point-to-point between two pins — not a bus, and **there is no external pull-up
resistor on the net.** The line idles high on the two internal pulls: the ECP5's
`PULLMODE="UP"` and the SAMD11's `GPIO_PULL_UP`.

The ECP5 specifies both of these as currents rather than resistances. The pull
is a weak current source, tens of µA, so its effective impedance is V/I — tens
of kΩ, far too soft to time an edge with. The output driver is separately
selectable via `DRIVE=` (4, 8, 12, 16 or 20 mA); T6 does not set it, so it takes
the 8 mA default.

That split is why the pull strength does not constrain the rate. The pull only
has to hold idle-high when neither end is driving, where speed is irrelevant;
every real edge is driven at 8 mA by whichever end is transmitting.

**Each end drives push-pull while transmitting and tri-states otherwise.** The
FPGA declares T6 as `dir="io"` and gates its output enable on `tx_active`;
Apollo switches PA09 between a driven output and a pulled-up SERCOM input. The
output enables exist to switch direction on a half-duplex wire, not for signal
integrity: two push-pull drivers left enabled together would be a short between
active drivers, so only one is ever enabled.

Which end that is follows from the protocol rather than from any bus
arbitration: the FPGA only ever replies (§6).

The idle pulls also cover configuration, when T6 is high-impedance — Apollo
enables its pull-up before enabling the receiver, since a floating input frames
noise as start bits.

No open-drain, and no board change to add a resistor. Open-drain would make
every rising edge depend on pull-up current, and with only weak internal pulls
that RC would set the rate ceiling. Actively driven edges have no such limit,
which is what keeps §2's headroom available.

**Apollo receives in hardware** (SERCOM1 PAD3, interrupt on RXC) and
**transmits in software**. Hardware transmit is impossible on this pin: `TXPO`
selects only PAD0 or PAD2 (datasheet Table 25-9), and PA09 is PAD3 on both
SERCOM1 and SERCOM0.

Transmit is bit-banged but hardware-timed: one bit per TC1 overflow interrupt,
never a delay loop, so interrupts are never disabled and USB service is never
delayed.

- **TC1 runs at NVIC priority 3, below USB at 0.** USB must be able to preempt
  the bit clock; the reverse would make bit-banging delay USB service, which is
  the problem an interrupt-driven transmit exists to avoid. §2 is the cost.
- **MFRQ raises OVF, not MC0.** CC0 is TOP and the counter wraps at it
  (datasheet 30.6.2.5), so the period event is an overflow — there is no compare
  match to catch.

## 2. Baud selection: faster is better

**A slower rate makes this link worse.** Transmit is bit-banged from an ISR
below USB, so a USB interrupt can stretch the bit being driven. What matters is
how long a *frame* is exposed to that — ten bit periods:

| Baud | Bit period | Byte occupies CPU | Measured |
|---|---|---|---|
| 115200 | 8.68 µs | 86.8 µs | 97/100 |
| **230400** | **4.34 µs** | **43.4 µs** | **100/100** |
| 460800 | 2.17 µs | 21.7 µs | 1/100 |

Halving the byte time halves the window in which a USB ISR can land inside a
frame. USB ISRs here are far shorter than half a bit at either rate, so when one
does land the receiver still samples mid-bit: the exposure *count* drops without
the per-event margin dropping enough to matter.

460800 fails differently — CRC corruption, not timeouts. At 104 CPU cycles per
bit nothing remains once Cortex-M0+ ISR entry and exit are subtracted, so the
bit clock cannot keep time regardless of USB.

230400 is therefore a measured optimum between two failure modes, not a
conservative choice. **1 Mbaud is unreachable on this transmit path** — at 1 µs
bits there is no margin against a preempting ISR at all. The hardware receiver
is not the constraint.

Re-measure whenever the transmit path, ISR priorities or CPU clock change: the
optimum is a property of those, not of the wire.

### 2.1 Turnaround: 40 µs, absolute

The FPGA waits a fixed 40 µs after receiving a command before replying. The
receiver frames a byte at the **middle** of its stop bit, but the master needs
until its **end**, plus its own handover — Apollo returns the pin from GPIO to
the SERCOM one bit period after the stop bit, and the SERCOM must then resync.

**It must not scale with the bit period.** The handover cost is fixed in CPU
cycles and does not shrink as baud rises; scaling starved it exactly where it
was needed (100% at 115200, 92% at 230400, 0% above). 25 µs is the measured
floor on r1.4; 40 µs is twice that, so the margin does not rest on a boundary
measured on one board.

### 2.2 The FPGA bit period is fixed at build time

`divisor = clk_freq_hz // baud`, computed during elaboration. **Nothing checks
that the responder's domain actually runs at `clk_freq_hz`.** A design that
raises `sync` and leaves the argument alone doubles the effective baud, and a
UART tolerates about ±2% — the link dies rather than degrades.

Instantiate under `DomainRenamer` onto a domain pinned to the stated frequency,
with one constant feeding both the domain and the responder.

## 3. Commands

| CMD | Name | Response | Total |
|---|---|---|---|
| `0x01` | `PING` | STATUS + 2 (protocol version, build ID) + CRC8 | 4 B |
| `0x02` | `STATUS` | STATUS + CRC8 | 2 B |
| `0x2B` | `POWER` | STATUS + 16 (VBUS×4, VSENSE×4, LE16 each) + CRC8 | 18 B |
| `0x2C` | `DEVICES` | STATUS + 4 (flash JEDEC ID ×3, flags) + CRC8 | 6 B |
| `0x40`–`0x7F` | `LED` | STATUS + CRC8 | 2 B |

**Response length is fixed per command and known to both sides at compile
time** — no length field, the way an SPI register read works. This removes
length parsing from both ends and removes the FPGA's need for any timeout (§7).

`POWER` returns full 16-bit values rather than high bytes only: losing
measurement precision to save 700 µs on a 10 Hz poll is a poor trade.

`DEVICES` reports flash identity plus a flags byte carrying *presence* —
HyperRAM has no JEDEC ID, so the only useful thing to report is whether it
answered. Its OK bit reflects whether the flash ID was actually read, so
power-on zeros cannot be mistaken for a device that answered with zeros.

`LED` carries its pattern in the low six bits of the opcode, so the command fits
in one byte and the protocol stays stateless. `0x40` all off, `0x7F` all on.

Unknown commands return `STATUS` alone with bit 0 clear, so the master can tell
"not understood" from "not there".

## 4. Status byte

On every response, so polling for data is also polling for health. There is no
separate heartbeat transaction.

| Bit | Meaning |
|---|---|
| 0 | Command OK. Clear means the payload is not valid. |
| 1 | Events pending, including a latched button press |
| 2 | Error flag set since last read |
| 3 | FPGA reconfigured since last poll |
| 4-5 | FPGA state: 0 idle, 1 active, 2 fault, 3 reserved |
| 6 | Heartbeat toggle — flips on every response |
| 7 | Reserved, transmit zero |

Bit 6 is a toggle rather than a counter because a value that *changes* proves
the FPGA is executing, where a repeated value could be a wedged state machine
replaying a stale buffer.

Bit 1 is read-and-clear, cleared only once a response carrying it has been
**fully transmitted** — clearing on receipt would lose a press if the reply were
lost. A press arriving mid-response re-sets the latch rather than being
swallowed.

## 5. CRC-8

CRC-8/ATM: polynomial `0x07`, init `0x00`, no reflection, no final XOR. Over the
status byte and payload, excluding the CRC byte.

Sized to the frames: catches all single- and double-bit errors and any burst up
to 8 bits. CRC-16 targets frames far longer than 18 bytes and does not earn its
second byte.

A CRC is warranted because the interesting failure is silent: a corrupted VBUS
byte is a plausible wrong voltage nothing downstream can detect, unlike a
corrupted heartbeat which merely fails a pattern match. `PING` and `STATUS`
carry it too — a receiver that validates everything is simpler than one that
validates some. Apollo verifies it rather than deferring to the host, and
returns the bytes either way so a caller can inspect what arrived.

## 6. Collision avoidance

No arbitration. **The FPGA never transmits unasked**, so only one side drives at
any moment by construction rather than by protocol.

Both ends must still handle hearing themselves on the shared wire:

- **The FPGA forces its receiver input idle-high while transmitting.** The pad
  reads back what it drives, so otherwise the responder frames its own reply as
  incoming commands. Forcing idle-high rather than gating the strobe leaves the
  receiver on a clean line, never mid-frame when transmission ends.
- **Apollo transmits first, then arms the collector.** Its receiver stays
  enabled during bit-bang, so it echoes the command byte back. Arming afterwards
  is safe: the FPGA cannot reply until it has the whole command byte.

## 7. Timeouts and resynchronisation

**The FPGA has none, by construction.** Timeouts exist to abandon partial state;
the FPGA holds none. It receives one byte, transmits a complete response, and
returns to idle. This is why lengths are fixed rather than prefixed — a length
field would make the FPGA stateful across bytes and immediately require a
timeout to recover from a truncated command.

**Apollo has exactly one**, and it cannot be designed away: the other end may
genuinely not be there — unconfigured, wedged, or running gateware without this
protocol — which no framing can fix.

The deadline is derived from `ADV_UART_BAUD`, not hard-coded, so it stays correct
when the rate changes ([#85](https://github.com/awtoau/cynthion-workspace/issues/85)):
ten character times per expected byte plus a fixed turnaround allowance,
folded to a compile-time constant and rounded up. Runtime division would pull in
`__udivsi3` — 266 bytes of soft-division helper on this Cortex-M0+.

Apollo also **refuses to issue commands before DONE is high**: until then
FPGA_ADV floats on the pull-up alone. Refusing distinguishes *not configured*
from *did not reply*, which a timeout cannot. DONE latches, so it answers "has
the FPGA configured since power-up", not "is it running now".

On expiry Apollo discards the partial response, flushes the receiver, and may
retry; repeated failures fall back to the FPGA reset/reconfiguration path.

**There is nothing to resynchronise within.** The longest frame is 18 bytes and
each byte is independently framed by its own start and stop bits, so recovery is
per-transaction: CRC for corruption, timeout for absence, and the next command
starts from a known state because the FPGA is stateless between commands.

Byte-level resync is the UART's own start-bit hunt. On FERR, BUFOVF or PERR
Apollo drops the byte, clears the flags, and resynchronises rather than feeding
garbage upstream. The one streaming matcher (§9) restarts its match *on* the
current byte rather than discarding it, so pattern bytes offset by one still
re-lock.

## 8. Observability

Apollo keeps three counters, so corruption is visible rather than silent:

| Counter | Meaning |
|---|---|
| `ok` | Response arrived with valid CRC |
| `crc_fail` | Arrived complete, CRC mismatch |
| `timeout` | Did not arrive |

They **saturate at 255 rather than wrapping** — a count stuck at 255 still says
"this link is bad", where a wrapped counter can read as healthy. Reading clears
them, giving the count since the last look.

The responder drives the six board LEDs from its own state, so a failing link is
diagnosable by looking at the board:

| LED | Meaning |
|---|---|
| 0 | Command byte arrived recently (stretched to ~1/8 s to be visible) |
| 1 | Transmitting a response |
| 2 | Last command understood |
| 3 | Last command was POWER |
| 4 | Heartbeat — toggles per response, so it blinks under polling |
| 5 | Last command rejected as unknown (latched until the next good one) |

These separate the three cases a silent link otherwise confuses: the FPGA never
saw the command, saw it and rejected it, or answered and the master lost the
reply.

Raw receive taps (`rx_strobe`, `rx_byte`) let a harness distinguish "nothing
arrived" from "something arrived that was not a valid command" — the responder's
own state reflects only bytes it accepted. The test bitstream adds a JTAG view
of the same state: an independent path is what makes a broken link diagnosable
rather than merely broken.

`fpga_adv_set_toggle()` drives FPGA_ADV as a raw 10 Hz square wave from the main
loop, bypassing TC1 and the shift register, to separate transmit-path faults
from pin configuration or wiring faults.

## 9. Relationship to EIC mode

EIC edge-counting is the power-on default, so firmware with the sideband behaves
identically to firmware without it until a host selects UART mode.

| Mode | Mechanism | Port request |
|---|---|---|
| `EIC` | Rising-edge count on EXTINT7 | More than 2 edges in a 200 ms window |
| `UART` | Framed 4-byte pattern `C1 14 01 A5` | Complete frame within 300 ms |

The UART timeout is longer than the EIC window because a frame can be lost to a
single bit error, and one dropped frame should not surrender the port.

Switching modes discards what the outgoing mode observed, or a switch could
report a port request derived from the other mechanism — edges counted from UART
traffic are not an advertisement. Leaving UART mode stops the receiver *before*
the pin is re-muxed, so a partial frame cannot raise RXC against a pin no longer
wired to it.

While a command is in flight, received bytes are its response and are never
offered to the pattern matcher, so a reply containing pattern bytes cannot be
mistaken for an advertisement.

## 10. Runtime JTAG channel

The sideband neither replaces JTAG nor reclaims its pins.

| JTAG role | ATSAMD11 | ECP5 |
|---|---|---|
| TDI | PA14, SERCOM0 PAD0 | TDI |
| TCK | PA15, SERCOM0 PAD1 | TCK |
| TDO | PA10, SERCOM0 PAD2 | TDO |
| TMS | PA11, GPIO | TMS |

The bitstream instantiates the ECP5 JTAGG primitive with a user instruction such
as ER1. Apollo moves the TAP into Shift-DR, uses hardware SPI for byte-aligned
shift, then exits via TMS.

Only one operation can own the TAP at a time, so all TAP users — runtime ER
transfers, programming, debug scans — acquire one mutex. Before and after each
owner, hold TMS high for at least five TCK edges to return the TAP to a known
state.

The sideband stays usable while the TAP is occupied, which is its point. It is
not a substitute for DONE: silence means the runtime design is not
communicating, whereas DONE says whether configuration completed. PROGRAMN,
INITN and DONE keep their configuration-management roles.

Planned ER split: **ER1** a fixed 32- or 64-bit control/status register
(capability and build ID, sticky faults, counters); **ER2** a byte-stream FIFO
window with available/free counts in ER1.

## 11. Deliberate omissions

**COBS.** Its `0x00` delimiter buys resync within a continuous stream. This is
short request/response with fixed lengths — there is no stream to resync into.

**Sequence numbers.** For detecting loss in a stream. Here a lost response is
caught by the timeout, a corrupted one by the CRC.

**Variable-length payloads.** If ever needed, encode the length in the command
byte's high bits so the FPGA still knows the frame size from byte one,
preserving the stateless property in §7.

**Unsolicited FPGA transmission.** Excluded so the link is collision-free
without arbitration.

**Leaving either driver permanently enabled.** Two push-pull drivers enabled at
once short against each other. Each end enables its driver only while
transmitting (§1).

**Open-drain, and an external pull-up to go with it.** Neither is needed
point-to-point. Open-drain would make every rising edge an RC against the weak
internal pulls, setting a rate ceiling that actively driven edges do not have.
[adv_speed](../../ecp5-test/adv_speed/adv_speed_gateware.py) builds both modes
to measure where that crossover falls, and so whether a board revision should
carry a resistor.

## 12. Test methodology

**Phase 0 — board and tools.** Board revision and FPGA package, continuity T6 to
PA09, IO bank at 3.3 V, PA09 SERCOM mux against the exact datasheet.

**Phase 1 — unit simulation.** All byte values `0x00`–`0xFF`, plus directed
cases and random idle gaps. Pass: zero directed mismatches and ≥100,000 random
bytes. Transmitter assertions: high after reset and when idle; exactly 10 bit
periods per byte; LSB first; `ready` cannot assert before the stop bit
completes; data captured only on `valid && ready`.

**Phase 2 — physical signal.** Idle near 3.3 V; bit time within 1% of nominal;
analyzer decodes the expected response.

**Phase 3 — receiver.** Expose `rx_bytes`, `good_responses`, `framing_errors`,
`overruns`, `last_rx_ms` over CONTROL USB.

**Phase 4 — framing.** Inject corruption, deletion, duplication, truncation.
Pass: corrupted frames rejected unless by chance CRC-valid; the next command
recovers; invalid traffic never refreshes liveness.

**Phase 5 — load and soak.** Sustained polling while stressing CONTROL USB, AUX
USB and JTAG. 24 hours initially, 72 for sign-off. USB must be genuinely busy —
this is where §2's interrupt interaction appears.

**Phase 6 — fault injection.** Clock stoppage, reset, reconfiguration, forced
line states, interrupted JTAG transaction, random corruption.

**Phase 7 — rate characterisation.** Re-run §2's measurement after any change to
the transmit path, ISR priorities or CPU clock.

## 13. Acceptance criteria

- Bitstream builds and claims only T6 for the sideband.
- Analyzer decodes the expected exchange at 230400 baud.
- Apollo receives responses through SERCOM without polling.
- Apollo recovers after either side resets.
- CONTROL USB reports OK, CRC-failure and timeout counts.
