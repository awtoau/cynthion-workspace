#!/usr/bin/env python3
#
# Stage a firmware image into HyperRAM over JTAG, with the CPU held in reset.
# SPDX-License-Identifier: BSD-3-Clause

"""
Loads firmware into a Cynthion whose console is not answering.

    ./scripts/soc_jtag_stage.py tmp/payload.bin        # hold, stage, release, read
    ./scripts/soc_jtag_stage.py --status               # ask the sink what it sees
    ./scripts/soc_jtag_stage.py --hold                 # hold the CPU in reset
    ./scripts/soc_jtag_stage.py --release              # let it go
    ./scripts/soc_jtag_stage.py IMAGE --port /dev/ttyACM0   # Apollo's console

Needs a configured FPGA and nothing else: no USB enumeration of the SoC, no console,
no running CPU. That is the case it exists for -- the USB bulk path (`soc_payload.py`)
needs a working shell to receive its own replacement.

## What it does

    step              frame          why
    ----------------  -------------  -----------------------------------------
    read the status   nop            the signature proves ER1 reached the sink
    hold the CPU      reset 1        nothing may execute while its RAM is written
    stage the image   write @ 16     one shift, whatever the image's size
    stage len + crc   write @ 2      the bootloader checks both
    stage the magic   write @ 0      last: a header without it is ignored
    read the status   nop            `staged` and `overflow` say what landed
    release the CPU   reset 0        it reboots, finds the header, runs the image

The layout is `firmware/cynthion-soc/src/hyperram.rs`, unchanged, so `try_boot`
cannot tell a JTAG-staged image from a console-staged one.

## Speed

One `shift_data` carries the whole image, the way `LSC_BITSTREAM_BURST` carries a
bitstream: `apollo_fpga` chunks it into USB transfers but the TAP never leaves
SHIFT-DR, so the cost is the JTAG clock and not a round trip per word. The run
prints bytes per second; `docs/comparisons.md` section 15 records what it should be.

## Reading the console afterwards

A tty has one reader. If `./tio_user.py --serve` is running, this talks through its
socket on 9000; if something else holds the port, the run says which pid rather than
reporting an empty console.
"""

import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_jtag_stage.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

from jtag_stage import (CMD_NOP, CMD_RESET, CMD_WRITE,   # noqa: E402
                        SIGNATURE)

# The ECP5 user instruction the sink answers on. ER2 (0x38) belongs to the RISC-V
# debug module; see `ecp5-test/riscv/jtag_stage.py`.
ER1 = 0x32

# The staging layout, in 16-bit HyperRAM words, from
# `firmware/cynthion-soc/src/hyperram.rs`. Duplicated rather than imported because
# the other side of it is Rust.
HDR_MAGIC  = 0
HDR_LENGTH = 2
HDR_CRC    = 4
IMAGE_WORD = 16
MAGIC      = 0x4359_4e42
MAX_IMAGE  = 32 * 1024

# How many console reads to make while waiting for the bootloader to speak.
#
# Bounded by reads, not by a clock: each returns as soon as bytes arrive and only
# blocks for the port's own timeout when there are none. The bootloader prints its
# verdict within a few milliseconds of the reset being released -- the CRC is a
# bitwise pass over at most 32 KiB -- so this is generous by orders of magnitude and
# costs nothing when the board is answering.
CONSOLE_READS = 12


class Sink:
    """The ER1 staging sink, as frames on a JTAG chain."""

    def __init__(self, chain):
        self.chain = chain
        self.shifted_bytes = 0
        self.shift_seconds = 0.0

    def _frame(self, payload, capture=False):
        """Shift one frame. Returns the status word when `capture` is set.

        The instruction is shifted every time. That is what makes a frame
        self-delimiting: the path from IRPAUSE to SHIFT-DR passes through
        CAPTURE-DR, so the sink's decoder starts from this frame's command byte
        whatever the previous frame was doing.
        """
        chain = self.chain
        chain.shift_instruction(ER1, length=8, state_after='IRPAUSE')

        started = time.perf_counter()
        if capture:
            response = chain.shift_data(tdi=payload, length=len(payload) * 8,
                                        byteorder='little', state_after='DRPAUSE')
        else:
            # `ignore_response` is not a convenience: capturing TDO costs a
            # GET_IN_BUFFER control transfer per chunk, measured at ~28% of a
            # configure in #108, and there is nothing to read back mid-image.
            chain.shift_data(tdi=payload, length=len(payload) * 8,
                             byteorder='little', ignore_response=True,
                             state_after='DRPAUSE')
            response = None
        elapsed = time.perf_counter() - started

        self.shifted_bytes += len(payload)
        self.shift_seconds += elapsed
        return (int(response) if response is not None else None), elapsed

    def status(self):
        """The sink's 64-bit status word, as a dict."""
        value, _ = self._frame(bytes([CMD_NOP]) + b"\x00" * 7, capture=True)
        return {
            "signature": value & 0xffff,
            "staged":    (value >> 16) & 0xffff_ffff,
            "overflow":  (value >> 48) & 1,
            "busy":      (value >> 49) & 1,
            "cpu_reset": (value >> 50) & 1,
        }

    def set_reset(self, hold):
        self._frame(bytes([CMD_RESET]) + struct.pack("<I", 1 if hold else 0))

    def write(self, word_address, data):
        """Store `data` from `word_address`. Returns the seconds the shift took."""
        payload = bytes([CMD_WRITE]) + struct.pack("<I", word_address) + data
        _, elapsed = self._frame(payload)
        return elapsed


def describe(status):
    return (f"signature {status['signature']:#06x}, staged {status['staged']} words, "
            f"overflow {status['overflow']}, busy {status['busy']}, "
            f"cpu_reset {status['cpu_reset']}")


def stage(sink, image, emit):
    """Write the image and its header. Returns (image seconds, total seconds)."""
    crc = zlib.crc32(image) & 0xffff_ffff

    # HyperRAM is 16 bits wide, so an odd-length image still fills its last word.
    # The pad is outside `length`, so the bootloader never reads it and the CRC is
    # over the image as given.
    padded = image + (b"\x00" if len(image) % 2 else b"")

    started = time.perf_counter()
    image_seconds = sink.write(IMAGE_WORD, padded)

    # Length and CRC first, magic last, matching `hyperram::write_header`. A run
    # that dies between the two leaves a header the bootloader ignores rather than
    # one it believes.
    sink.write(HDR_LENGTH, struct.pack("<II", len(image), crc))
    sink.write(HDR_MAGIC, struct.pack("<I", MAGIC))
    total = time.perf_counter() - started

    emit(f"crc32 {crc:#010x} over {len(image)} bytes")
    return image_seconds, total


def read_console(link, emit, want):
    """Drain the console until one of `want` appears, or the reads run out."""
    text = ""
    for _ in range(CONSOLE_READS):
        chunk = link.read_available()
        if chunk:
            text += chunk.decode("ascii", "replace")
            if any(marker in text for marker in want):
                break
    return text


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, nargs="?", help="raw binary to stage")
    parser.add_argument("--status", action="store_true",
                        help="read the sink's status and stop")
    parser.add_argument("--hold", action="store_true",
                        help="hold the CPU in reset and stop")
    parser.add_argument("--release", action="store_true",
                        help="release the CPU from reset and stop")
    parser.add_argument("--port", help="console tty; omit to use the USB console")
    parser.add_argument("--no-console", action="store_true",
                        help="stage without opening or reading a console")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        image = None
        if args.image is not None:
            if not args.image.exists():
                emit(f"no such image: {args.image}")
                return 1
            image = args.image.read_bytes()
            if not image or len(image) > MAX_IMAGE:
                emit(f"image is {len(image)} bytes; the slot is 1..{MAX_IMAGE}")
                return 1
            emit(f"{args.image.name}: {len(image)} bytes")
        elif not (args.status or args.hold or args.release):
            emit("nothing to do; pass an image, --status, --hold or --release")
            return 1

        from apollo_fpga import ApolloDebugger

        # The console is opened BEFORE the CPU is released, or the banner is gone by
        # the time anything is listening. Opening it first also means a port held by
        # another reader is reported before the board has been touched.
        link = None
        if image is not None and not args.no_console:
            from soc_shell import Link, other_readers
            try:
                link = Link.open(args.port)
                emit(f"console: {link.how}")
                if link.node:
                    thieves = other_readers(link.node)
                    if thieves:
                        emit("*** ANOTHER PROCESS IS READING THIS PORT ***")
                        for pid, command in thieves:
                            emit(f"      pid {pid}: {command}")
                        emit("Anything this reports about the console is contention,")
                        emit("not silence. Stop it, or run `./tio_user.py --serve`.")
            except Exception as error:
                emit(f"no console ({error}); staging anyway")

        debugger = ApolloDebugger()
        with debugger.jtag as chain:
            sink = Sink(chain)

            before = sink.status()
            emit(f"sink: {describe(before)}")
            if before["signature"] != SIGNATURE:
                emit(f"the sink did not answer on ER1 (want {SIGNATURE:#06x}).")
                emit("Either this bitstream has no JTAG staging sink, or something")
                emit("else is claiming ER1. `apollo configure` a current build.")
                if link:
                    link.close()
                return 1

            if args.status:
                if link:
                    link.close()
                return 0

            if args.hold or args.release:
                sink.set_reset(args.hold)
                emit(f"cpu_reset -> {sink.status()['cpu_reset']}")
                if link:
                    link.close()
                return 0

            # Held for the whole of the staging, not just the write. The shell owns
            # the same HyperRAM through its own CSR port, and it is executing from
            # the block RAM the image is about to replace.
            sink.set_reset(True)
            emit("CPU held in reset")

            image_seconds, total_seconds = stage(sink, image, emit)

            after = sink.status()
            emit(f"sink: {describe(after)}")

            words = (len(image) + 1) // 2
            ok = after["staged"] == words and not after["overflow"]
            if not ok:
                emit(f"the sink accepted {after['staged']} words, not {words}"
                     f"{' -- and its FIFO overflowed' if after['overflow'] else ''}.")
                emit("The image in HyperRAM is not the image on disk; the CRC below")
                emit("will say so too.")

            emit(f"image:  {len(image)} bytes in {image_seconds * 1e3:.1f} ms "
                 f"= {len(image) / image_seconds / 1024:.1f} KiB/s")
            emit(f"total:  header included, {total_seconds * 1e3:.1f} ms "
                 f"= {len(image) / total_seconds / 1024:.1f} KiB/s")

            sink.set_reset(False)
            emit("CPU released")

        if link:
            text = read_console(link, emit, ("crc ok", "crc MISMATCH"))
            link.close()
            emit("--- console ---")
            emit(text.strip())
            if "crc ok" not in text:
                emit()
                emit("No `crc ok` from the bootloader. `crc MISMATCH` means the")
                emit("image reached HyperRAM damaged; silence means the CPU did not")
                emit("restart, or nothing is reading this port.")
                emit(f"log: {LOG.relative_to(ROOT)}")
                return 1

        emit()
        emit(f"log: {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
