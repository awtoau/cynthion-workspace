#!/usr/bin/env python3
#
# The SoC config-flash peripheral (#593) against the #590 datasheet model.
# SPDX-License-Identifier: BSD-3-Clause

"""`gateware/soc/peripherals/flash.py` driven through its CSRs, `buf` and the XIP window.

- Part: `flash_baseline_sim.W25Q32JV` (W25Q32JV rev G); data invalid from tCLQX 1.5 ns
  to tCLQV 5 ns after MCLK falls, so the capture eye is narrower than a period.
- Clocks: fsck 90 MHz (tCLQV < T/2 keeps the model one await per edge), MCLK = fsck
  inverted, fcap generated here and delayed T/N per PHASESTEP falling edge, sync 60.
- Covers: calibration after lock (x1 JEDEC, x4 EBh vs a 0Bh reference), JEDEC/SR1 over
  the command port, XIP fills (byte order, one EBh per line, hits), erase/program with
  WREN + BUSY poll, /CS held per transaction, XIP vs command interleave, refusals,
  watchdog, abort, recalibration, IRQ.
- Negative controls: a silent part (no eye, XIP refused), a blank reference (x1 only),
  a wrong expectation of the bench.

    ./scripts/soc/soc_flash_sim.py     # log: tmp/logs/dev.log
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
from flash_baseline_sim import BUSY_MODEL, W25Q32JV  # noqa: E402

from amaranth import ClockDomain, ClockSignal, Module, Mux, Signal  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

import peripherals.flash as fp  # noqa: E402

SCK_MHZ, SYNC_MHZ, N_STEPS = 90.0, 60.0, 16
T = 1 / (SCK_MHZ * 1e6)
TCLQX, TCLQV = 1.5e-9, 5.0e-9
CAP_PHASE0 = 0.3 * T
REF = 0x0B0000
REGION = 0x300000
JEDEC = fp.desc(0x9F, dw=fp.W1, read=True)
SR1 = fp.desc(0x05, dw=fp.W1, read=True)
READ0B = fp.desc(0x0B, abits=24, dummy=8, dw=fp.W1, read=True, tail=1)
ERASE = fp.desc(0x20, abits=24)
PROGRAM = fp.desc(0x02, abits=24, dw=fp.W1)


class Part(W25Q32JV):
    """The baseline model, plus a mute switch and /CS-window accounting."""

    def __init__(self):
        super().__init__()
        self.mute = False
        self.windows = 0


def le_word(dev, a):
    return sum(dev.read(a + i) << (8 * i) for i in range(4))


class Bench:
    def __init__(self, periph, ctx):
        self.p, self.ctx = periph, ctx
        self.regs = periph._regs

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
        return dict(busy=v & 1, ready=(v >> 1) & 1, locked=(v >> 2) & 1, cal=(v >> 3) & 1,
                    x4=(v >> 4) & 1, saw_busy=(v >> 5) & 1, code=(v >> 8) & 15,
                    op=(v >> 12) & 15)

    async def wait_idle(self, bound):
        for _ in range(bound):
            st = await self.status()
            if not st["busy"]:
                return st
        return None

    async def run(self, d, *, addr=0, length=0, wren=0, wait=0, wait_us=0, bound=4000,
                  op=fp.OP_RUN):
        for name, value in (("desc", d), ("addr", addr), ("len", length),
                            ("flags", wren | wait << 1), ("wait_us", wait_us)):
            await self.write(name, value)
        await self.write("cmd", op)
        return await self.wait_idle(bound)

    async def buf_words(self, n, write=None):
        ctx, wb = self.ctx, self.p.buf
        out = []
        for i in range(n):
            ctx.set(wb.adr, i)
            ctx.set(wb.sel, 0xF)
            ctx.set(wb.we, int(write is not None))
            if write is not None:
                ctx.set(wb.dat_w, write[i])
            ctx.set(wb.cyc, 1)
            ctx.set(wb.stb, 1)
            await self.tick()
            while not ctx.get(wb.ack):
                await self.tick()
            out.append(ctx.get(wb.dat_r))
            ctx.set(wb.cyc, 0)
            ctx.set(wb.stb, 0)
            await self.tick()
        return out

    async def xip(self, addr, we=False):
        """One XIP word: ('ack', data) / ('err', None) / ('none', None) past the bound."""
        ctx, wb = self.ctx, self.p.xip
        ctx.set(wb.adr, addr >> 2)
        ctx.set(wb.sel, 0xF)
        ctx.set(wb.we, int(we))
        ctx.set(wb.cyc, 1)
        ctx.set(wb.stb, 1)
        result = ("none", None)
        for _ in range(self.p.fill_limit + 16):
            await self.tick()
            if ctx.get(wb.ack):
                result = ("ack", ctx.get(wb.dat_r))
                break
            if ctx.get(wb.err):
                result = ("err", None)
                break
        ctx.set(wb.cyc, 0)
        ctx.set(wb.stb, 0)
        await self.tick()
        return result


def run(checks, name, scenario, dev=None, background=None):
    dev = dev or Part()
    periph = fp.Flash(sck_mhz=SCK_MHZ, sync_mhz=SYNC_MHZ, n_steps=N_STEPS, sim=True)
    m = Module()
    fcap, fsck = ClockDomain("fcap", reset_less=True), ClockDomain("fsck")
    m.domains += [ClockDomain("sync"), fsck, fcap,
                  ClockDomain("fcapn", clk_edge="neg", reset_less=True)]
    m.d.comb += ClockSignal("fcapn").eq(fcap.clk)
    m.submodules.p = periph
    f_val, f_oe = Signal(4), Signal(4)
    for n in range(4):
        m.d.comb += periph.pad_i[n].eq(Mux(f_oe[n], f_val[n], periph.pad_oe[n] & periph.pad_o[n]))
    fsck_clk = fsck.clk
    stats = dict(cs_high_min=None, run=0, no_setup=0, gated=0, contention=0, steps=0)
    rng = random.Random(593)

    def apply(ctx, drive):
        if drive is None or dev.mute:
            ctx.set(f_oe, 0)
            return
        ctx.set(f_val, drive[0])
        ctx.set(f_oe, drive[1])
        if drive[1] & ctx.get(periph.pad_oe):
            stats["contention"] += 1

    async def part(ctx):
        while True:
            await ctx.negedge(fsck_clk)                  # MCLK rising, when not gated
            dev.clk += 1
            cs = ctx.get(periph.pad_cs)
            drive = None
            if cs and not ctx.get(periph.pad_ck_en):
                stats["gated"] += 1
                await ctx.posedge(fsck_clk)
                continue
            if cs and stats["gated"] == 0 and dev.gen is None:
                stats["no_setup"] += 1
            stats["gated"] = 0
            if not cs:
                if dev.gen is not None:
                    dev.cs_rise()
                stats["run"] += 1
            else:
                if stats["run"] and dev.clk > 50:
                    stats["cs_high_min"] = min(stats["cs_high_min"] or 999, stats["run"])
                stats["run"] = 0
                if dev.gen is None:
                    dev.windows += 1
                    dev.gen = dev.transaction()
                    next(dev.gen)
                io = ctx.get(periph.pad_o) & ctx.get(periph.pad_oe)
                drive = dev.gen.send(io)
            await ctx.posedge(fsck_clk)                  # MCLK falling: the part drives
            if drive is not None and not dev.mute:
                await ctx.delay(TCLQX)
                ctx.set(f_val, rng.getrandbits(4))
                ctx.set(f_oe, drive[1])
                await ctx.delay(TCLQV - TCLQX)
            apply(ctx, drive)

    async def capclk(ctx):
        await ctx.delay(CAP_PHASE0)
        prev = 1
        while True:
            ctx.set(fcap.clk, 1)
            await ctx.delay(T / 2)
            ctx.set(fcap.clk, 0)
            *_, ps = await ctx.delay(T / 2).sample(periph.phasestep)
            if prev and not ps:
                stats["steps"] += 1
                await ctx.delay(T / N_STEPS)             # delay direction: capture later
            prev = ps

    result = {}

    async def control(ctx):
        ctx.set(periph.locked, 1)
        bench = Bench(periph, ctx)
        await scenario(checks, bench, dev, ctx, periph, stats)
        result["ok"] = True

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(T, domain="fsck")
    sim.add_clock(1 / (SYNC_MHZ * 1e6), domain="sync", phase=1.7e-9)
    sim.add_testbench(part, background=True)
    sim.add_process(capclk)
    sim.add_testbench(control)
    if background is not None:
        async def bg(ctx):
            await background(checks, Bench(periph, ctx), dev, ctx, periph, stats)
            result["bg"] = True
        sim.add_testbench(bg)
    emit(f"scenario: {name}")
    sim.run()
    checks.check(f"{name}: scenario ran to the end", result.get("ok", False)
                 and (background is None or result.get("bg", False)))
    return dev, stats


# ---------------------------------------------------------------- scenarios
# Status polls (a 16-bit read: 2 sync cycles): the gateware's own calibration bound,
# 1.25x, + 100 for the crossings.
BOOT_POLLS = int(1.25 * fp.cal_cycles(N_STEPS) * SYNC_MHZ / SCK_MHZ / 2) + 100


def seed_part(dev, base, n, seed):
    rng = random.Random(seed)
    for a in range(base, base + n):
        dev.mem[a] = rng.getrandbits(8)


async def wait_boot(b):
    for _ in range(BOOT_POLLS):
        st = await b.status()
        if st["ready"] and not st["busy"]:
            return st
    return None


async def jedec(b):
    st = await b.run(JEDEC, length=3)
    word = (await b.buf_words(1))[0]
    return st, ((word & 0xFF) << 16) | (word & 0xFF00) | ((word >> 16) & 0xFF)


async def s_main(checks, b, dev, ctx, p, stats):
    st = await wait_boot(b)
    if not checks.check("boot: calibration finishes, ready + locked", st is not None
                        and st["ready"] and st["locked"],
                        f"{await b.status()} steps {stats['steps']} windows {dev.windows} "
                        f"cmds {[hex(c) for c in dev.cmds[-4:]]}"):
        return
    cal = await b.read("cal")
    w1, w4 = (cal >> 16) & 0xFF, (cal >> 24) & 0xFF
    checks.check("boot: x1 and x4 eyes found, x4 applied", st["cal"] and st["x4"]
                 and w1 >= fp.CAL_MIN_WIDTH and w4 >= fp.CAL_MIN_WIDTH,
                 measurement=f"step {cal & 0xFF} lat {(cal >> 8) & 15} xfer {(cal >> 12) & 1} "
                             f"width x1 {w1} x4 {w4} of {N_STEPS} per lat, "
                             f"{stats['steps']} PHASESTEPs seen")
    checks.check("boot: the PHASESTEP count the part saw ends at the placed step",
                 stats["steps"] % N_STEPS == cal & 0xFF, f"{stats['steps']} vs {cal & 0xFF}")
    checks.check("boot: steps register = the PLL's 8 x div", await b.read("steps") == N_STEPS)
    checks.check("boot: no sticky error", await b.read("errors") == 0)

    st, ident = await jedec(b)
    checks.check("JEDEC over the command port = EF4016", st["code"] == 0 and ident == 0xEF4016,
                 f"{st} {ident:#08x}")
    st = await b.run(SR1, length=1)
    sr1 = (await b.buf_words(1))[0] & 0xFF
    checks.check("SR1 = 00 (idle, no WEL)", st["code"] == 0 and sr1 == 0, f"{st} {sr1:#x}")

    # XIP: byte order, one fill per line, hits
    fills0 = await b.read("xip_fills")
    words = []
    for i in range(16):
        words.append(await b.xip(REF + 4 * i))
    fills1 = await b.read("xip_fills")
    checks.check("XIP: 16 words of one line, little-endian, == the part",
                 all(w == ("ack", le_word(dev, REF + 4 * i)) for i, w in enumerate(words)),
                 str(words[:2]))
    checks.check("XIP: one fill for the whole line", fills1 - fills0 == 1, f"{fills1 - fills0}")
    windows = dev.windows
    again = await b.xip(REF + 8)
    checks.check("XIP: a hit costs no transaction", again[0] == "ack" and dev.windows == windows
                 and await b.read("xip_fills") == fills1)
    picks = [REF + 4 * random.Random(1).randrange(0, 1024) for _ in range(12)]
    got = [await b.xip(a) for a in picks]
    checks.check("XIP: 12 scattered words across lines", all(
        g == ("ack", le_word(dev, a)) for g, a in zip(got, picks)), str(got[:2]))
    checks.check("bench control: a wrong expectation is caught",
                 got[0] != ("ack", le_word(dev, picks[0]) ^ 1))
    a = REF + 0x200
    first = await b.xip(a)
    dev.mem[a] ^= 0xFF
    held = await b.xip(a)
    await jedec(b)
    fresh = await b.xip(a)
    checks.check("XIP line buffer: a hit serves the buffered line; a command's done "
                 "invalidates it and the next load sees the part",
                 held == first and fresh == ("ack", le_word(dev, a)) and fresh != first,
                 f"{first} {held} {fresh}")

    # erase + program through the command port
    cmds = len(dev.cmds)
    st = await b.run(ERASE, addr=REGION, wren=1, wait=1, wait_us=100)
    us = await b.read("poll_us")
    model_us = BUSY_MODEL[0x20] / SCK_MHZ
    checks.check("erase: WREN + 20h + one BUSY poll, each its own /CS window",
                 dev.cmds[cmds:] == [0x06, 0x20, 0x05], str([hex(c) for c in dev.cmds[cmds:]]))
    checks.check("erase: OK, BUSY seen, poll time = the model's", st["code"] == 0
                 and st["saw_busy"] and abs(us - model_us) <= 2,
                 f"{st} {us} us", measurement=f"poll {us} us vs model {model_us:.1f} us")
    blank = [await b.xip(REGION + 4 * i) for i in range(4)]
    checks.check("erase: the line refetches after the command and reads blank",
                 all(w == ("ack", 0xFFFFFFFF) for w in blank), str(blank))
    data = [random.Random(2).getrandbits(32) for _ in range(64)]
    await b.buf_words(64, write=data)
    cmds = len(dev.cmds)
    st = await b.run(PROGRAM, addr=REGION, length=256, wren=1, wait=1, wait_us=100)
    checks.check("program 256 B: WREN, 02h, poll; data in one /CS window",
                 st["code"] == 0 and dev.cmds[cmds:] == [0x06, 0x02, 0x05], str(st))
    checks.check("program: the part holds the buffer, little-endian",
                 all(le_word(dev, REGION + 4 * i) == w for i, w in enumerate(data)))
    back = [await b.xip(REGION + 4 * i) for i in range(64)]
    checks.check("program: XIP reads it back", all(g == ("ack", w) for g, w in zip(back, data)))
    st = await b.run(READ0B, addr=REGION + 16, length=8)
    rb = await b.buf_words(2)
    checks.check("program: 0Bh over the command port reads it back", st["code"] == 0
                 and rb == data[4:6], f"{[hex(x) for x in rb]}")

    # refusals and errors
    await b.write("errors", 0xFF)
    r = await b.xip(REF, we=True)
    checks.check("XIP store: ERR + sticky xip_write", r[0] == "err"
                 and (await b.read("errors")) >> fp.ERR_XIP_WRITE & 1)
    st = await b.run(JEDEC, length=fp.BUF_BYTES + 1)
    checks.check("len > 256: ARG", st["code"] == fp.CODE_ARG, str(st))
    st = await b.run(ERASE, addr=REGION, wren=1, wait=1, wait_us=0)
    checks.check("wait with wait_us 0: ARG", st["code"] == fp.CODE_ARG, str(st))
    await b.write("irq_en", 3)
    await b.write("events", 3)
    for name, value in (("desc", ERASE), ("addr", REGION), ("len", 0), ("flags", 3),
                        ("wait_us", 3)):
        await b.write(name, value)
    await b.write("errors", 0xFF)
    await b.write("cmd", fp.OP_RUN)
    r = await b.xip(REF + 0x40)
    checks.check("XIP during a write-class command: ERR at once + sticky xip_blocked",
                 r[0] == "err" and (await b.read("errors")) >> fp.ERR_XIP_BLOCKED & 1, str(r))
    await b.write("cmd", fp.OP_RUN)
    st = await b.status()
    checks.check("command while busy: BUSY, the one in flight still busy",
                 st["code"] == fp.CODE_BUSY and st["busy"], str(st))
    st = await b.wait_idle(4000)
    err = await b.read("errors")
    checks.check("poll past wait_us: WDOG + sticky wdog + IRQ", st["code"] == fp.CODE_WDOG
                 and err >> fp.ERR_WDOG & 1 and ctx.get(p.irq), f"{st} {err:#x}")
    await b.write("events", 3)
    checks.check("events W1C clears the IRQ", not ctx.get(p.irq))
    await b.tick(int(BUSY_MODEL[0x20] / SCK_MHZ * SYNC_MHZ) + 50)
    for name, value in (("flags", 3), ("wait_us", 10_000)):
        await b.write(name, value)
    await b.write("cmd", fp.OP_RUN)
    await b.tick(200)
    await b.write("ctrl", 1)
    st = await b.wait_idle(200)
    checks.check("abort ends a poll: ABORT", st is not None and st["code"] == fp.CODE_ABORT,
                 str(st))
    await b.tick(int(BUSY_MODEL[0x20] / SCK_MHZ * SYNC_MHZ) + 50)
    st, ident = await jedec(b)
    checks.check("after watchdog and abort: the next command works", st["code"] == 0
                 and ident == 0xEF4016, f"{st} {ident:#x}")

    # recalibration from the CPU
    st = await b.run(0, op=fp.OP_CALIBRATE, bound=BOOT_POLLS)
    cal2 = await b.read("cal")
    checks.check("CALIBRATE from the CPU: OK, x4", st is not None and st["code"] == 0
                 and st["cal"] and st["x4"], f"{st}",
                 measurement=f"step {cal2 & 0xFF} lat {(cal2 >> 8) & 15} vs boot "
                             f"{cal & 0xFF}/{(cal >> 8) & 15}")
    r = await b.xip(REF + 0x104)
    checks.check("after CALIBRATE: XIP still right", r == ("ack", le_word(dev, REF + 0x104)),
                 str(r))
    # finished: JEDEC, SR1, JEDEC, erase, program, 0Bh, WDOG erase, ABORT erase, JEDEC,
    # CALIBRATE; the three refusals do not count
    seq = await b.read("seq")
    checks.check("seq counts finished commands, not refusals", seq == 10, f"{seq}")


async def s_interleave_cmds(checks, b, dev, ctx, p, stats):
    st = await wait_boot(b)
    stats["booted"] = st is not None
    ok = 0
    for _ in range(12):
        st, ident = await jedec(b)
        ok += st is not None and st["code"] == 0 and ident == 0xEF4016
    checks.check("interleave: 12 JEDEC reads while XIP misses run, all right", ok == 12, f"{ok}")
    stats["cmds_done"] = True


async def s_interleave_xip(checks, b, dev, ctx, p, stats):
    for _ in range(BOOT_POLLS * 6):
        if stats.get("booted"):
            break
        await b.tick()
    good = n = 0
    rng = random.Random(3)
    while not stats.get("cmds_done") and n < 400:
        a = REF + 64 * rng.randrange(0, 64) + 4 * rng.randrange(0, 16)
        r = await b.xip(a)
        good += r == ("ack", le_word(dev, a))
        n += 1
    checks.check("interleave: XIP line misses between commands all right, none starved",
                 n > 0 and good == n, f"{good}/{n}")


async def s_silent(checks, b, dev, ctx, p, stats):
    st = await wait_boot(b)
    err = await b.read("errors")
    checks.check("negative control, silent part: no eye, cal invalid, sticky cal",
                 st is not None and not st["cal"] and err >> fp.ERR_CAL & 1, f"{st} {err:#x}")
    r = await b.xip(REF)
    checks.check("uncalibrated: XIP answers ERR, never waits", r[0] == "err", str(r))
    st, _ = await jedec(b)
    checks.check("uncalibrated: commands still run (bring-up path)", st["code"] == 0, str(st))
    dev.mute = False
    st = await b.run(0, op=fp.OP_CALIBRATE, bound=BOOT_POLLS)
    checks.check("CALIBRATE once the part answers: recovers", st is not None
                 and st["code"] == 0 and st["cal"], str(st))


async def s_blank_ref(checks, b, dev, ctx, p, stats):
    st = await wait_boot(b)
    cal = await b.read("cal")
    checks.check("negative control, blank reference: x1 placement kept, x4 off",
                 st is not None and st["cal"] and not st["x4"] and (cal >> 24) == 0, f"{st}")


def main():
    emit("flash peripheral -- #590 core, XIP + command port, calibration, errors (#593)")
    checks = Checks(emit)
    dev = Part()
    seed_part(dev, REF, 4096, 590)
    _, stats = run(checks, "main", s_main, dev=dev)
    checks.check("no bus contention", stats["contention"] == 0, f"{stats['contention']}")
    checks.check("every transaction has a stopped-clock /CS setup cycle",
                 stats["no_setup"] == 0, f"{stats['no_setup']}")
    checks.check("/CS high >= 2 CLK between transactions",
                 (stats["cs_high_min"] or 0) >= 2, f"{stats['cs_high_min']}")
    dev = Part()
    seed_part(dev, REF, 4096, 591)
    run(checks, "interleave", s_interleave_cmds, dev=dev, background=s_interleave_xip)
    dev = Part()
    dev.mute = True
    seed_part(dev, REF, 4096, 592)
    run(checks, "silent part", s_silent, dev=dev)
    run(checks, "blank reference", s_blank_ref, dev=Part())
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
