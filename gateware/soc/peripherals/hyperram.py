#!/usr/bin/env python3
#
# HyperRAM: the #581 core at CK 180 behind CSRs, a 4 KiB window and COPY (#588).
# SPDX-License-Identifier: BSD-3-Clause

"""HyperRAM peripheral. Map, codes, errors and bounds: docs/chips/hyperram/soc-peripheral.md.

- `sync`: CSRs, the window's CPU port, command/result crossing, CK meter checks.
- `hr` (+ `hr90` for CK): `_Engine` -- core, PHY, the window's HyperRAM port, watchdogs.
- Crossings: toggle handshakes over payloads held static (sync latches a command before
  it toggles; the engine sets results a cycle before it toggles), or a FIFO.
- Core clients, one transaction at a time, round robin: command ops, the stream ring
  (#580), stager word pairs (JTAG ER1).
"""

import importlib.util
import sys
from pathlib import Path

from amaranth             import (Cat, ClockSignal, Const, DomainRenamer, Elaboratable,
                                  Module, Mux, ResetInserter, Signal)
from amaranth.lib         import memory, wiring
from amaranth.lib.cdc     import FFSynchronizer, ResetSynchronizer
from amaranth.lib.fifo    import AsyncFIFO, SyncFIFOBuffered
from amaranth.lib.wiring  import In, Out
from amaranth_soc         import csr, wishbone
from amaranth_soc.memory  import MemoryMap

_BASELINE = Path(__file__).resolve().parents[2] / "hyperram_baseline"
if str(_BASELINE) not in sys.path:
    sys.path.insert(0, str(_BASELINE))
from hyperbus import (CS_HIGH_CYCLES, ECP5HyperBusPHY, GUARD_CYCLES,  # noqa: E402
                      POST_GUARD_CYCLES, RESET_NS, TAIL_CYCLES, WAIT_DEFAULT,
                      HyperBusCore, PinIntent, max_burst_words)

_spec = importlib.util.spec_from_file_location("hyperram_baseline_top", _BASELINE / "top.py")
_baseline_top = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_baseline_top)
pattern, pattern_py, BaselinePLL = (_baseline_top.pattern, _baseline_top.pattern_py,
                                    _baseline_top._PLL)

__all__ = ["HyperRAM", "CK_MHZ"]

CK_MHZ = 180.0
WORDS = 1 << 22                     # 64 Mbit = 4 Mi 16-bit words
BYTES = 2 * WORDS
WINDOW_BYTES = 4096
# Calibration writes/reads the top 4 KiB only; staging lives at the bottom.
CAL_WORDS = 2048
CAL_BASE = WORDS - CAL_WORDS
CAL_SEED = 0xC41B
TAPS = 128
APPLET_ID = 0x48524D33          # "HRM3"

OP_COPY_OUT, OP_COPY_IN, OP_FILL, OP_TEST, OP_CALIBRATE = 1, 2, 3, 4, 5
OP_REG_READ, OP_REG_WRITE, OP_SET_DELAY, OP_DEVICE_RESET = 6, 7, 8, 9
FILL_CONST, FILL_RAMP, FILL_PATTERN = 0, 1, 2
TEST_WRITE, TEST_READ, TEST_BOTH = 1, 2, 3

(CODE_OK, CODE_BUSY, CODE_ARG, CODE_NOT_READY, CODE_UNCAL, CODE_WDOG, CODE_HANDSHAKE,
 CODE_REG_MISMATCH, CODE_CHECK, CODE_ABORT, CODE_NO_CLOCK, CODE_CAL_NONE) = \
    0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 13

(ERR_OP, ERR_WDOG, ERR_UNDERFLOW, ERR_STREAM_OVF, ERR_STREAM_UNF, ERR_HANDSHAKE,
 ERR_PLL_LOST, ERR_PLL_NOLOCK, ERR_CK_BAD, ERR_CK_DEAD, ERR_CAL_NONE,
 ERR_REG_MISMATCH, ERR_CHECK) = range(13)
N_ERR = 13

# ID0, ID1, CR0, CR1 word addresses in register space (A01-006 Table 2).
REG_ADDRS = (0x0000, 0x0001, 0x0800, 0x0801)

# Cycles around n data words (hyperbus.py): start 1, PRE guard, CA 3, TAIL, POST guard,
# CS# high, +1 for `done`. A read whose words never arrive also spends n cycles in FILL.
# XFER watchdog = 1.25 x (n [+ n read] + latency + this); expiry WDOG.
XFER_OVERHEAD = 1 + GUARD_CYCLES + 3 + TAIL_CYCLES + POST_GUARD_CYCLES + CS_HIGH_CYCLES + 1
# sync<->hr toggle round trip: 2 FF + edge each way <= 8 sync cycles; 1.25x.
HANDSHAKE_SYNC = 10
# Stream ring transactions, words: under the tCSM cap at CK 180, half the FIFO in words.
STREAM_BURST = 256
STREAM_FIFO = 512                   # 32-bit entries
# PLL lock: tLOCK2 15 ms max (ECP5 DS-02012, PLL timing); 1.25x.
PLL_LOCK_S = 1.25 * 15e-3
CK_WINDOW_BITS = 19                 # hr cycles counted over 2**19 60 MHz periods (#581)
CK_TOLERANCE = 2                    # one oscillator feeds both: +/-1 per gate edge

SRC_WIN, SRC_GEN, SRC_STREAM, SRC_PAIR = 0, 1, 2, 3
SNK_WIN, SNK_CHECK, SNK_STREAM, SNK_REG = 0, 1, 2, 3


def _rw(width, init=0):
    return csr.Register({"v": csr.Field(csr.action.RW, width, init=init)}, access="rw")


def _ro(width):
    return csr.Register({"v": csr.Field(csr.action.R, width)}, access="r")


def _swap(word):
    return Cat(word[8:16], word[0:8])


class _Engine(Elaboratable):
    """Everything on the HyperRAM side. Written in `sync`; renamed to `hr`."""

    RESULTS = ("cycles", "words", "xacts", "checked", "errs", "bad_index",
               "bad_got_want", "missing")

    def __init__(self, *, ck_mhz, pins, auto_calibrate, cal_words=CAL_WORDS):
        self.ck_mhz, self.pins = ck_mhz, pins
        self.cal_words, self.cal_base = cal_words, WORDS - cal_words
        # the window's HyperRAM side: one address per cycle (true dual port), byte lanes
        self.win_addr, self.win_rdata = Signal(10), Signal(32)
        self.win_wdata, self.win_we = Signal(32), Signal(4)
        self.auto_calibrate = auto_calibrate
        self.max_burst = max_burst_words(ck_mhz)
        # command (static while `start` is pending), see HyperRAM
        self.start = Signal()
        self.ack = Signal()
        self.o = {k: Signal(w, name=f"o_{k}") for k, w in (
            ("op", 4), ("hr", 23), ("win", 12), ("len", 24), ("fill_mode", 2),
            ("test_mode", 2), ("hang", 1), ("fill_value", 32), ("seed", 16), ("xor", 16),
            ("burst", 16), ("lat", 5), ("delay", 7), ("reg_sel", 2), ("reg_wdata", 16))}
        # results, static once `done` pulses
        self.done = Signal()
        self.r = {k: Signal(32, name=f"r_{k}") for k in self.RESULTS}
        self.r_bits, self.r_state = Signal(16), Signal(16)
        self.r_code, self.r_op, self.r_cmd = Signal(4), Signal(4), Signal()
        self.r_reg = Signal(16)
        self.cal_tap, self.cal_width, self.cal_valid = Signal(7), Signal(8), Signal()
        self.cal_map = Signal(TAPS)
        self.busy, self.ready, self.underflow = Signal(), Signal(), Signal()
        # PHY delay
        self.delay_tap, self.delay_load, self.delay_busy = Signal(7), Signal(), Signal()
        # pair client
        self.pair_start, self.pair_ack = Signal(), Signal()
        self.p_addr, self.p_data = Signal(22), Signal(32)
        # stream client (AsyncFIFO hr sides)
        self.stream_on = Signal()
        self.s_base, self.s_mask = Signal(22), Signal(22)
        self.s_level = Signal(23)
        self.sin_data, self.sin_rdy, self.sin_level, self.sin_en = \
            Signal(32), Signal(), Signal(range(STREAM_FIFO + 1)), Signal()
        self.sout_data, self.sout_en, self.sout_rdy, self.sout_level = \
            Signal(32), Signal(), Signal(), Signal(range(STREAM_FIFO + 1))

    def elaborate(self, platform):
        m = Module()
        s = m.d.sync
        o = self.o
        core = HyperBusCore(ck_mhz=self.ck_mhz, pins=self.pins, reg_write=True, masked=True)
        core_rst = Signal()
        m.submodules.core = ResetInserter(core_rst)(core)
        pf_clear = Signal()
        m.submodules.prefetch = pf = ResetInserter(pf_clear)(
            SyncFIFOBuffered(width=18, depth=8))

        # ---- op descriptor and progress
        op_live, op_cmd = Signal(), Signal()
        d_op, d_test, lat, hang = Signal(4), Signal(2), Signal(5, init=WAIT_DEFAULT), Signal()
        d_w0, d_nw, d_a0, d_b1 = Signal(22), Signal(23), Signal(), Signal()
        d_last = Signal(23)                          # d_nw - 1, registered
        d_q0, d_odd, d_ramp, d_seed = Signal(11), Signal(), Signal(8), Signal(16)
        wi, left, cur = Signal(23), Signal(23), Signal(22)
        both_phase, reg_phase = Signal(), Signal()
        burst_eff = Signal(16)
        m.d.comb += burst_eff.eq(Mux((o["burst"] == 0) | (o["burst"] > self.max_burst),
                                     self.max_burst, o["burst"] & ~1))
        n_cmd = Mux(left < burst_eff, left, burst_eff)
        c = {k: Signal(32, name=f"c_{k}") for k in self.RESULTS}
        c_bits = Signal(16)
        cmd_pending, pair_pending, prefer_other = Signal(), Signal(), Signal()
        with m.If(self.start):
            s += [cmd_pending.eq(1)]
            m.d.comb += self.ack.eq(1)
        with m.If(self.pair_start):
            s += pair_pending.eq(1)

        # ---- the transaction in flight
        x_src, x_snk, x_write, x_reg, x_cmd = Signal(2), Signal(2), Signal(), Signal(), Signal()
        x_addr, x_wi0, n_x = Signal(32), Signal(23), Signal(16)
        xi = Signal(16)
        wd_cnt, wd_lim = Signal(17), Signal(17)
        started = Signal()
        m.d.comb += [core.n.eq(n_x), core.write.eq(x_write), core.reg.eq(x_reg),
                     core.addr.eq(x_addr), core.wait.eq(lat)]

        # ---- stream ring
        s_wptr, s_rptr = Signal(22), Signal(22)
        m.d.comb += self.s_level.eq((s_wptr - s_rptr) & self.s_mask)
        with m.If(~self.stream_on):
            s += [s_wptr.eq(0), s_rptr.eq(0)]
        # registered: a cycle stale is harmless (pointers move at a transaction's start)
        stream_w_ok, stream_r_ok = Signal(), Signal()
        s += [stream_w_ok.eq(self.stream_on & (self.sin_level >= STREAM_BURST // 2)
                             & (self.s_level + STREAM_BURST <= self.s_mask)),
              stream_r_ok.eq(self.stream_on & (self.s_level >= STREAM_BURST)
                             & (self.sout_level + STREAM_BURST // 2 <= STREAM_FIFO))]
        other_ok = pair_pending | stream_w_ok | stream_r_ok

        # ---- window reader -> prefetch (COPY_OUT source)
        rd_active, rd_q, rd_reads = Signal(), Signal(11), Signal(24)
        rd_first, rd_valid, rd_half, rd_prev = Signal(), Signal(), Signal(), Signal(16)
        rd_issue = Signal()
        s += [rd_valid.eq(rd_issue), rd_half.eq(rd_q[0])]
        entry = self.win_rdata
        win_word = Signal(16)          # bus order: [15:8] window byte 2q, [7:0] byte 2q+1
        m.d.comb += win_word.eq(Mux(rd_half, Cat(entry[24:32], entry[16:24]),
                                    Cat(entry[8:16], entry[0:8])))
        m.d.comb += rd_issue.eq(rd_active & (rd_reads != 0) & (pf.level + rd_valid < 6))
        with m.If(rd_issue):
            s += [rd_q.eq(rd_q + 1), rd_reads.eq(rd_reads - 1)]
        re_wi = Signal(23)                           # index of the next word emitted
        with m.If(rd_valid):
            s += [rd_prev.eq(win_word), rd_first.eq(0)]
            with m.If(~(d_odd & rd_first)):
                s += re_wi.eq(re_wi + 1)
                m.d.comb += [pf.w_en.eq(1),
                             pf.w_data.eq(Cat(Mux(d_odd, Cat(win_word[8:16], rd_prev[0:8]),
                                                  win_word),
                                              (re_wi == d_last) & d_b1, (re_wi == 0) & d_a0))]

        # ---- generator: one word per cycle into the prefetch, from registers only
        g_active, g_w, g_wi, g_left, g_ramp = Signal(), Signal(22), Signal(23), Signal(23), Signal(8)
        fv = o["fill_value"]
        const_word = Mux(g_w[0], Cat(fv[24:32], fv[16:24]), Cat(fv[8:16], fv[0:8]))
        gen = Mux((d_op == OP_FILL) & (o["fill_mode"] == FILL_CONST), const_word,
                  Mux((d_op == OP_FILL) & (o["fill_mode"] == FILL_RAMP),
                      Cat((g_ramp + 1)[0:8], g_ramp), pattern(g_w) ^ d_seed))
        with m.If(g_active & (g_left != 0) & (pf.level < 6)):
            m.d.comb += [pf.w_en.eq(1),
                         pf.w_data.eq(Cat(gen, (g_wi == d_last) & d_b1, (g_wi == 0) & d_a0))]
            s += [g_w.eq(g_w + 1), g_wi.eq(g_wi + 1), g_left.eq(g_left - 1),
                  g_ramp.eq(g_ramp + 2)]

        s_half = Signal()
        pd = Signal(32)                  # pair payload, latched when served
        src = Signal(16)
        with m.Switch(x_src):
            with m.Case(SRC_WIN, SRC_GEN):
                m.d.comb += src.eq(Mux(x_reg, o["reg_wdata"], pf.r_data[0:16]))
            with m.Case(SRC_STREAM):
                m.d.comb += src.eq(Mux(s_half, self.sin_data[16:32], self.sin_data[0:16]))
            with m.Case(SRC_PAIR):
                m.d.comb += src.eq(Mux(xi[0], _swap(pd[16:32]), _swap(pd[0:16])))
        from_pf = x_cmd & ~x_reg & ((x_src == SRC_WIN) | (x_src == SRC_GEN))
        m.d.comb += [core.wdata.eq(src), core.wmask.eq(Mux(from_pf, pf.r_data[16:18], 0))]
        with m.If(core.wready):
            s += xi.eq(xi + 1)
            with m.If(from_pf):
                m.d.comb += pf.r_en.eq(1)
                with m.If(~pf.r_rdy):
                    s += self.underflow.eq(1)
            with m.If(x_src == SRC_STREAM):
                s += s_half.eq(~s_half)
                m.d.comb += self.sin_en.eq(s_half)

        # ---- sinks. Window writer and checker run from registers: stage A latches the
        # word with its address/masks/want, stage B writes or compares, stage C counts.
        got = core.rdata
        c_w, c_wi, c_q = Signal(22), Signal(23), Signal(11)
        a_v, a_snk, a_got, a_want = Signal(), Signal(2), Signal(16), Signal(16)
        a_w, a_ma, a_mb, a_q, a_first = Signal(22), Signal(), Signal(), Signal(11), Signal()
        b_v, b_diff, b_got, b_want, b_w = Signal(), Signal(16), Signal(16), Signal(16), Signal(22)
        prev_b, prev_b_ok = Signal(8), Signal()
        so_lo, so_half = Signal(16), Signal()
        flush_now = Signal()
        w_addr = Signal(10)

        def win_write(word_q, a_byte, b_byte, en_a, en_b):
            # window word q: byte 2q (A position) and 2q+1 (B position), little-endian entry
            m.d.comb += [w_addr.eq(word_q[1:11]),
                         self.win_wdata.eq(Cat(a_byte, b_byte, a_byte, b_byte)),
                         self.win_we.eq(Mux(word_q[0], Cat(Const(0, 2), en_a, en_b),
                                      Cat(en_a, en_b, Const(0, 2))))]

        s += [a_v.eq(0), b_v.eq(0)]
        with m.If(core.rvalid):
            s += xi.eq(xi + 1)
            with m.Switch(x_snk):
                with m.Case(SNK_WIN, SNK_CHECK):
                    s += [a_v.eq(1), a_snk.eq(x_snk), a_got.eq(got), a_w.eq(c_w), a_q.eq(c_q),
                          a_want.eq(pattern(c_w) ^ d_seed
                                    ^ Mux(d_op == OP_CALIBRATE, 0, o["xor"])),
                          a_ma.eq(x_cmd & (c_wi == 0) & d_a0),
                          a_mb.eq(x_cmd & (c_wi == d_last) & d_b1),
                          a_first.eq(c_wi == 0),
                          c_w.eq(c_w + 1), c_wi.eq(c_wi + 1), c_q.eq(c_q + 1)]
                with m.Case(SNK_STREAM):
                    s += so_half.eq(~so_half)
                    with m.If(~so_half):
                        s += so_lo.eq(got)
                    with m.Else():
                        m.d.comb += [self.sout_en.eq(1), self.sout_data.eq(Cat(so_lo, got))]
                with m.Case(SNK_REG):
                    s += self.r_reg.eq(got)
        with m.If(a_v & (a_snk == SNK_WIN)):
            with m.If(d_odd):
                # A -> B position of word q; the previous word's B -> A position of q
                win_write(a_q, prev_b, a_got[8:16], prev_b_ok & ~a_first, ~a_ma)
                s += [prev_b.eq(a_got[0:8]), prev_b_ok.eq(~a_mb)]
            with m.Else():
                win_write(a_q, a_got[8:16], a_got[0:8], ~a_ma, ~a_mb)
        with m.If(a_v & (a_snk == SNK_CHECK)):
            s += [b_v.eq(1), b_got.eq(a_got), b_want.eq(a_want), b_w.eq(a_w),
                  b_diff.eq((a_got ^ a_want) & Cat(Mux(a_mb, 0, 0xFF), Mux(a_ma, 0, 0xFF)))]
        with m.If(b_v):
            s += c["checked"].eq(c["checked"] + 1)
            with m.If(b_diff != 0):
                s += [c["errs"].eq(c["errs"] + 1), c_bits.eq(c_bits | b_diff)]
                with m.If(c["bad_index"] == 0xFFFFFFFF):
                    s += [c["bad_index"].eq(b_w), c["bad_got_want"].eq(Cat(b_want, b_got))]
        with m.If(flush_now):
            win_write((d_q0 + d_nw)[0:11], prev_b, Const(0, 8), prev_b_ok, 0)

        # ---- calibration
        cal_phase, cal_tap = Signal(3), Signal(8)
        scan, scan_i = Signal(TAPS), Signal(8)
        best_s, best_l, run_s, run_l = Signal(8), Signal(8), Signal(8), Signal(8)
        auto_pending = Signal(init=int(self.auto_calibrate))

        # ---- helpers
        def load_words(w0, nw, a0, b1):
            s.__iadd__([d_w0.eq(w0), d_nw.eq(nw), d_last.eq(nw - 1), d_a0.eq(a0),
                        d_b1.eq(b1), wi.eq(0), left.eq(nw), cur.eq(w0)])

        def start_generator(w0, nw, ramp):
            s.__iadd__([g_active.eq(1), g_w.eq(w0), g_wi.eq(0), g_left.eq(nw),
                        g_ramp.eq(ramp)])
            m.d.comb.__iadd__([pf_clear.eq(1)])

        def clear_counters():
            s.__iadd__([c[k].eq(0) for k in c] + [c["bad_index"].eq(0xFFFFFFFF),
                                                  c_bits.eq(0), both_phase.eq(0)])

        res_out = Signal()

        def complete(code):
            s.__iadd__([self.r_code.eq(code), self.r_op.eq(d_op), self.r_cmd.eq(op_cmd),
                        self.r_bits.eq(c_bits), res_out.eq(1), op_live.eq(0),
                        rd_active.eq(0), g_active.eq(0)] + [self.r[k].eq(c[k]) for k in c])

        with m.If(res_out):
            s += res_out.eq(0)
            m.d.comb += self.done.eq(1)

        def start_txn(src_, snk, write, reg, addr, n, is_cmd, wi0):
            s.__iadd__([x_src.eq(src_), x_snk.eq(snk), x_write.eq(write), x_reg.eq(reg),
                        x_addr.eq(addr), n_x.eq(n), x_cmd.eq(is_cmd), x_wi0.eq(wi0),
                        xi.eq(0), wd_cnt.eq(0), started.eq(0),
                        c_w.eq(addr), c_wi.eq(wi0), c_q.eq(d_q0 + wi0)])

        def serve_other():
            with m.If(pair_pending):
                start_txn(SRC_PAIR, SNK_WIN, 1, 0, self.p_addr & ~1, 2, 0, 0)
                s.__iadd__([pair_pending.eq(0), pd.eq(self.p_data)])
                m.d.comb += self.pair_ack.eq(1)
            with m.Elif(stream_w_ok):
                start_txn(SRC_STREAM, SNK_WIN, 1, 0, self.s_base + (s_wptr & self.s_mask),
                          STREAM_BURST, 0, 0)
                s.__iadd__([s_wptr.eq(s_wptr + STREAM_BURST), s_half.eq(0)])
            with m.Else():
                start_txn(SRC_GEN, SNK_STREAM, 0, 0, self.s_base + (s_rptr & self.s_mask),
                          STREAM_BURST, 0, 0)
                s.__iadd__([s_rptr.eq(s_rptr + STREAM_BURST), so_half.eq(0)])

        reg_addr = Mux(o["reg_sel"][1], Cat(o["reg_sel"][0], Const(0, 10), Const(1, 1)),
                       o["reg_sel"][0])
        lim_xfer = ((Mux(x_write, n_x, 2 * n_x) + lat + XFER_OVERHEAD) * 5) >> 2
        lim_delay = ((8 + 8 * self.delay_tap + 4) * 5) >> 2
        rst_cycles = int(RESET_NS * self.ck_mhz / 1000.0)
        lim_reset = (2 * rst_cycles + 8) * 5 // 4
        state = Signal(4)

        with m.If(op_live):
            s += c["cycles"].eq(c["cycles"] + 1)

        with m.FSM() as fsm:
            with m.State("WAIT_READY"):
                m.d.comb += state.eq(0)
                with m.If(core.ready):
                    s += self.ready.eq(1)
                    with m.If(auto_pending):
                        s += [auto_pending.eq(0), op_live.eq(1), op_cmd.eq(0),
                              d_op.eq(OP_CALIBRATE), cal_phase.eq(0), lat.eq(WAIT_DEFAULT)]
                        clear_counters()
                    m.next = "PICK"

            with m.State("PICK"):
                m.d.comb += state.eq(1)
                with m.If(op_live):
                    with m.If(prefer_other & other_ok):
                        s += prefer_other.eq(0)
                        serve_other()
                        m.next = "XFER"
                    with m.Else():
                        s += prefer_other.eq(1)
                        m.next = "OP"
                with m.Elif(cmd_pending):
                    w0 = o["hr"][1:23]
                    last = Signal(24)
                    m.d.comb += last.eq(o["hr"] + o["len"] - 1)
                    nw = last[1:24] - w0 + 1
                    e = Signal(12)
                    m.d.comb += e.eq(o["win"] - o["hr"][0])
                    s += [cmd_pending.eq(0), op_live.eq(1), op_cmd.eq(1), d_op.eq(o["op"]),
                          lat.eq(o["lat"]), hang.eq(o["hang"]), d_test.eq(o["test_mode"]),
                          d_ramp.eq(o["fill_value"][0:8] - o["hr"][0]), d_seed.eq(o["seed"]),
                          d_q0.eq(e[1:12]), d_odd.eq(e[0]), rd_q.eq(e[1:12]),
                          rd_reads.eq(nw + e[0]), rd_first.eq(1),
                          rd_active.eq(o["op"] == OP_COPY_OUT), prev_b_ok.eq(0), re_wi.eq(0),
                          cal_phase.eq(0), reg_phase.eq(0), g_active.eq(0)]
                    m.d.comb += pf_clear.eq(1)
                    clear_counters()
                    load_words(w0, nw, o["hr"][0], ~last[0])
                    with m.If((o["op"] == OP_FILL)
                              | ((o["op"] == OP_TEST) & o["test_mode"][0])):
                        start_generator(w0, nw, o["fill_value"][0:8] - o["hr"][0])
                    m.next = "OP"
                with m.Elif(other_ok):
                    serve_other()
                    m.next = "XFER"

            with m.State("OP"):
                m.d.comb += state.eq(2)
                with m.Switch(d_op):
                    with m.Case(OP_COPY_OUT, OP_FILL):
                        with m.If(left == 0):
                            complete(CODE_OK)
                            m.next = "PICK"
                        with m.Else():
                            start_txn(Mux(d_op == OP_COPY_OUT, SRC_WIN, SRC_GEN), SNK_WIN,
                                      1, 0, cur, n_cmd, 1, wi)
                            m.next = "XFER"
                    with m.Case(OP_COPY_IN):
                        with m.If(left == 0):
                            m.d.comb += flush_now.eq(d_odd)
                            complete(CODE_OK)
                            m.next = "PICK"
                        with m.Else():
                            start_txn(SRC_GEN, SNK_WIN, 0, 0, cur, n_cmd, 1, wi)
                            m.next = "XFER"
                    with m.Case(OP_TEST):
                        with m.If(left == 0):
                            bad = d_test[1] & ((c["errs"] != 0) | (c["missing"] != 0))
                            complete(Mux(bad, CODE_CHECK, CODE_OK))
                            m.next = "PICK"
                        with m.Else():
                            rd = (d_test == TEST_READ) | ((d_test == TEST_BOTH) & both_phase)
                            start_txn(SRC_GEN, SNK_CHECK, ~rd, 0, cur, n_cmd, 1, wi)
                            m.next = "XFER"
                    with m.Case(OP_REG_READ, OP_REG_WRITE):
                        with m.If(reg_phase):
                            with m.If((d_op == OP_REG_WRITE) & (self.r_reg != o["reg_wdata"])):
                                complete(CODE_REG_MISMATCH)
                            with m.Else():
                                complete(CODE_OK)
                            m.next = "PICK"
                        with m.Else():
                            # REG_WRITE writes, then this reads it back
                            start_txn(SRC_GEN, SNK_REG, (d_op == OP_REG_WRITE) & ~both_phase,
                                      1, reg_addr, 1, 1, 0)
                            m.next = "XFER"
                    with m.Case(OP_SET_DELAY):
                        s += self.delay_tap.eq(o["delay"])
                        m.next = "DELAY_LOAD"
                    with m.Case(OP_DEVICE_RESET):
                        m.d.comb += core.reset_req.eq(core.idle)
                        with m.If(core.idle):
                            s += [wd_cnt.eq(0), self.ready.eq(0)]
                            m.next = "RESET_WAIT"
                    with m.Case(OP_CALIBRATE):
                        with m.Switch(cal_phase):
                            with m.Case(0):
                                s += [d_test.eq(TEST_WRITE), cal_tap.eq(0),
                                      self.cal_map.eq(0), self.cal_valid.eq(0),
                                      d_seed.eq(CAL_SEED), cal_phase.eq(1)]
                                load_words(self.cal_base, self.cal_words, 0, 0)
                                start_generator(self.cal_base, self.cal_words, 0)
                            with m.Case(1):          # write the region
                                with m.If(left == 0):
                                    s += cal_phase.eq(2)
                                with m.Else():
                                    start_txn(SRC_GEN, SNK_CHECK, 1, 0, cur, n_cmd, 1, wi)
                                    m.next = "XFER"
                            with m.Case(2):          # next tap
                                with m.If(cal_tap == TAPS):
                                    s += [cal_phase.eq(4), scan.eq(self.cal_map), scan_i.eq(0),
                                          run_l.eq(0), best_l.eq(0), best_s.eq(0)]
                                with m.Else():
                                    s += [self.delay_tap.eq(cal_tap[0:7]), cal_phase.eq(3),
                                          d_test.eq(TEST_READ), c["errs"].eq(0),
                                          c["missing"].eq(0)]
                                    load_words(self.cal_base, self.cal_words, 0, 0)
                                    m.next = "DELAY_LOAD"
                            with m.Case(3):          # read and check at this tap
                                with m.If(left == 0):
                                    clean = (c["errs"] == 0) & (c["missing"] == 0)
                                    s += [self.cal_map.eq(self.cal_map
                                                          | (clean.as_unsigned() << cal_tap[0:7])),
                                          cal_tap.eq(cal_tap + 1), cal_phase.eq(2)]
                                with m.Else():
                                    start_txn(SRC_GEN, SNK_CHECK, 0, 0, cur, n_cmd, 1, wi)
                                    m.next = "XFER"
                            with m.Case(4):          # widest clean run
                                with m.If(scan_i == TAPS):
                                    s += cal_phase.eq(5)
                                with m.Else():
                                    s += [scan.eq(scan >> 1), scan_i.eq(scan_i + 1)]
                                    with m.If(scan[0]):
                                        with m.If(run_l == 0):
                                            s += run_s.eq(scan_i)
                                        s += run_l.eq(run_l + 1)
                                        with m.If(run_l + 1 > best_l):
                                            s += [best_l.eq(run_l + 1),
                                                  best_s.eq(Mux(run_l == 0, scan_i, run_s))]
                                    with m.Else():
                                        s += run_l.eq(0)
                            with m.Case(5):          # apply the centre
                                with m.If(best_l == 0):
                                    s += self.cal_width.eq(0)
                                    complete(CODE_CAL_NONE)
                                    m.next = "PICK"
                                with m.Else():
                                    centre = (best_s + (best_l >> 1))[0:7]
                                    s += [self.cal_tap.eq(centre), self.cal_width.eq(best_l),
                                          self.delay_tap.eq(centre), cal_phase.eq(6)]
                                    m.next = "DELAY_LOAD"
                            with m.Case(6):
                                s += self.cal_valid.eq(1)
                                complete(CODE_OK)
                                m.next = "PICK"
                    with m.Default():
                        complete(CODE_ARG)
                        m.next = "PICK"

            with m.State("XFER"):
                m.d.comb += state.eq(3)
                s += wd_cnt.eq(wd_cnt + 1)
                with m.If(wd_cnt == 0):
                    s += wd_lim.eq(lim_xfer)
                with m.If(core.idle & ~started):
                    m.d.comb += core.start.eq(1)
                    s += started.eq(1)
                with m.If(core.done & ~(hang & x_cmd)):
                    with m.If(x_cmd):
                        s += [c["xacts"].eq(c["xacts"] + 1), c["words"].eq(c["words"] + n_x)]
                        with m.If(~x_write):
                            s += c["missing"].eq(c["missing"] + core.missing)
                        with m.If(x_reg):
                            s += [reg_phase.eq(~x_write), both_phase.eq(x_write)]
                        with m.Elif((d_test == TEST_BOTH) & ~both_phase
                                    & ((d_op == OP_TEST))):
                            s += both_phase.eq(1)
                        with m.Else():
                            s += [left.eq(left - n_x), wi.eq(wi + n_x), cur.eq(cur + n_x),
                                  both_phase.eq(0)]
                    m.next = "DRAIN"
                with m.Elif((wd_cnt != 0) & (wd_cnt >= wd_lim)):
                    s += self.r_state.eq(Cat(Const(3, 4), core.fsm_state, x_src, x_snk,
                                             x_cmd, x_write))
                    m.next = "WDOG"

            with m.State("DRAIN"):
                # the window writer / checker stages finish before anything reads them
                with m.If(~a_v & ~b_v):
                    m.next = "PICK"

            with m.State("DELAY_LOAD"):
                m.d.comb += [state.eq(4), self.delay_load.eq(1)]
                s += [wd_cnt.eq(0), wd_lim.eq(lim_delay)]
                m.next = "DELAY_WAIT"

            with m.State("DELAY_WAIT"):
                m.d.comb += state.eq(5)
                s += wd_cnt.eq(wd_cnt + 1)
                with m.If((wd_cnt > 1) & ~self.delay_busy):
                    with m.If(d_op == OP_CALIBRATE):
                        m.next = "PICK"
                    with m.Else():
                        complete(CODE_OK)
                        m.next = "PICK"
                with m.Elif(wd_cnt >= wd_lim):
                    s += self.r_state.eq(Cat(Const(5, 4), Const(0, 4)))
                    m.next = "WDOG"

            with m.State("RESET_WAIT"):
                m.d.comb += state.eq(6)
                s += wd_cnt.eq(wd_cnt + 1)
                with m.If((wd_cnt > 2) & core.ready):
                    s += self.ready.eq(1)
                    complete(CODE_OK)
                    m.next = "PICK"
                with m.Elif(wd_cnt >= lim_reset):
                    s += self.r_state.eq(Cat(Const(6, 4), core.fsm_state))
                    complete(CODE_NOT_READY)
                    m.next = "WAIT_READY"

            with m.State("WDOG"):
                m.d.comb += [state.eq(7), core_rst.eq(1)]
                complete(CODE_WDOG)
                s += self.ready.eq(0)
                m.next = "WAIT_READY"

        # COPY_OUT reads the window for a whole op; every write is outside one
        m.d.comb += [self.busy.eq(op_live | cmd_pending),
                     self.win_addr.eq(Mux(rd_active, rd_q[1:11], w_addr))]
        return m


class HyperRAM(wiring.Component):
    """CSR `bus`, data `window` (Wishbone, 4 KiB, sync), `irq`.

    `stream_*` (sync) is the #580 ring client; `pair_*` (sync) the JTAG stager's
    write port. Tie off when unused. `sim=True` drops the PLL and PHY: the bench
    drives `hr`, `usb`, `sim_locked`, the PinIntent `pins` and reads `delay_tap`.
    """

    def __init__(self, *, ck_mhz=CK_MHZ, sync_mhz=60.0, sim=False,
                 ck_window_bits=CK_WINDOW_BITS, auto_calibrate=True, cal_words=CAL_WORDS, pll_lock_s=PLL_LOCK_S,
                 sim_fast_delay=False):
        self.ck_mhz, self.sync_mhz, self.sim = float(ck_mhz), float(sync_mhz), sim
        self.cal_words, self.pll_lock_s = cal_words, pll_lock_s
        self.sim_fast_delay = sim_fast_delay
        self.ck_window_bits, self.auto_calibrate = ck_window_bits, auto_calibrate
        self.max_burst = max_burst_words(self.ck_mhz)
        self.ck_expect = round(self.ck_mhz / 60.0 * (1 << ck_window_bits))
        self.pins = PinIntent()
        self.sim_locked = Signal(init=1)
        self.delay_tap = Signal(7)

        r = self._regs = {}
        r["id"] = (0x00, _ro(32))
        r["ck_khz"] = (0x04, _ro(32))
        r["status"] = (0x08, csr.Register({
            "busy": csr.Field(csr.action.R, 1), "ready": csr.Field(csr.action.R, 1),
            "locked": csr.Field(csr.action.R, 1), "cal_valid": csr.Field(csr.action.R, 1),
            "stream_on": csr.Field(csr.action.R, 1), "hr_busy": csr.Field(csr.action.R, 1),
            "_pad": csr.Field(csr.action.R, 2),
            "code": csr.Field(csr.action.R, 4), "op": csr.Field(csr.action.R, 4)},
            access="r"))
        r["cmd"] = (0x0c, csr.Register({"op": csr.Field(csr.action.W, 4)}, access="w"))
        r["ctrl"] = (0x0d, csr.Register({"abort": csr.Field(csr.action.W, 1)}, access="w"))
        r["events"] = (0x0e, csr.Register({"v": csr.Field(csr.action.RW1C, 2)}, access="rw"))
        r["irq_en"] = (0x0f, _rw(2))
        r["errors"] = (0x10, csr.Register({"v": csr.Field(csr.action.RW1C, N_ERR)},
                                          access="rw"))
        r["hr_addr"] = (0x14, _rw(23))
        r["win_addr"] = (0x18, _rw(12))
        r["len"] = (0x1c, _rw(24))
        r["fill_value"] = (0x20, _rw(32))
        r["mode"] = (0x24, csr.Register({
            "fill": csr.Field(csr.action.RW, 2), "test": csr.Field(csr.action.RW, 2),
            "inject_hang": csr.Field(csr.action.RW, 1)}, access="rw"))
        r["reg_sel"] = (0x25, _rw(2, init=2))
        r["latency"] = (0x26, _rw(5, init=WAIT_DEFAULT))
        r["delay"] = (0x27, _rw(7))
        r["seed"] = (0x28, _rw(16))
        r["xor"] = (0x2a, _rw(16))
        r["burst"] = (0x2c, _rw(16))
        r["reg_wdata"] = (0x2e, _rw(16, init=0x8F2F))
        r["reg_rdata"] = (0x30, _ro(16))
        r["cal"] = (0x34, csr.Register({
            "tap": csr.Field(csr.action.R, 7), "_pad": csr.Field(csr.action.R, 1),
            "width": csr.Field(csr.action.R, 8), "valid": csr.Field(csr.action.R, 1)},
            access="r"))
        for i in range(4):
            r[f"cal_map{i}"] = (0x38 + 4 * i, _ro(32))
        for i, name in enumerate(_Engine.RESULTS[:7]):
            r[name] = (0x48 + 4 * i, _ro(32))
        r["bad_bits"] = (0x64, _ro(16))
        r["state"] = (0x66, _ro(16))
        r["missing"] = (0x68, _ro(32))
        r["ck_count"] = (0x6c, _ro(32))
        r["stream_base"] = (0x70, _rw(22))
        r["stream_mask"] = (0x74, _rw(22, init=(1 << 20) - 1))
        r["stream_ctrl"] = (0x78, _rw(1))
        r["stream_level"] = (0x7c, _ro(23))

        builder = csr.Builder(addr_width=7, data_width=8)
        for name, (offset, reg) in r.items():
            builder.add(name, reg, offset=offset)
        self._bridge = csr.Bridge(builder.as_memory_map())
        # Four byte lanes, each a true-dual-port EBR: one address per side per cycle.
        self._win = [memory.Memory(shape=8, depth=WINDOW_BYTES // 4, init=[])
                     for _ in range(4)]

        super().__init__({
            "bus":    In(csr.Signature(addr_width=7, data_width=8)),
            "window": In(wishbone.Signature(addr_width=10, data_width=32, granularity=8)),
            "irq":    Out(1),
            "stream_w_data": In(32), "stream_w_en": In(1), "stream_w_rdy": Out(1),
            "stream_r_data": Out(32), "stream_r_en": In(1), "stream_r_rdy": Out(1),
            # Held until `pair_ack`. Even word address; each 16-bit half is two bytes,
            # low byte at the lower address.
            "pair_req": In(1), "pair_addr": In(32), "pair_data": In(32), "pair_ack": Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map
        wmap = MemoryMap(addr_width=12, data_width=8)
        wmap.add_resource(name=("window",), size=WINDOW_BYTES, resource=self._win[0])
        self.window.memory_map = wmap

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
            # as the PLL's own: hr held in reset while unlocked, released synchronously
            m.submodules.hr_rst = ResetSynchronizer(~self.sim_locked, domain="hr")
        else:
            m.submodules.pll = pll = BaselinePLL(domain="hr", mhz=self.ck_mhz,
                                                 clki=ClockSignal("usb"),
                                                 phase90_domain="hr90", sync_reset=True)
            m.d.comb += locked.eq(pll.locked)

        # ---- abort: CTRL.abort held 8 sync cycles, synchronised into hr
        abort_hold, abort_hr = Signal(4), Signal()
        with m.If(r["ctrl"].f.abort.w_stb & r["ctrl"].f.abort.w_data):
            m.d.sync += abort_hold.eq(8)
        with m.Elif(abort_hold != 0):
            m.d.sync += abort_hold.eq(abort_hold - 1)
        m.submodules.abort_ff = FFSynchronizer(abort_hold != 0, abort_hr, o_domain="hr")

        # ---- window: CPU port in sync, HyperRAM port in hr
        wb = self.window
        hr_side = []
        for lane, mem in enumerate(self._win):
            m.submodules[f"win{lane}"] = mem
            cpu_wp = mem.write_port(domain="sync")
            cpu_rp = mem.read_port(domain="sync", transparent_for=(cpu_wp,))
            hr_wp = mem.write_port(domain="hr")
            hr_rp = mem.read_port(domain="hr", transparent_for=(hr_wp,))
            m.d.comb += [cpu_rp.addr.eq(wb.adr), cpu_wp.addr.eq(wb.adr),
                         wb.dat_r[8 * lane:8 * lane + 8].eq(cpu_rp.data),
                         cpu_wp.data.eq(wb.dat_w[8 * lane:8 * lane + 8]),
                         cpu_wp.en.eq(wb.sel[lane] & wb.cyc & wb.stb & wb.we)]
            hr_side.append((hr_rp, hr_wp))
        m.d.sync += wb.ack.eq(wb.cyc & wb.stb & ~wb.ack)

        # ---- engine
        eng = _Engine(ck_mhz=self.ck_mhz, pins=self.pins,
                      auto_calibrate=self.auto_calibrate, cal_words=self.cal_words)
        for lane, (hr_rp, hr_wp) in enumerate(hr_side):
            m.d.comb += [hr_rp.addr.eq(eng.win_addr), hr_wp.addr.eq(eng.win_addr),
                         eng.win_rdata[8 * lane:8 * lane + 8].eq(hr_rp.data),
                         hr_wp.data.eq(eng.win_wdata[8 * lane:8 * lane + 8]),
                         hr_wp.en.eq(eng.win_we[lane])]
        m.submodules.engine = DomainRenamer("hr")(ResetInserter(abort_hr)(eng))
        m.d.comb += self.delay_tap.eq(eng.delay_tap)
        if not self.sim:
            m.submodules.phy = phy = ECP5HyperBusPHY(bus=platform.request("ram", 0),
                                                     pins=self.pins)
            m.d.comb += [phy.delay.eq(eng.delay_tap), phy.delay_load.eq(eng.delay_load),
                         eng.delay_busy.eq(phy.delay_busy), phy.delay_dir.eq(0)]
        else:
            # the DELAYF stepper's duration (LOADN 8 + 8 per tap), so its bound is exercised
            step = Signal(11)
            with m.If(eng.delay_load):
                m.d.hr += step.eq(4 if self.sim_fast_delay else 8 + 8 * eng.delay_tap)
            with m.Elif(step != 0):
                m.d.hr += step.eq(step - 1)
            m.d.comb += eng.delay_busy.eq(step != 0)

        # ---- stream FIFOs (not reset by abort: AsyncFIFO resets from its write side)
        m.submodules.stream_in = sin = AsyncFIFO(width=32, depth=STREAM_FIFO,
                                                 w_domain="sync", r_domain="hr")
        m.submodules.stream_out = sout = AsyncFIFO(width=32, depth=STREAM_FIFO,
                                                   w_domain="hr", r_domain="sync")
        stream_on = Signal()
        m.submodules.son_ff = FFSynchronizer(f("stream_ctrl").data[0], stream_on,
                                             o_domain="hr")
        m.d.comb += [sin.w_data.eq(self.stream_w_data), sin.w_en.eq(self.stream_w_en),
                     self.stream_w_rdy.eq(sin.w_rdy),
                     self.stream_r_data.eq(sout.r_data), sout.r_en.eq(self.stream_r_en),
                     self.stream_r_rdy.eq(sout.r_rdy),
                     eng.sin_data.eq(sin.r_data), eng.sin_rdy.eq(sin.r_rdy),
                     eng.sin_level.eq(sin.r_level), sin.r_en.eq(eng.sin_en),
                     sout.w_data.eq(eng.sout_data), sout.w_en.eq(eng.sout_en),
                     eng.sout_rdy.eq(sout.w_rdy), eng.sout_level.eq(sout.w_level),
                     eng.stream_on.eq(stream_on),
                     # quasi-static: written while the stream is off
                     eng.s_base.eq(f("stream_base").data), eng.s_mask.eq(f("stream_mask").data)]
        level_s = Signal(23)
        m.submodules.slv_ff = FFSynchronizer(eng.s_level, level_s)
        m.d.comb += f("stream_level").r_data.eq(level_s)

        # ---- events / errors
        ev_set, err_set = Signal(2), Signal(N_ERR)
        m.d.comb += [r["events"].f.v.set.eq(ev_set), r["errors"].f.v.set.eq(err_set),
                     ev_set[1].eq(err_set.any()),
                     self.irq.eq((r["events"].f.v.data & f("irq_en").data).any())]

        def hr_pulse(name, pulse):
            """hr pulse -> sync pulse, via a toggle (pulses >= 4 hr cycles apart)."""
            t, t_s, t_p = Signal(name=f"{name}_t"), Signal(name=f"{name}_ts"), Signal()
            with m.If(pulse):
                m.d.hr += t.eq(~t)
            m.submodules[f"{name}_ff"] = FFSynchronizer(t, t_s)
            m.d.sync += t_p.eq(t_s)
            return t_s ^ t_p

        def to_sync(name, sig):
            out = Signal.like(sig, name=f"{name}_s")
            m.submodules[f"{name}_ff"] = FFSynchronizer(sig, out)
            return out

        done_s = hr_pulse("done", eng.done)
        ack_s = hr_pulse("ack", eng.ack)
        und_s, und_p = to_sync("und", eng.underflow), Signal()
        m.d.sync += und_p.eq(und_s)
        with m.If(und_s & ~und_p):
            m.d.comb += err_set[ERR_UNDERFLOW].eq(1)
        ready_s, cal_valid_s = to_sync("ready", eng.ready), to_sync("calv", eng.cal_valid)
        hr_busy_s, locked_s = to_sync("hrbusy", eng.busy), to_sync("locked", locked)

        # ---- commands
        status = r["status"].f
        cmd = r["cmd"].f.op
        pending, awaiting = Signal(), Signal()
        hs_count = Signal(range(HANDSHAKE_SYNC + 1))
        code_r, op_r = Signal(4), Signal(4)
        start_t = Signal()
        m.d.comb += [status.code.r_data.eq(code_r), status.op.r_data.eq(op_r),
                     status.busy.r_data.eq(pending), status.ready.r_data.eq(ready_s & locked_s),
                     status.locked.r_data.eq(locked_s), status.cal_valid.r_data.eq(cal_valid_s),
                     status.stream_on.r_data.eq(f("stream_ctrl").data[0]),
                     status.hr_busy.r_data.eq(hr_busy_s),
                     f("id").r_data.eq(APPLET_ID),
                     f("ck_khz").r_data.eq(int(round(self.ck_mhz * 1000)))]
        start_h, start_p = Signal(), Signal()
        m.submodules.start_ff = FFSynchronizer(start_t, start_h, o_domain="hr")
        m.d.hr += start_p.eq(start_h)
        m.d.comb += eng.start.eq(start_h ^ start_p)

        def finish(code, op):
            m.d.sync += [code_r.eq(code), op_r.eq(op), pending.eq(0), awaiting.eq(0)]
            m.d.comb += ev_set[0].eq(1)
            with m.If(code != CODE_OK):
                m.d.comb += err_set[ERR_OP].eq(1)

        w = cmd.w_data
        mode = r["mode"].f
        hr_v, win_v, len_v = f("hr_addr").data, f("win_addr").data, f("len").data
        sized = (w == OP_COPY_OUT) | (w == OP_COPY_IN) | (w == OP_FILL) | (w == OP_TEST)
        copy = (w == OP_COPY_OUT) | (w == OP_COPY_IN)
        needs_cal = ((w == OP_COPY_IN) | (w == OP_REG_READ) | (w == OP_REG_WRITE)
                     | ((w == OP_TEST) & mode.test.data[1]))
        arg_bad = ((w == 0) | (w > OP_DEVICE_RESET)
                   | (sized & ((len_v == 0) | (hr_v + len_v > BYTES)))
                   | (copy & (win_v + len_v > WINDOW_BYTES))
                   | ((w == OP_TEST) & (mode.test.data == 0)))
        with m.If(cmd.w_stb):
            with m.If(pending | hr_busy_s):
                finish(CODE_BUSY, w)
            with m.Elif(~(ready_s & locked_s)):
                finish(CODE_NOT_READY, w)
            with m.Elif(arg_bad):
                finish(CODE_ARG, w)
            with m.Elif(needs_cal & ~cal_valid_s):
                finish(CODE_UNCAL, w)
            with m.Else():
                o = eng.o
                m.d.sync += [
                    o["op"].eq(w), o["hr"].eq(hr_v), o["win"].eq(win_v), o["len"].eq(len_v),
                    o["fill_mode"].eq(mode.fill.data), o["test_mode"].eq(mode.test.data),
                    o["hang"].eq(mode.inject_hang.data), o["fill_value"].eq(f("fill_value").data),
                    o["seed"].eq(f("seed").data), o["xor"].eq(f("xor").data),
                    o["burst"].eq(f("burst").data), o["lat"].eq(f("latency").data),
                    o["delay"].eq(f("delay").data), o["reg_sel"].eq(f("reg_sel").data),
                    o["reg_wdata"].eq(f("reg_wdata").data),
                    pending.eq(1), awaiting.eq(1), hs_count.eq(0), start_t.eq(~start_t),
                    op_r.eq(w), code_r.eq(CODE_OK)]

        with m.If(awaiting & ~cmd.w_stb):
            with m.If(ack_s):
                m.d.sync += awaiting.eq(0)
            with m.Elif(hs_count == HANDSHAKE_SYNC):
                finish(CODE_HANDSHAKE, op_r)
                m.d.comb += err_set[ERR_HANDSHAKE].eq(1)
            with m.Else():
                m.d.sync += hs_count.eq(hs_count + 1)

        # results: static in the engine since the cycle before `done`
        vis = {k: Signal(32, name=f"vis_{k}") for k in _Engine.RESULTS}
        vis_bits, vis_state, vis_reg = Signal(16), Signal(16), Signal(16)
        vis_cal, vis_map = Signal(17), Signal(TAPS)
        for k in vis:
            if k in r:
                m.d.comb += f(k).r_data.eq(vis[k])
        m.d.comb += [f("missing").r_data.eq(vis["missing"]), f("bad_bits").r_data.eq(vis_bits),
                     f("state").r_data.eq(vis_state), f("reg_rdata").r_data.eq(vis_reg),
                     r["cal"].f.tap.r_data.eq(vis_cal[0:7]),
                     r["cal"].f.width.r_data.eq(vis_cal[8:16]),
                     r["cal"].f.valid.r_data.eq(vis_cal[16])]
        for i in range(4):
            m.d.comb += f(f"cal_map{i}").r_data.eq(vis_map[32 * i:32 * (i + 1)])
        with m.If(done_s):
            m.d.sync += [vis_bits.eq(eng.r_bits), vis_state.eq(eng.r_state),
                         vis_reg.eq(eng.r_reg), vis_map.eq(eng.cal_map),
                         vis_cal.eq(Cat(eng.cal_tap, Const(0, 1), eng.cal_width, eng.cal_valid))]
            m.d.sync += [vis[k].eq(eng.r[k]) for k in vis]
            with m.If(eng.r_cmd & pending):
                finish(eng.r_code, eng.r_op)
            with m.Elif(eng.r_code != CODE_OK):
                m.d.comb += err_set[ERR_OP].eq(1)
            for code, bit in ((CODE_WDOG, ERR_WDOG), (CODE_CHECK, ERR_CHECK),
                              (CODE_CAL_NONE, ERR_CAL_NONE),
                              (CODE_REG_MISMATCH, ERR_REG_MISMATCH)):
                with m.If(eng.r_code == code):
                    m.d.comb += err_set[bit].eq(1)

        # ---- PLL lock and CK meter
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
        m.submodules.gate_ff = FFSynchronizer(ref_cnt[-1], gate, o_domain="hr")
        hr_cnt, ck_count_h, ck_upd = Signal(32), Signal(32), Signal()
        m.d.hr += gate_prev.eq(gate)
        m.d.comb += ck_upd.eq(~gate & gate_prev)
        with m.If(gate):
            m.d.hr += hr_cnt.eq(hr_cnt + 1)
        with m.Elif(gate_prev):
            m.d.hr += [ck_count_h.eq(hr_cnt), hr_cnt.eq(0)]
        ck_upd_d = Signal()
        m.d.hr += ck_upd_d.eq(ck_upd)               # count settles before the toggle
        ck_s = hr_pulse("ck", ck_upd_d)
        ck_count = Signal(32)
        m.d.comb += f("ck_count").r_data.eq(ck_count)
        # one update per 2**(bits+1) reference periods; 1.25x in sync cycles
        age_bound = int(1.25 * (1 << (bits + 1)) * self.sync_mhz / 60.0)
        ck_age = Signal(range(age_bound + 2))
        ck_dead = Signal()
        with m.If(ck_s):
            m.d.sync += [ck_count.eq(ck_count_h), ck_age.eq(0), ck_dead.eq(0)]
            with m.If((ck_count_h > self.ck_expect + CK_TOLERANCE)
                      | (ck_count_h + CK_TOLERANCE < self.ck_expect)):
                m.d.comb += err_set[ERR_CK_BAD].eq(1)
        with m.Elif(ck_age <= age_bound):
            m.d.sync += ck_age.eq(ck_age + 1)
            with m.If(ck_age == age_bound):
                m.d.sync += ck_dead.eq(1)
                m.d.comb += err_set[ERR_CK_DEAD].eq(1)
        # a command outstanding with no hr clock can never finish
        with m.If(pending & ck_dead & ~done_s & ~cmd.w_stb & ~awaiting):
            finish(CODE_NO_CLOCK, op_r)

        # stream FIFO misuse by the client
        with m.If(self.stream_w_en & ~sin.w_rdy):
            m.d.comb += err_set[ERR_STREAM_OVF].eq(1)
        with m.If(self.stream_r_en & ~sout.r_rdy):
            m.d.comb += err_set[ERR_STREAM_UNF].eq(1)

        # ---- pair client (stager): held request -> hr toggle -> hr ack toggle
        pair_t, pair_busy = Signal(), Signal()
        with m.If(self.pair_req & ~pair_busy):
            m.d.sync += [eng.p_addr.eq(self.pair_addr[0:22]), eng.p_data.eq(self.pair_data),
                         pair_busy.eq(1), pair_t.eq(~pair_t)]
        pair_h, pair_p = Signal(), Signal()
        m.submodules.pair_ff = FFSynchronizer(pair_t, pair_h, o_domain="hr")
        m.d.hr += pair_p.eq(pair_h)
        m.d.comb += eng.pair_start.eq(pair_h ^ pair_p)
        pair_ack_s = hr_pulse("pairack", eng.pair_ack)
        with m.If(pair_busy & pair_ack_s):
            m.d.sync += pair_busy.eq(0)
            m.d.comb += self.pair_ack.eq(1)
        return m
