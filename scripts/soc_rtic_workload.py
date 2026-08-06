#!/usr/bin/env python3
#
# The #115 workload on all three dispatchers, including RTIC.
# SPDX-License-Identifier: BSD-3-Clause

"""RTIC against the superloop and the hand-written dispatcher, same workload.

`docs/rtic.md` §6 names this as the measurement that
would settle three of its four open rows: run the real workload against an RTIC
build instead of a skeleton that increments a counter. `scripts/soc_workload.py`
does the first two models; this adds the third and prints them side by side.

    ./scripts/soc_rtic_workload.py             # sizes, then all three runs
    ./scripts/soc_rtic_workload.py --sizes     # sizes only, no QEMU
    ./scripts/soc_rtic_workload.py --events 2000

Four builds, and the reason there are four:

    workload     the shell, superloop            control (known-good figures)
    preempt      the shell, src/dispatch.rs      control (known-good figures)
    rticcs       src/bin/workload_rtic.rs        the port, uninstrumented
    rticprobe    the same, instrumented          the anatomy, never the latency

The controls run first and their numbers are asserted against
`docs/rtic.md` §5, so a change in the harness, the
toolchain or QEMU is caught before anything is concluded about the thing under
test. `--no-control` skips that, and says so in the output.

Everything goes to `tmp/logs/dev.log` through `scripts/devlog.py`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import devlog                                          # noqa: E402
from soc_workload import Session, sections             # noqa: E402

CRATE = ROOT / "firmware" / "cynthion-soc"
TRIPLE = "riscv32imac-unknown-none-elf"
BUILDS = ROOT / "tmp" / "rtic-workload-builds"

# See soc_workload.py: 4,000 arrivals a virtual second, so 2,000 events is half
# a virtual second and ~100 assertions of the 5 ms deferred job.
EVENTS = 2000

# Wall-clock budgets for the harness, not the guest. The guest's own time is
# virtual and deterministic; these bound how long the host waits before calling
# a silent guest a failure. A 2,000-event run measures 0.3 s of wall time, so
# 20 s of boot and 900 s of run are three orders of margin and expiry means the
# guest died rather than that it was slow.
BOOT_S = 20.0
RUN_S = 900.0

# `variant -> (features, binary)`. `None` means the crate's default binary, the
# shell; the RTIC builds are their own `[[bin]]`.
VARIANTS = {
    "workload": (["workload"], None),
    "preempt": (["preempt"], None),
    "bare": (["wlbare"], "workload-bare"),
    "rtic": (["rticcs"], "workload-rtic"),
    "rtic-spin": (["rticcs", "rticspin"], "workload-rtic"),
    "rtic-probe": (["rticprobe"], "workload-rtic"),
    "rtic-spin-probe": (["rticprobe", "rticspin"], "workload-rtic"),
}

# Which banner each binary prints when it is ready for a command.
BANNER = {
    "workload": b"Cynthion RISC-V SoC",
    "preempt": b"Cynthion RISC-V SoC",
    "bare": b"workload bare",
}

# What `docs/rtic.md` §5 records for the two controls, at
# 2,000 events. Asserted before the third model is believed: a new instrument's
# first run is against the known-good configuration.
#
# `worst` and `mean` are per-event and do not scale; `missed` and `asserts` are
# counts over the run and are scaled by `events / 2000`. `mean` is allowed 2 µs
# either way -- it is a sum of per-event latencies divided by the count, and the
# last burst of a short run lands unevenly.
CONTROL = {
    "workload": {"worst": 1266, "mean": 418, "missed": 700, "asserts": 100},
    "preempt": {"worst": 271, "mean": 170, "missed": 0, "asserts": 100},
}
CONTROL_EVENTS = 2000
SCALED = ("missed", "asserts")

# `worst` and `mean` are allowed 2 µs either way.
#
# The guest is deterministic under `-icount`; what is not is WHEN the host types
# `usb <n>`, and the deferral source is a wall-grid alarm the run has to phase
# against. A run that starts a few hundred microseconds later meets its first
# 5 ms assertion at a different point in a burst. Measured across runs of the
# identical binary: worst 1265-1266, mean 418-419.
SLACK_US = 2
SLACK = ("worst", "mean")


def control_check(name: str, got: dict, events: int) -> list[str]:
    """Every way this run disagrees with the documented one."""
    wrong = []
    for key, want in CONTROL[name].items():
        if key in SCALED:
            want = want * events // CONTROL_EVENTS
        have = got.get(key)
        if key in SLACK:
            if have is None or abs(have - want) > SLACK_US:
                wrong.append(f"{key} is {have}, expected {want} "
                             f"+/-{SLACK_US}")
        elif have != want:
            wrong.append(f"{key} is {have}, expected {want}")
    return wrong


def build(name: str, features: list[str], binary: str | None, qemu: bool):
    """Build one variant. Returns (elf, error-or-None)."""
    target = BUILDS / (name + ("-qemu" if qemu else "-board"))
    wanted = list(features) + (["qemu"] if qemu else [])
    env = dict(os.environ)
    # RUSTFLAGS rather than the target-specific variable, for the reason
    # `scripts/soc_test.py:build_firmware` gives: cargo JOINS the two and the
    # linker gets both memory maps. Only for the QEMU build -- setting it empty
    # for the board build would REPLACE `.cargo/config.toml`'s rustflags.
    if qemu:
        env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    else:
        env.pop("RUSTFLAGS", None)
    cmd = ["cargo", "build", "--release", "--target", TRIPLE,
           "--target-dir", str(target), "--features", ",".join(wanted)]
    if binary is not None:
        cmd += ["--bin", binary]
    done = subprocess.run(cmd, cwd=CRATE, env=env, capture_output=True,
                          text=True)
    elf = target / TRIPLE / "release" / (binary or "cynthion-soc")
    if done.returncode != 0:
        return elf, (done.stderr or done.stdout).strip()[-1500:]
    return elf, None


def run(elf: Path, events: int, banner: bytes, trace: Path | None = None):
    """Boot, send `usb <events>`, return the report text or None."""
    session = Session(elf, trace)
    try:
        if session.expect(banner, BOOT_S) is None:
            return None
        mark = len(session.snapshot())
        session.send(f"usb {events}\r".encode())
        # "  event " is the last line of `workload::report` for every model, so
        # waiting on it means the whole report has been printed.
        if session.expect(b"  event ", RUN_S, mark) is None:
            return None
        # The RTIC build prints three more lines after it; give the drain a
        # moment to catch them. Bounded by the same `expect`, so a build that
        # never prints them is reported rather than waited on.
        session.expect(b"  tick ", 2.0, mark)
        return session.snapshot()[mark:].decode("ascii", "replace")
    finally:
        session.close()


def parse_report(text: str) -> dict:
    """The numbers out of `workload::report`, for the control assertion."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if "latency" in parts:
            found["worst"] = int(parts[parts.index("worst") + 1])
            found["mean"] = int(parts[parts.index("mean") + 1])
        elif "events" in parts and "missed" in parts:
            found["missed"] = int(parts[parts.index("missed") + 1])
            found["completed"] = int(parts[parts.index("completed") + 1])
            found["dropped"] = int(parts[parts.index("dropped") + 1])
        elif "defer" in parts:
            found["asserts"] = int(parts[parts.index("asserts") + 1])
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", action="store_true", help="build and size only")
    ap.add_argument("--events", type=int, default=EVENTS)
    ap.add_argument("--no-control", action="store_true",
                    help="skip the two control models (they are the baseline)")
    ap.add_argument("--trace", action="store_true",
                    help="take a -d exec trace of each run, for the cache model")
    args = ap.parse_args()

    devlog.emit(f"# soc_rtic_workload {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    elves = {}
    devlog.emit("\n## sizes")
    devlog.emit(f"{'variant':11} {'target':6} {'.text':>8} {'.rodata':>8} "
                f"{'.bss':>8}")
    for name, (features, binary) in VARIANTS.items():
        for qemu in (False, True):
            elf, error = build(name, features, binary, qemu)
            if error:
                devlog.log(f"build failed: {name} "
                           f"{'qemu' if qemu else 'board'}", "ERROR")
                devlog.emit(error)
                return 1
            elves[(name, qemu)] = elf
            size = sections(elf)
            devlog.emit(f"{name:11} {'qemu' if qemu else 'board':6} "
                        f"{size.get('.text', 0):8} {size.get('.rodata', 0):8} "
                        f"{size.get('.bss', 0):8}")

    if args.sizes:
        return 0

    order = list(VARIANTS)
    if args.no_control:
        order = [name for name in order if name not in CONTROL]
        devlog.emit("\n(controls skipped -- the table below has no baseline)")

    failed = False
    for name in order:
        devlog.emit(f"\n## {name}")
        banner = BANNER.get(name, b"workload on RTIC")
        trace = ROOT / "tmp" / "logs" / f"trace-{name}.log" if args.trace else None
        started = time.monotonic()
        report = run(elves[(name, True)], args.events, banner, trace)
        if report is None:
            devlog.log(f"{name}: the run never reported", "ERROR")
            return 1
        for line in report.splitlines():
            if line.strip() and not line.startswith(">"):
                devlog.emit("  " + line.strip())
        devlog.emit(f"  ({time.monotonic() - started:.1f}s wall)")

        if name in CONTROL:
            wrong = control_check(name, parse_report(report), args.events)
            for line in wrong:
                devlog.log(f"CONTROL {name}: {line} from "
                           f"docs/rtic.md §5", "ERROR")
            failed = failed or bool(wrong)
            if not wrong:
                devlog.emit("  control: reproduces "
                            "docs/rtic.md §5")
        if trace is not None and trace.exists():
            devlog.emit(f"  trace {trace.stat().st_size // 1024} KiB -> "
                        f"scripts/soc_icache_model.py {trace}")

    if failed:
        devlog.log("the controls did not reproduce; the RTIC numbers above "
                   "are not comparable to the documented ones", "ERROR")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
