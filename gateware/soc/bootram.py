#!/usr/bin/env python3
#
# HyperRAM as CPU memory and as a firmware staging buffer.
# SPDX-License-Identifier: BSD-3-Clause

"""
Makes HyperRAM addressable while preserving both firmware staging paths.

    CPU cache --------Wishbone-----> HyperRAM
    host --USB bulk--> CPU --CSR---> HyperRAM --bootloader--> block RAM --> run
    host --JTAG ER1--> sink --------^

Firmware is block RAM init, so it normally lives inside the bitstream and
changing one byte costs a ~60 s resynthesis. Here the image goes into HyperRAM
(8 MiB, so images are not block-RAM sized) and a small resident bootloader
copies it into block RAM and jumps to it.

## Three access paths, one controller

    path        needs                          reaches HyperRAM through
    ----------  -----------------------------  -------------------------------------
    CPU memory  a running CPU                  `HyperRAMWishbone`, cache-line bursts
    USB bulk    a running CPU and console      `HyperRAMBoot`, one 32-bit pair at a time
    JTAG ER1    only a configured FPGA         `jtag_stage.JTAGStager`, one pair at a time

The JTAG path exists for the case the USB path cannot serve: a board whose
console is wedged still has JTAG, and staging over it holds the CPU in reset
throughout. Both land in the same layout, so `firmware/cynthion-boot` runs either
unchanged -- it reads the header and cannot tell which path filled it.

## The staging CSR moves one 32-bit pair at a time

Fetch a pair, auto-increment by two, raise a flag the firmware polls. It was 16
bits wide until the DQS PHY made that the expensive choice -- see
`HyperRAMBoot`'s docstring, and `docs/soc-memory-bus.md` for the survey of
what other HyperRAM controllers do.

  * No FIFO, no side-effecting read, no register whose value depends on how
    many times it has been read. That last class of bug cost a day on the SPI
    controller, where reading `data` popped a FIFO and a cached line then
    returned the first byte forever.
  * The cost is one HyperRAM transaction per word instead of a burst. Measured
    by the shell's `bench hyperram` at sync 60 MHz: 156 cycles per 16-bit word
    read and 113 per word written, which is 0.77 MB/s and 1.06 MB/s -- so a
    32 KiB image is 43 ms to read back and 31 ms to write. The 8 ms this said
    before was an estimate and was five times optimistic. The Wishbone window is
    faster -- its `main=1` region lets the D-cache line-fill -- but it is one
    HyperBus transaction per 32-bit beat on this SoC, not one per line: see
    `HyperRAMWishbone`'s `sustained` for why the master decides that.

Placeholder-BRAM (`ecpbram`), JTAG staging and this USB path are compared in
`../../docs/architecture.md`.
"""

from amaranth import C, Cat, Elaboratable, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.utils import log2_int
from amaranth_soc import csr, wishbone
from amaranth_soc.memory import MemoryMap

from luna.gateware.interface.psram import HyperBusPHY, HyperRAMPHY

# Both controllers are ours now, and so is one of the two PHYs.
# `hyperram_dqs_phy` records the three I/O faults that make upstream's DQS PHY
# uninstantiable on r1.4; the two controllers are luna's FSMs vendored so their
# unimplemented tCSHI recovery and their forced latency branch could be fixed.
# `HyperRAMPHY` stays upstream's -- it elaborates here and it works.
# `docs/upstream-boundary.md`: do not inherit a stack to get one file -- vendor
# the file.
from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY
from peripherals.hyperram_dqs_controller import HyperRAMDQSController
from peripherals.hyperram_controller import HyperRAMController

HYPERRAM_SIZE = 8 * 1024 * 1024

# Which DQSBUFM tap samples returning data. A property of the board and of CK,
# not of the design -- `scripts/hyperram_readclksel_sweep.py` walks all eight
# against BURSTDET, which is #148. `0b010` is upstream's untested default and is
# the starting point, not an answer.
HYPERRAM_READCLKSEL = 0b010

# Fixed-latency `sync` cycles for the DQS controller.
#
# SIX, derived from the part rather than from a tap sweep. CR0 = 0x8f2f selects
# fixed latency at 7 clocks, so the device takes 14 CK on every transaction, and
# at 4:1 gearing that is 7 fabric beats. The controller waits this count plus a
# zero beat, so it must be at least 6 to still be listening when the data
# arrives. `scripts/hyperram_latency_probe.py` sweeps it against the model and
# reports 6, 7 and 8 completing while 1-5 do not.
#
# It was 4, chosen to pull the capture EARLIER against an assumed late read, and
# upstream's 5 is also below the minimum. Both sample before the device presents,
# which is what a uniform one-word displacement looks like -- the window read
# 0/16 at 4 while single-word staging reads still came back correct. See #186.
HYPERRAM_LATENCY_CLOCKS = 6

# tCSM, the longest CS# may stay Low. CR1[1:0] = 01b, which Table 12 ties to a
# 64 ms array refresh over 8192 rows at T_CASE < 85 C, halved.
HYPERRAM_TCSM_NS = 4000.0

# Per-transaction overhead in CK, counted off `HyperRAMController`'s states:
# 3 command words + 13 `HANDLE_LATENCY` + 1 `RECOVERY`. `LATCH_RWDS` runs with CK
# held Low and does not count, and neither does the rest of `RECOVERY`: only its
# first cycle is spent with CS# still Low, which is what this budget is about.
HYPERRAM_OVERHEAD_CK = 17

# tCK maximum. Rev A01-006 Table 22 bounds it at 100 ns; A01-003 leaves it blank.
# Note 2, in both, says the real constraint is tCSM -- but a stall longer than
# this sits in the gap between that note and the table on A01-006 silicon, so
# nothing here goes there. See `docs/soc-memory-bus.md` 5.
HYPERRAM_TCK_MAX_NS = 100.0

# How much of tCSM the cap is allowed to spend. The remaining tenth covers the
# two things this arithmetic cannot see: the PLL solves to the nearest achievable
# output rather than exactly `SYNC_MHZ`, and the overhead below is a count of
# states rather than a measured gap.
HYPERRAM_TCSM_MARGIN = 0.9

# CK in MHz when the caller does not say. `HyperRAMPHY` emits one CK per `sync`
# cycle, so for the non-DQS build this is `vexii_hello_soc.SYNC_MHZ` -- which
# passes its own value in, because `riscv_clock_ladder.py` rewrites that constant
# and a second copy here would drift silently. This default serves sims and
# bring-up tops only.
HYPERRAM_CK_MHZ = 60.0


def hyperram_max_burst_words(ck_mhz, *, clock_stop=False):
    """Words one transaction may hold CS# Low for, derived from tCSM.

    THE CAP GUARDS A TIME LIMIT AND USED TO BE WRITTEN AS A BARE WORD COUNT.
    748 words is right at CK 192 MHz and is 12.5 us -- over three times tCSM --
    at the 60 MHz this SoC runs, unreachable today only because a Wishbone burst
    never exceeds 32 words. It has to come from the clock.

    `clock_stop` makes the divergence explicit rather than latent: Active Clock
    Stop spends CS#-Low cycles that produce no word, one per 32-bit beat and so
    one per two words, and the same wall-clock budget then buys two thirds as
    many. Word count stops tracking CS#-Low time the moment CK can be gated.

    Rounded down to an even number: a 32-bit beat is two 16-bit words at
    `word_width=16` and a cap landing mid-beat would cut one in half.
    """
    budget_ck = (HYPERRAM_TCSM_NS * HYPERRAM_TCSM_MARGIN * 1e-3 * ck_mhz
                 - HYPERRAM_OVERHEAD_CK)
    return max(2, int(budget_ck / (1.5 if clock_stop else 1.0)) & ~1)


def hyperram_max_stall_cycles(ck_mhz):
    """`sync` cycles CK may be parked Low inside a data phase, from tCK max.

    Six at 60 MHz. The bubble this design has to cover is ONE cycle -- the one
    `RegisteredResponse` withholds STB for -- so the bound never fires in normal
    operation. It exists for the case a master drops CYC part way through a
    burst, where `mmap.req` never returns and CS# would otherwise stay Low until
    the next reset.
    """
    return max(1, int(HYPERRAM_TCK_MAX_NS * 1e-3 * ck_mhz))


# The cap at the default clock. `HyperRAMWishbone` computes its own from the
# `ck_mhz` it is given -- this is here for a caller that wants to report or check
# the number without building one.
HYPERRAM_MAX_BURST_WORDS = hyperram_max_burst_words(HYPERRAM_CK_MHZ)


class ClockStopPHY(Elaboratable):
    """One `HyperBusPHY` record split in two, so CK can stop with CS# still Low.

    Everything passes through except `clk_en`, which the device side sees as
    `clk_en & ~stall`. `HyperRAMPHY` drives CK from
    `ODDRX1F(i_D0=clk_en, i_D1=0)`, so dropping `clk_en` emits no pulse and parks
    CK **Low** -- the state W956A8 rev A01-006 10.2.2 recommends stopping in, and
    the one 10.1 defines as Idle.

    This is Active Clock Stop: a named device state with its own truth-table row,
    its own DC parameter (I_CC6) and its own timing figure, Figure 13, which draws
    CS# held Low across a flat CK between two words of a data phase. It is NOT an
    inference from silence, and it needs no change to the controller -- it sits
    beneath one, on the record, and this wrapper is workspace gateware of the same
    kind as `HyperRAMWishbone`.

    `dev` is the record the pads (or a device model) see; `ctrl` is the one to
    hand `HyperRAMController`.
    """

    def __init__(self, *, dev):
        self.dev = dev
        self.ctrl = HyperBusPHY()
        self.stall = Signal()

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [
            self.dev.clk_en.eq(self.ctrl.clk_en & ~self.stall),
            self.dev.cs.eq(self.ctrl.cs),
            self.dev.reset.eq(self.ctrl.reset),
            self.dev.dq.o.eq(self.ctrl.dq.o),
            self.dev.dq.e.eq(self.ctrl.dq.e),
            self.dev.rwds.o.eq(self.ctrl.rwds.o),
            self.dev.rwds.e.eq(self.ctrl.rwds.e),
            self.ctrl.dq.i.eq(self.dev.dq.i),
            self.ctrl.rwds.i.eq(self.dev.rwds.i),
        ]
        return m


class HyperRAMWishbone(wiring.Component):
    """A 32-bit Wishbone window onto HyperRAM.

    The bus contains memory, not registers.

      * Reads only capture data and acknowledge the request.
      * Partial stores read, merge, and write because this controller has no mask port.
      * Linear incrementing cycles keep one HyperBus transaction open, but only
        when `sustained` says the master can feed one (see below).
      * Classic, wrapped, and partial-write cycles end after one Wishbone beat.

    ## `sustained`: coalescing needs a master that never bubbles

    A HyperBus data phase cannot be stalled. Once CS# is low and the latency has
    run, the device clocks one word in or out on every CK and this controller
    exposes no backpressure -- `write_ready`/`read_ready` are asserted on every
    cycle of `WRITE_DATA`/`READ_DATA` regardless of whether anyone is listening.
    So a transaction held open across Wishbone beats is a promise to supply a
    word every single cycle until it closes.

    A beat is two words at `word_width=16` and one at 32, so keeping that promise
    means a beat every two cycles, or every one. Anything slower and the device
    eats whatever happens to be on `dq` in the gap: a junk word at a real
    address, and -- because the engine's half-of-the-beat tracker advances with
    the device while this module's resets per beat -- every following beat
    emitted high-half-first.

    This SoC cannot keep it either way. `RegisteredResponse` withholds STB for
    one cycle after each acknowledgement, so the CPU delivers a beat every THREE
    cycles. No amount of buffering fixes a sustained rate deficit, so coalescing
    is off by default and a caller that has a zero-wait master opts in.

    The other way to opt in is `BootRAM`'s `clock_stop`, which removes the
    promise instead of keeping it: CK stops on the word boundary and the device
    waits. See `ClockStopPHY`. `sustained` without it is the arrangement the
    board measured at 8/16 words correct.

    `word_width` is the controller's data width, not the bus's -- the bus is
    always 32 bits. `HyperRAMController` hands over 16 bits at a time, so a beat
    is two words and the low half is held in `read_low` until the high half
    arrives; `HyperRAMDQSController` hands over 32, so a beat is one word and that
    assembly disappears. Everything else is identical, which is the reason this
    is a parameter rather than a second module.

    | signal | direction | meaning |
    |--------|-----------|---------|
    | `req` | out | one pending 32-bit transfer |
    | `req_addr` | out | first 16-bit HyperRAM word address |
    | `in_valid` | in | one returned or accepted controller word |
    """

    def __init__(self, *, size=HYPERRAM_SIZE, word_width=16, sustained=False,
                 ck_mhz=HYPERRAM_CK_MHZ, clock_stop=False):
        if size <= 0 or size & (size - 1):
            raise ValueError("HyperRAM window size must be a power of two")
        if size % 4:
            raise ValueError("HyperRAM window size must contain whole Wishbone words")
        if word_width not in (16, 32):
            raise ValueError("HyperRAM controller word width must be 16 or 32")
        self._word_width = word_width
        self._sustained = sustained
        # The cap is tCSM, in time, so it is computed from CK rather than stated.
        self.max_burst_words = hyperram_max_burst_words(
            ck_mhz, clock_stop=clock_stop)
        self.max_burst_beats = self.max_burst_words // 2

        wb_addr_width = log2_int(size // 4)
        memory_map = MemoryMap(addr_width=log2_int(size), data_width=8)
        memory_map.add_resource(self, name=("memory", "hyperram"), size=size)

        # For #173: whether the window currently believes it is inside a burst.
        # An output for instrumentation only -- nothing reads it to decide
        # anything, so it cannot change behaviour.
        self.bursting_out = Signal()

        super().__init__({
            "bus": wiring.In(wishbone.Signature(
                addr_width=wb_addr_width, data_width=32, granularity=8,
                features={"cti", "bte", "err"})),
            "req":       wiring.Out(1),
            "req_write": wiring.Out(1),
            "req_final": wiring.Out(1),
            "req_addr":  wiring.Out(32),
            "req_data":  wiring.Out(32),
            "granted":   wiring.In(1),
            "in_data":   wiring.In(word_width),
            "in_valid":  wiring.In(1),
        })
        self.bus.memory_map = memory_map

    def elaborate(self, platform):
        m = Module()

        pending = Signal()
        is_write = Signal()
        address = Signal(len(self.bus.adr))
        write_data = Signal(32)
        select = Signal(4)
        read_low = Signal(16)
        second_word = Signal()

        # Does this word_event close the 32-bit Wishbone beat? With a 16-bit
        # controller only the second of two words does; with a 32-bit one every
        # word does, and `second_word` stays low forever.
        beat_done = Signal()
        rmw_read = Signal()
        final_beat = Signal()
        bursting = Signal()
        burst_beats = Signal(range(self.max_burst_beats))

        request = self.bus.cyc & self.bus.stb & ~pending
        word_event = self.granted & self.in_valid
        m.d.comb += beat_done.eq(1 if self._word_width == 32 else second_word)
        complete = pending & word_event & beat_done & ~rmw_read
        # Decided in Python, not on the bus: with `sustained` false this is a
        # constant and the whole burst path is pruned rather than left as logic
        # that can never assert.
        burst_candidate = (
            ((self.bus.cti == wishbone.CycleType.INCR_BURST)
             & (self.bus.bte == wishbone.BurstTypeExt.LINEAR)
             & (~self.bus.we | (self.bus.sel == 0b1111)))
            if self._sustained else C(0))

        m.d.comb += self.bursting_out.eq(bursting)

        m.d.comb += [
            self.bus.ack.eq(complete),
            self.bus.err.eq(0),
            self.bus.dat_r.eq(self.in_data if self._word_width == 32
                              else Cat(read_low, self.in_data)),
            # Between burst beats the next Wishbone request keeps ownership while
            # its fields replace the beat acknowledged on the preceding edge.
            self.req.eq(pending | (bursting & self.bus.cyc & self.bus.stb)),
            self.req_write.eq(Mux(pending, is_write & ~rmw_read, self.bus.we)),
            self.req_final.eq(final_beat),
            self.req_addr.eq(address << 1),
            self.req_data.eq(Mux(pending, write_data, self.bus.dat_w)),
        ]

        with m.If(request):
            m.d.sync += [
                pending.eq(1),
                is_write.eq(self.bus.we),
                address.eq(self.bus.adr),
                write_data.eq(self.bus.dat_w),
                select.eq(self.bus.sel),
                second_word.eq(0),
                rmw_read.eq(self.bus.we & (self.bus.sel != 0b1111)),
                final_beat.eq(
                    ~burst_candidate
                    | (self.bus.cti == wishbone.CycleType.END_OF_BURST)
                    | (bursting & (burst_beats == self.max_burst_beats - 1))
                ),
            ]
            with m.If(~bursting):
                m.d.sync += [
                    bursting.eq(burst_candidate),
                    burst_beats.eq(1),
                ]
            with m.Elif(burst_beats == self.max_burst_beats - 1):
                m.d.sync += [bursting.eq(0), burst_beats.eq(0)]
            with m.Else():
                m.d.sync += burst_beats.eq(burst_beats + 1)

        with m.If((pending | (bursting & self.bus.cyc & self.bus.stb)) & word_event):
            with m.If(~beat_done):
                # 16-bit controller only: hold the low half until the high one
                # lands. `beat_done` is constant-1 at 32 bits, so Amaranth prunes
                # this branch and `read_low` with it.
                with m.If(~is_write | rmw_read):
                    m.d.sync += read_low.eq(self.in_data)
                m.d.sync += second_word.eq(1)
            with m.Else():
                with m.If(rmw_read):
                    old_data = (self.in_data if self._word_width == 32
                                else Cat(read_low, self.in_data))
                    m.d.sync += [
                        write_data.eq(Cat(*[
                            Mux(select[index], write_data.word_select(index, 8),
                                old_data.word_select(index, 8))
                            for index in range(4)
                        ])),
                        rmw_read.eq(0),
                        second_word.eq(0),
                    ]
                with m.If(~rmw_read):
                    m.d.sync += [
                        pending.eq(0),
                        second_word.eq(0),
                    ]
                    with m.If(final_beat):
                        m.d.sync += [bursting.eq(0), burst_beats.eq(0)]

        return m

class HyperRAMBoot(wiring.Component):
    """CPU-side read port for HyperRAM: fetch a word, auto-increment, poll for valid.

    Registers, on a byte-wide CSR bus:

        0x00  addr     W  32   set the word address; must be EVEN
        0x04  addr_rd  R  32   read it back; auto-increments by 2 after each fetch
        0x08  ctrl     W   1   bit 0: fetch the pair at `addr`
        0x0c  status   R   1   bit 0: `data` holds a fetched pair
        0x10  data     R  32   the fetched pair
        0x14  wdata    W  32   store this pair at `addr`, then auto-increment

    Reads have no side effects, so firmware may read `data` twice or not at all.
    `status.valid` clears when the next fetch starts, not when data is read.

    ## Why this is 32 bits wide and addresses in PAIRS

    It used to move one 16-bit word, because the non-DQS controller moves one
    16-bit word per cycle and matching it was free. The DQS PHY moves 32 bits per
    cycle and has no narrower mode -- `HyperBusDQSPHY` is "a 32-bit HyperBus
    interface on a DQS group for use with a 4:1 PHY module" -- so a 16-bit port
    on top of it has to be synthesised from a half-select and an RWDS byte mask.

    That synthesis was the source of three separate faults: writes came back with
    the masked half replaced by a copy of the other (`a5c3` read as `c3c3`), an
    odd start address hung the read path outright, and every access carried an
    address-bit-0 mux that no other HyperRAM controller has. LiteX's
    `litehyperbus` and OpenHBMC both express byte granularity as byte enables on
    a 32-bit bus mapped onto RWDS; neither has a narrow side-port, and luna's own
    DQS interface ties the write mask to zero because it does not support one.

    Nothing here needs 16-bit granularity. This port stages a firmware image,
    which is a byte stream of arbitrary length, and the CPU reaches HyperRAM only
    through a cached 32-bit window whose traffic is whole 64-byte cache lines.

    `addr` stays in the part's native 16-bit word units, because that is what the
    datasheet and the memory window both use -- but a 32-bit access covers two of
    them, so it must be even and the auto-increment steps by two.
    """

    def __init__(self):
        # Write-to-set plus a separate read-back, rather than one RW field.
        #
        # csr.action.RW keeps its storage internal and exposes only `data`, so the
        # auto-increment below has nowhere to write. Splitting the directions means this
        # module owns the address register and both the CPU and the increment can drive
        # it.
        self._addr = csr.Register({"addr": csr.Field(csr.action.W, 32)},
                                  access="w")
        self._addr_rd = csr.Register({"addr": csr.Field(csr.action.R, 32)},
                                     access="r")
        self._ctrl = csr.Register({"fetch": csr.Field(csr.action.W, 1)},
                                  access="w")
        self._status = csr.Register({"valid": csr.Field(csr.action.R, 1)},
                                    access="r")
        self._data = csr.Register({"data": csr.Field(csr.action.R, 32)},
                                  access="r")

        # Writing `wdata` stores that pair at `addr` and auto-increments -- one register
        # write per pair, no separate "go" strobe. This is how firmware stages an image
        # into HyperRAM before rebooting into it.
        self._wdata = csr.Register({"data": csr.Field(csr.action.W, 32)},
                                   access="w")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("addr", self._addr, offset=0x00)
        builder.add("addr_rd", self._addr_rd, offset=0x04)
        builder.add("ctrl", self._ctrl, offset=0x08)
        builder.add("status", self._status, offset=0x0c)
        builder.add("data", self._data, offset=0x10)
        builder.add("wdata", self._wdata, offset=0x14)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": wiring.In(csr.Signature(addr_width=5, data_width=8)),

            # To the shared HyperRAM arbiter.
            "req":       wiring.Out(1),   # held high until `done`
            "req_write": wiring.Out(1),   # the pending request is a write
            "req_addr":  wiring.Out(32),
            "req_data":  wiring.Out(32),
            "granted":   wiring.In(1),    # the arbiter is serving us
            "in_data":   wiring.In(32),
            "in_valid":  wiring.In(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        address = Signal(32)
        data = Signal(32)
        wdata = Signal(32)
        is_write = Signal()
        valid = Signal()
        busy = Signal()
        write_done = Signal()

        # Software writes to `addr` set the pointer; the auto-increment below also
        # drives it, so this is a plain register rather than the CSR's own storage.
        with m.If(self._addr.f.addr.w_stb):
            m.d.sync += address.eq(self._addr.f.addr.w_data)

        m.d.comb += [
            self._addr_rd.f.addr.r_data.eq(address),
            self._status.f.valid.r_data.eq(valid),
            self._data.f.data.r_data.eq(data),
            self.req.eq(busy),
            self.req_write.eq(is_write),
            self.req_addr.eq(address),
            self.req_data.eq(wdata),

            # `busy` doubles as the write-in-progress flag, so firmware polls the same
            # bit for both directions.
            self._status.f.valid.r_data.eq(valid & ~busy),
        ]

        with m.If(self._wdata.f.data.w_stb & ~busy):
            m.d.sync += [
                wdata.eq(self._wdata.f.data.w_data),
                busy.eq(1),
                is_write.eq(1),
                valid.eq(0),
            ]

        with m.If(busy & is_write & write_done):
            # `valid` must be set for a write too. Firmware polls ONE flag for both
            # directions, so leaving it clear after a write meant the poll never
            # completed -- a hang, with the transfer having actually succeeded.
            m.d.sync += [busy.eq(0), is_write.eq(0), valid.eq(1),
                         address.eq(address + 2)]

        with m.If(self._ctrl.f.fetch.w_stb & ~busy):
            m.d.sync += is_write.eq(0)
            # Clear `valid` at the START of a fetch, not when data is read. Firmware
            # polls it, and leaving it set from the previous word would let a poll
            # succeed immediately and read stale data -- indistinguishable from a fast
            # HyperRAM.
            m.d.sync += [busy.eq(1), valid.eq(0)]

        m.d.comb += write_done.eq(self.granted & self.in_valid & is_write)

        with m.If(busy & ~is_write & self.granted & self.in_valid):
            m.d.sync += [
                data.eq(self.in_data),
                valid.eq(1),
                busy.eq(0),
                # Auto-increment so a sequential copy needs one register write per
                # access. `addr` counts the part's 16-bit words and this access moved
                # two of them.
                address.eq(address + 2),
            ]

        return m


class BootRAM(Elaboratable):
    """HyperRAM shared by the memory window, staging CSR, and JTAG sink.

    JTAG wins when masters collide because its shift cannot stall. The two CPU
    paths may wait: Wishbone holds its cycle and staging firmware polls status.

    | priority | master | transfer width |
    |----------|--------|----------------|
    | 1 | JTAG staging sink | 16 bits |
    | 2 | CPU staging CSR | 16 bits |
    | 3 | CPU Wishbone window | one 32-bit beat, or a bounded linear burst |

    `sustained` is forwarded to `HyperRAMWishbone` and is what decides between
    those last two. It belongs to the MASTER, not to this module or the part:
    see that class for why a bubbling master and a held-open transaction corrupt
    each other.

    `clock_stop` is the other half of that answer and the reason `sustained` can
    ever be true here. It gates CK on word boundaries so the device waits out the
    master's bubble instead of eating whatever is on DQ -- see `ClockStopPHY`, and
    `docs/soc-memory-bus.md` 5 for the datasheet clauses. Without it a
    coalesced 64-byte line writes 48 device words where 32 are wanted.

    Attributes
    ----------
    port : HyperRAMBoot
        The CPU's CSR port; add `port.bus` to the SoC's CSR decoder.
    mmap : HyperRAMWishbone
        The CPU's memory port; add `mmap.bus` to the SoC's Wishbone decoder.
    jtag_req, jtag_addr, jtag_data, jtag_ack : Signal
        The second requester, in `sync`. Wire these to a `JTAGStager`.
    clk_stop : Signal
        Drive a `ClockStopPHY.stall` with this when `interface` is supplied from
        outside; zero unless `clock_stop`.
    """

    def __init__(self, *, interface=None, dqs=False, sustained=False,
                 clock_stop=False, ck_mhz=HYPERRAM_CK_MHZ):
        # DQS changes the data width -- 32 bits per beat against 16 -- so it is
        # recorded here and read where the word assembly is decided.
        self._dqs = dqs
        if clock_stop and dqs:
            # `HyperRAMDQSPHY` gears CK off `fast` through ODDRX2F and the mask
            # path adds a second strobe; neither is modelled or measured, and
            # gating a 2:1 geared clock is a different question from gating a
            # 1:1 one. Refuse rather than emit something untested.
            raise ValueError("Active Clock Stop is not wired for the DQS PHY")
        self._clock_stop = clock_stop
        # The DQS PHY gears the fabric at CK/2, and the vendored controller needs
        # the FABRIC clock to turn tCSHI into a cycle count.
        self._sync_mhz = ck_mhz / 2 if dqs else ck_mhz
        self._max_stall = hyperram_max_stall_cycles(ck_mhz)
        self.port = HyperRAMBoot()
        self.mmap = HyperRAMWishbone(word_width=32 if dqs else 16,
                                     sustained=sustained, ck_mhz=ck_mhz,
                                     clock_stop=clock_stop)
        self._interface = interface

        # For a caller that supplies its own `interface`: what to hand a
        # `ClockStopPHY`. When this module builds its own PHY it wires this up
        # internally and the output is instrumentation.
        self.clk_stop = Signal()

        self.jtag_req  = Signal()
        self.jtag_addr = Signal(32)
        # 32 bits, and `jtag_addr` must be even: every owner of this arbiter now
        # presents a whole 32-bit pair. See `HyperRAMBoot`'s docstring for why the
        # 16-bit ports were removed rather than kept alongside.
        self.jtag_data = Signal(32)
        self.jtag_ack  = Signal()

        # Instrumentation, for #173. These are the three facts that separate
        # "one burst per cache line" from "sixteen transactions", and none of
        # them was observable from outside this module.
        #
        # Outputs only, driven from signals that already exist below. Nothing
        # here changes what the bus does -- a probe that perturbed the timing it
        # measures would be worse than no probe.
        self.probe_start = Signal()   # a HyperBus transaction began
        self.probe_beat  = Signal()   # a Wishbone beat was acknowledged
        self.probe_burst = Signal()   # ... and it arrived marked as a burst
        # The DATA PHASE, which is where the missing 316 CK are.
        #
        # `words` is one 16-bit HyperRAM word delivered; a 64-byte line is 32 of
        # them. `busy` is a cycle with the controller not idle. Together they give
        # the gap between words: if busy/line is 348 and words/line is 32, the
        # data phase is not streaming and the gap is the fault.
        self.probe_word  = Signal()
        self.probe_busy  = Signal()
        # A cycle with CK parked Low inside a data phase. This is the CS#-Low
        # time that word count stops accounting for once `clock_stop` is on, so
        # it is the thing to count against tCSM on the board.
        self.probe_stall = Signal()
        # WHERE THE IDLE CYCLES GO. The controller is busy 72 CK of every 348 CK
        # line, so 276 CK are spent somewhere in this module and the window. These
        # split that time by state so it is attributed rather than guessed at:
        #
        #   want    the window is asking and the FSM has not started a transaction
        #   arming  the FSM has issued `start` and the controller has not left idle
        #
        # `want` is the cost of the round trip back through IDLE per transaction;
        # `arming` is the fixed handshake before the bus moves.
        self.probe_want   = Signal()
        self.probe_arming = Signal()
        # How long the CPU holds the window's Wishbone cycle open per line.
        #
        # `busy + want + arming` accounts for only 74 CK of a 348 CK line, so 274
        # are upstream of this module. If `cyc` is ~348 the CPU is waiting on this
        # bus for the whole line and the stall is between the window and the
        # controller; if it is ~74 the CPU is slow to ask, and the stall is in the
        # cache or the arbiter. One counter separates them.
        self.probe_cyc = Signal()

        # The DQS read path's self-report. Zero on the non-DQS build, where
        # there is no DLL and no strobe detector to report anything.
        # Driven by the probe's CSR so the tap can be swept without a rebuild.
        self.readclksel = Signal(7, init=HYPERRAM_READCLKSEL)

        # 0..3. How many cycles late the read data is against the CK that asked
        # for it -- see the sweep comment in `elaborate`. #185.
        self.read_stall_cycles = Signal(2)

        self.probe_dll_locked = Signal()
        self.probe_dll_ready = Signal()
        self.probe_burstdet = Signal()

    def elaborate(self, platform):
        m = Module()

        if self._interface is None:
            if self._dqs:
                # Raw pads: the DQS primitives own the tristates, the gearing and
                # the output buffers, so nothing here may be an Amaranth-managed
                # pin. The PHY drives CK, CS# and RESET# itself.
                ram_bus = platform.request("ram", 0, dir="-")
                psram_phy = HyperRAMDQSPHY(
                    bus=ram_bus,
                    readclksel=self.readclksel[:3],
                    read_phase=self.readclksel[3])
                psram = HyperRAMDQSController(
                    phy=psram_phy.phy,
                    sync_mhz=self._sync_mhz,
                    high_latency_clocks=HYPERRAM_LATENCY_CLOCKS)
                # Active high into the PHY; the pad is `PinsN` and the PHY reads
                # that polarity from the pin map rather than restating it.
                m.d.comb += [
                    psram_phy.phy.reset.eq(0),
                    self.probe_dll_locked.eq(psram_phy.dll_locked),
                    self.probe_dll_ready.eq(psram_phy.dll_ready),
                    self.probe_burstdet.eq(psram_phy.phy.burstdet),
                ]
            else:
                ram_bus = platform.request("ram")
                psram_phy = HyperRAMPHY(bus=ram_bus)
                if self._clock_stop:
                    gate = ClockStopPHY(dev=psram_phy.phy)
                    m.submodules.clock_stop = gate
                    m.d.comb += gate.stall.eq(self.clk_stop)
                    psram = HyperRAMController(phy=gate.ctrl,
                                               sync_mhz=self._sync_mhz)
                else:
                    psram = HyperRAMController(phy=psram_phy.phy,
                                               sync_mhz=self._sync_mhz)
                m.d.comb += ram_bus.reset.o.eq(0)
            m.submodules += [psram_phy, psram]
        else:
            psram = self._interface

        m.submodules.port = port = self.port
        m.submodules.mmap = mmap = self.mmap

        OWNER_CSR, OWNER_JTAG, OWNER_WISHBONE = range(3)

        owner = Signal(range(3))
        writing = Signal()
        address = Signal(32)
        write_data = Signal(32)
        second_word = Signal()

        # Does a 32-bit beat take TWO controller words? That is a property of the
        # controller's width alone, now that every owner presents 32 bits -- the
        # non-DQS controller moves 16 bits per cycle, the DQS one moves 32.
        #
        # It used to be a per-owner signal, set for the Wishbone window and clear
        # for the staging ports, because the staging ports were 16 bits wide. It
        # was never gated on `dqs`, so in DQS builds the window was still marked
        # wide against a controller that was already 32 bits.
        wide = not self._dqs

        start = Signal()
        selected_owner = Signal(range(3))
        selected_write = Signal()
        selected_address = Signal(32)
        selected_data = Signal(32)

        # The default is the staging CSR. Wishbone supersedes it, then JTAG supersedes
        # both because a JTAG shift cannot be stalled after it has begun.
        m.d.comb += [
            selected_owner.eq(OWNER_CSR),
            selected_write.eq(port.req_write),
            selected_address.eq(port.req_addr),
            selected_data.eq(port.req_data),
        ]
        with m.If(mmap.req):
            m.d.comb += [
                selected_owner.eq(OWNER_WISHBONE),
                selected_write.eq(mmap.req_write),
                selected_address.eq(mmap.req_addr),
                selected_data.eq(mmap.req_data),
            ]
        with m.If(self.jtag_req):
            m.d.comb += [
                selected_owner.eq(OWNER_JTAG),
                selected_write.eq(1),
                selected_address.eq(self.jtag_addr),
                selected_data.eq(self.jtag_data),
            ]

        any_request = self.jtag_req | port.req | mmap.req

        with m.FSM() as fsm:
            with m.State("IDLE"):
                with m.If(any_request):
                    m.d.comb += start.eq(1)
                    m.d.sync += [
                        owner.eq(selected_owner),
                        writing.eq(selected_write),
                        address.eq(selected_address),
                        write_data.eq(selected_data),
                        second_word.eq(0),
                    ]
                    m.next = "STARTING"

            with m.State("STARTING"):
                # Wait for the controller to actually leave idle before watching for it
                # to return. `psram.idle` is still HIGH on the cycle the transfer is
                # issued, so a state that exits on `idle` alone falls straight through
                # and reports completion before anything happened.
                with m.If(~psram.idle):
                    m.next = "BUSY"

            with m.State("BUSY"):
                with m.If(psram.idle):
                    m.d.sync += [writing.eq(0), second_word.eq(0)]
                    m.next = "IDLE"

        active = ~fsm.ongoing("IDLE")

        # --- Active Clock Stop -----------------------------------------------
        #
        # A HyperBus data phase has no backpressure: once the latency has run the
        # device moves a word every CK, and `write_ready`/`read_ready` are
        # asserted whether or not anyone is listening. The only legal way to make
        # it wait is to stop CK with CS# still Low. See `ClockStopPHY`.
        #
        # The stall is simply the window having no word to offer. `mmap.req` is
        # `pending | (bursting & cyc & stb)` and `pending` is set at the start of
        # a beat and cleared only when that beat completes -- and a beat can only
        # complete on a `word_event`, which exists only in WRITE_DATA/READ_DATA.
        # So `req` is high across SHIFT_COMMAND and HANDLE_LATENCY by
        # construction, which is what keeps this out of the latency window, where
        # DQ is High-Z and a pause is illegal. Nothing new tracks the FSM.
        stall = Signal()
        want_stall = Signal()
        m.d.comb += want_stall.eq(
            (active & (owner == OWNER_WISHBONE) & ~mmap.req)
            if self._clock_stop else C(0))

        # THE BOUND, stated where the stall is generated. Rev A01-006 gives tCK a
        # 100 ns maximum that A01-003 leaves blank, so a longer pause sits in the
        # gap between that table and 10.2.2 and this design does not go there.
        # Six cycles at 60 MHz against a worst case of one; it fires only if a
        # master abandons a burst, and then it closes the transaction rather than
        # hold CS# Low -- which costs one duplicated word on a write, the lesser
        # of that and missing a refresh deadline.
        stall_count = Signal(range(self._max_stall + 1))
        stall_timeout = Signal()
        m.d.comb += stall.eq(want_stall & ~stall_timeout)
        with m.If(start):
            m.d.sync += [stall_count.eq(0), stall_timeout.eq(0)]
        with m.Elif(stall):
            m.d.sync += stall_count.eq(stall_count + 1)
            with m.If(stall_count == self._max_stall - 1):
                m.d.sync += stall_timeout.eq(1)
        with m.Else():
            m.d.sync += stall_count.eq(0)

        # The CK gate is one cycle behind the word gate ON WRITES, and level with
        # it on reads. `dq.o` is registered inside the controller, so the word on
        # the wire in cycle T is the one `write_ready` accepted in T-1; a read's
        # data and its RWDS transition arrive in the same cycle `read_ready`
        # fires. Gate both at T and the last accepted write word is never clocked
        # out. This is the fifth risk named in `docs/soc-memory-bus.md` 5 --
        # "the cycle the stall asserts must not be one in which the controller's
        # registered `dq.o` has already moved on" -- and the sim is what settled
        # it: without the register a coalesced line write returns 0/16.
        stall_ck = Signal()
        m.d.sync += stall_ck.eq(stall)

        # The read side's delay is UNKNOWN on silicon and is swept, not chosen.
        #
        # The sim model returns read data in the same cycle as the CK that asked
        # for it, so N=0 is correct there and the level-aligned gate passes.
        # Hardware does not: `HyperRAMPHY` samples DQ and RWDS through `IDDRX1F`
        # and tACC comes before that, so stopping CK at cycle T stops data
        # arriving at T+N. Gate the wrong cycle and the controller keeps
        # asserting `read_ready` at a window that has stopped listening.
        #
        # N is a property of the board and the PHY, so it is a runtime selector
        # like READCLKSEL rather than a constant somebody picked. #185.
        stall_pipe = Signal(4)
        m.d.sync += stall_pipe.eq(Cat(stall, stall_pipe[:3]))
        stall_read = Signal()
        with m.Switch(self.read_stall_cycles):
            with m.Case(0):
                m.d.comb += stall_read.eq(stall)
            for n in range(1, 4):
                with m.Case(n):
                    m.d.comb += stall_read.eq(stall_pipe[n - 1])

        m.d.comb += self.clk_stop.eq(Mux(writing, stall_ck, stall_read))

        word_event = (Mux(writing, psram.write_ready, psram.read_ready)
                      & ~Mux(writing, stall, stall_read))

        # `wide` and "keeps the transaction open" used to be the same flag, which
        # worked only because the 16-bit controller made them coincide: the
        # Wishbone window was the sole owner needing two words AND the sole owner
        # that bursts. The DQS controller separates them -- the window still
        # bursts but its beat is now one word -- so they are two signals.

        # Has this owner's 32-bit beat been fully transferred? On the DQS
        # controller one word IS the beat, so this is always true and the whole
        # second-word half disappears at elaboration.
        half_done = Signal()
        m.d.comb += half_done.eq(second_word if wide else 1)

        # Does this owner continue past the current beat? Only the window does,
        # and only while it says so; that is what makes a cache line one burst
        # rather than sixteen transactions. The staging ports move one word and stop.
        streaming = Signal()
        m.d.comb += streaming.eq(
            Mux(start, selected_owner, owner) == OWNER_WISHBONE)

        current_final = Signal()
        m.d.comb += current_final.eq(half_done & Mux(streaming, mmap.req_final, 1))

        # Do not advance past the closing half. This holds both `final_word` and the
        # last write data through recovery, as required by the upstream controller.
        if wide:
            with m.If(active & word_event & ~current_final):
                m.d.sync += second_word.eq(~second_word)

        # The 32 bits this owner is presenting. The window replaces its fields on
        # the edge that acknowledges the previous beat, so mid-burst the live
        # request is the truth and the latched copy is one beat stale.
        live_data = Signal(32)
        m.d.comb += live_data.eq(Mux(
            start, selected_data,
            Mux(owner == OWNER_WISHBONE, mmap.req_data, write_data)))

        live_address = Signal(32)
        m.d.comb += live_address.eq(Mux(start, selected_address, address))

        # --- controller width adaptation -------------------------------------
        #
        # HyperBus sends the lower-addressed word first.
        #
        # THIS COMMENT USED TO CLAIM the DQS PHY puts that first word in the HIGH
        # half of its 32-bit port, and that `dev.py test-board` cross-checked it.
        # The second part is definitely wrong: the cross-check writes and reads
        # through paths that share this function, so an inverted swap cancels and
        # it can never see one.
        #
        # OBSERVED, not concluded: staging `a5c31234` with the swap applied put
        # `a5c3` into the lower word; without the swap it put `1234` there. That
        # is consistent with the wire taking the low half first -- but the SECOND
        # word is corrupt in both cases (`c3c3`, then `3434`: the first word's low
        # byte repeated), so one 32-bit value cannot separate an ordering fault
        # from a permutation that happens to leave those four bytes looking right.
        #
        # `hr ramp` writes 0-255, where every byte names its own position, and
        # that is what settles it.
        #
        # NEITHER DIRECTION SWAPS NOW. `hyperram_ceiling_top.py` is the reference:
        # it drives `psram.write_data` from its pattern and compares
        # `psram.read_data` against it directly, with no swap either way, and it
        # moves millions of words. Whatever the controller's half convention is,
        # a design that applies no transform on either side is self-consistent
        # with it -- and `BootRAM` applying one on reads only was the remaining
        # asymmetry between the two. See #186.
        read_word = Signal(32 if self._dqs else 16)
        m.d.comb += read_word.eq(psram.read_data)

        if self._dqs:
            # Every owner presents a whole 32-bit pair, so this is one assignment
            # and there is no byte mask at all.
            #
            # It used to duplicate a 16-bit staging word into both halves and let
            # an RWDS mask choose which landed. That never worked on silicon --
            # `a5c3` read back as `c3c3`, the masked half replaced by a copy of
            # the other -- and luna's DQS interface does not support a write mask
            # in the first place: it drives `rwds.o` to 0 unconditionally, which
            # is why reaching it needed a subclass. Byte granularity was
            # synthesised for ports that never needed it.
            # NO SWAP ON THE WRITE SIDE -- one-variable experiment, #186.
            #
            # The comment above asserts the PHY puts the first word on the wire
            # in the HIGH half. The board says otherwise: staging `a5c31234`
            # becomes `0x1234a5c3` after the swap, and memory comes back holding
            # `a5c3` in the FIRST word -- the LOW half of the swapped value. That
            # is what happens if the wire takes the low half first and the swap is
            # backwards.
            #
            # The read side keeps the swap for now, deliberately: changing both at
            # once cannot be attributed. If this alone fixes the write ordering,
            # the two directions need opposite conventions and the comment above
            # is wrong; if it does not, the fault is not the swap.
            m.d.comb += psram.write_data.eq(live_data)
        else:
            m.d.comb += psram.write_data.eq(
                Mux(second_word, live_data[16:], live_data[:16]))

        # `live_address` is already even by construction: the window addresses in
        # 32-bit beats and both staging ports step by two. The `& ~1` that used to
        # be here was covering for the 16-bit ports, which could name an odd word.
        # A staging port is 32 bits, so on the 16-bit controller it takes two
        # words: hold the first and present the pair when the second lands. The
        # window does the same thing inside `HyperRAMWishbone`; the staging ports
        # used to need none of it because they were themselves 16 bits.
        if wide:
            staged_low = Signal(16)
            staging_word = active & (owner == OWNER_CSR) & word_event
            with m.If(staging_word & ~second_word):
                m.d.sync += staged_low.eq(read_word)
            staged_read = Cat(staged_low, read_word)
            staged_valid = staging_word & second_word
            # One ack per 32-bit pair, not per device word.
            jtag_done = (active & (owner == OWNER_JTAG)
                         & psram.write_ready & second_word)
        else:
            staged_read = read_word
            staged_valid = active & (owner == OWNER_CSR) & word_event
            jtag_done = active & (owner == OWNER_JTAG) & psram.write_ready

        m.d.comb += [
            psram.single_page.eq(0),
            # Memory, always. Reading the part's ID and CR registers says
            # nothing about whether the MEMORY path works -- a paired fetch in
            # register space returns the same value in both halves, so it cannot
            # even see a swapped pair -- and it was being cited as if it could.
            # Use JTAG staging to write a known value if a verifiable read is
            # wanted. `gateware/probes/hyperram/hyperram_identify.py` is where register
            # access belongs, testing registers as registers.
            psram.register_space.eq(0),
            psram.start_transfer.eq(start),
            psram.address.eq(live_address),

            # These are held from the start edge through the whole transfer. Earlier
            # pulsed drivers returned plausible wrong data rather than failing.
            psram.perform_write.eq(Mux(start, selected_write, writing)),
            # `current_final` already folds in the start edge through `half_done`
            # and `streaming`, so it no longer needs a Mux of its own here.
            # `stall_timeout` is the tCK escape above: closing the transaction is
            # the only exit `HyperRAMController` offers, so the bound uses it.
            psram.final_word.eq(current_final | stall_timeout),
            port.granted.eq(active & (owner == OWNER_CSR)),
            port.in_data.eq(staged_read),
            port.in_valid.eq(staged_valid),

            mmap.granted.eq(active & (owner == OWNER_WISHBONE)),
            mmap.in_data.eq(read_word),
            mmap.in_valid.eq(active & (owner == OWNER_WISHBONE) & word_event),

            self.jtag_ack.eq(jtag_done),

            # #173. `start` is the transaction begin the FSM already computes;
            # `mmap.bus.ack` is one Wishbone beat completing; `bursting` is the
            # window's own record that the beat came in with CTI=INCR_BURST and
            # BTE=LINEAR. Counting them outside this module answers whether a
            # 64-byte line is one transaction or sixteen.
            self.probe_start.eq(start),
            self.probe_beat.eq(mmap.bus.ack),
            self.probe_burst.eq(mmap.bus.ack & mmap.bursting_out),
            self.probe_word.eq(word_event),
            self.probe_busy.eq(~psram.idle),
            self.probe_stall.eq(stall),
            self.probe_want.eq(mmap.req & fsm.ongoing("IDLE")),
            self.probe_arming.eq(fsm.ongoing("STARTING")),
            self.probe_cyc.eq(mmap.bus.cyc),
        ]

        return m
