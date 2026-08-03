#!/usr/bin/env python3
#
# The RISC-V core-local interruptor, as a CSR peripheral.
# SPDX-License-Identifier: BSD-3-Clause

"""
A RISC-V CLINT: the machine timer and software interrupts for one hart.

Standard, not bespoke, so that `firmware/cynthion-soc/src/timer.rs` and the
RISC-V ports of FreeRTOS, RTIC and Zephyr all work unchanged -- none of them
will drive a custom timer peripheral without a port being written first. QEMU's
`-M virt` has a CLINT too (`clint@2000000`), which is what makes
`scripts/soc_test.py` evidence about the tick that ships. The alternatives
weighed are in `../../docs/comparisons.md`.

## Register map -- the SiFive/QEMU layout, not chosen here

Byte offsets from the base of a 64 KiB window:

    0x0000  msip          RW  bit 0 raises hart 0's machine software interrupt
    0x4000  mtimecmp_lo   RW  low half of the deadline
    0x4004  mtimecmp_hi   RW  high half
    0xbff8  mtime_lo      R   low half of the counter
    0xbffc  mtime_hi      R   high half

  * One hart. `virt`'s hart 0 is at the same offsets, so no stride arithmetic is
    needed on either target.
  * `mtimecmp` and `mtime` are TWO 32-bit registers each, not one 64-bit
    register. That is what the map already is on a 32-bit machine, and it is
    also what `amaranth_soc.csr` requires: a multi-byte register commits a write
    on its LAST byte, so a single 64-bit `mtimecmp` could not be written half at
    a time -- and writing it half at a time is the whole of the standard
    no-spurious-interrupt sequence. See `timer.rs`.
  * Bytes above the implemented width read zero and ignore writes.

## Register discipline

**No read in this peripheral changes any state**, so there is nothing to keep
apart from a poll loop and nothing to tabulate. `mtime` is a wire from the
counter; `mtimecmp` and `msip` read back their own storage. Compare
`uart16550.py`, which has three side-effecting reads and a table saying so.

## mtime is read-only, and it is the counter `rdtime` already reads

`mtime` is an input, driven in `vexii_hello_soc.py` from `VexiiRiscv.mtime` --
the same free-running counter the `time` CSR reads. One counter, so `csrr time`
and a load from 0xbff8 cannot disagree, and `src/clock.rs` and `src/timer.rs`
are measuring the same thing.

Writing `mtime` is permitted by the specification and is not implemented here.
Nothing needs it: firmware sets a deadline relative to now, it does not reset
now. A writable `mtime` would need the counter to move out of the CPU wrapper
and would make `time` a value someone could step backwards, which every
interval in this firmware is written assuming cannot happen.

## mtimecmp resets to all ones

Which is "never". The specification does not define a reset value, and QEMU's
resets to zero -- where `mtime >= mtimecmp` is true immediately and the line is
asserted from the first cycle. Harmless, because `mie.MTIE` is clear out of
reset, but it means a probe or an ILA sees a timer interrupt asserted on a
design nobody has programmed yet. All ones is quiet until asked.

**Firmware must not depend on either value.** `timer::init` writes `mtimecmp`
before it enables `mie.MTIE`, which is the only sequence that is correct on both
targets.

## The interrupt is a LEVEL

`irq_timer` is `mtime >= mtimecmp`, held for as long as that holds, exactly as
the specification defines `mip.MTIP`. There is no acknowledge register: the only
way to lower the line is to move the deadline, which is what the handler's
`mtimecmp += period` does. A handler that returns without advancing it is
re-entered immediately and forever -- the same livelock `vexii_plic.py` and
`uart16550.py` describe, arrived at from the other direction.

## Cost

Two 64-bit comparisons' worth of carry chain, one flip-flop per bit of
`mtimecmp` (64) and `msip` (1), and the 16-bit address decode. The counter
itself is not here -- it already existed in `vexii_cpu.py` for `rdtime`.

The comparator's result is REGISTERED rather than driven straight out. A 64-bit
`>=` is a 64-bit carry chain, and this design has spent commits recovering
timing margin on the Wishbone path; putting a flip-flop after the compare means
the chain begins and ends at a register and feeds nothing else. The cost is one
`sync` cycle of latency on a 1 ms tick, which is 16.7 ns in 1 ms.
"""

from amaranth              import Module, Signal, Cat, unsigned
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out

from amaranth_soc          import csr


__all__ = ["Clint"]


# Fixed offsets, from the SiFive CLINT layout every RISC-V machine uses and
# QEMU's `virt` presents. Not parameters: a CLINT at different offsets is not a
# CLINT, and the whole reason for this file is that a generic driver can find
# its way around without being told.
MSIP_BASE     = 0x0000
MTIMECMP_BASE = 0x4000
MTIME_BASE    = 0xbff8

# 64 KiB, which is the window `virt` advertises (`reg = <0x0 0x2000000 0x0
# 0x10000>`). `mtime_hi` at 0xbffc is the last thing in the map, so 16 bits is
# also the smallest address width that can hold it.
ADDR_WIDTH = 16

# "Never", as a deadline. See the module docstring.
NEVER = 0xffff_ffff


class Clint(wiring.Component):
    """A single-hart RISC-V CLINT on a byte-wide CSR bus.

    Attributes
    ----------
    bus : csr.Interface(addr_width=16, data_width=8)
        The register map above. Add through a `WishboneCSRBridge`.
    mtime : Signal(64), in
        The machine timer counter. Drive it from the same counter the `time` CSR
        reads -- `VexiiRiscv.mtime` -- so that the two cannot disagree.
    irq_timer : Signal(), out
        To the CPU's machine timer interrupt input. A level: `mtime >= mtimecmp`,
        registered.
    irq_software : Signal(), out
        To the CPU's machine software interrupt input. `msip` bit 0, and nothing
        else.
    """

    def __init__(self):
        # Bit 0 only. The specification defines msip as a 32-bit register whose
        # upper 31 bits are read-only zero, so a driver may store a word here
        # and read back what it means.
        self._msip = csr.Register({"pending": csr.Field(csr.action.RW, 1)},
                                  access="rw")

        self._mtimecmp_lo = csr.Register(
            {"value": csr.Field(csr.action.RW, 32, init=NEVER)}, access="rw")
        self._mtimecmp_hi = csr.Register(
            {"value": csr.Field(csr.action.RW, 32, init=NEVER)}, access="rw")

        # Read-only, and combinational from the counter. Two registers rather
        # than one, so that a 32-bit machine's two loads are two register reads
        # and neither is a partial access to something wider.
        self._mtime_lo = csr.Register(
            {"value": csr.Field(csr.action.R, 32)}, access="r")
        self._mtime_hi = csr.Register(
            {"value": csr.Field(csr.action.R, 32)}, access="r")

        builder = csr.Builder(addr_width=ADDR_WIDTH, data_width=8)
        builder.add("msip",        self._msip,        offset=MSIP_BASE)
        builder.add("mtimecmp_lo", self._mtimecmp_lo, offset=MTIMECMP_BASE + 0)
        builder.add("mtimecmp_hi", self._mtimecmp_hi, offset=MTIMECMP_BASE + 4)
        builder.add("mtime_lo",    self._mtime_lo,    offset=MTIME_BASE + 0)
        builder.add("mtime_hi",    self._mtime_hi,    offset=MTIME_BASE + 4)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":          In(csr.Signature(addr_width=ADDR_WIDTH,
                                             data_width=8)),
            "mtime":        In(unsigned(64)),
            "irq_timer":    Out(unsigned(1)),
            "irq_software": Out(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        mtimecmp = Cat(self._mtimecmp_lo.f.value.data,
                       self._mtimecmp_hi.f.value.data)

        m.d.comb += [
            self._mtime_lo.f.value.r_data.eq(self.mtime[:32]),
            self._mtime_hi.f.value.r_data.eq(self.mtime[32:]),
        ]

        # `>=`, not `==`. A deadline that is merely passed must still fire: the
        # counter advances every cycle and a handler that is late by one tick
        # would otherwise miss the match and wait for the counter to wrap --
        # 9700 years at 60 MHz, which reads as a tick that stopped for no reason.
        # It is also what the specification says (`mip.MTIP` is set while
        # `mtime >= mtimecmp`).
        fired = Signal()
        m.d.sync += fired.eq(self.mtime >= mtimecmp)

        m.d.comb += [
            self.irq_timer.eq(fired),
            self.irq_software.eq(self._msip.f.pending.data),
        ]

        return m
