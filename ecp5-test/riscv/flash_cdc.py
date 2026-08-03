#!/usr/bin/env python3
#
# A flash PHY in its own clock domain, presented to the core in `sync`.
# SPDX-License-Identifier: BSD-3-Clause

"""
Decouple the flash clock from the CPU clock.

## Why

SCK is derived from whatever domain the PHY runs in, and the PHY has always run
in `sync` -- the CPU's domain. So the flash rate was a function of the CPU clock,
and the CPU clock is chosen for the CPU.

That cost most of the part's speed. `docs/chips/w25q32-config-flash.md` records 60
measured points -- five modes x four divisors x three sync rates -- **all PASS**,
to **144 MHz SCK and 71.70 MB/s**. The SoC ran at 30 MHz and 14.95 MB/s, the
bottom rung, because `sync` was 60 MHz and `SPIClockGenerator` halves it.

The part is not the limit and neither is the pin: `USRMCLK` is unmodelled by
Lattice and by prjtrellis, so measurement is the only authority and measurement
found no ceiling. The limit was our clock tree.

## What this does

Runs the PHY in a separate, faster domain and crosses the three signals of
`SPIControlPort` back to `sync`, so the memory-mapped core, the controller and
the crossbar are untouched and still sit beside the CPU.

    sync domain                         flash domain (faster)
    -----------                         ---------------------
    mmap core  --> source --> [AsyncFIFO] --> PHY --> pads
    crossbar   <-- sink   <-- [AsyncFIFO] <--
    controller --> cs     --> [FFSynchronizer] -->

## Why FIFOs rather than a constraint

The two domains come from one PLL at an integer ratio, so they are mesochronous
and a timing constraint could in principle cover the crossing. In practice
nextpnr treats separately-declared domains as unrelated, so the choice is between
writing the constraints and writing the FIFOs. The FIFOs are checkable by
simulation, survive a change of ratio, and do not depend on anyone maintaining a
constraint file alongside a clock decision. They cost two BRAM-free shallow
FIFOs and a few cycles of latency per transaction, against a transaction that
already spends tens of SCK edges on the wire.

## `cs` is a level, and is synchronised rather than queued

Chip select is not part of either stream: it is held across a whole transaction
and released after it. Putting it through a FIFO would order it against data it
is not ordered against; a two-flop synchroniser is what a level crossing wants.

**This does mean `cs` and the first data word are not guaranteed to arrive in the
same flash-domain cycle.** That is safe here because the PHY asserts the pin from
`cs` and only shifts when `source.valid`, so an early `cs` costs idle clocks with
the pin already low, and a late one cannot truncate a transfer that has not
started. It would NOT be safe on a PHY that inferred transaction boundaries from
`cs` edges.
"""

from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO
from amaranth.lib.wiring import In, Out

from luna_soc.gateware.core.spiflash.port import SPIControlPort


class ClockCrossedPHY(wiring.Component):
    """`inner` in `phy_domain`, its control port presented in `sync`.

    A drop-in for the PHY it wraps: the same flipped `SPIControlPort`, so the
    crossbar connects to it identically and nothing upstream knows the flash is
    on another clock.

    `depth` is 4 rather than 2 because an AsyncFIFO's usable depth is its depth
    minus the synchroniser latency in each direction, and 2 leaves almost no
    slack -- the core would stall on nearly every word. It is not 8 or more
    because these are not throughput buffers: the PHY consumes a word in tens of
    SCK edges, so a deep queue would only add latency to the first word of a
    burst.
    """

    #: The inner PHY's instrumentation signals, forwarded so the ILA and the pin
    #: probe still build.
    #:
    #: **These are in `phy_domain`, and anything sampling them in `sync` is
    #: reading across a clock boundary.** For the pin probe that is tolerable --
    #: it counts edges and reports coarse state. For `FlashILA` it is not: its
    #: whole purpose is the sub-cycle relationship between `sample_stb` and the
    #: data it latches, and that relationship does not survive an unsynchronised
    #: crossing. The ILA belongs in `phy_domain` with its own bus crossing, or it
    #: belongs retired -- it was built to find the JEDEC read fault, which is
    #: fixed. Until then its captures are evidence about gross behaviour only.
    OBSERVED = ("o_sr_in_shift", "o_sr_out_load", "o_sample", "o_update",
                "o_clk", "o_dq_i1", "o_dq_o0", "o_in_xfer", "o_in_xfer_end")

    def __init__(self, inner, *, phy_domain, data_width=32, depth=4):
        self._inner = inner
        self._phy_domain = phy_domain
        self._depth = depth
        for name in self.OBSERVED:
            if hasattr(inner, name):
                setattr(self, name, getattr(inner, name))
        # The PHY's own signature, flipped the same way, so this substitutes for
        # it without the crossbar being changed.
        super().__init__(SPIControlPort(data_width).flip())

    def elaborate(self, platform):
        m = Module()
        m.submodules.phy = phy = self._inner
        out_domain = self._phy_domain

        # --- core -> PHY ---------------------------------------------------
        # data, len, width and mask travel together as one payload: they
        # describe a single transfer and splitting them across two FIFOs would
        # let a word arrive with the previous transfer's width.
        source = self.source
        out_layout = [("data", len(source.data)), ("len", len(source.len)),
                      ("width", len(source.width)), ("mask", len(source.mask))]
        out_width = sum(width for _, width in out_layout)

        m.submodules.to_phy = to_phy = AsyncFIFO(
            width=out_width, depth=self._depth,
            r_domain=out_domain, w_domain="sync")

        packed = []
        for name, _ in out_layout:
            packed.append(getattr(source, name))
        m.d.comb += [
            to_phy.w_data.eq(_cat(packed)),
            to_phy.w_en.eq(source.valid),
            source.ready.eq(to_phy.w_rdy),
        ]

        offset = 0
        for name, width in out_layout:
            m.d.comb += getattr(phy.source, name).eq(
                to_phy.r_data[offset:offset + width])
            offset += width
        m.d.comb += [
            phy.source.valid.eq(to_phy.r_rdy),
            to_phy.r_en.eq(phy.source.ready),
        ]

        # --- PHY -> core ---------------------------------------------------
        m.submodules.from_phy = from_phy = AsyncFIFO(
            width=len(phy.sink.data), depth=self._depth,
            r_domain="sync", w_domain=out_domain)
        m.d.comb += [
            from_phy.w_data.eq(phy.sink.data),
            from_phy.w_en.eq(phy.sink.valid),
            phy.sink.ready.eq(from_phy.w_rdy),

            self.sink.data.eq(from_phy.r_data),
            self.sink.valid.eq(from_phy.r_rdy),
            from_phy.r_en.eq(self.sink.ready),
        ]

        # --- cs ------------------------------------------------------------
        # A level, so synchronised rather than queued. See the module docstring
        # for why that is safe with this PHY and would not be with every PHY.
        m.submodules.cs_sync = FFSynchronizer(self.cs, phy.cs, o_domain=out_domain)

        return m


def _cat(signals):
    """`Cat` of a list, lowest field first, matching the unpacking above."""
    from amaranth import Cat
    return Cat(*signals)
