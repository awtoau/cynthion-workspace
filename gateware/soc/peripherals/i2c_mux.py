#!/usr/bin/env python3
#
# One I2C controller across three pin-sets, and the Type-C lines that come with it.
# See awtoau/cynthion-workspace#121 and #98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Which I2C bus the one controller drives, and which Type-C controller wants
attention.

r1.4 has three physically separate I2C buses, and the split is forced:

    select  bus              pins        device
    0       target_type_c    A4 / C4     FUSB302B @ 0x22
    1       aux_type_c       H12 / G14   FUSB302B @ 0x22
    2       power_monitor    D7 / C7     PAC1954  @ 0x10

**Both FUSB302Bs answer 0x22**, so they cannot share a bus. One `I2CMaster`
fans out to all three pin-sets under this two-bit select --
`docs/gateware-architecture-plan.md`, and `../../docs/architecture.md` for the
alternatives.

Select and interrupt lines are one peripheral because the handler needs both in
the same breath: read `lines` to find who asserted, write `select` to reach it.

## Registers

    +0  SELECT  rw  2 bits, values above
    +1  LINES   r   bit 0 target int, bit 1 aux int,
                    bit 2 target fault, bit 3 aux fault

Both reads are pure. The FUSB302B's own interrupt registers are read-to-clear
and that is where clearing belongs -- a clear-on-read CSR here would put a
state-changing read one byte from the register the handler polls
(`peripherals/uart16550.py`).

## Traps this is shaped around

  * **The select does not move under a transfer.** `applied` follows SELECT
    only while the controller is idle. Switching mid-transfer leaves the old
    bus half-driven and puts an edge on the new one that every device on it
    reads as a START. A write during a transfer is held, not obeyed.
  * **Idle buses are DRIVEN idle.** Unselected buses get SCL high and SDA
    released every cycle. These pins are `PULLMODE="NONE"`, so undriven means
    floating, and a floating SDA is a transient that reads as a START.
  * **One PLIC source per `int` line**, not an OR of the two. Both are levels.
    A shared level obliges its handler to clear EVERY asserting device before
    returning, and missing one is a storm that presents as a hung CPU; two
    sources delete that obligation rather than documenting it. The PLIC has 27
    spare sources, so nothing was saved by sharing (`../../docs/architecture.md`,
    decision 8).
  * **`fault` gets no source at all.** It means something different from `int`,
    and nothing here can clear it -- it drops when the device's fault does. An
    interrupt on a level the firmware cannot clear must stay masked until a
    poll says the level has gone, so it would add a handler and keep the poll.
    Reported in LINES, left to the 50 ms poller.
"""

from amaranth               import Module, Mux, Signal
from amaranth.lib           import wiring
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib.wiring    import In, Out

from amaranth_soc           import csr


__all__ = ["I2CBusMux", "BUS_TARGET_C", "BUS_AUX_C", "BUS_POWER_MONITOR",
           "BUS_RESOURCES"]


# Bus select values. The order is the platform's resource order rather than
# anything electrical, and it matches `gateware/probes/i2c/multiplexed.py` so that the
# simulation-only predecessor and this do not disagree about what "1" means.
BUS_TARGET_C = 0
BUS_AUX_C = 1
BUS_POWER_MONITOR = 2

# Which platform resource each select value drives.
BUS_RESOURCES = {
    BUS_TARGET_C: ("target_type_c", 0),
    BUS_AUX_C: ("aux_type_c", 0),
    BUS_POWER_MONITOR: ("power_monitor", 0),
}

# LINES bit positions.
LINE_TARGET_INT = 0
LINE_AUX_INT = 1
LINE_TARGET_FAULT = 2
LINE_AUX_FAULT = 3


class I2CBusMux(wiring.Component):
    """The bus select, the Type-C signals, and a PLIC source for each `int`.

    Attributes
    ----------
    bus : csr.Interface(addr_width=1, data_width=8)
        The two registers in the module docstring.
    select : Signal(2), out
        Which pin-set the controller drives. Only changes while `idle` is high.
    idle : Signal(), in
        From the controller: high when no transfer is in progress. Drive it from
        `~(SR.BUSY | SR.TIP)`.
    target_int, aux_int, target_fault, aux_fault : Signal(), in
        Straight from the pads. Active HIGH here: the platform declares all four
        `PinsN`, so Amaranth has already undone the active-low sense and a 1
        means the device is asserting.
    target_irq, aux_irq : Signal(), out
        One per controller, each its own `int` line and nothing else. Level
        sensitive; attach each to its own `vexii_plic.Plic` source. Separate
        rather than OR-ed so a handler knows which device asserted without
        reading LINES, and so clearing one cannot leave the other holding the
        line up.
    """

    def __init__(self):
        # +0. Which bus. A plain latch -- reading it back is how firmware
        # confirms what it asked for, and it must not be a status register.
        #
        # It RESETS to the power monitor, not to zero. Zero is `target_type_c`,
        # and a mux that came up pointing there would make the PAC1954
        # unreachable until some firmware happened to write this register -- so a
        # bitstream whose firmware never ran, or one whose firmware forgot,
        # would report no rails at all with nothing saying why. The rail
        # measurements are the thing most likely to be wanted from a board that
        # is otherwise misbehaving.
        self._select = csr.Register(
            {"select": csr.Field(csr.action.RW, 2, init=BUS_POWER_MONITOR)},
            access="rw")
        # +1. The four Type-C signals, read-only and pure.
        self._lines = csr.Register(
            {"target_int":   csr.Field(csr.action.R, 1),
             "aux_int":      csr.Field(csr.action.R, 1),
             "target_fault": csr.Field(csr.action.R, 1),
             "aux_fault":    csr.Field(csr.action.R, 1)},
            access="r")

        builder = csr.Builder(addr_width=1, data_width=8)
        builder.add("select", self._select)
        builder.add("lines",  self._lines)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":          In(csr.Signature(addr_width=1, data_width=8)),
            "select":       Out(2),
            "idle":         In(1),
            "target_int":   In(1),
            "aux_int":      In(1),
            "target_fault": In(1),
            "aux_fault":    In(1),
            "target_irq":   Out(1),
            "aux_irq":      Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        # The four Type-C signals come from other chips and their own clocks, so
        # they are asynchronous to `sync`. Sampling them raw would put a
        # metastable value into a CSR read and, worse, into the interrupt line --
        # which reads as an interrupt that fires when nothing happened, the
        # hardest kind of fault to attribute. Two stages each, which is what
        # FFSynchronizer gives.
        #
        # `init=0` because both signals are asserted LOW on the pad and the
        # platform's `PinsN` has already inverted them: 0 here is "not
        # asserting", which is what an absent or unconfigured controller does and
        # therefore the right value to come out of reset believing.
        target_int = Signal()
        aux_int = Signal()
        target_fault = Signal()
        aux_fault = Signal()
        m.submodules.target_int_cdc = FFSynchronizer(self.target_int, target_int)
        m.submodules.aux_int_cdc = FFSynchronizer(self.aux_int, aux_int)
        m.submodules.target_fault_cdc = FFSynchronizer(self.target_fault,
                                                       target_fault)
        m.submodules.aux_fault_cdc = FFSynchronizer(self.aux_fault, aux_fault)

        m.d.comb += [
            self._lines.f.target_int.r_data.eq(target_int),
            self._lines.f.aux_int.r_data.eq(aux_int),
            self._lines.f.target_fault.r_data.eq(target_fault),
            self._lines.f.aux_fault.r_data.eq(aux_fault),

            # LEVELS, one per device, and only the `int` lines. Post-CDC rather
            # than from the pad: an interrupt raised by a metastable sample is
            # an interrupt that fires when nothing happened, the hardest kind of
            # fault to attribute. See the module docstring for why these are two
            # signals and why `fault` is neither of them.
            self.target_irq.eq(target_int),
            self.aux_irq.eq(aux_int),
        ]

        # The select the pins actually see, which follows the register only while
        # the controller is idle. See the module docstring.
        applied = Signal(2, init=BUS_POWER_MONITOR)
        with m.If(self.idle):
            m.d.sync += applied.eq(self._select.f.select.data)
        m.d.comb += self.select.eq(applied)

        return m
