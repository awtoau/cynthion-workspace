#!/usr/bin/env python3
#
# The SoC config-flash peripheral (#593) against the #590 W25Q32JV datasheet model.
# SPDX-License-Identifier: BSD-3-Clause

"""`gateware/soc/peripherals/flash.py` driven through its CSRs, XIP window and buffer.

- Device: `flash_baseline_sim.W25Q32JV` (rev G), plus 66h/99h reset, opcode-ignore,
  stuck-BUSY and data-corruption hooks. Clocks at the real rates: sync 60, flash 190 MHz,
  MCLK +180 degrees gated by `ck_en`, data tCLQV 5 ns after MCLK falling; the capture
  clock is moved T/32 per PHASESTEP pulse, so calibration probes a real (sim) eye.
- Covers: auto-calibration (maps, centre, verify), XIP refills (one EBh per line, in
  order, mid-line start, an abandoned line), every command, protect/arg refusals, XIP
  during a command, XIP writes, abort, IRQ, and the error paths (watchdog, WEL,
  NOT_STARTED, no eye, a blank reference, PLL never locks, SCK wrong).
- Negative controls: a corrupted model byte is seen by XIP and READ; a corrupting phase
  band shows as dirty points in the map; changed flash content is read fresh.

    ./scripts/soc/flash_peripheral_sim.py     # log: tmp/logs/dev.log
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "flash"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402
from flash_baseline_sim import W25Q32JV  # noqa: E402

from amaranth import ClockDomain, ClockSignal, Module  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import peripherals.flash as fp  # noqa: E402

SYNC_MHZ, SCK_MHZ = 60.0, 190.0
T = 1 / (SCK_MHZ * 1e6)
TCLQV = 5e-9
LAT_ROWS = 8                    # the sim's round trip is lat 1-3; 8 rows keep it short
N_STEPS = 32                    # PHASESTEPs per period at SCK 190 (PLL output divider 4)
BUSY_SCALE = 1e-3               # model BUSY is 300..3000 CLK; bounds scale with it
CK_BITS = 8
WRITE_LO, WRITE_HI = 0x280000, 0x380000
HEADER = bytes([0xFF, 0x00]) + b"Part: LFE5U-25F-6CABGA256\x00\xff\xff" + bytes(range(0x30, 0x50))


class SocW25(W25Q32JV):
    """#590 model + 66h/99h, ignored opcodes, stuck BUSY and a corruption hook."""

    def __init__(self):
        super().__init__()
        self.reset_en = False
        self.resets = 0
        self.ignore = set()
        self.stuck = False
        self.corrupt = lambda: False
        self.cur = None
        self.eb = 0

    def transaction(self):
        self.cur = None
        n = len(self.cmds)
        inner = W25Q32JV.transaction(self)
        out = next(inner)
        while True:
            io = yield out
            out = inner.send(io)
            if self.cur is None and len(self.cmds) > n:
                self.cur = self.cmds[-1]
                self.eb += self.cur == 0xEB
            if out is not None and out[0] != "ddr" and self.corrupt():
                out = (out[0] ^ out[1], out[1])

    def cs_rise(self):
        c = self.cur
        if c in self.ignore:
            self.pending = None
        if c == 0x66 and self.bits_in == 8:
            self.reset_en = True
        elif c == 0x99 and self.bits_in == 8 and self.reset_en:
            self.wel, self.busy_until, self.resets = False, 0, self.resets + 1
            self.reset_en = False
        elif c is not None:
            self.reset_en = False
        p = self.pending
        super().cs_rise()
        if self.stuck and p is not None and p[0] in ("erase", "program") and self.busy():
            self.busy_until = 1 << 60

    def program(self, a, data):
        for i, b in enumerate(data):
            self.mem[a + i] = b


class Bench:
    def __init__(self, periph, ctx, cap):
        self.p, self.ctx, self.cap = periph, ctx, cap
        self.regs = periph._regs
        self.max_wait = 0

    async def tick(self, n=1):
        await self.ctx.tick("sync").repeat(n)

    def _size(self, name):
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
        return dict(busy=v & 1, ready=(v >> 1) & 1, locked=(v >> 2) & 1, init=(v >> 3) & 1,
                    xip=(v >> 4) & 1, eng_busy=(v >> 5) & 1, code=(v >> 8) & 15,
                    op=(v >> 12) & 15)

    async def wait_idle(self, bound):
        for _ in range(bound):
            st = await self.status()
            if not st["busy"]:
                return st
        return None

    async def cmd(self, op, bound=4000, **regs):
        for name, value in regs.items():
            await self.write(name, value)
        await self.write("cmd", op)
        return await self.wait_idle(bound)

    async def buf_write(self, offset, data):
        ctx, wb = self.ctx, self.p.buf
        for i in range(0, len(data), 4):
            word = int.from_bytes(bytes(data[i:i + 4]).ljust(4, b"\0"), "little")
            ctx.set(wb.adr, (offset + i) >> 2)
            ctx.set(wb.dat_w, word)
            ctx.set(wb.sel, 0xF)
            ctx.set(wb.we, 1)
            ctx.set(wb.cyc, 1)
            ctx.set(wb.stb, 1)
            await self.tick()
            while not ctx.get(wb.ack):
                await self.tick()
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            await self.tick()

    async def buf_read(self, offset, n):
        ctx, wb = self.ctx, self.p.buf
        out = []
        for i in range(0, n, 4):
            ctx.set(wb.adr, (offset + i) >> 2)
            ctx.set(wb.we, 0)
            ctx.set(wb.sel, 0xF)
            ctx.set(wb.cyc, 1)
            ctx.set(wb.stb, 1)
            await self.tick()
            while not ctx.get(wb.ack):
                await self.tick()
            out += list(ctx.get(wb.dat_r).to_bytes(4, "little"))
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            await self.tick()
        return out[:n]

    async def xip_beats(self, words, we=False):
        """VexiiRiscv refill shape: `cyc` held, one strobe per beat, one idle cycle after each
        ack (RegisteredResponse). Returns [(word or None on ERR, wait cycles)]."""
        ctx, x = self.ctx, self.p.xip
        out = []
        bound = fp.xip_wait_bound(SYNC_MHZ) + 4
        ctx.set(x.cyc, 1)
        for w in words:
            ctx.set(x.adr, w)
            ctx.set(x.we, int(we))
            ctx.set(x.sel, 0xF)
            ctx.set(x.stb, 1)
            n = 0
            while not (ctx.get(x.ack) or ctx.get(x.err)) and n < bound:
                await self.tick()
                n += 1
            self.max_wait = max(self.max_wait, n)
            got = None if (ctx.get(x.err) or n >= bound) else ctx.get(x.dat_r)
            await self.tick()
            ctx.set(x.stb, 0)
            await self.tick()
            out.append((got, n))
        ctx.set(x.cyc, 0)
        ctx.set(x.we, 0)
        await self.tick()
        return out

    async def xip_line(self, addr):
        base = (addr >> 2) & ~15
        return await self.xip_beats(range(base, base + 16))


def expect_words(dev, addr, n_words):
    return [int.from_bytes(bytes(dev.read(addr + 4 * i + k) for k in range(4)), "little")
            for i in range(n_words)]


def run(checks, name, scenario, dev=None, sck_mhz=SCK_MHZ, **kwargs):
    dev = dev or SocW25()
    periph = fp.Flash(sync_mhz=SYNC_MHZ, sim=True, busy_scale=BUSY_SCALE,
                      ck_window_bits=CK_BITS, lat_rows=LAT_ROWS, **kwargs)
    m = Module()
    cd_cap = ClockDomain("flash_cap", reset_less=True)
    mclk = ClockDomain("mclk")
    m.domains += [ClockDomain("sync"), ClockDomain("flash"), ClockDomain("usb"), cd_cap,
                  ClockDomain("flash_capn", clk_edge="neg", reset_less=True), mclk]
    m.d.comb += ClockSignal("flash_capn").eq(ClockSignal("flash_cap"))
    m.submodules.p = periph
    f_val, f_oe = [0] * 4, [0] * 4
    cap = {"steps": 0, "phase": 0}
    dev.cap = cap
    stats = {"no_setup": 0, "contention": 0, "cs_high_min": None}

    def apply(ctx, drive):
        if drive is None:
            f_oe[:] = [0] * 4
        else:
            for n in range(4):
                f_val[n], f_oe[n] = (drive[0] >> n) & 1, (drive[1] >> n) & 1
            if drive[1] & ctx.get(periph.pad_oe):
                stats["contention"] += 1
        po, poe = ctx.get(periph.pad_o), ctx.get(periph.pad_oe)
        ctx.set(periph.pad_i, sum(((f_val[n] if f_oe[n] else (po >> n) & (poe >> n) & 1) << n)
                                  for n in range(4)))

    pending_drive = []

    async def board(ctx):
        # Samples on MCLK rising; the drive it returns goes out tCLQV after the next falling
        # edge (`driver`). tCLQV is most of a period at 190 MHz, so one process cannot do both.
        gated, cs_high = 0, 0
        while True:
            await ctx.posedge(mclk.clk)
            dev.clk += 1
            cs = ctx.get(periph.pad_cs)
            if cs and not ctx.get(periph.pad_ck_en):
                gated += 1
                pending_drive.append("keep")
                continue
            if cs and gated == 0 and dev.gen is None:
                stats["no_setup"] += 1
            gated = 0
            if not cs:
                if dev.gen is not None:
                    dev.cs_rise()
                cs_high += 1
                drive = None
            else:
                if cs_high and dev.clk > 50:
                    lo = stats["cs_high_min"]
                    stats["cs_high_min"] = cs_high if lo is None else min(lo, cs_high)
                cs_high = 0
                if dev.gen is None:
                    dev.gen = dev.transaction()
                    next(dev.gen)
                io = ctx.get(periph.pad_o) & ctx.get(periph.pad_oe)
                drive = dev.gen.send(io)
            pending_drive.append(drive)

    async def driver(ctx):
        while True:
            await ctx.negedge(mclk.clk)
            if not pending_drive:
                continue
            drive = pending_drive.pop(0)
            await ctx.delay(TCLQV)
            if drive != "keep":
                apply(ctx, drive)

    async def capclk(ctx):
        prev, applied = 1, 0
        await ctx.delay(T / 2)
        while True:
            ctx.set(cd_cap.clk, 1)
            await ctx.delay(T / 2)
            ctx.set(cd_cap.clk, 0)
            ps = ctx.get(periph.phasestep)
            if prev and not ps:
                cap["steps"] += 1
            prev = ps
            extra = (cap["steps"] - applied) * T / periph.n_steps
            applied = cap["steps"]
            cap["phase"] = applied % periph.n_steps
            await ctx.delay(T / 2 + extra)

    result = {}

    async def control(ctx):
        bench = Bench(periph, ctx, cap)
        await scenario(checks, bench, dev, ctx, periph)
        result["ok"] = True
        result["max_wait"] = bench.max_wait

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(1 / (SYNC_MHZ * 1e6), domain="sync")
    sim.add_clock(1 / (sck_mhz * 1e6), domain="flash")
    sim.add_clock(1 / (sck_mhz * 1e6), domain="mclk", phase=1 / (sck_mhz * 1e6))
    sim.add_clock(1 / 60e6, domain="usb", phase=3.3e-9)
    sim.add_testbench(board, background=True)
    sim.add_testbench(driver, background=True)
    sim.add_testbench(capclk, background=True)
    sim.add_testbench(control)
    emit(f"scenario: {name}")
    sim.run()
    checks.check(f"{name}: scenario ran to the end", result.get("ok", False))
    checks.check(f"{name}: every transaction had a stopped-clock /CS setup cycle",
                 stats["no_setup"] == 0, f"{stats['no_setup']} without")
    checks.check(f"{name}: no bus contention", stats["contention"] == 0,
                 f"{stats['contention']} cycles")
    lo = stats["cs_high_min"]
    checks.check(f"{name}: /CS high >= tSHSL1 between transactions",
                 lo is None or lo * T >= fp.T_SHSL1_NS * 1e-9, f"min {lo} CLK")
    return result


# ---------------------------------------------------------------- scenarios
BOOT_BOUND = 80000          # status polls (2-3 sync cycles each): reset + 2 x 512 probes, 1 us per step


async def wait_ready(b, bound=BOOT_BOUND):
    for _ in range(bound):
        st = await b.status()
        if st["ready"] and not st["eng_busy"]:
            return st
    return None


async def boot(checks, b, tag):
    st = await wait_ready(b)
    checks.check(f"{tag}: reset + auto-calibration finish, ready", st is not None)
    return st


def runs(maps):
    """Widest clean run on the lat*32+phase axis per xfer, from the map rows."""
    best = (0, 0, 0)
    for x in (0, 1):
        bits = []
        for lat in range(LAT_ROWS):
            row = maps[(x, lat)]
            bits += [(row >> p) & 1 for p in range(N_STEPS)]
        start, n = 0, 0
        for i, bit in enumerate(bits):
            if bit:
                start, n = (i, 1) if n == 0 else (start, n + 1)
                if n > best[0]:
                    best = (n, start, x)
            else:
                n = 0
    return best


async def read_maps(b, off):
    raw = await b.buf_read(off, 2 * 16 * 4)
    return {(x, lat): int.from_bytes(bytes(raw[(x * 16 + lat) * 4:(x * 16 + lat) * 4 + 4]),
                                     "little")
            for x in (0, 1) for lat in range(LAT_ROWS)}


def seed_flash(dev, rng):
    dev.program(0, HEADER)
    data = [rng.randrange(256) for _ in range(8192)]
    dev.program(0x0B0000, data)
    return data


async def s_main(checks, b, dev, ctx, periph):
    rng = random.Random(593)
    await boot(checks, b, "main")
    checks.check("boot sends 66h+99h (SPI) once", dev.resets == 1, f"resets {dev.resets}")
    cal = await b.read("cal")
    centre, width, xfer, valid = cal & 0x1FF, (cal >> 16) & 0xFF, (cal >> 24) & 1, cal >> 31
    maps = await read_maps(b, fp.map_offsets(N_STEPS)[1])
    n, start, x = runs(maps)
    checks.check("cal: EBh placement = centre of the widest clean run in its own map",
                 valid and (width, centre, xfer) == (min(n, 255), start + n // 2, x),
                 f"cal {cal:#010x}; map run {n} @{start} xfer {x}",
                 measurement=f"EBh eye {width} points, centre lat {centre >> 5} "
                             f"phase {centre & 31} xfer {xfer}")
    jmaps = await read_maps(b, fp.map_offsets(N_STEPS)[0])
    jn, js, jx = runs(jmaps)
    checks.check("cal: the 9Fh map has a clean run", jn > 0, f"run {jn} @{js}",
                 measurement=f"9Fh eye {jn} points @{js} xfer {jx}")
    dirty = [maps[k] for k in maps]
    checks.check("cal: the map has dirty points (an eye, not a constant)",
                 any(r != 0 for r in dirty) and any(r != (1 << 32) - 1 for r in dirty))
    ref = await b.buf_read(fp.map_offsets(N_STEPS)[2], fp.CAL_LEN)
    checks.check("cal: reference = 0Bh read of CAL_ADDR", ref == [dev.read(i) for i in range(64)])

    await b.write("irq_en", 3)
    await b.write("write_lo", WRITE_LO >> 12)
    await b.write("write_hi", WRITE_HI >> 12)

    # ---- XIP
    data = [dev.read(0x0B0000 + i) for i in range(8192)]
    eb0 = dev.eb
    beats = await b.xip_line(0x0B0040)
    want = expect_words(dev, 0x0B0040, 16)
    checks.check("XIP line refill: 16 words in order, one EBh transaction",
                 [w for w, _ in beats] == want and dev.eb - eb0 == 1,
                 f"EBh {dev.eb - eb0}; got {[hex(w or 0) for w, _ in beats[:3]]}",
                 measurement=f"refill {sum(n + 2 for _, n in beats)} sync cycles, first beat "
                             f"{beats[0][1]}, worst beat {max(n for _, n in beats)}")
    fill_last = await b.read("fill_last")
    checks.check("FILL_LAST counts the refill", 0 < fill_last <= sum(n + 2 for _, n in beats) + 4,
                 str(fill_last))
    ok = True
    for line in rng.sample(range(0x0B0000, 0x0B2000, 64), 6):
        got = [w for w, _ in await b.xip_line(line)]
        ok &= got == expect_words(dev, line, 16)
    checks.check("XIP: six random lines", ok)
    base = (0x0B0400 >> 2)
    got = await b.xip_beats(range(base + 5, base + 16))
    checks.check("XIP: a refill starting mid-line skips the words before it",
                 [w for w, _ in got] == expect_words(dev, 0x0B0400 + 20, 11))
    got1 = await b.xip_beats(range(base + 64, base + 67))
    got2 = await b.xip_line(0x0B0800)
    checks.check("XIP: an abandoned line is drained before the next one",
                 [w for w, _ in got1] == expect_words(dev, 0x0B0500, 3)
                 and [w for w, _ in got2] == expect_words(dev, 0x0B0800, 16))
    dev.mem[0x0B0C05] ^= 0x40
    got = [w for w, _ in await b.xip_line(0x0B0C00)]
    checks.check("neg: a corrupted model byte is seen by XIP (no stale copy)",
                 got == expect_words(dev, 0x0B0C00, 16) and got[1] != int.from_bytes(
                     bytes(data[0xC04:0xC08]), "little"))
    got = await b.xip_beats([base], we=True)
    err = await b.read("errors")
    checks.check("XIP write: ERR + sticky xip_write", got[0][0] is None
                 and err >> fp.ERR_XIP_WRITE & 1, f"errors {err:#x}")
    await b.write("errors", (1 << fp.N_ERR) - 1)
    await b.write("events", 3)

    # ---- commands
    st = await b.cmd(fp.OP_JEDEC)
    res = await b.read("result")
    checks.check("JEDEC = EF4016", st["code"] == 0 and res & 0xFFFFFF == 0xEF4016,
                 f"{st} {res:#x}")
    st = await b.cmd(fp.OP_STATUS)
    res = await b.read("result")
    checks.check("STATUS = SR3 60 | SR2 02 | SR1 00", st["code"] == 0 and res == 0x600200,
                 f"{st} {res:#x}")
    ev = await b.read("events")
    checks.check("done event + IRQ", ev & 1 and ctx.get(periph.irq), f"events {ev:#x}")
    await b.write("events", 3)
    st = await b.cmd(fp.OP_WREN)
    checks.check("WREN: WEL set", st["code"] == 0 and dev.wel, str(st))

    a = 0x281000
    dev.program(a, [0x00] * 64)
    st = await b.cmd(fp.OP_ERASE_4K, addr=a + 0x123)
    busy = await b.read("busy_cycles")
    busy = busy * SCK_MHZ / SYNC_MHZ          # BUSY_CYCLES counts sync cycles
    checks.check("ERASE_4K: done, BUSY polled to the model's time",
                 st["code"] == 0 and abs(busy - 1500) < 80 and all(
                     dev.read(a + i) == 0xFF for i in range(4096)),
                 f"{st} busy {busy}", measurement=f"busy {busy:.0f} CLK (model 1500)")
    st = await b.cmd(fp.OP_READ, addr=a, buf_addr=0x200, len=256)
    got = await b.buf_read(0x200, 256)
    checks.check("READ after erase: blank", st["code"] == 0 and got == [0xFF] * 256, str(st))
    page = [rng.randrange(256) for _ in range(256)]
    await b.buf_write(0x400, page)
    st = await b.cmd(fp.OP_PROGRAM, addr=a, buf_addr=0x400, len=256)
    checks.check("PROGRAM 256 B from the buffer", st["code"] == 0
                 and [dev.read(a + i) for i in range(256)] == page, str(st))
    st = await b.cmd(fp.OP_READ, addr=a, buf_addr=0x800, len=256)
    got = await b.buf_read(0x800, 256)
    checks.check("READ back = programmed page", st["code"] == 0 and got == page, str(st))
    got = [w for w, _ in await b.xip_line(a + 64)]
    checks.check("XIP read of the programmed page", got == expect_words(dev, a + 64, 16))
    dev.mem[a + 9] ^= 0x01
    st = await b.cmd(fp.OP_READ, addr=a, buf_addr=0x800, len=16)
    got = await b.buf_read(0x800, 16)
    checks.check("neg: a corrupted byte is seen by READ", got[9] == page[9] ^ 1 and got[:9] == page[:9])
    st = await b.cmd(fp.OP_PROGRAM, addr=a + 0x100 + 7, buf_addr=0x400, len=9)
    checks.check("PROGRAM 9 B at an unaligned offset", st["code"] == 0 and [
        dev.read(a + 0x107 + i) for i in range(9)] == page[:9], str(st))
    st = await b.cmd(fp.OP_ERASE_64K, addr=0x290000)
    busy = await b.read("busy_cycles")
    busy = busy * SCK_MHZ / SYNC_MHZ
    checks.check("ERASE_64K: done", st["code"] == 0 and abs(busy - 3000) < 80, f"{st} {busy}")

    # refusals
    for what, op, regs, code in (
            ("erase below WRITE_LO", fp.OP_ERASE_4K, dict(addr=0x27F000), fp.CODE_PROTECT),
            ("64K erase crossing WRITE_HI", fp.OP_ERASE_64K, dict(addr=0x380000), fp.CODE_PROTECT),
            ("program across a page", fp.OP_PROGRAM, dict(addr=a + 0xF0, len=32), fp.CODE_ARG),
            ("program > 256 B", fp.OP_PROGRAM, dict(addr=a, len=257), fp.CODE_ARG),
            ("read past the buffer", fp.OP_READ, dict(buf_addr=0xF00, len=0x200), fp.CODE_ARG),
            ("read length 0", fp.OP_READ, dict(len=0), fp.CODE_ARG),
            ("op 0", 0, {}, fp.CODE_ARG), ("op 15", 15, {}, fp.CODE_ARG)):
        st = await b.cmd(op, **regs)
        checks.check(f"refused: {what}", st["code"] == code, str(st))
    await b.write("write_hi", 0)
    st = await b.cmd(fp.OP_PROGRAM, addr=a, buf_addr=0, len=4)
    checks.check("WRITE_HI 0: every write refused", st["code"] == fp.CODE_PROTECT, str(st))
    await b.write("write_hi", WRITE_HI >> 12)

    # XIP while a command is in flight, then a mid-erase abort
    await b.write("addr", 0x2A0000)
    await b.write("cmd", fp.OP_ERASE_64K)
    got = await b.xip_beats([base])
    err = await b.read("errors")
    checks.check("XIP during a command: ERR + sticky xip_busy",
                 got[0][0] is None and err >> fp.ERR_XIP_BUSY & 1, f"errors {err:#x}")
    await b.write("ctrl", 1)
    st = await b.wait_idle(100)
    checks.check("abort mid-erase: ABORT", st and st["code"] == fp.CODE_ABORT, str(st))
    st = await wait_ready(b)
    checks.check("after abort: reset (66h/99h) and recalibration, ready",
                 st is not None and dev.resets == 2 and not dev.busy(), f"resets {dev.resets}")
    got = [w for w, _ in await b.xip_line(0x0B0040)]
    checks.check("XIP after abort", got == expect_words(dev, 0x0B0040, 16))
    st = await b.cmd(fp.OP_RESET)
    checks.check("RESET: 66h+99h, ok, still calibrated",
                 st["code"] == 0 and dev.resets == 3 and (await b.status())["ready"], str(st))
    st = await b.cmd(fp.OP_CALIBRATE, bound=BOOT_BOUND)
    cal2 = await b.read("cal")
    # Sim edges land exactly on data changes at some phases (float time), so a rerun's
    # eye can differ by a few points; the placement must stay within the eye.
    checks.check("CALIBRATE rerun: ok, valid, placement within 3 points",
                 st["code"] == 0 and cal2 >> 31 and abs((cal2 & 0x1FF) - (cal & 0x1FF)) <= 3,
                 f"{st} {cal2:#x} vs {cal:#x}")
    seq = await b.read("seq")
    checks.check("SEQ counts finished commands", seq > 20, str(seq))
    checks.note(f"XIP worst beat {b.max_wait} sync cycles, bound {fp.xip_wait_bound(SYNC_MHZ)}")


async def s_errors(checks, b, dev, ctx, periph):
    await boot(checks, b, "errors")
    await b.write("write_lo", WRITE_LO >> 12)
    await b.write("write_hi", WRITE_HI >> 12)
    dev.ignore = {0x06}
    st = await b.cmd(fp.OP_ERASE_4K, addr=0x280000)
    checks.check("WREN ignored: WEL", st["code"] == fp.CODE_WEL, str(st))
    dev.ignore = {0x20}
    st = await b.cmd(fp.OP_ERASE_4K, addr=0x280000)
    err = await b.read("errors")
    checks.check("erase not taken: NOT_STARTED + sticky", st["code"] == fp.CODE_NOT_STARTED
                 and err >> fp.ERR_NOT_STARTED & 1, f"{st} {err:#x}")
    dev.ignore, dev.stuck = set(), True
    await b.buf_write(0, [0x55] * 16)
    st = await b.cmd(fp.OP_PROGRAM, addr=0x280000, buf_addr=0, len=16, bound=8000)
    err = await b.read("errors")
    state = await b.read("state")
    checks.check("BUSY never clears: WDOG at 1.25 x tPP, sticky, state latched",
                 st["code"] == fp.CODE_WDOG and err >> fp.ERR_WDOG & 1 and state & 15 == 6,
                 f"{st} errors {err:#x} state {state:#x}")
    dev.stuck = False
    st = await wait_ready(b)
    checks.check("after WDOG: reset clears BUSY, recalibrates", st is not None and not dev.busy())


async def s_cal_none(checks, b, dev, ctx, periph):
    st = None
    for _ in range(BOOT_BOUND):
        st = await b.status()
        if st["init"] and not st["eng_busy"]:
            break
    err = await b.read("errors")
    checks.check("no eye at any point: CAL_NONE, not ready, sticky cal",
                 not st["ready"] and err >> fp.ERR_CAL & 1, f"{st} {err:#x}")
    got = await b.xip_beats([0x0B0000 >> 2])
    err = await b.read("errors")
    checks.check("XIP uncalibrated: ERR + sticky xip_uncal",
                 got[0][0] is None and err >> fp.ERR_XIP_UNCAL & 1, f"{err:#x}")
    st = await b.cmd(fp.OP_JEDEC)
    checks.check("JEDEC uncalibrated: UNCAL", st["code"] == fp.CODE_UNCAL, str(st))


async def s_blank_ref(checks, b, dev, ctx, periph):
    st = None
    for _ in range(BOOT_BOUND):
        st = await b.status()
        if st["init"] and not st["eng_busy"]:
            break
    checks.check("blank reference at CAL_ADDR: not ready", not st["ready"], str(st))
    await b.write("cal_addr", 0x0B0000)
    st = await b.cmd(fp.OP_CALIBRATE, bound=BOOT_BOUND)
    checks.check("CALIBRATE with CAL_ADDR at a rich region: ok, ready",
                 st["code"] == 0 and (await b.status())["ready"], str(st))


async def s_band(checks, b, dev, ctx, periph):
    await boot(checks, b, "band")
    maps = await read_maps(b, fp.map_offsets(N_STEPS)[1])
    bad = [(x, lat) for (x, lat), row in maps.items() if any(row >> p & 1 for p in range(12, 16))]
    checks.check("neg: points read in a corrupting phase band are dirty in the map", not bad,
                 f"rows clean in 12..15: {bad}")
    cal = await b.read("cal")
    checks.check("band: placement outside the band", not 12 <= (cal & 31) < 16, f"{cal:#x}")


async def s_nolock(checks, b, dev, ctx, periph):
    ctx.set(periph.sim_locked, 0)
    await b.tick(400)
    err = await b.read("errors")
    st = await b.status()
    checks.check("PLL never locks: pll_nolock, not ready", err >> fp.ERR_PLL_NOLOCK & 1
                 and not st["ready"], f"{err:#x} {st}")
    await b.write("cmd", fp.OP_JEDEC)
    st = await b.status()
    checks.check("command without lock: NOT_READY", st["code"] == fp.CODE_NOT_READY, str(st))


async def s_ck_wrong(checks, b, dev, ctx, periph):
    await b.tick(3000)
    ck = await b.read("ck_count")
    want = round(180 / 60 * (1 << CK_BITS))
    checks.check("CK_COUNT reads the clock actually running (180, not 190)", abs(ck - want) <= 2,
                 f"ck_count {ck}, want {want}")


def main():
    emit("flash peripheral -- #590 core at SCK 190, XIP + command port, calibration, errors")
    checks = Checks(emit)
    rng = random.Random(1)

    def seeded(**hooks):
        dev = SocW25()
        seed_flash(dev, rng)
        for k, v in hooks.items():
            setattr(dev, k, v)
        return dev

    run(checks, "main", s_main, dev=seeded())
    run(checks, "errors", s_errors, dev=seeded())
    run(checks, "no eye", s_cal_none, dev=seeded(corrupt=lambda: True))
    blank = SocW25()
    blank.program(0x0B0000, [rng.randrange(256) for _ in range(256)])
    run(checks, "blank reference", s_blank_ref, dev=blank)
    band = seeded()
    band.corrupt = lambda: 12 <= band.cap["phase"] < 16
    run(checks, "corrupting phase band", s_band, dev=band)
    run(checks, "pll never locks", s_nolock, dev=seeded(), pll_lock_s=2e-6)
    run(checks, "sck 180", s_ck_wrong, dev=seeded(), sck_mhz=180.0)
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
