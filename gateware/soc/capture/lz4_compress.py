#!/usr/bin/env python3
#
# Streaming LZ4 block compressor with a stored fallback.
# SPDX-License-Identifier: BSD-3-Clause

"""LZ4 block-format compressor, one raw block (<= 1 KiB) in, one coded block out.

Design record: `docs/capture-compression.md`. Interface: `codec.py`.

## Structure (three decoupled parts, banked by block parity)

  ingest  -> raw_a, raw_b (2 x 2 KiB, bank = block parity), 16-byte lookahead window
  engine  -> sequence RAM (token fields per sequence) + coded size
  emitter -> reads sequence RAM + raw_b, writes the coded (or stored) stream

  - Ingest writes every byte to both raw copies (raw_a: match history, raw_b:
    literal / stored replay) so engine and emitter never share a read port.
  - The engine runs on the lookahead window one position per clock and never waits
    on the emitter, so block k is emitted while block k+1 is ingested.
  - The coded size is known when the engine finishes, before the first coded byte is
    emitted: `coded >= raw` selects `stored` (codec_id 0, bytes replayed from raw_b).

## Engine pipeline (one position per clock, no restart bubble)

  - S0: window head q. Hash of bytes q..q+3 -> table read (sync, 1 clk).
  - S1: table data {pos, check} for q. A candidate with check match, pos < q,
    offset <= WINDOW and >= 12 bytes remaining STARTS a speculative match; the
    history read for q (hist[cand]) is issued here. During an active match the read
    is hist[q - offset] (address is predictable, so extension is also 1 byte/clk).
    The table entry for q is written here (one stage after its read), so the
    same-address read/write of consecutive equal hashes is never relied on.
  - S2: compare history byte with the input byte for q. Equal (and >= 6 bytes
    remain): length+1. Otherwise the match ends; length >= 4 commits a sequence, less
    than 4 is dropped (those positions stay literals). S1 may start the next
    match in the same clock, so there is no gap between back-to-back matches.
  - Literals are not copied: a sequence record holds (literal count, match length,
    offset); literal bytes are re-read from raw_b at emit time.
  - Tail rules: a match starts only with >= 12 bytes remaining and extends only
    with >= 6 remaining (so it ends >= 5 bytes before the end). The lookahead
    window supplies the remaining count without knowing the block length.

## Cost of verification

Candidates are not pre-verified. A 6-bit check from the hash filters most stale or
colliding entries; a speculative match that fails before 4 bytes costs those
positions their own lookups (they stay literals), never correctness.
"""

from amaranth            import Array, Cat, Const, Module, Mux, Signal
from amaranth.lib        import wiring
from amaranth.lib.memory import Memory

from .codec import (BLOCK_BYTES, ID_LZ4, ID_STORED, LEN_BITS, CompressorSignature,
                    register_codec)

__all__ = ["LZ4Compressor"]

LOOKAHEAD   = 16     # window bytes; the engine needs >= 12 to decide a start
START_MIN   = 12     # LZ4 MFLIMIT: last match starts >= 12 bytes before the end
EXTEND_MIN  = 6      # last matched byte index <= n - 6  => match ends <= n - 5
MIN_MATCH   = 4
TABLE_BITS  = 8
CHECK_BITS  = 6


def ext_count(x):
    """Bytes in the 255-chain for a nibble overflow value `x` (x >= 15 needs >= 1)."""
    return Mux(x < 15, 0, Mux(x < 270, 1, Mux(x < 525, 2, Mux(x < 780, 3, 4))))


def rotl8(x, n):
    return Cat(x, x)[8 - n:16 - n]


class LZ4Compressor(wiring.Component):
    """`window` limits the match offset (256..1024); the raw buffers always hold a block."""

    def __init__(self, window=BLOCK_BYTES, **_):
        assert 1 <= window <= BLOCK_BYTES
        self.window = window
        self.dbg_coded = Signal(LEN_BITS)   # lz4 size of the block being emitted (stored or not), for sims
        super().__init__(CompressorSignature())

    def elaborate(self, platform):
        m = Module()

        raw_a = Memory(shape=8, depth=2 * BLOCK_BYTES, init=[])
        raw_b = Memory(shape=8, depth=2 * BLOCK_BYTES, init=[])
        tab   = Memory(shape=10 + CHECK_BITS, depth=1 << TABLE_BITS, init=[])
        seq   = Memory(shape=32, depth=512, init=[])
        m.submodules += [raw_a, raw_b, tab, seq]
        wa, wb   = raw_a.write_port(), raw_b.write_port()
        hist_rd  = raw_a.read_port()
        emit_rd  = raw_b.read_port()
        tab_rd, tab_wr = tab.read_port(), tab.write_port()
        seq_rd, seq_wr = seq.read_port(), seq.write_port()

        # ---------------------------------------------------------------- ingest
        ibank   = Signal()
        icount  = Signal(11)
        busy    = Signal(2)
        clr_busy = Signal(2)          # emitter finished that bank
        last_in = Signal()            # last byte of a block is in the window
        av      = Signal(5)
        adv0    = Signal()
        win     = Array([Signal(8, name=f"win{i}") for i in range(LOOKAHEAD)])

        limit = Mux(self.block_limit > BLOCK_BYTES, BLOCK_BYTES,
                    Mux(self.block_limit == 0, 1, self.block_limit))
        eff_last = Signal()
        m.d.comb += eff_last.eq(self.sink.last | (icount + 1 >= limit))
        bank_ok = (icount != 0) | ~busy.bit_select(ibank, 1)
        ready   = Signal()
        m.d.comb += ready.eq(bank_ok & ~last_in & ((av < LOOKAHEAD) | adv0))
        take = Signal()
        m.d.comb += [self.sink.ready.eq(ready), take.eq(self.sink.valid & ready)]
        for port in (wa, wb):
            m.d.comb += [port.addr.eq(Cat(icount[:10], ibank)),
                         port.data.eq(self.sink.data), port.en.eq(take)]

        m.d.comb += adv0.eq((av != 0) & (last_in | (av >= START_MIN)))
        rem0 = Signal(5)
        m.d.comb += rem0.eq(Mux(last_in, av, 31))
        last0 = Signal()
        m.d.comb += last0.eq(last_in & (av == 1))

        ins_idx = Signal(5)
        m.d.comb += ins_idx.eq(av - adv0)
        with m.If(adv0):
            for i in range(LOOKAHEAD - 1):
                m.d.sync += win[i].eq(win[i + 1])
        for i in range(LOOKAHEAD):
            with m.If(take & (ins_idx == i)):
                m.d.sync += win[i].eq(self.sink.data)
        m.d.sync += av.eq(av - adv0 + take)

        with m.If(take):
            m.d.sync += icount.eq(Mux(eff_last, 0, icount + 1))
            with m.If(icount == 0):
                m.d.sync += busy.bit_select(ibank, 1).eq(1)
            with m.If(eff_last):
                m.d.sync += [last_in.eq(1), ibank.eq(~ibank)]
        with m.If(adv0 & last0):
            m.d.sync += last_in.eq(0)
        for bank in range(2):
            with m.If(clr_busy[bank]):
                m.d.sync += busy[bank].eq(0)
        # busy set and clear never coincide for one bank (set needs ~busy).

        # ------------------------------------------------------------ S0 (head)
        p0 = Signal(10)
        b0, b1_, b2_, b3_ = win[0], win[1], win[2], win[3]
        idx0 = Signal(TABLE_BITS)
        chk0 = Signal(CHECK_BITS)
        m.d.comb += [
            idx0.eq(b0 ^ rotl8(b1_, 3) ^ rotl8(b2_, 5) ^ rotl8(b3_, 7)),
            chk0.eq((b3_ ^ rotl8(b2_, 2) ^ rotl8(b1_, 4) ^ rotl8(b0, 6))[:CHECK_BITS]),
            tab_rd.addr.eq(idx0),
            tab_rd.en.eq(adv0),
        ]
        with m.If(adv0):
            m.d.sync += p0.eq(Mux(last0, 0, p0 + 1))

        # --------------------------------------------------------------- S1 regs
        v1, last1 = Signal(), Signal()
        byte1     = Signal(8)
        p1        = Signal(10)
        r1        = Signal(5)
        idx1      = Signal(TABLE_BITS)
        chk1      = Signal(CHECK_BITS)
        m.d.sync += v1.eq(adv0)
        with m.If(adv0):
            m.d.sync += [byte1.eq(b0), p1.eq(p0), r1.eq(rem0), idx1.eq(idx0),
                         chk1.eq(chk0), last1.eq(last0)]

        # --------------------------------------------------------------- S2 regs
        v2, last2 = Signal(), Signal()
        byte2     = Signal(8)
        p2        = Signal(10)
        r2        = Signal(5)
        m.d.sync += [v2.eq(v1), byte2.eq(byte1), p2.eq(p1), r2.eq(r1), last2.eq(last1 & v1)]

        # ------------------------------------------------------- match state
        m_active = Signal()
        mstart   = Signal(10)
        mlen     = Signal(11)
        moff     = Signal(10)
        lit_start = Signal(11)
        nseq     = Signal(9)
        csize    = Signal(LEN_BITS)
        ebank    = Signal()

        hbyte = hist_rd.data
        eq2 = Signal()
        mismatch2 = Signal()
        cont = Signal()
        m.d.comb += [
            eq2.eq((hbyte == byte2) & (r2 >= EXTEND_MIN)),
            mismatch2.eq(v2 & m_active & ~eq2),
            cont.eq(m_active & ~mismatch2),
        ]

        cpos = tab_rd.data[:10]
        cchk = tab_rd.data[10:10 + CHECK_BITS]
        off_c = Signal(11)
        start = Signal()
        m.d.comb += [
            off_c.eq(p1 - cpos),
            start.eq(v1 & (r1 >= START_MIN) & (cchk == chk1) & (cpos < p1)
                     & (off_c <= self.window) & ~cont),
        ]
        m.d.comb += [
            hist_rd.addr.eq(Cat(Mux(cont, (p1 - moff)[:10], cpos), ebank)),
            hist_rd.en.eq(v1 & (cont | start)),
            tab_wr.addr.eq(idx1),
            tab_wr.data.eq(Cat(p1, chk1)),
            tab_wr.en.eq(v1 & (r1 >= START_MIN)),
        ]

        commit = Signal()
        m.d.comb += commit.eq(mismatch2 & (mlen >= MIN_MATCH))
        lit_n = Signal(11)
        m.d.comb += lit_n.eq(mstart - lit_start)
        mext = Signal(11)
        m.d.comb += mext.eq(mlen - MIN_MATCH)

        fin = Signal()
        m.d.comb += fin.eq(v2 & last2)
        fin_lit = Signal(11)
        m.d.comb += fin_lit.eq(p2 + 1 - lit_start)

        with m.If(start):
            m.d.sync += [m_active.eq(1), mstart.eq(p1), mlen.eq(0), moff.eq(off_c)]
        with m.Elif(v2 & m_active & eq2):
            m.d.sync += mlen.eq(mlen + 1)
        with m.Elif(mismatch2):
            m.d.sync += m_active.eq(0)

        m.d.comb += [
            seq_wr.addr.eq(Cat(nseq[:8], ebank)),
            seq_wr.en.eq(commit | fin),
            seq_wr.data.eq(Mux(fin, Cat(fin_lit, Const(0, 21)),
                               Cat(lit_n, mlen, moff))),
        ]
        seq_cost = Signal(LEN_BITS)
        m.d.comb += seq_cost.eq(
            Mux(fin,
                1 + ext_count(fin_lit) + fin_lit,
                1 + ext_count(lit_n) + lit_n + 2 + ext_count(mext)))
        with m.If(commit):
            m.d.sync += [lit_start.eq(p2), nseq.eq(nseq + 1), csize.eq(csize + seq_cost)]

        # block info, one slot per bank
        info_valid = Signal(2)
        info_n     = Array([Signal(LEN_BITS, name=f"info_n{i}") for i in range(2)])
        info_c     = Array([Signal(LEN_BITS, name=f"info_c{i}") for i in range(2)])
        info_ns    = Array([Signal(9, name=f"info_ns{i}") for i in range(2)])
        clr_info   = Signal(2)
        with m.If(fin):
            m.d.sync += [
                info_n[ebank].eq(p2 + 1),
                info_c[ebank].eq(csize + seq_cost),
                info_ns[ebank].eq(nseq + 1),
                info_valid.bit_select(ebank, 1).eq(1),
                ebank.eq(~ebank), nseq.eq(0), csize.eq(0), lit_start.eq(0),
                m_active.eq(0),
            ]
        for bank in range(2):
            with m.If(clr_info[bank]):
                m.d.sync += info_valid[bank].eq(0)

        # ---------------------------------------------------------------- emitter
        em_bank  = Signal()
        ov, ol   = Signal(), Signal()
        od       = Signal(8)
        out_id   = Signal(4)
        out_comp = Signal(LEN_BITS)
        out_raw  = Signal(LEN_BITS)
        m.d.comb += [
            self.source.valid.eq(ov), self.source.data.eq(od), self.source.last.eq(ol),
            self.codec_id.eq(out_id), self.comp_len.eq(out_comp), self.raw_len.eq(out_raw),
        ]
        can_push = Signal()
        m.d.comb += can_push.eq(~ov | self.source.ready)

        push, pl = Signal(), Signal()
        pd = Signal(8)
        with m.If(can_push):
            m.d.sync += [ov.eq(push), od.eq(pd), ol.eq(pl)]

        # RAM reader (stored replay and literal runs)
        ra, rd_left, cons_left = Signal(10), Signal(11), Signal(11)
        rv = Signal()
        reading = Signal()
        issue, consume = Signal(), Signal()
        m.d.comb += [
            consume.eq(reading & rv & can_push),
            issue.eq(reading & (rd_left != 0) & (~rv | consume)),
            emit_rd.addr.eq(Cat(ra, em_bank)),
            emit_rd.en.eq(issue),
        ]
        with m.If(issue):
            m.d.sync += [rv.eq(1), ra.eq(ra + 1), rd_left.eq(rd_left - 1)]
        with m.Elif(consume):
            m.d.sync += rv.eq(0)
        with m.If(consume):
            m.d.sync += cons_left.eq(cons_left - 1)

        e_n, e_c = Signal(LEN_BITS), Signal(LEN_BITS)
        stored   = Signal()
        e_lit, e_ml, e_off = Signal(11), Signal(11), Signal(10)
        e_final  = Signal()
        lp       = Signal(10)
        si       = Signal(9)
        ev       = Signal(11)
        sq_en = Signal()
        m.d.comb += [seq_rd.addr.eq(Cat(si[:8], em_bank)), seq_rd.en.eq(sq_en)]

        with m.FSM() as fsm:
            with m.State("IDLE"):
                with m.If(info_valid.bit_select(em_bank, 1) & ~ov):
                    n_, c_ = info_n[em_bank], info_c[em_bank]
                    use_stored = c_ >= n_
                    m.d.sync += [
                        e_n.eq(n_), e_c.eq(c_), stored.eq(use_stored),
                        out_id.eq(Mux(use_stored, ID_STORED, ID_LZ4)),
                        out_raw.eq(n_), out_comp.eq(Mux(use_stored, n_, c_)),
                        self.dbg_coded.eq(c_),
                        ra.eq(0), rd_left.eq(n_), cons_left.eq(n_), rv.eq(0),
                        lp.eq(0), si.eq(0),
                    ]
                    with m.If(use_stored):
                        m.d.sync += reading.eq(1)
                        m.next = "STORED"
                    with m.Else():
                        m.next = "SEQ_RD"
            with m.State("STORED"):
                m.d.comb += [push.eq(consume), pd.eq(emit_rd.data), pl.eq(cons_left == 1)]
                with m.If(consume & (cons_left == 1)):
                    m.d.sync += reading.eq(0)
                    m.next = "DONE"
            with m.State("SEQ_RD"):
                m.d.comb += sq_en.eq(1)
                m.d.sync += si.eq(si + 1)
                m.next = "LOAD"
            with m.State("LOAD"):
                m.d.sync += [e_lit.eq(seq_rd.data[0:11]), e_ml.eq(seq_rd.data[11:22]),
                             e_off.eq(seq_rd.data[22:32]),
                             e_final.eq(seq_rd.data[11:22] == 0)]
                m.next = "TOKEN"
            with m.State("TOKEN"):
                lit_nib = Mux(e_lit < 15, e_lit[:4], 15)
                mnib    = Mux(e_final, 0, Mux(e_ml - MIN_MATCH < 15, (e_ml - MIN_MATCH)[:4], 15))
                m.d.comb += [
                    push.eq(1),
                    pd.eq(Cat(mnib, lit_nib)),
                    sq_en.eq(can_push),     # prefetch the next record while this one emits
                ]
                with m.If(can_push):
                    m.d.sync += [si.eq(si + 1), ev.eq(e_lit - 15), ra.eq(lp),
                                 lp.eq(lp + e_lit + e_ml), rd_left.eq(e_lit),
                                 cons_left.eq(e_lit), rv.eq(0)]
                    with m.If(e_lit >= 15):
                        m.next = "LEXT"
                    with m.Elif(e_lit != 0):
                        m.d.sync += reading.eq(1)
                        m.next = "LITS"
                    with m.Else():
                        m.next = "OFFLO"
            with m.State("LEXT"):
                m.d.comb += [push.eq(1), pd.eq(Mux(ev >= 255, 255, ev[:8]))]
                with m.If(can_push):
                    with m.If(ev >= 255):
                        m.d.sync += ev.eq(ev - 255)
                    with m.Else():
                        m.d.sync += reading.eq(1)
                        m.next = "LITS"
            with m.State("LITS"):
                m.d.comb += [push.eq(consume), pd.eq(emit_rd.data),
                             pl.eq(e_final & (cons_left == 1))]
                with m.If(consume & (cons_left == 1)):
                    m.d.sync += reading.eq(0)
                    m.next = "LITS_END"
            with m.State("LITS_END"):
                with m.If(e_final):
                    m.next = "DONE"
                with m.Else():
                    m.next = "OFFLO"
            with m.State("OFFLO"):
                m.d.comb += [push.eq(1), pd.eq(e_off[:8])]
                with m.If(can_push):
                    m.next = "OFFHI"
            with m.State("OFFHI"):
                m.d.comb += [push.eq(1), pd.eq(e_off[8:10])]
                with m.If(can_push):
                    m.d.sync += ev.eq(e_ml - MIN_MATCH - 15)
                    with m.If(e_ml - MIN_MATCH >= 15):
                        m.next = "MEXT"
                    with m.Else():
                        m.next = "LOAD"
            with m.State("MEXT"):
                m.d.comb += [push.eq(1), pd.eq(Mux(ev >= 255, 255, ev[:8]))]
                with m.If(can_push):
                    with m.If(ev >= 255):
                        m.d.sync += ev.eq(ev - 255)
                    with m.Else():
                        m.next = "LOAD"
            with m.State("DONE"):
                m.d.comb += [clr_info.bit_select(em_bank, 1).eq(1),
                             clr_busy.bit_select(em_bank, 1).eq(1)]
                m.d.sync += em_bank.eq(~em_bank)
                m.next = "IDLE"
        return m


register_codec(ID_LZ4, "lz4", LZ4Compressor, None)
