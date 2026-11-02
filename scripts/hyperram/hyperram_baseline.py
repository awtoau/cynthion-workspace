#!/usr/bin/env python3
#
# Build, load and measure the HyperRAM sequential baseline (#581).
# SPDX-License-Identifier: BSD-3-Clause

"""Sequential HyperRAM throughput, non-RV path, on Cynthion r1.4.

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

from top import (APPLET_ID, MODE_BOTH, MODE_READ, MODE_REG, MODE_WRITE,  # noqa: E402
                 REG, reachable)

WORDS_ALL = 1 << 22          # whole 64 Mbit array, 16-bit words
CR0_ADDR = 0x800             # datasheet 9.1: CR0 word address
STALL_S = 60                 # dev-box rule: no output for 60 s = hung build (awto-dan#53)


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


def run_case(b, ck, *, mode, burst, total, xor=0, wait=None, start_addr=0, label=""):
    b.wr("MODE", mode)
    b.wr("BURST", burst)
    b.wr("TOTAL", total)
    b.wr("XOR", xor)
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
    row = dict(label=label, ck_mhz=ck, mode=mode, burst=burst, total=total, xor=xor,
               wait=wait, done=done, commit=commit(),
               time=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    for name in ("RUN_CYCLES", "WORDS_W", "WORDS_R", "SRC_WAIT", "SINK_WAIT", "XACTS",
                 "MISSING", "STRADDLED", "CHECKED", "ERRORS", "BAD_INDEX", "BAD_GOT_WANT",
                 "GENERATED", "REG_VALUE"):
        row[name.lower()] = b.rd_stable(name)
    moved = row["words_w"] + row["words_r"]
    row["mb_s"] = (2 * moved / (row["run_cycles"] / (ck * 1e6)) / 1e6) if row["run_cycles"] else 0
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
        + (f" first bad @{row['bad_index']} got {bad >> 16:#06x} want {bad & 0xFFFF:#06x}"
           if row["errors"] else ""))
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


def measure(ck, args):
    b = Board()
    ident = b.rd_stable("ID")
    if ident != APPLET_ID:
        say(f"ck{ck:g}: wrong applet id {ident:#010x}")
        return 1
    ck_khz, max_burst = b.rd_stable("CK_KHZ"), b.rd_stable("MAX_BURST")
    say(f"ck{ck:g}: applet ok, CK {ck_khz / 1000:g} MHz, max burst {max_burst}, "
        f"status {b.rd('STATUS'):#x}, commit {commit()}")
    cr0 = run_case(b, ck, mode=MODE_REG, burst=2, total=2, label="CR0 read")["reg_value"]
    say(f"  CR0 reads {cr0:#06x} (datasheet default 0x8f2f)")

    # Input-delay sweep: short write+read per tap; the eye is the run of clean taps.
    probe = 1 << 16
    eye = []
    for taps in range(0, 128, args.tap_step):
        cflag = set_delay(b, taps, args.direction)
        r = run_case(b, ck, mode=MODE_BOTH, burst=max_burst, total=probe, label=f"tap {taps}")
        eye.append((taps, r["errors"] + r["missing"], cflag))
    clean = [t for t, e, _ in eye if e == 0]
    say(f"  eye ck{ck:g}: " + " ".join(f"{t}:{'.' if e == 0 else 'X'}" for t, e, _ in eye))
    if not clean:
        say(f"  ck{ck:g}: no clean tap; measuring at tap 0 to record the failure")
        best = 0
    else:
        runs, cur = [], [clean[0]]
        for t in clean[1:]:
            if t - cur[-1] == args.tap_step:
                cur.append(t)
            else:
                runs.append(cur)
                cur = [t]
        runs.append(cur)
        widest = max(runs, key=len)
        best = widest[len(widest) // 2]
    set_delay(b, best, args.direction)
    say(f"  ck{ck:g}: measuring at tap {best}")

    run_case(b, ck, mode=MODE_WRITE, burst=max_burst, total=WORDS_ALL, label="write all")
    run_case(b, ck, mode=MODE_READ, burst=max_burst, total=WORDS_ALL, label="read all")
    run_case(b, ck, mode=MODE_READ, burst=max_burst, total=WORDS_ALL, xor=1,
             label="neg control xor")
    run_case(b, ck, mode=MODE_BOTH, burst=max_burst, total=WORDS_ALL, label="write+read")
    for burst in (16, 32, 64, 128, 256, 512):
        if burst < max_burst:
            run_case(b, ck, mode=MODE_WRITE, burst=burst, total=1 << 20, label=f"write b{burst}")
            run_case(b, ck, mode=MODE_READ, burst=burst, total=1 << 20, label=f"read b{burst}")
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
    sub.add_parser("clocks")
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
