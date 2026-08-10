#!/usr/bin/env python3
#
# The board's binary record stream, host side: bytes in, typed records out.
# SPDX-License-Identifier: BSD-3-Clause

"""
Decoder for the SoC's binary record stream, and the reference encoder the
firmware is checked against. The wire format is
[`docs/binary-protocol.md`](../docs/binary-protocol.md); this module is the only
host-side implementation of it.

    ./scripts/soc_stream.py --self-test     # no board: encode, decode, compare
    ./scripts/soc_stream.py --vectors       # golden frames as hex, for firmware
    ./scripts/soc_stream.py                 # decode a live console

## Two properties this exists to hold

**A stale decoder must refuse, not misparse.** The envelope version is in the
high nibble of every header, not only in `SESSION`, so a host that joins
mid-stream cannot parse v2 records with v1 rules. An unrecognised version is
counted and the frame dropped -- no record is ever emitted from bytes whose
layout is not understood.

**Producer layouts come from the board.** `CATALOGUE` records carry a `struct`
format string and field names per kind, so a firmware that adds a field stays
decodable by a host that predates it. `KNOWN` below is a *cross-check* against
those, never the source: a disagreement is reported loudly and the board wins.
Silently preferring either copy is the two-declarations-of-one-truth failure
this project keeps hitting.

## Why the CRC is CRC-32 and not something shorter

`firmware/cynthion-soc/src/hyperram.rs:100-118` already has a bitwise CRC-32 in
the image -- init 0xffffffff, reflected 0xedb88320, final NOT, which is exactly
`zlib.crc32`. Reusing it costs the firmware no new `.text` and costs this file no
hand-rolled loop. Two bytes more per record than a CRC-16, against a whole
implementation on each side that could be wrong in different ways.

It earns its place because **two independent places drop bytes silently**:
`Uart::put` abandons a byte after 200,000 spins (`uart.rs:329-339`) and Apollo's
console bridge drops the *oldest* byte when its 256-byte ring overflows
(`repos/apollo/firmware/src/console.c:48-59`). Either produces a truncated frame
that still looks plausible.

## What has and has not been exercised

`--self-test` and `tests/test_soc_stream.py` cover framing, CRC, resync, version
refusal, sequence gaps and catalogue-driven decode, all without a board.

**The live path has never run.** No firmware emits records yet and no shell
command enters binary mode, so `listen()` has nothing to decode; it reuses
`soc_shell.Link` so that when a producer exists it reaches the console the same
way the other five scripts do.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

# --------------------------------------------------------------------------
# the envelope -- frozen by docs/binary-protocol.md
# --------------------------------------------------------------------------

VERSION = 1                     # envelope version, high nibble of header byte 1

END = 0xC0                      # SLIP, RFC 1055
ESC = 0xDB
ESC_END = 0xDC
ESC_ESC = 0xDD

HEADER = struct.Struct("<BBHII")        # kind, ver|flags, len, seq, at_ms
HEADER_LEN = HEADER.size                # 12
CRC_LEN = 4
FRAME_MIN = HEADER_LEN + CRC_LEN        # an empty body is legal; 15 bytes is not

# Largest body the firmware may emit. Fixes the size of its staging buffer --
# there is no allocator on the target, so that is a compile-time array, and the
# host refuses anything longer rather than growing to meet it.
MAX_BODY = 256

# Flags, low nibble of header byte 1.
FLAG_RESYNC = 0x1               # first record after entering binary mode
FLAG_TRUNC = 0x2                # the producer clipped this body to MAX_BODY

# Reserved kinds: the stream's own affairs. Their layouts are frozen here
# because a host needs them before any catalogue has arrived.
SESSION = 0x00
DROP = 0x01
CATALOGUE = 0x02
TEXT = 0x03
ERROR = 0x04

SESSION_S = struct.Struct("<BBHIIII")   # 20 bytes
DROP_S = struct.Struct("<III")
CATALOGUE_S = struct.Struct("<BBBB")
ERROR_S = struct.Struct("<II")

SESSION_FIELDS = ["format_version", "kinds", "reserved", "firmware_git",
                  "gateware_git", "time_hz", "session_id"]

RESERVED_NAMES = {SESSION: "SESSION", DROP: "DROP", CATALOGUE: "CATALOGUE",
                  TEXT: "TEXT", ERROR: "ERROR"}

# Producer kinds. The numbers, names and units are fixed by the spec; the
# layouts are NOT -- they arrive in CATALOGUE records. These entries are the
# cross-check, and they are transcribed from the driver types:
#
#   POWER  power.rs:410-425  Reading { bus_mv: u32, current_ua: i32 } x4,
#                            plus the clock::Instant that latched them (u32
#                            ticks of `time_hz`, wraps every 71.6 s at 60 MHz).
#                            Channel order is power.rs:261, NOT connector order.
#   EVENT  events.rs:316-329 the ring slot verbatim and undecoded: packed code,
#                            64-bit payload, and the millisecond it was pushed
#                            -- which is not the millisecond it was sent.
#
# 0x30 BIST is allocated and deliberately unspecified: no producer exists, and
# the catalogue means a layout does not need freezing before one does.
KNOWN = {
    0x10: ("POWER", "<IiIiIiIiI",
           "target_a_mv,target_a_ua,target_c_mv,target_c_ua,"
           "aux_mv,aux_ua,control_mv,control_ua,latched_ticks"),
    0x20: ("EVENT", "<IQI", "code,value,at_ms"),
}

# Host to board, in binary mode. One raw byte each, no framing: the board's
# receive path is a byte-at-a-time state machine and this is the whole of it.
# Anything richer is a driver operation and waits for #303.
CTRL_LEAVE = 0x03               # ETX -- back to the text shell
CTRL_CATALOGUE = ord("?")       # re-emit SESSION and the catalogue


def crc32(data: bytes) -> int:
    """The firmware's `hyperram::Crc32`, which is CRC-32/ISO-HDLC."""
    return zlib.crc32(data) & 0xFFFFFFFF


# Standard check value, asserted at import in the manner of
# `sideband_decoder.crc8`: a CRC that disagrees with the firmware's would report
# every frame on a healthy link as corrupt, which reads as a broken board.
assert crc32(b"123456789") == 0xCBF43926, "CRC-32 is not the firmware's"


# --------------------------------------------------------------------------
# SLIP
# --------------------------------------------------------------------------

def slip_escape(payload: bytes) -> bytes:
    out = bytearray()
    for byte in payload:
        if byte == END:
            out += bytes((ESC, ESC_END))
        elif byte == ESC:
            out += bytes((ESC, ESC_ESC))
        else:
            out.append(byte)
    return bytes(out)


def slip_unescape(payload: bytes) -> bytes | None:
    """The frame's bytes, or None if an escape sequence is malformed.

    A trailing lone ESC, or ESC followed by anything but ESC_END/ESC_ESC, means
    the frame is not what the sender wrote. Returning None rather than guessing
    keeps a corrupted frame from becoming a plausible record.
    """
    out = bytearray()
    it = iter(payload)
    for byte in it:
        if byte != ESC:
            out.append(byte)
            continue
        nxt = next(it, None)
        if nxt == ESC_END:
            out.append(END)
        elif nxt == ESC_ESC:
            out.append(ESC)
        else:
            return None
    return bytes(out)


def encode_record(kind: int, seq: int, at_ms: int, body: bytes = b"",
                  flags: int = 0, version: int = VERSION) -> bytes:
    """One framed record. The reference the firmware's emitter is checked against.

    `version` is a parameter so a test can produce a frame from a future
    firmware and confirm this decoder refuses it.
    """
    if len(body) > MAX_BODY:
        raise ValueError(f"body is {len(body)} bytes, the format allows {MAX_BODY}")
    if not 0 <= flags <= 0xF:
        raise ValueError("flags are the low nibble only")
    payload = HEADER.pack(kind, (version << 4) | flags, len(body),
                          seq & 0xFFFFFFFF, at_ms & 0xFFFFFFFF) + body
    payload += struct.pack("<I", crc32(payload))
    return bytes((END,)) + slip_escape(payload) + bytes((END,))


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass
class Record:
    kind: int
    version: int
    flags: int
    seq: int
    at_ms: int
    body: bytes
    name: str = "?"
    fields: dict = field(default_factory=dict)
    note: str = ""              # why `fields` is empty, when it is

    @property
    def resync(self) -> bool:
        return bool(self.flags & FLAG_RESYNC)

    def __str__(self) -> str:
        head = f"{self.at_ms:>10} ms  #{self.seq:<8} {self.name:<10}"
        if self.fields:
            return head + " " + " ".join(f"{k}={v}" for k, v in self.fields.items())
        if self.note:
            return f"{head} ({self.note}) {self.body.hex()}"
        return head + " " + self.body.hex()


@dataclass
class Counters:
    """Every way a byte can fail to become a record. All of them are reported.

    Absence reading as "nothing happened" is the failure this project keeps
    being bitten by, so a run that saw nothing and a run that dropped six
    hundred frames must not print the same thing.
    """
    frames: int = 0
    records: int = 0
    discarded_bytes: int = 0    # before the first END: a mid-stream join, or text
    runt: int = 0               # shorter than a header plus a CRC
    oversize: int = 0           # a `len` past MAX_BODY, or disagreeing with the frame
    bad_escape: int = 0
    crc_bad: int = 0
    unknown_version: int = 0
    seq_gaps: int = 0
    seq_missing: int = 0        # records implied by those gaps, summed
    reported_lost: int = 0      # what DROP records said the board itself lost

    def clean(self) -> bool:
        return not (self.runt or self.oversize or self.bad_escape or self.crc_bad
                    or self.unknown_version or self.seq_gaps)


class Decoder:
    """Bytes in, records out. Never raises on wire data; counts and continues.

    The one thing it will not do is emit a record it is not sure of. A frame with
    a bad CRC, a malformed escape or an unknown envelope version is counted and
    dropped -- reporting it as data would be the misparse the format exists to
    make impossible.
    """

    def __init__(self, *, cross_check: bool = True):
        self.counters = Counters()
        self.layouts: dict[int, tuple[str, struct.Struct, list[str]]] = {}
        self.warnings: list[str] = []
        self.session: dict | None = None
        self._buf = bytearray()
        self._synced = False    # has an END been seen yet?
        self._last_seq: int | None = None
        self._cross_check = cross_check

    # -- framing ---------------------------------------------------------

    def feed(self, chunk: bytes) -> list[Record]:
        """Decode whatever is complete in `chunk`, holding any partial frame."""
        out = []
        for byte in chunk:
            if byte != END:
                if self._synced:
                    self._buf.append(byte)
                else:
                    # Joining mid-stream, or reading the text shell. Everything
                    # before the first delimiter is not a frame and never was.
                    self.counters.discarded_bytes += 1
                continue
            if not self._synced:
                self._synced = True
                self._buf.clear()
                continue
            if self._buf:
                record = self._frame(bytes(self._buf))
                if record is not None:
                    out.append(record)
            self._buf.clear()
        return out

    def _frame(self, raw: bytes) -> Record | None:
        self.counters.frames += 1

        payload = slip_unescape(raw)
        if payload is None:
            self.counters.bad_escape += 1
            return None
        if len(payload) < FRAME_MIN:
            self.counters.runt += 1
            return None

        kind, verflags, length, seq, at_ms = HEADER.unpack_from(payload, 0)
        if length > MAX_BODY or HEADER_LEN + length + CRC_LEN != len(payload):
            self.counters.oversize += 1
            return None

        want, = struct.unpack_from("<I", payload, len(payload) - CRC_LEN)
        if crc32(payload[:-CRC_LEN]) != want:
            self.counters.crc_bad += 1
            return None

        version = verflags >> 4
        if version != VERSION:
            # Checked AFTER the CRC, so a corrupted byte is reported as
            # corruption rather than as a firmware from the future.
            self.counters.unknown_version += 1
            return None

        record = Record(kind=kind, version=version, flags=verflags & 0xF, seq=seq,
                        at_ms=at_ms, body=payload[HEADER_LEN:HEADER_LEN + length])
        # Interpreted first: a SESSION whose id changed clears the sequence
        # expectation, and doing that after the check would count every restart
        # as four billion missing records.
        self._interpret(record)
        self._sequence(record)
        self.counters.records += 1
        return record

    def _sequence(self, record: Record) -> None:
        """A hole in `seq` is loss the board never got to report.

        Two independent signals, deliberately: `DROP` is the board saying it
        overran its own producer, this is the wire losing what the board sent.
        Either can be the only one that fires.
        """
        if self._last_seq is not None and not record.resync:
            missing = (record.seq - self._last_seq - 1) & 0xFFFFFFFF
            if missing:
                self.counters.seq_gaps += 1
                self.counters.seq_missing += missing
        self._last_seq = record.seq

    # -- interpretation --------------------------------------------------

    def _interpret(self, record: Record) -> None:
        known = self.layouts.get(record.kind) or KNOWN.get(record.kind)
        record.name = RESERVED_NAMES.get(record.kind) or \
            (known[0] if known else f"0x{record.kind:02x}")

        if record.kind == SESSION:
            self._session(record)
        elif record.kind == DROP:
            self._drop(record)
        elif record.kind == CATALOGUE:
            self._catalogue(record)
        elif record.kind == TEXT:
            record.fields = {"text": record.body.decode("ascii", "replace")}
        elif record.kind == ERROR:
            self._fixed(record, ERROR_S, ["code", "value"])
        elif record.kind in self.layouts:
            _, layout, names = self.layouts[record.kind]
            self._fixed(record, layout, names)
        else:
            record.note = "no catalogue entry for this kind"

    def _fixed(self, record: Record, layout: struct.Struct, names: list[str]) -> None:
        if len(record.body) != layout.size:
            record.note = (f"body is {len(record.body)} bytes, the layout wants "
                           f"{layout.size}")
            return
        record.fields = dict(zip(names, layout.unpack(record.body)))

    def _session(self, record: Record) -> None:
        if len(record.body) != SESSION_S.size:
            record.note = f"SESSION body is {len(record.body)}, want {SESSION_S.size}"
            return
        record.fields = dict(zip(SESSION_FIELDS, SESSION_S.unpack(record.body)))
        if record.fields["format_version"] != VERSION:
            # The header nibble already agreed or this frame would have been
            # refused, so this is the board contradicting itself.
            self.warnings.append(
                f"SESSION says format v{record.fields['format_version']} but the "
                f"header nibble says v{record.version}")
        # A new session restarts `seq`; that is not a gap.
        if self.session and self.session["session_id"] != record.fields["session_id"]:
            self._last_seq = None
        self.session = record.fields
        self.layouts.clear()

    def _drop(self, record: Record) -> None:
        self._fixed(record, DROP_S, ["lost", "from_seq", "at_first_ms"])
        self.counters.reported_lost += record.fields.get("lost", 0)

    def _catalogue(self, record: Record) -> None:
        if len(record.body) < CATALOGUE_S.size:
            record.note = "CATALOGUE body is shorter than its own header"
            return
        kind, name_len, fmt_len, fields_len = CATALOGUE_S.unpack_from(record.body, 0)
        at = CATALOGUE_S.size
        want = at + name_len + fmt_len + fields_len
        if want != len(record.body):
            record.note = f"CATALOGUE lengths sum to {want}, body is {len(record.body)}"
            return
        name = record.body[at:at + name_len].decode("ascii", "replace")
        at += name_len
        fmt = record.body[at:at + fmt_len].decode("ascii", "replace")
        at += fmt_len
        names = [n for n in record.body[at:at + fields_len]
                 .decode("ascii", "replace").split(",") if n]

        record.fields = {"kind": f"0x{kind:02x}", "name": name, "fmt": fmt,
                         "fields": ",".join(names)}

        try:
            layout = struct.Struct(fmt)
        except struct.error as error:
            record.note = f"unusable format string {fmt!r}: {error}"
            self.warnings.append(f"{name}: {record.note}")
            return
        count = len(layout.unpack(bytes(layout.size)))
        if len(names) != count:
            self.warnings.append(
                f"{name}: {len(names)} field names for {fmt!r}, which has {count} "
                f"fields -- decoded names would not line up, so this kind stays raw")
            return
        self.layouts[kind] = (name, layout, names)
        self._compare_with_known(kind, name, fmt, names)

    def _compare_with_known(self, kind: int, name: str, fmt: str,
                            names: list[str]) -> None:
        """The board is authoritative. This says so out loud when we disagree.

        `KNOWN` is what this file was written against. When the board says
        something else the board wins -- but preferring it silently is how a host
        ends up plotting a field that moved.
        """
        expected = KNOWN.get(kind)
        if not expected or not self._cross_check:
            return
        want_name, want_fmt, want_fields = expected
        if name != want_name:
            self.warnings.append(
                f"kind 0x{kind:02x} is {name!r} on the board, {want_name!r} here")
        if fmt != want_fmt or names != want_fields.split(","):
            self.warnings.append(
                f"{name}: board layout {fmt!r}/{','.join(names)} differs from this "
                f"decoder's {want_fmt!r}/{want_fields} -- using the board's")

    # -- reporting -------------------------------------------------------

    def summary(self) -> list[str]:
        counters = self.counters
        lines = [f"frames {counters.frames}, records {counters.records}, "
                 f"{counters.discarded_bytes} bytes discarded before framing"]
        for label, value in (("runt frames", counters.runt),
                             ("oversize or short bodies", counters.oversize),
                             ("malformed escapes", counters.bad_escape),
                             ("CRC failures", counters.crc_bad),
                             ("unknown envelope version", counters.unknown_version),
                             ("sequence gaps", counters.seq_gaps),
                             ("records missing across those gaps",
                              counters.seq_missing),
                             ("records the board reported losing",
                              counters.reported_lost)):
            if value:
                lines.append(f"  {label}: {value}")
        for warning in self.warnings:
            lines.append(f"  WARNING {warning}")
        if counters.unknown_version:
            lines.append("  the board speaks a format version this decoder does not "
                         "know; update scripts/soc_stream.py rather than trusting "
                         "anything above")
        return lines


# --------------------------------------------------------------------------
# encode helpers -- the vectors, the self-test, and any future producer
# --------------------------------------------------------------------------

def session_body(*, kinds: int, firmware_git: int, gateware_git: int,
                 time_hz: int, session_id: int) -> bytes:
    return SESSION_S.pack(VERSION, kinds, 0, firmware_git, gateware_git,
                          time_hz, session_id)


def catalogue_body(kind: int, name: str, fmt: str, fields: str) -> bytes:
    parts = (name.encode(), fmt.encode(), fields.encode())
    return CATALOGUE_S.pack(kind, *(len(p) for p in parts)) + b"".join(parts)


def vectors() -> list[tuple[str, bytes]]:
    """Golden frames, frozen byte-for-byte in `tests/test_soc_stream.py`.

    The firmware's emitter is checked against these bytes rather than against a
    second reading of the spec, so the two implementations cannot drift into
    agreeing with different documents.
    """
    return [
        ("SESSION", encode_record(
            SESSION, 0, 12,
            session_body(kinds=1, firmware_git=0x1A2B3C4D,
                         gateware_git=0x5E6F7A8B, time_hz=60_000_000,
                         session_id=0xC0DEC0DE),
            flags=FLAG_RESYNC)),
        ("CATALOGUE POWER", encode_record(
            CATALOGUE, 1, 12, catalogue_body(0x10, *KNOWN[0x10]))),
        # 4.994 V at 466 mA on target_a, and a negative current on target_c:
        # the switch tree is bidirectional and `current_ua` is i32 for that
        # reason (power.rs:410-413), so an unsigned decoder would read -12 mA
        # as 4.29 A.
        ("POWER", encode_record(
            0x10, 2, 13,
            struct.pack("<IiIiIiIiI", 4994, 466_000, 5001, -12_000,
                        3300, 0, 5000, 90_000, 0x0012_3456))),
        ("DROP", encode_record(DROP, 3, 900, DROP_S.pack(17, 4, 120))),
        # 0xC0 and 0xDB in a body: the two bytes SLIP has to escape.
        ("TEXT with delimiters", encode_record(
            TEXT, 4, 1000, b"\xc0\xdb ok\r\n")),
    ]


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def self_test() -> int:
    """Encode, decode, compare. No board, no firmware, no serial port."""
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        emit(f"  {'ok  ' if ok else 'FAIL'}  {label}"
             + (f" -- {detail}" if detail else ""))
        if not ok:
            failures += 1

    emit("round trip")
    decoder = Decoder()
    stream = b"".join(frame for _, frame in vectors())
    records = decoder.feed(stream)
    check("every vector decodes", len(records) == len(vectors()),
          f"{len(records)} of {len(vectors())}")
    check("no counter fired", decoder.counters.clean(), "; ".join(decoder.summary()))
    if len(records) == len(vectors()):
        check("SESSION carries the session id",
              records[0].fields.get("session_id") == 0xC0DEC0DE)
        check("the catalogue taught it POWER", 0x10 in decoder.layouts)
        check("POWER decoded from the catalogue",
              records[2].fields.get("target_a_mv") == 4994)
        check("a reverse current stays negative",
              records[2].fields.get("target_c_ua") == -12_000)
        check("delimiters survived escaping", records[4].body == b"\xc0\xdb ok\r\n")

    emit("joining mid-stream")
    decoder = Decoder()
    partial = stream[len(vectors()[0][1]) // 2:]
    records = decoder.feed(b"power\r\n  0  4.994 V\r\n> " + partial)
    check("the ASCII prologue and the half frame are discarded",
          decoder.counters.discarded_bytes > 0)
    check("the rest still decodes", len(records) == len(vectors()) - 1,
          f"{len(records)} records")

    emit("refusal")
    decoder = Decoder()
    check("a future version yields no records",
          not decoder.feed(encode_record(0x10, 0, 0, b"\x00" * 4,
                                         version=VERSION + 1)))
    check("and is counted", decoder.counters.unknown_version == 1)

    decoder = Decoder()
    corrupt = bytearray(vectors()[3][1])
    corrupt[5] ^= 0xFF
    check("a corrupted frame yields no records", not decoder.feed(bytes(corrupt)))
    check("and is counted as CRC, not as a version", decoder.counters.crc_bad == 1)

    emit("")
    emit(f"FAILURES: {failures}" if failures else "all checks passed")
    return 1 if failures else 0


def listen(seconds: float) -> int:
    """Decode a live console. Never yet run against a producer -- see the docstring."""
    import time

    import soc_shell

    try:
        link = soc_shell.Link.open()
    except RuntimeError as error:
        emit(f"could not reach the console: {error}")
        return 1
    emit(f"console: {link.how}")
    link.settle(0.05)

    decoder = Decoder()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            chunk = link.read_available()
            if not chunk:
                continue
            for record in decoder.feed(chunk):
                emit(str(record))
    finally:
        link.close()

    emit("")
    for line in decoder.summary():
        emit(line)
    if decoder.counters.records == 0:
        emit("no records: nothing on this board emits them yet, and the shell has "
             "no command to enter binary mode. See docs/binary-protocol.md.")
        return 1
    return 0 if decoder.counters.clean() else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true",
                        help="encode and decode the golden vectors; no board")
    parser.add_argument("--vectors", action="store_true",
                        help="print the golden frames as hex")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="how long to listen for (default 10)")
    args = parser.parse_args()

    if args.vectors:
        for label, frame in vectors():
            emit(f"{label:<22} {frame.hex()}")
        return 0
    if args.self_test:
        return self_test()
    return listen(args.seconds)


if __name__ == "__main__":
    sys.exit(main())
