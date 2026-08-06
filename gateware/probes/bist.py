#!/usr/bin/env python3
#
# Common control and comparison block for standalone built-in self-tests.
# SPDX-License-Identifier: BSD-3-Clause

"""The shared measurement core for CPU-free JTAG self-tests.

The test supplies activity and values; this block supplies the host contract:

  * a write-strobed ``go`` command and busy/done status
  * comparison against a golden value computed inside the design
  * a sticky error plus check and mismatch counters
  * a negative control that complements the golden value

Register numbers remain properties of each bitstream. Existing host tools are
part of those experiments, and renumbering them would make a harness extraction
look like a new protocol. ``addresses`` says where this common contract lands.
"""

from dataclasses import dataclass

from amaranth import Cat, Const, Elaboratable, Module, Mux, Signal
from luna.gateware.interface.jtag import JTAGRegisterInterface


@dataclass(frozen=True)
class BISTAddresses:
    """JTAG addresses occupied by the common BIST contract."""

    ident: int
    control: int
    status: int
    checks: int
    errors: int
    actual: int
    golden: int


class BISTHarness(Elaboratable):
    """JTAG control and a sticky, negative-controlled comparator.

    ``check`` is a one-cycle qualifier for ``actual`` and ``golden``. The
    application computes both values because supplying a host golden would test
    the transport as much as the hardware. ``done`` may be a pulse; it is
    latched until the next ``go`` so a short completion is visible over JTAG.
    """

    CONTROL_GO = 1 << 0
    CONTROL_NEGATIVE = 1 << 1

    STATUS_BUSY = 1 << 0
    STATUS_DONE = 1 << 1
    STATUS_ERROR = 1 << 2
    STATUS_NEGATIVE = 1 << 3

    def __init__(self, *, applet_id, addresses, width=32,
                 negative_control=False, simulate=False, transport=None):
        """`transport` is what carries the register window.

        None and `simulate=False` gives `JTAGRegisterInterface`, which is what
        every applet built so far uses. Passing one instead -- a
        `BistCsrTransport`, say -- puts the same window on the CPU's CSR bus
        without the engine knowing: it only ever calls `add_register` and
        `add_read_only_register` on this harness.

        That matters because JTAG is where three of this project's measurement
        failures came from (#204), the sharpest being a readback that slips a
        bit below a sync/TCK ratio of about four.
        """
        if not 1 <= width <= 32:
            raise ValueError("BIST comparator width must be in 1..32")
        if transport is not None and simulate:
            raise ValueError(
                "simulate=True means no transport at all; passing one as well "
                "is a contradiction rather than a preference")

        self.applet_id = applet_id
        self.addresses = addresses
        self.width = width
        self.negative_control_init = negative_control
        self.simulate = simulate
        self._transport = transport

        # Application side.
        self.busy = Signal()
        self.done = Signal()
        self.check = Signal()
        self.actual = Signal(width)
        self.golden = Signal(width)

        # Host command. Simulation drives sim_go/sim_negative because JTAGG is
        # an ECP5 hard block and is unrelated to comparator correctness.
        self.go = Signal()
        self.sim_go = Signal()
        self.sim_negative = Signal(init=negative_control)

        # The same state read by JTAG is exposed to application simulations.
        self.done_sticky = Signal()
        self.error = Signal()
        self.checks = Signal(32)
        self.errors = Signal(32)

        # Pulse to zero the counters from inside the design. See the note by
        # `command_go`: without this a gateware sweep cannot start a fresh
        # measurement per cell, because the only clear was a host register write.
        self.clear = Signal()
        self.negative = Signal(init=negative_control)
        self.status_extra = Signal(28)
        self.last_actual = Signal(width)
        self.last_golden = Signal(width)

        # Whether this harness OWNS the transport, and therefore elaborates it.
        # True for the JTAG one it builds itself; false for one handed in, which
        # the caller elaborates in the caller's own domain. See `elaborate`.
        self._owns_registers = transport is None
        self.registers = (None if simulate else
                          transport if transport is not None else
                          JTAGRegisterInterface(default_read_value=0xDEADBEEF))

    def add_read_only_register(self, address, *, read):
        """Add one application result to this harness's JTAG window."""
        if self.registers is None:
            raise RuntimeError("application registers are added during elaborate()")
        self.registers.add_read_only_register(address, read=read)

    def add_register(self, address, *, value_signal):
        """Add one application parameter to this harness's JTAG window."""
        if self.registers is None:
            raise RuntimeError("application registers are added during elaborate()")
        self.registers.add_register(address, value_signal=value_signal)

    def elaborate(self, platform):
        m = Module()
        addresses = self.addresses

        # A GATEWARE-DRIVEN clear, for a sweep that walks combinations itself.
        # The counters previously cleared only on `command_go`, which is a rising
        # edge of the HOST's control register -- unreachable from inside the
        # design. A sweep therefore accumulated errors across every cell and
        # saturated during the first one.
        command_go = Signal()
        negative = self.negative

        if self.simulate:
            m.d.comb += [command_go.eq(self.sim_go),
                         negative.eq(self.sim_negative)]
        else:
            registers = self.registers
            # Added here ONLY when this harness created it. An externally
            # supplied transport belongs to whoever built it, and that matters
            # for more than tidiness: the SoC rig wraps this engine in a
            # DomainRenamer to move it to `hr`, and anything elaborated inside
            # that is renamed with it. A CSR bridge dragged into `hr` while the
            # CPU stays in `sync` never completes its handshake, so the FIRST
            # register read stalls the bus and the shell hangs with nothing
            # printed -- which reads as a lock-up rather than a clocking fault.
            if self._owns_registers:
                m.submodules.registers = registers

            control = Signal(32, init=(self.CONTROL_NEGATIVE
                                       if self.negative_control_init else 0))
            registers.add_register(addresses.control, value_signal=control,
                                   name="bist_control")
            registers.add_read_only_register(addresses.ident, read=self.applet_id)
            registers.add_read_only_register(addresses.checks, read=self.checks)
            registers.add_read_only_register(addresses.errors, read=self.errors)
            registers.add_read_only_register(addresses.actual, read=self.last_actual)
            registers.add_read_only_register(addresses.golden, read=self.last_golden)
            registers.add_read_only_register(
                addresses.status,
                read=Cat(self.busy, self.done_sticky, self.error, negative,
                         self.status_extra))

            previous_go = Signal()
            m.d.sync += previous_go.eq(control[0])
            m.d.comb += [command_go.eq(control[0] & ~previous_go),
                         negative.eq(control[1])]

        m.d.comb += self.go.eq(command_go)

        # A new measurement clears the accumulated verdict. Within a run the
        # error is sticky: a mismatch lasting one cycle survives a slow poll.
        with m.If(command_go | self.clear):
            m.d.sync += [
                self.done_sticky.eq(0),
                self.error.eq(0),
                self.checks.eq(0),
                self.errors.eq(0),
            ]
        with m.Else():
            with m.If(self.done):
                m.d.sync += self.done_sticky.eq(1)
            with m.If(self.check):
                wanted = Mux(negative, ~self.golden, self.golden)
                m.d.sync += [self.checks.eq(self.checks + 1),
                             self.last_actual.eq(self.actual),
                             self.last_golden.eq(self.golden)]
                with m.If(self.actual != wanted):
                    m.d.sync += [self.error.eq(1),
                                 self.errors.eq(self.errors + 1)]

        return m
