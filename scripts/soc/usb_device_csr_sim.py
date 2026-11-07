#!/usr/bin/env python3
#
# peripherals.usb_device.UsbDevicePort: CSR plumbing to the engine's command port, across sync/usb.
# SPDX-License-Identifier: BSD-3-Clause

"""
The CSR wrapper alone (ULPI pins idle, no host): role switch, op handshake, snapshot, events. #612.

    python3 scripts/soc/usb_device_csr_sim.py

- `sync` 50 MHz and `usb` 60 MHz, so every crossing is a real one.
- Proof the command path reaches the engine: a command with a stale `cmd_seq` bumps `STALE`
  in the snapshot; one with the current `seq` does not.
- Not covered: the ULPI translator against a PHY, and anything the engine does on the wire
  (`usb_device_ep0_sim.py`, `usb_target_sim.py own_*`).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from amaranth import ClockDomain, Module
from amaranth.sim import Simulator
from devlog import emit
from sim_check_harness import Checks
from peripherals import usb_device as ud
from usbcore.device import CMD_STATUS

SYNC_PERIOD, USB_PERIOD = 1 / 50e6, 1 / 60e6
# A CSR op crosses twice (toggle out, toggle back), each 2-3 destination cycles; 40 sync cycles, 2x.
OP_CLOCKS = 40
# Role switch: QUIET_CYCLES usb cycles of dir low plus the crossings; 60 sync cycles, 2x.
ROLE_CLOCKS = 60


class Csr:
    def __init__(self, ctx, bus):
        self.ctx, self.bus = ctx, bus

    async def write(self, addr, value):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.w_data, value)
        c.set(self.bus.w_stb, 1)
        await c.tick("sync")
        c.set(self.bus.w_stb, 0)
        # The bridge registers strobes one cycle late; the SoC's bus gives the CPU that gap.
        await c.tick("sync")

    async def read(self, addr):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.r_stb, 1)
        await c.tick("sync")
        value = int(c.get(self.bus.r_data))
        c.set(self.bus.r_stb, 0)
        return value

    async def until(self, addr, mask, want, clocks):
        for _ in range(clocks):
            if (await self.read(addr)) & mask == want:
                return True
        return False

    async def op(self, code):
        await self.write(ud.REG_OP, code)
        return await self.until(ud.REG_STATUS, 0x08, 0, OP_CLOCKS)


def main():
    checks = Checks(emit)
    m = Module()
    m.domains.sync = ClockDomain("sync")
    m.domains.usb = ClockDomain("usb")
    port = ud.UsbDevicePort()
    m.submodules.port = port
    sim = Simulator(m)
    sim.add_clock(SYNC_PERIOD, domain="sync")
    sim.add_clock(USB_PERIOD, domain="usb")

    async def tb(ctx):
        csr = Csr(ctx, port.bus)
        ctx.set(port.regs_idle, 1)
        ctx.set(port.bus.r_stb, 0)

        status = await csr.read(ud.REG_STATUS)
        checks.check("reset: regs own the PHY, nothing busy", status & 0x0f == 0, f"status {status:#04x}")

        await csr.write(ud.REG_ROLE, ud.ROLE_DEVICE)
        ok = await csr.until(ud.REG_STATUS, 0x01, 0x01, ROLE_CLOCKS)
        checks.check("ROLE=device: STATUS.role set within the bound", ok)
        checks.check("ROLE=device: PHY pins handed over (regs see dir=1)", int(ctx.get(port.regs_dir)) == 1)

        # Engine is out of reset now: seq 0, stale 0.
        ok = await csr.op(ud.OP_SNAPSHOT)
        checks.check("SNAPSHOT completes (op_busy clears)", ok)
        checks.check("snapshot: stale 0, seq 0", (await csr.read(ud.REG_STALE)) == 0
                     and (await csr.read(ud.REG_SEQ)) == 0)

        await csr.write(ud.REG_CMD_SEQ, 7)
        await csr.write(ud.REG_CMD, CMD_STATUS)
        ok = await csr.op(ud.OP_CMD)
        await csr.op(ud.OP_SNAPSHOT)
        checks.check("CMD with a stale seq reaches the engine: STALE 1", ok and (await csr.read(ud.REG_STALE)) == 1)

        await csr.write(ud.REG_CMD_SEQ, 0)
        await csr.op(ud.OP_CMD)
        await csr.op(ud.OP_SNAPSHOT)
        checks.check("CMD with the current seq is not counted stale: STALE still 1",
                     (await csr.read(ud.REG_STALE)) == 1)

        # Op in flight drops a second OP and raises EVENTS.busy_drop; IRQ follows IRQ_EN.
        await csr.write(ud.REG_IRQ_EN, 0x10)
        await csr.write(ud.REG_OP, ud.OP_SNAPSHOT)
        await csr.write(ud.REG_OP, ud.OP_SNAPSHOT)
        events = await csr.read(ud.REG_EVENTS)
        checks.check("second OP while busy: EVENTS.busy_drop set", events & 0x10 == 0x10, f"events {events:#04x}")
        checks.check("busy_drop with IRQ_EN bit set raises irq", int(ctx.get(port.irq)) == 1)
        await csr.write(ud.REG_EVENTS, 0x10)
        await csr.read(ud.REG_STATUS)
        checks.check("EVENTS is write-1-to-clear: irq drops", int(ctx.get(port.irq)) == 0)
        await csr.until(ud.REG_STATUS, 0x08, 0, OP_CLOCKS)

        await csr.write(ud.REG_ROLE, ud.ROLE_REGS)
        ok = await csr.until(ud.REG_STATUS, 0x01, 0x00, ROLE_CLOCKS)
        checks.check("ROLE=regs: STATUS.role clears, PHY handed back", ok and int(ctx.get(port.regs_dir)) == 0)

    sim.add_testbench(tb)
    sim.run()
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
