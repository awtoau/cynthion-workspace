#!/usr/bin/env python3
#
# Fail if Apollo's flash or RAM budget regresses.
# SPDX-License-Identifier: BSD-3-Clause

"""
Enforces Apollo's memory budget, so a regression fails rather than being noticed.

`check.py` already prints `arm-none-eabi-size` output for the Apollo build, but as
`informational=True` -- it cannot fail, so a change that pushes flash from 89% to
99% passes the loop and is caught only when someone reads the log. On a part with
**14 KB of usable flash and 4 KB of RAM**, that is the wrong default.

## Why the numbers are what they are

**Flash: 14 KB.** The part has 16 KB with the first 2 KB reserved for the saturn-v
bootloader. Upstream's own build sits at 96.04% -- 568 bytes free -- which is why
LTO is load-bearing here rather than an optimisation: the Makefile's `-flto=auto`
reclaims 2968 bytes, more than 5x upstream's entire headroom.

**RAM: 4 KB**, of which the stack reservation is 1024 bytes. That reservation was
unmeasured until `stack_probe.c` measured it: **344 bytes at the deepest path
exercised** (a full JTAG configure), so roughly 3x margin.

## The ceilings

Set above current usage with room to work, not at it. A ceiling equal to current
usage fails on the next byte added and gets raised reflexively, which trains people
to ignore it.

The point is to catch a *step change* -- a new buffer, a disabled optimisation, a
library pulled in -- not to police normal drift.

    ./scripts/apollo_budget_check.py
    ./scripts/apollo_budget_check.py --stack-measured 344
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import arm_binutils_resolve  # noqa: E402
from devlog import emit  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
ELF = FIRMWARE / "_build" / "cynthion_d11" / "firmware.elf"

ROM_BYTES = 14 * 1024
RAM_BYTES = 4 * 1024

# Ceilings, as a fraction. Chosen above current usage (about 89.8% flash, 73.6%
# RAM) with working room, so this catches a step change rather than nagging about
# a byte.
#
# Flash at 95% leaves ~717 bytes of headroom, which is more than upstream ships
# with. Below that and the LTO discussion becomes urgent again.
ROM_CEILING = 0.95
# RAM at 85% leaves ~614 bytes. The stack is the thing that would consume it, and
# it is measured at 344 of a 1024-byte reservation, so this has real margin.
RAM_CEILING = 0.85


def sections(elf):
    output = subprocess.run([arm_binutils_resolve.tool("size"), "-A", str(elf)],
                            capture_output=True, text=True).stdout
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("."):
            try:
                found[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return found


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--elf", type=Path, default=ELF)
    parser.add_argument("--stack-measured", type=int, default=None,
                        help="a measured high-water mark, to report against the "
                             "reservation")
    args = parser.parse_args()

    if not arm_binutils_resolve.tool("size"):
        print("no arm-none-eabi-size anywhere -- skipping")
        return 0
    if not args.elf.exists():
        print(f"no ELF at {args.elf} -- build first")
        return 1

    # Which `size` produced these numbers, and whether PATH would have lied.
    arm_binutils_resolve.report(emit, "size")
    emit()

    found = sections(args.elf)
    text = found.get(".text", 0)
    data = found.get(".data", 0)
    bss = found.get(".bss", 0)
    stack = found.get(".stack", 0)

    rom = text + data
    ram = data + bss + stack
    rom_pct = 100 * rom / ROM_BYTES
    ram_pct = 100 * ram / RAM_BYTES

    failures = []

    verdict = "ok"
    if rom_pct > ROM_CEILING * 100:
        verdict = f"OVER {ROM_CEILING * 100:.0f}% CEILING"
        failures.append(f"flash at {rom_pct:.2f}%")
    emit(f"  flash  {rom:>6} / {ROM_BYTES}  {rom_pct:>6.2f}%  "
         f"(ceiling {ROM_CEILING * 100:.0f}%)  {verdict}")

    verdict = "ok"
    if ram_pct > RAM_CEILING * 100:
        verdict = f"OVER {RAM_CEILING * 100:.0f}% CEILING"
        failures.append(f"RAM at {ram_pct:.2f}%")
    emit(f"  RAM    {ram:>6} / {RAM_BYTES}  {ram_pct:>6.2f}%  "
         f"(ceiling {RAM_CEILING * 100:.0f}%)  {verdict}")
    emit(f"         .text {text}  .data {data}  .bss {bss}  "
         f".stack {stack}")

    if args.stack_measured is not None:
        if args.stack_measured >= stack:
            emit(f"  stack  MEASURED {args.stack_measured} >= reservation "
                 f"{stack} -- OVERFLOW")
            failures.append(f"stack high-water {args.stack_measured} "
                            f"reaches the {stack}-byte reservation")
        else:
            headroom = stack - args.stack_measured
            emit(f"  stack  measured {args.stack_measured} of {stack}, "
                 f"{headroom} unused "
                 f"({stack / args.stack_measured:.1f}x margin)")

    emit()
    if failures:
        emit("BUDGET REGRESSION:")
        for failure in failures:
            emit(f"  - {failure}")
        emit()
        emit("A step change rather than drift is what this catches: a new")
        emit("buffer, a disabled optimisation, or a newly linked library.")
        emit("Raising the ceiling is the wrong first response -- check what")
        emit("grew, with scripts/apollo_memory_report.py.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
