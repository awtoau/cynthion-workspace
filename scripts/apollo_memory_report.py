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
initialised data, `.bss`, and a fixed `.stack` reservation, all known at link
time.

## Totals come from `apollo_budget_check.py`

Not recomputed here. Both scripts reported the same wrong figures for the same
reason (#199) -- summing sections by *name*, on a link whose initialised data
sits in a section called `.relocate`. One accounting, counted by address, used by
both.

Symbols are attributed the same way: an address is looked up in the section
table, rather than trusting `nm`'s letter. `nm --size-sort` is deliberately not
used -- it invents a size for linker-script symbols that have none, which put
`_etext` in this report at 536,855,336 bytes and pushed `.bss` past 100% of
itself.

## What it will not tell you

**The stack high-water mark.** `.stack` is a *reservation*; the measurement comes
from `stack_probe.c` on hardware (#74) and is passed to `apollo_budget_check.py`
with `--stack-measured`. This part has no MPU, so an overflow corrupts `.bss`
silently rather than faulting.

**Worst-case stack depth statically.** `-fstack-usage` looks like the answer and
is not: the firmware is built `-flto=auto -flto-partition=one`, so LTO inlines
across translation units and per-function frame sizes stop matching the frames in
the final binary. The numbers come out individually plausible and collectively
wrong. Disabling LTO to get them is worse -- LTO is load-bearing for the flash
budget, see #73.

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

import apollo_budget_check  # noqa: E402
import arm_binutils_resolve  # noqa: E402
from devlog import emit  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"

# Anything at or above this is worth a second look on a 4 KB part: it is 1/64th
# of all RAM in a single object.
NOTABLE_BYTES = 64


def symbols(elf, sections):
    """(name, size, kind, section) per sized object, from `nm -S`.

    Attributed to a section by address. The linkage letter is what LTO produced,
    not what the source said -- a lowercase 'b' means file-local *in the final
    binary*, which LTO may have demoted from a global declaration.
    """
    output = subprocess.run([arm_binutils_resolve.tool("nm"), "-S", str(elf)],
                            capture_output=True, text=True).stdout
    found = []
    for line in output.splitlines():
        match = re.match(r"^([0-9a-f]+)\s+([0-9a-f]+)\s+(\S)\s+(\S+)", line)
        if not match:
            continue
        addr, size, kind, name = match.groups()
        addr, size = int(addr, 16), int(size, 16)
        region = next((s["name"] for s in sections
                       if s["vma"] <= addr < s["vma"] + s["size"]), None)
        found.append((name, size, kind, region))
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

    try:
        book = apollo_budget_check.account(elf)
    except (FileNotFoundError, ValueError) as problem:
        print(f"cannot account for {elf}: {problem}")
        return 1

    rom_bytes, ram_bytes = book["rom"][1], book["ram"][1]
    rom_used, ram_used = book["rom_used"], book["ram_used"]
    bss = next((s["size"] for s in book["sections"] if s["name"] == ".bss"), 0)
    stack = next((s["size"] for s in book["sections"] if s["name"] == ".stack"), 0)

    emit(f"  flash  {bar(rom_used, rom_bytes)} {rom_used:>6} / {rom_bytes} "
         f"= {100 * rom_used / rom_bytes:.2f}%")
    emit("         " + "  ".join(f"{s['name']} {s['size']}"
                                 for s in book["flash"]))
    emit()
    emit(f"  RAM    {bar(ram_used, ram_bytes)} {ram_used:>6} / {ram_bytes} "
         f"= {100 * ram_used / ram_bytes:.2f}%")
    emit("         " + "  ".join(
        f"{s['name']} {s['size']}" + (" (reservation)" if s["name"] == ".stack"
                                      else "")
        for s in book["inram"]))
    emit()

    free = ram_bytes - ram_used
    emit(f"  {free} bytes unallocated, above a {stack}-byte stack reservation "
         f"measured at 344 (#74).")
    emit("  This part has no MPU, so an overflow corrupts .bss silently "
         "instead of faulting.")
    emit()

    objects = symbols(elf, book["sections"])
    ram_sections = [s["name"] for s in book["inram"]]

    for region in ram_sections + [s["name"] for s in book["flash"]
                                  if s["name"] not in ram_sections]:
        chosen = sorted((o for o in objects if o[3] == region),
                        key=lambda o: -o[1])
        if not chosen:
            continue
        where = "RAM" if region in ram_sections else "flash"
        emit(f"  largest in {where} ({region}):")
        for name, size, kind, _ in chosen[:args.top]:
            linkage = "global" if kind.isupper() else "local"
            flag = "  <-- notable" if (where == "RAM"
                                       and size >= NOTABLE_BYTES) else ""
            emit(f"    {size:>6}  {linkage:<6} {name[:44]}{flag}")
        emit()

    # Optimisation candidates, stated as questions rather than conclusions:
    # whether two buffers can share depends on exclusivity, which a symbol
    # table cannot show.
    big = [(n, s) for n, s, _, r in objects
           if r == ".bss" and s >= NOTABLE_BYTES]
    if big and bss:
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
