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
  stats             `mcycle` and `minstret` are live, the busy/idle split
                    attributes work to busy, and the 50 ms poll is meeting its
                    interval
  bench             every memory walk terminates, the table is formatted, and a
                    write walk leaves the block RAM pattern intact -- an index
                    that escapes its mask corrupts `.bss` here as on the board

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
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

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

# The same budget on the board, where a reply is three orders of magnitude faster.
#
# 3.0 s is right for QEMU, where a reply is dominated by the host's scheduler. On
# the board every command measured answers well inside 50 ms -- the slowest is
# `bench hyperram` at 47 ms, walking 8 KiB -- so 3 s is ~64x the worst case and
# every FAILING assertion sits out that full budget. The board suite spent 12.5 s
# with six failures and 4.4 s with none; the timeouts were most of the difference.
#
# 0.25 s is ~5x the slowest command. It is a fixed number rather than a measured
# one on purpose: a calibration probe was tried and removed. It could not measure
# the round trip reliably -- a raw `select` probe on the same port gives 0.33 ms
# while the same traffic through the session class gives ~43 ms, a discrepancy
# TCP_NODELAY did not explain and which is still open -- and worse, sending three
# probes at startup made "an untouched shell is mostly idle" fail on every run.
# An instrument that loads its subject reports the load.
BOARD_REPLY_S = 0.25

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


class BoardSession:
    """The same shell, on real silicon, over the console.

    Same three methods as `Session`, so every assertion below runs unchanged
    against the board. That is the whole point: a suite that only ever ran under
    emulation proves the shell's LOGIC and nothing about the SoC -- the console
    peripheral, the flash the code now executes from, the HyperRAM and every I2C
    device are stubbed or absent there.

    ## Two transports, and why the socket is preferred

    If `tio_user.py --serve` is running it owns the tty, and a second reader
    interleaves the stream: each process takes bytes the other never sees, which
    produces output like "ivlive0alive" and looks exactly like a firmware fault.
    So this connects to the service when there is one and only opens the port
    directly when there is not.

    ## The port is found by id, never by number

    `/dev/ttyACM<n>` is assigned in enumeration order and moves when anything else
    is plugged in, so a test naming one tests whichever device happens to have
    that number. `/dev/serial/by-id/` is stable.

    ## What this CANNOT do, and why the QEMU path stays

    It needs a board, and a board running firmware that is already talking. It
    cannot be the pre-commit gate on a machine with nothing attached, and it
    cannot bisect a shell that does not reach its prompt. The two are
    complementary rather than one replacing the other.
    """

    SERVICE_PORT = 9000

    def __init__(self, emit):
        self.proc = None            # nothing to poll; `expect` handles this
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.errors = bytearray()
        self.socket = None
        self.serial = None

        try:
            self.socket = socket.create_connection(
                ("127.0.0.1", self.SERVICE_PORT), timeout=2)
            # TCP_NODELAY: correct for this traffic shape, and NOT the fix for
            # the latency below.
            #
            # One-byte commands and short replies are exactly what Nagle holds
            # back, so disabling it is right on its own terms. It was also my
            # hypothesis for the ~43 ms this session measures against 0.33 ms from
            # a raw probe on the same port -- 40 ms being the delayed-ACK
            # signature. Setting it changed nothing, so that hypothesis is wrong
            # and the cause is still unidentified. Kept because it is correct,
            # not because it helped.
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # 20 ms, not 200: this bounds how long a byte can sit unread when
            # recv is between calls, and it was a fifth of a second.
            self.socket.settimeout(0.02)
            emit(f"console: through the service on {self.SERVICE_PORT}")
        except OSError:
            port = self._find_port()
            if port is None:
                raise RuntimeError(
                    "no console: no service on "
                    f"{self.SERVICE_PORT} and nothing under /dev/serial/by-id/ "
                    "matching the SoC console")
            import serial                       # only needed on this path
            self.serial = serial.Serial(port, 115200, timeout=0.2)
            emit(f"console: {port}")

        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    @staticmethod
    def _find_port():
        """The SoC console, by id. Never by ttyACM number -- see the docstring."""
        by_id = Path("/dev/serial/by-id")
        if not by_id.is_dir():
            return None
        # The SoC's own CDC, not Apollo's. Both are 1d50 devices on this board.
        for entry in sorted(by_id.iterdir()):
            name = entry.name.lower()
            if "cynthion" in name or "1d50" in name or "vexii" in name:
                return str(entry)
        return None

    def _drain(self):
        while True:
            try:
                if self.socket is not None:
                    chunk = self.socket.recv(4096)
                    if not chunk:
                        return
                else:
                    chunk = self.serial.read(256)
            except (TimeoutError, OSError):
                continue
            if chunk:
                with self.lock:
                    self.buf.extend(chunk)

    def snapshot(self):
        with self.lock:
            return bytes(self.buf)

    def send(self, data):
        if self.socket is not None:
            self.socket.sendall(data)
        else:
            self.serial.write(data)
            self.serial.flush()

    def expect(self, needle, budget, since=0):
        # 5 ms. NOT 1 ms, and the reason is a regression this caused.
        #
        # A 1 ms poll made "an untouched shell is mostly idle" fail on every run:
        # the harness was busy enough that the thing it was measuring stopped
        # being untouched. A measuring instrument that loads its subject reports
        # the load.
        #
        # NOT 10 ms either, which is what it was. At 10 ms the poll is a floor on
        # anything this can report, and calibration read a rock-steady "50.4 ms"
        # across three runs -- so stable, and so close to the firmware's 50 ms
        # power poll, that it read as a real mechanism and very nearly justified
        # halving that poll to fix a latency the firmware does not have.
        deadline = time.monotonic() + budget
        while True:
            found = self.snapshot().find(needle, since)
            if found >= 0:
                return found
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)

    def close(self):
        if self.socket is not None:
            self.socket.close()
        if self.serial is not None:
            self.serial.close()


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


def ask_fresh_qemu(text, needle, seconds, settle=None, settle_s=0):
    """Boot a new image and run one command on it. `None` if it never spoke.

    `settle` is something to wait for BEFORE asking. One caller needs it: a
    measurement of an idle machine taken 40 ms after boot is mostly a
    measurement of the command that asked, so `stats` waits for the idle
    re-banner first and gets a couple of seconds of doing nothing to average
    over.
    """
    session = Session(ELF)
    try:
        first = session.expect(b"Cynthion RISC-V SoC", BOOT_S)
        if first is None:
            return None
        if settle is not None:
            session.expect(settle, settle_s, first + 1)
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
    parser.add_argument("--board", action="store_true",
                        help="run against the BOARD over its console instead of "
                             "QEMU. Needs a configured board already running this "
                             "firmware; builds nothing and loads nothing")
    args = parser.parse_args()

    failures = []

    def check(name, ok, detail=""):
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)
            for line in detail.splitlines():
                emit(f"        {line}")

    if args.board:
        # Nothing is built and nothing is loaded: this drives whatever the
        # board is already running. Use `./dev.py fw` to put firmware there
        # first -- keeping the two separate is what makes a failure here mean
        # "this firmware misbehaves on real hardware" rather than "something
        # in a combined build-and-test step went wrong".
        emit("target: the board, over its console")
    elif not args.no_build:
        failed = build_firmware()
        if failed is not None:
            emit("cargo build (qemu) failed:")
            emit(failed)
            return 1
        emit(f"built {ELF.relative_to(ROOT)}: {ELF.stat().st_size} bytes")

    if not args.board and not ELF.exists():
        emit(f"no QEMU image at {ELF.relative_to(ROOT)}; drop --no-build")
        return 1

    if not args.board:
        emit(f"qemu: {QEMU} {' '.join(QEMU_ARGS)}")
    emit()

    # Which target's answers are correct differs for a handful of checks:
    # `target::BOARD` is None under QEMU, so the shell reports "this target
    # has no ..." for every device. On the board the same commands reach real
    # silicon and the expectations invert. Everything else -- the line editor,
    # the log ring, the arithmetic, the formatting -- is identical, which is
    # why one suite can serve both.
    board = args.board

    if board:
        try:
            session = BoardSession(emit)
        except RuntimeError as error:
            emit(f"cannot reach the board: {error}")
            return 1
    else:
        session = Session(ELF)
    try:
        banner = b"Cynthion RISC-V SoC - Rust firmware"

        # --- the firmware speaks at all -------------------------------------
        #
        # On the board there is no boot to wait for: it has been running since
        # `configure`, and the banner is long past. Worse, the shell latches
        # `spoken` on the first keypress and never re-announces, so a console
        # anyone has typed at will never show one again -- waiting for it would
        # hang for BOOT_S and then report a dead firmware that is fine.
        #
        # So the board syncs on the PROMPT instead: send Enter, expect `>`.
        # That proves the same thing the banner proves under QEMU -- the shell
        # is reading its input and writing its output -- without assuming a
        # boot happened just now.
        if board:
            session.send(b"\r")
            at = session.expect(b">", BOOT_S)
            # A shorter budget for the board, and `global` because every check
            # below reads REPLY_S at call time.
            if at is not None:
                global REPLY_S
                REPLY_S = BOARD_REPLY_S
            check("the board's shell answers at the prompt", at is not None,
                  "sent Enter and no prompt came back.\n"
                  "Either nothing is configured, the console is being read by\n"
                  "another process, or the firmware is not running.\n"
                  f"received in {BOOT_S}s: "
                  f"{show(session.snapshot()) or '(nothing)'}")
        else:
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

        # Both banner assertions need a boot that just happened, and the
        # second needs a console nobody has typed at. Neither holds for a board
        # that has been up since `configure`; asserting them there would be
        # testing how recently someone pressed a key.
        if not board:
            check("banner names the help commands",
                  session.expect(b"type `help` or `?` for commands",
                                 REPLY_S, at) is not None,
                  "the second banner line did not follow the first")

            # --- the idle re-banner --------------------------------------
            # Must come before anything is sent: the first keypress latches
            # `spoken` and the shell never re-announces again. This is the
            # board's reported symptom -- one banner at boot and then silence
            # forever -- so it is worth an assertion even though it costs a
            # couple of seconds.
            again = session.expect(banner, IDLE_S, at + len(banner))
            check("the shell re-announces itself while idle", again is not None,
                  f"no second banner within {IDLE_S}s of the first.\n"
                  "The poll loop stopped turning, or `spoken` latched with "
                  "nothing\n"
                  "typed. An idle shell that never speaks cannot be told apart "
                  "from a\n"
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

        # Spelled as a user would type them, because that is what the listing
        # is for: `flash id` and `bram read <hex>` are one command each, not a
        # region column beside a verb column.
        listing = [b"help, ?", b"flash id", b"flash read <hex>",
                   b"bram read <hex>", b"hyperram read <hex>", b"check",
                   b"info", b"selftest", b"ports", b"irq", b"time", b"cpu stats",
                   b"bench [region]", b"log [n|tags]", b"board", b"led",
                   b"i2c", b"power", b"phy", b"typec", b"sideband",
                   b"load <hex>", b"reset"]
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
        #
        # ON THE BOARD every one of these reaches real silicon, so the
        # expectation inverts: the answer must NOT be "no board peripherals",
        # and must not be an I2C error either. That is a weaker assertion than
        # naming the expected voltages -- deliberately, because this suite runs
        # against whatever is plugged in and a target with no cable attached is
        # a legitimate state. What it catches is the case worth catching: a
        # command that reaches for a device and gets nothing back.
        absent = b"no board peripherals on this target"
        for name in ("led", "i2c", "power", "phy", "typec", "sideband"):
            if board:
                mark = len(session.snapshot())
                session.send(name.encode() + b"\r")
                session.expect(b"> ", REPLY_S, mark)
                reply = session.snapshot()[mark:]
                check(f"`{name}` reaches a real device",
                      absent not in reply and b"no acknowledge" not in reply
                      and len(reply.strip()) > 0,
                      f"`{name}` either reported no peripherals on a board that\n"
                      f"has them, or got no acknowledge from the bus.\n"
                      f"received: {show(reply) or '(nothing)'}")
            else:
                command(name, [absent],
                        f"`{name}` is registered and reports the target has none")

        if not board:
            command("led green on", [absent],
                    "`led` with arguments reaches the same handler")
            command("power floor aux 25", [absent],
                    "`power floor` parses its arguments on a boardless target")
            command("i2c target", [absent],
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
        # The assertion is that the board check comes BEFORE the bus access,
        # so on a target with no board the poll stays silent. On the board it
        # correctly has something to say -- that is the same code reaching the
        # opposite, correct conclusion, not a different behaviour to assert.
        if not board:
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
                        [b"image ", b"tools ", b"memory ", b"boot ", b"cpu ",
                         b"trap ", b"plic ", b"gateware "],
                        "`info` reports every section")

        # The bootloader's breadcrumb, and what this target has to say about
        # it.
        #
        # `-M virt` jumps straight to this image's entry point: there is no
        # bootloader under it and 0x3fc is not memory. Saying so is the check
        # -- the failure being guarded against is `info` reading that address
        # anyway and rendering whatever it found as a boot status, which
        # would be a confident answer about a thing that never happened.
        check("`info` reports the bootloader's verdict" if board
              else "`info` says this target has no bootloader under it",
              (b"nothing staged" in reply or b"staged image" in reply
               or b"no mark" in reply) if board
              else b"no bootloader on this target" in reply,
              "target::BOOT_STATUS is None on the QEMU build, and `info`\n"
              "must report that rather than decode an address that is not\n"
              "memory here.\n"
              f"received: {show(reply) or '(nothing)'}")

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
        check("`info` reports the gateware's identity" if board
              else "`info` says this target carries no gateware id",
              (b"gateware " in reply and b"gateware none" not in reply)
              if board else b"gateware none" in reply,
              "on the board the gateware line must carry a real identity read\n"
              "from the bitstream's own CSR; under QEMU it must report that\n"
              "this target is not a bitstream, rather than a hash read from an\n"
              "unmapped address.\n"
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
        check("`selftest` runs the items this target HAS" if board
              else "`selftest` skips what this target has not got",
              (b"fail" not in reply and b"gateware" in reply) if board
              else b"skip" in reply and b"skipped" in reply,
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
        check("every rail carries a real measurement" if board
              else "an unsampled rail renders as absent, not as zero",
              (b"NOT SAMPLED" not in reply and b" V" in reply) if board
              else b"NOT SAMPLED" in reply and b"0.000 V" not in reply
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
        check("every branch is dated",
              (b"[NEVER sampled]" not in reply and b"sampled" in reply)
              if board else b"[NEVER sampled]" in reply,
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
        check("the controllers and the PHYs are present" if board
              else "an absent controller and an absent PHY each say why",
              (b"ABSENT: no i2c bus on this target" not in reply
               and b"ABSENT: no ulpi window on this target" not in reply)
              if board
              else b"ABSENT: no i2c bus on this target" in reply
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
        # The drained records are NOT part of the reply to `log 10`.
        #
        # `log n` pushes into the ring and answers "log pushed 10 of 10"; the
        # main loop drains it afterwards. So `command` returns as soon as that
        # answer appears, with none of the ten lines necessarily received yet,
        # and both checks below were reading a buffer that was still filling.
        # About one run in six they read it too early -- reported as "the
        # records never appeared" and "found 4 stamps", neither of which was
        # true a millisecond later.
        #
        # Waiting for the LAST record is what makes the read deterministic:
        # `log test 9` cannot arrive before the nine before it.
        mark = len(session.snapshot())
        command("log 10", [b"log pushed 10 of 10"],
                "ten records fit in the deferred log")
        drained = session.expect(b"log test 9", REPLY_S, mark)
        check("the main loop drains the log and formats it",
              drained is not None,
              "the records pushed by `log 10` never appeared on the console.\n"
              "A handler that records and is never drained is a handler that\n"
              "silently said nothing.\n"
              f"received: {show(session.snapshot()[mark:])}")
        # Re-read AFTER the wait. The `reply` command() returned was captured
        # at the moment the "pushed" needle matched, which is the stale one.
        reply = session.snapshot()[mark:]

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
        # WAIT for the LOST line rather than snapshotting for it.
        #
        # It is not part of the reply to `log 20`. The shell notices the loss
        # and reports it from the main loop, so it arrives some time after the
        # `dropped 5` that `command` waited on. Reading the buffer the instant
        # that needle appeared was a race the emulator lost about one run in
        # three -- and it read as a firmware fault ("the shell never reported
        # the lost records") when the shell reported them perfectly, a
        # millisecond later.
        mark = len(session.snapshot())
        if board:
            # EXACT COUNTS ARE AN EMULATOR PROPERTY HERE, not a firmware one.
            #
            # Under QEMU nothing drains while `log 20` runs, so exactly 15 fit
            # and exactly 5 are dropped. On the board the main loop is really
            # concurrent with the pushes and drains some of the ring on the
            # way, so the split moves between runs -- it was 15/5 on one run
            # and passed, then failed on the next with a different, equally
            # correct split.
            #
            # So assert the INVARIANT, which is what the drop counter is for:
            # every record is either pushed or counted as lost, and none
            # silently vanishes.
            session.send(b"log 20\r")
            session.expect(b"> ", REPLY_S, mark)
            reply = session.snapshot()[mark:]
            pushed = re.search(rb"log pushed (\d+) of (\d+)", reply)
            dropped = re.search(rb"dropped (\d+)", reply)
            # `dropped` is a CUMULATIVE counter, not this command's share.
            #
            # The QEMU test asserts `dropped 5` and is right to: a fresh boot
            # has dropped nothing before, so the running total IS this
            # command's. On a board that has been up for a while it read 80,
            # and an assertion that added it to `pushed` and expected 20 was
            # arithmetic on two different quantities.
            #
            # What IS deterministic on both is the ring's capacity: 15 records
            # fit, because head == tail has to mean empty so the completely
            # full state is not representable. And the excess must be counted
            # rather than silently lost, which is what the counter existing and
            # being non-zero says.
            check("an overfull log drops the excess and counts it",
                  pushed is not None and pushed.group(1) == b"15"
                  and pushed.group(2) == b"20"
                  and dropped is not None and int(dropped.group(1)) > 0,
                  "the ring holds 15, so `log 20` must report 15 pushed and a\n"
                  "non-zero cumulative drop count.\n"
                  f"received: {show(reply) or '(nothing)'}")
        else:
            command("log 20", [b"log pushed 15 of 20", b"dropped 5"],
                    "an overfull log drops the excess and counts it")
        check("the drop is reported on the console, not only on request",
              session.expect(b"event(s) LOST", REPLY_S, mark) is not None,
              "the shell never reported the lost records.\n"
              "Silently losing log lines is how a fault becomes invisible.\n"
              f"received: {show(session.snapshot()[mark:])}")

        # The lost records took their timestamps with them, so the column
        # jumps. The report has to say where the jump starts, or a reader
        # is left to spot a silence -- which is the failure mode the drop
        # counter exists to prevent, one level up.
        # Same race, same fix: wait for the dated form before matching it.
        # The check above only established that a LOST line exists.
        check("the loss report dates the gap it left in the column",
              session.expect(b"event(s) LOST from ", REPLY_S, mark) is not None
              and re.search(rb"event\(s\) LOST from \d{6}\.\d{3}",
                            session.snapshot()) is not None,
              "the LOST line carried no time for the first record lost.\n"
              "Without it the gap in the timestamp column is a silence\n"
              "rather than a fact.\n"
              f"received: {show(session.snapshot()[-500:])}")

        # --- the payload tags ---------------------------------------------------
        #
        # A record is a 32-bit code and a 64-bit value, never a string, and
        # the code's top byte says how to read the value (#124). The decoder
        # for that byte is the generic arm of `events::report` -- the one
        # every code without a hand-written sentence lands on -- so `log
        # tags` pushes one sample per tag straight at it.
        #
        # Waiting on the ASCII sample rather than only on the command's own
        # reply: it is the LAST of the nine pushed, and the reply is printed
        # before the main loop drains any of them, so its arrival is what
        # says all nine were formatted.
        reply = command("log tags",
                        [b"log pushed 10 tag samples", b"declared 16 bits"],
                        "one record per payload tag fits and is drained")

        # Each tag, named individually so a failure says which one. The code
        # is `TT.SS.NNNN` -- tag, subsystem, number -- with the sample's
        # number chosen as 0x10 + tag, so every line names its own tag twice
        # and a decoder that read the wrong byte cannot pass by accident.
        tags = [
            ("none, the code standing alone", b"code 00.00.0010 (no payload)"),
            ("u8", b"code 01.00.0011 u8 a5"),
            ("u16", b"code 02.00.0012 u16 beef"),
            ("u32", b"code 03.00.0013 u32 deadbeef"),
            ("u64", b"code 04.00.0014 u64 0123456789abcdef"),
            ("f32, as bits", b"code 05.00.0015 f32 bits 3f800000"),
            ("f64, as bits", b"code 08.00.0018 f64 bits 3ff0000000000000"),
            ("8 x u8", b"code 10.00.0020 bytes 00 11 22 33 44 55 66 77"),
            # Eight characters, in order, as characters. The whole reason
            # tag 17 exists is that a short identifier should not need an
            # allocator, a length field or a truncation rule -- and it is
            # worthless if it comes out as hex.
            ("8 ASCII characters", b'code 11.00.0021 "cynthion"'),
            # The tag is a promise. A value that does not fit the one it was
            # given is printed whole and marked, not truncated -- a silently
            # narrowed payload is a wrong number that looks right, which is
            # the worst thing a log line can be. This row is the only reason
            # that guard is known to work.
            ("a value that does not fit its tag",
             b"code 02.00.0030 u16 2345 "
             b"!! declared 16 bits, value is 0000000000012345"),
        ]
        for what, needle in tags:
            check(f"payload tag round-trips: {what}", needle in reply,
                  f"the ring did not carry this tag through push and drain.\n"
                  f"expected: {needle!r}\n"
                  f"received: {show(reply) or '(nothing)'}")

        # The float tags are RESERVED, not implemented, and the line has to
        # say so. This CPU is rv32imac: rendering an f32 pulls
        # compiler-builtins soft-float into an image that has 32 KiB of block
        # RAM, which is why `src/power.rs` uses integer rationals. An
        # unimplemented arm that looks like an oversight is the trap this
        # asserts against.
        check("a float payload says why it is bits and not a number",
              reply.count(b"(bits only: rv32imac has no F extension)") == 2,
              "the f32/f64 lines printed bits with no reason given.\n"
              "The next person implements the formatter and the image grows\n"
              "by a page, with nothing recording that it was a decision.\n"
              f"received: {show(reply) or '(nothing)'}")

        # Same rule as every other event line: stamped when PUSHED. These
        # ten are pushed in a burst with no waiting between them, so they
        # share a millisecond or two -- what is asserted is that the column
        # is there at all, not its spread.
        stamped = len(re.findall(rb"\d{6}\.\d{3} irq log: code \d\d\.", reply))
        check("every tag sample is stamped like any other event",
              stamped == 10,
              f"{stamped} of 10 tag lines carried a timestamp. A deferred\n"
              f"line with no stamp cannot be correlated with anything, which\n"
              f"is the only reason to defer it rather than drop it.\n"
              f"received: {show(reply) or '(nothing)'}")

        # --- the console is interrupt-driven ------------------------------------
        # `virt`'s PLIC is at 0x0c000000 and its 16550 is on source 10, both read
        # out of the device tree (see src/target.rs). Assert the firmware found it
        # there, and that the handler has actually run.
        # The addresses are the TARGET's, so only the shape is common: on
        # `virt` the PLIC is at 0x0c000000 with the 16550 on source 10, both
        # from the device tree; the SoC puts its own at 0xf0400000. Asserting
        # QEMU's numbers against the board would be asserting that the board is
        # QEMU.
        reply = command("irq",
                        [b"plic  @f0400000", b"log  waiting"] if board
                        else [b"plic  @0c000000", b"src 10", b"log  waiting"],
                        "`irq` finds the PLIC and names the console's source")

        # The count itself. Everything typed so far arrived through the handler, so
        # this cannot be small -- but the assertion that matters is `> 0`, because
        # zero means the shell is answering from somewhere other than the interrupt
        # path, which would make every check above it evidence about the wrong code.
        # The BUSIEST console, not the last one.
        #
        # This took the last `irqs N` it found. Under QEMU there is one console
        # so the two are the same; the board has two, the second has seen
        # nothing, and the assertion read 0 on a target that had served 128 --
        # reported as "bytes are reaching the shell without the handler", which
        # would have been alarming and was entirely the harness.
        served = 0
        for chunk in reply.split(b"irqs ")[1:]:
            digits = bytes(byte for byte in chunk.split(b" ")[0]
                           if 0x30 <= byte <= 0x39)
            if digits:
                served = max(served, int(digits))
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
            # The CLINT address is the TARGET's: `virt` puts it at 0x02000000
            # per its device tree, the SoC at 0xf0800000. Asserting one against
            # the other asserts that the board is QEMU.
            reply = command("time",
                            [b"uptime", b"clint   @f0800000"] if board
                            else [b"uptime", b"clint   @02000000"], name)
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
        # NEITHER worst case is asserted on, and the reason is the same for
        # both -- which is the correction here, because `cost` used to be
        # bounded while `late` was not.
        #
        # Under emulation both are dominated by the host's scheduler. A guest
        # preempted mid-handler resumes with the guest counter having advanced
        # by however long the host was elsewhere, and that lands in `cost`
        # exactly as it lands in `late`; 2563 counter ticks of lateness has
        # been seen on an idle machine. A bound on a WORST case measures this
        # laptop, not the firmware.
        #
        # It failed intermittently for exactly that reason -- passing twice
        # and failing twice in four consecutive runs of an unchanged tree --
        # and an intermittent gate is worse than no gate: it blocks work at
        # random and teaches everyone to re-run until green, which is how a
        # real failure gets waved through.
        #
        # What IS asserted is that the measurement exists and is running: a
        # non-zero cost means the handler was entered and timed. The
        # sustainability question -- can the handler finish inside its own
        # period -- is a question about the board, where the counter is not
        # someone else's scheduler, and `stats` on the console is where it
        # gets answered.
        cost = re.search(rb"cost\s+worst (\d+) ticks", reply)
        late = re.search(rb"late worst (\d+) ticks", reply)
        check("the tick handler reports a duration, so it is being timed",
              cost is not None and int(cost.group(1)) > 0,
              "`time` reported no worst-case handler duration, or zero.\n"
              "Zero means the handler never ran or the timing is not wired "
              "up;\n"
              "either way every other number on this line is meaningless.\n"
              f"received: {show(reply) or '(nothing)'}")
        emit(f"        tick cost {cost.group(1).decode() if cost else '?'} "
             f"ticks, worst lateness "
             f"{late.group(1).decode() if late else '?'} ticks, "
             f"of 10000 in a period")

        # --- where the cycles go ------------------------------------------------
        #
        # `mcycle` and `minstret` are decoded by the SoC's generated core
        # (`--with-rdtime` adds `zicntr`, which instantiates the plugin --
        # see src/metrics.rs) and by `virt`. An UNDECODED CSR read traps, so
        # a core without them is not a zero here, it is an illegal
        # instruction in the main loop -- which is why the liveness check
        # below is the one that matters and why it runs on this target at
        # all rather than waiting for a bitstream.
        def stats_state(name):
            """`stats`, parsed: (window, busy basis points, ipc per 1000,
                turns, mean, worst, polls, worst gap ms)."""
            reply = command("cpu stats", [b"cycles   window", b"loop     turns",
                                      b"poll     every"], name)
            cycles = re.search(rb"window (\d+)\s+busy (\d+)\.(\d\d)%"
                               rb"\s+ipc (\d+)\.(\d\d\d)", reply)
            loop = re.search(rb"turns (\d+)\s+mean (\d+) cycles"
                             rb"\s+worst (\d+) cycles", reply)
            poll = re.search(rb"every (\d+) ms\s+polls (\d+)"
                             rb"\s+worst gap (\d+) ms", reply)
            if not (cycles and loop and poll):
                return None, reply
            return (int(cycles.group(1)),
                    int(cycles.group(2)) * 100 + int(cycles.group(3)),
                    int(cycles.group(4)) * 1000 + int(cycles.group(5)),
                    int(loop.group(1)), int(loop.group(2)),
                    int(loop.group(3)),
                    int(poll.group(2)), int(poll.group(3))), reply

        # Twice, either side of a known amount of work, because the
        # interesting number is the DIFFERENCE. The first reading is an
        # idle shell -- the harness has been waiting on replies, so almost
        # every turn found nothing -- and it is the figure issue #129 is
        # after: how much of the machine is spare.
        idle, reply = stats_state("`stats` reports cycles, loop and poll")

        # 20 ms of spinning inside the firmware, in ONE turn of the main
        # loop, reached through `irq::pop` -- so it is charged busy by
        # construction and it is large enough to show against an otherwise
        # idle shell. The same command the tick's drift assertion uses, for
        # the same reason: the interval is real and the harness spends no
        # wall-clock budget on it.
        command("log 20", [b"log pushed 15 of 20"],
                "an overfull log drops the excess and counts it, once more")
        stats, reply = stats_state("`stats` answers again after a busy turn")

        if idle is not None:
            emit(f"        idle shell: busy {idle[1] / 100:.2f}% of "
                 f"{idle[0]} cycles, ipc {idle[2] / 1000:.3f}")

        if stats is None or idle is None:
            check("the cycle and instruction counters are live", False,
                  "`stats` could not be parsed, so there is nothing to "
                  "check.\n"
                  f"received: {show(reply) or '(nothing)'}")
        else:
            (window, busy, ipc, turns, mean, worst, polls, gap) = stats

            # `--` in any of the three fields is a denominator too small to
            # scale by, which after a whole session of commands can only
            # mean a counter that is not advancing.
            check("the cycle and instruction counters are live",
                  window > 0 and ipc > 0 and mean > 0 and turns > 0,
                  f"window {window} cycles, ipc {ipc}/1000, mean {mean} "
                  f"cycles over {turns} turns.\n"
                  "A zero in any of these is `mcycle` or `minstret` "
                  "standing still, which\n"
                  "makes every figure this command prints a ratio of "
                  "nothing.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # The whole point of the command: that work MOVES the number.
            # `log 20` spent 20 ms in one busy turn between the two
            # readings, so the fraction must have risen. A comparison
            # rather than `> 0` because it survives the window halving --
            # halving preserves the ratio exactly -- and because a split
            # that charged everything to busy would also pass `> 0`.
            check("work moves the busy fraction", busy > idle[1],
                  f"busy was {idle[1] / 100:.2f}% and is "
                  f"{busy / 100:.2f}% after a command that spends 20 ms\n"
                  "inside the firmware in a single turn. Nothing is "
                  "calling metrics::busy,\n"
                  "or the flag is being consumed before the turn it "
                  "belongs to closes.\n"
                  f"received: {show(reply) or '(nothing)'}")

            # A fraction that exceeds its whole is a scaling bug, and it is
            # the one failure mode of `parts()` that would still print
            # something plausible-looking.
            check("the busy fraction is a fraction", busy <= 10_000,
                  f"`stats` reported {busy / 100:.2f}% busy, which is more "
                  "than all of the cycles.")

            # A maximum below the mean it is drawn from cannot happen, so
            # this catches the two being taken over different windows --
            # which they are: `worst` is since boot and `mean` is windowed.
            check("the worst turn bounds the mean turn", worst >= mean,
                  f"worst turn {worst} cycles is below the mean of {mean}.")

            # The poll's own interval, measured by the poll rather than
            # assumed from the constant. It CANNOT be below the nominal --
            # the poll only runs once the interval has elapsed -- so a
            # smaller number means the gap is being measured against the
            # wrong instant. It is not bounded above here: under TCG the
            # gap is the host scheduler's, exactly as `late` is, and a
            # bound would measure this machine. The value is reported.
            check("the power poll is meeting its 50 ms interval",
                  polls > 0 and gap >= 50,
                  f"{polls} poll(s), worst gap {gap} ms against a nominal "
                  "50 ms.\n"
                  "Zero polls means the interval check never passed; a gap "
                  "below the\n"
                  "interval means it is being timed from the wrong "
                  "instant.\n"
                  f"received: {show(reply) or '(nothing)'}")

            emit(f"        after 20 ms of work: busy {busy / 100:.2f}% of "
                 f"{window} cycles, ipc {ipc / 1000:.3f}, turn mean {mean} "
                 f"worst {worst} cycles, {polls} polls worst gap {gap} ms")

        # The headline number, on a shell nobody has touched.
        #
        # The readings above are taken after a session of commands, three
        # of which spend 20 ms spinning, so they measure the test. A fresh
        # machine measures the thing issue #129 asks about: how much of the
        # CPU an idle shell leaves spare, and therefore whether an RTOS
        # would fit. It waits for the idle re-banner before asking, so the
        # window averaged over is a couple of seconds of doing nothing
        # rather than the 40 ms between boot and the question -- over which
        # the command itself would be most of the answer.
        # QEMU ONLY, for a reason in the name: it needs a shell nobody has
        # typed at, and it gets one by booting a second emulator. The board
        # under test has been up since `configure` and has been answering
        # commands for the whole suite, so there is no untouched shell to ask
        # -- reconfiguring to manufacture one would make this the only check
        # that resets the target it is measuring.
        #
        # `3.0` is passed explicitly rather than `REPLY_S`, which is rebound to
        # the board's 0.25 s in board mode. That rebinding reached this nested
        # QEMU session and gave it a quarter second to boot an emulator, so it
        # returned nothing and reported "a freshly booted shell reported no
        # busy figure" -- a board-mode assertion failing on a QEMU timeout.
        fresh = None if board else ask_fresh_qemu(
            "cpu stats", b"poll     every", 3.0,
            settle=b"Cynthion RISC-V SoC", settle_s=IDLE_S)
        resting = re.search(rb"busy (\d+)\.(\d\d)%", fresh or b"")
        resting = (int(resting.group(1)) * 100 + int(resting.group(2))
                   if resting else None)
        if not board:
            check("an untouched shell is mostly idle",
                  resting is not None and resting < 5_000,
                  "a freshly booted shell that nobody has typed at reported "
                  f"{'no busy figure' if resting is None else f'{resting / 100:.2f}% busy'}.\n"
                  "Over half the machine spent on a loop that is only asking "
                  "means the split\n"
                  "is charging idle turns to busy, and the figure cannot be "
                  "used to size an RTOS.\n"
                  f"received: {show(fresh or b'') or '(nothing)'}")
        if resting is not None:
            emit(f"        untouched shell: busy {resting / 100:.2f}%")

        # --- bench ------------------------------------------------------------
        # What this target can and cannot say about `bench`.
        #
        # It CAN say that every walk terminates, that the arithmetic never
        # divides by zero, that the table is formatted, and -- the one that
        # matters -- that the block RAM pattern survives a write walk. That
        # last is the check for an index that escapes its mask: a walk that
        # ran off the end of the buffer would corrupt `.bss` here exactly as
        # it would on the board.
        #
        # It CANNOT say anything about speed. `virt` has no D-cache to prove
        # live and `flash_word` is a two-value stand-in rather than an SPI
        # part. Timing evidence is the board's.
        command("bench bram",
                [b"region", b"cycles/acc", b"bram", b"read seq",
                 b"read rnd", b"write seq", b"write rnd",
                 b"0 words wrong"],
                "`bench bram` walks block RAM and the pattern survives it")
        command("bench flash", [b"flash", b"read seq", b"read rnd", b"ok"],
                "`bench flash` reads the window and checks known content")

        # The HyperRAM answer here is the REFUSAL, and asserting it is worth
        # more than asserting a walk would be. The QEMU backend is a `.bss`
        # array sized for a staged image, about 32k words; `bench` walks at
        # word 0x10000, deliberately above anything staging uses, so on this
        # target the port does not answer. What that exercises is the guard:
        # every spin in `hyperram::read_word` is bounded at 100_000 rather
        # than unbounded, so a dead port makes the walks take about a minute
        # instead of failing -- which reads as a hung shell. The probe is
        # what turns that into a sentence, and this is the check that it
        # short-circuits rather than grinding.
        # ON THE BOARD THE PORT ANSWERS, so this inverts into the assertion
        # worth having on real silicon: the walk completes and the pattern
        # survives it. Measured 0.79-1.03 MB/s with 0 words wrong over 8 KiB.
        if board:
            command("bench hyperram", [b"words wrong"],
                    "`bench hyperram` walks the part and checks the pattern")
        else:
            command("bench hyperram", [b"hyperram did not answer"],
                    "`bench hyperram` refuses a port that does not answer "
                    "instead of spinning on it")
        command("bench frobnicate", [b"usage: bench"],
                "`bench` with an unknown region says how to call it")

        # --- one word out of one memory ---------------------------------------
        # `flash id`, and `read <hex>` on each of the three regions (#161).
        #
        # What this target can say is the SHAPE, and that is most of what the
        # change was: that the same verb reaches all three regions, that each
        # answer names the region it came from, that an offset is bounded per
        # region rather than against one hardcoded 4 MiB, and that the two
        # memories with nothing to identify say so instead of printing an
        # empty line.
        #
        # It cannot say what the flash holds -- `target::flash_word` is a
        # two-value stand-in here, the same one `check` uses -- so the two
        # offsets asserted below are exactly the two that stand-in knows. On
        # the board they are the bitstream header and a word 0x40 into it, and
        # the assertion is then about real silicon without changing.
        command("flash id", [b"flash @0 615000ff", b"4096 KiB"],
                "`flash id` reports the first word and the size of the window")
        command("flash read 40", [b"flash @000040 2a558800"],
                "`flash read` names the region and the offset it read")
        # Aligned DOWN to the containing word rather than refused: 0x42 is
        # inside the word at 0x40, and the reply says 000040 so which word was
        # read is never in doubt.
        command("flash read 42", [b"flash @000040 2a558800"],
                "`flash read` aligns an unaligned offset and says so")

        # The bound, per region, and flash is the one that matters: above
        # 4 MiB the address aliases back onto offset 0, so an unchecked read
        # past the end returns the bitstream header and looks like a
        # successful read of somewhere else entirely.
        command("flash read 400000", [b"past the end"],
                "`flash read` refuses an offset past the flash window")
        command("bram read 100000", [b"past the end"],
                "`bram read` bounds against block RAM, not against flash")
        command("hyperram read 800000", [b"past the end"],
                "`hyperram read` bounds against the 8 MiB part")

        # Block RAM answers on every target: under QEMU offset 0 is the first
        # word of this image's own region. The value is whatever the linker put
        # there, so only the shape is asserted -- what is being checked is that
        # a load reached memory and came back, not what it found.
        command("bram read 0", [b"bram @000000"],
                "`bram read` reads block RAM and names the region")

        # `flash id` is the only identify there is. HyperBus has no JEDEC
        # sequence and fabric has no identity, so those two say why rather than
        # being registered as commands that print nothing.
        command("bram id", [b"bram has no id"],
                "`bram id` says there is nothing to identify")
        command("hyperram id", [b"HyperBus carries no JEDEC id"],
                "`hyperram id` says HyperBus has no such thing")

        # A region with no verb, and a verb with no number.
        command("flash", [b"usage: flash read <hex offset>"],
                "a bare region name says how to call it")
        command("bram read zz", [b"usage: bram read <hex offset>"],
                "a malformed offset is refused rather than read as zero")

        # The HyperRAM read, and here the two targets genuinely differ.
        #
        # Under QEMU the backend is a `.bss` array of about 32k words, so byte
        # offset 0x20000 -- word 0x10000, the area `bench` walks -- is above it
        # and the port does not answer. That is the assertion worth having:
        # every spin in `hyperram::read_word` is bounded, so a dead port
        # produces a sentence in milliseconds instead of a shell that looks
        # hung. The reply must also not be a plausible-looking `ffffffff`,
        # which is why the firmware probes before believing that value.
        #
        # ON THE BOARD the port answers, so the same command must come back
        # with a word rather than a refusal.
        if board:
            command("hyperram read 20000", [b"hyperram @020000"],
                    "`hyperram read` reads the part over the staging port")
        else:
            command("hyperram read 20000", [b"hyperram did not answer"],
                    "`hyperram read` reports a silent port instead of "
                    "spinning on it")

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
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
