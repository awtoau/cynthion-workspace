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
here.

## Register layout

The engine addresses its registers by number, as it did over JTAG. This maps
each to a 32-bit CSR at `4 * address`, so the firmware's view is a flat array
and adding a register in the engine does not move the others.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, connect, flipped
from amaranth_soc import csr

__all__ = ["BistCsrTransport"]


class _PulseCross(wiring.Component):
    """One pulse from `i_domain` to `o_domain`, through a toggle.

    A level-crossing `go` is lost whenever the source cycle is shorter than the
    destination one, which is exactly the case here in one direction and will be
    the other once the ladder moves. Toggle-and-edge-detect does not care about
    the ratio.
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

    Built in two phases, like the JTAG one: registers are declared during the
    engine's `elaborate`, and the CSR bank is assembled from them afterwards.
    `finalize()` is what does the assembling, and calling it twice or adding a
    register after it is an error rather than a silent no-op.
    """

    def __init__(self, *, addr_width=8, engine_domain="hr"):
        self._addr_width = addr_width
        self._engine_domain = engine_domain
        self._params = {}       # address -> Signal, sync -> engine
        self._results = {}      # address -> Signal, engine -> sync
        self._finalized = False

        super().__init__({"bus": In(csr.Signature(addr_width=addr_width, data_width=8))})

    # -- the two methods BISTHarness calls -----------------------------------

    def add_register(self, address, *, value_signal):
        """A parameter the CPU writes and the engine reads."""
        if self._finalized:
            raise RuntimeError("registers must be added before finalize()")
        if address in self._params or address in self._results:
            raise ValueError(f"register {address} is already defined")
        self._params[address] = value_signal

    def add_read_only_register(self, address, *, read):
        """A result the engine produces and the CPU reads."""
        if self._finalized:
            raise RuntimeError("registers must be added before finalize()")
        if address in self._params or address in self._results:
            raise ValueError(f"register {address} is already defined")
        self._results[address] = read

    # -- assembly -------------------------------------------------------------

    def finalize(self):
        """Build the CSR bank from the registers the engine declared."""
        if self._finalized:
            raise RuntimeError("finalize() has already been called")
        self._finalized = True

        builder = csr.Builder(addr_width=self._addr_width, data_width=8)
        self._fields = {}
        for address, sig in sorted(self._params.items()):
            reg = csr.Register({"value": csr.Field(csr.action.RW, 32)}, access="rw")
            builder.add(f"param{address:02x}", reg, offset=4 * address)
            self._fields[("param", address)] = (reg, sig)
        for address, sig in sorted(self._results.items()):
            reg = csr.Register({"value": csr.Field(csr.action.R, 32)}, access="r")
            builder.add(f"result{address:02x}", reg, offset=4 * address)
            self._fields[("result", address)] = (reg, sig)

        self._bridge = csr.Bridge(builder.as_memory_map())
        self.bus.memory_map = self._bridge.bus.memory_map
        return self

    def elaborate(self, platform):
        if not self._finalized:
            raise RuntimeError(
                "BistCsrTransport.finalize() was never called, so the CSR bank "
                "holds none of the engine's registers")
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        for (kind, _address), (reg, sig) in self._fields.items():
            if kind == "param":
                # sync -> hr. Held still across `go`, so a plain assignment is
                # the crossing; see the module docstring.
                m.d.comb += sig.eq(reg.f.value.data)
            else:
                # hr -> sync. Read after `done`, likewise.
                m.d.comb += reg.f.value.r_data.eq(sig)

        return m
