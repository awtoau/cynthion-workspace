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

## Counted by address, not by section name

Section names are what broke this before (#199). The earlier version summed
`.text + .data`, and **this link has no `.data`**: the d11 linker script routes
`*(.data .data.*)` into an output section called `.relocate`, which lives in RAM
with its initialiser in flash. 80 bytes of flash and 80 bytes of RAM were
invisible to the guard, in the direction that reassures.

So nothing here matches a name:

- **flash** is what the loader writes: `PT_LOAD` file content, placed by *load*
  address, spanning from the rom region's origin to the highest loaded byte.
- **RAM** is every `SHF_ALLOC` section whose *virtual* address lands in the ram
  region -- initialised, zeroed, or reserved alike.
- an allocated section that lands in **neither** region is a hard failure rather
  than a silent zero. That is the shape of the original defect, and it is the one
  thing a name-keyed lookup can never notice.

Two independent cross-checks, because an accounting that only agrees with itself
is what #199 was:

- the region sizes come from the **link map**, not from a constant here, so a
  linker-script change cannot leave the arithmetic behind.
- the flash total is compared against the length of `firmware.bin`, the flat
  image that is actually programmed. A disagreement fails.

## Why the numbers are what they are

**Flash: 14 KB.** The part has 16 KB with the first 2 KB reserved for the saturn-v
bootloader. Upstream's own build sits at 96.04% -- 568 bytes free -- which is why
LTO is load-bearing here rather than an optimisation: the Makefile's `-flto=auto`
reclaims 2968 bytes, more than 5x upstream's entire headroom.

**RAM: 4 KB**, of which the stack reservation is 704 bytes (`STACK_SIZE=0x2C0` in
the firmware Makefile). That reservation is measurement-derived: `stack_probe.c`
reports 344 bytes at the deepest path exercised.

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
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
ELF = FIRMWARE / "_build" / "cynthion_d11" / "firmware.elf"

# Ceilings, as a fraction of each region. See #199 for the current usage they are
# measured against and for why they sit where they do.
ROM_CEILING = 0.95
RAM_CEILING = 0.85

SHF_ALLOC = 0x2
SHT_NOBITS = 8
PT_LOAD = 1


def elf_layout(path):
    """Sections and PT_LOAD segments of a 32-bit little-endian ARM ELF.

    Parsed here rather than scraped from `size`/`readelf` output: this check is
    about addresses, and the tools print names.
    """
    blob = path.read_bytes()
    if blob[:4] != b"\x7fELF" or blob[4] != 1 or blob[5] != 1:
        raise ValueError(f"{path}: not a 32-bit little-endian ELF")

    (e_phoff, e_shoff) = struct.unpack_from("<II", blob, 28)
    (e_phentsize, e_phnum, e_shentsize, e_shnum,
     e_shstrndx) = struct.unpack_from("<HHHHH", blob, 42)

    segments = []
    for i in range(e_phnum):
        (p_type, p_offset, p_vaddr, p_paddr, p_filesz,
         p_memsz) = struct.unpack_from("<IIIIII", blob, e_phoff + i * e_phentsize)
        if p_type == PT_LOAD:
            segments.append({"vaddr": p_vaddr, "paddr": p_paddr,
                             "filesz": p_filesz, "memsz": p_memsz})

    def header(i):
        return struct.unpack_from("<IIIIIIIIII", blob, e_shoff + i * e_shentsize)

    strtab_off = header(e_shstrndx)[4]

    sections = []
    for i in range(e_shnum):
        (sh_name, sh_type, sh_flags, sh_addr, _off, sh_size,
         *_rest) = header(i)
        end = blob.index(b"\0", strtab_off + sh_name)
        name = blob[strtab_off + sh_name:end].decode()
        if not (sh_flags & SHF_ALLOC) or sh_size == 0:
            continue
        sections.append({"name": name, "vma": sh_addr, "size": sh_size,
                         "nobits": sh_type == SHT_NOBITS})

    # Load address, from the segment that carries the section. VMA and LMA differ
    # for exactly the case that broke #199.
    for section in sections:
        section["lma"] = section["vma"]
        for segment in segments:
            if segment["vaddr"] <= section["vma"] < segment["vaddr"] + segment["memsz"]:
                section["lma"] = section["vma"] - segment["vaddr"] + segment["paddr"]
                break

    return sections, segments


def regions(mapfile):
    """`{name: (origin, length)}` from the link map's Memory Configuration.

    From the link rather than from a constant here, so a linker-script edit
    cannot leave this file's arithmetic behind.
    """
    found = {}
    in_table = False
    for line in mapfile.read_text(errors="replace").splitlines():
        if line.startswith("Memory Configuration"):
            in_table = True
            continue
        if in_table:
            if line.startswith("Linker script and memory map"):
                break
            match = re.match(r"^(\S+)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", line)
            if match and match.group(1) != "*default*":
                found[match.group(1)] = (int(match.group(2), 16),
                                         int(match.group(3), 16))
    return found


def within(address, region):
    origin, length = region
    return origin <= address < origin + length


def account(elf):
    """The one memory accounting, by address. `apollo_memory_report.py` uses it too.

    Raises `FileNotFoundError` when the link map is missing: the region sizes come
    from the link, not from a constant, so there is nothing to check against.
    """
    mapfile = elf.with_suffix(elf.suffix + ".map")
    if not mapfile.exists():
        raise FileNotFoundError(mapfile)
    memory = regions(mapfile)
    missing = [n for n in ("rom", "ram") if n not in memory]
    if missing:
        raise ValueError(f"{mapfile} has no {missing} region: {sorted(memory)}")
    rom, ram = memory["rom"], memory["ram"]

    sections, _segments = elf_layout(elf)

    # Flash by LOAD address, RAM by virtual address. A section is in both when its
    # initialiser lives in flash and its storage in RAM -- which is `.relocate`,
    # the 80 bytes #199 was about.
    flash = sorted((s for s in sections
                    if not s["nobits"] and within(s["lma"], rom)),
                   key=lambda s: s["lma"])
    inram = sorted((s for s in sections if within(s["vma"], ram)),
                   key=lambda s: s["vma"])

    # Spans, not sums: alignment padding between sections is programmed too.
    return {
        "elf": elf, "map": mapfile, "rom": rom, "ram": ram,
        "sections": sections, "flash": flash, "inram": inram,
        "rom_used": max((s["lma"] + s["size"] for s in flash),
                        default=rom[0]) - rom[0],
        "rom_sum": sum(s["size"] for s in flash),
        "ram_used": max((s["vma"] + s["size"] for s in inram),
                        default=ram[0]) - ram[0],
        # Allocated but in neither region. Zero is the only correct value; the
        # name-keyed version of this check could not see the case at all.
        "unaccounted": [s for s in sections
                        if not (not s["nobits"] and within(s["lma"], rom))
                        and not within(s["vma"], ram)],
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--elf", type=Path, default=ELF)
    parser.add_argument("--stack-measured", type=int, default=None,
                        help="a measured high-water mark, to report against the "
                             "reservation")
    args = parser.parse_args()

    if not args.elf.exists():
        print(f"no ELF at {args.elf} -- build first")
        return 1

    try:
        book = account(args.elf)
    except FileNotFoundError as missing:
        print(f"no link map at {missing} -- the region sizes come from the link, "
              f"not from a constant, so there is nothing to check against")
        return 1
    except ValueError as bad:
        print(bad)
        return 1

    rom, ram = book["rom"], book["ram"]
    rom_used, ram_used = book["rom_used"], book["ram_used"]
    failures = []

    emit(f"Apollo memory budget: {args.elf}")
    emit()
    emit(f"  regions, from {book['map'].name}:")
    emit(f"    rom  0x{rom[0]:08x} + {rom[1]} bytes")
    emit(f"    ram  0x{ram[0]:08x} + {ram[1]} bytes")
    emit()

    emit("  flash, by load address:")
    for s in book["flash"]:
        emit(f"    {s['name']:<14} {s['size']:>6}  LMA 0x{s['lma']:08x}"
             f"..0x{s['lma'] + s['size']:08x}")
    if rom_used != book["rom_sum"]:
        emit(f"    {'(padding)':<14} {rom_used - book['rom_sum']:>6}  alignment "
             f"between sections")

    emit()
    emit("  RAM, by virtual address:")
    for s in book["inram"]:
        kind = "reserved" if s["nobits"] else "loaded from flash"
        emit(f"    {s['name']:<14} {s['size']:>6}  VMA 0x{s['vma']:08x}"
             f"..0x{s['vma'] + s['size']:08x}  {kind}")
    emit()

    # The defect this check was built around: an allocated section belonging to
    # neither region must read as a failure, not as zero.
    for s in book["unaccounted"]:
        failures.append(f"section {s['name']} ({s['size']} bytes, "
                        f"VMA 0x{s['vma']:08x}, LMA 0x{s['lma']:08x}) is "
                        f"allocated but in no known region -- unaccounted")

    # Second opinion from the artifact that is actually programmed. An accounting
    # that only agrees with itself is what #199 was.
    binary = args.elf.with_suffix(".bin")
    if binary.exists():
        size = binary.stat().st_size
        if size == rom_used:
            emit(f"  cross-check: {binary.name} is {size} bytes, agrees")
        else:
            failures.append(f"{binary.name} is {size} bytes but the ELF accounts "
                            f"for {rom_used} -- the accounting has a hole")
    else:
        emit(f"  cross-check: no {binary.name}, flash figure unconfirmed")
    emit()

    rom_limit = int(ROM_CEILING * rom[1])
    ram_limit = int(RAM_CEILING * ram[1])
    rom_pct = 100 * rom_used / rom[1]
    ram_pct = 100 * ram_used / ram[1]

    verdict = "ok"
    if rom_used > rom_limit:
        verdict = f"OVER by {rom_used - rom_limit}"
        failures.append(f"flash at {rom_pct:.2f}% ({rom_used} of {rom[1]}), "
                        f"{rom_used - rom_limit} bytes over the "
                        f"{ROM_CEILING * 100:.0f}% ceiling of {rom_limit}")
    emit(f"  flash  {rom_used:>6} / {rom[1]}  {rom_pct:>6.2f}%  "
         f"(ceiling {ROM_CEILING * 100:.0f}% = {rom_limit})  {verdict}")

    verdict = "ok"
    if ram_used > ram_limit:
        verdict = f"OVER by {ram_used - ram_limit}"
        failures.append(f"RAM at {ram_pct:.2f}% ({ram_used} of {ram[1]}), "
                        f"{ram_used - ram_limit} bytes over the "
                        f"{RAM_CEILING * 100:.0f}% ceiling of {ram_limit}")
    emit(f"  RAM    {ram_used:>6} / {ram[1]}  {ram_pct:>6.2f}%  "
         f"(ceiling {RAM_CEILING * 100:.0f}% = {ram_limit})  {verdict}")

    stack = next((s["size"] for s in book["sections"]
                  if s["name"] == ".stack"), 0)
    if args.stack_measured is not None and stack:
        if args.stack_measured >= stack:
            emit(f"  stack  MEASURED {args.stack_measured} >= reservation "
                 f"{stack} -- OVERFLOW")
            failures.append(f"stack high-water {args.stack_measured} "
                            f"reaches the {stack}-byte reservation")
        else:
            emit(f"  stack  measured {args.stack_measured} of {stack}, "
                 f"{stack - args.stack_measured} unused "
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
        emit("grew, with scripts/apollo_memory_report.py, and what each way")
        emit("out costs, with scripts/apollo_budget_levers.py.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
