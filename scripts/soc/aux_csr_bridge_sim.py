#!/usr/bin/env python3
#
# peripherals.aux_csr_bridge.AuxCsrBridge against a toy Wishbone slave. #612
# SPDX-License-Identifier: BSD-3-Clause

"""
Write/read/events framing, an ERR answer, and resync after an unknown opcode.

    python3 scripts/soc/aux_csr_bridge_sim.py

- Slave: 16 words at byte addresses 0x1000..0x103f, ACK one cycle after STB; anything else answers ERR.
- Not covered: the AUX USB transport, and `bus/fault.py` (its own sims).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from amaranth import ClockDomain, Module
from amaranth.sim import Simulator
from devlog import emit
from sim_check_harness import Checks
from peripherals.aux_csr_bridge import AuxCsrBridge, OP_EVENTS, OP_READ, OP_WRITE

# A reply byte appears within the FSM's few states plus the bus beat; 40 cycles, >4x.
REPLY_CLOCKS = 40


def main():
    checks = Checks(emit)
    m = Module()
    m.domains.sync = ClockDomain("sync")
    dut = AuxCsrBridge()
    m.submodules.dut = dut
    sim = Simulator(m)
    sim.add_clock(1 / 60e6)
    mem = {}

    async def slave(ctx):
        while True:
            await ctx.tick()
            if ctx.get(dut.wb.cyc) and ctx.get(dut.wb.stb):
                addr = int(ctx.get(dut.wb.adr)) << 2
                await ctx.tick()
                ok = 0x1000 <= addr < 0x1040
                if ok and ctx.get(dut.wb.we):
                    mem[addr] = int(ctx.get(dut.wb.dat_w))
                ctx.set(dut.wb.dat_r, mem.get(addr, 0) if ok else 0)
                ctx.set(dut.wb.ack, int(ok))
                ctx.set(dut.wb.err, int(not ok))
                await ctx.tick()
                ctx.set(dut.wb.ack, 0)
                ctx.set(dut.wb.err, 0)

    async def send(ctx, data):
        for byte in data:
            ctx.set(dut.rx.payload, byte)
            ctx.set(dut.rx.valid, 1)
            await ctx.tick().until(dut.rx.ready)
        ctx.set(dut.rx.valid, 0)

    async def reply(ctx, n):
        got = []
        ctx.set(dut.tx.ready, 1)
        for _ in range(REPLY_CLOCKS):
            if ctx.get(dut.tx.valid):
                got.append(int(ctx.get(dut.tx.payload)))
            await ctx.tick()
            if len(got) == n:
                break
        return got

    def le(value):
        return list(value.to_bytes(4, "little"))

    async def tb(ctx):
        ctx.set(dut.tx.ready, 0)
        await send(ctx, [OP_WRITE, *le(0x1008), *le(0xdeadbeef)])
        checks.check("write: status 0", await reply(ctx, 1) == [0])
        await send(ctx, [OP_READ, *le(0x1008)])
        checks.check("read back: data then status 0", await reply(ctx, 5) == [*le(0xdeadbeef), 0])
        await send(ctx, [OP_READ, *le(0x2000)])
        got = await reply(ctx, 5)
        checks.check("read of an unclaimed address: status 1 (ERR)", got[4:] == [1], f"{got}")
        await send(ctx, [OP_WRITE, *le(0x3000), *le(1)])
        checks.check("write to an unclaimed address: status 1", await reply(ctx, 1) == [1])
        ctx.set(dut.events, 1)
        await send(ctx, [OP_EVENTS])
        got = await reply(ctx, 2)
        checks.check("events: bit set, status 0", got == [1, 0], f"{got}")
        await send(ctx, [0x7f, OP_READ, *le(0x1008)])
        checks.check("unknown opcode dropped, next frame served",
                     await reply(ctx, 5) == [*le(0xdeadbeef), 0])

    sim.add_testbench(tb)
    sim.add_testbench(slave, background=True)
    sim.run()
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
