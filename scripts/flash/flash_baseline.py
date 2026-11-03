#!/usr/bin/env python3
#
# Build, load and measure the config-flash baseline (reference test gateware).
# SPDX-License-Identifier: BSD-3-Clause

"""Config flash (W25Q32JV) read/program/erase baseline on Cynthion r1.4, no CPU.

    ./scripts/flash/flash_baseline.py sweep              # every SCK, one table (normal use)
    ./scripts/flash/flash_baseline.py scan --sck 20      # read-only: ID, SRs, sector map
    ./scripts/flash/flash_baseline.py build --sck 50 100
    ./scripts/flash/flash_baseline.py table

- Gateware: `gateware/flash_baseline/` (datasheet + Lattice docs only; see
  docs/chips/w25q32-flash-baseline.md). One SCK per bitstream.
- Load: `apollo configure` via scripts/soc_confirm.py (volatile; soc_run.py restores the SoC).
- Erase/program touch only [TEST_BASE, TEST_BASE + TEST_SIZE); reads cover the whole part.
- Log: tmp/logs/flash_baseline.log; results: tmp/flash-baseline/results.jsonl.
"""

import argparse
import contextlib
import io
import json
import math
import os
import selectors
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
GW = ROOT / "gateware" / "flash_baseline"
BOARD = ROOT / "gateware" / "hyperram_baseline"
OUT = ROOT / "tmp" / "flash-baseline"
LOG = ROOT / "tmp" / "logs" / "flash_baseline.log"
RESULTS = OUT / "results.jsonl"
OSS_CAD_ENV = Path.home() / "opt" / "oss-cad-suite" / "environment"
sys.path.insert(0, str(GW))

from spiflash import (BITMAP_WORDS, CHECK_BLANK, CHECK_PATTERN, OP_RAW, OP_READ,  # noqa: E402
                      OP_WLOOP, READ_MODES, W1, W4, desc)
from top import APPLET_ID, CK_WINDOW_BITS, REG, reachable  # noqa: E402

PART_BYTES = 4 << 20
# Test region: 1 MiB of erased blocks between 0x263000 and 0x3FF000, the nearest used
# sectors in the map `scan` found (docs/chips/w25q32-flash-baseline.md).
TEST_BASE, TEST_SIZE = 0x280000, 0x100000
# Rated 133 MHz (datasheet 9.6 FR); reachable rungs from 60 MHz, then past the rating.
SWEEP_SCK = [20, 50, 80, 100, 120, 132, 140, 150, 160, 180, 200, 240]
# Datasheet 9.6 maxima (s): what each WLOOP opcode may take per unit.
T_MAX = {0x02: 3e-3, 0x32: 3e-3, 0x20: 0.4, 0x52: 1.6, 0xD8: 2.0}
WIDTH = {"0Bh 1-1-1": 1, "3Bh 1-1-2": 2, "BBh 1-2-2": 2, "6Bh 1-1-4": 4, "EBh 1-4-4": 4}
EYE_MODE = "EBh 1-4-4"
EYE_BYTES = 4096
LAT_RANGE = range(0, 12)
JTAG_S = 1.0                 # debugger round trips on top of any op deadline (hyperram harness)
ALL_RESULT_REGS = ("RUN_CYCLES", "BYTES", "TXS", "CHECKED", "ERRORS", "BAD_INDEX",
                   "BAD_GOT_WANT", "BAD_BITS", "RESULT", "BUSY_MIN", "BUSY_MAX", "BUSY_SUM",
                   "BUSY_N", "STATE")
RESULT_REGS = {OP_RAW: ("RESULT", "RUN_CYCLES", "BUSY_MAX", "BUSY_N", "STATE"),
               OP_READ: ("RUN_CYCLES", "BYTES", "TXS", "CHECKED", "ERRORS", "BAD_INDEX",
                         "BAD_GOT_WANT", "BAD_BITS", "STATE"),
               OP_WLOOP: ("RUN_CYCLES", "TXS", "BUSY_MIN", "BUSY_MAX", "BUSY_SUM", "BUSY_N",
                          "STATE")}
QUICK_REGS = {OP_RAW: ("RESULT",), OP_READ: ("CHECKED", "ERRORS")}   # eye probes
STALL_S = 60                 # dev-box rule: no build output for 60 s = hung (awto-dan#53)
RUN_ID = None


def say(text):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def gateware_rev():
    paths = ["gateware/flash_baseline", "gateware/hyperram_baseline/board_r14.py"]
    sha = git("log", "-1", "--format=%h", "--", *paths)
    return sha + ("-dirty" if git("status", "--porcelain", "--", *paths) else "")


def record(row):
    OUT.mkdir(parents=True, exist_ok=True)
    row = dict(run_id=RUN_ID, gateware=gateware_rev(),
               time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row)
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# ---- build -------------------------------------------------------------------

BUILD_PY = """
import sys
sys.path.insert(0, {board!r})
sys.path.insert(0, {gw!r})
from board_r14 import CynthionR14Baseline
from top import FlashBaselineTop
CynthionR14Baseline().build(FlashBaselineTop(sck_mhz={sck!r}), build_dir={out!r}, do_program=False)
print("BUILD OK")
"""


def build_one(sck):
    out = OUT / f"sck{sck:g}"
    out.mkdir(parents=True, exist_ok=True)
    blog = out / "build.log"
    env = dict(os.environ)
    # The board judges, not the placer: a missed constraint must still yield a bitstream.
    env["AMARANTH_nextpnr_opts"] = "--timing-allow-fail"
    env["AMARANTH_ecppack_opts"] = "--compress --freq 38.8"
    code = BUILD_PY.format(board=str(BOARD), gw=str(GW), sck=float(sck), out=str(out))
    cmd = ["bash", "-c", f'source "{OSS_CAD_ENV}" && exec "{sys.executable}" -c "$0"', code]
    start = time.monotonic()
    with blog.open("w") as f:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        last = ""
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            if not sel.select(timeout=STALL_S):
                proc.kill()
                say(f"sck{sck:g}: build STALLED {STALL_S} s silent; last line: {last[:160]}; "
                    f"loadavg {os.getloadavg()}")
                return False
            line = proc.stdout.readline()
            if not line:
                break
            f.write(line)
            last = line.strip()
        proc.wait()
    ok = proc.returncode == 0 and "BUILD OK" in blog.read_text()
    say(f"sck{sck:g}: build {'ok' if ok else 'FAILED'} in {time.monotonic() - start:.0f} s; {blog}")
    return ok


def build_cached(scks):
    rev = gateware_rev()

    def cached(sck):
        stamp = OUT / f"sck{sck:g}" / "gateware_rev"
        return ((OUT / f"sck{sck:g}" / "top.bit").exists() and stamp.exists()
                and stamp.read_text().strip() == rev)

    stale = [s for s in scks if not cached(s)]
    say(f"gateware {rev}; building {stale or 'nothing (cached)'}")
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sck, ok in zip(stale, ex.map(build_one, stale)):
            if not ok:
                return False
            (OUT / f"sck{sck:g}" / "gateware_rev").write_text(rev + "\n")
    return True


# ---- board -------------------------------------------------------------------

def configure(sck):
    sys.path.insert(0, str(ROOT / "scripts"))
    from soc_confirm import configure_and_confirm
    rc = configure_and_confirm(OUT / f"sck{sck:g}" / "top.bit", expect="design")
    say(f"configure sck{sck:g}: rc {rc}")
    return rc == 0


class Hung(Exception):
    pass


class Board:
    def __init__(self):
        from apollo_fpga import ApolloDebugger
        self.dbg = ApolloDebugger()
        self.regs = self.dbg.registers
        self.cache = {}

    def rd(self, name):
        return self.regs.register_read(REG[name])

    def rd_stable(self, name):
        """Static registers only: two agreeing reads, or raise (a JTAG slip)."""
        a = self.rd(name)
        for _ in range(3):
            b = self.rd(name)
            if a == b:
                return a
            a = b
        raise RuntimeError(f"register {name} never read the same twice")

    def wr(self, name, value, force=False):
        if force or self.cache.get(name) != value:
            self.regs.register_write(REG[name], value)
            self.cache[name] = value


class Flash:
    """One loaded bitstream: ops, phase/lat control, measured SCK."""

    def __init__(self, b, sck):
        self.b, self.sck = b, sck
        self.div = b.rd_stable("PLL_DIV")
        self.n_steps = 8 * self.div                  # PHASESTEP per period, TN-02200 18.4.1
        self.f = measured_sck(b)
        self.hz = self.f * 1e6
        self.go_bit = 0
        self.pstep_bit = 0
        b.wr("CSH", math.ceil(10e-9 * self.hz) | math.ceil(50e-9 * self.hz) << 8, force=True)
        b.wr("TS", 0, force=True)
        b.wr("MBYTE", 0xF0, force=True)

    # -- capture setting ---------------------------------------------------------
    def phase(self):
        return self.b.rd("PHASE_POS") % self.n_steps

    def set_capture(self, step, lat):
        """Delay-only PHASESTEPs (glitch-free direction) to reach `step`, then lat/xfer."""
        delta = (step - self.phase()) % self.n_steps
        if delta:
            self.pstep_bit ^= 1
            self.b.wr("PHASE_STEP", (self.pstep_bit << 31) | delta)
            # Waits for: delta pulses x 4 cycles of 60 MHz (67 ns each). Bounded at 200 polls.
            for _ in range(200):
                if not self.b.rd("STATUS") & 4:
                    break
            else:
                raise Hung(f"PHASESTEP {delta} never finished")
        frac = step / self.n_steps
        self.b.wr("XFER", int(frac < 0.25 or frac >= 0.75))
        self.b.wr("LAT", lat)

    # -- ops -------------------------------------------------------------------------
    def op(self, kind, *, d=0, addr=0, length=0, burst=0, count=0, stride=0, check=0, seed=0,
           xor=0, poll_after=0, expect_s=0.0, label="", quick=False):
        b = self.b
        for name, v in (("OP", kind | poll_after << 8), ("DESC", d), ("ADDR", addr),
                        ("LENGTH", length), ("BURST", burst), ("COUNT", count),
                        ("STRIDE", stride), ("CHECK", check), ("SEED", seed), ("XOR", xor)):
            b.wr(name, v)
        self.go_bit ^= 1
        b.wr("GO", self.go_bit)
        # Waits for: STATUS.done. Expected `expect_s` (transfer at the measured SCK, plus
        # datasheet-max BUSY for program/erase); x1.25 plus the debugger's round trips.
        deadline = time.monotonic() + 1.25 * expect_s + JTAG_S
        done = False
        while time.monotonic() < deadline:
            if b.rd("STATUS") & 1:
                done = True
                break
        row = dict(label=label, sck=self.sck, sck_meas=self.f, op=kind, desc=d, addr=addr,
                   length=length, burst=burst, count=count, check=check, seed=seed, xor=xor,
                   done=done, phase=self.phase(), n_steps=self.n_steps,
                   lat=b.cache.get("LAT"), xfer=b.cache.get("XFER"))
        for name in RESULT_REGS[kind] if not quick else QUICK_REGS[kind]:
            row[name.lower()] = b.rd_stable(name) if done else b.rd(name)
        for name in ALL_RESULT_REGS:
            row.setdefault(name.lower(), 0)
        if not done:
            say(f"  {label}: HUNG after {1.25 * expect_s + JTAG_S:.2f} s, state {row['state']:#06x}")
        return row

    def read_s(self, mode, length, burst):
        txs = max(1, math.ceil(length / burst))
        return (length * 8 / WIDTH[mode] + txs * 60) / self.hz

    def raw(self, d, length=0, label="raw", poll_after=0, expect_s=0.0):
        return self.op(OP_RAW, d=d, length=length, poll_after=poll_after, expect_s=expect_s,
                       label=label)

    def read(self, mode, addr, length, burst=None, check=CHECK_PATTERN, seed=0, xor=0, label=""):
        burst = burst or length
        r = self.op(OP_READ, d=READ_MODES[mode], addr=addr, length=length, burst=burst,
                    check=check, seed=seed, xor=xor, expect_s=self.read_s(mode, length, burst),
                    label=label or f"read {mode}")
        secs = r["run_cycles"] / self.hz if r["run_cycles"] else 0
        r["mb_s"] = r["bytes"] / secs / 1e6 if secs and r["done"] else 0
        r["mode"] = mode
        return r

    def wloop(self, opcode, addr, count, stride, *, burst=0, dw=W1, seed=0, label=""):
        d = desc(opcode, abits=24, dw=dw)
        r = self.op(OP_WLOOP, d=d, addr=addr, count=count, stride=stride, burst=burst, seed=seed,
                    expect_s=count * T_MAX[opcode], label=label)
        n = r["busy_n"] or 1
        r.update(opcode=opcode, t_min_ms=r["busy_min"] / self.hz * 1e3 if r["busy_n"] else None,
                 t_max_ms=r["busy_max"] / self.hz * 1e3, t_avg_ms=r["busy_sum"] / n / self.hz * 1e3)
        return r


def measured_sck(b):
    """sck cycles per 2**CK_WINDOW_BITS cycles of the 60 MHz pin; median of 3 windows, MHz."""
    vals = []
    for _ in range(3):
        first = b.rd("CK_COUNT")
        # Waits for: the next window (8.74 ms). Bounded at 400 polls (each >= 1 ms over JTAG).
        for _ in range(400):
            v = b.rd("CK_COUNT")
            if v != first:
                break
        vals.append(v)
    return sorted(vals)[1] * 60.0 / (1 << CK_WINDOW_BITS)


def new_seed(prev):
    """32-bit seed whose every byte differs from `prev`'s: no byte of a stale pattern matches."""
    while True:
        s = int.from_bytes(os.urandom(4), "little")
        if all(((s ^ prev) >> (8 * k)) & 0xFF for k in range(4)):
            return s


def open_board(sck):
    b = Board()
    ident = b.rd_stable("ID")
    if ident != APPLET_ID:
        say(f"sck{sck:g}: wrong applet id {ident:#010x}")
        return None
    fl = Flash(b, sck)
    say(f"sck{sck:g}: applet ok, SCK requested {b.rd_stable('CK_KHZ') / 1000:g} MHz, "
        f"measured {fl.f:.4f} MHz, PLL div {fl.div} ({fl.n_steps} phase steps), "
        f"lock {bool(b.rd('STATUS') & 2)}")
    return fl


# ---- calibration -------------------------------------------------------------

def eye_key(fl, step, lat):
    """Capture time in phase steps: past 3/4 T the xfer path adds an engine cycle."""
    extra = 1 if step / fl.n_steps >= 0.75 else 0
    return (lat - extra) * fl.n_steps + step


def pick(fl, grid):
    """Centre of the widest clean run along capture time. grid: {(step, lat): ok}."""
    pts = sorted(grid, key=lambda k: eye_key(fl, *k))
    runs, cur = [], []
    for k in pts:
        if grid[k]:
            cur.append(k)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if not runs:
        return None, 0
    widest = max(runs, key=len)
    return widest[len(widest) // 2], len(widest)


def eye(fl, probe, label, stride):
    grid = {}
    steps = range(0, fl.n_steps, stride)
    for step in steps:
        for lat in LAT_RANGE:
            fl.set_capture(step, lat)
            grid[(step, lat)] = probe()
    best, width = pick(fl, grid)
    rows = []
    for lat in LAT_RANGE:
        rows.append(f"lat {lat:>2} " + "".join("." if grid[(s, lat)] else "X" for s in steps))
    say(f"  eye {label} (phase steps 0..{fl.n_steps - 1} by {stride}):\n    " + "\n    ".join(rows))
    say(f"  {label}: best {best}, widest clean run {width} points")
    record(dict(label=f"eye {label}", sck=fl.sck, sck_meas=fl.f, n_steps=fl.n_steps,
                stride=stride, best=best, width=width,
                grid=[[s, l, int(ok)] for (s, l), ok in grid.items()]))
    return best, width


def jedec_ok(fl):
    r = fl.raw(desc(0x9F, dw=W1, read=True), length=3, label="JEDEC ID")
    return r["done"] and r["result"] & 0xFFFFFF == 0xEF4016


# ---- one SCK point ---------------------------------------------------------------

def identify(fl):
    """JEDEC ID, SR1-3, QPI probe. Reads only, plus 38h/FFh (volatile; ignored by IQ)."""
    out = {}
    for name, cmd in (("jedec", 0x9F), ("sr1", 0x05), ("sr2", 0x35), ("sr3", 0x15)):
        n = 3 if name == "jedec" else 1
        out[name] = fl.raw(desc(cmd, dw=W1, read=True), length=n, label=name)["result"]
    fl.raw(desc(0x38), label="QPI enter (38h)")
    out["qpi_jedec"] = fl.raw(desc(0x9F, cmd_w4=True, dw=W4, read=True), length=3,
                              label="QPI 9Fh")["result"]
    fl.raw(desc(0xFF, cmd_w4=True), label="QPI exit (FFh)")
    out["jedec_after"] = fl.raw(desc(0x9F, dw=W1, read=True), length=3,
                                label="JEDEC after probe")["result"]
    say(f"  ID: JEDEC {out['jedec']:#08x}, SR1 {out['sr1']:#04x} SR2 {out['sr2']:#04x} "
        f"SR3 {out['sr3']:#04x}; QPI 9Fh -> {out['qpi_jedec']:#08x}, "
        f"SPI after {out['jedec_after']:#08x}")
    return out


def scan(fl, label="scan"):
    """Whole-part EBh read checked against 0xFF; per-4 KiB-sector bitmap of non-blank."""
    r = fl.read(EYE_MODE, 0, PART_BYTES, check=CHECK_BLANK, label=label)
    words = []
    for w in range(BITMAP_WORDS):
        fl.b.wr("BITMAP_IDX", w)
        words.append(fl.b.rd("BITMAP_DATA"))
    used = [s for s in range(BITMAP_WORDS * 32) if words[s // 32] >> (s % 32) & 1]
    ranges, start = [], None
    for s in range(BITMAP_WORDS * 32 + 1):
        on = s in set(used) if s < BITMAP_WORDS * 32 else False
        if on and start is None:
            start = s
        if not on and start is not None:
            ranges.append((start << 12, s << 12))
            start = None
    say(f"  {label}: {r['mb_s']:.2f} MB/s, non-FF bytes {r['errors']} / {r['checked']}, "
        f"used 4K sectors {len(used)}: " + ", ".join(f"{a:#08x}-{e:#08x}" for a, e in ranges))
    return r, words, ranges


def measure(sck, state):
    fl = open_board(sck)
    if fl is None:
        return 1
    # 1. capture from the JEDEC ID (x1 on IO1): enough for status polls and the scan.
    best, width = eye(fl, lambda: jedec_ok(fl), "JEDEC x1", max(1, fl.n_steps // 16))
    if best is None:
        record(dict(label="point", sck=sck, sck_meas=fl.f, ok=False, why="no JEDEC eye"))
        say(f"sck{sck:g}: no capture setting reads the JEDEC ID; point abandoned")
        return 0
    fl.set_capture(*best)
    ident = identify(fl)
    try:
        sr, words, ranges = scan(fl)
        record(dict(label="scan", sck=sck, sck_meas=fl.f, ident=ident, mb_s=sr["mb_s"],
                    errors=sr["errors"], checked=sr["checked"], done=sr["done"], bitmap=words,
                    ranges=ranges, test=[TEST_BASE, TEST_BASE + TEST_SIZE]))
        # 2. fresh seed: erase (4K x8, 32K x1, 64K x rest), blank check, program (x1 half,
        #    quad half). Every case is one pass.
        seed = new_seed(state["seed"])
        stale = state["seed"]
        state["seed"] = seed
        er = [fl.wloop(0x20, TEST_BASE, 8, 4096, label="erase 4K (20h)"),
              fl.wloop(0x52, TEST_BASE + 0x8000, 1, 0x8000, label="erase 32K (52h)"),
              fl.wloop(0xD8, TEST_BASE + 0x10000, TEST_SIZE // 0x10000 - 1, 0x10000,
                       label="erase 64K (D8h)")]
        blank = fl.read(EYE_MODE, TEST_BASE, TEST_SIZE, check=CHECK_BLANK, label="blank check")
        half = TEST_SIZE // 2
        pg = [fl.wloop(0x02, TEST_BASE, half // 256, 256, burst=256, dw=W1, seed=seed,
                       label="program 02h"),
              fl.wloop(0x32, TEST_BASE + half, half // 256, 256, burst=256, dw=W4, seed=seed,
                       label="program 32h")]
        for r in er + pg:
            say(f"  {r['label']:<18} n {r['busy_n']:>5} min {r['t_min_ms'] or 0:9.3f} "
                f"avg {r['t_avg_ms']:9.3f} max {r['t_max_ms']:9.3f} ms"
                + ("" if r["done"] else " HUNG"))
            record(dict(r, kind="write"))
        say(f"  blank check: errors {blank['errors']} / {blank['checked']}"
            + ("" if blank["done"] else " HUNG"))
        record(dict(blank, kind="blank"))
        # 3. capture from the seeded EBh read (all four IO, both directions).
        probe = lambda: (lambda r: r["done"] and r["errors"] == 0 and r["checked"] == EYE_BYTES)(
            fl.read(EYE_MODE, TEST_BASE, EYE_BYTES, seed=seed, label="eye"))
        best, width = eye(fl, probe, "EBh seeded", max(1, fl.n_steps // 32))
        if best is None:
            say(f"sck{sck:g}: no clean EBh capture; measuring at the JEDEC setting")
            best = state.get("fallback")
        else:
            fl.set_capture(*best)
        # 4. every mode, whole test region in one transaction, then 32-byte bursts.
        for mode in READ_MODES:
            r = fl.read(mode, TEST_BASE, TEST_SIZE, seed=seed, label=f"{mode} region")
            report(fl, r)
            r = fl.read(mode, TEST_BASE, 1 << 16, burst=32, seed=seed, label=f"{mode} burst32")
            report(fl, r)
        for lbl, kw in (("ctl stale seed", dict(seed=stale)), ("ctl xor", dict(seed=seed, xor=1))):
            r = fl.read(EYE_MODE, TEST_BASE, TEST_SIZE, label=lbl, **kw)
            report(fl, r)
    except Hung as e:
        say(f"sck{sck:g}: {e}")
    return 0


def report(fl, r):
    pin = fl.f * WIDTH[r["mode"]] / 8
    say(f"  {r['label']:<22} {r['mb_s']:8.2f} MB/s ({100 * r['mb_s'] / pin:5.1f}% of pin) "
        f"errors {r['errors']} / {r['checked']} txs {r['txs']}"
        + ("" if r["done"] else " HUNG")
        + (f" bits {r['bad_bits']:#04x} first bad @{r['bad_index']:#x} "
           f"got {r['bad_got_want'] >> 8:#04x} want {r['bad_got_want'] & 0xFF:#04x}"
           if r["errors"] and "ctl" not in r["label"] else ""))
    record(dict(r, kind="read", pin_mb_s=pin))


# ---- commands ----------------------------------------------------------------

def sweep(args):
    global RUN_ID
    RUN_ID = time.strftime("%Y%m%d-%H%M%S")
    say(f"sweep {RUN_ID}: SCK {args.sck}; test region {TEST_BASE:#x}+{TEST_SIZE:#x}")
    if not build_cached(args.sck):
        return 1
    state = dict(seed=new_seed(0))
    rc = 0
    for sck in args.sck:
        if not configure(sck):
            rc = 1
            record(dict(label="point", sck=sck, ok=False, why="configure failed"))
            continue
        rc |= measure(sck, state)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        table(RUN_ID)
    out = OUT / f"sweep-{RUN_ID}.md"
    out.write_text(buf.getvalue())
    print(buf.getvalue())
    say(f"sweep {RUN_ID} done: table {out}")
    return rc


def scan_only(args):
    global RUN_ID
    RUN_ID = time.strftime("%Y%m%d-%H%M%S")
    if not build_cached([args.sck]) or not configure(args.sck):
        return 1
    fl = open_board(args.sck)
    best, _ = eye(fl, lambda: jedec_ok(fl), "JEDEC x1", max(1, fl.n_steps // 16))
    if best is None:
        return 1
    fl.set_capture(*best)
    ident = identify(fl)
    r, words, ranges = scan(fl)
    record(dict(label="scan", sck=args.sck, sck_meas=fl.f, ident=ident, mb_s=r["mb_s"],
                errors=r["errors"], checked=r["checked"], done=r["done"], bitmap=words,
                ranges=ranges))
    return 0


def table(run_id=None):
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines()]
    if run_id is None:
        run_id = next((r["run_id"] for r in reversed(rows) if r.get("run_id")), None)
    rows = [r for r in rows if r.get("run_id") == run_id]
    scks = sorted({r["sck"] for r in rows if "sck" in r})

    def get(sck, label):
        return next((r for r in reversed(rows) if r.get("sck") == sck and r["label"] == label), None)

    def cell(r):
        if r is None:
            return "-"
        if not r["done"]:
            return "HUNG"
        return f"{r['mb_s']:.2f} / {r['errors']}"

    modes = list(READ_MODES)
    print(f"Run {run_id}. Cells: MB/s / errors over the {TEST_SIZE >> 20} MiB test region "
          f"(one transaction); burst32 = 64 KiB in 32-byte transactions.\n")
    print("| SCK req | SCK meas | eye EBh (pts) | " + " | ".join(modes)
          + " | EBh burst32 | 0Bh burst32 | stale-seed ctl | xor ctl | blank chk | scan MB/s |")
    print("|" + "---|" * (len(modes) + 9))
    for sck in scks:
        e = get(sck, "eye EBh seeded")
        meas = next((r.get("sck_meas") for r in rows if r.get("sck") == sck and r.get("sck_meas")), 0)
        cells = [cell(get(sck, f"{m} region")) for m in modes]
        ctl = [get(sck, "ctl stale seed"), get(sck, "ctl xor")]
        bl, sc = get(sck, "blank check"), get(sck, "scan")
        print(f"| {sck:g} | {meas:.3f} | {e['width'] if e else '-'} | " + " | ".join(cells)
              + f" | {cell(get(sck, 'EBh 1-4-4 burst32'))} | {cell(get(sck, '0Bh 1-1-1 burst32'))}"
              + " | " + " | ".join("-" if c is None else ("HUNG" if not c["done"] else str(c["errors"]))
                                   for c in ctl)
              + f" | {'-' if bl is None else bl['errors']} | "
              + f"{'-' if sc is None else format(sc['mb_s'], '.2f')} |")
    print("\n| SCK | op | n | min ms | avg ms | max ms |")
    print("|---|---|---|---|---|---|")
    for sck in scks:
        for lbl in ("erase 4K (20h)", "erase 32K (52h)", "erase 64K (D8h)", "program 02h",
                    "program 32h"):
            r = get(sck, lbl)
            if r:
                print(f"| {sck:g} | {lbl} | {r['busy_n']} | {r['t_min_ms'] or 0:.3f} | "
                      f"{r['t_avg_ms']:.3f} | {r['t_max_ms']:.3f} |" + ("" if r["done"] else " HUNG"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build")
    pb.add_argument("--sck", type=float, nargs="+", required=True)
    pw = sub.add_parser("sweep")
    pw.add_argument("--sck", type=float, nargs="+", default=SWEEP_SCK)
    ps = sub.add_parser("scan", help="read-only: ID, status registers, sector map")
    ps.add_argument("--sck", type=float, default=20)
    sub.add_parser("clocks")
    pt = sub.add_parser("table")
    pt.add_argument("--run")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if args.cmd == "clocks":
        print(reachable(3.2, 300))
        return 0
    if args.cmd == "build":
        return 0 if build_cached(args.sck) else 1
    if args.cmd == "scan":
        return scan_only(args)
    if args.cmd == "table":
        return table(args.run)
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
