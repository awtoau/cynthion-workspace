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
    CPU memory  a running CPU                  `HyperRAMWishbone`, two-word bursts
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

HYPERRAM_SIZE = 8 * 1024 * 1024


class HyperRAMWishbone(wiring.Component):
    """A 32-bit Wishbone window backed by two 16-bit HyperRAM words.

    The bus contains memory, not registers.

      * Reads only capture data and acknowledge the request.
      * Partial stores read, merge, and write because this controller has no mask port.
      * One request stays asserted until both 16-bit words complete.

    | signal | direction | meaning |
    |--------|-----------|---------|
    | `req` | out | one pending 32-bit transfer |
    | `req_addr` | out | first 16-bit HyperRAM word address |
    | `in_valid` | in | one returned or accepted 16-bit word |
    """

    def __init__(self, *, size=HYPERRAM_SIZE):
        if size <= 0 or size & (size - 1):
            raise ValueError("HyperRAM window size must be a power of two")
        if size % 4:
            raise ValueError("HyperRAM window size must contain whole Wishbone words")

        wb_addr_width = log2_int(size // 4)
        memory_map = MemoryMap(addr_width=log2_int(size), data_width=8)
        memory_map.add_resource(self, name=("memory", "hyperram"), size=size)

        super().__init__({
            "bus": wiring.In(wishbone.Signature(
                addr_width=wb_addr_width, data_width=32, granularity=8,
                features={"cti", "bte", "err"})),
            "req":       wiring.Out(1),
            "req_write": wiring.Out(1),
            "req_addr":  wiring.Out(32),
            "req_data":  wiring.Out(32),
            "granted":   wiring.In(1),
            "in_data":   wiring.In(16),
            "in_valid":  wiring.In(1),
        })
        self.bus.memory_map = memory_map

    def elaborate(self, platform):
        m = Module()

        pending = Signal()
        answered = Signal()
        is_write = Signal()
        address = Signal(len(self.bus.adr))
        write_data = Signal(32)
        select = Signal(4)
        read_low = Signal(16)
        second_word = Signal()
        rmw_read = Signal()

        request = self.bus.cyc & self.bus.stb & ~pending & ~answered

        m.d.comb += [
            self.bus.ack.eq(answered),
            self.bus.err.eq(0),
            self.req.eq(pending),
            self.req_write.eq(is_write & ~rmw_read),
            self.req_addr.eq(address << 1),
            self.req_data.eq(write_data),
        ]

        # `answered` separates consecutive transfers while the initiator releases STB.
        # This keeps the peripheral safe without relying on the SoC's response stage.
        m.d.sync += answered.eq(0)

        with m.If(request):
            m.d.sync += [
                pending.eq(1),
                is_write.eq(self.bus.we),
                address.eq(self.bus.adr),
                write_data.eq(self.bus.dat_w),
                select.eq(self.bus.sel),
                second_word.eq(0),
                rmw_read.eq(self.bus.we & (self.bus.sel != 0b1111)),
            ]

        with m.If(pending & self.granted & self.in_valid):
            with m.If(~second_word):
                with m.If(~is_write | rmw_read):
                    m.d.sync += read_low.eq(self.in_data)
                m.d.sync += second_word.eq(1)
            with m.Else():
                with m.If(rmw_read):
                    old_data = Cat(read_low, self.in_data)
                    m.d.sync += [
                        write_data.eq(Cat(*[
                            Mux(select[index], write_data.word_select(index, 8),
                                old_data.word_select(index, 8))
                            for index in range(4)
                        ])),
                        rmw_read.eq(0),
                        second_word.eq(0),
                    ]
                with m.Elif(~is_write):
                    m.d.sync += self.bus.dat_r.eq(Cat(read_low, self.in_data))
                with m.If(~rmw_read):
                    m.d.sync += [
                        pending.eq(0),
                        answered.eq(1),
                        second_word.eq(0),
                    ]

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
    | 3 | CPU Wishbone window | 32 bits in one two-word burst |

    Attributes
    ----------
    port : HyperRAMBoot
        The CPU's CSR port; add `port.bus` to the SoC's CSR decoder.
    mmap : HyperRAMWishbone
        The CPU's memory port; add `mmap.bus` to the SoC's Wishbone decoder.
    jtag_req, jtag_addr, jtag_data, jtag_ack : Signal
        The second requester, in `sync`. Wire these to a `JTAGStager`.
    """

    def __init__(self, *, interface=None):
        self.port = HyperRAMBoot()
        self.mmap = HyperRAMWishbone()
        self._interface = interface

        self.jtag_req  = Signal()
        self.jtag_addr = Signal(32)
        self.jtag_data = Signal(16)
        self.jtag_ack  = Signal()

    def elaborate(self, platform):
        m = Module()

        if self._interface is None:
            ram_bus = platform.request("ram")
            psram_phy = HyperRAMPHY(bus=ram_bus)
            psram = HyperRAMInterface(phy=psram_phy.phy)
            m.submodules += [psram_phy, psram]
            m.d.comb += ram_bus.reset.o.eq(0)
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

        # The first ready advances a 32-bit request to its upper half. `final_word`
        # then stays high through recovery; pulsing it leaves the controller bursting.
        with m.If(active & wide & ~second_word & word_event):
            m.d.sync += second_word.eq(1)

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.start_transfer.eq(start),
            psram.address.eq(Mux(start, selected_address, address)),

            # These are held from the start edge through the whole transfer. Earlier
            # pulsed drivers returned plausible wrong data rather than failing.
            psram.perform_write.eq(Mux(start, selected_write, writing)),
            psram.final_word.eq(Mux(start, ~selected_wide,
                                    ~wide | second_word)),
            psram.write_data.eq(Mux(
                start, selected_data[:16],
                Mux(second_word, write_data[16:], write_data[:16]))),
            port.granted.eq(active & (owner == OWNER_CSR)),
            port.in_data.eq(psram.read_data),
            port.in_valid.eq(active & (owner == OWNER_CSR) & word_event),

            mmap.granted.eq(active & (owner == OWNER_WISHBONE)),
            mmap.in_data.eq(psram.read_data),
            mmap.in_valid.eq(active & (owner == OWNER_WISHBONE) & word_event),

            self.jtag_ack.eq(active & (owner == OWNER_JTAG) & psram.write_ready),
        ]

        return m
