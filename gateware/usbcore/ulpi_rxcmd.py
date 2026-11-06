#
# ULPI RXCMD decoder: the PHY's status byte into UTMI line and event signals.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
ULPI 1.1 3.8.1.2: while the PHY owns the bus (DIR high past the turnaround) and NXT is low,
the data byte is an RXCMD.

- Sampled only when `dir` was also high the cycle before and no register read is running
  (`reg_busy`: a read's data byte arrives with NXT low and is not an RXCMD).
- `last_rxcmd` holds the most recent RXCMD; the UTMI signals decode from it combinationally.
- `rx_start` / `rx_stop`: one-cycle strobes, one cycle after the RXCMD that set / cleared
  RxActive.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["RxCmdDecoder"]


class RxCmdDecoder(wiring.Component):
    dir:             In(1)
    nxt:             In(1)
    data:            In(8)
    reg_busy:        In(1)

    last_rxcmd:      Out(8)
    rx_start:        Out(1)
    rx_stop:         Out(1)
    line_state:      Out(2)
    vbus_valid:      Out(1)
    session_valid:   Out(1)
    session_end:     Out(1)
    rx_active:       Out(1)
    rx_error:        Out(1)
    host_disconnect: Out(1)
    id_digital:      Out(1)

    def elaborate(self, platform):
        m = Module()
        dir_prev = Signal()
        m.d.usb += [dir_prev.eq(self.dir), self.rx_start.eq(0), self.rx_stop.eq(0)]

        with m.If(dir_prev & self.dir & ~self.nxt & ~self.reg_busy):
            m.d.usb += self.last_rxcmd.eq(self.data)
            with m.If(~self.rx_active & self.data[4]):
                m.d.usb += self.rx_start.eq(1)
            with m.If(self.rx_active & ~self.data[4]):
                m.d.usb += self.rx_stop.eq(1)

        cmd = self.last_rxcmd
        m.d.comb += [
            self.line_state.eq(cmd[0:2]),
            self.vbus_valid.eq(cmd[2:4] == 0b11),
            self.session_valid.eq(cmd[2:4] == 0b10),
            self.session_end.eq(cmd[2:4] == 0b00),
            self.rx_active.eq(cmd[4]),
            self.rx_error.eq(cmd[4:6] == 0b11),
            self.host_disconnect.eq(cmd[4:6] == 0b10),
            self.id_digital.eq(cmd[6]),
        ]
        return m
