#!/usr/bin/env python3
#
# Build the Rust firmware, build the bitstream, load it, and read the console.
# SPDX-License-Identifier: BSD-3-Clause

"""
One command for the whole SoC loop: cargo, objcopy, gateware, configure, console.

This existed as a four-step shell incantation that was being pasted by hand, with the
`AMARANTH_nextpnr_opts` speedup remembered or forgotten each time. Every step here was
already being run; the value is that none of them can now be skipped or mistyped.

    ./scripts/soc_run.py                 # build everything, load, read the console
    ./scripts/soc_run.py --no-build      # just load what is already built, and read
    ./scripts/soc_run.py --c-firmware    # the C generator instead of the Rust crate
    ./scripts/soc_run.py --skip-tests    # configure without the QEMU gate first

## The gate

`scripts/soc_test.py` runs the same shell under QEMU and asserts what it says, and this
refuses to configure the board if it fails. It goes FIRST, before cargo and before the
~60 s gateware build, because the whole point is to not spend a minute of synthesis and
a reconfigure discovering something an emulator could have said in three seconds.

It only covers the Rust shell's logic -- it cannot see the USB console peripheral, the
HyperRAM, or the flash, all of which are stubbed on that target. A pass means "if the
board misbehaves, the shell's logic is not why", which is exactly the question that has
been expensive to answer by hand. `--c-firmware` skips it: the gate tests the Rust crate,
which that path does not build.

## What it does to the board

Configures the FPGA over JTAG. **SRAM only** -- nothing is written to flash, so a power
cycle restores whatever was there. Two RISC-V images are baked into the bitstream as
block RAM init: the resident bootloader at 0x0 and the shell at 0x400. That is why a
change to either needs the gateware rebuilt, about a minute -- and why a change to the
shell alone does not have to: `scripts/soc_jtag_stage.py` stages one over the other in
seconds, and the bootloader runs it on the way back up.

## Why the console read is at the end

The banner prints once at reset and the host takes ~0.5 s to enumerate, so anything
that configures and then opens the port misses it. This opens the port first where it
can, and otherwise reports what it caught. A reader that silently misses the banner is
worse than one that says it did.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "firmware" / "cynthion-soc"
ELF = CRATE / "target" / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc"
FIRMWARE_BIN = ROOT / "tmp" / "rust_fw.bin"

# Where the image sits inside the 64 KiB block RAM initialiser. The bootloader owns
# the kilobyte below it; see firmware/cynthion-soc/memory.x.
IMAGE_ORIGIN = 0x400

# Sections the linker placed in flash, extracted separately because flash loads by a
# different path from block RAM. The offset is moondancer's established firmware slot,
# clear of the FPGA configuration at flash offset zero.
RODATA_BIN = ROOT / "tmp" / "rust_rodata.bin"
# What we last wrote to flash, and where. Compared before writing again.
FLASH_WRITTEN = ROOT / "tmp" / "flash-written.txt"
FLASH_RODATA_OFFSET = 0x000b_0000

# The memory-mapped flash window, for deciding which sections load by
# programming the part rather than by bitstream init. Must match FLASH_BASE
# and FLASH_SIZE in gateware/soc/top.py.
FLASH_BASE = 0x1000_0000
FLASH_SIZE = 0x0040_0000

# The resident bootloader: 492 bytes at 0x0, and what the reset vector points at.
# Built alongside the image and packed into the same block RAM init, so one bitstream
# carries both and a board with nothing staged comes up on the image below.
BOOT_CRATE = ROOT / "firmware" / "cynthion-boot"
BOOT_ELF = (BOOT_CRATE / "target" / "riscv32imac-unknown-none-elf" / "release"
            / "cynthion-boot")
BOOT_BIN = ROOT / "tmp" / "rust_boot.bin"
GATEWARE = ROOT / "gateware" / "soc" / "top.py"
# Where the synthesis tools' own output goes. Thousands of lines per build, of
# which about four are worth a person's attention -- so they are diverted here
# rather than interleaved into `tmp/logs/dev.log`, which is the log about the
# board.
SYNTH_LOG = ROOT / "tmp" / "logs" / "synthesis.log"
BITSTREAM = ROOT / "tmp" / "awto_soc" / "build" / "top.bit"
# The digest of the gateware sources the bitstream beside it was built from.
# Written on a successful build, compared before every configure, and compared
# again before synthesis so an unchanged tree does not resynthesise. See
# `bitstream_is_stale`.
GATEWARE_BUILT = ROOT / "tmp" / "awto_soc" / "build" / "gateware-digest.txt"

sys.path.insert(0, str(ROOT / "gateware"))

# Imported rather than restated. `fast_build_env.py` held a byte-identical copy of this
# string and the reasoning behind it, and `./dev.py audit` found it by reporting it as
# unreachable -- two definitions of the same build flags, one of which nothing called.
# The next person to tune one would have tuned the wrong one.
#
# Measured on this design: 64 s -> 59 s, with no change to utilisation. --threads alone
# does nothing, which is the trap: nextpnr's SA refinement is 16 of its 24 seconds and is
# serial unless --parallel-refine is passed. --router router2 recovers the Fmax that
# --parallel-refine on its own gives up. Full workings in fast_build_env.py.
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, spawn  # noqa: E402
from fast_build_env import NEXTPNR_OPTS  # noqa: E402

# The recorder every build goes through. Area, timing and the full configuration
# land in Postgres from one unconditional call below, so there is no way to
# produce a bitstream without a row describing it -- and if the database is not
# there, the record is spooled rather than lost. See #232 and
# scripts/build_metrics.py.
import build_metrics  # noqa: E402


def run(cmd, cwd=None, env=None, shell=False):
    return subprocess.run(cmd, cwd=cwd or ROOT, env=env, shell=shell,
                          capture_output=True, text=True)


def firmware_digest(path=None):
    """A short hash of the firmware image, for saying WHICH firmware.

    The provenance already in the build -- a short git hash from `build.rs` into
    USERCODE -- cannot answer this. A git hash does not move when a file is edited
    and not committed, which is the common case while working, and is exactly the
    case where a stale load is hardest to notice.

    So: hash the bytes. `soc_run.py` prints this for what it built and again for
    what it read back out of the bitstream, and the two must agree.
    """
    import hashlib
    path = path or FIRMWARE_BIN
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def firmware_in_bitstream(build_dir, emit):
    """The firmware bytes the built bitstream actually carries, or None.

    Reads them back out of `firmware.hex`, which the gateware build writes beside
    the bitstream as the block RAM initialiser. That is one step removed from the
    .bit itself -- `scripts/bram_patch.py` goes all the way and unpacks the
    bitstream -- but it is the file the build hands to the packer, so a mismatch
    here means the image and the firmware came from different runs.
    """
    hex_path = build_dir / "firmware.hex"
    if not hex_path.exists():
        emit(f"  no {hex_path.name} beside the bitstream; cannot verify")
        return None
    words = []
    for line in hex_path.read_text().split():
        try:
            words.append(int(line, 16))
        except ValueError:
            return None
    return b"".join(word.to_bytes(4, "little") for word in words)


# Environment variables `gateware/soc/top.py` reads at import time. They change
# what is elaborated without changing a byte of source, so they are part of the
# bitstream's identity -- see `gateware_digest`. Anything added to `top.py` as an
# `os.environ.get` that alters the design belongs here.
# Each entry is (name, default), and the digest hashes the RESOLVED value.
#
# Hashing the raw environment string was wrong: unset and set-to-the-default
# produce identical gateware but different digests, so an ordinary run after a
# sweep resynthesised for no reason -- and, worse, refused a `--firmware-only`
# load as STALE when the bitstream was in fact correct.
#
# The defaults must match `top.py`'s. They are duplicated here rather than
# imported because importing `top` pulls in the whole Amaranth elaboration just
# to read three strings.
VARIANT_ENV = (
    ("CYNTHION_HYPERRAM_BIST", ""),
    ("CYNTHION_HYPERRAM_CK_MHZ", "100"),
    ("CYNTHION_HYPERRAM_BIST_DQS", "1"),
)


def variant_settings():
    """The variant selection as `top.py` will resolve it, normalised."""
    out = []
    for name, default in VARIANT_ENV:
        value = os.environ.get(name, default) or default
        # `top.py` treats "" and "0" alike for the two flags, so normalise them
        # to one spelling; otherwise `=0` and unset hash differently.
        if name != "CYNTHION_HYPERRAM_CK_MHZ" and value in ("", "0"):
            value = "0"
        out.append(f"{name}={value}")
    return out


def gateware_digest():
    """A hash of everything that determines the bitstream's content.

    CONTENT, not modification time. See `bitstream_is_stale`.

    **The git hash is in here, and it has to be.** The gateware stamps `HEAD`
    into USERCODE, so two bitstreams built from byte-identical sources at
    different commits are different bitstreams -- and `soc_probe` compares that
    USERCODE against the firmware's own build stamp to catch a stale load. A
    digest over the sources alone would let a commit skip synthesis and leave the
    board stamped with the previous one, which turns that check into a false
    positive. A check that cries wolf is worse than the staleness it was added to
    catch.

    So a commit costs a resynthesis. The saving is on the case that was actually
    wasting the time: uncommitted iteration, and branch switches that rewrite
    mtimes without changing a byte.

    **The variant environment is in here too, and for a sharper reason.** The
    BIST build (#226) is selected by `CYNTHION_HYPERRAM_BIST` rather than by
    editing a file, so two completely different bitstreams -- one with the
    HyperRAM on the bus, one with the engine owning the pins -- hash identically
    from their sources. Without these, switching variants SKIPS SYNTHESIS and
    configures the board with the other one, while printing "the bitstream was
    built from these exact sources".

    That is not a slow build, it is a wrong board: a shipping build reloaded to
    act as a control silently reconfigured with the measurement bitstream, and
    the control it was supposed to provide was worthless. Any variable that
    changes what `top.py` elaborates belongs on this list.
    """
    import hashlib
    digest = hashlib.sha256()
    for source in sorted((ROOT / "gateware" / "soc").rglob("*.py")):
        digest.update(source.relative_to(ROOT).as_posix().encode())
        digest.update(source.read_bytes())
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True)
    digest.update(head.stdout.strip().encode())
    for setting in variant_settings():
        digest.update(setting.encode())
    return digest.hexdigest()[:16]


# Where built bitstreams are kept, keyed by the gateware digest.
#
# Every CK rung is a bitstream, and ~20 were built and thrown away in one
# session because the build directory holds exactly one. Re-visiting a rung then
# costs another ~130 s of synthesis for a file that already existed.
#
# The digest already identifies a bitstream exactly -- sources, HEAD and the
# variant environment -- so it is the natural key. A hit is a copy; a miss is a
# build.
BITCACHE = ROOT / "tmp" / "bitcache"


def bitcache_take(digest, emit):
    """Restore a previously built bitstream for this digest. True on a hit."""
    have = BITCACHE / digest
    if not (have / "top.bit").exists():
        return False
    for item in have.iterdir():
        shutil.copy2(item, BITSTREAM.parent / item.name)
    emit(f"bitstream CACHED: reused {digest} from "
         f"{have.relative_to(ROOT)} -- no synthesis")
    return True


def bitcache_put(digest, emit):
    """Keep this bitstream, so the same rung never synthesises twice."""
    have = BITCACHE / digest
    have.mkdir(parents=True, exist_ok=True)
    kept = 0
    for name in ("top.bit", "top.svf", "firmware.hex", "gateware-digest.txt"):
        source = BITSTREAM.parent / name
        if source.exists():
            shutil.copy2(source, have / name)
            kept += 1
    emit(f"bitstream cached as {digest} ({kept} files)")


def bitstream_is_stale(emit):
    """Does the built bitstream describe gateware that is no longer this tree?

    THE CHECK THAT WAS MISSING, and its absence cost an hour: `./dev.py build`
    failed at the gateware step, `./dev.py fw` skipped the build by design, and
    `configure` loaded a bitstream from two clock settings ago. The board came
    up, the shell answered, and every number read off it was attributed to a
    configuration that had never been built. Both halves reported success.

    ## Content, not modification time

    This compared mtimes, and mtimes are not a statement about content. **`git
    checkout` rewrites the mtime of every file it touches, including files whose
    content it does not change** -- so switching branches, merging, or checking
    out the same commit again marked the bitstream stale and forced a 115-second
    resynthesis that produced a byte-identical result. That is the single
    largest waste in the edit-build-load loop, and the fastest synthesis is the
    one that does not run.

    A digest of the sources is recorded beside the bitstream when it is built and
    compared here. It answers the question actually being asked -- "is this
    bitstream built from this gateware" -- rather than a proxy for it.

    Not the committed git hash, which was the other candidate: that does not move
    for an edit which has not been committed, and an uncommitted edit is the
    normal state while working and exactly when this bites.

    ## Errs toward refusing

    No record beside the bitstream means stale. The interesting case is a build
    that failed and left the previous artifact in place looking plausible, and a
    missing record is indistinguishable from one that was never written.
    """
    if not BITSTREAM.exists():
        return False                    # a different error, reported elsewhere

    want = gateware_digest()
    have = (GATEWARE_BUILT.read_text().strip()
            if GATEWARE_BUILT.exists() else None)
    if have == want:
        return False

    emit("STALE BITSTREAM: it was not built from this gateware.")
    if have is None:
        emit(f"  no record beside the bitstream; cannot show it was built "
             f"from these sources (want {want})")
    else:
        emit(f"  bitstream built from {have}, this tree is {want}")
    emit("Refusing to configure. The board would run a gateware that is not this")
    emit("source -- a different clock, a different memory map, or a peripheral")
    emit("that moved -- and every measurement taken from it would be attributed")
    emit("to the wrong configuration. Run `./dev.py build` and fix what fails.")
    return True


def split_sections(elf, emit):
    """Allocated sections of `elf`, grouped by which window they load into.

    Returns `(bram, flash)` as lists of section names in address order, or
    `(None, None)` if the ELF could not be read.

    The grouping is by ADDRESS, so `memory.x` is the only place the layout is
    decided. Sections with no content in the file are skipped: `.bss` is
    allocated but NOBITS, and asking objcopy for it produces nothing while making
    the artifact look like it should contain something.
    """
    # `objdump -h`, not `readelf -S`, and the difference is the whole point.
    #
    # A section has two addresses: the VMA it RUNS at and the LMA it is LOADED
    # at. `.data` is `> REGION_DATA AT > REGION_RODATA` -- it runs in block RAM
    # and is loaded from flash, with riscv-rt copying it across at startup. So
    # its VMA is 0x400 and its LMA is in the flash window.
    #
    # `objcopy -O binary` emits at LOAD addresses, so the grouping must use LMA.
    # Classifying by VMA put `.data` in the block RAM artifact while objcopy
    # emitted it at its flash LMA, and the resulting binary spanned both memories:
    # 269,210,876 bytes for 76 bytes of content. Invisible until `.data` stopped
    # being empty.
    #
    # `readelf -S` prints only the VMA, which is why this uses `objdump -h`.
    result = run(["riscv64-linux-gnu-objdump", "-h", str(elf)])
    if result.returncode != 0:
        emit("objdump failed; cannot decide which sections go where:")
        emit((result.stderr or result.stdout).strip()[-400:])
        return None, None

    # ALLOC AND LOAD, from the flags line objdump prints under each section.
    #
    # Without this the walk picks up `.comment`, `.debug_*`, `.riscv.attributes`
    # and `.bss` -- none of which are loaded into anything -- and groups them by
    # an LMA of 0, which lands them in the block RAM image. The first version did
    # exactly that and produced an artifact listing eleven sections that do not
    # exist at runtime.
    lines = result.stdout.splitlines()
    bram, flash = [], []
    for index, line in enumerate(lines):
        flags = lines[index + 1] if index + 1 < len(lines) else ""
        if "ALLOC" not in flags or "LOAD" not in flags:
            continue
        # ` 4 .data  00000036  00000400  100bd8fc  0000f400  2**2`
        #        name    size       VMA       LMA      offset
        match = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+"
                         r"([0-9a-f]+)\s+", line)
        if not match:
            continue
        name, size, lma = match.groups()
        if int(size, 16) == 0:
            continue
        # ALLOC and not NOBITS: the flags are on the following line.
        address = int(lma, 16)
        if not name.startswith("."):
            continue
        if FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
            flash.append((address, name))
        elif address < 0x0001_0000:
            bram.append((address, name))
        # Anything else -- debug sections, .comment, unallocated -- belongs to
        # neither image and is dropped rather than guessed at.

    bram = [name for _, name in sorted(bram)]
    flash = [name for _, name in sorted(flash)]
    if not bram and not flash:
        emit("no allocated sections found in the ELF; refusing to guess")
        return None, None
    return bram, flash


def derive_bram_bin(emit):
    """Rewrite `FIRMWARE_BIN` from the ELF. Returns `(bram, flash)` section lists.

    A function rather than four inline lines because `bram_patch.py` needs the
    SAME derivation and must not carry a second copy of it. Its whole purpose is
    the fast path -- edit firmware, patch, load -- and on that path
    `tmp/rust_fw.bin` is stale by construction unless something regenerates it.

    Checking the file's age would be the weaker fix. An intermediate that CAN be
    stale eventually is, and #155 is what that costs: the patcher reported
    `0 of them changed`, verified its own round trip, and loaded firmware several
    edits behind, with every layer reporting success.

    Returns `(None, None)` if the ELF could not be read or objcopy failed; the
    caller decides whether that is fatal.
    """
    bram_sections, flash_sections = split_sections(ELF, emit)
    if bram_sections is None:
        return None, None

    # An EMPTY section list means "copy everything" to objcopy, not "copy
    # nothing", and that is a trap worth naming: with `.text` in flash there is
    # nothing left for block RAM, and the empty list produced a 48 KiB
    # `rust_fw.bin` containing the entire image. It then matched the flash
    # artifact byte for byte, and the stale-bitstream check compared that
    # against itself and passed.
    #
    # So an empty group is written as an empty file, explicitly.
    result = run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                  *(f"--only-section={s}" for s in bram_sections
                    or [".no-such-section"]),
                  str(ELF), str(FIRMWARE_BIN)])
    if result.returncode != 0:
        emit("objcopy failed:")
        emit((result.stderr or result.stdout).strip()[-400:])
        return None, None
    emit(f"Rust firmware: {FIRMWARE_BIN.stat().st_size} bytes "
         f"({firmware_digest()})  "
         f"[{' '.join(bram_sections) if bram_sections else 'nothing in block RAM'}]")
    return bram_sections, flash_sections


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-build", action="store_true",
                        help="skip cargo and gateware; load what exists")
    parser.add_argument("--c-firmware", action="store_true",
                        help="use scripts/riscv_firmware.py instead of the Rust crate")
    parser.add_argument("--write-tests", action="store_true",
                        help="C firmware only: compile in the flash erase/program tests")
    parser.add_argument("--force-flash", action="store_true",
                        help="write the flash even when it already holds this "
                             "image; use if anything else has touched it")
    parser.add_argument("--no-flash", action="store_true",
                        help="do not program .rodata into flash; the board keeps "
                             "whatever it already holds, which is only correct if "
                             "nothing in .rodata changed")
    parser.add_argument("--no-read", action="store_true",
                        help="do not read the console afterwards")
    parser.add_argument("--build-only", action="store_true",
                        help="build firmware, bootloader and gateware, then stop; "
                             "touches no hardware, so it needs no board attached")
    parser.add_argument("--firmware-only", action="store_true",
                        help="skip the ~60 s gateware build: rebuild the firmware, "
                             "write it to flash, and reconfigure the existing "
                             "bitstream. Only valid when the bitstream carries no "
                             "firmware, which it does not once .text is in flash")
    parser.add_argument("--skip-tests", action="store_true",
                        help="configure even though the QEMU shell tests have not run")
    parser.add_argument("--features", default="",
                        help="cargo features for the shell crate, comma-separated. "
                             "`rtic` builds the RTIC dispatcher instead of the "
                             "superloop -- see docs/rtic.md. The SHIPPING image "
                             "takes no features, and the QEMU gate asserts that, "
                             "so this is for a measurement run and not for a "
                             "board that gets left in that state")
    args = parser.parse_args()

    firmware = FIRMWARE_BIN

    # The gate. Before everything, including --no-build: a bitstream built earlier
    # from firmware that fails these assertions is no safer to load than one built
    # now, and the source it is tested against is the source on disk either way.
    if args.skip_tests:
        emit("shell tests SKIPPED (--skip-tests)")
    elif args.c_firmware:
        emit("shell tests skipped: they cover the Rust crate, not the C generator")
    else:
        # Output is streamed rather than captured. This runs a QEMU boot and a
        # handful of assertions; watching them tick past is the point, and a
        # captured block printed afterwards would arrive only once it no longer
        # mattered.
        #
        # The features go through, so the gate tests THE IMAGE BEING FLASHED. A
        # gate that always ran the default build would pass while a feature build
        # went to the board unexamined, which is the same shape as a stale
        # bitstream: the check is real and it is about something else.
        rc = spawn([sys.executable, str(ROOT / "scripts" / "soc_test.py")]
                   + (["--features", args.features] if args.features else []),
                   cwd=ROOT)
        if rc != 0:
            emit("shell tests FAILED -- not configuring the board.")
            emit("The same shell logic misbehaves under QEMU, so a reconfigure")
            emit("would only reproduce it an order of magnitude more slowly.")
            emit("Details above and in tmp/logs/dev.log; --skip-tests")
            emit("overrides this if the board is what you are debugging.")
            return 1

    # REGENERATE THE PERIPHERAL MAP, every time, rather than checking it.
    #
    # This used to run `--check` and REFUSE on drift, on the reasoning that
    # rewriting checked-in source mid-build is a surprise. Measured, that
    # reasoning does not survive: `--check` is 0.515 s and a full regeneration
    # is 0.712 s, because `--check` already regenerates into a temporary place
    # and diffs it. So the refusing version paid nearly the whole cost, threw
    # the result away, and made someone run it again by hand -- 0.2 s saved
    # against a 60 s synthesis, in exchange for an ordering trap that cost an
    # hour tonight.
    #
    # The map is a DERIVED artifact. Regenerating it is what the repo's own
    # rule says to do with those, and the diff landing in `git status` is
    # correct: the gateware changed, so the addresses did.
    #
    # It is still a hard stop if the generator itself fails, because then
    # nothing knows where the peripherals are.
    # ...except for the BIST variant, which deliberately has a DIFFERENT map:
    # no HYPERRAM window and no BOOTRAM, because the engine owns the part's pins.
    # Regenerating here would overwrite the committed PAC -- the shipping
    # variant's -- with a measurement build's, and the next ordinary build would
    # then check itself against a map it does not have.
    #
    # Nothing is lost by skipping it. The BIST peripheral is addressed by a
    # literal in `firmware/cynthion-soc/src/bist.rs` precisely so that the PAC
    # does not have to change, and `tests/test_bist_constants.py` holds that
    # literal to `top.py` in 0.01 s.
    if not args.c_firmware and os.environ.get("CYNTHION_HYPERRAM_BIST", "") not in ("", "0"):
        emit("peripheral map NOT regenerated: CYNTHION_HYPERRAM_BIST has its own")
        emit("  no HYPERRAM window and no BOOTRAM in that variant, by design.")
        emit("  the committed PAC stays the shipping one; the BIST peripheral is")
        emit("  a literal in bist.rs, checked by tests/test_bist_constants.py.")
    elif not args.c_firmware:
        before = None
        generated = ROOT / "firmware" / "cynthion-soc-pac" / "src" / "base.rs"
        if generated.exists():
            before = generated.read_bytes()

        result = run([sys.executable,
                      str(ROOT / "scripts" / "soc_generate_pac.py")])
        if result.returncode != 0:
            emit("PERIPHERAL MAP GENERATION FAILED:")
            emit((result.stdout or result.stderr).strip()[-700:])
            emit("Refusing to build: without it nothing knows where the "
                 "peripherals are.")
            return 1

        if before is not None and generated.read_bytes() != before:
            emit("peripheral map REGENERATED -- the gateware moved something. "
                 "`git diff firmware/*-pac` shows what.")

    # `--no-build` skips COMPILING. It must NOT skip deriving the artifacts
    # from the ELF or writing them: `run --no-build` used to configure the
    # FPGA and leave stale firmware in flash, so the board ran code that was
    # not the tree's while reporting success. That reads as a firmware bug
    # and cost three confused measurements before it was spotted.
    if True:
        if args.c_firmware:
            if not args.no_build:
                cmd = [sys.executable,
                       str(ROOT / "scripts" / "riscv_firmware.py")]
                if args.write_tests:
                    cmd.append("--write-tests")
                result = run(cmd)
                if result.returncode != 0:
                    emit("C firmware build failed:")
                    emit((result.stderr or result.stdout).strip()[-600:])
                    return 1
            firmware = ROOT / "tmp" / "riscv_hello" / "hello.bin"
            emit(f"C firmware: {firmware.stat().st_size} bytes")
        else:
            if not args.no_build:
                cargo = ["cargo", "build", "--release"]
                if args.features:
                    # Said out loud, every time. A board left running a
                    # feature-built image looks exactly like one running the
                    # shipping image, and the difference has to be in the log or
                    # it is in nobody's head.
                    cargo += ["--features", args.features]
                    emit(f"BUILDING WITH FEATURES: {args.features} -- this is NOT "
                         f"the shipping image")
                result = run(cargo, cwd=CRATE)
                if result.returncode != 0:
                    emit("cargo build failed:")
                    emit((result.stderr or result.stdout).strip()[-900:])
                    return 1
            # objcopy rather than cargo-binutils: one less thing to install, and the
            # cross binutils are already here for the C path.
            #
            # SPLIT BY DESTINATION, not one flat image. Sections can live in block
            # RAM or in flash, and those load by completely different paths -- block
            # RAM as bitstream init, flash by writing the part. A single `-O binary`
            # over an ELF spanning both pads the address gap between 0x0 and
            # 0x10000000, which produced a 269 MB "firmware" the first time this was
            # tried.
            #
            # `--only-section` per destination keeps each artifact the size of what
            # is actually in it. A build with nothing in flash simply produces an
            # empty second file, so this costs nothing when the layout is all-RAM.
            # WHICH sections go where is read from the ELF, not listed here.
            #
            # This used to name them: `.init .text .data` to block RAM,
            # `.rodata` to flash. That is a copy of memory.x's REGION_ALIAS
            # lines maintained in a different file and a different language,
            # and it is wrong the moment a section moves -- which is exactly
            # what moving `.text` to flash does. A named list would have put
            # `.text` in the block RAM artifact and flash would have been
            # programmed with rodata alone, leaving the CPU fetching from an
            # address nothing had written.
            #
            # So the linker decides and this follows: group the allocated
            # sections by whether their address lands in the flash window.
            bram_sections, flash_sections = derive_bram_bin(emit)
            if bram_sections is None:
                return 1

            # Whatever the linker put in flash, as its own artifact.
            result = run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                          *(f"--only-section={s}" for s in flash_sections
                            or [".no-such-section"]),
                          str(ELF), str(RODATA_BIN)])
            rodata = RODATA_BIN.stat().st_size if RODATA_BIN.exists() else 0
            if rodata:
                emit(f"flash image: {rodata} bytes for {FLASH_RODATA_OFFSET:#x} "
                     f"({firmware_digest(RODATA_BIN)})")
                if args.build_only:
                    emit("  NOT WRITTEN (--build-only touches no hardware).")
                elif args.no_flash:
                    emit("  NOT WRITTEN (--no-flash). The board will run with "
                         "whatever .rodata flash already holds.")
                else:
                    emit("  deferred: flash is written just before the "
                         "configure, so the board keeps running this one "
                         "until the next is ready")

        # The bootloader, unless this is the C path -- that generator emits an
        # image linked for 0 and has no bootloader to sit under it.
        if not args.c_firmware:
            # The BIST variant's bootloader must NOT probe for a staged image:
            # that window is absent from the decoder, not merely unresponsive,
            # and VexiiRiscv traps an access to an address in no declared
            # region. The trap lands on the first load, before any of the
            # bootloader's own fallbacks can run, so the board is silent from
            # reset with no banner and no console.
            boot_cmd = ["cargo", "build", "--release"]
            if os.environ.get("CYNTHION_HYPERRAM_BIST", "") not in ("", "0"):
                boot_cmd += ["--features", "hyperram-bist"]
                emit("bootloader: staging probe COMPILED OUT (BIST variant has "
                     "no BootRAM to probe)")
            result = run(boot_cmd, cwd=BOOT_CRATE)
            if result.returncode != 0:
                emit("bootloader build failed:")
                emit((result.stderr or result.stdout).strip()[-900:])
                return 1
            result = run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                          str(BOOT_ELF), str(BOOT_BIN)])
            if result.returncode != 0:
                emit("bootloader objcopy failed:")
                emit((result.stderr or result.stdout).strip()[-400:])
                return 1
            # Where it goes, what it is for, and how much room is left -- a bare
            # byte count answers none of those, and this is the one line about
            # the thing that owns the reset vector.
            #
            # It is packed into the bitstream's block RAM initialiser at 0x0,
            # not written to flash, so it changes only when the gateware is
            # rebuilt. The kilobyte below IMAGE_ORIGIN is its budget; overrunning
            # it would land on the shell's first instruction, and the symptom
            # would be a board that configures and does nothing.
            boot_size = BOOT_BIN.stat().st_size
            emit(f"bootloader: {boot_size} bytes at 0x0 in block RAM, "
                 f"{IMAGE_ORIGIN - boot_size} free below the shell at "
                 f"{IMAGE_ORIGIN:#x}")
            emit(f"  from {BOOT_CRATE.relative_to(ROOT)}; packed into the "
                 f"bitstream, so it reaches the board only on a reconfigure")
            if boot_size > IMAGE_ORIGIN:
                emit(f"  OVERRUN: it does not fit under {IMAGE_ORIGIN:#x} and "
                     f"would overwrite the shell's reset path")
                return 1
        elif BOOT_BIN.exists():
            # A stale one from a Rust build would be packed at 0x0 under a C image
            # that is itself linked for 0x0. Two things at the reset vector is one
            # too many, and the symptom is a dead CPU.
            BOOT_BIN.unlink()

        # THE FAST PATH, and the reason `.text` in flash is worth having.
        #
        # Synthesis is ~60 s of the ~90 s loop, and once no part of the
        # firmware is packed into the block RAM initialiser it buys nothing
        # for a firmware change: the bitstream that is already on the board is
        # byte-identical to the one this would produce. Write flash,
        # reconfigure to reset the CPU, done in seconds.
        #
        # THE GUARD IS THE POINT. If anything is still destined for block RAM,
        # that content reaches the CPU only through the bitstream, and skipping
        # the build would configure a bitstream carrying the PREVIOUS firmware
        # while flash carries the new one -- a half-updated image, reported as
        # success. That is the exact failure this script was written to stop,
        # so it refuses rather than warning.
        if args.firmware_only:
            if bram_sections:
                emit("--firmware-only REFUSED: these sections still load via "
                     "the bitstream:")
                emit(f"  {' '.join(bram_sections)}")
                emit("Skipping the gateware build would leave them at their "
                     "previous contents while flash carried the new ones. "
                     "Run without --firmware-only.")
                return 1
            if not BITSTREAM.exists():
                emit(f"--firmware-only needs an existing bitstream at "
                     f"{BITSTREAM.relative_to(ROOT)}; there is none. "
                     f"Run once without it.")
                return 1
            emit("gateware build SKIPPED (--firmware-only); nothing of this "
                 "firmware travels in the bitstream")
            emit(f"  reconfiguring {BITSTREAM.relative_to(ROOT)} to reset the "
                 f"CPU onto the flash just written")
            return configure_and_read(args, emit)

        # The OSS CAD Suite environment has to be sourced, so this one step is a
        # shell command rather than a bare exec.
        build = (f'source "$HOME/opt/oss-cad-suite/environment" && '
                 f'AMARANTH_nextpnr_opts="{NEXTPNR_OPTS}" '
                 f'python3.15t {GATEWARE} --build --firmware {firmware} '
                 f'--bootloader {BOOT_BIN}')
        # SKIP IT ENTIRELY when the sources have not changed.
        #
        # This is the same argument as the flash write's skip, and a bigger
        # number: 115 s against 3.5 s. The old mtime comparison could not make
        # it, because `git checkout` rewrites mtimes on files it does not
        # change, so a branch switch forced a full resynthesis that produced a
        # byte-identical bitstream.
        #
        # `--force-flash` has no counterpart here on purpose: deleting the
        # digest file is the escape hatch, and it is one that cannot be reached
        # by accident.
        want_gateware = gateware_digest()
        have_gateware = (GATEWARE_BUILT.read_text().strip()
                         if GATEWARE_BUILT.exists() else None)
        # A rung built before is a FILE COPY, not a synthesis. The CK values are
        # solved arithmetic -- `reachable_ck` enumerates them -- so each one is a
        # bitstream that only ever needs building once. Without this, revisiting
        # a rung cost another ~130 s for a file that had already existed and been
        # overwritten.
        if have_gateware != want_gateware and bitcache_take(want_gateware, emit):
            have_gateware = want_gateware
        if BITSTREAM.exists() and have_gateware == want_gateware:
            emit(f"synthesis SKIPPED: the bitstream was built from these exact "
                 f"sources ({want_gateware})")
            emit(f"  delete {GATEWARE_BUILT.relative_to(ROOT)} to force a "
                 f"rebuild")
            return configure_and_read(args, emit)

        # Say what is about to take a minute, BEFORE it takes it. Amaranth,
        # yosys and nextpnr print nothing this log can see until they are done,
        # so without this line the log has a silent 60-90 s hole in it and the
        # only thing to conclude from a hole is that something hung.
        emit(f"synthesis: yosys + nextpnr on {GATEWARE.relative_to(ROOT)}, "
             f"typically 60-120 s")
        emit(f"  full tool output -> {SYNTH_LOG.relative_to(ROOT)} "
             f"(thousands of lines; only the summary comes back here)")
        build_started = time.perf_counter()
        result = run(["bash", "-c", build])
        build_seconds = time.perf_counter() - build_started
        output = (result.stdout or "") + (result.stderr or "")

        # DIVERTED, not discarded. Every line yosys and nextpnr produced goes to
        # its own file: it is the record when a build misbehaves, and it is far
        # too much to interleave into `dev.log`, where it buries the handful of
        # lines that are about the board.
        SYNTH_LOG.parent.mkdir(parents=True, exist_ok=True)
        SYNTH_LOG.write_text(output)
        emit(f"synthesis finished in {build_seconds:.1f} s "
             f"({len(output.splitlines())} lines -> "
             f"{SYNTH_LOG.relative_to(ROOT)})")

        # THE FREQUENCIES, on success as well as on failure.
        #
        # These are the numbers every clock decision here turns on, and until
        # now no successful build printed one. On failure nextpnr puts them on
        # stderr; on SUCCESS it writes them only to `--log top.tim`, so a
        # passing build said nothing about its margin.
        #
        # That margin is not academic: the build this was added on passes at
        # 72.70 MHz against a 72.00 MHz constraint -- 1% -- and an earlier run
        # in the same log failed at 64.76. It has been swinging either side of
        # the line with placement, invisibly.
        #
        # `top.tim` ACCUMULATES across runs, so only the last block is this
        # build's. Reading the first would report an older attempt as if it
        # were current, which is the same class of mistake as a stale
        # bitstream.
        timing = ROOT / "tmp" / "awto_soc" / "build" / "top.tim"
        frequencies = []
        if timing.exists():
            for line in timing.read_text().splitlines():
                if "Max frequency for clock" in line:
                    frequencies.append(line.split("Info:")[-1].strip())
        else:
            frequencies = [line.split("Info:")[-1].strip()
                           for line in output.splitlines()
                           if "Max frequency for clock" in line]
        # One entry per domain, last wins.
        latest = {}
        for entry in frequencies:
            latest[entry.split("'")[1] if "'" in entry else entry] = entry
        for entry in latest.values():
            emit("  " + entry)

        # RECORD IT. Every build, no flag to skip, before the failure return.
        #
        # A build that misses timing is the most interesting row in the table --
        # it is the one that says how far past the edge a change pushed the
        # design -- so this runs for a failed place-and-route too, and the row
        # carries `status='fail'`.
        #
        # It is here rather than in `dev.py` because `dev.py build` is not the
        # only way a bitstream gets made: `./dev.py run` and a bare
        # `scripts/soc_run.py` both reach this line, and metrics that only the
        # tidy path records are metrics with a hole exactly where somebody was
        # in a hurry. See #232.
        #
        # It cannot fail the build. If Postgres is down the record is spooled to
        # tmp/build-metrics/pending/ and said so loudly; the bitstream is still
        # good and throwing it away over a stopped database would be absurd.
        build_metrics.record_build(
            BITSTREAM.parent, status="ok" if result.returncode == 0 else "fail",
            build_seconds=build_seconds)

        if result.returncode != 0:
            emit("gateware build failed:")
            # THE TOOL'S ERROR FIRST, then the tail.
            #
            # This printed the last 900 characters, which on an Amaranth
            # build is the end of a Python traceback -- the subprocess
            # wrapper, never the reason. nextpnr's own `ERROR:` line had
            # already scrolled past, so every failure tonight cost a SECOND
            # full synthesis run by hand to read it.
            # NOT "Max frequency" again -- those are printed above for every
            # build, and repeating them here made one failure look like two.
            reasons = [line for line in output.splitlines()
                       if line.startswith(("ERROR:", "Error:"))
                       and "Max frequency" not in line]
            for line in reasons[-8:]:
                emit("  " + line.strip())
            if not reasons:
                emit((result.stderr or result.stdout).strip()[-700:])
            return 1

        # Recorded only now, after the build has returned zero. Written before
        # it would claim a bitstream that may not exist.
        GATEWARE_BUILT.write_text(gateware_digest() + "\n")
        bitcache_put(gateware_digest(), emit)

        report = ROOT / "tmp" / "awto_soc" / "build" / "top.rpt"
        if report.exists():
            undriven = report.read_text().count("has no driver")
            emit(f"gateware built. undriven wires: {undriven}")
            if undriven:
                emit("  *** undriven wires present -- a peripheral is unconnected,")
                emit("      which produces a CPU that runs and reaches nothing")
        else:
            emit("gateware built")

        # THE CHECK THIS SCRIPT EXISTED WITHOUT, and which cost most of a day.
        #
        # Three separate times the board ran firmware that was not the firmware
        # just built, with every step reporting success: a stale intermediate
        # patched in, a stale bitstream loaded, and a fresh bitstream that still
        # lacked a committed command. Nothing compared what was built against
        # what was about to be configured, so nothing could say so.
        #
        # This does. A mismatch stops the run rather than reaching the board,
        # because a board running unknown firmware invalidates every measurement
        # taken from it -- and those are believed, written down and acted on.
        if not args.c_firmware and not bram_sections:
            # NOT a pass. There is nothing to compare: every byte of this
            # firmware reaches the CPU through flash, so the bitstream is not
            # evidence about it either way. Saying "carries the firmware just
            # built" here would be a check reporting success on an empty
            # comparison, which is worse than no check.
            emit("bitstream carries no firmware (all of it is in flash); "
                 "the stale-image check does not apply")
        elif not args.c_firmware:
            carried = firmware_in_bitstream(BITSTREAM.parent, emit)
            if carried is not None:
                # The image is linked for IMAGE_ORIGIN, and the bootloader
                # occupies the kilobyte below it, so the image starts 0x400 into
                # the block RAM initialiser rather than at its start.
                built = FIRMWARE_BIN.read_bytes()
                image = carried[IMAGE_ORIGIN:IMAGE_ORIGIN + len(built)]
                if image != built:
                    emit("STALE BITSTREAM: it does not carry the firmware just "
                         "built.")
                    emit(f"  built:   {firmware_digest()} "
                         f"({len(built)} bytes)")
                    seen = hashlib.sha256(image).hexdigest()
                    emit(f"  carried: {seen[:12]}")
                    emit("Refusing to configure. The board would run firmware "
                         "that is not this source, and every measurement taken "
                         "from it would be wrong in a way nothing reports.")
                    return 1
                emit(f"bitstream carries the firmware just built "
                     f"({firmware_digest()})")

    # Stop here, deliberately AFTER the stale-bitstream comparison.
    #
    # A build-only run is what `./dev.py build` and the pre-commit gate use, so it
    # runs on a machine with no board attached. Placing the return after the
    # comparison means the check that has caught three stale-image incidents also
    # runs there -- it reads two files and touches nothing.
    if args.build_only:
        emit("build complete (--build-only): nothing configured, nothing written")
        return 0

    return configure_and_read(args, emit)




def write_rodata_flash(args, emit):
    """Program `.rodata` into flash. `True` on success, `False` to abort.

    **Called immediately before the configure, NOT when the artifact is built.**

    The CPU executes from this flash. Writing it while the board is running
    overwrites the `.text` of the shell that is running, so the board goes silent
    at that instant -- and for a full build it then stays silent for the ninety
    seconds of synthesis before anything reconfigures it. That looked like
    synthesis killing the gateware, and it was the flash write two steps earlier.

    Deferring it to here closes the window: the old image keeps running until the
    new bitstream is ready, and the gap between the write and the reconfigure
    that revives the CPU is now a couple of seconds rather than a couple of
    minutes.
    """
    if not RODATA_BIN.exists() or not RODATA_BIN.stat().st_size:
        return True
    if args.build_only:
        emit("  NOT WRITTEN (--build-only touches no hardware).")
        return True
    if args.no_flash:
        emit("  NOT WRITTEN (--no-flash). The board will run with whatever "
             ".rodata flash already holds.")
        return True
    # Writing the flash the board BOOTS from, so this states the offset every
    # time rather than assuming anyone remembers it. 0xb0000 is moondancer's
    # firmware slot and is clear of the FPGA configuration at offset zero -- but
    # "clear of" is a fact about this layout, not a property of the tool, and the
    # tool will write wherever it is told.
    #
    # SKIP IF IT IS ALREADY THERE. Measured: one USB round trip to the flash is
    # 3.00 ms, and a 256-byte page costs several of them -- write-enable,
    # program, then poll the status register until the chip reports done. 58,940
    # bytes is 231 pages and takes 4.72 s, against roughly 0.6 s of actual
    # W25Q32DV erase and program time. The transport is the cost, not the flash.
    #
    # Most iterations do not change `.rodata` at all -- a gateware edit, a clock
    # change, a peripheral moving -- so most of those 4.72 s buy nothing. The
    # digest was already being computed and printed; it just was not compared.
    #
    # The record is what THIS script last wrote. Anything else touching the flash
    # invalidates it, so `--force-flash` exists and the record is deleted on any
    # write failure rather than left claiming a state that was not reached.
    want = f"{FLASH_RODATA_OFFSET:#x} {firmware_digest(RODATA_BIN)}"
    already = (FLASH_WRITTEN.read_text().strip()
               if FLASH_WRITTEN.exists() else None)
    if already == want and not args.force_flash:
        emit(f"  flash already holds this image "
             f"({firmware_digest(RODATA_BIN)} at {FLASH_RODATA_OFFSET:#x}) "
             f"-- not rewriting. `--force-flash` writes it anyway.")
        return True

    emit(f"  writing flash at {FLASH_RODATA_OFFSET:#x} "
         f"(bitstream at 0x0 is not touched)")
    emit(f"  the CPU executes from here, so it goes quiet NOW and comes back "
         f"on the reconfigure a few seconds from now")
    started = time.perf_counter()
    result = run([sys.executable,
                  str(ROOT / "repos" / "apollo" / "apollo_fpga"
                      / "commands" / "cli.py"),
                  "flash-program",
                  "--offset", str(FLASH_RODATA_OFFSET),
                  str(RODATA_BIN)])
    if result.returncode != 0:
        # The record goes FIRST, so a partial write is never remembered as a
        # completed one.
        FLASH_WRITTEN.unlink(missing_ok=True)
        emit("  flash write FAILED:")
        emit((result.stderr or result.stdout).strip()[-500:])
        emit("Refusing to configure: the board would run against .rodata that "
             "is not this build, and every constant it reads would be silently "
             "wrong.")
        return False
    elapsed = time.perf_counter() - started
    size = RODATA_BIN.stat().st_size
    FLASH_WRITTEN.write_text(want + "\n")
    emit(f"  flash written: {size} bytes in {elapsed:.2f} s "
         f"({size / elapsed / 1024:.1f} KiB/s) over Apollo JTAG -> SPI "
         f"bit-bang; bound by the W25Q32's page program, not the link")
    return True


def configure_and_read(args, emit):
    """Configure the FPGA, then report what the console says.

    Split out so `--firmware-only` can reach it without going through the gateware
    build. Everything above it in `main` produces artifacts; this is the whole of
    what touches the board.
    """
    if True:
        if not BITSTREAM.exists():
            emit(f"no bitstream at {BITSTREAM.relative_to(ROOT)}")
            return 1

        # Nothing reaches the board past this point without the gateware on it
        # being the gateware in the tree.
        if bitstream_is_stale(emit):
            return 1

        # THE FLASH WRITE, here and not where the artifact was built.
        #
        # The CPU executes from this flash, so writing it overwrites the `.text`
        # of the running shell and the board goes silent at that instant. Done
        # before the gateware build, that silence lasted the ninety seconds of
        # synthesis -- which reads as synthesis having killed the design, and was
        # the flash write two steps earlier. Here, the old image runs until the
        # new bitstream is ready and the gap is a couple of seconds.
        #
        # After the staleness check, so a run that is going to refuse to
        # configure does not destroy the working image on its way to refusing.
        if not write_rodata_flash(args, emit):
            return 1

        result = run([sys.executable,
                      str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
                      "configure", str(BITSTREAM)])
        if result.returncode != 0:
            emit("configure failed:")
            emit((result.stderr or result.stdout).strip()[-400:])
            return 1
        emit("configured")

        if args.no_read:
            return 0

        # Read through the console service if one is running, and NEVER open the tty
        # while it is. Both processes reading the same port interleaves the stream --
        # each takes bytes the other never sees, giving output like "ivlive0alive" --
        # and every steal makes the service drop and reattach, which looked like the
        # FPGA reconfiguring in a loop. It was not: the tick counter kept climbing, so
        # the CPU never restarted. Two readers, one port.
        import socket

        served = False
        try:
            sock = socket.create_connection(("127.0.0.1", 9000), timeout=3)
            served = True
        except OSError:
            sock = None

        if served:
            # ANNOUNCED BEFORE THE WAIT, not after it.
            #
            # This is a bounded read of the board's console: it stops at 400
            # bytes or at the timeout, whichever comes first, and a board that
            # boots quietly will always hit the timeout. Printing the header
            # afterwards left a silent 13 s hole between `configured` and the
            # first console line, and a hole in a log reads as a hang.
            #
            # **Waits for**: whatever the board says of its own accord after a
            # reset.
            #
            # **Expected duration**: the banner is ON DEMAND -- it is held in the
            # log ring and flushed on the first received byte -- so the board
            # normally says NOTHING here. What does arrive, when it arrives, is
            # emitted within about a second of reset. Enumeration is ~0.5 s.
            #
            # **Multiplier**: 2 s, about 1.3x the ~1.5 s worst case. It was 12 s,
            # chosen when the banner printed unprompted; with an on-demand banner
            # that is ten seconds of waiting for a board that is designed to be
            # quiet, on every single run. A wait that always expires teaches
            # people to ignore it.
            #
            # **On expiry**: prints how long it waited and that nothing came,
            # which for this board is the ordinary case rather than a fault. A CR
            # is sent first precisely so there IS something to read -- passively
            # waiting on a silent-by-design console measures nothing.
            CONSOLE_READ_SECONDS = 2
            emit(f"poking the console and reading for up to "
                 f"{CONSOLE_READ_SECONDS} s (the banner is on demand, so a CR "
                 f"is sent first)")
            try:
                sock.sendall(b"\r")
            except OSError:
                pass
            sock.settimeout(CONSOLE_READ_SECONDS)
            buf = b""
            while len(buf) < 400:
                try:
                    chunk = sock.recv(200)
                    if not chunk:
                        break
                    buf += chunk
                except OSError:
                    break
            sock.close()
            if not buf:
                emit(f"  nothing in {CONSOLE_READ_SECONDS} s, not even to a CR "
                     f"-- the shell is not answering")
            emit("--- console (via the service on 9000) ---")
            emit(buf.decode("ascii", "replace").strip()[:500])
        else:
            import usb_ids
            import serial

            # Two settle passes: a fresh configure can take longer than one to produce a
            # bound tty, and a false "no device" reads as a hardware fault.
            node = (usb_ids.wait_for_tty("riscv_console")
                    or usb_ids.wait_for_tty("riscv_console"))
            if not node:
                emit("no console tty appeared.")
                emit("Check `lsusb -d 1d50:6180`: a device on the bus without a bound")
                emit("tty is a transient state after a reconfigure, not a fault.")
                return 1
            emit(f"console: {node}")
            port = serial.Serial(node, 115200, timeout=8)
            data = port.read(400)
            port.close()
            emit("--- console ---")
            emit(data.decode("ascii", "replace").strip()[:500])

            # An empty read here has two causes and they look identical: the firmware
            # said nothing, or something else read what it said. A tty has one reader,
            # and a `./scripts/tio_user.py` left running in another terminal takes every byte
            # while this reports a blank console. That has been mistaken for dead
            # firmware on a board that was working perfectly.
            if not data.strip():
                from soc_shell import other_readers

                thieves = other_readers(node)
                if thieves:
                    emit()
                    emit("*** ANOTHER PROCESS IS READING THIS PORT ***")
                    for pid, command in thieves:
                        emit(f"      pid {pid}: {command}")
                    emit("The blank console above is contention, not silence. Stop it,")
                    emit("or restart it as `./scripts/tio_user.py --serve` so this reads through")
                    emit("its socket on port 9000 instead of competing for the tty.")

        emit()

    return 0


if __name__ == "__main__":
    sys.exit(main())
