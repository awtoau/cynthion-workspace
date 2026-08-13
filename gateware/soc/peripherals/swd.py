#!/usr/bin/env python3
#
# SWD on a bidirectional pin pair: the host half, so Cynthion is the probe.
# SPDX-License-Identifier: BSD-3-Clause

"""
The SWD **host** (debug probe) engine. `peripherals/sbu.py` is what selects it.

Everything here is ARM IHI 0074E (ADIv6.0), chapter B4. `sources/README.md` has
the document; section numbers below are that issue's.

## 52.5 MHz, and the speed table under it

**This engine runs in `swd`, a 105 MHz domain of its own**, and divides it. The
speed is an INDEX into a fixed table, which is the shape real probes ship:
OpenOCD's `stlink_usb.c` carries `stlink_khz_to_speed_map_swd[]`, twelve
`{kHz, divisor}` pairs from 4000 down to 5 kHz, and an ST-Link V2 tops out at
4 MHz. Ours starts at **52.5 MHz, thirteen times that**, and reaches 25.6 kHz:

    index   div     SWCLK      index   div      SWCLK
        0     2   52.500 MHz       6     24    4.375 MHz
        1     4   26.250           7     32    3.281
        2     6   17.500           8     64    1.641
        3     8   13.125           9    256    0.410
        4    12    8.750          10   1024    0.103
        5    16    6.562          11   4096    0.026

**The bottom of the range is not decoration.** A target with a slow debug clock,
a long cable or a protection switch in the path needs it, which is why ST goes to
5 kHz.

`swd` is CLKOS2 of PLL0 -- the third output of the VCO that already makes
`sync`, so it costs no bel and **moves nothing**: the VCO stays at 420 MHz and
`CLKOS2_DIV = 4` is 105.000 MHz exactly. CLKOS2 reaches general fabric only,
never an edge clock (#314), which is all this needs.

**What index 0 spends is round-trip time, not fabric.** The host launches at the
falling edge and samples at the next falling edge, so the target's data has one
half period -- 9.5 ns at 52.5 MHz -- to leave the target, cross the link and meet
setup here. That budget is the same one every 50 MHz probe has, and a link that
misses it returns a wrong bit that parity and the acknowledge catch. The answer
is a larger index, not a synchroniser, and that is what the table is for.

**Unproven without a board:** that the 105 MHz domain closes timing, and that the
round trip through two DPO2036 protection switches and a cable fits 9.5 ns. #97
proves DC continuity on the SBU pins and nothing about a fast edge. If 105 MHz
does not close, the fallback is a 52.5 MHz domain with SWCLK forwarded from the
clock itself -- same top speed, a clock-to-pad path instead of a register.

## Prior art, read before this was written

  * **`Wren6991/OpenDAP`, CC0-1.0.** The target side, in Verilog, and the
    authority this design was checked against: its DP registers SWDIO in IO-cell
    flops on the RISING edge of SWCLK, which is B4.3.1 implemented rather than
    quoted. Its `test/common/swd_util.cpp` is a host-side driver, and its phase
    order -- header, 1 hi-Z, 3 ACK, then `hiz_clocks(1)` on a non-OK response,
    32+1 read bits then one turnaround, or one turnaround then 32+1 write bits --
    is bit for bit what this FSM does. Nothing was copied: that is Verilog for
    the other end of the link. It is what makes the phase order evidence rather
    than a reading.
  * **`Wren6991/Hazard3-SWD-SoC`, Apache-2.0**, is the same DP on an ECP5.
  * **`deanm1278/FPGA_SWD`** is master-side but has **no licence file**, so it is
    reference only and was not read for structure.

## We are the HOST, not the target

#518 is "make Cynthion a debug probe over USB-C", so this generates `SWCLK`,
drives the packet request, and reads the target's acknowledge and read data.

**The other choice changes the design, not a parameter.** A target-side SW-DP
takes `SWCLK` as an input -- no divisor, every flop in a domain clocked by the
far end or double-sampled against it -- and it must implement the DP register
file (DPIDR, CTRL/STAT, SELECT, RDBUFF), sticky error flags, WAIT/FAULT
generation, and the line-reset detector. None of that exists here: this side
issues transactions and reports what came back.

## Timing comes from B4.3.1, and it is stated in terms of the TARGET

> "When the target samples SWDIO, sampling is performed on the rising edge of
> SWCLK. When the target drives SWDIO, or stops driving it, signal changes are
> performed on the rising edge of SWCLK."

The words "falling edge" do not appear in IHI 0074E. The host's edge follows
from the target's:

  * **Host drives on the falling edge.** A bit placed at a falling edge is stable
    across the rising edge that the target samples on.
  * **Host samples on the falling edge.** A target-driven bit changes at a rising
    edge and holds to the next, so the falling edge is its mid-cell.

So this design does everything on the falling edge, and `SWCLK` parks LOW between
transactions -- B4.1.1 permits stopping the clock after eight idle cycles.

## Turnaround, B4.1.3 and B4.2

Neither side drives during a turnaround period; `DLCR.TURNROUND` sets its length
and the reset default is one clock. Where the periods fall is per-transaction and
is NOT symmetric:

    write   request -> Trn -> ACK -> Trn -> WDATA      (no Trn after WDATA)
    read    request -> Trn -> ACK ->        RDATA -> Trn

**A read has no turnaround between ACK and RDATA** -- the target drives both --
and **a write has one**. Getting either wrong shifts the data phase by a clock,
which reads back as plausible data with a parity failure rather than as a fault.
A WAIT or FAULT acknowledge ends the transaction after one turnaround: there is
no data phase, because `CTRL/STAT.ORUNDETECT` overrun detection is not driven
from here.

## Parity is EVEN, over the payload only, B4.1.6

  * request: over APnDP, RnW, A[2:3] -- four bits, not the whole eight
  * data: over the 32 data bits
  * **ACK is never covered.** A reader that parity-checks the 36-bit read stream
    as one block gets a wrong answer for every transaction.

## What is not here

  * **Debug Accessory Mode entry.** CC-side FUSB302B work, #518 item 2. Nothing
    in this module depends on a mode having been negotiated.
  * **The JTAG-to-SWD switching sequences** of B5, and the dormant-state exit
    sequence. Bring-up is against a SAMD11 that is not an SWJ-DP behind a mode
    switch, so a line reset is enough to reach DPIDR.
  * **SWD protocol version 2 multi-drop** (TARGETSEL, dormant state).
  * **Pin assignment.** This engine has two named signals and no opinion about
    which balls carry them -- `peripherals/sbu.py` owns the pads, the swap and
    the mode.

## The line is driven, not open-drain

B4.3.1: "Both the target and host can drive the bus HIGH and LOW or tristate it",
with a 100k pull-up at the target that only holds the last state. So `swdio_o`
carries the bit and `swdio_oe` gates it -- unlike `probes/sideband/sideband_link.py`,
whose pad is open-drain because Apollo may drive it at any time. B4.3.1 also
requires both ports to tolerate short contention, which is what makes a
turnaround-period disagreement a wrong answer rather than damage.
"""

from amaranth import Array, Cat, C, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


__all__ = ["SwdHost", "ACK_OK", "ACK_WAIT", "ACK_FAULT", "MIN_DIVISOR"]


# ACK[0:2], as it arrives: ACK[0] first on the wire and therefore in bit 0.
# B4.1.5 -- "the OK response of 0b001 appears on the wire as 1, followed by 0,
# followed by 0". Table B4-1 prints the same three responses in transmission
# order, so its `0b100` for OK is this `0b001` written backwards; a peripheral
# that copies the table's digits reports every response as its opposite.
ACK_OK    = 0b001
ACK_WAIT  = 0b010
ACK_FAULT = 0b100

# Payload bits per data phase: 32 data plus one parity. B4.1.6.
DATA_BITS = 33

# Packet request bits: Start, APnDP, RnW, A[2], A[3], Parity, Stop, Park. B4.2.
REQUEST_BITS = 8

# B4.3.3: a line reset is "holding the data signal HIGH for at least 50 clock
# cycles, followed by at least two idle cycles". This is that floor; the idle
# cycles are the ones every transaction ends with.
LINE_RESET_CLOCKS = 50

# B4.1.1: after a data phase the host may "continue to clock the SWD interface
# with at least eight idle cycles. After completing this sequence, the host can
# stop the clock." SWCLK parks low here, so this is the number that makes parking
# it legal rather than a preference.
IDLE_CYCLES = 8

# Two domain cycles per SWCLK period is the floor for a clock a register can
# generate: one cycle high, one low. That is 52.5 MHz from the 105 MHz `swd`
# domain, and the whole point of having that domain.
MIN_DIVISOR = 2

# The speed table, indexed by `speed`. Divisors of the `swd` domain, spanning
# 2048:1 so a slow target is reachable without a rebuild. See the module
# docstring for the frequencies and where the shape came from.
SPEED_DIVISORS = (2, 4, 6, 8, 12, 16, 24, 32, 64, 256, 1024, 4096)


class SwdHost(wiring.Component):
    """One SWD transaction at a time, at the pads.

    Instantiate under `DomainRenamer("swd")`: written in `sync` and run in the
    105 MHz domain, with the crossings in `peripherals/sbu.py`.

    Parameters
    ----------
    speeds : tuple of int
        Domain cycles per SWCLK period, one per `speed` index, each 2 or more.
        The high phase gets the odd cycle of an odd divisor, since the high phase
        is the round trip's budget.
    turnaround : int
        Clock cycles per turnaround period, 1 to 4 -- the values
        `DLCR.TURNROUND` can encode (B2, table B2-5). **Must match the target's
        DLCR**, and the reset default at both ends is 1.

    Attributes
    ----------
    speed : Signal, in
        Index into `speeds`. Latched at `start`, so a write mid-transaction
        cannot change the clock under a transfer already on the wire.
    start : Signal(), in
        One cycle, while `busy` is low: run one transaction with the request
        inputs below.
    line_reset : Signal(), in
        One cycle, while `busy` is low: 50 clocks with SWDIO high, then the idle
        cycles. B4.3.3.
    apndp, rnw : Signal(), in
    addr : Signal(2), in
        A[3:2] -- bit 0 is A2, which goes on the wire first. Held from `start`
        until `done`.
    wdata : Signal(32), in
        The write payload; ignored when `rnw` is set.
    busy : Signal(), out
    done : Signal(), out
        One cycle at the end of the transaction, including its idle cycles.
    ack : Signal(3), out
        ACK[0:2] as received, so OK reads 0b001. All-zeros or all-ones is a
        target that never drove the line, which the DP cannot answer.
    rdata : Signal(32), out
        Valid at `done` after a read that acknowledged OK.
    parity_error : Signal(), out
        The read payload's even parity did not match the bit that followed it.
        Latched at `done`, cleared at the next `start`.
    swclk_o, swdio_o, swdio_oe : Signal(), out
    swdio_i : Signal(), in
        The pad, unsynchronised. Crossing into `sync` is this module's job.
    """

    def __init__(self, *, speeds=SPEED_DIVISORS, turnaround=1):
        speeds = tuple(speeds)
        if any(divisor < MIN_DIVISOR for divisor in speeds):
            raise ValueError(
                f"every speed must be at least {MIN_DIVISOR} domain cycles per "
                f"SWCLK period -- a register-generated clock needs one cycle "
                f"high and one low, and there is no faster SWCLK than half the "
                f"domain. Got {speeds!r}")
        if not 1 <= turnaround <= 4:
            raise ValueError(
                f"turnaround must be 1 to 4 clocks, the values DLCR.TURNROUND "
                f"can encode, not {turnaround!r}")

        self._speeds     = speeds
        self._turnaround = turnaround

        super().__init__({
            "speed":        In(range(len(speeds))),
            "start":        In(1),
            "line_reset":   In(1),
            "apndp":        In(1),
            "rnw":          In(1),
            "addr":         In(2),
            "wdata":        In(32),

            "busy":         Out(1),
            "done":         Out(1),
            "ack":          Out(3),
            "rdata":        Out(32),
            "parity_error": Out(1),

            "swclk_o":      Out(1),
            "swdio_o":      Out(1),
            "swdio_oe":     Out(1),
            "swdio_i":      In(1),
        })

    @property
    def speeds(self):
        """Domain cycles per SWCLK period, by index."""
        return self._speeds

    @property
    def turnaround(self):
        return self._turnaround

    def table(self, domain_hz):
        """(index, divisor, SWCLK Hz) for every speed, for a docstring or a
        firmware header."""
        return [(index, divisor, domain_hz / divisor)
                for index, divisor in enumerate(self._speeds)]

    def elaborate(self, platform):
        m = Module()

        # NO SYNCHRONISER, and that is the design rather than an omission. The
        # return data is source-synchronous: this module generates SWCLK, so the
        # target's launch edge is one this design made. The sampling flop is the
        # shift register below, clocked at the falling edge -- adding two flops
        # in front would move the sample a whole SWCLK period late at divisor 2.
        # A round trip that misses the window gives a wrong bit, which parity and
        # the acknowledge catch; the fix is a larger divisor, not a synchroniser.
        pad = Signal()
        m.d.comb += pad.eq(self.swdio_i)

        # ---- the clock ------------------------------------------------------
        #
        # The divisor is chosen at run time from the speed table and LATCHED at
        # `start`: a rate that changed mid-transaction would stretch one bit and
        # nothing on the wire would say which. An odd divisor is an asymmetric
        # clock and the odd cycle goes to the high phase, which is the half the
        # round trip has to fit inside. `running` is every state but IDLE, and
        # SWCLK parks low between transactions.
        widest  = max(self._speeds)
        highs   = Array([-(-divisor // 2) for divisor in self._speeds])
        lows    = Array([divisor // 2 for divisor in self._speeds])
        high    = Signal(range(widest + 1), init=1)
        low     = Signal(range(widest + 1), init=1)

        running = Signal()
        swclk   = Signal()
        count   = Signal(range(widest))
        tick    = Signal()
        fall    = Signal()

        m.d.comb += [
            self.swclk_o.eq(swclk),
            tick.eq(count == 0),
            fall.eq(running & tick & swclk),
        ]

        with m.If(~running):
            m.d.sync += [
                count.eq(lows[self.speed] - 1),
                swclk.eq(0),
                high.eq(highs[self.speed]),
                low.eq(lows[self.speed]),
            ]
        with m.Elif(tick):
            m.d.sync += [count.eq(Mux(swclk, low - 1, high - 1)),
                         swclk.eq(~swclk)]
        with m.Else():
            m.d.sync += count.eq(count - 1)

        # ---- the shifters ---------------------------------------------------
        #
        # One bit leaves and one bit arrives per falling edge, so a phase is a
        # bit count and nothing else. `tx[0]` is on the wire; `rx` fills from the
        # top, which leaves the first bit received in bit 0 -- the LSB-first
        # order of B4.1.5, for both ACK and data.
        tx      = Signal(DATA_BITS, init=1)
        rx      = Signal(DATA_BITS)
        left    = Signal(range(max(LINE_RESET_CLOCKS, DATA_BITS) + 1))
        drive   = Signal()
        ack     = Signal(3)

        request_parity = self.apndp ^ self.rnw ^ self.addr[0] ^ self.addr[1]
        request = Cat(C(1, 1),          # Start
                      self.apndp,
                      self.rnw,
                      self.addr[0],     # A[2], first of the pair on the wire
                      self.addr[1],     # A[3]
                      request_parity,
                      C(0, 1),          # Stop, always 0 in synchronous SWD
                      C(1, 1))          # Park, high so the target reads the
                                        # parked line as 1 through its pull-up
        write_payload = Cat(self.wdata, self.wdata.xor())

        m.d.comb += [
            self.swdio_o.eq(tx[0]),
            self.swdio_oe.eq(drive),
            self.ack.eq(ack),
        ]

        with m.FSM():

            with m.State("IDLE"):
                with m.If(self.start | self.line_reset):
                    m.d.sync += [
                        self.parity_error.eq(0),
                        ack.eq(0),
                    ]
                with m.If(self.start):
                    m.d.sync += [tx.eq(request), left.eq(REQUEST_BITS)]
                    m.next = "REQUEST"
                with m.Elif(self.line_reset):
                    # The line held high, so every bit shifted out is a 1.
                    m.d.sync += [tx.eq(C(-1, DATA_BITS)),
                                 left.eq(LINE_RESET_CLOCKS)]
                    m.next = "RESET_HIGH"

            with m.State("REQUEST"):
                m.d.comb += [running.eq(1), drive.eq(1)]
                with m.If(fall):
                    m.d.sync += [tx.eq(tx[1:]), left.eq(left - 1)]
                    with m.If(left == 1):
                        m.d.sync += left.eq(self._turnaround)
                        m.next = "TRN_TO_TARGET"

            with m.State("RESET_HIGH"):
                m.d.comb += [running.eq(1), drive.eq(1)]
                with m.If(fall):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        # B4.3.3 wants idle cycles after the 50 high clocks, and
                        # B4.1.4 idles LOW -- so the shifter has to be cleared.
                        m.d.sync += [tx.eq(0), left.eq(IDLE_CYCLES)]
                        m.next = "IDLE_CYCLES"

            with m.State("TRN_TO_TARGET"):
                m.d.comb += running.eq(1)
                with m.If(fall):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        m.d.sync += left.eq(3)
                        m.next = "ACK"

            # The branch is taken on the same falling edge that clocks in the
            # last ACK bit, from the combinational `ack_now` rather than from the
            # register. At divisor 2 a decision state would be half an SWCLK
            # period, and the read data phase begins on the very next edge.
            with m.State("ACK"):
                ack_now = Signal(3)
                m.d.comb += [running.eq(1), ack_now.eq(Cat(ack[1:], pad))]
                with m.If(fall):
                    m.d.sync += [ack.eq(ack_now), left.eq(left - 1)]
                    with m.If(left == 1):
                        with m.If(ack_now != ACK_OK):
                            # No data phase: overrun detection is not enabled
                            # from here, so B4.2.3 and B4.2.4 end the transaction
                            # after one turnaround. Driving 33 bits at a target
                            # that is not listening would be read as a fresh
                            # packet request.
                            m.d.sync += left.eq(self._turnaround)
                            m.next = "TRN_TO_HOST"
                        with m.Elif(self.rnw):
                            # B4.2.2: no turnaround between ACK and RDATA -- the
                            # target drives both.
                            m.d.sync += [rx.eq(0), left.eq(DATA_BITS)]
                            m.next = "RDATA"
                        with m.Else():
                            m.d.sync += left.eq(self._turnaround)
                            m.next = "TRN_TO_WDATA"

            with m.State("RDATA"):
                m.d.comb += running.eq(1)
                with m.If(fall):
                    m.d.sync += [rx.eq(Cat(rx[1:], pad)), left.eq(left - 1)]
                    with m.If(left == 1):
                        m.d.sync += left.eq(self._turnaround)
                        m.next = "TRN_TO_HOST"

            # B4.2.1: the turnaround a write has and a read does not.
            with m.State("TRN_TO_WDATA"):
                m.d.comb += running.eq(1)
                with m.If(fall):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        m.d.sync += [tx.eq(write_payload), left.eq(DATA_BITS)]
                        m.next = "WDATA"

            with m.State("WDATA"):
                m.d.comb += [running.eq(1), drive.eq(1)]
                with m.If(fall):
                    m.d.sync += [tx.eq(tx[1:]), left.eq(left - 1)]
                    with m.If(left == 1):
                        # No turnaround after WDATA: the host keeps the line.
                        m.d.sync += [tx.eq(0), left.eq(IDLE_CYCLES)]
                        m.next = "IDLE_CYCLES"

            with m.State("TRN_TO_HOST"):
                m.d.comb += running.eq(1)
                with m.If(fall):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        # Idle cycles are clocked with the line LOW, B4.1.4.
                        m.d.sync += [tx.eq(0), left.eq(IDLE_CYCLES)]
                        m.next = "IDLE_CYCLES"
                        # The read payload's parity, checked over the 32 data
                        # bits alone. `rx` is stale for a write or a non-OK
                        # acknowledge, so the result is only meaningful where
                        # `rdata` is.
                        m.d.sync += self.rdata.eq(rx[:32])
                        m.d.sync += self.parity_error.eq(rx[:32].xor() ^ rx[32])

            with m.State("IDLE_CYCLES"):
                m.d.comb += [running.eq(1), drive.eq(1)]
                with m.If(fall):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        m.d.comb += self.done.eq(1)
                        m.d.sync += tx.eq(1)
                        m.next = "IDLE"

        m.d.comb += self.busy.eq(running)

        return m
