#!/usr/bin/env python3
#
# What the bitstream is, readable by the CPU inside it.
# SPDX-License-Identifier: BSD-3-Clause

"""
What bitstream this is, and the one thing the die will say about itself.

Firmware and gateware are built separately and travel together by convention
only -- `load` replaces the firmware without rebuilding the bitstream, which is
the point of it -- so they can disagree about where a peripheral is, how wide a
register is, or what `sync` runs at. Every one of those is silent: the firmware
reads a plausible number from the wrong place and reports it.

These registers are the gateware's own account of itself, so
`firmware/cynthion-soc/src/info.rs` can print it beside the firmware's and say
when the two were built from different trees.

## Registers

    +0x00  magic    32  R   0x43594e31, "CYN1" -- this window answers at all
    +0x04  git      32  R   bit 31 dirty, bits 30..0 the short git hash
    +0x08  built    32  R   when, packed (below)
    +0x0c  sync_hz  32  R   what `sync` ACTUALLY runs at, in Hz
    +0x10  cpu      32  R   cache geometry and the ISA the core was generated with
    +0x14  usb_hz   32  R   what the `usb` domain actually runs at, in Hz
    +0x18  die       9  R   the ECP5's temperature readout, live (below)

`git` is the encoding `../build_helpers.py` already stamps into the ECP5's
USERCODE, and it is the same function that produces it -- so the value Apollo
reads over JTAG and the value the CPU reads here cannot disagree.

`built` is packed rather than an epoch count so that firmware can print a date
without a calendar routine:

    bits 31..26  year - 2000    bits 16..12  hour
    bits 25..22  month 1..12    bits 11..6   minute
    bits 21..17  day 1..31      bits  5..0   second

UTC, so the field means one thing wherever it is read. Minute resolution would
have fit in 26 bits; seconds are there because two bitstreams built a minute
apart during a bring-up session are exactly the pair that get confused.

`sync_hz` and `usb_hz` are `VariableClockDomainGenerator`'s `actual_*_mhz`, not
the requested figures. The PLL divides a solved VCO and lands where it lands;
its own docstring says to report what it produced. `firmware/.../target.rs`
derives `TIME_HZ` from the request by hand, and this is what lets that copy be
checked instead of trusted.

`cpu` carries what `cpu/cpu.py` was asked to generate, which is not otherwise
knowable from inside the process: `misa` on this core reads 0x40001100 -- `rv32im`
-- while it is generated `--with-rva --with-rvc`, so the core understates itself
and this is the other account.

    bits 15..0   cache sets (fetch and lsu are generated equal)
    bits 23..16  cache ways
    bit 24 M     bit 25 A      bit 26 C      bit 27 rdtime

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

## What the ECP5 will NOT tell the CPU

Worth writing down, because each was checked rather than assumed:

    USERCODE   JTAG only (opcode 0xc0). No fabric primitive exists in Lattice's
               ecp5u.v, in yosys, in prjtrellis or in nextpnr -- it is a command
               in the bitstream's command stream, not a bit in a tile, so there
               is no wire to read. Hence this peripheral.
    IDCODE     JTAG only (0xe0). 0x21111043 on a 12F, 0x41111043 on a 25F --
               the field this board's die-identity question turns on, and the
               CPU cannot see it. Apollo can.
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
written. `die` is the one field that varies, and it varies on its own account --
so this window is outside the hazards `peripherals/uart16550.py` describes, and a diagnostic
may read any of it at any rate.

## Why the values are not in the SVD

`scripts/soc_generate_pac.py` recovers a register's reset value from its field
actions, and `csr.action.R` has none -- so the generated crate carries these
addresses and a `resetMask` of zero. That is correct: the value is a property of
the bitstream on the board, not of the source the PAC was generated from, and a
constant baked into the firmware's copy of the map would be precisely the lie
this peripheral exists to catch.
"""

import sys
from pathlib import Path

from amaranth               import Cat, Const, Instance, Module, Signal
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In

from amaranth_soc           import csr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_helpers import usercode


__all__ = ["GatewareId", "MAGIC", "DIE_PRESENT", "pack_built", "pack_cpu",
           "CPU_M", "CPU_A", "CPU_C", "CPU_RDTIME"]


# "CYN1". A window that decodes to nothing reads as zeros on this bus rather
# than faulting, so a peripheral that is simply absent is otherwise invisible --
# the same failure `ports` guards against on the 16550s. Zero and 0xffffffff are
# both excluded by construction here.
MAGIC = 0x4359_4e31

CPU_M      = 1 << 24
CPU_A      = 1 << 25
CPU_C      = 1 << 26
CPU_RDTIME = 1 << 27


def pack_built(when):
    """Pack a `datetime` in UTC into the `built` word."""
    when = when.utctimetuple()
    return (((when.tm_year - 2000) & 0x3f) << 26
            | (when.tm_mon & 0xf) << 22
            | (when.tm_mday & 0x1f) << 17
            | (when.tm_hour & 0x1f) << 12
            | (when.tm_min & 0x3f) << 6
            | (when.tm_sec & 0x3f))


def pack_cpu(sets, ways, flags):
    """Pack the cache geometry and ISA flags into the `cpu` word."""
    return (sets & 0xffff) | (ways & 0xff) << 16 | flags


# How often a temperature conversion is started, as a power of two `sync`
# cycles. 2**19 is 8.7 ms at 60 MHz -- see the `die` section above for why that
# is neither too fast nor a figure that needs tuning.
DTR_PERIOD_BITS = 19

# In `die`: the block is present in this build at all.
DIE_PRESENT = 1 << 8


class GatewareId(wiring.Component):
    """What this bitstream is, and what the die says.

    Attributes
    ----------
    bus : csr.Interface(addr_width=5, data_width=8)
        The seven registers.

    Parameters
    ----------
    sync_hz, usb_hz : int
        What the two domains ACTUALLY run at -- the domain generator's
        `actual_sync_mhz` and `actual_usb_mhz`, not the requested figures.
        `target::TIME_HZ` in the firmware is a hand-derived copy of the request,
        and this is what lets it be checked rather than trusted.
    cache_sets, cache_ways : int
        As passed to `cpu/cpu.py`'s `VexiiRiscv`.
    cpu_flags : int
        The CPU_* bits above, for the ISA extensions the core was generated
        with.
    built : datetime or None
        When, in UTC. `None` means now, which is what a build wants; a caller
        passes one only to make a test deterministic.
    git : int or None
        The USERCODE-format identifier. `None` reads the working tree.
    """

    def __init__(self, *, sync_hz, usb_hz, cache_sets, cache_ways=1,
                 cpu_flags=CPU_M | CPU_A | CPU_C | CPU_RDTIME,
                 built=None, git=None):
        if built is None:
            from datetime import datetime, timezone
            built = datetime.now(timezone.utc)
        self._values = {
            "magic":   MAGIC,
            "git":     usercode() if git is None else git,
            "built":   pack_built(built),
            "sync_hz": int(sync_hz),
            "cpu":     pack_cpu(cache_sets, cache_ways, cpu_flags),
            "usb_hz":  int(usb_hz),
        }

        self._regs = {
            name: csr.Register({"value": csr.Field(csr.action.R, 32)},
                               access="r")
            for name in self._values
        }
        # Nine bits: the eight the block produces, plus one saying whether there
        # is a block. Zero from a design with no DTR and zero from a DTR that has
        # never completed a conversion would otherwise be the same reading.
        self._die = csr.Register({"value": csr.Field(csr.action.R, 9)},
                                 access="r")

        # addr_width=5 -- six 32-bit registers and one more is 28 bytes, and a
        # window must be a power of two aligned to its own size.
        builder = csr.Builder(addr_width=5, data_width=8)
        for name, register in self._regs.items():
            builder.add(name, register)
        builder.add("die", self._die)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=5, data_width=8)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        for name, register in self._regs.items():
            m.d.comb += register.f.value.r_data.eq(Const(self._values[name], 32))

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


if __name__ == "__main__":
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    print(f"magic {MAGIC:#010x}  git {usercode():#010x}  "
          f"built {pack_built(now):#010x}  ({now.isoformat()})")
