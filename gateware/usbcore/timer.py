#
# Inter-packet timer: the bus turnaround windows of USB 2.0 7.1.18.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One saturating counter in the 60 MHz `usb` domain, restarted by any client's `start`.

- Client view: `InterpacketTimerSignature` (`start` out; `tx_allowed`, `tx_timeout`,
  `rx_timeout` in). Each output is a one-cycle strobe when the count reaches its value.
- `tx_allowed` / `tx_timeout`: earliest / latest response after a receive.
- `rx_timeout`: no response expected after a transmit.
- Counts are ULPI clocks (USB 2.0 7.1.18, ULPI 1.1 figure 18):
  - HS: respond after 1, by 24; give up after 92 (736 bit times).
  - FS: respond after 10 (2 bit times), by 32 (6.5); give up after 80 (16).
  - Any other `speed` code (LS, unknown) uses the HS counts. No LS timing yet.
"""

import functools
import operator

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .pid import USBSpeed

__all__ = ["InterpacketTimerSignature", "InterpacketTimer", "HS_COUNTS", "FS_COUNTS"]

# (tx_allowed, tx_timeout, rx_timeout) in 60 MHz cycles.
HS_COUNTS = (1, 24, 92)
FS_COUNTS = (10, 32, 80)


class InterpacketTimerSignature(wiring.Signature):
    def __init__(self):
        super().__init__({
            "start":      Out(1),
            "tx_allowed": In(1),
            "tx_timeout": In(1),
            "rx_timeout": In(1),
        })


class InterpacketTimer(wiring.Component):
    """Shared timer. `add_interface()` clients (client-side objects) before elaboration."""

    speed: In(2)

    def __init__(self, *, fs_only=False):
        self._fs_only = fs_only
        self._clients = []
        super().__init__()

    def add_interface(self, client):
        self._clients.append(client)

    def elaborate(self, platform):
        m = Module()
        top = max(FS_COUNTS if self._fs_only else FS_COUNTS + HS_COUNTS)
        count = Signal(range(top + 2))
        restart = functools.reduce(operator.or_, (c.start for c in self._clients), 0)

        with m.If(restart):
            m.d.usb += count.eq(0)
        with m.Elif(count < top + 1):
            m.d.usb += count.eq(count + 1)

        at_min, at_max, rx_out = Signal(), Signal(), Signal()

        def strobes(counts):
            return [at_min.eq(count == counts[0]), at_max.eq(count == counts[1]),
                    rx_out.eq(count == counts[2])]

        with m.If(self.speed == USBSpeed.FULL):
            m.d.comb += strobes(FS_COUNTS)
        with m.Else():
            if not self._fs_only:
                m.d.comb += strobes(HS_COUNTS)

        for c in self._clients:
            m.d.comb += [c.tx_allowed.eq(at_min), c.tx_timeout.eq(at_max),
                         c.rx_timeout.eq(rx_out)]
        return m
