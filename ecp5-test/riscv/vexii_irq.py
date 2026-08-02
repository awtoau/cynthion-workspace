#!/usr/bin/env python3
#
# A pending/enable interrupt concentrator, as a CSR peripheral.
# SPDX-License-Identifier: BSD-3-Clause

"""
Concentrates up to 32 interrupt sources onto the one machine external line
standard RISC-V gives the CPU.

    pending   R    one bit per source, the concentrated irq lines
    enable    RW   mask
    irq_out = (pending & enable) != 0

No claim, no complete, no priorities. Software reads `pending`, takes
`ilog2()` and dispatches -- which is what moondancer's firmware already does
(`moondancer-pac/src/csr.rs:55-66`), so its ~500-line match on
`pac::Interrupt` compiles against this unchanged. `add()` and `interrupts()`
keep luna_soc's signatures so `luna_soc/generate/svd.py` still finds the map
and the generated PAC keeps its interrupt numbering.

**For a new SoC, use `vexii_plic.py` instead.** A standard PLIC is what keeps
the firmware's interrupt path identical on the board and under QEMU's `-M
virt`, which is where the test gate runs. This peripheral is kept for
moondancer's existing PAC. See `../../docs/comparisons.md`.
"""

from amaranth             import Module, Signal, Cat
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, Out
from amaranth.hdl         import unsigned

from amaranth_soc         import csr


class InterruptController(wiring.Component):
    """Interrupt concentrator with a CSR interface.

    Parameters
    ----------
    width : int
        How many interrupt sources. 32 is the width moondancer's PAC numbers
        against, so anything less renumbers its `Interrupt` enum.
    """

    class Pending(csr.Register, access="r"):
        def __init__(self, width):
            super().__init__({"pending": csr.Field(csr.action.R,
                                                   unsigned(width))})

    class Enable(csr.Register, access="rw"):
        def __init__(self, width):
            super().__init__({"enable": csr.Field(csr.action.RW,
                                                  unsigned(width))})

    def __init__(self, *, width=32, name="interrupt_controller"):
        self._width = width
        self._name = name
        self._interrupts = {}

        self._pending = self.Pending(width)
        self._enable = self.Enable(width)

        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("pending", self._pending)
        builder.add("enable", self._enable)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=4, data_width=8)),
            # To the CPU's single machine-external interrupt input.
            "irq_out": Out(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def interrupts(self):
        """The interrupt map, in luna_soc's shape.

        The SVD generator reads this to name interrupts in the PAC, so the
        signature has to match or the regenerated firmware bindings change.
        """
        return self._interrupts

    def add(self, peripheral, *, name, number=None):
        """Attach a peripheral's irq line to a bit.

        Same validation as luna_soc's, because a duplicate number or name here
        surfaces later as an interrupt firing the wrong handler.
        """
        if number is None:
            raise ValueError("You need to supply a value for the IRQ number.")
        if number >= self._width:
            raise ValueError(f"IRQ number {number} exceeds width {self._width}")
        if number in self._interrupts:
            raise ValueError(f"IRQ number '{number}' has already been used.")
        if name in dict(self._interrupts.values()):
            raise ValueError(f"Peripheral name '{name}' has already been used.")
        if peripheral in dict(self._interrupts.values()).values():
            raise ValueError(f"Peripheral '{name}' has already been added.")
        self._interrupts[number] = (name, peripheral)

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        pending = Signal(self._width)
        for number, (name, peripheral) in self._interrupts.items():
            m.d.comb += pending[number].eq(peripheral.irq)

        enable = self._enable.f.enable.data

        m.d.comb += [
            self._pending.f.pending.r_data.eq(pending),
            # Masked, so a disabled source cannot hold the CPU's line high.
            # Reporting unmasked pending bits while gating the output would let
            # software see an interrupt it had deliberately turned off.
            self.irq_out.eq((pending & enable).any()),
        ]
        return m
