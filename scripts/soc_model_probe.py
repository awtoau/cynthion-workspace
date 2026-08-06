#!/usr/bin/env python3
"""Build one skeleton per concurrency model and report what each runtime costs.

`docs/rtic.md` is the reading. This is the measurement behind
it: five skeletons that do the SAME VISIBLE WORK -- a PLIC front end, two
sources, one shared counter, an idle loop -- built by the same profile against
the same linker script, so the difference between any two `.text` figures is the
runtime and nothing else.

    model-bare           riscv-rt and nothing: the floor
    model-coop           a ready bitmap, a job table, a dispatch loop
    cynthion-soc-rtic    RTIC 2.3 on riscv-clint-backend (also scripts/rtic_probe.py)
    model-embassy        embassy-executor 0.10, platform-riscv32

and one pair that answers the hardware-timer question rather than the runtime
question -- the same three periodic jobs, scheduled two ways:

    model-coop-swqueue   three deadlines multiplexed onto the one `mtimecmp`
    model-coop-hwtimer   three FPGA comparators, one PLIC line each

The budget these spend is the 4 KiB direct-mapped I-cache, not flash and not
block RAM. See the `opt-level` table in `firmware/cynthion-soc/Cargo.toml` for
why code size on this machine is a speed question.

NOT in `./dev.py gate`: the `rtic` and `embassy` features fetch crates a default
build never touches, and a gate that needs the network fails on a flight.

Writes ./tmp/logs/soc_model_probe.log as well as stdout.
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
LOG = WORKSPACE / "tmp/logs/soc_model_probe.log"

# (label, binary, features). The shell is first so every other figure has
# something to be a fraction of.
BUILDS = [
    ("shell (the product)", "cynthion-soc", []),
    ("bare: riscv-rt only", "model-bare", ["models"]),
    ("cooperative", "model-coop", ["models"]),
    ("RTIC 2.3", "cynthion-soc-rtic", ["rtic"]),
    ("Embassy 0.10", "model-embassy", ["embassy"]),
    ("coop + 3 jobs, 1 mtimecmp", "model-coop-swqueue", ["models"]),
    ("coop + 3 jobs, 3 timers", "model-coop-hwtimer", ["models"]),
]

# What each runtime figure is measured against. `model-bare` is the floor: the
# PLIC front end, the trap vector and the two counters, with no runtime at all.
FLOOR = "bare: riscv-rt only"

# The pair whose difference is the software timer queue.
TIMER_PAIR = ("coop + 3 jobs, 3 timers", "coop + 3 jobs, 1 mtimecmp")

# The I-cache is the budget. 4 KiB, direct-mapped, one way.
ICACHE = 4096


def size_of(elf: Path) -> dict[str, int]:
    """Allocated section sizes, keyed by name."""
    tool = shutil.which("llvm-size") or shutil.which("size")
    if tool is None:
        raise SystemExit("neither llvm-size nor size is on PATH")
    out = subprocess.run(
        [tool, "-A", elf.as_posix()], capture_output=True, text=True, check=True
    ).stdout
    sizes = {}
    for line in out.splitlines():
        match = re.match(r"^(\.\S+)\s+(\d+)\s+\d+", line)
        if match:
            sizes[match.group(1)] = int(match.group(2))
    return sizes


def main() -> int:
    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    results: dict[str, dict[str, int]] = {}
    failed: list[str] = []

    # Every build links against the board's map, not `virt`'s. One target, so
    # the numbers are comparable; scripts/rtic_probe.py does both for RTIC and
    # found the QEMU figures within 3%.
    env = {"RUSTFLAGS": "-C link-arg=-Tmemory.x -C link-arg=-Tlink.x"}

    for label, binary, features in BUILDS:
        argv = ["cargo", "build", "--release", "--bin", binary]
        if features:
            argv += ["--features", ",".join(features)]
        emit(f"$ {' '.join(argv)}")
        proc = subprocess.run(
            argv, cwd=CRATE, capture_output=True, text=True, env={**os.environ, **env}
        )
        if proc.returncode != 0:
            for line in (proc.stderr or "").splitlines():
                if line.startswith("error"):
                    emit(f"  {line}")
            emit(f"  FAIL: {label} did not build")
            failed.append(label)
            continue
        results[label] = size_of(TARGET / binary)
        emit(f"  ok: {label}")

    floor = results.get(FLOOR, {}).get(".text")

    emit()
    emit(f"{'model':<28} {'.text':>8} {'.rodata':>8} {'.bss':>8} {'runtime':>9} {'cache':>7}")
    emit("-" * 74)
    for label, _binary, _features in BUILDS:
        sizes = results.get(label)
        if sizes is None:
            emit(f"{label:<28} {'--':>8}")
            continue
        text = sizes.get(".text", 0)
        runtime = "--"
        share = "--"
        if floor is not None and label not in (FLOOR, "shell (the product)"):
            runtime = f"{text - floor}"
            share = f"{100.0 * (text - floor) / ICACHE:.0f}%"
        emit(
            f"{label:<28} {text:>8} {sizes.get('.rodata', 0):>8} "
            f"{sizes.get('.bss', 0):>8} {runtime:>9} {share:>7}"
        )

    emit()
    emit("`runtime` is .text above the bare floor -- the scheduler and nothing else.")
    emit(f"`cache` is that as a fraction of the {ICACHE // 1024} KiB direct-mapped I-cache.")

    hw, sw = TIMER_PAIR
    if hw in results and sw in results:
        delta = results[sw][".text"] - results[hw][".text"]
        emit()
        emit(
            f"three hardware comparators against one mtimecmp: "
            f"{delta} bytes of .text and "
            f"{results[sw].get('.bss', 0) - results[hw].get('.bss', 0)} of .bss, "
            f"for the same three periodic jobs"
        )

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
