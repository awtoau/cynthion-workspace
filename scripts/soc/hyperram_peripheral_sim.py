#!/usr/bin/env python3
#
# The SoC HyperRAM peripheral (#588) against the #581 datasheet model.
# SPDX-License-Identifier: BSD-3-Clause

"""`gateware/soc/peripherals/hyperram.py` driven through its CSRs and window.

- Device: `hyperram_baseline_sim.Device` (datasheet A01-006), extended here with the
  per-byte write mask (Fig 7), register writes (s7.4) and a read-corruption hook.
- Pin path latencies as the baseline sim (ODDR, fabric CS#/OE, round trip, IDDR).
- Covers: auto-calibration and its centre, COPY both ways at every alignment, FILL,
  TEST, register read/write, the stream ring and stager pairs interleaved with a
  command, and the error paths (watchdog, no clean tap, mismatch, refusals, CK, PLL).
- Negative controls: XOR'd checker, a corrupting device, a broken expectation.

    ./scripts/soc/hyperram_peripheral_sim.py     # log: tmp/logs/hyperram_peripheral_sim.log
"""

from __future__ import annotations

import collections
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402
from hyperram_baseline_sim import Device  # noqa: E402

from amaranth import ClockDomain, Module  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import peripherals.hyperram as hrp  # noqa: E402

CK_MHZ, SYNC_MHZ = 180.0, 60.0
LP, LCS, LI, R_HALVES = 3, 1, 1, 6
CAL_WORDS = 64
CK_BITS = 8


class SocDevice(Device):
    """Baseline model + byte masks, register writes, CR1, and a read fault hook."""

    def __init__(self):
        super().__init__()
        self.regs = {0x800: 0x8F2F, 0x801: 0xFFC1, 0x0: 0x0C81, 0x1: 0x0000}
        self.corrupt = lambda: False      # True: flip bits in returned data
        self.ignore_reg_writes = False

    def step(self, cs, ck_en, dq_o, rwds_o):
        quiet = [(0, 0), (0, 0)]
        if not cs:
            self.state, self.ca, self.cs_low = "idle", [], 0
            return quiet
        self.cs_low += 1
        self.cs_low_max = max(self.cs_low_max, self.cs_low)
        if not ck_en:
            return quiet
        if self.state == "idle":
            self.state = "ca"
        if self.state == "ca":
            self.ca.append(dq_o)
            if len(self.ca) == 3:
                ca = (self.ca[0] << 32) | (self.ca[1] << 16) | self.ca[2]
                self.read = bool(ca >> 47 & 1)
                self.regsp = bool(ca >> 46 & 1)
                self.addr = ((ca >> 16) & ((1 << 29) - 1)) << 3 | (ca & 7)
                if self.regsp and not self.read:
                    self.state = "regw"           # zero latency: data follows the CA
                else:
                    self.left = 2 * 7 - 1
                    self.state = "lat"
            return [(0, 1), (0, 1)]
        if self.state == "regw":
            if not self.ignore_reg_writes:
                self.regs[self.addr] = dq_o
            self.state = "done"
            return quiet
        if self.state == "done":
            return quiet
        if self.state == "lat":
            self.left -= 1
            if self.left == 0:
                self.state = "data"
            return quiet
        if self.read:
            word = self.regs.get(self.addr, 0) if self.regsp else self.mem.get(self.addr, 0x5A5A)
            if self.corrupt():
                word ^= 0x0101
            self.addr += 1
            return [(word >> 8, 1), (word & 0xFF, 0)]
        old = self.mem.get(self.addr, 0x5A5A)
        hi = old & 0xFF00 if rwds_o & 2 else dq_o & 0xFF00
        lo = old & 0x00FF if rwds_o & 1 else dq_o & 0x00FF
        self.mem[self.addr] = hi | lo
        self.addr += 1
        return quiet

    def byte(self, b):
        word = self.mem.get(b >> 1, 0x5A5A)
        return (word >> 8) & 0xFF if b % 2 == 0 else word & 0xFF


class Bench:
    def __init__(self, periph, ctx):
        self.p, self.ctx = periph, ctx
        self.regs = periph._regs

    async def tick(self, n=1):
        await self.ctx.tick("sync").repeat(n)

    def _size(self, name):
        # the register's footprint: amaranth-soc rounds it to a power of two, and a write
        # commits on the footprint's last byte
        n = (self.regs[name][1].element.width + 7) // 8
        return 1 << (n - 1).bit_length()

    async def write(self, name, value):
        off, ctx, bus = self.regs[name][0], self.ctx, self.p.bus
        for i in range(self._size(name)):
            ctx.set(bus.addr, off + i)
            ctx.set(bus.w_data, (value >> (8 * i)) & 0xFF)
            ctx.set(bus.w_stb, 1)
            await self.tick()
        ctx.set(bus.w_stb, 0)
        await self.tick()

    async def read(self, name):
        off, ctx, bus = self.regs[name][0], self.ctx, self.p.bus
        value = 0
        for i in range(self._size(name)):
            ctx.set(bus.addr, off + i)
            ctx.set(bus.r_stb, 1)
            await self.tick()
            ctx.set(bus.r_stb, 0)
            value |= ctx.get(bus.r_data) << (8 * i)
        return value

    async def status(self):
        v = await self.read("status")
        return dict(busy=v & 1, ready=(v >> 1) & 1, locked=(v >> 2) & 1, cal=(v >> 3) & 1,
                    hr_busy=(v >> 5) & 1, code=(v >> 8) & 15, op=(v >> 12) & 15)

    async def wait_idle(self, bound):
        for _ in range(bound):
            st = await self.status()
            if not st["busy"] and not st["hr_busy"]:
                return st
        return None

    async def cmd(self, op, bound=20000, **regs):
        for name, value in regs.items():
            await self.write(name, value)
        await self.write("cmd", op)
        return await self.wait_idle(bound)

    async def win_write(self, offset, data):
        ctx, wb = self.ctx, self.p.window
        for i, b in enumerate(data):
            a = offset + i
            ctx.set(wb.adr, a >> 2)
            ctx.set(wb.dat_w, b << (8 * (a & 3)))
            ctx.set(wb.sel, 1 << (a & 3))
            ctx.set(wb.we, 1)
            ctx.set(wb.cyc, 1)
            ctx.set(wb.stb, 1)
            await self.tick()
            while not ctx.get(wb.ack):
                await self.tick()
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            await self.tick()

    async def win_read(self, offset, n):
        ctx, wb = self.ctx, self.p.window
        out = []
        for i in range(n):
            a = offset + i
            ctx.set(wb.adr, a >> 2)
            ctx.set(wb.we, 0)
            ctx.set(wb.sel, 0xF)
            ctx.set(wb.cyc, 1)
            ctx.set(wb.stb, 1)
            await self.tick()
            while not ctx.get(wb.ack):
                await self.tick()
            out.append((ctx.get(wb.dat_r) >> (8 * (a & 3))) & 0xFF)
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            await self.tick()
        return out


def run(checks, name, scenario, dev=None, hr_mhz=CK_MHZ, **kwargs):
    dev = dev or SocDevice()
    kwargs.setdefault("sim_fast_delay", True)
    periph = hrp.HyperRAM(ck_mhz=CK_MHZ, sync_mhz=SYNC_MHZ, sim=True, ck_window_bits=CK_BITS,
                          cal_words=CAL_WORDS, **kwargs)
    m = Module()
    m.domains += [ClockDomain("sync"), ClockDomain("hr"), ClockDomain("usb")]
    m.submodules.p = periph
    p = periph.pins

    async def board(ctx):
        out_q = collections.deque([(0, 0, 0)] * LP)
        cs_q = collections.deque([(0, 0, 0)] * LCS)
        ret_q = collections.deque([(0, 0)] * R_HALVES)
        in_q = collections.deque([((0, 0), (0, 0))] * LI)
        while True:
            out_q.append((ctx.get(p.ck_en), ctx.get(p.dq_o), ctx.get(p.rwds_o)))
            cs_q.append((ctx.get(p.cs), ctx.get(p.dq_oe), ctx.get(p.rwds_oe)))
            ck_en, dq_o, rwds_o = out_q.popleft()
            cs, dq_oe, rwds_oe = cs_q.popleft()
            dq_o = dq_o if dq_oe else 0
            rwds_o = rwds_o if rwds_oe else 3
            dev.tap = ctx.get(periph.delay_tap)
            for half in dev.step(cs, ck_en, dq_o, rwds_o):
                ret_q.append(half)
            sample = (ret_q.popleft(), ret_q.popleft())
            in_q.append(sample)
            (d0, r0), (d1, r1) = in_q.popleft()
            ctx.set(p.dq_q0, d0)
            ctx.set(p.rwds_q0, r0)
            ctx.set(p.dq_q1, d1)
            ctx.set(p.rwds_q1, r1)
            await ctx.tick("hr")

    result = {}

    async def control(ctx):
        bench = Bench(periph, ctx)
        await scenario(checks, bench, dev, ctx, periph)
        result["ok"] = True

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(1 / (hr_mhz * 1e6), domain="hr")
    sim.add_clock(1 / (SYNC_MHZ * 1e6), domain="sync")
    sim.add_clock(1 / 60e6, domain="usb", phase=3.3e-9)
    sim.add_testbench(board, background=True)
    sim.add_testbench(control)
    emit(f"scenario: {name}")
    sim.run()
    checks.check(f"{name}: scenario ran to the end", result.get("ok", False))


# ---------------------------------------------------------------- scenarios
BOOT_BOUND = 60000          # status polls: auto-cal = 128 x (delay <= 1032 + reads) hr cycles


async def wait_boot(b):
    for _ in range(BOOT_BOUND):
        st = await b.status()
        if st["ready"] and not st["hr_busy"] and not st["busy"]:
            return st
    return None


async def boot(checks, b, tag):
    st = await wait_boot(b)
    checks.check(f"{tag}: auto-calibration finishes", st is not None)
    return st


def bad_band(dev, low, high):
    dev.corrupt = lambda: low <= getattr(dev, "tap", 0) < high


async def s_main(checks, b, dev, ctx, periph):
    st = await boot(checks, b, "main")
    cal = await b.read("cal")
    tap, width, valid = cal & 0x7F, (cal >> 8) & 0xFF, (cal >> 16) & 1
    # clean taps 0..19 and 40..127: the widest run is 88 long, centred on 84
    checks.check("cal: centre of the widest clean run", (tap, width, valid) == (84, 88, 1),
                 f"tap {tap} width {width} valid {valid}")
    m0 = await b.read("cal_map0")
    checks.check("cal: map marks taps 20..39 dirty", m0 == 0x000FFFFF, f"map0 {m0:#010x}")
    checks.check("status after boot: ready, locked, calibrated, idle",
                 st and st["ready"] and st["locked"] and st["cal"] and not st["busy"], str(st))
    dev.corrupt = lambda: False
    await b.write("irq_en", 3)

    # register space
    st = await b.cmd(hrp.OP_REG_READ, reg_sel=2)
    reg = await b.read("reg_rdata")
    checks.check("REG_READ CR0 = 0x8F2F", st["code"] == 0 and reg == 0x8F2F, f"{st} {reg:#x}")
    st = await b.cmd(hrp.OP_REG_WRITE, reg_sel=3, reg_wdata=0xFFC2)
    checks.check("REG_WRITE CR1 lands and reads back", st["code"] == 0 and dev.regs[0x801] == 0xFFC2,
                 f"{st} CR1 {dev.regs[0x801]:#x}")
    dev.ignore_reg_writes = True
    st = await b.cmd(hrp.OP_REG_WRITE, reg_sel=3, reg_wdata=0xFFC1)
    err = await b.read("errors")
    checks.check("REG_WRITE that does not land: REG_MISMATCH + sticky bit",
                 st["code"] == hrp.CODE_REG_MISMATCH and err >> hrp.ERR_REG_MISMATCH & 1,
                 f"{st} errors {err:#x}")
    dev.ignore_reg_writes = False
    await b.write("errors", 0x1FFF)

    # FILL constant over an odd range, then COPY_IN at every alignment pair
    rng = random.Random(588)
    for hr_a, win_a, n in ((0x101, 0x10, 37), (0x200, 0x21, 50), (0x301, 0x41, 23),
                           (0x400, 0x60, 64), (0x7FF, 0x0, 1)):
        st = await b.cmd(hrp.OP_FILL, hr_addr=hr_a, len=n, mode=hrp.FILL_RAMP,
                         fill_value=rng.randrange(256))
        start = await b.read("fill_value") & 0xFF
        want = [(start + i) & 0xFF for i in range(n)]
        have = [dev.byte(hr_a + i) for i in range(n)]
        outside = (dev.byte(hr_a - 1), dev.byte(hr_a + n))
        checks.check(f"FILL ramp @{hr_a:#x}+{n}: bytes, neighbours untouched",
                     st["code"] == 0 and have == want and outside == (0x5A, 0x5A),
                     f"{st} have {have[:6]} want {want[:6]} outside {outside}")
        await b.win_write(win_a - 1, [0xEE])
        await b.win_write(win_a + n, [0xEE])
        st = await b.cmd(hrp.OP_COPY_IN, hr_addr=hr_a, win_addr=win_a, len=n)
        got = await b.win_read(win_a - 1, n + 2)
        checks.check(f"COPY_IN hr {hr_a:#x} -> win {win_a:#x} len {n}",
                     st["code"] == 0 and got[1:-1] == want and got[0] == 0xEE and got[-1] == 0xEE,
                     f"{st} got {got[:6]}.. ends {got[0]:#x} {got[-1]:#x}")

    # COPY_OUT at every alignment: bytes in range land, neighbours keep their value
    for hr_a, win_a, n in ((0x1001, 0x100, 45), (0x1100, 0x201, 30), (0x1201, 0x301, 17),
                           (0x1300, 0x400, 1), (0x1401, 0x500, 1)):
        data = [rng.randrange(256) for _ in range(n)]
        await b.win_write(win_a, data)
        before = (dev.byte(hr_a - 1), dev.byte(hr_a + n))
        st = await b.cmd(hrp.OP_COPY_OUT, hr_addr=hr_a, win_addr=win_a, len=n)
        have = [dev.byte(hr_a + i) for i in range(n)]
        after = (dev.byte(hr_a - 1), dev.byte(hr_a + n))
        checks.check(f"COPY_OUT win {win_a:#x} -> hr {hr_a:#x} len {n}, masked ends",
                     st["code"] == 0 and have == data and after == before,
                     f"{st} have {have[:6]} want {data[:6]} edges {before}->{after}")

    # FILL constant + pattern, TEST write/read/both, XOR negative control
    st = await b.cmd(hrp.OP_FILL, hr_addr=0x2000, len=16, mode=hrp.FILL_CONST,
                     fill_value=0x44332211)
    have = [dev.byte(0x2000 + i) for i in range(8)]
    checks.check("FILL constant repeats by byte lane",
                 st["code"] == 0 and have == [0x11, 0x22, 0x33, 0x44] * 2, str(have))
    st = await b.cmd(hrp.OP_TEST, hr_addr=0x4000, len=4096, mode=hrp.TEST_BOTH << 2,
                     seed=0x1234, burst=0)
    words = await b.read("words")
    errs = await b.read("errs")
    checked = await b.read("checked")
    checks.check("TEST write+read 4 KiB: clean, every word checked",
                 st["code"] == 0 and errs == 0 and checked == 2048 and words == 4096,
                 f"{st} errs {errs} checked {checked} words {words}")
    st = await b.cmd(hrp.OP_TEST, mode=hrp.TEST_READ << 2, xor=0x0001)
    errs = await b.read("errs")
    err = await b.read("errors")
    irq = ctx.get(periph.irq)
    checks.check("negative control: XOR'd checker fails every word, CHECK + IRQ",
                 st["code"] == hrp.CODE_CHECK and errs == 2048 and err >> hrp.ERR_CHECK & 1
                 and irq, f"{st} errs {errs} errors {err:#x} irq {irq}")
    bi, gw = await b.read("bad_index"), await b.read("bad_got_want")
    want0 = hrp.pattern_py(0x2000) ^ 0x1234 ^ 0x0001
    checks.check("first bad index / got / want",
                 bi == 0x2000 and gw & 0xFFFF == want0 and gw >> 16 == want0 ^ 1,
                 f"index {bi:#x} got/want {gw:#010x}")
    await b.write("xor", 0)
    await b.write("errors", 0x1FFF)
    await b.write("events", 3)

    # a corrupting device is seen by a clean checker
    dev.corrupt = lambda: True
    st = await b.cmd(hrp.OP_TEST, hr_addr=0x4000, len=256, mode=hrp.TEST_READ << 2)
    errs = await b.read("errs")
    checks.check("negative control: corrupted reads are counted", errs == 128 and
                 st["code"] == hrp.CODE_CHECK, f"{st} errs {errs}")
    dev.corrupt = lambda: False

    # refusals
    st = await b.cmd(hrp.OP_FILL, hr_addr=0, len=0)
    checks.check("len 0 refused: ARG", st["code"] == hrp.CODE_ARG, str(st))
    st = await b.cmd(hrp.OP_COPY_IN, hr_addr=0, win_addr=4000, len=200)
    checks.check("window overrun refused: ARG", st["code"] == hrp.CODE_ARG, str(st))
    st = await b.cmd(hrp.OP_FILL, hr_addr=hrp.BYTES - 2, len=4)
    checks.check("array overrun refused: ARG", st["code"] == hrp.CODE_ARG, str(st))
    await b.write("hr_addr", 0)
    await b.write("len", 64 * 1024)
    await b.write("cmd", hrp.OP_FILL)
    await b.write("cmd", hrp.OP_FILL)
    v = await b.status()
    checks.check("command while busy refused: BUSY", v["code"] == hrp.CODE_BUSY, str(v))
    await b.wait_idle(40000)

    # watchdog: the engine ignores `done`, the bound fires, the next op works
    await b.write("errors", 0x1FFF)
    st = await b.cmd(hrp.OP_FILL, hr_addr=0x6000, len=64, mode=hrp.FILL_PATTERN | (1 << 4))
    err = await b.read("errors")
    state = await b.read("state")
    checks.check("forced hang: WDOG code, sticky bit, STATE names the XFER",
                 st is not None and st["code"] == hrp.CODE_WDOG and err >> hrp.ERR_WDOG & 1
                 and state & 0xF == 3, f"{st} errors {err:#x} state {state:#x}")
    await b.write("mode", 0)
    st = await b.cmd(hrp.OP_FILL, hr_addr=0x6000, len=4, mode=hrp.FILL_CONST, fill_value=0x99)
    checks.check("core still in RESET# after the watchdog: NOT_READY",
                 st["code"] == hrp.CODE_NOT_READY or st["code"] == 0, str(st))
    st = await wait_boot(b)
    st = await b.cmd(hrp.OP_FILL, hr_addr=0x6000, len=4, mode=hrp.FILL_CONST, fill_value=0x99)
    checks.check("after the watchdog: core reset, next op OK",
                 st["code"] == 0 and dev.byte(0x6000) == 0x99, str(st))

    # broken expectation must fail (the sim's own negative control)
    checks.check("negative control of the bench: a wrong expectation is caught",
                 not ([dev.byte(0x2000 + i) for i in range(4)] == [0x44, 0x33, 0x22, 0x11]))


async def s_stream(checks, b, dev, ctx, periph):
    await boot(checks, b, "stream")
    base, words = 0x100000, 4096
    await b.write("stream_base", base)
    await b.write("stream_mask", words - 1)
    await b.write("stream_ctrl", 1)
    n_entries = 1024
    sent, got = [], []

    # a FILL runs concurrently, interleaved by the arbiter
    await b.write("hr_addr", 0x8000)
    await b.write("len", 8192)
    await b.write("mode", hrp.FILL_CONST)
    await b.write("fill_value", 0x5AA5C33C)
    await b.write("cmd", hrp.OP_FILL)
    for _ in range(200000):
        if len(sent) < n_entries:
            if ctx.get(periph.stream_w_rdy):
                v = (hrp.pattern_py(2 * len(sent)) | hrp.pattern_py(2 * len(sent) + 1) << 16) \
                    ^ 0xA5A55A5A
                ctx.set(periph.stream_w_data, v)
                ctx.set(periph.stream_w_en, 1)
                sent.append(v)
            else:
                ctx.set(periph.stream_w_en, 0)
        else:
            ctx.set(periph.stream_w_en, 0)
        rdy = ctx.get(periph.stream_r_rdy)
        if rdy:
            got.append(ctx.get(periph.stream_r_data))
        ctx.set(periph.stream_r_en, rdy)
        await ctx.tick("sync")
        ctx.set(periph.stream_r_en, 0)
        if len(got) >= n_entries - STREAM_TAIL:
            break
    st = await b.wait_idle(20000)
    fill_ok = all(dev.byte(0x8000 + i) == (0x3C, 0xC3, 0xA5, 0x5A)[i & 3] for i in range(0, 8192, 97))
    n = len(got)
    checks.check("stream: data out == data in, in order, through the ring",
                 n >= n_entries - STREAM_TAIL and got == sent[:n], f"got {n} of {len(sent)}")
    checks.check("stream: the concurrent FILL completed and is right",
                 st is not None and st["code"] == 0 and fill_ok, str(st))
    err = await b.read("errors")
    checks.check("stream: no FIFO misuse flagged", err & ((1 << hrp.ERR_STREAM_OVF)
                                                         | (1 << hrp.ERR_STREAM_UNF)) == 0,
                 f"errors {err:#x}")

    # stager pairs: even word address, each half low byte first
    for k, (addr, data) in enumerate(((0x40000, 0x44332211), (0x40002, 0x88776655))):
        ctx.set(periph.pair_addr, addr)
        ctx.set(periph.pair_data, data)
        ctx.set(periph.pair_req, 1)
        for _ in range(200):
            await ctx.tick("sync")
            if ctx.get(periph.pair_ack):
                break
        ctx.set(periph.pair_req, 0)
        await ctx.tick("sync").repeat(4)
    await ctx.tick("sync").repeat(100)
    have = [dev.byte(0x80000 + i) for i in range(8)]
    checks.check("stager pairs land little-endian at the byte address",
                 have == [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88], str(have))


STREAM_TAIL = 128     # the last partial burst stays in the ring until more arrives


async def s_cal_none(checks, b, dev, ctx, periph):
    bad_band(dev, 0, 128)
    st = await wait_boot(b)
    cal = await b.read("cal")
    err = await b.read("errors")
    checks.check("no clean tap: CAL invalid, cal_none sticky",
                 st is not None and not st["cal"] and not (cal >> 16) & 1
                 and err >> hrp.ERR_CAL_NONE & 1, f"{st} cal {cal:#x} errors {err:#x}")
    st = await b.cmd(hrp.OP_COPY_IN, hr_addr=0, win_addr=0, len=4)
    checks.check("uncalibrated read refused: UNCAL", st["code"] == hrp.CODE_UNCAL, str(st))
    st = await b.cmd(hrp.OP_FILL, hr_addr=0, len=4)
    checks.check("writes still work uncalibrated", st["code"] == 0, str(st))
    dev.corrupt = lambda: False
    st = await b.cmd(hrp.OP_CALIBRATE, bound=BOOT_BOUND)
    cal = await b.read("cal")
    checks.check("CALIBRATE rerun by the RV recovers", st["code"] == 0 and (cal >> 16) & 1
                 and (cal >> 8) & 0xFF == 128, f"{st} cal {cal:#x}")


async def s_ck_pll(checks, b, dev, ctx, periph):
    await boot(checks, b, "ck")
    await ctx.tick("sync").repeat(3 << CK_BITS + 2)
    count = await b.read("ck_count")
    err = await b.read("errors")
    checks.check("CK meter: count = CK/60 x 2^bits",
                 abs(count - periph.ck_expect) <= hrp.CK_TOLERANCE
                 and not err >> hrp.ERR_CK_BAD & 1, f"count {count} expect {periph.ck_expect}")
    ctx.set(periph.sim_locked, 0)
    await ctx.tick("sync").repeat(8)
    err = await b.read("errors")
    checks.check("PLL lock lost: sticky pll_lost", err >> hrp.ERR_PLL_LOST & 1, f"{err:#x}")
    ctx.set(periph.sim_locked, 1)


async def s_ck_wrong(checks, b, dev, ctx, periph):
    await boot(checks, b, "ck wrong")
    await ctx.tick("sync").repeat(3 << CK_BITS + 2)
    err = await b.read("errors")
    count = await b.read("ck_count")
    checks.check("CK off frequency (sim hr at 160): ck_bad sticky", err >> hrp.ERR_CK_BAD & 1,
                 f"count {count} expect {periph.ck_expect} errors {err:#x}")


async def s_nolock(checks, b, dev, ctx, periph):
    ctx.set(periph.sim_locked, 0)
    await ctx.tick("sync").repeat(int(2e-6 * SYNC_MHZ * 1e6 * 1.3) + 20)
    err = await b.read("errors")
    st = await b.status()
    checks.check("PLL never locks: pll_nolock sticky, not ready", err >> hrp.ERR_PLL_NOLOCK & 1
                 and not st["ready"], f"{err:#x} {st}")
    await b.write("cmd", hrp.OP_FILL)
    st = await b.status()
    checks.check("command without a locked PLL refused: NOT_READY",
                 st["code"] == hrp.CODE_NOT_READY, str(st))


def main():
    emit("hyperram peripheral -- #581 core at CK 180, window + COPY, FILL/TEST, CAL, errors")
    checks = Checks(emit)
    dev = SocDevice()
    bad_band(dev, 20, 40)
    run(checks, "main", s_main, dev=dev, sim_fast_delay=False)
    run(checks, "stream + stager", s_stream)
    run(checks, "no clean tap", s_cal_none)
    run(checks, "ck + pll", s_ck_pll)
    run(checks, "ck wrong", s_ck_wrong, hr_mhz=160.0)
    run(checks, "pll never locks", s_nolock, pll_lock_s=2e-6)
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
