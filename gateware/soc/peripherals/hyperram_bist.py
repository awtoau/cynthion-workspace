#!/usr/bin/env python3
#
# The HyperRAM BIST engine as a CPU peripheral, off the Wishbone. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""`HyperRAMCeiling` on the CPU's CSR bus, in its own clock domain.

## What this is for

Every HyperRAM figure this project has recorded was taken with at least one
broken instrument -- five overlapping faults fixed between 5 and 7 August 2026,
which is why `docs/chips/hyperram/bist-plan.md` deletes the old numbers rather
than annotating them. This is not another experiment on that pile: it is a rig
whose output would be admissible, and its job is to re-establish everything from
zero.

## The shape

The HyperRAM stops being memory the SoC addresses and becomes **a peripheral the
SoC commands**. The CPU writes the axis values, starts a pass, polls, reads the
counters and prints a row. That removes three defect classes by construction:

  * no host in the measurement loop -- the CPU commands the pass and prints
    per cell, at ~2 ms each;
  * no Wishbone decoder, cache, arbiter or `RegisteredResponse` bubble between
    the engine and the part -- so the unfinished SoC DQS write path (#186, #212)
    is not in the way and the matrix can be produced without fixing it first;
  * the CPU can print per cell, so a hang names the cell it hung on. The
    gateware sweep died at cell 0 with no way to see why (#210).

The engine, its comparator, its negative control and its sweep FSM are
**unchanged**. `HyperRAMCeiling` never touches the transport -- it calls
`add_register` / `add_read_only_register` on the harness -- so this is an
injection, not a fork.

## Open: who owns the HyperRAM pins

`platform.request("ram")` succeeds once, and the engine calls it itself. So as
written this variant takes the pins exclusively, and the SoC's own window,
BootRAM and staging path cannot be built alongside it.

`docs/chips/hyperram/bist-plan.md` says that is the wrong answer, and says so
from experience -- it is the first link in the chain that produced a silent
board: engine claims the pins, the window goes, BootRAM goes, the bootloader
probes a window that is now absent, VexiiRiscv traps, no banner. The plan's
requirement is **share the pins, do not reassign them**: one requester, a CSR
selecting who drives, everything else still built and merely idle.

That mux belongs in `top.py`, where the requester lives. Until it is there, this
peripheral is only safe in a measurement-only bitstream.

## Domains

The engine runs in `hr` off the second PLL, so device CK is not the CPU clock
(`hyperram_clocks.py`). Nothing streams across: parameters are written before a
pass and held still, `go` and `done` cross as pulses, counters are read after
`done`. `bist_csr.py` carries the reasoning and the reason it is not a FIFO.
"""

from amaranth import DomainRenamer, Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In, connect, flipped
from amaranth_soc import csr

__all__ = ["HyperRAMBist"]


class HyperRAMBist(wiring.Component):
    """The ceiling engine, addressable by the CPU rather than by JTAG.

    Parameters
    ----------
    ck_mhz : float
        The **device** clock, not the fabric one. The DQS PHY emits two CK per
        `hr` cycle, so the domain generator halves it; taking the device number
        here means a caller cannot get that factor of two wrong.
    dqs : bool
        Which PHY. Also selects the comparator width -- 32 bits on the DQS path,
        16 on the other -- because a 16-bit comparator on a 32-bit path scores
        half of every word as correct by not looking at it.
    negative_control : bool
        Build the deliberately-wrong variant. A cell that passes without its
        control having fired is recorded as *no result*, not as a pass.
    """

    def __init__(self, *, ck_mhz, dqs=True, negative_control=False,
                 addr_width=7, domain="hr"):
        self.ck_mhz = ck_mhz
        self.dqs = dqs
        self.negative_control = negative_control
        self._domain = domain

        # Imported here rather than at module scope, and with the path added
        # rather than assumed: the engine lives under `gateware/probes/`, which
        # is not on `sys.path` for every consumer of this package. A top-level
        # import would make the whole SoC unimportable for anyone who had not
        # added it, and the failure would name this module rather than the path.
        import sys
        from pathlib import Path
        here = Path(__file__).resolve()
        for extra in (here.parents[2] / "probes",   # the engine
                      here.parent):                 # this package's siblings
            if str(extra) not in sys.path:
                sys.path.insert(0, str(extra))

        from hyperram.hyperram_ceiling_top import HyperRAMCeiling
        from bist_csr import BistCsrTransport

        self._transport = BistCsrTransport(addr_width=addr_width,
                                           engine_domain=domain)
        # `own_clocks=False`: the SoC's generator makes `hr`.
        # `own_leds=False`: the SoC's GPIO already owns them.
        # `own_dtr=False`: `fabric_status` has the ECP5's single DTR, and the
        #   engine does not need one -- die temperature is a property of the
        #   chip, readable from there.
        # The HyperRAM pins it still requests itself; see the module docstring's
        # open point, which is why this is not yet safe alongside the window.
        self._engine = HyperRAMCeiling(
            sync_mhz=ck_mhz / 2 if dqs else ck_mhz,
            dqs=dqs, negative_control=negative_control,
            transport=self._transport, own_clocks=False, own_leds=False,
            own_dtr=False)

        # Taken FROM the transport rather than recomputed. The bus has to span
        # both windows, and the result window sits at a fixed offset the firmware
        # also knows -- so its width is not a function of `addr_width` alone, and
        # a second derivation here would disagree the moment either moved.
        super().__init__({
            "bus": In(csr.Signature(
                addr_width=self._transport.bus.signature.addr_width,
                data_width=8)),
        })
        # The transport builds its whole window at construction, so the map is
        # available immediately and no finalize step exists to be forgotten.
        self.bus.memory_map = self._transport.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        # The engine is written in `sync`/`fast` like every other probe, and is
        # MOVED here rather than parameterised. That keeps one copy of it shared
        # with the JTAG applet -- a second copy differing only in domain names is
        # how two rigs stop measuring the same thing.
        #
        # Without this the engine runs in `sync`, which is the CPU's clock, and
        # the whole rig collapses back to what it was built to escape: changing
        # CK would drag the console divisor and the CLINT tick with it.
        #
        # ORDER MATTERS, and it is the engine FIRST. The engine declares its
        # registers during ITS elaborate, by calling `add_register` /
        # `add_read_only_register` on the transport. The transport binds those
        # declarations to CSR fields during ITS elaborate. Amaranth elaborates
        # submodules in the order they are added, so a transport added first
        # binds an empty set -- every result register then reads zero, for ever.
        #
        # That is the worst possible failure for this rig: zero errors out of
        # zero words is exactly what a PASSING cell looks like to anything that
        # does not check. `BistCsrTransport.elaborate` raises rather than allow
        # it, and `present()` catches it too because the ident is a result and
        # reads zero along with everything else.
        m.submodules.engine = DomainRenamer({"sync": self._domain,
                                             "fast": f"{self._domain}_fast"})(
            self._engine)

        # The transport is elaborated HERE, in `sync`, and deliberately NOT
        # inside the renamer above. Its CSR bridge talks to the CPU, so it must
        # be clocked by the CPU. `BISTHarness` only elaborates a transport it
        # created itself, which is what leaves this one to us.
        m.submodules.transport = self._transport
        connect(m, flipped(self.bus), self._transport.bus)
        return m

    @property
    def transport(self):
        """The register window, for a caller that wants it directly."""
        return self._transport
