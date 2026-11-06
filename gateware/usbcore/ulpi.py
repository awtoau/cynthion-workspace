#
# ULPI PHY (USB3343) to UTMI+: the L0 translator.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
`ULPITranslator(ulpi=pins)` presents a ULPI PHY as the PHY side of `utmi.UTMISignature`.

- `pins`: luna/amaranth resource shape: `data.i/.o/.oe`, `dir.i`, `nxt.i`, `stp.o`;
  optional `clk` (`.i` or `.o`) and `rst.o`.
- Parts: `RxCmdDecoder` (line state, events), `ULPITxTranslator` (TXCMD packets),
  `ULPIControlWriter` (UTMI controls -> PHY registers), `ULPIRegisterWindow` (luna's).
- Data lines: driven while `dir` is low; the transmitter wins over the register window.
- Receive: `rx_active` sets on DIR rising with NXT, or on an RXCMD's RxActive rising;
  clears when DIR falls or an RXCMD clears RxActive. `rx_data`/`rx_valid` are the pins one
  cycle late; `rx_valid = nxt & rx_active`.
- Transmit and register writes are mutually exclusive; both wait for `dir` low and,
  with `rst`, for the PHY start time (1 ms, USB334x table 4.3).
- `platform.ulpi_extra_registers` {address: value} are written once after reset
  (the board's D+/D- swap, 0x39).
- `busy`: any ULPI activity or `dir`; owners must see it low before taking the pins.
"""

from amaranth import ClockSignal, Module, ResetSignal, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .ulpi_ctrl import ULPIControlWriter
from .ulpi_regwin import ULPIRegisterWindow
from .ulpi_rxcmd import RxCmdDecoder
from .ulpi_tx import ULPITxTranslator
from .utmi import UTMI_FROM_PHY, UTMI_TO_PHY

__all__ = ["ULPITranslator"]

STARTUP_CYCLES = 60_000         # 1 ms at 60 MHz: USB334x Tstart

RXCMD_SIGNALS = ("line_state", "vbus_valid", "session_valid", "session_end", "rx_error",
                 "host_disconnect", "id_digital")
CONTROL_SIGNALS = ("xcvr_select", "term_select", "op_mode", "suspend", "id_pullup",
                   "dm_pulldown", "dp_pulldown", "chrg_vbus", "dischrg_vbus",
                   "use_external_vbus_indicator")


class ULPITranslator(wiring.Component):

    def __init__(self, *, ulpi, use_platform_registers=True, handle_clocking=True):
        self.ulpi = ulpi
        self.use_platform_registers = use_platform_registers
        self.handle_clocking = handle_clocking
        self._extra = {}
        members = {name: In(width) for name, width in UTMI_TO_PHY}
        members.update({name: Out(width) for name, width in UTMI_FROM_PHY})
        members.update({"busy": Out(1), "last_rx_command": Out(8)})
        super().__init__(members)

    def add_extra_register(self, address, value, *, default_value=None):
        """Keep PHY register `address` at `value`; `default_value` = its reset value."""
        if default_value is None:
            if isinstance(value, Signal):
                raise ValueError("a Signal value needs default_value")
            default_value = 0xFF ^ value
        self._extra[address] = (value, default_value)

    def elaborate(self, platform):
        m = Module()
        ulpi = self.ulpi
        m.submodules.window = window = ULPIRegisterWindow()
        m.submodules.control = control = ULPIControlWriter(window)
        m.submodules.rxcmd = rxcmd = RxCmdDecoder()
        m.submodules.transmit = transmit = ULPITxTranslator()

        if self.use_platform_registers:
            for address, value in getattr(platform, "ulpi_extra_registers", {}).items():
                self.add_extra_register(address, value)
        for address, (value, default) in self._extra.items():
            control.add_register(address, value, reset_value=default)

        domain = "usb"
        if self.handle_clocking:
            domain = getattr(platform, "ulpi_raw_clock_domain", "usb")
            clk = getattr(ulpi, "clk", None)
            if hasattr(clk, "oe"):
                raise TypeError("a bidirectional ULPI clock needs handling by hand")
            elif hasattr(clk, "o"):
                m.d.comb += clk.o.eq(ClockSignal(domain))
            elif hasattr(clk, "i"):
                m.d.comb += ClockSignal(domain).eq(clk.i)
            else:
                raise TypeError(f"ULPI clk of type {type(clk)}: handle clocking by hand")

        phy_ready = Signal()
        if hasattr(ulpi, "rst"):
            m.d.comb += ulpi.rst.o.eq(ResetSignal(domain))
            start = Signal(range(STARTUP_CYCLES + 1))
            m.d.usb += start.eq(start + 1)
            with m.If(start == STARTUP_CYCLES):
                m.d.usb += phy_ready.eq(1)
        else:
            m.d.usb += phy_ready.eq(1)

        dir_i, nxt_i, data_i = ulpi.dir.i, ulpi.nxt.i, ulpi.data.i
        m.d.comb += [
            ulpi.data.oe.eq(~dir_i),
            self.busy.eq(window.busy | transmit.busy | control.busy | dir_i),
            self.last_rx_command.eq(rxcmd.last_rxcmd),

            rxcmd.dir.eq(dir_i), rxcmd.nxt.eq(nxt_i), rxcmd.data.eq(data_i),
            rxcmd.reg_busy.eq(window.busy),

            transmit.nxt.eq(nxt_i),
            transmit.op_mode.eq(self.op_mode),
            transmit.bus_idle.eq(~control.busy & ~dir_i & phy_ready),
            transmit.tx_data.eq(self.tx_data),
            transmit.tx_valid.eq(self.tx_valid),
            self.tx_ready.eq(transmit.tx_ready),

            control.bus_idle.eq(~transmit.busy & phy_ready),
            window.ulpi_data_in.eq(data_i),
            window.ulpi_dir.eq(dir_i),
            window.ulpi_next.eq(nxt_i),
        ]
        m.d.comb += [getattr(self, n).eq(getattr(rxcmd, n)) for n in RXCMD_SIGNALS]
        m.d.comb += [getattr(control, n).eq(getattr(self, n)) for n in CONTROL_SIGNALS]

        with m.If(transmit.out_req):
            m.d.comb += [ulpi.data.o.eq(transmit.data_o), ulpi.stp.o.eq(transmit.stp)]
        with m.Else():
            m.d.comb += [ulpi.data.o.eq(window.ulpi_data_out), ulpi.stp.o.eq(window.ulpi_stop)]

        dir_prev = Signal()
        m.d.usb += dir_prev.eq(dir_i)
        with m.If(~dir_i | rxcmd.rx_stop):
            m.d.usb += self.rx_active.eq(0)
        with m.Elif((~dir_prev & dir_i & nxt_i) | rxcmd.rx_start):
            m.d.usb += self.rx_active.eq(1)

        m.d.usb += [self.rx_data.eq(data_i), self.rx_valid.eq(nxt_i & self.rx_active)]
        return m
