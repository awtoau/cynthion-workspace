#!/usr/bin/env python3
#
# What RTIC costs once real work is in the tasks. See awtoau/cynthion-workspace#115.
# SPDX-License-Identifier: BSD-3-Clause

"""Build both USB spikes and report what the dispatcher costs.

    python3 scripts/soc_usb_probe.py

`docs/rtic.md` and `docs/rtic.md` both measured RTIC
against a skeleton whose tasks increment a counter. The objection to that
measurement is fair and was made on #115: an idle skeleton says nothing about a
runtime resident on the dispatch path of a real workload.

So both binaries here run moondancer's ported control-transfer path -- the same
`smolusb` state machine, the same descriptor tables, the same endpoint stand-in,
the same event queue, the same work per event. The only difference is who
dispatches: an `#[rtic::app]` hardware task, or a superloop. The difference
between the two `.text` figures is therefore RTIC and nothing else, now measured
with a real workload underneath it rather than a counter.

`scripts/soc_usb_test.py` is the other half: it asserts the two behave
identically, which is what makes a size comparison between them mean anything.

Not in `./dev.py gate`, for the reason `scripts/rtic_probe.py` is not: the
`rtic` feature fetches a dependency graph a default build never sees.

Writes ./tmp/logs/soc_usb_probe.log as well as stdout.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "firmware/cynthion-soc"
TARGET = CRATE / "target/riscv32imac-unknown-none-elf/release"
LOG = ROOT / "tmp/logs/soc_usb_probe.log"

# (label, cargo features, binary, is the pair's RTIC half)
#
# The board link and the QEMU link are both reported, because they differ: the
# QEMU build swaps the `#[cfg]` branches in src/target.rs, and a cost claimed for
# one target that does not hold on the other is worth seeing.
BUILDS = [
    ("usb, superloop", ["usbport"], "usb-bare"),
    ("usb, RTIC", ["rtic"], "usb-rtic"),
    ("usb, superloop (qemu)", ["usbport", "qemu"], "usb-bare"),
    ("usb, RTIC (qemu)", ["rtic", "qemu"], "usb-rtic"),
    # The counter-only skeletons, for the comparison this probe exists to
    # correct.
    ("skeleton, cooperative", ["models"], "model-coop"),
    ("skeleton, RTIC", ["rtic"], "cynthion-soc-rtic"),
    # The shell, so the I-cache arithmetic has its denominator.
    ("shell", [], "cynthion-soc"),
]

# Pairs to difference: (label, rtic build, non-rtic build, what it isolates)
PAIRS = [
    ("with moondancer's control path in the tasks",
     "usb, RTIC", "usb, superloop"),
    ("with a counter in the tasks",
     "skeleton, RTIC", "skeleton, cooperative"),
]

# The QEMU builds link against `virt`'s map. Passing the wrong script links
# `.text` into a window that machine has no memory at.
LINKER = {False: "memory.x", True: "memory-qemu.x"}

# 4 KiB, direct-mapped, one way. The budget that decides this question --
# see the `opt-level` table in firmware/cynthion-soc/Cargo.toml.
ICACHE = 4096


def size_of(elf):
    """Allocated section sizes, keyed by name."""
    tool = shutil.which("llvm-size") or shutil.which("size")
    if tool is None:
        raise SystemExit("neither llvm-size nor size is on PATH")
    out = subprocess.run([tool, "-A", elf.as_posix()],
                         capture_output=True, text=True, check=True).stdout
    sizes = {}
    for line in out.splitlines():
        match = re.match(r"^(\.\S+)\s+(\d+)\s+\d+", line)
        if match:
            sizes[match.group(1)] = int(match.group(2))
    return sizes


def main():
    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    results = {}
    failed = []

    for label, features, binary in BUILDS:
        qemu = "qemu" in features
        argv = ["cargo", "build", "--release", "--bin", binary]
        if features:
            argv += ["--features", ",".join(features)]
        env = {"RUSTFLAGS": f"-C link-arg=-T{LINKER[qemu]} -C link-arg=-Tlink.x"}
        proc = subprocess.run(argv, cwd=CRATE, capture_output=True, text=True,
                              env={**os.environ, **env})
        if proc.returncode != 0:
            emit(f"  FAIL: {label} did not build")
            emit((proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "")
            failed.append(label)
            continue
        results[label] = size_of(TARGET / binary)
        emit(f"  ok: {label}")

    emit()
    emit(f"{'build':<24} {'.text':>8} {'.rodata':>8} {'.bss':>8} "
         f"{'.uninit':>8} {'RAM':>8}")
    emit("-" * 70)
    for label, _f, _b in BUILDS:
        sizes = results.get(label)
        if sizes is None:
            emit(f"{label:<24} {'--':>8} {'--':>8} {'--':>8} "
                 f"{'--':>8} {'--':>8}")
            continue
        ram = sizes.get(".bss", 0) + sizes.get(".uninit", 0)
        emit(f"{label:<24} {sizes.get('.text', 0):>8} "
             f"{sizes.get('.rodata', 0):>8} {sizes.get('.bss', 0):>8} "
             f"{sizes.get('.uninit', 0):>8} {ram:>8}")

    emit()
    emit("`.uninit` is not a rounding detail: RTIC puts every `#[shared]` and")
    emit("`#[local]` resource there rather than in `.bss`, because `#[init]`'s")
    emit("return value initialises them and zeroing them first would be waste.")
    emit("The superloop keeps the same values as locals in `main`, which is the")
    emit("stack instead. So the RAM column is the honest comparison and `.bss`")
    emit("alone understates RTIC by the size of whatever the resources hold.")

    emit()
    emit("what the dispatcher costs, by difference:")
    emit()
    emit(f"{'workload':<44} {'.text':>8} {'RAM':>8} {'of I-cache':>11}")
    emit("-" * 75)
    for what, rtic_label, plain_label in PAIRS:
        rtic, plain = results.get(rtic_label), results.get(plain_label)
        if rtic is None or plain is None:
            emit(f"{what:<44} {'--':>8} {'--':>8} {'--':>11}")
            continue
        text = rtic.get(".text", 0) - plain.get(".text", 0)
        ram = ((rtic.get(".bss", 0) + rtic.get(".uninit", 0))
               - (plain.get(".bss", 0) + plain.get(".uninit", 0)))
        emit(f"{what:<44} {text:>+8} {ram:>+8} "
             f"{100.0 * text / ICACHE:>10.1f}%")

    shell = results.get("shell")
    usb_rtic = results.get("usb, RTIC")
    if shell and usb_rtic:
        emit()
        emit(f"for scale: the shell is {shell.get('.text', 0):,} bytes of "
             f".text against a {ICACHE // 1024} KiB direct-mapped I-cache, so "
             f"it already misses constantly.")

    emit()
    if failed:
        emit(f"RESULT: FAIL - {', '.join(failed)}")
        rc = 1
    else:
        emit(f"RESULT: PASS - all {len(BUILDS)} builds link")
        rc = 0

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
