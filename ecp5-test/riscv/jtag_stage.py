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
    ER2   0x38   the RISC-V debug module (`vexii_cpu.py`)

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
    51..63  zero

The register is reloaded on every JTCK edge outside a shift and shifts LSB first
during one, so TDO carries the status of the moment the frame began. Reading it
changes nothing.

## Rates

    stage                   rate
    ----------------------  ---------------------------------------
    Apollo's JTAG SCK       12 MHz -> 750 kword/s (`firmware/src/jtag.c:904`)
    one HyperRAM write      ~22 `sync` cycles -> 2.7 Mword/s at 60 MHz

The sink drains 3.6x faster than JTAG can fill it, so the FIFO is a clock crossing
and a jitter buffer rather than a store, and 32 entries is generous. A JTAG shift
cannot be stalled, so `overflow` records the case where that margin is wrong
instead of losing words silently.
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
        self.data = Signal(16)
        self.ack  = Signal()

        self.cpu_reset = Signal()

    def elaborate(self, platform):
        m = Module()

        # A clock domain on TCK, which runs only while the host is clocking the chain.
        #
        # Asynchronously reset from `sync`, so `cpu_hold` is known to be clear at
        # power-up. A synchronous reset could not do that: with no JTAG attached there
        # is no edge to apply it on, and a board that came up with its CPU held in
        # reset would look identical to a dead one.
        m.domains.jtck = ClockDomain("jtck", local=True, async_reset=True)
        m.d.comb += [
            ClockSignal("jtck").eq(self.tck),
            ResetSignal("jtck").eq(ResetSignal("sync")),
        ]

        m.submodules.fifo = fifo = AsyncFIFO(
            width=18, depth=self._depth, w_domain="jtck", r_domain="sync")

        # High exactly while ER1 payload bits are on the wire. Everything else --
        # capture, update, pause, idle -- resets the frame decoder, so the decoder
        # depends on nothing about JCE1 beyond this one property.
        active = Signal()
        m.d.comb += active.eq(self.ce & self.shift)

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
        m.d.comb += word_done.eq(active & have_cmd & (bit_index == 15))

        m.d.comb += [
            fifo.w_data.eq(Cat(word_now, tag)),
            fifo.w_en.eq(word_done & (command == CMD_WRITE)),
        ]

        busy = Signal()
        busy_jtck = Signal()
        m.submodules.busy_cdc = FFSynchronizer(busy, busy_jtck, o_domain="jtck")

        status = Cat(Const(SIGNATURE, 16), staged, overflow, busy_jtck, cpu_hold,
                     Const(0, 13))
        tdo_sr = Signal(64)
        m.d.comb += self.tdo.eq(tdo_sr[0])

        with m.If(~active):
            m.d.jtck += [
                have_cmd.eq(0),
                bit_index.eq(0),
                word_index.eq(0),
                tdo_sr.eq(status),
            ]
        with m.Else():
            m.d.jtck += tdo_sr.eq(tdo_sr[1:])

            with m.If(~have_cmd):
                m.d.jtck += [
                    cmd_sr.eq(cmd_now),
                    bit_index.eq(bit_index + 1),
                ]
                with m.If(bit_index == 7):
                    m.d.jtck += [have_cmd.eq(1), bit_index.eq(0), command.eq(cmd_now)]

                    # Cleared here rather than at the start of the frame, so that a
                    # `nop` frame reports what the previous `write` did. A counter
                    # cleared per frame could only ever read back zero.
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
        held    = Signal(16)

        m.d.comb += [self.addr.eq(address), self.data.eq(held)]

        entry_data = fifo.r_data[0:16]
        entry_tag  = fifo.r_data[16:18]

        with m.FSM() as fsm:
            with m.State("POP"):
                with m.If(fifo.r_rdy):
                    m.d.comb += fifo.r_en.eq(1)
                    with m.Switch(entry_tag):
                        with m.Case(TAG_ADDR_LO):
                            m.d.sync += address[0:16].eq(entry_data)
                        with m.Case(TAG_ADDR_HI):
                            m.d.sync += address[16:32].eq(entry_data)
                        with m.Default():
                            m.d.sync += held.eq(entry_data)
                            m.next = "WRITE"

            with m.State("WRITE"):
                m.d.comb += self.req.eq(1)
                with m.If(self.ack):
                    # The address advances here and nowhere else, so a `write` frame
                    # names its start once and the rest is sequential.
                    m.d.sync += address.eq(address + 1)
                    m.next = "POP"

        m.d.comb += busy.eq(fifo.r_rdy | ~fsm.ongoing("POP"))

        return m
