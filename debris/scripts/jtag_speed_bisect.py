#!/usr/bin/env python3
#
# Measure the standard JTAG benchmark against historical Apollo firmware.
# See awtoau/cynthion-workspace#100.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds, flashes and benchmarks past Apollo commits, so old claims get real numbers.

Three speed changes landed before `jtag_fixed_benchmark.py` existed, and their
effects were recorded on whatever bitstream was to hand at the time -- which is why
these docs held forty non-comparable millisecond figures. This restates them on the
standard test by actually reflashing each commit.

Not a `git bisect` despite the name: only four commits between the stock `v1.1.1`
tag and HEAD touch the JTAG or SPI path, so every one can be measured directly. A
bisect searches for a boundary; here the whole range fits in four data points.

## What each point costs

A DFU cycle plus a firmware build, roughly a minute. The board ends on whatever
commit was measured last, so the script restores HEAD's firmware at the end --
otherwise a run leaves the board on old firmware and the next thing to use it
behaves oddly for no visible reason.

## The risk, stated plainly

**This reflashes the microcontroller repeatedly.** If it is interrupted between
`boot_to_dfu()` and a successful program, the board sits in the bootloader. That is
recoverable -- `apollo exit-dfu`, or the script's own recovery path -- but it is the
reason this is a deliberate script rather than something to run casually.

It does not touch the FPGA. The benchmark leaves the TAP in SHIFT-DR with no
ISC_ENABLE, so whatever the FPGA is configured with survives.

    ./scripts/jtag_speed_bisect.py --list
    ./scripts/jtag_speed_bisect.py
    ./scripts/jtag_speed_bisect.py --commits v1.1.1 e034daa
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APOLLO = ROOT / "repos" / "apollo"
FIRMWARE = APOLLO / "firmware"
BINARY = FIRMWARE / "_build" / "cynthion_d11" / "firmware.bin"
LOG = ROOT / "tmp" / "logs" / "jtag_speed_bisect.log"
RESULTS = ROOT / "tmp" / "jtag_speed_bisect.json"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))

# The commits that touch the JTAG or SPI path, oldest first. Found with:
#   git log --oneline v1.1.1..HEAD -- firmware/src/jtag.c \
#       firmware/src/boards/cynthion_d11/spi.c apollo_fpga/jtag.py \
#       apollo_fpga/ecp5.py firmware/Makefile
DEFAULT_COMMITS = [
    ("v1.1.1", "stock release -- naive SPI loop, no LTO"),
    ("4bf7691", "enable LTO"),
    ("e034daa", "pipelined spi_send + suppress TDO readback (the 1.53x claim)"),
    ("0e9bfb1", "JTAG_BUFFER_SIZE define (no functional change)"),
    ("HEAD", "512-byte buffers + GET_INFO"),
]

# Bounded poll counts, not delays: each iteration is one lsusb call, so this is
# "give up after N checks" rather than a wall-clock wait. 200 is far more than a
# DFU transition needs and still returns promptly when the device never appears.
USB_POLL_LIMIT = 200


def poll_usb(token):
    for _ in range(USB_POLL_LIMIT):
        out = subprocess.run(["lsusb", "-d", "1d50:615c"],
                             capture_output=True, text=True).stdout
        if token in out:
            return True
    return False


def build_at(commit):
    """Check out one commit's firmware and build it. Returns (ok, detail)."""
    # Check out ONLY the firmware tree, never the whole submodule.
    #
    # A full checkout also replaces apollo_fpga/, the host library -- and
    # boot_to_dfu() is one of THIS project's additions, absent from stock v1.1.1.
    # So checking out an old commit removes the very method needed to flash it,
    # and every point fails with AttributeError before touching the board. That
    # happened, and it left the board in the bootloader when the run gave up
    # mid-cycle.
    #
    # Restricting the checkout to firmware/ keeps the host side at HEAD, which is
    # what should vary anyway: the measurement is of firmware behaviour, and
    # using one host library across all points removes it as a variable.
    result = subprocess.run(["git", "checkout", "-q", commit, "--", "firmware"],
                            cwd=APOLLO, capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"checkout failed: {result.stderr.strip()[:120]}"

    # Move aside source files this project ADDED, for commits that predate them.
    #
    # SOURCES is a wildcard over src/*.c, so a file we added stays in the build
    # even after checking out an older firmware tree -- it is untracked there, so
    # git leaves it alone. Building our stack_probe.c against stock v1.1.1 fails
    # twice over: -Werror=array-bounds on one construct, and then 107% ROM /
    # 103% RAM because stock has no LTO. Both are artifacts of mixing eras, not
    # facts about either commit.
    moved = []
    for name in ("stack_probe.c", "stack_probe.h",
                 "apollo_mode.c", "apollo_mode.h"):
        path = FIRMWARE / "src" / name
        tracked = subprocess.run(["git", "ls-tree", commit, f"firmware/src/{name}"],
                                 cwd=APOLLO, capture_output=True, text=True).stdout
        if path.exists() and not tracked.strip():
            path.rename(path.with_suffix(path.suffix + ".setaside"))
            moved.append(path)

    subprocess.run(["make", "APOLLO_BOARD=cynthion", "clean"], cwd=FIRMWARE,
                   capture_output=True, text=True)
    # Clean is not optional here. The buffer size lives in a header, and an
    # incremental build reuses stale objects -- which silently produced a binary
    # with the old size earlier in this work, reading exactly like the change
    # having no effect.
    result = subprocess.run(["make", "APOLLO_BOARD=cynthion"], cwd=FIRMWARE,
                            capture_output=True, text=True)

    for path in moved:
        path.with_suffix(path.suffix + ".setaside").rename(path)

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, f"build failed: {tail[-1][:120] if tail else '?'}"
    return True, None


def flash():
    """DFU cycle. Returns (ok, detail).

    Tolerates the board already being in the bootloader, which is where a
    previous interrupted run leaves it -- in that case skip straight to
    programming rather than failing on an ApolloDebugger that cannot enumerate a
    bootloader.
    """
    already_in_dfu = "Bootloader" in subprocess.run(
        ["lsusb", "-d", "1d50:615c"], capture_output=True, text=True).stdout

    if not already_in_dfu:
        from apollo_fpga import ApolloDebugger
        try:
            ApolloDebugger().boot_to_dfu()
        except Exception as error:
            return False, f"could not enter DFU: {type(error).__name__}"
        if not poll_usb("Bootloader"):
            return False, "never reached the bootloader"

    from fwup.dfu import DFUTarget
    try:
        target = DFUTarget(idVendor=0x1d50, idProduct=0x615c)
        target.program(BINARY.read_bytes())
        target.run_user_program()
    except Exception as error:
        return False, f"programming failed: {type(error).__name__}"
    if not poll_usb("Debugger"):
        return False, "never came back from the bootloader"
    return True, None


def benchmark(chunks):
    """Run the standard benchmark. Returns {chunk: best_ms} or None."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "jtag_fixed_benchmark.py"),
         "--chunks", *[str(c) for c in chunks], "--runs", "3"],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return None

    found = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        # "  256 B/chunk   480 chunks  best   558.8 ms ..."
        if len(parts) > 6 and parts[1] == "B/chunk" and "best" in parts:
            try:
                found[int(parts[0])] = float(parts[parts.index("best") + 1])
            except (ValueError, IndexError):
                continue
    return found or None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commits", nargs="+",
                        help="override the commit list")
    parser.add_argument("--chunks", type=int, nargs="+", default=[256],
                        help="chunk sizes; 256 is the only one every commit "
                             "supports, since 512 needs GET_INFO")
    parser.add_argument("--list", action="store_true",
                        help="show the plan and exit")
    args = parser.parse_args()

    plan = ([(c, "") for c in args.commits] if args.commits
            else DEFAULT_COMMITS)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("Apollo JTAG speed, across history, on the standard benchmark")
        emit(f"chunk sizes: {args.chunks}")
        emit()

        if args.list:
            for commit, note in plan:
                emit(f"  {commit:<10} {note}")
            emit()
            emit("Each point is a clean build plus a DFU cycle, roughly a minute.")
            emit("Only 256 B is measured by default: 512 needs GET_INFO, which")
            emit("only HEAD implements, so a 512 row on older firmware would")
            emit("silently run at the 256-byte fallback and read as no change.")
            return 0

        original = subprocess.run(["git", "rev-parse", "HEAD"], cwd=APOLLO,
                                  capture_output=True, text=True).stdout.strip()
        emit(f"restoring {original[:9]} when done")
        emit()

        results = {}
        try:
            for commit, note in plan:
                emit(f"  {commit:<10} {note}")
                ok, detail = build_at(commit)
                if not ok:
                    emit(f"             {detail}")
                    continue
                ok, detail = flash()
                if not ok:
                    emit(f"             {detail}")
                    continue
                timings = benchmark(args.chunks)
                if timings is None:
                    emit("             benchmark produced no result")
                    continue
                results[commit] = timings
                emit("             " + "  ".join(
                    f"{c} B: {t:.1f} ms" for c, t in sorted(timings.items())))
        finally:
            emit()
            emit(f"restoring {original[:9]}")
            subprocess.run(["git", "checkout", "-q", original, "--", "firmware"],
                           cwd=APOLLO, capture_output=True, text=True)
            ok, detail = build_at(original)
            if ok and flash()[0]:
                emit("             restored and flashed")
            else:
                emit("             RESTORE FAILED -- the board is on old "
                     "firmware; rebuild and reflash manually")

        RESULTS.write_text(json.dumps(results, indent=2) + "\n")
        emit()
        emit(f"results: {RESULTS}")
        emit(f"log: {LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
