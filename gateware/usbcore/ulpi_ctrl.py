#
# ULPI control registers kept in step with the UTMI control signals.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
UTMI control inputs have no ULPI pins: they live in PHY registers (ULPI 1.1 4.2).

- Each register has a shadow of what the PHY holds (preset to its reset value). A shadow
  that differs from the wanted value requests a write through the register window.
- Priority: extras (e.g. the board's vendor register 0x39) in the order added, then
  function control (0x04), then OTG control (0x0A). Same order as luna's translator.
- A write starts only while `bus_idle`; the shadow updates on the window's `done`.
- `busy` (registered) covers a requested write and the window's own busy.
- The window must have no other user while this block runs (reads are held off).
"""

from amaranth import Cat, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["ULPIControlWriter", "REG_FUNC_CTRL", "REG_OTG_CTRL"]

REG_FUNC_CTRL, REG_OTG_CTRL = 0x04, 0x0A
FUNC_CTRL_RESET, OTG_CTRL_RESET = 0b0100_0001, 0b0000_0110


class ULPIControlWriter(wiring.Component):
    bus_idle:     In(1)
    xcvr_select:  In(2)
    term_select:  In(1)
    op_mode:      In(2)
    suspend:      In(1)
    id_pullup:    In(1)
    dp_pulldown:  In(1)
    dm_pulldown:  In(1)
    chrg_vbus:    In(1)
    dischrg_vbus: In(1)
    use_external_vbus_indicator: In(1)
    busy:         Out(1)

    def __init__(self, register_window):
        self._window = register_window
        self._extra = []        # (address, wanted value, PHY reset value)
        super().__init__()

    def add_register(self, address, value, *, reset_value):
        self._extra.append((address, value, reset_value))

    def elaborate(self, platform):
        m = Module()
        win = self._window
        func = Cat(self.xcvr_select, self.term_select, self.op_mode, Const(0, 1),
                   ~self.suspend, Const(0, 1))
        otg = Cat(self.id_pullup, self.dp_pulldown, self.dm_pulldown, self.dischrg_vbus,
                  self.chrg_vbus, Const(0, 2), self.use_external_vbus_indicator)
        regs = [*self._extra, (REG_FUNC_CTRL, func, FUNC_CTRL_RESET),
                (REG_OTG_CTRL, otg, OTG_CTRL_RESET)]

        m.d.comb += [win.read_request.eq(0), win.write_request.eq(0)]
        state = []
        for address, wanted, reset in regs:
            shadow = Signal(8, init=reset, name=f"shadow_{address:02x}")
            pending = Signal(8, name=f"pending_{address:02x}")
            stale = shadow != wanted
            with m.If(stale):
                m.d.usb += pending.eq(wanted)
            state.append((address, shadow, pending, stale))

        cond = m.If           # one priority chain: the first stale register is served
        for address, shadow, pending, stale in state:
            with cond(stale):
                start = ~win.done & self.bus_idle
                m.d.comb += [win.address.eq(address), win.write_data.eq(pending),
                             win.write_request.eq(start)]
                m.d.usb += self.busy.eq(start | win.busy)
                with m.If(win.done):
                    m.d.usb += shadow.eq(pending)
            cond = m.Elif
        with m.Else():
            m.d.usb += self.busy.eq(win.busy)
        return m
