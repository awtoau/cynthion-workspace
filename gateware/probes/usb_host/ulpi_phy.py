#
# A ULPI PHY model: the link-side pins of a USB3343, a register file, and a UTMI
# wire side that plugs into `model.connect_utmi`. Simulation only.
# SPDX-License-Identifier: BSD-3-Clause

"""
Sits between a ULPI link (the SoC's port mux) and `model.ModelDevice`.

Modelled (from the ULPI timing LUNA's own link produces, not from silicon):
- Idle NOP, TXCMD (0x4X) with `nxt` throttled by a small FIFO, `stp` ends a packet.
- Register write (0x8X, value, stp) and read (0xCX, turnaround, value) on a register file
  (ID, function control 0x04, OTG control 0x0a, scratch 0x16).
- Device-to-host packets: dir up, two RXCMDs (RxActive), data bytes with `nxt`, RXCMD
  RxActive=0, dir down.
- RXCMD line-state reports, debounced 8 cycles and held back while a packet is in flight.
- `host_disconnect` high: RXCMD RxEvent = HostDisconnect (0b10) in every RXCMD.
- Function control bits [4:3] go straight to UTMI `op_mode` (chirp / SE0 for the wire).

Not modelled: bit stuffing, NRZI, SYNC/EOP, `nxt` gaps from stuffing, RxError, VBUS
events, abort of a transmit by `dir`. A real USB3343 differs in all of these.
"""

from amaranth import C, Cat, DomainRenamer, Elaboratable, Module, Mux, Signal
from amaranth.lib.fifo import SyncFIFO

from luna.gateware.interface.utmi import UTMIInterface

# nxt is withdrawn when the TX FIFO holds this many bytes; the wire drains 1/cycle.
TX_THROTTLE = 24
LS_DEBOUNCE = 8

IDENTITY = {0x00: 0x24, 0x01: 0x04, 0x02: 0x09, 0x03: 0x00}


class UlpiPhyModel(Elaboratable):
    def __init__(self, *, func_ctrl=0x41):
        # Link side (the link's data_o is `data_i` here).
        self.data_i = Signal(8)
        self.stp    = Signal()
        self.dir    = Signal()
        self.nxt    = Signal()
        self.data_o = Signal(8)
        # Wire side: plug into model.connect_utmi like a host UTMI.
        self.utmi = UTMIInterface()
        # Visible state.
        # `mute`: the PHY ignores every link command (an absent or unpowered part).
        self.mute      = Signal()
        self.host_disconnect = Signal()    # RXCMD[5:4] = 0b10 while high
        self.func_ctrl = Signal(8, init=func_ctrl)
        self.otg_ctrl  = Signal(8, init=0x06)
        self.scratch   = Signal(8)
        self.vendor_swap = Signal(8)   # register 0x39, the board's D+/D- swap
        self.writes    = Signal(8)   # register writes seen
        self.rxcmds    = Signal(16)  # line-state RXCMDs sent

    def elaborate(self, platform):
        m = Module()
        u = self.utmi

        m.submodules.txf = txf = DomainRenamer("usb")(SyncFIFO(width=8, depth=32))
        m.submodules.rxf = rxf = DomainRenamer("usb")(SyncFIFO(width=8, depth=64))

        m.d.comb += [
            u.op_mode.eq(self.func_ctrl[3:5]),
            u.xcvr_select.eq(self.func_ctrl[0:2]),
            u.term_select.eq(self.func_ctrl[2]),
            u.tx_valid.eq(txf.r_rdy),
            u.tx_data.eq(txf.r_data),
            txf.r_en.eq(u.tx_ready & txf.r_rdy),
            rxf.w_data.eq(u.rx_data),
            rxf.w_en.eq(u.rx_valid & rxf.w_rdy),
        ]

        ls = u.line_state
        ls_q, ls_cnt = Signal(2), Signal(4)
        reported = Signal(2, init=0b11)
        m.d.usb += ls_q.eq(ls)
        with m.If(ls == ls_q):
            m.d.usb += ls_cnt.eq(Mux(ls_cnt == LS_DEBOUNCE, ls_cnt, ls_cnt + 1))
        with m.Else():
            m.d.usb += ls_cnt.eq(0)

        in_flight = u.tx_valid | u.rx_active | rxf.r_rdy
        ls_pending = Signal()
        m.d.comb += ls_pending.eq((ls_cnt >= LS_DEBOUNCE) & (ls_q != reported) & ~in_flight)

        addr, pid = Signal(6), Signal(4)
        regval = Signal(8)
        with m.Switch(addr):
            for a, v in IDENTITY.items():
                with m.Case(a):
                    m.d.comb += regval.eq(v)
            with m.Case(0x04):
                m.d.comb += regval.eq(self.func_ctrl)
            with m.Case(0x0a):
                m.d.comb += regval.eq(self.otg_ctrl)
            with m.Case(0x16):
                m.d.comb += regval.eq(self.scratch)
            with m.Default():
                m.d.comb += regval.eq(0xff)

        cmd = self.data_i
        cmd_active = Signal(8)
        cmd_idle = Signal(8)
        m.d.comb += [
            # RXCMD: line state, VbusValid, RxActive.
            cmd_active.eq(Cat(ls, C(0b11, 2), C(1, 1), C(0, 3))),
            cmd_idle.eq(Cat(ls, C(0b11, 2), Mux(self.host_disconnect, C(0b10, 2), C(0, 2)), C(0, 2))),
        ]

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.usb += [self.nxt.eq(0), self.dir.eq(0), self.data_o.eq(0)]
                with m.If(self.mute):
                    pass
                with m.Elif(cmd[6:8] == 0b01):
                    m.d.usb += [self.nxt.eq(1), pid.eq(cmd[0:4])]
                    m.next = "TX_CMD"
                with m.Elif(cmd[6:8] == 0b10):
                    m.d.usb += [self.nxt.eq(1), addr.eq(cmd[0:6])]
                    m.next = "W_DATA"
                with m.Elif(cmd[6:8] == 0b11):
                    m.d.usb += [self.nxt.eq(1), addr.eq(cmd[0:6])]
                    m.next = "R_TURN"
                with m.Elif(rxf.r_rdy):
                    m.d.usb += [self.dir.eq(1), self.data_o.eq(cmd_active)]
                    m.next = "RX_TURN"
                with m.Elif(ls_pending):
                    m.d.usb += [self.dir.eq(1), reported.eq(ls_q),
                                self.rxcmds.eq(self.rxcmds + 1)]
                    m.next = "LS_TURN"

            # ---- link transmit: PID byte, then one byte per `nxt` cycle -------
            with m.State("TX_CMD"):
                m.d.usb += self.nxt.eq(1)
                with m.If(pid != 0):
                    m.d.comb += [txf.w_data.eq(Cat(pid, ~pid)), txf.w_en.eq(1)]
                m.next = "TX_DATA"
            with m.State("TX_DATA"):
                with m.If(self.stp):
                    m.d.usb += self.nxt.eq(0)
                    m.next = "IDLE"
                with m.Else():
                    with m.If(self.nxt):
                        m.d.comb += [txf.w_data.eq(self.data_i), txf.w_en.eq(1)]
                    m.d.usb += self.nxt.eq(txf.level < TX_THROTTLE)

            # ---- register write ------------------------------------------------
            with m.State("W_DATA"):
                m.d.usb += self.nxt.eq(1)
                m.next = "W_DATA2"
            with m.State("W_DATA2"):
                m.d.usb += [self.nxt.eq(0), self.writes.eq(self.writes + 1)]
                with m.Switch(addr):
                    with m.Case(0x04):
                        m.d.usb += self.func_ctrl.eq(self.data_i)
                    with m.Case(0x0a):
                        m.d.usb += self.otg_ctrl.eq(self.data_i)
                    with m.Case(0x16):
                        m.d.usb += self.scratch.eq(self.data_i)
                    with m.Case(0x39):
                        m.d.usb += self.vendor_swap.eq(self.data_i)
                m.next = "W_STOP"
            with m.State("W_STOP"):
                m.next = "IDLE"

            # ---- register read -------------------------------------------------
            with m.State("R_TURN"):
                m.d.usb += [self.nxt.eq(0), self.dir.eq(1)]
                m.next = "R_DATA"
            with m.State("R_DATA"):
                m.d.usb += self.data_o.eq(regval)
                m.next = "R_REL"
            with m.State("R_REL"):
                m.d.usb += self.dir.eq(0)
                m.next = "IDLE"

            # ---- device-to-host packet ----------------------------------------
            # c1 turnaround, c2 and c3 RXCMD(RxActive), then data on `nxt`.
            with m.State("RX_TURN"):
                m.d.usb += self.data_o.eq(cmd_active)
                m.next = "RX_CMD2"
            with m.State("RX_CMD2"):
                m.d.usb += self.data_o.eq(cmd_active)
                m.next = "RX_CMD3"
            with m.State("RX_CMD3"):
                m.d.usb += [self.nxt.eq(1), self.data_o.eq(rxf.r_data),
                            ]
                m.d.comb += rxf.r_en.eq(rxf.r_rdy)
                m.next = "RX_DATA"
            with m.State("RX_DATA"):
                with m.If(rxf.r_rdy):
                    m.d.usb += [self.nxt.eq(1), self.data_o.eq(rxf.r_data)]
                    m.d.comb += rxf.r_en.eq(1)
                with m.Elif(u.rx_active):
                    m.d.usb += [self.nxt.eq(0), self.data_o.eq(cmd_active)]
                with m.Else():
                    m.d.usb += [self.nxt.eq(0), self.data_o.eq(cmd_idle),
                                reported.eq(ls)]
                    m.next = "RX_END"
            with m.State("RX_END"):
                m.d.usb += [self.dir.eq(0), self.data_o.eq(0)]
                m.next = "IDLE"

            with m.State("LS_TURN"):
                m.d.usb += self.data_o.eq(cmd_idle)
                m.next = "LS_CMD"
            with m.State("LS_CMD"):
                m.d.usb += [self.dir.eq(0), self.data_o.eq(0)]
                m.next = "IDLE"

        return m
