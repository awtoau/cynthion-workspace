#!/usr/bin/env python3
"""Build the RTIC skeleton and report what RTIC costs on this machine.

`docs/architecture.md` decision 19 has said for months that RTIC fits this SoC
"blocked by: nothing known", on the strength of the PLIC being a standard one.
Nobody had compiled it. This does, for both targets, so the claim is a build
result rather than a reading of the register map.

It is NOT in `./dev.py gate`. The `rtic` feature pulls sixteen packages that a
default build never fetches, and a gate that needs the network to pass is a gate
that fails on the first flight without wifi. Run it when the RTIC work moves.

What the numbers mean: the skeleton is two tasks and a PLIC front end, so its
`.text` is the RTIC runtime plus almost nothing. Weigh it against the shell's
own `.text` and the 4 KiB direct-mapped I-cache -- see the `opt-level` table in
`firmware/cynthion-soc/Cargo.toml` for why code size here is a speed question
and not a space one.

Writes ./tmp/logs/rtic_probe.log as well as stdout.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
CRATE = WORKSPACE / "firmware/cynthion-soc"
TARGET = CRATE / "target/riscv32imac-unknown-none-elf/release"
LOG = WORKSPACE / "tmp/logs/rtic_probe.log"

# (label, cargo features, binary). The shell is built too, unchanged, because
# the point of `required-features` is that turning RTIC on does not touch it --
# and that is only worth asserting if both are built the same way.
BUILDS = [
    ("shell", [], "cynthion-soc"),
    ("shell (qemu)", ["qemu"], "cynthion-soc"),
    ("rtic skeleton", ["rtic"], "cynthion-soc-rtic"),
    ("rtic skeleton (qemu)", ["rtic", "qemu"], "cynthion-soc-rtic"),
]

# The QEMU builds link against `virt`'s memory map, not the board's. Passing the
# wrong one links `.text` into a window that machine has no memory at, which
# scripts/soc_test.py explains at length.
LINKER = {False: "memory.x", True: "memory-qemu.x"}


def size_of(elf: Path) -> dict[str, int]:
    """Allocated section sizes, keyed by name."""
    tool = shutil.which("llvm-size") or shutil.which("size")
    if tool is None:
        raise SystemExit("neither llvm-size nor size is on PATH")
    out = subprocess.run([tool, "-A", elf.as_posix()],
                         capture_output=True, text=True, check=True).stdout
    sizes = {}
    for line in out.splitlines():
        m = re.match(r"^(\.\S+)\s+(\d+)\s+\d+", line)
        if m:
            sizes[m.group(1)] = int(m.group(2))
    return sizes


def main() -> int:
    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    results: dict[str, dict[str, int]] = {}
    failed: list[str] = []

    for label, features, binary in BUILDS:
        qemu = "qemu" in features
        argv = ["cargo", "build", "--release", "--bin", binary]
        if features:
            argv += ["--features", ",".join(features)]
        emit(f"$ {' '.join(argv)}   [-T{LINKER[qemu]}]")

        env = {"RUSTFLAGS": f"-C link-arg=-T{LINKER[qemu]} -C link-arg=-Tlink.x"}
        proc = subprocess.run(argv, cwd=CRATE, capture_output=True, text=True,
                              env={**os.environ, **env})
        if proc.returncode != 0:
            emit(proc.stderr.strip().splitlines()[-1] if proc.stderr else "")
            emit(f"  FAIL: {label} did not build")
            failed.append(label)
            continue
        results[label] = size_of(TARGET / binary)
        emit(f"  ok: {label}")

    emit()
    emit(f"{'build':<24} {'.text':>8} {'.rodata':>8} {'.bss':>8}")
    emit("-" * 52)
    for label, _f, _b in BUILDS:
        s = results.get(label)
        if s is None:
            emit(f"{label:<24} {'--':>8} {'--':>8} {'--':>8}")
            continue
        emit(f"{label:<24} {s.get('.text', 0):>8} "
             f"{s.get('.rodata', 0):>8} {s.get('.bss', 0):>8}")

    if "shell" in results and "rtic skeleton" in results:
        emit()
        shell, rtic = results["shell"], results["rtic skeleton"]
        share = 100.0 * rtic.get(".text", 0) / max(shell.get(".text", 1), 1)
        emit(f"the skeleton's .text is {share:.1f}% of the shell's, "
             f"against a 4 KiB I-cache")

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
