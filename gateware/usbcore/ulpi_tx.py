#
# ULPI transmit: a UTMI byte stream into a ULPI TXCMD packet.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
ULPI 1.1 3.8.2.

- Start: `tx_valid` while `bus_idle`. The TXCMD byte is `0x40 | PID` (the first UTMI byte's
  low nibble), and NXT on it doubles as UTMI `tx_ready` for that first byte.
- `op_mode == 2` (bit stuffing and NRZI off: chirp, SE0): TXCMD is NOPID `0x40` and the
  first byte is not consumed by it; the end drives 0xFF with STP (ULPI 3.8.2.3).
- Body: `tx_data` passes through, `tx_ready = nxt`.
- End: `tx_valid` low -> STP for one cycle with data 0x00, then idle.
- `out_req` (registered) says this block owns the data lines; it is set at start and only
  cleared at the end, so a start abandoned before NXT keeps it set until the next packet.
"""

from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .pid import OpMode

__all__ = ["ULPITxTranslator", "TXCMD"]

TXCMD = 0b0100_0000


class ULPITxTranslator(wiring.Component):
    tx_data:  In(8)
    tx_valid: In(1)
    tx_ready: Out(1)
    op_mode:  In(2)
    bus_idle: In(1)
    nxt:      In(1)

    out_req:  Out(1)
    data_o:   Out(8)
    stp:      Out(1)
    busy:     Out(1)

    def elaborate(self, platform):
        m = Module()
        raw = self.op_mode == OpMode.DISABLE_BITSTUFF_AND_NRZI

        with m.FSM(domain="usb") as fsm:
            m.d.comb += self.busy.eq(~fsm.ongoing("IDLE"))
            with m.State("IDLE"):
                with m.If(self.tx_valid & self.bus_idle):
                    m.d.usb += self.out_req.eq(1)
                    with m.If(raw):
                        m.d.comb += self.data_o.eq(TXCMD)
                    with m.Else():
                        m.d.comb += [self.data_o.eq(TXCMD | self.tx_data[0:4]),
                                     self.tx_ready.eq(self.nxt)]
                    with m.If(self.nxt):
                        m.next = "BODY"
            with m.State("BODY"):
                m.d.comb += [self.data_o.eq(self.tx_data), self.tx_ready.eq(self.nxt)]
                with m.If(~self.tx_valid):
                    m.d.usb += self.out_req.eq(0)
                    m.d.comb += [self.stp.eq(1), self.data_o.eq(0)]
                    with m.If(raw):
                        m.d.comb += self.data_o.eq(0xFF)
                    m.next = "IDLE"
        return m
