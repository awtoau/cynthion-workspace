#!/usr/bin/env python3
#
# One PHY, one controller, and who drives them. See #432.
# SPDX-License-Identifier: BSD-3-Clause

"""HyperRAM's role is a boot-time mode, not a build-time variant.

    mode 0  STAGE  BootRAM's three masters, through `HyperRAMHandover`
    mode 1  BIST   the ceiling engine, native in `hr`

## A mux, not an arbiter

The boot sequence frees the part: stage an image into HyperRAM, the resident
bootloader copies it into block RAM, jump. After the copy the CPU runs from
block RAM and HyperRAM has no live user, so the masters are never concurrent.

## Where the domains cut

PHY, controller and engine are all `hr`, off the second PLL, so a CK rung does
not move the CPU clock. BootRAM stays in `sync` and reaches the part through
`HyperRAMHandover`: one 32-bit pair per transaction with a polled flag, the same
shape as the engine's `go`/`done` `FFSynchronizer` pair. Not a stream, so no
FIFO -- `stream_buffer.py` records what a continuous crossing between two
unequal clocks did to the console.

The Wishbone memory window rides the same handshake, because `sustained` is off
and each 32-bit beat is already one HyperBus transaction. Coalescing a cache
line into one held-open transaction across the crossing is the async bridge
#425 still wants, and is not here.

## Refusal rather than a hang

A master that asks while the other mode owns the part is answered `refused`, and
the sticky bit reaches `hyperram_ck.status`. The alternative is the failure this
tree keeps paying for: the bootloader polling a staging flag that will never
complete, with no banner and nothing naming the cause.
"""

from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.lib.cdc import FFSynchronizer

from peripherals.hyperram_controller import HyperRAMController
from peripherals.hyperram_dqs_controller import HyperRAMDQSController

__all__ = ["HyperRAMPort", "HyperRAMShared", "HyperRAMHandover",
           "MODE_STAGE", "MODE_BIST"]

# Who owns the part. The reset value is STAGE, so a board that has only been
# configured -- no firmware yet -- can still be staged over JTAG.
MODE_STAGE = 0
MODE_BIST = 1


class HyperRAMPort:
    """One master's view of the controller and its PHY. Signals only.

    Sized from the controller instance rather than from a second copy of its
    arithmetic, and `Signal.like` carries the init values with it -- so a port
    that leaves the latency fields alone presents the controller's own resets,
    which is what the staging side wants.
    """

    # Master to controller.
    CONTROL = ("address", "register_space", "perform_write", "single_page",
               "start_transfer", "final_word", "write_data",
               "latency_clocks", "low_latency_clocks", "fixed_latency")
    # Controller back to master. `idle` and the two strobes are gated by the
    # grant, so an unselected master sees a controller that is never idle and
    # cannot start into someone else's transfer.
    STATUS = ("idle", "read_ready", "write_ready", "read_data")

    def __init__(self, controller, *, name):
        for field in self.CONTROL + self.STATUS:
            setattr(self, field, Signal.like(getattr(controller, field),
                                             name=f"{name}_{field}"))
        # The PHY's runtime knobs, muxed with the rest: the engine toggles
        # RESET# on its heartbeat and sweeps the DQS tap, the staging side holds
        # RESET# released and takes its tap from the probe CSR.
        self.phy_reset = Signal(name=f"{name}_phy_reset")
        self.readclksel = Signal(3, name=f"{name}_readclksel")
        self.read_phase = Signal(name=f"{name}_read_phase")
        # Back from the PHY. Ungated: nothing acts on them.
        self.dll_locked = Signal(name=f"{name}_dll_locked")
        self.dll_ready = Signal(name=f"{name}_dll_ready")
        self.burstdet = Signal(name=f"{name}_burstdet")
        # This port drives the controller. Separate from `idle`, which is low
        # both when another mode owns the part and when this one is mid-burst.
        self.granted = Signal(name=f"{name}_granted")


class HyperRAMShared(Elaboratable):
    """The pins, the PHY, the controller, and the mux in front of the port.

    Elaborate this in `hr` -- `top.py` wraps it in the same `DomainRenamer` the
    engine gets, so PHY, controller and engine are one domain and the only
    crossing is the handover's.

    Attributes
    ----------
    stage, bist : HyperRAMPort
        The two masters' ports. `sel` picks which one drives.
    sel : Signal
        `MODE_STAGE` or `MODE_BIST`, in `hr`. Latched only while the controller
        is idle, so a mode write cannot land inside a HyperBus transaction.
    """

    def __init__(self, *, dqs, ck_mhz, latency_clocks):
        self.dqs = dqs
        self.ck_mhz = ck_mhz
        # The FABRIC rate. The DQS PHY emits two CK per `hr` cycle.
        self.sync_mhz = ck_mhz / 2 if dqs else ck_mhz
        self.width = 32 if dqs else 16

        # Built here, wired to its pads in `elaborate`: the controller only
        # reads `phy` when it elaborates, and the pads cannot be requested
        # without a platform.
        if dqs:
            self.psram = HyperRAMDQSController(
                phy=None, sync_mhz=self.sync_mhz,
                high_latency_clocks=latency_clocks)
        else:
            self.psram = HyperRAMController(phy=None, sync_mhz=self.sync_mhz)

        self.stage = HyperRAMPort(self.psram, name="stage")
        self.bist = HyperRAMPort(self.psram, name="bist")

        self.sel = Signal()
        # The mode the mux is actually on, back to the CPU. It follows `sel`
        # only at an idle controller, so firmware reads what the part is doing
        # rather than what was asked for.
        self.mode = Signal()

    @property
    def ports(self):
        return (self.stage, self.bist)

    def elaborate(self, platform):
        m = Module()

        psram = self.psram
        dll_locked = Signal()
        dll_ready = Signal()
        burstdet = Signal()
        readclksel = Signal(3)
        read_phase = Signal()
        phy_reset = Signal()

        if self.dqs:
            # Raw pads: the DQS primitives own the tristates, the gearing and
            # the output buffers. The PHY drives CK, CS# and RESET# itself.
            from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY

            ram_bus = platform.request("ram", 0, dir="-")
            phy = HyperRAMDQSPHY(bus=ram_bus, readclksel=readclksel,
                                 read_phase=read_phase)
            m.d.comb += [dll_locked.eq(phy.dll_locked),
                         dll_ready.eq(phy.dll_ready),
                         burstdet.eq(phy.phy.burstdet),
                         # Active high into the PHY; the pad is `PinsN` and the
                         # PHY reads that polarity from the pin map.
                         phy.phy.reset.eq(phy_reset)]
        else:
            from luna.gateware.interface.psram import HyperRAMPHY

            ram_bus = platform.request("ram", 0)
            phy = HyperRAMPHY(bus=ram_bus)
            # No DQSBUFM, so no tap to select and no strobe to detect; the
            # platform's buffer holds RESET# released.
            m.d.comb += ram_bus.reset.o.eq(~phy_reset)

        psram.phy = phy.phy
        m.submodules += [phy, psram]

        # WHICH MODE IS LIVE, and it moves only at an idle controller. A mode
        # write mid-transaction would hand the pads over with CS# still Low.
        with m.If(psram.idle):
            m.d.sync += self.mode.eq(self.sel)

        stage, bist = self.ports
        for field in HyperRAMPort.CONTROL:
            m.d.comb += getattr(psram, field).eq(
                Mux(self.mode, getattr(bist, field), getattr(stage, field)))
        m.d.comb += [
            phy_reset.eq(Mux(self.mode, bist.phy_reset, stage.phy_reset)),
            readclksel.eq(Mux(self.mode, bist.readclksel, stage.readclksel)),
            read_phase.eq(Mux(self.mode, bist.read_phase, stage.read_phase)),
        ]

        for index, port in enumerate(self.ports):
            m.d.comb += [
                port.granted.eq(self.mode == index),
                port.idle.eq(psram.idle & (self.mode == index)),
                port.read_ready.eq(psram.read_ready & (self.mode == index)),
                port.write_ready.eq(psram.write_ready & (self.mode == index)),
                port.read_data.eq(psram.read_data),
                port.dll_locked.eq(dll_locked),
                port.dll_ready.eq(dll_ready),
                port.burstdet.eq(burstdet),
            ]

        return m


class HyperRAMHandover(Elaboratable):
    """One 32-bit HyperRAM transaction, asked for in `sync` and run in `hr`.

    `interface` is what `BootRAM` is handed in place of a controller: the same
    word protocol, in `sync`, with the transaction itself run on the other side
    of a four-phase handshake. BootRAM sees a controller that happens to be
    slow to go idle; the device sees one whole transaction issued by logic in
    its own domain.

        write   BootRAM offers 32 bits as one or two words -> the pair crosses
                -> `hr` runs the transaction -> `ack` -> `idle`
        read    request crosses -> `hr` runs it -> the pair crosses back ->
                BootRAM is offered it as one or two words -> `idle`

    ONE TRANSACTION PER HANDSHAKE, and that is the whole reason this is not a
    FIFO. A HyperBus data phase cannot be stalled: once CS# is Low the device
    moves a word every CK. Crossing per WORD would put a CDC round trip inside
    the data phase; crossing per TRANSACTION puts it either side, where a delay
    costs latency and nothing else.

    WRITES ARE POSTED. BootRAM is offered both halves before the transaction
    runs, so a Wishbone write acknowledges once the pair is held rather than
    once the device has it. Ordering is what makes that safe: one transaction
    crosses at a time, and the next cannot start until this one has gone idle.

    `sustained` bursts do not cross. Each 32-bit beat is its own transaction,
    which is what the shipping SoC already does -- `RegisteredResponse`
    withholds STB for a cycle after every acknowledgement, so no master here can
    feed a held-open transaction anyway.

    Attributes
    ----------
    interface : object
        Hand this to `BootRAM(interface=...)`.
    port : HyperRAMPort
        Hand this to `HyperRAMShared`.
    refused : Signal
        Sticky, in `sync`: a transaction was asked for while the engine owned
        the part. Cleared by `refused_clear`.
    """

    class Interface:
        """The controller's word protocol, as `BootRAM` uses it."""

        def __init__(self, *, width):
            self.address = Signal(32)
            self.register_space = Signal()
            self.perform_write = Signal()
            self.single_page = Signal()
            self.start_transfer = Signal()
            self.final_word = Signal()
            self.write_data = Signal(width)
            self.idle = Signal(init=1)
            self.read_ready = Signal()
            self.write_ready = Signal()
            self.read_data = Signal(width)

    def __init__(self, *, port, width, domain="hr"):
        self.port = port
        self.width = width
        self._domain = domain
        # Two device words per 32-bit pair on the 16-bit controller, one on the
        # 32-bit DQS one.
        self.words = 32 // width
        self.interface = self.Interface(width=width)

        self.refused = Signal()
        self.refused_clear = Signal()

    def elaborate(self, platform):
        m = Module()
        hr = self._domain
        face = self.interface
        port = self.port

        # --- the handshake --------------------------------------------------
        #
        # Four phase, both edges synchronised: `req` up, `done` up, `req` down,
        # `done` down. The payload either side is held still for the whole time
        # its flag is high, so nothing but the two flags crosses as an edge.
        req = Signal()
        done = Signal()
        req_hr = Signal()
        done_sync = Signal()
        m.submodules.req_cdc = FFSynchronizer(req, req_hr, o_domain=hr)
        m.submodules.done_cdc = FFSynchronizer(done, done_sync)

        address = Signal(32)
        writing = Signal()
        register_space = Signal()
        wdata = Signal(32)
        rdata = Signal(32)
        refused_hr = Signal()

        # --- the `sync` side, which is what BootRAM talks to -----------------
        word = Signal(range(self.words + 1))
        last_word = self.words - 1

        m.d.comb += face.idle.eq(0)

        with m.FSM() as fsm:
            with m.State("IDLE"):
                m.d.comb += face.idle.eq(1)
                with m.If(face.start_transfer):
                    m.d.sync += [
                        address.eq(face.address),
                        writing.eq(face.perform_write),
                        register_space.eq(face.register_space),
                        word.eq(0),
                    ]
                    m.next = "COLLECT"
                    with m.If(~face.perform_write):
                        m.next = "RUN"

            with m.State("COLLECT"):
                # One `write_ready` per device word, back to back. BootRAM
                # advances its own half-select on each and presents the next
                # half on the cycle after -- the device is not on this side of
                # the handshake, so the words can be taken as fast as they come.
                m.d.comb += face.write_ready.eq(1)
                m.d.sync += [
                    wdata.word_select(word, self.width).eq(face.write_data),
                    word.eq(word + 1),
                ]
                with m.If(word == last_word):
                    m.d.sync += word.eq(0)
                    m.next = "RUN"

            with m.State("RUN"):
                m.d.comb += req.eq(1)
                with m.If(done_sync):
                    m.d.sync += word.eq(0)
                    m.next = "SETTLE"
                    with m.If(~writing):
                        m.next = "DELIVER"

            with m.State("DELIVER"):
                # The pair, offered back in the same halves it would have
                # arrived in. Low half first: HyperBus sends the
                # lower-addressed word first and BootRAM assembles in that
                # order.
                m.d.comb += [
                    face.read_ready.eq(1),
                    face.read_data.eq(rdata.word_select(word, self.width)),
                ]
                m.d.sync += word.eq(word + 1)
                with m.If(word == last_word):
                    m.next = "SETTLE"

            with m.State("SETTLE"):
                # `req` is already low; wait for the far side to drop `done`
                # before another transaction can raise `req` again.
                with m.If(~done_sync):
                    m.next = "IDLE"

        # `refused_hr` is held still by the far side for as long as `done` is,
        # and `done_sync` arrives two flops after it -- so it is settled by the
        # time this samples it.
        with m.If(refused_hr & done_sync):
            m.d.sync += self.refused.eq(1)
        with m.If(self.refused_clear):
            m.d.sync += self.refused.eq(0)

        # --- the `hr` side, which is what the device sees --------------------
        hr_word = Signal(range(self.words + 1))
        m.d.comb += [
            port.address.eq(address),
            port.perform_write.eq(writing),
            port.register_space.eq(register_space),
            port.single_page.eq(0),
            port.write_data.eq(wdata.word_select(hr_word, self.width)),
            port.final_word.eq(hr_word == last_word),
            # RESET# released, and the DQS tap comes from the probe CSR.
            port.phy_reset.eq(0),
        ]

        with m.FSM(domain=hr) as runner:
            with m.State("IDLE"):
                m.d[hr] += hr_word.eq(0)
                with m.If(req_hr):
                    m.next = "START"
                    # REFUSED, not queued and not hung. The engine owns the
                    # part; say so and complete, so a poll on the far side
                    # ends.
                    with m.If(~port.granted):
                        m.next = "REFUSED"

            with m.State("START"):
                m.d.comb += port.start_transfer.eq(1)
                m.next = "ARM"

            with m.State("ARM"):
                # `idle` is still high on the cycle the transfer is issued, so
                # a state that exits on `idle` alone falls straight through and
                # reports a transfer that never happened.
                with m.If(~port.idle):
                    m.next = "BUSY"

            with m.State("BUSY"):
                with m.If(port.idle):
                    m.next = "DONE"

            with m.State("REFUSED"):
                # All ones rather than the last transaction's data: a stale
                # pair reads as a plausible one, and the sticky bit is the only
                # thing that would have said otherwise.
                m.d[hr] += [refused_hr.eq(1), rdata.eq(~0)]
                m.next = "DONE"

            with m.State("DONE"):
                m.d.comb += done.eq(1)
                with m.If(~req_hr):
                    m.d[hr] += refused_hr.eq(0)
                    m.next = "IDLE"

        active = ~runner.ongoing("IDLE") & ~runner.ongoing("DONE")
        word_event = Mux(writing, port.write_ready, port.read_ready)
        with m.If(active & word_event):
            m.d[hr] += hr_word.eq(hr_word + 1)
            with m.If(~writing):
                m.d[hr] += rdata.word_select(hr_word, self.width).eq(
                    port.read_data)

        return m
