# FPGA_ADV command protocol

Half-duplex request/response over the single FPGA_ADV wire. Apollo is always
the master; the FPGA only ever speaks when asked.

Companion to [apollo-fpga-sideband-design.md](apollo-fpga-sideband-design.md),
which covers the physical layer and the unidirectional telemetry case. This
document specifies the bidirectional command layer built on top.

Tracking: [#68](https://github.com/awtoau/cynthion-workspace/issues/68),
[#84](https://github.com/awtoau/cynthion-workspace/issues/84).

## 1. Shape

```
Apollo → FPGA:  [CMD]
FPGA  → Apollo: [STATUS][payload ...][CRC8]
```

**Response length is fixed per command and known to both sides at compile
time.** There is no length field: Apollo issues `0x2B` and receives exactly 18
bytes, the way an SPI register read works. This removes length parsing from
both ends and, more usefully, removes the FPGA's need for any timeout — see §6.

## 2. Commands

| CMD | Name | Response | Total |
|---|---|---|---|
| `0x01` | `PING` | STATUS + 2 (protocol version, build ID) + CRC8 | 4 B |
| `0x02` | `STATUS` | STATUS + CRC8 | 2 B |
| `0x2B` | `POWER` | STATUS + 16 (VBUS×4, VSENSE×4, LE16 each) + CRC8 | 18 B |

`POWER` returns the full 16-bit values rather than the high bytes only. Eight
bytes would fit two channels or four channel MSBs, and losing measurement
precision to save 700 µs on a 10 Hz poll is a poor trade.

Unknown commands return `STATUS` alone with bit 0 clear.

## 3. Status byte

Present on every response, so polling for data is also polling for health.
There is no separate heartbeat transaction.

| Bit | Meaning |
|---|---|
| 0 | Command OK. Clear means the rest of the payload is not valid. |
| 1 | Events pending |
| 2 | Error flag set since last read |
| 3 | FPGA reconfigured since last poll |
| 4-5 | FPGA state: 0 idle, 1 active, 2 fault, 3 reserved |
| 6 | **Heartbeat toggle** — flips on every response |
| 7 | Reserved, transmit zero |

Bit 6 replaces the edge-counting heartbeat and is stronger evidence: a value
that *changes* every reply proves the FPGA is executing, not that a wedged
state machine is repeating a stale buffer.

## 4. CRC-8

Polynomial `0x07`, initial value `0x00`, no reflection, no final XOR
(CRC-8/ATM). Computed over the status byte and payload, excluding the CRC byte
itself.

Adequate for these frame sizes: it catches all single- and double-bit errors
and any burst up to 8 bits. CRC-16 is designed for frames far longer than 18
bytes and does not earn its second byte here.

The link is not assumed error-free. During development this gateware
transmitted `C1 51 A5` where `C1 14 01 A5` was intended — a state machine bug
that silently dropped one byte and corrupted another. The failure mode is what
matters: a corrupted VBUS byte becomes a plausible wrong voltage that nothing
downstream can detect. Contrast a corrupted heartbeat, which merely fails a
pattern match.

`PING` and `STATUS` carry the CRC too, for uniformity — a receiver that
validates every response is simpler than one that validates some.

## 5. Physical layer

Inherited from the sideband design, §2.1:

| | |
|---|---|
| Pin | PA09 (SAMD11) ↔ T6 (ECP5) |
| Rate | 115200 baud, 8-N-1 |
| Idle | High |

Apollo receives on SERCOM1 PAD3 in hardware. Apollo **transmits** by
bit-banging PA09 from a TC1 compare interrupt: `TXPO` selects only PAD0 or
PAD2 (datasheet Table 25-9), and PA09 is PAD3 on both SERCOM1 and SERCOM0, so
hardware transmit is unavailable on this pin.

One bit per timer interrupt, never a delay loop. The TC1 interrupt sits below
USB in the NVIC, so bit-banging cannot delay USB service. The converse is
accepted: USB preempting a bit stretches it, which at 115200 (8.68 µs bits) is
comfortably tolerated because USB ISRs are far shorter than half a bit. At
1 Mbaud that margin disappears, which is an independent reason to keep software
transmit at the lower rate whatever the receive path proves capable of.

## 6. Timeouts

**The FPGA has none, by construction.**

Timeouts exist to abandon partial state. The FPGA holds none: it receives one
command byte, transmits a complete response, and returns to idle. There is no
half-parsed condition to abandon, so there is nothing to time out.

This is why response lengths are fixed rather than length-prefixed. A length
field would make the FPGA stateful across bytes and would immediately require a
timeout to recover from a truncated command.

**Apollo has exactly one**, and it cannot be designed away:

| Timeout | Value at 115200 | Guards against |
|---|---|---|
| Response | ~5 ms | The FPGA being absent: unconfigured, wedged, or running gateware without this protocol |

That is not a protocol concern. The other end genuinely may not be there, which
no framing can fix. It is the same "did anything arrive" check the existing
heartbeat staleness logic already performs.

On expiry Apollo discards any partial response, flushes the SERCOM receiver,
and may retry. After repeated failures it falls back to the existing FPGA
reset/reconfiguration path.

Derive the value from `ADV_UART_BAUD` rather than hard-coding it, so it stays
correct when the rate changes (see
[#85](https://github.com/awtoau/cynthion-workspace/issues/85)).

## 7. Deliberate omissions

**COBS.** The sideband design specifies COBS framing for a continuous telemetry
stream, where a `0x00` delimiter allows resync after corruption. This is short
request/response with fixed lengths, so there is no stream to resync into and
COBS's overhead buys nothing.

**Sequence numbers.** Useful for detecting loss in a stream. Here, a lost
response is detected by the timeout and a corrupted one by the CRC.

**Variable-length payloads.** If a future command needs them, encode the length
in the command byte's high bits so the FPGA still knows the full frame size
from byte one, preserving the stateless property in §6.

**Unsolicited FPGA transmission.** The FPGA never speaks unasked. This is what
makes the link collision-free without arbitration: only one side can be
transmitting at any time, by construction rather than by protocol.
