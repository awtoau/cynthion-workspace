# SPDX-License-Identifier: BSD-3-Clause
"""Reference test gateware: config-flash baseline top. Not product gateware.

- Clocks: one EHXPLLL from the 60 MHz pin. CLKOP = `sck` (engine, pin registers),
  CLKOS = `cap` (read capture, runtime phase via PHASESTEP), CLKOS2 = +180 degrees, driven
  to MCLK through a DCCA gate and USRMCLK (FPGA-TN-02039 6.1.2). One SCK per bitstream.
- Registers: LUNA's public JTAGRegisterInterface in `sync` (the 60 MHz pin), read by
  `apollo_fpga` over Apollo's JTAG (same pattern as gateware/hyperram_baseline/top.py).
- Controls are static while an op runs; `go` crosses by PulseSynchronizer, results are read
  once `done` (FFSynchronizer, masked from go until the engine clears it).
"""

from amaranth import (Cat, ClockDomain, ClockSignal, Const, DomainRenamer, Elaboratable,
                      Instance, Module, ResetInserter, ResetSignal, Signal)
from amaranth.lib.cdc import FFSynchronizer, PulseSynchronizer

from spiflash import FlashPHY, FlashPins, OpEngine, TxEngine

APPLET_ID = 0x464C4231   # "FLB1"

REG = dict(ID=1, GO=2, OP=3, DESC=4, MBYTE=5, ADDR=6, LENGTH=7, BURST=8, COUNT=9, STRIDE=10,
           CSH=11, LAT=12, XFER=13, XOR=14, SEED=15, CHECK=16, WDATA=17, PHASE_STEP=18,
           BITMAP_IDX=19, TS=20, CHK_LO=21, CHK_HI=22, ABORT=23,
           STATUS=32, CK_KHZ=33, PLL_DIV=34, CK_COUNT=35, RUN_CYCLES=36, BYTES=37, TXS=38,
           CHECKED=39, ERRORS=40, BAD_INDEX=41, BAD_GOT_WANT=42, BAD_BITS=43, RESULT=44,
           BUSY_MIN=45, BUSY_MAX=46, BUSY_SUM=47, BUSY_N=48, STATE=49, PHASE_POS=50,
           BITMAP_DATA=51, SEQ=52)

# CK meter window: 2**19 cycles of the 60 MHz reference (8.74 ms).
CK_WINDOW_BITS = 19
# ECP5 EHXPLLL limits (FPGA-DS-02012, sysCLOCK PLL): PFD >= 10 MHz, VCO 400..800 MHz.
PFD_MIN_MHZ, VCO_MIN_MHZ, VCO_MAX_MHZ = 10.0, 400.0, 800.0


def solve_pll(mhz, ref_mhz=60.0):
    """(clki_div, clkfb_div, div): out = ref * fb / clki; the largest output divider in the
    VCO range, so one PHASESTEP (1/(8*div) of a period, TN-02200 18.4.1) is the finest."""
    for clki in range(1, int(ref_mhz // PFD_MIN_MHZ) + 1):
        for fb in range(1, 81):
            if abs(ref_mhz * fb / clki - mhz) > 1e-6:
                continue
            divs = [d for d in range(1, 129) if VCO_MIN_MHZ <= mhz * d <= VCO_MAX_MHZ]
            if divs:
                return clki, fb, max(divs)
    return None


def reachable(low, high, ref_mhz=60.0):
    out = {round(ref_mhz * fb / ci, 6) for ci in range(1, 7) for fb in range(1, 81)}
    return sorted(f for f in out if low <= f <= high and solve_pll(f, ref_mhz))


def phase_params(div, degrees):
    """CPHASE/FPHASE for a static shift (prjtrellis `ecppll`: VCO steps of 1/8)."""
    count = degrees / 360.0 * div
    coarse = int(count)
    fine = int(round((count - coarse) * 8))
    if fine == 8:
        coarse, fine = coarse + 1, 0
    return div - 1 + coarse, fine


class _PLL(Elaboratable):
    def __init__(self, *, mhz, clki):
        self.mhz, self.clki = mhz, clki
        solved = solve_pll(mhz)
        if solved is None:
            raise ValueError(f"no PLL configuration gives {mhz} MHz; reachable near it: "
                             f"{reachable(mhz - 10, mhz + 10)}")
        self.clki_div, self.fb_div, self.div = solved
        self.clk = Signal(name="clk_sck")
        self.clk_cap = Signal(name="clk_cap")
        self.clk_flash = Signal(name="clk_flash")
        self.locked = Signal()
        self.phasestep = Signal(init=1)          # active low, TN-02200 18.3.9

    def elaborate(self, platform):
        m = Module()
        cph180, fph180 = phase_params(self.div, 180.0)
        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8", a_MFG_ENABLE_FILTEROPAMP="1",
            a_MFG_GMCREF_SEL="2",
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            p_STDBY_ENABLE="DISABLED", p_DPHASE_SOURCE="ENABLED",
            p_OUTDIVIDER_MUXA="DIVA", p_OUTDIVIDER_MUXB="DIVB",
            p_OUTDIVIDER_MUXC="DIVC", p_OUTDIVIDER_MUXD="DIVD",
            p_CLKI_DIV=self.clki_div, p_CLKFB_DIV=self.fb_div, p_FEEDBK_PATH="CLKOP",
            p_CLKOP_ENABLE="ENABLED", p_CLKOP_DIV=self.div,
            p_CLKOP_CPHASE=self.div - 1, p_CLKOP_FPHASE=0,
            p_CLKOS_ENABLE="ENABLED", p_CLKOS_DIV=self.div,
            p_CLKOS_CPHASE=self.div - 1, p_CLKOS_FPHASE=0,
            p_CLKOS2_ENABLE="ENABLED", p_CLKOS2_DIV=self.div,
            p_CLKOS2_CPHASE=cph180, p_CLKOS2_FPHASE=fph180,
            i_CLKI=self.clki, i_CLKFB=self.clk,
            i_RST=0, i_STDBY=0,
            # PHASESEL 00 = CLKOS, PHASEDIR 0 = delay (glitch-free direction, 18.4.1).
            i_PHASESEL0=0, i_PHASESEL1=0, i_PHASEDIR=0,
            i_PHASESTEP=self.phasestep, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=self.clk, o_CLKOS=self.clk_cap, o_CLKOS2=self.clk_flash, o_LOCK=self.locked)
        if platform is not None:
            for c in (self.clk, self.clk_cap, self.clk_flash):
                platform.add_clock_constraint(c, self.mhz * 1e6)
        return m


class FlashBaselineTop(Elaboratable):
    def __init__(self, *, sck_mhz):
        self.sck_mhz = float(sck_mhz)

    def elaborate(self, platform):
        from luna.gateware.interface.jtag import JTAGRegisterInterface

        m = Module()
        clk60 = platform.request(platform.default_clk).i
        m.domains.sync = ClockDomain("sync")
        m.d.comb += [ClockSignal("sync").eq(clk60), ResetSignal("sync").eq(0)]
        m.submodules.pll = pll = _PLL(mhz=self.sck_mhz, clki=clk60)
        m.domains.sck = ClockDomain("sck")
        m.domains.cap = ClockDomain("cap", reset_less=True)
        m.domains.capn = ClockDomain("capn", clk_edge="neg", reset_less=True)
        # LOCK is asynchronous to both domains: synchronised before it resets `sck`; a
        # sticky `lock_lost` (STATUS[3]) records any drop after the first lock.
        lock_sck, lock_sync, locked_once, lock_lost = Signal(), Signal(), Signal(), Signal()
        m.submodules.lock_sck = FFSynchronizer(pll.locked, lock_sck, o_domain="sck")
        m.submodules.lock_sync = FFSynchronizer(pll.locked, lock_sync, o_domain="sync")
        with m.If(lock_sync):
            m.d.sync += locked_once.eq(1)
        with m.Elif(locked_once):
            m.d.sync += lock_lost.eq(1)
        m.d.comb += [ClockSignal("sck").eq(pll.clk), ResetSignal("sck").eq(~lock_sck),
                     ClockSignal("cap").eq(pll.clk_cap), ClockSignal("capn").eq(pll.clk_cap)]

        regs = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.regs = regs

        def rw(name):
            sig = Signal(32, name=f"reg_{name.lower()}")
            regs.add_register(REG[name], value_signal=sig)
            return sig

        # ---- flash pins, USRMCLK ---------------------------------------------------
        bus = platform.request("flash", 0)
        ios = [bus.io0, bus.io1, bus.io2, bus.io3]
        pad_o, pad_oe, pad_i, pad_cs, pad_ck_en = Signal(4), Signal(4), Signal(4), Signal(), Signal()
        for n, io in enumerate(ios):
            m.d.comb += [io.o.eq(pad_o[n]), io.oe.eq(pad_oe[n]), pad_i[n].eq(io.i)]
        m.d.comb += bus.cs.o.eq(pad_cs)
        ts = rw("TS")
        # MCLK gate: CE changes on `sck` edges = CLKOS2 falling, i.e. while the gated clock
        # is low, so no runt pulse.
        clk_mclk = Signal()
        m.submodules.mclk_gate = Instance("DCCA", i_CLKI=pll.clk_flash, i_CE=pad_ck_en,
                                          o_CLKO=clk_mclk)
        m.submodules.usrmclk = Instance("USRMCLK", i_USRMCLKI=clk_mclk, i_USRMCLKTS=ts[0])

        pins = FlashPins()
        tx = TxEngine(pins)
        eng = OpEngine(tx)
        phy = FlashPHY(pins, pad_o=pad_o, pad_oe=pad_oe, pad_i=pad_i, pad_cs=pad_cs,
                       pad_ck_en=pad_ck_en)
        # ABORT: a write that changes ABORT[0] resets the engine (a hung BUSY poll would
        # otherwise hold it for the rest of the load).
        abort = rw("ABORT")
        abort_prev = Signal()
        m.d.sync += abort_prev.eq(abort[0])
        m.submodules.abort_ps = abort_ps = PulseSynchronizer(i_domain="sync", o_domain="sck")
        m.d.comb += abort_ps.i.eq(abort[0] != abort_prev)
        m.submodules.tx = ResetInserter({"sck": abort_ps.o})(DomainRenamer({"sync": "sck"})(tx))
        m.submodules.eng = ResetInserter({"sck": abort_ps.o})(DomainRenamer({"sync": "sck"})(eng))
        m.submodules.phy = DomainRenamer({"sync": "sck"})(phy)

        go = rw("GO")
        go_prev = Signal()
        m.d.sync += go_prev.eq(go[0])
        m.submodules.go_ps = go_ps = PulseSynchronizer(i_domain="sync", o_domain="sck")
        # Every write that changes GO[0] starts one op: one JTAG write per op.
        m.d.comb += [go_ps.i.eq(go[0] != go_prev), eng.go.eq(go_ps.o)]
        op = rw("OP")
        csh = rw("CSH")
        m.d.comb += [eng.op.eq(op[:3]), eng.poll_after.eq(op[8]),
                     eng.csh_r.eq(csh[0:8]), eng.csh_w.eq(csh[8:16])]
        for name, sig in (("DESC", eng.desc), ("MBYTE", eng.mbyte), ("ADDR", eng.addr),
                          ("LENGTH", eng.length), ("BURST", eng.burst), ("COUNT", eng.count),
                          ("STRIDE", eng.stride), ("XOR", eng.xor), ("SEED", eng.seed),
                          ("CHECK", eng.check), ("WDATA", eng.wdata), ("XFER", phy.xfer),
                          ("CHK_LO", eng.chk_lo), ("CHK_HI", eng.chk_hi)):
            m.d.comb += sig.eq(rw(name))
        lat = rw("LAT")                  # [3:0] token delay, [8] DDR pairing
        m.d.comb += [tx.lat.eq(lat[:4]), tx.half.eq(lat[8])]
        bidx = rw("BITMAP_IDX")
        m.d.comb += eng.bitmap_rd.addr.eq(bidx)
        regs.add_read_only_register(REG["BITMAP_DATA"], read=eng.bitmap_rd.data)

        # done: masked from go until the engine has cleared it.
        done_sync = Signal()
        m.submodules.done_ff = FFSynchronizer(eng.done, done_sync, o_domain="sync")
        stale, done = Signal(), Signal()
        with m.If(go_ps.i):
            m.d.sync += stale.eq(1)
        with m.Elif(~done_sync):
            m.d.sync += stale.eq(0)
        m.d.comb += done.eq(done_sync & ~stale)

        # ---- PHASESTEP pulses: [15:0] count, applied on a write that changes [31] -------
        # Low 2 / high 2 cycles of 60 MHz = 33 ns each, above 4 VCO cycles (<= 10 ns).
        # Registered: the PLL latches on the falling edge (TN-02200 18.4.1), so no glitch.
        pstep = rw("PHASE_STEP")
        p_prev = Signal()
        p_left = Signal(16)
        p_cnt = Signal(2)
        p_pos = Signal(32)
        p_out = Signal(init=1)
        m.d.comb += pll.phasestep.eq(p_out)
        m.d.sync += [p_prev.eq(pstep[31]), p_out.eq(1)]
        with m.If(pstep[31] != p_prev):
            m.d.sync += [p_left.eq(pstep[:16]), p_cnt.eq(0)]
        with m.Elif(p_left != 0):
            m.d.sync += [p_cnt.eq(p_cnt + 1), p_out.eq(p_cnt[1])]
            with m.If(p_cnt == 3):
                m.d.sync += [p_left.eq(p_left - 1), p_pos.eq(p_pos + 1)]
        regs.add_read_only_register(REG["PHASE_POS"], read=p_pos)

        # ---- CK meter: sck cycles over 2**CK_WINDOW_BITS cycles of the 60 MHz pin ----
        ref_cnt = Signal(CK_WINDOW_BITS + 1)
        m.d.sync += ref_cnt.eq(ref_cnt + 1)
        gate, gate_prev = Signal(), Signal()
        m.submodules.gate_ff = FFSynchronizer(ref_cnt[-1], gate, o_domain="sck")
        sck_cnt, ck_count = Signal(32), Signal(32)
        m.d.sck += gate_prev.eq(gate)
        with m.If(gate):
            m.d.sck += sck_cnt.eq(sck_cnt + 1)
        with m.Elif(gate_prev):
            m.d.sck += [ck_count.eq(sck_cnt), sck_cnt.eq(0)]
        regs.add_read_only_register(REG["CK_COUNT"], read=ck_count)

        status = Cat(done, lock_sync, p_left != 0, lock_lost)
        regs.add_read_only_register(REG["ID"], read=APPLET_ID)
        regs.add_read_only_register(REG["STATUS"], read=status)
        regs.add_read_only_register(REG["CK_KHZ"], read=int(round(self.sck_mhz * 1000)))
        regs.add_read_only_register(REG["PLL_DIV"], read=pll.div)
        for name in ("RUN_CYCLES", "BYTES", "TXS", "CHECKED", "ERRORS", "BAD_INDEX",
                     "BAD_GOT_WANT", "BAD_BITS", "RESULT", "BUSY_MIN", "BUSY_MAX", "BUSY_SUM",
                     "BUSY_N", "STATE", "SEQ"):
            regs.add_read_only_register(REG[name], read=getattr(eng, name.lower()))
        return m
