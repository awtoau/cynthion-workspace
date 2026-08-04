#!/usr/bin/env python3
#
# The FPGA_ADV link the shipping bitstream needs: liveness, and a byte each way.
# SPDX-License-Identifier: BSD-3-Clause

"""
Half-duplex request/response on pin T6, with Apollo as master.

    Apollo -> FPGA:  [CMD]
    FPGA  -> Apollo: [STATUS][payload ...][CRC8]

| CMD | Name | Response | Total |
|---|---|---|---|
| `0x01` | `PING` | STATUS + protocol version + the CPU's byte + CRC8 | 4 B |
| `0x02` | `STATUS` | STATUS + CRC8 | 2 B |
| `0x80`-`0xFF` | `WRITE` | STATUS + CRC8; the low 7 bits reach the CPU | 2 B |

Response length is fixed per command and known to both sides at compile time, so
there is no length field and this module holds no state between commands: it
receives one byte, transmits a complete response, and returns to idle. There is
no half-parsed condition to abandon, and therefore no timeout on this side.

## Two jobs, and this is all of them

**Liveness.** Apollo polls, the status byte answers, and bit 6 toggles per reply.
A value that *changes* proves the responder is executing, where a repeated value
could be a wedged state machine replaying a stale buffer.

**A byte each way.** `PING` carries one byte out, a `WRITE` opcode carries seven
bits in, and `ecp5-test/riscv/sideband_csr.py` is the CPU's end of both. This
works when USB has not enumerated, when the console is silent, and when JTAG is
occupied -- ER1 belongs to Apollo's debug SPI and ER2 to the RISC-V debug module.

**Nothing else.** No flash identity, no HyperRAM presence, no power payload. The
SoC reads its own hardware better -- `power` over I2C, `board` for the memories,
`info` and `selftest` for the image -- and a shipping design must not answer a
query for a capability it does not have. `ecp5-test/sideband/sideband_gateware.py`
is the test bitstream, it uses `apollo_fpga.gateware.sideband.SidebandResponder`,
and it is where a board that will not boot far enough to have a console is asked
about its hardware.

## 230400 is DETERMINED, not chosen

**Any other rate is wrong until proven otherwise on hardware.** The `baud`
argument exists because the receiver has to agree and both ends are built
separately, not because the rate is open.

The ceiling is hardware, not margin. Apollo's SERCOM **cannot transmit** on
PA09: `TXPO` selects PAD0 or PAD2 and PA09 is PAD3, so Apollo's transmit is
bit-banged. Measured with `ecp5-test/adv_speed/`, which streams a counting
sequence so a drop and a corruption are distinguishable:

    230400   100/100 frames
    460800     1/100 frames, with CRC corruption

Raising it also buys little. The 40 us turnaround is an ABSOLUTE time -- it is
the master's pin-handover cost in fixed CPU cycles -- so it does not shrink with
baud, and it already caps a one-byte exchange near 3900/s. Payload size
amortises it; baud does not.

Proving a different rate means a fresh `adv_speed` run on hardware at that rate,
both directions, with the frame counts recorded. Not a build that appears to
work.

## The clock

`divisor = clk_freq_hz // baud`, computed during elaboration, and **nothing
checks that the domain actually runs at `clk_freq_hz`**. A design that raises
`sync` and leaves the argument alone doubles the effective baud; a UART tolerates
about +/-2%, so the link dies rather than degrades. Pass the domain's real
frequency, or instantiate under `DomainRenamer` onto a domain pinned to it.

## One wire, so turnaround is the hard part

The receiver frames a byte at the MIDDLE of its stop bit; the master needs until
its END, plus its own handover. Apollo bit-bangs transmit and returns the pin to
its SERCOM one bit period after the stop bit, then the SERCOM resyncs. Replying
immediately loses the first bits of the response -- observed as the FPGA
receiving every command correctly while the master saw nothing.

**The turnaround is an ABSOLUTE time, not a multiple of the bit period.** The
master's handover cost is fixed in CPU cycles and does not shrink as baud rises.
Scaling it starved the delay exactly where it was needed: 100% at 115200, 92% at
230400, 0% above. Measured floor 25 us on r1.4; the default is 40 us, twice that,
so the margin does not rest on a boundary measured on one board.
"""

from amaranth import Cat, Const, Elaboratable, Module, Mux, Signal
from amaranth_stdio.serial import AsyncSerialRX


__all__ = ["SidebandLink", "CMD_PING", "CMD_STATUS", "CMD_WRITE_BASE",
           "CMD_WRITE_MASK", "PAYLOAD_SIZE", "PROTOCOL_VERSION"]


CMD_PING   = 0x01
CMD_STATUS = 0x02

# A host-to-FPGA byte occupies 0x80-0xFF: the low seven bits are the value, so the
# whole message fits in the single opcode byte and the protocol stays stateless.
# The top bit is the selector, which keeps every other opcode free rather than
# carving the map into ranges twice.
CMD_WRITE_BASE = 0x80
CMD_WRITE_MASK = 0x7F

# Response payload sizes, excluding the status byte and the CRC.
PAYLOAD_SIZE = {
    CMD_PING:   2,
    CMD_STATUS: 0,
}

# Status byte bits.
STATUS_OK          = 0
STATUS_EVENTS      = 1
STATUS_ERROR       = 2
STATUS_RECONFIG    = 3
STATUS_STATE_SHIFT = 4
STATUS_HEARTBEAT   = 6

# Bumped from the 0x01 of the responder that carried POWER and DEVICES. A host
# that pings and gets 0x02 knows which opcode map it is talking to, which is the
# only thing that makes removing commands safe from the other end.
PROTOCOL_VERSION = 0x02


class CRC8(Elaboratable):
    """ CRC-8/ATM: polynomial 0x07, init 0x00, no reflection, no final XOR.

    One byte at a time, eight shifts per clock. At 230400 baud a byte takes ~43
    us, so an eight-deep combinational chain is never the critical path.

    A CRC is warranted because the interesting failure is silent: a corrupted
    status byte is a plausible wrong state rather than an obvious fault.
    """

    def __init__(self):
        self.data   = Signal(8)
        self.strobe = Signal()
        self.clear  = Signal()
        self.crc    = Signal(8)

    def elaborate(self, platform):
        m = Module()

        with m.If(self.clear):
            m.d.sync += self.crc.eq(0)
        with m.Elif(self.strobe):
            value = self.crc ^ self.data
            for _ in range(8):
                value = Mux(value[7], (value << 1)[:8] ^ 0x07, (value << 1)[:8])
            m.d.sync += self.crc.eq(value)

        return m


class UARTTx(Elaboratable):
    """ 8-N-1 transmitter. Idles high, as a UART line at rest must. """

    def __init__(self, divisor):
        self.divisor = divisor
        self.data    = Signal(8)
        self.start   = Signal()
        self.tx      = Signal(init=1)
        self.busy    = Signal()

    def elaborate(self, platform):
        m = Module()

        counter = Signal(range(self.divisor))
        tick    = Signal()
        m.d.comb += tick.eq(counter == 0)
        m.d.sync += counter.eq(Mux(tick, self.divisor - 1, counter - 1))

        # start + 8 data + stop, shifted out LSB first; all-ones is idle.
        shifter = Signal(10, init=0x3FF)
        bit_no  = Signal(range(10))

        m.d.comb += self.tx.eq(shifter[0])

        with m.FSM():

            with m.State("IDLE"):
                m.d.comb += self.busy.eq(0)
                m.d.sync += shifter.eq(0x3FF)
                with m.If(self.start):
                    m.next = "LOAD"

            # Load on a bit boundary. Loading mid-bit would overwrite the shifter
            # before the previous stop bit had been held for its full period,
            # truncating one byte and corrupting the next.
            with m.State("LOAD"):
                m.d.comb += self.busy.eq(1)
                with m.If(tick):
                    m.d.sync += [
                        shifter.eq(Cat(Const(0, 1), self.data, Const(1, 1))),
                        bit_no.eq(0),
                    ]
                    m.next = "SHIFT"

            with m.State("SHIFT"):
                m.d.comb += self.busy.eq(1)
                with m.If(tick):
                    m.d.sync += [
                        shifter.eq(Cat(shifter[1:], 1)),
                        bit_no.eq(bit_no + 1),
                    ]
                    with m.If(bit_no == 9):
                        m.next = "IDLE"

        return m


class SidebandLink(Elaboratable):
    """ Answers Apollo on FPGA_ADV: a status byte, a byte out, a byte in.

    Attributes
    ----------
    rx : Signal(), in
        The line, synchronised. The instantiator owns the CDC.
    pad_o, pad_oe : Signal(), out
        Wire straight to the pad. Open-drain: `pad_o` is always 0.
    tx_active : Signal(), out
        High for the whole reply, including the final stop bit.
    state : Signal(2), in
    events, error, reconfigured : Signal(), in
        Reported in the status byte.
    message : Signal(8), in
        The byte `PING` returns. The CPU's voice on this link.
    received : Signal(7), out
    received_strobe : Signal(), out
        The low seven bits of a `WRITE` opcode, valid for the one cycle the
        strobe is high. Strobed when the command is accepted rather than when the
        reply completes: the value has arrived either way, and delaying it would
        make a lost reply also lose the message.
    """

    def __init__(self, *, clk_freq_hz=60e6, baud=230400, turnaround_us=40):
        self.clk_freq_hz = clk_freq_hz
        self.baud = baud
        self.divisor = int(clk_freq_hz // baud)

        if self.divisor < 8:
            raise ValueError(
                f"clock {clk_freq_hz/1e6:.1f} MHz is too slow for {baud} baud: "
                f"divisor {self.divisor} leaves no room to sample mid-bit")

        self.turnaround_cycles = max(int(clk_freq_hz * turnaround_us / 1e6), 1)

        self.rx        = Signal(init=1)
        self.pad_o     = Signal()
        self.pad_oe    = Signal()
        self.tx_active = Signal()

        self.state        = Signal(2)
        self.events       = Signal()
        self.error        = Signal()
        self.reconfigured = Signal()

        self.message         = Signal(8)
        self.received        = Signal(7)
        self.received_strobe = Signal()

        # Raw receive taps, for instrumentation. A harness can then tell "nothing
        # arrived" from "something arrived that was not a valid command" -- the
        # responder's own state reflects only bytes it accepted.
        self.rx_strobe = Signal()
        self.rx_byte   = Signal(8)

    def elaborate(self, platform):
        m = Module()

        m.submodules.tx_uart = tx_uart = UARTTx(self.divisor)
        m.submodules.crc     = crc     = CRC8()
        m.submodules.rx_uart = rx_uart = AsyncSerialRX(divisor=self.divisor)

        tx = Signal(init=1)
        m.d.comb += tx.eq(tx_uart.tx)

        # Hold the receiver's input idle-high while transmitting.
        #
        # The pin is bidirectional, so the pad reads back whatever it drives:
        # without this the responder frames its own reply as incoming bytes and
        # dispatches on them. Observed as three received bytes for a one-byte
        # command, the last being our own CRC.
        #
        # Idle-high rather than gating the strobe, so the receiver sees a clean
        # line and cannot be left mid-frame when transmission ends.
        m.d.comb += rx_uart.i.eq(Mux(self.tx_active, 1, self.rx))

        # Always ready to accept: each byte is acted on immediately, so there is
        # nothing to back-pressure.
        #
        # Gated on err.frame. AsyncSerialRX strobes rdy for EVERY completed frame
        # including one whose stop bit was low, and err.frame is the only thing
        # distinguishing a byte from noise. Without this gate a break condition --
        # the line held low, exactly what happens while Apollo re-muxes the pin or
        # the FPGA reconfigures -- frames as several garbage bytes, each dispatched
        # as a command and each answered onto a wire the master may be driving.
        received = Signal()
        m.d.comb += [
            rx_uart.ack.eq(1),
            received.eq(rx_uart.rdy & ~rx_uart.err.frame),
            self.rx_strobe.eq(received),
            self.rx_byte.eq(rx_uart.data),
        ]

        # Open-drain, and the reason this can share the pad with the advertiser by
        # OR-ing: never drive high, only pull low, and only while we own the line.
        # `oe` tracks the bit value rather than merely tx_active, so the driver is
        # off for every high bit and for the whole lead-in before the start bit.
        #
        # Contention becomes impossible by construction: whatever the master does,
        # the worst case is two drivers pulling one wire low. Push-pull at both
        # ends is a low-impedance path through two output stages -- a
        # hardware-damage risk, not a link-reliability one.
        m.d.comb += [
            self.pad_o.eq(0),
            self.pad_oe.eq(self.tx_active & ~tx),
        ]

        heartbeat = Signal()
        ok        = Signal()
        status    = Signal(8)
        m.d.comb += status.eq(Cat(
            ok,                 # 0
            self.events,        # 1
            self.error,         # 2
            self.reconfigured,  # 3
            self.state,         # 4-5
            heartbeat,          # 6
            Const(0, 1),        # 7
        ))

        largest = max(PAYLOAD_SIZE.values())
        turnaround  = Signal(range(self.turnaround_cycles + 1))
        payload_len = Signal(range(largest + 1))
        byte_index  = Signal(range(largest + 1))
        send_byte   = Signal(8)

        # send_byte is registered, not combinational: UARTTx latches .data in its
        # LOAD state, one cycle after .start. A combinational value would have
        # returned to its default by then, which transmitted every byte as zero.
        m.d.comb += [
            tx_uart.data.eq(send_byte),
            tx_uart.start.eq(0),
            crc.strobe.eq(0),
            crc.clear.eq(0),
        ]

        with m.FSM():

            with m.State("IDLE"):
                m.d.comb += self.tx_active.eq(0)
                with m.If(received):
                    m.d.comb += crc.clear.eq(1)

                    with m.Switch(rx_uart.data):
                        with m.Case(CMD_PING):
                            m.d.sync += [ok.eq(1),
                                         payload_len.eq(PAYLOAD_SIZE[CMD_PING])]
                        with m.Case(CMD_STATUS):
                            m.d.sync += [ok.eq(1),
                                         payload_len.eq(PAYLOAD_SIZE[CMD_STATUS])]
                        with m.Default():
                            with m.If(rx_uart.data[7]):
                                # A byte from the host. Acknowledged with the
                                # status byte alone -- there is nothing to send
                                # back, and a reply proves it was accepted.
                                m.d.sync += [ok.eq(1), payload_len.eq(0)]
                                # Valid WITH the strobe, not after it: the
                                # consumer latches both in the same cycle, so a
                                # registered value would be one cycle stale and
                                # deliver the previous byte.
                                m.d.comb += [
                                    self.received_strobe.eq(1),
                                    self.received.eq(
                                        rx_uart.data & CMD_WRITE_MASK),
                                ]
                            with m.Else():
                                # Unknown: status alone, OK clear. The master gets
                                # a well-formed reply rather than a timeout, so it
                                # can tell "not understood" from "not there".
                                m.d.sync += [ok.eq(0), payload_len.eq(0)]

                    m.d.sync += [byte_index.eq(0),
                                 turnaround.eq(self.turnaround_cycles)]
                    m.next = "TURNAROUND"

            with m.State("TURNAROUND"):
                m.d.comb += self.tx_active.eq(0)
                m.d.sync += turnaround.eq(turnaround - 1)
                with m.If(turnaround == 0):
                    m.next = "SETTLE"

            # One cycle for the sync writes above (ok, payload_len) to land.
            # status is combinational, so sampling it in the very next cycle would
            # capture the previous command's flags -- which showed up as the OK bit
            # reading zero on a valid command.
            with m.State("SETTLE"):
                m.d.comb += self.tx_active.eq(1)
                m.next = "SEND_STATUS"

            with m.State("SEND_STATUS"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(~tx_uart.busy):
                    m.d.sync += send_byte.eq(status)
                    m.d.comb += [
                        crc.data.eq(status),
                        crc.strobe.eq(1),
                        tx_uart.start.eq(1),
                    ]
                    m.next = "SEND_STATUS_WAIT"

            with m.State("SEND_STATUS_WAIT"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(tx_uart.busy):
                    m.next = "PAYLOAD"

            with m.State("PAYLOAD"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(byte_index == payload_len):
                    m.next = "SEND_CRC"
                with m.Elif(~tx_uart.busy):
                    # PING is the only command with a payload, and it is two
                    # bytes, so this is a choice rather than a table.
                    selected = Signal(8)
                    m.d.comb += selected.eq(Mux(byte_index == 0,
                                                Const(PROTOCOL_VERSION, 8),
                                                self.message))
                    m.d.sync += send_byte.eq(selected)
                    m.d.comb += [
                        crc.data.eq(selected),
                        crc.strobe.eq(1),
                        tx_uart.start.eq(1),
                    ]
                    m.d.sync += byte_index.eq(byte_index + 1)
                    m.next = "PAYLOAD_WAIT"

            with m.State("PAYLOAD_WAIT"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(tx_uart.busy):
                    m.next = "PAYLOAD"

            with m.State("SEND_CRC"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(~tx_uart.busy):
                    m.d.sync += send_byte.eq(crc.crc)
                    m.d.comb += tx_uart.start.eq(1)
                    m.next = "DRAIN"

            with m.State("DRAIN"):
                m.d.comb += self.tx_active.eq(1)
                # Hold tx_active until the final stop bit has actually gone out,
                # or releasing the line would truncate it.
                with m.If(tx_uart.busy):
                    m.next = "FINISH"

            with m.State("FINISH"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(~tx_uart.busy):
                    m.d.sync += heartbeat.eq(~heartbeat)
                    m.next = "IDLE"

        return m
