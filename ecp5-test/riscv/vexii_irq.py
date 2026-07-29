#!/usr/bin/env python3
#
# A CSR-mapped interrupt controller, replacing VexRiscv's in-CPU registers.
# SPDX-License-Identifier: BSD-3-Clause

"""
Concentrates many interrupt sources onto the one line standard RISC-V defines.

VexRiscv used a non-standard `ExternalInterruptArrayPlugin`: a 32-bit
`irq_external` input, with the mask and pending registers living *inside the
CPU* at custom CSRs 0xBC0 (`mim`) and 0xFC0 (`mip`). luna_soc's
`InterruptController` is therefore a pure concentrator -- it wires each
peripheral's `irq` to a bit and the CPU does the rest.

VexiiRiscv implements only standard RISC-V, where the machine external
interrupt is a single wire (`PrivilegedPlugin.scala:185`). There is no
interrupt array and no equivalent of those CSRs. Moondancer has around twenty
sources -- three USB devices with control/in/out endpoints each, two timers --
so something has to tell software which one fired, and let it mask them
individually.

This does that in a peripheral instead of in the CPU:

    pending   read-only    one bit per source, the concentrated irq lines
    enable    read-write   mask
    irq_out = (pending & enable) != 0

That is deliberately the same information VexRiscv's CSRs carried, in the same
bit order. The firmware's dispatch reads pending, takes `ilog2()` and converts
to an `Interrupt` enum (`moondancer-pac/src/csr.rs:55-66`); swapping a CSR read
for an MMIO load leaves that logic, and the ~500-line match on `pac::Interrupt`
behind it, compiling unchanged.

A PLIC would be the standard answer, but VexiiRiscv's is Tilelink-only, larger,
and has claim/complete semantics the firmware does not use -- it would force
firmware changes to solve a problem the firmware does not have.

`add()` and `interrupts()` keep luna_soc's signatures so the SVD generator
(`luna_soc/generate/svd.py`, via `introspect.interrupts()`) still finds the
map and the regenerated PAC keeps the same interrupt numbering.
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
        How many interrupt sources. 32 matches what VexRiscv's array carried,
        so bit numbering is preserved across the swap.
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
