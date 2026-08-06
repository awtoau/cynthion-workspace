#!/usr/bin/env python3
#
# A standard NS16550A register map in front of a byte stream.
# SPDX-License-Identifier: BSD-3-Clause

"""
A standard NS16550A register map in front of an `amaranth.lib.stream` byte pipe.

Its value is being unremarkable: byte-identical to the NS16550A QEMU's `-M virt`
presents, so `firmware/cynthion-soc/src/uart.rs` and `scripts/soc_test.py` cover
the driver that ships on the board.

## Registers

    +0  RBR (R) / THR (W)  receive / transmit           (DLL when LCR.DLAB)
    +1  IER                interrupt enable             (DLM when LCR.DLAB)
    +2  IIR (R) / FCR (W)  interrupt id / FIFO control
    +3  LCR                line control; bit 7 is DLAB
    +4  MCR                modem control
    +5  LSR (R)            line status -- data ready, transmitter, errors
    +6  MSR (R)            modem status
    +7  SCR                scratch

Fixed 16-byte FIFOs. No 16650/16750 extensions, no enhanced mode.

## Every read that changes state

Three of the eight addresses have a read side effect, and they are the three the
NS16550A defines that way. A driver written for a real part needs none of this
table; it is here so that one written for this part need not infer it.

    read            changes                                    cleared state
    --------------  -----------------------------------------  ----------------
    +0  RBR         pops the receive FIFO                       LSR.DR, once the
                    (DLAB clear only -- with DLAB set this      FIFO is empty
                    address is DLL and pops nothing)
    +2  IIR         clears the transmit-empty interrupt, and    IIR id 0b001
                    only while IIR is reporting it
    +5  LSR         clears OE, PE, FE, BI and the bit 7         IIR id 0b011
                    summary, and the interrupt they raise

Every other read -- IER, LCR, MCR, MSR, SCR, and RBR with DLAB set -- is free of
side effects and may be repeated at any rate.

**A read of LSR destroys what it returns.** Firmware that polls LSR and throws
the value away throws away the only report of an overrun there will ever be:
`read_lsr` in `firmware/cynthion-soc/src/uart.rs` is the single place that driver
reads it, for that reason.

The same table, and a walk of what Linux's 8250 driver would do against this part,
are in `../../docs/chips/ns16550a-console-uart.md`.

## What makes a side-effecting read safe here, and it is not their absence

**Nothing anyone polls shares a 32-bit word with a register whose read changes
state.** LSR is at +5 and RBR at +0, which puts them in different words, so no
widening, prefetch, replay or byte-lane sweep of a poll loop can reach the data
register. That is structural rather than a convention someone has to remember,
and it is the whole reason the standard layout was adopted.

IIR at +2 does share a word with RBR at +0, and what keeps them apart is the bus:
VexiiRiscv's `LsuCachelessWishbonePlugin` drives a single-byte `sel` for a byte
access (`VexiiRiscv.v:7499-7515`), `amaranth_soc.csr.wishbone` strobes only the
lanes `sel` names, and the console sits in a `main=0` PMA region where no cache
line fill reaches it. An earlier version of this peripheral made reading IIR
side-effect free to harden against a widened access instead;
`../../docs/architecture.md` records that decision and its reversal.

## There is no UART in this UART

No baud generator, no start bits, no line. Bytes go in and out on stream ports;
the transport is the instantiator's choice (USB CDC, or a pin via
`peripherals/serial_line.py`). These exist only so a generic driver's setup sequence
succeeds, and each is stored and otherwise ignored:

    DLL/DLM   set no rate; without them a driver would transmit its divisor
    LCR       word length, parity, stop bits -- no bits on a wire to frame
    MCR       except bit 4 LOOP, which routes MCR into MSR (see +6 below)
    MSR       reads a constant "modem ready" so a CTS wait terminates

## Interrupt

`irq` is a LEVEL, held while any enabled condition holds, reported through IIR in
the standard priority order, and wired to `cpu/plic.py`. IER resets to zero, so
a design that polls LSR and ignores `irq` is unaffected.

    IIR id  condition                       enabled by  cleared by
    ------  ------------------------------  ----------  ----------------------
    0b011   an LSR error bit is set         IER.ELSI    reading LSR
    0b010   LSR.DR -- a byte is waiting     IER.ERBFI   reading RBR until empty
    0b001   the transmit FIFO went empty    IER.ETBEI   reading IIR, or THR
    0b110   character timeout               --          no timer, never raised
    0b000   modem status                    --          MSR is constant, never

The transmit-empty interrupt is the one piece of state in the interrupt path. It
is SET when the transmit FIFO becomes empty, and when a driver enables ETBEI while
it already is; it is CLEARED by reading IIR while IIR reports it, or by writing
THR. Deriving it from `LSR.THRE` instead would be simpler and is what this had --
and it would storm, because the standard sequence a driver runs is "take the
interrupt, read IIR, return", and a level would still be asserted at the return.

## Errors

    LSR bit 1  OE  a byte was destroyed before it reached this peripheral
    LSR bit 2  PE  parity  -- nothing here checks parity, so always 0
    LSR bit 3  FE  a frame arrived with no stop bit
    LSR bit 4  BI  break   -- nothing here detects a break, so always 0
    LSR bit 7      any of the four above; derived, not stored

`overrun` and `frame_error` are INPUTS, because this module cannot see either.
`sink.ready` is real backpressure -- a byte offered while the receive FIFO is full
is held rather than dropped -- so a full FIFO here is a stall and not a loss, and
latching OE on it would report an overrun every time the CPU was briefly busy.
Where a byte is actually destroyed is a property of the transport:

  * a USB CDC endpoint NAKs while its buffer is full and the host retries, so
    that path loses nothing and drives neither input.
  * an async serial line has no flow control. `peripherals/serial_line.py` presents a byte
    for one cycle whether or not anything is ready for it, and counts the frames
    it drops for a bad stop bit. `top.py` wires both to these inputs.

Each input is a one-cycle pulse; each latches its bit until LSR is read.

## Buffering is not this module's business

16 bytes because the NS16550A has 16 bytes. Anything deeper belongs between
this peripheral and the transport, sized for that transport --
`peripherals/stream_buffer.py`.

## Instantiating more than one

Nothing here knows its own address: no module state, no fixed base, no name in
the register map.

    console = Uart16550()
    apollo  = Uart16550()

Place each behind its own `WishboneCSRBridge` at its own base address.

Bespoke vs 16550, and the FIFO-depth options, are in `../../docs/architecture.md`.
"""

from amaranth               import Module, Mux, Cat, C, Signal
from amaranth.lib           import wiring, stream
from amaranth.lib.fifo      import SyncFIFOBuffered
from amaranth.lib.wiring    import In, Out
from amaranth.hdl           import ResetInserter

from amaranth_soc           import csr


__all__ = ["Uart16550", "SplitRW"]


# The NS16550A's FIFO depth, and not a parameter.
#
# Making it adjustable would mean firmware had to discover it, which is what the
# 16650/16750 depth registers exist for and why nothing agrees about them. 16 is
# what every driver already assumes when it sees a 16550A in IIR, and 16 entries
# of 8 bits map to distributed LUT RAM on the ECP5 rather than to a DP16KD, so
# the cost of being standard here is a few LUTs.
FIFO_DEPTH = 16


class SplitRW(csr.FieldAction):
    """A field whose read side and write side are different registers.

    The 16550 defines three addresses this way -- RBR/THR, IER/DLM (by DLAB) and
    IIR/FCR -- and `amaranth_soc.csr.action` has no action for it: `R` and `W`
    are one-directional and `RW` owns its own storage, so none of them can
    express "reading here and writing here reach different hardware".

    Exposes both directions raw, so the peripheral decides what a read returns
    and what a write does. In particular `r_stb` is a *strobe*: the RBR path uses
    it to pop the receive FIFO and the IIR path to clear the transmit-empty
    interrupt. Which reads change what is the table in the module docstring.
    """

    def __init__(self, shape):
        super().__init__(shape, access="rw", members={
            "r_data": In(shape),
            "r_stb":  Out(1),
            "w_data": Out(shape),
            "w_stb":  Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [
            self.port.r_data.eq(self.r_data),
            self.r_stb.eq(self.port.r_stb),
            self.w_data.eq(self.port.w_data),
            self.w_stb.eq(self.port.w_stb),
        ]
        return m


class Uart16550(wiring.Component):
    """An NS16550A register map in front of a stream source and sink.

    Attributes
    ----------
    bus : csr.Interface(addr_width=3, data_width=8)
        The eight byte-wide registers. Add through a `WishboneCSRBridge`.
    source : stream.Signature(8), out
        Bytes written to THR, in order. Attach to whatever transmits.
    sink : stream.Signature(8), in
        Bytes to be delivered to RBR. Attach to whatever receives.
    overrun : Signal(), in
        One cycle per byte the transport destroyed on its way here. Latches
        LSR.OE. Leave unconnected on a transport that cannot lose a byte.
    frame_error : Signal(), in
        One cycle per frame the transport dropped for a bad stop bit. Latches
        LSR.FE. Leave unconnected on a transport that has no frames.
    irq : Signal(), out
        The interrupt request, active high and LEVEL sensitive. Attach to a
        source input of `vexii_plic.Plic`, or leave it unconnected and poll LSR
        as before -- it costs nothing when nothing reads it, and IER resets to
        zero, so this line is low until a driver asks for it.

    The bus, the FIFOs and both stream ports are all in the `sync` domain. If a
    transport runs in another domain, cross it *outside* this module -- see
    `stream_buffer.StreamBuffer`, which is what a crossing costs and where it
    belongs.
    """

    def __init__(self):
        # +0. RBR on read, THR on write -- and DLL in both directions when
        # LCR.DLAB is set, which is why this cannot be a plain W register.
        self._rbr_thr = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +1. IER, or DLM when DLAB is set. Both are storage, but which storage
        # depends on a bit in another register, so the routing is done here
        # rather than by a csr.action.RW that would own the wrong one.
        self._ier_dlm = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +2. IIR on read, FCR on write. Genuinely different registers on a real
        # part too; this is not an economy.
        self._iir_fcr = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +3. LCR. Only bit 7 (DLAB) does anything; the rest is stored so a
        # driver that writes 8N1 and reads it back is satisfied.
        self._lcr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")
        # +4. MCR. Entirely stored and ignored -- there are no modem pins.
        self._mcr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")
        # +5. LSR. Read-only, and by the standard map in the SECOND 32-bit word --
        # four bytes clear of RBR at +0, which is what makes a poll loop unable to
        # reach the data register whatever the bus does to the access. Do not
        # move it.
        #
        # Reading it clears the error bits, so `csr.action.R` is used for its
        # `r_stb` as much as for its read path.
        self._lsr = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                 access="r")
        # +6. MSR, a constant. See the module docstring.
        self._msr = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                 access="r")
        # +7. SCR, eight bits of scratch. The classic "is there a UART here"
        # probe: write a value, read it back. Costs eight flops and answers a
        # question that has otherwise needed a bitstream rebuild to ask.
        self._scr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")

        # addr_width=3 -- exactly the eight registers, so the window is 8 bytes
        # and the decoder cannot silently alias a ninth address onto RBR.
        builder = csr.Builder(addr_width=3, data_width=8)
        builder.add("rbr_thr", self._rbr_thr)
        builder.add("ier",     self._ier_dlm)
        builder.add("iir_fcr", self._iir_fcr)
        builder.add("lcr",     self._lcr)
        builder.add("mcr",     self._mcr)
        builder.add("lsr",     self._lsr)
        builder.add("msr",     self._msr)
        builder.add("scr",     self._scr)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":         In(csr.Signature(addr_width=3, data_width=8)),
            "source":      Out(stream.Signature(8)),
            "sink":        In(stream.Signature(8)),
            "overrun":     In(1),
            "frame_error": In(1),
            "irq":         Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        # FCR bits 1 and 2 clear the receive and transmit FIFOs. Amaranth's FIFOs
        # have no clear input, so the clear is a one-cycle synchronous reset of
        # the whole FIFO through ResetInserter -- which is exactly what "clear"
        # means for a structure whose entire state is its pointers and level.
        #
        # One cycle is enough because `w_stb` from the CSR multiplexer is already
        # registered and lasts exactly one cycle, and every pointer in the FIFO
        # is in the same domain as that strobe.
        rx_clear = Signal()
        tx_clear = Signal()

        # 16 deep, per the standard. SyncFIFOBuffered rather than SyncFIFO: the
        # buffered variant registers its output, so `r_data` is a flop rather
        # than a memory read port feeding the CSR read mux, and the read path
        # through the bus bridge stays short. At this depth the storage is
        # distributed LUT RAM on the ECP5, not a block RAM.
        rx = ResetInserter(rx_clear)(SyncFIFOBuffered(width=8, depth=FIFO_DEPTH))
        tx = ResetInserter(tx_clear)(SyncFIFOBuffered(width=8, depth=FIFO_DEPTH))
        m.submodules.rx = rx
        m.submodules.tx = tx

        # The divisor latches. Written, read back, and connected to nothing.
        # Init 1 rather than 0 because 0 is "divisor unset" to some drivers and
        # 1 is the fastest legal value, which is the closest thing to the truth
        # here: this pipe runs at whatever the transport runs at.
        dll = Signal(8, init=1)
        dlm = Signal(8, init=0)

        # Interrupt enable. Bits 0, 1 and 2 drive `irq`; bit 3 is stored only.
        #
        #   bit 0  ERBFI  received data available
        #   bit 1  ETBEI  transmit holding register empty
        #   bit 2  ELSI   receiver line status -- an LSR error bit
        #   bit 3  EDSSI  modem status -- MSR is a constant, so this enables
        #                 nothing
        ier = Signal(8)

        # FCR bit 0. Reported back through IIR bits 7:6 so a driver's FIFO probe
        # (write FCR=1, read IIR, look for 0b11 in the top bits) identifies a
        # 16550A. The FIFOs are always present and always 16 deep whatever this
        # says -- clearing it does not turn them into a one-byte holding
        # register, because a one-byte mode would be a second data path to get
        # wrong for the benefit of software written before 1987.
        fifo_en = Signal(init=0)

        dlab = self._lcr.f.data.data[7]

        # ---- +0  RBR / THR / DLL -------------------------------------------
        thr = self._rbr_thr.f.data
        m.d.comb += [
            tx.w_data.eq(thr.w_data),
            # DLAB gates the FIFO push. Without this, a driver programming the
            # baud divisor transmits two junk characters, which on a console is
            # a corrupt banner and on a protocol is a framing error -- and the
            # driver is doing nothing wrong.
            tx.w_en.eq(thr.w_stb & ~dlab),

            # The side-effecting read a data register is defined to have, and the
            # one every driver knows about. Reading with DLAB set returns the
            # divisor and pops nothing. The other two are IIR and LSR; the table
            # is in the module docstring.
            thr.r_data.eq(Mux(dlab, dll, rx.r_data)),
            rx.r_en.eq(thr.r_stb & ~dlab),
        ]
        with m.If(thr.w_stb & dlab):
            m.d.sync += dll.eq(thr.w_data)

        # ---- +1  IER / DLM --------------------------------------------------
        ier_reg = self._ier_dlm.f.data
        m.d.comb += ier_reg.r_data.eq(Mux(dlab, dlm, ier))
        with m.If(ier_reg.w_stb):
            with m.If(dlab):
                m.d.sync += dlm.eq(ier_reg.w_data)
            with m.Else():
                m.d.sync += ier.eq(ier_reg.w_data)

        # ---- the transmitter's own idea of empty ------------------------------
        #
        # `level == 0`, not `~r_rdy`. SyncFIFOBuffered registers its output, so a
        # byte written on one cycle does not reach `r_rdy` until the cycle after
        # next, while `level` counts it one cycle after the write. Both are
        # honest about the FIFO; the difference is a window in which a driver
        # that wrote a byte and immediately polled would be told the transmitter
        # was empty. `level` closes it.
        tx_empty = Signal()
        m.d.comb += tx_empty.eq(tx.level == 0)

        # ---- the receive-error latches ---------------------------------------
        #
        # Set by a one-cycle pulse from the transport, held until LSR is read.
        # SET BEATS CLEAR: an error arriving on the same cycle as the read is
        # kept for the next reader rather than falling between the two, which is
        # the whole reason for reporting errors at all.
        #
        # Nothing drives PE or BI: no parity is ever checked here and no break is
        # ever detected, so those two bits are wired to zero rather than given
        # flops that could only ever hold zero.
        lsr_read = self._lsr.f.data.r_stb
        overrun_seen = Signal()
        frame_seen = Signal()
        m.d.sync += [
            overrun_seen.eq(self.overrun     | (overrun_seen & ~lsr_read)),
            frame_seen.eq  (self.frame_error | (frame_seen   & ~lsr_read)),
        ]
        line_status = Signal()
        m.d.comb += line_status.eq(overrun_seen | frame_seen)

        # ---- the interrupt ---------------------------------------------------
        #
        # Three conditions, each ANDed with its IER bit:
        #
        #   err_pending  IER.ELSI  and an LSR error bit is set
        #   rx_pending   IER.ERBFI and a byte is waiting        (LSR.DR)
        #   tx_pending   IER.ETBEI and the transmit FIFO emptied
        #
        # LEVEL SENSITIVE, held for as long as a condition holds. The PLIC in
        # front of this expects a level (`cpu/plic.py`), and so does a handler
        # that drains only part of a FIFO: an edge would mean the remaining
        # bytes are never announced, giving a console that accepts one burst and
        # then appears to hang.
        #
        # Each condition is cleared by the read the standard says clears it --
        # LSR, RBR and IIR respectively -- so a handler that follows the standard
        # sequence lowers the line without having to know anything about this
        # implementation.
        err_pending = Signal()
        rx_pending = Signal()
        tx_pending = Signal()
        thre_int = Signal(init=1)
        m.d.comb += [
            err_pending.eq(ier[2] & line_status),
            rx_pending.eq(ier[0] & rx.r_rdy),
            tx_pending.eq(ier[1] & thre_int),
            self.irq.eq(err_pending | rx_pending | tx_pending),
        ]

        # ---- +2  IIR (R) / FCR (W) ------------------------------------------
        iir = self._iir_fcr.f.data

        # The interrupt id, per the standard's priority order. Three of the five
        # can be raised here, so the order is: line status, then receive, then
        # transmit.
        #
        #   0b011  receiver line status
        #   0b010  received data available
        #   0b110  character timeout     -- no timer, never raised
        #   0b001  transmit holding register empty
        #   0b000  modem status          -- MSR is a constant, never raised
        #
        # With nothing pending the whole low nibble reads 0b0001, which is the
        # standard's "none" entry -- so the id bits go to zero rather than
        # holding the last winner. A part that leaves 0b001 there while bit 0
        # says "no interrupt" reads back 0xc3 at rest, which is not a value any
        # NS16550A produces.
        iir_id = Signal(3)
        m.d.comb += iir_id.eq(Mux(err_pending, 0b011,
                                  Mux(rx_pending, 0b010,
                                      Mux(tx_pending, 0b001, 0b000))))

        m.d.comb += [
            # Bit 0 CLEAR means an interrupt is pending -- the sense is
            # inverted, and getting it the wrong way round gives a driver that
            # services an interrupt it was never told about.
            # Bits 3:1 are the id, and zero while bit 0 is set.
            # Bits 5:4 are always zero. Bits 7:6 mirror the FIFO-enable bit,
            # which is how a driver tells a 16550A from a 16550 or a 16450.
            iir.r_data.eq(Cat(~self.irq, iir_id, C(0, 2), fifo_en, fifo_en)),

            # A clear is a strobe, not a stored bit: FCR bits 1 and 2 are
            # self-clearing on a real part and nothing reads them back.
            rx_clear.eq(iir.w_stb & iir.w_data[1]),
            tx_clear.eq(iir.w_stb & iir.w_data[2]),
        ]
        with m.If(iir.w_stb):
            m.d.sync += fifo_en.eq(iir.w_data[0])

        # ---- the transmit-empty interrupt ------------------------------------
        #
        # Latched state, and the standard's rules for it verbatim:
        #
        #   * writing THR clears it -- there is now a byte to send
        #   * the transmit FIFO going empty sets it
        #   * reading IIR clears it, but ONLY while IIR is reporting it, so a
        #     read taken for a receive interrupt does not silently swallow a
        #     transmit one
        #   * enabling ETBEI while the FIFO is already empty sets it, or a driver
        #     whose first act is to enable transmit interrupts would wait forever
        #     for an edge that already happened
        #
        # A driver may therefore do what every 16550 driver does: take the
        # interrupt, read IIR, write what it has, return. Deriving this from
        # LSR.THRE instead -- which this peripheral did -- makes that sequence an
        # interrupt storm, because the level is still asserted at the return.
        tx_was_empty = Signal(init=1)
        m.d.sync += tx_was_empty.eq(tx_empty)

        with m.If(thr.w_stb & ~dlab):
            m.d.sync += thre_int.eq(0)
        with m.Elif(tx_empty & ~tx_was_empty):
            m.d.sync += thre_int.eq(1)
        with m.Elif(iir.r_stb & tx_pending & ~rx_pending & ~err_pending):
            m.d.sync += thre_int.eq(0)
        with m.Elif(ier_reg.w_stb & ~dlab & ier_reg.w_data[1] & tx_empty):
            m.d.sync += thre_int.eq(1)

        # ---- +5  LSR --------------------------------------------------------
        #
        #   bit 0  DR    a byte is waiting in the receive FIFO
        #   bit 1  OE    a byte was destroyed before it reached this peripheral
        #   bit 2  PE    parity  -- nothing checks parity here, so 0
        #   bit 3  FE    a frame arrived with no stop bit
        #   bit 4  BI    break   -- nothing detects a break here, so 0
        #   bit 5  THRE  the transmit FIFO is EMPTY. Not "has room": the standard
        #                promises a driver that sees this set may write a whole
        #                FIFO, and Linux's 8250 does exactly that
        #                (`up->tx_loadsz` bytes in serial8250_tx_chars). Reporting
        #                space-for-one here would lose the other fifteen.
        #   bit 6  TEMT  the transmitter is empty. There is no shift register
        #                below the FIFO, so this is the same condition as THRE --
        #                "the last byte has been handed to the transport", not
        #                that it has arrived.
        #   bit 7        any of bits 1..4; derived, so it clears with them.
        #
        # Bits 1..4 and bit 7 are cleared by this read. See the table in the
        # module docstring, and `Uart::read_lsr` in the firmware for the one
        # place that is allowed to consume them.
        m.d.comb += self._lsr.f.data.r_data.eq(
            Cat(rx.r_rdy, overrun_seen, C(0, 1), frame_seen, C(0, 1),
                tx_empty, tx_empty, line_status))

        # ---- +6  MSR --------------------------------------------------------
        #
        # Normally CTS, DSR and DCD asserted; RI clear; no delta bits. A driver
        # that waits for CTS before transmitting, or treats DCD low as "carrier
        # lost" and closes the port, would otherwise hang against a peripheral
        # that has no modem pins to report on.
        #
        # WITH MCR.LOOP SET, MSR REPORTS MCR, which is the one part of loopback
        # mode that is not decoration. Linux's `autoconfig` opens with:
        #
        #     MCR = LOOP | OUT2 | RTS;  if ((MSR & 0xf0) != (DCD | CTS)) return;
        #
        # -- "check to see if a UART is really there" (`8250_port.c`). A constant
        # MSR fails it and the port is not found AT ALL, which is a worse outcome
        # than any wrong modem bit. Four wires buy the whole probe.
        #
        #   MCR bit 0 DTR  -> MSR bit 5 DSR
        #   MCR bit 1 RTS  -> MSR bit 4 CTS
        #   MCR bit 2 OUT1 -> MSR bit 6 RI
        #   MCR bit 3 OUT2 -> MSR bit 7 DCD
        #
        # The DATA half of loopback -- THR looping back into RBR -- is not
        # implemented, and the delta bits 3:0 stay zero. What that costs is
        # `autoconfig_irq`, which uses both to discover an unknown interrupt line;
        # a driver told its interrupt by a device tree, which is every driver this
        # SoC will meet, never runs it.
        mcr = self._mcr.f.data.data
        m.d.comb += self._msr.f.data.r_data.eq(
            Mux(mcr[4],
                Cat(C(0, 4), mcr[1], mcr[0], mcr[2], mcr[3]),
                0xb0))

        # ---- the stream ports ------------------------------------------------
        m.d.comb += [
            self.source.payload.eq(tx.r_data),
            self.source.valid.eq(tx.r_rdy),
            tx.r_en.eq(self.source.ready),

            rx.w_data.eq(self.sink.payload),
            rx.w_en.eq(self.sink.valid),
            # Backpressure, not silent loss: a byte offered while the FIFO is
            # full is HELD by the producer, so nothing is destroyed here and
            # nothing sets LSR.OE here. Overrun is reported by the transport, on
            # the `overrun` input, because the transport is where a byte can
            # actually be lost. See the module docstring, and stream_buffer.py
            # for the elastic buffering that keeps it rare.
            self.sink.ready.eq(rx.w_rdy),
        ]

        return m
