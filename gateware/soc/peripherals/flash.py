#!/usr/bin/env python3
#
# Config flash: the #590 core at SCK 190 MHz behind XIP, CSRs and a 4 KiB buffer (#593).
# SPDX-License-Identifier: BSD-3-Clause

"""Config flash peripheral. Map, codes, errors, bounds: docs/chips/flash-soc-peripheral.md.

- `sync`: CSRs, the XIP window, the buffer's CPU port, the op sequencer and calibration
  (`_Engine`), PHASESTEP.
- `flash` = PLL B CLKOP: one transaction at a time (`_TxUnit`: the #590 TxEngine and the
  receive sinks), pin registers; `flash_cap`/`flash_capn` = CLKOS (capture, runtime phase);
  MCLK = CLKOS2 (+180 degrees) through a DCCA gate into USRMCLK.
- Crossing per transaction: operands held static in `sync`, a start toggle, a done toggle
  back; results static in `flash` once done toggles. XIP words cross by AsyncFIFO.
- Calibration after lock: probe every (xfer, lat, phase) with 9Fh, place, then a 0Bh
  reference and every point again with EBh against it. Placement is by probing; the
  step count only says where to go, and each placement is verified by probing again.
"""

import importlib.util
import sys
from pathlib import Path

from amaranth             import (Cat, ClockDomain, ClockSignal, Const, DomainRenamer,
                                  Elaboratable, Instance, Module, Mux, ResetInserter,
                                  ResetSignal, Signal)
from amaranth.lib         import memory, wiring
from amaranth.lib.cdc     import FFSynchronizer
from amaranth.lib.fifo    import AsyncFIFO
from amaranth.lib.wiring  import In, Out
from amaranth_soc         import csr, wishbone
from amaranth_soc.memory  import MemoryMap

_BASELINE = Path(__file__).resolve().parents[2] / "flash_baseline"
if str(_BASELINE) not in sys.path:
    sys.path.insert(0, str(_BASELINE))
from spiflash import (MBYTE, POLL, READ_MODES, W1, WREN, FlashPHY,  # noqa: E402
                      FlashPins, TxEngine, desc)

_spec = importlib.util.spec_from_file_location("flash_baseline_top", _BASELINE / "top.py")
_baseline_top = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_baseline_top)
BaselinePLL, solve_flash_pll = _baseline_top._PLL, _baseline_top.solve_pll

__all__ = ["Flash", "SCK_MHZ", "xip_wait_bound"]

SCK_MHZ = 190.0                    # #590 decision (Dan, 2026-11-03)
FLASH_BYTES = 4 << 20
LINE_BYTES, LINE_WORDS = 64, 16    # VexiiRiscv L1 line (top.CACHE_SETS geometry)
BUF_BYTES = 4096
PAGE_BYTES = 256
APPLET_ID = 0x464C5332             # "FLS2"
JEDEC_ID = 0xEF4016                # W25Q32JV-IQ

# Calibration: 64 B reference at CAL_ADDR (reset 0: the bitstream header, ASCII + FF/00).
CAL_LEN = 64
LAT_ROWS = 16                      # TxEngine LAT_MAX + 1
VERIFY_N = 4

EBH = READ_MODES["EBh 1-4-4"]
REF_READ = READ_MODES["0Bh 1-1-1"]
JEDEC = desc(0x9F, dw=W1, read=True)
SR = {1: desc(0x05, dw=W1, read=True), 2: desc(0x35, dw=W1, read=True),
      3: desc(0x15, dw=W1, read=True)}
PP = desc(0x02, abits=24, dw=W1)
SE = desc(0x20, abits=24)
BE = desc(0xD8, abits=24)
# 8 CLK of 1s on 4 lines (ends continuous read, M5-4 != 10), QPI exit (FFh 4-wide), then
# 66h+99h 4-wide (QPI) and 1-wide (SPI). Each is ignored by a part not in that mode.
RESET_SEQ = (desc(0xFF, cmd_w4=True, abits=24, aw=2), desc(0xFF, cmd_w4=True),
             desc(0x66, cmd_w4=True), desc(0x99, cmd_w4=True), desc(0x66), desc(0x99))

(OP_READ, OP_PROGRAM, OP_ERASE_4K, OP_ERASE_64K, OP_JEDEC, OP_STATUS, OP_WREN,
 OP_CALIBRATE, OP_RESET) = range(1, 10)
OP_MAX = OP_RESET

(CODE_OK, CODE_BUSY, CODE_ARG, CODE_NOT_READY, CODE_UNCAL, CODE_PROTECT, CODE_WEL,
 CODE_WDOG, CODE_NOT_STARTED, CODE_CAL_NONE, CODE_ABORT, CODE_CAL_DATA,
 CODE_CAL_VERIFY) = range(13)

(ERR_OP, ERR_WDOG, ERR_XIP_UNCAL, ERR_XIP_BUSY, ERR_XIP_WRITE, ERR_PLL_LOST,
 ERR_PLL_NOLOCK, ERR_CAL, ERR_NOT_STARTED, ERR_XIP_FILL) = range(10)
N_ERR = 10

SINK_NONE, SINK_RES, SINK_BUF, SINK_FIFO, SINK_CMP, SINK_REF, SINK_POLL = range(7)
RET_SEQ, RET_CAL, RET_RST, RET_XIP = range(4)

# W25Q32JV rev G 9.6, maxima: tSHSL1 10 ns (after reads), tSHSL2 50 ns (after writes),
# tRST 30 us, tPP 3 ms, tSE 400 ms, tBE2 2000 ms. Every bound below is 1.25x of one.
T_SHSL1_NS, T_SHSL2_NS = 10, 50
T_RST_S, T_PP_S, T_SE_S, T_BE2_S = 30e-6, 3e-3, 400e-3, 2.0
# Transaction cycles outside the data phase, worst case (spiflash.TxEngine): start 1, LOAD 1,
# SETUP 1, CMD 8, ADDR 24 (x1), DUMMY 8, TAIL 1, HOLD 1, GAP max(CSH_W, LAT_MAX + 2) 17, done 1.
TX_OVERHEAD = 64
# The per-transaction crossing, sync cycles: start toggle 2 FF + edge into `flash`, done
# toggle 2 FF + edge back, ISSUE 1; <= 8, x1.25.
CROSS_SYNC = 10
PLL_LOCK_S = 1.25 * 15e-3          # tLOCK2 15 ms (DS-02012)
CK_WINDOW_BITS = 19                # flash cycles per 2**19 periods of the 60 MHz oscillator
# PHASESTEP, sync cycles: low 2, high 2 (33 ns each at 60 MHz, >= 4 VCO cycles, TN-02200
# 18.4.1; the #590 baseline's pulse), then 1 us before the next probe.
PS_LOW, PS_HIGH, PS_SETTLE = 2, 2, 60


def _ceil(x):
    return int(-(-x // 1))


def xip_wait_bound(sync_mhz, sck_mhz=SCK_MHZ):
    """The longest a healthy XIP beat waits, in sync cycles, x1.25.

    Worst case: the rest of an abandoned fill drains, then a new fill delivers word 15.
    Each fill <= TX_OVERHEAD + 128 data + LAT 15 + 2 flash cycles plus its crossing; and
    the FIFO crossing (4 sync).
    """
    fill = (TX_OVERHEAD + 2 * LINE_BYTES + 15 + 2) * sync_mhz / sck_mhz + CROSS_SYNC
    return _ceil(1.25 * (2 * fill + 4))


def map_offsets(n_steps):
    """Buffer offsets of the 9Fh map, the EBh map and the reference."""
    size = 2 * LAT_ROWS * n_steps // 8
    return 0, size, 2 * size


def _rw(width, init=0):
    return csr.Register({"v": csr.Field(csr.action.RW, width, init=init)}, access="rw")


def _ro(width):
    return csr.Register({"v": csr.Field(csr.action.R, width)}, access="r")


class _TxUnit(Elaboratable):
    """One transaction per `start` pulse, operands static; the receive sinks. In `flash`."""

    def __init__(self):
        self.pins = FlashPins()
        self.start, self.done = Signal(), Signal()
        self.desc, self.addr, self.length = Signal(32), Signal(32), Signal(32)
        self.csh, self.lat, self.sink, self.ptr = Signal(4), Signal(4), Signal(3), Signal(12)
        # results, static once `done` pulses
        self.result, self.cmp_errs, self.cmp_n = Signal(24), Signal(8), Signal(8)
        self.nib_or, self.nib_and = Signal(4), Signal(4)
        self.saw_busy, self.ovf = Signal(), Signal()
        self.fifo_w_data, self.fifo_w_en, self.fifo_w_rdy = Signal(32), Signal(), Signal()
        # buffer byte port: read data one cycle after the address
        self.buf_addr, self.buf_rdata = Signal(12), Signal(8)
        self.buf_wdata, self.buf_we = Signal(8), Signal()

    def elaborate(self, platform):
        m = Module()
        s = m.d.sync
        m.submodules.tx = tx = TxEngine(self.pins)
        m.d.comb += [tx.lat.eq(self.lat), tx.half.eq(0), tx.desc.eq(self.desc),
                     tx.addr.eq(self.addr), tx.length.eq(self.length),
                     tx.csh.eq(self.csh), tx.start.eq(self.start), self.done.eq(tx.done)]
        rptr, wptr = Signal(12), Signal(12)
        word, wb = Signal(24), Signal(2)
        with m.If(self.start):
            s += [rptr.eq(self.ptr), wptr.eq(self.ptr), self.result.eq(0), wb.eq(0),
                  self.cmp_errs.eq(0), self.cmp_n.eq(0), self.nib_or.eq(0),
                  self.nib_and.eq(0xF), self.saw_busy.eq(0), self.ovf.eq(0)]
        got = tx.rx_byte
        m.d.comb += [self.buf_addr.eq(Mux(self.buf_we, wptr, rptr)),
                     tx.wbyte.eq(self.buf_rdata)]
        with m.If(tx.wnext):
            s += rptr.eq(rptr + 1)
        with m.If(tx.rx_valid):
            with m.Switch(self.sink):
                with m.Case(SINK_RES):
                    s += self.result.eq(Cat(got, self.result[:16]))
                with m.Case(SINK_BUF, SINK_REF):
                    m.d.comb += [self.buf_we.eq(1), self.buf_wdata.eq(got)]
                    s += [wptr.eq(wptr + 1),
                          self.nib_or.eq(self.nib_or | got[0:4] | got[4:8]),
                          self.nib_and.eq(self.nib_and & got[0:4] & got[4:8])]
                with m.Case(SINK_FIFO):
                    s += wb.eq(wb + 1)
                    with m.If(wb == 3):
                        m.d.comb += [self.fifo_w_en.eq(1), self.fifo_w_data.eq(Cat(word, got))]
                        with m.If(~self.fifo_w_rdy):
                            s += self.ovf.eq(1)
                    with m.Else():
                        s += word.eq(Cat(word[8:24], got))
                with m.Case(SINK_CMP):
                    s += [self.cmp_n.eq(self.cmp_n + 1), rptr.eq(rptr + 1)]
                    with m.If((got != self.buf_rdata) & (self.cmp_errs != 0xFF)):
                        s += self.cmp_errs.eq(self.cmp_errs + 1)
                with m.Case(SINK_POLL):
                    s += self.result.eq(got)
                    with m.If(got[0]):
                        s += self.saw_busy.eq(1)
        return m


class _Engine(Elaboratable):
    """Op sequencer, calibration and XIP fills, in `sync`; transactions through `_TxUnit`."""

    def __init__(self, *, sync_mhz, sck_mhz, auto_calibrate, busy_scale, n_steps,
                 lat_rows=LAT_ROWS):
        self.auto_calibrate, self.lat_rows = auto_calibrate, lat_rows
        self.n_steps, self.pb = n_steps, n_steps.bit_length() - 1
        hz = sync_mhz * 1e6
        self.csh_r = _ceil(T_SHSL1_NS * sck_mhz / 1000)
        self.csh_w = _ceil(T_SHSL2_NS * sck_mhz / 1000)
        # flash cycles -> sync cycles, x1.25, as (x * k_num) >> 8
        self.k_num = _ceil(256 * 1.25 * sync_mhz / sck_mhz)
        self.lim_rst = _ceil(1.25 * T_RST_S * hz * busy_scale) + 8
        self.lim_busy = {k: _ceil(1.25 * t * hz * busy_scale) + 2 * CROSS_SYNC + TX_OVERHEAD
                         for k, t in ((OP_PROGRAM, T_PP_S), (OP_ERASE_4K, T_SE_S),
                                      (OP_ERASE_64K, T_BE2_S))}
        # the transaction unit: operands (static from `u_start` to `u_done`) and results
        self.u_start, self.u_done = Signal(), Signal()     # toggle out, pulse in
        self.u_desc, self.u_addr, self.u_len = Signal(32), Signal(32), Signal(32)
        self.u_csh, self.u_lat, self.u_sink, self.u_ptr = Signal(4), Signal(4), Signal(3), Signal(12)
        self.xfer = Signal()
        self.u_rst = Signal()                              # level: reset the unit
        self.u_result, self.u_cmp_errs, self.u_cmp_n = Signal(24), Signal(8), Signal(8)
        self.u_nib_or, self.u_nib_and = Signal(4), Signal(4)
        self.u_saw_busy, self.u_ovf = Signal(), Signal()
        # command (static while `start` is pending)
        self.start = Signal()
        self.o_op, self.o_addr, self.o_buf = Signal(4), Signal(22), Signal(12)
        self.o_len, self.o_cal_addr = Signal(13), Signal(22)
        # XIP fill: line index static while `x_start` is pending
        self.x_start, self.x_line, self.x_err = Signal(), Signal(16), Signal()
        # calibration maps into the buffer, through its sync port
        self.map_we, self.map_addr, self.map_data = Signal(), Signal(12), Signal(8)
        # PHASESTEP stepper (outside this module and its resets)
        self.step_req, self.step_busy, self.pos = Signal(), Signal(), Signal(self.pb)
        # results, static once `done` pulses
        self.done = Signal()
        self.r_code, self.r_op, self.r_cmd = Signal(4), Signal(4), Signal()
        self.r_result, self.r_busy = Signal(24), Signal(29)
        self.r_state = Signal(16)
        self.cal = Signal(32)
        self.ready, self.init_done, self.busy = Signal(), Signal(), Signal()
        self.state_o = Signal(4)
        self.dbg_e = Signal(28)

    def elaborate(self, platform):
        m = Module()
        s = m.d.sync

        # ---- op state
        op_live, op_cmd = Signal(), Signal()
        d_op, step = Signal(4), Signal(3)
        cmd_pending, x_pending = Signal(), Signal()
        with m.If(self.start):
            s += cmd_pending.eq(1)
        with m.If(self.x_start):
            s += x_pending.eq(1)
        lat_r, xfer_r = Signal(4), Signal()
        m.d.comb += [self.u_lat.eq(lat_r), self.xfer.eq(xfer_r)]
        result = Signal(24)
        cmp_errs, cmp_n = Signal(8), Signal(8)
        nib_or, nib_and = Signal(4), Signal(4)
        saw_busy, ovf = Signal(), Signal()

        # ---- transaction issue and its watchdog (sync cycles)
        sink, ret = Signal(3), Signal(2)
        wd, wd_lim = Signal(29), Signal(29)
        x_desc, x_addr, x_len, x_csh, x_ptr = Signal(32), Signal(24), Signal(13), Signal(4), Signal(12)
        x_inf, x_w4, x_plim = Signal(), Signal(), Signal(29)
        u_busy = Signal()

        def issue(dsc, addr24, length, csh, snk, data_cycles=None, r=RET_SEQ, lim=None, ptr=0):
            dsc = Const(dsc, 32) if isinstance(dsc, int) else dsc
            s.__iadd__([x_desc.eq(dsc), x_addr.eq(addr24), x_csh.eq(csh), sink.eq(snk),
                        ret.eq(r), x_inf.eq(lim is not None), x_w4.eq(data_cycles == "x4"),
                        x_ptr.eq(ptr)])
            if lim is not None:
                s.__iadd__(x_plim.eq(lim))
            else:
                s.__iadd__(x_len.eq(length))
            m.next = "ISSUE"

        res_out = Signal()
        with m.If(res_out):
            s += res_out.eq(0)
            m.d.comb += self.done.eq(1)

        def complete(code, nxt="IDLE"):
            s.__iadd__([self.r_code.eq(code), self.r_op.eq(d_op), self.r_cmd.eq(op_cmd),
                        self.r_result.eq(result), res_out.eq(1), op_live.eq(0)])
            m.next = nxt

        def begin(op, is_cmd):
            s.__iadd__([d_op.eq(op), op_cmd.eq(is_cmd), op_live.eq(1), step.eq(0),
                        result.eq(0)])

        # ---- calibration state
        pb, n = self.pb, self.n_steps
        map_j, map_e, ref_off = map_offsets(n)
        c_pass, c_x, c_lat, c_ph = Signal(), Signal(), Signal(4), Signal(pb)
        pk, verifying, refread = Signal(), Signal(), Signal()
        c_ver = Signal(range(VERIFY_N + 1))
        row = Signal(n)
        run_s, run_l = Signal(4 + pb), Signal(6 + pb)
        best_s, best_l = Signal(4 + pb), Signal(6 + pb)
        best_x = Signal()
        seek_t, step_ret = Signal(pb), Signal(2)
        rb = Signal(4)
        cal_addr = Signal(22)
        after_rst = Signal()
        hold = Signal(5)
        state = Signal(4)

        def scan_start(pass_):
            s.__iadd__([c_pass.eq(pass_), c_x.eq(0), c_lat.eq(0), c_ph.eq(0), row.eq(0),
                        run_l.eq(0), best_l.eq(0), best_s.eq(0), best_x.eq(0),
                        verifying.eq(0), refread.eq(0), pk.eq(pass_), seek_t.eq(0)])
            m.next = "SEEK"

        def go_step(r):
            s.__iadd__(step_ret.eq(r))
            m.next = "STEP"

        STEP_SEEK, STEP_PROBE, STEP_ROW = 0, 1, 2
        tx_idle = ~u_busy

        with m.FSM() as fsm:
            # Unit reset (abort, watchdog): hold it 16 cycles; done pulses meanwhile are stale.
            with m.State("BOOT"):
                m.d.comb += [state.eq(0), self.u_rst.eq(1)]
                s += hold.eq(hold + 1)
                with m.If((hold == 31) & ~self.step_busy):
                    s += [rb.eq(0), after_rst.eq(1), self.init_done.eq(0), self.ready.eq(0),
                          hold.eq(0), u_busy.eq(0)]
                    m.next = "RST"

            with m.State("RST"):
                m.d.comb += state.eq(1)
                with m.If(tx_idle):
                    with m.Switch(rb):
                        for i, d in enumerate(RESET_SEQ):
                            with m.Case(i):
                                issue(d, 0xFFFFFF if i == 0 else 0, 0, self.csh_w, SINK_NONE,
                                      r=RET_RST)

            with m.State("RST_NEXT"):
                m.d.comb += state.eq(1)
                s += rb.eq(rb + 1)
                with m.If(rb == len(RESET_SEQ) - 1):
                    s += wd.eq(0)
                    m.next = "RST_WAIT"
                with m.Else():
                    m.next = "RST"

            with m.State("RST_WAIT"):
                m.d.comb += state.eq(2)
                s += wd.eq(wd + 1)
                with m.If(wd == self.lim_rst):
                    s += self.init_done.eq(1)
                    with m.If(after_rst & self.auto_calibrate):
                        s += cal_addr.eq(self.o_cal_addr)
                        begin(OP_CALIBRATE, 0)
                        m.next = "CAL_START"
                    with m.Elif(after_rst):
                        m.next = "IDLE"
                    with m.Else():
                        complete(CODE_OK)

            with m.State("IDLE"):
                m.d.comb += state.eq(3)
                with m.If(x_pending & self.ready):
                    s += x_pending.eq(0)
                    issue(EBH, Cat(Const(0, 6), self.x_line), LINE_BYTES, self.csh_r,
                          SINK_FIFO, "x4", r=RET_XIP)
                with m.Elif(x_pending):
                    s += x_pending.eq(0)
                    m.d.comb += self.x_err.eq(1)
                with m.Elif(cmd_pending):
                    s += [cmd_pending.eq(0), cal_addr.eq(self.o_cal_addr)]
                    begin(self.o_op, 1)
                    m.next = "SEQ"

            with m.State("XIP_DONE"):
                m.d.comb += state.eq(4)
                with m.If(ovf):
                    m.d.comb += self.x_err.eq(1)
                m.next = "IDLE"

            with m.State("SEQ"):
                m.d.comb += state.eq(5)
                with m.Switch(d_op):
                    with m.Case(OP_JEDEC):
                        with m.If(step == 0):
                            issue(JEDEC, 0, 3, self.csh_r, SINK_RES)
                        with m.Else():
                            complete(CODE_OK)
                    with m.Case(OP_STATUS):
                        with m.Switch(step):
                            for i, k in enumerate((3, 2, 1)):
                                with m.Case(i):
                                    issue(SR[k], 0, 1, self.csh_r, SINK_RES)
                            with m.Default():
                                complete(CODE_OK)
                    with m.Case(OP_WREN, OP_PROGRAM, OP_ERASE_4K, OP_ERASE_64K):
                        with m.Switch(step):
                            with m.Case(0):
                                issue(WREN, 0, 0, self.csh_w, SINK_NONE)
                            with m.Case(1):
                                issue(SR[1], 0, 1, self.csh_r, SINK_RES)
                            with m.Case(2):
                                with m.If(~result[1]):
                                    complete(CODE_WEL)
                                with m.Elif(d_op == OP_WREN):
                                    complete(CODE_OK)
                                with m.Elif(d_op == OP_PROGRAM):
                                    issue(PP, self.o_addr, self.o_len, self.csh_w, SINK_NONE,
                                          ptr=self.o_buf)
                                with m.Else():
                                    issue(Mux(d_op == OP_ERASE_4K, Const(SE, 32),
                                              Const(BE, 32)),
                                          self.o_addr, 0, self.csh_w, SINK_NONE)
                            with m.Case(3):
                                lim = Mux(d_op == OP_PROGRAM, self.lim_busy[OP_PROGRAM],
                                          Mux(d_op == OP_ERASE_4K,
                                              self.lim_busy[OP_ERASE_4K],
                                              self.lim_busy[OP_ERASE_64K]))
                                issue(POLL, 0, 0, self.csh_w, SINK_POLL, lim=lim)
                            with m.Default():
                                s += self.r_busy.eq(wd)
                                complete(Mux(saw_busy, CODE_OK, CODE_NOT_STARTED))
                    with m.Case(OP_READ):
                        with m.If(step == 0):
                            issue(EBH, self.o_addr, self.o_len, self.csh_r, SINK_BUF, "x4",
                                  ptr=self.o_buf)
                        with m.Else():
                            complete(CODE_OK)
                    with m.Case(OP_RESET):
                        s += [rb.eq(0), after_rst.eq(0), step.eq(0)]
                        m.next = "RST"
                    with m.Case(OP_CALIBRATE):
                        m.next = "CAL_START"
                    with m.Default():
                        complete(CODE_ARG)

            with m.State("ISSUE"):
                data = Mux(x_w4, x_len << 1, x_len << 3)
                s += [self.u_desc.eq(x_desc), self.u_csh.eq(x_csh), self.u_sink.eq(sink),
                      self.u_ptr.eq(x_ptr), self.u_len.eq(Mux(x_inf, 0xFFFFFFFF, x_len)),
                      self.u_addr.eq(Mux(x_desc[10:16] == 32, Cat(Const(MBYTE, 8), x_addr),
                                         x_addr)),
                      wd.eq(0), step.eq(step + 1),
                      wd_lim.eq(Mux(x_inf, x_plim,
                                    (((data + TX_OVERHEAD) * self.k_num) >> 8) + 2 * CROSS_SYNC))]
                m.next = "LAUNCH"

            with m.State("LAUNCH"):
                # operands registered last cycle: now toggle, so they are static at the unit
                m.d.comb += state.eq(6)
                s += [self.u_start.eq(~self.u_start), u_busy.eq(1)]
                m.next = "TX"

            with m.State("TX"):
                m.d.comb += state.eq(6)
                s += wd.eq(wd + 1)
                with m.If(self.u_done):
                    s += [u_busy.eq(0), cmp_errs.eq(self.u_cmp_errs), cmp_n.eq(self.u_cmp_n),
                          nib_or.eq(self.u_nib_or), nib_and.eq(self.u_nib_and),
                          saw_busy.eq(self.u_saw_busy), ovf.eq(self.u_ovf)]
                    with m.If(sink == SINK_POLL):
                        s += result.eq(self.u_result[:8])
                    with m.Elif((sink == SINK_RES) & (x_len == 1)):
                        s += result.eq(Cat(self.u_result[:8], result[:16]))
                    with m.Elif(sink == SINK_RES):
                        s += result.eq(self.u_result)
                    with m.Switch(ret):
                        with m.Case(RET_SEQ):
                            m.next = "SEQ"
                        with m.Case(RET_CAL):
                            m.next = "CAL_EVAL"
                        with m.Case(RET_RST):
                            m.next = "RST_NEXT"
                        with m.Case(RET_XIP):
                            m.next = "XIP_DONE"
                with m.Elif(wd >= wd_lim):
                    s += self.r_state.eq(Cat(Const(6, 4), ret, sink, Const(0, 7)))
                    m.next = "WDOG"

            with m.State("WDOG"):
                m.d.comb += state.eq(7)
                with m.If(ret == RET_XIP):
                    m.d.comb += self.x_err.eq(1)
                s += [self.ready.eq(0), self.init_done.eq(0), step.eq(0), hold.eq(0),
                      self.u_start.eq(0)]
                with m.If(op_live):
                    complete(CODE_WDOG, nxt="BOOT")
                with m.Else():
                    m.next = "BOOT"

            # ---- calibration
            with m.State("CAL_START"):
                m.d.comb += state.eq(8)
                s += [self.ready.eq(0), self.cal.eq(0)]
                scan_start(0)

            with m.State("SEEK"):
                m.d.comb += state.eq(9)
                with m.If(self.pos == seek_t):
                    m.next = "CAL_PROBE"
                with m.Else():
                    go_step(STEP_SEEK)

            with m.State("STEP"):
                m.d.comb += [state.eq(10), self.step_req.eq(1)]
                m.next = "STEP_WAIT"

            with m.State("STEP_WAIT"):
                m.d.comb += state.eq(10)
                with m.If(~self.step_busy):
                    with m.Switch(step_ret):
                        with m.Case(STEP_SEEK):
                            m.next = "SEEK"
                        with m.Case(STEP_PROBE):
                            m.next = "CAL_PROBE"
                        with m.Case(STEP_ROW):
                            m.next = "CAL_NEXTROW"

            with m.State("CAL_PROBE"):
                m.d.comb += state.eq(11)
                with m.If(~verifying):
                    s += [lat_r.eq(c_lat), xfer_r.eq(c_x)]
                with m.If(pk == 0):
                    s += result.eq(0)
                    issue(JEDEC, 0, 3, self.csh_r, SINK_RES, r=RET_CAL)
                with m.Else():
                    issue(EBH, cal_addr, CAL_LEN, self.csh_r, SINK_CMP, "x4", r=RET_CAL,
                          ptr=ref_off)

            with m.State("CAL_EVAL"):
                m.d.comb += state.eq(12)
                ok = Signal()
                m.d.comb += ok.eq(Mux(pk == 0, result[:24] == JEDEC_ID,
                                      (cmp_errs == 0) & (cmp_n == CAL_LEN)))
                idx = Cat(c_ph, c_lat)
                with m.If(refread):
                    with m.If((nib_or == 0xF) & (nib_and == 0)):
                        scan_start(1)
                    with m.Else():
                        complete(CODE_CAL_DATA)
                with m.Elif(verifying):
                    with m.If(~ok):
                        complete(CODE_CAL_VERIFY)
                    with m.Elif(c_ver == VERIFY_N - 1):
                        with m.If(c_pass == 0):
                            m.next = "CAL_REF"
                        with m.Else():
                            s += self.ready.eq(1)
                            complete(CODE_OK)
                    with m.Else():
                        s += [c_ver.eq(c_ver + 1), pk.eq(c_pass & ~pk)]
                        m.next = "CAL_PROBE"
                with m.Else():
                    s += row.eq(row | (ok.as_unsigned() << c_ph))
                    with m.If(ok):
                        s += run_l.eq(run_l + 1)
                        with m.If(run_l == 0):
                            s += run_s.eq(idx)
                        with m.If(run_l + 1 > best_l):
                            s += [best_l.eq(run_l + 1), best_x.eq(c_x),
                                  best_s.eq(Mux(run_l == 0, idx, run_s))]
                    with m.Else():
                        s += run_l.eq(0)
                    with m.If(c_ph == n - 1):
                        s += [self.map_addr.eq(Mux(c_pass, map_e, map_j)
                                               + (Cat(c_lat, c_x) << (pb - 3))), rb.eq(0)]
                        m.next = "CAL_ROW"
                    with m.Else():
                        s += c_ph.eq(c_ph + 1)
                        go_step(STEP_PROBE)

            with m.State("CAL_ROW"):
                m.d.comb += [state.eq(13), self.map_we.eq(1),
                             self.map_data.eq(row.word_select(rb[:pb - 3], 8))]
                s += [rb.eq(rb + 1), self.map_addr.eq(self.map_addr + 1)]
                with m.If(rb == n // 8 - 1):
                    s += [row.eq(0), c_ph.eq(0)]
                    go_step(STEP_ROW)

            with m.State("CAL_NEXTROW"):
                m.d.comb += state.eq(13)
                with m.If(c_lat == self.lat_rows - 1):
                    s += [c_lat.eq(0), run_l.eq(0)]
                    with m.If(c_x):
                        m.next = "CAL_PLACE"
                    with m.Else():
                        s += c_x.eq(1)
                        m.next = "CAL_PROBE"
                with m.Else():
                    s += c_lat.eq(c_lat + 1)
                    m.next = "CAL_PROBE"

            with m.State("CAL_PLACE"):
                m.d.comb += state.eq(14)
                centre = Signal(4 + pb)
                m.d.comb += centre.eq(best_s + (best_l >> 1))
                width = Mux(best_l > 255, 255, best_l)[:8]
                info = Cat(centre, Const(0, 12 - pb), width, best_x, Const(0, 7))
                with m.If(best_l == 0):
                    complete(CODE_CAL_NONE)
                with m.Else():
                    s += [lat_r.eq(centre[pb:pb + 4]), xfer_r.eq(best_x),
                          seek_t.eq(centre[0:pb]), verifying.eq(1), c_ver.eq(0), pk.eq(c_pass)]
                    with m.If(c_pass):
                        s += self.cal.eq(info | (1 << 31))
                    m.next = "SEEK"

            with m.State("CAL_REF"):
                m.d.comb += state.eq(15)
                s += [refread.eq(1), verifying.eq(0)]
                issue(REF_READ, cal_addr, CAL_LEN, self.csh_r, SINK_REF, r=RET_CAL, ptr=ref_off)

        m.d.comb += [self.busy.eq(~fsm.ongoing("IDLE") | cmd_pending | x_pending),
                     self.state_o.eq(state)]
        w0, w1, last, r_or = Signal(8), Signal(8), Signal(4), Signal(8)
        with m.If(fsm.ongoing("CAL_PLACE")):
            with m.If(c_pass):
                s += w1.eq(Mux(best_l > 255, 255, best_l))
            with m.Else():
                s += w0.eq(Mux(best_l > 255, 255, best_l))
        with m.If(fsm.ongoing("CAL_EVAL") & ~verifying & ~refread & (c_pass == 0)):
            s += r_or.eq(r_or | result[16:24])
        with m.If(fsm.ongoing("CAL_START")):
            s += [r_or.eq(0), w0.eq(0), w1.eq(0)]
        with m.If(res_out):
            s += last.eq(self.r_code)
        m.d.comb += self.dbg_e.eq(Cat(last, w0, w1, r_or))
        return m


class Flash(wiring.Component):
    """CSR `bus`, `xip` (4 MiB, read-only, cached by the CPU), `buf` (4 KiB), `irq`.

    `sim=True` drops the PLL, MCLK gate and pads: the bench defines `flash`, `flash_cap`,
    `flash_capn`, drives `sim_locked` and `pad_i`, reads `pad_*` and `phasestep`.
    """

    def __init__(self, *, sync_mhz=60.0, sck_mhz=SCK_MHZ, sim=False, auto_calibrate=True,
                 busy_scale=1.0, pll_lock_s=PLL_LOCK_S, ck_window_bits=CK_WINDOW_BITS,
                 lat_rows=LAT_ROWS):
        self.lat_rows = lat_rows
        self.sync_mhz, self.sck_mhz, self.sim = float(sync_mhz), float(sck_mhz), sim
        self.auto_calibrate, self.busy_scale = auto_calibrate, busy_scale
        self.pll_lock_s, self.ck_window_bits = pll_lock_s, ck_window_bits
        solved = solve_flash_pll(self.sck_mhz)
        if solved is None or 8 * solved[2] not in (32, 64):
            raise ValueError(f"SCK {sck_mhz} MHz: PLL {solved}; calibration handles 32 or 64 "
                             f"phase steps per period (output divider 4 or 8)")
        self.n_steps = 8 * solved[2]
        self.pb = self.n_steps.bit_length() - 1
        self.sim_locked = Signal(init=1)
        self.phasestep = Signal(init=1)
        self.pad_o, self.pad_oe, self.pad_i = Signal(4), Signal(4), Signal(4)
        self.pad_cs, self.pad_ck_en = Signal(), Signal()

        r = self._regs = {}
        r["id"] = (0x00, _ro(32))
        r["status"] = (0x08, csr.Register({
            "busy": csr.Field(csr.action.R, 1), "ready": csr.Field(csr.action.R, 1),
            "locked": csr.Field(csr.action.R, 1), "init": csr.Field(csr.action.R, 1),
            "xip_active": csr.Field(csr.action.R, 1), "eng_busy": csr.Field(csr.action.R, 1),
            "_pad": csr.Field(csr.action.R, 2),
            "code": csr.Field(csr.action.R, 4), "op": csr.Field(csr.action.R, 4)},
            access="r"))
        r["cmd"] = (0x0c, csr.Register({"op": csr.Field(csr.action.W, 4)}, access="w"))
        r["ctrl"] = (0x0d, csr.Register({"abort": csr.Field(csr.action.W, 1)}, access="w"))
        r["events"] = (0x0e, csr.Register({"v": csr.Field(csr.action.RW1C, 2)}, access="rw"))
        r["irq_en"] = (0x0f, _rw(2))
        r["errors"] = (0x10, csr.Register({"v": csr.Field(csr.action.RW1C, N_ERR)},
                                          access="rw"))
        r["addr"] = (0x14, _rw(22))
        r["buf_addr"] = (0x18, _rw(12))
        r["len"] = (0x1a, _rw(13))
        r["result"] = (0x1c, _ro(32))
        r["write_lo"] = (0x20, _rw(11))        # 4 KiB sectors
        r["write_hi"] = (0x22, _rw(11))
        r["cal_addr"] = (0x28, _rw(22))
        r["cal"] = (0x2c, _ro(32))
        r["busy_cycles"] = (0x38, _ro(29))
        r["state"] = (0x3c, _ro(16))
        r["seq"] = (0x3e, _ro(16))
        r["ck_count"] = (0x40, _ro(22))
        r["fill_last"] = (0x48, _ro(16))
        r["fill_max"] = (0x4a, _ro(16))
        builder = csr.Builder(addr_width=7, data_width=8)
        for name, (offset, reg) in r.items():
            builder.add(name, reg, offset=offset)
        self._bridge = csr.Bridge(builder.as_memory_map())
        self._buf = [memory.Memory(shape=8, depth=BUF_BYTES // 4, init=[]) for _ in range(4)]

        super().__init__({
            "bus": In(csr.Signature(addr_width=7, data_width=8)),
            "xip": In(wishbone.Signature(addr_width=20, data_width=32, granularity=8,
                                         features={"err"})),
            "buf": In(wishbone.Signature(addr_width=10, data_width=32, granularity=8)),
            "irq": Out(1),
            "dbg": Out(32),
        })
        self.bus.memory_map = self._bridge.bus.memory_map
        xmap = MemoryMap(addr_width=22, data_width=8)
        xmap.add_resource(name=("xip",), size=FLASH_BYTES, resource=self)
        self.xip.memory_map = xmap
        bmap = MemoryMap(addr_width=12, data_width=8)
        bmap.add_resource(name=("buffer",), size=BUF_BYTES, resource=self._buf[0])
        self.buf.memory_map = bmap

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)
        r = {name: reg for name, (_off, reg) in self._regs.items()}

        def f(name):
            return r[name].f.v

        # ---- clocks
        locked = Signal()
        if self.sim:
            m.d.comb += locked.eq(self.sim_locked)
        else:
            m.submodules.pll = pll = BaselinePLL(mhz=self.sck_mhz, clki=ClockSignal("usb"))
            m.domains.flash = ClockDomain("flash")
            m.domains.flash_cap = ClockDomain("flash_cap", reset_less=True)
            m.domains.flash_capn = ClockDomain("flash_capn", clk_edge="neg", reset_less=True)
            m.d.comb += [locked.eq(pll.locked), pll.phasestep.eq(self.phasestep),
                         ClockSignal("flash").eq(pll.clk),
                         ClockSignal("flash_cap").eq(pll.clk_cap),
                         ClockSignal("flash_capn").eq(pll.clk_cap)]
            lock_f = Signal()
            m.submodules.lock_f = FFSynchronizer(locked, lock_f, o_domain="flash")
            m.d.comb += ResetSignal("flash").eq(~lock_f)
        locked_s = Signal()
        m.submodules.locked_ff = FFSynchronizer(locked, locked_s)

        # ---- abort: CTRL.abort resets the engine (sync) and, with it, the unit
        abort_w = r["ctrl"].f.abort.w_stb & r["ctrl"].f.abort.w_data

        # ---- engine (sync), transaction unit + PHY (flash), PHASESTEP stepper (sync)
        eng = _Engine(sync_mhz=self.sync_mhz, sck_mhz=self.sck_mhz,
                      auto_calibrate=self.auto_calibrate, busy_scale=self.busy_scale,
                      n_steps=self.n_steps, lat_rows=self.lat_rows)
        m.submodules.engine = ResetInserter(abort_w)(eng)
        unit = _TxUnit()
        u_rst = Signal()
        m.submodules.u_rst_ff = FFSynchronizer(eng.u_rst, u_rst, o_domain="flash")
        m.submodules.unit = DomainRenamer("flash")(ResetInserter(u_rst)(unit))
        phy = FlashPHY(unit.pins, pad_o=self.pad_o, pad_oe=self.pad_oe, pad_i=self.pad_i,
                       pad_cs=self.pad_cs, pad_ck_en=self.pad_ck_en)
        m.submodules.phy = DomainRenamer({"sync": "flash", "cap": "flash_cap",
                                          "capn": "flash_capn"})(phy)
        # Operands, lat and xfer are static from before the start toggle to the done toggle.
        m.d.comb += [phy.xfer.eq(eng.xfer), unit.desc.eq(eng.u_desc), unit.addr.eq(eng.u_addr),
                     unit.length.eq(eng.u_len), unit.csh.eq(eng.u_csh), unit.lat.eq(eng.u_lat),
                     unit.sink.eq(eng.u_sink), unit.ptr.eq(eng.u_ptr),
                     eng.u_result.eq(unit.result), eng.u_cmp_errs.eq(unit.cmp_errs),
                     eng.u_cmp_n.eq(unit.cmp_n), eng.u_nib_or.eq(unit.nib_or),
                     eng.u_nib_and.eq(unit.nib_and), eng.u_saw_busy.eq(unit.saw_busy),
                     eng.u_ovf.eq(unit.ovf)]
        st_h, st_p = Signal(), Signal()
        m.submodules.ustart_ff = FFSynchronizer(eng.u_start, st_h, o_domain="flash")
        m.d.flash += st_p.eq(st_h)
        m.d.comb += unit.start.eq(st_h ^ st_p)
        dn_t, dn_s, dn_p = Signal(), Signal(), Signal()
        with m.If(unit.done):
            m.d.flash += dn_t.eq(~dn_t)
        m.submodules.udone_ff = FFSynchronizer(dn_t, dn_s)
        m.d.sync += dn_p.eq(dn_s)
        m.d.comb += eng.u_done.eq(dn_s ^ dn_p)

        # Reset-less: the PLL keeps its phase across abort and lock resets, so its count does.
        ps_cnt = Signal(7, reset_less=True)
        ps_busy = Signal(reset_less=True)
        pos = Signal(self.pb, reset_less=True)
        ps_out = Signal(init=1, reset_less=True)
        m.d.comb += [self.phasestep.eq(ps_out), eng.step_busy.eq(ps_busy), eng.pos.eq(pos)]
        with m.If(~ps_busy):
            with m.If(eng.step_req):
                m.d.sync += [ps_busy.eq(1), ps_cnt.eq(0), ps_out.eq(0)]
        with m.Else():
            m.d.sync += ps_cnt.eq(ps_cnt + 1)
            with m.If(ps_cnt == PS_LOW - 1):
                m.d.sync += ps_out.eq(1)
            with m.If(ps_cnt == PS_LOW + PS_HIGH + PS_SETTLE - 1):
                m.d.sync += [ps_busy.eq(0), pos.eq(pos + 1)]

        if not self.sim:
            qspi = platform.request("qspi_flash", 0)
            for i in range(4):
                io = getattr(qspi, f"io{i}")
                m.d.comb += [io.o.eq(self.pad_o[i]), io.oe.eq(self.pad_oe[i]),
                             self.pad_i[i].eq(io.i)]
            m.d.comb += qspi.cs.o.eq(self.pad_cs)
            # MCLK gate: CE changes on `flash` edges = CLKOS2 falling, so no runt pulse.
            clk_mclk = Signal()
            m.submodules.mclk_gate = Instance("DCCA", i_CLKI=pll.clk_flash,
                                              i_CE=self.pad_ck_en, o_CLKO=clk_mclk)
            m.submodules.usrmclk = Instance("USRMCLK", i_USRMCLKI=clk_mclk, i_USRMCLKTS=0)

        # ---- buffer: CPU port (and calibration maps) in sync, unit byte port in flash
        wb = self.buf
        e_lane = Signal(2)
        m.d.flash += e_lane.eq(unit.buf_addr[0:2])
        e_rdata = []
        s_addr = Mux(eng.map_we, eng.map_addr[2:12], wb.adr)
        for lane, mem in enumerate(self._buf):
            m.submodules[f"buf{lane}"] = mem
            cpu_wp = mem.write_port(domain="sync")
            cpu_rp = mem.read_port(domain="sync", transparent_for=(cpu_wp,))
            e_wp = mem.write_port(domain="flash")
            e_rp = mem.read_port(domain="flash", transparent_for=(e_wp,))
            m.d.comb += [cpu_rp.addr.eq(s_addr), cpu_wp.addr.eq(s_addr),
                         wb.dat_r[8 * lane:8 * lane + 8].eq(cpu_rp.data),
                         cpu_wp.data.eq(Mux(eng.map_we, eng.map_data,
                                            wb.dat_w[8 * lane:8 * lane + 8])),
                         cpu_wp.en.eq(Mux(eng.map_we, eng.map_addr[0:2] == lane,
                                          wb.sel[lane] & wb.cyc & wb.stb & wb.we)),
                         e_rp.addr.eq(unit.buf_addr[2:12]), e_wp.addr.eq(unit.buf_addr[2:12]),
                         e_wp.data.eq(unit.buf_wdata),
                         e_wp.en.eq(unit.buf_we & (unit.buf_addr[0:2] == lane))]
            e_rdata.append(e_rp.data)
        m.d.comb += unit.buf_rdata.eq(Cat(*e_rdata).word_select(e_lane, 8))
        # A calibration map write takes the sync port for one cycle; the CPU's access waits.
        m.d.sync += wb.ack.eq(wb.cyc & wb.stb & ~wb.ack & ~eng.map_we)

        # ---- XIP words: flash -> sync
        fifo = AsyncFIFO(width=32, depth=LINE_WORDS, w_domain="flash", r_domain="sync")
        abort_f = Signal()
        m.submodules.abort_ff = FFSynchronizer(eng.u_rst, abort_f, o_domain="flash")
        m.submodules.xfifo = ResetInserter({"flash": abort_f})(fifo)
        m.d.comb += [fifo.w_data.eq(unit.fifo_w_data), fifo.w_en.eq(unit.fifo_w_en),
                     unit.fifo_w_rdy.eq(fifo.w_rdy)]

        # ---- events / errors
        ev_set, err_set = Signal(2), Signal(N_ERR)
        m.d.comb += [r["events"].f.v.set.eq(ev_set), r["errors"].f.v.set.eq(err_set),
                     ev_set[1].eq(err_set.any()),
                     self.irq.eq((r["events"].f.v.data & f("irq_en").data).any())]
        ready_s = Signal()
        m.d.comb += ready_s.eq(eng.ready & locked_s)

        # ---- commands
        status = r["status"].f
        cmd = r["cmd"].f.op
        pending = Signal()
        code_r, op_r = Signal(4), Signal(4)
        seq = Signal(16)
        x_active = Signal()
        m.d.comb += [status.code.r_data.eq(code_r), status.op.r_data.eq(op_r),
                     status.busy.r_data.eq(pending), status.ready.r_data.eq(ready_s),
                     status.locked.r_data.eq(locked_s), status.init.r_data.eq(eng.init_done),
                     status.xip_active.r_data.eq(x_active), status.eng_busy.r_data.eq(eng.busy),
                     f("id").r_data.eq(APPLET_ID), f("seq").r_data.eq(seq)]

        def finish(code, op):
            m.d.sync += [code_r.eq(code), op_r.eq(op), pending.eq(0), seq.eq(seq + 1)]
            m.d.comb += ev_set[0].eq(1)
            with m.If(code != CODE_OK):
                m.d.comb += err_set[ERR_OP].eq(1)

        w = cmd.w_data
        a_v, b_v, l_v = f("addr").data, f("buf_addr").data, f("len").data
        lo, hi = Cat(Const(0, 12), f("write_lo").data), Cat(Const(0, 12), f("write_hi").data)
        sector = Cat(Const(0, 12), a_v[12:22])
        block = Cat(Const(0, 16), a_v[16:22])
        span_lo = Mux(w == OP_ERASE_4K, sector, Mux(w == OP_ERASE_64K, block, a_v))
        span_n = Mux(w == OP_ERASE_4K, 4096, Mux(w == OP_ERASE_64K, 65536, l_v))
        writes = (w == OP_PROGRAM) | (w == OP_ERASE_4K) | (w == OP_ERASE_64K)
        protect = writes & ((span_lo < lo) | (span_lo + span_n > hi))
        arg_bad = ((w == 0) | (w > OP_MAX)
                   | (((w == OP_READ) | (w == OP_PROGRAM))
                      & ((l_v == 0) | (b_v + l_v > BUF_BYTES)))
                   | ((w == OP_READ) & (a_v + l_v > FLASH_BYTES))
                   | ((w == OP_PROGRAM) & ((l_v > PAGE_BYTES) | (a_v[0:8] + l_v > PAGE_BYTES))))
        needs_cal = (w != OP_CALIBRATE) & (w != OP_RESET)
        with m.If(cmd.w_stb):
            with m.If(pending):
                finish(CODE_BUSY, w)
            with m.Elif(~(eng.init_done & locked_s)):
                finish(CODE_NOT_READY, w)
            with m.Elif(arg_bad):
                finish(CODE_ARG, w)
            with m.Elif(protect):
                finish(CODE_PROTECT, w)
            with m.Elif(needs_cal & ~ready_s):
                finish(CODE_UNCAL, w)
            with m.Else():
                m.d.sync += [eng.o_op.eq(w), eng.o_addr.eq(a_v), eng.o_buf.eq(b_v),
                             eng.o_len.eq(l_v), pending.eq(1), op_r.eq(w), code_r.eq(CODE_OK)]
                m.d.comb += eng.start.eq(1)
        m.d.comb += eng.o_cal_addr.eq(f("cal_addr").data)

        vis = {k: Signal(32, name=f"vis_{k}") for k in ("result", "busy_cycles", "cal")}
        vis_state = Signal(16)
        for k, sig in vis.items():
            m.d.comb += f(k).r_data.eq(sig)
        m.d.comb += f("state").r_data.eq(vis_state)
        with m.If(eng.done):
            m.d.sync += [vis["result"].eq(eng.r_result), vis["busy_cycles"].eq(eng.r_busy),
                         vis["cal"].eq(eng.cal), vis_state.eq(eng.r_state)]
            with m.If(eng.r_cmd & pending):
                finish(eng.r_code, eng.r_op)
            with m.Elif(eng.r_code != CODE_OK):
                m.d.comb += err_set[ERR_OP].eq(1)
            for code, bit in ((CODE_WDOG, ERR_WDOG), (CODE_NOT_STARTED, ERR_NOT_STARTED),
                              (CODE_CAL_NONE, ERR_CAL), (CODE_CAL_DATA, ERR_CAL),
                              (CODE_CAL_VERIFY, ERR_CAL)):
                with m.If(eng.r_code == code):
                    m.d.comb += err_set[bit].eq(1)
        with m.If(abort_w & pending):
            finish(CODE_ABORT, op_r)

        # ---- XIP: in-order line streaming
        x = self.xip
        x_line, x_next, x_left = Signal(16), Signal(5), Signal(5)
        x_fault = Signal()
        m.d.comb += [x_active.eq(x_left != 0), x.dat_r.eq(fifo.r_data), eng.x_line.eq(x_line)]
        req = x.cyc & x.stb
        a_line, a_word = x.adr[4:20], x.adr[0:4]
        same = x_active & (a_line == x_line)
        f_cnt, f_last, f_max = Signal(16), Signal(16), Signal(16)
        m.d.comb += [f("fill_last").r_data.eq(f_last), f("fill_max").r_data.eq(f_max)]
        with m.If(x_active & (f_cnt != 0xFFFF)):
            m.d.sync += f_cnt.eq(f_cnt + 1)

        def pop():
            m.d.comb += fifo.r_en.eq(1)
            m.d.sync += x_left.eq(x_left - 1)
            with m.If(x_left == 1):
                m.d.sync += [f_last.eq(f_cnt), f_max.eq(Mux(f_cnt > f_max, f_cnt, f_max))]

        with m.If(eng.x_err | abort_w):
            m.d.sync += [x_left.eq(0), x_fault.eq(eng.x_err)]
            m.d.comb += err_set[ERR_XIP_FILL].eq(eng.x_err)
        with m.Elif(req & x.we):
            m.d.comb += [x.err.eq(1), err_set[ERR_XIP_WRITE].eq(1)]
        with m.Elif(req & x_fault):
            m.d.comb += x.err.eq(1)
            m.d.sync += x_fault.eq(0)
        with m.Elif(req & same & (a_word == x_next)):
            with m.If(fifo.r_rdy):
                m.d.comb += x.ack.eq(1)
                m.d.sync += x_next.eq(x_next + 1)
                pop()
        with m.Elif(req & same & (a_word > x_next)):
            with m.If(fifo.r_rdy):
                m.d.sync += x_next.eq(x_next + 1)
                pop()
        with m.Elif(req & x_active):
            with m.If(fifo.r_rdy):
                pop()
        with m.Elif(req):
            with m.If(~ready_s):
                m.d.comb += [x.err.eq(1), err_set[ERR_XIP_UNCAL].eq(1)]
            with m.Elif(pending):
                m.d.comb += [x.err.eq(1), err_set[ERR_XIP_BUSY].eq(1)]
            with m.Else():
                m.d.sync += [x_line.eq(a_line), x_next.eq(0), x_left.eq(LINE_WORDS),
                             f_cnt.eq(1)]
                m.d.comb += eng.x_start.eq(1)
        # Leftovers of a failed fill: the unit pushes nothing after `x_err`.
        with m.If(~x_active & fifo.r_rdy):
            m.d.comb += fifo.r_en.eq(1)

        m.d.comb += self.dbg.eq(Cat(eng.state_o, eng.dbg_e))

        # ---- PLL lock and SCK meter
        locked_prev, lock_seen = Signal(), Signal()
        m.d.sync += locked_prev.eq(locked_s)
        with m.If(locked_s):
            m.d.sync += lock_seen.eq(1)
        with m.If(locked_prev & ~locked_s):
            m.d.comb += err_set[ERR_PLL_LOST].eq(1)
        nolock_bound = int(self.pll_lock_s * self.sync_mhz * 1e6)
        nolock_cnt = Signal(range(nolock_bound + 2))
        with m.If(~lock_seen & (nolock_cnt <= nolock_bound)):
            m.d.sync += nolock_cnt.eq(nolock_cnt + 1)
            with m.If(nolock_cnt == nolock_bound):
                m.d.comb += err_set[ERR_PLL_NOLOCK].eq(1)

        bits = self.ck_window_bits
        ref_cnt = Signal(bits + 1)
        m.d.usb += ref_cnt.eq(ref_cnt + 1)
        gate, gate_prev = Signal(), Signal()
        m.submodules.gate_ff = FFSynchronizer(ref_cnt[-1], gate, o_domain="flash")
        f_count, ck_count_f, ck_upd = Signal(22), Signal(22), Signal()
        m.d.flash += gate_prev.eq(gate)
        m.d.comb += ck_upd.eq(~gate & gate_prev)
        with m.If(gate):
            m.d.flash += f_count.eq(f_count + 1)
        with m.Elif(gate_prev):
            m.d.flash += [ck_count_f.eq(f_count), f_count.eq(0)]
        ck_upd_d, ck_t, ck_ts, ck_tp = Signal(), Signal(), Signal(), Signal()
        m.d.flash += ck_upd_d.eq(ck_upd)
        with m.If(ck_upd_d):
            m.d.flash += ck_t.eq(~ck_t)
        m.submodules.ck_ff = FFSynchronizer(ck_t, ck_ts)
        m.d.sync += ck_tp.eq(ck_ts)
        ck_count = Signal(22)
        m.d.comb += f("ck_count").r_data.eq(ck_count)
        with m.If(ck_ts ^ ck_tp):
            m.d.sync += ck_count.eq(ck_count_f)
        return m
