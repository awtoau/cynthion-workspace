#
# Handshake packets (ACK, NAK, STALL, NYET): generate and detect.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
L1 handshakes: one PID byte, no payload (USB 2.0 8.4.5).

- `HandshakeGenerator`: pulse `issue_<pid>`; the byte is latched and held on `tx` until
  `ready`. Requests while a byte is pending are ignored. Later `issue_*` lines win.
- `HandshakeDetector`: `detected.<pid>` strobes one cycle after a one-byte packet with a
  valid check nibble ends. Longer packets or a bad check nibble strobe nothing.
"""

from amaranth import Module, Signal
from amaranth.lib import data, wiring
from amaranth.lib.wiring import In, Out

from .pid import PID
from .utmi import UTMIRxSignature, UTMITxSignature

__all__ = ["HandshakeGenerator", "HandshakeDetector", "Handshakes"]

HANDSHAKES = ("ack", "nak", "stall", "nyet")


class Handshakes(data.Struct):
    ack:   1
    nak:   1
    stall: 1
    nyet:  1


class HandshakeGenerator(wiring.Component):
    issue_ack:   In(1)
    issue_nak:   In(1)
    issue_stall: In(1)
    issue_nyet:  In(1)
    tx:          Out(UTMITxSignature())

    def elaborate(self, platform):
        m = Module()
        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                for name in HANDSHAKES:
                    with m.If(getattr(self, f"issue_{name}")):
                        m.d.usb += self.tx.data.eq(PID[name.upper()].byte())
                        m.next = "SEND"
            with m.State("SEND"):
                m.d.comb += self.tx.valid.eq(1)
                with m.If(self.tx.ready):
                    m.next = "IDLE"
        return m


class HandshakeDetector(wiring.Component):
    rx:       In(UTMIRxSignature())
    detected: Out(Handshakes)

    def elaborate(self, platform):
        m = Module()
        pid = Signal(4)
        m.d.usb += self.detected.eq(0)
        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                with m.If(self.rx.active):
                    m.next = "PID"
            with m.State("PID"):
                with m.If(~self.rx.active):
                    m.next = "IDLE"
                with m.Elif(self.rx.valid):
                    with m.If(self.rx.data[0:4] == ~self.rx.data[4:8]):
                        m.d.usb += pid.eq(self.rx.data[0:4])
                        m.next = "END"
                    with m.Else():
                        m.next = "SKIP"
            with m.State("END"):
                with m.If(~self.rx.active):
                    for name in HANDSHAKES:
                        m.d.usb += getattr(self.detected, name).eq(pid == PID[name.upper()])
                    m.next = "IDLE"
                with m.Elif(self.rx.valid):
                    m.next = "SKIP"
            with m.State("SKIP"):
                with m.If(~self.rx.active):
                    m.next = "IDLE"
        return m
