# SPDX-License-Identifier: BSD-3-Clause
"""Reference test gateware (flash baseline): SPI flash transaction engine, op engine, PHY.

Clean room: W25Q32JV datasheet rev G (`~/vendors/winbond/w25q32jv_revg_20180327.pdf`) and
Lattice FPGA-TN-02039 / TN-02200 / TN-02035 only. No repo or upstream SPI code was read.

- `sync` here is the flash clock domain (`sck` in the top). SCK at the pin is the PLL's
  180-degree output, so every output launched on a `sync` edge reaches the flash mid-low
  (datasheet 9.8: inputs sampled on CLK rising).
- Reads are captured in `cap` (same PLL, runtime phase) and moved back; `lat` delays the
  per-cycle sample token to match the round trip. Both are calibrated by the harness.
- One transaction: CMD, ADDR (+ mode byte), DUMMY, DATA, TAIL with /CS low, then GAP.
"""

from amaranth import Cat, Const, Elaboratable, Module, Mux, Signal
from amaranth.lib.memory import Memory

SECTOR_BITS = 12                 # 4 KiB erase sector (datasheet 6.1)
ADDR_BYTES_BITS = 22             # 4 MiB part
BITMAP_WORDS = (1 << (ADDR_BYTES_BITS - SECTOR_BITS)) // 32

W1, W2, W4, W8 = 0, 1, 2, 3      # width codes: 1, 2, 4 lines; 4 lines DDR (a byte per CLK)
OP_RAW, OP_READ, OP_WLOOP = 1, 2, 3
CHECK_NONE, CHECK_PATTERN, CHECK_BLANK = 0, 1, 2
LAT_MAX = 15

(P_IDLE, P_LOAD, P_CMD, P_ADDR, P_DUMMY, P_DATA, P_TAIL, P_GAP) = range(8)


def desc(cmd, *, abits=0, aw=W1, dummy=0, dw=W1, read=False, poll=False, cmd_w4=False,
         cmd_en=True, tail=0):
    """Pack a transaction descriptor (DESC register layout)."""
    assert abits in (0, 24, 32) and 0 <= dummy < 32 and 0 <= tail < 16
    return (cmd | cmd_en << 8 | cmd_w4 << 9 | abits << 10 | aw << 16 | dummy << 18 | dw << 23
            | read << 25 | poll << 26 | tail << 28)


# Read modes the board wires (datasheet Instruction Set Tables 1/2, Figs 16-24).
# abits 32 = address + mode byte M7-0 = Fxh (JV-IQ documents no continuous read mode).
READ_MODES = {
    "0Bh 1-1-1": desc(0x0B, abits=24, aw=W1, dummy=8, dw=W1, read=True, tail=1),
    "3Bh 1-1-2": desc(0x3B, abits=24, aw=W1, dummy=8, dw=W2, read=True, tail=1),
    "BBh 1-2-2": desc(0xBB, abits=32, aw=W2, dummy=0, dw=W2, read=True, tail=1),
    "6Bh 1-1-4": desc(0x6B, abits=24, aw=W1, dummy=8, dw=W4, read=True, tail=1),
    "EBh 1-4-4": desc(0xEB, abits=32, aw=W4, dummy=4, dw=W4, read=True, tail=1),
}
# DTR (W25Q32JV-DTR rev H Table 4, Fig 27a): address + M on 4 lines DDR (4 CLK), 7 dummy,
# a byte per CLK. The address phase here is SDR, 6 cycles: right only for a pair-equal
# address (0); cycles 4-6 are M = 00h (no continuous read) and 2 dummies, so 5 more.
DTR = desc(0xED, abits=24, aw=W4, dummy=5, dw=W8, read=True, tail=1)
MBYTE = 0xF0
WREN = desc(0x06)
POLL = desc(0x05, dw=W1, read=True, poll=True)


def pattern(a, seed):
    """Byte due at byte address `a` (22 bits): low lines toggle byte to byte, every address
    bit folds in, and each of the 4 seed bytes xors a quarter of the bytes."""
    b = a[1:9] ^ a[9:17] ^ Cat(Const(0, 2), a[17:22])
    inv = Cat(*([a[0]] * 8))
    return (b ^ inv ^ seed.word_select(a[0:2], 8))[:8]


def pattern_py(a, seed):
    b = ((a >> 1) & 0xFF) ^ ((a >> 9) & 0xFF) ^ ((((a >> 17) & 0x1F) << 2) & 0xFF)
    b ^= 0xFF if a & 1 else 0
    return b ^ ((seed >> (8 * (a & 3))) & 0xFF)


class FlashPins:
    """What the engine wants on the pins this cycle (registered once by the PHY)."""

    def __init__(self):
        self.cs = Signal()
        self.o = Signal(4)
        self.oe = Signal(4)
        # Sampled data, back in the engine domain: [0:4] at the `cap` rising edge,
        # [4:8] at the `cap` falling edge before it.
        self.i = Signal(8)


class TxEngine(Elaboratable):
    """One transaction per `start` (desc/addr/length/csh sampled then); `done` ends GAP."""

    def __init__(self, pins):
        self.pins = pins
        self.start = Signal()
        self.done = Signal()
        self.idle = Signal()
        self.desc = Signal(32)
        self.addr = Signal(32)        # LSB-justified, `abits` wide
        self.length = Signal(32)      # data bytes
        self.csh = Signal(8)          # /CS high cycles after the transaction (min)
        self.lat = Signal(4)
        self.half = Signal()          # DDR: which falling/rising samples pair into a byte
        self.wbyte = Signal(8)
        self.wnext = Signal()         # wbyte consumed this cycle
        self.rx_valid = Signal()
        self.rx_byte = Signal(8)
        self.phase = Signal(3)

    def elaborate(self, platform):
        m = Module()
        p = self.pins
        d = Signal(32)
        cmd, cmd_en, cmd_w4 = d[0:8], d[8], d[9]
        abits, aw, dummy, dw = d[10:16], d[16:18], d[18:23], d[23:25]
        read, poll, tail = d[25], d[26], d[28:32]

        phase = self.phase
        a = Signal(32)
        csh = Signal(8)
        sr = Signal(32)
        w = Signal(2)                  # width of the current phase
        cyc = Signal(8)                # cycles left in CMD/ADDR/DUMMY/TAIL/GAP
        bcyc = Signal(3)               # cycles left in the current data byte
        left = Signal(32)              # data bytes left
        first = Signal()
        stop = Signal()                # poll: BUSY=0 seen

        def cycles(bits, wc):
            return Mux(wc == W4, bits >> 2, Mux(wc == W2, bits >> 1, bits))

        def per_byte(wc):
            return Mux(wc == W8, 0, Mux(wc == W4, 1, Mux(wc == W2, 3, 7)))

        def shifted(wc):
            return Mux(wc == W4, sr << 4, Mux(wc == W2, sr << 2, sr << 1))

        in_set = Signal(4)
        out_o, out_oe, rd_o, rd_oe = Signal(4), Signal(4), Signal(4), Signal(4)
        m.d.comb += [
            in_set.eq(Mux(dw[1], 0b1111, Mux(dw == W2, 0b0011, 0b0010))),
            out_o.eq(Mux(w == W4, sr[28:32],
                     Mux(w == W2, Cat(sr[30], sr[31], 1, 1), Cat(sr[31], 0, 1, 1)))),
            out_oe.eq(Mux(w == W1, 0b1101, 0b1111)),
            # Lines the flash drives are released; IO2/IO3 held High when not data lines.
            rd_o.eq(Const(0b1100, 4) & ~in_set),
            rd_oe.eq(~in_set),
        ]

        def enter_gap():
            # GAP also outlasts the token line, so `done` follows the last received byte.
            m.d.sync += [phase.eq(P_GAP), cyc.eq(Mux(csh > self.lat + 2, csh, self.lat + 2))]

        def enter_tail():
            with m.If(read & (tail != 0)):
                m.d.sync += [phase.eq(P_TAIL), cyc.eq(tail - 1)]
            with m.Else():
                enter_gap()

        def load_wbyte():
            m.d.sync += sr.eq(self.wbyte << 24)
            m.d.comb += self.wnext.eq(1)

        def enter_data():
            with m.If(left != 0):
                m.d.sync += [phase.eq(P_DATA), w.eq(dw), bcyc.eq(per_byte(dw)), first.eq(1)]
                with m.If(~read):
                    load_wbyte()
            with m.Else():
                enter_tail()

        def enter_dummy():
            with m.If(dummy != 0):
                m.d.sync += [phase.eq(P_DUMMY), cyc.eq(dummy - 1)]
            with m.Else():
                enter_data()

        def enter_addr():
            with m.If(abits != 0):
                m.d.sync += [phase.eq(P_ADDR), w.eq(aw), cyc.eq(cycles(abits, aw) - 1),
                             sr.eq(Mux(abits == 32, a, a << 8))]
            with m.Else():
                enter_dummy()

        tok = Signal()
        with m.Switch(phase):
            with m.Case(P_IDLE):
                m.d.comb += self.idle.eq(1)
                with m.If(self.start):
                    m.d.sync += [d.eq(self.desc), a.eq(self.addr), left.eq(self.length),
                                 csh.eq(self.csh), stop.eq(0), phase.eq(P_LOAD)]
            with m.Case(P_LOAD):
                with m.If(cmd_en):
                    m.d.sync += [phase.eq(P_CMD), sr.eq(cmd << 24), w.eq(Mux(cmd_w4, W4, W1)),
                                 cyc.eq(Mux(cmd_w4, 1, 7))]
                with m.Else():
                    enter_addr()
            with m.Case(P_CMD, P_ADDR):
                m.d.comb += [p.cs.eq(1), p.o.eq(out_o), p.oe.eq(out_oe)]
                m.d.sync += [sr.eq(shifted(w)), cyc.eq(cyc - 1)]
                with m.If(cyc == 0):
                    with m.If(phase == P_CMD):
                        enter_addr()
                    with m.Else():
                        enter_dummy()
            with m.Case(P_DUMMY):
                m.d.comb += [p.cs.eq(1), p.o.eq(rd_o), p.oe.eq(rd_oe)]
                m.d.sync += cyc.eq(cyc - 1)
                with m.If(cyc == 0):
                    enter_data()
            with m.Case(P_DATA):
                m.d.comb += p.cs.eq(1)
                with m.If(read):
                    m.d.comb += [p.o.eq(rd_o), p.oe.eq(rd_oe), tok.eq(1)]
                with m.Else():
                    m.d.comb += [p.o.eq(out_o), p.oe.eq(out_oe)]
                    m.d.sync += sr.eq(shifted(w))
                m.d.sync += [bcyc.eq(bcyc - 1), first.eq(0)]
                with m.If(bcyc == 0):
                    m.d.sync += [left.eq(left - 1), bcyc.eq(per_byte(w))]
                    with m.If((left == 1) | (poll & stop)):
                        enter_tail()
                    with m.Elif(~read):
                        load_wbyte()
            with m.Case(P_TAIL):
                m.d.comb += [p.cs.eq(1), p.o.eq(rd_o), p.oe.eq(rd_oe)]
                m.d.sync += cyc.eq(cyc - 1)
                with m.If(cyc == 0):
                    enter_gap()
            with m.Case(P_GAP):
                m.d.sync += cyc.eq(cyc - 1)
                with m.If(cyc == 0):
                    m.d.comb += self.done.eq(1)
                    m.d.sync += phase.eq(P_IDLE)

        # ---- receiver: token delay line, then MSB-first byte assembly ---------------
        here = Cat(tok, first & tok, w)
        line = Signal(4 * LAT_MAX)
        m.d.sync += line.eq(Cat(here, line[:-4]))
        stage = Signal(4)
        lat_m1 = Signal(4)
        m.d.comb += [lat_m1.eq(self.lat - 1),
                     stage.eq(Mux(self.lat == 0, here, line.word_select(lat_m1, 4)))]
        d_tok, d_first, d_w = stage[0], stage[1], stage[2:4]
        acc = Signal(8)
        nbits = Signal(4)
        base_acc = Mux(d_first, 0, acc)
        new_acc = Signal(8)
        new_bits = Signal(4)
        prev_r = Signal(4)
        m.d.sync += prev_r.eq(p.i[0:4])
        ddr = Mux(self.half, Cat(p.i[4:8], prev_r), Cat(p.i[0:4], p.i[4:8]))
        m.d.comb += [
            new_acc.eq(Mux(d_w == W8, ddr,
                       Mux(d_w == W4, Cat(p.i[0:4], base_acc[0:4]),
                       Mux(d_w == W2, Cat(p.i[0:2], base_acc[0:6]),
                           Cat(p.i[1], base_acc[0:7]))))),
            new_bits.eq(Mux(d_first, 0, nbits)
                        + Mux(d_w == W8, 8, Mux(d_w == W4, 4, Mux(d_w == W2, 2, 1)))),
        ]
        m.d.sync += self.rx_valid.eq(0)
        with m.If(d_tok):
            with m.If(new_bits == 8):
                m.d.sync += [self.rx_valid.eq(1), self.rx_byte.eq(new_acc), nbits.eq(0)]
            with m.Else():
                m.d.sync += [acc.eq(new_acc), nbits.eq(new_bits)]
        with m.If(self.rx_valid & poll & ~self.rx_byte[0] & (phase == P_DATA)):
            m.d.sync += stop.eq(1)
        return m


class OpEngine(Elaboratable):
    """RAW (one transaction, optional poll), READ (bursts + checker), WLOOP (per unit:
    WREN, op, poll BUSY with the time counted)."""

    def __init__(self, tx):
        self.tx = tx
        self.go = Signal()
        self.op = Signal(3)
        self.poll_after = Signal()
        self.desc = Signal(32)
        self.mbyte = Signal(8, init=MBYTE)
        self.addr = Signal(24)
        self.length = Signal(32)
        self.burst = Signal(32)
        self.count = Signal(16)
        self.stride = Signal(24)
        self.csh_r = Signal(8, init=2)
        self.csh_w = Signal(8, init=8)
        self.check = Signal(2)
        self.chk_lo = Signal(24)       # compare only addresses in [chk_lo, chk_hi); hi 0 = all
        self.chk_hi = Signal(24)
        self.xor = Signal(8)
        self.seed = Signal(32)
        self.wdata = Signal(32)
        # results (static once `done`)
        self.done = Signal()
        self.run_cycles = Signal(32)
        self.bytes = Signal(32)
        self.txs = Signal(32)
        self.checked = Signal(32)
        self.errors = Signal(32)
        self.bad_index = Signal(32, init=0xFFFFFFFF)
        self.bad_got_want = Signal(16)
        self.bad_bits = Signal(8)
        self.result = Signal(32)
        self.busy_min = Signal(32, init=0xFFFFFFFF)
        self.busy_max = Signal(32)
        self.busy_sum = Signal(32)
        self.busy_n = Signal(16)
        self.state = Signal(16)
        self.seq = Signal(16)          # ops started since load: proves a result is this op's
        self.bitmap = Memory(shape=32, depth=BITMAP_WORDS, init=[])
        self.bitmap_rd = self.bitmap.read_port(domain="comb")   # read after `done`

    def elaborate(self, platform):
        m = Module()
        tx = self.tx
        m.submodules.bitmap = self.bitmap
        wp = self.bitmap.write_port()
        offset = Signal(32)            # READ: bytes issued; WLOOP: unit index
        unit_addr = Signal(24)
        gen_addr = Signal(ADDR_BYTES_BITS)
        chk_addr = Signal(ADDR_BYTES_BITS)
        busy = Signal(32)
        polling = Signal()
        wbyte_idx = Signal(2)
        is_raw = Signal()
        checking = Signal()
        running = Signal()

        m.d.comb += tx.wbyte.eq(Mux(is_raw, self.wdata.word_select(~wbyte_idx, 8),
                                    pattern(gen_addr, self.seed)))
        with m.If(tx.wnext):
            m.d.sync += [gen_addr.eq(gen_addr + 1), wbyte_idx.eq(wbyte_idx + 1)]

        def issue(dsc, addr, length, csh):
            dsc = Const(dsc, 32) if isinstance(dsc, int) else dsc
            a24 = Const(addr, 24) if isinstance(addr, int) else addr[:24]
            m.d.comb += [tx.start.eq(1), tx.desc.eq(dsc), tx.length.eq(length), tx.csh.eq(csh),
                         tx.addr.eq(Mux(dsc[10:16] == 32, Cat(self.mbyte, a24), a24))]

        # ---- checker, sector bitmap, raw result --------------------------------------
        want = Signal(8)
        got = tx.rx_byte
        bad = Signal()
        m.d.comb += [want.eq(Mux(self.check == CHECK_BLANK, 0xFF,
                                 pattern(chk_addr, self.seed) ^ self.xor)),
                     bad.eq(got != want)]
        sector = chk_addr[SECTOR_BITS:]
        acc = Signal(32)
        new_acc = Signal(32)
        word_start = chk_addr[:SECTOR_BITS + 5] == 0
        m.d.comb += new_acc.eq(Mux(word_start, 0, acc) | (bad << sector[:5]))
        with m.If(tx.rx_valid & checking & ~polling):
            m.d.sync += [chk_addr.eq(chk_addr + 1), self.bytes.eq(self.bytes + 1)]
            in_win = (self.chk_hi == 0) | ((chk_addr >= self.chk_lo) & (chk_addr < self.chk_hi))
            with m.If((self.check != CHECK_NONE) & in_win):
                m.d.sync += [self.checked.eq(self.checked + 1), self.errors.eq(self.errors + bad),
                             self.bad_bits.eq(self.bad_bits | (got ^ want))]
                with m.If(bad & (self.bad_index == 0xFFFFFFFF)):
                    m.d.sync += [self.bad_index.eq(chk_addr), self.bad_got_want.eq(Cat(want, got))]
            with m.If(self.check == CHECK_BLANK):
                m.d.sync += acc.eq(new_acc)
                m.d.comb += [wp.en.eq(1), wp.addr.eq(sector[5:]), wp.data.eq(new_acc)]
        with m.If(tx.rx_valid & is_raw & ~polling):
            m.d.sync += self.result.eq(Cat(got, self.result[:24]))

        with m.If(running):
            m.d.sync += self.run_cycles.eq(self.run_cycles + 1)
        with m.If(polling):
            m.d.sync += busy.eq(busy + 1)
        with m.If(tx.done):
            m.d.sync += self.txs.eq(self.txs + 1)

        remaining = Signal(32)
        this_burst = Signal(32)
        m.d.comb += [remaining.eq(self.length - offset),
                     this_burst.eq(Mux(remaining < self.burst, remaining, self.burst))]

        with m.FSM() as fsm:
            with m.State("IDLE"):
                with m.If(self.go):
                    m.d.sync += [self.done.eq(0), offset.eq(0), unit_addr.eq(self.addr),
                                 self.seq.eq(self.seq + 1),
                                 gen_addr.eq(self.addr), chk_addr.eq(self.addr),
                                 wbyte_idx.eq(0), is_raw.eq(self.op == OP_RAW),
                                 checking.eq(self.op == OP_READ),
                                 self.run_cycles.eq(0), self.bytes.eq(0), self.txs.eq(0),
                                 self.checked.eq(0), self.errors.eq(0), acc.eq(0),
                                 self.bad_index.eq(0xFFFFFFFF), self.bad_got_want.eq(0),
                                 self.bad_bits.eq(0), self.result.eq(0),
                                 self.busy_min.eq(0xFFFFFFFF), self.busy_max.eq(0),
                                 self.busy_sum.eq(0), self.busy_n.eq(0)]
                    m.next = "DISPATCH"
            with m.State("DISPATCH"):
                m.d.comb += running.eq(1)
                with m.Switch(self.op):
                    with m.Case(OP_RAW):
                        m.next = "RAW"
                    with m.Case(OP_READ):
                        m.next = "READ"
                    with m.Case(OP_WLOOP):
                        m.next = "W_WREN"
                    with m.Default():
                        m.next = "FINISH"
            with m.State("RAW"):
                m.d.comb += running.eq(1)
                with m.If(tx.idle):
                    issue(self.desc, unit_addr, self.length, self.csh_w)
                    m.next = "RAW_WAIT"
            with m.State("RAW_WAIT"):
                m.d.comb += running.eq(1)
                with m.If(tx.done):
                    m.d.sync += busy.eq(0)
                    with m.If(self.poll_after):
                        m.next = "POLL"
                    with m.Else():
                        m.next = "FINISH"
            with m.State("READ"):
                m.d.comb += running.eq(1)
                with m.If(offset >= self.length):
                    m.next = "FINISH"
                with m.Elif(tx.idle):
                    issue(self.desc, self.addr + offset, this_burst, self.csh_r)
                    m.d.sync += offset.eq(offset + this_burst)
                    m.next = "READ_WAIT"
            with m.State("READ_WAIT"):
                m.d.comb += running.eq(1)
                with m.If(tx.done):
                    m.next = "READ"
            with m.State("W_WREN"):
                m.d.comb += running.eq(1)
                with m.If(offset == self.count):
                    m.next = "FINISH"
                with m.Elif(tx.idle):
                    issue(WREN, 0, 0, self.csh_w)
                    m.next = "W_WREN_WAIT"
            with m.State("W_WREN_WAIT"):
                m.d.comb += running.eq(1)
                with m.If(tx.done):
                    m.next = "W_OP"
            with m.State("W_OP"):
                m.d.comb += running.eq(1)
                with m.If(tx.idle):
                    issue(self.desc, unit_addr, self.burst, self.csh_w)
                    m.d.sync += gen_addr.eq(unit_addr)
                    m.next = "W_OP_WAIT"
            with m.State("W_OP_WAIT"):
                m.d.comb += running.eq(1)
                with m.If(tx.done):
                    m.d.sync += busy.eq(0)
                    m.next = "POLL"
            with m.State("POLL"):
                m.d.comb += [running.eq(1), polling.eq(1)]
                with m.If(tx.idle):
                    issue(POLL, 0, 0xFFFFFFFF, self.csh_w)
                    m.next = "POLL_WAIT"
            with m.State("POLL_WAIT"):
                m.d.comb += [running.eq(1), polling.eq(1)]
                with m.If(tx.done):
                    m.d.sync += [self.busy_sum.eq(self.busy_sum + busy),
                                 self.busy_n.eq(self.busy_n + 1)]
                    with m.If(busy < self.busy_min):
                        m.d.sync += self.busy_min.eq(busy)
                    with m.If(busy > self.busy_max):
                        m.d.sync += self.busy_max.eq(busy)
                    with m.If(self.op == OP_WLOOP):
                        m.d.sync += [offset.eq(offset + 1), unit_addr.eq(unit_addr + self.stride)]
                        m.next = "W_WREN"
                    with m.Else():
                        m.next = "FINISH"
            with m.State("FINISH"):
                m.d.sync += self.done.eq(1)
                m.next = "IDLE"
        m.d.comb += self.state.eq(Cat(fsm.state, Const(0, 8 - len(fsm.state)), tx.phase))
        return m


class FlashPHY(Elaboratable):
    """Fabric registers on the pins (bank 8 is the bottom side: no IOLOGIC DDR, TN-02035
    5.12.4).

    - Outputs: one `sync` register stage for /CS, IO, OE.
    - Inputs: a `cap` flop, a `capn` (falling `cap`) flop, then `sync`; `xfer` picks the path
      that does not race the `sync` edge at the current capture phase.
    """

    def __init__(self, pins, *, pad_o, pad_oe, pad_i, pad_cs):
        self.pins = pins
        self.pad_o, self.pad_oe, self.pad_i, self.pad_cs = pad_o, pad_oe, pad_i, pad_cs
        self.xfer = Signal()

    def elaborate(self, platform):
        m = Module()
        p = self.pins
        m.d.sync += [self.pad_cs.eq(p.cs), self.pad_o.eq(p.o), self.pad_oe.eq(p.oe)]
        q_c, q_f, q_fr, q_cn = Signal(4), Signal(4), Signal(4), Signal(8)
        m.d.cap += [q_c.eq(self.pad_i), q_fr.eq(q_f)]
        m.d.capn += [q_f.eq(self.pad_i), q_cn.eq(Cat(q_c, q_fr))]
        m.d.sync += p.i.eq(Mux(self.xfer, q_cn, Cat(q_c, q_fr)))
        return m
