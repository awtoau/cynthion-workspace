#!/usr/bin/env python3
#
# The binary record stream, decoded without a board. See #250.
# SPDX-License-Identifier: BSD-3-Clause

"""The wire format is written down in three places and they must agree.

    docs/binary-protocol.md   the contract
    scripts/soc_stream.py     the host decoder and the reference encoder
    firmware/cynthion-soc/    the producer -- not written yet

Nothing here needs the board, and that is the point: the format's whole claim is
that a host can tell a good frame from a bad one, and a version it knows from
one it does not, from the bytes alone. Every one of those judgements is
exercised here against bytes fed in by hand.

The golden frames are **frozen as hex**. When the firmware producer lands, it is
checked against these bytes rather than against a second reading of the spec --
otherwise the two implementations can drift into agreeing with different
documents, which is how the layouts in this repo have gone wrong before.
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_stream as st  # noqa: E402

SPEC = ROOT / "docs" / "binary-protocol.md"
FIRMWARE_CRC = ROOT / "firmware" / "cynthion-soc" / "src" / "hyperram.rs"

# Frozen. Regenerate deliberately with `./scripts/soc_stream.py --vectors`, and
# only when the format is being changed on purpose -- a diff here is a wire
# format change, which is a version bump.
GOLDEN = {
    "SESSION":
        "c000111400000000000c000000010100004d3c2b1a8b7a6f5e00879303dedbdcde"
        "dbdce41aac06c0",
    "CATALOGUE POWER":
        "c002107400010000000c00000010050a61504f5745523c49694969496949694974"
        "61726765745f615f6d762c7461726765745f615f75612c7461726765745f635f6d"
        "762c7461726765745f635f75612c6175785f6d762c6175785f75612c636f6e7472"
        "6f6c5f6d762c636f6e74726f6c5f75612c6c6174636865645f7469636b738f8c20"
        "38c0",
    "POWER":
        "c010102400020000000d00000082130000501c07008913000020d1ffffe40c0000"
        "0000000088130000905f010056341200d212945fc0",
    "DROP":
        "c001100c000300000084030000110000000400000078000000913d104dc0",
    "TEXT with delimiters":
        "c00310070004000000e8030000dbdcdbdd206f6b0d0ab3fb4b8cc0",
}


# --------------------------------------------------------------------------
# the CRC is the firmware's, not a second implementation
# --------------------------------------------------------------------------

def firmware_crc32(data: bytes) -> int:
    """`hyperram::Crc32` transcribed from `hyperram.rs:100-118`, byte for byte.

    The decoder calls `zlib.crc32`, which is only correct if that is what the
    board computes. This is the check: the same loop, run here, against the
    stdlib. If someone changes the firmware's polynomial or drops the final NOT,
    every frame on a healthy link starts reading as corrupt -- and the board
    reads as broken.
    """
    crc = 0xFFFF_FFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            mask = -(crc & 1) & 0xFFFF_FFFF
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask)
    return (~crc) & 0xFFFF_FFFF


def test_the_firmwares_crc_is_zlibs():
    assert firmware_crc32(b"123456789") == 0xCBF4_3926
    for _, frame in st.vectors():
        payload = st.slip_unescape(frame[1:-1])[:-st.CRC_LEN]
        assert firmware_crc32(payload) == zlib.crc32(payload) & 0xFFFF_FFFF


def test_the_firmware_still_uses_that_polynomial():
    """The transcription above is only a check while the original is unchanged."""
    text = FIRMWARE_CRC.read_text()
    assert "0xedb8_8320" in text, (
        "hyperram.rs no longer uses the reflected CRC-32 polynomial; "
        "scripts/soc_stream.py calls zlib.crc32 on the assumption that it does")
    assert re.search(r"pub fn finish\(&self\) -> u32 \{\s*!self\.0", text), (
        "hyperram.rs's Crc32::finish no longer inverts; zlib.crc32 does, so the "
        "two would disagree on every frame")


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def test_every_byte_survives_escaping():
    """A body of all 256 values, which is where SLIP has to earn its place."""
    body = bytes(range(256))
    frame = st.encode_record(0x10, 1, 2, body)
    assert frame.count(st.END) == 2, "a delimiter leaked into the escaped payload"
    records = st.Decoder().feed(frame)
    assert len(records) == 1 and records[0].body == body


def test_an_empty_body_is_legal():
    records = st.Decoder().feed(st.encode_record(st.ERROR, 0, 0, b""))
    assert len(records) == 1 and records[0].body == b""


def test_a_body_over_the_maximum_is_refused_by_the_encoder():
    """The bound exists because the firmware's staging buffer is a fixed array."""
    try:
        st.encode_record(0x10, 0, 0, b"\x00" * (st.MAX_BODY + 1))
    except ValueError:
        return
    raise AssertionError("the encoder allowed a body past MAX_BODY")


def test_the_header_is_twelve_bytes():
    """Frozen: it is the offset every field in the spec's table is quoted at."""
    assert st.HEADER_LEN == 12 and st.HEADER.format == "<BBHII"
    assert st.FRAME_MIN == 16


# --------------------------------------------------------------------------
# the golden frames
# --------------------------------------------------------------------------

def test_the_golden_frames_have_not_moved():
    produced = {label: frame.hex() for label, frame in st.vectors()}
    assert produced == GOLDEN, (
        "the encoder no longer produces the frozen frames. If the format changed "
        "on purpose, bump soc_stream.VERSION and regenerate with "
        "`./scripts/soc_stream.py --vectors`; if it did not, this is a bug that "
        "would have shipped as a firmware/host mismatch")


def test_the_golden_frames_decode():
    decoder = st.Decoder()
    records = decoder.feed(b"".join(bytes.fromhex(h) for h in GOLDEN.values()))
    assert len(records) == len(GOLDEN)
    assert decoder.counters.clean(), decoder.summary()
    assert not decoder.warnings, decoder.warnings


def test_a_session_id_that_needs_escaping_still_round_trips():
    """0xC0DEC0DE contains two delimiters. It is in the vectors for that reason."""
    record = st.Decoder().feed(bytes.fromhex(GOLDEN["SESSION"]))[0]
    assert record.fields["session_id"] == 0xC0DEC0DE
    assert record.resync, "the first record of a session must carry RESYNC"


# --------------------------------------------------------------------------
# refusal -- the property the format exists for
# --------------------------------------------------------------------------

def test_an_unknown_version_yields_no_record():
    decoder = st.Decoder()
    frame = st.encode_record(0x10, 0, 0, b"\x00" * 4, version=st.VERSION + 1)
    assert decoder.feed(frame) == []
    assert decoder.counters.unknown_version == 1
    assert decoder.counters.crc_bad == 0, (
        "a well-formed frame from a future firmware must report as a version "
        "this decoder does not know, not as corruption")
    assert any("version" in line for line in decoder.summary())


def test_a_corrupted_frame_reports_as_corruption():
    decoder = st.Decoder()
    corrupt = bytearray(bytes.fromhex(GOLDEN["POWER"]))
    corrupt[7] ^= 0x01
    assert decoder.feed(bytes(corrupt)) == []
    assert decoder.counters.crc_bad == 1
    assert decoder.counters.unknown_version == 0


def test_a_length_that_disagrees_with_the_frame_is_refused():
    """A length field is not trusted; the frame's own extent settles it."""
    frame = bytearray(st.encode_record(0x10, 0, 0, b"\x01\x02\x03\x04"))
    frame[3] = 0x40                     # len says 64 bytes; the frame has four
    decoder = st.Decoder()
    assert decoder.feed(bytes(frame)) == []
    assert decoder.counters.oversize == 1


def test_a_lone_escape_is_refused():
    decoder = st.Decoder()
    assert decoder.feed(bytes([st.END]) + b"\x00" * 14 + bytes([st.ESC, st.END])) == []
    assert decoder.counters.bad_escape == 1


def test_a_runt_frame_is_counted():
    decoder = st.Decoder()
    assert decoder.feed(bytes([st.END]) + b"\x01\x02\x03" + bytes([st.END])) == []
    assert decoder.counters.runt == 1


# --------------------------------------------------------------------------
# joining, truncation, loss
# --------------------------------------------------------------------------

def test_shell_output_produces_no_records_and_says_so():
    """The state a host is in before any producer exists, and today's real case."""
    decoder = st.Decoder()
    assert decoder.feed(b"power\r\n  target_a  4.994 V  466 mA\r\n> ") == []
    assert decoder.counters.discarded_bytes > 0
    assert decoder.counters.frames == 0


def test_a_host_joining_mid_record_recovers_at_the_next_frame():
    stream = b"".join(bytes.fromhex(h) for h in GOLDEN.values())
    decoder = st.Decoder()
    records = decoder.feed(stream[17:])         # into the middle of SESSION
    assert len(records) == len(GOLDEN) - 1
    assert decoder.counters.discarded_bytes > 0


def test_a_truncated_frame_costs_only_itself():
    """Apollo's bridge drops the OLDEST byte on ring overflow (console.c:48-59).

    So bytes vanish from the middle of the stream, and the next delimiter still
    starts a good frame. This is the case length-prefixed framing cannot recover
    from without luck.
    """
    first = bytes.fromhex(GOLDEN["TEXT with delimiters"])
    maimed = bytes.fromhex(GOLDEN["POWER"])
    maimed = maimed[:6] + maimed[20:]           # the ring ate fourteen bytes
    good = bytes.fromhex(GOLDEN["DROP"])
    decoder = st.Decoder()
    records = decoder.feed(first + maimed + good)
    assert [r.kind for r in records] == [st.TEXT, st.DROP]
    assert decoder.counters.crc_bad + decoder.counters.oversize == 1


def test_a_gap_in_the_sequence_is_counted():
    decoder = st.Decoder()
    decoder.feed(st.encode_record(st.ERROR, 10, 0, st.ERROR_S.pack(1, 2)))
    decoder.feed(st.encode_record(st.ERROR, 14, 0, st.ERROR_S.pack(1, 2)))
    assert decoder.counters.seq_gaps == 1
    assert decoder.counters.seq_missing == 3


def test_a_resync_record_is_not_a_gap():
    decoder = st.Decoder()
    decoder.feed(st.encode_record(st.ERROR, 900, 0, st.ERROR_S.pack(1, 2)))
    decoder.feed(st.encode_record(st.SESSION, 0, 0,
                                  st.session_body(kinds=0, firmware_git=1,
                                                  gateware_git=2, time_hz=60_000_000,
                                                  session_id=7),
                                  flags=st.FLAG_RESYNC))
    assert decoder.counters.seq_gaps == 0, (
        "a session restart resets `seq`; counting that as loss would make every "
        "reboot look like 4 billion missing records")


def test_the_board_reporting_its_own_loss_is_a_separate_signal():
    decoder = st.Decoder()
    decoder.feed(bytes.fromhex(GOLDEN["DROP"]))
    assert decoder.counters.reported_lost == 17
    assert decoder.counters.seq_gaps == 0, (
        "DROP is the board overrunning its producer; a seq hole is the wire "
        "losing what it sent. They must not be conflated")


def test_a_new_session_resets_the_sequence_expectation():
    decoder = st.Decoder()

    def session(session_id, seq):
        return st.encode_record(st.SESSION, seq, 0,
                                st.session_body(kinds=0, firmware_git=1,
                                                gateware_git=2,
                                                time_hz=60_000_000,
                                                session_id=session_id))

    decoder.feed(session(1, 5000))
    decoder.feed(session(2, 0))
    decoder.feed(st.encode_record(st.ERROR, 1, 0, st.ERROR_S.pack(0, 0)))
    assert decoder.counters.seq_gaps == 0


# --------------------------------------------------------------------------
# the catalogue is what makes a stale decoder still useful
# --------------------------------------------------------------------------

def test_a_kind_this_decoder_never_heard_of_decodes_from_the_catalogue():
    """The claim: a firmware that adds a producer is readable by today's host."""
    decoder = st.Decoder()
    decoder.feed(st.encode_record(st.CATALOGUE, 0, 0,
                                  st.catalogue_body(0x40, "TYPEC", "<BBH",
                                                    "port,cc,status")))
    record = decoder.feed(st.encode_record(0x40, 1, 0,
                                           struct.pack("<BBH", 1, 2, 0x1234)))[0]
    assert record.name == "TYPEC"
    assert record.fields == {"port": 1, "cc": 2, "status": 0x1234}
    assert not decoder.warnings


def test_an_uncatalogued_kind_is_kept_raw_rather_than_guessed():
    record = st.Decoder().feed(st.encode_record(0x40, 0, 0, b"\xde\xad"))[0]
    assert record.fields == {} and record.body == b"\xde\xad"
    assert "no catalogue entry" in record.note


def test_a_layout_that_disagrees_with_this_decoder_warns_and_the_board_wins():
    """The two-declarations-of-one-truth case, made loud instead of silent."""
    decoder = st.Decoder()
    decoder.feed(st.encode_record(st.CATALOGUE, 0, 0,
                                  st.catalogue_body(0x10, "POWER", "<II",
                                                    "bus_mv,current_ua")))
    assert decoder.warnings, "a changed POWER layout passed without a word"
    record = decoder.feed(st.encode_record(0x10, 1, 0,
                                           struct.pack("<II", 4994, 466_000)))[0]
    assert record.fields == {"bus_mv": 4994, "current_ua": 466_000}


def test_a_catalogue_whose_names_do_not_match_its_format_is_rejected():
    """Names and fields off by one would mislabel every value, silently."""
    decoder = st.Decoder()
    decoder.feed(st.encode_record(st.CATALOGUE, 0, 0,
                                  st.catalogue_body(0x41, "BAD", "<III", "a,b")))
    assert 0x41 not in decoder.layouts
    assert decoder.warnings


def test_an_unusable_format_string_does_not_raise():
    decoder = st.Decoder()
    records = decoder.feed(st.encode_record(st.CATALOGUE, 0, 0,
                                            st.catalogue_body(0x42, "X", "<Z", "a")))
    assert 0x42 not in decoder.layouts
    assert "unusable" in records[0].note


# --------------------------------------------------------------------------
# the spec and the code must agree
# --------------------------------------------------------------------------

def spec_kinds():
    """`| \\`0xNN\\` | NAME | ... |` rows from the kind table in the spec."""
    rows = re.findall(r"^\|\s*`(0x[0-9a-f]{2})`\s*\|\s*([A-Z]+)\s*\|(.*)$",
                      SPEC.read_text(), re.MULTILINE)
    assert rows, "the kind table in docs/binary-protocol.md no longer parses"
    return rows


def test_every_kind_in_the_spec_has_the_same_number_here():
    names = dict(st.RESERVED_NAMES)
    names.update({kind: entry[0] for kind, entry in st.KNOWN.items()})
    for number, name, _ in spec_kinds():
        kind = int(number, 16)
        if name == "BIST":
            assert kind not in names, (
                "the spec calls BIST deliberately unspecified, but soc_stream.py "
                "has a layout for it; one of the two has to move")
            continue
        assert names.get(kind) == name, (
            f"the spec calls {number} {name}; soc_stream.py calls it "
            f"{names.get(kind)!r}")


def test_every_layout_in_the_spec_is_the_layout_here():
    here = {st.SESSION: st.SESSION_S.format, st.DROP: st.DROP_S.format,
            st.ERROR: st.ERROR_S.format}
    here.update({kind: entry[1] for kind, entry in st.KNOWN.items()})
    for number, name, rest in spec_kinds():
        match = re.search(r"`(<[A-Za-z]+)`", rest)
        if not match:
            continue                    # CATALOGUE, TEXT and BIST carry no struct
        assert here.get(int(number, 16)) == match.group(1), (
            f"the spec gives {name} the layout {match.group(1)}, soc_stream.py "
            f"uses {here.get(int(number, 16))!r}")


def test_the_spec_names_the_version_and_the_bound_this_code_uses():
    text = SPEC.read_text()
    assert f"MAX_BODY = {st.MAX_BODY}" in text
    assert "`0x03`" in text and "`?`" in text, (
        "the host-to-board control bytes are the only way out of binary mode; "
        "they have to stay written down")


def test_the_self_test_passes():
    """`--self-test` is what a person runs; it must not rot separately."""
    assert st.self_test() == 0
