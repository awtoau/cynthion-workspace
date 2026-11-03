# SPDX-License-Identifier: BSD-3-Clause
"""Option (b): synchronous 2:1 crossing; `fast` (120 MHz) and `usb` (60 MHz) from ONE PLL.

- Requires: both clocks are outputs of the same EHXPLLL, `usb` edges T/4 after a `fast` edge
  (T = fast period). docs/ulpi-bridge.md has the clocking, reset and failure analysis.
- No synchronisers: one-word mailboxes with toggle handshakes, sampled directly across.
- `fast` writes to usb-read registers only on the `safe` edge (3T/4 after a usb edge).
- PhaseCheck holds the bridge in reset until qualified; any later miss sets sticky `fault`.
"""

from amaranth import (ClockDomain, ClockSignal, DomainRenamer, Elaboratable, Module,
                      ResetInserter, Signal)
from amaranth.lib.cdc import AsyncFFSynchronizer
from amaranth.lib.fifo import SyncFIFOBuffered

from .async_bridge import DEPTH, BridgePorts
from .stream import WORD_BITS, Packer, Unpacker

QUALIFY = 16    # consecutive good usb periods before the bridge leaves reset


class PhaseCheck(Elaboratable):
    """Samples a usb-domain toggle on fast rising and falling edges.

    - Good: the toggle lands in the first half of every second fast cycle, never the second half.
    - Catches any ratio other than 2:1, frequency drift, a stopped usb clock, and phase out of window.
    """

    def __init__(self, *, usb="usb", fast="fast", enable=True):
        # enable=False: sim-only mutation, the check removed (`ok` high, never `fault`)
        self.usb, self.fast, self.enable = usb, fast, enable
        self.safe = Signal()        # fast: the coming edge is 3T/4 after a usb edge
        self.ok = Signal()          # fast: qualified and no fault
        self.fault = Signal()       # fast: sticky until `clear`
        self.clear = Signal()
        self.misses = Signal(16)    # fast: bad samples seen, saturating

    def elaborate(self, platform):
        m = Module()
        neg = f"{self.fast}_neg"
        m.domains += ClockDomain(neg, clk_edge="neg", reset_less=True, local=True)
        m.d.comb += ClockSignal(neg).eq(ClockSignal(self.fast))
        t60 = Signal(reset_less=True)
        m.d[self.usb] += t60.eq(~t60)
        fn = Signal(reset_less=True)
        m.d[neg] += fn.eq(t60)
        r, rd, f, xq = Signal(), Signal(), Signal(), Signal()
        m.d[self.fast] += [r.eq(t60), rd.eq(r), f.eq(fn)]
        x = rd != f                 # toggle in the first half of the previous cycle
        late = f != r               # toggle in its second half
        bad = late | (x == xq)
        m.d[self.fast] += xq.eq(x)
        warm = Signal(2)
        good = Signal(range(QUALIFY + 1))
        qualified = Signal()
        with m.If(warm != 3):
            m.d[self.fast] += warm.eq(warm + 1)
        with m.Elif(bad & self.enable):
            m.d[self.fast] += good.eq(0)
            with m.If(self.misses != 0xFFFF):
                m.d[self.fast] += self.misses.eq(self.misses + 1)
            with m.If(qualified):
                m.d[self.fast] += self.fault.eq(1)
        with m.Elif(x & (good != QUALIFY)):
            m.d[self.fast] += good.eq(good + 1)
        with m.If(good == QUALIFY):
            m.d[self.fast] += qualified.eq(1)
        with m.If(self.clear):
            m.d[self.fast] += [self.fault.eq(0), qualified.eq(0), good.eq(0)]
        m.d.comb += self.safe.eq(xq)
        m.d.comb += self.ok.eq(qualified & ~self.fault if self.enable else 1)
        return m


class _ToFast(Elaboratable):
    """usb -> fast mailbox. Holder in usb, taker in fast; `m_tog != a_tog` means full."""

    def __init__(self, usb, fast, safe):
        self.usb, self.fast, self.safe = usb, fast, safe
        self.i_word, self.i_valid, self.i_ready = Signal(WORD_BITS), Signal(), Signal()
        self.o_word, self.o_valid, self.o_ready = Signal(WORD_BITS), Signal(), Signal()
        self.a_tog = Signal()       # fast-launched, read by usb

    def elaborate(self, platform):
        m = Module()
        data, m_tog, a_tog = Signal(WORD_BITS), Signal(), self.a_tog
        m.d.comb += self.i_ready.eq(m_tog == a_tog)
        with m.If(self.i_valid & self.i_ready):
            m.d[self.usb] += [data.eq(self.i_word), m_tog.eq(~m_tog)]
        m.d.comb += [self.o_word.eq(data), self.o_valid.eq((m_tog != a_tog) & self.safe)]
        with m.If(self.o_valid & self.o_ready):
            m.d[self.fast] += a_tog.eq(~a_tog)
        return m


class _ToUsb(Elaboratable):
    """fast -> usb mailbox. Holder in fast (loads only on `safe`), taker in usb."""

    def __init__(self, usb, fast, safe):
        self.usb, self.fast, self.safe = usb, fast, safe
        self.i_word, self.i_valid, self.i_ready = Signal(WORD_BITS), Signal(), Signal()
        self.o_word, self.o_valid, self.o_ready = Signal(WORD_BITS), Signal(), Signal()
        self.data, self.t_tog = Signal(WORD_BITS), Signal()     # fast-launched, read by usb

    def elaborate(self, platform):
        m = Module()
        data, t_tog, a_tog = self.data, self.t_tog, Signal()
        m.d.comb += self.i_ready.eq((t_tog == a_tog) & self.safe)
        with m.If(self.i_valid & self.i_ready):
            m.d[self.fast] += [data.eq(self.i_word), t_tog.eq(~t_tog)]
        m.d.comb += [self.o_word.eq(data), self.o_valid.eq(t_tog != a_tog)]
        with m.If(self.o_valid & self.o_ready):
            m.d[self.usb] += a_tog.eq(~a_tog)
        return m


class _Core(Elaboratable):
    def __init__(self, outer, run_usb, run_fast):
        self.o, self.run_usb, self.run_fast = outer, run_usb, run_fast

    def elaborate(self, platform):
        o, m = self.o, Module()
        m.submodules.pack = pack = Packer(domain=o.usb, swap=o.swap)
        m.submodules.unpack = unpack = Unpacker(domain=o.usb)
        m.submodules.to_fast = tf = o.to_fast
        m.submodules.to_usb = tu = o.to_usb
        fifo_kw = dict(width=WORD_BITS, depth=o.depth + 1)     # +1: the output register
        m.submodules.rx_fifo = rxf = _fast(SyncFIFOBuffered(**fifo_kw), o.fast)
        m.submodules.tx_fifo = txf = _fast(SyncFIFOBuffered(**fifo_kw), o.fast)
        o._wire_bytes(m, pack, unpack, o.usb, rx_gate=self.run_usb, overflow=False)
        m.d.comb += [tf.i_word.eq(pack.o_word), tf.i_valid.eq(pack.o_valid),
                     pack.o_ready.eq(tf.i_ready),
                     rxf.w_data.eq(tf.o_word), rxf.w_en.eq(tf.o_valid), tf.o_ready.eq(rxf.w_rdy),
                     o.rxw_word.eq(rxf.r_data), o.rxw_valid.eq(rxf.r_rdy),
                     rxf.r_en.eq(o.rxw_ready),
                     txf.w_data.eq(o.txw_word), txf.w_en.eq(o.txw_valid),
                     o.txw_ready.eq(txf.w_rdy & self.run_fast),
                     tu.i_word.eq(txf.r_data), tu.i_valid.eq(txf.r_rdy), txf.r_en.eq(tu.i_ready),
                     unpack.i_word.eq(tu.o_word), unpack.i_valid.eq(tu.o_valid),
                     tu.o_ready.eq(unpack.i_ready)]
        return m


def _fast(fifo, fast):
    return DomainRenamer({"sync": fast})(fifo)


class RatioBridge(Elaboratable, BridgePorts):
    """`check=False`, `gate=False`, `swap=True` are sim-only mutations (negative controls)."""

    def __init__(self, *, depth=DEPTH, usb="usb", fast="fast", check=True, swap=False, gate=True):
        BridgePorts.__init__(self)
        self.depth, self.usb, self.fast, self.swap, self.gate = depth, usb, fast, swap, gate
        self._safe = Signal()
        self.to_fast = _ToFast(usb, fast, self._safe)
        self.to_usb = _ToUsb(usb, fast, self._safe)
        self.check = PhaseCheck(usb=usb, fast=fast, enable=check)
        self.up = Signal()      # usb: bridge out of reset; the ULPI link starts only after this

    def elaborate(self, platform):
        m = Module()
        m.submodules.check = chk = self.check
        rst_fast, rst_usb = Signal(), Signal()
        m.d.comb += rst_fast.eq(~chk.ok)
        m.submodules.rst_usb = AsyncFFSynchronizer(rst_fast, rst_usb, o_domain=self.usb)
        m.d.comb += self.up.eq(~rst_usb)
        m.d.comb += self._safe.eq(chk.safe if self.gate else 1)
        core = _Core(self, ~rst_usb, ~rst_fast)
        m.submodules.core = ResetInserter({self.fast: rst_fast, self.usb: rst_usb})(core)
        self._track_overflow(m, self.usb)     # outside the core: counts bytes refused in reset
        return m
