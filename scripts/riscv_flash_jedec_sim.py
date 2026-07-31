#!/usr/bin/env python3
#
# Drive the JEDEC ID sequence through SPIController in simulation.
# SPDX-License-Identifier: BSD-3-Clause

"""
Runs the exact register sequence the firmware uses to read the JEDEC ID, with a
flash model on the pins, and reports where it stops.

On hardware the ID reads back as all zeros. Two causes have already been ruled
out: the crossbar starving the controller (fixed, and proven fixed in
scripts/riscv_flash_crossbar_sim.py), and the memory-mapped read path being
broken (it is not -- it returns bytes that match `apollo flash-read` exactly).
So the fault is in the controller path itself, and on hardware every signal in
that path is inside the FPGA and invisible.

The model implements one command, 0x9f, and returns three bytes. It is a test
fixture rather than a flash emulator: a real W25Q32 has status registers,
program and erase, and mode continuation, none of which is exercised here and
all of which would be untested code posing as a reference.

    ./scripts/riscv_flash_jedec_sim.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_flash_jedec_sim.log"

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.sim import Simulator

from luna_soc.gateware.core import spiflash as spiflash_core
from luna_soc.gateware.core.spiflash import SPIPHYController
from luna_soc.gateware.core.spiflash.controller import SPIController

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
from vexii_flash import FairSPIControlPortCrossbar, ModalSPIFlashMemoryMap

# The ID this board's part returns. Used as the model's answer, NOT as a pass
# condition -- the check is that three bytes come back at all and match what the
# model was told to send.
MODEL_ID = [0xEF, 0x40, 0x16]

# CSR byte offsets within SPIController, verified against the elaborated memory
# map rather than assumed.
REG_PHY, REG_CS, REG_STATUS, REG_RX, REG_TX = 0x0, 0x4, 0x5, 0x8, 0xC

# Bounds the run. Each 8-bit transfer at divisor 0 is ~16 sync cycles, and the
# sequence is four transfers plus FIFO and FSM overhead, so a few thousand
# cycles is ample. On expiry the testbench reports what it collected, which is
# the diagnostic -- a hang would not be.
MAX_CYCLES = 4000


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


class Harness(wiring.Component):
    """Controller, crossbar, PHY and a flash model, wired as the SoC wires them."""

    def __init__(self, *, with_mmap):
        self._with_mmap = with_mmap
        super().__init__({})

    def elaborate(self, platform):
        m = Module()

        # Pins, with dq.i driven by the model below.
        self.dq_i = Signal(4)
        self.dq_o = Signal(4)
        self.dq_oe = Signal()
        self.cs_o = Signal()
        self.sck = Signal()

        class Pads:
            pass
        pads = Pads()
        pads.dq = type("DQ", (), {})()
        pads.dq.i, pads.dq.o, pads.dq.oe = self.dq_i, self.dq_o, self.dq_oe
        pads.cs = type("CS", (), {})()
        pads.cs.o = self.cs_o
        pads.sck = self.sck

        m.submodules.phy = phy = SPIPHYController(pads=pads, divisor=0,
                                                 domain="sync")
        m.submodules.ctrl = self.ctrl = ctrl = SPIController(
            data_width=32, name="spi0", domain="sync")

        if self._with_mmap:
            # The memory map is present and idle-but-holding-cs, which is the
            # state that starved the controller through upstream's crossbar.
            m.submodules.mmap = mmap = ModalSPIFlashMemoryMap(
                size=4 * 1024 * 1024, mode="single", name="spiflash",
                domain="sync")
            m.submodules.xbar = xbar = FairSPIControlPortCrossbar(
                data_width=32, num_ports=2, domain="sync")
            wiring.connect(m, ctrl.source, xbar.slave0.source)
            wiring.connect(m, ctrl.sink, xbar.slave0.sink)
            m.d.comb += xbar.slave0.cs.eq(ctrl.cs)
            wiring.connect(m, mmap.source, xbar.slave1.source)
            wiring.connect(m, mmap.sink, xbar.slave1.sink)
            m.d.comb += xbar.slave1.cs.eq(mmap.cs)
            wiring.connect(m, xbar.controller.source, phy.source)
            wiring.connect(m, xbar.controller.sink, phy.sink)
            m.d.comb += phy.cs.eq(xbar.controller.cs)
        else:
            wiring.connect(m, ctrl.source, phy.source)
            wiring.connect(m, ctrl.sink, phy.sink)
            m.d.comb += phy.cs.eq(ctrl.cs)

        return m


async def csr_write(ctx, bus, addr, value, width=1):
    """Write `width` bytes to the CSR bus, little end first, as the bridge does.

    The final byte of the burst is what commits the register in amaranth-soc, so
    `addr + width - 1` must be the register's last byte. Writing past it leaves
    the commit strobe on a byte the register does not own and the write is
    silently dropped -- confirmed by sweeping start/width against SPIController:
    0x8 width 8 and 0xc width 4 both commit, 0xc width 8 does not.
    """
    for i in range(width):
        ctx.set(bus.addr, addr + i)
        ctx.set(bus.w_data, (value >> (8 * i)) & 0xFF)
        ctx.set(bus.w_stb, 1)
        await ctx.tick()
    ctx.set(bus.w_stb, 0)
    await ctx.tick()


async def csr_read(ctx, bus, addr, width=1):
    """Read `width` bytes from the CSR bus."""
    value = 0
    for i in range(width):
        ctx.set(bus.addr, addr + i)
        ctx.set(bus.r_stb, 1)
        await ctx.tick()
        ctx.set(bus.r_stb, 0)
        await ctx.tick()
        value |= ctx.get(bus.r_data) << (8 * i)
    return value


def run(handle, with_mmap):
    dut = Harness(with_mmap=with_mmap)
    result = {"bytes": [], "cs_high_cycles": 0, "sck_edges": 0}

    async def flash_model(ctx):
        """Answer 0x9f with MODEL_ID, bit-banged on the falling edge of SCK."""
        shift_in, bit_count, cmd, out_bits = 0, 0, None, []
        last_sck, last_cs = 0, 1
        for _ in range(MAX_CYCLES):
            await ctx.tick()
            cs = ctx.get(dut.cs_o)
            sck = ctx.get(dut.sck)
            if cs and not last_cs:
                shift_in, bit_count, cmd, out_bits = 0, 0, None, []
            if not cs:
                if sck and not last_sck:
                    result["sck_edges"] += 1
                    shift_in = ((shift_in << 1) | ctx.get(dut.dq_o) & 1) & 0xFF
                    bit_count += 1
                    if bit_count == 8 and cmd is None:
                        cmd = shift_in
                        if cmd == 0x9F:
                            for byte in MODEL_ID:
                                out_bits += [(byte >> b) & 1
                                             for b in range(7, -1, -1)]
                if not sck and last_sck and out_bits:
                    ctx.set(dut.dq_i, (out_bits.pop(0) << 1))
            last_sck, last_cs = sck, cs

    async def driver(ctx):
        bus = dut.ctrl.bus

        # Exactly the firmware's sequence: queue all four transfers, then drain.
        await csr_write(ctx, bus, REG_PHY, 8 | (1 << 6) | (0x1 << 10), width=4)
        await csr_write(ctx, bus, REG_TX, 0x9F000000, width=4)
        await csr_write(ctx, bus, REG_PHY, 8 | (1 << 6) | (0x0 << 10), width=4)
        for _ in range(3):
            await csr_write(ctx, bus, REG_TX, 0, width=4)

        for _ in range(4):
            for _ in range(MAX_CYCLES // 8):
                status = await csr_read(ctx, bus, REG_STATUS)
                if status & 0x1:
                    break
                if ctx.get(dut.cs_o) == 0:
                    result["cs_high_cycles"] += 1
            else:
                result["bytes"].append(None)
                continue
            result["bytes"].append(await csr_read(ctx, bus, REG_RX, width=4))

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(flash_model, background=True)
    sim.add_testbench(driver)
    sim.run()

    label = "with the memory map present" if with_mmap else "controller alone"
    emit(handle, f"  {label}:")
    emit(handle, f"    SCK rising edges seen by the model: "
                 f"{result['sck_edges']}")
    got = result["bytes"]
    emit(handle, f"    RX words: "
                 f"{['timeout' if b is None else format(b, '08x') for b in got]}")
    if len(got) == 4 and all(b is not None for b in got):
        ident = ((got[1] & 0xFF) << 16) | ((got[2] & 0xFF) << 8) | (got[3] & 0xFF)
        emit(handle, f"    assembled ID: {ident:06x}")
        return ident
    return None


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        emit(handle, "JEDEC read through SPIController, in simulation")
        emit(handle, f"model answers 0x9f with "
                     f"{''.join(f'{b:02x}' for b in MODEL_ID)}")
        emit(handle)

        alone = run(handle, with_mmap=False)
        emit(handle)
        shared = run(handle, with_mmap=True)

        emit(handle)
        want = int("".join(f"{b:02x}" for b in MODEL_ID), 16)
        if alone == want and shared == want:
            emit(handle, "Both configurations read the model's ID. The "
                         "controller path and the arbitration are correct here, "
                         "so a hardware failure is in the pins or the PHY "
                         "timing, not this logic.")
        elif alone == want and shared != want:
            emit(handle, "The controller works alone and fails sharing the "
                         "PHY -- arbitration is still wrong.")
        else:
            emit(handle, "The controller fails even alone; the fault is in the "
                         "controller register sequence or the PHY.")

        emit(handle)
        emit(handle, f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
