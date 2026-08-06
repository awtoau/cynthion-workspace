#!/usr/bin/env python3
#
# The CLINT monotonic for RTIC: does it work, what does it cost, how late is it.
# SPDX-License-Identifier: BSD-3-Clause

"""Build and run `src/bin/mono_rtic.rs` under QEMU.

Issue #115's fourth open question is "implementation of a CLINT monotonic", and
`docs/rtic.md` §9 answers it with "`rtic-monotonics` 2.2.1 has nothing
for RISC-V". That is checkable and it is wrong: the crate ships `esp32c3` and
`esp32c6` backends, both RISC-V. What it has no backend for is the **CLINT**,
which is five methods of `rtic_time::TimerQueueBackend`.

This builds the one that was missing and measures it against an absolute 5 ms
grid, including one deadline deliberately set in the past.

    ./scripts/soc_rtic_monotonic.py
    ./scripts/soc_rtic_monotonic.py --sizes

`--sizes` also prints what the dependency costs, because that is the other half
of the answer: `cargo tree` before and after.

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

# Wall-clock budgets for the harness, not the guest. The run is 100 periods of
# 5 ms of VIRTUAL time -- half a virtual second, measured at under a second of
# wall time -- so 20 s of boot and 120 s of run are two orders of margin, and
# expiry means the guest stopped rather than that it was slow.
BOOT_S = 20.0
RUN_S = 120.0


def build(qemu: bool):
    target = BUILDS / ("mono-qemu" if qemu else "mono-board")
    env = dict(os.environ)
    if qemu:
        env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    else:
        env.pop("RUSTFLAGS", None)
    features = "rticmono,qemu" if qemu else "rticmono"
    done = subprocess.run(
        ["cargo", "build", "--release", "--target", TRIPLE,
         "--target-dir", str(target), "--bin", "mono-rtic",
         "--features", features],
        cwd=CRATE, env=env, capture_output=True, text=True)
    elf = target / TRIPLE / "release" / "mono-rtic"
    if done.returncode != 0:
        return elf, (done.stderr or done.stdout).strip()[-1500:]
    return elf, None


def packages(features: str) -> int:
    """How many packages the graph has with these features on."""
    done = subprocess.run(
        ["cargo", "tree", "--target", TRIPLE, "--prefix", "none",
         "--no-dedupe", "--features", features],
        cwd=CRATE, capture_output=True, text=True)
    if done.returncode != 0:
        return -1
    return len({line.split()[0] for line in done.stdout.splitlines()
                if line.strip()})


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", action="store_true", help="build and size only")
    args = ap.parse_args()

    devlog.emit(f"# soc_rtic_monotonic {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    elves = {}
    devlog.emit("\n## sizes")
    for qemu in (False, True):
        elf, error = build(qemu)
        if error:
            devlog.log(f"build failed ({'qemu' if qemu else 'board'})", "ERROR")
            devlog.emit(error)
            return 1
        elves[qemu] = elf
        size = sections(elf)
        devlog.emit(f"mono-rtic  {'qemu' if qemu else 'board':6} "
                    f".text {size.get('.text', 0):8} "
                    f".rodata {size.get('.rodata', 0):8} "
                    f".bss {size.get('.bss', 0):8}")

    devlog.emit("\n## dependency cost")
    for name, features in (("shell", "qemu"),
                           ("+ rtic", "qemu,rticcs"),
                           ("+ rtic + monotonic", "qemu,rticmono")):
        devlog.emit(f"{name:20} {packages(features):3} packages")

    if args.sizes:
        return 0

    devlog.emit("\n## run")
    session = Session(elves[True])
    try:
        if session.expect(b"CLINT monotonic on RTIC", BOOT_S) is None:
            devlog.log("the guest never printed its banner", "ERROR")
            return 1
        mark = len(session.snapshot())
        # "  clock" is the last line the report prints.
        if session.expect(b"  clock ", RUN_S, mark) is None:
            devlog.log("the run never reported -- see the transcript", "ERROR")
            devlog.emit(session.snapshot()[mark:].decode("ascii", "replace"))
            return 1
        report = session.snapshot()[mark:].decode("ascii", "replace")
    finally:
        session.close()

    for line in report.splitlines():
        if line.strip():
            devlog.emit("  " + line.strip())

    # The two assertions that make this a test rather than a printout.
    failures = []
    for line in report.splitlines():
        parts = line.split()
        if "periods" in parts and "of" in parts:
            got = int(parts[parts.index("periods") + 1])
            want = int(parts[parts.index("of") + 1])
            if got != want:
                failures.append(f"{got} periods ran of {want}")
        if "early" in parts:
            early = int(parts[parts.index("worst") + 1])
            if early != 0:
                failures.append(f"a deadline fired {early} ticks EARLY, which "
                                f"is a defect and not jitter")
    for failure in failures:
        devlog.log(failure, "ERROR")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
