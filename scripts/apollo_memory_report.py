#!/usr/bin/env python3
#
# Where Apollo's 4 KB of RAM and 14 KB of flash actually go.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reports Apollo's memory use per object, and flags what is worth optimising.

The d11 target is 4 KB of RAM and 14 KB of usable flash, and both are tight
enough that a 256-byte buffer is a policy decision. This makes the current state
readable instead of requiring three `arm-none-eabi-*` invocations and arithmetic.

**It is measurable at all because Apollo links no heap.** There is no `malloc`,
`free`, `_sbrk` or `.heap` section in the binary, so RAM is entirely static:
`.data` + `.bss` + a fixed `.stack` reservation, all known at link time. Nothing
here is an estimate except the stack headroom, which is called out as such.

## What it will not tell you

**The stack high-water mark.** `.stack` is a 1024-byte *reservation*, not a
measurement, and nothing here can see how much of it is used -- that needs the
region filled with a pattern at reset and read back after exercising the deep
paths (issue #74). Until then, free RAM is headroom over an unmeasured figure
rather than spare capacity, which is why "give the leftover to a buffer" is
unsafe.

**Worst-case stack depth statically.** `-fstack-usage` looks like the answer and
is not: the firmware is built `-flto=auto -flto-partition=one`, so LTO inlines
across translation units and per-function frame sizes stop matching the frames in
the final binary. The numbers come out individually plausible and collectively
wrong. Disabling LTO to get them is worse -- it reclaims 2968 bytes on a part that
is otherwise 568 bytes from its ceiling.

    ./scripts/apollo_memory_report.py
    ./scripts/apollo_memory_report.py --board cynthion --top 20
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import arm_binutils_resolve  # noqa: E402
from devlog import emit  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"

# From the d11 linker script. Flash is 16 KB total with the first 2 KB reserved
# for the saturn-v bootloader, leaving 14 KB for the application.
ROM_BYTES = 14 * 1024
RAM_BYTES = 4 * 1024

# Anything at or above this is worth a second look on a 4 KB part: it is 1/64th
# of all RAM in a single object.
NOTABLE_BYTES = 64


def sections(elf):
    """Section sizes, from `size -A`."""
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


def symbols(elf):
    """(name, size, kind, section) per object, from `nm -S`.

    Note the linkage letter is what LTO produced, not what the source said. A
    lowercase 'b' means file-local *in the final binary*; the declaration may
    well be global, with LTO having demoted it because nothing outside the
    translation unit turned out to use it. Reading linkage off this table alone
    misreports the source, which is worth knowing before concluding anything
    about who can see what.
    """
    output = subprocess.run([arm_binutils_resolve.tool("nm"), "-S", "--size-sort", str(elf)],
                            capture_output=True, text=True).stdout
    found = []
    for line in output.splitlines():
        match = re.match(r"^([0-9a-f]+)\s+([0-9a-f]+)\s+(\S)\s+(\S+)", line)
        if not match:
            continue
        _addr, size, kind, name = match.groups()
        region = {"b": ".bss", "B": ".bss", "d": ".data", "D": ".data",
                  "t": ".text", "T": ".text", "r": ".rodata",
                  "R": ".rodata"}.get(kind)
        found.append((name, int(size, 16), kind, region))
    return found


def bar(used, total, width=32):
    filled = min(width, int(width * used / total)) if total else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", default="cynthion")
    parser.add_argument("--top", type=int, default=15,
                        help="how many objects to list per region")
    parser.add_argument("--elf", type=Path,
                        help="an ELF to report on instead of building")
    args = parser.parse_args()

    if not arm_binutils_resolve.tool("size") or not arm_binutils_resolve.tool("nm"):
        print("no arm-none-eabi binutils found")
        return 1

    elf = args.elf
    if elf is None:
        build = APOLLO / "firmware"
        result = subprocess.run(["make", f"APOLLO_BOARD={args.board}"],
                                cwd=build, capture_output=True, text=True)
        if result.returncode != 0:
            print("firmware build failed:")
            print((result.stderr or result.stdout)[-600:])
            return 1
        elf = build / "_build" / f"{args.board}_d11" / "firmware.elf"
        if not elf.exists():
            # Board name and build directory do not always match.
            candidates = list((build / "_build").glob("*/firmware.elf"))
            if not candidates:
                print("built, but no firmware.elf found")
                return 1
            elf = candidates[0]

    emit(f"Apollo memory report: {elf.relative_to(ROOT) if ROOT in elf.parents else elf}")
    emit()
    arm_binutils_resolve.report(emit, "size", "nm")
    emit()

    found = sections(elf)
    text = found.get(".text", 0)
    data = found.get(".data", 0)
    bss = found.get(".bss", 0)
    stack = found.get(".stack", 0)

    rom_used = text + data
    ram_used = data + bss + stack

    emit(f"  flash  {bar(rom_used, ROM_BYTES)} {rom_used:>6} / {ROM_BYTES} "
         f"= {100 * rom_used / ROM_BYTES:.2f}%")
    emit(f"         .text {text}  .data {data}")
    emit()
    emit(f"  RAM    {bar(ram_used, RAM_BYTES)} {ram_used:>6} / {RAM_BYTES} "
         f"= {100 * ram_used / RAM_BYTES:.2f}%")
    emit(f"         .data {data}  .bss {bss}  .stack {stack} (reservation)")
    emit()

    free = RAM_BYTES - ram_used
    emit(f"  {free} bytes unallocated, against a {stack}-byte stack "
         f"reservation that is UNMEASURED (#74).")
    emit("  That is headroom over a guess, not spare capacity -- and this "
         "part has no MPU,")
    emit("  so an overflow corrupts .bss silently instead of faulting.")
    emit()

    objects = symbols(elf)

    for region, label in ((".bss", "RAM (.bss)"), (".data", "RAM (.data)"),
                          (".text", "flash (.text)")):
        chosen = [o for o in objects if o[3] == region]
        chosen.sort(key=lambda o: -o[1])
        if not chosen:
            continue
        emit(f"  largest in {label}:")
        for name, size, kind, _ in chosen[:args.top]:
            linkage = "global" if kind.isupper() else "local"
            flag = "  <-- notable" if (region != ".text"
                                       and size >= NOTABLE_BYTES) else ""
            emit(f"    {size:>6}  {linkage:<6} {name[:44]}{flag}")
        emit()

    # Optimisation candidates, stated as questions rather than conclusions:
    # whether two buffers can share depends on exclusivity, which a symbol
    # table cannot show.
    big = [(n, s) for n, s, _, r in objects
           if r == ".bss" and s >= NOTABLE_BYTES]
    if big:
        total = sum(s for _, s in big)
        emit(f"  {len(big)} .bss objects of {NOTABLE_BYTES}+ bytes account "
             f"for {total} of {bss} ({100 * total / bss:.0f}%).")
        emit("  Worth asking of each: is it live at the same time as the "
             "others? Two buffers")
        emit("  that are provably exclusive can share one allocation. See "
             "#103 and #63.")
        emit()


    return 0


if __name__ == "__main__":
    sys.exit(main())
