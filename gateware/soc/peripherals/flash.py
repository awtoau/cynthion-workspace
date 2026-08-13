#!/usr/bin/env python3
#
# Memory-mapped configuration SPI flash for the VexiiRiscv SoC.
# SPDX-License-Identifier: BSD-3-Clause

"""
The configuration flash on the Wishbone bus, as read-only memory the CPU can
address directly.

The bus side, the shift registers and the ECP5 `USRMCLK` special case are all
luna_soc's (`SPIFlashMemoryMap`, `SPIPHYController`,
`ECP5ConfigurationFlashInterface`). What this file adds:

  * a selectable read mode (`ModalSPIFlashMemoryMap`) -- upstream hardcodes
    quad in the body of `elaborate`
  * an arbiter that cannot starve a port (`FairSPIControlPortCrossbar`)
  * a chip select that holds across CPU register writes (see `HoldSPIController`)
  * an ILA on the SPI wires, readable over the USB console

`../../docs/architecture.md` records why each is ours rather than upstream's.

## Read modes

    SINGLE  0x03 Read Data       cmd/addr/data 1-lane, 0 dummy bits
    QUAD    0xeb Quad I/O Read   cmd 1-lane, addr/dummy/data 4-lane, 24 dummy

  * **Bring up in SINGLE.** Quad puts address, dummy cycles and data on four
    lanes at once, so a wiring fault, a sample-timing fault and a mode fault
    are indistinguishable -- every one returns wrong bytes. Single uses one
    output and one input pin with no dummy cycles.
  * **The quad dummy value `0xff0000` is not arbitrary.** `0xeb` sends mode
    bits M7-M0 straight after the address; `0xff` is not `0xax`, so the flash
    does not enter Continuous Read and the next transaction still needs its
    opcode. `0xa0` would leave the chip expecting an address where the
    controller sends a command -- every read after the first is garbage, while
    the first looks perfect.
  * Quad needs the Quad Enable bit in status register 2. It is already set on
    this board (SR2 = 0x02, `docs/luna_ecp5_fpga/flash-detailed.md`). Nothing
    here ever writes to the flash; the bitstream lives at offset 0.

## PHY divisor

`SPIPHYController(divisor=d)` clocks SCK at `sync / (2 * (1 + d))`. At 80 MHz:

    d=0   40.0 MHz
    d=1   20.0 MHz
    d=2   13.3 MHz

40 MHz is the fast end of what Lattice specifies: `MCLK` is a configuration
pin, tristated into user mode and reachable only through `USRMCLK`, and
FPGA-TN-02039 characterises it to 62 MHz. Faster has been measured to work on
this board, which is not what a default should assume.
"""

from amaranth               import Cat, C, Module, Signal, DomainRenamer, unsigned
from amaranth.lib.cdc       import FFSynchronizer
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
from luna_soc.gateware.core.spiflash.phy   import (SPIPHYController,
                                                   SPIClockGenerator)
from luna_soc.gateware.core.spiflash.controller import SPIController
from luna_soc.gateware.core.spiflash.port  import (SPIControlPort,
                                                   StreamCore2PHY,
                                                   StreamPHY2Core)
from luna_soc.gateware.core.spiflash.utils import WaitTimer

from luna.gateware.debug.ila import IntegratedLogicAnalyzer


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

    Re-arbitrates whenever the PHY is between transfers, so a port holding `cs`
    keeps its burst but cannot monopolise the PHY. Chip select follows the
    grant, so no burst is interrupted mid-transfer.

    **The starvation this avoids.** luna_soc's `SPIControlPortCrossbar` gates
    its round-robin on `grant_update.eq(~rr.valid | ~rr.requests[i])` -- it
    re-evaluates only when the incumbent stops asking, so a port holding `cs`
    indefinitely owns the PHY indefinitely. `SPIFlashMemoryMap` holds `cs` for
    `MMAP_DEFAULT_TIMEOUT` (256 cycles) after every burst, which is what lets a
    sequential read skip the command, address and dummy phases -- so on a SoC
    whose firmware reads flash regularly, the controller sharing the crossbar
    is never granted.

    The symptom is misleading: memory-mapped reads work perfectly while every
    controller command returns zeros, which reads as a broken controller.
    Measured in `scripts/riscv_flash_crossbar_sim.py` -- with the memory map
    holding `cs`, the controller is not granted in 600 cycles; with it idle the
    grant arrives. See `../../docs/architecture.md`.
    """

    def __init__(self, *, data_width=32, num_ports=2, domain="sync"):
        self._domain = domain
        self._num_ports = num_ports
        super().__init__(dict(
            controller=wiring.Out(SPIControlPort(data_width)),
            **{f"slave{i}": wiring.In(SPIControlPort(data_width))
               for i in range(num_ports)}))

        # Which port holds the grant, brought out so instrumentation can confirm
        # on HARDWARE that the controller is being served. The starvation fix is
        # proven in simulation only, and "fixed in simulation" is exactly the
        # claim this investigation has repeatedly found wanting.
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


class ObservablePHY(SPIPHYController):
    """luna_soc's SPI PHY with its internal capture strobes brought out.

    The pin probe cornered the JEDEC fault at the input sampling path: the
    controller asserts chip select, clocks out a valid 0x9f, releases DQ for
    exactly the right 24 response edges, and reads back zeros. What counters
    cannot show is PHASE -- whether the input bit is latched on the wrong edge,
    a cycle early or late, or not at all on the second through fourth transfers.
    That is a waveform question, and answering it needs the signal that decides
    WHEN an input bit is captured.

    In `SPIPHYController.elaborate` that signal is `sr_in_shift`, a local. This
    subclass re-implements the same elaborate body verbatim and assigns the
    interesting signals to attributes so an ILA can reach them.

    RE-IMPLEMENTED RATHER THAN PATCHED, and the copy is deliberate: the point of
    this class is to observe what the real PHY does, so any behavioural
    difference between this and upstream would invalidate the measurement. The
    body below is upstream's, unchanged except for the assignments that expose
    signals. If upstream changes, this must be re-synced or it is measuring a
    different circuit than the one that ships.
    """

    def __init__(self, pads, data_width=32, divisor=0, domain="sync",
                 full_sck=False):
        super().__init__(pads, data_width=data_width, divisor=divisor,
                         domain=domain)
        self.full_sck = full_sck
        # Exposed for instrumentation. Driven in elaborate().
        self.o_sr_in_shift = Signal()
        self.o_sr_out_load = Signal()
        self.o_sample      = Signal()
        self.o_update      = Signal()
        self.o_clk         = Signal()
        self.o_dq_i1       = Signal()
        # What the PHY DRIVES on DQ0. The input side alone shows the flash
        # staying silent but not whether the command it was sent was correct,
        # and "the flash ignored a valid 0x9f" and "the flash was sent
        # something that is not 0x9f" are different faults.
        self.o_dq_o0       = Signal()
        self.o_in_xfer     = Signal()
        self.o_in_xfer_end = Signal()

    def elaborate(self, platform):
        m = Module()

        pads   = self.pads
        sink   = self.sink
        source = self.source

        # Full-rate SCK: one bit time per domain cycle instead of two, and the
        # pad driven as a gated clock rather than a registered level (#100).
        if self.full_sck:
            from soc.peripherals.flash_sck_full import FullRateClockGenerator
            clkgen = FullRateClockGenerator(self.divisor, domain=self._domain)
        else:
            clkgen = SPIClockGenerator(self.divisor, domain=self._domain)
        m.submodules.clkgen = clkgen
        spi_clk_divisor = self.divisor

        cs_delay = 0
        cs_enable = Signal()
        if cs_delay > 0:
            m.submodules.cs_timer = cs_timer = WaitTimer(cs_delay + 1,
                                                         domain=self._domain)
            m.d.comb += [
                cs_timer.wait.eq(self.cs),
                cs_enable.eq(cs_timer.done),
            ]
        else:
            m.d.comb += cs_enable.eq(self.cs)

        dq_o  = Signal.like(pads.dq.o)
        dq_i  = Signal.like(pads.dq.i)
        dq_oe = Signal.like(pads.dq.oe)
        if self.full_sck:
            from soc.peripherals.flash_sck_full import gated_sck
            gated_sck(m, pads, clkgen, domain=self._domain)
        m.d.sync += [
            *([] if self.full_sck else [pads.sck.eq(clkgen.clk)]),
            pads.cs.o  .eq(cs_enable),
            pads.dq.o  .eq(dq_o),
            pads.dq.oe .eq(dq_oe),
            dq_i       .eq(pads.dq.i),
        ]
        if hasattr(pads.cs, "oe"):
            m.d.comb += pads.cs.oe.eq(1)

        sr_cnt       = Signal(8, reset_less=True)
        sr_out_load  = Signal()
        sr_out_shift = Signal()
        sr_out       = Signal(len(source.data), reset_less=True)
        sr_in_shift  = Signal()
        sr_in        = Signal(len(source.data), reset_less=True)

        m.d.comb += dq_oe.eq(source.mask)
        with m.Switch(source.width):
            with m.Case(1):
                m.d.comb += dq_o.eq(sr_out[-1:])
            with m.Case(2):
                m.d.comb += dq_o.eq(sr_out[-2:])
            with m.Case(4):
                m.d.comb += dq_o.eq(sr_out[-4:])
            with m.Case(8):
                m.d.comb += dq_o.eq(sr_out[-8:])

        with m.If(sr_out_load):
            m.d.sync += sr_out.eq(
                source.data << (len(source.data) - source.len).as_unsigned())

        with m.If(sr_out_shift):
            with m.Switch(source.width):
                with m.Case(1):
                    m.d.sync += sr_out.eq(Cat(C(0, 1), sr_out))
                with m.Case(2):
                    m.d.sync += sr_out.eq(Cat(C(0, 2), sr_out))
                with m.Case(4):
                    m.d.sync += sr_out.eq(Cat(C(0, 4), sr_out))
                with m.Case(8):
                    m.d.sync += sr_out.eq(Cat(C(0, 8), sr_out))

        with m.If(sr_in_shift):
            with m.Switch(source.width):
                with m.Case(1):
                    m.d.sync += sr_in.eq(Cat(dq_i[1], sr_in))
                with m.Case(2):
                    m.d.sync += sr_in.eq(Cat(dq_i[:2], sr_in))
                with m.Case(4):
                    m.d.sync += sr_in.eq(Cat(dq_i[:4], sr_in))
                with m.Case(8):
                    m.d.sync += sr_in.eq(Cat(dq_i[:8], sr_in))

        m.d.comb += sink.data.eq(sr_in)

        with m.FSM(domain=self._domain) as fsm:
            with m.State("WAIT-CMD-DATA"):
                with m.If(cs_enable & source.valid):
                    m.d.sync += sr_cnt.eq(source.len - source.width)
                    m.d.comb += sr_out_load.eq(1)
                    m.next = "XFER"

            with m.State("XFER"):
                m.d.comb += [
                    clkgen.en   .eq(1),
                    sr_in_shift .eq(clkgen.sample),
                    sr_out_shift.eq(clkgen.update),
                ]
                with m.If(clkgen.update):
                    m.d.sync += sr_cnt.eq(sr_cnt - source.width)
                    with m.If(sr_cnt == 0):
                        m.next = "XFER-END"

            with m.State("XFER-END"):
                with m.If((spi_clk_divisor > 0) | clkgen.sample):
                    m.d.comb += source.ready.eq(1)
                    m.d.comb += sr_in_shift.eq(spi_clk_divisor == 0)
                    m.next = "SEND-STATUS-DATA"

            with m.State("SEND-STATUS-DATA"):
                m.d.comb += sink.valid.eq(1)
                with m.If(sink.ready):
                    m.next = "WAIT-CMD-DATA"

            m.d.comb += [
                self.o_in_xfer     .eq(fsm.ongoing("XFER")),
                self.o_in_xfer_end .eq(fsm.ongoing("XFER-END")),
            ]

        m.d.comb += [
            self.o_sr_in_shift .eq(sr_in_shift),
            self.o_sr_out_load .eq(sr_out_load),
            self.o_sample      .eq(clkgen.sample),
            self.o_update      .eq(clkgen.update),
            self.o_clk         .eq(clkgen.clk),
            self.o_dq_i1       .eq(dq_i[1]),
            self.o_dq_o0       .eq(dq_o[0]),
        ]

        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m


class HoldableSPIController(wiring.Component):
    """luna_soc's SPIController with a chip select that software can hold down.

    The ILA capture (scripts/riscv_flash_ila_decode.py) shows upstream's chip
    select dropping between every transfer of a multi-part command: four
    separate 8-bit windows for a JEDEC read, with CS deasserted for 81, 36 and
    36 samples in between. The flash resets its command state on every rising
    edge of CS, so the 24 clocks after the command byte are shifted in with no
    command pending and the chip drives nothing -- dq_i[1] reads zero for all
    32 bits, which is exactly what the ID register reported.

    The cause is in upstream's chip select generation:

        with m.State("RISE"):  m.d.comb += cs.eq(tx_fifo.r_rdy)
        with m.State("FALL"):  m.d.comb += cs.eq(self._cs.f.select.w_data
                                                 | tx_fifo.r_rdy)

    `self._cs.f.select` is a csr.action.W field, so `w_data` is valid only
    during the cycle of the write strobe. It is a pulse, not a latch, so the
    `cs` register cannot hold chip select down across transfers and CS collapses
    to `tx_fifo.r_rdy` alone.

    Software cannot work around that. Queueing every transfer before draining
    any was tried and does not help: the CPU issues CSR writes far more slowly
    than the PHY drains the FIFO, so the FIFO empties mid-command regardless of
    the order of operations. The 81-sample gap in the capture is precisely the
    CPU writing registers while the FIFO sits empty.

    So the hold has to be in gateware. This wraps the upstream controller and
    replaces its chip select with `held | upstream_cs`, where `held` is a proper
    latching register: write 1 to assert, write 0 to release. A command becomes

        hold(1); push...; pop...; hold(0);

    and CS stays low throughout regardless of how slowly the CPU feeds the FIFO.

    Everything else -- the FIFOs, the PHY stream handshake, the register map --
    is upstream's, untouched. The `cs` register keeps its original pulse
    behaviour so existing firmware is unaffected; `hold` is a new register at
    its own offset.
    """

    def __init__(self, *, data_width=32, name=None, domain="sync"):
        self._domain = domain
        self._inner = SPIController(data_width=data_width, name=name,
                                    domain=domain)

        self._hold = csr.Register({"select": csr.Field(csr.action.RW, 1)},
                                  access="rw")
        builder = csr.Builder(addr_width=2, data_width=8)
        builder.add("hold", self._hold)
        self._hold_bridge = csr.Bridge(builder.as_memory_map())

        # Two CSR windows behind one decoder: upstream's registers where
        # firmware already expects them, and `hold` after them.
        self._decoder = csr.Decoder(addr_width=6, data_width=8)
        self._decoder.add(self._inner.bus, addr=0x00)
        self._decoder.add(self._hold_bridge.bus, addr=0x20)

        super().__init__({
            "bus":    wiring.In(self._decoder.bus.signature),
            "source": wiring.Out(StreamCore2PHY(data_width)),
            "sink":   wiring.In(StreamPHY2Core(data_width)),
            "cs":     wiring.Out(unsigned(1)),
        })
        self.bus.memory_map = self._decoder.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.inner       = self._inner
        m.submodules.hold_bridge = self._hold_bridge
        m.submodules.decoder     = self._decoder

        # Wired by hand. Both `self.bus` and the decoder's bus are CSR targets,
        # so wiring.connect() sees two drivers on r_data and refuses, while
        # flipping either side puts two drivers on addr instead. Explicit
        # assignment is unambiguous: the external bridge drives address and
        # write data in, the decoder drives read data back out.
        m.d.comb += [
            self._decoder.bus.addr   .eq(self.bus.addr),
            self._decoder.bus.r_stb  .eq(self.bus.r_stb),
            self._decoder.bus.w_stb  .eq(self.bus.w_stb),
            self._decoder.bus.w_data .eq(self.bus.w_data),
            self.bus.r_data          .eq(self._decoder.bus.r_data),
        ]

        # Wired by hand rather than with wiring.connect. The component's own
        # `source`/`sink` face outward while the inner controller's face inward,
        # so a connect() of the two sees two drivers on the same members and
        # refuses. Explicit assignment states the direction of each signal.
        m.d.comb += [
            self.source.data    .eq(self._inner.source.data),
            self.source.len     .eq(self._inner.source.len),
            self.source.width   .eq(self._inner.source.width),
            self.source.mask    .eq(self._inner.source.mask),
            self.source.valid   .eq(self._inner.source.valid),
            self._inner.source.ready .eq(self.source.ready),

            self._inner.sink.data  .eq(self.sink.data),
            self._inner.sink.valid .eq(self.sink.valid),
            self.sink.ready        .eq(self._inner.sink.ready),
        ]

        # The whole point: CS is asserted while software holds it OR while the
        # upstream controller wants it. The OR rather than a replacement means
        # a command that does not bother to hold still works exactly as before.
        #
        # ---- and the crossing, when there is one -------------------------
        #
        # `_hold` is an `amaranth_soc.csr` register, so its flip-flop is in
        # `sync` -- it is on the CPU's bus and it cannot be anywhere else. The
        # inner controller and the pad are in `self._domain`. When those differ,
        # `hold.data` changes on a `sync` edge and is ORed straight onto a chip
        # select that the SPI clock is timed against.
        #
        # Nothing in the FPGA samples it, which is why it builds and simulates
        # clean: the thing that samples CS is the flash, and what it sees is an
        # edge with no defined relationship to SCK. The W25Q32's tCSS/tCSH are
        # setup and hold against the clock, so the failure is a command byte
        # accepted or dropped depending on where the two clocks happen to sit --
        # which is the class of fault this file already exists to fix once.
        #
        # `#241` was the same shape: a parameter that looked applied and was not.
        # The difference here is that the parameter CANNOT simply be applied --
        # renaming the register into `self._domain` would put the CPU's own bus
        # there and move the crossing somewhere worse. So the register stays in
        # `sync` and the LEVEL crosses, which is what `FFSynchronizer` is for.
        held = self._hold.f.select.data
        if self._domain != "sync":
            crossed = Signal()
            m.submodules.hold_cdc = FFSynchronizer(held, crossed,
                                                  o_domain=self._domain)
            held = crossed
        m.d.comb += self.cs.eq(held | self._inner.cs)

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


class FlashILA(wiring.Component):
    """A logic analyser on the flash path, read back over the CPU's console.

    The pin probe answered "does anything happen" and cornered the fault at the
    input sampling path. It cannot answer what is left, because what is left is
    PHASE: whether the input bit is latched on the wrong edge, a cycle early or
    late, or not at all on the second through fourth transfers of a multi-part
    command. Counters have no time axis. This does.

    Built on LUNA's `IntegratedLogicAnalyzer`, which is the capture engine --
    trigger, sample memory and a read port. What is added here is a CSR face, so
    samples come back over the USB console rather than needing a spare UART pin.
    That matters on r1.4: the `uart` pins are shared with JTAG TDI/TMS, so a
    design that drives them fights the adapter loading its own bitstream, and
    `AsyncSerialILA` would need exactly those pins.

    NARROW AND DEEP, deliberately. Eight signals is enough to answer the
    question and 1024 samples is 32 SCK edges at divisor 0 with room for the
    gaps between transfers -- the window has to span all four transfers of a
    JEDEC read, because "does the strobe still fire on transfer 2" is the
    hypothesis. A wider capture would buy signals nobody is asking about at the
    cost of the depth that makes the trace readable.

    One DP16KD at 8 bits x 1024. The SoC uses 41 of 56, so this fits without
    displacing anything else -- the constraint that matters, since a build that
    stops reproducing the fault wastes the run.
    """

    # The eight signals, in bit order. This list IS the trace format: the host
    # side decodes by position, so changing the order changes the meaning of
    # every captured byte.
    # Bit 7 carries the OUTGOING data bit rather than in_xfer_end. The FSM
    # windows are already understood from the previous capture; what is not
    # known is whether the command actually driven onto DQ0 is the command the
    # firmware asked for. Eight bits is one BRAM-friendly byte per sample and
    # this is the more valuable eighth signal.
    SIGNAL_NAMES = ["sck", "dq_i1", "cs", "sr_in_shift",
                    "sample", "update", "in_xfer", "dq_o0"]

    def __init__(self, *, sample_depth=1024):
        self.sample_depth = sample_depth

        self._status = csr.Register({
            "complete": csr.Field(csr.action.R, 1),
            "sampling": csr.Field(csr.action.R, 1),
        }, access="r")
        self._arm = csr.Register({"strobe": csr.Field(csr.action.W, 1)},
                                 access="w")
        # Which sample to read. 16 bits covers any depth this will ever have.
        self._index = csr.Register({"value": csr.Field(csr.action.RW, 16)},
                                   access="rw")
        self._sample = csr.Register({"bits": csr.Field(csr.action.R, 8)},
                                    access="r")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("status", self._status)
        builder.add("arm",    self._arm)
        builder.add("index",  self._index)
        builder.add("sample", self._sample)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": wiring.In(csr.Signature(addr_width=5, data_width=8)),
            # The probed signals. All inputs -- this module drives nothing into
            # the flash path and cannot perturb what it measures.
            "sck":          wiring.In(unsigned(1)),
            "dq_i1":        wiring.In(unsigned(1)),
            "cs":           wiring.In(unsigned(1)),
            "sr_in_shift":  wiring.In(unsigned(1)),
            "sample_stb":   wiring.In(unsigned(1)),
            "update_stb":   wiring.In(unsigned(1)),
            "in_xfer":      wiring.In(unsigned(1)),
            "dq_o0":        wiring.In(unsigned(1)),
            # Asserted by software immediately before the transaction to be
            # captured. Triggering on the TRANSACTION rather than on `cs`
            # falling is the point: if the fault were that nothing is issued,
            # triggering on the symptom's absence would capture nothing and
            # confirm only what is already known.
            "trigger":      wiring.In(unsigned(1)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        signals = [self.sck, self.dq_i1, self.cs, self.sr_in_shift,
                   self.sample_stb, self.update_stb, self.in_xfer,
                   self.dq_o0]

        # samples_pretrigger=2 doubles as a synchroniser for the pin inputs,
        # and catches the couple of cycles before the trigger so the first
        # clock edge is not lost off the front of the window.
        m.submodules.ila = ila = IntegratedLogicAnalyzer(
            signals=signals, sample_depth=self.sample_depth,
            samples_pretrigger=2)

        m.d.comb += [
            ila.trigger.eq(self.trigger | self._arm.f.strobe.w_stb),
            ila.captured_sample_number.eq(self._index.f.value.data),
            self._sample.f.bits.r_data.eq(ila.captured_sample),
            self._status.f.complete.r_data.eq(ila.complete),
            self._status.f.sampling.r_data.eq(ila.sampling),
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
