#!/usr/bin/env python3
#
# The HyperRAM BIST engine as a CPU peripheral, off the Wishbone. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""`HyperRAMCeiling` on the CPU's CSR bus, in its own clock domain.

## What this is for

Every HyperRAM figure this project has recorded was taken with at least one
broken instrument. The four defects and the dates they were fixed:

    controller sampled a full CK early (HIGH_LATENCY_CLOCKS 5 -> 6)   2026-08-05
    RECOVERY fell through to IDLE with no tCSHI gap                   2026-08-05
    the negative control armed AFTER the engine started               2026-08-06
    JTAG register readback slips below a sync/TCK ratio of ~4         2026-08-06

Nothing measured before the last two discriminates. This is not another
experiment on that pile -- it is a rig whose output would be admissible, and its
job is to re-establish everything from zero.

## The shape

The HyperRAM stops being memory the SoC addresses and becomes **a peripheral the
SoC commands**. The CPU writes the axis values, starts a pass, polls, reads the
counters and prints a row. That removes three of the four defect classes above by
construction:

  * no `JTAGRegisterInterface` anywhere in the measurement path;
  * no Wishbone decoder, cache, arbiter or `RegisteredResponse` bubble between
    the engine and the part -- so the unfinished SoC DQS write path (#92, #211)
    is not in the way and the matrix can be produced without fixing it first;
  * the CPU can print per cell, so a hang names the cell it hung on. The
    gateware sweep dies at cell 0 with no way to see why (#210).

The engine, its comparator, its negative control and its sweep FSM are
**unchanged**. `HyperRAMCeiling` never touches the transport -- it calls
`add_register` / `add_read_only_register` on the harness -- so this is an
injection, not a fork.

## Domains

The engine runs in `hr` off the second PLL, so device CK is not the CPU clock
(`hyperram_clocks.py`). Nothing streams across: parameters are written before a
pass and held still, `go` and `done` cross as pulses, counters are read after
`done`. `bist_csr.py` carries the reasoning and the reason it is not a FIFO.
"""

from amaranth import Module
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
                 addr_width=8, domain="hr"):
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
        # The HyperRAM pins it still requests itself, and must -- that is the
        # one resource this variant hands it exclusively, which is why BootRAM
        # is not built alongside.
        self._engine = HyperRAMCeiling(
            sync_mhz=ck_mhz / 2 if dqs else ck_mhz,
            dqs=dqs, negative_control=negative_control,
            transport=self._transport, own_clocks=False, own_leds=False)

        # One bit wider than the engine's: parameters and results occupy two
        # windows, so every engine register has two bus addresses. See
        # `BistCsrTransport`.
        super().__init__({
            "bus": In(csr.Signature(addr_width=addr_width + 1, data_width=8)),
        })
        # The transport builds its whole window at construction, so the map is
        # available immediately and no finalize step exists to be forgotten.
        self.bus.memory_map = self._transport.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.engine = self._engine
        # The transport is deliberately NOT added here. `BISTHarness.elaborate`
        # already does `m.submodules.registers = registers`, and adding the same
        # instance in two places elaborates it twice -- which surfaces four
        # frames inside `amaranth_soc` as `'frozenset' object has no attribute
        # 'add'`, the CSR bridge's shadow having been frozen by the first pass.
        # Only the bus needs joining up, and that does not require ownership.
        connect(m, flipped(self.bus), self._transport.bus)
        return m

    @property
    def transport(self):
        """The register window, for a caller that wants it directly."""
        return self._transport
