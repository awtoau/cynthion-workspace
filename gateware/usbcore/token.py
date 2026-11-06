#
# Token packets (OUT, IN, SETUP, SOF, PING): generate and detect.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
L1 tokens: PID byte, then 11 bits (address [6:0], endpoint [10:7]; SOF: frame) and CRC5,
LSB first in two bytes (USB 2.0 8.4.1, 8.4.3).

- `TokenGenerator`: one `Token` from stream `i` -> three bytes on `tx`.
- `TokenDetector`: `new_token` / `new_frame` strobe one cycle after a token with a good
  check nibble and CRC5 ends; `pid`, `addr`, `endp`, `frame` hold until the next one.
  Anything else (bad CRC5 or check nibble, wrong length, other PIDs) strobes nothing.
  No address filter: the device layer compares `addr`.
"""

from amaranth import Cat, Module, Signal
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from .crc import crc5
from .pid import PID, PID_GROUP_TOKEN
from .utmi import UTMIRxSignature, UTMITxSignature

__all__ = ["Token", "TokenGenerator", "TokenDetector"]


class Token(data.Struct):
    pid:  4
    data: 11


class TokenGenerator(wiring.Component):
    i:  In(stream.Signature(Token))
    tx: Out(UTMITxSignature())

    def elaborate(self, platform):
        m = Module()
        tok = Signal(Token)
        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.usb += tok.eq(self.i.payload)
                    m.next = "PID"
            for state, byte, nxt in (
                    ("PID", Cat(tok.pid, ~tok.pid), "LO"),
                    ("LO",  tok.data[0:8], "HI"),
                    ("HI",  Cat(tok.data[8:11], crc5(tok.data)), "IDLE")):
                with m.State(state):
                    m.d.comb += [self.tx.data.eq(byte), self.tx.valid.eq(1)]
                    with m.If(self.tx.ready):
                        m.next = nxt
        return m


class TokenDetector(wiring.Component):
    rx:        In(UTMIRxSignature())
    pid:       Out(4)
    addr:      Out(7)
    endp:      Out(4)
    frame:     Out(11)
    new_token: Out(1)
    new_frame: Out(1)

    def elaborate(self, platform):
        m = Module()
        rx = self.rx
        cur_pid = Signal(4)
        body = Signal(11)
        m.d.usb += [self.new_token.eq(0), self.new_frame.eq(0)]

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                with m.If(rx.active):
                    m.next = "PID"
            with m.State("PID"):
                is_token = (rx.data[0:2] == PID_GROUP_TOKEN) | (rx.data[0:4] == PID.PING)
                with m.If(~rx.active):
                    m.next = "IDLE"
                with m.Elif(rx.valid):
                    with m.If(is_token & (rx.data[0:4] == ~rx.data[4:8])):
                        m.d.usb += cur_pid.eq(rx.data[0:4])
                        m.next = "LO"
                    with m.Else():
                        m.next = "SKIP"
            with m.State("LO"):
                with m.If(~rx.active):
                    m.next = "IDLE"
                with m.Elif(rx.valid):
                    m.d.usb += body[0:8].eq(rx.data)
                    m.next = "HI"
            with m.State("HI"):
                with m.If(~rx.active):
                    m.next = "IDLE"
                with m.Elif(rx.valid):
                    with m.If(rx.data[3:8] == crc5(Cat(body[0:8], rx.data[0:3]))):
                        m.d.usb += body[8:11].eq(rx.data[0:3])
                        m.next = "END"
                    with m.Else():
                        m.next = "SKIP"
            with m.State("END"):
                with m.If(~rx.active):
                    with m.If(cur_pid == PID.SOF):
                        m.d.usb += [self.frame.eq(body), self.new_frame.eq(1)]
                    with m.Else():
                        m.d.usb += [self.pid.eq(cur_pid), Cat(self.addr, self.endp).eq(body),
                                    self.new_token.eq(1)]
                    m.next = "IDLE"
                with m.Elif(rx.valid):
                    m.next = "SKIP"
            with m.State("SKIP"):
                with m.If(~rx.active):
                    m.next = "IDLE"
        return m
