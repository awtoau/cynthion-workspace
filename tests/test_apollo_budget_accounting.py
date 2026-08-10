#!/usr/bin/env python3
#
# The flash budget must count sections it has never heard of. See #199.
# SPDX-License-Identifier: BSD-3-Clause

"""Does `apollo_budget_check.py` count by address, or by section name?

#199: the check summed `.text + .data` on a link that has **no `.data`** -- the
d11 linker script routes `*(.data .data.*)` into an output section called
`.relocate`, whose storage is RAM and whose initialiser is in flash. 80 bytes of
each were invisible, and both ceilings were already breached while the guard
printed `ok`.

A rename to `.relocate` would fix the symptom and leave the defect. So the
fixtures here use section names the check has never seen (`.initdata`,
`.somewhere`), and the assertions are about where bytes are *placed*:

- an initialised-data section with LMA in flash and VMA in RAM counts once
  against each,
- alignment padding between flash sections is counted, because it is programmed,
- an allocated section in neither region is a failure, not a zero.

The ELFs are built here byte by byte rather than compiled: this runs in the
`python` check, which has no ARM toolchain.
"""

import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import apollo_budget_check  # noqa: E402

ROM_ORIGIN, ROM_LENGTH = 0x0800, 0x3800
RAM_ORIGIN, RAM_LENGTH = 0x20000000, 0x1000

SHT_PROGBITS, SHT_NOBITS, SHT_STRTAB = 1, 8, 3
SHF_ALLOC = 0x2

MAP = f"""
Memory Configuration

Name             Origin             Length             Attributes
rom              0x{ROM_ORIGIN:08x}         0x{ROM_LENGTH:08x}         xr
ram              0x{RAM_ORIGIN:08x}         0x{RAM_LENGTH:08x}         xrw
*default*        0x00000000         0xffffffff

Linker script and memory map
"""


def write_elf(directory, sections, segments, name="firmware.elf"):
    """A minimal ELF32-little-ARM with the given sections and PT_LOAD segments.

    `sections` are dicts of name/type/flags/addr/size; `segments` of
    vaddr/paddr/filesz/memsz. No content is stored -- the check reads headers.
    """
    names = b"\0" + b"".join(s["name"].encode() + b"\0" for s in sections) + b".shstrtab\0"
    offsets, cursor = {}, 1
    for s in sections:
        offsets[s["name"]] = cursor
        cursor += len(s["name"]) + 1
    shstrtab_name = cursor

    header_size, phentsize, shentsize = 52, 32, 40
    phoff = header_size
    strtab_off = phoff + phentsize * len(segments)
    shoff = strtab_off + len(names)

    # NULL section, then the caller's, then .shstrtab.
    entries = [b"\0" * shentsize]
    for s in sections:
        entries.append(struct.pack(
            "<IIIIIIIIII", offsets[s["name"]], s["type"], s["flags"], s["addr"],
            0, s["size"], 0, 0, 4, 0))
    entries.append(struct.pack("<IIIIIIIIII", shstrtab_name, SHT_STRTAB, 0, 0,
                               strtab_off, len(names), 0, 0, 1, 0))

    blob = struct.pack("<4sBBBBB7xHHIIIIIHHHHHH",
                       b"\x7fELF", 1, 1, 1, 0, 0, 2, 40, 1, 0, phoff, shoff,
                       0, header_size, phentsize, len(segments), shentsize,
                       len(entries), len(entries) - 1)
    for seg in segments:
        blob += struct.pack("<IIIIIIII", 1, 0, seg["vaddr"], seg["paddr"],
                            seg["filesz"], seg["memsz"], 4, 4)
    blob += names + b"".join(entries)

    path = directory / name
    path.write_bytes(blob)
    path.with_suffix(".elf.map").write_text(MAP)
    return path


def two_region_elf(directory, *, text=1000, initdata=80, bss=200, pad=0):
    """The shape that broke: initialised data with LMA in flash, VMA in RAM."""
    text_end = ROM_ORIGIN + text
    return write_elf(
        directory,
        sections=[
            {"name": ".text", "type": SHT_PROGBITS, "flags": SHF_ALLOC,
             "addr": ROM_ORIGIN, "size": text},
            {"name": ".initdata", "type": SHT_PROGBITS, "flags": SHF_ALLOC,
             "addr": RAM_ORIGIN, "size": initdata},
            {"name": ".bss", "type": SHT_NOBITS, "flags": SHF_ALLOC,
             "addr": RAM_ORIGIN + initdata, "size": bss},
        ],
        segments=[
            {"vaddr": ROM_ORIGIN, "paddr": ROM_ORIGIN,
             "filesz": text, "memsz": text},
            # `pad` models alignment between the end of .text and the load
            # address of the initialisers.
            {"vaddr": RAM_ORIGIN, "paddr": text_end + pad,
             "filesz": initdata, "memsz": initdata},
            {"vaddr": RAM_ORIGIN + initdata, "paddr": RAM_ORIGIN + initdata,
             "filesz": 0, "memsz": bss},
        ])


def test_initialised_data_counts_against_flash(tmp_path):
    book = apollo_budget_check.account(two_region_elf(tmp_path))
    assert book["rom_used"] == 1080, "the 80 bytes of initialisers are in flash"
    assert [s["name"] for s in book["flash"]] == [".text", ".initdata"]


def test_initialised_data_counts_against_ram_as_well(tmp_path):
    book = apollo_budget_check.account(two_region_elf(tmp_path))
    assert book["ram_used"] == 280
    assert [s["name"] for s in book["inram"]] == [".initdata", ".bss"]


def test_a_name_keyed_sum_would_miss_it(tmp_path):
    """The control: the arithmetic #199 shipped, run against the same fixture."""
    book = apollo_budget_check.account(two_region_elf(tmp_path))
    by_name = {s["name"]: s["size"] for s in book["sections"]}
    old = by_name.get(".text", 0) + by_name.get(".data", 0)
    assert old == 1000
    assert book["rom_used"] - old == 80, "exactly the bytes the old sum dropped"


def test_padding_between_flash_sections_is_counted(tmp_path):
    """Programmed bytes, not the sum of section sizes."""
    book = apollo_budget_check.account(two_region_elf(tmp_path, pad=12))
    assert book["rom_sum"] == 1080
    assert book["rom_used"] == 1092


def test_a_section_in_neither_region_is_a_failure(tmp_path):
    path = write_elf(
        tmp_path,
        sections=[
            {"name": ".text", "type": SHT_PROGBITS, "flags": SHF_ALLOC,
             "addr": ROM_ORIGIN, "size": 1000},
            {"name": ".somewhere", "type": SHT_PROGBITS, "flags": SHF_ALLOC,
             "addr": 0x40000000, "size": 64},
        ],
        segments=[
            {"vaddr": ROM_ORIGIN, "paddr": ROM_ORIGIN,
             "filesz": 1000, "memsz": 1000},
            {"vaddr": 0x40000000, "paddr": 0x40000000,
             "filesz": 64, "memsz": 64},
        ])
    book = apollo_budget_check.account(path)
    assert [s["name"] for s in book["unaccounted"]] == [".somewhere"]


def test_regions_come_from_the_link_map_not_from_a_constant(tmp_path):
    path = two_region_elf(tmp_path)
    book = apollo_budget_check.account(path)
    assert book["rom"] == (ROM_ORIGIN, ROM_LENGTH)
    assert book["ram"] == (RAM_ORIGIN, RAM_LENGTH)

    path.with_suffix(".elf.map").unlink()
    with pytest.raises(FileNotFoundError):
        apollo_budget_check.account(path)


def test_the_real_elf_agrees_with_the_programmed_image():
    """The cross-check that makes the accounting falsifiable. Needs a build."""
    elf = ROOT / "repos/apollo/firmware/_build/cynthion_d11/firmware.elf"
    binary = elf.with_suffix(".bin")
    if not elf.exists() or not binary.exists():
        pytest.skip("no Apollo build here -- needs arm-none-eabi-gcc")
    book = apollo_budget_check.account(elf)
    assert book["rom_used"] == binary.stat().st_size
    assert book["unaccounted"] == []
