#!/usr/bin/env python3
#
# Transaction framer + packet ring + record-aligned block feeder for the capture path.
# SPDX-License-Identifier: BSD-3-Clause

"""Packets in, one 16-bit-timestamped record per TRANSACTION out.

Record format, ts16 rollover rule, drop policy, integration: `docs/capture-compression.md` (stage 2).
Header: ts16 (2) | flags (1) | len (2); then payload.
"""

from functools import reduce
from operator import or_

from amaranth            import Array, C, Cat, Module, Mux, Signal
from amaranth.lib        import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from .codec import BLOCK_BYTES, byte_stream_signature

__all__ = ["HDR_BYTES", "KIND_TXN", "KIND_EVENT", "KIND_DROP", "KIND_WRAP", "SNAP_BYTES",
           "WRAP_AGE", "CaptureRing"]

HDR_BYTES  = 5
SNAP_BYTES = BLOCK_BYTES - HDR_BYTES      # a record always fits one block
WRAP_AGE   = 0x8000
EVENT_QUEUE = 4
KIND_TXN, KIND_EVENT, KIND_DROP, KIND_WRAP = range(4)


class CaptureRing(wiring.Component):
    def __init__(self, *, ring=2048, fifo_depth=16, lenq_depth=16, ts_shift=0, ts_init=0,
                 txn_idle=512, flush_idle=1024, counter_bits=16):
        assert ring & (ring - 1) == 0 and fifo_depth & (fifo_depth - 1) == 0
        assert lenq_depth & (lenq_depth - 1) == 0 and ring >= 2 * BLOCK_BYTES // 2
        self._p = dict(ring=ring, fifo=fifo_depth, lenq=lenq_depth, shift=ts_shift,
                       init=ts_init, txn_idle=txn_idle, flush_idle=flush_idle)
        cb = counter_bits
        super().__init__({
            "pkt_data":  In(8), "pkt_valid": In(1), "pkt_last": In(1), "pkt_err": In(1),
            "ev_valid":  In(1), "ev_code":  In(8),
            "flush":     In(1),
            "source":    Out(byte_stream_signature()),
            "blk_ts":    Out(16), "blk_ts_valid": Out(1),
            "now":       Out(16),
            "ring_used": Out(range(ring + 1)),
            "n_pkt": Out(cb), "n_sof": Out(cb), "n_nak": Out(cb), "n_err": Out(cb),
            "n_dropped": Out(cb), "n_lost": Out(cb),
        })

    def elaborate(self, platform):
        p, m = self._p, Module()
        RING, FD, LD = p["ring"], p["fifo"], p["lenq"]
        RB, FB, LB = (RING - 1).bit_length(), (FD - 1).bit_length(), (LD - 1).bit_length()
        EB = (EVENT_QUEUE - 1).bit_length()
        PW = RB + 1

        # ---- time base
        now, tick = Signal(16, init=p["init"]), Signal()
        if p["shift"]:
            pre = Signal(p["shift"])
            m.d.comb += tick.eq(pre.all())
            m.d.sync += pre.eq(pre + 1)
        else:
            m.d.comb += tick.eq(1)
        with m.If(tick):
            m.d.sync += now.eq(now + 1)
        m.d.comb += self.now.eq(now)

        # ---- input FIFO: {ts16, first, err, last, data8}
        fmem = Memory(shape=27, depth=FD, init=[])
        m.submodules.fmem = fmem
        f_wr, f_rd = fmem.write_port(), fmem.read_port(domain="comb")
        f_w, f_r = Signal(FB + 1), Signal(FB + 1)
        f_empty = f_w == f_r
        f_full  = (f_w - f_r)[:FB + 1] == FD
        in_first, resync, f_clear = Signal(init=1), Signal(), Signal()
        push = self.pkt_valid & ~resync & ~f_full
        m.d.comb += [
            f_clear.eq(self.pkt_valid & ~resync & f_full),
            f_wr.addr.eq(f_w[:FB]),
            f_wr.data.eq(Cat(self.pkt_data, self.pkt_last, self.pkt_err, in_first, now)),
            f_wr.en.eq(push),
            f_rd.addr.eq(f_r[:FB]),
        ]
        with m.If(self.pkt_valid):
            m.d.sync += in_first.eq(self.pkt_last)
            with m.If(resync & self.pkt_last):
                m.d.sync += resync.eq(0)
        with m.If(push):
            m.d.sync += f_w.eq(f_w + 1)
        pop = Signal()
        with m.If(pop):
            m.d.sync += f_r.eq(f_r + 1)
        with m.If(f_clear):
            m.d.sync += [f_w.eq(0), f_r.eq(0), resync.eq(~self.pkt_last), self.n_lost.eq(self.n_lost + 1)]

        # counters see every packet, before anything can drop it
        with m.If(self.pkt_valid & in_first):
            m.d.sync += self.n_pkt.eq(self.n_pkt + 1)
            with m.If(self.pkt_data == 0xA5):
                m.d.sync += self.n_sof.eq(self.n_sof + 1)
            with m.If(self.pkt_data == 0x5A):
                m.d.sync += self.n_nak.eq(self.n_nak + 1)
        with m.If(self.pkt_valid & self.pkt_last & self.pkt_err):
            m.d.sync += self.n_err.eq(self.n_err + 1)

        h_data, h_last, h_err = f_rd.data[0:8], f_rd.data[8], f_rd.data[9]
        h_first, h_ts = f_rd.data[10], f_rd.data[11:27]
        pid_ok  = h_data[4:8] == ~h_data[0:4]
        is_data = pid_ok & (h_data[0:2] == 3)
        is_hs   = pid_ok & (h_data[0:2] == 2)
        is_tok  = ~(is_data | is_hs)
        is_sof  = h_data == 0xA5

        # ---- ring + record length queue
        ring = Memory(shape=8, depth=RING, init=[])
        lenq = Memory(shape=11, depth=LD, init=[])
        m.submodules.ring, m.submodules.lenq = ring, lenq
        r_wr, r_rd = ring.write_port(), ring.read_port()
        q_wr, q_rd = lenq.write_port(), lenq.read_port(domain="comb")
        wr, rp, rec_start = Signal(PW), Signal(PW), Signal(PW)
        used = Signal(PW)
        q_w, q_r = Signal(LB + 1), Signal(LB + 1)
        q_empty = q_w == q_r
        q_full  = (q_w - q_r)[:LB + 1] == LD
        m.d.comb += [
            used.eq((wr - rp)[:PW]),
            self.ring_used.eq(used),
            q_rd.addr.eq(q_r[:LB]),
            q_wr.addr.eq(q_w[:LB]),
        ]
        can_reserve = used <= RING - (HDR_BYTES + 1)

        # ---- framer
        ts_rec, plen = Signal(16), Signal(11)
        fl_err, fl_to, fl_trunc, fl_tok, fl_data, fl_hs, fl_sof = (Signal() for _ in range(7))
        ovf, in_pkt, cur_tok, close_to = (Signal() for _ in range(4))
        pkt_len, idle_cnt = Signal(3), Signal(range(p["txn_idle"] + 2))
        wb_base, wb_i, wb_n, wb_rec = Signal(PW), Signal(3), Signal(3), Signal(11)
        wbh = Array(Signal(8, name=f"wbh{i}") for i in range(6))
        drop_cnt, age = Signal(8), Signal(16)
        time_gap, ev_pend, ev_pop = Signal(), Signal(), Signal()
        dn, drop_clear, start_dn = Signal(), Signal(), Signal()
        dn_src = [start_dn]

        with m.If(drop_clear):
            m.d.sync += drop_cnt.eq(dn)
        with m.Elif(dn):
            m.d.sync += [drop_cnt.eq(Mux(drop_cnt == 255, 255, drop_cnt + 1))]
        with m.If(dn):
            m.d.sync += self.n_dropped.eq(self.n_dropped + 1)

        with m.If(tick & (age != 0xFFFF)):
            m.d.sync += age.eq(age + 1)
        with m.If(tick & (age == 0xFFFE)):
            m.d.sync += time_gap.eq(1)

        emem = Memory(shape=24, depth=EVENT_QUEUE, init=[])
        m.submodules.emem = emem
        e_wr, e_rd = emem.write_port(), emem.read_port(domain="comb")
        e_w, e_r = Signal(EB + 1), Signal(EB + 1)
        e_full = (e_w - e_r)[:EB + 1] == EVENT_QUEUE
        ev_ts, ev_code = e_rd.data[0:16], e_rd.data[16:24]
        ev_dn, ev_full_dn = Signal(), Signal()
        dn_src += [ev_dn, ev_full_dn]
        m.d.comb += [
            ev_dn.eq(self.ev_valid & e_full), ev_pend.eq(e_w != e_r),
            e_rd.addr.eq(e_r[:EB]), e_wr.addr.eq(e_w[:EB]),
            e_wr.data.eq(Cat(now, self.ev_code)), e_wr.en.eq(self.ev_valid & ~e_full),
        ]
        with m.If(self.ev_valid & ~e_full):
            m.d.sync += [e_w.eq(e_w + 1), age.eq(0)]
        with m.If(ev_pop):
            m.d.sync += e_r.eq(e_r + 1)

        # small record (event/drop/wrap) source selection, only with the input FIFO empty
        want_ev   = ev_pend
        want_drop = drop_cnt != 0
        want_wrap = age >= WRAP_AGE
        ev_first  = want_ev & ~f_empty & ((h_ts - ev_ts)[:16] < 0x8000)   # keeps ts order
        small_len = Mux(want_ev | want_drop, HDR_BYTES + 1, HDR_BYTES)
        small_ok  = (used <= RING - small_len) & ~q_full

        def load_header(ts, flags, plen_lo, plen_hi, extra):
            return [wbh[0].eq(ts[0:8]), wbh[1].eq(ts[8:16]), wbh[2].eq(flags),
                    wbh[3].eq(plen_lo), wbh[4].eq(plen_hi), wbh[5].eq(extra)]

        m.d.comb += [r_wr.en.eq(0), q_wr.en.eq(0)]
        take = Signal()
        has_any = fl_tok | fl_data | fl_hs
        closes = (~in_pkt) & has_any & h_first & (
            is_tok | (is_data & (fl_data | fl_hs)) | (is_hs & fl_hs))
        tok_now = Mux(h_first, is_tok, cur_tok)
        hs_now  = fl_hs | (h_first & is_hs)
        sof_now = fl_sof | (h_first & is_sof)
        total   = Mux(h_first, 0, pkt_len) + 1
        wr_ok   = (plen < SNAP_BYTES) & ~ovf & (used < RING)

        with m.FSM():
            with m.State("IDLE"):
                with m.If(~f_empty & ~ev_first):
                    m.next = "START"
                with m.Elif(want_ev | ((want_drop | want_wrap) & f_empty)):
                    with m.If(small_ok):
                        m.d.sync += [wb_base.eq(wr), wb_i.eq(0), wb_n.eq(small_len),
                                     wb_rec.eq(small_len), age.eq(0)]
                        with m.If(want_ev):
                            m.d.sync += load_header(ev_ts, KIND_EVENT, 1, 0, ev_code)
                            m.d.comb += ev_pop.eq(1)
                        with m.Elif(want_drop):
                            m.d.sync += load_header(now, KIND_DROP | (time_gap << 4), 1, 0, drop_cnt)
                            m.d.sync += time_gap.eq(0)
                            m.d.comb += drop_clear.eq(1)
                        with m.Else():
                            m.d.sync += load_header(now, KIND_WRAP, 0, 0, 0)
                        m.next = "BACKFILL"
                    with m.Elif(want_ev):
                        m.d.comb += ev_pop.eq(1)
                        m.d.comb += ev_full_dn.eq(1)
            with m.State("START"):
                with m.If(f_clear):
                    m.next = "IDLE"
                with m.Elif(can_reserve & ~q_full):
                    m.d.sync += [rec_start.eq(wr), wr.eq(wr + HDR_BYTES), ts_rec.eq(h_ts),
                                 plen.eq(0), ovf.eq(0), in_pkt.eq(0), idle_cnt.eq(0), age.eq(0)]
                    m.d.sync += [fl_err.eq(0), fl_to.eq(0), fl_trunc.eq(0), fl_tok.eq(0),
                                 fl_data.eq(0), fl_hs.eq(0), fl_sof.eq(0)]
                    m.next = "OPEN"
                with m.Else():
                    m.d.sync += [in_pkt.eq(0), idle_cnt.eq(0), fl_tok.eq(0), fl_data.eq(0),
                                 fl_hs.eq(0), fl_sof.eq(0)]
                    m.d.comb += start_dn.eq(1)
                    m.next = "SKIP"
            with m.State("SKIP"):          # discard the rest of a transaction that got no record
                with m.If(f_clear | (~f_empty & closes)):
                    m.next = "IDLE"
                with m.Elif(~f_empty):
                    m.d.comb += pop.eq(1)
                    m.d.sync += [idle_cnt.eq(0), in_pkt.eq(~h_last)]
                    with m.If(h_first):
                        m.d.sync += [fl_tok.eq(fl_tok | is_tok), fl_data.eq(fl_data | is_data),
                                     fl_hs.eq(fl_hs | is_hs), fl_sof.eq(fl_sof | is_sof)]
                    with m.If(h_last & (hs_now | sof_now)):
                        m.next = "IDLE"
                with m.Elif(~in_pkt):
                    m.d.sync += idle_cnt.eq(idle_cnt + 1)
                    with m.If(idle_cnt >= p["txn_idle"]):
                        m.next = "IDLE"
            with m.State("OPEN"):
                abort_dn = Signal()
                dn_src.append(abort_dn)
                with m.If(f_clear):
                    m.d.sync += wr.eq(rec_start)
                    m.d.comb += abort_dn.eq(1)
                    m.next = "IDLE"
                with m.Elif(~f_empty & closes):
                    m.d.sync += close_to.eq(~fl_hs & ~fl_sof)
                    m.next = "CLOSE"
                with m.Elif(~f_empty):
                    m.d.comb += [pop.eq(1), take.eq(1)]
                    m.d.sync += [idle_cnt.eq(0), in_pkt.eq(~h_last), cur_tok.eq(tok_now),
                                 pkt_len.eq(Mux(total > 6, 7, total))]
                    with m.If(h_first):
                        m.d.sync += [fl_tok.eq(fl_tok | is_tok), fl_data.eq(fl_data | is_data),
                                     fl_hs.eq(fl_hs | is_hs), fl_sof.eq(fl_sof | is_sof)]
                    with m.If(wr_ok):
                        m.d.comb += [r_wr.addr.eq(wr[:RB]), r_wr.data.eq(h_data), r_wr.en.eq(1)]
                        m.d.sync += [wr.eq(wr + 1), plen.eq(plen + 1)]
                    with m.Elif(plen >= SNAP_BYTES):
                        m.d.sync += fl_trunc.eq(1)
                    with m.Else():
                        m.d.sync += ovf.eq(1)
                    with m.If(h_last):
                        m.d.sync += fl_err.eq(fl_err | h_err | (tok_now & (total != 3)))
                        with m.If(hs_now | sof_now):
                            m.d.sync += close_to.eq(0)
                            m.next = "CLOSE"
                with m.Elif(~in_pkt):
                    m.d.sync += idle_cnt.eq(idle_cnt + 1)
                    with m.If(idle_cnt >= p["txn_idle"]):
                        m.d.sync += close_to.eq(~fl_hs & ~fl_sof)
                        m.next = "CLOSE"
            with m.State("CLOSE"):
                close_dn = Signal()
                dn_src.append(close_dn)
                with m.If(ovf):
                    m.d.sync += wr.eq(rec_start)
                    m.d.comb += close_dn.eq(1)
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += load_header(
                        ts_rec,
                        Cat(C(KIND_TXN, 2), fl_err, close_to, fl_trunc, fl_tok, fl_data, fl_hs),
                        plen[0:8], plen[8:11], 0)
                    m.d.sync += [wb_base.eq(rec_start), wb_i.eq(0), wb_n.eq(HDR_BYTES),
                                 wb_rec.eq(plen + HDR_BYTES)]
                    m.next = "BACKFILL"
            with m.State("BACKFILL"):
                m.d.comb += [r_wr.addr.eq((wb_base + wb_i)[:RB]), r_wr.data.eq(wbh[wb_i]),
                             r_wr.en.eq(1)]
                m.d.sync += wb_i.eq(wb_i + 1)
                with m.If(wb_i == wb_n - 1):
                    m.d.sync += wr.eq(wb_base + wb_rec)
                    m.d.comb += [q_wr.data.eq(wb_rec), q_wr.en.eq(1)]
                    m.d.sync += q_w.eq(q_w + 1)
                    m.next = "IDLE"

        m.d.comb += dn.eq(reduce(or_, dn_src))

        # ---- block feeder
        rem, used_b = Signal(11), Signal(11)
        s1_valid, s1_last = Signal(), Signal()
        idle_f = Signal(range(p["flush_idle"] + 2))
        nonempty = ~q_empty
        fits     = (used_b + 1 + q_rd.data) <= BLOCK_BYTES
        flush_now = self.flush | (idle_f >= p["flush_idle"])
        sink_valid = s1_valid & (~s1_last | nonempty | flush_now)
        end_blk    = s1_last & (~nonempty | ~fits | flush_now)
        consume    = sink_valid & self.source.ready
        issue      = (rem != 0) & (~s1_valid | consume)
        do_pop     = (rem == 0) & nonempty & (~s1_valid | (consume & s1_last))
        m.d.comb += [
            r_rd.addr.eq(rp[:RB]), r_rd.en.eq(issue),
            self.source.valid.eq(sink_valid),
            self.source.data.eq(r_rd.data),
            self.source.last.eq(end_blk),
            q_rd.addr.eq(q_r[:LB]),
        ]
        with m.If(do_pop):
            m.d.sync += [rem.eq(q_rd.data), q_r.eq(q_r + 1)]
        with m.If(issue):
            m.d.sync += [rem.eq(rem - 1), rp.eq(rp + 1), s1_valid.eq(1), s1_last.eq(rem == 1)]
        with m.Elif(consume):
            m.d.sync += s1_valid.eq(0)
        with m.If(s1_valid & s1_last & ~nonempty & ~consume):
            m.d.sync += idle_f.eq(idle_f + 1)
        with m.Else():
            m.d.sync += idle_f.eq(0)
        m.d.sync += self.blk_ts_valid.eq(0)
        with m.If(consume):
            m.d.sync += used_b.eq(Mux(end_blk, 0, used_b + 1))
            with m.If(used_b == 0):
                m.d.sync += self.blk_ts[0:8].eq(r_rd.data)
            with m.If(used_b == 1):
                m.d.sync += [self.blk_ts[8:16].eq(r_rd.data), self.blk_ts_valid.eq(1)]
        return m
