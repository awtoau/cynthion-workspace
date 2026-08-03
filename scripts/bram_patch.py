#!/usr/bin/env python3
#
# Rewrite the block RAM init of a built bitstream, by location, without resynthesis.
# SPDX-License-Identifier: BSD-3-Clause

"""
Swap firmware into a built bitstream in ~22 s instead of rebuilding it in ~95 s.

    ./scripts/bram_patch.py --firmware tmp/rust_fw.bin --bootloader tmp/rust_boot.bin

Measured on this design: 95 s for a full rebuild, 22 s here, of which 20 s is the
source check and 2 s is the rewrite, `ecppack` and the read-back. Skipping the source
check gives ~2 s and gives up the guarantee; see below.

When only the firmware changes the logic is unchanged -- same netlist, same placement,
same routing. The only delta is 512 Kibit of block RAM initialisation. This locates
those bits in `top.config` and rewrites them, then re-runs `ecppack`.

## How the bits are located

`ecpbram` searches BY VALUE and refuses on real firmware: an image is ~87 % zeroes and
those zeroes also fill every unused block RAM on the die, so the pattern is not unique.
This addresses them instead, through a chain that is entirely in the build's own output:

| step                | source                                    |
|---------------------|-------------------------------------------|
| memory -> 32 slices | bit *k* of every word goes to `ram.mem.0.k` |
| slice -> init words | 16384 bits packed 8 per 9-bit init word   |
| slice -> WID        | matched against `top.config`, bijectively |
| WID -> tile         | `word: EBRn.WID` in the EBR `.tile_group` |

Only the third step is a search, and it searches for a *derived* 18432-bit pattern that
must match exactly one block, with all 32 forming a bijection onto tiles configured
`DATA_WIDTH_A=1, DATA_WIDTH_B=18`. Nothing else on the die is configured that way.

## What makes a wrong result loud

Four checks, all of which refuse rather than warn:

- **Freshness.** The firmware image is rewritten from the ELF before anything is read,
  so the fast path cannot patch a stale intermediate. `--no-derive` skips it and says
  so loudly. This one is newest and was the missing one: see #155, where its absence
  produced a bitstream several edits behind while every layer reported success.
- **Layout.** All 32 slices derived from `firmware.hex` must match distinct blocks. If
  `firmware.hex` is not what was synthesised, they do not match and it stops.
- **Source.** The design is re-elaborated from the tree and its RTLIL compared with the
  `top.il` in the build directory. Any gateware edit, package upgrade or VexiiRiscv
  flag change since the build makes them differ and it stops. ~20 s;
  `--no-verify-source` skips it and says so loudly.
- **Round trip.** The packed `.bit` is unpacked again with `ecpunpack` and the 32 blocks
  compared with what was intended. A patch that did not land is caught here.

## Two things the design does that stop RTLIL being reproducible

Neither is a difference in logic, and both are reconciled rather than ignored -- if
anything else moved, the comparison still fails.

- `gateware_id.py` packs `datetime.now()` into a register. The build's own stamp is
  read back out of its `top.il` and the design elaborated a second time with it pinned,
  which is why the source check costs two elaborations rather than one.
- LUNA names some generated modules after `id(self)`, so a module carries a different
  name each run under ASLR. Those names are normalised on both sides.

## What it does not check

The tool versions. A `top.config` packed by a different `nextpnr-ecp5` than the one on
the path today is still valid logic for the same design, so this is not a correctness
gap -- but it means a patched bitstream is not a claim about the current toolchain.

## Why the result is not compared against a full rebuild

It cannot be. `--parallel-refine` makes nextpnr non-deterministic: two rebuilds of the
*same* firmware give different `.config` files and different `.bit` files. What was
verified instead, and is the stronger statement: the patched config differs from the
one it came from in nothing but the 32 `.bram_init` blocks, and the patched `.bit`
unpacks to exactly the same 64 KiB block RAM image as a full rebuild of the new
firmware.
"""

import argparse
import hashlib
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "bram_patch.log"
BUILD = ROOT / "tmp" / "vexii_hello" / "build"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

# The default is the same intermediate `soc_run.py` writes, and knowing that it is
# the default is what lets this tool regenerate it. An explicitly-given `--firmware`
# is the caller's own file and is left alone. See #155.
DEFAULT_FIRMWARE = ROOT / "tmp" / "rust_fw.bin"

# The block RAM is 32 bits wide and yosys splits it one bit per DP16KD, so slice k
# holds bit k of every word. Each DP16KD stores its 16384 bits as 2048 init words of
# 9 bits, of which 8 are used -- address a lands in word a >> 3, bit a & 7.
SLICES = 32
INIT_WORDS = 2048
BITS_PER_INIT_WORD = 8

# The only tiles that can belong to this memory. A 1-bit-wide port A is what splitting
# a 32-bit memory across 32 primitives produces, and nothing else in this design has it.
RAM_TILE_WIDTHS = (1, 18)


class Refuse(Exception):
    """A check failed. Nothing is written."""


def emit(text, handle):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def rel(path):
    """`path` relative to the repo when it is inside it, absolute when it is not.

    `--firmware` and `--output` may name a file anywhere, and bare
    `Path.relative_to` raises for anything outside the tree -- so the reporting
    path would crash on exactly the unusual invocation it was meant to describe.
    """
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


# --------------------------------------------------------------------------- config


def parse_config(text):
    """Return {wid: [init words]} and {wid: {tile, dwa, dwb}} from a `.config`."""
    inits = {}
    for match in re.finditer(r"^\.bram_init (\d+)\n((?:[0-9a-f ]+\n)+)", text, re.M):
        inits[int(match.group(1))] = [int(t, 16) for t in match.group(2).split()]

    tiles = {}
    for match in re.finditer(
            r"^\.tile_group ([^\n]*MIB_EBR[^\n]*)\n((?:\w+: [^\n]+\n)+)", text, re.M):
        body = match.group(2)
        wid = re.search(r"word: EBR\d\.WID (\d+)", body)
        if not wid:
            continue
        # The WID word is written least significant bit first.
        value = sum(int(bit) << i for i, bit in enumerate(wid.group(1)))
        dwa = re.search(r"DATA_WIDTH_A (\d+)", body)
        dwb = re.search(r"DATA_WIDTH_B (\d+)", body)
        tiles[value] = dict(tile=match.group(1).split()[0],
                            dwa=int(dwa.group(1)) if dwa else None,
                            dwb=int(dwb.group(1)) if dwb else None)
    return inits, tiles


def slices_of(words):
    """Pack 16384 32-bit words into the 32 per-slice init images."""
    out = []
    for bit in range(SLICES):
        image = []
        for base in range(0, len(words), BITS_PER_INIT_WORD):
            value = 0
            for offset in range(BITS_PER_INIT_WORD):
                value |= ((words[base + offset] >> bit) & 1) << offset
            image.append(value)
        out.append(image)
    return out


def locate(inits, tiles, old_words):
    """Map slice -> WID, or refuse.

    The mapping is a bijection onto tiles the placer configured 1/18, established by
    matching each slice's 18432 derived bits against the blocks in the bitstream. A
    stale `firmware.hex` does not match and is caught here rather than shipped.
    """
    candidates = {wid for wid, t in tiles.items()
                  if (t["dwa"], t["dwb"]) == RAM_TILE_WIDTHS}
    if len(candidates) != SLICES:
        raise Refuse(f"{len(candidates)} block RAMs are configured "
                     f"{RAM_TILE_WIDTHS[0]}/{RAM_TILE_WIDTHS[1]}, expected {SLICES}. "
                     f"This config was not built from this design.")

    mapping = {}
    for index, image in enumerate(slices_of(old_words)):
        hits = [wid for wid in candidates if inits.get(wid) == image]
        if len(hits) != 1:
            raise Refuse(
                f"slice {index} matches {len(hits)} block RAMs {hits}, expected 1.\n"
                f"  {BUILD.name}/firmware.hex is not what {BUILD.name}/top.config was "
                f"built from, or the memory's layout has changed. Rebuild.")
        mapping[index] = hits[0]

    if len(set(mapping.values())) != SLICES:
        raise Refuse("two slices matched the same block RAM; the mapping is not a "
                     "bijection. Rebuild.")
    return mapping


def rewrite(text, mapping, new_slices):
    """Return the config with the 32 mapped blocks replaced."""
    replaced = 0

    def substitute(match):
        nonlocal replaced
        wid = int(match.group(1))
        if wid not in mapping.values():
            return match.group(0)
        index = next(i for i, w in mapping.items() if w == wid)
        body = "".join(
            " ".join(f"{word:03x}" for word in new_slices[index][row * 8:row * 8 + 8])
            + "\n"
            for row in range(INIT_WORDS // 8))
        replaced += 1
        return f".bram_init {wid}\n{body}"

    out = re.sub(r"^\.bram_init (\d+)\n((?:[0-9a-f ]+\n)+)", substitute, text, flags=re.M)
    if replaced != SLICES:
        raise Refuse(f"rewrote {replaced} blocks, expected {SLICES}")
    return out


# --------------------------------------------------------------------------- image


def assemble(image_path, boot_path, soc):
    """Build the 64 KiB init image exactly as the gateware build does."""
    boot = boot_path.read_bytes() if boot_path and boot_path.exists() else b""
    if boot and len(boot) > soc.IMAGE_ORIGIN:
        raise Refuse(f"bootloader is {len(boot)} bytes, over IMAGE_ORIGIN "
                     f"({soc.IMAGE_ORIGIN})")
    image = image_path.read_bytes()
    origin = soc.IMAGE_ORIGIN if boot else 0
    if origin + len(image) > soc.RAM_SIZE:
        raise Refuse(f"image is {len(image)} bytes at {origin:#x}, block RAM is "
                     f"{soc.RAM_SIZE}")
    raw = (boot + b"\x00" * (soc.IMAGE_ORIGIN - len(boot)) + image) if boot else image
    raw += b"\x00" * (soc.RAM_SIZE - len(raw))
    return [int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)]


# ------------------------------------------------------------------- source identity


def elaborate_il(words, soc):
    """Re-elaborate the design and return its RTLIL.

    This is the check that the build directory belongs to the tree as it stands. It
    costs an sbt run and an Amaranth elaboration -- about 9 s of the ~95 s a rebuild
    takes -- and unlike a source hash it compares the thing that was actually built.
    """
    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4
    plan = CynthionPlatformRev1D4().prepare(soc.HelloSoC(firmware=words), "top")
    return plan.files["top.il"]


# LUNA names some generated modules after `id(self)`, so the same design elaborates to
# the same logic under a different module name every run. Normalised on both sides: a
# name that differs while everything it names is identical cannot reach the bitstream.
PYTHON_ID = re.compile(r"_\d{10,}\b")


def built_stamp(fresh, built):
    """Read the build's own `gateware_id` timestamp out of its RTLIL.

    `gateware_id.py` packs `datetime.now()` into a register, so no two elaborations of
    identical source are byte-identical and a bare comparison can never pass. This
    recovers the built-in timestamp so the design can be elaborated again *with* it,
    which turns the comparison back into an exact one instead of an approximate one.

    The 32-bit lines that differ must all carry the same pair of constants, and both
    must unpack to a plausible date. Anything else and there is nothing to recover.
    """
    old = new = None
    for line_fresh, line_built in zip(fresh.splitlines(), built.splitlines()):
        if line_fresh == line_built:
            continue
        pair = [re.findall(r"32'([01]{32})", line) for line in (line_fresh, line_built)]
        if not (len(pair[0]) == len(pair[1]) == 1):
            continue                       # a narrower slice of the same word
        candidate = (int(pair[0][0], 2), int(pair[1][0], 2))
        if old is None:
            new, old = candidate
        elif (new, old) != candidate:
            return None                    # more than one constant moved
    if old is None:
        return None

    for stamp in (old, new):
        year, month, day = 2000 + (stamp >> 26), (stamp >> 22) & 0xF, (stamp >> 17) & 0x1F
        hour, minute, second = (stamp >> 12) & 0x1F, (stamp >> 6) & 0x3F, stamp & 0x3F
        if not (2020 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31
                and hour < 24 and minute < 60 and second < 60):
            return None
    return old if new >= old else None


# --------------------------------------------------------------------------- packing


def ecppack_command(build_dir, config, bit):
    """The build's own ecppack line, with the files swapped.

    Read from `build_top.sh` rather than written out here, so `--compress --freq 38.8`
    and anything the platform adds later stay in step without being duplicated.
    """
    for line in (build_dir / "build_top.sh").read_text().splitlines():
        if "ECPPACK" in line and "--input" in line:
            args = line.replace('"$ECPPACK"', "ecppack").split()
            out = []
            skip = False
            for index, token in enumerate(args):
                if skip:
                    skip = False
                    continue
                if token == "--input":
                    out += ["--input", str(config)]
                    skip = True
                elif token == "--bit":
                    out += ["--bit", str(bit)]
                    skip = True
                elif token == "--svf":
                    skip = True
                else:
                    out.append(token)
            return out
    raise Refuse("no ecppack line in build_top.sh")


def run_trellis(argv, cwd):
    """Run a Project Trellis tool with the OSS CAD Suite environment sourced."""
    command = ('source "$HOME/opt/oss-cad-suite/environment" && '
               + " ".join(f"'{a}'" for a in argv))
    return subprocess.run(["bash", "-c", command], cwd=cwd,
                          capture_output=True, text=True)


# ------------------------------------------------------------------------------ main


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--firmware", type=Path, default=DEFAULT_FIRMWARE,
                        help="the image, linked for IMAGE_ORIGIN")
    parser.add_argument("--no-derive", action="store_true",
                        help="do not regenerate the firmware image from the ELF "
                             "first. UNSAFE: this tool's whole purpose is the fast "
                             "path, and on that path the image is stale by "
                             "construction unless something rewrites it")
    parser.add_argument("--bootloader", type=Path, default=ROOT / "tmp" / "rust_boot.bin",
                        help="the resident bootloader, linked for 0x0")
    parser.add_argument("--build-dir", type=Path, default=BUILD)
    parser.add_argument("--output", type=Path,
                        help="where to write the patched bitstream "
                             "(default: in place, over top.bit)")
    parser.add_argument("--no-verify-source", action="store_true",
                        help="skip re-elaborating the design to prove the build "
                             "directory matches the tree. UNSAFE: a gateware edit "
                             "since the build then yields a bitstream with stale logic")
    parser.add_argument("--no-round-trip", action="store_true",
                        help="skip unpacking the result and comparing it")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        try:
            return patch(args, handle)
        except Refuse as failure:
            emit("REFUSED: " + str(failure), handle)
            emit("Nothing was written. Rebuild with scripts/soc_run.py.", handle)
            return 1


def refresh_firmware(args, handle):
    """Rewrite the firmware image from the ELF, then say exactly what will be patched.

    #155. This tool patched from `tmp/rust_fw.bin` and never checked whether that
    file was current. It is only written as part of a full `soc_run.py`, which is
    precisely the case where patching is not needed -- so on the fast path this
    tool exists to serve, its input was stale by construction. The patch then
    "succeeded", reported `0 of them changed`, verified its own round trip, and
    loaded firmware several edits behind the source tree. Every layer reported
    success.

    Deriving beats checking. A staleness check would still leave a window between
    the check and the read, and would have to be right about which of the ELF, the
    crate sources and the linker script counts as newer. Rewriting the file from
    the ELF makes the question not arise.

    The neighbouring gateware check is the model: it REFUSES, says the design
    elaborates differently than the build directory holds, and names the fix. The
    firmware side had no equivalent; this is it.
    """
    if args.firmware != DEFAULT_FIRMWARE:
        emit(f"firmware: {rel(args.firmware)} (given explicitly, "
             f"not derived)", handle)
    elif args.no_derive:
        emit("", handle)
        emit("*** --no-derive: the firmware image was NOT rebuilt from the ELF.", handle)
        emit("*** If the crate has been rebuilt since this file was written, the", handle)
        emit("*** bitstream this produces carries the OLD firmware, and a report", handle)
        emit("*** of `0 of them changed` below will be indistinguishable from", handle)
        emit("*** success. See #155.", handle)
        emit("", handle)
    else:
        import soc_run
        if not soc_run.ELF.exists():
            raise Refuse(
                f"no {rel(soc_run.ELF)}; there is no compiled firmware "
                "to derive an image from. Build it with scripts/soc_run.py.")
        sections, _flash = soc_run.derive_bram_bin(lambda line: emit(line, handle))
        if sections is None:
            raise Refuse("could not derive the firmware image from the ELF; the "
                         "objcopy output above says why. Nothing was patched.")

    if not args.firmware.exists():
        raise Refuse(f"no {rel(args.firmware)} to patch from")

    # Say WHICH bytes are about to be compared, so a later `0 of them changed` is
    # qualified by an identity rather than standing on its own.
    raw = args.firmware.read_bytes()
    stamp = datetime.fromtimestamp(args.firmware.stat().st_mtime).isoformat(
        timespec="seconds")
    emit(f"patching from {rel(args.firmware)}: {len(raw)} bytes, "
         f"{hashlib.sha256(raw).hexdigest()[:12]}, written {stamp}", handle)


def patch(args, handle):
    start = time.monotonic()
    refresh_firmware(args, handle)
    build_dir = args.build_dir
    config_path = build_dir / "top.config"
    hex_path = build_dir / "firmware.hex"
    for path in (config_path, hex_path, build_dir / "build_top.sh"):
        if not path.exists():
            raise Refuse(f"no {path.relative_to(ROOT)}; there is no build to patch")

    import vexii_hello_soc as soc

    old_words = [int(line, 16) for line in hex_path.read_text().split()]
    if len(old_words) != soc.RAM_SIZE // 4:
        raise Refuse(f"firmware.hex has {len(old_words)} words, expected "
                     f"{soc.RAM_SIZE // 4}")
    new_words = assemble(args.firmware, args.bootloader, soc)

    text = config_path.read_text()
    inits, tiles = parse_config(text)
    emit(f"{len(inits)} block RAMs in {config_path.relative_to(ROOT)}", handle)

    mapping = locate(inits, tiles, old_words)
    emit(f"located all {SLICES} slices of the firmware memory:", handle)
    for index in sorted(mapping)[:4]:
        emit(f"  slice {index:2d} -> WID {mapping[index]:3d} "
             f"{tiles[mapping[index]]['tile']}", handle)
    emit(f"  ... and {SLICES - 4} more", handle)

    # The source check. Elaborating with the OLD firmware must reproduce the build's
    # own RTLIL byte for byte: that ties the build directory to the tree as it stands
    # and to firmware.hex at the same time.
    if args.no_verify_source:
        emit("", handle)
        emit("*** --no-verify-source: the design was NOT re-elaborated.", handle)
        emit("*** If any gateware source changed since this build, the bitstream", handle)
        emit("*** this writes carries the OLD logic with the NEW firmware.", handle)
        emit("", handle)
    else:
        il_path = build_dir / "top.il"
        if not il_path.exists():
            raise Refuse("no top.il in the build directory to compare against")
        mark = time.monotonic()
        built = PYTHON_ID.sub("_id", il_path.read_text())
        fresh = PYTHON_ID.sub("_id", elaborate_il(old_words, soc))
        if fresh != built:
            # The design stamps its own build time into a register, so the first
            # elaboration never matches. Recover that stamp and elaborate once more
            # with it pinned, which restores an exact comparison rather than an
            # approximate one -- if anything but the stamp moved, this still fails.
            stamp = built_stamp(fresh, built)
            if stamp is not None:
                import gateware_id
                gateware_id.pack_built = lambda when, word=stamp: word
                fresh = PYTHON_ID.sub("_id", elaborate_il(old_words, soc))
        if fresh != built:
            raise Refuse(
                "the design elaborates differently than the build directory holds.\n"
                f"  fresh   {hashlib.sha256(fresh.encode()).hexdigest()[:16]} "
                f"{len(fresh)} bytes\n"
                f"  top.il  {hashlib.sha256(built.encode()).hexdigest()[:16]} "
                f"{len(built)} bytes\n"
                "  Gateware, a package or a VexiiRiscv flag has changed since the "
                "build. Patching would give this bitstream's OLD logic. Rebuild.")
        emit(f"source verified: RTLIL reproduces top.il exactly "
             f"({time.monotonic() - mark:.1f} s)", handle)

    new_slices = slices_of(new_words)
    patched = rewrite(text, mapping, new_slices)
    changed = sum(1 for index in range(SLICES)
                  if inits[mapping[index]] != new_slices[index])
    # `0 of them changed` was the reassuring reading of a failure: indistinguishable
    # from "your firmware was already in there", which is normal and welcome. It is
    # now trustworthy, because the image above was derived from the ELF moments ago
    # rather than found lying in tmp/ -- but it still says which of the two cases it
    # is, since the whole lesson of #155 is that a number with one reading is worth
    # less than a sentence with one meaning.
    emit(f"rewrote {SLICES} blocks, {changed} of them changed", handle)
    if changed == 0:
        if args.no_derive or args.firmware != DEFAULT_FIRMWARE:
            emit("  the bitstream already held these bytes -- but this image was "
                 "NOT derived from the ELF, so that may instead mean it is stale",
                 handle)
        else:
            emit("  the bitstream already held this exact firmware; the image was "
                 "derived from the ELF above, so this is a genuine no-op", handle)

    out_config = build_dir / "top.patched.config"
    out_config.write_text(patched)

    # Prove the text landed before spending ecppack on it.
    check_inits, _ = parse_config(out_config.read_text())
    for index in range(SLICES):
        if check_inits[mapping[index]] != new_slices[index]:
            raise Refuse(f"slice {index} did not land in the rewritten config")

    bit = args.output or (build_dir / "top.bit")
    mark = time.monotonic()
    result = run_trellis(ecppack_command(build_dir, out_config, bit), build_dir)
    if result.returncode != 0:
        raise Refuse("ecppack failed:\n" + (result.stderr or result.stdout)[-600:])
    emit(f"packed {bit.relative_to(ROOT)} ({time.monotonic() - mark:.1f} s)", handle)

    if not args.no_round_trip:
        mark = time.monotonic()
        unpacked = build_dir / "top.roundtrip.config"
        result = run_trellis(["ecpunpack", str(bit), str(unpacked)], build_dir)
        if result.returncode != 0:
            raise Refuse("ecpunpack failed:\n" + (result.stderr or result.stdout)[-600:])
        back, _ = parse_config(unpacked.read_text())
        for index in range(SLICES):
            if back.get(mapping[index]) != new_slices[index]:
                raise Refuse(f"slice {index} does not read back from the bitstream")
        emit(f"round trip verified: all {SLICES} slices read back from the .bit "
             f"({time.monotonic() - mark:.1f} s)", handle)
        unpacked.unlink()

    # Only now, with the bitstream proven, does the recorded init become the new one.
    hex_path.write_text("".join(f"{word:08x}\n" for word in new_words))
    config_path.write_text(patched)
    out_config.unlink()

    emit(f"done in {time.monotonic() - start:.1f} s", handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
