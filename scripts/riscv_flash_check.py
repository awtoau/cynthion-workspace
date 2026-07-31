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
    "pass":   re.compile(r"^--- flash pass ([0-9a-f]{8})$"),
    # The trailing text is either a capacity report or a no-response verdict,
    # both of which the checker interprets rather than the pattern.
    "jedec":  re.compile(r"^jedec        ([0-9a-f]{8})  (.+)$"),
    "at0":    re.compile(r"^read @0      ([0-9a-f]{8})$"),
    "atbench": re.compile(r"^read @0x40   ([0-9a-f]{8}) ([0-9a-f]{8})  "
                         r"(same|DIFFER)  2nd read ([0-9a-f]{8}) cycles$"),
    "bench":  re.compile(r"^read bench   ([0-9a-f]{8}) cycles / ([0-9a-f]{8})"
                         r" words, sum ([0-9a-f]{8})$"),
    "erase":  re.compile(r"^erase 4K     ([0-9a-f]{8}) cycles, after "
                         r"([0-9a-f]{8})  (erased|NOT ERASED)$"),
    "program": re.compile(r"^program 256B ([0-9a-f]{8}) cycles$"),
    "verify": re.compile(r"^verify       ([0-9a-f]{8}) of ([0-9a-f]{8}) words"
                         r" differ(, first @([0-9a-f]{8}) want ([0-9a-f]{8})"
                         r" got ([0-9a-f]{8}))?$"),
    "cached": re.compile(r"^cached word  ([0-9a-f]{8}) want ([0-9a-f]{8})  "
                         r"(coherent|STALE \(cache\))$"),
    "tick":   re.compile(r"^tick ([0-9a-f]{8})$"),
}

# The arithmetic the firmware does, as a fixed expectation. These are the
# regression test for the CPU itself: they were correct before the flash was
# added and must still be.
EXPECT_SUM = "acf13568"
EXPECT_PROD = "369d0368"

# The JEDEC ID is checked STRUCTURALLY, not against a value.
#
# This board reads 00ef4016 -- Winbond (ef), SPI NOR (40), capacity 2^0x16 =
# 4 MiB -- and that is recorded here so the expected value is on the record. It
# is deliberately not asserted: ef4017 is the 8 MiB W25Q64, ef4018 the 16 MiB
# W25Q128, c22016 a Macronix equivalent and 9d6016 an ISSI one. All are healthy
# parts that an equality check would call a failure, and what the read is
# actually testing is whether the controller can issue an arbitrary command and
# get a sane answer -- not which chip is fitted.
#
# So only the two "nothing answered" patterns fail: all-zeros means nothing
# drove the bus, all-ones means it floated high.
JEDEC_THIS_BOARD = "00ef4016"
JEDEC_NO_RESPONSE = ("00000000", "00ffffff", "ffffffff")

# The capacity the rest of the SoC assumes. The memory map is sized at 4 MiB and
# everything above it aliases back to offset 0, so a part reporting a different
# capacity would make the address map wrong -- worth failing on, unlike the
# manufacturer bytes.
EXPECT_CAPACITY = 4 * 1024 * 1024

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

# How many rounds to wait through for the console to appear after a
# reconfigure. See wait_for_console() for why this cannot be a bare
# `udevadm settle` loop.
TTY_ROUNDS = 60


# The offset the firmware reads twice and benchmarks. Must match
# FLASH_TEST_OFFSET in scripts/riscv_firmware.py.
BENCH_OFFSET = 0x00000040


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


def wait_for_console(handle):
    """Block until the RISC-V console tty appears and opens, or give up.

    `usb_ids.wait_for_tty` is the right helper and is used here to do the actual
    identification -- by VID:PID out of sysfs, never by node number, because
    this workstation has eleven ttyACM nodes across four vendors and an earlier
    investigation spent hours reading an ST-LINK.

    What it cannot do alone is wait. Each of its iterations blocks on
    `udevadm settle`, which drains the kernel's uevent queue and then returns --
    and immediately after `apollo configure` that queue is ALREADY EMPTY,
    because the FPGA has not finished reconfiguring and the device has not begun
    to enumerate, so there are no events yet to drain. All of its rounds
    complete in milliseconds and it reports nothing found, on a board whose
    console appears healthily a second later. That happened twice here before
    it was understood; its own docstring warns about the bare-poll version of
    this exact mistake.

    So this blocks on `udevadm monitor` instead, which does not drain a queue --
    it subscribes to the kernel's uevent stream and emits a line per device
    event as it happens. Reading one line from it is a genuine block until the
    kernel does something, which is the condition actually being waited on.
    Each tty add during enumeration wakes the loop, and the check runs again.

    Waiting for: FPGA reconfiguration, USB enumeration, and the kernel binding a
    CDC-ACM driver. Why these values: the monitor is killed after TTY_ROUNDS
    events or when the console is found, whichever comes first; enumerating this
    board produces a few dozen events, so 60 covers it with margin while
    bounding a dead board to the events it does produce plus the poll below. On
    expiry: return None and report it -- never fall back to a guessed node.
    """
    import usb_ids

    # Check first: with --no-configure, or a board that never went away, the
    # console is already there and no event will ever arrive to announce it.
    node = usb_ids.wait_for_tty("riscv_console", settles=1)
    if node:
        return node

    monitor = subprocess.Popen(
        ["udevadm", "monitor", "--udev", "--subsystem-match=tty"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for _ in range(TTY_ROUNDS):
            # Blocks until the kernel reports a tty event. This is the line
            # that makes the loop a wait rather than a spin.
            if monitor.stdout.readline() == "":
                break
            node = usb_ids.wait_for_tty("riscv_console", settles=1)
            if node:
                emit(handle, "  console appeared after a tty event")
                return node
    finally:
        monitor.terminate()
        monitor.wait()
    return None


def collect(handle):
    """Open the console by USB identity and return the lines it printed."""
    import serial

    import usb_ids

    node = wait_for_console(handle)
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

    # 2. The controller's command path answers.
    #
    #    Structural, not an equality check against this board's part number --
    #    see JEDEC_THIS_BOARD. Only "nothing answered" fails, plus a capacity
    #    that contradicts the 4 MiB the address map is built around.
    if "jedec" not in seen:
        failures.append("no `jedec` line -- the firmware did not get that far")
    else:
        jedec, detail = seen["jedec"][0]
        if jedec in JEDEC_NO_RESPONSE:
            failures.append(f"jedec {jedec}: nothing answered on the command "
                            f"path ({detail})")
            emit(handle, f"  jedec        {jedec}  {detail}")
        else:
            capacity = 1 << (int(jedec, 16) & 0xff)
            note = ""
            if capacity != EXPECT_CAPACITY:
                failures.append(
                    f"jedec {jedec}: capacity byte says {capacity} bytes, but "
                    f"the memory map is sized for {EXPECT_CAPACITY} and the "
                    f"region above it aliases offset 0")
                note = "  CAPACITY MISMATCH"
            elif jedec != JEDEC_THIS_BOARD:
                # A different but plausible part. Not a failure.
                note = f"  (this board previously read {JEDEC_THIS_BOARD})"
            emit(handle, f"  jedec        {jedec}  command path works, "
                         f"{capacity // (1024 * 1024)} MiB{note}")

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

    if "atbench" in seen:
        first, second, verdict = seen["atbench"][0]
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
                failures.append(f"read @0x40: read {first}, "
                                f"bitstream has {want}")
                note = f"  WRONG (bitstream: {want})"
            else:
                note = "  matches the bitstream file"
        emit(handle, f"  read @0x40  {first} {second}  {verdict}{note}")
    else:
        failures.append("no `read @0x40` line")

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

    # 5. Erase, program and verify -- only present in a --write-tests image.
    if "erase" in seen:
        cyc, after, verdict = seen["erase"][0]
        seconds = int(cyc, 16) / (SYNC_MHZ * 1e6)
        emit(handle, f"  erase 4K     {int(cyc, 16)} cycles = "
                     f"{seconds * 1e3:.1f} ms, reads back {after} ({verdict})")
        if verdict != "erased":
            failures.append(f"sector did not erase: reads {after}, want "
                            f"ffffffff")

    if "program" in seen:
        cyc = int(seen["program"][0][0], 16)
        seconds = cyc / (SYNC_MHZ * 1e6)
        emit(handle, f"  program 256B {cyc} cycles = {seconds * 1e3:.2f} ms")

    if "verify" in seen:
        for groups in seen["verify"]:
            bad, total = int(groups[0], 16), int(groups[1], 16)
            if bad:
                index, want, got = groups[3], groups[4], groups[5]
                failures.append(
                    f"verify: {bad} of {total} words differ, first at word "
                    f"{int(index, 16)}: wrote {want}, read {got}")
                emit(handle, f"  verify       {bad}/{total} differ, first "
                             f"@{int(index, 16)} want {want} got {got}")
            else:
                emit(handle, f"  verify       {total}/{total} words match")

    if "cached" in seen:
        for got, want, verdict in seen["cached"]:
            # Reported, never failed on. A stale cached read after a write is a
            # real property of a cacheable mapping with no invalidate
            # instruction built into this CPU -- see flash_read32_uncached.
            emit(handle, f"  cached word  {got} (wrote {want}) -- {verdict}")

    # 6. Dropped characters.
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
