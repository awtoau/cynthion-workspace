#!/usr/bin/env python3
#
# The CPU moves the HyperRAM PLL's output phase at run time. See #228, #294.
# SPDX-License-Identifier: BSD-3-Clause

"""
One write steps `EHXPLLL`'s dynamic phase shifter, and one read says it moved.

    +0  ctrl    RW  bits 1:0  sel        which output (see PHASESEL below)
                    bit 2     dir        0 forward/lagging, 1 backward
                    bit 3     step       W1, one step per write
    +4  status  RO  bit 0     busy       a step pulse is in flight
                    bit 1     locked     the HyperRAM PLL says it is locked
                    bit 2     level      the probe's raw sample
                    bit 3     probe      this build HAS a probe clock
                    bits 15:4 rotation   steps per 360 degrees of the probe
    +8  count   RO            probe highs in the last window, of WINDOW
    +c  steps   RO            net steps applied, two's complement

## What one step is

**1/8 of a VCO period**, independent of the output divider, so a full rotation of
an output is `8 * CLKOx_DIV` steps. At VCO 400 MHz that is 312.5 ps a step.
`hyperram_clocks.py` carries the citation.

## PHASESEL is not natural binary, and CLKOP is a trap

    0b11 CLKOP    0b00 CLKOS    0b01 CLKOS2    0b10 CLKOS3

CLKOP is the FEEDBACK output on this PLL. Shifting it puts the shifter inside the
loop: the PLL corrects CLKOP back and every other output moves instead. The
encoding puts that trap at `sel = 3`, which is also the value a driver reaches by
counting -- so the shell names outputs rather than numbers.

Not refused here. A gateware interlock costs a rebuild to find out what the trap
actually does, and this rig exists to measure rather than assume.

## The pulse, and why it is shaped in `sync`

The PLL acts on PHASESTEP's FALLING edge and requires (ECP5 datasheet Table 3.23,
p.71) at least 4 VCO cycles in EACH state, with PHASESEL/PHASEDIR stable 5 ns
before. nextpnr returns `TMG_IGNORE` for every EHXPLLL port, so **no timing check
in the flow covers this** -- it is met by construction or not at all.

`sync` is 60 MHz, one cycle is 16.7 ns. Two cycles high and two low is 33.3 ns
each way against a 10 ns requirement at the slowest reachable VCO (4 cycles of
400 MHz) -- 3.3x, and the margin is spent because the CPU cannot tell the
difference: a step costs two register writes either way.

`sel` and `dir` come from `ctrl`, written by an earlier store. The gap between
two CPU stores is hundreds of nanoseconds, so the 5 ns setup is met by the bus,
not by a counter -- and `busy` refuses a step while one is in flight rather than
letting a second write move `sel` mid-pulse.

## The probe, and why a lever needs one

`level` is the phase detector in `hyperram_clocks.py`: a `hr` toggle sampled on
the probe clock. Same frequency, one VCO, so it is CONSTANT for a given phase and
flips when a step walks the sample across an edge -- twice per rotation.

`count` is that bit accumulated over a fixed window, which separates the three
answers a single read cannot: solidly 0, solidly WINDOW, or somewhere between,
which is the metastable band at the crossing and is the most interesting reading
of the three. Without it a flicker reads as whichever sample the CPU happened to
take.

## WHAT THIS IS NOT: a capture-phase axis on either PHY as they stand

The lever moves phase. It does **not** move the HyperRAM read window, because on
both PHYs one clock launches CK and captures the data that comes back:

  * non-DQS (`luna .../psram.py` `HyperRAMPHY`): the CK `ODDRX1F` and the data
    `IDDRX1F` both take `i_SCLK=ClockSignal()`, which is `hr` = CLKOP.
  * DQS (`peripherals/hyperram_dqs_phy.py`): `ODDRX2DQSB` (CK), `ODDRX2DQA` (DQ)
    and `DQSBUFM`/`IDDRX2DQA` (capture) all take `i_ECLK=ClockSignal("fast")`,
    which is `hr_fast` = CLKOS.

Shift that clock by d and the launch edge moves by d, the pad's CK moves by d,
the device's answer comes back d later, and the capture edge has moved by d too.
The external delay did not change, so the margin is **identical**. It is
common-mode, and it cancels exactly.

Two consequences, both worth knowing before spending a build:

  * On non-DQS the axis does not exist at all: `hr` is CLKOP, and CLKOP is the
    feedback output, which this PLL must not shift.
  * On DQS the shiftable output IS the capture clock (CLKOS), so a shift reaches
    the capture path -- and takes CK with it. What a shift there DOES move is
    ECLK against SCLK, which is the gearbox's own alignment and not a
    measurement axis.

A live axis needs the capture clock to be a DIFFERENT PLL output from the one
that generates CK. `EHXPLLL` has the outputs for it (CLKOS2/CLKOS3 are free), but
on the DQS path the bank ECLK input mux takes CLKOP/CLKOS only and both are spoken
for -- so it is the non-DQS PHY that could be rebuilt around a separate CK clock,
not the DQS one. That is a PHY change, not a clocking change, and it is not in
this file.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr

__all__ = ["PLLPhase", "PHASESEL"]

# The encoding, from Diamond's `PLL_Options_XO2.htm` and its `EHXPLLL.v`.
PHASESEL = {"clkos": 0b00, "clkos2": 0b01, "clkos3": 0b10, "clkop": 0b11}

# `sync` cycles PHASESTEP is held in each state. See the docstring: 2 is the
# smallest integer that clears 4 VCO cycles at the slowest reachable VCO.
PULSE_CYCLES = 2

# `sync` cycles the probe bit is accumulated over. 65536 at 60 MHz is 1.1 ms --
# long enough that a metastable crossing shows as a middling count rather than as
# a coin flip, short enough that a 40-step rotation is 44 ms of shell time.
WINDOW_CYCLES = 65536


class PLLPhase(wiring.Component):
    """`ctrl` out to `HyperRAMDomains`' phase ports, and the probe back.

    `rotation` is `8 * CLKOP_DIV`, passed in rather than recomputed: it is a
    property of the dividers the PLL was built with, and a second derivation here
    would disagree the moment those moved.
    """

    def __init__(self, *, rotation, has_probe=True):
        self._rotation = int(rotation)
        self._has_probe = bool(has_probe)
        if not 1 <= self._rotation < 4096:
            raise ValueError(f"{self._rotation} steps per rotation does not fit "
                             f"the 12-bit status field")

        self._ctrl = csr.Register({
            "sel":      csr.Field(csr.action.RW, 2),
            "dir":      csr.Field(csr.action.RW, 1),
            "step":     csr.Field(csr.action.W, 1),
            "reserved": csr.Field(csr.action.R, 28),
        }, access="rw")
        self._status = csr.Register({
            "busy":     csr.Field(csr.action.R, 1),
            "locked":   csr.Field(csr.action.R, 1),
            "level":    csr.Field(csr.action.R, 1),
            "probe":    csr.Field(csr.action.R, 1),
            "rotation": csr.Field(csr.action.R, 12),
            "pad":      csr.Field(csr.action.R, 16),
        }, access="r")
        self._count = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                   access="r")
        self._steps = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                   access="r")

        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("ctrl", self._ctrl, offset=0x00)
        builder.add("status", self._status, offset=0x04)
        builder.add("count", self._count, offset=0x08)
        builder.add("steps", self._steps, offset=0x0c)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":         In(csr.Signature(addr_width=4, data_width=8)),
            "phase_sel":   Out(2),
            "phase_dir":   Out(1),
            "phase_step":  Out(1),
            "probe_level": In(1),
            "locked":      In(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        # -- the step pulse ---------------------------------------------------
        # A down-counter rather than an FSM: high for PULSE_CYCLES, low for
        # PULSE_CYCLES, and `busy` for both. The falling edge in the middle is
        # what the PLL acts on, so the SECOND half is not slack -- it is the
        # hold time after the event.
        timer = Signal(range(2 * PULSE_CYCLES + 1))
        busy = Signal()
        steps = Signal(32)

        m.d.comb += busy.eq(timer != 0)
        with m.If(busy):
            m.d.sync += timer.eq(timer - 1)
        with m.Elif(self._ctrl.f.step.w_stb & self._ctrl.f.step.w_data):
            m.d.sync += [
                timer.eq(2 * PULSE_CYCLES),
                # Counted where the pulse is ISSUED, so `steps` cannot claim a
                # step the PLL was never asked for.
                steps.eq(steps + (1 - 2 * self._ctrl.f.dir.data)),
            ]

        m.d.comb += [
            self.phase_sel.eq(self._ctrl.f.sel.data),
            self.phase_dir.eq(self._ctrl.f.dir.data),
            # High for the first half of the window, low for the second.
            self.phase_step.eq(timer > PULSE_CYCLES),
        ]

        # -- the probe --------------------------------------------------------
        # `probe_level` is a level in an unrelated domain and is CONSTANT for a
        # given phase, so the only hazard is the crossing itself -- which is the
        # reading being taken. Two FFs so a metastable sample cannot reach the
        # counter's carry chain.
        level = Signal()
        m.submodules.probe_cdc = FFSynchronizer(self.probe_level, level)

        window = Signal(range(WINDOW_CYCLES))
        highs = Signal(range(WINDOW_CYCLES + 1))
        latched = Signal(range(WINDOW_CYCLES + 1))
        with m.If(window == WINDOW_CYCLES - 1):
            m.d.sync += [window.eq(0), latched.eq(highs + level), highs.eq(0)]
        with m.Else():
            m.d.sync += [window.eq(window + 1), highs.eq(highs + level)]

        locked_sync = Signal()
        m.submodules.locked_cdc = FFSynchronizer(self.locked, locked_sync)

        m.d.comb += [
            self._ctrl.f.reserved.r_data.eq(0),
            self._status.f.busy.r_data.eq(busy),
            self._status.f.locked.r_data.eq(locked_sync),
            self._status.f.level.r_data.eq(level),
            self._status.f.probe.r_data.eq(int(self._has_probe)),
            self._status.f.rotation.r_data.eq(self._rotation),
            self._status.f.pad.r_data.eq(0),
            self._count.f.value.r_data.eq(latched),
            self._steps.f.value.r_data.eq(steps),
        ]
        return m
