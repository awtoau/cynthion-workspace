#!/usr/bin/env python3
#
# The two things only the fabric knows, readable by the CPU inside it.
# SPDX-License-Identifier: BSD-3-Clause

"""
What the die and the bus are DOING. Nothing about what this bitstream is.

## Registers

    +0x00  die         9  R   the ECP5's temperature readout, live (below)
    +0x04  bus_fault  32  R   what `bus/fault.BusFault` has terminated

Both vary at runtime. Neither can be known from anywhere else on this board.

## Nothing constant belongs here

A constant in fabric is folded logic that moves with its value (#447, #443).
Where each build fact lives instead:

    which bitstream    ECP5 USERCODE, stamped by `ecppack --usercode`, resolved
                       by `gateware/usercode_map.py` and checked over JTAG by
                       `scripts/soc_confirm.py`
    declared clocks    the build record, and `target::TIME_HZ` for the firmware's
                       own expectation -- `scripts/soc_generate_pac.py` generates
                       it from `SYNC_MHZ`
    cache and ISA      the build record, and `target::CPU_HAS_*`
    presence           `die` bit 8, set unconditionally in every platform build
                       and zero from a window that decodes to nothing

## `die` -- the temperature readout

The ECP5 has one DTR block: pulse `STARTPULSE`, and eight cycles later
`DTROUT[7]` goes high and `DTROUT[5:0]` holds a code.

    bit 8     this build has a DTR -- 0 in simulation and on any platformless
              elaboration, where the block is not instantiated at all
    bit 7     valid, as the block reports it
    bits 5..0 the code

**The code is not degrees.** Table 4.3 of FPGA-TN-02210 (*Power Consumption and
Management for ECP5 and ECP5-5G Devices*) maps the 64 codes to junction
temperatures, and the mapping is not linear -- 1 degree per step between codes
21 and 29, ten degrees per step at the ends. The table is in
`firmware/cynthion-soc/src/info.rs`, applied there rather than here, and the
datasheet's own warning travels with it: section 2.20 of FPGA-DS-02012 says the
block "is not specifically calibrated for absolute accuracy".

A conversion is started every 2**19 `sync` cycles -- 8.7 ms at 60 MHz. The
conversion itself is tens of microseconds in Lattice's simulation model, so the
period is two orders of magnitude clear of it, and a die's thermal time constant
is seconds: a faster cadence would report the same number more often. It is
free-running rather than started by a register write because a read that changes
state is the hazard this SoC's register discipline exists to avoid, and a
temperature nobody asked for costs a counter.

## `bus_fault`

What `bus/fault.BusFault` has terminated, and how close it came.

`worst` is the measurement that keeps `BUS_TIMEOUT_CYCLES` honest: the bound is
1.25x a DERIVED worst case, and a board reporting a high-water mark anywhere
near it is one about to fault on legitimate traffic. Without a way to read it,
the margin is an assertion nobody can check -- the dead-instrument problem #411
was filed about.

## What the ECP5 will NOT tell the CPU

Worth writing down, because each was checked rather than assumed:

    USERCODE   JTAG only (opcode 0xc0). No fabric primitive exists in Lattice's
               ecp5u.v, in yosys, in prjtrellis or in nextpnr -- it is a command
               in the bitstream's command stream, not a bit in a tile, so there
               is no wire to read. That is what makes it free, and why the
               identity check is the host's.
    IDCODE     JTAG only (0xe0). 0x21111043 on a 12F, 0x41111043 on a 25F --
               prjtrellis' `devices.json` is the table, and the CPU cannot see
               it. Apollo can; `scripts/soc_confirm.py` checks it.
    TraceID    JTAG only (0x19), 64 bits, of which 8 are user-programmable
               through the one-time feature row. There is no fabric path and no
               confirmed per-die serial: the chip cannot tell the CPU which chip
               it is.
    SED CRC    the soft-error block exists in silicon and in prjtrellis, but
               yosys has no SEDGA cell and nextpnr's bitstream writer never
               emits SED.CHECKALWAYS -- so it cannot be enabled from this flow.
    PLL lock   the domain generator holds `sync` in reset until the PLL locks,
               so a CPU that is executing has already answered the question.

## Register discipline

Every read returns what the field holds and changes nothing; nothing here can be
written. Both fields vary on their own account -- so this window is outside the
hazards `peripherals/uart16550.py` describes, and a diagnostic may read either
of them at any rate.
"""

from amaranth               import Cat, Instance, Module, Signal
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In

from amaranth_soc           import csr


__all__ = ["FabricStatus", "DIE_PRESENT"]


# How often a temperature conversion is started, as a power of two `sync`
# cycles. 2**19 is 8.7 ms at 60 MHz -- see the `die` section above for why that
# is neither too fast nor a figure that needs tuning.
DTR_PERIOD_BITS = 19

# In `die`: the block is present in this build at all. Also the window's presence
# guard, since it is set unconditionally in every platform build.
DIE_PRESENT = 1 << 8


class FabricStatus(wiring.Component):
    """The die's temperature and the bus's fault counters.

    Attributes
    ----------
    bus : csr.Interface(addr_width=3, data_width=8)
        Two registers.
    """

    def __init__(self):
        # Nine bits: the eight the block produces, plus one saying whether there
        # is a block. Zero from a design with no DTR and zero from a DTR that has
        # never completed a conversion would otherwise be the same reading.
        self._die = csr.Register({"value": csr.Field(csr.action.R, 9)},
                                 access="r")
        self._bus_fault = csr.Register({
            "unclaimed": csr.Field(csr.action.R, 8),
            "timeouts":  csr.Field(csr.action.R, 8),
            "worst":     csr.Field(csr.action.R, 16),
        }, access="r")

        # addr_width=3 -- two 32-bit registers is 8 bytes, and a window must be a
        # power of two aligned to its own size.
        builder = csr.Builder(addr_width=3, data_width=8)
        builder.add("die", self._die)
        builder.add("bus_fault", self._bus_fault)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=3, data_width=8)),
            # From `BusFault`. Undriven in a design without one, which reads as
            # "nothing has been terminated" -- true of a design that cannot
            # terminate anything.
            "fault_unclaimed": In(8),
            "fault_timeouts": In(8),
            "fault_worst": In(16),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        m.d.comb += [
            self._bus_fault.f.unclaimed.r_data.eq(self.fault_unclaimed),
            self._bus_fault.f.timeouts.r_data.eq(self.fault_timeouts),
            self._bus_fault.f.worst.r_data.eq(self.fault_worst),
        ]

        # The DTR is a hard block, so it exists only when there is a device to
        # put it in. Guarding on the platform keeps every simulation of this
        # peripheral -- and the elaboration `soc_generate_pac.py` does -- free of
        # a black box nothing can model.
        if platform is None:
            m.d.comb += self._die.f.value.r_data.eq(0)
            return m

        counter = Signal(DTR_PERIOD_BITS)
        readout = Signal(8)
        m.d.sync += counter.eq(counter + 1)

        bits = [Signal(name=f"dtrout{index}") for index in range(8)]
        m.submodules.dtr = Instance(
            "DTR",
            i_STARTPULSE=(counter == 0),
            **{f"o_DTROUT{index}": bit for index, bit in enumerate(bits)})

        # Latched, and only while the block says the value is good. DTROUT[7] is
        # the valid bit; sampling the code without it would report whatever the
        # outputs hold mid-conversion.
        with m.If(bits[7]):
            m.d.sync += readout.eq(Cat(*bits))

        m.d.comb += self._die.f.value.r_data.eq(readout | DIE_PRESENT)

        return m
