# SPDX-License-Identifier: BSD-3-Clause
"""Sequential-throughput baseline top (#581): generator -> FIFO -> HyperRAM -> FIFO -> checker.

- `sync` (registers, generator, checker) and `hr`/`hr90` (PHY, controller, engine) are
  separate PLLs; two AsyncFIFOs are the only data crossing.
- FIFOs are 32 bits wide so `sync` moves 2 words/cycle: source and sink outrun the bus.
- Modes: 1 write, 2 read, 3 write/read alternating per burst, 4 register read (`reg_addr`).
- A burst starts only when the FIFO holds (write) or has room for (read) all of it:
  the data phase cannot stall.
- Readout: LUNA's public JTAGRegisterInterface, read by `apollo_fpga` over Apollo's JTAG.
"""

from amaranth import (Cat, ClockDomain, ClockSignal, Const, DomainRenamer, Elaboratable,
                      Instance, Module, Mux, ResetSignal, Signal)
from amaranth.lib.cdc import FFSynchronizer, PulseSynchronizer
from amaranth.lib.fifo import AsyncFIFO

from hyperbus import ECP5HyperBusPHY, HyperBusCore, PinIntent

APPLET_ID = 0x48524231   # "HRB1"

REG = dict(ID=1, GO=2, MODE=3, BURST=4, TOTAL=5, XOR=6, STATUS=7, CK_KHZ=8, MAX_BURST=9,
           SYNC_KHZ=10, RUN_CYCLES=11, WORDS_W=12, WORDS_R=13, SRC_WAIT=14, SINK_WAIT=15,
           XACTS=16, MISSING=17, CHECKED=18, ERRORS=19, BAD_INDEX=20, BAD_GOT_WANT=21,
           GENERATED=22, WAIT=23, DELAY=24, REG_ADDR=25, REG_VALUE=26, STRADDLED=27,
           START_ADDR=28, SEED=29, BAD_BITS=30, CK_COUNT=31, STATE=32)

MODE_WRITE, MODE_READ, MODE_BOTH, MODE_REG = 1, 2, 3, 4

ADDRESS_BITS = 22           # 64 Mbit = 2**22 16-bit words
FIFO_DEPTH = 512            # 32-bit entries; holds half the longest legal burst
# 2 words/cycle at 130 MHz = 260 Mwords/s: above the bus up to CK 240.
SYNC_MHZ = 130.0
# CK meter window: 2**19 cycles of the 60 MHz reference (8.74 ms).
CK_WINDOW_BITS = 19

# ECP5 EHXPLLL limits (FPGA-DS-02012, sysCLOCK PLL): PFD >= 10 MHz, VCO 400..800 MHz.
PFD_MIN_MHZ, VCO_MIN_MHZ, VCO_MAX_MHZ = 10.0, 400.0, 800.0


def solve_pll(mhz, ref_mhz=60.0):
    """(clki_div, clkfb_div, div) with feedback on CLKOP: out = ref * fb / clki, VCO = out * div."""
    for clki in range(1, int(ref_mhz // PFD_MIN_MHZ) + 1):
        for fb in range(1, 81):
            if abs(ref_mhz * fb / clki - mhz) > 1e-6:
                continue
            for div in range(1, 129):
                if VCO_MIN_MHZ <= mhz * div <= VCO_MAX_MHZ:
                    return clki, fb, div
    return None


def reachable(low, high, ref_mhz=60.0):
    out = {round(ref_mhz * fb / ci, 6) for ci in range(1, 7) for fb in range(1, 81)}
    return sorted(f for f in out if low <= f <= high and solve_pll(f, ref_mhz))


def phase_params(div, degrees):
    """CPHASE/FPHASE for a shift, as prjtrellis `ecppll` computes it (VCO steps of 1/8)."""
    count = degrees / 360.0 * div
    coarse = int(count)
    fine = int(round((count - coarse) * 8))
    if fine == 8:
        coarse, fine = coarse + 1, 0
    return div - 1 + coarse, fine


def pattern(addr):
    """Word due at a 22-bit word address: every bit toggles beat to beat; all address bits fold in."""
    q = addr[1:17] ^ Cat(Const(0, 11), addr[17:22])
    return q ^ Cat(*([addr[0]] * 16))


def pattern_py(addr):
    q = ((addr >> 1) & 0xFFFF) ^ (((addr >> 17) & 0x1F) << 11)
    return q ^ (0xFFFF if addr & 1 else 0)


class SeqEngine(Elaboratable):
    """Generator/checker in `sync`; burst engine around `HyperBusCore` in `hr`."""

    def __init__(self, *, core, fifo_depth=FIFO_DEPTH):
        self.core = core
        self.fifo_depth = fifo_depth
        # sync-side controls
        self.go = Signal()
        self.mode = Signal(3)
        self.burst = Signal(16)
        self.total = Signal(ADDRESS_BITS + 1)
        self.start_addr = Signal(ADDRESS_BITS)
        self.xor = Signal(16)
        self.seed = Signal(16)          # xored into written data; vary per write
        self.wait = Signal(5, init=core.wait.init)
        self.reg_addr = Signal(32)
        # results (hr counters, static once `done`)
        self.done = Signal()
        self.run_cycles = Signal(32)
        self.words_w = Signal(32)
        self.words_r = Signal(32)
        self.src_wait = Signal(32)
        self.sink_wait = Signal(32)
        self.xacts = Signal(32)
        self.missing = Signal(32)
        self.straddled = Signal(32)
        self.reg_value = Signal(16)
        self.state = Signal(32)          # engine FSM, core FSM, FIFO levels: where a stall is
        self.checked = Signal(32)
        self.errors = Signal(32)
        self.bad_index = Signal(32, init=0xFFFFFFFF)
        self.bad_got_want = Signal(32)
        self.bad_bits = Signal(16)      # OR of got ^ want over the run
        self.generated = Signal(32)

    def elaborate(self, platform):
        m = Module()
        core = self.core
        m.submodules.wfifo = wfifo = AsyncFIFO(width=32, depth=self.fifo_depth,
                                               w_domain="sync", r_domain="hr")
        m.submodules.rfifo = rfifo = AsyncFIFO(width=32, depth=self.fifo_depth,
                                               w_domain="hr", r_domain="sync")
        m.submodules.go_ps = go_ps = PulseSynchronizer(i_domain="sync", o_domain="hr")
        m.d.comb += go_ps.i.eq(self.go)

        # ---- sync: generator -------------------------------------------------
        gen_left = Signal(ADDRESS_BITS + 1)
        gen_addr = Signal(ADDRESS_BITS)
        with m.If(self.go):
            m.d.sync += [gen_left.eq(Mux(self.mode[0] & ~self.mode[2], self.total & ~1, 0)),
                         gen_addr.eq(self.start_addr & ~1), self.generated.eq(0)]
        m.d.comb += [wfifo.w_data.eq(Cat(pattern(gen_addr) ^ self.seed, pattern(gen_addr + 1) ^ self.seed)),
                     wfifo.w_en.eq((gen_left != 0) & ~self.go)]
        with m.If(wfifo.w_en & wfifo.w_rdy):
            m.d.sync += [gen_left.eq(gen_left - 2), gen_addr.eq(gen_addr + 2),
                         self.generated.eq(self.generated + 2)]

        # ---- sync: checker ---------------------------------------------------
        chk_addr = Signal(ADDRESS_BITS)
        want_lo, want_hi = Signal(16), Signal(16)
        got_lo, got_hi = rfifo.r_data[:16], rfifo.r_data[16:]
        m.d.comb += [want_lo.eq(pattern(chk_addr) ^ self.seed ^ self.xor),
                     want_hi.eq(pattern(chk_addr + 1) ^ self.seed ^ self.xor),
                     rfifo.r_en.eq(1)]
        with m.If(self.go):
            m.d.sync += [chk_addr.eq(self.start_addr & ~1), self.checked.eq(0),
                         self.errors.eq(0), self.bad_index.eq(0xFFFFFFFF),
                         self.bad_got_want.eq(0), self.bad_bits.eq(0)]
        with m.Elif(rfifo.r_rdy):
            bad_lo, bad_hi = got_lo != want_lo, got_hi != want_hi
            m.d.sync += [chk_addr.eq(chk_addr + 2), self.checked.eq(self.checked + 2),
                         self.errors.eq(self.errors + bad_lo + bad_hi),
                         self.bad_bits.eq(self.bad_bits | (got_lo ^ want_lo) | (got_hi ^ want_hi))]
            with m.If((self.bad_index == 0xFFFFFFFF) & (bad_lo | bad_hi)):
                m.d.sync += [self.bad_index.eq(chk_addr + ~bad_lo),
                             self.bad_got_want.eq(Mux(bad_lo, Cat(want_lo, got_lo),
                                                      Cat(want_hi, got_hi)))]

        # ---- hr: engine --------------------------------------------------------
        burst = Signal(16)
        mode = Signal(3)
        left_w = Signal(ADDRESS_BITS + 1)
        left_r = Signal(ADDRESS_BITS + 1)
        addr_w = Signal(ADDRESS_BITS)
        addr_r = Signal(ADDRESS_BITS)
        n = Signal(16)
        half_w, half_r = Signal(), Signal()
        lo = Signal(16)
        phase = Signal()
        done_hr = Signal()
        is_w = Signal()

        m.d.comb += [core.wdata.eq(Mux(half_w, wfifo.r_data[16:], wfifo.r_data[:16])),
                     core.n.eq(n), core.wait.eq(self.wait), core.write.eq(is_w),
                     core.addr.eq(Mux(mode == MODE_REG, self.reg_addr,
                                      Mux(is_w, addr_w, addr_r)))]
        m.d.comb += core.reg.eq(mode == MODE_REG)

        with m.If(core.wready):
            m.d.comb += wfifo.r_en.eq(half_w)
            m.d.hr += half_w.eq(~half_w)
        with m.If(core.rvalid & (mode != MODE_REG)):
            m.d.hr += half_r.eq(~half_r)
            with m.If(~half_r):
                m.d.hr += lo.eq(core.rdata)
            with m.Else():
                m.d.comb += [rfifo.w_en.eq(1), rfifo.w_data.eq(Cat(lo, core.rdata))]
        with m.If(core.rvalid & (mode == MODE_REG)):
            m.d.hr += self.reg_value.eq(core.rdata)

        def clip(left):
            return Mux(left < burst, left, burst)

        busy = Signal()
        with m.If(busy):
            m.d.hr += self.run_cycles.eq(self.run_cycles + 1)

        with m.FSM(domain="hr") as fsm:
            with m.State("IDLE"):
                with m.If(go_ps.o):
                    m.d.hr += [burst.eq(self.burst & ~1), mode.eq(self.mode), done_hr.eq(0),
                               phase.eq(0), addr_w.eq(self.start_addr & ~1),
                               addr_r.eq(self.start_addr & ~1), half_w.eq(0), half_r.eq(0),
                               left_w.eq(Mux((self.mode == MODE_WRITE) | (self.mode == MODE_BOTH),
                                             self.total & ~1, 0)),
                               left_r.eq(Mux((self.mode == MODE_READ) | (self.mode == MODE_BOTH),
                                             self.total & ~1, 0)),
                               self.run_cycles.eq(0), self.words_w.eq(0), self.words_r.eq(0),
                               self.src_wait.eq(0), self.sink_wait.eq(0), self.xacts.eq(0),
                               self.missing.eq(0), self.straddled.eq(0)]
                    m.next = "PICK"
            with m.State("PICK"):
                m.d.comb += busy.eq(1)
                want_w = (left_w != 0) & ((mode != MODE_BOTH) | ~phase | (left_r == 0))
                with m.If(mode == MODE_REG):
                    m.d.hr += n.eq(1)
                    m.next = "REG"
                with m.Elif(((left_w == 0) & (left_r == 0)) | (burst == 0)):
                    m.next = "DONE"
                with m.Elif(want_w):
                    m.d.hr += [n.eq(clip(left_w)), is_w.eq(1)]
                    m.next = "WAIT_W"
                with m.Else():
                    m.d.hr += [n.eq(clip(left_r)), is_w.eq(0)]
                    m.next = "WAIT_R"
            with m.State("REG"):
                with m.If(core.idle):
                    m.d.comb += core.start.eq(1)
                    m.next = "XFER"
            with m.State("WAIT_W"):
                m.d.comb += busy.eq(1)
                ready = wfifo.r_level >= (n >> 1)
                with m.If(~ready):
                    m.d.hr += self.src_wait.eq(self.src_wait + 1)
                with m.If(ready & core.idle):
                    m.d.comb += core.start.eq(1)
                    m.d.hr += self.xacts.eq(self.xacts + 1)
                    m.next = "XFER"
            with m.State("WAIT_R"):
                m.d.comb += busy.eq(1)
                room = rfifo.w_level + (n >> 1) <= self.fifo_depth
                with m.If(~room):
                    m.d.hr += self.sink_wait.eq(self.sink_wait + 1)
                with m.If(room & core.idle):
                    m.d.comb += core.start.eq(1)
                    m.d.hr += self.xacts.eq(self.xacts + 1)
                    m.next = "XFER"
            with m.State("XFER"):
                m.d.comb += busy.eq(mode != MODE_REG)
                with m.If(core.done):
                    with m.If(mode == MODE_REG):
                        m.next = "DONE"
                    with m.Elif(is_w):
                        m.d.hr += [left_w.eq(left_w - n), addr_w.eq(addr_w + n),
                                   self.words_w.eq(self.words_w + n), phase.eq(1)]
                        m.next = "PICK"
                    with m.Else():
                        m.d.hr += [left_r.eq(left_r - n), addr_r.eq(addr_r + n),
                                   self.words_r.eq(self.words_r + n), phase.eq(0),
                                   self.missing.eq(self.missing + core.missing),
                                   self.straddled.eq(self.straddled + core.straddled)]
                        m.next = "PICK"
            with m.State("DONE"):
                m.d.hr += done_hr.eq(1)
                m.next = "IDLE"

        # `done` is masked from go until the hr side has cleared it.
        done_sync = Signal()
        m.submodules.done_ff = FFSynchronizer(done_hr, done_sync, o_domain="sync")
        stale = Signal()
        with m.If(self.go):
            m.d.sync += stale.eq(1)
        with m.Elif(~done_sync):
            m.d.sync += stale.eq(0)
        m.d.comb += self.done.eq(done_sync & ~stale)
        # Read asynchronously from sync: a snapshot for locating a stall, not a counter.
        # [7:0] engine state, [11:8] core state, [25:16] write-FIFO level (entries).
        m.d.comb += self.state.eq(Cat(fsm.state, Const(0, 8 - len(fsm.state)),
                                      core.fsm_state, Const(0, 4), wfifo.r_level))
        return m


class _PLL(Elaboratable):
    """EHXPLLL: CLKOP -> `domain`, optional CLKOS at +`phase90_domain` 90 degrees; reset until lock."""

    def __init__(self, *, domain, mhz, clki, phase90_domain=None):
        self.domain, self.mhz, self.clki, self.d90 = domain, mhz, clki, phase90_domain
        solved = solve_pll(mhz)
        if solved is None:
            raise ValueError(f"no PLL configuration gives {mhz} MHz; reachable near it: "
                             f"{reachable(mhz - 10, mhz + 10)}")
        self.clki_div, self.fb_div, self.div = solved

    def elaborate(self, platform):
        m = Module()
        m.domains += ClockDomain(self.domain)
        clk, locked = Signal(name=f"clk_{self.domain}"), Signal(name=f"lock_{self.domain}")
        extra = {}
        if self.d90:
            m.domains += ClockDomain(self.d90)
            clk90 = Signal(name=f"clk_{self.d90}")
            cph, fph = phase_params(self.div, 90.0)
            extra = dict(p_CLKOS_ENABLE="ENABLED", p_CLKOS_DIV=self.div,
                         p_CLKOS_CPHASE=cph, p_CLKOS_FPHASE=fph, o_CLKOS=clk90)
            m.d.comb += [ClockSignal(self.d90).eq(clk90), ResetSignal(self.d90).eq(~locked)]
            if platform is not None:
                platform.add_clock_constraint(clk90, self.mhz * 1e6)
        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8", a_MFG_ENABLE_FILTEROPAMP="1",
            a_MFG_GMCREF_SEL="2",
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            p_STDBY_ENABLE="DISABLED", p_DPHASE_SOURCE="DISABLED",
            p_OUTDIVIDER_MUXA="DIVA", p_OUTDIVIDER_MUXB="DIVB",
            p_OUTDIVIDER_MUXC="DIVC", p_OUTDIVIDER_MUXD="DIVD",
            p_CLKI_DIV=self.clki_div, p_CLKFB_DIV=self.fb_div,
            p_FEEDBK_PATH="CLKOP", p_CLKOP_ENABLE="ENABLED",
            p_CLKOP_DIV=self.div, p_CLKOP_CPHASE=self.div - 1, p_CLKOP_FPHASE=0,
            i_CLKI=self.clki, i_CLKFB=clk,
            i_RST=0, i_STDBY=0, i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk, o_LOCK=locked, **extra)
        m.d.comb += [ClockSignal(self.domain).eq(clk), ResetSignal(self.domain).eq(~locked)]
        if platform is not None:
            platform.add_clock_constraint(clk, self.mhz * 1e6)
        return m


class BaselineTop(Elaboratable):
    def __init__(self, *, ck_mhz, sync_mhz=SYNC_MHZ):
        self.ck_mhz, self.sync_mhz = float(ck_mhz), float(sync_mhz)

    def elaborate(self, platform):
        from luna.gateware.interface.jtag import JTAGRegisterInterface

        m = Module()
        clk60 = platform.request(platform.default_clk).i
        m.submodules.sync_pll = _PLL(domain="sync", mhz=self.sync_mhz, clki=clk60)
        m.submodules.hr_pll = _PLL(domain="hr", mhz=self.ck_mhz, clki=clk60,
                                   phase90_domain="hr90")

        pins = PinIntent()
        bus = platform.request("ram", 0)
        m.submodules.phy = phy = ECP5HyperBusPHY(bus=bus, pins=pins)
        core = HyperBusCore(ck_mhz=self.ck_mhz, pins=pins)
        m.submodules.core = DomainRenamer({"sync": "hr"})(core)
        m.submodules.engine = eng = SeqEngine(core=core)

        regs = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.regs = regs

        def rw(addr):
            sig = Signal(32, name=f"reg_{addr}")
            regs.add_register(addr, value_signal=sig)
            return sig

        go = rw(REG["GO"])
        go_prev = Signal()
        m.d.sync += go_prev.eq(go[0])
        m.d.comb += eng.go.eq(go[0] & ~go_prev)
        for name, sig in (("MODE", eng.mode), ("BURST", eng.burst), ("TOTAL", eng.total),
                          ("XOR", eng.xor), ("REG_ADDR", eng.reg_addr),
                          ("START_ADDR", eng.start_addr), ("SEED", eng.seed)):
            m.d.comb += sig.eq(rw(REG[name]))
        wait_reg = rw(REG["WAIT"])
        m.d.comb += eng.wait.eq(Mux(wait_reg[8], wait_reg[:5], core.wait.init))

        # DELAY: [6:0] taps, [7] direction, [8] apply (edge).
        delay_reg = rw(REG["DELAY"])
        apply_prev = Signal()
        m.d.sync += apply_prev.eq(delay_reg[8])
        m.submodules.delay_ps = delay_ps = PulseSynchronizer(i_domain="sync", o_domain="hr")
        m.d.comb += [delay_ps.i.eq(delay_reg[8] & ~apply_prev), phy.delay_load.eq(delay_ps.o),
                     phy.delay.eq(delay_reg[:7]), phy.delay_dir.eq(delay_reg[7])]

        # CK meter: hr cycles counted over 2**CK_WINDOW_BITS cycles of the 60 MHz pin.
        m.domains += ClockDomain("ref", reset_less=True)
        m.d.comb += ClockSignal("ref").eq(clk60)
        ref_cnt = Signal(CK_WINDOW_BITS + 1)
        m.d.ref += ref_cnt.eq(ref_cnt + 1)
        gate, gate_prev = Signal(), Signal()
        m.submodules.gate_ff = FFSynchronizer(ref_cnt[-1], gate, o_domain="hr")
        hr_cnt, ck_count = Signal(32), Signal(32)
        m.d.hr += gate_prev.eq(gate)
        with m.If(gate):
            m.d.hr += hr_cnt.eq(hr_cnt + 1)
        with m.Elif(gate_prev):
            m.d.hr += [ck_count.eq(hr_cnt), hr_cnt.eq(0)]
        regs.add_read_only_register(REG["CK_COUNT"], read=ck_count)

        status = Cat(eng.done, phy.delay_busy, phy.cflag, core.ready)
        regs.add_read_only_register(REG["ID"], read=APPLET_ID)
        regs.add_read_only_register(REG["STATUS"], read=status)
        regs.add_read_only_register(REG["CK_KHZ"], read=int(round(self.ck_mhz * 1000)))
        regs.add_read_only_register(REG["SYNC_KHZ"], read=int(round(self.sync_mhz * 1000)))
        regs.add_read_only_register(REG["MAX_BURST"], read=core.max_burst)
        for name in ("RUN_CYCLES", "WORDS_W", "WORDS_R", "SRC_WAIT", "SINK_WAIT", "XACTS",
                     "MISSING", "CHECKED", "ERRORS", "BAD_INDEX", "BAD_GOT_WANT",
                     "GENERATED", "REG_VALUE", "STRADDLED", "BAD_BITS", "STATE"):
            regs.add_read_only_register(REG[name], read=getattr(eng, name.lower()))
        return m
