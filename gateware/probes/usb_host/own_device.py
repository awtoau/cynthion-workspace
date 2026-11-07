#
# Our device engine behind one UTMI port, for scripts/soc/usb_target_sim.py (sim only).
# SPDX-License-Identifier: GPL-3.0-or-later

"""
`OwnDevice`: `usbcore.device.Ep0Device` on a LUNA-style UTMI interface, so the target-port
harness can swap it for `TargetDevice`. The CPU side is driven by a testbench process
(`scripts/soc/usb_own_device.py`) standing in for firmware.
"""

from amaranth import Elaboratable, Module, Signal
from luna.gateware.interface.utmi import UTMIInterface
from usbcore.device import Ep0Device


class OwnDevice(Elaboratable):
    def __init__(self):
        self.utmi = UTMIInterface()
        self.present = Signal(init=1)
        self.engine = Ep0Device()

    def elaborate(self, platform):
        m = Module()
        m.submodules.engine = e = self.engine
        u, v = self.utmi, e.utmi
        m.d.comb += [
            u.tx_data.eq(v.tx_data), u.tx_valid.eq(v.tx_valid),
            u.xcvr_select.eq(v.xcvr_select), u.term_select.eq(v.term_select),
            u.op_mode.eq(v.op_mode),
            v.tx_ready.eq(u.tx_ready), v.rx_data.eq(u.rx_data), v.rx_active.eq(u.rx_active),
            v.rx_valid.eq(u.rx_valid), v.line_state.eq(u.line_state),
        ]
        return m
