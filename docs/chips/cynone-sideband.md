# CynOne: the one-wire sideband between Apollo and the ECP5

**One wire between Apollo and the ECP5, carrying two jobs that interfere.** This is
the reference for all of it: the electrical rules, the protocol, the port-ownership
signal that shares the wire, and what has been settled and must not be re-proposed.

Half-duplex request/response, Apollo always the master, the FPGA speaking only when
asked — except for the one deliberate exception in §8.

```
Apollo → FPGA:  [CMD]
FPGA  → Apollo: [STATUS][payload ...][CRC8]
```

It works when USB has not enumerated, when the console is silent, and when JTAG is
occupied. That is the whole reason it exists, and it is the property every design
question below is measured against.

| | |
|---|---|
| Responder, shipping SoC | [`ecp5-test/sideband_link.py`](../../ecp5-test/sideband_link.py) — protocol v2 |
| Responder, test bitstream | `apollo_fpga.gateware.sideband` via [`ecp5-test/sideband/sideband_gateware.py`](../../ecp5-test/sideband/sideband_gateware.py) — protocol v1 |
| Pad sharing, both blocks | [`ecp5-test/sideband_debug.py`](../../ecp5-test/sideband_debug.py) |
| Port request | [`ecp5-test/sideband_advertise.py`](../../ecp5-test/sideband_advertise.py) |
| CPU's end | [`ecp5-test/riscv/sideband_csr.py`](../../ecp5-test/riscv/sideband_csr.py) |
| Master | `repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c` |
| Host decode | [`scripts/sideband_decoder.py`](../../scripts/sideband_decoder.py), [`ecp5-test/sideband/test_protocol.py`](../../ecp5-test/sideband/test_protocol.py) |
| Simulation | [`scripts/sideband_link_sim.py`](../../scripts/sideband_link_sim.py) — the responder at the pad; [`scripts/sideband_advertise_sim.py`](../../scripts/sideband_advertise_sim.py) — the advertisement, frame-exact |

Open work is tracked from [#184](https://github.com/awtoau/cynthion-workspace/issues/184),
which is the master and lists the children. Nothing in this file is a plan.

## 1. The wire

| | |
|---|---|
| Pin | PA09 (SAMD11, SERCOM1 PAD3, EIC EXTINT7) ↔ T6 (ECP5) |
| Rate | 230400 baud, 8-N-1, LSB first |
| Idle | high, on internal pull-ups only |
| Logic | 3.3 V LVCMOS, **open drain at both ends** |
| Flow control | none |

Point-to-point between two pins — not a bus — and **there is no external pull-up
resistor on the net.** The line idles high on two internal pulls: the ECP5's
`PULLMODE="UP"` and the SAMD11's `GPIO_PULL_UP`.

**`PULLMODE="UP"` is load-bearing, not cosmetic.** The ECP5 defaults an
unconfigured IO to pull-*down* while Apollo pulls PA09 up. Opposing pulls settle at
a mid-rail divider voltage when neither end drives, which a UART reads as a
permanent break — not a slow link, a dead one. Both ends must agree on idle-high.
Declared at `ecp5-test/cynthion_platform/cynthion_r1_4.py:120-126`.

The ECP5 specifies the pull as a current, tens of µA, so its effective impedance is
V/I — tens of kΩ, far too soft to time an edge with. The output driver is separately
selectable via `DRIVE=`; T6 does not set it and takes the 8 mA default. The pull
only has to hold idle-high when neither end drives, where speed is irrelevant.

### Open drain, and why it is not push-pull

Both ends have been open drain since
[#88](https://github.com/awtoau/cynthion-workspace/issues/88).

* **FPGA.** `pad_o` is hardwired to 0 and `pad_oe` tracks the *bit value*, not
  merely `tx_active` (`sideband_link.py:293-296`). The driver is off for every high
  bit and for the whole lead-in before the start bit. `SidebandAdvertiser` does the
  same, which is what lets the two blocks share the pad by simply OR-ing `o` and
  `oe`: two pull-downs on one wire is a pull-down, so sharing is free rather than an
  arbitration problem (`sideband_debug.py:153-160`).
* **Apollo.** The SAMD11 has no hardware open-drain mode, so it is emulated: `OUT`
  for PA09 is parked low once, and each bit toggles only `DIR`. Low drives low, high
  releases and lets the pull-ups raise the line (`fpga_adv.c:106-139`). `DIRSET`/
  `DIRCLR` on `PORT_IOBUS` rather than `gpio_set_pin_direction()`, for two reasons:
  the HAL call is three register writes and this runs once per bit; and the HAL's
  `GPIO_DIRECTION_IN` path writes `WRCONFIG` without `PULLEN`, so it *clears the
  pull-up* — a release through it would float the line rather than let it rise,
  which for an open-drain transmitter is the difference between a stop bit and a
  break.

Push-pull at both ends puts a driven high against a driven low on any timing slip —
a low-impedance path through two output stages, tens to hundreds of milliamps, and a
hardware-damage risk rather than a link-reliability one. It is not hypothetical:
`tx_active` leads the first bit onto the wire by 0.75 bit times, and simulating the
retry-overlap case measured up to 30 µs of exactly that per collision with both ends
push-pull, and zero with both open drain
(`debris/scripts/sideband_contention_probe.py`).

**The RC objection was tested and refuted.** Open drain replaces a driven rising
edge with an RC against two weak internal pulls, estimated 0.3–1.5 µs, which is
7–35% of a bit at 230400 — arithmetic could not settle whether that closes. A soak
did: see §2. The rise time itself has still not been observed on a scope; the soak
shows only that it is not the binding constraint at 115200 or 230400.

The idle pulls also cover configuration, when T6 is high-impedance — Apollo enables
its pull-up *before* enabling the receiver, since a floating input frames noise as
start bits.

Whichever end drives follows from the protocol rather than from arbitration: the
FPGA only ever replies (§7), and the one exception is bounded (§8).

### What this wire is for, given the other one exists

The SoC also has a full `Uart16550` console to Apollo on **R14/T14**, full duplex,
hardware UART at both ends, with LSR.OE and LSR.FE actually wired, which Apollo
tunnels to the host CDC. It is a strictly better console. **But R14/T14 are the
ECP5's TDI/TMS**, so that link is unavailable during exactly the situation the
sideband is for. The sideband's advantage over it is not bandwidth and not
robustness; it is surviving a JTAG session — see
[`chips/samd11-apollo.md`](samd11-apollo.md). Any proposal that duplicates the
R14/T14 console on this wire is buying only that.

The sideband likewise neither replaces JTAG nor reclaims its pins. Only one operation
can own the TAP at a time, so all TAP users acquire one mutex; the sideband stays
usable while they hold it, which is its point. It is not a substitute for DONE —
silence means the runtime design is not communicating, whereas DONE says whether
configuration completed.

## 2. Rate: 230400, and faster is better

**Apollo receives in hardware and transmits in software.** Hardware transmit is
impossible on this pin: `TXPO` selects only PAD0 or PAD2 (SAMD11 datasheet
Table 25-9) and PA09 is PAD3 on both SERCOM0 and SERCOM1, so the SERCOM cannot reach
it (`fpga_adv.c:370-376`). Transmit is bit-banged but hardware-timed — one bit per
TC1 overflow interrupt, never a delay loop, so interrupts are never disabled and USB
service is never delayed.

* **TC1 runs at NVIC priority 3, below USB at 0.** USB must be able to preempt the
  bit clock; the reverse would make bit-banging delay USB service, which is the
  problem an interrupt-driven transmit exists to avoid.
* **MFRQ raises OVF, not MC0.** CC0 is TOP and the counter wraps at it (datasheet
  30.6.2.5), so the period event is an overflow — there is no compare match to catch.
* TC1 rather than SysTick because SysTick already belongs to `board_millis()`.

That transmit path is the whole reason a *slower* rate is worse. A byte occupies the
CPU for ten bit periods, and what matters is how long a frame is exposed to a USB ISR
landing inside it:

| baud | bit period | byte occupies CPU | bring-up, 100 exchanges | soak, 5000 exchanges |
|---|---|---|---|---|
| 115200 | 8.68 µs | 86.8 µs | 97/100 | 4904/5000 — **fail** |
| **230400** | **4.34 µs** | **43.4 µs** | **100/100** | **5000/5000 — pass** |
| 460800 | 2.17 µs | 21.7 µs | 1/100 | — |

Bring-up figures are `fpga_adv.c:67-81`; the soak is `debris/scripts/sideband_soak.py`,
run open drain at both ends after the drive-style change, scoring each direction
separately. It targets the **test** bitstream, because it soaks with `POWER` — 18
bytes, the longest reply in either map, which the shipping link does not implement.
The physical layer it measures is common to both. Halving the byte time halves the
window in which a USB ISR can stretch a
bit past the receiver's sample point. The ISRs here are far shorter than half a bit
at either rate, so when one does land the receiver still samples mid-bit: the
exposure *count* drops without the per-event margin dropping enough to matter.

**The 115200 failure is timeouts, not corruption** — 96 short replies against 98
counted timeouts and **zero CRC errors**. Bytes never arrived rather than arriving
damaged, which is the signature of a stretched bit, not of a marginal rise time. An
RC limit cannot produce a failure that gets *worse* as the bit period lengthens.
Divisor error is at most 0.16% everywhere in the matrix, well inside a UART's ~2%
budget, so clock resolution is not implicated either.

Push-pull is in the soak matrix as a **control**, not for completeness: it has no RC
limit, so a failure there is clocking or sampling. Without it, "open drain fails at
115200" would have read as an RC result, which is precisely the wrong conclusion and
the one the pre-soak arithmetic pointed at.

**460800 fails differently** — CRC corruption rather than timeouts. At 104 CPU cycles
per bit nothing remains once Cortex-M0+ ISR entry and exit are subtracted, so the bit
clock cannot keep time regardless of USB. **1 Mbaud is unreachable on this transmit
path**: at 1 µs bits there is no margin against a preempting ISR at all. The hardware
receiver is not the constraint. 23,040 B/s is the ceiling on this board.

230400 is therefore a measured optimum between two failure modes, not a conservative
choice. It is a property of the transmit path, the ISR priorities and the CPU clock —
re-measure when any of those changes, not when the wire changes.

### The bit period is fixed at build time on the FPGA side

`divisor = clk_freq_hz // baud`, computed during elaboration, and **nothing checks
that the responder's domain actually runs at `clk_freq_hz`.** A design that raises
`sync` and leaves the argument alone doubles the effective baud; at ~2% tolerance the
link dies rather than degrades. Instantiate under `DomainRenamer` onto a domain
pinned to the stated frequency, with one constant feeding both. `SidebandDebug` hands
the same frequency to the link and the advertiser so they cannot disagree — a frame at
the wrong rate is not an advertisement.

This has bitten twice. `SidebandDebug`'s default was 115200 while the firmware ran at
230400, a live 2× mismatch with no error anywhere because neither end ever frames a
byte; and `SidebandDebug` derived its baud from an argument that could differ from the
domain's real frequency (`decisions.md:206`,
[`soc-status-leds.md`](../hardware.md#what-the-six-fpga-leds-mean)).

## 3. Turnaround: 40 µs, absolute

The FPGA waits a fixed 40 µs after receiving a command before replying
(`sideband_link.py:214`). The receiver frames a byte at the **middle** of its stop
bit, but the master needs until its **end**, plus its own handover: Apollo returns
the pin from GPIO to the SERCOM one bit period after the stop bit, and the SERCOM
must then resync. Replying immediately loses the first bits of the response —
observed as the FPGA receiving every command correctly while the master saw nothing.

**It must not scale with the bit period.** The handover cost is fixed in CPU cycles
and does not shrink as baud rises; scaling it starved the delay exactly where it was
needed — 100% at 115200, 92% at 230400, 0% above. 25 µs is the measured floor on
r1.4; 40 µs is twice that, so the margin does not rest on a boundary measured on one
board.

It is a per-*exchange* cost, not per byte, and it amortises away — see §6.

## 4. Commands

**Two opcode maps, one envelope.** The shipping SoC and the test bitstream answer
different commands, share the framing, and are told apart by `PING`'s version byte.
Why the maps diverged, and why the removed commands were removed rather than stubbed,
is [`../architecture.md`](../architecture.md#peripherals).

### Shipping — `ecp5-test/sideband_link.py`, protocol v2

| CMD | Name | Response | Total |
|---|---|---|---|
| `0x01` | `PING` | STATUS + protocol version + the CPU's byte + CRC8 | 4 B |
| `0x02` | `STATUS` | STATUS + CRC8 | 2 B |
| `0x80`–`0xFF` | `WRITE` | STATUS + CRC8; the low 7 bits reach the CPU | 2 B |

A heartbeat and a byte each way. Seven bits inbound rather than eight because the
opcode and the value share one byte — the top bit is the selector, which keeps every
other opcode free rather than carving the map into ranges twice.

The CPU's end is `SidebandControl`, four CSR bytes: `ctrl` (state, events, error,
reconfigured, `advertise` bit 5, `own` bit 7), `tx` (the byte `PING` returns), `rx`
(the last `WRITE`'s low seven bits), `rxcnt`. `rxcnt` is a wrapping count rather than
a ready flag because reading it must not be side-effecting, and because a count
distinguishes "Apollo sent the same byte again" from "Apollo has sent nothing".
`own` resets to 0 and the fabric's hardwired bits win until firmware sets it, so a
design that never reaches its firmware still answers with the fabric's own account of
itself.

**`POWER`, `DEVICES` and `LED` are not implemented here** and are answered as unknown
commands, not with a well-formed frame of zeros.

### Test bitstream — `apollo_fpga.gateware.sideband`, protocol v1

| CMD | Name | Response | Total |
|---|---|---|---|
| `0x01` | `PING` | STATUS + 2 (protocol version, build ID) + CRC8 | 4 B |
| `0x02` | `STATUS` | STATUS + CRC8 | 2 B |
| `0x03` | `LED_RELEASE` | STATUS + CRC8 | 2 B |
| `0x2B` | `POWER` | STATUS + 16 (VBUS×4, VSENSE×4, LE16 each) + CRC8 | 18 B |
| `0x2C` | `DEVICES` | STATUS + 4 (flash JEDEC ID ×3, flags) + CRC8 | 6 B |
| `0x40`–`0x7F` | `LED` | STATUS + CRC8 | 2 B |

`POWER` returns full 16-bit values rather than high bytes only: losing measurement
precision to save 700 µs on a 10 Hz poll is a poor trade. `DEVICES` reports flash
identity plus a flags byte carrying *presence* — HyperRAM has no JEDEC ID, so the
only useful thing to report is whether it answered; its OK bit reflects whether the
flash ID was actually read, so power-on zeros cannot be mistaken for a device that
answered with zeros. `LED` carries its pattern in the low six bits of the opcode, so
the command fits in one byte and the protocol stays stateless. `0x40` all off, `0x7F`
all on.

### Framing, and the stateless property

**Response length is fixed per command and known to both sides at compile time.**
`PAYLOAD_SIZE` is keyed by opcode with no length field (`sideband_link.py:84-87`).
The responder sets `payload_len`, `byte_index` and `turnaround` from the command byte
in `IDLE` and returns to `IDLE`, so **there is no half-parsed condition to abandon
and therefore no timeout on the FPGA side** (§7).

That property survives multi-byte replies. It does *not* require a length field: an
opcode family with fixed lengths (`0x10`–`0x1F` returning 1..16 bytes, say) still
tells the frame size from byte one, and a valid count can travel in payload byte 0
with the rest padded. What genuinely breaks the property is a multi-byte *command*,
and only that.

Unknown commands return `STATUS` alone with bit 0 clear, so the master can tell "not
understood" from "not there".

**Apollo knows neither map.** The host supplies the command byte and the expected
length through vendor request `0xC3` (§10) and `fpga_adv_command()` shifts whatever it
is given (`fpga_adv.c:418`). The only opcode-derived constant in the firmware is
`ADV_RESPONSE_MAX 18` (`:143`), sized for `POWER`; the shipping link's longest reply
is 4 bytes, so it is larger than needed and still correct. **No firmware change is
required by either map.**

## 5. The status byte, CRC, and observability

The status byte is on **every** response, so polling for data is also polling for
health. There is no separate heartbeat transaction, and the byte is identical in both
maps — which is what makes liveness one question rather than two.

| bit | meaning |
|---|---|
| 0 | command OK; clear means the payload is not valid |
| 1 | events pending |
| 2 | error flag set since last read |
| 3 | FPGA reconfigured since last poll |
| 4–5 | FPGA state: 0 idle, 1 active, 2 fault, 3 reserved |
| 6 | heartbeat toggle — flips on every response |
| 7 | reserved, transmits zero |

Bit 6 is a toggle rather than a counter because a value that *changes* proves the
responder is executing, where a repeated value could be a wedged state machine
replaying a stale buffer. It flips in `FINISH`, after the last stop bit has gone out
(`sideband_link.py:435-439`). **This is the "cheap I am alive" signal, and it already
exists** — combined with `PING` returning the CPU's own `tx` byte, liveness needs a
poll, not a new mechanism.

*Test bitstream only:* bit 1 also carries a latched USER button press, read-and-clear,
cleared only once a response carrying it has been **fully transmitted** — clearing on
receipt would lose a press if the reply were lost. The shipping link has no button
input; bits 1–5 there are whatever the fabric or the CPU reports.

**CRC-8/ATM**: polynomial `0x07`, init `0x00`, no reflection, no final XOR, over the
status byte and payload and excluding the CRC byte. Sized to the frames — it catches
all single- and double-bit errors and any burst up to 8 bits; CRC-16 targets frames
far longer than 18 bytes and does not earn its second byte. A CRC is warranted
because the interesting failure is silent: a corrupted VBUS byte is a plausible wrong
voltage nothing downstream can detect, unlike a corrupted heartbeat which merely fails
a pattern match. `PING` and `STATUS` carry it too — a receiver that validates
everything is simpler than one that validates some. Apollo verifies it rather than
deferring to the host, and returns the bytes either way so a caller can inspect what
arrived.

**Apollo keeps three counters** so corruption is visible rather than silent: `ok`
(valid CRC), `crc_fail` (arrived complete, mismatched), `timeout` (did not arrive).
They **saturate at 255 rather than wrapping** — a count stuck at 255 still says "this
link is bad", where a wrapped counter can read as healthy. Reading clears them.

The FPGA exposes raw receive taps (`rx_strobe`, `rx_byte`) so a harness can tell
"nothing arrived" from "something arrived that was not a valid command" — the
responder's own state reflects only bytes it accepted. `fpga_adv_set_toggle()` drives
FPGA_ADV as a raw 10 Hz square wave from the main loop, bypassing TC1 and the shift
register, to separate transmit-path faults from pin or wiring faults.

*Test bitstream only:* its responder drives the six board LEDs from its own state, so
a failing link is diagnosable by looking at the board — command arrived / transmitting
/ understood / was `POWER` / heartbeat / rejected. These separate the three cases a
silent link otherwise confuses: the FPGA never saw the command, saw it and rejected
it, or answered and the master lost the reply. The shipping SoC's LEDs report the CPU
and the buses instead; it has a console, so the link does not have to be its own
display.

## 6. Exchange arithmetic

An N-byte payload costs `(1 + N + 2) × 43.403 µs + 40 µs`: one command byte out, the
turnaround, then status + payload + CRC back. Derived from the measured constants in
§2 and §3:

| N | exchange | payload rate | of the 23,040 B/s wire ceiling | turnaround's share |
|---|---|---|---|---|
| 0 (`STATUS`) | 170.2 µs | — | — | 23.5% |
| 1 | 213.6 µs | 4,681 B/s | 20.3% | 18.7% |
| 8 | 517.4 µs | 15,461 B/s | 67.1% | 7.7% |
| 32 | 1,559.1 µs | 20,525 B/s | 89.1% | 2.6% |
| 64 | 2,948.0 µs | 21,709 B/s | 94.2% | 1.4% |
| 128 | 5,725.8 µs | 22,355 B/s | 97.0% | 0.7% |

Read as a console rate, N=8 is already 154 kbaud-equivalent and N=64 is 217 k. The
Apollo↔SoC console that exists today runs at 115200 — 11,520 B/s. **Bandwidth is
sufficient from N=8 upward, and the 40 µs is never the binding term.**

Flow control splits by direction:

* **FPGA→Apollo is inherently flow-controlled.** Nothing moves unless Apollo asks, so
  Apollo can never be overrun. The cost moves to the FPGA, which would need a FIFO to
  absorb a burst between polls, and that FIFO's overflow flag would be the LSR.OE
  equivalent this path does not have.
* **Apollo→FPGA is unprotected and silently lossy.** A `0x80`–`0xFF` WRITE strobes
  `received` for one cycle; if the CPU has not read `rx` before the next WRITE the
  first byte is gone and only `rxcnt` shows it. Seven bits per 170.2 µs exchange is
  5,875 seven-bit values per second, or ~2,900 B/s if full bytes are split across two
  exchanges.

**The sideband is not behind a 16550.** The SoC's two `Uart16550`s are the USB CDC
console and the Apollo-facing R14/T14 port; `overrun` and `frame_error` are driven
only for the latter, from `SerialLine`. `SidebandControl` has no FIFO in either
direction and no overrun output at all — LSR.OE does not reach this path and cannot
without new gateware.

## 7. Collision avoidance, timeouts, recovery

**No arbitration.** The FPGA never transmits unasked (§8 is the one exception), so
only one side drives at any moment by construction rather than by protocol. Both ends
must still handle hearing themselves on the shared wire:

* **The FPGA forces its receiver input idle-high while transmitting.** The pad reads
  back what it drives, so otherwise the responder frames its own reply as incoming
  commands — observed as three received bytes for a one-byte command, the last being
  its own CRC. Forcing idle-high rather than gating the strobe leaves the receiver on
  a clean line, never mid-frame when transmission ends.
* **The FPGA gates `rdy` on `~err.frame`.** `AsyncSerialRX` strobes `rdy` for every
  completed frame including one whose stop bit was low. Without the gate a break
  condition — the line held low, exactly what happens while Apollo re-muxes the pin or
  the FPGA reconfigures — frames as several garbage bytes, each dispatched as a
  command and each answered onto a wire the master may be driving.
* **Apollo transmits first, then arms the collector.** Its receiver stays enabled
  during bit-bang, so it echoes the command byte back. Arming afterwards is safe: the
  FPGA cannot reply until it has the whole command byte.
* **The receive edge is asynchronous** and `SidebandDebug` puts an `FFSynchronizer`
  on it. Without one, a start bit sampled mid-transition is seen at different times by
  different flops and the link fails intermittently — the worst failure for a debug
  channel.

**The FPGA has no timeout, by construction.** Timeouts exist to abandon partial state
and it holds none: one byte in, a complete response out, back to idle. This is why
lengths are fixed rather than prefixed (§4).

**Apollo has exactly one**, and it cannot be designed away: the other end may
genuinely not be there — unconfigured, wedged, or running gateware without this
protocol — which no framing can fix. The deadline is derived from `ADV_UART_BAUD`
rather than hard-coded, so it stays correct when the rate changes: ten character
times per expected byte plus a fixed turnaround allowance, folded to a compile-time
constant and rounded up. A runtime division would pull in `__udivsi3`, 266 bytes of
soft division on this Cortex-M0+, for one divide.

Apollo also **refuses to issue commands before DONE is high**: until then FPGA_ADV
floats on the pull-up alone. Refusing distinguishes *not configured* from *did not
reply*, which a timeout cannot. DONE latches, so it answers "has the FPGA configured
since power-up", not "is it running now".

**There is nothing to resynchronise within.** The longest frame is 18 bytes and each
byte is independently framed, so recovery is per-transaction: CRC for corruption,
timeout for absence, and the next command starts from a known state because the FPGA
is stateless between commands. On expiry Apollo discards the partial response,
flushes the receiver, and may retry; repeated failures fall back to the FPGA
reset/reconfiguration path. On FERR, BUFOVF or PERR it drops the byte, clears the
flags and resynchronises rather than feeding garbage upstream — and the one streaming
matcher (§8) restarts its match *on* the current byte rather than discarding it, so
pattern bytes offset by one still re-lock.

## 8. The second job: the CONTROL port request

**FPGA_ADV's primary purpose upstream is port takeover, not debug.** Apollo keeps the
CONTROL USB mux switched to the FPGA only while it keeps hearing an advertisement.
Two mechanisms exist, and Apollo selects between them:

| mode | advertisement | port request | default |
|---|---|---|---|
| `EIC` | rising edges on EXTINT7 | >2 edges in a 200 ms window | **power-on** |
| `UART` | the frame `C1 14 01 A5`, 8-N-1 | a complete frame within `HEARTBEAT_TIMEOUT_MS` 300 | selected by the host |

The UART timeout is longer than the EIC window because a frame can be lost to a
single bit error and one dropped frame should not surrender the port.

**EIC mode and the sideband cannot share the wire.** EIC counts edges and sideband
traffic is edges, so a poll reads as a port request. Neither can a square wave and a
UART share it: upstream `ApolloAdvertiser` drives FPGA_ADV with a 20 ms period —
`half_period = clk_freq_hz × 10e-3` and a toggle each time, so a **50 Hz square wave**
— which is low for half of every 20 ms, and no byte survives that. UART mode is the
only mode in which one wire does both jobs, and the reasoning over the alternatives is
[`../architecture.md`](../architecture.md#peripherals).

Apollo has implemented UART mode since the sideband landed. **No upstream gateware
ever emitted the frame**; `ecp5-test/sideband_advertise.py` is the missing half.

| | |
|---|---|
| Frame | `C1 14 01 A5`, 8-N-1, LSB first — 40 bit periods, 174 µs |
| Interval | 100 ms, so three frames fit inside the 300 ms timeout |
| Duty | 0.17% |
| Enable | off at reset; `SidebandControl` ctrl bit 5, `sideband::ADVERTISE` |
| Guard | 20 bit periods (86.8 µs) of continuous idle-high before a frame may start |
| Hold | no frame starts or is truncated while `link.tx_active` |

The pattern and the 300 ms timeout are duplicated between C and Python because no
build step sees both; a mismatch is silent, and Apollo simply never grants the port.
Three frames per timeout means two consecutive losses still hold the port; one per
timeout would surrender it on the first.

**This deliberately breaks "the FPGA never transmits unasked", and that is the whole
cost of the decision.** What replaces it:

* **The responder wins the wire.** The advertiser holds off while `tx_active`, so a
  reply is never corrupted by the FPGA's own advertisement. Deferring costs the
  advertisement one interval at most; the reverse would corrupt a reply Apollo is
  already waiting for.
* **The idle guard exceeds the longest gap inside a transaction.** The 40 µs
  turnaround is 9.2 bit periods; requiring 20 proves no transaction is in flight
  rather than merely that the wire is quiet this instant.
* **Both ends open drain**, so an overlap is two pull-downs rather than a short (§1).
* **Both directions recover.** An overlapped command or reply fails the CRC and Apollo
  retries; an overlapped advertisement is one of three in the window.
* **A reply is never offered to the matcher.** While a command is in flight every
  received byte belongs to its response, so a reply containing pattern bytes cannot be
  read as an advertisement.

**Off at reset, which is the opposite of upstream.** `ApolloAdvertiser` advertises
from configuration and `ApolloAdvertiserRequestHandler` supplies a `stop` request to
end it. Here the FPGA asks rather than assumes: a bitstream that seized CONTROL on
configuration would take the port from Apollo's own debug interface, which is the path
used to recover a board that will not boot. Clearing bit 5 hands the port back one
timeout later. `advertise` sits outside `own` because it is not something the link
*reports* — it is something the FPGA *does* — so there is no `fabric_advertise`.

**The provenance of everything in this section is simulation.**
`scripts/sideband_advertise_sim.py` checks the frame bit-exactly against Apollo's
matcher; nothing here has been observed on a wire. The one time Apollo was put into
UART mode from a host, the commands timed out for unrelated reasons —
[#209](https://github.com/awtoau/cynthion-workspace/issues/209).

The port-ownership state machine on the Apollo side — who gets CONTROL and when, and
the `0xc2` policy flag — is in [`hardware.md`](../hardware.md#who-gets-the-port-and-when).

## 9. How the two jobs interfere

The advertisement may only start after **20 bit periods of continuous idle-high**,
where `line_busy = ~rx | hold`. Apollo's `fpga_adv_command()` busy-waits for the whole
exchange, so back-to-back polling leaves inter-exchange gaps far shorter than 86.8 µs:
the guard resets on every command's start bit and never fills. Three missed frames is
300 ms, which is `HEARTBEAT_TIMEOUT_MS`, so `fpga_requesting_port()` goes false and
the next `fpga_adv_task()` pass calls `take_over_usb()`.

**Driving the link hard makes the FPGA lose the CONTROL port.** There is a second path
to the same place: `SERCOM1_Handler` routes *every* byte into the response collector
while `response_want != 0`, so any advertisement frame that does begin during a poll is
consumed as response bytes and never reaches the matcher.

This is structural, not a tuning problem — the bulk data and the port-ownership signal
share one half-duplex wire, and one of them is gated on the wire being idle. It is the
constraint [#184](https://github.com/awtoau/cynthion-workspace/issues/184) exists to
state once, and the reason a poll cannot simply be added.

**The interaction is unsimulated.** `sideband_link_sim.py` covers `SidebandLink`
alone and `sideband_advertise_sim.py` covers `SidebandAdvertiser` alone; nothing
elaborates `SidebandDebug`, so the shared pad, `hold` and the idle guard are tested
against a *stimulus* that mimics a responder, not against a responder. Apollo's C has
no automated coverage at all — `repos/apollo/firmware/test/` contains one file,
`test_apollo_mode.c`. Anything that adds polling needs a combined simulation that
models Apollo's poll duty and asserts the advertisement still gets out.

## 10. The host interface

Everything host-side goes through vendor request `0xC3`
(`VENDOR_REQUEST_FPGA_ADV_MODE`), which overloads `wValue`:

| `wValue` | effect |
|---|---|
| `0xFFFF` | read the current mode back |
| `0xFFFE` | issue a sideband command — `wIndex` low byte is the command, high byte the expected reply length |
| `0xFFFD` | toggle the diagnostic square wave (`wIndex` 1 on, 0 off) |
| `0xFFFC` | read link health `[ok, crc_fail, timeout]`, saturating, cleared by the read |
| anything else | **set the mode** (0 = EIC, 1 = UART) |

**The default case is a write, and the trap is real**: a host probing with `wValue=0`
does not read anything — it selects EIC mode, the opposite of what a reader expects.
A short reply to `0xFFFE` means timeout, and Apollo returns what arrived so the host
can tell "nothing" from "partial", i.e. an absent FPGA from a broken link. An unknown
mode stalls rather than being silently ignored.

`fpga_adv_command()` refuses unless the mode is UART, and `fpga_adv_set_mode()` has
exactly one caller — this handler. **Nothing in firmware ever selects a mode**, so
a board's advertisement mechanism is decided entirely by whether a host issued `0xC3`.

Switching modes discards what the outgoing mode observed, or a switch could report a
port request derived from the other mechanism — edges counted from UART traffic are
not an advertisement. Leaving UART mode stops the receiver *before* the pin is
re-muxed, so a partial frame cannot raise RXC against a pin no longer wired to it.

## 11. Settled — do not re-propose

* **Open drain at both ends, and no external pull-up.** §1. The RC objection was
  measured and refuted; the contention hazard it replaced was measured at up to 30 µs
  per collision. #88.
* **230400 baud.** §2. A slower rate is *worse*, and "drop to 115200 if open drain
  proves marginal" is backwards — 115200 fails on the jitter axis, which is the axis
  that actually fails.
* **The 40 µs turnaround is absolute.** §3. Scaling it with baud has already been
  tried and starved it.
* **Bandwidth is not the problem.** §6. The turnaround is per exchange, not per byte;
  N=8 already beats the existing 115200 console and N=64 amortises it to 1.4%.
* **Statelessness is not the problem.** §4. `PAYLOAD_SIZE` is opcode-keyed, so
  multi-byte replies need no length field. Only a multi-byte *command* breaks it.
* **A two-way attention line does not work.** Electrically safe and cheap, and it
  duplicates the advertisement with a weaker signal. Below ~2.2 µs a low is under the
  16× oversampler's threshold and is not registered at all; at one bit period it is
  indistinguishable from a start bit; only a break of a full character time (43.4 µs)
  is reliably detected, and that frames as `0x00`+FERR — which aliases with exactly
  the corruption the CRC exists to catch. `SERCOM1_Handler` already tests for that
  condition and discards it. If unsolicited FPGA→Apollo signalling beyond "I want the
  port" is wanted, add a type byte to the advertisement frame, which already has the
  collision discipline a new mechanism would have to reinvent.
* **"I am alive" already exists.** §5, status bit 6. It needs a poll, not a mechanism.
* **The advertisement is off at reset.** §8. Inverting upstream's polarity is
  deliberate and protects the recovery path.
* **Removed commands stay removed, not zero-stubbed.** [`../architecture.md`](../architecture.md#peripherals).
* **Anything Apollo-side is gated by the d11's memory budget**, which is enforced and
  has single-digit headroom under the ceiling — [`chips/samd11-apollo.md`](samd11-apollo.md#memory-budget--the-binding-constraint-on-this-board).
  The FPGA side is cheap by comparison: the whole shipping sideband is 350 logic cells
  and 178 FF, 2.9% of an LFE5U-12F ([`../architecture.md`](../architecture.md#peripherals)).
  A proposal that fits the wire and the fabric can still fail on the MCU, and usually
  does.

## 12. Deliberate omissions

**COBS.** Its `0x00` delimiter buys resync within a continuous stream. This is short
request/response with fixed lengths — there is no stream to resync into.

**Sequence numbers.** For detecting loss in a stream. Here a lost response is caught
by the timeout and a corrupted one by the CRC.

**Variable-length payloads.** If ever needed, encode the length in the command byte's
high bits so the FPGA still knows the frame size from byte one (§4).

**Unsolicited FPGA transmission.** Excluded for commands, so request/response is
collision-free without arbitration. The port-request advertisement is the one
deliberate exception and §8 states what it costs and what bounds it.

**Leaving either driver permanently enabled.** Both ends enable their driver only
while pulling low (§1).

## 13. Where earlier documents were wrong

Recorded so a reader who finds the old text elsewhere — in git history, in a
docstring, in an issue — knows which way it was settled.

| claim | where it appeared | settled |
|---|---|---|
| Push-pull while driving; open drain rejected because the RC would set the rate ceiling | `debris/docs/fpga-adv-sideband.md` §1 and §11 | **Wrong.** Open drain shipped at both ends (#88) and the soak refuted the RC argument. §1. |
| `ApolloAdvertiser` is a **25 Hz** square wave | `sideband_advertise.py` docstring, `architecture.md` 25, `upstream-boundary.md` | **50 Hz.** `repos/apollo/apollo_fpga/gateware/advertiser.py:42-48` sets `half_period` to 10 ms and toggles each time, so the period is 20 ms. The accompanying "low for half of every 20 ms" was always right, and so is `hardware.md`'s "50 Hz, toggles every 10 ms". |
| `fpga_adv_transceive()` at `fpga_adv.c:437` | `debris/docs/fpga-adv-sideband.md` §3.3, `architecture.md` 24 | The function is `fpga_adv_command()` at `:418`; `:437` is the mode guard. |
| `scripts/sideband_soak.py` and `sideband_contention_probe.py` are not in the tree | `debris/docs/sideband-review.md` §5 | They were retired to `debris/scripts/` in `25087b8`, not lost. The measurements they produced remain reproducible from there. |
| Baud is 115200 | #209, describing firmware `a7b8283` | Correct **for that firmware**. `b48d4bf` raised both ends to 230400; the two must move together. |
