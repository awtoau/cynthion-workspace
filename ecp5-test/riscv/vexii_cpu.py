#!/usr/bin/env python3
#
# VexiiRiscv as a drop-in replacement for luna_soc's VexRiscv component.
# SPDX-License-Identifier: BSD-3-Clause

"""
Wraps a Wishbone-configured VexiiRiscv in the shape luna_soc's VexRiscv has.

The moondancer SoC attaches its CPU by two Wishbone masters, `ibus` and `dbus`,
each 30-bit addressed with 32-bit data and the `err`/`cti`/`bte` features. If a
replacement presents the same signature, the rest of the SoC does not have to
change: the arbiter, the decoder, the CSR bridge and every peripheral see a
Wishbone master and do not care what generates it.

VexiiRiscv's *default* configuration would be a poor fit -- 41 top-level ports
across three native SpinalHDL stream buses. But it ships Wishbone bridges
upstream, and with them the core presents exactly two Wishbone masters.

**Caches are not optional here.** The cacheless Wishbone bridge asserts
`!up.p.withAmo` (`LsuCachelessBridge.scala:203`), so it cannot be built with
atomics. Moondancer's firmware targets `riscv32imac`, and the A is atomics. The
L1 bridge carries no such assertion, so the cached configuration is the only one
that can run the existing firmware.

Three interrupt lines replace VexRiscv's 32-bit `irq_external` array. VexRiscv
used a non-standard `ExternalInterruptArrayPlugin` with mask and pending
registers inside the CPU at custom CSRs 0xBC0/0xFC0; VexiiRiscv implements only
standard RISC-V, where the external interrupt is a single wire. Concentrating
many sources onto it, and letting software find out which fired, needs a
separate peripheral -- see `vexii_irq.py`.

    from vexii_cpu import VexiiRiscv
    cpu = VexiiRiscv(reset_addr=0x100b0000)
"""

import subprocess
from pathlib import Path

from amaranth               import Elaboratable, Module, Signal, Instance, Cat
from amaranth               import ClockSignal, ResetSignal
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In, Out
from amaranth.hdl           import unsigned

from amaranth_soc           import wishbone

ROOT = Path(__file__).resolve().parent.parent.parent
VEXII = ROOT / "repos" / "vexiiriscv"

# The generator flags that produce a Wishbone core with caches and atomics.
#
# --lsu-l1-wishbone is separate from --lsu-wishbone and is the one that matters
# here: the flags dispatch per path (Param.scala:932 cacheless, 972 cached), so
# passing only the cacheless flags with caches enabled silently produces a
# native core with no warning at all.
GENERATE_FLAGS = [
    "--xlen", "32",
    "--with-rvm", "--with-rvc", "--with-rva", "--with-rdtime",
    "--with-fetch-l1", "--fetch-l1-sets", "64", "--fetch-l1-ways", "1",
    "--with-lsu-l1", "--lsu-l1-sets", "64", "--lsu-l1-ways", "1",
    # All three are needed. A cached core still has an uncached LSU path --
    # that is how it reaches I/O regions, and peripherals live in one -- so it
    # appears as its own top-level bus. Omitting --lsu-wishbone leaves it
    # native and unconnected, and the only symptom is undriven
    # LsuPlugin_logic_bus_* wires.
    "--fetch-wishbone", "--lsu-wishbone", "--lsu-l1-wishbone",
]


# The SoC's address map, as PMA regions. Anything not listed here is
# unreachable rather than merely uncached.
DEFAULT_REGIONS = [
    "base=00000000,size=00010000,main=1,exe=1",   # block RAM
    "base=f0000000,size=10000000,main=0,exe=0",   # CSR peripherals
]


def generate(reset_addr, cache_sets=64, output=None, regions=None):
    """Run the Scala generator and return the path to the Verilog.

    Regenerating is a few seconds, so this is called at elaboration rather than
    checked in. The alternative -- a committed .v -- drifts silently from the
    flags that produced it.
    """
    regions = regions if regions is not None else DEFAULT_REGIONS
    flags = list(GENERATE_FLAGS)
    for index, flag in enumerate(flags):
        if flag in ("--fetch-l1-sets", "--lsu-l1-sets"):
            flags[index + 1] = str(cache_sets)
    flags += ["--reset-vector", hex(reset_addr)]

    # Declare every region the SoC actually has. VexiiRiscv's defaultPma
    # (Param.scala:58-77) covers only 0x80000000 and 0x10000000, so a design
    # with memory at 0x00000000 has it in no region at all and every access --
    # including every stack operation -- traps. The failure looks exactly like
    # a dead CPU.
    #
    # main=1 means normal cacheable memory; main=0 marks I/O, which is what
    # keeps peripheral accesses out of the data cache. That replaces
    # VexRiscv's hardcoded "uncached iff address bit 31".
    for region in regions:
        flags += ["--region", region]

    result = subprocess.run(
        ["sbt", "--batch", "--no-server",
         f"runMain vexiiriscv.Generate {' '.join(flags)}"],
        cwd=VEXII, capture_output=True, text=True)

    if result.returncode != 0:
        errors = [l for l in result.stdout.splitlines()
                  if l.startswith("[error]")]
        raise RuntimeError("VexiiRiscv generation failed: "
                           + (errors[-1] if errors else "unknown"))

    emitted = VEXII / "VexiiRiscv.v"
    if not emitted.exists():
        raise RuntimeError("generator produced no VexiiRiscv.v")

    if output:
        output.write_bytes(emitted.read_bytes())
        return output
    return emitted


class VexiiRiscv(wiring.Component):
    """VexiiRiscv presenting luna_soc's VexRiscv interface.

    `irq_external` is a single line rather than VexRiscv's 32-bit array,
    because that is what standard RISC-V defines. Everything else matches, so
    this substitutes into `facedancer/top.py` without touching the bus fabric.
    """

    name       = "vexiiriscv"
    arch       = "riscv"
    byteorder  = "little"
    data_width = 32

    def __init__(self, *, reset_addr=0x00000000, cache_sets=64,
                 regions=None):
        self._reset_addr = reset_addr
        self._cache_sets = cache_sets
        self._regions = regions

        super().__init__({
            "ext_reset":    In(unsigned(1)),

            # One wire, not 32. See the module docstring.
            "irq_external": In(unsigned(1)),
            "irq_timer":    In(unsigned(1)),
            "irq_software": In(unsigned(1)),

            "ibus": Out(wishbone.Signature(
                addr_width=30, data_width=32, granularity=8,
                features=("err", "cti", "bte"))),
            "dbus": Out(wishbone.Signature(
                addr_width=30, data_width=32, granularity=8,
                features=("err", "cti", "bte"))),

            # The uncached data path, and the reason this CPU has three masters
            # where VexRiscv has two.
            #
            # A write-back cache can only move whole lines, so MMIO cannot go
            # through `dbus`: the cache would fetch a line (reading registers
            # nobody asked to read), modify one word, and write the line back
            # (disturbing neighbours) whenever it happened to evict -- not when
            # the store was issued. Volatile peripheral access needs a path that
            # does exactly the transfer requested, exactly when requested.
            #
            # So every access to a `main=0` PMA region arrives here instead.
            # VexRiscv achieves the same split with one bus and a hardcoded
            # "uncached iff address bit 31"; VexiiRiscv makes it declarative per
            # region, which is more capable and less forgiving -- leaving this
            # port unconnected costs nothing at synthesis and produces a CPU
            # that runs, passes timing, enumerates, and never reaches a
            # peripheral, because the store waits forever for an ACK that has
            # no driver.
            "iobus": Out(wishbone.Signature(
                addr_width=30, data_width=32, granularity=8,
                features=("err", "cti", "bte"))),
        })

    def elaborate(self, platform):
        m = Module()

        verilog = generate(self._reset_addr, self._cache_sets,
                           regions=self._regions)
        if platform is not None:
            platform.add_file("VexiiRiscv.v", verilog.read_text())

        # rdtime is a mandatory input with no VexRiscv equivalent. Leaving it
        # unconnected does not fail synthesis -- it silently breaks every
        # rdtime read, which reads as a firmware bug rather than a wiring one.
        rdtime = Signal(64)
        m.d.sync += rdtime.eq(rdtime + 1)

        m.submodules.cpu = Instance(
            "VexiiRiscv",
            i_clk=ClockSignal("sync"),
            i_reset=ResetSignal("sync") | self.ext_reset,

            i_PrivilegedPlugin_logic_rdtime=rdtime,
            i_PrivilegedPlugin_logic_harts_0_int_m_external=self.irq_external,
            i_PrivilegedPlugin_logic_harts_0_int_m_timer=self.irq_timer,
            i_PrivilegedPlugin_logic_harts_0_int_m_software=self.irq_software,

            # Instruction bus.
            o_FetchL1WishbonePlugin_logic_bus_CYC=self.ibus.cyc,
            o_FetchL1WishbonePlugin_logic_bus_STB=self.ibus.stb,
            o_FetchL1WishbonePlugin_logic_bus_WE=self.ibus.we,
            o_FetchL1WishbonePlugin_logic_bus_ADR=self.ibus.adr,
            o_FetchL1WishbonePlugin_logic_bus_SEL=self.ibus.sel,
            o_FetchL1WishbonePlugin_logic_bus_DAT_MOSI=self.ibus.dat_w,
            o_FetchL1WishbonePlugin_logic_bus_CTI=self.ibus.cti,
            o_FetchL1WishbonePlugin_logic_bus_BTE=self.ibus.bte,
            i_FetchL1WishbonePlugin_logic_bus_DAT_MISO=self.ibus.dat_r,
            i_FetchL1WishbonePlugin_logic_bus_ACK=self.ibus.ack,
            i_FetchL1WishbonePlugin_logic_bus_ERR=self.ibus.err,

            # Data bus.
            o_LsuL1WishbonePlugin_logic_bus_CYC=self.dbus.cyc,
            o_LsuL1WishbonePlugin_logic_bus_STB=self.dbus.stb,
            o_LsuL1WishbonePlugin_logic_bus_WE=self.dbus.we,
            o_LsuL1WishbonePlugin_logic_bus_ADR=self.dbus.adr,
            o_LsuL1WishbonePlugin_logic_bus_SEL=self.dbus.sel,
            o_LsuL1WishbonePlugin_logic_bus_DAT_MOSI=self.dbus.dat_w,
            o_LsuL1WishbonePlugin_logic_bus_CTI=self.dbus.cti,
            o_LsuL1WishbonePlugin_logic_bus_BTE=self.dbus.bte,
            i_LsuL1WishbonePlugin_logic_bus_DAT_MISO=self.dbus.dat_r,
            i_LsuL1WishbonePlugin_logic_bus_ACK=self.dbus.ack,
            i_LsuL1WishbonePlugin_logic_bus_ERR=self.dbus.err,

            # Uncached I/O bus. Same geometry as dbus -- 30-bit word address,
            # 4-bit select -- so it decodes identically; only the routing
            # differs.
            o_LsuCachelessWishbonePlugin_logic_bridge_down_CYC=self.iobus.cyc,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_STB=self.iobus.stb,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_WE=self.iobus.we,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_ADR=self.iobus.adr,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_SEL=self.iobus.sel,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_DAT_MOSI=self.iobus.dat_w,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_CTI=self.iobus.cti,
            o_LsuCachelessWishbonePlugin_logic_bridge_down_BTE=self.iobus.bte,
            i_LsuCachelessWishbonePlugin_logic_bridge_down_DAT_MISO=self.iobus.dat_r,
            i_LsuCachelessWishbonePlugin_logic_bridge_down_ACK=self.iobus.ack,
            i_LsuCachelessWishbonePlugin_logic_bridge_down_ERR=self.iobus.err,
        )
        return m
