#!/usr/bin/env python3
#
# HyperRAM throughput test: write a pattern, read it back, verify and time it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures HyperRAM read and write throughput and checks the data.

Built in the same shape that worked for the flash: JTAG registers for control
and results, a block-RAM capture of what came back, and verification against a
pattern the host can recompute. A throughput figure on its own cannot tell a
working transfer from a fast stream of nonsense -- the flash work produced two
wrong conclusions from a summary statistic before a capture buffer settled it.

Unlike the flash there is no independent reference path here: nothing else on
the board can read this chip. So the test writes a known pattern first and
verifies the read against it. A counting pattern is used rather than a constant
so that a stuck bus, a shifted word and a duplicated burst all look different
from a correct read.

The clock is what is being pushed. LUNA's DQS interface uses the ECP5's DQS
logic, which is what makes higher rates plausible at all: the strobe travels
with the data, so the round-trip delay that capped the flash does not apply in
the same way.
"""

from amaranth                            import (Cat, Const, Elaboratable, Module,
                                                 Signal, unsigned)
from amaranth.lib.memory                 import Memory
from amaranth.lib                        import io

from luna.gateware.architecture.car      import LunaECP5DomainGenerator
from luna.gateware.interface.jtag        import JTAGRegisterInterface
from luna.gateware.interface.psram       import HyperRAMPHY, HyperRAMInterface


# The clock under test. HyperRAM is DDR, so the data rate is twice this on an
# 8-bit bus: 16 bits per clock, 2 bytes, so bytes/s = clock * 2.
CLOCK_MHZ = 60

# Words to transfer per run. 1024 32-bit words is 4 KiB, long enough that the
# command and latency phases stop dominating the figure.
TRANSFER_WORDS = 2048
CAPTURE_DEPTH  = 256

APPLET_ID = 0x48524150   # "HRAP"

REGISTER_ID          = 1
REGISTER_STATUS      = 2   # done flags, error count
REGISTER_WRITE_TIME  = 3   # cycles the write burst occupied
REGISTER_READ_TIME   = 4   # cycles the read burst occupied
REGISTER_CAPTURE_ADDR = 5  # write: word index into the capture buffer
REGISTER_CAPTURE_DATA = 6  # read: that word
REGISTER_FIRST_WORD  = 7   # first word read back, for a quick sanity check


def pattern_for(index):
    """The 16-bit value written at a given word index.

    The interface is 16 bits wide, not 32 -- an earlier version of this test
    assumed 32 and produced data that looked like a bit-shift on readback,
    which is a convincing impersonation of a timing fault.

    Index in the low bits, its complement in the high bits: a stuck lane, a
    shifted word and a repeated burst then all look different from each other
    and from correct data, where a constant fill would hide all three.
    """
    return (((~index) & 0xFF) << 8) | (index & 0xFF)


class HyperRAMSpeedTest(Elaboratable):
    """ Writes a pattern to HyperRAM, reads it back, verifies and times both. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies={"fast": CLOCK_MHZ * 2,
                               "sync": CLOCK_MHZ,
                               "usb":  60})

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        # LUNA's DQS PHY and interface. The DQS variant matters: the strobe
        # travels with the data, so read timing does not depend on a fixed
        # round-trip estimate the way the flash's did.
        # The non-DQS PHY takes an ordinary platform.request() bus, which is
        # what makes it usable here: the board's HyperRAM clock is a
        # differential pair and Amaranth builds that buffer itself. The DQS PHY
        # assigns to bus.clk as a single net, which a DifferentialPort is not,
        # and interposing a buffer breaks nextpnr's rule that DELAYG must sit
        # directly on a top-level pin.
        ram_bus = platform.request("ram")
        psram_phy = HyperRAMPHY(bus=ram_bus)
        m.submodules.psram_phy = psram_phy
        psram = HyperRAMInterface(phy=psram_phy.phy)
        m.submodules.psram = psram

        index      = Signal(range(TRANSFER_WORDS + 1))
        errors     = Signal(16)
        write_time = Signal(32)
        read_time  = Signal(32)
        first_word = Signal(16)
        write_done = Signal()
        read_done  = Signal()

        # 16 bits: the interface's width, not 32.
        expected = Signal(16)
        m.d.comb += expected.eq(Cat(index[:8], ~index[:8]))

        #
        # Capture buffer for the first words read back.
        #
        memory = Memory(shape=unsigned(16), depth=CAPTURE_DEPTH, init=[])
        m.submodules.memory = memory
        write_port = memory.write_port()
        read_port  = memory.read_port(domain="sync")

        capture_addr = Signal(range(CAPTURE_DEPTH))
        registers.add_register(REGISTER_CAPTURE_ADDR, value_signal=capture_addr)
        m.d.comb += read_port.addr.eq(capture_addr)

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.write_data.eq(expected),
        ]

        with m.FSM():
            # Give the PHY time to come out of reset before the first command.
            with m.State("INIT"):
                with m.If(psram.idle):
                    m.d.sync += index.eq(0)
                    m.next = "WRITE_START"

            with m.State("WRITE_START"):
                m.d.comb += [
                    psram.address.eq(0),
                    psram.perform_write.eq(1),
                    psram.start_transfer.eq(1),
                    psram.final_word.eq(TRANSFER_WORDS == 1),
                ]
                m.next = "WRITE"

            with m.State("WRITE"):
                m.d.sync += write_time.eq(write_time + 1)
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.final_word.eq(index == TRANSFER_WORDS - 1),
                ]
                with m.If(psram.write_ready):
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == TRANSFER_WORDS - 1):
                        m.d.sync += write_done.eq(1)
                        m.next = "WRITE_DRAIN"

            with m.State("WRITE_DRAIN"):
                m.d.sync += write_time.eq(write_time + 1)
                with m.If(psram.idle):
                    m.d.sync += index.eq(0)
                    m.next = "READ_START"

            with m.State("READ_START"):
                m.d.comb += [
                    psram.address.eq(0),
                    psram.perform_write.eq(0),
                    psram.start_transfer.eq(1),
                    psram.final_word.eq(TRANSFER_WORDS == 1),
                ]
                m.next = "READ"

            with m.State("READ"):
                m.d.sync += read_time.eq(read_time + 1)
                m.d.comb += [
                    psram.final_word.eq(index == TRANSFER_WORDS - 1),
                    write_port.addr.eq(index),
                    write_port.data.eq(psram.read_data),
                    write_port.en.eq(psram.read_ready & (index < CAPTURE_DEPTH)),
                ]
                with m.If(psram.read_ready):
                    with m.If(index == 0):
                        m.d.sync += first_word.eq(psram.read_data)
                    # Count mismatches rather than stopping at the first: how
                    # many are wrong separates a single glitch from a bus that
                    # is not working at all.
                    with m.If(psram.read_data != expected):
                        m.d.sync += errors.eq(errors + 1)
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == TRANSFER_WORDS - 1):
                        m.d.sync += read_done.eq(1)
                        m.next = "DONE"

            with m.State("DONE"):
                pass

        registers.add_read_only_register(REGISTER_WRITE_TIME, read=write_time)
        registers.add_read_only_register(REGISTER_READ_TIME,  read=read_time)
        registers.add_read_only_register(REGISTER_FIRST_WORD, read=first_word)
        registers.add_read_only_register(REGISTER_CAPTURE_DATA,
                                         read=read_port.data)
        registers.add_read_only_register(
            REGISTER_STATUS,
            read=Cat(write_done, read_done, Const(0, 6),
                     errors, Const(CLOCK_MHZ, 8)))

        return m
