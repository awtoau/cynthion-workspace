# SPDX-License-Identifier: BSD-3-Clause
"""HyperBus x8 controller for the sequential baseline (#581), written from the datasheet. REFERENCE core (README.md here); the SoC peripheral (#588) reuses it.

Sources (full citations in docs/chips/hyperram/sequential-baseline.md):
- W956A8MBYA datasheet rev A01-006: CA layout (Tables 1/2), data placement
  (Figs 6/7), AC timing (Tables 21/22), tCSM (Table 24), reset (tRP/tRH/tRPH).
- LiteX `litex/soc/cores/hyperbus.py`: latency count starts 1 CK before the CA ends.
- LUNA `psram.py`: realigning a word whose bytes straddle two IDDR cycles.
- prjtrellis `ecppll`: EHXPLLL phase arithmetic.

Shape:
- One CK per `hr` cycle, 16 bits per cycle (DDR x8). Pin intent is registered;
  `ECP5HyperBusPHY` maps it to ODDRX1F/IDDRX1F, the sim maps it to a model.
- CK is clocked off a 90-degree PLL output, so its edges sit mid-byte (writes, CA).
- Reads: RWDS is sampled with DQ by the same IDDR; a rising RWDS sample marks byte A.
  Input DELAYF taps are runtime, so the sample point can be swept across the eye.
- CS# and OE are fabric registers whose latency differs from ODDR's; GUARD_CYCLES
  with CS# Low and CK stopped either side of the clocked phase absorb that.
"""

from amaranth import (Cat, ClockDomain, ClockSignal, Const, Elaboratable, Instance, Module, Mux,
                      ResetSignal, Signal)

# Table 24 (CR1[1:0]=01b default): CS# Low at most 4 us below 85 C.
T_CSM_NS = 4000.0
# Table 21, 166 MHz grade: CS# High >= 6 ns between transactions; 2 cycles is >= 12 ns at <= 166 MHz.
CS_HIGH_CYCLES = 2
# CS# Low, CK stopped, before the first and after the last CK, with OE held: covers
# fabric (CS#/OE) vs ODDR (CK/DQ/RWDS) output latency. Board: ODDR lags by ~3 (#581 doc).
GUARD_CYCLES = 3
POST_GUARD_CYCLES = 6
# Ceiling of the runtime latency register; the burst cap assumes it.
WAIT_MAX = 31
# CR0 default 0x8F2F: 7 clocks, fixed 2x. Data starts 2L-1 CK after the last CA CK.
WAIT_DEFAULT = 2 * 7 - 1
# Read tail: the last word's round trip after CK stops. Expected <= 7 cycles
# (ODDR 2 + IDDR 2 + tCKD 7 ns = 2 CK at 166 MHz, + 1); x1.25 -> 9. Expiry: rest filled, counted missing.
TAIL_CYCLES = 9
# Reset (datasheet tRP 200 ns, tRH 200 ns, tRPH 400 ns), minimums: held 2 us each.
RESET_NS = 2000.0


def max_burst_words(ck_mhz):
    """Longest even burst whose CS# Low time fits tCSM at the longest latency setting."""
    budget = int(T_CSM_NS * ck_mhz / 1000.0)
    return (budget - (GUARD_CYCLES + POST_GUARD_CYCLES + 3 + WAIT_MAX + TAIL_CYCLES)) & ~1


class PinIntent:
    """What the pins do this cycle (registered by the core), and the IDDR samples back."""

    def __init__(self):
        self.cs = Signal()            # 1 = selected (CS# Low)
        self.ck_en = Signal()
        self.ck_en_next = Signal()    # comb: `ck_en` one cycle early (the PHY's hr90 crossing)
        self.dq_o = Signal(16)        # [15:8] byte A (first half), [7:0] byte B
        self.dq_oe = Signal()
        self.rwds_o = Signal(2)       # [1] first half
        self.rwds_oe = Signal()
        self.reset = Signal()         # 1 = RESET# Low
        self.dq_q0 = Signal(8)        # earlier half-cycle sample
        self.dq_q1 = Signal(8)
        self.rwds_q0 = Signal()
        self.rwds_q1 = Signal()


class HyperBusCore(Elaboratable):
    """Burst engine at the pin-intent level, in `sync` (rename to `hr`)."""

    def __init__(self, *, ck_mhz, pins=None, reg_write=False, masked=False):
        # reg_write: register WRITE (zero latency, RWDS not driven; A01-006 s7.4, Fig 32).
        # masked: `wmask` drives RWDS per byte on writes (Fig 7). Both off = the #581 core.
        self.ck_mhz = ck_mhz
        self.reg_write, self.masked = reg_write, masked
        self.pins = pins or PinIntent()
        self.max_burst = max_burst_words(ck_mhz)
        self.start = Signal()               # taken while `idle`
        self.write = Signal()
        self.reg = Signal()                 # register space, one word; reads unless reg_write
        self.addr = Signal(32)              # word address
        self.n = Signal(16)
        self.wait = Signal(5, init=WAIT_DEFAULT)
        self.idle = Signal()
        self.ready = Signal()               # reset sequence finished
        self.done = Signal()                # one cycle, transaction over
        self.wdata = Signal(16)             # taken on cycles where `wready`
        self.wready = Signal()
        self.wmask = Signal(2)              # with `masked`: [1] masks byte A, [0] byte B
        self.rvalid = Signal()
        self.rdata = Signal(16)
        self.missing = Signal(16)           # words of the last read the tail filled
        self.straddled = Signal()           # last read word straddled IDDR cycles
        self.reset_req = Signal()           # pulse: run the RESET# sequence again
        self.fsm_state = Signal(4)

    def elaborate(self, platform):
        m = Module()
        p = self.pins

        is_write = Signal()
        is_reg = Signal()
        n = Signal(16)
        wait = Signal(5)
        ca = Signal(48)
        k = Signal(16)                      # CK index within the clocked phase
        cnt = Signal(16)
        received = Signal(16)
        data_start = Signal(16)
        filling = Signal()
        listen = Signal()                   # read data may be arriving

        m.d.comb += data_start.eq(3 + wait)
        reg_w = Const(int(self.reg_write), 1)
        drive_rwds = is_write & ~is_reg
        rst_cycles = int(RESET_NS * self.ck_mhz / 1000.0)
        rst_cnt = Signal(range(2 * rst_cycles + 1), init=2 * rst_cycles)

        m.d.sync += [p.cs.eq(0), p.ck_en.eq(0), p.dq_oe.eq(0), p.rwds_oe.eq(0),
                     p.rwds_o.eq(0), p.reset.eq(rst_cnt > rst_cycles)]

        with m.FSM() as fsm:
            with m.State("RESET"):
                with m.If(rst_cnt == 0):
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += rst_cnt.eq(rst_cnt - 1)
            with m.State("IDLE"):
                m.d.comb += [self.idle.eq(1), self.ready.eq(1)]
                with m.If(self.reset_req):
                    m.d.sync += rst_cnt.eq(2 * rst_cycles)
                    m.next = "RESET"
                with m.Elif(self.start):
                    m.d.sync += [
                        is_write.eq(self.write & (~self.reg | reg_w)), is_reg.eq(self.reg),
                        n.eq(Mux(self.reg, 1, self.n)),
                        wait.eq(Mux(self.reg & self.write & reg_w, 0, self.wait)),
                        ca.eq(Cat(self.addr[0:3], Const(0, 13), self.addr[3:32],
                                  Const(1, 1), self.reg, ~self.write | (self.reg & ~reg_w))),
                        cnt.eq(0), k.eq(0), received.eq(0), self.missing.eq(0),
                        p.cs.eq(1)]
                    m.next = "PRE"
            with m.State("PRE"):
                m.d.sync += [p.cs.eq(1), p.dq_oe.eq(1), p.dq_o.eq(ca[32:48]), cnt.eq(cnt + 1)]
                with m.If(cnt == GUARD_CYCLES - 1):
                    m.next = "CLOCK"
            with m.State("CLOCK"):
                m.d.sync += [p.cs.eq(1), p.ck_en.eq(1), k.eq(k + 1)]
                m.d.comb += listen.eq(k >= data_start - 2)
                with m.If(k < 3):
                    m.d.sync += [p.dq_oe.eq(1),
                                 p.dq_o.eq(Mux(k == 0, ca[32:48], Mux(k == 1, ca[16:32], ca[0:16])))]
                with m.Elif(k < data_start):
                    # Read: OE held until the ODDR'd CA has left the pins, well before data.
                    m.d.sync += [p.dq_oe.eq(is_write | (k < data_start - 4)),
                                 p.rwds_oe.eq(is_write & (k >= 7))]
                with m.Else():
                    m.d.sync += [p.dq_oe.eq(is_write), p.rwds_oe.eq(drive_rwds)]
                    with m.If(is_write):
                        m.d.comb += self.wready.eq(1)
                        m.d.sync += p.dq_o.eq(self.wdata)
                        if self.masked:
                            m.d.sync += p.rwds_o.eq(self.wmask)
                with m.If(k == data_start + n - 1):
                    m.d.sync += cnt.eq(0)
                    m.next = "TAIL"
            with m.State("TAIL"):
                m.d.comb += listen.eq(1)
                m.d.sync += [p.cs.eq(1), p.dq_oe.eq(is_write), p.rwds_oe.eq(drive_rwds),
                             cnt.eq(cnt + 1)]
                with m.If(is_write | (received == n)):
                    m.d.sync += cnt.eq(0)
                    m.next = "POST"
                with m.Elif(cnt == TAIL_CYCLES - 1):
                    m.d.sync += [cnt.eq(0), filling.eq(1)]
                    m.next = "FILL"
            with m.State("FILL"):
                m.d.sync += p.cs.eq(1)
                with m.If(received == n):
                    m.d.sync += [cnt.eq(0), filling.eq(0)]
                    m.next = "POST"
            with m.State("POST"):
                m.d.sync += [p.cs.eq(1), p.dq_oe.eq(is_write), p.rwds_oe.eq(drive_rwds),
                             cnt.eq(cnt + 1)]
                with m.If(cnt == POST_GUARD_CYCLES - 1):
                    m.d.sync += cnt.eq(0)
                    m.next = "HIGH"
            with m.State("HIGH"):
                m.d.sync += cnt.eq(cnt + 1)
                with m.If(cnt == CS_HIGH_CYCLES - 1):
                    m.d.comb += self.done.eq(1)
                    m.next = "IDLE"

        # ---- receive -------------------------------------------------------
        # Sample stream per cycle is (q0, q1). A rising RWDS sample is byte A:
        # at q0 the word is (q0, q1); at the previous q1 it is (prev q1, q0).
        prev_r0, prev_r1, prev_d1 = Signal(), Signal(), Signal(8)
        m.d.sync += [prev_r0.eq(p.rwds_q0), prev_r1.eq(p.rwds_q1), prev_d1.eq(p.dq_q1)]
        aligned = Signal()
        straddle = Signal()
        m.d.comb += [aligned.eq(~prev_r1 & p.rwds_q0),
                     straddle.eq(~prev_r0 & prev_r1)]
        window = Signal()
        m.d.sync += window.eq(~is_write & listen)
        with m.If(filling & (received < n)):
            m.d.comb += [self.rvalid.eq(1), self.rdata.eq(0)]
            m.d.sync += [received.eq(received + 1), self.missing.eq(self.missing + 1)]
        with m.Elif(window & (received < n) & (aligned | straddle)):
            m.d.comb += [self.rvalid.eq(1),
                         self.rdata.eq(Mux(aligned, Cat(p.dq_q1, p.dq_q0),
                                           Cat(p.dq_q0, prev_d1)))]
            m.d.sync += [received.eq(received + 1), self.straddled.eq(straddle)]
        m.d.comb += [self.fsm_state.eq(fsm.state), p.ck_en_next.eq(fsm.ongoing("CLOCK"))]
        return m


class ECP5HyperBusPHY(Elaboratable):
    """Pin intent to ECP5 IOLOGIC. Domains: `hr` (data), `hr90` (CK, +90 degrees).

    `delay` (7 bits, `hr`) is the input DELAYF tap target on DQ and RWDS, applied on `delay_load`.
    """

    def __init__(self, *, bus, pins):
        self.bus, self.pins = bus, pins
        self.delay = Signal(7)
        self.delay_load = Signal()
        self.delay_busy = Signal()
        self.delay_dir = Signal()
        self.cflag = Signal()

    def elaborate(self, platform):
        m = Module()
        p, bus = self.pins, self.bus

        def oddr(d0, d1, q, domain="hr"):
            m.submodules += Instance("ODDRX1F", i_D0=d0, i_D1=d1, i_SCLK=ClockSignal(domain),
                                     i_RST=ResetSignal(domain), o_Q=q)

        # CK lags DQ a quarter cycle. hr -> hr90 via an hr falling-edge stage: half and
        # three-quarter cycle budgets instead of one quarter (#628); CK edges unchanged.
        m.domains.hr_neg = ClockDomain("hr_neg", clk_edge="neg", reset_less=True, local=True)
        m.d.comb += ClockSignal("hr_neg").eq(ClockSignal("hr"))
        ck_en_neg, ck_en90 = Signal(), Signal()
        m.d.hr_neg += ck_en_neg.eq(p.ck_en_next)
        m.d.hr90 += ck_en90.eq(ck_en_neg)
        oddr(ck_en90, 0, bus.clk.o, "hr90")

        for i in range(8):
            oddr(p.dq_o[8 + i], p.dq_o[i], bus.dq.o[i])
        oddr(p.rwds_o[1], p.rwds_o[0], bus.rwds.o)
        m.d.comb += [bus.dq.oe.eq(p.dq_oe), bus.rwds.oe.eq(p.rwds_oe),
                     bus.cs.o.eq(p.cs), bus.reset.o.eq(p.reset)]

        # Input DELAYF stepper: LOADN to tap 0, then `delay` MOVE pulses (4 cycles high/low).
        loadn, move = Signal(init=1), Signal()
        step_left, phase = Signal(8), Signal(4)
        with m.FSM(domain="hr"):
            with m.State("IDLE"):
                with m.If(self.delay_load):
                    m.d.hr += [loadn.eq(0), phase.eq(0), step_left.eq(self.delay)]
                    m.next = "LOAD"
            with m.State("LOAD"):
                m.d.comb += self.delay_busy.eq(1)
                m.d.hr += phase.eq(phase + 1)
                with m.If(phase == 7):
                    m.d.hr += [loadn.eq(1), phase.eq(0)]
                    m.next = "STEP"
            with m.State("STEP"):
                m.d.comb += self.delay_busy.eq(1)
                m.d.hr += phase.eq(phase + 1)
                with m.If(step_left == 0):
                    m.next = "IDLE"
                with m.Elif(phase == 3):
                    m.d.hr += move.eq(1)
                with m.Elif(phase == 7):
                    m.d.hr += [move.eq(0), step_left.eq(step_left - 1), phase.eq(0)]

        dq_i = Signal(8)
        rwds_i = Signal()
        cflags = Signal(9)
        for i, (pad, z) in enumerate([(bus.dq.i[j], dq_i[j]) for j in range(8)]
                                     + [(bus.rwds.i, rwds_i)]):
            m.submodules += Instance("DELAYF", i_A=pad, i_LOADN=loadn, i_MOVE=move,
                                     i_DIRECTION=self.delay_dir, o_Z=z, o_CFLAG=cflags[i],
                                     p_DEL_MODE="USER_DEFINED", p_DEL_VALUE=0)
        m.d.comb += self.cflag.eq(cflags.any())
        for i in range(8):
            m.submodules += Instance("IDDRX1F", i_D=dq_i[i], i_SCLK=ClockSignal("hr"),
                                     i_RST=ResetSignal("hr"),
                                     o_Q0=p.dq_q0[i], o_Q1=p.dq_q1[i])
        m.submodules += Instance("IDDRX1F", i_D=rwds_i, i_SCLK=ClockSignal("hr"),
                                 i_RST=ResetSignal("hr"), o_Q0=p.rwds_q0, o_Q1=p.rwds_q1)
        return m
