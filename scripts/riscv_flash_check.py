#!/usr/bin/env python3
#
# Configure the VexiiRiscv SoC and check what it says about the SPI flash.
# SPDX-License-Identifier: BSD-3-Clause

"""
Configures the FPGA, reads the RISC-V console, and decides whether the
memory-mapped flash works.

This exists because "did it work" has four separate answers here and reading
them off a terminal by eye gets two of them wrong. It checks all four:

  1. the SoC still runs at all  -- `prod 369d0368` and well-formed `tick` lines
  2. the flash identifies       -- `flash jedec ef4016`
  3. reads are repeatable       -- two reads of one offset, byte-identical
  4. no dropped characters      -- see below

Character corruption is checked explicitly and separately from value
correctness because a real bug here produced correct counter VALUES with
dropped CHARACTERS -- `tic 00000`, `tck 000001` -- when a FIFO spanned two
clock domains. Counting valid `tick NNNNNNNN` lines alone would have called
that a pass, because the values that did arrive were right. So this rejects any
line that is not exactly one of the shapes the firmware emits.

THE PORT IS RESOLVED BY USB IDENTITY, NEVER BY NODE NUMBER. This workstation
has eleven ttyACM nodes across four vendors, and an earlier investigation into
a silent SoC spent hours reading an ST-LINK.

    ./scripts/riscv_flash_check.py
    ./scripts/riscv_flash_check.py --no-configure   # board already programmed
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_flash_check.log"
BITSTREAM = ROOT / "tmp" / "vexii_hello" / "build" / "top.bit"

# Where Apollo's reference reads are dropped. `flash-read` writes to a file
# rather than stdout, so it needs somewhere to put them.
MIRROR = ROOT / "tmp" / "flash_reference"
APOLLO_CLI = ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"

sys.path.insert(0, str(ROOT / "ecp5-test"))

# What the firmware prints. Anchored and exact: a pattern loose enough to match
# `tic 00000` would defeat the point of checking for dropped characters.
EXPECTED = {
    "banner": re.compile(r"^RISC-V on Cynthion: block RAM, USB console\.$"),
    "sum":    re.compile(r"^sum  ([0-9a-f]{8})$"),
    "prod":   re.compile(r"^prod ([0-9a-f]{8})$"),
    "jedec":  re.compile(r"^flash jedec  ([0-9a-f]{8})$"),
    "at0":    re.compile(r"^flash @0     ([0-9a-f]{8})$"),
    "at128k": re.compile(r"^flash @128K  ([0-9a-f]{8}) ([0-9a-f]{8})  "
                         r"(same|DIFFER)$"),
    "bench":  re.compile(r"^flash bench  ([0-9a-f]{8}) cycles / ([0-9a-f]{8})"
                         r" words, sum ([0-9a-f]{8})$"),
    "tick":   re.compile(r"^tick ([0-9a-f]{8})$"),
}

# The arithmetic the firmware does, as a fixed expectation. These are the
# regression test for the CPU itself: they were correct before the flash was
# added and must still be.
EXPECT_SUM = "acf13568"
EXPECT_PROD = "369d0368"

# JEDEC EF 40 16 -- Winbond W25Q32, 4 MiB. Printed as a 32-bit hex word, so the
# top byte is zero.
EXPECT_JEDEC = "00ef4016"

# The CPU clock, for turning cycle counts into a rate. Must match SYNC_MHZ in
# ecp5-test/riscv/vexii_hello_soc.py.
SYNC_MHZ = 80

# How many console lines to read before deciding.
#
# Not a duration: the firmware emits a fixed prologue and then ticks about once
# a second, so a line budget bounds the run by the thing being measured rather
# than by a clock. 12 covers the prologue (7 lines including blanks) plus
# several ticks -- enough to see the tick counter actually advance, which one
# tick alone would not show. If the SoC is dead, the read below blocks on its
# serial timeout instead and reports what it did get.
LINES_WANTED = 12

# Serial read timeout, in seconds, per line.
#
# Waiting for: one line of console output. Why this value: the firmware's tick
# loop is a busy-wait calibrated for roughly one second at 60 MHz, so a healthy
# board produces a line at least that often; 3 s is comfortably more than one
# tick period and still bounds a dead board to seconds rather than forever. On
# expiry: the read returns short, the loop stops, and the collected lines are
# reported as-is -- a partial transcript is diagnostic, a hang is not.
LINE_TIMEOUT_S = 3.0

# How many `udevadm settle` rounds to wait through for the console to appear.
# The same value scripts/riscv_clock_ladder.py uses against the same board.
TTY_SETTLE_LIMIT = 20


# The offset the firmware reads twice and benchmarks. Must match
# FLASH_TEST_OFFSET in scripts/riscv_firmware.py.
BENCH_OFFSET = 0x00020000


def emit(handle, text=""):
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def expected_words():
    """What the flash holds at the checked offsets, read by an independent path.

    `apollo flash-read` reaches the flash over JTAG, through Apollo's own
    controller on the microcontroller -- a completely separate path from the
    gateware under test, sharing only the flash chip itself. That makes it a
    real reference: if the two disagree, the gateware is wrong.

    The locally built .bit file is NOT the reference, and using it would be a
    mistake worth naming. `apollo configure` loads a bitstream over JTAG
    directly into the FPGA's configuration SRAM; it never writes flash. So the
    flash still holds whatever bitstream was programmed into it previously,
    which is generally not the one just built. The first few bytes would match
    anyway -- every ECP5 bitstream opens with the same preamble -- so that
    comparison would appear to pass and mean nothing.

    Returns {byte offset: lowercase 8-hex-digit little-endian word}, or an empty
    dict if Apollo cannot read the flash, in which case the caller reports the
    values it saw without comparing rather than inventing an expectation.
    """
    MIRROR.parent.mkdir(parents=True, exist_ok=True)
    words = {}
    for offset in (0, BENCH_OFFSET):
        target = MIRROR.with_suffix(f".{offset:08x}.bin")
        result = subprocess.run(
            [sys.executable, str(APOLLO_CLI), "flash-read",
             "--offset", str(offset), "--length", "4", str(target)],
            capture_output=True, text=True, cwd=str(ROOT))
        if result.returncode != 0 or not target.exists():
            return {}
        chunk = target.read_bytes()[:4]
        if len(chunk) < 4:
            return {}
        words[offset] = f"{int.from_bytes(chunk, 'little'):08x}"
    return words


def configure(handle):
    """Load the bitstream over JTAG via Apollo."""
    if not BITSTREAM.exists():
        emit(handle, f"no bitstream at {BITSTREAM}")
        return False

    emit(handle, f"configuring {BITSTREAM}")
    result = subprocess.run(
        [sys.executable, str(APOLLO_CLI), "configure", str(BITSTREAM)],
        capture_output=True, text=True, cwd=str(ROOT))
    for line in (result.stdout + result.stderr).strip().splitlines():
        emit(handle, f"    {line}")
    if result.returncode != 0:
        emit(handle, "configure failed")
        return False
    return True


def collect(handle):
    """Open the console by USB identity and return the lines it printed."""
    import serial

    import usb_ids

    # `usb_ids.wait_for_tty` settles udev and confirms the port actually opens,
    # rather than trusting a path to exist -- immediately after a reconfigure
    # the node from BEFORE it can still be present, and it will not open.
    #
    # Waiting for: the CDC-ACM device to enumerate after reconfiguration. Why
    # this many settles: each iteration blocks on `udevadm settle`, which drains
    # the kernel's uevent queue, so the loop advances on real device activity
    # rather than on a counter. TTY_SETTLE_LIMIT is the value
    # scripts/riscv_clock_ladder.py already uses for the same board and the same
    # device. On expiry: report that nothing appeared -- never fall back to
    # reading whatever node happens to exist, which on this workstation means an
    # ST-LINK.
    node = usb_ids.wait_for_tty("riscv_console", settles=TTY_SETTLE_LIMIT)
    if node is None:
        emit(handle, "no riscv_console tty appeared")
        emit(handle, "  the SoC did not enumerate, or USB is not up")
        return None

    emit(handle, f"console: {node}")
    lines = []
    with serial.Serial(node, 115200, timeout=LINE_TIMEOUT_S) as port:
        while len(lines) < LINES_WANTED:
            raw = port.readline()
            if not raw:
                emit(handle, "  read timed out; reporting what arrived")
                break
            lines.append(raw.decode("ascii", "replace").rstrip("\r\n"))
    return lines


def check(handle, lines, expected):
    """Match every line against the expected shapes and report."""
    emit(handle)
    emit(handle, "console transcript:")
    for line in lines:
        emit(handle, f"    {line!r}")
    emit(handle)

    seen = {}
    malformed = []
    for line in lines:
        if line == "":
            continue
        for name, pattern in EXPECTED.items():
            match = pattern.match(line)
            if match:
                seen.setdefault(name, []).append(match.groups())
                break
        else:
            # Not blank and matching nothing: this is the dropped-character
            # signature. Report the line verbatim -- what it turned into says
            # which characters were lost.
            malformed.append(line)

    failures = []

    # 1. The SoC still runs. `prod` is the arithmetic check; ticks prove it is
    #    still running afterwards rather than having stopped in the flash code.
    if seen.get("prod", [("",)])[0][0] != EXPECT_PROD:
        failures.append(f"prod: got {seen.get('prod')}, want {EXPECT_PROD}")
    else:
        emit(handle, f"  prod         {EXPECT_PROD}  as before")

    if seen.get("sum", [("",)])[0][0] != EXPECT_SUM:
        failures.append(f"sum: got {seen.get('sum')}, want {EXPECT_SUM}")
    else:
        emit(handle, f"  sum          {EXPECT_SUM}  as before")

    ticks = [int(g[0], 16) for g in seen.get("tick", [])]
    if len(ticks) < 2:
        failures.append(f"ticks: {len(ticks)} well-formed tick lines, want >= 2")
    elif ticks != list(range(ticks[0], ticks[0] + len(ticks))):
        failures.append(f"ticks: not consecutive: {ticks}")
    else:
        emit(handle, f"  ticks        {ticks}  consecutive")

    # 2. The flash identifies itself.
    jedec = seen.get("jedec", [("",)])[0][0]
    if jedec != EXPECT_JEDEC:
        failures.append(f"jedec: got {jedec!r}, want {EXPECT_JEDEC}")
        emit(handle, f"  jedec        {jedec}  WRONG (want {EXPECT_JEDEC})")
    else:
        emit(handle, f"  jedec        {jedec}  Winbond W25Q32, 4 MiB")

    # 3. Reads through the memory map, checked against Apollo's own read of the
    #    same offsets.
    #
    #    "The CPU read the same value twice" only proves the read is stable, and
    #    a controller stuck returning a constant passes that perfectly. Apollo
    #    reaches the flash over JTAG through entirely different hardware, so
    #    agreement between the two turns "stable" into "correct".
    if "at0" in seen:
        got = seen["at0"][0][0]
        want = expected.get(0)
        if want is None:
            emit(handle, f"  flash @0     {got}  (no bitstream to compare)")
        elif got != want:
            failures.append(f"flash @0: read {got}, bitstream has {want}")
            emit(handle, f"  flash @0     {got}  WRONG (bitstream: {want})")
        else:
            emit(handle, f"  flash @0     {got}  matches the bitstream file")

    if "at128k" in seen:
        first, second, verdict = seen["at128k"][0]
        want = expected.get(BENCH_OFFSET)
        note = ""
        if verdict != "same":
            failures.append(f"repeat read differs: {first} vs {second}")
        if first == "00000000":
            failures.append("read is all zeros -- flash not responding")
        elif first == "ffffffff":
            # Correct for erased flash, but this offset is inside the
            # bitstream, so all-ones here means the read failed.
            failures.append("read is all ones -- inside the bitstream, so "
                            "this is a failed read rather than erased flash")
        if want is not None:
            if first != want:
                failures.append(f"flash @128K: read {first}, "
                                f"bitstream has {want}")
                note = f"  WRONG (bitstream: {want})"
            else:
                note = "  matches the bitstream file"
        emit(handle, f"  flash @128K  {first} {second}  {verdict}{note}")
    else:
        failures.append("no `flash @128K` line")

    # 4. Throughput, derived rather than asserted -- there is no threshold to
    #    pass, only a number to report.
    if "bench" in seen:
        cyc, words, total = (int(v, 16) for v in seen["bench"][0])
        if cyc:
            bytes_read = words * 4
            seconds = cyc / (SYNC_MHZ * 1e6)
            emit(handle, f"  bench        {cyc} cycles for {bytes_read} bytes"
                         f" = {bytes_read / seconds / 1e6:.2f} MB/s"
                         f" ({cyc / words:.1f} cycles/word)")
        if total == 0:
            failures.append("bench sum is zero -- every word read as zero")

    # 5. Dropped characters.
    if malformed:
        for line in malformed:
            failures.append(f"malformed line {line!r} -- dropped characters?")
    else:
        emit(handle, "  characters   no malformed lines")

    emit(handle)
    if failures:
        emit(handle, "FAIL")
        for failure in failures:
            emit(handle, f"    {failure}")
        return False
    emit(handle, "PASS")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-configure", action="store_true",
                        help="skip programming; read a board already running")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        # The reference read comes FIRST, and the order is not incidental.
        # `apollo flash-read` needs the flash bus to itself, so it forces the
        # FPGA offline to take it -- doing this after configuring would kill the
        # SoC that is about to be measured. Configuring afterwards puts the
        # board back in the state the rest of this script expects.
        expected = {}
        if not args.no_configure:
            emit(handle, "reading reference bytes over JTAG (forces the FPGA "
                         "offline)")
            expected = expected_words()
            if expected:
                for offset, word in sorted(expected.items()):
                    emit(handle, f"    apollo @{offset:#x}: {word}")
            else:
                emit(handle, "    unavailable; values will be reported "
                             "without comparison")

            if not configure(handle):
                return 1

        lines = collect(handle)
        if lines is None:
            return 1

        ok = check(handle, lines, expected)
        emit(handle)
        emit(handle, f"log: {LOG}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
