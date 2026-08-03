#!/usr/bin/env python3
#
# CPU control of the VBUS distribution switches.
# SPDX-License-Identifier: BSD-3-Clause

"""
One register, so firmware can pass power from a host port through to a target.

    +0  ctrl   RW   bit 3  target_c_vbus_en     TARGET-C  <-> the shared rail
                    bit 4  control_vbus_en      CONTROL   <-> the shared rail
                    bit 5  aux_vbus_en          AUX       <-> the shared rail
                    bit 6  target_a_discharge   drain TARGET A
                    bit 7  enable               gate: all switches open when 0

    +1  input  RW   bit 0  control_in           CONTROL powers the board  (1 at reset)
                    bit 1  aux_in               AUX powers the board      (1 at reset)

## `input` is a separate register, and is NOT behind the gate

These two are the board's own power inputs, and they are the pins GSG mean by
"the hardware now allows the user to select which port to use if 5 V supplies are
available on both ports". They are worth driving: an unrequested pin is left
high-Z and the board's pull decides it, which is an unknown default rather than a
safe one.

**But clearing both cuts this SoC's own supply.** The FPGA would lose power, lose
its configuration, release the pins, and power would return -- a reset loop, not
a brick, but not something to reach by accident either. So:

  * they reset to **1, enabled**, because the board must come up powered;
  * they are **not** gated by `ctrl.enable`. That gate exists so firmware can
    open every passthrough in one write; wiring the power inputs to it would make
    that same write cut the board's own power, which inverts its purpose;
  * they live in a different register so a careless write to `ctrl` cannot
    disturb them.

Overvoltage shutoff of an input is automatic in hardware above 5.5 V (`D17`, a
5.6 V zener) and does not need these. They are for choosing an input, and for
deliberately isolating one.

## The bit numbering is upstream's, deliberately

`repos/packetry/src/backend/cynthion.rs` carries the same layout in its analyzer
state word, and `cynthion/.../gateware/analyzer/top.py` drives the same four
switches from it. Keeping the numbering identical means a reading of one design
transfers to the other; a different order for the same switches would be
gratuitous incompatibility. Bits 0-2 are the analyzer's own (`enable`, `speed`)
and are left as holes here rather than repacked, for the same reason.

## What the switches do

TARGET A is the common node. Each enable connects its port to that shared rail,
so **one switch closed passes that port's VBUS to TARGET A**, and two closed
passes one port's VBUS to TARGET A *and onward* to the other port. Two switches
is routing one supply to two destinations, not tying two supplies together --
see `docs/hardware.md`, and mossmann in greatscottgadgets/cynthion#184.

## `enable` is a gate, and it is why this is not eight GPIO pins

Everything opens when bit 7 is clear, including out of reset. A register that
came up with a switch closed would pass power before firmware ran, and the reset
state is the one thing here no amount of later care can fix.

`amaranth_soc.gpio.Peripheral` would have been less code and is the wrong shape:
it offers interchangeable pins with no semantics, and these bits are not
interchangeable. Named fields document themselves, and the gate is expressible
here in a way a pin array cannot express at all.

## What this does NOT do

**No interlock, deliberately, and no voltage check.** Both were considered and
neither belongs in gateware:

  * The board already protects itself. Overvoltage shuts either *input* off
    automatically above 5.5 V (D17, a 5.6 V zener), input and passthrough are
    separate functions, and the passthrough network is rated for the full 20 V
    SPR range. Nothing firmware can command here damages Cynthion.
  * What remains at risk is an attached device -- a USB-A peripheral on TARGET A
    cannot refuse 20 V arriving from a PD source on TARGET-C. Guarding that needs
    the PAC1954's measured voltage, and gateware cannot read an I2C part. It is a
    firmware job, which is what GSG describe the monitors being for.

So this register is the mechanism and firmware holds the policy. See
`firmware/cynthion-soc/src/vbus.rs`.
"""

from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr


# Bit positions, upstream's. Exported so the firmware generator and any test
# read them from one place rather than transcribing.
BIT_TARGET_C  = 3
BIT_CONTROL   = 4
BIT_AUX       = 5
BIT_DISCHARGE = 6
BIT_ENABLE    = 7


class VbusControl(wiring.Component):
    """The VBUS distribution switches, behind one CSR."""

    def __init__(self):
        self._ctrl = csr.Register({
            # Bits 0-2 are upstream's analyzer fields. Held as a read-only zero
            # hole so the numbering matches without inventing meanings for them.
            "reserved":   csr.Field(csr.action.R, 3),
            "target_c":   csr.Field(csr.action.RW, 1),
            "control":    csr.Field(csr.action.RW, 1),
            "aux":        csr.Field(csr.action.RW, 1),
            "discharge":  csr.Field(csr.action.RW, 1),
            "enable":     csr.Field(csr.action.RW, 1),
        }, access="rw")

        # The power inputs. `init=1` on both: the board must come up powered, and
        # a register that reset to 0 here would cut this SoC's own supply the
        # instant the FPGA configured.
        self._input = csr.Register({
            "control_in": csr.Field(csr.action.RW, 1, init=1),
            "aux_in":     csr.Field(csr.action.RW, 1, init=1),
            "reserved":   csr.Field(csr.action.R, 6),
        }, access="rw")

        builder = csr.Builder(addr_width=2, data_width=8)
        builder.add("ctrl", self._ctrl, offset=0x00)
        builder.add("input", self._input, offset=0x01)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=2, data_width=8)),

            # To the pads. Active high, matching the platform's `Pins(...)`.
            "target_c_vbus_en":  Out(1),
            "control_vbus_en":   Out(1),
            "aux_vbus_en":       Out(1),
            "target_a_discharge": Out(1),

            # The power inputs. Declared `PinsN` in the platform, so Amaranth has
            # already undone the active-low sense: a 1 here drives the pad low and
            # the input is ON. Reset is 1 for the reason in the module docstring.
            "control_vbus_in_en": Out(1, init=1),
            "aux_vbus_in_en":     Out(1, init=1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        m.d.comb += self._ctrl.f.reserved.r_data.eq(0)

        # The gate. Combinational rather than a latched copy: `enable` going low
        # must open every switch in the same cycle, so a firmware panic handler
        # can clear one bit and know the power path is open.
        gate = self._ctrl.f.enable.data
        m.d.comb += [
            self.target_c_vbus_en   .eq(gate & self._ctrl.f.target_c.data),
            self.control_vbus_en    .eq(gate & self._ctrl.f.control.data),
            self.aux_vbus_en        .eq(gate & self._ctrl.f.aux.data),
            self.target_a_discharge .eq(gate & self._ctrl.f.discharge.data),

            # Ungated, deliberately. `ctrl.enable` opens every passthrough in one
            # write; if it reached these, that same write would cut the board's
            # own power. See the module docstring.
            self.control_vbus_in_en .eq(self._input.f.control_in.data),
            self.aux_vbus_in_en     .eq(self._input.f.aux_in.data),
        ]
        m.d.comb += self._input.f.reserved.r_data.eq(0)
        return m
