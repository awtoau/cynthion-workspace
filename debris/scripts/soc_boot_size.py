#!/usr/bin/env python3
#
# Measure the resident bootloader, and what each size setting is actually worth.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures `firmware/cynthion-boot` and reports what every size knob buys.

    ./scripts/soc_boot_size.py            # the table, plus the symbol listing
    ./scripts/soc_boot_size.py --quick    # the committed profile only

## Why a script rather than a note

The bootloader is resident: it is the thing that recovers a board, so its size is a
budget rather than a statistic, and `firmware/cynthion-boot/memory.x` asserts against
it at link time. A number written into a comment is true on the day it is written.

The ablations matter more than the total. A setting that is on because someone believed
in it, rather than because it was measured, is a setting the next person re-applies on
faith somewhere it costs something -- so a row worth 0 bytes is as useful an answer here
as a row worth 400.

## What is measured

`.text` + `.rodata` + `.data` + `.bss`: what occupies block RAM. NOT the ELF on disk.
`debug = true` adds ~12 KB of DWARF to the file and nothing at all to the image, and
measuring the file would argue for dropping the debug info a person actually reads.

Each variant is linked against a measuring script in `tmp/` with a deliberately roomy
BOOT region, so a variant that overflows the real 1 KiB still reports a number instead
of a link error.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_boot_size.log"
CRATE = ROOT / "firmware" / "cynthion-boot"
MEMORY_X = CRATE / "memory.x"
MEASURE_X = ROOT / "tmp" / "soc_boot_size" / "measure.x"
ELF = (CRATE / "target" / "riscv32imac-unknown-none-elf" / "release"
       / "cynthion-boot")

OBJCOPY_PREFIX = "riscv64-linux-gnu-"

# The committed profile, and the variants each row turns off.
#
# `panic` is not a row: `panic = "abort"` is the only setting this target accepts --
# riscv32imac-unknown-none-elf declares `panic-strategy = "abort"` and rustc rejects
# `panic = "unwind"` for it -- so there is nothing to compare it against.
BASE = {
    "OPT_LEVEL": "z",
    "LTO": "true",
    "CODEGEN_UNITS": "1",
    "DEBUG": "true",
}

VARIANTS = [
    ("committed", {}),
    ("opt-level = \"s\"", {"OPT_LEVEL": "s"}),
    ("opt-level = 3", {"OPT_LEVEL": "3"}),
    ("lto = \"fat\"", {"LTO": "fat"}),
    ("lto = false", {"LTO": "false"}),
    ("codegen-units = 16", {"CODEGEN_UNITS": "16"}),
    ("debug = false", {"DEBUG": "false"}),
]

# Sections that occupy block RAM. `.start` is this crate's own reset-vector section.
IMAGE_SECTIONS = (".start", ".text", ".rodata", ".data", ".bss")


def measuring_script():
    """The real linker script with a roomy BOOT region, written into tmp/."""
    text = MEMORY_X.read_text()
    text = text.replace("BOOT  : ORIGIN = 0x00000000, LENGTH = 1K",
                        "BOOT  : ORIGIN = 0x00000000, LENGTH = 8K")
    text = text.replace("IMAGE : ORIGIN = 0x00000400, LENGTH = 63K",
                        "IMAGE : ORIGIN = 0x00002000, LENGTH = 56K")
    MEASURE_X.parent.mkdir(parents=True, exist_ok=True)
    MEASURE_X.write_text(text)
    return MEASURE_X


def build(profile, script):
    """Build one variant. Returns (ok, output)."""
    env = dict(os.environ)
    for key, value in profile.items():
        env[f"CARGO_PROFILE_RELEASE_{key}"] = value
    # `RUSTFLAGS`, not `CARGO_TARGET_<TRIPLE>_RUSTFLAGS`. The latter is ADDED to the
    # crate's `.cargo/config.toml` rustflags rather than replacing them, so both linker
    # scripts reach lld and it refuses with "region 'BOOT' already defined". `RUSTFLAGS`
    # takes precedence over the config and is the only one that replaces.
    env["RUSTFLAGS"] = f"-C link-arg=-T{script}"
    result = subprocess.run(["cargo", "build", "--release"], cwd=CRATE, env=env,
                            capture_output=True, text=True)
    return result.returncode == 0, (result.stderr or result.stdout)


def sections(elf):
    """Section sizes, as a dict. `size -A` rather than `size`, which lumps .bss in."""
    out = subprocess.run([f"{OBJCOPY_PREFIX}size", "-A", str(elf)],
                         capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("."):
            try:
                found[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return found


def image_bytes(found):
    return sum(found.get(name, 0) for name in IMAGE_SECTIONS)


def symbols(elf):
    """Defined symbols, largest first."""
    out = subprocess.run(
        [f"{OBJCOPY_PREFIX}nm", "--size-sort", "--print-size", "--defined-only",
         "--demangle", str(elf)], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            rows.append((int(parts[1], 16), parts[3]))
    rows.sort(reverse=True)
    return rows


def formatting_symbols(elf):
    """Any sign that core::fmt survived. Empty is the answer we want."""
    out = subprocess.run([f"{OBJCOPY_PREFIX}nm", "--demangle", str(elf)],
                         capture_output=True, text=True).stdout
    pattern = re.compile(r"core::fmt|Formatter|Display|Debug|panic_fmt|"
                         r"format_args|write_str", re.I)
    return [line for line in out.splitlines() if pattern.search(line)]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true",
                        help="measure the committed profile only")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        script = measuring_script()
        variants = VARIANTS[:1] if args.quick else VARIANTS

        emit(f"Measuring {CRATE.relative_to(ROOT)}")
        emit(f"linked against {MEASURE_X.relative_to(ROOT)} (roomy BOOT, so an "
             f"oversized variant still reports)")
        emit()
        emit(f"  {'variant':<22} {'.text':>7} {'.rodata':>8} {'.data':>7} "
             f"{'.bss':>6} {'image':>7}  vs committed")
        emit(f"  {'-' * 22} {'-' * 7} {'-' * 8} {'-' * 7} {'-' * 6} {'-' * 7}  "
             f"{'-' * 11}")

        baseline = None
        for name, override in variants:
            profile = dict(BASE)
            profile.update(override)
            ok, output = build(profile, script)
            if not ok:
                emit(f"  {name:<22} BUILD FAILED")
                for line in output.strip().splitlines()[-4:]:
                    emit(f"      {line}")
                continue

            found = sections(ELF)
            total = image_bytes(found)
            if baseline is None:
                baseline = total
            delta = total - baseline
            change = "baseline" if delta == 0 and name == "committed" else \
                     ("same" if delta == 0 else f"{delta:+d} bytes")
            emit(f"  {name:<22} "
                 f"{found.get('.text', 0) + found.get('.start', 0):>7} "
                 f"{found.get('.rodata', 0):>8} {found.get('.data', 0):>7} "
                 f"{found.get('.bss', 0):>6} {total:>7}  {change}")

        # Back to the committed profile, so the tree is not left holding a variant.
        build(BASE, script)

        emit()
        emit("Largest symbols, committed profile:")
        for size, name in symbols(ELF):
            emit(f"  {size:>5}  {name}")

        emit()
        stragglers = formatting_symbols(ELF)
        if stragglers:
            emit("*** core::fmt SURVIVED -- something formats:")
            for line in stragglers:
                emit(f"  {line}")
        else:
            emit("core::fmt: no formatting symbols in the image, as intended")

        # And finally the real thing, linked against the real script, so the number
        # reported last is the number that ships.
        env = dict(os.environ)
        subprocess.run(["cargo", "build", "--release"], cwd=CRATE, env=env,
                       capture_output=True, text=True)
        found = sections(ELF)
        emit()
        emit(f"Shipped image (linked against {MEMORY_X.relative_to(ROOT)}): "
             f"{image_bytes(found)} bytes")
        emit(f"  log: {LOG.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
