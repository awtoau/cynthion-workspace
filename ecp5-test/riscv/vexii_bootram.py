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
    USB bulk    a running CPU and console      `HyperRAMBoot`, one word at a time
    JTAG ER1    only a configured FPGA         `jtag_stage.JTAGStager`, one word at a time

The JTAG path exists for the case the USB path cannot serve: a board whose
console is wedged still has JTAG, and staging over it holds the CPU in reset
throughout. Both land in the same layout, so `firmware/cynthion-boot` runs either
unchanged -- it reads the header and cannot tell which path filled it.

## The staging CSR moves one word at a time

Fetch a 16-bit word, auto-increment, raise a flag the firmware polls.

  * No FIFO, no side-effecting read, no register whose value depends on how
    many times it has been read. That last class of bug cost a day on the SPI
    controller, where reading `data` popped a FIFO and a cached line then
    returned the first byte forever.
  * The cost is one HyperRAM transaction per word instead of a burst. Measured
    by the shell's `bench hyperram` at sync 60 MHz: 156 cycles per 16-bit word
    read and 113 per word written, which is 0.77 MB/s and 1.06 MB/s -- so a
    32 KiB image is 43 ms to read back and 31 ms to write. The 8 ms this said
    before was an estimate and was five times optimistic. Bursting is available
    through the Wishbone window, whose `main=1` region lets the D-cache line-fill.

Placeholder-BRAM (`ecpbram`), JTAG staging and this USB path are compared in
`../../docs/decisions.md`.
"""

from amaranth import Cat, Elaboratable, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.utils import log2_int
from amaranth_soc import csr, wishbone
from amaranth_soc.memory import MemoryMap

from luna.gateware.interface.psram import HyperRAMPHY, HyperRAMInterface

# Ours, not upstream's: upstream's DQS PHY cannot be instantiated on r1.4 at all.
# `hyperram_dqs_phy` records the three I/O faults and `docs/upstream-boundary.md`
# the rule -- vendor I/O for this board is ours, the HyperBus protocol above it
# is not, which is why the controller below still comes from luna.
from hyperram_dqs_phy import HyperRAMDQSPHY
from hyperram_mask import MaskedHyperRAMDQSInterface

HYPERRAM_SIZE = 8 * 1024 * 1024

# Which DQSBUFM tap samples returning data. A property of the board and of CK,
# not of the design -- `scripts/hyperram_readclksel_sweep.py` walks all eight
# against BURSTDET, which is #148. `0b010` is upstream's untested default and is
# the starting point, not an answer.
HYPERRAM_READCLKSEL = 0b010

# Fixed-latency `sync` cycles for the DQS controller. Upstream is 5; at 4:1
# gearing that is 10 CK and puts every capture setting at least one word late.
# See `hyperram_mask` for the measurement.
HYPERRAM_LATENCY_CLOCKS = 4

# CR1 sets tCSM to 4 us. Leave an even-word margin below 768 CK at 192 MHz so
# a missing Wishbone terminator cannot hold CS# low through a refresh deadline.
HYPERRAM_MAX_BURST_WORDS = 748
HYPERRAM_MAX_BURST_BEATS = HYPERRAM_MAX_BURST_WORDS // 2


class HyperRAMWishbone(wiring.Component):
    """A 32-bit Wishbone window onto HyperRAM.

    The bus contains memory, not registers.

      * Reads only capture data and acknowledge the request.
      * Partial stores read, merge, and write because this controller has no mask port.
      * Linear incrementing cycles keep one HyperBus transaction open.
      * Classic, wrapped, and partial-write cycles end after one Wishbone beat.

    `word_width` is the controller's data width, not the bus's -- the bus is
    always 32 bits. `HyperRAMInterface` hands over 16 bits at a time, so a beat
    is two words and the low half is held in `read_low` until the high half
    arrives; `HyperRAMDQSInterface` hands over 32, so a beat is one word and that
    assembly disappears. Everything else is identical, which is the reason this
    is a parameter rather than a second module.

    | signal | direction | meaning |
    |--------|-----------|---------|
    | `req` | out | one pending 32-bit transfer |
    | `req_addr` | out | first 16-bit HyperRAM word address |
    | `in_valid` | in | one returned or accepted controller word |
    """

    def __init__(self, *, size=HYPERRAM_SIZE, word_width=16):
        if size <= 0 or size & (size - 1):
            raise ValueError("HyperRAM window size must be a power of two")
        if size % 4:
            raise ValueError("HyperRAM window size must contain whole Wishbone words")
        if word_width not in (16, 32):
            raise ValueError("HyperRAM controller word width must be 16 or 32")
        self._word_width = word_width

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
        burst_beats = Signal(range(HYPERRAM_MAX_BURST_BEATS))

        request = self.bus.cyc & self.bus.stb & ~pending
        word_event = self.granted & self.in_valid
        m.d.comb += beat_done.eq(1 if self._word_width == 32 else second_word)
        complete = pending & word_event & beat_done & ~rmw_read
        burst_candidate = (
            (self.bus.cti == wishbone.CycleType.INCR_BURST)
            & (self.bus.bte == wishbone.BurstTypeExt.LINEAR)
            & (~self.bus.we | (self.bus.sel == 0b1111))
        )

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
                    | (bursting & (burst_beats == HYPERRAM_MAX_BURST_BEATS - 1))
                ),
            ]
            with m.If(~bursting):
                m.d.sync += [
                    bursting.eq(burst_candidate),
                    burst_beats.eq(1),
                ]
            with m.Elif(burst_beats == HYPERRAM_MAX_BURST_BEATS - 1):
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

        0x00  addr     W  32   set the word address
        0x04  addr_rd  R  32   read it back; auto-increments after each fetch
        0x08  ctrl     W   1   bit 0: fetch the word at `addr`
        0x0c  status   R   1   bit 0: `data` holds a fetched word
        0x0d  data_lo  R   8   low byte
        0x0e  data_hi  R   8   high byte
        0x10  wdata    W  16   store this word at `addr`, then auto-increment

    Reads have no side effects, so firmware may read `data_lo`/`data_hi` in either order,
    twice, or not at all. `status.valid` clears when the next fetch starts, not when data
    is read.
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
        self._data_lo = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                     access="r")
        self._data_hi = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                     access="r")

        # Writing `wdata` stores that word at `addr` and auto-increments -- one register
        # write per word, no separate "go" strobe. This is how firmware stages an image
        # into HyperRAM before rebooting into it.
        self._wdata = csr.Register({"data": csr.Field(csr.action.W, 16)},
                                   access="w")

        builder = csr.Builder(addr_width=5, data_width=8)
        builder.add("addr", self._addr, offset=0x00)
        builder.add("addr_rd", self._addr_rd, offset=0x04)
        builder.add("ctrl", self._ctrl, offset=0x08)
        builder.add("status", self._status, offset=0x0c)
        builder.add("data_lo", self._data_lo, offset=0x0d)
        builder.add("data_hi", self._data_hi, offset=0x0e)
        builder.add("wdata", self._wdata, offset=0x10)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": wiring.In(csr.Signature(addr_width=5, data_width=8)),

            # To the shared HyperRAM arbiter.
            "req":       wiring.Out(1),   # held high until `done`
            "req_write": wiring.Out(1),   # the pending request is a write
            "req_addr":  wiring.Out(32),
            "req_data":  wiring.Out(16),
            "granted":   wiring.In(1),    # the arbiter is serving us
            "in_data":   wiring.In(16),
            "in_valid":  wiring.In(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        address = Signal(32)
        data = Signal(16)
        wdata = Signal(16)
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
            self._data_lo.f.data.r_data.eq(data[:8]),
            self._data_hi.f.data.r_data.eq(data[8:]),
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
                         address.eq(address + 1)]

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
                # Auto-increment so a sequential copy needs one register write per word
                # instead of two. HyperRAM addresses are in 16-bit words.
                address.eq(address + 1),
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
    | 3 | CPU Wishbone window | 32-bit beats in one bounded linear burst |

    Attributes
    ----------
    port : HyperRAMBoot
        The CPU's CSR port; add `port.bus` to the SoC's CSR decoder.
    mmap : HyperRAMWishbone
        The CPU's memory port; add `mmap.bus` to the SoC's Wishbone decoder.
    jtag_req, jtag_addr, jtag_data, jtag_ack : Signal
        The second requester, in `sync`. Wire these to a `JTAGStager`.
    """

    def __init__(self, *, interface=None, dqs=False):
        # DQS changes the data width -- 32 bits per beat against 16 -- so it is
        # recorded here and read where the word assembly is decided.
        self._dqs = dqs
        self.port = HyperRAMBoot()
        self.mmap = HyperRAMWishbone(word_width=32 if dqs else 16)
        self._interface = interface

        self.jtag_req  = Signal()
        self.jtag_addr = Signal(32)
        self.jtag_data = Signal(16)
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
        self.readclksel = Signal(4, init=HYPERRAM_READCLKSEL)

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
                psram = MaskedHyperRAMDQSInterface(
                    phy=psram_phy.phy,
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
                psram = HyperRAMInterface(phy=psram_phy.phy)
                m.d.comb += ram_bus.reset.o.eq(0)
            m.submodules += [psram_phy, psram]
        else:
            psram = self._interface

        m.submodules.port = port = self.port
        m.submodules.mmap = mmap = self.mmap

        OWNER_CSR, OWNER_JTAG, OWNER_WISHBONE = range(3)

        owner = Signal(range(3))
        writing = Signal()
        wide = Signal()
        address = Signal(32)
        write_data = Signal(32)
        second_word = Signal()

        start = Signal()
        selected_owner = Signal(range(3))
        selected_write = Signal()
        selected_wide = Signal()
        selected_address = Signal(32)
        selected_data = Signal(32)

        # The default is the staging CSR. Wishbone supersedes it, then JTAG supersedes
        # both because a JTAG shift cannot be stalled after it has begun.
        m.d.comb += [
            selected_owner.eq(OWNER_CSR),
            selected_write.eq(port.req_write),
            selected_wide.eq(0),
            selected_address.eq(port.req_addr),
            selected_data.eq(port.req_data),
        ]
        with m.If(mmap.req):
            m.d.comb += [
                selected_owner.eq(OWNER_WISHBONE),
                selected_write.eq(mmap.req_write),
                selected_wide.eq(1),
                selected_address.eq(mmap.req_addr),
                selected_data.eq(mmap.req_data),
            ]
        with m.If(self.jtag_req):
            m.d.comb += [
                selected_owner.eq(OWNER_JTAG),
                selected_write.eq(1),
                selected_wide.eq(0),
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
                        wide.eq(selected_wide),
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
                    m.d.sync += [writing.eq(0), wide.eq(0), second_word.eq(0)]
                    m.next = "IDLE"

        active = ~fsm.ongoing("IDLE")
        word_event = Mux(writing, psram.write_ready, psram.read_ready)

        # `wide` and "keeps the transaction open" used to be the same flag, which
        # worked only because the 16-bit controller made them coincide: the
        # Wishbone window was the sole owner needing two words AND the sole owner
        # that bursts. The DQS controller separates them -- the window still
        # bursts but its beat is now one word -- so they are two signals.

        # Has this owner's 32-bit beat been fully transferred?
        half_done = Signal()
        m.d.comb += half_done.eq(Mux(start, ~selected_wide, ~wide | second_word))

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
        with m.If(active & wide & word_event & ~current_final):
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
        # HyperBus sends the lower-addressed word first, and the DQS PHY puts
        # the first word in the HIGH half of its 32-bit port (`dq.i[24:32]` is
        # the first byte on the wire). Every port here expects the lower address
        # in the low half, which is what the 16-bit controller produces
        # naturally. Swap once at the boundary rather than in each of the three
        # ports -- and note this ordering is asserted from the PHY's gearing
        # wiring, so `dev.py test-board` cross-checks it by writing through one
        # port and reading through another.
        def swap_halves(value):
            return Cat(value[16:32], value[0:16])

        read_word = Signal(32 if self._dqs else 16)
        if self._dqs:
            m.d.comb += read_word.eq(swap_halves(psram.read_data))
        else:
            m.d.comb += read_word.eq(psram.read_data)

        if self._dqs:
            # Staging owners present one 16-bit word. Duplicate it into both
            # halves and let the mask decide which lands, so a store to an odd
            # word address needs neither a read-modify-write nor a buffer.
            staged = live_data[:16]
            m.d.comb += psram.write_data.eq(swap_halves(
                Mux(streaming, live_data, Cat(staged, staged))))

            # Which of the four byte lanes to inhibit, indexed by the swapped
            # value's own bytes: 0 and 1 are the even word, 2 and 3 the odd one.
            # The Wishbone window always presents a whole 32-bit beat -- its
            # partial stores go through the read-merge-write path in
            # `HyperRAMWishbone` -- so it inhibits nothing.
            inhibit = Signal(4)
            m.d.comb += inhibit.eq(
                Mux(streaming, 0b0000, Mux(live_address[0], 0b0011, 0b1100)))
            # `rwds.o` is in wire order, bit 3 first, and the wire carries the
            # even word's high byte, its low byte, then the odd word's two.
            m.d.comb += psram.write_mask.eq(
                Cat(inhibit[2], inhibit[3], inhibit[0], inhibit[1]))
        else:
            m.d.comb += psram.write_data.eq(
                Mux(second_word, live_data[16:], live_data[:16]))

        # A 32-bit controller transfers an aligned PAIR of words, so the odd
        # address bit picks a half rather than a transaction.
        #
        # It must stay EVEN. Compensating a read skew by asking for the word
        # before was tried and HANGS the board: an odd start address gives the
        # DQS gearing nothing to align to, `datavalid` never arrives, the beat
        # never acknowledges and the CPU stalls in the load forever. The skew is
        # corrected in the PHY's read window instead, where it belongs.
        aligned = Signal(32)
        m.d.comb += aligned.eq(live_address & ~1)

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.start_transfer.eq(start),
            psram.address.eq(aligned if self._dqs else live_address),

            # These are held from the start edge through the whole transfer. Earlier
            # pulsed drivers returned plausible wrong data rather than failing.
            psram.perform_write.eq(Mux(start, selected_write, writing)),
            # `current_final` already folds in the start edge through `half_done`
            # and `streaming`, so it no longer needs a Mux of its own here.
            psram.final_word.eq(current_final),
            port.granted.eq(active & (owner == OWNER_CSR)),
            port.in_data.eq(Mux(live_address[0], read_word[16:32], read_word[:16])
                            if self._dqs else read_word),
            port.in_valid.eq(active & (owner == OWNER_CSR) & word_event),

            mmap.granted.eq(active & (owner == OWNER_WISHBONE)),
            mmap.in_data.eq(read_word),
            mmap.in_valid.eq(active & (owner == OWNER_WISHBONE) & word_event),

            self.jtag_ack.eq(active & (owner == OWNER_JTAG) & psram.write_ready),

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
            self.probe_want.eq(mmap.req & fsm.ongoing("IDLE")),
            self.probe_arming.eq(fsm.ongoing("STARTING")),
            self.probe_cyc.eq(mmap.bus.cyc),
        ]

        return m
