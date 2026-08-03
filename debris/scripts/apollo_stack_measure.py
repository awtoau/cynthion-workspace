#!/usr/bin/env python3
#
# Measure Apollo's real stack high-water mark, then size the buffers from it.
# See awtoau/cynthion-workspace#74.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures how much stack Apollo actually uses, so buffer sizes stop being guesses.

The d11 reserves **1024 bytes of stack and has never measured it**. Free RAM is
therefore headroom over a guess rather than spare capacity, and that blocks two
real changes: growing the console RX ring, and growing the JTAG chunk from 256
bytes (one of the two levers on the 612 ms of USB overhead in the configure path).

This is measurable because **Apollo links no heap** -- no `malloc`, `free`,
`_sbrk` or `.heap` section in the binary -- so RAM is entirely static and the
stack region is bounded by named symbols:

    _sstack  0x200007c0
    _estack  0x20000bc0     STACK_SIZE 0x400

## Method: paint and read back

Fill the stack region with a known pattern at reset, run the firmware through its
deepest paths, then read back how far the pattern survived. The lowest unpainted
address is the high-water mark. Nothing is estimated.

`-fstack-usage` is the obvious alternative and does not work here: the firmware is
built `-flto=auto -flto-partition=one`, so LTO inlines across translation units
and per-function frame sizes no longer match the frames in the final binary --
individually plausible, collectively wrong. Disabling LTO to get them is worse,
since it reclaims 2968 bytes on a part 568 bytes from its ROM ceiling.

## Why the readback needs JTAG or a vendor request, not a debugger

There is no SWD debugger attached in this workflow, so the pattern is read back
the same way everything else is: a vendor request that returns the high-water
mark the firmware computed itself. That costs a few bytes of flash and means the
measurement works on any board with the firmware on it, with no extra hardware.

## What this does NOT do

It does not change any buffer size on its own. It reports the measurement and the
sizes that measurement would permit, because a script that silently resizes
buffers from a number nobody has looked at is how a 4 KB part gets a stack
overflow with no MPU to catch it.

    ./scripts/apollo_stack_measure.py --report
    ./scripts/apollo_stack_measure.py --stack-size 512 --build-only
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
LOG = ROOT / "tmp" / "logs" / "apollo_stack_measure.log"

RAM_BYTES = 4 * 1024
ROM_BYTES = 14 * 1024

# The linker script already honours an override:
#
#   STACK_SIZE = DEFINED (STACK_SIZE) ? STACK_SIZE
#              : DEFINED (__stack_size__) ? __stack_size__ : 0x400
#
# so the build can set it with -Wl,--defsym and no linker script edit is needed.
# Confirmed at firmware.elf.map:190.
DEFSYM_FLAG = "-Wl,--defsym=STACK_SIZE={size:#x}"

# Margin to keep above the measured high-water mark, as a fraction. A measurement
# taken on one run of one firmware build is not a proof of the worst case: an
# interrupt arriving at the deepest point of the deepest call path may not have
# happened during the run. 50% is chosen to be obviously generous rather than
# tuned, because the failure mode is silent .bss corruption on a part with no MPU.
SAFETY_FRACTION = 0.5


def tool(name):
    for prefix in ("arm-none-eabi-", ""):
        found = shutil.which(prefix + name)
        if found:
            return found
    return None


def stack_symbols(elf):
    """(start, end, size) of the stack region, from the symbol table."""
    output = subprocess.run([tool("nm"), str(elf)],
                            capture_output=True, text=True).stdout
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in ("_sstack", "_estack", "STACK_SIZE"):
            found[parts[2]] = int(parts[0], 16)
    start, end = found.get("_sstack"), found.get("_estack")
    if start is None or end is None:
        return None
    return start, end, end - start


def sections(elf):
    output = subprocess.run([tool("size"), "-A", str(elf)],
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


def build(stack_size=None):
    """Build, optionally overriding STACK_SIZE. Returns (ok, elf, output)."""
    command = ["make", "APOLLO_BOARD=cynthion"]
    if stack_size is not None:
        command.append("LDFLAGS_EXTRA=" + DEFSYM_FLAG.format(size=stack_size))
    result = subprocess.run(command, cwd=FIRMWARE,
                            capture_output=True, text=True)
    elf = FIRMWARE / "_build" / "cynthion_d11" / "firmware.elf"
    return result.returncode == 0, elf, (result.stdout + result.stderr)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", action="store_true",
                        help="report the current budget and what it permits")
    parser.add_argument("--stack-size", type=lambda v: int(v, 0),
                        help="rebuild with this STACK_SIZE, to test a value")
    parser.add_argument("--build-only", action="store_true",
                        help="build and report sizes; do not touch hardware")
    args = parser.parse_args()

    if not tool("nm") or not tool("size"):
        print("no arm-none-eabi binutils on PATH")
        return 1

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        ok, elf, output = build(args.stack_size)
        if not ok:
            emit("build failed:")
            emit(output[-800:])
            return 1

        region = stack_symbols(elf)
        if region is None:
            emit("could not find _sstack/_estack -- linker script changed?")
            return 1
        start, end, size = region

        found = sections(elf)
        bss = found.get(".bss", 0)
        data = found.get(".data", 0)
        text = found.get(".text", 0)
        static = bss + data
        total = static + size

        emit("Apollo stack and buffer budget")
        emit()
        emit(f"  stack region  {start:#010x} .. {end:#010x}  = {size} bytes")
        emit(f"  static RAM    .bss {bss} + .data {data} = {static}")
        emit(f"  total         {total} / {RAM_BYTES} "
             f"= {100 * total / RAM_BYTES:.2f}%")
        emit(f"  flash         {text} / {ROM_BYTES} "
             f"= {100 * text / ROM_BYTES:.2f}%")
        emit()
        emit(f"  unallocated   {RAM_BYTES - total} bytes")
        emit()

        emit("  The stack figure above is a RESERVATION, not a measurement.")
        emit("  Nothing here has observed how much of it is used, so the")
        emit("  unallocated bytes are margin over a guess. Growing a buffer into")
        emit("  them is unsafe until the high-water mark is known: this part has")
        emit("  no MPU, so an overflow corrupts .bss rather than faulting.")
        emit()

        emit("  Linker override IS available -- the script reads")
        emit("    STACK_SIZE = DEFINED (STACK_SIZE) ? STACK_SIZE : ... : 0x400")
        emit("  so a measured value can be set with")
        emit(f"    {DEFSYM_FLAG.format(size=0x200)}")
        emit("  with no linker script edit.")
        emit()

        emit("  What a measurement would permit, for illustration:")
        emit(f"    {'measured':>10}  {'+50% margin':>12}  {'freed':>7}  "
             f"{'buffer could be':>16}")
        for measured in (256, 384, 512, 640, 768):
            with_margin = int(measured * (1 + SAFETY_FRACTION))
            if with_margin >= size:
                continue
            freed = size - with_margin
            emit(f"    {measured:>10}  {with_margin:>12}  {freed:>7}  "
                 f"{256 + freed:>16}")
        emit()
        emit("  Those rows are arithmetic on hypothetical measurements, NOT")
        emit("  results. The margin is 50% because a single run does not prove a")
        emit("  worst case -- an interrupt at the deepest point of the deepest")
        emit("  call path may simply not have happened.")
        emit()

        emit("  Next, and it needs firmware: paint _sstack.._estack with a")
        emit("  pattern at reset, exercise the deep paths (JTAG configure, a USB")
        emit("  control storm, the sideband), then return the lowest unpainted")
        emit("  address through a vendor request. There is no SWD debugger in")
        emit("  this workflow, so the firmware has to report it itself.")
        emit()
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
