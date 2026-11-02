#!/usr/bin/env python3
#
# The role mux and CSR/FIFO shim (peripherals/usb_host.py), driven like firmware.
# SPDX-License-Identifier: BSD-3-Clause

"""
Stage 1 of the AOA adaptor, gateware only.

    ./scripts/soc/usb_host_shim_sim.py        # every check
    ./scripts/soc/usb_host_shim_sim.py -v     # and print every packet on the wire
    ./scripts/soc/usb_host_shim_sim.py raw    # one group

Groups:
- golden: `regs` behaviour through the mux is cycle-identical to bare `UlpiRegisters`,
  before and after a role round trip (off -> host -> regs -> raw -> regs).
- switching: bounded waits, PHY init on entering host, timeout paths.
- raw: CPU writes TXCMD + SETUP token + DATA0, the model device answers, RX FIFO tags.
- host: the usb_host_sie_sim scenarios through CSRs (enumeration, 512 B bulk IN).
- fifos: overflow, underflow, the 512 B `rx_len` case.

Bench: shim + UlpiRegisters + `ulpi_phy.UlpiPhyModel` + LUNA `ModelDevice`; `sync` and
`usb` are different frequencies. Not covered: silicon, bit-level framing, a real
USB3343's `nxt` gaps and RxError.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import DomainRenamer, Elaboratable, Module, Signal  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from peripherals.ulpi_window import UlpiRegisters  # noqa: E402
from peripherals.usb_host import (  # noqa: E402
    UsbPort, ROLE_OFF, ROLE_REGS, ROLE_HOST, ROLE_RAW, REG_ROLE, REG_CTRL, REG_IRQ_EN,
    REG_ROLE_STATUS, REG_LINK, REG_EVENTS, REG_RXPID, REG_ADDR, REG_EP, REG_RESP,
    REG_RXLEN_LO, REG_RXLEN_HI, REG_RX_DATA, REG_RX_TAG, REG_RX_LEVEL, REG_TX_DATA,
    REG_TX_LAST, REG_TX_CMD, REG_TX_LEVEL, CTRL_BUS_RESET, CTRL_START, CTRL_CLR_TX,
    CTRL_CLR_RX, EV_DONE, EV_LINE, EV_RX_OVERFLOW, EV_TX_OVERFLOW, EV_RX_UNDERFLOW,
    EV_BUSY_DROP, EV_TIMEOUT, QUIET_CYCLES)
from usb_host.model import (  # noqa: E402
    BULK_IN_ENDPOINT, BULK_MAX_PACKET_SIZE, CONTROL_MAX_PACKET_SIZE, PRODUCT_ID,
    VENDOR_ID, ModelDevice, Packet, connect_utmi,
    scale_timing_for_simulation)
from usb_host.ulpi_phy import UlpiPhyModel  # noqa: E402
from soc_board_sim import ModelPhy, PHY_IDENTITY  # noqa: E402
from usb_host_sie_sim import (  # noqa: E402
    GET_DESCRIPTOR_DEVICE, PID_ACK, PID_DATA0, PID_DATA1, PID_SETUP, SET_CONFIGURATION_1,
    crc16, crc5, setup_packet)

SYNC_PERIOD = 15e-9       # 66.7 MHz
USB_PERIOD = 16.667e-9    # 60 MHz; unrelated to sync on purpose

ULPI_REG_READ, ULPI_REG_WRITE = 0x01, 0x02

TYPE_SETUP, TYPE_IN, TYPE_OUT = 0, 1, 2
DEVICE_ADDRESS = 0x12

# Poll budgets. Each is an upper bound on a wait with a known expected duration.
BRINGUP_POLLS = 4000      # x 64 sync ticks; scaled reset + chirp is ~40k usb cycles
DONE_POLLS = 800          # x 16 sync ticks; one transaction is < 12k usb cycles


class Wire(Elaboratable):
    """`model.connect_utmi` for a bench whose single clock is called `usb`."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def elaborate(self, platform):
        m = Module()
        connect_utmi(m, self.a, self.b)
        return m


class Bench(Elaboratable):
    """UsbPort + UlpiRegisters + PHY model (+ optional device on its wire)."""

    def __init__(self, *, with_device=True, full_speed_only=False, func_ctrl=0x41,
                 **port_kwargs):
        self.port = UsbPort(**port_kwargs)
        self.regs = UlpiRegisters()
        self.phy = UlpiPhyModel(func_ctrl=func_ctrl)
        self.force_dir = Signal()
        self.with_device = with_device
        if with_device:
            self.device = ModelDevice(full_speed_only=full_speed_only)

    def elaborate(self, platform):
        m = Module()
        p, r, phy = self.port, self.regs, self.phy
        m.submodules.port, m.submodules.regs, m.submodules.phy = p, r, phy
        m.d.comb += [
            p.data_i.eq(phy.data_o), p.dir_i.eq(phy.dir | self.force_dir), p.nxt_i.eq(phy.nxt),
            phy.data_i.eq(p.data_o), phy.stp.eq(p.stp_o),
            r.data_i.eq(phy.data_o), r.dir_i.eq(p.regs_dir), r.nxt_i.eq(p.regs_nxt),
            p.regs_data_o.eq(r.data_o), p.regs_data_oe.eq(r.data_oe),
            p.regs_stp_o.eq(r.stp_o), p.regs_idle.eq(r.usb_idle),
        ]
        if self.with_device:
            m.submodules.device = self.device
            m.submodules.wire = DomainRenamer({"sync": "usb"})(Wire(phy.utmi, self.device.utmi))
        return m


class Csr:
    """Byte CSR access: strobe, sample, settle tick (same timing as soc_board_sim.Bus)."""

    def __init__(self, ctx, bus):
        self.ctx, self.bus = ctx, bus

    async def read(self, addr):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.r_stb, 1)
        await c.tick()
        c.set(self.bus.r_stb, 0)
        value = c.get(self.bus.r_data)
        await c.tick()
        return value

    async def write(self, addr, value):
        c = self.ctx
        c.set(self.bus.addr, addr)
        c.set(self.bus.w_data, value)
        c.set(self.bus.w_stb, 1)
        await c.tick()
        c.set(self.bus.w_stb, 0)
        await c.tick()

    async def idle(self, ticks):
        for _ in range(ticks):
            await self.ctx.tick()


def capture_usb(device_utmi, packets):
    """`model.capture_host_packets`, clocked by `usb` (that helper ticks `sync`)."""
    async def process(ctx):
        cycle, current = 0, []
        while True:
            _, _, valid, active, data = await ctx.tick("usb").sample(
                device_utmi.rx_valid, device_utmi.rx_active, device_utmi.rx_data)
            if valid:
                current.append(int(data))
            if current and not active:
                packets.append(Packet(cycle, current))
                current = []
            cycle += 1
    return process


def run(bench, testbench, extra=(), processes=()):
    sim = Simulator(Fragment.get(bench, None))
    sim.add_clock(SYNC_PERIOD)
    sim.add_clock(USB_PERIOD, domain="usb")
    for process in processes:
        sim.add_process(process)
    for process in extra:
        sim.add_testbench(process, background=True)
    sim.add_testbench(testbench)
    sim.run()


async def poll(csr, addr, test, polls, gap=0):
    """Read `addr` until `test(value)`; returns the value, or None after `polls` reads."""
    for _ in range(polls):
        value = await csr.read(addr)
        if test(value):
            return value
        if gap:
            await csr.idle(gap)
    return None


async def set_role(csr, role, polls=200):
    """Write ROLE, wait for the switch to finish. Returns ROLE_STATUS or None."""
    await csr.write(REG_ROLE, role)
    return await poll(csr, REG_ROLE_STATUS, lambda v: not v & 0x04, polls)


def role_of(status):
    return None if status is None else status & 3


def timed_out(status):
    return bool(status & 0x08)


# ---------------------------------------------------------------------------
# golden: regs through the mux == bare UlpiRegisters
# ---------------------------------------------------------------------------

async def ulpi_settle(csr, limit=4000):
    """Spin on UlpiRegisters STATUS.busy (bound 4096 usb cycles; a poll is 2 ticks)."""
    for _ in range(limit):
        status = await csr.read(3)
        if not status & 1:
            return status
    return None


async def regs_sequence(csr, mark):
    """Fixed UlpiRegisters traffic: ID reads and a scratch write/read."""
    seen = []
    for address in (0x00, 0x01, 0x02, 0x03):
        mark(True)
        await csr.write(0, address)
        await csr.write(2, ULPI_REG_READ)
        status = await ulpi_settle(csr)
        mark(False)
        seen.append((address, status, await csr.read(1)))
    mark(True)
    await csr.write(0, 0x16)
    await csr.write(1, 0x5A)
    await csr.write(2, ULPI_REG_WRITE)
    seen.append(("w", await ulpi_settle(csr)))
    mark(False)
    mark(True)
    await csr.write(2, ULPI_REG_READ)
    status = await ulpi_settle(csr)
    mark(False)
    seen.append((0x16, status, await csr.read(1)))
    return seen


def trim_idle(trace):
    """Drop leading/trailing cycles where the link drives nothing: NOP, oe, no stp."""
    idle = (0, 1, 0)
    lo, hi = 0, len(trace)
    while lo < hi and trace[lo] == idle:
        lo += 1
    while hi > lo and trace[hi - 1] == idle:
        hi -= 1
    return trace[lo:hi]


def golden_run(through_port):
    """The fixed regs traffic twice; between them a role round trip if through the port."""
    phy = ModelPhy({**PHY_IDENTITY, 0x16: 0x00})
    regs = UlpiRegisters()
    pin_data, pin_dir, pin_nxt = Signal(8), Signal(), Signal()
    top = Module()
    top.submodules.regs = regs
    if through_port:
        port = UsbPort(switch_limit=3000)
        top.submodules.port = port
        top.d.comb += [
            regs.dir_i.eq(port.regs_dir), regs.nxt_i.eq(port.regs_nxt),
            regs.data_i.eq(pin_data),
            port.regs_data_o.eq(regs.data_o), port.regs_data_oe.eq(regs.data_oe),
            port.regs_stp_o.eq(regs.stp_o), port.regs_idle.eq(regs.usb_idle),
            port.data_i.eq(pin_data), port.dir_i.eq(pin_dir), port.nxt_i.eq(pin_nxt),
        ]
        pins = (port.data_o, port.data_oe, port.stp_o)
    else:
        top.d.comb += [regs.dir_i.eq(pin_dir), regs.nxt_i.eq(pin_nxt),
                       regs.data_i.eq(pin_data)]
        pins = (regs.data_o, regs.data_oe, regs.stp_o)

    state = {"cur": None, "traces": [[], []], "phase": 0, "visible": [], "roles": []}

    async def wires(ctx):
        while True:
            data_o, oe, stp = (int(ctx.get(p)) for p in pins)
            if state["cur"] is not None:
                state["cur"].append((data_o, oe, stp))
            phy.step(data_o)
            ctx.set(pin_dir, phy.dir)
            ctx.set(pin_nxt, phy.nxt)
            ctx.set(pin_data, phy.data_i)
            await ctx.tick("usb")

    def mark(start):
        if start:
            state["cur"] = []
        else:
            state["traces"][state["phase"]].append(trim_idle(state["cur"]))
            state["cur"] = None

    async def testbench(ctx):
        ulpi_csr = Csr(ctx, regs.bus)
        state["visible"].append(await regs_sequence(ulpi_csr, mark))
        state["phase"] = 1
        if through_port:
            pc = Csr(ctx, port.bus)
            for role in (ROLE_HOST, ROLE_REGS, ROLE_RAW, ROLE_REGS):
                state["roles"].append(role_of(await set_role(pc, role)))
        state["visible"].append(await regs_sequence(ulpi_csr, mark))

    run(top, testbench, extra=(wires,))
    return state


def group_golden(checks, verbose=False):
    emit("  golden: regs through the mux")
    ref = golden_run(False)
    cand = golden_run(True)
    checks.check(
        "regs: pin traces (data, oe, stp per usb cycle) equal bare UlpiRegisters, default role",
        ref["traces"][0] == cand["traces"][0] and all(ref["traces"][0]),
        f"ref {[len(t) for t in ref['traces'][0]]} cand {[len(t) for t in cand['traces'][0]]}",
        measurement=f"{len(cand['traces'][0])} transactions, "
                    f"{sum(len(t) for t in cand['traces'][0])} cycles compared")
    checks.check(
        "regs: CSR-visible results equal bare UlpiRegisters, default role",
        ref["visible"][0] == cand["visible"][0]
        and cand["visible"][0][0][2] == PHY_IDENTITY[0],
        f"ref {ref['visible'][0]} cand {cand['visible'][0]}")
    checks.check(
        "role round trip off -> host -> regs -> raw -> regs completes through every role",
        cand["roles"] == [ROLE_HOST, ROLE_REGS, ROLE_RAW, ROLE_REGS],
        f"role_status sequence {cand['roles']}")
    checks.check(
        "regs: pin traces equal bare UlpiRegisters after the round trip",
        ref["traces"][1] == cand["traces"][1] and all(cand["traces"][1]),
        f"ref {[len(t) for t in ref['traces'][1]]} cand {[len(t) for t in cand['traces'][1]]}")
    checks.check(
        "regs: CSR-visible results equal bare UlpiRegisters after the round trip",
        ref["visible"][1] == cand["visible"][1],
        f"ref {ref['visible'][1]} cand {cand['visible'][1]}")
    # Control: the comparison must be able to fail.
    probe = [list(t) for t in cand["traces"][0]]
    probe[0] = probe[0] + [(0, 1, 1)]
    checks.check("regs: the trace comparison detects a one-cycle difference",
                 probe != ref["traces"][0])


# ---------------------------------------------------------------------------
# switching
# ---------------------------------------------------------------------------

def group_switching(checks, verbose=False):
    emit("  switching")
    bench = Bench(with_device=False, switch_limit=2000, func_ctrl=0x55)  # not the reset value
    phy, port = bench.phy, bench.port
    seen = {"func_writes": []}

    async def watch_phy(ctx):
        last = int(ctx.get(phy.func_ctrl))
        while True:
            await ctx.tick("usb")
            now = int(ctx.get(phy.func_ctrl))
            if now != last:
                seen["func_writes"].append(now)
                last = now

    async def testbench(ctx):
        pc = Csr(ctx, port.bus)
        uc = Csr(ctx, bench.regs.bus)
        seen["reset_role"] = await pc.read(REG_ROLE_STATUS)

        # ---- the quiet rule: no switch while DIR is high -----------------
        ctx.set(bench.force_dir, 1)
        await pc.write(REG_ROLE, ROLE_RAW)
        await pc.idle(QUIET_CYCLES * 20)
        seen["held"] = await pc.read(REG_ROLE_STATUS)
        ctx.set(bench.force_dir, 0)
        seen["released"] = await poll(pc, REG_ROLE_STATUS, lambda v: not v & 4, 200)

        # ---- a second ROLE write while one is pending is dropped ---------
        await pc.write(REG_ROLE, ROLE_HOST)
        await pc.write(REG_ROLE, ROLE_REGS)
        seen["dropped_ev"] = await pc.read(REG_EVENTS)
        seen["host"] = await poll(pc, REG_ROLE_STATUS, lambda v: not v & 4, 400)
        seen["role_readback"] = await pc.read(REG_ROLE)
        await pc.idle(40)
        seen["host_writes"] = list(seen["func_writes"])

        # ---- UlpiRegisters is denied the bus while host owns it ---------
        await uc.write(0, 0x00)
        await uc.write(2, ULPI_REG_READ)
        seen["denied"] = await ulpi_settle(uc)

        # ---- back to regs: the window works again ------------------------
        seen["regs"] = await set_role(pc, ROLE_REGS, polls=400)
        await uc.write(0, 0x00)
        await uc.write(2, ULPI_REG_READ)
        await ulpi_settle(uc)
        seen["id_after"] = await uc.read(1)

        # ---- expiry: DIR held past the limit -----------------------------
        await pc.write(REG_EVENTS, 0xFF)
        ctx.set(bench.force_dir, 1)
        await pc.write(REG_ROLE, ROLE_HOST)
        seen["expiry"] = await poll(pc, REG_ROLE_STATUS, lambda v: not v & 4, 1500, gap=4)
        seen["expiry_ev"] = await pc.read(REG_EVENTS)
        ctx.set(bench.force_dir, 0)

        # ---- init failure: PHY ignores commands ---------------------------
        await pc.write(REG_EVENTS, 0xFF)
        ctx.set(phy.mute, 1)
        await pc.write(REG_ROLE, ROLE_HOST)
        seen["init_fail"] = await poll(pc, REG_ROLE_STATUS, lambda v: not v & 4, 400)
        seen["init_ev"] = await pc.read(REG_EVENTS)
        ctx.set(phy.mute, 0)

    run(bench, testbench, extra=(watch_phy,))

    checks.check("the role resets to off", seen["reset_role"] == ROLE_OFF,
                 f"ROLE_STATUS {seen['reset_role']:#x}")
    checks.check(
        "no switch while DIR is high; it applies after DIR falls",
        role_of(seen["held"]) == ROLE_OFF and seen["held"] & 4
        and role_of(seen["released"]) == ROLE_RAW,
        f"held {seen['held']:#x} released {seen['released']}")
    checks.check(
        "a ROLE write while a switch is pending is dropped and flagged BUSY_DROP",
        seen["dropped_ev"] & EV_BUSY_DROP and role_of(seen["host"]) == ROLE_HOST
        and seen["role_readback"] == ROLE_HOST,
        f"events {seen['dropped_ev']:#x} host {seen['host']} readback {seen['role_readback']}")
    checks.check(
        "entering host first rewrites the PHY's function control to its reset value",
        seen["host_writes"][:1] == [0x41],
        f"function-control writes seen: {[hex(v) for v in seen['host_writes']]}")
    checks.check(
        "UlpiRegisters is denied the bus under host (times out as 'PHY absent')",
        seen["denied"] is not None and seen["denied"] & 2,
        f"STATUS {seen['denied']}")
    checks.check(
        "host -> regs returns the bus to UlpiRegisters",
        role_of(seen["regs"]) == ROLE_REGS and seen["id_after"] == PHY_IDENTITY[0],
        f"status {seen['regs']} id {seen['id_after']}")
    checks.check(
        "switch expiry: role unchanged, ROLE_STATUS.timeout and EV_TIMEOUT set",
        role_of(seen["expiry"]) == ROLE_REGS and timed_out(seen["expiry"])
        and seen["expiry_ev"] & EV_TIMEOUT,
        f"status {seen['expiry']} events {seen['expiry_ev']:#x}")
    checks.check(
        "host entry with a mute PHY falls back to regs and flags the timeout",
        role_of(seen["init_fail"]) == ROLE_REGS and timed_out(seen["init_fail"])
        and seen["init_ev"] & EV_TIMEOUT,
        f"status {seen['init_fail']} events {seen['init_ev']:#x}")


# ---------------------------------------------------------------------------
# raw: the CPU is the link
# ---------------------------------------------------------------------------

def token_bytes(address, endpoint):
    value = address | (endpoint << 7)
    token = value | (crc5(value) << 11)
    return [token & 0xFF, token >> 8]


def data_packet_bytes(payload):
    crc = crc16(payload)
    return list(payload) + [crc & 0xFF, crc >> 8]


async def send(csr, packet):
    """Queue one ULPI transmit: every byte but the last as data, the last with STP."""
    for byte in packet[:-1]:
        await csr.write(REG_TX_DATA, byte)
    await csr.write(REG_TX_LAST, packet[-1])


async def drain_rx(csr, limit=600):
    """Pop the RX FIFO until empty. Returns [(tag, byte)]; peeks the tag before each pop."""
    entries = []
    for _ in range(limit):
        tag_reg = await csr.read(REG_RX_TAG)
        if not tag_reg & 2:
            break
        entries.append((tag_reg & 1, await csr.read(REG_RX_DATA)))
    return entries


def group_raw(checks, verbose=False):
    emit("  raw")
    bench = Bench(with_device=True, full_speed_only=False)
    port, phy, device = bench.port, bench.phy, bench.device
    packets, seen = [], {}

    async def testbench(ctx):
        pc = Csr(ctx, port.bus)
        seen["role"] = await set_role(pc, ROLE_RAW)

        # SETUP token + DATA0(GET_DESCRIPTOR) as bytes the CPU computed itself.
        await send(pc, [0x40 | 0xD] + token_bytes(0, 0))
        await pc.idle(40)
        await send(pc, [0x40 | 0x3] + data_packet_bytes(GET_DESCRIPTOR_DEVICE))
        seen["rx_arrived"] = await poll(pc, REG_RX_LEVEL, lambda v: v >= 3, 400, gap=8)
        await pc.idle(200)
        seen["peek_a"] = await pc.read(REG_RX_TAG)
        seen["peek_b"] = await pc.read(REG_RX_TAG)
        seen["rx"] = await drain_rx(pc)
        seen["events"] = await pc.read(REG_EVENTS)
        seen["link"] = await pc.read(REG_LINK)

        # ULPI register read without STP (TX_CMD), result comes back as an RXCMD entry.
        await pc.write(REG_EVENTS, 0xFF)
        await pc.write(REG_TX_CMD, 0xC0)
        await pc.idle(60)
        seen["reg_read"] = await drain_rx(pc)
        # Register write (command + value + STP), then read it back.
        await pc.write(REG_TX_DATA, 0x80 | 0x16)
        await pc.write(REG_TX_LAST, 0xA5)
        await pc.idle(60)
        await pc.write(REG_TX_CMD, 0xC0 | 0x16)
        await pc.idle(60)
        seen["scratch"] = int(ctx.get(phy.scratch))
        seen["reg_readback"] = await drain_rx(pc)

        # Bulk IN against a 128-entry RX FIFO that nobody drains. The unconfigured
        # model endpoint streams far past 515 bytes (observed 6300); only the head matters.
        await pc.write(REG_EVENTS, 0xFF)
        await pc.idle(3000)      # the endpoint NAKs until its own FIFO has filled
        await send(pc, [0x40 | 0x9] + token_bytes(0, BULK_IN_ENDPOINT))
        await pc.idle(2500)
        seen["overflow_ev"] = await pc.read(REG_EVENTS)
        seen["overflow_level"] = await pc.read(REG_RX_LEVEL)
        seen["overflow_head"] = []
        for _ in range(128):
            seen["overflow_head"].append((await pc.read(REG_RX_TAG) & 1, await pc.read(REG_RX_DATA)))

    run(bench, testbench, processes=(capture_usb(device.utmi, packets),))

    if verbose:
        for packet in packets:
            emit(f"        {packet.cycle:7d}  " + " ".join(f"{b:02x}" for b in packet.data))

    checks.check("raw role entered", role_of(seen["role"]) == ROLE_RAW,
                 f"status {seen['role']}")
    tokens = [p for p in packets if p.pid == PID_SETUP]
    ok = bool(tokens) and len(tokens[0].data) == 3
    if ok:
        value = tokens[0].data[1] | (tokens[0].data[2] << 8)
        ok = (value & 0x7F, (value >> 7) & 0xF, value >> 11) == (0, 0, crc5(0))
    checks.check("raw: the SETUP token the CPU queued is on the wire with a valid CRC5", ok,
                 f"packets {[(hex(p.pid), len(p.data)) for p in packets]}")
    datas = [p for p in packets if p.pid == PID_DATA0]
    checks.check(
        "raw: DATA0 carries the 8 SETUP bytes and the CRC16 the CPU computed",
        bool(datas) and datas[0].data[1:] == data_packet_bytes(GET_DESCRIPTOR_DEVICE),
        f"{[hex(b) for b in datas[0].data] if datas else None}")
    rx = seen["rx"]
    data_entries = [b for tag, b in rx if tag == 0]
    checks.check(
        "raw: the model device's handshake is in the RX FIFO as exactly one data byte, an ACK",
        data_entries == [PID_ACK], f"rx entries {[(t, hex(b)) for t, b in rx]}")
    first_data = next((i for i, (t, _) in enumerate(rx) if t == 0), None)
    checks.check(
        "raw: RXCMDs (tag 1) bracket the packet, RxActive set before and clear after",
        first_data is not None and first_data >= 1
        and all(t == 1 for t, _ in rx[:first_data]) and rx[first_data - 1][1] & 0x10
        and rx[-1][0] == 1 and not rx[-1][1] & 0x10,
        f"rx entries {[(t, hex(b)) for t, b in rx]}")
    checks.check(
        "raw: RX_TAG is a peek (same twice) and reports tag and valid",
        seen["peek_a"] == seen["peek_b"] and seen["peek_a"] & 2,
        f"{seen['peek_a']:#x} {seen['peek_b']:#x}")
    checks.check("raw: a line-state RXCMD raised EV_LINE", seen["events"] & EV_LINE,
                 f"events {seen['events']:#x}")
    checks.check(
        "raw: LINK reports idle and the line state from the last RXCMD",
        seen["link"] & 0x40 and (seen["link"] & 3) in (0, 1),
        f"link {seen['link']:#x}")
    checks.check(
        "raw: a ULPI register read (TX_CMD, no STP) returns the value as an RXCMD entry",
        (1, PHY_IDENTITY[0]) in seen["reg_read"],
        f"entries {[(t, hex(b)) for t, b in seen['reg_read']]}")
    checks.check(
        "raw: a ULPI register write (command, value, STP) lands in the PHY and reads back",
        seen["scratch"] == 0xA5 and (1, 0xA5) in seen["reg_readback"],
        f"scratch {seen['scratch']:#x} readback {seen['reg_readback']}")
    head = seen["overflow_head"]
    checks.check(
        "raw: a long packet into a full RX FIFO sets EV_RX_OVERFLOW, keeps 128 entries, "
        "and keeps the OLDEST (the tail is dropped, nothing is overwritten)",
        seen["overflow_ev"] & EV_RX_OVERFLOW and seen["overflow_level"] == 128
        and [t for t, _ in head[:2]] == [1, 1] and head[2][0] == 0
        and [b for _, b in head[3:]] == list(range(125)),
        f"events {seen['overflow_ev']:#x} level {seen['overflow_level']} "
        f"head {[(t, hex(b)) for t, b in head[:6]]}")


# ---------------------------------------------------------------------------
# host: the usb_host_sie_sim scenarios through the CSRs
# ---------------------------------------------------------------------------

RESP_NAMES = {0: "NONE", 1: "ACK", 2: "NAK", 3: "STALL", 4: "TIMEOUT", 5: "CRC_ERROR",
              6: "RX_OVERFLOW"}
RESP_ACK, RESP_TIMEOUT = 1, 4
SPEED_HIGH, SPEED_FULL = 0, 1


async def host_up(csr):
    """Enter host and wait for reset_done. Returns the LINK byte or None."""
    status = await set_role(csr, ROLE_HOST, polls=400)
    if role_of(status) != ROLE_HOST:
        return None
    return await poll(csr, REG_LINK, lambda v: v & 0x80, BRINGUP_POLLS, gap=64)


async def host_transact(csr, *, type, address, endpoint, data_pid=0, payload=()):
    """One transfer the way firmware issues it. Returns a dict of what the CSRs said."""
    for byte in payload:
        await csr.write(REG_TX_DATA, byte)
    await csr.write(REG_ADDR, address)
    await csr.write(REG_EP, endpoint | (type << 4) | (data_pid << 6))
    await csr.write(REG_EVENTS, EV_DONE)
    await csr.write(REG_CTRL, CTRL_START)
    events = await poll(csr, REG_EVENTS, lambda v: v & EV_DONE, DONE_POLLS, gap=8)
    out = {"events": events, "done": events is not None}
    if events is None:
        return out
    resp = await csr.read(REG_RESP)
    out["resp"], out["nyet"] = resp & 7, bool(resp & 8)
    out["rxlen"] = await csr.read(REG_RXLEN_LO) | (await csr.read(REG_RXLEN_HI) << 8)
    out["pid"] = await csr.read(REG_RXPID)
    out["irq_pending_events"] = events
    data = []
    spins = 0
    while len(data) < out["rxlen"] and spins < out["rxlen"] * 8 + 64:
        spins += 1
        if await csr.read(REG_RX_TAG) & 2:
            data.append(await csr.read(REG_RX_DATA))
    out["data"] = data
    return out


def group_host(checks, verbose=False):
    emit("  host (high speed)")
    bench = Bench(with_device=True, full_speed_only=False)
    port, device = bench.port, bench.device
    packets, seen = [], {}

    async def testbench(ctx):
        pc = Csr(ctx, port.bus)
        seen["irq_before"] = int(ctx.get(port.irq))
        await pc.write(REG_IRQ_EN, 0b0010)      # done
        seen["link"] = await host_up(pc)
        if seen["link"] is None:
            return
        seen["connected_link"] = await pc.read(REG_LINK)

        mark = len(packets)
        seen["setup"] = await host_transact(
            pc, type=TYPE_SETUP, address=0, endpoint=0, payload=GET_DESCRIPTOR_DEVICE)
        seen["irq_done"] = int(ctx.get(port.irq))
        seen["setup_packets"] = packets[mark:]
        await pc.write(REG_EVENTS, 0xFF)
        seen["irq_cleared"] = int(ctx.get(port.irq))
        seen["descriptor"] = await host_transact(pc, type=TYPE_IN, address=0, endpoint=0)
        mark = len(packets)
        seen["status_stage"] = await host_transact(
            pc, type=TYPE_OUT, address=0, endpoint=0, data_pid=1)
        seen["status_packets"] = packets[mark:]

        seen["set_address"] = await host_transact(
            pc, type=TYPE_SETUP, address=0, endpoint=0,
            payload=setup_packet(0x00, 0x05, DEVICE_ADDRESS, 0, 0))
        seen["set_address_status"] = await host_transact(
            pc, type=TYPE_IN, address=0, endpoint=0, data_pid=1)
        seen["old_address"] = await host_transact(
            pc, type=TYPE_SETUP, address=0, endpoint=0, payload=GET_DESCRIPTOR_DEVICE)

        await host_transact(pc, type=TYPE_SETUP, address=DEVICE_ADDRESS, endpoint=0,
                            payload=GET_DESCRIPTOR_DEVICE)
        seen["readdressed"] = await host_transact(
            pc, type=TYPE_IN, address=DEVICE_ADDRESS, endpoint=0)
        await host_transact(pc, type=TYPE_OUT, address=DEVICE_ADDRESS, endpoint=0, data_pid=1)
        await host_transact(pc, type=TYPE_SETUP, address=DEVICE_ADDRESS, endpoint=0,
                            payload=SET_CONFIGURATION_1)
        seen["set_config"] = await host_transact(
            pc, type=TYPE_IN, address=DEVICE_ADDRESS, endpoint=0, data_pid=1)

        # A start while a transfer runs is dropped and flagged; the transfer is unharmed.
        await pc.write(REG_EVENTS, 0xFF)
        await pc.write(REG_ADDR, DEVICE_ADDRESS)
        await pc.write(REG_EP, BULK_IN_ENDPOINT | (TYPE_IN << 4))
        await pc.write(REG_CTRL, CTRL_START)
        await pc.write(REG_CTRL, CTRL_START)
        seen["busy_ev"] = await pc.read(REG_EVENTS)
        done = await poll(pc, REG_EVENTS, lambda v: v & EV_DONE, DONE_POLLS, gap=8)
        seen["bulk_done"] = done is not None
        seen["bulk_len"] = (await pc.read(REG_RXLEN_LO)) | (await pc.read(REG_RXLEN_HI) << 8)
        seen["sie_rx_len"] = int(ctx.get(port.sie.ctrl.status.rx_len))
        bulk = []
        spins = 0
        while len(bulk) < seen["bulk_len"] and spins < 6000:
            spins += 1
            if await pc.read(REG_RX_TAG) & 2:
                bulk.append(await pc.read(REG_RX_DATA))
        seen["bulk"] = bulk
        seen["bulk_pid"] = await pc.read(REG_RXPID)
        seen["bulk_resp"] = (await pc.read(REG_RESP)) & 7

    run(bench, testbench, processes=(capture_usb(device.utmi, packets),))

    if verbose:
        for packet in packets:
            emit(f"        {packet.cycle:7d}  " + " ".join(f"{b:02x}" for b in packet.data))

    link = seen.get("link")
    checks.check("host: the role switch, PHY init, bus reset and chirp reach reset_done",
                 link is not None, f"no reset_done within {BRINGUP_POLLS} polls")
    if link is None:
        return
    checks.check("host: a high-speed device is reported as HIGH in LINK.speed",
                 (link >> 2) & 3 == SPEED_HIGH, f"LINK {link:#x}")
    checks.check("host: LINK reports connected, idle, not in reset, line state J",
                 seen["connected_link"] & 0x70 == 0x50 and seen["connected_link"] & 3 == 1,
                 f"LINK {seen['connected_link']:#x}")
    checks.check("host: the IRQ line is low at reset", seen["irq_before"] == 0)

    def resp_name(result):
        return RESP_NAMES.get(result.get("resp"), result.get("resp"))

    setup = seen["setup"]
    checks.check("host: GET_DESCRIPTOR SETUP is ACKed and `done` is raised",
                 setup["done"] and setup["resp"] == RESP_ACK, f"{setup}")
    checks.check("host: the IRQ goes high on done and low when EVENTS is cleared",
                 seen["irq_done"] == 1 and seen["irq_cleared"] == 0,
                 f"{seen['irq_done']} {seen['irq_cleared']}")
    datas = [p for p in seen["setup_packets"] if p.pid in (PID_DATA0, PID_DATA1)]
    checks.check(
        "host: the SETUP payload from TX_DATA goes out as DATA0 with a CRC16 that checks",
        bool(datas) and datas[0].pid == PID_DATA0
        and datas[0].data[1:] == data_packet_bytes(GET_DESCRIPTOR_DEVICE),
        f"{[hex(b) for b in datas[0].data] if datas else None}")
    desc = seen["descriptor"]["data"]
    checks.check(
        "host: the device descriptor comes back through the RX FIFO byte for byte",
        len(desc) == 18 and desc[0] == 18 and desc[1] == 1
        and desc[7] == CONTROL_MAX_PACKET_SIZE
        and desc[8] | (desc[9] << 8) == VENDOR_ID
        and desc[10] | (desc[11] << 8) == PRODUCT_ID
        and seen["descriptor"]["rxlen"] == 18,
        f"{seen['descriptor']}")
    checks.check("host: RXPID reports the first received PID (DATA1 for the descriptor)",
                 seen["descriptor"]["pid"] == PID_DATA1 and not seen["descriptor"]["nyet"],
                 f"pid {seen['descriptor'].get('pid')}")
    status_data = [p for p in seen["status_packets"] if p.pid in (PID_DATA0, PID_DATA1)]
    checks.check(
        "host: the data toggle is the caller's: EP.data_pid=1 puts DATA1 on the wire",
        bool(status_data) and status_data[0].pid == PID_DATA1
        and seen["status_stage"]["resp"] == RESP_ACK,
        f"{[hex(p.pid) for p in seen['status_packets']]}")
    checks.check(
        "host: SET_ADDRESS completes; a transfer to the OLD address times out",
        seen["set_address"]["resp"] == RESP_ACK and seen["set_address_status"]["resp"] == RESP_ACK
        and seen["old_address"]["resp"] == RESP_TIMEOUT,
        f"{resp_name(seen['set_address'])} {resp_name(seen['set_address_status'])} "
        f"old {resp_name(seen['old_address'])}")
    checks.check("host: the device answers on the new address with the same descriptor",
                 seen["readdressed"]["data"] == desc and desc, f"{seen['readdressed']}")
    checks.check("host: SET_CONFIGURATION completes", seen["set_config"]["resp"] == RESP_ACK,
                 resp_name(seen["set_config"]))
    checks.check("host: a second START while a transfer runs is dropped and flagged BUSY_DROP",
                 seen["busy_ev"] & EV_BUSY_DROP and seen["bulk_done"],
                 f"events {seen['busy_ev']:#x}")
    bulk = seen["bulk"]
    checks.check(
        "host: a 512-byte high-speed bulk IN arrives intact through the 128-entry RX FIFO",
        len(bulk) == BULK_MAX_PACKET_SIZE and bulk == [i & 0xFF for i in range(len(bulk))]
        and seen["bulk_resp"] == RESP_ACK and seen["bulk_pid"] in (PID_DATA0, PID_DATA1),
        f"{len(bulk)} bytes resp {seen['bulk_resp']} pid {seen['bulk_pid']:#x}")
    checks.check(
        "host: RXLEN is 512 where the SIE's own 8-bit rx_len wrapped to 0",
        seen["bulk_len"] == BULK_MAX_PACKET_SIZE and seen["sie_rx_len"] == 0,
        f"RXLEN {seen['bulk_len']} SIE rx_len {seen['sie_rx_len']}")

    emit("  host (full speed)")
    bench = Bench(with_device=True, full_speed_only=True)
    port, device = bench.port, bench.device
    fs = {}

    async def fs_testbench(ctx):
        pc = Csr(ctx, port.bus)
        fs["link"] = await host_up(pc)
        if fs["link"] is None:
            return
        fs["setup"] = await host_transact(
            pc, type=TYPE_SETUP, address=0, endpoint=0, payload=GET_DESCRIPTOR_DEVICE)
        fs["descriptor"] = await host_transact(pc, type=TYPE_IN, address=0, endpoint=0)

    run(bench, fs_testbench)
    desc = fs.get("descriptor", {}).get("data", [])
    checks.check(
        "host: a full-speed-only device is reported as FULL and enumerates its descriptor",
        fs.get("link") is not None and (fs["link"] >> 2) & 3 == SPEED_FULL
        and fs["setup"]["resp"] == RESP_ACK and len(desc) == 18
        and desc[8] | (desc[9] << 8) == VENDOR_ID,
        f"{fs}")


# ---------------------------------------------------------------------------
# fifos
# ---------------------------------------------------------------------------

def group_fifos(checks, verbose=False):
    emit("  fifos")
    bench = Bench(with_device=False)
    port, phy = bench.port, bench.phy
    packets, seen = [], {}

    async def testbench(ctx):
        pc = Csr(ctx, port.bus)
        # TX in `off`: nothing can use the byte, so it is refused and flagged.
        await pc.write(REG_TX_DATA, 0x55)
        seen["off_level"] = await pc.read(REG_TX_LEVEL)
        seen["off_ev"] = await pc.read(REG_EVENTS)
        await pc.write(REG_EVENTS, 0xFF)

        await set_role(pc, ROLE_RAW)
        # 130 data bytes with no packet end: the engine waits for a LAST marker.
        for i in range(130):
            await pc.write(REG_TX_DATA, i & 0xFF)
        seen["tx_level"] = await pc.read(REG_TX_LEVEL)
        seen["tx_ev"] = await pc.read(REG_EVENTS)
        await pc.idle(100)
        seen["tx_held"] = await pc.read(REG_TX_LEVEL)
        await pc.write(REG_CTRL, CTRL_CLR_TX)
        seen["tx_cleared"] = await poll(pc, REG_TX_LEVEL, lambda v: v == 0, 200, gap=4)

        # The engine still works after the flush: a register read goes out.
        await pc.write(REG_EVENTS, 0xFF)
        await pc.write(REG_TX_CMD, 0xC0)
        await pc.idle(60)
        seen["after_flush"] = await drain_rx(pc)

        # Underflow: pop of an empty FIFO returns 0 and is flagged.
        await pc.write(REG_EVENTS, 0xFF)
        seen["empty_level"] = await pc.read(REG_RX_LEVEL)
        seen["empty_pop"] = await pc.read(REG_RX_DATA)
        seen["under_ev"] = await pc.read(REG_EVENTS)

        # CLR_RX empties a FIFO holding several entries.
        for _ in range(3):
            await pc.write(REG_TX_CMD, 0xC0)
            await pc.idle(40)
        seen["rx_before_clear"] = await pc.read(REG_RX_LEVEL)
        await pc.write(REG_CTRL, CTRL_CLR_RX)
        seen["rx_cleared"] = await poll(pc, REG_RX_LEVEL, lambda v: v == 0, 200, gap=4)

    run(bench, testbench)

    checks.check(
        "fifos: a TX byte while the role has no TX path is refused (level 0) and flagged",
        seen["off_level"] == 0 and seen["off_ev"] & EV_TX_OVERFLOW,
        f"level {seen['off_level']} events {seen['off_ev']:#x}")
    checks.check(
        "fifos: 130 bytes into the 128-entry TX FIFO keep 128 and flag EV_TX_OVERFLOW",
        seen["tx_level"] == 128 and seen["tx_ev"] & EV_TX_OVERFLOW,
        f"level {seen['tx_level']} events {seen['tx_ev']:#x}")
    checks.check(
        "fifos: with no packet marker the raw engine sends nothing (level holds)",
        seen["tx_held"] == 128, f"level {seen['tx_held']}")
    checks.check("fifos: CLR_TX empties the TX FIFO", seen["tx_cleared"] == 0,
                 f"level {seen['tx_cleared']}")
    checks.check(
        "fifos: the raw TX engine works after a flush",
        (1, PHY_IDENTITY[0]) in seen["after_flush"], f"{seen['after_flush']}")
    checks.check(
        "fifos: popping an empty RX FIFO returns 0 and flags EV_RX_UNDERFLOW",
        seen["empty_level"] == 0 and seen["empty_pop"] == 0
        and seen["under_ev"] & EV_RX_UNDERFLOW,
        f"level {seen['empty_level']} pop {seen['empty_pop']} events {seen['under_ev']:#x}")
    checks.check(
        "fifos: CLR_RX empties an RX FIFO holding entries",
        seen["rx_before_clear"] >= 3 and seen["rx_cleared"] == 0,
        f"before {seen['rx_before_clear']} after {seen['rx_cleared']}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every packet the device saw")
    parser.add_argument("groups", nargs="*", help="only these groups")
    args = parser.parse_args()
    scale_timing_for_simulation()
    checks = Checks(emit)
    emit("USB port role mux and CSR/FIFO shim (peripherals/usb_host.py)")
    for name, group in GROUPS:
        if not args.groups or name in args.groups:
            group(checks, verbose=args.verbose)
    return checks.summary()


GROUPS = [("golden", group_golden), ("switching", group_switching), ("raw", group_raw),
          ("host", group_host), ("fifos", group_fifos)]

if __name__ == "__main__":
    sys.exit(main())
