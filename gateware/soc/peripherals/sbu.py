#!/usr/bin/env python3
#
# One bidirectional pin pair, three modes: raw, serial, SWD.
# SPDX-License-Identifier: BSD-3-Clause

"""
The sideband peripheral: two pins, three modes, one instance per port.

    mode 0  raw     software drives and samples both pins, with every
                    transition captured and timestamped in the fabric
    mode 1  serial  a UART, `peripherals/serial_line.py` on the pair
    mode 2  swd     `peripherals/swd.py`, host side -- Cynthion clocks it

**Raw is the reset mode and the fallback.** Any sideband protocol that has no
engine here is still reachable from software through it, and the capture FIFO is
what makes an unknown protocol readable rather than guessable. The two built
engines are an optimisation of that path, not a replacement for it.

The three share the pads and the tristate logic; the mode register decides who
drives. Nothing is hardwired to `SBU1`/`SBU2`: the ports are a pin pair, so the
same peripheral goes on a PMOD for bring-up.

## Pin 0 and pin 1

    mode      pin 0                        pin 1
    raw       driven and sampled           driven and sampled
    serial    transmit                     receive
    swd       SWCLK, always driven         SWDIO, turnaround per B4.2

`mode.swap` exchanges them, and it covers both reasons the pair can arrive the
other way round: SBU1/SBU2 swap on a cable flip like CC1/CC2, and the two ports
may be wired with opposite polarity (#517 row 9, `docs/sbu.md`). One control,
because both are the same correction and correcting them separately would mean
tracking which of the two applied.

## Bring-up is the on-board SAMD11, not a cable

`J6` is the ARM 10-pin Cortex debug header, `MCU_SWCLK`/`MCU_SWDIO` to `U6`
PA30/PA31. Jumper `user_pmod` 0 pins 1 and 2 -- balls `C9` and `B9`, declared
bidirectional LVCMOS33 -- to it, instantiate this peripheral on that pair, and
the target is a real SW-DP two inches away instead of whatever a Type-C cable
found.

**Read DPIDR and stop.** That SAMD11 is Apollo: halting it takes the board off
USB, and the debug port that would recover it is the one being driven.

## The rates each mode reaches, and the two domains that make them

  * **swd -- 52.5 MHz at speed index 0.** The engine runs in `swd`, a 105 MHz
    domain that is CLKOS2 of the PLL already making `sync`: the VCO stays at
    420 MHz and `CLKOS2_DIV = 4` is 105.000 exactly, so nothing else moves.
    Twelve speeds, 52.5 MHz down to 25.6 kHz, selected by `speed` -- the table
    and where its shape came from are in `peripherals/swd.py`.
  * **serial -- standard baud rates, settable at run time**, in `sync`. `baud`
    is `sync` cycles per bit and 60 MHz divides onto the standard ladder within
    0.16%, so no fractional divisor is needed:

        9600 -> 6250  0.00%      115200 -> 521  -0.03%
       19200 -> 3125  0.00%      230400 -> 260   0.16%
       38400 -> 1562  0.03%      921600 ->  65   0.16%
       57600 -> 1042 -0.03%     1000000 ->  60   0.00%

    3 Mbit/s is 20, 6 Mbit/s is 10, **10 Mbit/s is 6, exact**. The ceiling is
    12 Mbit/s at divisor 5, `AsyncSerialRX`'s own floor for keeping its sample in
    the middle of a bit, and `serial_line.py` refuses less at elaboration.
  * **raw** -- capture runs in `sync`, so 16.7 ns resolution and a 30 MHz
    transition rate before entries merge. Driving is bounded by how fast the CPU
    can store to a CSR, which is a firmware number rather than a pin one.

## Two domains means a crossing, and it is handled rather than assumed away

The CSRs are in `sync` and the SWD engine is in `swd`. Nothing here is
plesiochronous -- both come off the one VCO -- but they are unrelated edges, so:

  * `start` and `line_reset` cross as **pulses**, through `PulseSynchronizer`.
  * `apndp`, `rnw`, `addr` and `wdata` cross as **data behind that handshake**:
    they are CSR values, static long before the pulse arrives, and the engine
    latches them when it starts. Firmware writing them while `busy` is what the
    dropped-command rule below prevents.
  * `done` crosses back as a pulse and sets a sticky flag in `sync`. `ack`,
    `rdata` and `parity_error` are **captured on that flag**, not synchronised
    bit by bit -- a multi-bit value synchronised per bit can be sampled mid-change
    and produce a word that was never on the wire.
  * `busy` is a level and crosses through two flops.
"""

from amaranth import Cat, DomainRenamer, Module, Mux, ResetInserter, Signal
from amaranth.lib import stream, wiring
from amaranth.lib.cdc import FFSynchronizer, PulseSynchronizer
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr

from peripherals.serial_line import SerialLine
from peripherals.swd import SPEED_DIVISORS, SwdHost


__all__ = ["SbuPort", "MODE_RAW", "MODE_SERIAL", "MODE_SWD"]


MODE_RAW    = 0
MODE_SERIAL = 1
MODE_SWD    = 2

# Cycles between transitions, as stored. Saturating rather than wrapping: "at
# least this long" is a usable answer and a wrapped delta is a wrong one.
DELTA_BITS = 30


class SbuPort(wiring.Component):
    """A sideband pin pair behind CSRs, in one of three modes.

        +0x00 mode      RW  1:0 mode, 2 swap, 3 irq_enable
        +0x01 raw_out   RW  1:0 pin values, 3:2 output enables
        +0x02 raw_in    R   1:0 pin levels, 2 capture valid, 3 capture overflow
        +0x03 cmd       W   0 swd start, 1 swd line reset, 2 capture pop,
                            3 capture clear, 4 acknowledge `complete`
        +0x04 swd_ctrl  RW  0 APnDP, 1 RnW, 3:2 A[3:2]
        +0x05 status    R   0 busy, 1 complete, 4:2 ACK, 5 parity error
        +0x06 speed     RW  3:0 SWD speed index, into `swd.SPEED_DIVISORS`
        +0x08 wdata     RW  32
        +0x0c rdata     R   32
        +0x10 capture   R   1:0 levels after the transition, 31:2 `sync` cycles
                            since the previous one
        +0x14 baud      RW  `sync` cycles per serial bit, 18 bits over four
                            addresses. 5 is the floor and anything below it
                            cannot frame.

    A register wider than the bus commits on a write to its LAST address, so a
    partial write of `wdata`, `baud` or any other multi-byte register changes
    nothing -- not part of it.

    Reading `capture` and popping it are two accesses, so a reader that is
    interrupted between them still gets the entry it read.

    Parameters
    ----------
    swd_speeds : tuple of int
        The SWD speed table, `swd` cycles per SWCLK period. Index 0 is what
        `speed` comes up at.
    serial_divisor : int
        `sync` cycles per bit at reset. Settable at run time through `baud`.
    baud_bits : int
        Width of that register. 18 reaches 228 baud, below every standard rate.
    capture_depth : int
        Transitions held before the FIFO overflows and says so.

    Attributes
    ----------
    pin_o, pin_oe : Signal(2), out
    pin_i : Signal(2), in
        The pair, unsynchronised. Bit 0 is SWCLK in SWD mode.
    source, sink : stream.Signature(8)
        Serial mode's bytes. Feed a `StreamBuffer` and a `Uart16550`, which is
        where a serial line's own interrupt comes from -- not from here.
    frame_error, overrun : Signal(), out
        Serial mode's two loss reports, for the 16550's LSR.
    irq : Signal(), out
        LEVEL. A completed SWD transaction, or a capture entry waiting, gated by
        `mode.irq_enable`. One per port, so two ports are two sources -- the
        peripheral is the source, never the pins (`docs/soc-interrupts.md`).
    """

    def __init__(self, *, swd_speeds=SPEED_DIVISORS, serial_divisor=6,
                 baud_bits=18, capture_depth=16):
        self._swd = SwdHost(speeds=swd_speeds)
        self._serial = SerialLine(divisor=serial_divisor,
                                  divisor_bits=baud_bits)
        self._capture_depth = capture_depth
        self._baud_bits = baud_bits

        self._mode = csr.Register({
            "mode":       csr.Field(csr.action.RW, 2),
            "swap":       csr.Field(csr.action.RW, 1),
            "irq_enable": csr.Field(csr.action.RW, 1),
        }, access="rw")
        self._raw_out = csr.Register({
            "value": csr.Field(csr.action.RW, 2),
            "oe":    csr.Field(csr.action.RW, 2),
        }, access="rw")
        self._raw_in = csr.Register({
            "level":    csr.Field(csr.action.R, 2),
            "valid":    csr.Field(csr.action.R, 1),
            "overflow": csr.Field(csr.action.R, 1),
        }, access="r")
        self._cmd = csr.Register({
            "start":         csr.Field(csr.action.W, 1),
            "line_reset":    csr.Field(csr.action.W, 1),
            "capture_pop":   csr.Field(csr.action.W, 1),
            "capture_clear": csr.Field(csr.action.W, 1),
            "ack":           csr.Field(csr.action.W, 1),
        }, access="w")
        self._swd_ctrl = csr.Register({
            "apndp": csr.Field(csr.action.RW, 1),
            "rnw":   csr.Field(csr.action.RW, 1),
            "addr":  csr.Field(csr.action.RW, 2),
        }, access="rw")
        self._speed = csr.Register({
            "index": csr.Field(csr.action.RW, len(swd_speeds).bit_length()),
        }, access="rw")
        self._baud = csr.Register(
            {"value": csr.Field(csr.action.RW, baud_bits, init=serial_divisor)},
            access="rw")
        self._status = csr.Register({
            "busy":         csr.Field(csr.action.R, 1),
            "complete":     csr.Field(csr.action.R, 1),
            "ack":          csr.Field(csr.action.R, 3),
            "parity_error": csr.Field(csr.action.R, 1),
        }, access="r")
        self._wdata = csr.Register({"value": csr.Field(csr.action.RW, 32)},
                                   access="rw")
        self._rdata = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                   access="r")
        self._capture = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                     access="r")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("mode",     self._mode,     offset=0x00)
        builder.add("raw_out",  self._raw_out,  offset=0x01)
        builder.add("raw_in",   self._raw_in,   offset=0x02)
        builder.add("cmd",      self._cmd,      offset=0x03)
        builder.add("swd_ctrl", self._swd_ctrl, offset=0x04)
        builder.add("status",   self._status,   offset=0x05)
        builder.add("speed",    self._speed,    offset=0x06)
        builder.add("wdata",    self._wdata,    offset=0x08)
        builder.add("rdata",    self._rdata,    offset=0x0c)
        builder.add("capture",  self._capture,  offset=0x10)
        builder.add("baud",     self._baud,     offset=0x14)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":         In(csr.Signature(addr_width=5, data_width=8)),
            "irq":         Out(1),

            "pin_o":       Out(2),
            "pin_oe":      Out(2),
            "pin_i":       In(2),

            "source":      Out(stream.Signature(8)),
            "sink":        In(stream.Signature(8)),
            "frame_error": Out(1),
            "overrun":     Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    @property
    def swd(self):
        return self._swd

    @property
    def serial(self):
        return self._serial

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        # The engine is written in `sync` and RUNS in `swd`; its ports are the
        # same signals either way, which is what the crossings below attach to.
        m.submodules.swd    = DomainRenamer("swd")(self._swd)
        m.submodules.serial = serial = self._serial
        connect(m, flipped(self.bus), self._bridge.bus)
        swd = self._swd

        mode = self._mode.f.mode.data
        swap = self._mode.f.swap.data

        # ---- the pads -------------------------------------------------------
        #
        # One synchroniser for both pins, shared by every mode: the engines
        # downstream see a `sync` signal whoever owns the pair. `SerialLine` has
        # its own on top of this, which is deliberate -- its idle qualifier
        # counts what it samples, and taking the count from a different flop than
        # the frame is how a receiver arms mid-character.
        level = Signal(2)
        m.submodules.pin_cdc = FFSynchronizer(self.pin_i, level, init=0b11)

        # Post-swap view, so everything below is written in pin-0/pin-1 terms and
        # the correction exists once.
        pins_in = Signal(2)
        drive_o = Signal(2)
        drive_oe = Signal(2)
        m.d.comb += [
            pins_in.eq(Mux(swap, Cat(level[1], level[0]), level)),
            self.pin_o.eq(Mux(swap, Cat(drive_o[1], drive_o[0]), drive_o)),
            self.pin_oe.eq(Mux(swap, Cat(drive_oe[1], drive_oe[0]), drive_oe)),
        ]

        # The SWD engine takes the pad RAW, before that synchroniser: it samples
        # in `swd` at its own falling edge, and a `sync` flop in front of it
        # would be both the wrong domain and a whole SWCLK period of delay.
        m.d.comb += [
            swd.swdio_i.eq(Mux(swap, self.pin_i[0], self.pin_i[1])),
            serial.rx_i.eq(pins_in[1]),
        ]

        with m.Switch(mode):
            with m.Case(MODE_SERIAL):
                m.d.comb += [
                    drive_o.eq(Cat(serial.tx_o, 0)),
                    drive_oe.eq(Cat(serial.tx_oe, 0)),
                ]
            with m.Case(MODE_SWD):
                m.d.comb += [
                    drive_o.eq(Cat(swd.swclk_o, swd.swdio_o)),
                    # SWCLK is the host's alone and is driven for the whole
                    # transaction, including while it is parked low.
                    drive_oe.eq(Cat(1, swd.swdio_oe)),
                ]
            with m.Default():
                m.d.comb += [
                    drive_o.eq(self._raw_out.f.value.data),
                    drive_oe.eq(self._raw_out.f.oe.data),
                ]

        connect(m, serial.source, flipped(self.source))
        connect(m, flipped(self.sink), serial.sink)
        m.d.comb += [
            self.frame_error.eq(serial.frame_error),
            self.overrun.eq(serial.overrun),
        ]

        # ---- the SWD engine, and the crossing into its domain ----------------
        #
        # The request fields are wired straight across. They are CSR values that
        # firmware sets before it writes `cmd`, so they are static by the time
        # the start pulse arrives two `swd` edges later -- data behind a
        # handshake, which is the one multi-bit crossing that needs no
        # synchroniser.
        in_swd = Signal()
        m.d.comb += in_swd.eq(mode == MODE_SWD)
        m.d.comb += [
            swd.apndp.eq(self._swd_ctrl.f.apndp.data),
            swd.rnw.eq(self._swd_ctrl.f.rnw.data),
            swd.addr.eq(self._swd_ctrl.f.addr.data),
            swd.wdata.eq(self._wdata.f.value.data),
            swd.speed.eq(self._speed.f.index.data),
            serial.divisor.eq(self._baud.f.value.data),
        ]

        busy = Signal()
        m.submodules.busy_cdc = FFSynchronizer(swd.busy, busy)

        # A command that arrives mid-transaction is dropped rather than queued: a
        # queued one would start against whatever `swd_ctrl` held by the time it
        # ran. `busy` is the synchronised copy, so this refuses for two extra
        # `sync` cycles at each end of a transaction -- which is the safe
        # direction, and a transaction is thousands of cycles.
        started = Signal()
        reset_requested = Signal()
        m.d.comb += [
            started.eq(in_swd & ~busy & self._cmd.f.start.w_stb
                       & self._cmd.f.start.w_data),
            reset_requested.eq(in_swd & ~busy & self._cmd.f.line_reset.w_stb
                               & self._cmd.f.line_reset.w_data),
        ]

        m.submodules.start_cdc = start_cdc = PulseSynchronizer("sync", "swd")
        m.submodules.reset_cdc = reset_cdc = PulseSynchronizer("sync", "swd")
        m.submodules.done_cdc  = done_cdc  = PulseSynchronizer("swd", "sync")
        m.d.comb += [
            start_cdc.i.eq(started),
            reset_cdc.i.eq(reset_requested),
            swd.start.eq(start_cdc.o),
            swd.line_reset.eq(reset_cdc.o),
            done_cdc.i.eq(swd.done),
        ]

        # Captured on the arrival of `done`, not synchronised bit by bit: `ack`
        # and `rdata` stop changing before the engine raises `done`, and a
        # per-bit synchroniser sampling a word mid-change delivers one that was
        # never on the wire.
        complete = Signal()
        ack = Signal(3)
        rdata = Signal(32)
        parity_error = Signal()
        acknowledged = Signal()
        m.d.comb += acknowledged.eq(self._cmd.f.ack.w_stb
                                    & self._cmd.f.ack.w_data)

        # Sticky, because `done` is one cycle wide and an interrupt the CPU has
        # not reached yet must still be there when it does. `cmd.ack` clears it,
        # and so does starting the next transaction -- a flag left set from the
        # previous one would make the new one look finished before it was.
        with m.If(acknowledged | started | reset_requested):
            m.d.sync += complete.eq(0)
        with m.Elif(done_cdc.o):
            m.d.sync += [
                complete.eq(1),
                ack.eq(swd.ack),
                rdata.eq(swd.rdata),
                parity_error.eq(swd.parity_error),
            ]

        # ---- raw mode's capture ---------------------------------------------
        #
        # One entry per transition of either pin: the levels after it, and how
        # long the previous levels held. Running only in raw mode is what keeps
        # it a fallback tool rather than a firehose -- an SWD transaction is 100
        # transitions and would fill any FIFO worth building.
        clear = Signal()
        m.d.comb += clear.eq(self._cmd.f.capture_clear.w_stb
                             & self._cmd.f.capture_clear.w_data)
        fifo = SyncFIFOBuffered(width=32, depth=self._capture_depth)
        m.submodules.capture = fifo = ResetInserter(clear)(fifo)

        previous = Signal(2, init=0b11)
        delta    = Signal(DELTA_BITS)
        changed  = Signal()
        overflow = Signal()
        in_raw   = Signal()
        m.d.comb += [
            in_raw.eq(mode == MODE_RAW),
            changed.eq(in_raw & (pins_in != previous)),
            # `delta + 1` counts the transition's own cycle, so a level held N
            # cycles reads N rather than N-1.
            fifo.w_data.eq(Cat(pins_in, delta + 1)),
            fifo.w_en.eq(changed),
            fifo.r_en.eq(self._cmd.f.capture_pop.w_stb
                         & self._cmd.f.capture_pop.w_data),
        ]

        with m.If(changed):
            m.d.sync += [previous.eq(pins_in), delta.eq(0)]
        with m.Elif(delta != (1 << DELTA_BITS) - 1):
            m.d.sync += delta.eq(delta + 1)

        with m.If(clear):
            m.d.sync += overflow.eq(0)
        with m.Elif(changed & ~fifo.w_rdy):
            m.d.sync += overflow.eq(1)

        # ---- what the CPU reads ---------------------------------------------
        m.d.comb += [
            self._raw_in.f.level.r_data.eq(pins_in),
            self._raw_in.f.valid.r_data.eq(fifo.r_rdy),
            self._raw_in.f.overflow.r_data.eq(overflow),
            self._capture.f.value.r_data.eq(fifo.r_data),

            self._status.f.busy.r_data.eq(busy),
            self._status.f.complete.r_data.eq(complete),
            self._status.f.ack.r_data.eq(ack),
            self._status.f.parity_error.r_data.eq(parity_error),
            self._rdata.f.value.r_data.eq(rdata),

            self.irq.eq(self._mode.f.irq_enable.data
                        & ((in_swd & complete) | (in_raw & fifo.r_rdy))),
        ]

        return m
