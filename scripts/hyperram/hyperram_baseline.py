#!/usr/bin/env python3
#
# Build, load and measure the HyperRAM sequential baseline (#581).
# SPDX-License-Identifier: BSD-3-Clause

"""Sequential HyperRAM throughput, non-RV path, on Cynthion r1.4.

    ./scripts/hyperram/hyperram_baseline.py sweep          # all CKs, one table (the normal use)
    ./scripts/hyperram/hyperram_baseline.py build --ck 100 120 140 150 160 165
    ./scripts/hyperram/hyperram_baseline.py run --ck 165            # configure + measure
    ./scripts/hyperram/hyperram_baseline.py run --ck 165 --no-configure

- Gateware: `gateware/hyperram_baseline/` (datasheet + public designs only; see
  docs/chips/hyperram/sequential-baseline.md). One CK per bitstream.
- Load: `apollo configure` (volatile; power-cycle or scripts/soc_run.py restores the SoC).
- Readout: LUNA JTAG registers via `apollo_fpga.ApolloDebugger`.
- Log: tmp/logs/hyperram_baseline.log; results: tmp/hyperram-baseline/results.jsonl.
"""

import argparse
import contextlib
import io
import json
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
GW = ROOT / "gateware" / "hyperram_baseline"
OUT = ROOT / "tmp" / "hyperram-baseline"
LOG = ROOT / "tmp" / "logs" / "hyperram_baseline.log"
RESULTS = OUT / "results.jsonl"
OSS_CAD_ENV = Path.home() / "opt" / "oss-cad-suite" / "environment"
sys.path.insert(0, str(GW))

from top import (APPLET_ID, CK_WINDOW_BITS, MODE_BOTH, MODE_READ, MODE_REG,  # noqa: E402
                 MODE_WRITE, REG, reachable)

WORDS_ALL = 1 << 22          # whole 64 Mbit array, 16-bit words
CR0_ADDR = 0x800             # datasheet 9.1: CR0 word address
# Every reachable rung tried, rated max 166 (165 reachable), then x3, x3.5, x4 of 60 MHz.
SWEEP_CK = [60, 80, 100, 120, 140, 150, 160, 165, 180, 210, 240]
RUN_ID = None                # set per invocation; tags results.jsonl rows
STALL_S = 60                # dev-box rule: no output for 60 s = hung build (awto-dan#53)


def say(text):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def commit():
    sha = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "gateware/hyperram_baseline",
                            "scripts/hyperram/hyperram_baseline.py"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


# ---- build -------------------------------------------------------------------

BUILD_PY = """
import sys
sys.path.insert(0, {gw!r})
from board_r14 import CynthionR14Baseline
from top import BaselineTop
CynthionR14Baseline().build(BaselineTop(ck_mhz={ck!r}), build_dir={out!r}, do_program=False)
print("BUILD OK")
"""


def build_one(ck):
    out = OUT / f"ck{ck:g}"
    out.mkdir(parents=True, exist_ok=True)
    blog = out / "build.log"
    env = dict(os.environ)
    # The board judges, not the placer: a missed constraint must still yield a bitstream.
    env["AMARANTH_nextpnr_opts"] = "--timing-allow-fail"
    env["AMARANTH_ecppack_opts"] = "--compress --freq 38.8"
    code = BUILD_PY.format(gw=str(GW), ck=float(ck), out=str(out))
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
                say(f"ck{ck:g}: build STALLED {STALL_S} s silent; last line: {last[:160]}; "
                    f"loadavg {os.getloadavg()}")
                return False
            line = proc.stdout.readline()
            if not line:
                break
            f.write(line)
            last = line.strip()
        proc.wait()
    ok = proc.returncode == 0 and "BUILD OK" in blog.read_text()
    say(f"ck{ck:g}: build {'ok' if ok else 'FAILED'} in {time.monotonic() - start:.0f} s; {blog}")
    return ok


# ---- board -------------------------------------------------------------------

def configure(ck):
    bit = OUT / f"ck{ck:g}" / "top.bit"
    # Retried: a running SoC drives its UART on the JTAG-shared pins (IDCODE ffffffff).
    for attempt in range(1, 4):
        r = subprocess.run(["apollo", "configure", str(bit)], cwd=ROOT, capture_output=True,
                           text=True)
        say(f"configure ck{ck:g} attempt {attempt}: rc {r.returncode} "
            f"{(r.stdout + r.stderr).strip()[-160:]}")
        if r.returncode == 0:
            return True
    return False


class Board:
    def __init__(self):
        sys.path.insert(0, str(ROOT / "repos" / "apollo"))
        from apollo_fpga import ApolloDebugger
        self.dbg = ApolloDebugger()
        self.regs = self.dbg.registers

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

    def wr(self, name, value):
        self.regs.register_write(REG[name], value)


def new_seed():
    """Nonzero 16-bit seed: each write leaves data a stale read cannot match."""
    return int.from_bytes(os.urandom(2), "little") or 1


def measured_ck(b):
    """hr cycles per 2**CK_WINDOW_BITS cycles of the 60 MHz pin; median of 3 windows, MHz."""
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


def run_case(b, ck, *, mode, burst, total, xor=0, wait=None, start_addr=0, label="",
             seed=None, ck_meas=None):
    if seed is None:
        seed = new_seed() if mode in (MODE_WRITE, MODE_BOTH) else getattr(b, "seed", 0)
    if mode in (MODE_WRITE, MODE_BOTH):
        b.seed = seed
    b.wr("MODE", mode)
    b.wr("BURST", burst)
    b.wr("TOTAL", total)
    b.wr("XOR", xor)
    b.wr("SEED", seed)
    b.wr("START_ADDR", start_addr)
    b.wr("WAIT", 0 if wait is None else (0x100 | wait))
    b.wr("REG_ADDR", CR0_ADDR)
    b.wr("GO", 0)
    b.wr("GO", 1)
    # Waits for: STATUS.done. Expected: words / CK (+ per-burst overhead < 10%) + JTAG
    # poll latency; x1.25 on the transfer plus 1 s for the debugger. Expiry: case marked hung.
    words = total * (2 if mode == MODE_BOTH else 1)
    deadline = time.monotonic() + 1.25 * 1.1 * words / (ck * 1e6) + 1.0
    done = False
    while time.monotonic() < deadline:
        if b.rd("STATUS") & 1:
            done = True
            break
    row = dict(run_id=RUN_ID, label=label, ck_mhz=ck, ck_meas_mhz=ck_meas, mode=mode, burst=burst, total=total,
               xor=xor, seed=seed, wait=wait, done=done, commit=commit(),
               time=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    for name in ("RUN_CYCLES", "WORDS_W", "WORDS_R", "SRC_WAIT", "SINK_WAIT", "XACTS",
                 "MISSING", "STRADDLED", "CHECKED", "ERRORS", "BAD_INDEX", "BAD_GOT_WANT",
                 "GENERATED", "REG_VALUE", "BAD_BITS"):
        # A run that never finished leaves counters moving: one read, flagged HUNG.
        row[name.lower()] = b.rd_stable(name) if done else b.rd(name)
    moved = row["words_w"] + row["words_r"]
    clock = ck_meas or ck
    row["mb_s"] = (2 * moved / (row["run_cycles"] / (clock * 1e6)) / 1e6) if row["run_cycles"] else 0
    row["efficiency"] = moved / row["run_cycles"] if row["run_cycles"] else 0
    OUT.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    bad = row["bad_got_want"]
    say(f"  {label:<22} mode {mode} burst {burst:>4} total {total:>8}: "
        f"{row['mb_s']:7.1f} MB/s eff {100 * row['efficiency']:5.1f}% "
        f"errors {row['errors']} / {row['checked']} missing {row['missing']} "
        f"straddled {row['straddled']} src_wait {row['src_wait']} sink_wait {row['sink_wait']}"
        + ("" if done else " HUNG")
        + (f" bad bits {row['bad_bits']:#06x} first bad @{row['bad_index']} "
           f"got {bad >> 16:#06x} want {bad & 0xFFFF:#06x}" if row["errors"] else ""))
    return row


def set_delay(b, taps, direction=0):
    b.wr("DELAY", (direction << 7) | taps)
    b.wr("DELAY", 0x100 | (direction << 7) | taps)
    # Waits for: the DELAYF stepper (8 + 8*taps hr cycles, < 2 us). Bounded by 50 polls.
    for _ in range(50):
        if not b.rd("STATUS") & 2:
            break
    b.wr("DELAY", (direction << 7) | taps)
    return bool(b.rd("STATUS") & 4)


def open_board(ck):
    b = Board()
    ident = b.rd_stable("ID")
    if ident != APPLET_ID:
        say(f"ck{ck:g}: wrong applet id {ident:#010x}")
        return None, None, None
    ck_khz, max_burst = b.rd_stable("CK_KHZ"), b.rd_stable("MAX_BURST")
    ck_meas = measured_ck(b)
    say(f"ck{ck:g}: applet ok, CK requested {ck_khz / 1000:g} MHz, measured {ck_meas:.4f} MHz "
        f"(60 MHz pin as reference), sync {b.rd_stable('SYNC_KHZ') / 1000:g} MHz, "
        f"max burst {max_burst}, status {b.rd('STATUS'):#x}, commit {commit()}")
    return b, max_burst, ck_meas


def sweep_eye(b, ck, max_burst, ck_meas, args):
    """Short seeded write+read per tap. Returns (best tap, eye width in taps, eye list)."""
    probe = 1 << 16
    eye = []
    for taps in range(0, 128, args.tap_step):
        set_delay(b, taps, args.direction)
        r = run_case(b, ck, mode=MODE_BOTH, burst=max_burst, total=probe, label=f"tap {taps}",
                     ck_meas=ck_meas)
        eye.append((taps, r["errors"] + r["missing"] + (0 if r["done"] else probe),
                    r["bad_bits"]))
    say(f"  eye ck{ck:g}: " + " ".join(f"{t}:{'.' if e == 0 else 'X'}" for t, e, _ in eye))
    clean = [t for t, e, _ in eye if e == 0]
    if not clean:
        best = min(eye, key=lambda x: x[1])[0]
        say(f"  ck{ck:g}: no clean tap; fewest errors at tap {best}")
        return best, 0, eye
    # Clean runs, wrapping round tap 127 -> 0 is not assumed (taps are not periodic).
    runs, cur = [], [clean[0]]
    for t in clean[1:]:
        if t - cur[-1] == args.tap_step:
            cur.append(t)
        else:
            runs.append(cur)
            cur = [t]
    runs.append(cur)
    widest = max(runs, key=len)
    return widest[len(widest) // 2], len(widest) * args.tap_step, eye


def measure(ck, args):
    b, max_burst, ck_meas = open_board(ck)
    if b is None:
        return 1
    cr0_raw = run_case(b, ck, mode=MODE_REG, burst=2, total=2, label="CR0 read")["reg_value"]
    best, width, eye = sweep_eye(b, ck, max_burst, ck_meas, args)
    set_delay(b, best, args.direction)
    cr0 = run_case(b, ck, mode=MODE_REG, burst=2, total=2, label="CR0 read")["reg_value"]
    say(f"  ck{ck:g}: measuring at tap {best}; widest clean run {width} taps; CR0 "
        f"{cr0_raw:#06x} at tap 0, {cr0:#06x} at tap {best} (default 0x8f2f)")
    with RESULTS.open("a") as f:
        f.write(json.dumps(dict(run_id=RUN_ID, label="eye", ck_mhz=ck, ck_meas_mhz=ck_meas,
                                tap=best, width=width, step=args.tap_step, eye=eye,
                                cr0_tap0=cr0_raw, cr0=cr0, commit=commit())) + "\n")
    kw = dict(burst=max_burst, total=WORDS_ALL, ck_meas=ck_meas)
    run_case(b, ck, mode=MODE_WRITE, label="write all", **kw)
    run_case(b, ck, mode=MODE_READ, label="read all", **kw)
    run_case(b, ck, mode=MODE_READ, xor=1, label="neg control xor", **kw)
    run_case(b, ck, mode=MODE_READ, seed=b.seed ^ 0x5A5A, label="neg control stale seed", **kw)
    run_case(b, ck, mode=MODE_BOTH, label="write+read", **kw)
    if not args.quick:
        for burst in (16, 32, 64, 128, 256, 512):
            if burst < max_burst:
                run_case(b, ck, mode=MODE_WRITE, burst=burst, total=1 << 20,
                         label=f"write b{burst}", ck_meas=ck_meas)
                run_case(b, ck, mode=MODE_READ, burst=burst, total=1 << 20,
                         label=f"read b{burst}", ck_meas=ck_meas)
    return 0


def gateware_rev():
    """Bitstream cache key: last commit touching the gateware, plus -dirty."""
    sha = subprocess.run(["git", "log", "-1", "--format=%h", "--", "gateware/hyperram_baseline"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "gateware/hyperram_baseline"],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def sweep(args):
    """Build what the cache lacks, then load and measure each CK in turn; one table."""
    global RUN_ID
    RUN_ID = time.strftime("%Y%m%d-%H%M%S")
    rev = gateware_rev()

    def cached(ck):
        stamp = OUT / f"ck{ck:g}" / "gateware_rev"
        return ((OUT / f"ck{ck:g}" / "top.bit").exists() and stamp.exists()
                and stamp.read_text().strip() == rev)

    stale = [ck for ck in args.ck if not cached(ck)]
    say(f"sweep {RUN_ID}: gateware {rev}; CK {args.ck}; building {stale or 'nothing (cached)'}")
    with ThreadPoolExecutor(max_workers=6) as ex:
        for ck, ok in zip(stale, ex.map(build_one, stale)):
            if not ok:
                return 1
            (OUT / f"ck{ck:g}" / "gateware_rev").write_text(rev + "\n")
    args.quick = not args.bursts
    rc = 0
    for ck in args.ck:
        if not configure(ck):
            rc = 1
            continue
        rc |= measure(ck, args)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        table(RUN_ID)
    out = OUT / f"sweep-{RUN_ID}.md"
    out.write_text(buf.getvalue())
    print(buf.getvalue())
    say(f"sweep {RUN_ID} done: table {out}")
    return rc


def table(run_id=None):
    keep = {}
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines()]
    if run_id is None:
        run_id = next((r.get("run_id") for r in reversed(rows) if r.get("run_id")), None)
    for r in rows:
        if r.get("run_id") != run_id:
            continue
        if not r["label"].startswith(("tap", "case", "CR0", "cr0")):
            keep[(r["ck_mhz"], r["label"])] = r
    cks = sorted({k[0] for k in keep if (k[0], "read all") in keep})

    def err(r):
        if not r["done"]:
            return "HUNG"
        return f"{r['errors']}" + (f" (bits {r['bad_bits']:#06x})" if r["errors"] else "")

    def rate(r):
        return f"{r['mb_s']:.1f}" if r["done"] else "HUNG"

    print("| CK req MHz | CK meas MHz | burst | eye taps (step) | tap | CR0 at tap | write MB/s | "
          "read MB/s | write+read MB/s | read errors | write+read errors | missing (r, w+r) | "
          "xor ctl errors | stale-seed ctl errors | read % of pin (meas) | commit |")
    print("|" + "---|" * 16)
    for ck in cks:
        w, r, b, x, s, e = (keep[(ck, k)] for k in (
            "write all", "read all", "write+read", "neg control xor", "neg control stale seed",
            "eye"))
        meas = r["ck_meas_mhz"]
        pct = f"{100 * r['mb_s'] / (2 * meas):.1f}" if r["done"] else "-"
        print(f"| {ck:g} | {meas:.4f} | {w['burst']} | {e['width']} ({e['step']}) | {e['tap']} | "
              f"{e.get('cr0', 0):#06x} | {rate(w)} | {rate(r)} | {rate(b)} | {err(r)} | {err(b)} | "
              f"{r['missing']}, {b['missing']} | {err(x)} | {err(s)} | {pct} | {r['commit']} |")
    print("\nWords per case: 4194304 (whole array); controls must equal it.\n")
    for ck in cks:
        e = keep[(ck, "eye")]
        print(f"| {ck:g} | `" + "".join("." if n == 0 else "X" for _, n, _ in e["eye"]) + "` |")
    sizes = (16, 32, 64, 128, 256, 512)
    print()
    print("| CK MHz | " + " | ".join(f"burst {n} w / r MB/s" for n in sizes) + " |")
    print("|" + "---|" * (len(sizes) + 1))
    for ck in cks:
        cells = []
        for n in sizes:
            w, r = keep.get((ck, f"write b{n}")), keep.get((ck, f"read b{n}"))
            cells.append("-" if not w else f"{w['mb_s']:.0f} / {r['mb_s']:.0f}"
                         + ("" if r["errors"] == 0 else f" ({r['errors']} err)"))
        print(f"| {ck:g} | " + " | ".join(cells) + " |")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build")
    pb.add_argument("--ck", type=float, nargs="+", required=True)
    pb.add_argument("--jobs", type=int, default=4)
    pr = sub.add_parser("run")
    pr.add_argument("--ck", type=float, nargs="+", required=True)
    pr.add_argument("--no-configure", action="store_true")
    pr.add_argument("--tap-step", type=int, default=4)
    pr.add_argument("--direction", type=int, default=0)
    pr.add_argument("--quick", action="store_true", help="skip the burst-length sweep")
    pw = sub.add_parser("sweep", help="every CK in one invocation: build cache, load, measure, table")
    pw.add_argument("--ck", type=float, nargs="+", default=SWEEP_CK)
    pw.add_argument("--tap-step", type=int, default=8)
    pw.add_argument("--direction", type=int, default=0)
    pw.add_argument("--bursts", action="store_true", help="add the burst-length sweep per CK")
    sub.add_parser("clocks")
    sub.add_parser("table", help="markdown tables from results.jsonl (last run per CK/case)")
    pc = sub.add_parser("case", help="one run on what is loaded: MODE:BURST:TOTAL[:START[:WAIT[:XOR]]] ...")
    pc.add_argument("--ck", type=float, required=True)
    pc.add_argument("--tap", type=int)
    pc.add_argument("--direction", type=int, default=0)
    pc.add_argument("specs", nargs="+")
    args = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)

    if args.cmd == "clocks":
        print(reachable(50, 200))
        return 0
    if args.cmd == "sweep":
        return sweep(args)
    if args.cmd == "table":
        return table()
    if args.cmd == "case":
        b = Board()
        if b.rd_stable("ID") != APPLET_ID:
            say("wrong applet loaded")
            return 1
        if args.tap is not None:
            set_delay(b, args.tap, args.direction)
        for spec in args.specs:
            given = [int(x, 0) for x in spec.split(":")]
            mode, burst, total, start, wait, xor = given + [0, None, 0][len(given) - 3:]
            run_case(b, args.ck, mode=mode, burst=burst, total=total, start_addr=start,
                     wait=wait, xor=xor, label=f"case {spec}")
        return 0
    if args.cmd == "build":
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            ok = list(ex.map(build_one, args.ck))
        return 0 if all(ok) else 1
    rc = 0
    for ck in args.ck:
        if not args.no_configure and not configure(ck):
            rc = 1
            continue
        rc |= measure(ck, args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
