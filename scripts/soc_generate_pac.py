#!/usr/bin/env python3
#
# Generate an SVD and a Rust PAC from the SoC's own memory map.
# SPDX-License-Identifier: BSD-3-Clause

"""
Emits `soc.svd` from the Amaranth SoC, then a Peripheral Access Crate from that.

## Why generate rather than hand-write

The register map is not written down anywhere today -- it is *implied* by the
`csr.Builder` calls in the gateware, and firmware rediscovers it by hand. Every
base address in `firmware/cynthion-soc/src/target.rs` was transcribed by a human
reading `gateware/soc/top.py`, and the surface grew by three
peripherals (GPIO, I2C, sideband) in one sitting.

That is the class of error that cost most of a day elsewhere: firmware sent
`0x9f << 24` because a comment asserted the PHY did not left-justify, when it
does. The hardware and the comment disagreed and nothing could catch it.

A generated address cannot drift. Move a peripheral and the constant moves with
it; rename one and the firmware stops compiling.

## The chain

    AwtoSoc.decoder.bus.memory_map  ->  soc.svd  ->  svd2rust + form  ->  PAC
                                     ->  src/base.rs

The SVD emitter here is ours. `luna_soc.generate.svd` is excluded by policy (see
`docs/upstream-boundary.md`) and did not work anyway: it formats `csr_base` with
`{:08x}` and every window in this design reports `csr_base = None`.

## What comes out, and what could not

`amaranth_soc` knows a great deal about the map and nothing about what any of it
*means*:

  * recoverable -- window names and absolute bases, window sizes, register names,
    byte offsets, widths, access modes, field names, field bit positions, field
    widths, field access modes, and the reset value of every field whose action
    carries an `init`;
  * not recoverable -- prose. `csr.Register` rewrites `__doc__` from a template,
    so every register in the design reports the docstring "A CSR register."
    Descriptions below are therefore mechanical, built from the name, the offset
    and the width. The reasoning lives in the gateware modules and stays there.

Reset values are emitted with a `<resetMask>` covering only the bits whose reset
is actually known. A field action with no `init` (a `csr.action.R` fed from a
wire, say) contributes nothing rather than a fabricated zero -- a zero would be
a claim the memory map does not make.

## Byte-granular access, and why the register accessors are not adopted

Every CSR here sits behind an `amaranth_soc` multiplexer with a granularity of 8
bits, and a multi-byte register is not one wide access: reading its lowest byte
latches a shadow copy and writing its highest byte commits one. The firmware's
`gpio.rs` writes Mode's low byte then its high byte for exactly this reason.

`svd2rust` generates one natural-width volatile access per register -- a `u16`
read for a 16-bit register -- which is a different bus transaction from the two
ordered byte accesses the multiplexer specifies. So this crate is used for
*addresses*, which are what drifts, and the drivers keep their own byte-level
access, which is what the hardware specifies. See the report in the issue.

## What it does NOT do

It does not touch the FPGA and does not build a bitstream. It elaborates the SoC
far enough to read its memory map, which needs no toolchain and no board.

    ./scripts/soc_generate_pac.py            # svd + crate
    ./scripts/soc_generate_pac.py --svd-only
    ./scripts/soc_generate_pac.py --check    # regenerate and diff, change nothing
"""

import argparse
import subprocess
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "firmware" / "cynthion-soc-pac"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# Register footprints this emitter is willing to describe, in bytes.
#
# SVD's `<size>` is a bit width and consumers assume a power of two, because that
# is what a load instruction can be. A CSR occupying three bytes would have to be
# rounded up, which would overlap whatever follows it and produce an SVD that
# describes a different machine. Refuse instead -- see `too_wide` below.
FOOTPRINTS = {1: 8, 2: 16, 4: 32, 8: 64}


def build_soc():
    """Elaborate the SoC far enough to expose its memory map.

    Imports rather than builds: the memory map is decided during elaboration, so no
    synthesis or place-and-route is involved and no board is touched.
    """
    import cpu.cpu as vexii_cpu
    # The CPU is a black box for this walk; generating its implementation cannot
    # affect the decoder map. Reuse the checked-in matching profile so this
    # metadata-only command does not require sbt or a generator server.
    cached_cpu = ROOT / "gateware" / "probes" / "cpu_matrix" / "soc-cpu" / "VexiiRiscv.v"
    if not cached_cpu.exists():
        raise FileNotFoundError(f"memory-map elaboration needs {cached_cpu}")
    vexii_cpu.generate = lambda *args, **kwargs: cached_cpu

    import top as soc_top
    from amaranth.hdl import Fragment, UnusedElaboratable

    # Requesting a platform resource builds a PinBuffer per pin, and elaborating
    # only the SoC leaves every one of them unused. Amaranth says so from the
    # buffer's __del__, so the warnings arrive at garbage-collection time and a
    # context manager around Fragment.get() does not catch them -- forty lines of
    # noise then bury the address listing this script exists to print. The design
    # is never built here, so "never used" is the expected outcome, not news.
    warnings.filterwarnings("ignore", category=UnusedElaboratable)

    firmware = [0] * 16  # contents are irrelevant; only the map is read
    soc = soc_top.AwtoSoc(firmware=firmware)

    # Elaborate before reading the map. The decoder and every peripheral window are
    # created INSIDE elaborate(), so a freshly constructed SoC has no memory map at all
    # and the failure reads as "could not find a memory map" rather than as a missing
    # elaboration.
    #
    # A real platform is needed, not None: elaborate() calls platform.request() for the
    # ULPI PHY, the flash pins and the LEDs. Requesting resources needs no toolchain and
    # touches no hardware -- this still builds nothing.
    from board.cynthion_r1_4 import CynthionPlatformRev1D4

    # The platform is returned too, and it is not incidental. After elaboration it
    # knows which resources were actually `request`ed, which is the only authority
    # on whether a pin is free -- a question no static table can answer, because
    # the answer is a property of what the design did.
    platform = CynthionPlatformRev1D4()
    Fragment.get(soc, platform)
    return soc, platform


def ident_str(text):
    """Anything that is not alphanumeric becomes an underscore.

    SVD names end up as Rust identifiers, and svd2rust does not sanitise: a name
    with a hyphen or a dot in it produces a crate that does not compile, several
    thousand lines away from the cause.
    """
    return "".join(char if char.isalnum() or char == "_" else "_" for char in text)


def ident(name):
    """An SVD identifier from an `amaranth_soc` `Name`.

    A `Name` is a tuple of parts -- `Name('memory', 'blockram')` -- so parts join
    with an underscore before sanitising.
    """
    return ident_str("_".join(str(part) for part in name))


class Peripheral:
    """One SVD peripheral: a window that directly contains registers, or memory."""

    def __init__(self, path, base, size, kind):
        self.path = path            # tuple of identifier strings, outermost first
        self.base = base            # absolute byte address
        self.size = size            # window size in bytes
        self.kind = kind            # "registers" or "buffer"
        self.registers = []         # (name, offset, footprint, register object)
        # (interrupt name, PLIC source number), in the order declared. A list
        # rather than one number because a window may raise more than one
        # source: `i2c_mux.I2CBusMux` gives each FUSB302B its own, so that a
        # handler knows which device asserted without a register read.
        self.irqs = []

    @property
    def name(self):
        return "_".join(self.path).upper()


def named_windows(memory_map, base=0, path=(), found=None):
    """Every *named* window in the map, as `path -> (base, size)`.

    Not every window has a name. `WishboneCSRBridge` wraps each peripheral's CSR
    map in an anonymous one, and `spi0` has two more inside that -- so the tree is
    a level or two deeper than the address map anyone would draw. `all_resources()`
    already drops anonymous names from a resource's path, so this drops them too
    and the two agree.

    `board` therefore yields `board/gpio`, `board/i2c` and `board/sideband` at
    their own bases rather than one peripheral with a 32-byte hole in it. That is
    how the firmware thinks of them -- three handles, three addresses -- and it is
    what makes the generated constants comparable with `target.rs`.
    """
    if found is None:
        found = {}
    for window, name, (start, end, ratio) in memory_map.windows():
        # A sparse window interleaves its resources across the parent's data
        # lanes, so a resource's parent-space offset is not its own offset times
        # anything simple. Nothing here uses one; refuse rather than emit
        # addresses that are quietly wrong.
        if ratio != 1:
            raise NotImplementedError(
                f"window {ident(name)} is sparse (ratio {ratio}); this emitter "
                f"only describes dense windows")
        if name is None:
            named_windows(window, base + start, path, found)
            continue
        sub_path = path + (ident(name),)
        found[sub_path] = (base + start, end - start)
        named_windows(window, base + start, sub_path, found)
    return found


def walk(memory_map):
    """Flatten the memory map into peripherals, outermost address first."""
    from amaranth_soc import csr

    windows = named_windows(memory_map)
    peripherals = {}

    for info in memory_map.all_resources():
        path = tuple(ident(part) for part in info.path[:-1])
        if path not in windows:
            raise KeyError(f"resource {'/'.join(path)} is in no named window")
        base, size = windows[path]
        if path not in peripherals:
            # A window is memory rather than registers when what it holds is not a
            # `csr.Register`: the block RAM and the memory-mapped flash each appear
            # as a single resource spanning the whole window. Emitting those as
            # registers would claim a 64 KiB load instruction exists.
            kind = "registers" if isinstance(info.resource, csr.Register) else "buffer"
            peripherals[path] = Peripheral(path, base, size, kind)
        peripherals[path].registers.append(
            (ident(info.path[-1]), info.start - base, info.end - info.start,
             info.resource))

    ordered = sorted(peripherals.values(), key=lambda p: p.base)
    for peripheral in ordered:
        peripheral.registers.sort(key=lambda entry: entry[1])
    return ordered


def fields_of(register):
    """(name, offset, width, access, init) for every field, LSB first.

    Field offsets are not stored anywhere. `Register.elaborate` packs fields into
    the element in `flatten()` order starting at bit 0, so that order *is* the
    layout and reproducing it here is the only way to recover bit positions. If
    upstream ever changed the packing, this would be the line to change with it.
    """
    from amaranth.hdl import Shape

    offset = 0
    for path, field in register:
        width = Shape.cast(field.port.shape).width
        # `init` exists on the actions that hold state (`csr.action.RW` and
        # friends) and not on the ones that pass a wire through. A missing init is
        # reported as None and reaches `<resetMask>` as a hole, rather than being
        # rounded to zero.
        init = getattr(field, "init", None)
        if init is None:
            init = getattr(getattr(field, "data", None), "init", None)
        name = "_".join(str(key) for key in path) or "value"
        yield ident_str(name), offset, width, field.port.access, init
        offset += width


ACCESS = {"r": "read-only", "w": "write-only", "rw": "read-write"}


def access_name(access):
    """SVD access string from an `amaranth_soc` access enum.

    Both `csr.Element.Access` and `csr.FieldPort.Access` have values "r", "w" and
    "rw"; going through `.value` rather than the member name keeps this working
    for either.
    """
    return ACCESS[str(access.value)]


def sub(parent, tag, text=None):
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = str(text)
    return element


def build_svd(peripherals, description):
    """The whole SVD document, as an ElementTree root."""
    device = ET.Element("device", {
        "schemaVersion": "1.1",
        "xmlns:xs": "http://www.w3.org/2001/XMLSchema-instance",
        "xs:noNamespaceSchemaLocation": "CMSIS-SVD.xsd",
    })
    sub(device, "vendor", "awtoau")
    sub(device, "name", "CYNTHION_SOC")
    sub(device, "version", "1.0")
    sub(device, "description", description)
    # One byte per address, which is what the Wishbone decoder's granularity=8
    # means, and a 32-bit machine. `size` is the DEFAULT register width; every
    # register below states its own, because they range from 8 to 64 bits.
    sub(device, "addressUnitBits", 8)
    sub(device, "width", 32)
    sub(device, "size", 32)
    sub(device, "access", "read-write")
    sub(device, "resetValue", "0x00000000")
    sub(device, "resetMask", "0x00000000")

    peripherals_element = sub(device, "peripherals")
    for peripheral in peripherals:
        element = sub(peripherals_element, "peripheral")
        sub(element, "name", peripheral.name)
        sub(element, "description", peripheral_description(peripheral))
        sub(element, "baseAddress", f"0x{peripheral.base:08X}")

        block = sub(element, "addressBlock")
        sub(block, "offset", "0x0")
        sub(block, "size", f"0x{peripheral.size:X}")
        sub(block, "usage", peripheral.kind)

        # SVD allows several <interrupt> children per peripheral, and svd2rust
        # requires their names to be distinct across the device. Both fall out
        # of the naming below: a window with one source is named after itself,
        # and one with several appends the suffix it declared.
        for irq_name, number in peripheral.irqs:
            interrupt = sub(element, "interrupt")
            sub(interrupt, "name", irq_name)
            sub(interrupt, "value", number)

        if peripheral.kind != "registers":
            continue

        registers = sub(element, "registers")
        for name, offset, footprint, register in peripheral.registers:
            add_register(registers, peripheral, name, offset, footprint, register)

    return device


def peripheral_description(peripheral):
    if peripheral.kind == "buffer":
        return (f"{'/'.join(peripheral.path)}: {peripheral.size} bytes of memory "
                f"at 0x{peripheral.base:08x}")
    count = len(peripheral.registers)
    return (f"{'/'.join(peripheral.path)}: {count} "
            f"register{'' if count == 1 else 's'} at 0x{peripheral.base:08x}")


def add_register(parent, peripheral, name, offset, footprint, register):
    element = sub(parent, "register")
    sub(element, "name", name.upper())
    sub(element, "description",
        f"{peripheral.name}.{name.upper()}, {register.element.width} bits at "
        f"+0x{offset:02x}")
    sub(element, "addressOffset", f"0x{offset:X}")
    sub(element, "size", FOOTPRINTS[footprint])
    sub(element, "access", access_name(register.element.access))

    reset_value = 0
    reset_mask = 0
    field_elements = []
    for field_name, bit, width, access, init in fields_of(register):
        field_elements.append((field_name, bit, width, access))
        if init is not None:
            reset_value |= (int(init) & ((1 << width) - 1)) << bit
            reset_mask |= ((1 << width) - 1) << bit

    sub(element, "resetValue", f"0x{reset_value:X}")
    # Only the bits whose reset is known. A consumer that trusts resetValue over
    # the whole width would otherwise be told that a read-only status field resets
    # to zero, which the memory map never says.
    sub(element, "resetMask", f"0x{reset_mask:X}")

    fields = sub(element, "fields")
    for field_name, bit, width, access in field_elements:
        field = sub(fields, "field")
        sub(field, "name", field_name.upper())
        sub(field, "description",
            f"{field_name} [{bit + width - 1}:{bit}]" if width > 1
            else f"{field_name} [{bit}]")
        sub(field, "bitOffset", bit)
        sub(field, "bitWidth", width)
        sub(field, "access", access_name(access))


def verify_svd(path, peripherals, emit):
    """Parse the file back and check it describes what was asked for.

    Structural, not cosmetic: a zero-byte SVD and an SVD full of empty peripherals
    both "succeed" if nothing reads the result back. Everything below compares the
    parsed document against the memory map it came from, so a silently truncated
    write or a peripheral that lost its registers fails here rather than in
    `svd2rust`'s error output.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    problems = []

    parsed = {}
    for element in root.findall("./peripherals/peripheral"):
        name = element.findtext("name")
        base = int(element.findtext("baseAddress"), 16)
        registers = element.findall("./registers/register")
        parsed[name] = (base, registers)

    expected_registers = 0
    expected_fields = 0
    for peripheral in peripherals:
        if peripheral.name not in parsed:
            problems.append(f"{peripheral.name} is missing from the SVD")
            continue
        base, registers = parsed[peripheral.name]
        if base != peripheral.base:
            problems.append(f"{peripheral.name} base 0x{base:08x} != "
                            f"0x{peripheral.base:08x}")
        if peripheral.kind != "registers":
            continue
        expected_registers += len(peripheral.registers)
        if len(registers) != len(peripheral.registers):
            problems.append(f"{peripheral.name} has {len(registers)} registers, "
                            f"expected {len(peripheral.registers)}")
        for register in registers:
            offset = int(register.findtext("addressOffset"), 16)
            size = int(register.findtext("size"))
            fields = register.findall("./fields/field")
            if not fields:
                problems.append(f"{peripheral.name}.{register.findtext('name')} "
                                f"has no fields")
            # Every field must fit inside the register it is declared in. An
            # off-by-one in the packing above would put a field's top bit outside
            # the load that reads it, and nothing downstream would notice.
            for field in fields:
                top = int(field.findtext("bitOffset")) + int(field.findtext("bitWidth"))
                if top > size:
                    problems.append(
                        f"{peripheral.name}.{register.findtext('name')}."
                        f"{field.findtext('name')} ends at bit {top}, past the "
                        f"{size}-bit register")
            expected_fields += len(fields)
            if offset + size // 8 > peripheral.size:
                problems.append(f"{peripheral.name}.{register.findtext('name')} "
                                f"at +0x{offset:x} is outside its address block")

    emit(f"verified: {len(parsed)} peripherals, {expected_registers} registers, "
         f"{expected_fields} fields round-tripped")
    for problem in problems:
        emit(f"  PROBLEM: {problem}")
    return not problems


def cross_check(peripherals, emit):
    """Compare generated addresses against the remaining independent copies.

    The point of the whole exercise. `top.py` names the addresses it
    passes to `decoder.add()`, and `target.rs` names the addresses the firmware
    dereferences; neither is derived from the map, so either can be wrong. A
    mismatch reported here is a real defect in one of the three, not a formatting
    difference -- resolve it, do not adjust this function.
    """
    import re
    import top as soc_module

    bases = {peripheral.name: peripheral.base for peripheral in peripherals}
    expected = {
        "RAM": soc_module.RAM_BASE,
        "CONSOLE": soc_module.CONSOLE_BASE,
        "APOLLO_UART": soc_module.APOLLO_UART_BASE,
        "BOARD_GPIO": soc_module.GPIO_BASE,
        "BOARD_I2C": soc_module.I2C_BASE,
        "BOARD_SIDEBAND": soc_module.SIDEBAND_BASE,
        "BOARD_ULPI": soc_module.ULPI_BASE,
        "BOARD_I2C_MUX": soc_module.I2C_MUX_BASE,
        "BOARD_FABRIC": soc_module.FABRIC_BASE,
        "PLIC": soc_module.PLIC_BASE,
        "CLINT": soc_module.CLINT_BASE,
        "SPIFLASH": soc_module.FLASH_BASE,
        "HYPERRAM": soc_module.HYPERRAM_BASE,
        "SPI0": soc_module.FLASH_CSR_BASE,
        "FLASH_PROBE": soc_module.FLASH_PROBE_BASE,
        "BOOTRAM": soc_module.BOOTRAM_BASE,
    }

    ok = True
    for name, address in sorted(expected.items()):
        actual = bases.get(name)
        if actual == address:
            continue
        # ABSENT and MISMATCH are different faults and must not print the same
        # line. This used to be one f-string with the None test inside the
        # format spec, so the absent case formatted `None` with `:08x` and
        # raised TypeError four frames up -- the diagnostic for a missing
        # peripheral crashed instead of naming it.
        if actual is None:
            emit(f"  ABSENT gateware {name}: constant 0x{address:08x}, "
                 f"but the elaborated map has no such peripheral")
        else:
            emit(f"  MISMATCH gateware {name}: constant 0x{address:08x}, "
                 f"map 0x{actual:08x}")
        ok = False
    emit(f"gateware constants: {len(expected)} checked, "
         f"{'all agree' if ok else 'DISAGREEMENT ABOVE'}")

    # And the firmware, from the other direction. `target.rs` is expected to name
    # `cynthion_soc_pac::base::X` rather than an address, so what is checked is
    # that the reference is there and that no literal has crept back in beside it.
    src = ROOT / "firmware" / "cynthion-soc" / "src"
    target = (src / "target.rs").read_text()
    firmware_ok = True
    for name in ("CONSOLE", "APOLLO_UART", "PLIC", "BOARD_GPIO", "BOARD_I2C",
                 "BOARD_SIDEBAND", "BOARD_FABRIC", "SPIFLASH", "CLINT",
                 "CONSOLE_IRQ", "APOLLO_UART_IRQ",
                 "BOARD_I2C_MUX_TARGET_IRQ", "BOARD_I2C_MUX_AUX_IRQ"):
        if f"cynthion_soc_pac::base::{name}" not in target:
            emit(f"  target.rs no longer refers to base::{name}")
            firmware_ok = False

    # A transcribed address that has come back. Only the CSR region is scanned:
    # `-M virt` puts nothing at 0xf0000000 or above (its PLIC is at 0x0c000000,
    # its 16550 at 0x10000000, its RAM at 0x80000000), so a literal up there in
    # firmware source can only be one of ours, whichever branch it is in. Below
    # that the two machines genuinely collide -- virt's UART sits exactly where
    # our flash window does -- and a scan would flag the QEMU constant that is
    # supposed to be there.
    csr_bases = {address: name for name, address in bases.items()
                 if address >= 0xf0000000}
    for path in sorted(src.glob("*.rs")):
        text = path.read_text()
        for literal in re.findall(r"0x([0-9a-fA-F][0-9a-fA-F_]*)", text):
            value = int(literal.replace("_", ""), 16)
            if value in csr_bases:
                emit(f"  {path.name} has 0x{value:08x} written out; that is "
                     f"base::{csr_bases[value]} and it should be named, not copied")
                firmware_ok = False

    # Byte-granular accesses still need hand-rolled volatile operations, but their
    # addresses must come from the register walk. Checking the references as well
    # as the generated file makes a reintroduced literal fail immediately.
    hyperram = (src / "hyperram.rs").read_text()
    for name in ("ADDR", "CTRL", "STATUS", "DATA", "WDATA"):
        if f"offset::{name}" not in hyperram:
            emit(f"  hyperram.rs no longer refers to bootram::offset::{name}")
            firmware_ok = False

    for crate_src in sorted((ROOT / "firmware").glob("*/src")):
        for path in sorted(crate_src.glob("*.rs")):
            for match in re.finditer(r"BASE\s*\+\s*0x[0-9a-fA-F_]+", path.read_text()):
                emit(f"  {path.relative_to(ROOT)} has a register offset written "
                     f"out: {match.group(0)}")
                firmware_ok = False

    emit("firmware: every address comes from the generated crate" if firmware_ok
         else "firmware: AN ADDRESS IS TRANSCRIBED OR MISSING -- see above")

    return ok and firmware_ok and split_check(soc_module, emit)


def split_check(soc_module, emit):
    """The block RAM split, in the six files that each state it separately.

    The decoder sees one 64 KiB window, so nothing in the memory map can catch this:
    where the bootloader ends and the image begins is agreed by linker scripts, a Rust
    constant and two Python constants, and none of them derives from another. A
    disagreement is not a build error -- it is a bootloader that jumps into the middle
    of an image, or a staged image accepted at a length that will not fit.

    Reported by reading, not by importing. Two of these are linker scripts.
    """
    import re

    origin = soc_module.IMAGE_ORIGIN
    size = soc_module.RAM_SIZE - origin

    def find(path, pattern, what):
        text = (ROOT / path).read_text()
        match = re.search(pattern, text)
        if not match:
            return None, f"  {path}: could not find {what}"
        return int(match.group(1), 0), None

    # Each entry: file, pattern, what it should equal, and what it is called there.
    # `K` suffixes are resolved by the multiplier, so a region length reads as bytes.
    wanted = [
        ("firmware/cynthion-boot/memory.x",
         r"IMAGE\s*:\s*ORIGIN\s*=\s*(0x[0-9a-fA-F]+)", origin, 1, "IMAGE origin"),
        ("firmware/cynthion-boot/memory.x",
         r"IMAGE\s*:\s*ORIGIN\s*=\s*0x[0-9a-fA-F]+,\s*LENGTH\s*=\s*(\d+)K",
         size, 1024, "IMAGE length"),
        ("firmware/cynthion-boot/memory.x",
         r"BOOT\s*:\s*ORIGIN\s*=\s*0x[0-9a-fA-F]+,\s*LENGTH\s*=\s*(\d+)K",
         origin, 1024, "BOOT length"),
        ("firmware/cynthion-soc/memory.x",
         r"RAM\s*:\s*ORIGIN\s*=\s*(0x[0-9a-fA-F]+)", origin, 1, "RAM origin"),
        ("firmware/cynthion-soc/memory.x",
         r"RAM\s*:\s*ORIGIN\s*=\s*0x[0-9a-fA-F]+,\s*LENGTH\s*=\s*(\d+)K",
         size, 1024, "RAM length"),
        ("firmware/cynthion-payload/memory.x",
         r"PAYLOAD\s*:\s*ORIGIN\s*=\s*(0x[0-9a-fA-F]+)", origin, 1,
         "PAYLOAD origin"),
        ("firmware/cynthion-payload/memory.x",
         r"PAYLOAD\s*:\s*ORIGIN\s*=\s*0x[0-9a-fA-F]+,\s*LENGTH\s*=\s*(\d+)K",
         size, 1024, "PAYLOAD length"),
        ("firmware/cynthion-soc/src/hyperram.rs",
         r"MAX_IMAGE:\s*u32\s*=\s*(\d+)\s*\*\s*1024", size, 1024, "MAX_IMAGE"),
        ("scripts/soc_payload.py",
         r"PAYLOAD_SIZE\s*=\s*(\d+)\s*\*\s*1024", size, 1024, "PAYLOAD_SIZE"),
        ("scripts/soc_jtag_stage.py",
         r"MAX_IMAGE\s*=\s*(\d+)\s*\*\s*1024", size, 1024, "MAX_IMAGE"),
        # The boot status word: the last four bytes of BOOT, named twice.
        # `memory.x` computes it; `target.rs` has to write the number, because it
        # is block RAM and so is not in the generated memory map.
        ("firmware/cynthion-soc/src/target.rs",
         r"BOOT_STATUS:\s*Option<usize>\s*=\s*Some\((0x[0-9a-fA-F]+)\)",
         origin - 4, 1, "BOOT_STATUS"),
    ]

    split_ok = True
    for path, pattern, expected, scale, what in wanted:
        value, problem = find(path, pattern, what)
        if problem:
            emit(problem)
            split_ok = False
            continue
        if value * scale != expected:
            emit(f"  MISMATCH {path} {what}: {value * scale}, "
                 f"gateware says {expected}")
            split_ok = False

    emit(f"block RAM split: bootloader 0x0+{origin}, image "
         f"{origin:#x}+{size}, {len(wanted)} statements of it "
         f"{'all agree' if split_ok else 'DISAGREE -- see above'}")
    return split_ok


def write_bases(peripherals, emit):
    """A Rust module of plain `usize` addresses.

    Why this exists alongside `svd2rust`'s output: svd2rust gives each peripheral
    a `PTR` of pointer type, and Rust forbids casting a pointer to an integer in a
    const context. `target.rs` needs `const` addresses -- `UART_BASES` is a
    `&[usize]` -- so a pointer constant cannot be used there at all. These are the
    same numbers from the same walk of the same map, as integers.
    """
    lines = [
        "//! Where each peripheral is. Generated by `scripts/soc_generate_pac.py`.",
        "//!",
        "//! Read out of `AwtoSoc.decoder.bus.memory_map` -- the SoC's own",
        "//! description of itself -- not transcribed. Do not edit: the next run",
        "//! overwrites this file, and a hand edit here would recreate exactly the",
        "//! drift the generator exists to remove.",
        "//!",
        "//! These are `usize` rather than `svd2rust`'s `PTR` constants because",
        "//! Rust cannot cast a pointer to an integer in a const context, and",
        "//! `target.rs` needs const addresses.",
        "",
    ]
    for peripheral in peripherals:
        lines.append(f"/// {peripheral_description(peripheral)}")
        lines.append(f"pub const {peripheral.name}: usize = "
                     f"0x{peripheral.base:08x};")
        lines.append(f"/// Size of the {peripheral.name} window, in bytes.")
        lines.append(f"pub const {peripheral.name}_SIZE: usize = "
                     f"0x{peripheral.size:08x};")
        for irq_name, number in peripheral.irqs:
            lines.append(f"/// PLIC source number wired to {irq_name}.")
            lines.append(f"pub const {irq_name}_IRQ: u32 = {number};")
        lines.append("")

    # Cacheability is NOT in the memory map, and that is why it is emitted here.
    #
    # Every other constant above is read out of `decoder.bus.memory_map`, which
    # describes where a window is and how big it is and nothing else. Whether the
    # CPU caches that window is a PMA property of VexiiRiscv's region table, set
    # in the SoC script. So a firmware wanting to report it had only a comment to
    # go on -- and the comment in `target.rs` was still claiming `main=1` after
    # the window was switched to uncached, which is exactly the drift this
    # generator exists to remove.
    import top as soc_module
    from peripherals.i2c_master import prescale_for

    # CLOCK-DERIVED CONSTANTS, generated for the reason today made expensive.
    #
    # `SYNC_MHZ` moved from 60 to 72 and two firmware constants had to move with
    # it. `TIME_HZ` was caught, because `info` compares it against the gateware's
    # own report and said "SYNC MISMATCH". The I2C prescale was NOT caught: there
    # is no check, and its own comment names the symptom of forgetting -- "a bus
    # that violates its own setup times and answers most of the time", which is
    # intermittent and would have been blamed on the board.
    #
    # Neither is a constant anyone should be maintaining by hand. `prescale_for`
    # is the gateware's own function, called here with the gateware's own clock,
    # so the number the firmware uses is the number the hardware was built for.
    # THE SOLVED RATE, not `SYNC_MHZ`. `SYNC_MHZ` is what the design asks the PLL
    # for; `actual_sync_mhz` is what the dividers produce, recomputed from them.
    # They agree today only because `solve_pll` refuses anything but an exact
    # match, and the day it learns to approximate every interval in the firmware
    # is wrong by the rounding ratio -- `rdtime` counts `sync` cycles, so
    # `SYNC_HZ` is the timebase for all of them, and the I2C prescale sets a bus
    # whose own comment names the symptom of getting it wrong: "a bus that
    # violates its own setup times and answers most of the time".
    #
    # `top.py` derives the console divisor, the Apollo UART divisor, the HyperRAM
    # CK and the heartbeat from the same value. This is the firmware's half of
    # the same rule.
    from clocks import SocClocks
    sync_hz = round(SocClocks(sync_mhz=soc_module.SYNC_MHZ).actual_sync_mhz * 1e6)
    lines += [
        "/// The `sync` clock, in Hz, from the SoC's own `SYNC_MHZ`.",
        "///",
        "/// `rdtime` counts one per `sync` cycle, so this is the timebase for",
        "/// every interval in the firmware. Hand-maintained it silently stretched",
        "/// or shrank them all whenever the gateware clock moved.",
        f"pub const SYNC_HZ: u32 = {sync_hz};",
        "",
        "/// I2C prescale for {} kHz SCL at that clock, from the gateware's own"
        .format(soc_module.I2C_SCL_HZ // 1000),
        "/// `prescale_for` -- `f_SCL = f_sync / (5 * (PRER + 1))`.",
        f"pub const I2C_PRESCALE: u16 = "
        f"{prescale_for(sync_hz, soc_module.I2C_SCL_HZ)};",
        "",
        "/// Whether the SPIFLASH window is cached (VexiiRiscv PMA `main`).",
        "///",
        "/// From `FLASH_CACHED` in the SoC script, not from the memory map:",
        "/// cacheability is a property of the CPU's region table, so no window",
        "/// description carries it. Uncached routes the window to the `iobus`,",
        "/// where every load is a full SPI command/address/dummy/data",
        "/// transaction and the I-cache cannot fetch from it at all.",
        f"pub const SPIFLASH_CACHED: bool = "
        f"{'true' if soc_module.FLASH_CACHED else 'false'};",
        "",
    ]

    # WHAT THE CORE WAS GENERATED WITH, from `cpu/cpu.py`'s own flag list.
    #
    # `misa` reads rv32im whatever it was generated with, so this is the only
    # account of A, C and rdtime -- and `init.rs` gates the ISA self-test on it,
    # because an instruction class the core lacks TRAPS rather than answering
    # wrongly. Same provenance as `SYNC_HZ` above.
    import cpu.cpu as vexii_cpu

    isa = vexii_cpu.isa_generated()
    lines += [
        "/// What `gateware/soc/cpu/cpu.py` generated the core with.",
        "///",
        "/// `misa` hardwires rv32im however the core was built, so these are",
        "/// the only account. `init.rs` runs a class only when it is here.",
    ]
    lines += [f"pub const CPU_HAS_{name.upper()}: bool = "
              f"{'true' if present else 'false'};"
              for name, present in isa.items()]
    lines += [
        "",
        "/// L1 cache geometry, as `top.py` asked for it.",
        f"pub const CPU_CACHE_SETS: u32 = {soc_module.CACHE_SETS};",
        f"pub const CPU_CACHE_WAYS: u32 = {soc_module.CACHE_WAYS};",
        "",
    ]

    path = OUT / "src" / "base.rs"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    emit(f"wrote {path.relative_to(ROOT)}")



def write_hardware(peripherals, platform, emit):
    """A Rust description of the machine, for the firmware to print (#168).

    Two tables from two authorities, neither of them typed by a person:

      * **peripherals** -- from the same walk of `decoder.bus.memory_map` that
        produces every address in `base.rs`, so the map the board reports and the
        map the firmware uses cannot disagree.
      * **connector pins** -- from the platform's `connectors` and `resources`,
        plus what `elaborate()` actually requested. That last part is why this is
        generated rather than written down: **whether a pin is free is a property
        of what the design did**, and no static table can track it.

    The size argument that used to forbid this is gone. These are `.rodata`, which
    since #162 stage two lives in a 3392 KiB flash region rather than competing for
    63 KiB of block RAM.
    """
    lines = [
        "//! What this machine contains. Generated by `scripts/soc_generate_pac.py`.",
        "//!",
        "//! Do not edit: the next run overwrites it. Both tables come from the",
        "//! SoC's own memory map and the platform's own resource list, so neither",
        "//! can drift from the design the way a hand-written table does.",
        "",
        "/// One peripheral window.",
        "pub struct Peripheral {",
        "    pub name: &'static str,",
        "    pub base: usize,",
        "    pub size: usize,",
        "    /// How many registers the memory map declares. Zero for a memory",
        "    /// window, which holds one resource spanning the whole thing.",
        "    pub registers: u16,",
        "}",
        "",
        "pub static PERIPHERALS: &[Peripheral] = &[",
    ]
    for peripheral in peripherals:
        lines.append(f'    Peripheral {{ name: "{peripheral.name.lower()}", '
                     f"base: 0x{peripheral.base:08x}, "
                     f"size: 0x{peripheral.size:08x}, "
                     f"registers: {len(peripheral.registers)} }},")
    lines.append("];")
    lines.append("")

    # --- connector pins -----------------------------------------------------
    #
    # Which resource holds each connector pin, if any. Built by expanding every
    # resource's `Pins` back to `conn:number`, because that is the form the
    # connector mapping is keyed by.
    claimed = {}
    for key, resource in platform.resources.items():
        name, number = key
        for io in getattr(resource, "ios", []):
            for pin in getattr(io, "names", []):
                claimed[pin] = f"{name}{number}" if number else name

    requested = set()
    for key in getattr(platform, "_requested", {}):
        requested.add(key if isinstance(key, tuple) else (key, 0))

    lines += [
        "/// One pin on a physical connector.",
        "pub struct ConnectorPin {",
        "    pub connector: &'static str,",
        "    /// The number printed on the connector, not an index.",
        "    pub pin: &'static str,",
        "    /// The FPGA ball.",
        "    pub ball: &'static str,",
        "    /// The platform resource that declares it, or \"\" when the pin is",
        "    /// on no resource at all.",
        "    pub resource: &'static str,",
        "    /// Whether the SoC's `elaborate()` requested that resource. A pin",
        "    /// with a resource nobody requested is FREE.",
        "    pub claimed: bool,",
        "}",
        "",
        "pub static CONNECTOR_PINS: &[ConnectorPin] = &[",
    ]
    for (conn_name, conn_number), connector in sorted(platform.connectors.items()):
        for pin, ball in connector.mapping.items():
            key = f"{conn_name}_{conn_number}:{pin}"
            resource = claimed.get(key, "")
            is_claimed = False
            for name, number in requested:
                if resource and resource == (f"{name}{number}" if number else name):
                    is_claimed = True
            lines.append(
                f'    ConnectorPin {{ connector: "{conn_name}{conn_number}", '
                f'pin: "{pin}", ball: "{ball}", '
                f'resource: "{resource}", claimed: {"true" if is_claimed else "false"} }},')
    lines.append("];")
    lines.append("")

    path = OUT / "src" / "hardware.rs"
    path.write_text("\n".join(lines))
    emit(f"wrote {path.relative_to(ROOT)}: {len(peripherals)} peripherals, "
         f"{sum(len(c.mapping) for c in platform.connectors.values())} connector pins")


def render_offsets(peripheral):
    """A generated Rust module for one peripheral's register offsets."""
    lines = [
        "/// Byte offsets from this peripheral's generated base address.",
        "pub mod offset {",
    ]
    for name, offset, _, _ in peripheral.registers:
        lines.append(f"    pub const {name.upper()}: usize = 0x{offset:02x};")
    lines.append("}")
    return "\n".join(lines)


def check_offsets(peripherals, emit):
    """Confirm the checked-in PAC exposes the offsets from this memory-map walk."""
    ok = True
    checked = 0
    for peripheral in peripherals:
        if peripheral.kind != "registers":
            continue
        path = OUT / "src" / f"{peripheral.name.lower()}.rs"
        expected = render_offsets(peripheral)
        if not path.exists() or not path.read_text().rstrip().endswith(expected):
            emit(f"  {path.relative_to(ROOT)} offsets are STALE -- regenerate the PAC")
            ok = False
        checked += len(peripheral.registers)
    emit(f"committed register offsets: {checked} checked, "
         f"{'all current' if ok else 'STALE -- see above'}")
    return ok


def write_offsets(peripherals, emit):
    """Append plain integer offsets after `form` creates each register module."""
    count = 0
    for peripheral in peripherals:
        if peripheral.kind != "registers":
            continue
        path = OUT / "src" / f"{peripheral.name.lower()}.rs"
        path.write_text(path.read_text().rstrip() + "\n\n" +
                        render_offsets(peripheral) + "\n")
        count += len(peripheral.registers)
    emit(f"wrote {count} register offsets")


CARGO_TOML = """# Generated register definitions for the Cynthion r1.4 RISC-V SoC.
#
# `src/` below the `base` module is svd2rust output and is regenerated wholesale by
# `scripts/soc_generate_pac.py`; edit the gateware, not this crate.
[package]
name = "cynthion-soc-pac"
version = "0.1.0"
edition = "2021"
license = "BSD-3-Clause"
description = "Register definitions generated from the Cynthion SoC's memory map"

[dependencies]
# The only dependency svd2rust's `--target none` output has: a `Cell` that is
# always accessed volatile, which is what makes a register read a load rather
# than something the optimiser may fold. The `riscv` crate would come in with
# `--target riscv`, which also emits a `device.x` interrupt vector table -- and
# this firmware already has its own trap handler and its own linker script, so
# that file would fight `memory.x` rather than help it.
vcell = "0.1.3"

[features]
# svd2rust guards `Peripherals::take()` on this. Nothing here enables it: taking
# the singleton needs a critical section, and this firmware's peripherals are
# reached through `target.rs` addresses rather than through the singleton. The
# feature is declared so the `cfg` is a known one and the build is warning-clean.
critical-section = []

[lints.rust]
# svd2rust generates one module per peripheral and does not use all of them.
# Warning about that on every build of the firmware would train everyone to
# ignore warnings from this crate, which is where a real one would appear.
dead_code = "allow"
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--svd-only", action="store_true",
                        help="stop after writing soc.svd")
    parser.add_argument("--check", action="store_true",
                        help="regenerate into ./tmp and report whether the "
                             "committed SVD is current; change nothing")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    status = run(args, emit)
    emit()
    return status


def run(args, emit):
    emit("Generating a PAC from the SoC's own memory map")
    emit()

    soc, platform = build_soc()
    memory_map = soc.decoder.bus.memory_map
    peripherals = walk(memory_map)

    # The PLIC source numbers, from where they are wired rather than from a
    # second list that could disagree with the wiring.
    #
    # A window declares either one number, and the interrupt is named after the
    # window, or a `{suffix: number}` mapping when it raises several -- the mux
    # gives each FUSB302B its own source, and the two are not interchangeable, so
    # they cannot share a name.
    sources = getattr(soc, "interrupt_sources", {})
    by_name = {peripheral.name: peripheral for peripheral in peripherals}
    for name, declared in sources.items():
        if name.upper() not in by_name:
            emit(f"  PROBLEM: interrupt source {declared} names {name!r}, which "
                 f"is not a window in the memory map")
            return 1
        peripheral = by_name[name.upper()]
        if isinstance(declared, dict):
            peripheral.irqs = [(f"{peripheral.name}_{suffix.upper()}", number)
                               for suffix, number in declared.items()]
        else:
            peripheral.irqs = [(peripheral.name, declared)]

    # Two wires on one source number is a handler that dispatches to the wrong
    # device, and it is invisible until the wrong device is the one asserting.
    taken = {}
    for peripheral in peripherals:
        for irq_name, number in peripheral.irqs:
            if number in taken:
                emit(f"  PROBLEM: source {number} is claimed by both "
                     f"{taken[number]} and {irq_name}")
                return 1
            taken[number] = irq_name

    emit(f"{len(peripherals)} peripherals:")
    for peripheral in peripherals:
        irq = "".join(f"  irq {number}" for _, number in peripheral.irqs)
        emit(f"  {peripheral.name:<16} 0x{peripheral.base:08x} "
             f"+0x{peripheral.size:<8x} {len(peripheral.registers):>2} regs"
             f"{irq}")
    emit()

    too_wide = [(peripheral.name, name, footprint)
                for peripheral in peripherals
                for name, _, footprint, _ in peripheral.registers
                if peripheral.kind == "registers" and footprint not in FOOTPRINTS]
    for name, register, footprint in too_wide:
        emit(f"  PROBLEM: {name}.{register} occupies {footprint} bytes, which is "
             f"not a width a load instruction has")
    if too_wide:
        return 1

    if not cross_check(peripherals, emit):
        emit()
        emit("A hand-written constant disagrees with the memory map. That is the "
             "defect this generator exists to find; fix the source, do not "
             "regenerate over it.")
        return 1
    emit()

    document = build_svd(peripherals, "Cynthion r1.4 VexiiRiscv SoC, generated "
                                      "from its Amaranth memory map")
    ET.indent(document, space="  ")
    svd_path = (ROOT / "tmp" / "soc.svd") if args.check else (OUT / "soc.svd")
    svd_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(document).write(svd_path, encoding="utf-8",
                                   xml_declaration=True)
    emit(f"wrote {svd_path.relative_to(ROOT)} ({svd_path.stat().st_size} bytes)")

    if not verify_svd(svd_path, peripherals, emit):
        return 1

    if args.check:
        committed = OUT / "soc.svd"
        if not committed.exists():
            emit("no committed soc.svd to compare against")
            return 1
        current = committed.read_bytes() == svd_path.read_bytes()
        emit("committed soc.svd is current" if current
             else "committed soc.svd is STALE -- run without --check")
        offsets_current = check_offsets(peripherals, emit)
        return 0 if current and offsets_current else 1

    if args.svd_only:
        emit("stopping after the SVD, as asked")
        return 0

    for tool in ("svd2rust", "form"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            emit(f"{tool} is not installed. `cargo install {tool}` puts it in "
                 f"~/.cargo/bin; nothing here installs it for you.")
            return 1

    # svd2rust emits into the working directory, so run it there. `--target none`
    # rather than `riscv`: the riscv target also emits `device.x`, a vector table
    # for `riscv-rt`'s interrupt dispatch, and this firmware traps through its own
    # handler in `src/irq.rs` behind its own `memory.x`.
    result = subprocess.run(
        ["svd2rust", "-i", str(svd_path), "--target", "none"],
        cwd=OUT, capture_output=True, text=True)
    if result.returncode != 0:
        emit("svd2rust failed:")
        emit((result.stderr or result.stdout).strip()[-1500:])
        return 1
    emit("svd2rust ok")

    # `form` splits the single generated lib.rs into a module tree, which is what
    # makes the result readable and diffable -- a one-file PAC shows up in review
    # as several thousand changed lines whatever moved.
    src = OUT / "src"
    if not (OUT / "lib.rs").exists():
        emit("svd2rust produced no lib.rs")
        return 1
    src.mkdir(exist_ok=True)
    result = subprocess.run(["form", "-i", "lib.rs", "-o", "src/"],
                            cwd=OUT, capture_output=True, text=True)
    if result.returncode != 0:
        emit("form failed:")
        emit((result.stderr or result.stdout).strip()[-800:])
        return 1
    (OUT / "lib.rs").unlink()
    emit("form ok")

    write_bases(peripherals, emit)
    write_hardware(peripherals, platform, emit)
    write_offsets(peripherals, emit)
    # `form` writes src/lib.rs itself, so the module declaration has to go on
    # afterwards or the next run silently drops it.
    lib = OUT / "src" / "lib.rs"
    lib.write_text(lib.read_text() + "\npub mod base;\npub mod hardware;\n")
    (OUT / "Cargo.toml").write_text(CARGO_TOML)

    # svd2rust emits one enormous line per file and `form` does not reflow it, so
    # without this the crate is unreadable and every regeneration shows up in
    # review as a single changed line. Needs the Cargo.toml written above, so it
    # goes here and not next to `form`. Not fatal if rustfmt is absent -- the code
    # compiles either way -- but say so, because a reviewer looking at one-line
    # files should know why.
    result = subprocess.run(["cargo", "fmt"], cwd=OUT, capture_output=True,
                            text=True)
    emit("cargo fmt ok" if result.returncode == 0
         else "cargo fmt unavailable; the generated crate stays unformatted")

    result = subprocess.run(["cargo", "build", "--release"],
                            cwd=OUT, capture_output=True, text=True)
    emit(f"cargo build: {'ok' if result.returncode == 0 else 'FAILED'}")
    if result.returncode != 0:
        emit((result.stderr or result.stdout).strip()[-1500:])
        return 1

    emit()
    emit(f"crate in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
