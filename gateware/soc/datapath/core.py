#
# The data path: parser, FIFOs, codec slots, sequencer, builder, and the RV's CSRs.
# SPDX-License-Identifier: BSD-3-Clause

"""`DataPath`: one `sync`-domain block between the PC's data/control endpoints and
`UsbPort`'s engine port. Register map and RV sequence: `docs/data-path.md`.

    pc_rx -> HeaderParser -> desc queue (CSR) ; payload -> CodecStage -> OUT ring
    OUT ring -> PacketSequencer <-> UsbPort engine port -> IN FIFO
    IN FIFO -> CodecStage -> BlockBuilder (header from H_* CSRs) -> pc_tx
    ctl_rx / ctl_tx: byte port for the control endpoint pair (CTL_* CSRs)

## Register map (8-bit CSR, 4 per 32-bit word; read side effects alone in a word)
    0x00 IRQ_EN   rw  0 desc 1 done 2 sent 3 ctl_rx
    0x01 EVENTS   rw1c 0 done 1 sent
    0x02 CTRL     w   0 pop 1 arm 2 send 3 abort 4 flush (strobes)
    0x03 STATUS   r   0 desc_ready 1 seq_busy 2 tx_busy [5:3] desc count 6 present
    0x04 D_FLAGS  r   head descriptor: header byte 1
    0x05 D_TAG    r   header byte 2
    0x06 D_AUX    r   header byte 3
    0x07 D_ERR    r   0 bad magic 1 length > MAX_LEN
    0x08-0x0a D_LEN r 17 bit
    0x0c ADDR     rw  [6:0]
    0x0d EP       rw  [3:0] endpoint 4 dir_in 5 start toggle 6 zlp 7 discard
    0x0e-0x0f MPS rw  10 bit
    0x10-0x12 LEN rw  17 bit
    0x14-0x15 NAK_LIMIT rw 16 bit, reset NAK_LIMIT_DEFAULT
    0x18 R_RESP   r   [2:0] last response 3 next toggle
    0x19 R_FLAGS  r   0 short 1 nak_limit 2 stall 3 error 4 overrun 5 seg 6 abort
    0x1a-0x1b R_NAKS r 16 bit
    0x1c-0x1e R_MOVED r 18 bit
    0x20 H_FLAGS 0x21 H_TAG 0x22 H_AUX 0x23 H_STATUS  rw  result header bytes 1-4
    0x24-0x26 H_LEN rw 18 bit
    0x28 CTL_RX   r   POPS the control OUT byte
    0x2c CTL_STATUS r 0 rx_valid 1 rx_last 2 tx_ready
    0x2d CTL_TX   w   push a byte; 0x2e CTL_TX_LAST w push the transfer's last byte
"""

from amaranth             import Cat, Module, Mux, ResetInserter, Signal
from amaranth.lib         import wiring
from amaranth.lib.fifo    import SyncFIFOBuffered
from amaranth.lib.wiring  import In, Out

from amaranth_soc         import csr

from capture.codec        import byte_stream_signature
from datapath.builder     import BlockBuilder
from datapath.codec_stage import CodecStage
from datapath.header      import LEN_BITS
from datapath.parser      import DESC_WIDTH, HeaderParser, desc_fields
from datapath.ring        import RewindFifo
from datapath.sequencer   import MPS_BITS, NAK_LIMIT_DEFAULT, PacketSequencer
from peripherals.uart16550 import SplitRW

__all__ = ["DataPath", "ENGINE_MEMBERS", "REG"]

ADDR_WIDTH = 6
DESC_DEPTH = 4

REG = dict(
    IRQ_EN=0x00, EVENTS=0x01, CTRL=0x02, STATUS=0x03,
    D_FLAGS=0x04, D_TAG=0x05, D_AUX=0x06, D_ERR=0x07, D_LEN0=0x08, D_LEN1=0x09, D_LEN2=0x0a,
    ADDR=0x0c, EP=0x0d, MPS_LO=0x0e, MPS_HI=0x0f, LEN0=0x10, LEN1=0x11, LEN2=0x12,
    NAK_LO=0x14, NAK_HI=0x15,
    R_RESP=0x18, R_FLAGS=0x19, R_NAKS_LO=0x1a, R_NAKS_HI=0x1b,
    R_MOVED0=0x1c, R_MOVED1=0x1d, R_MOVED2=0x1e,
    H_FLAGS=0x20, H_TAG=0x21, H_AUX=0x22, H_STATUS=0x23, H_LEN0=0x24, H_LEN1=0x25, H_LEN2=0x26,
    CTL_RX=0x28, CTL_STATUS=0x2c, CTL_TX=0x2d, CTL_TX_LAST=0x2e,
)

# (name, direction from the data path's side, width): the UsbPort engine port, flipped.
ENGINE_MEMBERS = (
    ("eng_sel", Out, 1), ("eng_start", Out, 1), ("eng_addr", Out, 7), ("eng_ep", Out, 7),
    ("eng_tx_data", Out, 8), ("eng_tx_valid", Out, 1), ("eng_tx_ready", In, 1),
    ("eng_rx_data", In, 8), ("eng_rx_valid", In, 1), ("eng_rx_ready", Out, 1),
    ("eng_done", In, 1), ("eng_resp", In, 3), ("eng_len", In, 16), ("eng_pid", In, 8),
)

# (register, width, init) of the plain read/write ones; storage lives here.
_RW = (("IRQ_EN", 4, 0), ("ADDR", 7, 0), ("EP", 8, 0), ("MPS_LO", 8, 0), ("MPS_HI", 2, 2),
       ("LEN0", 8, 0), ("LEN1", 8, 0), ("LEN2", 1, 0),
       ("NAK_LO", 8, NAK_LIMIT_DEFAULT & 0xFF), ("NAK_HI", 8, NAK_LIMIT_DEFAULT >> 8),
       ("H_FLAGS", 8, 0), ("H_TAG", 8, 0), ("H_AUX", 8, 0), ("H_STATUS", 8, 0),
       ("H_LEN0", 8, 0), ("H_LEN1", 8, 0), ("H_LEN2", 2, 0))
_RO = (("STATUS", 7), ("D_FLAGS", 8), ("D_TAG", 8), ("D_AUX", 8), ("D_ERR", 2),
       ("D_LEN0", 8), ("D_LEN1", 8), ("D_LEN2", 1), ("R_RESP", 4), ("R_FLAGS", 7),
       ("R_NAKS_LO", 8), ("R_NAKS_HI", 8), ("R_MOVED0", 8), ("R_MOVED1", 8), ("R_MOVED2", 2),
       ("CTL_STATUS", 3))


class DataPath(wiring.Component):
    def __init__(self, *, out_depth=2048, in_depth=2048):
        self.out_depth, self.in_depth = out_depth, in_depth
        self._regs = {}
        builder = csr.Builder(addr_width=ADDR_WIDTH, data_width=8)
        for name, width, _ in _RW:
            self._regs[name] = csr.Register({"v": csr.Field(SplitRW, width)}, access="rw")
        for name, width in _RO:
            self._regs[name] = csr.Register({"v": csr.Field(csr.action.R, width)}, access="r")
        self._regs["EVENTS"] = csr.Register({"v": csr.Field(csr.action.RW1C, 2)}, access="rw")
        self._regs["CTRL"] = csr.Register({"v": csr.Field(csr.action.W, 5)}, access="w")
        self._regs["CTL_RX"] = csr.Register({"v": csr.Field(SplitRW, 8)}, access="rw")
        for name in ("CTL_TX", "CTL_TX_LAST"):
            self._regs[name] = csr.Register({"v": csr.Field(csr.action.W, 8)}, access="w")
        for name, reg in sorted(self._regs.items(), key=lambda kv: REG[kv[0]]):
            builder.add(name.lower(), reg, offset=REG[name])
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":    In(csr.Signature(addr_width=ADDR_WIDTH, data_width=8)),
            "irq":    Out(1),
            "pc_rx":  In(byte_stream_signature()),
            "pc_tx":  Out(byte_stream_signature()),
            "ctl_rx": In(byte_stream_signature()),
            "ctl_tx": Out(byte_stream_signature()),
            **{name: d(w) for name, d, w in ENGINE_MEMBERS},
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)
        f = {name: reg.f.v for name, reg in self._regs.items()}

        # ---- plain rw storage -------------------------------------------
        v = {}
        for name, width, init in _RW:
            v[name] = Signal(width, init=init, name=f"r_{name.lower()}")
            with m.If(f[name].w_stb):
                m.d.sync += v[name].eq(f[name].w_data)
            m.d.comb += f[name].r_data.eq(v[name])
        ctrl = f["CTRL"]
        strobe = [Signal(name=f"ctrl{i}") for i in range(5)]
        for i, s in enumerate(strobe):
            m.d.comb += s.eq(ctrl.w_stb & ctrl.w_data[i])
        pop, arm, send, abort, flush = strobe

        # ---- blocks -----------------------------------------------------
        parser = HeaderParser()
        m.submodules.parser = ResetInserter(flush)(parser)
        m.submodules.codec_out = codec_out = CodecStage()
        m.submodules.codec_in = codec_in = CodecStage()
        m.submodules.out_ring = out_ring = RewindFifo(self.out_depth)
        m.submodules.in_fifo = in_fifo = RewindFifo(self.in_depth)
        m.submodules.seq = seq = PacketSequencer(in_cap=self.in_depth)
        m.submodules.builder = bld = BlockBuilder()
        m.submodules.desc_q = desc_q = ResetInserter(flush)(
            SyncFIFOBuffered(width=DESC_WIDTH, depth=DESC_DEPTH))

        wiring.connect(m, wiring.flipped(self.pc_rx), parser.sink)
        wiring.connect(m, parser.payload, codec_out.sink)
        m.d.comb += [
            desc_q.w_data.eq(parser.desc), desc_q.w_en.eq(parser.desc_valid),
            parser.desc_ready.eq(desc_q.w_rdy),
            desc_q.r_en.eq(pop),
            out_ring.w_data.eq(codec_out.source.data),
            out_ring.w_en.eq(codec_out.source.valid),
            codec_out.source.ready.eq(out_ring.w_rdy),
            out_ring.flush.eq(flush), in_fifo.flush.eq(flush), in_fifo.commit.eq(1),
            # sequencer <-> FIFOs
            seq.o_data.eq(out_ring.r_data), seq.o_rdy.eq(out_ring.r_rdy),
            out_ring.r_en.eq(seq.o_en), seq.o_level.eq(out_ring.r_level),
            out_ring.commit.eq(seq.o_commit), out_ring.rewind.eq(seq.o_rewind),
            in_fifo.w_data.eq(seq.i_data), in_fifo.w_en.eq(seq.i_en),
            seq.i_free.eq(self.in_depth - in_fifo.w_level),
            # IN FIFO -> codec -> builder -> PC
            codec_in.sink.data.eq(in_fifo.r_data), codec_in.sink.valid.eq(in_fifo.r_rdy),
            in_fifo.r_en.eq(codec_in.sink.ready),
        ]
        wiring.connect(m, codec_in.source, bld.payload)
        wiring.connect(m, bld.source, wiring.flipped(self.pc_tx))
        for name, d, _ in ENGINE_MEMBERS:
            if d is Out:
                m.d.comb += getattr(self, name).eq(getattr(seq, name))
            else:
                m.d.comb += getattr(seq, name).eq(getattr(self, name))

        # ---- arm parameters and results -----------------------------------
        ep = v["EP"]
        m.d.comb += [
            seq.addr.eq(v["ADDR"]), seq.ep.eq(ep[0:4]), seq.dir_in.eq(ep[4]),
            seq.toggle.eq(ep[5]), seq.zlp.eq(ep[6]), seq.discard.eq(ep[7]),
            seq.mps.eq(Cat(v["MPS_LO"], v["MPS_HI"])[:MPS_BITS]),
            seq.length.eq(Cat(v["LEN0"], v["LEN1"], v["LEN2"])[:LEN_BITS]),
            seq.nak_limit.eq(Cat(v["NAK_LO"], v["NAK_HI"])),
            seq.arm.eq(arm), seq.abort.eq(abort),
            bld.flags.eq(v["H_FLAGS"]), bld.tag.eq(v["H_TAG"]), bld.aux.eq(v["H_AUX"]),
            bld.status.eq(v["H_STATUS"]),
            bld.length.eq(Cat(v["H_LEN0"], v["H_LEN1"], v["H_LEN2"])),
            bld.send.eq(send),
        ]
        d_flags, d_tag, d_aux, d_len, d_err = desc_fields(desc_q.r_data)

        def ro(name, value):
            m.d.comb += f[name].r_data.eq(value)
        ro("STATUS", Cat(desc_q.r_rdy, seq.busy, bld.busy, desc_q.r_level, 1))
        ro("D_FLAGS", d_flags), ro("D_TAG", d_tag), ro("D_AUX", d_aux), ro("D_ERR", d_err)
        ro("D_LEN0", d_len[0:8]), ro("D_LEN1", d_len[8:16]), ro("D_LEN2", d_len[16:])
        ro("R_RESP", Cat(seq.r_resp, seq.r_toggle)), ro("R_FLAGS", seq.r_flags)
        ro("R_NAKS_LO", seq.r_naks[0:8]), ro("R_NAKS_HI", seq.r_naks[8:16])
        ro("R_MOVED0", seq.r_moved[0:8]), ro("R_MOVED1", seq.r_moved[8:16])
        ro("R_MOVED2", seq.r_moved[16:])

        # ---- control endpoint byte port ----------------------------------
        crx, ctx = self.ctl_rx, self.ctl_tx
        rx_f = f["CTL_RX"]
        m.d.comb += [rx_f.r_data.eq(Mux(crx.valid, crx.data, 0)), crx.ready.eq(rx_f.r_stb)]
        tx_d, tx_l = self._regs["CTL_TX"].f.v, self._regs["CTL_TX_LAST"].f.v
        with m.If(ctx.ready):
            m.d.sync += ctx.valid.eq(0)
        with m.If(~ctx.valid & (tx_d.w_stb | tx_l.w_stb)):
            m.d.sync += [ctx.valid.eq(1), ctx.data.eq(Mux(tx_l.w_stb, tx_l.w_data, tx_d.w_data)),
                         ctx.last.eq(tx_l.w_stb)]
        ro("CTL_STATUS", Cat(crx.valid, crx.last, ~ctx.valid))

        # ---- events + IRQ ---------------------------------------------------
        ev = f["EVENTS"]
        m.d.comb += ev.set.eq(Cat(seq.done, bld.sent))
        en = v["IRQ_EN"]
        m.d.comb += self.irq.eq((en[0] & desc_q.r_rdy) | (en[1] & ev.data[0])
                                | (en[2] & ev.data[1]) | (en[3] & crx.valid))
        return m
