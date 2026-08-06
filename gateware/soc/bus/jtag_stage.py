#!/usr/bin/env python3
#
# A streaming JTAG sink that writes a firmware image into HyperRAM.
# SPDX-License-Identifier: BSD-3-Clause

"""
Stages a firmware image into HyperRAM over the ECP5's user JTAG.

    host --one DR shift--> ER1 --FIFO--> HyperRAM --bootloader--> block RAM --> run

One `chain.shift_data()` carries the whole image, the way `LSC_BITSTREAM_BURST`
carries a bitstream. The CPU is held in reset for the duration, so a board whose
console is wedged is still reloadable.

## The two user instructions

    IR    value  claimed by
    ----  -----  --------------------------------------------
    ER1   0x32   this sink
    ER2   0x38   the RISC-V debug module (`cpu/cpu.py`)

`JTAGG` is a singleton -- one per die -- and presents both. `UserJTAG` instantiates
it once and hands out each tap; nothing else in this tree may instantiate another.
ER1 is otherwise unclaimed here: Apollo's `ECP5_JTAGDebugSPIConnection` speaks it,
but only to gateware that implements LUNA's debug SPI, and this SoC does not.
Configuration and flash go through the ECP5's own opcodes, not ER1.

## Frame

Every ER1 data shift is one frame. Bits arrive LSB first, which is what
`chain.shift_data(tdi=..., byteorder="little")` produces.

    bit    field
    -----  -------------------------------------
    0..7   command
    8..    16-bit words, low bit first

    command  name    word 0         word 1          words 2..
    -------  ------  -------------  --------------  ------------------
    0x00     nop     --             --              --
    0xa1     reset   bit 0 holds    --              --
                     the CPU
    0xa2     write   address[15:0]  address[31:16]  image, one word each

Addresses are HyperRAM 16-bit word addresses, matching `HyperRAMBoot`. `nop` exists
so the status below can be read without writing, and so a frame of all-zeroes or
all-ones -- what an idle or floating chain shifts -- does nothing.

## Status, shifted out on TDO during every frame

    bit     field
    ------  ------------------------------------------------
    0..15   0x4a53, a signature: proves ER1 reached this sink
    16..47  image words accepted since the last `write` began
    48      overflow, sticky: a word arrived with the FIFO full
    49      busy: words are still on their way to HyperRAM
    50      the CPU is held in reset
    51..58  the last command byte decoded
    59..63  frames decoded, modulo 32

The register is reloaded on every JTCK edge outside a shift and shifts LSB first
during one, so TDO carries the state of the moment the frame began -- including,
usefully, what the frame before it did.

**Nothing here has a read side effect.** `staged` and `overflow` are cleared by a
`write` frame and by nothing else, so a `nop` may be repeated at any rate and answers
the same each time. The last two fields exist because a sink that answers with a
plausible-looking signature and a wrong command byte is the failure this design
actually had, and no other field distinguishes it.

## Rates

    stage                   rate
    ----------------------  ---------------------------------------
    Apollo's JTAG SCK       12 MHz -> 750 kword/s (`firmware/src/jtag.c:904`)
    one HyperRAM write      ~22 `sync` cycles -> 2.7 Mword/s at 60 MHz

The sink drains 3.6x faster than JTAG can fill it, so the FIFO is a clock crossing
and a jitter buffer rather than a store, and 32 entries is generous. A JTAG shift
cannot be stalled, so `overflow` records the case where that margin is wrong
instead of losing words silently.

Measured end to end by `scripts/soc_jtag_stage.py`: 32 KiB in 85 ms, 377 KiB/s. For
comparison, on the same board in the same session a register interface moves a 16-bit
word in 28 ms and `apollo configure` moves a bitstream at 294 KiB/s.
"""

from amaranth           import Cat, ClockDomain, ClockSignal, Const, Elaboratable
from amaranth           import Instance, Module, Mux, ResetSignal, Signal
from amaranth.lib.cdc   import FFSynchronizer
from amaranth.lib.fifo  import AsyncFIFO

# Frame commands. Neither is 0x00 or 0xff, so an idle chain -- which shifts one or
# the other -- decodes as `nop` and does nothing.
CMD_NOP   = 0x00
CMD_RESET = 0xa1
CMD_WRITE = 0xa2

# Status signature. Distinct from the magic in `firmware/cynthion-soc/src/hyperram.rs`:
# that one identifies a staged image, this one identifies the sink.
SIGNATURE = 0x4a53

# FIFO tags. An entry carries 16 bits of payload plus which of these it is, so the
# start address and the image travel in one ordered stream and no address can
# overtake the words it applies to.
TAG_DATA    = 0
TAG_ADDR_LO = 1
TAG_ADDR_HI = 2

# What Apollo clocks TCK at, plus headroom. Constraining it stops nextpnr reporting
# a path in this domain as the design's critical path when it is nowhere near it.
JTCK_CONSTRAINT_HZ = 20e6


class UserJTAG(Elaboratable):
    """The ECP5's user JTAG taps, from the one `JTAGG` the die has.

    Attributes
    ----------
    tck, tdi, shift, update, rstn : Signal, output
        Shared by both taps. `rstn` is active low.
    ce1, tdo1 : Signal
        The ER1 tap: `ce1` is high while ER1 is selected, `tdo1` is driven back out.
    ce2, tdo2 : Signal
        The ER2 tap, likewise.
    """

    def __init__(self):
        self.tck    = Signal()
        self.tdi    = Signal()
        self.shift  = Signal()
        self.update = Signal()
        self.rstn   = Signal()

        self.ce1  = Signal()
        self.tdo1 = Signal()
        self.ce2  = Signal()
        self.tdo2 = Signal()

    def elaborate(self, platform):
        m = Module()

        m.submodules.jtagg = Instance(
            "JTAGG",
            o_JTCK=self.tck,
            o_JTDI=self.tdi,
            o_JSHIFT=self.shift,
            o_JUPDATE=self.update,
            o_JRSTN=self.rstn,
            o_JCE1=self.ce1,
            i_JTDO1=self.tdo1,
            o_JCE2=self.ce2,
            i_JTDO2=self.tdo2,
        )

        if platform is not None:
            platform.add_clock_constraint(self.tck, JTCK_CONSTRAINT_HZ)

        return m


class JTAGStager(Elaboratable):
    """The ER1 frame decoder, its FIFO, and the HyperRAM write engine behind it.

    Attributes
    ----------
    tck, tdi, ce, shift : Signal, input
        The ER1 tap from `UserJTAG`.
    tdo : Signal, output
        Status, LSB first.
    req, addr, data : Signal, output
        A HyperRAM write request, held until `ack`. Same shape as the arbiter side
        of `HyperRAMBoot`, so `BootRAM` serves both the same way.
    ack : Signal, input
        The word was taken.
    cpu_reset : Signal, output
        Drive `VexiiRiscv.ext_reset` with this. In `sync`, so it is usable directly.
    """

    def __init__(self, *, depth=32):
        self._depth = depth

        self.tck   = Signal()
        self.tdi   = Signal()
        self.ce    = Signal()
        self.shift = Signal()
        self.tdo   = Signal()

        self.req  = Signal()
        self.addr = Signal(32)
        # 32 bits: two 16-bit wire words paired up. The link stays 16-bit; see
        # the pairing FSM in `elaborate`.
        self.data = Signal(32)
        self.ack  = Signal()

        self.cpu_reset = Signal()

    def elaborate(self, platform):
        m = Module()

        # A clock domain on TCK, which runs only while the host is clocking the chain.
        #
        # **The FALLING edge, and this is not a style choice.** The ECP5's TAP samples
        # TDI on the rising edge of TCK and JTAGG passes it out as JTDI, so JTDI,
        # JSHIFT and JCE1 all change on the rising edge -- sampling them there is a
        # hold-time race with no timing arc for nextpnr to close. It does not fail
        # cleanly: it fails per placement. Two builds of identical RTL decoded the
        # same command byte as 0xa2 and 0xc4, and in the 0xa2 build one comparison of
        # that byte read true while another read false in the same cycle, because
        # each saw `tdi` through a different amount of routing.
        #
        # On the falling edge every JTAGG output has been stable for half a TCK. TDO
        # is correct on the same edge for the mirror-image reason: JTAGG registers
        # JTDO1 on the falling edge, capturing the value from before it, which is the
        # bit the host is about to sample. `luna.gateware.interface.jtag` makes the
        # same choice on the same primitive.
        #
        # Asynchronously reset from `sync`, so `cpu_hold` is known to be clear at
        # power-up. A synchronous reset could not do that: with no JTAG attached there
        # is no edge to apply it on, and a board that came up with its CPU held in
        # reset would look identical to a dead one.
        m.domains.jtck = ClockDomain("jtck", local=True, async_reset=True,
                                     clk_edge="neg")
        m.d.comb += [
            ClockSignal("jtck").eq(self.tck),
            ResetSignal("jtck").eq(ResetSignal("sync")),
        ]

        m.submodules.fifo = fifo = AsyncFIFO(
            width=18, depth=self._depth, w_domain="jtck", r_domain="sync")

        # High while the TAP is shifting ER1 data. Everything else -- capture, update,
        # pause, idle -- is "not shifting", so the frame decoder depends on nothing
        # about JCE1 beyond this one property.
        active = Signal()
        m.d.comb += active.eq(self.ce & self.shift)

        # The same thing, one falling edge later, and the input path uses it.
        #
        # JSHIFT and JCE1 are decodes of the TAP state, which advances on the rising
        # edge; JTDI is the bit the TAP sampled on that same edge. So at a falling
        # edge, `active` describes the bit being clocked NOW while `tdi` still holds
        # the one before it. Capturing `tdi` under `active` therefore takes one
        # garbage bit ahead of the payload and shifts the whole frame: the command
        # byte decodes as 0x44 instead of 0xa2, and nothing else can be right after
        # that. `shifting` lines the enable up with the data.
        #
        # The output path deliberately does NOT use it -- see `tdo_sr` below.
        shifting = Signal()
        m.d.jtck += shifting.eq(active)

        command  = Signal(8)
        have_cmd = Signal()
        cmd_sr   = Signal(8)
        word_sr  = Signal(16)

        # Bits within the current field, then words within the frame. `word_index`
        # saturates at 2: past the address the tag no longer changes.
        bit_index  = Signal(4)
        word_index = Signal(2)

        staged   = Signal(32)
        overflow = Signal()
        cpu_hold = Signal()

        # The bit on TDI has not been clocked in yet, so the completed field is the
        # register shifted by one with TDI on the end.
        cmd_now  = Cat(cmd_sr[1:8], self.tdi)
        word_now = Cat(word_sr[1:16], self.tdi)

        tag = Signal(2)
        m.d.comb += tag.eq(Mux(word_index == 0, TAG_ADDR_LO,
                               Mux(word_index == 1, TAG_ADDR_HI, TAG_DATA)))

        word_done = Signal()
        m.d.comb += word_done.eq(shifting & have_cmd & (bit_index == 15))

        m.d.comb += [
            fifo.w_data.eq(Cat(word_now, tag)),
            fifo.w_en.eq(word_done & (command == CMD_WRITE)),
        ]

        busy = Signal()
        busy_jtck = Signal()
        m.submodules.busy_cdc = FFSynchronizer(busy, busy_jtck, o_domain="jtck")

        frames = Signal(5)
        status = Cat(Const(SIGNATURE, 16), staged, overflow, busy_jtck, cpu_hold,
                     command, frames)
        tdo_sr = Signal(64)
        m.d.comb += self.tdo.eq(tdo_sr[0])

        # The output path runs on `active`, not `shifting`.
        #
        # JTAGG registers JTDO1 into the TDO pad on the falling edge, taking the value
        # from BEFORE that edge -- which is the bit the host samples in the low period
        # that follows. So shifting on the same edge the host is clocking presents the
        # right bit; delaying it the way the input path is delayed would present each
        # bit twice.
        with m.If(~active):
            m.d.jtck += tdo_sr.eq(status)
        with m.Else():
            m.d.jtck += tdo_sr.eq(tdo_sr[1:])

        with m.If(~shifting):
            m.d.jtck += [
                have_cmd.eq(0),
                bit_index.eq(0),
                word_index.eq(0),
            ]
        with m.Else():
            with m.If(~have_cmd):
                m.d.jtck += [
                    cmd_sr.eq(cmd_now),
                    bit_index.eq(bit_index + 1),
                ]
                with m.If(bit_index == 7):
                    m.d.jtck += [have_cmd.eq(1), bit_index.eq(0), command.eq(cmd_now),
                                 frames.eq(frames + 1)]

                    # Only a `write` clears the counters, so reading the status
                    # cannot destroy what it reports. A `nop` frame may be repeated
                    # any number of times and answers the same each time.
                    with m.If(cmd_now == CMD_WRITE):
                        m.d.jtck += [staged.eq(0), overflow.eq(0)]

            with m.Else():
                m.d.jtck += [
                    word_sr.eq(word_now),
                    bit_index.eq(bit_index + 1),
                ]
                with m.If(bit_index == 15):
                    m.d.jtck += bit_index.eq(0)
                    with m.If(word_index != 2):
                        m.d.jtck += word_index.eq(word_index + 1)

                    with m.If(command == CMD_WRITE):
                        with m.If(tag == TAG_DATA):
                            m.d.jtck += staged.eq(staged + 1)
                        with m.If(~fifo.w_rdy):
                            m.d.jtck += overflow.eq(1)

                    with m.If((command == CMD_RESET) & (word_index == 0)):
                        m.d.jtck += cpu_hold.eq(word_now[0])

        m.submodules.reset_cdc = FFSynchronizer(cpu_hold, self.cpu_reset)

        #
        # The HyperRAM side, in `sync`.
        #
        address = Signal(32)
        held    = Signal(32)

        m.d.comb += [self.addr.eq(address), self.data.eq(held)]

        entry_data = fifo.r_data[0:16]
        entry_tag  = fifo.r_data[16:18]

        # THE WIRE FORMAT IS STILL 16-BIT WORDS. The arbiter below now takes a
        # 32-bit pair, because the DQS controller has no narrower granule -- but
        # that is a property of the memory side, not of the link, and pushing it
        # onto the link would change the host tool and the frame format for no
        # gain. Two data words are collected here and issued as one request.
        #
        # A `write` frame therefore wants an EVEN start address and an even
        # number of data words. `scripts/soc_jtag_stage.py` pads the image, since
        # a firmware image is a byte stream and its length is the host's to round.
        second = Signal()

        with m.FSM() as fsm:
            with m.State("POP"):
                with m.If(fifo.r_rdy):
                    m.d.comb += fifo.r_en.eq(1)
                    with m.Switch(entry_tag):
                        with m.Case(TAG_ADDR_LO):
                            m.d.sync += address[0:16].eq(entry_data)
                            # An address restarts the pair: a frame that names a
                            # new start must not carry a half-pair into it.
                            m.d.sync += second.eq(0)
                        with m.Case(TAG_ADDR_HI):
                            m.d.sync += address[16:32].eq(entry_data)
                            m.d.sync += second.eq(0)
                        with m.Default():
                            with m.If(second):
                                m.d.sync += [held[16:32].eq(entry_data),
                                             second.eq(0)]
                                m.next = "WRITE"
                            with m.Else():
                                m.d.sync += [held[0:16].eq(entry_data),
                                             second.eq(1)]

            with m.State("WRITE"):
                m.d.comb += self.req.eq(1)
                with m.If(self.ack):
                    # The address advances here and nowhere else, so a `write` frame
                    # names its start once and the rest is sequential. Two 16-bit
                    # words went out, so it steps by two.
                    m.d.sync += address.eq(address + 2)
                    m.next = "POP"

        m.d.comb += busy.eq(fifo.r_rdy | ~fsm.ongoing("POP"))

        return m
