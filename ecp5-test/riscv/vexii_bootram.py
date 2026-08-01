#!/usr/bin/env python3
#
# HyperRAM as a firmware staging buffer: Apollo writes it, the CPU boots from it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Lets Apollo load RISC-V firmware over JTAG without rebuilding the bitstream.

## The problem

Firmware is block RAM init, so it lives *inside* the bitstream. Changing one byte means
a full resynthesis -- about 60 s -- to produce a design whose logic is bit-for-bit
identical.

`ecpbram` can swap BRAM contents in a built bitstream in about a second, and that works,
but it needs the design synthesised with a known *random* placeholder image: it locates
the old contents by value, and a real firmware image is ~87% zeroes, which also fill
every unused BRAM tile on the die. It refuses with "Conflicting from pattern". Building
against a placeholder couples every rebuild to a magic file, and a stale `.config`
silently yields the wrong firmware.

## The path this takes

    Apollo --JTAG--> HyperRAM --bootloader--> block RAM --> run

Apollo writes the image into HyperRAM over JTAG, releases the CPU from reset, and a small
resident bootloader in block RAM copies it across and jumps to it. Nothing rewrites the
bitstream, nothing depends on matching synthesised contents, and the image can be as
large as HyperRAM (8 MiB) rather than as large as block RAM.

## Two masters, never at once

HyperRAM is reached by JTAG *and* by the CPU, but never simultaneously: Apollo holds the
CPU in reset while it writes, and only reads happen after the CPU is released. So this
uses a strict priority rather than a fair arbiter -- there is no contention to be fair
about. That is a deliberate contrast with `FairSPIControlPortCrossbar`, where two masters
genuinely do compete and starvation was a real bug.

## Why the CPU port is one word at a time

The JTAG side bursts through a FIFO, because that is what the upstream diagnostic does
and it is proven. The CPU side deliberately does not: it fetches a single 16-bit word,
auto-increments the address, and raises a valid flag the firmware polls.

No FIFO, no side-effecting reads, and no register whose value depends on how many times
it has been read -- the class of bug that cost a day on the SPI controller, where reading
`data` popped a FIFO and a cached line made every subsequent read return the first byte
forever.

It is slower: one HyperRAM transaction per word instead of a burst. For a 32 KiB image
that is roughly 8 ms at 60 MHz, against the ~60 s it replaces. Bursting is an
optimisation available later; correctness first.
"""

from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth_soc import csr

from luna.gateware.interface.psram import HyperRAMPHY, HyperRAMInterface

class HyperRAMBoot(wiring.Component):
    """CPU-side read port for HyperRAM: fetch a word, auto-increment, poll for valid.

    Registers, on a byte-wide CSR bus:

        0x00  addr     W  32   set the word address
        0x04  addr_rd  R  32   read it back; auto-increments after each fetch
        0x08  ctrl     W   1   bit 0: fetch the word at `addr`
        0x09  status   R   1   bit 0: `data` holds a fetched word
        0x0a  data_lo  R   8   low byte
        0x0b  data_hi  R   8   high byte
        0x0c  wdata    W  16   store this word at `addr`, then auto-increment

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

        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("addr", self._addr)
        builder.add("addr_rd", self._addr_rd)
        builder.add("ctrl", self._ctrl)
        builder.add("status", self._status)
        builder.add("data_lo", self._data_lo)
        builder.add("data_hi", self._data_hi)
        builder.add("wdata", self._wdata)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus": wiring.In(csr.Signature(addr_width=4, data_width=8)),

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
    """HyperRAM with a single CPU-side port.

    There is no JTAG loader here any more. Apollo used to stage images through
    `JTAGRegisterInterface`, and it worked -- but at 34 ms per 16-bit word, entirely USB
    round-trip bound, a 32 KiB image took about nine minutes: slower than the ~60 s
    bitstream rebuild it existed to replace. The CPU receives images over the USB bulk
    endpoint it already has and writes them here itself, which is the same transport
    `apollo flash-write` uses and roughly four orders of magnitude faster.

    That also removes the arbiter. The only two users were JTAG and the CPU; now the CPU
    stages an image and, after a reset, the bootloader reads it back -- the same master in
    two phases, which cannot collide with itself.

    Attributes
    ----------
    port : HyperRAMBoot
        The CPU's CSR port; add `port.bus` to the SoC's CSR decoder.
    """

    def __init__(self):
        self.port = HyperRAMBoot()

    def elaborate(self, platform):
        m = Module()

        ram_bus = platform.request("ram")
        psram_phy = HyperRAMPHY(bus=ram_bus)
        psram = HyperRAMInterface(phy=psram_phy.phy)
        m.submodules += [psram_phy, psram]

        m.submodules.port = port = self.port

        # Held for the whole transfer, not pulsed. `perform_write` and `write_data` must
        # stay asserted until the controller finishes: pulsing them produced plausible
        # wrong answers rather than failures in earlier HyperRAM work.
        writing = Signal()

        # One-cycle strobe on the IDLE -> BUSY transition. Deriving this from `port.req`
        # would hold start_transfer high for as long as the request stood, restarting the
        # transfer underneath itself.
        start = Signal()

        with m.FSM():
            with m.State("IDLE"):
                with m.If(port.req):
                    m.d.comb += start.eq(1)
                    m.d.sync += writing.eq(port.req_write)
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
                    m.d.sync += writing.eq(0)
                    m.next = "IDLE"

        m.d.comb += [
            ram_bus.reset.o.eq(0),
            psram.single_page.eq(0),

            # Memory, never HyperRAM's own configuration registers. Those are only needed
            # to identify the part, which `ecp5-test/hyperram/hyperram_identify.py` does
            # in its own bitstream.
            psram.register_space.eq(0),

            # Single-word transfers, so every word is the final one.
            psram.final_word.eq(1),

            psram.perform_write.eq(writing | (start & port.req_write)),
            psram.start_transfer.eq(start),
            psram.address.eq(port.req_addr),
            psram.write_data.eq(port.req_data),

            port.granted.eq(1),
            port.in_data.eq(psram.read_data),

            # A write completes on `write_ready`, a read on `read_ready`. The CPU polls
            # one flag for both, so pick whichever event ends the current request.
            port.in_valid.eq(Mux(writing, psram.write_ready, psram.read_ready)),
        ]

        return m
