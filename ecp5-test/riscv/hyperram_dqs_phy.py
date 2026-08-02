#!/usr/bin/env python3
#
# The ECP5 DQS read path for HyperRAM, wired for this board's pins. See awtoau/cynthion-workspace#92.
# SPDX-License-Identifier: BSD-3-Clause

"""
A source-synchronous PHY: the read strobe travels with the data.

The non-DQS PHY in use today counts a fixed number of cycles after the command
and samples wherever that lands. It works -- 220.2 MB/s, byte-exact -- but the
sampling point is an estimate, and nextpnr reports the path as failing timing
while every word verifies. This one clocks the input registers from RWDS itself
through `DQSBUFM`, so the sample point follows the device rather than a count.

## Replaced, not wrapped -- and only this layer

    HyperRAMDQSInterface   upstream (luna)   protocol: command, latency, burst
    HyperRAMDQSPHY         HERE              ECP5 I/O for these pins
    the pads               platform          r1.4

Upstream's PHY cannot be instantiated on r1.4 at all, for three separate
reasons, so there is nothing to wrap:

  * it assigns `bus.clk` as a single net; the platform declares the HyperRAM
    clock as a `DiffPairs`, and the assignment fails outright;
  * it instantiates `BB` on `bus.dq.io` and `bus.rwds.io`, which exist only on a
    raw (`dir="-"`) request, while `bus.clk`/`bus.cs` are written as if buffered;
  * it never drives `bus.reset` -- the record carries the field and the
    elaboration ignores it, leaving the HyperRAM's RESET# floating.

The layer above is protocol and is upstream's, unchanged. That is the boundary
`docs/upstream-boundary.md` asks for: vendor I/O for this board is
Cynthion-specific and ours; the HyperBus command encoding is not.

## The board is wired for DQS, contrary to what was recorded

`DQSBUFM` is not fabric. It serves one fixed DQS group, and the strobe has to
arrive on that group's designated pin -- no routing reaches `DQSI`. r1.4 puts
RWDS on D1, which prjtrellis tags `LDQS8`, and all eight DQ lines in `LDQ8`/
`LDQSN8`. Same group, same bank.

`scripts/hyperram_dqs_pins.py` checks this against the device database and is
the reason the claim is stated rather than assumed;
`docs/luna_ecp5_fpga/hyperram-detailed.md` previously recorded "unusable -- no
DQS pin group", which is wrong.

## What this needs that the non-DQS path does not

  * **a `fast` domain at 2x `sync`.** `ODDRX2F`/`IDDRX2DQA`/`TSHX2DQA` are 4:1
    gearing primitives and take ECLK. `VariableClockDomainGenerator(...,
    with_fast=True)` solves for it, and solves `usb` to exactly 60 MHz at the
    same time -- which is not optional, because the ULPI PHY does not enumerate
    otherwise and that presents as a dead board rather than a timing error.
  * **a 32-bit data path.** `HyperBusDQSPHY` moves 32 bits per `sync` cycle
    where `HyperBusPHY` moves 16. Anything written against the 16-bit interface
    reads back looking bit-shifted -- see the trap in `hyperram-detailed.md`.

## Two upstream defects this does not fix, and why

`HyperRAMDQSInterface`'s `RECOVERY` state carries `# TODO: implement recovery`
and falls straight through to `IDLE`, so CS can be re-asserted on the next
cycle. And `with m.If(extra_latency | 1)` makes the low-latency branch dead.

Neither is fixed here because both are in the protocol layer, which is not this
file's business, and the second is **correct for this part as configured**: CR0
reads `0x8f2f` with fixed-latency enabled, so the device takes the long latency
on every transaction and the branch upstream forces is the right one. Honouring
RWDS would only pay after CR0 is reprogrammed to variable latency, which is a
change to make deliberately and measure. `hyperram_dqs_top.py` holds the
recovery gap outside the controller instead, where it can be counted.
"""

from amaranth import ClockSignal, Elaboratable, Instance, Module, ResetSignal, Signal

from luna.gateware.interface.psram import HyperBusDQSPHY


# Delay modes, from Lattice's ECP5 sysIO documentation (FPGA-TN-02032).
#
# `DQS_CMD_CLK` is the fixed delay meant for the clock and the command pins of a
# DDR interface -- it lines the outgoing clock up with the outgoing data. It goes
# on CK and CS together; delaying one and not the other moves CS relative to the
# clock edge it is specified against.
DEL_MODE_CMD_CLK = "DQS_CMD_CLK"

# `DQS_ALIGNED_X2` is the input delay that pairs with 4:1 gearing on the read
# path. It is what puts DQ inside the window DQSR90 opens.
DEL_MODE_ALIGNED_X2 = "DQS_ALIGNED_X2"

# How long the DDRDLL settle sequence holds in each of its phases, in `sync`
# cycles. Not a timeout and not tuned here: it is upstream's sequence
# (INIT -> FREEZE -> UPDATE -> UPDATED -> UNPAUSE), and the eight cycles are the
# UDDCNTLN pulse width the primitive requires, which Lattice specifies in cycles
# rather than nanoseconds.
DDRDLL_SETTLE_CYCLES = 8


def _pad(port, index, value):
    """`value`, in the polarity this pad wants, from the resource's own `invert`.

    The platform declares CS and RESET# with `PinsN`, so their pads are active
    low; `dir="-"` hands that back as `port.invert` rather than applying it.
    Reading it here means the polarity lives in ONE place -- the pin map -- and
    upstream's habit of writing `~self.phy.cs` inline is not repeated as a second
    statement of the same fact that can drift from it.
    """
    return ~value if port.invert[index] else value


class HyperRAMDQSPHY(Elaboratable):
    """ECP5 DQS I/O for HyperRAM, driving raw pads.

    Parameters
    ----------
    bus :
        The platform's own `ram` resource, requested with ``dir="-"``::

            bus = platform.request("ram", 0, dir="-")

        so ``bus.clk`` is a `DifferentialPort` and the rest are
        `SingleEndedPort`s. The pin map is not restated here: this PHY needs raw
        pads, not different pins, and a second copy of thirteen ball numbers is
        a thing that goes wrong quietly.

    Attributes
    ----------
    phy : HyperBusDQSPHY
        The 32-bit record `HyperRAMDQSInterface` connects to. Upstream's, so the
        controller above is upstream's unchanged.
    dll_locked : Signal
        DDRDLLA's LOCK. Brought out because upstream keeps it internal, and a
        design that reports a clean read with the DLL unlocked has measured
        nothing -- the delay code the whole read path depends on was never
        valid. Nothing above the PHY can otherwise tell.
    dll_ready : Signal
        The settle sequence has finished and PAUSE is released. No transaction
        should be issued before this.
    """

    def __init__(self, *, bus):
        self.bus = bus
        self.phy = HyperBusDQSPHY()
        self.dll_locked = Signal()
        self.dll_ready = Signal()

    def elaborate(self, platform):
        m = Module()

        # The DDRDLL settle sequence, from upstream. It exists because DDRDLLA's
        # delay code is only valid once it has locked, and the IOLOGIC has to be
        # held in PAUSE until the code has been copied into it.
        pause = Signal()
        freeze = Signal()
        lock = Signal()
        uddcntln = Signal()
        counter = Signal(range(DDRDLL_SETTLE_CYCLES + 1))
        m.d.sync += counter.eq(counter + 1)

        with m.FSM(name="ddrdll"):
            with m.State("INIT"):
                m.d.sync += [pause.eq(1), freeze.eq(0), uddcntln.eq(0)]
                with m.If(lock):
                    m.d.sync += [freeze.eq(1), counter.eq(0)]
                    m.next = "FREEZE"

            with m.State("FREEZE"):
                with m.If(counter == DDRDLL_SETTLE_CYCLES):
                    m.d.sync += [uddcntln.eq(1), counter.eq(0)]
                    m.next = "UPDATE"

            with m.State("UPDATE"):
                with m.If(counter == DDRDLL_SETTLE_CYCLES):
                    m.d.sync += [uddcntln.eq(0), counter.eq(0)]
                    m.next = "UPDATED"

            with m.State("UPDATED"):
                with m.If(counter == DDRDLL_SETTLE_CYCLES):
                    m.d.sync += [pause.eq(0), counter.eq(0)]
                    m.next = "READY"

            with m.State("READY"):
                m.d.comb += self.dll_ready.eq(1)

        m.d.comb += self.dll_locked.eq(lock)

        # RWDS, the strobe. Bidirectional: the device drives it during a read to
        # say when data is valid, and this side drives it during a write as the
        # data mask.
        rwds_o = Signal()
        rwds_oe_n = Signal()
        rwds_in = Signal()

        dqsr90 = Signal()
        dqsw = Signal()
        dqsw270 = Signal()
        ddrdel = Signal()
        readptr = Signal(3)
        writeptr = Signal(3)

        m.submodules += [
            Instance("DDRDLLA",
                i_CLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                i_FREEZE=freeze,
                i_UDDCNTLN=uddcntln,
                o_DDRDEL=ddrdel,
                o_LOCK=lock,
            ),
            Instance("BB",
                i_I=rwds_o,
                i_T=rwds_oe_n,
                o_O=rwds_in,
                io_B=self.bus.rwds.io[0],
            ),
            Instance("TSHX2DQSA",
                i_RST=ResetSignal(),
                i_ECLK=ClockSignal("fast"),
                i_SCLK=ClockSignal(),
                i_DQSW=dqsw,
                i_T0=~self.phy.rwds.e,
                i_T1=~self.phy.rwds.e,
                o_Q=rwds_oe_n,
            ),
            Instance("DQSBUFM",
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),

                i_DQSI=rwds_in,
                i_DDRDEL=ddrdel,
                i_PAUSE=pause,
                i_READ0=self.phy.read[0],
                i_READ1=self.phy.read[1],

                # Which of the read clock's phases samples the incoming burst.
                # Upstream's 0b010, kept: BURSTDET is what tells you whether it
                # is right, and it is brought out below so a sweep can find out
                # on hardware rather than here.
                i_READCLKSEL0=0,
                i_READCLKSEL1=1,
                i_READCLKSEL2=0,

                i_RDLOADN=0,
                i_RDMOVE=0,
                i_RDDIRECTION=1,
                i_WRLOADN=0,
                i_WRMOVE=0,
                i_WRDIRECTION=1,

                o_DQSR90=dqsr90,
                o_DQSW=dqsw,
                o_DQSW270=dqsw270,
                **{f"o_RDPNTR{i}": readptr[i] for i in range(len(readptr))},
                **{f"o_WRPNTR{i}": writeptr[i] for i in range(len(writeptr))},

                o_DATAVALID=self.phy.datavalid,
                o_BURSTDET=self.phy.burstdet,
            ),
        ]

        # The clock, differential.
        #
        # THIS IS THE PART UPSTREAM CANNOT DO ON THIS BOARD. Its PHY writes the
        # DELAYG output straight onto `bus.clk`, which is a `DiffPairs` here.
        # Amaranth drives an LVCMOS33D pair by driving the TRUE pin only and
        # letting the bank generate the complement -- so there is exactly one
        # buffer to instantiate, on `.p`, and `.n` is not driven by fabric at
        # all. Driving `.n` as well would be two drivers on one differential
        # pair, which is a wiring fault rather than an inversion.
        clk_out = Signal()
        clk_delayed = Signal()
        m.submodules += [
            Instance("ODDRX2F",
                i_D0=0,
                i_D1=self.phy.clk_en[1],
                i_D2=0,
                i_D3=self.phy.clk_en[0],
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=clk_out,
            ),
            Instance("DELAYG",
                p_DEL_MODE=DEL_MODE_CMD_CLK,
                i_A=clk_out,
                o_Z=clk_delayed,
            ),
            Instance("OBZ", i_I=clk_delayed, i_T=0, o_O=self.bus.clk.p[0]),
        ]

        # Chip select. All four gearing inputs carry the same value, so this is
        # an ordinary registered output that happens to sit in the IOLOGIC; what
        # it buys is a clock-to-out that tracks the clock's, which is the point
        # of the matched DELAYG.
        cs_pad = _pad(self.bus.cs, 0, self.phy.cs)
        cs_out = Signal()
        cs_delayed = Signal()
        m.submodules += [
            Instance("ODDRX2F",
                i_D0=cs_pad,
                i_D1=cs_pad,
                i_D2=cs_pad,
                i_D3=cs_pad,
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=cs_out,
            ),
            Instance("DELAYG",
                p_DEL_MODE=DEL_MODE_CMD_CLK,
                i_A=cs_out,
                o_Z=cs_delayed,
            ),
            Instance("OBZ", i_I=cs_delayed, i_T=0, o_O=self.bus.cs.io[0]),
        ]

        # RESET#, which upstream leaves floating.
        #
        # `HyperBusDQSPHY` declares `reset` and `HyperRAMDQSPHY.elaborate` never
        # reads it, so a design built on upstream's PHY drives nothing onto the
        # HyperRAM's reset pin. A floating RESET# on a part that is otherwise
        # working is the shape of fault this workspace has learned to distrust:
        # it does not fail, it occasionally forgets.
        m.submodules.reset_buf = Instance(
            "OBZ", i_I=_pad(self.bus.reset, 0, self.phy.reset), i_T=0,
            o_O=self.bus.reset.io[0])

        # RWDS out, during writes, as the data mask.
        m.submodules += [
            Instance("ODDRX2DQSB",
                i_DQSW=dqsw,
                i_D0=self.phy.rwds.o[3],
                i_D1=self.phy.rwds.o[2],
                i_D2=self.phy.rwds.o[1],
                i_D3=self.phy.rwds.o[0],
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=rwds_o,
            ),
        ]

        # The eight data lines. 32 bits of `phy.dq` per `sync` cycle across eight
        # pads is four device edges each, which is the 4:1 gearing these
        # primitives exist for.
        for i in range(8):
            dq_in = Signal(name=f"dq_in{i}")
            dq_in_delayed = Signal(name=f"dq_in_delayed{i}")
            dq_oe_n = Signal(name=f"dq_oe_n{i}")
            dq_o = Signal(name=f"dq_o{i}")

            m.submodules += [
                Instance("BB",
                    i_I=dq_o,
                    i_T=dq_oe_n,
                    o_O=dq_in,
                    io_B=self.bus.dq.io[i],
                ),
                Instance("TSHX2DQA",
                    i_T0=~self.phy.dq.e,
                    i_T1=~self.phy.dq.e,
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_DQSW270=dqsw270,
                    i_RST=ResetSignal(),
                    o_Q=dq_oe_n,
                ),
                Instance("ODDRX2DQA",
                    i_DQSW270=dqsw270,
                    i_D0=self.phy.dq.o[i + 24],
                    i_D1=self.phy.dq.o[i + 16],
                    i_D2=self.phy.dq.o[i + 8],
                    i_D3=self.phy.dq.o[i],
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_RST=ResetSignal(),
                    o_Q=dq_o,
                ),
                Instance("DELAYG",
                    p_DEL_MODE=DEL_MODE_ALIGNED_X2,
                    i_A=dq_in,
                    o_Z=dq_in_delayed,
                ),
                Instance("IDDRX2DQA",
                    i_D=dq_in_delayed,
                    i_DQSR90=dqsr90,
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_RST=ResetSignal(),
                    **{f"i_RDPNTR{j}": readptr[j] for j in range(len(readptr))},
                    **{f"i_WRPNTR{j}": writeptr[j] for j in range(len(writeptr))},
                    o_Q0=self.phy.dq.i[i + 24],
                    o_Q1=self.phy.dq.i[i + 16],
                    o_Q2=self.phy.dq.i[i + 8],
                    o_Q3=self.phy.dq.i[i],
                ),
            ]

        return m
