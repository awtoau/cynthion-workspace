#
# Per-packet loop between the data FIFOs and the host engine (UsbPort engine port).
# SPDX-License-Identifier: BSD-3-Clause

"""`PacketSequencer`: one armed descriptor -> USB packets, until a stop condition.

Armed by the RV with ADDR, EP, dir, start toggle, MPS, LEN, NAK limit (`docs/data-path.md`).

- OUT: packets of min(MPS, left) bytes from the OUT ring; the ring read is committed on
  ACK and rewound on anything else, so a NAKed packet is re-sent from the same bytes.
  A ZLP follows when LEN is 0, or `zlp` is set and the last packet was full.
- OUT `discard`: LEN bytes leave the ring and nothing goes on the wire (a rejected block).
- IN: an IN token only when the IN FIFO has MPS free. ACKed data with the expected
  DATA0/1 is kept and the toggle flips; a repeated PID (lost ACK) is dropped.
- Stops: LEN moved, short packet (IN), STALL, NAK limit, ERR_LIMIT errors in a row
  (TIMEOUT / CRC / overflow / dropped start), abort, or IN segment full (`seg`).
- Results: last response, next toggle, bytes moved, NAKs seen, stop flags.
"""

from amaranth             import Module, Signal, Mux
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, Out

from datapath.header      import LEN_BITS

__all__ = ["PacketSequencer", "ERR_LIMIT", "NAK_LIMIT_DEFAULT", "MPS_BITS",
           "R_SHORT", "R_NAK_LIMIT", "R_STALL", "R_ERROR", "R_OVERRUN", "R_SEG", "R_ABORT"]

# Transaction errors in a row before the descriptor stops. USB 2.0 8.6.4 / 11.17.4:
# a host retries a transaction three times on error.
ERR_LIMIT = 3

# NAKs in a row before the descriptor returns to the RV (programmable, NAK_LIMIT).
# Min HS NAKed transaction ~60 usb cycles (token 8 + turnaround + handshake 6 + SIE
# IPD 24 + CDC/drain ~20) = 1 us; 1000 = one FS frame (1 ms) of back-to-back NAKs.
NAK_LIMIT_DEFAULT = 1000

MPS_BITS = 10               # <= 512: the SIE's TX FIFO holds one packet

PID_DATA0, PID_DATA1 = 0xC3, 0x4B
RESP_NONE, RESP_ACK, RESP_NAK, RESP_STALL = 0, 1, 2, 3

# Stop flags (R_FLAGS bits).
R_SHORT, R_NAK_LIMIT, R_STALL, R_ERROR, R_OVERRUN, R_SEG, R_ABORT = (1 << i for i in range(7))


class PacketSequencer(wiring.Component):
    def __init__(self, *, in_cap=2048):
        self.in_cap = in_cap
        L = LEN_BITS
        super().__init__({
            # arm parameters (held by the CSR block while busy)
            "addr": In(7), "ep": In(4), "dir_in": In(1), "toggle": In(1), "zlp": In(1),
            "discard": In(1), "mps": In(MPS_BITS), "length": In(L), "nak_limit": In(16),
            "arm": In(1), "abort": In(1),
            # OUT ring read side
            "o_data": In(8), "o_rdy": In(1), "o_en": Out(1), "o_level": In(range(1 << 12)),
            "o_commit": Out(1), "o_rewind": Out(1),
            # IN FIFO write side
            "i_data": Out(8), "i_en": Out(1), "i_free": In(range(1 << 12)),
            # engine port (UsbPort eng_*)
            "eng_sel": Out(1), "eng_start": Out(1), "eng_addr": Out(7), "eng_ep": Out(7),
            "eng_tx_data": Out(8), "eng_tx_valid": Out(1), "eng_tx_ready": In(1),
            "eng_rx_data": In(8), "eng_rx_valid": In(1), "eng_rx_ready": Out(1),
            "eng_done": In(1), "eng_resp": In(3), "eng_len": In(16), "eng_pid": In(8),
            # results
            "busy": Out(1), "done": Out(1),
            "r_resp": Out(3), "r_toggle": Out(1), "r_flags": Out(7),
            "r_moved": Out(L + 1), "r_naks": Out(16),
        })

    def elaborate(self, platform):
        m = Module()
        L = LEN_BITS
        toggle = Signal()
        moved = Signal(L + 1)
        naks, nak_run = Signal(16), Signal(16)
        errs = Signal(range(ERR_LIMIT + 1))
        flags = Signal(7)
        last_full, zlp_sent = Signal(), Signal()
        pkt, cnt = Signal(MPS_BITS), Signal(16)
        resp = Signal(3)
        keep = Signal()
        abort_req = Signal()
        left = Signal(L + 1)
        m.d.comb += left.eq(self.length - moved)

        typ = Mux(self.dir_in, 1, 2)        # UsbPort EP.type: 1 IN, 2 OUT
        m.d.comb += [
            self.eng_addr.eq(self.addr),
            self.eng_ep.eq(self.ep | (typ << 4) | (toggle << 6)),
            self.eng_tx_data.eq(self.o_data),
            self.i_data.eq(self.eng_rx_data),
            self.r_toggle.eq(toggle), self.r_moved.eq(moved), self.r_naks.eq(naks),
            self.r_flags.eq(flags), self.r_resp.eq(resp),
        ]
        with m.If(self.abort & self.busy):
            m.d.sync += abort_req.eq(1)

        def finish(flag=0):
            if flag:
                m.d.sync += flags.eq(flags | flag)
            m.next = "FINISH"

        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.arm):
                    m.d.sync += [toggle.eq(self.toggle), moved.eq(0), naks.eq(0),
                                 nak_run.eq(0), errs.eq(0), flags.eq(0), resp.eq(RESP_NONE),
                                 last_full.eq(1), zlp_sent.eq(0), abort_req.eq(0)]
                    m.next = "NEXT"
            with m.State("NEXT"):
                m.d.comb += [self.busy.eq(1), self.eng_sel.eq(1)]
                m.d.sync += cnt.eq(0)
                with m.If(abort_req):
                    finish(R_ABORT)
                with m.Elif(self.discard & ~self.dir_in):
                    m.next = "DISCARD"
                with m.Elif(~self.dir_in):
                    with m.If(left == 0):
                        with m.If(~zlp_sent & ((self.length == 0) | (self.zlp & last_full))):
                            m.d.sync += pkt.eq(0)
                            m.next = "START"
                        with m.Else():
                            finish()
                    with m.Else():
                        n = Mux(left > self.mps, self.mps, left)
                        with m.If(self.o_level >= n):
                            m.d.sync += pkt.eq(n)
                            m.next = "PUSH"
                with m.Else():
                    with m.If(moved >= self.length):
                        finish()
                    with m.Elif(moved + self.mps > self.in_cap):
                        finish(R_SEG)
                    with m.Elif(self.i_free >= self.mps):
                        m.next = "START"
            with m.State("DISCARD"):
                m.d.comb += [self.busy.eq(1), self.o_commit.eq(1)]
                with m.If(left == 0):
                    finish()
                with m.Elif(self.o_rdy):
                    m.d.comb += self.o_en.eq(1)
                    m.d.sync += moved.eq(moved + 1)
            with m.State("PUSH"):
                m.d.comb += [self.busy.eq(1), self.eng_sel.eq(1),
                             self.eng_tx_valid.eq(self.o_rdy),
                             self.o_en.eq(self.eng_tx_ready)]
                with m.If(self.o_rdy & self.eng_tx_ready):
                    m.d.sync += cnt.eq(cnt + 1)
                    with m.If(cnt == pkt - 1):
                        m.next = "START"
            with m.State("START"):
                m.d.comb += [self.busy.eq(1), self.eng_sel.eq(1), self.eng_start.eq(1)]
                m.next = "WAIT"
            with m.State("WAIT"):
                m.d.comb += [self.busy.eq(1), self.eng_sel.eq(1)]
                with m.If(self.eng_done):
                    r = self.eng_resp
                    want = Mux(toggle, PID_DATA1, PID_DATA0)
                    m.d.sync += [resp.eq(r), cnt.eq(0),
                                 nak_run.eq(Mux(r == RESP_NAK, nak_run + 1, 0))]
                    with m.If(r == RESP_NAK):
                        m.d.sync += naks.eq(Mux(naks == 0xFFFF, naks, naks + 1))
                    with m.If(r != RESP_ACK):
                        m.d.comb += self.o_rewind.eq(~self.dir_in)
                    with m.If((r == RESP_ACK) & ~self.dir_in):
                        m.d.comb += self.o_commit.eq(1)
                        m.d.sync += [moved.eq(moved + pkt), toggle.eq(~toggle), errs.eq(0),
                                     last_full.eq(pkt == self.mps), zlp_sent.eq(pkt == 0)]
                        m.next = "NEXT"
                    with m.Elif((r == RESP_ACK) & self.dir_in):
                        good = self.eng_pid == want
                        m.d.sync += [pkt.eq(self.eng_len), keep.eq(good),
                                     errs.eq(Mux(good, 0, errs + 1))]
                        with m.If(good):
                            m.d.sync += [moved.eq(moved + self.eng_len), toggle.eq(~toggle)]
                            with m.If(self.eng_len < self.mps):
                                m.d.sync += flags.eq(flags | R_SHORT)
                            with m.If(moved + self.eng_len > self.length):
                                m.d.sync += flags.eq(flags | R_OVERRUN)
                        m.next = "DRAIN"
                    with m.Elif(r == RESP_NAK):
                        with m.If(nak_run + 1 >= self.nak_limit):
                            finish(R_NAK_LIMIT)
                        with m.Else():
                            m.next = "NEXT"
                    with m.Elif(r == RESP_STALL):
                        finish(R_STALL)
                    with m.Else():
                        # TIMEOUT / CRC / overflow / NONE: bytes the SIE took are dropped.
                        m.d.sync += [errs.eq(errs + 1), pkt.eq(self.eng_len), keep.eq(0)]
                        m.next = "DRAIN"
            with m.State("DRAIN"):
                m.d.comb += [self.busy.eq(1), self.eng_sel.eq(1)]
                with m.If(cnt == pkt):
                    with m.If(errs == ERR_LIMIT):
                        finish(R_ERROR)
                    with m.Elif(flags & (R_SHORT | R_OVERRUN)):
                        finish()
                    with m.Else():
                        m.next = "NEXT"
                with m.Else():
                    m.d.comb += [self.eng_rx_ready.eq(1), self.i_en.eq(keep & self.eng_rx_valid)]
                    with m.If(self.eng_rx_valid):
                        m.d.sync += cnt.eq(cnt + 1)
            with m.State("FINISH"):
                m.d.comb += [self.busy.eq(1), self.done.eq(1)]
                m.next = "IDLE"
        return m
