#!/usr/bin/env python3
#
# Run the #115 USB workload under QEMU, on each concurrency model, and size it.
# SPDX-License-Identifier: BSD-3-Clause

"""The measurement issue #115 asks for: a real workload, not an idle shell.

Builds the shell three ways -- untouched, with `--features workload`, and with
`--features preempt` -- runs the synthetic USB device-emulation load on the last
two under `qemu-system-riscv32 -M virt`, and prints what each cost.

    ./scripts/soc_workload.py               # sizes and both runs
    ./scripts/soc_workload.py --sizes       # sizes only, no QEMU
    ./scripts/soc_workload.py --trace       # also take an I-cache trace

## Why `-icount`

Without it `mcycle` and `minstret` on `virt` are both the host TSC, which is why
`./dev.py test` reports `ipc 1.000` for every build ever made -- the figure is
not a measurement of anything. With `-icount shift=N` the guest advances virtual
time by one instruction per tick, `minstret` becomes the **true retired
instruction count**, and `mcycle` becomes virtual nanoseconds. So instruction
counts here are real and cycle counts are a 1-IPC idealisation.

`shift=4` is 62.5 MHz, within 4% of the board's 60 MHz, so a virtual
millisecond costs about as many instructions here as a real one does there.

## What this cannot measure, and says so

IPC, I-cache misses and frontend/backend stalls come from the CPU's own
`mhpmcounter3..6` (`docs/riscv-core-build.md`), which exist on the board and not
in QEMU. The board's IPC at `opt-level = "z"` is 0.302; QEMU's is 1.0 by
construction. `scripts/soc_icache_model.py` drives a cache model from the
execution trace `--trace` collects, which is a model and not a counter.

Output is mirrored to ./tmp/logs/soc_workload.log.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CRATE = ROOT / "firmware" / "cynthion-soc"
TRIPLE = "riscv32imac-unknown-none-elf"
LOG = ROOT / "tmp" / "logs" / "soc_workload.log"

# One target dir per variant. Sharing one would make every alternation a full
# rebuild and, worse, would leave whichever variant ran last at the path the
# next reader picks up -- the argument `scripts/soc_test.py` already makes for
# keeping the QEMU build out of the crate's own `target/`.
BUILDS = ROOT / "tmp" / "workload-builds"

# See the module docstring. 4 is 62.5 MHz.
SHIFT = 4

# Wall-clock budgets. The guest's own time is virtual and deterministic; these
# bound how long the harness waits on the host, and expiry is reported as the
# real result it is (the guest never printed) rather than retried.
BOOT_S = 20.0
RUN_S = 900.0

# Events per run.
#
# 4,000 arrive per virtual second, so 2,000 events is half a virtual second --
# long enough for ~100 assertions of the 5 ms deferred job, which is what the
# worst-case latency figure needs to be a maximum over rather than a sample.
EVENTS = 2000

VARIANTS = {
    "shell": [],
    "workload": ["workload"],
    "preempt": ["preempt"],
}


def qemu_args(trace=None):
    args = [
        "qemu-system-riscv32", "-M", "virt", "-cpu", "rv32", "-m", "64M",
        "-display", "none", "-monitor", "none", "-serial", "stdio",
        "-bios", "none", "-icount", f"shift={SHIFT},align=off,sleep=off",
        # The goldfish RTC's alarm, which is the workload's deferral source,
        # arms a timer on `rtc_clock`. That defaults to QEMU_CLOCK_HOST, so a
        # 5 ms alarm fires 5 ms of WALL time later -- and under `-icount
        # sleep=off` virtual time runs ahead of wall time by whatever TCG
        # manages, which measured here as an effective 8.5-9.8 ms instead of
        # 5 ms, varying run to run. `clock=vm` puts it on virtual time, which
        # is the only way the two models get the same schedule.
        "-rtc", "clock=vm",
    ]
    if trace is not None:
        args += ["-d", "in_asm,exec,nochain", "-D", str(trace)]
    return args


class Session:
    """One QEMU run, drained by a thread. Same shape as soc_test.py's."""

    def __init__(self, elf, trace=None):
        self.proc = subprocess.Popen(
            [*qemu_args(trace), "-kernel", str(elf)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        self.buf = bytearray()
        self.lock = threading.Lock()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                return
            with self.lock:
                self.buf.extend(chunk)

    def snapshot(self):
        with self.lock:
            return bytes(self.buf)

    def send(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def expect(self, needle, budget, since=0):
        deadline = time.monotonic() + budget
        while True:
            found = self.snapshot().find(needle, since)
            if found >= 0:
                return found
            if time.monotonic() >= deadline or self.proc.poll() is not None:
                return None
            time.sleep(0.02)

    def close(self):
        self.proc.kill()
        self.proc.wait()


def build(name, features, qemu):
    """Build one variant. Returns (elf, error-or-None)."""
    target = BUILDS / (name + ("-qemu" if qemu else "-board"))
    feature_list = list(features)
    if qemu:
        feature_list.append("qemu")
    env = dict(os.environ)
    # RUSTFLAGS rather than the target-specific variable, for the reason
    # `scripts/soc_test.py:build_firmware` gives: cargo JOINS the two and the
    # linker gets both memory maps.
    # ... and only for the QEMU build. Setting it empty for the board build
    # would REPLACE `.cargo/config.toml`'s rustflags rather than leave them, and
    # the board link needs memory.x from there.
    if qemu:
        env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    else:
        env.pop("RUSTFLAGS", None)
    cmd = ["cargo", "build", "--release", "--target", TRIPLE,
           "--target-dir", str(target)]
    if feature_list:
        cmd += ["--features", ",".join(feature_list)]
    done = subprocess.run(cmd, cwd=CRATE, env=env, capture_output=True,
                          text=True)
    elf = target / TRIPLE / "release" / "cynthion-soc"
    if done.returncode != 0:
        return elf, (done.stderr or done.stdout).strip()[-1200:]
    return elf, None


def sections(elf):
    """`.text`, `.rodata` and `.bss` sizes, from the ELF itself."""
    out = subprocess.run(["rust-objdump", "-h", str(elf)],
                         capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] in (".text", ".rodata", ".bss"):
            found[parts[1]] = int(parts[2], 16)
    return found


def run_workload(elf, events, trace=None):
    """Boot, run `usb <events>`, return the report text or None."""
    session = Session(elf, trace)
    try:
        if session.expect(b"Cynthion RISC-V SoC", BOOT_S) is None:
            return None
        mark = len(session.snapshot())
        session.send(f"usb {events}\r".encode())
        if session.expect(b"  event ", RUN_S, mark) is None:
            return None
        return session.snapshot()[mark:].decode("ascii", "replace")
    finally:
        session.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", action="store_true", help="build and size only")
    ap.add_argument("--trace", action="store_true",
                    help="take a -d exec trace of each run, for the cache model")
    ap.add_argument("--events", type=int, default=EVENTS)
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open("w")

    def say(line=""):
        print(line)
        out.write(line + "\n")
        out.flush()

    say(f"# soc_workload {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    elves = {}
    say("\n## sizes")
    say(f"{'variant':10} {'target':6} {'.text':>8} {'.rodata':>8} {'.bss':>8}")
    for name, features in VARIANTS.items():
        for qemu in (False, True):
            elf, error = build(name, features, qemu)
            if error:
                say(f"BUILD FAILED {name} {'qemu' if qemu else 'board'}:\n{error}")
                return 1
            elves[(name, qemu)] = elf
            size = sections(elf)
            say(f"{name:10} {'qemu' if qemu else 'board':6} "
                f"{size.get('.text', 0):8} {size.get('.rodata', 0):8} "
                f"{size.get('.bss', 0):8}")

    base = sections(elves[("shell", False)])
    for name in ("workload", "preempt"):
        grown = sections(elves[(name, False)])
        say(f"delta {name:>8} board .text {grown['.text'] - base['.text']:+}")
    workload = sections(elves[("workload", False)])
    preempt = sections(elves[("preempt", False)])
    say(f"the dispatcher alone, board .text "
        f"{preempt['.text'] - workload['.text']:+} "
        f"bss {preempt['.bss'] - workload['.bss']:+}")

    if args.sizes:
        return 0

    for name in ("workload", "preempt"):
        say(f"\n## {name}")
        trace = None
        if args.trace:
            trace = ROOT / "tmp" / "logs" / f"trace-{name}.log"
        started = time.monotonic()
        report = run_workload(elves[(name, True)], args.events, trace)
        if report is None:
            say("  the run never reported -- see the transcript above")
            return 1
        for line in report.splitlines():
            if line.strip() and not line.startswith(">"):
                say("  " + line.strip())
        say(f"  ({time.monotonic() - started:.1f}s wall)")
        if trace is not None and trace.exists():
            say(f"  trace {trace.stat().st_size // 1024} KiB -> "
                f"scripts/soc_icache_model.py {trace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
