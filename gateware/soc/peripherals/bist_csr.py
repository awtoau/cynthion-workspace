#!/usr/bin/env python3
#
# The BIST register window on the CPU's CSR bus instead of JTAG. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""
`BISTHarness`'s two register methods, backed by `amaranth_soc` CSR.

## Why not JTAG

`JTAGRegisterInterface` has produced three separate failures in the measurement
path (#204), the sharpest being that its readback slips a bit below a `sync`/TCK
ratio of about four -- so a value read back over it is not necessarily the value
the gateware holds. Every HyperRAM number this project has recorded came through
it.

`BISTHarness` never touches the transport directly: it holds one, and
`HyperRAMCeiling` calls `add_register` / `add_read_only_register` on the harness.
So the transport is one attribute, and this is the other implementation of it.
The engine, the comparator, the negative control and the sweep FSM are unchanged.

## The domain crossing, and why it is this small

The engine runs in `hr`, off the second PLL, so that device CK is not the CPU
clock (`hyperram_clocks.py`). The CPU runs in `sync`. Nothing streams between
them:

  * **parameters** (`sync` -> `hr`) are written before a pass and held still for
    its duration. They cross as plain signals, and the `go` pulse that follows
    them is what makes that safe -- a parameter is only read after `go` has been
    synchronised, by which point it has been stable for at least two `hr` edges.
  * **`go`** (`sync` -> `hr`) crosses as a pulse, through a toggle and an edge
    detector rather than a level, so a `sync` cycle shorter than an `hr` one
    cannot lose it.
  * **counters** (`hr` -> `sync`) are read after `done`, and `done` crosses the
    same way `go` does. A counter read while the engine is running is explicitly
    not meaningful and the status bit says so.

There is deliberately no FIFO. `stream_buffer.py` records a `SyncFIFOBuffered`
between two domains that "worked perfectly while both were 60, then produced a
stream with correct counter values and dropped characters" once they differed;
that is what a continuous crossing costs, and there is no continuous crossing
here. `scripts/soc_bist_cdc_sim.py` measures both, and shows a level crossing
losing 3 of 8 pulses at CK 100 and duplicating at CK 180.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, connect, flipped
from amaranth_soc import csr

__all__ = ["BistCsrTransport", "PulseCross"]


class PulseCross(wiring.Component):
    """One pulse from `i_domain` to `o_domain`, through a toggle.

    A level-crossing `go` is lost whenever the source cycle is shorter than the
    destination one, and duplicated when it is longer. Toggle-and-edge-detect
    does not care about the ratio, which matters because `hr` is CK/2 and moves
    across the ladder while `sync` stays pinned.
    """

    def __init__(self, *, i_domain, o_domain):
        self._i_domain = i_domain
        self._o_domain = o_domain
        super().__init__({"i": In(1), "o": wiring.Out(1)})

    def elaborate(self, platform):
        m = Module()
        toggle = Signal()
        with m.If(self.i):
            m.d[self._i_domain] += toggle.eq(~toggle)

        synced = Signal()
        m.submodules.sync = FFSynchronizer(toggle, synced, o_domain=self._o_domain)
        last = Signal()
        m.d[self._o_domain] += last.eq(synced)
        m.d.comb += self.o.eq(synced ^ last)
        return m


class BistCsrTransport(wiring.Component):
    """`add_register` / `add_read_only_register`, over CSR rather than JTAG.

    ## Why the whole window is built up front, and why there are two

    The obvious shape -- collect the registers the engine declares, then assemble
    a bank from them -- cannot work. The engine declares its registers during
    ITS elaborate, but `amaranth_soc` needs the memory map before the bridge is
    constructed, and the bridge is constructed when this is. That ordering is a
    cycle, not something rearranging calls can fix; it surfaces as
    "registers must be added before finalize()" raised four frames inside the
    SoC's own elaborate.

    So the map does not depend on which registers exist. Every address gets a
    register whether the engine uses it or not, and the two methods bind a signal
    to one that is already there. An unused address reads zero.

    That leaves the field TYPE, which also has to be fixed before the engine
    speaks: `csr.action.RW` exposes `.data`, what the CPU wrote, and has no
    `.r_data` for the gateware to drive; `csr.action.R` is the other way round.
    There is no field that is both. Declaring the split up front means restating
    the engine's register map here, where it would rot out of step -- and the way
    it would rot is a result silently reading back the CPU's last write, which
    looks exactly like a working measurement.

    So the window is built TWICE, at two offsets:

        base + 0x000 + 4*N    parameter N, RW, CPU writes and engine reads
        base + 0x100 + 4*N    result N, R, engine drives and CPU reads

    Address N therefore exists in both forms, and which one is real is settled by
    whichever method the engine calls. Nothing needs to know in advance, and the
    firmware side is unambiguous: parameters are written low, results are read
    high.

    The cost is 128 registers rather than the ~25 the engine uses. They are LUT
    RAM, not block RAM. What it buys is a construction order that works and a map
    that cannot drift.
    """

    #: Byte offset of the result window from the peripheral's base. Parameters
    #: sit below it. The firmware's `bist.rs` carries the same constant.
    RESULT_WINDOW = 0x100

    def __init__(self, *, addr_width=8, engine_domain="hr"):
        # `addr_width` is the ENGINE's, i.e. how many registers it can name. The
        # bus is one bit wider because each one appears in both windows.
        self._addr_width = addr_width
        self._engine_domain = engine_domain
        self._count = (1 << addr_width) // 4
        self._bus_addr_width = addr_width + 1
        self._params = {}
        self._results = {}

        builder = csr.Builder(addr_width=self._bus_addr_width, data_width=8)
        self._param_regs = {}
        self._result_regs = {}
        for address in range(self._count):
            w = csr.Register({"w": csr.Field(csr.action.RW, 32)}, access="rw")
            builder.add(f"p{address:02x}", w, offset=4 * address)
            self._param_regs[address] = w

            r = csr.Register({"w": csr.Field(csr.action.R, 32)}, access="r")
            builder.add(f"q{address:02x}", r,
                        offset=self.RESULT_WINDOW + 4 * address)
            self._result_regs[address] = r
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({"bus": In(csr.Signature(addr_width=self._bus_addr_width,
                                                  data_width=8))})
        self.bus.memory_map = self._bridge.bus.memory_map

    # -- the two methods BISTHarness calls -----------------------------------

    # `name` is accepted and ignored: `JTAGRegisterInterface` takes it for its
    # own debug output, and the engine passes it through. Rejecting it here
    # would make the two transports non-interchangeable for no benefit, which
    # is the one thing this class exists not to be.
    def add_register(self, address, *, value_signal, name=None):
        """A parameter the CPU writes and the engine reads."""
        self._claim(address)
        self._params[address] = value_signal

    def add_read_only_register(self, address, *, read, name=None):
        """A result the engine produces and the CPU reads."""
        self._claim(address)
        self._results[address] = read

    def _claim(self, address):
        if address >= self._count:
            raise ValueError(
                f"register {address} is outside this window: addr_width="
                f"{self._addr_width} holds {self._count} registers")
        if address in self._params or address in self._results:
            raise ValueError(f"register {address} is already defined")

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        for address, sig in self._params.items():
            # sync -> hr. Held still across `go`, so a plain assignment is the
            # crossing; see the module docstring.
            m.d.comb += sig.eq(self._param_regs[address].f.w.data)
        for address, sig in self._results.items():
            # hr -> sync. Read after `done`, likewise.
            m.d.comb += self._result_regs[address].f.w.r_data.eq(sig)

        return m
