#!/usr/bin/env python3
#
# The SoC's interrupt controller: amaranth_soc's EventMonitor, numbered.
# SPDX-License-Identifier: BSD-3-Clause

"""
Pending bits and enables, one machine-external wire out. Nothing else.

* `amaranth_soc.csr.event.EventMonitor` is the controller. This file only
  chooses the source numbers and each source's trigger.
* No priority, no threshold, no claim/complete. Priority is RTIC's, in software
  on `msip` -- see `../../../docs/soc-interrupts.md`.
* **Every pending bit latches, level sources included.** Set on trigger,
  cleared only by writing a 1 to it. A handler that services a source and does
  not clear the bit is re-entered forever.
* A level source's clear is ignored while the line is still asserted
  (`amaranth_soc.event.Monitor`: the set arm wins over the clear arm), so the
  order is always "clear the peripheral, then clear the bit".

## Register map

Two registers, byte-addressed on the CSR bus, one bit per source:

    0x0  enable    RW   bit n: source n may raise the CPU line
    0x4  pending   RW   bit n: source n has triggered; write 1 to clear

Four-byte aligned rather than packed, so the two never share a word and the
firmware's byte walk over one cannot latch a shadow of the other. Both are
`ceil(sources / 8)` bytes wide, low byte first -- read the low byte first, write
the high byte last, per the CSR multiplexer's shadow rules (`src/gpio.rs`).

## Numbering

`docs/soc-interrupts.md` numbers seventeen sources, 1..17, of which ten are
built. That table is the contract, so **bit position equals source number** and
bit 0 is unused: the gaps are sources with no hardware yet, each given a line
tied low, and wiring one up later renumbers nothing.
"""

from amaranth            import Module, unsigned
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

from amaranth_soc              import csr, event
from amaranth_soc.csr.event    import EventMonitor


__all__ = ["Interrupts", "ENABLE_OFFSET", "PENDING_OFFSET"]


# CSR byte offsets of the two registers. `EventMonitor` decides these from the
# order it adds them and from `alignment`; they are checked against the
# elaborated map below, because the firmware dereferences them.
ENABLE_OFFSET = 0x0
PENDING_OFFSET = 0x4


class Interrupts(wiring.Component):
    """The interrupt controller, with this SoC's source numbering.

    Parameters
    ----------
    triggers : dict of int to str
        Source number -> "level", "rise" or "fall". Fixed at elaboration: a
        source is one or the other and firmware cannot change it. Numbers not
        present are gaps -- their lines are tied low.

    Attributes
    ----------
    bus : csr.Interface(data_width=8)
        The register map above. Add through a `WishboneCSRBridge`.
    lines : Signal(max(triggers) + 1), in
        The interrupt lines, indexed by source number. Drive the ones that
        exist; the rest stay low.
    irq_out : Signal(), out
        To the CPU's machine external interrupt input.
    """

    def __init__(self, triggers):
        if not triggers:
            raise ValueError("triggers must name at least one source")
        for number, trigger in triggers.items():
            if not isinstance(number, int) or number < 0:
                raise ValueError(f"source number must be a non-negative int, "
                                 f"not {number!r}")
            event.Source.Trigger(trigger)   # raises on anything else

        self._triggers = dict(triggers)
        count = max(triggers) + 1

        event_map = event.EventMap()
        self._srcs = []
        for number in range(count):
            src = event.Source(trigger=triggers.get(number, "level"),
                               path=(f"source_{number}",))
            event_map.add(src)
            self._srcs.append(src)

        # alignment=2 keeps `enable` and `pending` in separate 32-bit words.
        self._monitor = EventMonitor(event_map, data_width=8, alignment=2)

        # `EventMonitor` drives its two mask registers' elements itself and never
        # elaborates them, so every elaboration of this SoC would otherwise warn
        # four times about objects upstream deliberately does not use.
        for register in (self._monitor._enable, self._monitor._pending):
            register._MustUse__used = True
            register.f.mask._MustUse__used = True

        memory_map = self._monitor.bus.memory_map
        starts = {"_".join(str(part) for part in name): start
                  for _, name, (start, _) in memory_map.resources()}
        if (starts.get("enable"), starts.get("pending")) != (ENABLE_OFFSET,
                                                             PENDING_OFFSET):
            raise RuntimeError(
                f"amaranth_soc puts the event registers at {starts}, not at "
                f"enable={ENABLE_OFFSET:#x} pending={PENDING_OFFSET:#x} -- "
                f"firmware/cynthion-soc/src/intc.rs dereferences those")

        super().__init__({
            "bus":     In(csr.Signature(addr_width=memory_map.addr_width,
                                        data_width=memory_map.data_width)),
            "lines":   In(unsigned(count)),
            "irq_out": Out(unsigned(1)),
        })
        self.bus.memory_map = memory_map

    @property
    def sources_count(self):
        """How many source numbers this instance covers, gaps included."""
        return len(self._srcs)

    @property
    def triggers(self):
        """Source number -> trigger, for the sources that have one."""
        return dict(self._triggers)

    def elaborate(self, platform):
        m = Module()
        m.submodules.monitor = self._monitor
        # Both sides flipped: `EventMonitor` declares its bus `In(mux.bus.signature)`
        # over an already-flipped signature, so from out here it faces the same
        # way this component's own bus does.
        wiring.connect(m, wiring.flipped(self.bus),
                       wiring.flipped(self._monitor.bus))

        for number, src in enumerate(self._srcs):
            m.d.comb += src.i.eq(self.lines[number])

        m.d.comb += self.irq_out.eq(self._monitor.src.i)
        return m
