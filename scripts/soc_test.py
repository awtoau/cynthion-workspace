#!/usr/bin/env python3
#
# Run the SoC shell under QEMU and assert what it says.
# SPDX-License-Identifier: BSD-3-Clause

"""
Boots the RISC-V firmware on `qemu-system-riscv32 -M virt`, drives its shell over a
pipe, and checks the replies. Non-interactive; exit status 0 if every assertion held.

    ./scripts/soc_test.py            # build the QEMU variant and test it
    ./scripts/soc_test.py --no-build # test what is already built
    ./scripts/soc_test.py -v         # also dump the full session transcript

`scripts/soc_run.py` runs this before it configures the board, and will not configure if
it fails. `--skip-tests` there is the escape hatch.

## Why an emulator at all

Every question asked of this shell so far has cost a ~60 s bitstream rebuild, a
reconfigure, and a USB enumeration that reliably eats the first half second of output.
That loop is too slow to debug a line editor with, and worse, it cannot distinguish
"the shell logic is wrong" from "the console peripheral is not delivering bytes" --
both look like a board that says nothing. QEMU removes the peripheral from the question:
if the shell misbehaves here, the bug is in the firmware's logic, and if it behaves here
and not on the board, the bug is below the firmware.

That argument only holds because both builds are the same source, and it got considerably
stronger when the SoC's console became a standard NS16550A
(`ecp5-test/riscv/uart16550.py`). `virt` presents an NS16550A too, so `src/uart.rs` --
the console driver itself, the thing that polls LSR and pokes THR -- is now compiled
unchanged for both. `--features qemu` selects a different list of base addresses in
`src/target.rs`, a flash stand-in, and a RAM array in place of the three HyperRAM MMIO
primitives. Nothing else.

That matters because the failure this SoC has actually suffered was *in the console
peripheral*, not above it: a read with a side effect sharing a 32-bit word with the
register the poll loop reads. A test running against a re-implemented console driver
could not have seen it and did not. This one at least drives the same driver.

The line editor, `run()`, `parse_hex()`, the idle re-banner and the CRLF translation are
literally the same instructions. If that ever stops being true this script stops being
evidence, so keep the differences in `src/target.rs`.

## What is asserted, and why each one

Every check below is a failure that has actually happened, or one that would be
invisible from the host if it happened:

  banner            the firmware reached `main` and the console works at all
  <enter> -> prompt Enter is recognised; the shell is not wedged in its poll loop
  help / ?          dispatch works, and the alias is not a separate code path
  unknown command   the fallthrough arm exists -- silence here reads as a hang
  check             the CPU's own arithmetic, and that results are formatted, not
                    hardcoded (0x12345678 * 3 == 0x369d0368)
  backspace         the on-screen erase AND the line buffer edit agree
  CRLF              no bare LF ever reaches the wire
  irq               the console is interrupt-driven, and the count is climbing

The interrupt check has a weaker sibling that is already implicit in every check
above it: the shell reads received bytes ONLY from the ring that
`firmware/cynthion-soc/src/irq.rs` fills from the machine external interrupt
handler. Nothing polls LSR for input any more. So a broken interrupt path is a
shell that never answers, and every assertion here would fail at once.

`irq` is asserted anyway because that failure mode is the loud one. The quiet
one is a source that is enabled and never fires, or a claim that was taken and
never completed -- which on a two-console board leaves one port dead while the
other works perfectly. The count and the pending word are what distinguish them,
and this target has a real PLIC (`plic@c000000`, the 16550 on source 10) rather
than a stand-in, so the check means the same thing here as on the board.

The CRLF one is the reason this file exists. On a raw CDC-ACM pipe nothing supplies a
line discipline, so a bare `\\n` from `writeln!` moves the cursor down a line without
returning it to column zero: output marches diagonally off the screen and the prompt is
never where it should be. It presents as "the shell is ignoring my keystrokes" while
every keystroke is in fact being handled. That fix has been made and reverted once
already, which is exactly what an assertion is for.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_test.log"
CRATE = ROOT / "firmware" / "cynthion-soc"

# A target dir of its own, NOT the crate's.
#
# The QEMU build differs by a feature and a linker script, so sharing `target/` would
# make every alternation a full rebuild -- and, far worse, would leave a QEMU-linked
# binary at the path soc_run.py objcopies into the bitstream. An image linked for
# 0x80000000 in block RAM at 0 is a board that fetches from nothing.
BUILD_DIR = ROOT / "tmp" / "qemu-build"
ELF = BUILD_DIR / "riscv32imac-unknown-none-elf" / "release" / "cynthion-soc"

QEMU = "qemu-system-riscv32"

# `virt`, because its 16550 at 0x10000000 and DRAM at 0x80000000 are what
# firmware/cynthion-soc/memory-qemu.x and the qemu branch of src/target.rs are written
# against. Both addresses were read out of `-machine dumpdtb`, not assumed.
#
# `-bios none` matters: the default is OpenSBI, which loads at 0x80000000 -- exactly
# where our .text goes. `-kernel` with an ELF then makes QEMU's reset stub jump straight
# to our entry point in machine mode, which is the mode riscv-rt's _start expects.
QEMU_ARGS = [
    "-M", "virt",
    "-cpu", "rv32",
    "-m", "64M",
    "-display", "none",
    "-monitor", "none",
    "-serial", "stdio",
    "-bios", "none",
]

# How long to wait for the firmware's first byte.
#
# The firmware itself prints within microseconds of the CPU starting; essentially all of
# this budget is QEMU's own startup -- fork/exec, dynamic linking, TCG init -- measured
# here at well under 0.5 s. 5 s is an order of magnitude of headroom so a loaded machine
# cannot produce a spurious failure. Expiry means the firmware never wrote to the UART at
# all, which is a real result and reported as one.
BOOT_S = 5.0

# How long to wait for the shell to answer one command.
#
# Under TCG the shell parses and prints a reply in well under a millisecond of wall time.
# The budget is for pipe scheduling between two processes, not for the guest. Expiry
# means the shell did not respond to a command it should have -- the exact symptom this
# script is chasing -- so it is reported with what was expected and everything received.
REPLY_S = 3.0

# How long to allow for the idle re-banner.
#
# The firmware re-announces after 12,000,000 turns of its poll loop, sized for ~2 s at
# 60 MHz on the board. Under TCG each turn costs one emulated MMIO read, which lands in
# the same place: measured at 2.05 s here, against 0.04 s to the first banner. 8 s is
# ~4x that, enough that a machine under load does not fail the check, and short enough
# that a shell which has genuinely stopped re-announcing is reported quickly. Expiry
# means the poll loop stopped turning or `spoken` latched with nothing typed -- which is
# precisely the symptom the board shows.
IDLE_S = 8.0

# How often the expect loop rechecks the buffer while waiting.
#
# The reader thread appends as bytes arrive, so this only sets how promptly a satisfied
# assertion is noticed. 20 ms costs at most 20 ms per assertion and a few dozen wakeups a
# second; polling faster just burns CPU racing a pipe that is already being drained.
POLL_S = 0.02


class Session:
    """One QEMU run, with its serial console on a pipe.

    Output is drained by a thread rather than read on demand. A guest that prints while
    nothing is reading fills the pipe buffer and blocks inside `put()`, which would look
    from here exactly like a hung shell -- the failure this script exists to detect,
    manufactured by the test harness. So: always draining, and `expect` only ever reads
    from the buffer.
    """

    def __init__(self, elf):
        self.proc = subprocess.Popen(
            [QEMU, *QEMU_ARGS, "-kernel", str(elf)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.errors = bytearray()
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        self.errs = threading.Thread(target=self._drain_err, daemon=True)
        self.errs.start()

    def _drain(self):
        while True:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                return
            with self.lock:
                self.buf.extend(chunk)

    def _drain_err(self):
        # QEMU's own complaints (bad machine, missing accelerator) land here and are the
        # difference between "the firmware is silent" and "QEMU never started".
        while True:
            chunk = self.proc.stderr.read(1)
            if not chunk:
                return
            self.errors.extend(chunk)

    def snapshot(self):
        with self.lock:
            return bytes(self.buf)

    def send(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def expect(self, needle, budget, since=0):
        """Wait for `needle` to appear at or after offset `since`. Index, or None."""
        deadline = time.monotonic() + budget
        while True:
            found = self.snapshot().find(needle, since)
            if found >= 0:
                return found
            if time.monotonic() >= deadline:
                return None
            if self.proc.poll() is not None:
                # A dead QEMU will never produce it; do not sit out the budget.
                return None
            time.sleep(POLL_S)

    def close(self):
        # Terminate rather than wait: this firmware is an infinite loop by design and
        # has no exit path. There is nothing to flush -- the reader thread has already
        # taken every byte QEMU wrote.
        self.proc.kill()
        self.proc.wait()


def show(data):
    """Bytes as something readable in a log, with the line endings still visible."""
    return (data.decode("ascii", "replace")
            .replace("\r", "<CR>").replace("\n", "<LF>\n"))


def build_firmware():
    """Build the QEMU image. `None` on success, otherwise the error text.

    RUSTFLAGS, not CARGO_TARGET_<TRIPLE>_RUSTFLAGS: cargo JOINS the
    target-specific env var with the same key from .cargo/config.toml, which
    hands the linker both memory.x and memory-qemu.x and fails with "region
    'RAM' already defined". RUSTFLAGS replaces them outright. It is not passed
    to host build scripts, because the build is cross-compiling -- which is why
    `firmware/cynthion-soc/build.rs` compiles for the host regardless.
    """
    env = dict(os.environ)
    env["RUSTFLAGS"] = ("-C link-arg=-Tmemory-qemu.x "
                        "-C link-arg=-Tlink.x")
    build = subprocess.run(
        ["cargo", "build", "--release", "--features", "qemu",
         "--target-dir", str(BUILD_DIR)],
        cwd=CRATE, env=env, capture_output=True, text=True)
    if build.returncode != 0:
        return (build.stderr or build.stdout).strip()[-1500:]
    return None


def tree_is_dirty():
    """What both build stamps call dirty.

    Plain `--porcelain`, untracked files included, because that is what
    `ecp5-test/build_helpers.py:usercode()` uses for the ECP5's USERCODE and
    what `firmware/cynthion-soc/build.rs` copies from it. The two definitions
    have to be the same one: `info` compares the words and would otherwise
    report a mismatch that is not one.
    """
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True)
    return bool(status.stdout.strip())


def ask_fresh_qemu(text, needle, seconds):
    """Boot a new image and run one command on it. `None` if it never spoke."""
    session = Session(ELF)
    try:
        if session.expect(b"Cynthion RISC-V SoC", BOOT_S) is None:
            return None
        mark = len(session.snapshot())
        session.send(text.encode() + b"\r")
        session.expect(needle, seconds, mark)
        return session.snapshot()[mark:]
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-build", action="store_true",
                        help="test the existing tmp/qemu-build image")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the whole session transcript")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        failures = []

        def check(name, ok, detail=""):
            emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
            if not ok:
                failures.append(name)
                for line in detail.splitlines():
                    emit(f"        {line}")

        if not args.no_build:
            failed = build_firmware()
            if failed is not None:
                emit("cargo build (qemu) failed:")
                emit(failed)
                return 1
            emit(f"built {ELF.relative_to(ROOT)}: {ELF.stat().st_size} bytes")

        if not ELF.exists():
            emit(f"no QEMU image at {ELF.relative_to(ROOT)}; drop --no-build")
            return 1

        emit(f"qemu: {QEMU} {' '.join(QEMU_ARGS)}")
        emit()

        session = Session(ELF)
        try:
            # --- the firmware speaks at all -------------------------------------
            banner = b"Cynthion RISC-V SoC - Rust firmware"
            at = session.expect(banner, BOOT_S)
            check("banner appears at boot", at is not None,
                  f"expected: {banner!r}\n"
                  f"received in {BOOT_S}s: {show(session.snapshot()) or '(nothing)'}\n"
                  f"qemu stderr: {bytes(session.errors).decode('ascii', 'replace')}")
            if at is None:
                # Nothing else can be meaningful, and every later assertion would
                # produce the same noise. Stop with the one useful message.
                emit()
                emit("firmware never reached the console; later checks skipped")
                return 1

            check("banner names the help commands",
                  session.expect(b"type `help` or `?` for commands",
                                 REPLY_S, at) is not None,
                  "the second banner line did not follow the first")

            # --- the idle re-banner ---------------------------------------------
            # Must come before anything is sent: the first keypress latches `spoken` and
            # the shell never re-announces again. This is the board's reported symptom
            # -- one banner at boot and then silence forever -- so it is worth an
            # assertion even though it costs a couple of seconds.
            again = session.expect(banner, IDLE_S, at + len(banner))
            check("the shell re-announces itself while idle", again is not None,
                  f"no second banner within {IDLE_S}s of the first.\n"
                  "The poll loop stopped turning, or `spoken` latched with nothing\n"
                  "typed. An idle shell that never speaks cannot be told apart from a\n"
                  "dead one.\n"
                  f"received: {show(session.snapshot()[at:]) or '(nothing)'}")

            # --- Enter produces a prompt ----------------------------------------
            mark = len(session.snapshot())
            session.send(b"\r")
            got = session.expect(b"> ", REPLY_S, mark)
            check("<enter> produces a prompt", got is not None,
                  f"expected a '> ' prompt after CR\n"
                  f"received: {show(session.snapshot()[mark:]) or '(nothing)'}")

            # --- help -----------------------------------------------------------
            def command(text, needles, name):
                """Send a line, require every needle in what comes back."""
                mark = len(session.snapshot())
                session.send(text.encode() + b"\r")
                missing = [n for n in needles
                           if session.expect(n, REPLY_S, mark) is None]
                reply = session.snapshot()[mark:]
                check(name, not missing,
                      f"sent: {text!r}\n"
                      f"missing: {missing}\n"
                      f"received in {REPLY_S}s: {show(reply) or '(nothing)'}")
                return reply

            listing = [b"help, ?", b"id", b"read <hex>", b"check", b"info",
                       b"selftest", b"ports", b"irq", b"time",
                       b"log [n]", b"board", b"led", b"i2c", b"power",
                       b"phy", b"typec", b"sideband", b"load <hex>", b"go",
                       b"reset"]
            command("help", listing, "`help` lists every command")
            command("?", listing, "`?` behaves as `help`")

            # --- unknown command --------------------------------------------------
            command("frobnicate", [b"unknown command; try `help`"],
                    "an unknown command says so")

            # --- the board peripherals ---------------------------------------------
            # `virt` has no LEDs, no I2C bus and no one-wire link to a
            # microcontroller, and `target::BOARD` is None on this build. What is
            # being checked here is therefore narrow and worth stating: that the
            # three commands are REGISTERED, that the help text spells them the
            # way the dispatcher does, and that they answer rather than falling
            # through to `unknown command` or faulting on an unmapped address.
            #
            # A stand-in for the hardware, of the kind `target::flash_word` has,
            # would only confirm that a model agrees with the driver. What the
            # drivers do is checked in `scripts/soc_board_sim.py` against the
            # gateware, and on the board.
            for name in ("led", "i2c", "power", "phy", "typec", "sideband"):
                command(name, [b"no board peripherals on this target"],
                        f"`{name}` is registered and reports the target has none")
            command("led green on", [b"no board peripherals on this target"],
                    "`led` with arguments reaches the same handler")
            command("power floor aux 25", [b"no board peripherals on this target"],
                    "`power floor` parses its arguments on a boardless target")
            command("i2c target", [b"no board peripherals on this target"],
                    "`i2c` takes a bus name on a boardless target too")

            # The monotonic clock, and the background poll built on it.
            #
            # `Monitor::poll` reads the `time` CSR every turn of the main loop on
            # BOTH targets and skips only the bus access when `target::BOARD` is
            # None -- so a `time` CSR that trapped would be an illegal
            # instruction in the main loop, and this gate would catch it in
            # seconds rather than a reconfigure finding it in minutes. Every
            # check above is that assertion; this one is the other half of it.
            #
            # Nothing about the power monitor may be printed here. The poll has
            # run several hundred times by now -- 50 ms apart, against a shell
            # that has been answering for seconds -- so a `poll` that reached for
            # the bus on a target that has none would have said so many times
            # over. Silence is the evidence that the board check comes before the
            # bus access and not after it.
            noise = session.snapshot()
            check("the background poll says nothing on a target with no board",
                  b"power:" not in noise,
                  f"the shell printed a power-monitor line under QEMU:\n"
                  f"{show(noise)}")

            # --- check ------------------------------------------------------------
            # The arithmetic lines only. `check` also reports two flash words, and on
            # this target those come from the stand-in in src/target.rs -- virt has
            # no flash, and its UART sits at the address the SoC's flash window uses.
            # Asserting them would be asserting the stub.
            command("check", [b"sum   acf13568 ok", b"prod  369d0368 ok"],
                    "`check` computes 0x12345678*3 == 0x369d0368")

            # The timestamp format, at the seven values `check` formats.
            #
            # The firmware prints what its own `core::fmt` produced and this is
            # what it must be. Each column is a way the format can go wrong:
            #
            #   000000.000  zero pads to the full width, not "0.0"
            #   000000.001  the milliseconds field pads, not "000000.1"
            #   000000.999  the last value before a carry into seconds
            #   000001.000  the carry itself
            #   000061.000  two digits of seconds, still six columns wide
            #   999999.999  the largest the six-digit field can hold
            #   000000.000  one millisecond past it -- WRAPS, and does not widen
            #
            # The last column is the one worth the line. Without the modulo in
            # `log::Stamp`, a machine up for 11.57 days prints a seven-digit
            # seconds field and every line after it is misaligned -- which is a
            # failure nobody will be running a test for when it happens.
            #
            # The expected string lives here rather than in the firmware because
            # comparing in firmware cost a `core::fmt` sink and seven `&str`s to
            # compare against, and this build has 32 KiB for everything. Same
            # reason `sum` and `prod` above are asserted here.
            command("check",
                    [b"stamp 000000.000 000000.001 000000.999 000001.000 "
                     b"000061.000 999999.999 000000.000"],
                    "the timestamp format is right at zero, at a carry, and past "
                    "the six-digit field")
            # --- info -------------------------------------------------------------
            # Shape, not values. The hash, the branch, the timestamp and the
            # compiler are different on every machine and every commit, so what
            # is asserted is that each line is there and that the two fields this
            # script can derive independently agree with it.
            reply = command("info",
                            [b"image ", b"tools ", b"memory ", b"cpu ",
                             b"trap ", b"plic ", b"gateware "],
                            "`info` reports every section")

            check("`info` names the target triple it was built for",
                  b"riscv32imac-unknown-none-elf" in reply,
                  "the tools line must carry the triple, or the report cannot\n"
                  "distinguish this image from one built for another target.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # The dirty flag, against git rather than against itself. A hash
            # with no dirty flag beside it is a claim nobody can check, which is
            # worse than no hash at all.
            expected = b"dirty" if tree_is_dirty() else b"clean"
            image_line = next((line for line in reply.split(b"\r\n")
                               if line.startswith(b"image ")), b"")
            check(f"`info` reports this tree as {expected.decode()}",
                  expected in image_line,
                  f"git says the tree is {expected.decode()}.\n"
                  f"image line: {show(image_line) or '(none)'}")

            check("`info` reports a block RAM budget",
                  b" free of " in reply and b"text " in reply,
                  "the memory line must give the sections and what is left of\n"
                  "the RAM half, which is the number that decides whether the\n"
                  "next command fits.\n"
                  f"received: {show(reply) or '(nothing)'}")

            check("`info` decodes misa to an ISA string",
                  b"misa " in reply and b"rv32" in reply,
                  "misa is what the core says it implements, and the point of\n"
                  "printing it is to compare that against what it was built with.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # `virt` is not a bitstream, so there is no build-id window to read
            # and `target::GATEWARE` is None. Saying so is the assertion: an
            # `info` that invented an identity here would be reporting a
            # peripheral that does not exist.
            check("`info` says this target carries no gateware id",
                  b"gateware none" in reply,
                  "under QEMU the gateware line must report that this target is\n"
                  "not a bitstream, not a hash read from an unmapped address.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # --- selftest -----------------------------------------------------------
            # The CPU items are the real ones here: `virt` runs the same
            # rv32imac image the board does, so the M, C and A paths and the
            # block RAM walk are the instructions and the memory that ship.
            reply = command("selftest",
                            [b"alu", b"muldiv", b"comp", b"atomic", b"ram",
                             b"clock", b"uart", b"gateware", b"flash", b"phy"],
                            "`selftest` runs every item")

            check("no selftest item failed", b"FAIL" not in reply,
                  "at least one item reported FAIL.\n"
                  f"received: {show(reply) or '(nothing)'}")

            check("`selftest` proves the compressed encodings are two bytes",
                  b"in 6 bytes" in reply,
                  "the C item measures the three instructions it ran with a\n"
                  "label difference. Six bytes is the whole assertion: the same\n"
                  "answer from 32-bit encodings would prove nothing about C.\n"
                  f"received: {show(reply) or '(nothing)'}")

            check("`selftest` walks the block RAM address and data lines",
                  b"addresses" in reply and b"32 data lines" in reply,
                  "the ram item must report how many addresses it walked; a\n"
                  "data-line walk alone passes on a shorted address line.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # Skipped, not passed. A target with no board cannot answer for the
            # PHY, and counting that as a pass would make the summary claim more
            # than it knows.
            check("`selftest` skips what this target has not got",
                  b"skip" in reply and b"skipped" in reply,
                  "the gateware and phy items must report `skip` under QEMU, and\n"
                  "the summary must count them separately from the passes.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # --- the board tree -----------------------------------------------
            #
            # `board` is the one hardware command that renders in full on this
            # target, because it reads no bus: every number it prints comes from
            # a cache that is simply empty here. So the formatting under QEMU is
            # the formatting on the board with nothing fed into it, and that is
            # what makes the assertions below evidence about the shipped code
            # rather than about a stub.
            #
            # What is checked is the property the tree exists for, not the
            # cosmetics: that an absent thing renders as absent, that every value
            # is dated, and that CONTROL is not printed as a third copy of the
            # same port.
            reply = command(
                "board",
                [b"+- CONTROL", b"+- AUX", b"+- TARGET",
                 b"rail       control", b"rail       aux",
                 b"rail       target_c", b"rail       target_a"],
                "`board` prints a branch per connector and every rail under one")

            # Absent is not zero. Nothing has ever sampled the monitor here, so
            # every rail must say so -- four rows of `0.000 V  0.000 mA` would be
            # four measurements that were never made, and they would be
            # indistinguishable from a board with every port unplugged.
            check("an unsampled rail renders as absent, not as zero",
                  b"NOT SAMPLED" in reply and b"0.000 V" not in reply
                  and b"0.000 mA" not in reply,
                  "a rail printed a fabricated zero, or failed to say it had no\n"
                  "sample. An unplugged rail on this board measures 0.76-0.92 mA\n"
                  "of ADC offset, so a small number is exactly what absence looks\n"
                  "like -- which is why absence has to be a word and not a value.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # The age field. `NEVER sampled` is what this target's empty cache
            # produces; on the board the same field carries milliseconds. Either
            # way it is present, which is the point: a tree of plausible numbers
            # from a stopped poller is worse than an obviously empty one.
            check("every branch is dated", b"[NEVER sampled]" in reply,
                  "the power header carried no age. A sample with no age cannot\n"
                  "be told apart from a poller that stopped an hour ago.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # CONTROL genuinely differs -- Type-C connector, no FUSB302B, and a
            # PHY that belongs to Apollo. Three identical-looking branches with
            # two values missing would read as a bug in the command.
            check("CONTROL is shown as the port that differs",
                  b"NO fusb302b" in reply and b"APOLLO'S" in reply,
                  "CONTROL was printed like AUX and TARGET. It has no PD\n"
                  "controller and its PHY is Apollo's, and a tree that implies\n"
                  "three identical ports is answering a question about a board\n"
                  "that does not exist.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # The other two absences, each with its own reason rather than a
            # shared shrug: no I2C bus for the controllers, no ULPI window for
            # the PHY.
            check("an absent controller and an absent PHY each say why",
                  b"ABSENT: no i2c bus on this target" in reply
                  and b"ABSENT: no ulpi window on this target" in reply,
                  "a missing part reported nothing, or reported it without a\n"
                  "reason. `--` with no cause is the same screen as a part that\n"
                  "is present and silent.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # And it touched no bus getting there. `board` reads only what the
            # poller and the interrupt path cached, so on a target whose
            # `target::BOARD` is None it cannot have reached for a controller --
            # and the shape of that mistake is the bus error it would print.
            check("`board` reaches no bus", b"no acknowledge" not in reply,
                  "`board` produced an I2C error, which means it issued a\n"
                  "transaction. It must read caches only: it touches every\n"
                  "device on the board, so a live sweep is the single most\n"
                  "likely thing to land inside the PAC1954's REFRESH window.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # --- the deferred interrupt log ----------------------------------------
            #
            # The ring in src/events.rs is what an interrupt handler uses instead
            # of printing, and it is pure logic with no hardware behind it -- so
            # this target exercises the code that ships rather than a stand-in.
            # `log` pushes through the same `events::push` a handler calls.
            #
            # Fill and wrap: the ring holds RING-1 = 15 records (head == tail has
            # to mean empty, so the completely-full state is not representable).
            # Ten fit, drain, ten more fit -- which only works if the indices wrap
            # correctly, since the second ten start past the end of the array.
            reply = command("log 10", [b"log pushed 10 of 10"],
                            "ten records fit in the deferred log")
            check("the main loop drains the log and formats it",
                  b"log test 9" in session.snapshot(),
                  "the records pushed by `log 10` never appeared on the console.\n"
                  "A handler that records and is never drained is a handler that\n"
                  "silently said nothing.\n"
                  f"received: {show(session.snapshot()[-400:])}")

            # THE ASSERTION THIS WHOLE SECTION IS FOR: the timestamp on a
            # deferred line is when the record was PUSHED, not when it was
            # printed.
            #
            # `log n` waits a millisecond between pushes, so the ten records are
            # a millisecond apart. Nothing drains the ring until the command
            # returns -- the main loop is what calls `events::drain` -- so all
            # ten are printed in one burst, in microseconds.
            #
            # Push-time capture therefore spreads the stamps across ~9 ms.
            # Drain-time capture collapses them onto one or two milliseconds,
            # because that is how long the printing takes. There is no tolerance
            # that admits both, which is what makes this worth asserting: the
            # bug it catches is invisible in every other check, and it is exactly
            # wrong for the events the ring exists for -- a Type-C state change,
            # an overrun, a fault.
            stamps = [int(s) * 1000 + int(ms) for s, ms in
                      re.findall(rb"(\d{6})\.(\d{3}) log test \d", reply)]
            spread = stamps[-1] - stamps[0] if len(stamps) >= 2 else 0
            check("a deferred line is stamped when it was pushed, not when it "
                  "was drained",
                  len(stamps) == 10 and spread >= 5
                  and stamps == sorted(stamps),
                  f"the ten `log test` lines were pushed a millisecond apart and\n"
                  f"drained together, so their stamps should span about 9 ms.\n"
                  f"found {len(stamps)} stamps spanning {spread} ms: {stamps}\n"
                  f"A span of 0 or 1 means the stamp is being read in "
                  f"`events::drain`\n"
                  f"rather than stored by `events::push`.")

            command("log 10", [b"log pushed 10 of 10"],
                    "and ten more fit after the ring has wrapped")

            # Drop counting: 20 at once cannot fit, and the ones that do not must
            # be COUNTED rather than silently lost. A queue that quietly discards
            # under exactly the conditions you most want to see is worse than no
            # queue.
            command("log 20", [b"log pushed 15 of 20", b"dropped 5"],
                    "an overfull log drops the excess and counts it")
            check("the drop is reported on the console, not only on request",
                  b"event(s) LOST" in session.snapshot(),
                  "the shell never reported the lost records.\n"
                  "Silently losing log lines is how a fault becomes invisible.\n"
                  f"received: {show(session.snapshot()[-500:])}")

            # --- the console is interrupt-driven ------------------------------------
            # `virt`'s PLIC is at 0x0c000000 and its 16550 is on source 10, both read
            # out of the device tree (see src/target.rs). Assert the firmware found it
            # there, and that the handler has actually run.
            reply = command("irq", [b"plic  @0c000000", b"src 10", b"log  waiting"],
                            "`irq` finds the PLIC and names the console's source")

            # The count itself. Everything typed so far arrived through the handler, so
            # this cannot be small -- but the assertion that matters is `> 0`, because
            # zero means the shell is answering from somewhere other than the interrupt
            # path, which would make every check above it evidence about the wrong code.
            served = 0
            for chunk in reply.split(b"irqs ")[1:]:
                digits = bytes(byte for byte in chunk.split(b" ")[0]
                               if 0x30 <= byte <= 0x39)
                if digits:
                    served = int(digits)
            check("the machine external handler has actually run", served > 0,
                  f"`irq` reported {served} interrupts on console 0.\n"
                  "Zero means bytes are reaching the shell without the handler, so\n"
                  "either something still polls LSR or this is not the firmware that\n"
                  "was built.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # Nothing was lost getting here. `lost` counts LSR reads that found an
            # error bit -- overrun or framing -- and every one of them is input the
            # shell never saw. QEMU's ns16550a sets LSR.OE for real when its FIFO
            # overflows, so this is a live assertion and not a constant: everything
            # typed above went through the same driver, the same handler and the
            # same ring as it does on the board.
            check("no console has lost a byte", b"lost 0" in reply,
                  "`irq` reports a nonzero `lost` count. Bytes reached the UART and "
                  "were destroyed before the CPU read them, which above all means "
                  "the assertions before this one were made against a truncated "
                  "conversation.\n"
                  f"received: {show(reply) or '(nothing)'}")

            check("no interrupt source is left claimed", b"pending 00000000" in reply,
                  "a bit set in `pending` while the shell is idle is a source that "
                  "asserted and was never serviced, or -- worse -- a claim that was "
                  "taken and never completed. The second kind is permanent: that "
                  "console is dead for the rest of the session with nothing else to "
                  "show for it.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # --- the 1 ms tick ------------------------------------------------------
            #
            # `virt` has a real CLINT at 0x02000000 -- `clint@2000000` in the
            # device tree, with `interrupts-extended = <cpu 3>, <cpu 7>` -- so
            # `src/timer.rs` is compiled unchanged for this target and for the
            # board, exactly as `src/plic.rs` and `src/uart.rs` are. What is
            # exercised here is the tick that ships, not a stand-in.
            def tick_state(name):
                """`time`, parsed: (uptime ms, counter ms, ticks).

                The counter arrives as the raw 64-bit `mtime` and its rate, and
                the division into milliseconds happens HERE. On rv32 that divide
                costs 912 bytes of `__udivdi3` -- measured, and more than the
                firmware's remaining room in block RAM -- so the shell prints the
                two numbers and lets a language with free 64-bit arithmetic do
                the rest.
                """
                reply = command("time", [b"uptime", b"clint   @02000000"], name)
                uptime = re.search(rb"uptime\s+(\d{6})\.(\d{3})", reply)
                mtime = re.search(rb"mtime ([0-9a-f]{8}):([0-9a-f]{8}) at (\d+) Hz",
                                  reply)
                ticks = re.search(rb"ticks (\d+)", reply)
                if not (uptime and mtime and ticks):
                    return None, reply
                counter = (int(mtime.group(1), 16) << 32) | int(mtime.group(2), 16)
                hertz = int(mtime.group(3))
                return (int(uptime.group(1)) * 1000 + int(uptime.group(2)),
                        counter * 1000 // hertz, int(ticks.group(1))), reply

            first, reply = tick_state("`time` finds the CLINT where the device "
                                      "tree says it is")
            check("the tick is running", first is not None
                  and b"running" in reply and first[2] > 0,
                  "`time` did not report a running tick with a nonzero count.\n"
                  "A CLINT whose mtimecmp was never programmed, or an mie.MTIE\n"
                  "that was never set, both look exactly like this.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # A known interval, without the test sleeping for it.
            #
            # `log 20` spends 20 ms inside the firmware, waiting on `rdtime`
            # between pushes -- a bounded spin on the free-running counter, which
            # is INDEPENDENT of the tick. So the interval is real, it is measured
            # by something the tick does not drive, and the harness spends no
            # wall-clock budget on it beyond the command's own round trip.
            command("log 20", [b"log pushed 15 of 20"],
                    "an overfull log drops the excess and counts it, again")
            second, reply = tick_state("`time` still answers after the interval")

            # THE DRIFT ASSERTION. Two independent measurements of one interval:
            # `uptime` is accumulated one millisecond at a time by the tick
            # handler, `counter` is read from the 64-bit mtime nothing periodic
            # touches. They must advance together.
            #
            #   tick stopped or masked      -- uptime stands still, counter moves
            #   mtimecmp reloaded from now  -- uptime falls behind by the latency
            #                                  of every period, cumulatively
            #   period computed wrong       -- the two diverge by a fixed ratio
            #
            # None of those is visible from any other check in this file: the
            # shell answers perfectly with a tick that is silently 30% slow.
            if first is None or second is None:
                check("the tick and the free-running counter agree", False,
                      "`time` could not be parsed, so there is nothing to compare.\n"
                      f"received: {show(reply) or '(nothing)'}")
            else:
                by_tick = second[0] - first[0]
                by_counter = second[1] - first[1]
                # A tenth, or 3 ms, whichever is larger. The interval is only a
                # few tens of milliseconds, so a fixed floor is needed for the
                # quantisation at both ends; the ratio is what catches a tick
                # running at the wrong rate over a longer one.
                slack = max(3, by_counter // 10)
                check("the tick and the free-running counter agree",
                      by_counter > 0 and abs(by_tick - by_counter) <= slack,
                      f"over one interval the tick counted {by_tick} ms and the\n"
                      f"free-running counter counted {by_counter} ms, which differ\n"
                      f"by more than {slack} ms.\n"
                      "These are two independent measurements of the same interval.\n"
                      "Disagreement means the tick is not firing at the rate it\n"
                      "claims -- which nothing else here would notice.")

            # What the tick costs, as a fraction of the period it repeats at.
            #
            # This is the first unconditional periodic load in the system, so the
            # question worth asking is whether it can sustain itself: a handler
            # that took longer than its own period would be re-entered on the way
            # out and the CPU would never reach the shell again. Half a period is
            # the bound because anything approaching it is a design problem
            # rather than a measurement.
            #
            # `late` is deliberately NOT asserted on. Under emulation it is
            # dominated by the host's scheduler -- 2563 counter ticks has been
            # seen on an idle machine -- so a bound on it would measure this
            # laptop rather than the firmware. It is reported below, and it is
            # worth watching on the board where it means something.
            cost = re.search(rb"cost\s+worst (\d+) ticks", reply)
            late = re.search(rb"late worst (\d+) ticks", reply)
            # 10 MHz on this machine (`timebase-frequency` in the device tree),
            # so a 1 ms period is 10000 counter ticks.
            check("the tick handler costs well under the period it repeats at",
                  cost is not None and 0 < int(cost.group(1)) < 5000,
                  "`time` reported a worst-case handler duration of half a period "
                  "or\n"
                  "more, or none at all. A handler that cannot finish inside its "
                  "own\n"
                  "period is re-entered on the way out, and the CPU never reaches "
                  "the\n"
                  "shell again.\n"
                  f"received: {show(reply) or '(nothing)'}")
            emit(f"        tick cost {cost.group(1).decode() if cost else '?'} "
                 f"ticks, worst lateness "
                 f"{late.group(1).decode() if late else '?'} ticks, "
                 f"of 10000 in a period")

            # --- backspace --------------------------------------------------------
            # Type a command with one wrong character, rub it out, and require the
            # command to run. This is the assertion that the buffer edit and the screen
            # edit agree: if backspace only erased on screen, `helpX` would reach run()
            # and come back "unknown command".
            mark = len(session.snapshot())
            session.send(b"helpX\x08\r")
            ran = session.expect(b"help, ?", REPLY_S, mark)
            reply = session.snapshot()[mark:]
            check("backspace removes a character from the line buffer",
                  ran is not None and b"unknown command" not in reply,
                  "sent: 'helpX' BS CR\n"
                  f"received: {show(reply) or '(nothing)'}")
            check("backspace erases on screen too",
                  b"\x08 \x08" in reply,
                  "expected the destructive-backspace sequence BS SP BS to be echoed\n"
                  f"received: {show(reply) or '(nothing)'}")

            # Backspace on an empty line must be a no-op, not an underflow. `len` is a
            # usize; without the guard this wraps to 4294967295 and the next character
            # written indexes far outside a 64-byte array.
            mark = len(session.snapshot())
            session.send(b"\x08\x08\x08help\r")
            check("backspace at an empty prompt does not corrupt the shell",
                  session.expect(b"help, ?", REPLY_S, mark) is not None,
                  "the shell stopped responding after backspacing past the start\n"
                  f"received: {show(session.snapshot()[mark:]) or '(nothing)'}")

            # --- line endings -----------------------------------------------------
            # Over the whole session, not per command: one stray writeln! anywhere is
            # enough to wreck the display, and it is cheapest to catch here.
            whole = session.snapshot()
            bare = [i for i, byte in enumerate(whole)
                    if byte == 0x0a and (i == 0 or whole[i - 1] != 0x0d)]
            context = ""
            if bare:
                first = bare[0]
                context = ("first at offset %d, in: %s"
                           % (first, show(whole[max(0, first - 60):first + 4])))
            check("every LF is preceded by CR",
                  not bare,
                  f"{len(bare)} bare LF byte(s) reached the wire.\n"
                  "On a raw CDC-ACM pipe there is no line discipline, so a bare LF\n"
                  "drops a line without returning to column zero and the shell looks\n"
                  "unresponsive. The fix belongs in Console's core::fmt::Write impl.\n"
                  + context)

            check("CRLF is actually present",
                  b"\r\n" in whole,
                  "no CRLF at all -- the console produced nothing line-shaped")

            transcript = session.snapshot()
        finally:
            session.close()

        # --- a modified tree must be reported as modified -----------------------
        #
        # The assertion above compares `info` against whatever state this tree
        # happens to be in. If that state was clean, the dirty path has not been
        # exercised -- and the dirty path is the one that matters: an image built
        # from an edit, reporting the commit it was edited from, is a hash that
        # lies.
        #
        # So modify the tree and rebuild. The modification is an untracked file
        # inside `src/`, which is deliberate on both counts: untracked is what
        # `build_helpers.usercode()` counts as dirty, so the two stamps agree,
        # and `src/` is a rerun trigger in build.rs, so cargo cannot serve a
        # cached "clean" from before it appeared. No tracked file is touched and
        # nothing is checked out, so a concurrent editor in this tree cannot lose
        # work to this test.
        if args.no_build:
            emit("  SKIP  a modified tree is reported as dirty (--no-build)")
        elif tree_is_dirty():
            emit("  (the tree is already modified; the check above covered "
                 "the dirty path)")
        else:
            marker = CRATE / "src" / "soc-test-dirty-marker"
            try:
                marker.write_text("`info` must call this tree dirty.\n")
                failed = build_firmware()
                reply = None if failed else ask_fresh_qemu(
                    "info", b"image ", REPLY_S)
                check("a modified tree is reported as dirty",
                      reply is not None and b"dirty" in reply,
                      f"build: {failed or 'ok'}\n"
                      "an untracked file was added under firmware/cynthion-soc/src\n"
                      "and the image rebuilt; `info` still did not say dirty.\n"
                      f"received: {show(reply or b'') or '(nothing)'}")
            finally:
                marker.unlink(missing_ok=True)
                # Rebuild clean, so the image left behind is the one this tree
                # describes and `soc_run.py` does not configure a board with an
                # image stamped from a state that no longer exists.
                build_firmware()

        emit()
        if args.verbose:
            emit("--- transcript ---")
            emit(show(transcript))
            emit("--- end ---")
            emit()

        if failures:
            emit(f"{len(failures)} FAILED: {', '.join(failures)}")
        else:
            emit("all checks passed")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
