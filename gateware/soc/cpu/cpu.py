#!/usr/bin/env python3
#
# An RV32IMAC VexiiRiscv core with caches, on Wishbone.
# SPDX-License-Identifier: BSD-3-Clause

"""
A cached RV32IMAC VexiiRiscv core presenting three Wishbone masters.

    from cpu.cpu import VexiiRiscv
    cpu = VexiiRiscv(reset_addr=0x0)   # ours is 0x0, not a flash base

## Ports

    ibus          Out  instruction fetch, through the L1 I-cache
    dbus          Out  data, through the L1 D-cache -- `main=1` PMA regions only
    iobus         Out  uncached data -- every `main=0` (I/O) PMA region
    irq_external  In   one wire; drive it from `cpu/plic.py`
    irq_timer     In   drive it from `cpu/clint.py`
    irq_software  In   likewise
    mtime         Out  the counter behind `rdtime`, for that CLINT to compare
    ext_reset     In   holds the core in reset; `bus/jtag_stage.py` drives it
    jtag_*        I/O  the ER2 tap, from `jtag_stage.UserJTAG`

All three buses are 30-bit addressed, 32-bit data, granularity 8, with
`err`/`cti`/`bte` -- the signature every peripheral and bridge in this tree
expects.

## Constraints, each of which fails silently if ignored

  * **Caches are mandatory.** The cacheless Wishbone bridge asserts
    `!up.p.withAmo` (`LsuCachelessBridge.scala:203`), so it cannot carry
    atomics, and the firmware target is `riscv32imac`. The L1 bridge has no
    such assertion.
  * **`--lsu-l1-wishbone` is a separate flag from `--lsu-wishbone`**, and both
    are needed with caches on. See `GENERATE_FLAGS`.
  * **Every memory region must be declared.** VexiiRiscv's `defaultPma` covers
    only 0x80000000 and 0x10000000; anything else traps on every access,
    including stack pushes, and presents as a dead CPU. See `generate`.
  * **`iobus` must be connected.** Leaving it dangling synthesises, meets
    timing and enumerates -- and every peripheral store waits forever for an
    ACK with no driver.

## Interrupts

Standard RISC-V: one machine external wire, not VexRiscv's 32-bit
`irq_external` array with mask/pending in CPU CSRs. Concentrating sources and
reporting which fired is therefore a peripheral's job -- `cpu/plic.py`, a
standard PLIC.

`irq_timer` and `irq_software` are the CLINT's, and `cpu/clint.py` is one.
Tie them off explicitly in a design that has none, so "no source" and "nobody
wired it" do not look identical.

Interrupts are the PLIC (`cpu/plic.py`) and the CLINT (`cpu/clint.py`).

The choices behind all of the above -- VexRiscv vs VexiiRiscv, cached vs
cacheless, PLIC vs a smaller concentrator -- are in `../../docs/architecture.md`.
"""

import contextlib
import fcntl
import os
import subprocess
from pathlib import Path

from amaranth               import Elaboratable, Module, Signal, Instance, Cat
from amaranth               import ClockSignal, ResetSignal
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In, Out
from amaranth.hdl           import unsigned

from amaranth_soc           import wishbone

# Four levels: cpu.py -> cpu/ -> soc/ -> gateware/ -> the workspace root. Three
# stopped at `gateware/`, where there is no `repos/`, so a regeneration died with
# FileNotFoundError on `gateware/repos/vexiiriscv`. It stayed hidden because a
# cached netlist means this path is never touched -- only a cache miss reaches
# it, and those are rare enough to be years apart.
ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _superproject(root):
    """The main checkout behind a git worktree, or None.

    A linked worktree's `.git` is a file naming `<super>/.git/worktrees/<name>`.
    """
    marker = root / ".git"
    if not marker.is_file():
        return None
    gitdir = Path(marker.read_text().partition("gitdir:")[2].strip())
    return gitdir.parents[2] if gitdir.parent.name == "worktrees" else None


def _checkout():
    """The VexiiRiscv sources to generate from.

    A worktree has NO `repos/vexiiriscv` of its own -- `git worktree add` does not
    populate submodules -- so it falls back to the main checkout's, which is then
    shared and is what `_generator_lock` must therefore be keyed on. `VEXII_ROOT`
    overrides both.
    """
    candidates = [Path(os.environ["VEXII_ROOT"])] if os.environ.get("VEXII_ROOT") else []
    candidates.append(ROOT / "repos" / "vexiiriscv")
    superproject = _superproject(ROOT)
    if superproject is not None:
        candidates.append(superproject / "repos" / "vexiiriscv")
    for candidate in candidates:
        if (candidate / "build.sbt").is_file():
            return candidate
    # Not raised at import: a caller that never regenerates (soc_generate_pac.py
    # patches generate out) must still be able to import this module.
    return candidates[-1]


VEXII = _checkout()

# The generator flags that produce a Wishbone core with caches and atomics.
#
# --lsu-l1-wishbone is separate from --lsu-wishbone and is the one that matters
# here: the flags dispatch per path (Param.scala:932 cacheless, 972 cached), so
# passing only the cacheless flags with caches enabled silently produces a
# native core with no warning at all.
GENERATE_FLAGS = [
    "--xlen", "32",
    "--with-rvm", "--with-rvc", "--with-rva", "--with-rdtime",
    # 128 sets x 1 way x 64 B = 8 KiB each. `generate()` substitutes sets from
    # its `cache_sets` argument -- these two literals are the default, not the
    # last word (`top.py`'s CACHE_SETS is); kept equal so no reader has to work
    # out which number won.
    #
    # ONE WAY IS STILL AN OPEN CHOICE, not forced. An earlier version of this
    # comment claimed `flash_cache_flush()` (`scripts/riscv_firmware.py`) needed
    # a direct-mapped cache, since it evicts by reading the cache's size at line
    # stride. Wrong twice over:
    #   * replacement policy is PLRU, not random (`FetchL1Plugin.scala:187`,
    #     `Plru.scala`); a sweep of the FULL cache size touches every set
    #     `wayCount` times with distinct tags, evicting every way under PLRU.
    #     The flush already reads the full size, so it survives ways > 1.
    #   * that flush is only in the GENERATED C test firmware -- the Rust
    #     firmware uses a real `fence.i` (`firmware/cynthion-soc/src/main.rs`),
    #     so the product never depended on the sweep at all.
    #
    # Real trade: conflict misses against Fmax and block RAM. RTIC doesn't
    # context-switch (preempts on one stack), but each handler is still a
    # separate instruction working set -- in a direct-mapped cache, two hot
    # handlers colliding on an index evict each other regardless of cache size.
    # Associativity fixes that; capacity doesn't. Against it: `bankCount =
    # wayCount` (`FetchL1Plugin.scala:128`), so ways cost block RAM fast, and a
    # way-select mux lands in the hit path at an Fmax the BTB already had to be
    # relaxed to meet.
    #
    # `scripts/soc_cache_sweep.py` measures the trade rather than arguing it.
    # 3 ways does not exist: PLRU asserts `isPow2` on the way count.
    "--with-fetch-l1", "--fetch-l1-sets", "128", "--fetch-l1-ways", "1",
    "--with-lsu-l1", "--lsu-l1-sets", "128", "--lsu-l1-ways", "1",
    # All three are needed. A cached core still has an uncached LSU path --
    # that is how it reaches I/O regions, and peripherals live in one -- so it
    # appears as its own top-level bus. Omitting --lsu-wishbone leaves it
    # native and unconnected, and the only symptom is undriven
    # LsuPlugin_logic_bus_* wires.
    "--fetch-wishbone", "--lsu-wishbone", "--lsu-l1-wishbone",

    # HARDWARE PERFORMANCE COUNTERS -- replace a night of inference.
    #
    # Four `mhpmcounter`s plus `mcycle`/`minstret`, selected by writing an event
    # id to the matching `mhpmevent`. VexiiRiscv's list (`misc/Service.scala`),
    # the two that matter here:
    #
    #     0x04  STALLED_CYCLES_FRONTEND   waiting on INSTRUCTION fetch
    #     0x05  STALLED_CYCLES_BACKEND    waiting on DATA
    #     0x18  DCACHE_LOAD_ACCESS        loads that reached the D-cache
    #
    # `bench` measured HyperRAM at 13.3 MB/s when the bus was doing 63.1: the
    # walk's own loop is fetched from flash and the CPU spent 79% of every cache
    # line stalled on instruction fetch, not on the memory under test.
    # `STALLED_CYCLES_FRONTEND` states that in one number instead of four
    # gateware probes and four wrong theories.
    #
    # Four is `ParamSimple`'s own default for configurations that enable them.
    # Plugin keeps 8-bit registers per counter, flushes into the 64-bit CSR RAM
    # on MSB set -- cheap on an FPGA. `zicntr`/`zihpm` added to the reported
    # ISA, visible in `info`.
    "--performance-counters", "4",

    # RISC-V debug module, through the ECP5's EXISTING JTAG chain.
    #
    # --debug-jtag-instruction, not --debug-jtag-tap: the tap variant builds its
    # own TAP needing four dedicated pins, and JTAG belongs to Apollo here (FPGA
    # configuration). The instruction variant hangs the debug module off a user
    # instruction (ER1/ER2) already in the chain -- same mechanism LUNA's
    # JTAGRegisterInterface uses -- so openocd and Apollo share one TAP.
    #
    # Buys halt, step, register and memory inspection. Without it a CPU that
    # stopped printing is indistinguishable from a CPU that stopped running --
    # the ambiguity that has cost the most time on this project.
    "--debug-jtag-instruction",

    # Branch prediction: a branch target buffer.
    #
    # Without one, BranchPlugin/LearnPlugin still resolve and record branches
    # but nothing acts on the record, so every taken branch redirects the
    # three-stage fetch and the pipeline refills. Measured at 7 instructions in
    # 28.77 cycles with every one hitting the I-cache (#140): 4 cycles/
    # instruction with no memory in the way at all.
    #
    # --with-btb is BtbPlugin at Param.scala's defaults: 512 sets, one chunk
    # (single issue), 16-bit hash, dual-port RAM.
    #
    # --relaxed-btb is NOT optional, not a precaution: at default jumpAt = 1 the
    # BTB's block RAM read, 16-bit hash compare, hit decision and fetch redirect
    # are all one cycle, and nextpnr closes at 57.55 MHz -- a hard FAIL against
    # the 60 MHz constraint, critical path starting at
    # `BtbPlugin_logic_mem.0.0.DOA8`, 4.10 ns clk-to-q before a single LUT.
    # Relaxed moves the redirect to jumpAt = 2, giving the compare its own
    # cycle.
    #
    # Cost: one cycle on a correctly predicted branch instead of zero -- far
    # cheaper than the full refetch it replaces (bench numbers above).
    #
    # rasDepth follows --with-ras and is 0 without it, so these flags alone are
    # the BTB and nothing else.
    "--with-btb", "--relaxed-btb", "--relaxed-branch",
]


# The SoC's address map, as PMA regions. Anything not listed here is
# unreachable rather than merely uncached.
DEFAULT_REGIONS = [
    "base=00000000,size=00010000,main=1,exe=1",   # block RAM
    "base=f0000000,size=10000000,main=0,exe=0",   # CSR peripherals
]


@contextlib.contextmanager
def _generator_lock():
    """Serialise the Scala generator against the checkout it runs in.

    Beside `tmp/`, not inside the submodule: an untracked lock file in
    `repos/vexiiriscv` shows up as a dirty submodule forever after.

    Keyed on the CHECKOUT, not on `ROOT`: worktrees have no submodules of their
    own and share the main checkout's, so a lock under each worktree's own `tmp/`
    excludes nothing between them -- which is the race in #306.
    """
    lock = VEXII.parent.parent / "tmp" / "vexii-generate.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def generate(reset_addr, cache_sets=128, output=None, regions=None,
             cache_ways=1):
    """Run the Scala generator and return the path to the Verilog.

    Regenerating is a few seconds, so this is called at elaboration rather than
    checked in. The alternative -- a committed .v -- drifts silently from the
    flags that produced it.

    `cache_ways` MUST be a power of two. SpinalHDL's PLRU asserts `isPow2` on the
    way count (`lib/misc/Plru.scala:15`), so 3 ways is not a configuration this
    core has -- it is 1, 2 or 4. The failure is a Scala assertion during
    generation rather than anything a build would quietly accept.

    Ways cost block RAM faster than sets do: `bankCount = wayCount`
    (`FetchL1Plugin.scala:128`), so each way is its own bank of DP16KDs on top of
    its own tag and PLRU memory.
    """
    if cache_ways & (cache_ways - 1):
        raise ValueError(
            f"cache_ways must be a power of two, not {cache_ways}: SpinalHDL's "
            f"Plru asserts isPow2 on it (lib/misc/Plru.scala:15). Use 1, 2 or 4.")
    regions = regions if regions is not None else DEFAULT_REGIONS
    flags = list(GENERATE_FLAGS)
    for index, flag in enumerate(flags):
        if flag in ("--fetch-l1-sets", "--lsu-l1-sets"):
            flags[index + 1] = str(cache_sets)
        if flag in ("--fetch-l1-ways", "--lsu-l1-ways"):
            flags[index + 1] = str(cache_ways)
    flags += ["--reset-vector", hex(reset_addr)]

    # Declare every region the SoC actually has. VexiiRiscv's defaultPma
    # (Param.scala:58-77) covers only 0x80000000 and 0x10000000, so a design
    # with memory at 0x00000000 has it in no region at all and every access --
    # including every stack operation -- traps. The failure looks exactly like
    # a dead CPU.
    #
    # main=1 means normal cacheable memory; main=0 marks I/O, which is what
    # keeps peripheral accesses out of the data cache and routes it to `iobus`.
    for region in regions:
        flags += ["--region", region]

    # ONE GENERATOR AT A TIME, and the copy is inside the lock.
    #
    # SpinalConfig's target directory is the process cwd, and sbt's cwd must be
    # the project root -- so every build on this machine writes the same
    # `repos/vexiiriscv/VexiiRiscv.v` and compiles into the same `target/`.
    # Concurrent builds gave two different yosys errors from a half-written 1.2
    # MB file, and could as easily have read a stale but valid one and produced a
    # bitstream from another configuration's core (#306).
    #
    # Holding the lock across generate-plus-copy means a caller passing `output`
    # gets a complete file that nothing else can then overwrite. A caller passing
    # none still gets the shared path and is still racy: `output` is how a
    # concurrent build stays correct, and `top.py` always passes one.
    #
    # Blocking, with no timeout: the wait is another build's generator, ~40 s,
    # and every waiter is doing the same work. A timeout here would abort a build
    # that was about to succeed.
    if not (VEXII / "build.sbt").is_file():
        raise FileNotFoundError(
            f"no VexiiRiscv checkout at {VEXII}. It is a submodule: "
            f"`git submodule update --init repos/vexiiriscv` in the main checkout, "
            f"or set VEXII_ROOT. A worktree has none of its own.")

    with _generator_lock():
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
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(emitted.read_bytes())
            return output
    return emitted


class VexiiRiscv(wiring.Component):
    """A VexiiRiscv core as an Amaranth component.

    The bus signature -- 30-bit address, 32-bit data, granularity 8, with
    `err`/`cti`/`bte` -- is what every arbiter, decoder and CSR bridge in this
    tree connects to.
    """

    name       = "vexiiriscv"
    arch       = "riscv"
    byteorder  = "little"
    data_width = 32

    def __init__(self, *, reset_addr=0x00000000, cache_sets=128,
                 regions=None, cache_ways=1, netlist=None):
        self._reset_addr = reset_addr
        self._cache_sets = cache_sets
        self._cache_ways = cache_ways
        self._regions = regions
        # Where the generated Verilog is kept for this build. None leaves it at
        # the generator's shared path in the submodule, which is correct for a
        # one-off (`print(generate(0))`) and wrong for anything that may run
        # beside another build -- see `generate` and #306.
        self._netlist = netlist

        super().__init__({
            "ext_reset":    In(unsigned(1)),

            # The ER2 tap, from `jtag_stage.UserJTAG`.
            #
            # Taken as ports rather than instantiated here because `JTAGG` is a
            # singleton -- one per die -- and `jtag_stage.JTAGStager` needs ER1 off
            # the same primitive. Left undriven the debug module sees `jtag_rstn` low
            # and stays in reset, which is the right answer for a SoC that has not
            # wired it.
            "jtag_tck":     In(unsigned(1)),
            "jtag_tdi":     In(unsigned(1)),
            "jtag_ce":      In(unsigned(1)),
            "jtag_shift":   In(unsigned(1)),
            "jtag_update":  In(unsigned(1)),
            "jtag_rstn":    In(unsigned(1)),
            "jtag_tdo":     Out(unsigned(1)),

            # One wire, not 32. See the module docstring.
            "irq_external": In(unsigned(1)),
            "irq_timer":    In(unsigned(1)),
            "irq_software": In(unsigned(1)),

            # The counter `rdtime` reads, brought out.
            #
            # A CLINT's `mtime` and the `time` CSR are required to be the same
            # counter, and the cheapest way to guarantee that is to have one.
            # Exposing it rather than moving it keeps a CPU with no CLINT
            # working: `rdtime` is still driven here, so an instantiator that
            # ignores this port loses nothing.
            "mtime":        Out(unsigned(64)),

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
                           regions=self._regions,
                           cache_ways=self._cache_ways,
                           output=self._netlist)
        if platform is not None:
            platform.add_file("VexiiRiscv.v", verilog.read_text())

        # rdtime is a mandatory input with no VexRiscv equivalent. Leaving it
        # unconnected does not fail synthesis -- it silently breaks every
        # rdtime read, which reads as a firmware bug rather than a wiring one.
        rdtime = Signal(64)
        m.d.sync += rdtime.eq(rdtime + 1)
        m.d.comb += self.mtime.eq(rdtime)

        # The RISC-V debug module, on the ER2 tap.
        #
        # ER2 = 0x38 rather than ER1 = 0x32 because ER1 carries the HyperRAM staging
        # sink (`bus/jtag_stage.py`), and two things answering one instruction would
        # corrupt both.
        #
        # `jtag_rstn` is active low, and `capture` is the non-shift half of the enable
        # window -- JTAGG gives an enable and a shift phase rather than a discrete
        # capture strobe.
        m.submodules.cpu = Instance(
            "VexiiRiscv",
            i_clk=ClockSignal("sync"),
            i_reset=ResetSignal("sync") | self.ext_reset,

            i_EmbeddedRiscvJtag_logic_jtagInstruction_tck=self.jtag_tck,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_tdi=self.jtag_tdi,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_enable=self.jtag_ce,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_capture=self.jtag_ce & ~self.jtag_shift,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_shift=self.jtag_shift,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_update=self.jtag_update,
            i_EmbeddedRiscvJtag_logic_jtagInstruction_reset=~self.jtag_rstn,
            o_EmbeddedRiscvJtag_logic_jtagInstruction_tdo=self.jtag_tdo,
            i_EmbeddedRiscvJtag_logic_debug_reset=ResetSignal("sync"),

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
