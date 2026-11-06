#
# Data packets (DATA0, DATA1, DATA2, MDATA): generate and receive, with CRC16.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
L1 data packets: PID byte, 0..1024 payload bytes, CRC16 low byte first (USB 2.0 8.4.4).

- `DataGenerator`: `data_pid` (0..3 = DATA0, DATA1, DATA2, MDATA) is sampled while idle.
  A packet starts on `stream.valid & first`; `valid & last` without `first` sends a ZLP.
  Payload bytes pass straight to `tx`; `last` (or `valid` dropping) appends the CRC.
- `DataReceiver`: payload bytes on `stream` (`next` strobes, two bytes behind the wire so
  the CRC never reaches the stream). At the end of the packet:
  - CRC good: `packet_complete` + `packet_id`, timer start, then `ready_for_response`
    when the timer's `tx_allowed` fires.
  - CRC bad: `crc_mismatch`; drop what was streamed.
  - Fewer than two bytes after the PID: ignored, no strobe.
- The CRC check is the residual of the whole packet (`crc.CRC16_RESIDUAL`).
"""

from amaranth import Cat, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .crc import CRC16
from .pid import DATA_PIDS, PID_GROUP_DATA
from .timer import InterpacketTimerSignature
from .utmi import UTMIRxSignature, UTMITxSignature

__all__ = ["TxPacketSignature", "RxPacketSignature", "DataGenerator", "DataReceiver"]


class TxPacketSignature(wiring.Signature):
    def __init__(self):
        super().__init__({"payload": Out(8), "valid": Out(1), "first": Out(1),
                          "last": Out(1), "ready": In(1)})


class RxPacketSignature(wiring.Signature):
    """`valid` spans the payload; `next` strobes each byte (no backpressure)."""

    def __init__(self):
        super().__init__({"payload": Out(8), "valid": Out(1), "next": Out(1)})


class DataGenerator(wiring.Component):
    data_pid: In(2)
    stream:   In(TxPacketSignature())
    tx:       Out(UTMITxSignature())

    def elaborate(self, platform):
        m = Module()
        m.submodules.crc = crc = CRC16()
        pid_byte = Signal(8)
        crc_hi = Signal(8)
        zlp = Signal()
        m.d.comb += crc.data.eq(self.stream.payload)

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                with m.Switch(self.data_pid):
                    for index, pid in enumerate(DATA_PIDS):
                        with m.Case(index):
                            m.d.usb += pid_byte.eq(pid.byte())
                with m.If(self.stream.valid & self.stream.first):
                    m.d.usb += zlp.eq(0)
                    m.next = "PID"
                with m.Elif(self.stream.valid & self.stream.last):
                    m.d.usb += zlp.eq(1)
                    m.next = "PID"
            with m.State("PID"):
                m.d.comb += [crc.clear.eq(1), self.tx.data.eq(pid_byte), self.tx.valid.eq(1)]
                with m.If(self.tx.ready & zlp):
                    m.next = "CRC_LO"
                with m.Elif(self.tx.ready):
                    m.next = "PAYLOAD"
            with m.State("PAYLOAD"):
                m.d.comb += [
                    self.tx.data.eq(self.stream.payload),
                    self.tx.valid.eq(self.stream.valid),
                    self.stream.ready.eq(self.tx.ready),
                    crc.update.eq(self.tx.ready & self.stream.valid),
                ]
                with m.If(self.tx.ready & (self.stream.last | ~self.stream.valid)):
                    m.next = "CRC_LO"
            with m.State("CRC_LO"):
                m.d.usb += crc_hi.eq(crc.crc[8:16])
                m.d.comb += [self.tx.data.eq(crc.crc[0:8]), self.tx.valid.eq(1)]
                with m.If(self.tx.ready):
                    m.next = "CRC_HI"
            with m.State("CRC_HI"):
                m.d.comb += [self.tx.data.eq(crc_hi), self.tx.valid.eq(1)]
                with m.If(self.tx.ready):
                    m.next = "IDLE"
        return m


class DataReceiver(wiring.Component):
    rx:                 In(UTMIRxSignature())
    timer:              Out(InterpacketTimerSignature())
    stream:             Out(RxPacketSignature())
    active_pid:         Out(4)
    packet_id:          Out(4)
    packet_complete:    Out(1)
    crc_mismatch:       Out(1)
    ready_for_response: Out(1)

    def elaborate(self, platform):
        m = Module()
        rx = self.rx
        m.submodules.crc = crc = CRC16()
        m.d.comb += [crc.data.eq(rx.data), crc.update.eq(rx.valid)]

        pipe = Signal(16)                  # [7:0] oldest byte, [15:8] newest
        shift = pipe.eq(Cat(pipe[8:16], rx.data))
        m.d.comb += self.stream.payload.eq(pipe[0:8])
        m.d.usb += [self.packet_complete.eq(0), self.crc_mismatch.eq(0)]

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                with m.If(rx.active):
                    m.next = "PID"
            with m.State("PID"):
                m.d.comb += crc.clear.eq(1)
                with m.If(~rx.active):
                    m.next = "IDLE"
                with m.Elif(rx.valid):
                    is_data = rx.data[0:2] == PID_GROUP_DATA
                    with m.If(is_data & (rx.data[0:4] == ~rx.data[4:8])):
                        m.d.usb += self.active_pid.eq(rx.data[0:4])
                        m.next = "FIRST"
                    with m.Else():
                        m.next = "SKIP"
            with m.State("FIRST"):
                with m.If(rx.valid):
                    m.d.usb += pipe[8:16].eq(rx.data)
                    m.next = "SECOND"
                with m.If(~rx.active):
                    m.next = "IDLE"
            with m.State("SECOND"):
                with m.If(rx.valid):
                    m.d.usb += shift
                    m.next = "EMIT"
                with m.Elif(~rx.active):
                    m.next = "IDLE"
            with m.State("EMIT"):
                m.d.comb += self.stream.valid.eq(1)
                with m.If(rx.valid):
                    m.d.comb += self.stream.next.eq(1)
                    m.d.usb += shift
                with m.If(~rx.active):
                    with m.If(crc.good):
                        m.d.usb += [self.packet_id.eq(self.active_pid),
                                    self.packet_complete.eq(1)]
                        m.d.comb += self.timer.start.eq(1)
                        m.next = "DELAY"
                    with m.Else():
                        m.d.usb += self.crc_mismatch.eq(1)
                        m.next = "IDLE"
            with m.State("DELAY"):
                with m.If(self.timer.tx_allowed):
                    m.d.comb += self.ready_for_response.eq(1)
                    m.next = "IDLE"
            with m.State("SKIP"):
                with m.If(~rx.active):
                    m.next = "IDLE"
        return m
