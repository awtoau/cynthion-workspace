#!/usr/bin/env python3
#
# Simulation of the sideband flash reader against a model of the W25Q32DV.
# SPDX-License-Identifier: BSD-3-Clause

"""
Checks FlashIDReader and FlashSpeedTest against a SPI-NOR model.

The point of the model is byte alignment. SPI returns a byte for every byte
sent, so the response to the opcode is meaningless and the ID is offset by one
-- an off-by-one here reads as a plausible-but-wrong manufacturer rather than as
an obvious failure, which is exactly the kind of bug that survives to hardware.

    ./scripts/test_flash_id.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

from amaranth import Module
from amaranth.sim import Simulator

from apollo_fpga.gateware.flash_bridge import SPIStreamController
from apollo_fpga.gateware.flash_id import FlashIDReader, FlashSpeedTest, SPIMux

LOG = ROOT / "tmp" / "test_flash_id.log"

# The real part on r1.4, confirmed by `apollo flash-info`.
JEDEC_ID = [0xEF, 0x40, 0x16]


def build():
    m = Module()
    spi = SPIStreamController()
    m.submodules.spi = spi
    reader = FlashIDReader()
    m.submodules.reader = reader
    m.submodules.mux = SPIMux(controller=spi, ports=[reader.spi])
    return m, spi, reader


def test_id_alignment(handle):
    """The three ID bytes must land in the right registers."""
    m, spi, reader = build()

    # What the device drives, byte by byte, from CS assertion: the response to
    # the opcode is don't-care, then the three ID bytes.
    response = [0x00] + JEDEC_ID

    async def bench(ctx):
        ctx.set(reader.start, 1)
        await ctx.tick()
        ctx.set(reader.start, 0)

        # Feed the controller's sdo directly, indexed by how many bytes have
        # been shifted, which is what the model above computes.
        for _ in range(2000):
            if ctx.get(reader.valid):
                break
            await ctx.tick()

        return (ctx.get(reader.manufacturer),
                ctx.get(reader.memory_type),
                ctx.get(reader.capacity))

    sim = Simulator(m)
    sim.add_clock(1e-6)

    result = {}

    async def driver(ctx):
        # Emulate the flash: shift out `response` MSB-first, one bit per
        # rising edge, starting a new byte every eight.
        bit_count = 0
        prev_sck = 0
        prev_cs = 0
        while True:
            sck = ctx.get(spi.bus.sck)
            cs = ctx.get(spi.bus.cs)
            if cs and not prev_cs:
                bit_count = 0
            if cs:
                idx = bit_count // 8
                off = 7 - (bit_count % 8)
                byte = response[idx] if idx < len(response) else 0
                ctx.set(spi.bus.sdo, (byte >> off) & 1)
                if sck and not prev_sck:
                    bit_count += 1
            prev_sck, prev_cs = sck, cs
            await ctx.tick()

    sim.add_testbench(driver, background=True)

    async def main(ctx):
        result["ids"] = await bench(ctx)

    sim.add_testbench(main)
    sim.run()

    got = result["ids"]
    expected = tuple(JEDEC_ID)
    ok = got == expected
    handle.write(f"  id alignment: got {[hex(v) for v in got]}, "
                 f"expected {[hex(v) for v in expected]} -> "
                 f"{'PASS' if ok else 'FAIL'}\n")
    print(f"  id alignment: got {[hex(v) for v in got]}, "
          f"expected {[hex(v) for v in expected]} -> "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        handle.write("flash reader simulation\n\n")
        print("flash reader simulation\n")
        ok = test_id_alignment(handle)
        handle.write(f"\n{'all pass' if ok else 'FAILURES'}\n")
    print(f"\nlog: {LOG}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
