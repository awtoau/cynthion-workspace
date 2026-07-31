#!/usr/bin/env python3
#
# Memory-mapped configuration SPI flash for the VexiiRiscv SoC.
# SPDX-License-Identifier: BSD-3-Clause

"""
The configuration flash on the Wishbone bus, as read-only memory the CPU can
address directly.

luna_soc already ships every part of this -- `SPIFlashMemoryMap` for the bus
side, `SPIPHYController` for the shift registers, `ECP5ConfigurationFlashInterface`
for the ECP5's `USRMCLK` special case -- so nothing here is a controller written
from scratch. What it adds is one thing luna_soc's version does not have: a
choice of read mode.

`luna_soc.gateware.core.spiflash.mmap.SPIFlashMemoryMap` hardcodes `0xeb`, Quad
I/O Fast Read, in the body of `elaborate`:

    flash_read_opcode = 0xeb
    flash_addr_width  = 4
    flash_bus_width   = 4
    flash_dummy_bits  = 24

That is the mode worth having, but it is the wrong mode to bring up *first*.
Quad puts the address, the dummy cycles and the data all on four lanes at once,
so a wiring fault, a sample-timing fault and a mode fault are indistinguishable
-- every one of them returns wrong bytes. Single-lane `0x03` uses one output pin
and one input pin, with no dummy cycles at all, so it either works or the wiring
is wrong, and nothing else is in the way.

`ModalSPIFlashMemoryMap` is the same FSM with those four constants moved into
`__init__`. Two modes are defined:

    SINGLE  0x03 Read Data       cmd/addr/data all 1-lane, 0 dummy bits
    QUAD    0xeb Quad I/O Read   cmd 1-lane, addr/dummy/data 4-lane, 24 dummy bits

The dummy value `0xff0000` for quad is not arbitrary. `0xeb` sends the four
address-phase bits M7-M0 immediately after the address; `0xff` there is *not*
`0xax`, so the flash does not enter Continuous Read mode and the next
transaction still needs its opcode. Sending `0xa0` instead would leave the chip
expecting an address where the controller sends a command, and every read after
the first would be garbage -- while the first one looked perfect.

Quad also requires the Quad Enable bit in status register 2. On this board it is
already set (SR2 = 0x02, recorded in docs/luna_ecp5_fpga/flash-detailed.md), so
nothing here writes to the flash. Nothing here ever writes to the flash: the
bitstream lives at offset 0.

## What the PHY divisor means

`SPIPHYController(divisor=d)` clocks SCK at `sync / (2 * (1 + d))`. At the
80 MHz sync this SoC runs:

    d=0   40.0 MHz    within the ECP5 MCLK pin's 62 MHz specification
    d=1   20.0 MHz
    d=2   13.3 MHz

40 MHz is the fast end of what Lattice specifies for this pin -- `MCLK` is a
configuration pin, tristated into user mode and reachable only through
`USRMCLK`, and FPGA-TN-02039 characterises it to 62 MHz. Faster has been
measured to work on this board and is not what a default should assume.
"""

from amaranth               import Cat, C, Module, Signal, DomainRenamer, unsigned
from amaranth.lib           import wiring

# luna_soc first, and the order is load-bearing. amaranth_soc is VENDORED
# inside luna_soc rather than installed standalone, and importing a luna_soc
# peripheral is what aliases it onto sys.modules under the bare name. Importing
# `amaranth_soc` before any luna_soc import fails outright; importing the
# vendored path directly instead yields a DIFFERENT class object for
# wishbone.Interface, so Decoder.add() rejects a bus that is structurally
# identical. Same constraint the SoC file documents at its own imports.
from luna_soc.gateware.core.spiflash.mmap  import SPIFlashMemoryMap

from amaranth_soc           import csr
from luna_soc.gateware.core.spiflash.port  import SPIControlPort
from luna_soc.gateware.core.spiflash.utils import WaitTimer


# Read modes, as (opcode, addr_width, bus_width, dummy_bits, dummy_value).
#
# `addr_width` and `bus_width` are lane counts, not bit counts: 1 for ordinary
# SPI, 4 for quad. The command phase is always one lane -- the flash has to be
# able to understand it before it knows what mode to switch to.
READ_MODES = {
    # 0x03 Read Data. One lane throughout, no dummy cycles. The flash is
    # specified for this at up to 50 MHz, which is above the 40 MHz this SoC
    # produces at divisor 0.
    "single": dict(opcode=0x03, addr_width=1, bus_width=1,
                   dummy_bits=0, dummy_value=0x000000),

    # 0xeb Fast Read Quad I/O. Address and data on four lanes, 24 dummy bits
    # (six clocks of mode byte plus four dummy clocks, expressed as 4-lane
    # transfers). See the note on 0xff above.
    "quad":   dict(opcode=0xeb, addr_width=4, bus_width=4,
                   dummy_bits=24, dummy_value=0xff0000),
}


class ModalSPIFlashMemoryMap(SPIFlashMemoryMap):
    """luna_soc's memory-mapped flash controller with a selectable read mode.

    Identical FSM; the four flash-protocol constants come from `READ_MODES`
    instead of being written into `elaborate`. Everything else -- burst
    continuation, the chip-select hold timer, the byte-order swap -- is
    upstream's and unmodified.
    """

    def __init__(self, *, size, mode="single", data_width=32, granularity=8,
                 name=None, domain="sync", byteorder="little"):
        if mode not in READ_MODES:
            raise ValueError(f"read mode {mode!r} is not one of "
                             f"{sorted(READ_MODES)}")
        self.mode = mode
        super().__init__(size=size, data_width=data_width,
                         granularity=granularity, name=name, domain=domain,
                         byteorder=byteorder)

    def elaborate(self, platform):
        m = Module()

        params = READ_MODES[self.mode]
        flash_read_opcode = params["opcode"]
        flash_cmd_bits    = 8
        flash_addr_bits   = 24
        flash_data_bits   = 32
        flash_cmd_width   = 1
        flash_addr_width  = params["addr_width"]
        flash_bus_width   = params["bus_width"]
        flash_dummy_bits  = params["dummy_bits"]
        flash_dummy_value = params["dummy_value"]

        source = self.source
        sink   = self.sink
        cs     = self.cs
        bus    = self.bus

        # Burst control. A sequential read that continues where the last one
        # ended skips the command, address and dummy phases entirely -- which is
        # the whole reason a memory map is faster than a register-poked
        # controller for the access pattern a CPU generates.
        burst_cs      = Signal()
        burst_adr     = Signal(len(self.bus.adr), reset_less=True)
        burst_timeout = WaitTimer(self.MMAP_DEFAULT_TIMEOUT, domain=self._domain)
        m.submodules.burst_timeout = burst_timeout

        with m.FSM(domain=self._domain):
            with m.State("IDLE"):
                m.d.comb += [
                    burst_timeout.wait.eq(1),
                    cs.eq(burst_cs),
                ]
                m.d.sync += burst_cs.eq(burst_cs & ~burst_timeout.done)
                with m.If(bus.cyc & bus.stb & ~bus.we):
                    with m.If(burst_cs & (bus.adr == burst_adr)):
                        m.next = "BURST-REQ"
                    with m.Else():
                        m.d.comb += cs.eq(0)
                        m.next = "BURST-CMD"

            with m.State("BURST-CMD"):
                m.d.comb += [
                    cs           .eq(1),
                    source.valid .eq(1),
                    source.data  .eq(flash_read_opcode),
                    source.len   .eq(flash_cmd_bits),
                    source.width .eq(flash_cmd_width),
                    source.mask  .eq(self.OE_MASK[flash_cmd_width]),
                ]
                with m.If(source.ready):
                    m.next = "CMD-RET"

            with m.State("CMD-RET"):
                m.d.comb += [cs.eq(1), sink.ready.eq(1)]
                with m.If(sink.valid):
                    m.next = "BURST-ADDR"

            with m.State("BURST-ADDR"):
                # `bus.adr` is a word address; the flash wants bytes, so two
                # zero bits are appended below it.
                m.d.comb += [
                    cs           .eq(1),
                    source.valid .eq(1),
                    source.width .eq(flash_addr_width),
                    source.mask  .eq(self.OE_MASK[flash_addr_width]),
                    source.data  .eq(Cat(C(0, 2), bus.adr)),
                    source.len   .eq(flash_addr_bits),
                ]
                m.d.sync += [
                    burst_cs  .eq(1),
                    burst_adr .eq(bus.adr),
                ]
                with m.If(source.ready):
                    m.next = "ADDR-RET"

            with m.State("ADDR-RET"):
                m.d.comb += [cs.eq(1), sink.ready.eq(1)]
                with m.If(sink.valid):
                    if flash_dummy_bits == 0:
                        m.next = "BURST-REQ"
                    else:
                        m.next = "DUMMY"

            # Skipped entirely in single-lane mode: 0x03 has no dummy cycles,
            # and issuing one would offset every byte read by a clock.
            #
            # NOTE the `if` above is Python, not `m.If`. Upstream writes
            # `with m.If(flash_dummy_bits == 0)`, which compares two Python ints
            # and yields a Python bool that Amaranth then treats as a constant
            # condition -- it works, but it emits both branches. This decides at
            # elaboration.
            if flash_dummy_bits:
                with m.State("DUMMY"):
                    m.d.comb += [
                        cs           .eq(1),
                        source.valid .eq(1),
                        source.width .eq(flash_addr_width),
                        source.mask  .eq(self.OE_MASK[flash_addr_width]),
                        source.data  .eq(flash_dummy_value),
                        source.len   .eq(flash_dummy_bits),
                    ]
                    with m.If(source.ready):
                        m.next = "DUMMY-RET"

                with m.State("DUMMY-RET"):
                    m.d.comb += [cs.eq(1), sink.ready.eq(1)]
                    with m.If(sink.valid):
                        m.next = "BURST-REQ"

            with m.State("BURST-REQ"):
                # mask=0 means every DQ pin is an input for this phase: the
                # flash is driving now, and holding an output enable on would
                # fight it. In single-lane mode DQ0 is still the controller's
                # output pin electrically, but nothing is being sent, so
                # releasing it is harmless and keeps one code path.
                m.d.comb += [
                    cs           .eq(1),
                    source.valid .eq(1),
                    source.width .eq(flash_bus_width),
                    source.mask  .eq(0),
                    source.len   .eq(flash_data_bits),
                ]
                with m.If(source.ready):
                    m.next = "BURST-DAT"

            with m.State("BURST-DAT"):
                word = (self.reverse_bytes(sink.data)
                        if self.byteorder == "little" else sink.data)
                m.d.comb += [
                    cs         .eq(1),
                    sink.ready .eq(1),
                    bus.dat_r  .eq(word),
                ]
                with m.If(sink.valid):
                    m.d.comb += bus.ack.eq(1)
                    m.d.sync += burst_adr.eq(burst_adr + 1)
                    m.next = "IDLE"

        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m


class FairSPIControlPortCrossbar(wiring.Component):
    """Share one SPI PHY between two cores, without either starving the other.

    luna_soc's `SPIControlPortCrossbar` cannot do this, and the reason is one
    line:

        with m.Switch(rr.grant):
            for i in range(self._num_ports):
                with m.Case(i):
                    connect(m, ...)
                    m.d.comb += grant_update.eq(~rr.valid | ~rr.requests[i])

    `grant_update` -- the round-robin's enable -- is asserted only when the
    port that currently HOLDS the grant stops asking for it. So the arbiter
    re-evaluates only when the incumbent yields, and a port that holds `cs`
    indefinitely owns the PHY indefinitely.

    `SPIFlashMemoryMap` holds `cs` for `MMAP_DEFAULT_TIMEOUT` -- 256 cycles --
    after every burst, deliberately: keeping chip select asserted is what lets
    a sequential read skip the command, address and dummy phases, and that is
    most of why a memory map beats a register-poked controller for the access
    pattern a CPU generates. It is a good optimisation. It also means that on a
    SoC whose firmware reads flash at all regularly, the memory map is holding
    `cs` essentially always, and the controller sharing the crossbar is never
    granted.

    The symptom is precise and misleading: memory-mapped reads work perfectly
    while every command issued through the controller returns zeros. It reads
    like a broken controller, and the controller is fine -- its requests never
    reach the PHY. Verified in simulation
    (scripts/riscv_flash_crossbar_sim.py): with the memory map holding `cs`,
    the controller is not granted in 600 cycles; with it idle, the grant
    arrives.

    This version re-arbitrates whenever the PHY is between transfers rather
    than only when the incumbent yields, so a port holding `cs` keeps its burst
    but cannot monopolise the PHY. Chip select is driven by whichever port
    holds the grant, so a burst is never interrupted mid-transfer.
    """

    def __init__(self, *, data_width=32, num_ports=2, domain="sync"):
        self._domain = domain
        self._num_ports = num_ports
        super().__init__(dict(
            controller=wiring.Out(SPIControlPort(data_width)),
            **{f"slave{i}": wiring.In(SPIControlPort(data_width))
               for i in range(num_ports)}))

        # Which port currently holds the grant, brought out so instrumentation
        # can confirm on HARDWARE that the controller is actually being served.
        # The starvation bug this class replaces was proven fixed in simulation
        # only, and "fixed in simulation" is exactly the claim that the rest of
        # this investigation has found wanting.
        self.grant = Signal(range(num_ports))

    def get_port(self, index):
        return getattr(self, f"slave{index}")

    def elaborate(self, platform):
        m = Module()

        ports = [self.get_port(i) for i in range(self._num_ports)]

        grant = self.grant
        # `locked` marks a transfer in flight. Re-arbitrating in the middle of
        # one would swap the PHY's data source between the request and its
        # response, so the grant only moves while nothing is outstanding.
        locked = Signal()

        # Arbitrate on `source.valid` -- a port with a transfer actually ready
        # to send -- NOT on `cs`.
        #
        # This is the correction that matters, and getting it wrong is what
        # makes upstream starve. `cs` is a HOLD signal: the memory map keeps it
        # asserted for 256 cycles after a burst so a following sequential read
        # can skip the command and address phases. It says "do not deselect the
        # chip yet", not "I have work". Treating it as a request hands the PHY
        # to a port that is idle but unwilling to let go.
        #
        # `source.valid` is the real request. A port between bursts holds `cs`
        # with `valid` low, so the grant can move to the other port, and the
        # only cost is that the next burst re-sends its command -- a few
        # microseconds, against a controller path that otherwise never runs at
        # all.
        requests = Cat(port.source.valid for port in ports)

        with m.If(~locked):
            # Round-robin from the port after the current holder, so a
            # continuously requesting port cannot starve the others. Searching
            # upward first and wrapping gives each port its turn.
            for offset in reversed(range(1, self._num_ports + 1)):
                candidate = (grant + offset) % self._num_ports
                with m.If(requests.bit_select(candidate, 1)):
                    m.d.sync += grant.eq(candidate)

        # A transfer is outstanding from the cycle the PHY accepts a command
        # until it returns the corresponding data.
        with m.If(self.controller.source.valid & self.controller.source.ready):
            m.d.sync += locked.eq(1)
        with m.Elif(self.controller.sink.valid & self.controller.sink.ready):
            m.d.sync += locked.eq(0)

        with m.Switch(grant):
            for index, port in enumerate(ports):
                with m.Case(index):
                    m.d.comb += [
                        self.controller.source.data  .eq(port.source.data),
                        self.controller.source.len   .eq(port.source.len),
                        self.controller.source.width .eq(port.source.width),
                        self.controller.source.mask  .eq(port.source.mask),
                        self.controller.source.valid .eq(port.source.valid),
                        port.source.ready            .eq(self.controller.source.ready),

                        port.sink.data               .eq(self.controller.sink.data),
                        port.sink.valid              .eq(self.controller.sink.valid),
                        self.controller.sink.ready   .eq(port.sink.ready),

                        self.controller.cs           .eq(port.cs),
                    ]

        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m


class FlashPinProbe(wiring.Component):
    """Counts what actually happens on the SPI flash pins, readable as CSRs.

    This exists because simulation and hardware flatly contradict each other.
    Every stage of the controller path passes in simulation -- the controller
    alone, the PHY alone, controller through crossbar to PHY, and the full
    wishbone/CSR/controller/crossbar/PHY chain, all producing the expected eight
    SCK edges for an eight-bit transfer. On hardware the same path reads the
    JEDEC ID as zeros and an erase completes 14,000 times too fast to be real.
    Something between the CPU and the pads is not doing what the model says, and
    no amount of further simulation can say what.

    An ILA would answer it. So does this, with far less machinery: the questions
    are countable rather than waveform-shaped. Does chip select ever assert? Does
    the clock ever toggle? Is the data pin ever driven? Does the crossbar ever
    grant the controller? Four counters and a sticky bit each, read over a
    console that has been reliable all session and shares nothing with the SPI
    path.

    STICKY, NOT LIVE, and that distinction has already cost this project a wrong
    conclusion once. The sideband debug bits were raw Wishbone `cyc` strobes
    sampled whenever the host happened to ask; `cyc` is high for a few cycles per
    transaction, so reading zero was near-certain even on a perfectly busy CPU,
    and the core was nearly reported dead on that basis. An SCK edge is one cycle
    wide and the CPU reads these registers thousands of cycles later. Latching
    turns "is this happening at the instant I looked" -- which is always no --
    into "has this ever happened", which is the actual question.

    The counters are saturating rather than wrapping. A count that rolls over to
    a small number is indistinguishable from one that barely moved, and the
    difference between "eight edges" and "eight edges plus a multiple of 65536"
    is not worth the ambiguity when the thing being tested is whether anything
    happens at all.
    """

    def __init__(self):
        # 16-bit counters: an 8-bit JEDEC sequence is 32 edges, a 1 KiB
        # memory-mapped read is a few thousand. 16 bits holds a whole
        # positive-control read without saturating, which matters because the
        # control has to produce an obviously large number.
        self._cs_fell    = csr.Register({"seen": csr.Field(csr.action.R, 1)},
                                        access="r")
        self._sck_edges  = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                        access="r")
        self._dq_driven  = csr.Register({"seen": csr.Field(csr.action.R, 1)},
                                        access="r")
        self._grants     = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                        access="r")
        self._oe_edges   = csr.Register({"count": csr.Field(csr.action.R, 16)},
                                        access="r")
        # Written to clear every counter, so the firmware can measure the
        # DIFFERENCE across one operation rather than a total since reset. A
        # total cannot separate "this transaction did nothing" from "this
        # transaction did nothing but an earlier one did".
        self._clear      = csr.Register({"strobe": csr.Field(csr.action.W, 1)},
                                        access="w")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("cs_fell",   self._cs_fell)
        builder.add("sck_edges", self._sck_edges)
        builder.add("dq_driven", self._dq_driven)
        builder.add("grants",    self._grants)
        builder.add("oe_edges", self._oe_edges)
        builder.add("clear",     self._clear)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":       wiring.In(csr.Signature(addr_width=5, data_width=8)),
            # Sampled from the pins and the crossbar. All inputs; this module
            # drives nothing into the flash path and cannot perturb what it is
            # measuring.
            "cs":        wiring.In(unsigned(1)),
            "sck":       wiring.In(unsigned(1)),
            "dq_oe":     wiring.In(unsigned(1)),
            "grant_ctrl": wiring.In(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        cs_fell   = Signal()
        dq_driven = Signal()
        sck_count = Signal(16)
        grant_count = Signal(16)
        # SCK edges that occurred while the DQ output driver was enabled.
        oe_edges = Signal(16)

        # Edge detection needs the previous value of each signal.
        cs_prev    = Signal(reset=1)
        sck_prev   = Signal()
        grant_prev = Signal()
        m.d.sync += [
            cs_prev.eq(self.cs),
            sck_prev.eq(self.sck),
            grant_prev.eq(self.grant_ctrl),
        ]

        clear = self._clear.f.strobe.w_stb

        with m.If(clear):
            m.d.sync += [
                cs_fell.eq(0),
                dq_driven.eq(0),
                sck_count.eq(0),
                grant_count.eq(0),
                oe_edges.eq(0),
            ]
        with m.Else():
            # CS is active low at the pad. The platform declares it PinsN, so
            # the signal here is already the logical "selected" sense -- high
            # means selected. A falling edge of the PAD is a rising edge here.
            with m.If(self.cs & ~cs_prev):
                m.d.sync += cs_fell.eq(1)

            with m.If(self.dq_oe):
                m.d.sync += dq_driven.eq(1)

            # Output enable asserted while the clock is running is the
            # interesting case, not merely asserted at some point. During the
            # RECEIVE phase of a read the controller must release DQ so the
            # flash can drive it; if the output driver is still on while SCK
            # toggles, the FPGA and the flash are fighting over the same wire
            # and the sampled data is whatever the FPGA is driving -- which
            # would read back as the controller's own idle level rather than
            # the flash's answer.
            with m.If(self.dq_oe & self.sck & ~sck_prev):
                with m.If(oe_edges != 0xFFFF):
                    m.d.sync += oe_edges.eq(oe_edges + 1)

            # Rising edges only, and saturating.
            with m.If(self.sck & ~sck_prev):
                with m.If(sck_count != 0xFFFF):
                    m.d.sync += sck_count.eq(sck_count + 1)

            with m.If(self.grant_ctrl & ~grant_prev):
                with m.If(grant_count != 0xFFFF):
                    m.d.sync += grant_count.eq(grant_count + 1)

        m.d.comb += [
            self._cs_fell.f.seen.r_data.eq(cs_fell),
            self._sck_edges.f.count.r_data.eq(sck_count),
            self._dq_driven.f.seen.r_data.eq(dq_driven),
            self._grants.f.count.r_data.eq(grant_count),
            self._oe_edges.f.count.r_data.eq(oe_edges),
        ]
        return m


class QSPIFlashPins(wiring.Component):
    """Requests `qspi_flash` and wires it to a `PinSignature`.

    luna_soc has `provider.QSPIFlashProvider` for this, and it is not used here
    for one reason: it wraps `platform.request` in a bare `except:` that logs a
    warning and returns an empty module. A platform typo, a renamed resource or
    a resource already claimed elsewhere then produces a design that builds
    cleanly, passes timing, and reads zeros forever -- and the only trace is one
    log line among thousands. This lets the exception out.
    """

    def __init__(self, name="qspi_flash", index=0):
        from luna_soc.gateware.core import spiflash
        self._name  = name
        self._index = index
        super().__init__({"pins": wiring.In(spiflash.PinSignature())})

    def elaborate(self, platform):
        m = Module()
        qspi = platform.request(self._name, self._index)
        m.d.comb += [
            self.pins.dq.i .eq(qspi.dq.i),
            qspi.dq.oe     .eq(self.pins.dq.oe),
            qspi.dq.o      .eq(self.pins.dq.o),
            qspi.cs.o      .eq(self.pins.cs.o),
        ]
        return m
