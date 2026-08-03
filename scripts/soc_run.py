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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_run.log"
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
FLASH_RODATA_OFFSET = 0x000b_0000

# The resident bootloader: 492 bytes at 0x0, and what the reset vector points at.
# Built alongside the image and packed into the same block RAM init, so one bitstream
# carries both and a board with nothing staged comes up on the image below.
BOOT_CRATE = ROOT / "firmware" / "cynthion-boot"
BOOT_ELF = (BOOT_CRATE / "target" / "riscv32imac-unknown-none-elf" / "release"
            / "cynthion-boot")
BOOT_BIN = ROOT / "tmp" / "rust_boot.bin"
GATEWARE = ROOT / "ecp5-test" / "riscv" / "vexii_hello_soc.py"
BITSTREAM = ROOT / "tmp" / "vexii_hello" / "build" / "top.bit"

sys.path.insert(0, str(ROOT / "ecp5-test"))

# Measured on this design: 64 s -> 59 s, with no change to utilisation.
#
# --threads alone does nothing, which is the trap: nextpnr's SA refinement is 16 of its
# 24 seconds and is serial unless --parallel-refine is passed. --router router2 recovers
# the Fmax that --parallel-refine on its own gives up.
NEXTPNR_OPTS = "--parallel-refine --threads 31 --router router2"


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
    parser.add_argument("--no-flash", action="store_true",
                        help="do not program .rodata into flash; the board keeps "
                             "whatever it already holds, which is only correct if "
                             "nothing in .rodata changed")
    parser.add_argument("--no-read", action="store_true",
                        help="do not read the console afterwards")
    parser.add_argument("--skip-tests", action="store_true",
                        help="configure even though the QEMU shell tests have not run")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

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
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "soc_test.py")], cwd=ROOT)
            if result.returncode != 0:
                emit("shell tests FAILED -- not configuring the board.")
                emit("The same shell logic misbehaves under QEMU, so a reconfigure")
                emit("would only reproduce it an order of magnitude more slowly.")
                emit("Details above and in tmp/logs/soc_test.log; --skip-tests")
                emit("overrides this if the board is what you are debugging.")
                return 1

        if not args.no_build:
            if args.c_firmware:
                cmd = [sys.executable, str(ROOT / "scripts" / "riscv_firmware.py")]
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
                result = run(["cargo", "build", "--release"], cwd=CRATE)
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
                result = run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                              "--only-section=.init", "--only-section=.init.rust",
                              "--only-section=.text", "--only-section=.data",
                              str(ELF), str(FIRMWARE_BIN)])
                if result.returncode != 0:
                    emit("objcopy failed:")
                    emit((result.stderr or result.stdout).strip()[-400:])
                    return 1
                emit(f"Rust firmware: {FIRMWARE_BIN.stat().st_size} bytes "
                     f"({firmware_digest()})")

                # Whatever the linker put in flash, as its own artifact.
                result = run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                              "--only-section=.rodata", str(ELF), str(RODATA_BIN)])
                rodata = RODATA_BIN.stat().st_size if RODATA_BIN.exists() else 0
                if rodata:
                    emit(f"flash image: {rodata} bytes for {FLASH_RODATA_OFFSET:#x} "
                         f"({firmware_digest(RODATA_BIN)})")
                    if args.no_flash:
                        emit("  NOT WRITTEN (--no-flash). The board will run with "
                             "whatever .rodata flash already holds.")
                    else:
                        # Writing the flash the board BOOTS from, so this states the
                        # offset every time rather than assuming anyone remembers it.
                        # 0xb0000 is moondancer's firmware slot and is clear of the
                        # FPGA configuration at offset zero -- but "clear of" is a
                        # fact about this layout, not a property of the tool, and the
                        # tool will write wherever it is told.
                        emit(f"  writing flash at {FLASH_RODATA_OFFSET:#x} "
                             f"(bitstream at 0x0 is not touched)")
                        result = run([sys.executable,
                                      str(ROOT / "repos" / "apollo" / "apollo_fpga"
                                          / "commands" / "cli.py"),
                                      "flash-program",
                                      "--offset", str(FLASH_RODATA_OFFSET),
                                      str(RODATA_BIN)])
                        if result.returncode != 0:
                            emit("  flash write FAILED:")
                            emit((result.stderr or result.stdout).strip()[-500:])
                            emit("Refusing to configure: the board would run against "
                                 ".rodata that is not this build, and every constant "
                                 "it reads would be silently wrong.")
                            return 1
                        emit("  flash written")

            # The bootloader, unless this is the C path -- that generator emits an
            # image linked for 0 and has no bootloader to sit under it.
            if not args.c_firmware:
                result = run(["cargo", "build", "--release"], cwd=BOOT_CRATE)
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
                emit(f"bootloader: {BOOT_BIN.stat().st_size} bytes")
            elif BOOT_BIN.exists():
                # A stale one from a Rust build would be packed at 0x0 under a C image
                # that is itself linked for 0x0. Two things at the reset vector is one
                # too many, and the symptom is a dead CPU.
                BOOT_BIN.unlink()

            # The OSS CAD Suite environment has to be sourced, so this one step is a
            # shell command rather than a bare exec.
            build = (f'source "$HOME/opt/oss-cad-suite/environment" && '
                     f'AMARANTH_nextpnr_opts="{NEXTPNR_OPTS}" '
                     f'python3.15t {GATEWARE} --build --firmware {firmware} '
                     f'--bootloader {BOOT_BIN}')
            result = run(["bash", "-c", build])
            if result.returncode != 0:
                emit("gateware build failed:")
                emit((result.stderr or result.stdout).strip()[-900:])
                return 1

            report = ROOT / "tmp" / "vexii_hello" / "build" / "top.rpt"
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
            if not args.c_firmware:
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

        if not BITSTREAM.exists():
            emit(f"no bitstream at {BITSTREAM.relative_to(ROOT)}")
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
            sock.settimeout(12)
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
            # and a `./tio_user.py` left running in another terminal takes every byte
            # while this reports a blank console. That has been mistaken for dead
            # firmware on a board that was working perfectly.
            if not data.strip():
                sys.path.insert(0, str(ROOT / "scripts"))
                from soc_shell import other_readers

                thieves = other_readers(node)
                if thieves:
                    emit()
                    emit("*** ANOTHER PROCESS IS READING THIS PORT ***")
                    for pid, command in thieves:
                        emit(f"      pid {pid}: {command}")
                    emit("The blank console above is contention, not silence. Stop it,")
                    emit("or restart it as `./tio_user.py --serve` so this reads through")
                    emit("its socket on port 9000 instead of competing for the tty.")

        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
