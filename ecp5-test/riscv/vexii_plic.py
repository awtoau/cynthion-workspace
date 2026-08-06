#!/usr/bin/env python3
#
# The RISC-V Platform-Level Interrupt Controller, as a CSR peripheral.
# SPDX-License-Identifier: BSD-3-Clause

"""
A RISC-V PLIC: many interrupt sources onto the one machine external wire.

Standard, not bespoke, so that `firmware/cynthion-soc/src/plic.rs` and RTIC's
RISC-V backend both work unchanged -- QEMU's `-M virt` has a PLIC too, which is
what makes `scripts/soc_test.py` evidence about the interrupt path that ships.
The alternatives weighed are in `../../docs/architecture.md`.

## Register map -- fixed by riscv-plic-spec 1.0.0, not chosen here

Byte offsets from the base of a 4 MiB window:

    0x000000 + 4*i   priority[i]   RW   per source, i in 1..sources
    0x001000         pending       R    bit i: source i is requesting
    0x002000         enable        RW   bit i: context 0 accepts source i
    0x200000         threshold     RW   priority <= this is masked
    0x200004         claim (R)     take the highest-priority pending source
                     complete (W)  finish the source whose id is written

  * One context: hart 0, machine mode. `virt`'s context 0 is the same, so no
    stride calculation is needed on either.
  * Source 0 does not exist -- it is the "nothing pending" answer to a claim.
  * Offsets are the spec's; WIDTHS are only as wide as this design has bits
    for. Bytes above the implemented width read zero and ignore writes, which
    is what the spec says reserved bits do.

## Register discipline

The rule (see `uart16550.py`): **a read must never change state, and nothing
anyone polls may share a 32-bit word with one that does.** The standard map
already complies:

  * `claim` at 0x200004 is the only side-effecting read, and it is alone in its
    word -- `threshold` is a full word below it.
  * `pending`, the register a poll loop reads, is two megabytes away.
  * `pending`, `priority`, `enable`, `threshold` have no side effects either
    way.

## Level-sensitive sources -- and the livelock they can cause

`pending[i]` is `sources[i] & ~claimed[i]`: a wire from the source, gated by
this context's claim. No edge detector, no latch.

  * That is right for a 16550, whose `irq` stays high while its FIFO holds a
    byte. Latching an edge would lose the rest of a partly-drained FIFO and
    present as a console that accepts one burst and then hangs.
  * **The claim gate is what stops the level re-entering the handler servicing
    it.** `complete` releases it; a still-asserted source re-fires immediately,
    which is correct, and terminates only because the handler drains or masks
    it. See `firmware/cynthion-soc/src/irq.rs`.

## Cost

Combinational except one flip-flop per source (`claimed`), the priority and
enable storage, and the threshold. The address decode dominates, because the
spec's offsets span 22 bits -- the price of the map being the standard one.
"""

from amaranth              import Module, Mux, Cat, C, Signal, unsigned
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out

from amaranth_soc          import csr

# SplitRW is defined next to the 16550 because that is where it was first
# needed, but it is not UART-specific: it is "reading here and writing here
# reach different hardware", which claim/complete is the other instance of in
# this SoC. `amaranth_soc.csr.action` has no equivalent -- R and W are
# one-directional and RW owns its own storage.
from uart16550             import SplitRW


__all__ = ["Plic"]


# Priority levels, as a bit width. Three bits gives 1..7 usable levels, which is
# what QEMU's `virt` PLIC offers, so firmware that writes a priority of 1 or 7
# gets the same behaviour on both.
#
# A driver that writes a larger number here reads back the truncated value
# rather than an error. That is the standard's own behaviour -- the priority
# register is defined as WARL -- and it is why a driver should read back rather
# than assume.
PRIORITY_BITS = 3

# Fixed offsets, from riscv-plic-spec 1.0.0. Not parameters: a PLIC at different
# offsets is not a PLIC, and the whole reason for this file is that a generic
# driver can find its way around without being told.
PRIORITY_BASE = 0x000000
PENDING_BASE  = 0x001000
ENABLE_BASE   = 0x002000
CONTEXT_BASE  = 0x200000

# 4 MiB. The claim register at 0x200004 is the last thing in the map, so 22 bits
# of address is the smallest window that can hold a spec-compliant PLIC with one
# context. Narrowing it would save decode logic and produce something a generic
# driver could not use, which is the one thing this peripheral must not be.
ADDR_WIDTH = 22


class Plic(wiring.Component):
    """A single-context RISC-V PLIC on a byte-wide CSR bus.

    Parameters
    ----------
    sources : int
        How many interrupt sources, numbered 1..sources. At most 31, because
        `pending` and `enable` are one 32-bit register each and bit 0 belongs to
        the reserved source 0. A design needing more needs the spec's second
        word of each, which is four more lines and no attached hardware yet.

    Attributes
    ----------
    bus : csr.Interface(addr_width=22, data_width=8)
        The register map above. Add through a `WishboneCSRBridge`.
    sources : Signal(sources + 1), in
        The interrupt lines, indexed by source number. **Bit 0 is ignored** --
        source 0 is the PLIC's "nothing pending" answer and cannot be raised.
        Each line is a level: hold it high while the peripheral wants service.
    irq_out : Signal(), out
        To the CPU's machine external interrupt input.
    """

    def __init__(self, *, sources=2):
        if not isinstance(sources, int) or not 1 <= sources <= 31:
            raise ValueError(f"sources must be 1..31, not {sources!r}")
        self._sources = sources

        # Per-source priority. 32-bit registers holding PRIORITY_BITS of value,
        # because the spec defines them as 32-bit and a driver reads and writes
        # them as words; the unused upper bits read back zero.
        self._priority = {
            number: csr.Register({"level": csr.Field(csr.action.RW,
                                                     PRIORITY_BITS)},
                                 access="rw")
            for number in range(1, sources + 1)
        }

        # Pending. Read-only and purely combinational from the source levels --
        # the one register here that firmware might sit in a loop on, and so the
        # one that must have no side effect whatsoever. It is two megabytes from
        # the claim, which is the only register that does.
        self._pending = csr.Register({"bits": csr.Field(csr.action.R,
                                                        sources + 1)},
                                     access="r")

        # Enable, for context 0.
        #
        # Bit 0 is writable and inert. Making it read back as zero would need a
        # second storage element and a mask; making it do nothing needs neither,
        # because `pending[0]` is hardwired low below and a source can only be
        # selected if it is pending. Enabling source 0 therefore cannot raise an
        # interrupt no matter what a driver writes here.
        self._enable = csr.Register({"bits": csr.Field(csr.action.RW,
                                                       sources + 1)},
                                    access="rw")

        self._threshold = csr.Register(
            {"level": csr.Field(csr.action.RW, PRIORITY_BITS)}, access="rw")

        # Claim on read, complete on write, and genuinely different hardware in
        # each direction -- which is what SplitRW exists for.
        #
        # Eight bits, not the width of a source number. The spec's source ids go
        # to 1023 and a `complete` write carries one; truncating the field to
        # just enough bits for this design's sources would alias a write of, say,
        # 5 onto source 1 on a two-source PLIC and complete an interrupt nobody
        # claimed. Eight bits covers every source number this peripheral can be
        # built with (31), so an out-of-range completion matches nothing and is
        # ignored -- which is what it should be.
        self._claim = csr.Register({"source": csr.Field(SplitRW, 8)},
                                   access="rw")

        builder = csr.Builder(addr_width=ADDR_WIDTH, data_width=8)
        for number, register in self._priority.items():
            builder.add(f"priority{number}", register,
                        offset=PRIORITY_BASE + 4 * number)
        builder.add("pending",   self._pending,   offset=PENDING_BASE)
        builder.add("enable",    self._enable,    offset=ENABLE_BASE)
        builder.add("threshold", self._threshold, offset=CONTEXT_BASE + 0x0)
        builder.add("claim",     self._claim,     offset=CONTEXT_BASE + 0x4)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":     In(csr.Signature(addr_width=ADDR_WIDTH, data_width=8)),
            "sources": In(unsigned(sources + 1)),
            "irq_out": Out(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    @property
    def sources_count(self):
        """How many sources this instance was built for. 1..sources_count."""
        return self._sources

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        count = self._sources

        # One flop per source: has context 0 claimed this and not yet completed
        # it? This is the whole of the PLIC's state that a claim changes, and it
        # is why the claim read has a side effect at all.
        claimed = Signal(count + 1)

        enable = self._enable.f.bits.data
        threshold = self._threshold.f.level.data

        # Pending, per the spec: requesting, and not currently claimed.
        #
        # Bit 0 is tied low rather than left to `self.sources[0]`. Source 0 is
        # the "nothing pending" answer to a claim, so a design that accidentally
        # wired a peripheral to bit 0 would otherwise get an interrupt it could
        # never identify and could never complete -- a permanently stuck line
        # with no register saying why.
        pending = Signal(count + 1)
        m.d.comb += pending[0].eq(0)
        for number in range(1, count + 1):
            m.d.comb += pending[number].eq(self.sources[number]
                                           & ~claimed[number])

        m.d.comb += self._pending.f.bits.r_data.eq(pending)

        # Which source, if any, this context should be interrupted for.
        #
        # Built as a chain of Muxes rather than a loop of `m.If`, because the
        # obvious loop -- comparing each source against a running best that is
        # itself a comb signal -- is a combinational loop, and Amaranth would be
        # right to reject it. Each `best_*` below is a Python expression that
        # closes over the previous one, so the result is a tree.
        #
        # Iterating from the HIGHEST source number down, with `>=` in the test,
        # is what makes the lowest source number win a tie. The spec requires
        # that, and a design that breaks the tie the other way starves source 1
        # whenever source 2 is equally busy -- which here is the USB console
        # losing to the Apollo port.
        best_id = C(0, unsigned(8))
        best_priority = C(0, unsigned(PRIORITY_BITS))
        for number in range(count, 0, -1):
            level = self._priority[number].f.level.data
            # `level > threshold` is the spec's masking rule, and it is also how
            # priority 0 means "never interrupt": with the threshold at its
            # reset value of 0, a source left at priority 0 fails 0 > 0. A
            # driver that enables a source and forgets to give it a priority
            # therefore gets silence rather than a storm, which is the safer of
            # the two.
            ready = pending[number] & enable[number] & (level > threshold)
            # `>=` against the best so far, not just `ready`. Dropping this
            # comparison -- which the first draft of this file did -- makes the
            # LAST source examined win unconditionally, so the priority
            # registers are stored, read back, and ignored. Nothing about that
            # is visible from a console: both sources still get serviced, just
            # always in source-number order. `scripts/soc_plic_sim.py` is what
            # found it.
            take = ready & (level >= best_priority)
            best_id = Mux(take, C(number, 8), best_id)
            best_priority = Mux(take, level, best_priority)

        # Source 0 means nothing is pending, so this is the whole condition.
        m.d.comb += self.irq_out.eq(best_id != 0)

        # ---- 0x200004  claim (read) / complete (write) ----------------------
        claim = self._claim.f.source

        m.d.comb += claim.r_data.eq(best_id)

        # THE ONLY SIDE-EFFECTING READ IN THIS PERIPHERAL. See the module
        # docstring for why the standard map keeps it safely away from anything
        # a poll loop touches.
        #
        # `r_stb` fires on the register's first byte address only -- the CSR
        # multiplexer captures all four bytes into a read shadow at that
        # instant, and the remaining three byte reads come from the shadow. So a
        # 32-bit load claims exactly once, and `best_id` is still the pre-claim
        # value on the cycle it is sampled.
        #
        # A Switch over the real source numbers rather than an indexed
        # assignment, symmetric with `complete` below and for the same reason:
        # nothing here can address a `claimed` bit that has no source behind it.
        with m.If(claim.r_stb):
            for number in range(1, count + 1):
                with m.If(best_id == number):
                    m.d.sync += claimed[number].eq(1)

        # Complete. Written as a Switch over the real source numbers rather than
        # an indexed assignment, so a write of a number this PLIC has no source
        # for clears nothing instead of aliasing onto one that exists.
        #
        # The enable check is the spec's: a completion for a source this context
        # is not enabled for is ignored. Without it, a driver that disables a
        # source between claim and complete would leave `claimed` set forever
        # and that source silently dead.
        with m.If(claim.w_stb):
            for number in range(1, count + 1):
                with m.If((claim.w_data == number) & enable[number]):
                    m.d.sync += claimed[number].eq(0)

        return m
