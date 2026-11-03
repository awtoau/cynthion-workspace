#!/usr/bin/env python3
#
# A runtime role for one ULPI PHY, and the CPU's byte stream into it.
# See docs/usb-host-options.md and docs/aoa-adaptor.md.
# SPDX-License-Identifier: BSD-3-Clause

"""
Role mux + CSR/FIFO shim for one USB3343 port. Instanced on `target_phy` only.

Roles (CSR `ROLE`, reset value `off`):
- `off`:  pins belong to `UlpiRegisters`; identical to `regs` on the wire.
- `regs`: pins belong to `UlpiRegisters` (today's path).
- `host`: the vendored GUH SIE drives the PHY; CPU issues transfers.
- `raw`:  CPU drives ULPI bytes (TXCMD, tokens, data) and reads RXCMD/data bytes.

Parameterised by `name` only; nothing here knows which PHY it faces. The pins
and the `regs_*` hand-off are ports, so aux/control can be instanced later.

## Switching (usb domain)
- Request: `ROLE` write toggles a handshake; a write while one is pending is dropped (`BUSY_DROP`).
- Applied only when `dir` has been low for `QUIET_CYCLES` AND the outgoing owner is idle:
  regs window idle, host translator not busy, raw TX engine idle.
- Wait bound: `switch_limit` usb cycles (1.25 x the longest owner-busy time, a PHY
  reset: `PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES`).
- On expiry: role unchanged, `ROLE_STATUS.timeout` set, `TIMEOUT` event raised.
- Entering `host`: the PHY's function-control (0x04) and OTG-control (0x0a) registers
  are written to their reset values first (the translator's shadow assumes them).
  A failed write (`init_limit`) leaves the role at `regs` and sets `timeout`.
- While not selected, `UlpiRegisters` sees `dir=1`: a CSR read times out as "PHY absent".
- Leaving `host` resets the SIE (SOFs stop); FIFOs are NOT flushed (use `clr_tx`/`clr_rx`).

## Register map (8-bit CSR; 4 CSR bytes per 32-bit word; side effects live alone in a word)
    0x00 ROLE        rw  [1:0] requested role; read = last accepted request
    0x01 CTRL        w   0 bus_reset  1 start  2 clr_tx  3 clr_rx   (strobes)
    0x02 IRQ_EN      rw  0 rx_avail  1 done  2 line  3 errors
    0x04 ROLE_STATUS r   [1:0] current role  2 switching  3 timeout (sticky, cleared by a ROLE write)
    0x05 LINK        r   [1:0] line_state  [3:2] speed (0 HS 1 FS 2 LS 3 unknown)  4 connected
                         5 reset_active  6 host_idle  7 reset_done
    0x06 EVENTS      rw1c 0 done 1 line 2 rx_overflow 3 tx_overflow 4 rx_underflow
                         5 tx_abort 6 busy_drop 7 timeout
    0x07 RXPID       r   first PID byte received in the last transfer
    0x08 ADDR        rw  [6:0] device address
    0x09 EP          rw  [3:0] endpoint  [5:4] type (0 SETUP 1 IN 2 OUT)  6 data_pid (0 DATA0 1 DATA1)
    0x0a RESP        r   [2:0] TransferResponse  3 nyet (RXPID == NYET, SIE reports ACK)
    0x0c RXLEN_LO    r   payload bytes of the last IN, 16-bit (counted here, not by the SIE)
    0x0d RXLEN_HI    r
    0x10 RX_DATA     r   POPS the RX FIFO; empty -> 0 and `rx_underflow`
    0x11 RX_TAG      r   0 tag of head (1 = RXCMD)  1 head valid     (no side effect)
    0x12 RX_LEVEL    r   RX FIFO entries
    0x14 TX_DATA     w   push a byte
    0x15 TX_LAST     w   push the last byte of a packet, then STP (raw)
    0x16 TX_CMD      w   push the last byte of a command, NO STP (raw register read)
    0x17 TX_LEVEL    r   TX FIFO entries (write-domain view)
    0x18 TR_CTRL     w   [0] include bus-reset period [1] drop repeated cycles [2] arm now (else
                         at the next `start`) [3] stop + clear (other bits ignored)
    0x19 TR_STATE    r   [1:0] 0 idle 1 armed 2 recording 3 full  2 read busy  3 present
    0x1a/0x1b TR_CNT r   entries recorded
    0x1c/0x1d TR_IDX rw  entry to read; the HI write loads it (poll TR_STATE.2 clear)
    0x1e TR_SEL      rw  byte of the loaded entry (0..5, little-endian)
    0x1f TR_DATA     r   that byte

## FIFO entries
- TX (10 bit): `{tag[1:0], byte}`; tag 0 data, 1 last+STP, 2 last without STP. `host` ignores tags.
- RX (9 bit): `{tag, byte}`; tag 0 = data byte, tag 1 = RXCMD. `raw` also synthesises an RXCMD
  when DIR+NXT start a receive (RxActive set) and when DIR falls (RxActive cleared).
  A register read returns its value as a tag-1 entry.
- `host`: RX entries are payload bytes, tag 0. Depth `fifo_depth`; the SIE keeps its own 512 B.

## SIE traps (docs/usb-host-options.md 7.3)
- `rx_len` 8-bit wrap: RXLEN counts UTMI receive bytes less PID and CRC16 (3).
- Data toggle: caller's (`EP.data_pid`); no auto toggle.
- No completion strobe: the driver FSM edge-detects `idle` and raises `done` (and the IRQ).
- NYET read as ACK: `RESP.nyet` from the first received PID. FLAGGED ONLY; PING is not issued.
- Start while the SIE is not idle, or a response that never ends: `BUSY_DROP` / watchdog (`TIMEOUT`).
- Trace (0x18..0x1f) exists only when built with `trace_depth` (CYNTHION_USB_TRACE=1); a
  debug aid, absent from the shipping image (`TR_STATE.3` = 0).
- `host` TX bytes flow into the SIE's own 512 B FIFO as written; `clr_tx` empties both FIFOs.
  Stage, then start: the SIE sends whatever it holds at the next `start`.
- OUT payload over the SIE FIFO (512 B): refused at `start`, both FIFOs emptied, `tx_overflow`
  then `done` (#572). A `start` while a discard runs is dropped (`busy_drop`).
- Bus reset in `host`, or leaving `host`: a transfer in progress ends at once (`busy_drop` +
  `done`); a bus reset also empties both TX FIFOs.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from amaranth               import Module, Signal, Mux, Cat, ResetInserter, C
from amaranth.lib.memory    import Memory
from amaranth.lib           import wiring
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib.fifo      import AsyncFIFO
from amaranth.lib.wiring    import In, Out

from amaranth_soc           import csr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "probes"))

from luna.gateware.interface.ulpi       import ULPIRegisterWindow  # noqa: E402
from usb_host.guh.sie                   import USBSIE              # noqa: E402

from clocks                             import (PHY_PAD_RESET_CYCLES,  # noqa: E402
                                                PHY_PREP_CYCLES)
from peripherals.uart16550              import SplitRW             # noqa: E402


__all__ = ["UsbPort", "ROLE_OFF", "ROLE_REGS", "ROLE_HOST", "ROLE_RAW"]

ROLE_OFF, ROLE_REGS, ROLE_HOST, ROLE_RAW = 0, 1, 2, 3

# CSR byte offsets (the map in the module docstring).
REG_ROLE, REG_CTRL, REG_IRQ_EN = 0x00, 0x01, 0x02
REG_ROLE_STATUS, REG_LINK, REG_EVENTS, REG_RXPID = 0x04, 0x05, 0x06, 0x07
REG_ADDR, REG_EP, REG_RESP = 0x08, 0x09, 0x0a
REG_RXLEN_LO, REG_RXLEN_HI = 0x0c, 0x0d
REG_RX_DATA, REG_RX_TAG, REG_RX_LEVEL = 0x10, 0x11, 0x12
REG_TX_DATA, REG_TX_LAST, REG_TX_CMD, REG_TX_LEVEL = 0x14, 0x15, 0x16, 0x17
REG_TR_CTRL, REG_TR_STATE, REG_TR_CNT_LO, REG_TR_CNT_HI = 0x18, 0x19, 0x1a, 0x1b
REG_TR_IDX_LO, REG_TR_IDX_HI, REG_TR_SEL, REG_TR_DATA = 0x1c, 0x1d, 0x1e, 0x1f

# Trace entry (41 bit): [7:0] data, [15:8] flags, [17:16] line state, [35:18] cycles
# since the previous entry, [40:36] SIE's PHY request: xcvr[1:0] term op_mode[1:0]. Flags: dir nxt stp data_oe tx_valid rx_active rx_valid reset_active.
TRACE_WIDTH = 41
TR_IDLE, TR_ARMED, TR_REC, TR_FULL = 0, 1, 2, 3

CTRL_BUS_RESET, CTRL_START, CTRL_CLR_TX, CTRL_CLR_RX = 1 << 0, 1 << 1, 1 << 2, 1 << 3

EV_DONE, EV_LINE, EV_RX_OVERFLOW, EV_TX_OVERFLOW = 1 << 0, 1 << 1, 1 << 2, 1 << 3
EV_RX_UNDERFLOW, EV_TX_ABORT, EV_BUSY_DROP, EV_TIMEOUT = 1 << 4, 1 << 5, 1 << 6, 1 << 7

TAG_DATA, TAG_LAST_STP, TAG_LAST_NOSTP = 0, 1, 2

NYET_PID = 0x96

# ULPI bus must be idle this long before an owner change.
# Longest DIR turnaround/RXCMD burst is ~4 cycles; 8 = 2 x that.
QUIET_CYCLES = 8

# Role-switch wait: longest owner-busy time is a PHY reset (UlpiRegisters), 1.25 x.
# Expiry: role unchanged, ROLE_STATUS.timeout + EV_TIMEOUT.
SWITCH_LIMIT = (5 * (PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES)) // 4

# One ULPI register write is ~5 cycles; two interruptions by a 4-cycle RXCMD
# burst add ~18. 32 = 1.25 x 23. Expiry: role falls back to `regs`, timeout flagged.
INIT_LIMIT = 32

# SIE transfer end-to-end: one full-speed frame (60000) + SIE IPD (1000) ~ 61k, 1.25 x.
# Expiry: SIE reset (bus re-resets), response NONE, EV_TIMEOUT, done raised.
XFER_LIMIT = 76_000

# After `start`, SIE `idle` must fall within 2 cycles; 8 = 4 x (SIE FSM is 1 state deep).
START_EDGE_LIMIT = 8

# Raw engine: a packet's bytes are all in the FIFO when its marker arrives; the
# pointer sync is <= 3 usb cycles behind the marker. 4 = 1.25 x 3.
RAW_SETTLE = 4

# Raw engine: head byte missing mid-packet (all bytes were queued) -> abort.
RAW_STALL_LIMIT = 16

# Default ULPI function-control / OTG-control reset values (USB3343 table 5.x).
PHY_FUNC_CTRL_RESET, PHY_OTG_CTRL_RESET = 0x41, 0x06
ULPI_FUNC_CTRL, ULPI_OTG_CTRL = 0x04, 0x0a


class UsbPort(wiring.Component):
    """Role mux and CSR/FIFO shim for one ULPI port. See the module docstring."""

    def __init__(self, *, name="target", fifo_depth=128, sie_fifo_depth=512,
                 switch_limit=SWITCH_LIMIT, init_limit=INIT_LIMIT,
                 xfer_limit=XFER_LIMIT, trace_depth=0):
        self._trace_depth    = trace_depth
        self._name           = name
        self._fifo_depth     = fifo_depth
        self._sie_fifo_depth = sie_fifo_depth
        self._switch_limit   = switch_limit
        self._init_limit     = init_limit
        self._xfer_limit     = xfer_limit

        def rw(width):
            return csr.Register({"v": csr.Field(SplitRW, width)}, access="rw")

        def ro(*fields):
            return csr.Register({n: csr.Field(csr.action.R, w) for n, w in fields},
                                access="r")

        self._role   = rw(2)
        self._ctrl   = csr.Register({"bus_reset": csr.Field(csr.action.W, 1),
                                     "start":     csr.Field(csr.action.W, 1),
                                     "clr_tx":    csr.Field(csr.action.W, 1),
                                     "clr_rx":    csr.Field(csr.action.W, 1)},
                                    access="w")
        self._irq_en = rw(4)
        self._role_status = ro(("role", 2), ("switching", 1), ("timeout", 1))
        self._link   = ro(("line_state", 2), ("speed", 2), ("connected", 1),
                          ("reset_active", 1), ("idle", 1), ("reset_done", 1))
        self._events = csr.Register({"ev": csr.Field(csr.action.RW1C, 8)},
                                    access="rw")
        self._rxpid  = ro(("pid", 8))
        self._addr   = rw(7)
        self._ep     = rw(7)
        self._resp   = ro(("response", 3), ("nyet", 1))
        self._rxlen_lo = ro(("v", 8))
        self._rxlen_hi = ro(("v", 8))
        self._rx_data  = csr.Register({"v": csr.Field(SplitRW, 8)}, access="rw")
        self._rx_tag   = ro(("tag", 1), ("valid", 1))
        self._rx_level = ro(("v", 8))
        self._tx_data  = csr.Register({"v": csr.Field(csr.action.W, 8)}, access="w")
        self._tx_last  = csr.Register({"v": csr.Field(csr.action.W, 8)}, access="w")
        self._tx_cmd   = csr.Register({"v": csr.Field(csr.action.W, 8)}, access="w")
        self._tx_level = ro(("v", 8))
        self._tr_ctrl  = csr.Register({"v": csr.Field(csr.action.W, 4)}, access="w")
        self._tr_state = ro(("state", 2), ("busy", 1), ("present", 1))
        self._tr_cnt_lo = ro(("v", 8))
        self._tr_cnt_hi = ro(("v", 8))
        self._tr_idx_lo = rw(8)
        self._tr_idx_hi = rw(8)
        self._tr_sel   = rw(3)
        self._tr_data  = ro(("v", 8))

        builder = csr.Builder(addr_width=5, data_width=8)
        for reg_name, reg, offset in (
                ("role", self._role, REG_ROLE), ("ctrl", self._ctrl, REG_CTRL),
                ("irq_en", self._irq_en, REG_IRQ_EN),
                ("role_status", self._role_status, REG_ROLE_STATUS),
                ("link", self._link, REG_LINK), ("events", self._events, REG_EVENTS),
                ("rxpid", self._rxpid, REG_RXPID), ("addr", self._addr, REG_ADDR),
                ("ep", self._ep, REG_EP), ("resp", self._resp, REG_RESP),
                ("rxlen_lo", self._rxlen_lo, REG_RXLEN_LO),
                ("rxlen_hi", self._rxlen_hi, REG_RXLEN_HI),
                ("rx_data", self._rx_data, REG_RX_DATA),
                ("rx_tag", self._rx_tag, REG_RX_TAG),
                ("rx_level", self._rx_level, REG_RX_LEVEL),
                ("tx_data", self._tx_data, REG_TX_DATA),
                ("tx_last", self._tx_last, REG_TX_LAST),
                ("tx_cmd", self._tx_cmd, REG_TX_CMD),
                ("tx_level", self._tx_level, REG_TX_LEVEL),
                ("tr_ctrl", self._tr_ctrl, REG_TR_CTRL),
                ("tr_state", self._tr_state, REG_TR_STATE),
                ("tr_cnt_lo", self._tr_cnt_lo, REG_TR_CNT_LO),
                ("tr_cnt_hi", self._tr_cnt_hi, REG_TR_CNT_HI),
                ("tr_idx_lo", self._tr_idx_lo, REG_TR_IDX_LO),
                ("tr_idx_hi", self._tr_idx_hi, REG_TR_IDX_HI),
                ("tr_sel", self._tr_sel, REG_TR_SEL),
                ("tr_data", self._tr_data, REG_TR_DATA)):
            builder.add(reg_name, reg, offset=offset)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":          In(csr.Signature(addr_width=5, data_width=8)),
            "irq":          Out(1),
            # The pins, usb domain.
            "data_i":       In(8),
            "dir_i":        In(1),
            "nxt_i":        In(1),
            "data_o":       Out(8),
            "data_oe":      Out(1),
            "stp_o":        Out(1),
            # UlpiRegisters hand-off, usb domain.
            "regs_data_o":  In(8),
            "regs_data_oe": In(1),
            "regs_stp_o":   In(1),
            "regs_idle":    In(1),
            "regs_dir":     Out(1),
            "regs_nxt":     Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    # ------------------------------------------------------------------
    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        n_cdc = [0]

        def to_domain(sig, domain):
            n_cdc[0] += 1
            out = Signal(len(sig), name=f"x{n_cdc[0]}_{domain}")
            m.submodules[f"cdc{n_cdc[0]}"] = FFSynchronizer(sig, out, o_domain=domain)
            return out

        def pulse_to_sync(pulse):
            """usb-domain pulse -> sync-domain pulse; spacing >= 4 usb cycles."""
            toggle, pend, hold = Signal(), Signal(), Signal(2)
            m.d.usb += hold.eq(Mux(hold == 0, 0, hold - 1))
            with m.If(pulse):
                m.d.usb += pend.eq(1)
            with m.If(pend & (hold == 0)):
                m.d.usb += [toggle.eq(~toggle), hold.eq(3)]
                with m.If(~pulse):
                    m.d.usb += pend.eq(0)
            synced = to_domain(toggle, "sync")
            prev = Signal()
            m.d.sync += prev.eq(synced)
            return synced ^ prev

        # ============ CSR views =========================================
        role_w   = self._role.f.v
        ctrl     = self._ctrl.f
        irq_en_w = self._irq_en.f.v
        ev_f     = self._events.f.ev
        addr_w, ep_w = self._addr.f.v, self._ep.f.v
        rx_w     = self._rx_data.f.v

        ev_set = Signal(8)          # sync-domain event pulses
        m.d.comb += ev_f.set.eq(ev_set)

        irq_en = Signal(4)
        with m.If(irq_en_w.w_stb):
            m.d.sync += irq_en.eq(irq_en_w.w_data)
        m.d.comb += irq_en_w.r_data.eq(irq_en)

        addr_r, ep_r = Signal(7), Signal(7)
        with m.If(addr_w.w_stb):
            m.d.sync += addr_r.eq(addr_w.w_data)
        with m.If(ep_w.w_stb):
            m.d.sync += ep_r.eq(ep_w.w_data)
        m.d.comb += [addr_w.r_data.eq(addr_r), ep_w.r_data.eq(ep_r)]

        # ============ FIFOs =============================================
        D = self._fifo_depth
        tx = AsyncFIFO(width=10, depth=D, w_domain="sync", r_domain="usb")
        rx = AsyncFIFO(width=9, depth=D, w_domain="usb", r_domain="sync")
        m.submodules.tx_fifo = tx
        m.submodules.rx_fifo = rx

        # ============ role request (sync) ===============================
        role_cmd   = Signal(2)       # last accepted request
        req_t      = Signal()
        ack_usb    = Signal()        # usb toggle: switch finished
        ack_sync   = to_domain(ack_usb, "sync")
        sw_acked   = Signal()
        switching  = Signal()
        m.d.comb += switching.eq(req_t != sw_acked)
        role_now   = Signal(2)       # sync copy of the usb `cur_role`
        sw_timeout = Signal()        # sticky
        cur_role   = Signal(2)       # usb domain
        sw_to_usb  = Signal()        # usb pulse: switch/init timed out
        sw_to_flag = Signal()        # usb level for the timeout, valid at ack
        busy_drop_sync = Signal()

        with m.If(role_w.w_stb):
            with m.If(~switching):
                m.d.sync += [role_cmd.eq(role_w.w_data), req_t.eq(~req_t),
                             sw_timeout.eq(0)]
            with m.Else():
                m.d.comb += busy_drop_sync.eq(1)
        with m.If(ack_sync != sw_acked):
            m.d.sync += [sw_acked.eq(ack_sync), role_now.eq(cur_role)]
            with m.If(sw_to_flag):
                m.d.sync += sw_timeout.eq(1)
        m.d.comb += role_w.r_data.eq(role_cmd)

        role_ok_tx = Signal()        # sync: TX pushes are meaningful
        m.d.comb += role_ok_tx.eq((role_now == ROLE_HOST) | (role_now == ROLE_RAW))

        req_usb = to_domain(req_t, "usb")

        # ============ TX push (sync) ====================================
        tx_ovf = Signal()
        pushed = Signal(4)           # count of LAST entries pushed
        push_tag = Signal(2)
        push_byte = Signal(8)
        push = Signal()
        d, l, c = self._tx_data.f.v, self._tx_last.f.v, self._tx_cmd.f.v
        with m.If(d.w_stb):
            m.d.comb += [push.eq(1), push_tag.eq(TAG_DATA), push_byte.eq(d.w_data)]
        with m.Elif(l.w_stb):
            m.d.comb += [push.eq(1), push_tag.eq(TAG_LAST_STP), push_byte.eq(l.w_data)]
        with m.Elif(c.w_stb):
            m.d.comb += [push.eq(1), push_tag.eq(TAG_LAST_NOSTP), push_byte.eq(c.w_data)]
        m.d.comb += [
            tx.w_data.eq(Cat(push_byte, push_tag)),
            tx.w_en.eq(push & role_ok_tx & tx.w_rdy),
            tx_ovf.eq(push & ~(role_ok_tx & tx.w_rdy)),
        ]
        with m.If(tx.w_en & (push_tag != TAG_DATA)):
            m.d.sync += pushed.eq(pushed + 1)
        pushed_gray = Signal(4)
        m.d.sync += pushed_gray.eq(pushed ^ (pushed >> 1))
        pushed_gray_usb = to_domain(pushed_gray, "usb")
        pushed_usb = Signal(4)
        m.d.comb += pushed_usb.eq(Cat(
            pushed_gray_usb[0] ^ pushed_gray_usb[1] ^ pushed_gray_usb[2] ^ pushed_gray_usb[3],
            pushed_gray_usb[1] ^ pushed_gray_usb[2] ^ pushed_gray_usb[3],
            pushed_gray_usb[2] ^ pushed_gray_usb[3],
            pushed_gray_usb[3]))

        # clr_tx: request toggled to usb, which discards until empty.
        clr_tx_t = Signal()
        with m.If(ctrl.clr_tx.w_stb & ctrl.clr_tx.w_data[0]):
            m.d.sync += clr_tx_t.eq(~clr_tx_t)
        clr_tx_usb = to_domain(clr_tx_t, "usb")

        # clr_rx: pop until empty.
        flushing_rx = Signal()
        with m.If(ctrl.clr_rx.w_stb & ctrl.clr_rx.w_data[0]):
            m.d.sync += flushing_rx.eq(1)
        with m.If(flushing_rx & ~rx.r_rdy):
            m.d.sync += flushing_rx.eq(0)

        # ============ RX pop (sync) =====================================
        rx_pop = Signal()
        rx_under = Signal()
        m.d.comb += [
            rx_pop.eq((rx_w.r_stb | flushing_rx) & rx.r_rdy),
            rx.r_en.eq(rx_pop),
            rx_w.r_data.eq(Mux(rx.r_rdy, rx.r_data[0:8], 0)),
            rx_under.eq(rx_w.r_stb & ~rx.r_rdy),
            self._rx_tag.f.tag.r_data.eq(rx.r_data[8]),
            self._rx_tag.f.valid.r_data.eq(rx.r_rdy),
            self._rx_level.f.v.r_data.eq(rx.r_level),
            self._tx_level.f.v.r_data.eq(tx.w_level),
        ]

        # ============ usb: raw TX engine =================================
        popped = Signal(4)
        pkts = Signal(4)
        m.d.comb += pkts.eq(pushed_usb - popped)

        tx_tag  = tx.r_data[8:10]
        tx_byte = tx.r_data[0:8]

        raw_active = Signal()        # cur_role == RAW and no init in progress
        raw_idle = Signal()
        raw_pop = Signal()
        raw_stp = Signal()
        raw_out_req = Signal()
        raw_abort = Signal()
        settle = Signal(range(RAW_SETTLE + 1))
        stall = Signal(range(RAW_STALL_LIMIT + 1))
        first = Signal()

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.comb += raw_idle.eq(1)
                m.d.usb += [settle.eq(0), stall.eq(0), first.eq(1)]
                with m.If(raw_active & (pkts != 0) & tx.r_rdy & ~self.dir_i):
                    m.next = "SETTLE"
            with m.State("SETTLE"):
                m.d.usb += settle.eq(settle + 1)
                with m.If(~raw_active):
                    m.next = "IDLE"
                with m.Elif(settle == RAW_SETTLE - 1):
                    m.next = "SEND"
            with m.State("SEND"):
                m.d.comb += raw_out_req.eq(1)
                with m.If(self.dir_i):
                    # PHY took the bus. Before the first accept: retry. After: abort.
                    with m.If(first):
                        m.next = "SETTLE"
                        m.d.usb += settle.eq(0)
                    with m.Else():
                        m.d.comb += raw_abort.eq(1)
                        m.next = "DROP"
                with m.Elif(~tx.r_rdy):
                    m.d.usb += stall.eq(stall + 1)
                    m.d.comb += raw_out_req.eq(0)
                    with m.If(stall == RAW_STALL_LIMIT - 1):
                        m.d.comb += raw_abort.eq(1)
                        m.next = "STP"
                with m.Elif(self.nxt_i):
                    m.d.comb += raw_pop.eq(1)
                    m.d.usb += [first.eq(0), stall.eq(0)]
                    with m.If(tx_tag == TAG_LAST_STP):
                        m.d.usb += popped.eq(popped + 1)
                        m.next = "STP"
                    with m.Elif(tx_tag == TAG_LAST_NOSTP):
                        m.d.usb += popped.eq(popped + 1)
                        m.next = "IDLE"
            with m.State("STP"):
                m.d.comb += raw_stp.eq(1)
                m.next = "IDLE"
            with m.State("DROP"):
                # Discard the rest of the aborted packet, counting its marker.
                m.d.comb += raw_pop.eq(tx.r_rdy)
                with m.If(tx.r_rdy & (tx_tag != TAG_DATA)):
                    m.d.usb += popped.eq(popped + 1)
                    m.next = "IDLE"
                with m.Elif(~tx.r_rdy):
                    m.d.usb += stall.eq(stall + 1)
                    with m.If(stall == RAW_STALL_LIMIT - 1):
                        m.next = "IDLE"

        # ============ usb: raw RX engine =================================
        raw_rx_valid = Signal()
        raw_rx_tag   = Signal()
        raw_rx_byte  = Signal(8)
        prev_dir = Signal()
        last_cmd = Signal(8)
        rx_act   = Signal()
        m.d.usb += prev_dir.eq(self.dir_i)
        receiving = Signal()
        m.d.comb += receiving.eq(prev_dir & self.dir_i)
        m.d.usb += raw_rx_valid.eq(0)
        with m.If(raw_active):
            with m.If(receiving & ~self.nxt_i):
                m.d.usb += [raw_rx_valid.eq(1), raw_rx_tag.eq(1),
                            raw_rx_byte.eq(self.data_i), last_cmd.eq(self.data_i),
                            rx_act.eq(self.data_i[4])]
            with m.Elif(receiving & self.nxt_i):
                m.d.usb += [raw_rx_valid.eq(1), raw_rx_tag.eq(0),
                            raw_rx_byte.eq(self.data_i)]
            with m.Elif(~prev_dir & self.dir_i & self.nxt_i):
                m.d.usb += [raw_rx_valid.eq(1), raw_rx_tag.eq(1),
                            raw_rx_byte.eq(last_cmd | 0x10),
                            last_cmd.eq(last_cmd | 0x10), rx_act.eq(1)]
            with m.Elif(prev_dir & ~self.dir_i & rx_act):
                m.d.usb += [raw_rx_valid.eq(1), raw_rx_tag.eq(1),
                            raw_rx_byte.eq(last_cmd & 0xef),
                            last_cmd.eq(last_cmd & 0xef), rx_act.eq(0)]
        with m.Else():
            m.d.usb += rx_act.eq(0)

        # ============ usb: role switch ===================================
        init_active = Signal()
        init_done   = Signal()
        iw_reset    = Signal()
        iw = ULPIRegisterWindow()
        m.submodules.init_window = ResetInserter({"usb": iw_reset})(iw)
        m.d.comb += [
            iw.ulpi_data_in.eq(self.data_i),
            iw.ulpi_dir.eq(self.dir_i),
            iw.ulpi_next.eq(self.nxt_i),
        ]

        # Host SIE, built below; the switch FSM needs its busy.
        ulpi = SimpleNamespace(
            dir=SimpleNamespace(i=Signal(name="h_dir")),
            nxt=SimpleNamespace(i=Signal(name="h_nxt")),
            data=SimpleNamespace(i=Signal(8, name="h_data_i"), o=Signal(8, name="h_data_o"),
                                 oe=Signal(name="h_data_oe")),
            stp=SimpleNamespace(o=Signal(name="h_stp")),
        )
        sie = USBSIE(bus=ulpi, handle_clocking=False, fullspeed_only=False,
                     fifo_depth=self._sie_fifo_depth)
        self.sie = sie      # for simulation probes
        host_rst = Signal()
        m.submodules.sie = ResetInserter({"usb": host_rst})(sie)
        m.d.comb += [
            ulpi.dir.i.eq(self.dir_i),
            ulpi.nxt.i.eq(self.nxt_i),
            ulpi.data.i.eq(self.data_i),
        ]

        owner_idle = Signal()
        with m.Switch(cur_role):
            with m.Case(ROLE_OFF, ROLE_REGS):
                m.d.comb += owner_idle.eq(self.regs_idle)
            with m.Case(ROLE_HOST):
                m.d.comb += owner_idle.eq(~sie.utmi.busy)
            with m.Case(ROLE_RAW):
                m.d.comb += owner_idle.eq(raw_idle)

        tgt     = Signal(2)
        quiet   = Signal(range(QUIET_CYCLES + 1))
        waited  = Signal(range(self._switch_limit + 1))
        init_t  = Signal(range(self._init_limit + 1))
        quiet_ok = Signal()
        m.d.comb += quiet_ok.eq(~self.dir_i & owner_idle)

        def finish(timeout):
            m.d.usb += [ack_usb.eq(~ack_usb), sw_to_flag.eq(timeout)]
            if timeout:
                m.d.comb += sw_to_usb.eq(1)

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.usb += [quiet.eq(0), waited.eq(0)]
                with m.If(req_usb != ack_usb):
                    m.d.usb += tgt.eq(role_cmd)
                    m.next = "QUIET"
            with m.State("QUIET"):
                m.d.usb += waited.eq(waited + 1)
                m.d.usb += quiet.eq(Mux(quiet_ok, quiet + 1, 0))
                with m.If(quiet_ok & (quiet == QUIET_CYCLES - 1)):
                    m.next = "APPLY"
                with m.Elif(waited == self._switch_limit - 1):
                    finish(1)
                    m.next = "IDLE"
            with m.State("APPLY"):
                m.d.usb += [cur_role.eq(tgt), init_done.eq(0), init_t.eq(0)]
                with m.If(tgt == ROLE_HOST):
                    m.next = "INIT_A"
                with m.Else():
                    finish(0)
                    m.next = "IDLE"
            for state, reg, val, nxt in (("INIT_A", ULPI_FUNC_CTRL, PHY_FUNC_CTRL_RESET, "INIT_A_WAIT"),
                                         ("INIT_B", ULPI_OTG_CTRL, PHY_OTG_CTRL_RESET, "INIT_B_WAIT")):
                with m.State(state):
                    m.d.comb += [init_active.eq(1), iw.address.eq(reg),
                                 iw.write_data.eq(val)]
                    m.d.usb += init_t.eq(0)
                    with m.If(iw.busy == 0):
                        m.d.comb += iw.write_request.eq(1)
                        m.next = nxt
                with m.State(nxt):
                    m.d.comb += [init_active.eq(1), iw.address.eq(reg),
                                 iw.write_data.eq(val)]
                    m.d.usb += init_t.eq(init_t + 1)
                    done_state = "INIT_B" if state == "INIT_A" else "INIT_DONE"
                    with m.If(iw.done):
                        m.next = done_state
                    with m.Elif(init_t == self._init_limit - 1):
                        m.d.comb += iw_reset.eq(1)
                        m.d.usb += cur_role.eq(ROLE_REGS)
                        finish(1)
                        m.next = "IDLE"
            with m.State("INIT_DONE"):
                m.d.usb += init_done.eq(1)
                finish(0)
                m.next = "IDLE"

        sel_host = Signal()
        sel_raw  = Signal()
        sel_regs = Signal()
        m.d.comb += [
            sel_regs.eq(((cur_role == ROLE_OFF) | (cur_role == ROLE_REGS)) & ~init_active),
            sel_host.eq((cur_role == ROLE_HOST) & init_done & ~init_active),
            sel_raw.eq((cur_role == ROLE_RAW) & ~init_active),
            raw_active.eq(sel_raw),
        ]

        # ============ pin mux ============================================
        m.d.comb += [
            self.data_o.eq(0),
            self.data_oe.eq(~self.dir_i),
            self.stp_o.eq(0),
            self.regs_dir.eq(Mux(sel_regs, self.dir_i, 1)),
            self.regs_nxt.eq(Mux(sel_regs, self.nxt_i, 0)),
        ]
        with m.If(sel_regs):
            m.d.comb += [self.data_o.eq(self.regs_data_o),
                         self.data_oe.eq(self.regs_data_oe),
                         self.stp_o.eq(self.regs_stp_o)]
        with m.Elif(init_active):
            m.d.comb += [self.data_o.eq(iw.ulpi_data_out), self.stp_o.eq(iw.ulpi_stop)]
        with m.Elif(sel_host):
            m.d.comb += [self.data_o.eq(ulpi.data.o), self.data_oe.eq(ulpi.data.oe),
                         self.stp_o.eq(ulpi.stp.o)]
        with m.Elif(sel_raw):
            with m.If(raw_out_req):
                m.d.comb += self.data_o.eq(tx_byte)
            with m.Elif(raw_stp):
                m.d.comb += self.stp_o.eq(1)

        # ============ usb: host driver ===================================
        ctrl_start = ctrl.start.w_stb & ctrl.start.w_data[0]
        start_t, xfer_ack = Signal(), Signal()    # sync toggle / usb toggle
        done_t = Signal()                           # usb toggle: transfer finished
        done_sync = to_domain(done_t, "sync")
        done_prev = Signal()
        xfer_busy = Signal()
        m.d.sync += done_prev.eq(done_sync)
        done_pulse = Signal()
        m.d.comb += done_pulse.eq(done_sync != done_prev)
        m.d.comb += xfer_busy.eq(start_t != done_sync)
        busy_drop_start = Signal()
        with m.If(ctrl_start):
            with m.If(~xfer_busy):
                m.d.sync += start_t.eq(~start_t)
            with m.Else():
                m.d.comb += busy_drop_start.eq(1)
        start_usb = to_domain(start_t, "usb")

        brst_t = Signal()
        with m.If(ctrl.bus_reset.w_stb & ctrl.bus_reset.w_data[0]):
            m.d.sync += brst_t.eq(~brst_t)
        brst_usb = to_domain(brst_t, "usb")
        brst_prev = Signal()
        m.d.usb += brst_prev.eq(brst_usb)
        host_brst = Signal()        # usb pulse: bus reset requested in host role
        m.d.comb += host_brst.eq(sel_host & (brst_usb != brst_prev))

        addr_u, ep_u = to_domain(addr_r, "usb"), to_domain(ep_r, "usb")
        # addr/ep are written before `start` toggles and held until `done`; the
        # synchronised copies are stable by the time the toggle arrives (>= 2 cycles).

        xfer = sie.ctrl.xfer
        status = sie.ctrl.status
        start_pulse = Signal()
        m.d.comb += [
            xfer.start.eq(start_pulse),
            xfer.type.eq(ep_u[4:6]),
            xfer.dev_addr.eq(addr_u),
            xfer.ep_addr.eq(ep_u[0:4]),
            xfer.data_pid.eq(ep_u[6]),
            sie.ctrl.bus_reset.eq(host_brst),
        ]

        rx_cnt  = Signal(10)
        rx_pid  = Signal(8)
        got_pid = Signal()
        h_resp  = Signal(3)
        h_len   = Signal(16)
        wd      = Signal(range(self._xfer_limit + 1))
        drain   = Signal(3)
        wd_reset = Signal()
        h_drop  = Signal()
        h_tmo   = Signal()
        h_oversize = Signal()       # usb pulse: OUT payload over the SIE FIFO, refused
        flush_act = Signal()        # TX discard (shim FIFO and SIE FIFO) in progress
        flush_req = Signal()
        m.d.comb += host_rst.eq(~sel_host | wd_reset)

        with m.If(sel_host & sie.utmi.rx_valid):
            m.d.usb += rx_cnt.eq(rx_cnt + 1)
            with m.If(~got_pid):
                m.d.usb += [rx_pid.eq(sie.utmi.rx_data), got_pid.eq(1)]

        def finish_xfer(**result):
            m.d.usb += [h_resp.eq(result.get("resp", 0)), h_len.eq(result.get("length", 0)),
                        xfer_ack.eq(~xfer_ack), done_t.eq(~done_t)]

        def abort_on_reset():
            # A bus reset or leaving host ends any transfer at once (EV_BUSY_DROP + done).
            with m.If(host_brst | ~sel_host):
                m.d.comb += h_drop.eq(1)
                finish_xfer()
                m.next = "IDLE"

        with m.FSM(domain="usb"):
            with m.State("IDLE"):
                m.d.usb += [drain.eq(0), wd.eq(0)]
                with m.If(start_usb != xfer_ack):
                    with m.If(sel_host & status.idle & ~status.reset_active & ~flush_act):
                        m.next = "DRAIN"
                    with m.Else():
                        m.d.comb += h_drop.eq(1)
                        m.d.usb += rx_pid.eq(0)
                        finish_xfer()
            with m.State("DRAIN"):
                # All pushed bytes must be inside the SIE before it samples tx_len.
                m.d.usb += drain.eq(Mux(tx.r_rdy, 0, drain + 1))
                with m.If(tx.r_rdy & ~sie.ctrl.txs.ready):
                    # SIE FIFO full with bytes still queued: never sendable, discard it all.
                    # The event is raised here, >= 6 cycles before `done` (after the flush).
                    m.d.comb += [flush_req.eq(1), h_oversize.eq(1)]
                    m.next = "REFUSE"
                with m.Elif(drain == 4):
                    m.d.usb += [rx_cnt.eq(0), got_pid.eq(0)]
                    m.next = "PULSE"
                abort_on_reset()
            with m.State("REFUSE"):
                with m.If(~flush_act):
                    finish_xfer()
                    m.next = "IDLE"
                abort_on_reset()
            with m.State("PULSE"):
                m.d.comb += start_pulse.eq(1)
                m.d.usb += wd.eq(0)
                m.next = "STARTED"
                abort_on_reset()
            with m.State("STARTED"):
                m.d.usb += wd.eq(wd + 1)
                with m.If(~status.idle):
                    m.d.usb += wd.eq(0)
                    m.next = "RUN"
                with m.Elif(wd == START_EDGE_LIMIT - 1):
                    m.d.comb += h_drop.eq(1)
                    finish_xfer()
                    m.next = "IDLE"
                abort_on_reset()
            with m.State("RUN"):
                m.d.usb += wd.eq(wd + 1)
                with m.If(status.idle):
                    finish_xfer(resp=status.response.as_value(),
                                length=Mux(rx_cnt >= 3, rx_cnt - 3, 0))
                    m.next = "IDLE"
                with m.Elif(wd == self._xfer_limit - 1):
                    m.d.comb += [h_tmo.eq(1), wd_reset.eq(1)]
                    finish_xfer()
                    m.next = "IDLE"
                abort_on_reset()

        # TX discard: CLR_TX, a host bus reset, or a refused payload. Ends once both FIFOs
        # have been empty for 5 cycles (the CDC pointer sync is <= 3 behind).
        flush_t_prev = Signal()
        flush_quiet = Signal(3)
        m.d.usb += flush_t_prev.eq(clr_tx_usb)
        with m.If(flush_act):
            m.d.usb += flush_quiet.eq(Mux(tx.r_rdy | sie.ctrl.tx_pending, 0, flush_quiet + 1))
            with m.If(flush_quiet == 5):
                m.d.usb += [flush_act.eq(0), popped.eq(pushed_usb)]
        with m.If((clr_tx_usb != flush_t_prev) | flush_req | host_brst):
            m.d.usb += [flush_act.eq(1), flush_quiet.eq(0)]
        m.d.comb += sie.ctrl.tx_flush.eq(flush_act & sel_host)

        host_fwd = Signal()
        m.d.comb += host_fwd.eq(sel_host & ~flush_act)
        m.d.comb += [
            sie.ctrl.txs.payload.eq(tx_byte),
            sie.ctrl.txs.valid.eq(host_fwd & tx.r_rdy),
            tx.r_en.eq((host_fwd & tx.r_rdy & sie.ctrl.txs.ready)
                       | (flush_act & tx.r_rdy) | raw_pop),
        ]

        # RX push (usb): raw bytes or SIE payload.
        rx_push = Signal()
        rx_tag_in, rx_byte_in = Signal(), Signal(8)
        host_rx_take = Signal()
        m.d.comb += [
            host_rx_take.eq(sel_host & sie.ctrl.rxs.valid & rx.w_rdy),
            sie.ctrl.rxs.ready.eq(sel_host & rx.w_rdy),
        ]
        with m.If(raw_rx_valid & raw_active):
            m.d.comb += [rx_push.eq(1), rx_tag_in.eq(raw_rx_tag), rx_byte_in.eq(raw_rx_byte)]
        with m.Elif(host_rx_take):
            m.d.comb += [rx_push.eq(1), rx_tag_in.eq(0), rx_byte_in.eq(sie.ctrl.rxs.payload)]
        rx_drop = Signal()
        m.d.comb += [
            rx.w_data.eq(Cat(rx_byte_in, rx_tag_in)),
            rx.w_en.eq(rx_push & rx.w_rdy),
            rx_drop.eq(raw_rx_valid & raw_active & ~rx.w_rdy),
        ]

        # ============ link status (usb -> sync) ==========================
        ls_host = sie.utmi.line_state
        ls = Signal(2)
        connected = Signal()
        speed = Signal(2)
        with m.If(sel_host):
            m.d.comb += [ls.eq(ls_host), connected.eq(~status.disconnected),
                         speed.eq(status.detected_speed.as_value())]
        with m.Else():
            m.d.comb += [ls.eq(last_cmd[0:2]), connected.eq(last_cmd[4:6] != 0b10),
                         speed.eq(0b11)]
        # reset_done: sticky, set when reset_active falls after being seen high.
        reset_done, armed = Signal(), Signal()
        with m.If(~sel_host | (brst_usb != brst_prev)):
            m.d.usb += [reset_done.eq(0), armed.eq(0)]
        with m.Elif(status.reset_active):
            m.d.usb += armed.eq(1)
        with m.Elif(armed):
            m.d.usb += reset_done.eq(1)
        link_bits = Signal(8)
        m.d.comb += link_bits.eq(Cat(ls, speed, connected,
                                     sel_host & status.reset_active,
                                     Mux(sel_host, status.idle, raw_idle),
                                     reset_done))
        link_sync = to_domain(link_bits, "sync")
        l = self._link.f
        m.d.comb += [l.line_state.r_data.eq(link_sync[0:2]), l.speed.r_data.eq(link_sync[2:4]),
                     l.connected.r_data.eq(link_sync[4]), l.reset_active.r_data.eq(link_sync[5]),
                     l.idle.r_data.eq(link_sync[6]), l.reset_done.r_data.eq(link_sync[7])]

        rs = self._role_status.f
        m.d.comb += [rs.role.r_data.eq(role_now), rs.switching.r_data.eq(switching),
                     rs.timeout.r_data.eq(sw_timeout)]

        # Results latch when `done` arrives (the data is stable by then).
        res_resp, res_len, res_pid = Signal(3), Signal(16), Signal(8)
        h_resp_u, h_len_u, h_pid_u = h_resp, h_len, rx_pid
        with m.If(done_pulse):
            m.d.sync += [res_resp.eq(h_resp_u), res_len.eq(h_len_u), res_pid.eq(h_pid_u)]
        m.d.comb += [
            self._resp.f.response.r_data.eq(res_resp),
            self._resp.f.nyet.r_data.eq(res_pid == NYET_PID),
            self._rxlen_lo.f.v.r_data.eq(res_len[0:8]),
            self._rxlen_hi.f.v.r_data.eq(res_len[8:16]),
            self._rxpid.f.pid.r_data.eq(res_pid),
        ]

        # ============ events + IRQ =======================================
        ls_prev = Signal(2)
        line_pulse_u = Signal()
        m.d.usb += ls_prev.eq(ls)
        m.d.comb += line_pulse_u.eq((ls != ls_prev) & (sel_host | raw_active))
        m.d.comb += ev_set.eq(Cat(
            done_pulse,
            pulse_to_sync(line_pulse_u),
            pulse_to_sync(rx_drop),
            tx_ovf | pulse_to_sync(h_oversize),
            rx_under,
            pulse_to_sync(raw_abort),
            pulse_to_sync(h_drop) | busy_drop_start | busy_drop_sync,
            pulse_to_sync(sw_to_usb | h_tmo),
        ))
        ev = ev_f.data
        m.d.comb += self.irq.eq(
            (irq_en[0] & rx.r_rdy)
            | (irq_en[1] & ev[0])
            | (irq_en[2] & ev[1])
            | (irq_en[3] & (ev[2:8] != 0)))

        # ============ trace (debug build only) ===========================
        if self._trace_depth:
            self._elaborate_trace(m, to_domain, sel_host, start_pulse, sie)
        else:
            m.d.comb += self._tr_state.f.present.r_data.eq(0)

        return m

    def _elaborate_trace(self, m, to_domain, sel_host, start_pulse, sie):
        """Cycle-stamped record of the ULPI pins and SIE strobes, read back by CSR."""
        depth = self._trace_depth
        abits = (depth - 1).bit_length()
        mem = Memory(shape=TRACE_WIDTH, depth=depth, init=[])
        m.submodules.trace_mem = mem
        wport = mem.write_port(domain="usb")
        rport = mem.read_port(domain="usb")

        ctrl = self._tr_ctrl.f.v
        tr_cmd = Signal(4)
        tr_t = Signal()
        stb_d = Signal(2)
        m.d.sync += stb_d.eq(Cat(ctrl.w_stb, stb_d[0]))
        with m.If(ctrl.w_stb):
            m.d.sync += tr_cmd.eq(ctrl.w_data)
        with m.If(stb_d[1]):     # the command bits cross two cycles before the toggle
            m.d.sync += tr_t.eq(~tr_t)
        cmd_u, t_u = to_domain(tr_cmd, "usb"), to_domain(tr_t, "usb")
        t_prev = Signal()
        m.d.usb += t_prev.eq(t_u)
        new_cmd = Signal()
        m.d.comb += new_cmd.eq(t_u != t_prev)

        state = Signal(2)
        inc_reset, compress = Signal(), Signal()
        count = Signal(16)
        since = Signal(18)
        pin = self
        reset_act = Signal()
        m.d.comb += reset_act.eq(sel_host & sie.ctrl.status.reset_active)
        data = Signal(8)
        m.d.comb += data.eq(Mux(pin.dir_i, pin.data_i, pin.data_o))
        flags = Cat(pin.dir_i, pin.nxt_i, pin.stp_o, pin.data_oe, sie.utmi.tx_valid,
                    sie.utmi.rx_active, sie.utmi.rx_valid, reset_act)
        ls = sie.utmi.line_state
        ls_prev = Signal(2)
        m.d.usb += ls_prev.eq(ls)
        nd = Signal(18)
        m.d.comb += nd.eq(Cat(data, flags, ls))
        prev_nd, prev_ev = Signal(18), Signal()
        ev = Signal()
        req = Signal(5)
        req_prev = Signal(5)
        m.d.comb += req.eq(Cat(sie.utmi.xcvr_select, sie.utmi.term_select, sie.utmi.op_mode))
        m.d.usb += req_prev.eq(req)
        m.d.comb += ev.eq(pin.dir_i | pin.nxt_i | pin.stp_o
                          | (pin.data_oe & (pin.data_o != 0)) | (ls != ls_prev)
                          | (req != req_prev))
        m.d.usb += [prev_nd.eq(nd), prev_ev.eq(ev)]
        dup = Signal()
        m.d.comb += dup.eq(compress & prev_ev & (prev_nd == nd))
        gate = Signal()
        m.d.comb += gate.eq(inc_reset | ~reset_act)

        with m.If(new_cmd):
            with m.If(cmd_u[3]):
                m.d.usb += [state.eq(TR_IDLE), count.eq(0)]
            with m.Else():
                m.d.usb += [inc_reset.eq(cmd_u[0]), compress.eq(cmd_u[1]), count.eq(0),
                            since.eq(0),
                            state.eq(Mux(cmd_u[2], TR_REC, TR_ARMED))]
        with m.Else():
            with m.If((state == TR_ARMED) & start_pulse):
                m.d.usb += [state.eq(TR_REC), since.eq(0)]
            with m.If(state == TR_REC):
                m.d.usb += since.eq(Mux(since == (1 << 18) - 1, since, since + 1))
                with m.If(ev & gate & ~dup):
                    m.d.comb += [wport.en.eq(1)]
                    m.d.usb += [count.eq(count + 1), since.eq(1)]
                    with m.If(count == depth - 1):
                        m.d.usb += state.eq(TR_FULL)
        m.d.comb += [wport.addr.eq(count[:abits]),
                     wport.data.eq(Cat(nd, since, req))]

        # Read side: sync writes the index, usb latches the word, sync reads bytes.
        idx_lo, idx_hi = self._tr_idx_lo.f.v, self._tr_idx_hi.f.v
        sel_w = self._tr_sel.f.v
        idx_r, sel_r = Signal(16), Signal(3)
        with m.If(idx_lo.w_stb):
            m.d.sync += idx_r[0:8].eq(idx_lo.w_data)
        with m.If(idx_hi.w_stb):
            m.d.sync += idx_r[8:16].eq(idx_hi.w_data)
        with m.If(sel_w.w_stb):
            m.d.sync += sel_r.eq(sel_w.w_data)
        m.d.comb += [idx_lo.r_data.eq(idx_r[0:8]), idx_hi.r_data.eq(idx_r[8:16]),
                     sel_w.r_data.eq(sel_r)]
        ld_t = Signal()
        with m.If(idx_hi.w_stb):
            m.d.sync += ld_t.eq(~ld_t)
        ld_u = to_domain(ld_t, "usb")
        idx_u = to_domain(idx_r, "usb")
        # idx bits and the toggle cross separately (skew <= 1 cycle); latch 4 cycles on.
        ld_prev, ack_t = Signal(), Signal()
        ld_wait = Signal(3)
        word = Signal(TRACE_WIDTH)
        m.d.comb += [rport.addr.eq(idx_u[:abits]), rport.en.eq(1)]
        m.d.usb += ld_prev.eq(ld_u)
        with m.If(ld_u != ld_prev):
            m.d.usb += ld_wait.eq(4)
        with m.Elif(ld_wait != 0):
            m.d.usb += ld_wait.eq(ld_wait - 1)
            with m.If(ld_wait == 1):
                m.d.usb += [word.eq(rport.data), ack_t.eq(ld_u)]
        ack_s = to_domain(ack_t, "sync")
        word_s = to_domain(word, "sync")
        busy = Signal()
        m.d.comb += busy.eq(ld_t != ack_s)
        st_s = to_domain(state, "sync")
        cnt_s = to_domain(count, "sync")
        f = self._tr_state.f
        m.d.comb += [
            f.state.r_data.eq(st_s), f.busy.r_data.eq(busy), f.present.r_data.eq(1),
            self._tr_cnt_lo.f.v.r_data.eq(cnt_s[0:8]),
            self._tr_cnt_hi.f.v.r_data.eq(cnt_s[8:16]),
            self._tr_data.f.v.r_data.eq((word_s >> (sel_r * 8))[0:8]),
        ]
