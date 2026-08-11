#!/usr/bin/env python3
#
# Terminate a Wishbone cycle nobody else will. #409.
# SPDX-License-Identifier: BSD-3-Clause

"""
ERR for an address no window claims, and for one nobody answers.

## The defect

`amaranth_soc.wishbone.Decoder.elaborate` is one `m.Switch` with a `m.Case` per
window and **no default**. An address matching none of them drives no ACK and no
ERR, so a Wishbone classic initiator holds CYC and STB and waits forever.

That is not an obscure corner on this SoC. VexiiRiscv routes by PMA region and
the I/O region is declared `base=f0000000,size=10000000` (`cpu/cpu.py`) -- the
whole top 256 MiB -- while the decoder claims about twenty windows inside it.
Every gap is a hang the core believes is a legal access, so no trap fires: it is
simply waiting. `bist status` on the shipping variant read `f0000800`, which that
variant does not decode, and took the board with it (#409).

The comment at `top.py:HYPERRAM_CK_BASE` had already recorded the shape of it --
"a misaligned one decodes to nothing and hangs the CPU on the first read, with no
error at elaboration" -- and `soc_run.py` carries a `--features hyperram-bist`
guard whose stated reason is the same hang.

## Two mechanisms, and the second is not redundant

**UNCLAIMED.** The window patterns, compared again here, with the answer
inverted: a request matching no window is answered ERR in the cycle it is made.
This makes the fabric's address map TOTAL, so it cannot disagree with the CPU's
map by omission.

**TIMEOUT.** A counter, so a subordinate that IS decoded and has stopped
answering also terminates. This catches strictly more than the first: a decoded
peripheral stuck mid-transaction is invisible to any address compare, and that is
the still-unexplained half of #409 -- the BIST variant wedged with the engine
present and previously answering.

## The fence

A timed-out subordinate may still be mid-transaction and may assert ACK after the
initiator has given up and moved on -- which would terminate somebody else's
cycle. So a timeout latches `fenced`, which holds CYC and STB away from the
subordinate until the initiator releases the cycle it just failed. A request
arriving inside that window is answered ERR immediately rather than restrobing a
subordinate that is known sick.

## Where it goes

Downstream of `bus/wishbone_pipe.RegisteredResponse`, upstream of the decoder:

    wiring.connect(m, arbiter.bus,     bus_pipe.intr_bus)
    wiring.connect(m, bus_pipe.sub_bus, fault.intr_bus)
    wiring.connect(m, fault.sub_bus,    decoder.bus)

Not upstream of the pipe: the ERR here is combinational from the address, and in
front of the pipe it would land in the CPU's commit path -- the 16.45 ns run the
pipe was added to cut. Behind it, the pipe registers it like any other response.

## What it costs

One comparator over the same window patterns the decoder already compares, a
counter, and a handful of flip-flops. `scripts/soc_bus_sim.py` runs both
mechanisms against a decoder and reports the price of neither, because neither is
on the path of an access that succeeds.
"""

from amaranth              import Module, Signal
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out
from amaranth.utils        import exact_log2

from amaranth_soc          import wishbone


# What one maximal transfer from the SPI controller costs, in SCK beats.
#
# `phy.length` is a six-bit CSR field (`luna_soc/.../port.py`), so 63 bits on one
# lane is the longest transfer firmware can command, and the crossbar may let one
# through between any two phases of a memory-mapped read.
CONTENDING_BEATS = 63


def worst_ack_cycles(*, mode, divisor):
    """The slowest a healthy subordinate on this decoder can answer, in cycles.

    The memory-mapped flash is that subordinate and nothing else is close: every
    CSR peripheral here answers in 5 (four byte cycles through the 32/8 bridge
    plus one), block RAM in 1, and the HyperRAM window in 20.

    Per SPI phase, `luna_soc`'s PHY costs `2 * beats * (1 + divisor) + 3` cycles
    -- one to accept, two per SCK beat, one to end the transfer and one to hand
    the status back -- plus one cycle to leave IDLE, and each phase may be
    preceded by one maximal transfer from the controller sharing the crossbar.

    `mode` is an entry of `peripherals/flash.READ_MODES`. In cycles, not
    nanoseconds, so it does not move with `SYNC_MHZ`.
    """
    phases = [(8, 1), (24, mode["addr_width"])]
    if mode["dummy_bits"]:
        phases.append((mode["dummy_bits"], mode["bus_width"]))
    phases.append((32, mode["bus_width"]))

    def transfer(beats):
        return 2 * beats * (1 + divisor) + 3

    cold = 1 + sum(transfer(bits // width) for bits, width in phases)
    return cold + len(phases) * transfer(CONTENDING_BEATS)


class BusFault(wiring.Component):
    """Answer ERR where the decoder answers nothing at all.

    `decoder` is read at ELABORATION, not at construction, so it may be
    instantiated before the last `decoder.add()` -- which it has to be, since the
    windows are added as the peripherals are built.
    """

    def __init__(self, *, decoder, timeout, addr_width, data_width,
                 granularity=None, features=frozenset()):
        if "err" not in features:
            raise ValueError("BusFault exists to raise ERR; the bus must carry it")
        if timeout < 2:
            raise ValueError(f"a {timeout}-cycle bus timeout would fire on every "
                             f"subordinate that answers in more than one cycle")

        self._decoder = decoder
        self._timeout = timeout

        # What has been terminated here, for whoever wants to report it. Sticky
        # counts rather than strobes: the CPU samples whenever it is asked, and a
        # strobe is high for one cycle in sixty million.
        self.unclaimed_count = Signal(8)
        self.timeout_count = Signal(8)
        # The longest any request waited, in cycles, saturating. This is the
        # measurement that says whether `timeout` has margin -- a board reporting
        # a high-water mark near the bound is one about to fault spuriously.
        self.worst_wait = Signal(range(timeout + 1))

        signature = dict(addr_width=addr_width, data_width=data_width,
                         granularity=granularity, features=features)
        super().__init__({
            "intr_bus": In(wishbone.Signature(**signature)),
            "sub_bus": Out(wishbone.Signature(**signature)),
        })

    def elaborate(self, platform):
        m = Module()

        intr = self.intr_bus
        sub = self.sub_bus

        # Is this address inside a window somebody claimed? The same patterns
        # `Decoder.elaborate` switches on, and the same shortening: the decoder's
        # `adr` is a word address while a memory-map pattern is a byte address.
        claimed = Signal()
        granularity_bits = exact_log2(intr.data_width // intr.granularity)
        with m.Switch(intr.adr):
            for _map, _name, (pattern, _ratio) in \
                    self._decoder.bus.memory_map.window_patterns():
                with m.Case(pattern[:-granularity_bits or None]):
                    m.d.comb += claimed.eq(1)

        # A request that is on the bus and has not been answered yet.
        asking = intr.cyc & intr.stb & ~sub.ack & ~sub.err

        # How long this one has waited. Saturating, so the comparison below is
        # equality and the counter cannot wrap past its own bound.
        waited = Signal(range(self._timeout + 1))
        with m.If(~asking):
            m.d.sync += waited.eq(0)
        with m.Elif(waited != self._timeout):
            m.d.sync += waited.eq(waited + 1)

        unclaimed = asking & ~claimed
        expired = asking & (waited == self._timeout)

        # The edge at which the bound is REACHED, one cycle before `expired`.
        #
        # Everything latched below is latched from this rather than from
        # `expired`, because an initiator drops CYC in the same cycle it is
        # answered: sampling `expired` at the following edge samples it after
        # the request it describes has already gone.
        striking = asking & (waited == self._timeout - 1)

        # A subordinate that missed its bound is not to be strobed again until
        # the initiator has let go of the cycle it failed.
        fenced = Signal()
        with m.If(striking):
            m.d.sync += fenced.eq(1)
        with m.Elif(~intr.cyc):
            m.d.sync += fenced.eq(0)

        m.d.comb += [
            # CYC and STB are the two the fence withholds. Everything else may
            # follow through unconditionally: with neither of these asserted, no
            # subordinate acts on any of it.
            sub.cyc.eq(intr.cyc & ~fenced),
            sub.stb.eq(intr.stb & ~fenced),

            sub.adr.eq(intr.adr),
            sub.sel.eq(intr.sel),
            sub.we.eq(intr.we),
            sub.dat_w.eq(intr.dat_w),

            intr.ack.eq(sub.ack),
            intr.dat_r.eq(sub.dat_r),
            # ERR from the subordinate, from the address compare, from the
            # bound, or from the fence -- a request arriving while a sick
            # subordinate is being held off is answered rather than queued.
            intr.err.eq(sub.err | unclaimed | expired
                        | (fenced & intr.cyc & intr.stb)),
        ]

        for optional in ("cti", "bte", "lock"):
            if hasattr(sub, optional):
                m.d.comb += getattr(sub, optional).eq(getattr(intr, optional))

        # --- the account, for a CSR or a testbench --------------------------
        with m.If(unclaimed & (self.unclaimed_count != 0xff)):
            m.d.sync += self.unclaimed_count.eq(self.unclaimed_count + 1)
        with m.If(striking & (self.timeout_count != 0xff)):
            m.d.sync += self.timeout_count.eq(self.timeout_count + 1)
        with m.If(asking & (waited > self.worst_wait)):
            m.d.sync += self.worst_wait.eq(waited)

        return m
