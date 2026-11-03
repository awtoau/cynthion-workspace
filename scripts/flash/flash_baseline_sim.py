#!/usr/bin/env python3
#
# Simulate the config-flash baseline against a W25Q32JV model written from the datasheet.
# SPDX-License-Identifier: BSD-3-Clause

"""Reference test: `gateware/flash_baseline` (tx engine, op engine, PHY) against a model.

- Model: W25Q32JV-IQ, datasheet rev G only: instruction tables 1/2, Figs 16-24 bit order,
  data out after CLK falling (tCLQV), QE=1 fixed, JEDEC EF 4016, WEL/BUSY, page wrap,
  program clears bits only, erase sets 0xFF, commands executed on /CS rising, QPI (38h)
  ignored (IQ part). BUSY lasts a scaled cycle count.
- Clocks: engine `sync` at phase 0, flash CLK at +180 degrees, `cap` at a swept phase.
- Checks: ID/SR reads, erase + blank check, page program, every read mode, short bursts,
  measured BUSY vs the model, no bus contention, /CS high time, sector bitmap.
- Negative controls must fail: stale seed, xor, one corrupted model byte (exact index).
- Says nothing about pad timing, PLL phase steps or the part's AC limits.

    ./scripts/flash/flash_baseline_sim.py     # log: tmp/logs/flash_baseline_sim.log
"""

import logging
import os
import subprocess
import sys
from pathlib import Path


def repo_root():
    env = os.environ.get("CYNTHION_WORKSPACE_REPO")
    if env:
        return Path(env)
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                   text=True, check=True, cwd=Path(__file__).parent).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent.parent


ROOT = repo_root()
LOG = ROOT / "tmp" / "logs" / "flash_baseline_sim.log"
sys.path.insert(0, str(ROOT / "gateware" / "flash_baseline"))

from amaranth import ClockDomain, ClockSignal, Module, Mux, Signal  # noqa: E402
from amaranth.hdl import Fragment                                    # noqa: E402
from amaranth.sim import Simulator                                   # noqa: E402

from spiflash import (CHECK_BLANK, CHECK_PATTERN, DTR, OP_RAW, OP_READ,  # noqa: E402
                      OP_WLOOP, READ_MODES, W1, W4, FlashPHY, FlashPins, OpEngine, TxEngine,
                      desc, pattern_py)

log = logging.getLogger("flash_baseline_sim")

T_NS = 20.0                    # 50 MHz SCK in the sim
TCLQV_NS = 5.0                 # model output delay after CLK falling (datasheet max 6)
BUSY_MODEL = {0x02: 300, 0x32: 300, 0x20: 1500, 0x52: 2500, 0xD8: 3000}   # CLK cycles
BASE = 0x300000                # sim test region
CSH_R, CSH_W = 1, 3            # 10 / 50 ns at 50 MHz: ceil(t*F)


class W25Q32JV:
    """Bit-level model, one generator per /CS-low transaction (see `edge`)."""

    def __init__(self):
        self.mem = {}
        self.wel = False
        self.busy_until = 0
        self.clk = 0
        self.gen = None
        self.pending = None
        self.bits_in = 0
        self.contention = 0
        self.contended = set()
        self.cmds = []

    def read(self, a):
        return self.mem.get(a & 0x3FFFFF, 0xFF)

    def busy(self):
        return self.clk < self.busy_until

    def sr1(self):
        return int(self.busy()) | (int(self.wel) << 1)

    def take(self, n, w):
        v = 0
        for _ in range(n // w):
            io = yield None
            self.bits_in += w
            v = (v << w) | (io & 1 if w == 1 else io & ((1 << w) - 1))
        return v

    def out(self, byte_fn, w):
        """Drive bytes MSB first at width w, forever (until /CS rises)."""
        while True:
            b = byte_fn()
            for k in range(8 // w):
                bits = (b >> (8 - w * (k + 1))) & ((1 << w) - 1)
                if w == 1:
                    yield (bits << 1, 0b0010)
                elif w == 2:
                    yield (bits, 0b0011)
                else:
                    yield (bits, 0b1111)

    def transaction(self):
        self.bits_in = 0
        cmd = yield from self.take(8, 1)
        self.cmds.append(cmd)
        if self.busy() and cmd != 0x05:
            while True:
                yield None
        reader = None
        if cmd == 0x9F:
            ids = iter([0xEF, 0x40, 0x16] * 1000)
            yield from self.out(lambda: next(ids), 1)
        elif cmd == 0x05:
            yield from self.out(self.sr1, 1)
        elif cmd == 0x35:
            yield from self.out(lambda: 0x02, 1)
        elif cmd == 0x15:
            yield from self.out(lambda: 0x60, 1)
        elif cmd in (0x0B, 0x3B, 0x6B):
            a = yield from self.take(24, 1)
            for _ in range(8):
                yield None
            reader, w = a, {0x0B: 1, 0x3B: 2, 0x6B: 4}[cmd]
        elif cmd == 0xBB:
            a = yield from self.take(24, 2)
            yield from self.take(8, 2)
            reader, w = a, 2
        elif cmd == 0xEB:
            a = yield from self.take(24, 4)
            yield from self.take(8, 4)
            for _ in range(4):
                yield None
            reader, w = a, 4
        elif cmd == 0xED:
            # DTR (W25Q32JV-DTR rev H Fig 27a): address and M 2 nibbles per CLK, 7 dummies,
            # then a byte per CLK, high nibble after CLK falling, low after the next rising.
            # Inputs are sampled on rising edges only: the engine's SDR address phase holds
            # each nibble for a whole CLK, so this model needs a pair-equal address (0).
            a = 0
            for _ in range(3):
                n = yield None
                a = (a << 8) | ((n & 0xF) << 4) | (n & 0xF)
            yield None
            for _ in range(7):
                yield None
            addr = [a]
            while True:
                b = self.read(addr[0])
                addr[0] += 1
                yield ("ddr", (b >> 4, 0b1111), (b & 0xF, 0b1111))
        elif cmd in (0x02, 0x32):
            a = yield from self.take(24, 1)
            data = []
            self.pending = ("program", cmd, a, data, self.bits_in)
            w = 1 if cmd == 0x02 else 4
            while True:
                data.append((yield from self.take(8, w)))
        elif cmd in (0x20, 0x52, 0xD8):
            a = yield from self.take(24, 1)
            self.pending = ("erase", cmd, a, None, self.bits_in)
        elif cmd in (0x06, 0x04):
            self.pending = ("wel", cmd, 0, None, self.bits_in)
        if reader is not None:
            addr = [reader]

            def nxt():
                b = self.read(addr[0])
                addr[0] += 1
                return b
            yield from self.out(nxt, w)
        while True:
            yield None

    def cs_rise(self):
        """Commands execute on /CS high only if the bit count is exact (datasheet 8.2)."""
        p, self.pending, self.gen = self.pending, None, None
        if p is None:
            return
        kind, cmd, a, data, bits_at = p
        if kind == "wel":
            if self.bits_in == 8:
                self.wel = cmd == 0x06
        elif kind == "erase":
            if self.bits_in == 32 and self.wel:
                size = {0x20: 4096, 0x52: 32768, 0xD8: 65536}[cmd]
                start = a & ~(size - 1)
                for x in range(start, start + size):
                    self.mem.pop(x, None)
                self.wel = False
                self.busy_until = self.clk + BUSY_MODEL[cmd]
        elif kind == "program":
            if self.wel and (self.bits_in - bits_at) % 8 == 0 and data:
                page = a & ~0xFF
                for i, b in enumerate(data[-256:]):
                    x = page | ((a + i) & 0xFF)
                    self.mem[x] = self.read(x) & b
                self.wel = False
                self.busy_until = self.clk + BUSY_MODEL[cmd]


def build(phase_ns):
    pins = FlashPins()
    tx = TxEngine(pins)
    eng = OpEngine(tx)
    pad_o, pad_oe, pad_i, pad_cs = Signal(4), Signal(4), Signal(4), Signal()
    phy = FlashPHY(pins, pad_o=pad_o, pad_oe=pad_oe, pad_i=pad_i, pad_cs=pad_cs)
    m = Module()
    m.submodules += [tx, eng, phy]
    m.domains.cap = ClockDomain("cap", reset_less=True)
    m.domains.capn = ClockDomain("capn", clk_edge="neg", reset_less=True)
    fclk = ClockDomain("fclk")
    m.domains += fclk
    m.d.comb += ClockSignal("capn").eq(ClockSignal("cap"))
    f_val, f_oe = Signal(4), Signal(4)
    for n in range(4):
        m.d.comb += pad_i[n].eq(Mux(f_oe[n], f_val[n], pad_oe[n] & pad_o[n]))
    sim = Simulator(Fragment.get(m, None))
    # Phases relative to `sync`'s first rising edge at T/2 (the Simulator default).
    sim.add_clock(T_NS * 1e-9, phase=T_NS / 2 * 1e-9, domain="sync")
    sim.add_clock(T_NS * 1e-9, phase=(T_NS / 2 + phase_ns) * 1e-9, domain="cap")
    sim.add_clock(T_NS * 1e-9, phase=T_NS * 1e-9, domain="fclk")
    return sim, dict(tx=tx, eng=eng, phy=phy, pad_o=pad_o, pad_oe=pad_oe, pad_cs=pad_cs,
                     f_val=f_val, f_oe=f_oe, fclk=fclk)


def run(phase_ns, xfer, script):
    """Run `script(op, bitmap, dev)` as the control testbench; returns the model."""
    sim, h = build(phase_ns)
    dev = W25Q32JV()
    cs_high = {"run": 0, "min": None}

    def apply(ctx, drive):
        if drive is None:
            ctx.set(h["f_oe"], 0)
            return
        ctx.set(h["f_val"], drive[0])
        ctx.set(h["f_oe"], drive[1])
        if drive[1] & ctx.get(h["pad_oe"]):
            dev.contention += 1
            dev.contended.add(dev.cmds[-1])

    async def flash(ctx):
        clk = h["fclk"].clk
        rise = None                       # DDR low nibble due after the next rising edge
        while True:
            await ctx.posedge(clk)
            if rise is not None and ctx.get(h["pad_cs"]):
                await ctx.delay(TCLQV_NS * 1e-9)
                apply(ctx, rise)
            rise = None
            dev.clk += 1
            cs = ctx.get(h["pad_cs"])
            if not cs:
                if dev.gen is not None:
                    dev.cs_rise()
                cs_high["run"] += 1
                ctx.set(h["f_oe"], 0)
                drive = None
            else:
                if cs_high["run"] and dev.clk > 50:
                    cs_high["min"] = min(cs_high["min"] or 999, cs_high["run"])
                cs_high["run"] = 0
                if dev.gen is None:
                    dev.gen = dev.transaction()
                    next(dev.gen)
                oe = ctx.get(h["pad_oe"])
                io = ctx.get(h["pad_o"]) & oe
                drive = dev.gen.send(io)
                if drive is not None and drive[0] == "ddr":
                    drive, rise = drive[1], drive[2]
            await ctx.negedge(clk)
            await ctx.delay(TCLQV_NS * 1e-9)
            apply(ctx, drive)

    results = []

    async def control(ctx):
        eng, tx, phy = h["eng"], h["tx"], h["phy"]
        ctx.set(phy.xfer, xfer)

        async def op(kind, *, d=0, addr=0, length=0, burst=0, count=0, stride=0, check=0,
                     seed=0, xor=0, poll_after=0, lat=None, half=None, wdata=0, csh_r=CSH_R,
                     window=(0, 0), label=""):
            for sig, v in ((eng.op, kind), (eng.desc, d), (eng.addr, addr), (eng.length, length),
                           (eng.burst, burst), (eng.count, count), (eng.stride, stride),
                           (eng.check, check), (eng.seed, seed), (eng.xor, xor),
                           (eng.poll_after, poll_after), (eng.wdata, wdata),
                           (eng.csh_r, csh_r), (eng.csh_w, CSH_W),
                           (eng.chk_lo, window[0]), (eng.chk_hi, window[1])):
                ctx.set(sig, v)
            if lat is not None:
                ctx.set(tx.lat, lat)
            if half is not None:
                ctx.set(tx.half, half)
            ctx.set(eng.go, 1)
            await ctx.tick()
            ctx.set(eng.go, 0)
            # Waits for: the op. Expected: data bits (x1 worst case) + 64 per tx + modelled
            # BUSY per unit; x1.25. Expiry: the row is recorded not done.
            units = max(count, 1)
            data = length * 8 + (units * burst * 8 if kind == OP_WLOOP else 0)
            bound = int(1.25 * (data + 64 * (3 * units + length // max(burst, 1) + 2)
                                + units * BUSY_MODEL.get(d & 0xFF, 0))) + 200
            for _ in range(bound):
                await ctx.tick()
                if ctx.get(eng.done):
                    break
            row = dict(label=label, done=bool(ctx.get(eng.done)), length=length)
            for k in ("run_cycles", "bytes", "txs", "checked", "errors", "bad_index",
                      "bad_got_want", "bad_bits", "result", "busy_min", "busy_max", "busy_n"):
                row[k] = ctx.get(getattr(eng, k))
            results.append(row)
            log.info("    %-28s %s", label, {k: row[k] for k in (
                "done", "run_cycles", "bytes", "txs", "checked", "errors", "bad_index",
                "result", "busy_min", "busy_max", "busy_n")})
            return row

        async def bitmap(word):
            ctx.set(eng.bitmap_rd.addr, word)
            await ctx.tick()
            return ctx.get(eng.bitmap_rd.data)

        await ctx.tick().repeat(4)
        await script(op, bitmap, dev)

    sim.add_testbench(flash, background=True)
    sim.add_testbench(control)
    sim.run()
    return dev, results, cs_high


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(LOG, mode="w")])
    failures = []

    def check(ok, what):
        log.info("  %s  %s", "PASS" if ok else "FAIL", what)
        if not ok:
            failures.append(what)

    ebh = READ_MODES["EBh 1-4-4"]
    seed_a, seed_b, seed_c = 0x1A2B3C4D, 0xE5F60718, 0x96A5B4C3
    probe_len = 64

    # ---- 1. capture calibration: every phase must have a clean lat --------------------
    log.info("calibration: phase x lat on a seeded EBh read (%d bytes), SCK %g MHz",
             probe_len, 1000 / T_NS)
    chosen = {}
    for frac in (0.1, 0.4, 0.6, 0.9):
        ph = frac * T_NS
        xfer = int(frac < 0.25 or frac > 0.75)
        clean = []

        async def cal(op, bitmap, dev):
            # x1 capture from the JEDEC ID first: the BUSY polls below depend on it.
            for lat in range(0, 9):
                r = await op(OP_RAW, d=desc(0x9F, dw=W1, read=True), length=3, lat=lat,
                             label=f"JEDEC lat {lat}")
                if r["result"] & 0xFFFFFF == 0xEF4016:
                    break
            await op(OP_WLOOP, d=desc(0x20, abits=24), addr=BASE, count=1, label="erase")
            await op(OP_WLOOP, d=desc(0x02, abits=24, dw=W1), addr=BASE, burst=256, count=1,
                     stride=256, seed=seed_a, label="program")
            for lat in range(0, 9):
                r = await op(OP_READ, d=ebh, addr=BASE, length=probe_len, burst=probe_len,
                             check=CHECK_PATTERN, seed=seed_a, lat=lat, label=f"lat {lat}")
                if r["done"] and r["errors"] == 0 and r["checked"] == probe_len:
                    clean.append(lat)
        run(ph, xfer, cal)
        log.info("  phase %.1f ns (xfer %d): clean lat %s", ph, xfer, clean)
        check(len(clean) >= 1, f"phase {frac:.1f}T has a clean lat ({clean})")
        if clean:
            chosen[frac] = (ph, xfer, clean[0])

    # ---- 2. full case list at two phases ----------------------------------------------
    for frac in (0.4, 0.9):
        if frac not in chosen:
            continue
        ph, xfer, lat = chosen[frac]
        log.info("phase %.1f ns, xfer %d, lat %d: full case list", ph, xfer, lat)

        async def cases(op, bitmap, dev):
            r = await op(OP_RAW, d=desc(0x9F, dw=W1, read=True), length=3, lat=lat,
                         label="JEDEC ID")
            check(r["result"] & 0xFFFFFF == 0xEF4016, f"JEDEC ID {r['result']:#08x}")
            r = await op(OP_RAW, d=desc(0x35, dw=W1, read=True), length=1, label="SR2")
            check(r["result"] & 0xFF == 0x02, f"SR2 QE=1 ({r['result'] & 0xFF:#04x})")
            r = await op(OP_WLOOP, d=desc(0x20, abits=24), addr=BASE, count=2, stride=4096,
                         label="erase 2 sectors")
            check(r["busy_n"] == 2 and abs(r["busy_max"] - BUSY_MODEL[0x20]) < 60,
                  f"sector erase BUSY measured {r['busy_min']}..{r['busy_max']} "
                  f"vs model {BUSY_MODEL[0x20]}")
            r = await op(OP_READ, d=ebh, addr=BASE, length=8192, burst=8192, check=CHECK_BLANK,
                         label="blank check")
            check(r["errors"] == 0 and r["checked"] == 8192, "blank after erase")
            r = await op(OP_WLOOP, d=desc(0x02, abits=24, dw=W1), addr=BASE, burst=256,
                         count=2, stride=256, seed=seed_a, label="program 02h x2")
            check(r["busy_n"] == 2 and abs(r["busy_max"] - BUSY_MODEL[0x02]) < 60,
                  f"page program BUSY {r['busy_min']}..{r['busy_max']} vs {BUSY_MODEL[0x02]}")
            r = await op(OP_WLOOP, d=desc(0x32, abits=24, dw=W4), addr=BASE + 4096, burst=256,
                         count=1, stride=256, seed=seed_b, label="program 32h quad")
            check(r["busy_n"] == 1, "quad page program polled")
            for name, d in READ_MODES.items():
                r = await op(OP_READ, d=d, addr=BASE, length=512, burst=512,
                             check=CHECK_PATTERN, seed=seed_a, label=f"read {name}")
                check(r["done"] and r["errors"] == 0 and r["checked"] == 512,
                      f"{name}: 512 bytes, errors {r['errors']}")
                data_cyc = 512 * 8 // int(name[-1])
                check(data_cyc <= r["run_cycles"] <= data_cyc + 80,
                      f"{name}: run cycles {r['run_cycles']} = data {data_cyc} + overhead")
                r = await op(OP_READ, d=d, addr=BASE, length=512, burst=32, check=CHECK_PATTERN,
                             seed=seed_a, label=f"read {name} burst 32")
                check(r["errors"] == 0 and r["txs"] == 16, f"{name}: burst 32 clean, 16 txs")
            r = await op(OP_READ, d=ebh, addr=BASE + 4096, length=256, burst=256,
                         check=CHECK_PATTERN, seed=seed_b, label="read quad-programmed")
            check(r["errors"] == 0, "32h quad program reads back")
            # negative controls
            r = await op(OP_READ, d=ebh, addr=BASE, length=512, burst=512, check=CHECK_PATTERN,
                         seed=seed_c, label="neg: stale seed")
            check(r["errors"] == 512, f"stale seed fails every byte ({r['errors']})")
            r = await op(OP_READ, d=ebh, addr=BASE, length=512, burst=512, check=CHECK_PATTERN,
                         seed=seed_a, xor=1, label="neg: xor")
            check(r["errors"] == 512, f"xor fails every byte ({r['errors']})")
            dev.mem[BASE + 77] ^= 0x10
            r = await op(OP_READ, d=ebh, addr=BASE, length=512, burst=512, check=CHECK_PATTERN,
                         seed=seed_a, label="neg: corrupted byte")
            check(r["errors"] == 1 and r["bad_index"] == BASE + 77
                  and r["bad_got_want"] >> 8 == (r["bad_got_want"] & 0xFF) ^ 0x10,
                  f"corrupted model byte: errors {r['errors']} at {r['bad_index']:#x}")
            # scan: bitmap marks programmed sectors only
            r = await op(OP_READ, d=ebh, addr=BASE, length=3 * 4096, burst=3 * 4096,
                         check=CHECK_BLANK, label="scan 3 sectors")
            word = await bitmap((BASE >> 12) >> 5)
            sh = (BASE >> 12) & 31
            check((word >> sh) & 0b111 == 0b011, f"bitmap {word:#010x}: sectors 0,1 used, 2 blank")
            # QPI probe: 38h is ignored by the IQ part
            await op(OP_RAW, d=desc(0x38), label="QPI enter")
            r = await op(OP_RAW, d=desc(0x9F, cmd_w4=True, dw=W4, read=True), length=3,
                         label="QPI JEDEC ID")
            check(r["result"] & 0xFFFFFF != 0xEF4016, "QPI 9Fh does not answer (no QPI)")
            r = await op(OP_RAW, d=desc(0x9F, dw=W1, read=True), length=3, label="JEDEC ID after")
            check(r["result"] & 0xFFFFFF == 0xEF4016, "SPI still answers after the probe")
            # DTR EDh from address 0 (the only pair-equal address the SDR PHY sends), checked
            # in a window: page 0 programmed, the next page blank.
            await op(OP_WLOOP, d=desc(0x20, abits=24), addr=0, count=1, label="erase sector 0")
            await op(OP_WLOOP, d=desc(0x02, abits=24, dw=W1), addr=0, burst=256, count=1,
                     stride=256, seed=seed_a, label="program page 0")
            clean = []
            for dl in range(0, 8):
                for hf in (0, 1):
                    r = await op(OP_READ, d=DTR, length=512, burst=512, check=CHECK_PATTERN,
                                 seed=seed_a, window=(0, 256), lat=dl, half=hf,
                                 label=f"DTR lat {dl} half {hf}")
                    if r["errors"] == 0 and r["checked"] == 256:
                        clean.append((dl, hf))
            check(len(clean) >= 1, f"DTR has a clean (lat, half): {clean}")
            if clean:
                dl, hf = clean[0]
                r = await op(OP_READ, d=DTR, length=512, burst=512, check=CHECK_PATTERN,
                             seed=seed_a, window=(0, 256), lat=dl, half=hf, label="DTR read")
                check(r["bytes"] == 512 and 512 <= r["run_cycles"] <= 512 + 60,
                      f"DTR: a byte per CLK ({r['run_cycles']} cycles for 512)")
                r = await op(OP_READ, d=DTR, length=512, burst=512, check=CHECK_PATTERN,
                             seed=seed_a, window=(0, 512), label="neg: DTR window over blank")
                check(r["errors"] >= 250, f"window covering blank bytes fails ({r['errors']})")
                r = await op(OP_READ, d=DTR, length=512, burst=512, check=CHECK_PATTERN,
                             seed=seed_c, window=(0, 256), label="neg: DTR stale seed")
                check(r["errors"] == 256, f"DTR stale seed fails every byte ({r['errors']})")
                await op(OP_READ, d=ebh, addr=BASE, length=16, burst=16, lat=lat, half=0,
                         label="restore lat")

        dev, results, cs_high = run(ph, xfer, cases)
        check(all(r["done"] for r in results), "every op finished in bound")
        check(dev.contention == 0, f"no bus contention ({dev.contention}, commands "
              f"{sorted(hex(c) for c in dev.contended)})")
        check(cs_high["min"] is not None and cs_high["min"] >= CSH_R + 2,
              f"/CS high >= {CSH_R + 2} CLK between transactions (min {cs_high['min']})")
        wrong = [a for a in range(BASE, BASE + 512) if a != BASE + 77
                 and dev.read(a) != pattern_py(a, seed_a)]
        check(not wrong, "model memory == pattern(seed) after program")

    log.info("%s: %d failure(s); log %s", "FAIL" if failures else "PASS", len(failures), LOG)
    for f in failures:
        log.info("  failed: %s", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
