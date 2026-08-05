#!/usr/bin/env python3
#
# Run moondancer's ported USB control path under QEMU and assert it dispatches.
# See awtoau/cynthion-workspace#115.
# SPDX-License-Identifier: BSD-3-Clause

"""Drive a USB enumeration at both USB spikes and check what came back.

    python3 scripts/soc_usb_test.py              # build both, run both
    python3 scripts/soc_usb_test.py --only rtic  # one of them

`firmware/cynthion-soc/src/bin/usb_rtic.rs` and `usb_bare.rs` run the same
ported `smolusb` control state machine over the same endpoint stand-in; the
difference is that one dispatches from an `#[rtic::app]` hardware task and the
other from a superloop. This script sends both of them the same enumeration and
asserts they answer identically -- which is the only evidence that the port
WORKS rather than merely compiles.

## Why this is not `soc_test.py`

That script drives the shell, which is the product. This drives two spikes that
are measurement artefacts, and it needs the `rtic` and `usbport` features, which
between them fetch a dependency graph a default build never sees. It is not in
`./dev.py gate` for the reason `scripts/rtic_probe.py` is not: a gate that needs
the network fails on a flight.

## The frames

Bytes on the console are read by the firmware as smolusb's own event encoding
(`From<UsbEvent> for [u8; 2]`) with the SETUP payload appended. See the module
comment in `firmware/cynthion-soc/src/usb.rs`.

Output goes to the terminal and to `tmp/logs/dev.log`.
"""

import argparse
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

CRATE = ROOT / "firmware" / "cynthion-soc"

# A build directory of its own, for the reason soc_test.py gives: sharing
# `target/` with the board build would leave a QEMU-linked binary at the path the
# bitstream packer reads.
BUILD_DIR = ROOT / "tmp" / "usb-qemu-build"

QEMU = "qemu-system-riscv32"
QEMU_ARGS = [
    "-M", "virt",
    "-cpu", "rv32",
    "-m", "64M",
    "-display", "none",
    "-monitor", "none",
    "-serial", "stdio",
    "-bios", "none",
]

# (label, cargo feature, binary name)
SPIKES = [
    ("rtic", "rtic", "usb-rtic"),
    ("bare", "usbport", "usb-bare"),
]

# How long to wait for the banner. Same budget and same reasoning as
# `soc_test.py`: essentially all of it is QEMU's own startup, measured well under
# 0.5 s, and 5 s is an order of magnitude of headroom.
BOOT_S = 5.0

# How long to wait for the reply to a frame burst.
#
# The guest's share of this is microseconds -- eleven events through a state
# machine. The budget is for pipe scheduling between two processes, which is
# what `soc_test.py` sizes its own 3.0 s for. Expiry means the firmware stopped
# dispatching, which is the failure this script exists to find.
REPLY_S = 3.0

# - the frames ---------------------------------------------------------------


def setup(request_type, request, value, index, length):
    """One `ReceiveSetupPacket(0, ...)` frame: code 201, endpoint, 8 bytes."""
    return bytes([201, 0]) + struct.pack(
        "<BBHHH", request_type, request, value, index, length)


BUS_RESET = bytes([10])
REPORT = bytes([255])


def receive_packet(endpoint=0):
    return bytes([12, endpoint])


def send_complete(endpoint=0):
    return bytes([13, endpoint])


# A real enumeration, in the order a host does it. Each entry is
# (what it is, frames, what the state machine should be in afterwards).
#
# The SETUP bytes are the standard requests from the USB 2.0 specification,
# table 9-3, and they are what `smolusb::control` matches on.
ENUMERATION = [
    ("bus reset", BUS_RESET),

    # GET_DESCRIPTOR(Device), 64 bytes requested. The device descriptor is 18,
    # so the reply is 18 -- the assertion that the descriptor path really ran.
    ("get device descriptor", setup(0x80, 6, 0x0100, 0, 64)),
    ("send complete", send_complete()),
    ("host zlp", receive_packet()),

    # SET_ADDRESS(0x25). smolusb does not touch the address register until the
    # status stage completes, so the SendComplete is load-bearing.
    ("set address", setup(0x00, 5, 0x0025, 0, 0)),
    ("send complete", send_complete()),

    # GET_DESCRIPTOR(Configuration), 32 bytes.
    ("get configuration descriptor", setup(0x80, 6, 0x0200, 0, 32)),
    ("send complete", send_complete()),
    ("host zlp", receive_packet()),

    # GET_DESCRIPTOR(String, 0) -- the language list, 4 bytes.
    ("get string descriptor zero", setup(0x80, 6, 0x0300, 0, 255)),
    ("send complete", send_complete()),
    ("host zlp", receive_packet()),

    # SET_CONFIGURATION(1).
    ("set configuration", setup(0x00, 9, 0x0001, 0, 0)),
    ("send complete", send_complete()),

    # GET_STATUS(Device) -- two bytes, self-powered.
    ("get status", setup(0x80, 0, 0, 0, 2)),
    ("send complete", send_complete()),
    ("host zlp", receive_packet()),

    # A vendor request: libgreat's own, 0x65 UsbCommandRequest, DeviceToHost.
    # `Control` cannot handle it and hands the setup packet back, which is where
    # moondancer's `handle_vendor_request` takes over.
    ("vendor request", setup(0xc0, 0x65, 0, 0, 0)),
]

# Substrings that must appear somewhere in the run, each with what it proves.
#
# Every number here is a consequence of the enumeration above and of
# `smolusb::control`'s state machine, not of this script.
MUST_CONTAIN = [
    (b"usb-port ", "the spike booted and reached its console"),
    (b"usb: ReceiveSetupPacket(0) -> Send", "a SETUP packet reached the state "
     "machine and moved it to Send -- this is dispatch, and it is the whole "
     "point of the run"),
    (b"usb: SendComplete(0) -> WaitForZlp", "the status stage advanced the "
     "state machine, so this is a sequence and not one lucky event"),
    (b"usb: SendComplete(0) -> Idle", "SetAddress and Complete both land here"),
    (b"usb: dispatched 18 state Idle address 37 configuration 1",
     "eighteen events, the host's address 0x25 written through "
     "UsbDriver::set_address, and configuration 1 accepted"),
    # 18 device + 32 configuration + 4 string zero + 2 status. Every one of
    # those four lengths is decided by `write_descriptor`'s truncation against
    # the host's requested length, so the total is the descriptor path's
    # arithmetic and not a byte count that would come out right anyway.
    (b"usb: writes 6 bytes 56 zlps 2 primes 4 stalls 1 halts 0",
     "six endpoint writes totalling 56 bytes: the four descriptors, plus the "
     "two zero-length status packets SetAddress and SetConfiguration send"),
    (b"usb: vendor 1 ", "the vendor request was handed back by Control "
     "rather than swallowed -- moondancer's handle_vendor_request path"),
    (b"usb: trace descriptor 0 configuration 0 feature 0 zlp 0 overflow 0 "
     b"length 0 state 0", "smolusb's own error paths were all silent: no "
     "unhandled descriptor, no bad configuration, no state error"),
    (b"usb: first 12 01 00 02 00 00 00 40 50 1d 5b 61 04 01 01 02 03 01 "
     b"(18 bytes)", "the FIRST descriptor written back is the 18-byte device "
     "descriptor, little-endian, with Cynthion's own 1d50:615b -- so the "
     "bytes are right, not merely the count"),
    (b"usb: last 01 00 (2 bytes)", "the last write is GET_STATUS answering "
     "self-powered, which is `Control`'s own bit 0 and not a descriptor"),
]

# Summary fields whose value is a fact about the HOST, not about the firmware.
#
# `depth` is the high-water mark of the event queue, and the host writes frames
# faster than either guest drains them -- so what it records is when the pipe
# happened to be scheduled. Measured across five runs it moved between 16 and 18
# for the RTIC spike and 14 and 18 for the bare one, with the ranges overlapping
# and neither consistently deeper. That is not a latency measurement and it is
# not treated as one; it is excluded from the agreement check rather than
# quietly asserted at whatever it read on the day.
NOISY = (b"usb: vendor ",)


# - the harness --------------------------------------------------------------


class Session:
    """One QEMU run, drained by a thread.

    Always draining, for the reason `soc_test.py` gives: a guest that prints
    while nothing reads fills the pipe and blocks, which looks from here exactly
    like the hang this script exists to detect.
    """

    def __init__(self, elf):
        self.proc = subprocess.Popen(
            [QEMU, *QEMU_ARGS, "-kernel", str(elf)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        self.buf = bytearray()
        self.cond = threading.Condition()
        self.closed = False
        self.errors = bytearray()
        threading.Thread(target=self._drain, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()

    def _drain(self):
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            with self.cond:
                self.buf.extend(chunk)
                self.cond.notify_all()
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def _drain_err(self):
        # QEMU's own complaints land here, and they are the difference between
        # "the firmware is silent" and "QEMU never started".
        while True:
            chunk = self.proc.stderr.read(1)
            if not chunk:
                return
            self.errors.extend(chunk)

    def snapshot(self):
        with self.cond:
            return bytes(self.buf)

    def send(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def expect(self, needle, budget, since=0):
        """Wait for `needle` at or after `since`. Index, or None."""
        deadline = time.monotonic() + budget
        with self.cond:
            while True:
                at = self.buf.find(needle, since)
                if at != -1:
                    return at
                # A dead QEMU does not sit out the budget: EOF sets `closed`.
                if self.closed:
                    return None
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(left)

    def close(self):
        # Terminate rather than wait: these spikes are infinite loops by design.
        self.proc.kill()
        self.proc.wait()


def show(data):
    return (data.decode("ascii", "replace")
            .replace("\r", "").replace("\n", "\n    "))


def build(label, feature, binary):
    """Build one spike for QEMU. `None` on success, else the error text.

    RUSTFLAGS rather than the target-specific variable, for the reason
    `soc_test.py` documents: cargo JOINS the target-specific key with the one in
    .cargo/config.toml and hands the linker both memory.x and memory-qemu.x.
    """
    env = dict(os.environ)
    env["RUSTFLAGS"] = "-C link-arg=-Tmemory-qemu.x -C link-arg=-Tlink.x"
    proc = subprocess.run(
        ["cargo", "build", "--release", "--bin", binary,
         "--features", f"{feature},qemu", "--target-dir", str(BUILD_DIR)],
        cwd=CRATE, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip()[-1500:]
    return None


def run_one(label, binary):
    """Drive one spike. Returns (passed, failures, transcript)."""
    elf = BUILD_DIR / "riscv32imac-unknown-none-elf" / "release" / binary
    session = Session(elf)
    failures = []
    try:
        if session.expect(b"usb-port ", BOOT_S) is None:
            session.close()
            errors = bytes(session.errors).decode("ascii", "replace").strip()
            return False, [f"no banner within {BOOT_S}s"
                           + (f"; qemu said: {errors}" if errors else "")], ""

        # One frame at a time, so a spike that stops dispatching halfway is
        # visible as the line it stopped on rather than as a silent total.
        for what, frames in ENUMERATION:
            session.send(frames)
            # No expectation per frame: the journal is drained by idle, so the
            # ordering between "sent" and "printed" is not something to assert
            # frame by frame. The summary below is where the sequence is checked.
            _ = what

        session.send(REPORT)
        if session.expect(b"usb: done", REPLY_S) is None:
            failures.append(
                f"no summary within {REPLY_S}s of the report frame -- the "
                f"firmware stopped dispatching")

        transcript = session.snapshot()
        for needle, why in MUST_CONTAIN:
            if needle not in transcript:
                failures.append(
                    f"missing {needle.decode('ascii', 'replace')!r}\n"
                    f"      ({why})")
        return not failures, failures, transcript.decode("ascii", "replace")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=[label for label, _f, _b in SPIKES],
                        help="run one spike instead of both")
    parser.add_argument("--no-build", action="store_true",
                        help="use whatever is already in tmp/usb-qemu-build")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the whole transcript of each run")
    args = parser.parse_args()

    spikes = [s for s in SPIKES if args.only is None or s[0] == args.only]

    emit("soc_usb_test: moondancer's control path, ported, under QEMU")
    emit(f"  qemu: {QEMU} {' '.join(QEMU_ARGS)}")
    emit(f"  frames: {len(ENUMERATION)} events, then a report frame")
    emit("")

    transcripts = {}
    failed = []

    for label, feature, binary in spikes:
        if not args.no_build:
            error = build(label, feature, binary)
            if error:
                emit(f"  FAIL {label}: did not build")
                emit(f"    {error}")
                failed.append(label)
                continue

        passed, failures, transcript = run_one(label, binary)
        transcripts[label] = transcript
        if passed:
            emit(f"  ok   {label}: dispatched the enumeration and agreed with "
                 f"all {len(MUST_CONTAIN)} assertions")
        else:
            emit(f"  FAIL {label}:")
            for failure in failures:
                emit(f"    {failure}")
            if transcript:
                emit(f"    transcript:\n    {show(transcript.encode())}")
            failed.append(label)

    # The two spikes run the same state machine over the same events, so their
    # `usb:` lines must be identical apart from the banner. That is a stronger
    # check than either passing alone: it says the dispatcher was swapped and
    # the behaviour was not.
    if len(transcripts) == 2:
        emit("")
        def usb_lines(text):
            noisy = tuple(prefix.decode() for prefix in NOISY)
            return [line.strip() for line in text.splitlines()
                    if line.strip().startswith("usb: ")
                    and not line.strip().startswith(noisy)]
        rtic, bare = usb_lines(transcripts["rtic"]), usb_lines(transcripts["bare"])
        if rtic == bare:
            emit(f"  ok   both: {len(rtic)} `usb:` lines, byte-identical "
                 f"between the RTIC task and the superloop")
        else:
            emit("  FAIL both: the two dispatchers did not agree")
            for n, (a, b) in enumerate(zip(rtic, bare)):
                if a != b:
                    emit(f"    line {n}: rtic {a!r}")
                    emit(f"            bare {b!r}")
            if len(rtic) != len(bare):
                emit(f"    {len(rtic)} lines from rtic, {len(bare)} from bare")
            failed.append("agreement")

    if args.verbose:
        for label, transcript in transcripts.items():
            emit("")
            emit(f"  {label} transcript:\n    {show(transcript.encode())}")

    emit("")
    if failed:
        emit(f"RESULT: FAIL - {', '.join(failed)}")
        return 1
    emit("RESULT: PASS - the ported control path dispatches under both models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
