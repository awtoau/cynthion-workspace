#!/usr/bin/env python3
#
# The device-interface op protocol, encoded and decoded without a board. See #303.
# SPDX-License-Identifier: BSD-3-Clause

"""The op wire format is written down in four places and they must agree.

    docs/device-interface.md                          the contract
    scripts/soc/soc_ops.py                            op table, host codec
    firmware/cynthion-soc-tests/tests/ops_golden.txt  what the firmware replays
    firmware/cynthion-soc/src/ops/                    the board

The golden frames are FROZEN as hex below. A changed byte in the codec, the op
table or the framing fails here before any firmware is built; a deliberate
format change regenerates them with `soc_ops.py --vectors` and bumps a version.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
import soc_ops as ops  # noqa: E402
import soc_stream as st  # noqa: E402

DOC = ROOT / "docs" / "device-interface.md"
GOLDEN_FILE = ROOT / "firmware" / "cynthion-soc-tests" / "tests" / "ops_golden.txt"
SHELL = ROOT / "firmware" / "cynthion-soc" / "src" / "shell.rs"

# Frozen: (request frame, response frame) per case.
GOLDEN = {
    'sys.ping': (
        'c00810060001000000000000000100dbdddbdca5a51814ef77c0',
        'c0091007000100000088130000010000dbdddbdca5a59ffd7b01c0',
    ),
    'sys.capabilities': (
        'c0081004000200000000000000020000005a925484c0',
        'c009102b0002000000881300000200000140008000030000000e000e01000200'
        '030000010101000201020003010300040104000500060007e7158b1dc0',
    ),
    'sys.info': (
        'c0081002000300000000000000030074b7c47ac0',
        'c00910110003000000881300000300002b1a00800087930340e2010000024ccd'
        'b931c0',
    ),
    'power.read': (
        'c0081002000400000000000000000158cf32c4c0',
        'c009103c00040000008813000000010032000000048213000020d1ffff204e00'
        '000089130000501c0700204e0000010000000000000000204e00000088130000'
        '905f0100204e000001c5c90c63c0',
    ),
    'power.limits': (
        'c0081003000500000000000000010102da09a244c0',
        'c00910150005000000881300000101007c150000a00f0000dbdcc62d004039d2'
        'ff0302248a2354c0',
    ),
    'vbus.status': (
        'c00810020006000000000000000002df4ece59c0',
        'c0091006000600000088130000000200840402da1f1b55c0',
    ),
    'vbus.set': (
        'c008100600070000000000000001020101dc0513c84204c0',
        'c00910090007000000881300000102000089130000000af5d0cfc0',
    ),
    'led.get': (
        'c00810020008000000000000000003fa4e0030c0',
        'c0091006000800000088130000000300092001255bec22c0',
    ),
    'led.set': (
        'c008100400090000000000000001030301b6441fb9c0',
        'c009100500090000008813000001030009001556c22bc0',
    ),
    'typec.status': (
        'c0081002000a000000000000000004640b91aac0',
        'c009101b000a0000008813000000040001030191010100010204000000019100'
        'ffff00000000000047d1371ac0',
    ),
    'pd.status': (
        'c0081002000b0000000000000001041b51485cc0',
        'c009101e000b00000088130000010400010101010c0000000b00000000000000'
        '0000000001840300060000f2a47eb0c0',
    ),
    'usb.port': (
        'c0081003000c00000000000000000501d6c948c3c0',
        'c0091014000c000000881300000005000189130000501c070001010101010000'
        '02b283235ac0',
    ),
    'hr.status': (
        'c0081002000d000000000000000006317143a6c0',
        'c009101d000d000000881300000006007856341220bf0200bcbe020001010100'
        '000400000000000a200185dbdd1b18c0',
    ),
    'selftest.run': (
        'c0081002000e000000000000000007a4fa733ac0',
        'c009101d000e000000881300000007000a00010b000001000200030004000500'
        '06000700080009000a0253c9988bc0',
    ),
    'vbus.set refused uv': (
        'c0081006000f000000000000000102030200006bdeb3c2c0',
        'c0091008000f00000088130000010203038c0f0000e6689d1ac0',
    ),
    'unknown op': (
        'c0081002001000000000000000ff7f834feecec0',
        'c0091008001000000088130000ff7f010000000000b413b5b3c0',
    ),
    'bad args': (
        'c0081003001100000000000000010000feee98adc0',
        'c009100800110000008813000001000200000000003156886bc0',
    ),
}


def _generated():
    return {name: (req.hex(), rsp.hex()) for name, req, rsp in ops.golden_cases()}


def test_every_golden_frame_is_byte_for_byte_frozen():
    generated = _generated()
    assert set(generated) == set(GOLDEN)
    for name, (req, rsp) in GOLDEN.items():
        assert generated[name] == (req, rsp), name


def test_the_golden_check_can_fail():
    """Cannot-fail proof: a one-byte change anywhere is caught by the comparison."""
    name, (req, rsp) = next(iter(GOLDEN.items()))
    for frame in (req, rsp):
        mutated = frame[:10] + ("0" if frame[10] != "0" else "1") + frame[11:]
        assert mutated != frame
        decoder = st.Decoder()
        assert decoder.feed(bytes.fromhex(mutated)) == []
        assert decoder.counters.crc_bad + decoder.counters.oversize \
            + decoder.counters.unknown_version == 1
    # and the frozen comparison itself: a different request id is a different frame
    other = ops.encode_request("sys.ping", 2, 0xA5A5C0DB).hex()
    assert other != GOLDEN["sys.ping"][0]


def test_the_firmware_golden_file_is_what_the_generator_writes():
    assert GOLDEN_FILE.read_text() == ops.golden_text(), \
        "regenerate: ./scripts/soc/soc_ops.py --golden > " \
        "firmware/cynthion-soc-tests/tests/ops_golden.txt"


def test_every_request_and_response_decodes_to_what_was_encoded():
    for name, (req_hex, rsp_hex) in GOLDEN.items():
        dec = st.Decoder()
        recs = dec.feed(bytes.fromhex(req_hex) + bytes.fromhex(rsp_hex))
        assert len(recs) == 2 and dec.counters.clean(), name
        request = ops.decode_request(recs[0])
        response = ops.decode_response(recs[1])
        assert response.req_id == request.req_id, name
        assert response.op_code == request.op_code, name
        assert response.at_ms == ops.AT, name


def test_a_response_is_matched_by_its_echoed_id_not_by_order():
    first = ops.encode_response("sys.ping", 7, 1, 1)
    second = ops.encode_response("sys.ping", 9, 2, 2)
    recs = st.Decoder().feed(second + first)
    assert [ops.decode_response(r).req_id for r in recs] == [9, 7]


def test_requests_and_responses_stay_out_of_stream_gap_detection():
    dec = st.Decoder()
    frames = [ops.encode_response("sys.ping", i, 0, 0) for i in (100, 5, 4000)]
    dec.feed(st.encode_record(st.TEXT, 0, 0, b"a", flags=st.FLAG_RESYNC)
             + b"".join(frames) + st.encode_record(st.TEXT, 1, 0, b"b"))
    assert dec.counters.seq_gaps == 0 and dec.counters.clean()


def test_a_real_stream_gap_is_still_counted_around_responses():
    dec = st.Decoder()
    dec.feed(st.encode_record(st.TEXT, 0, 0, b"a", flags=st.FLAG_RESYNC)
             + ops.encode_response("sys.ping", 1, 0, 0)
             + st.encode_record(st.TEXT, 5, 0, b"b"))
    assert dec.counters.seq_gaps == 1 and dec.counters.seq_missing == 4


def test_failures_carry_reason_and_value():
    rsp = ops.decode_response(st.Decoder().feed(
        ops.encode_failure(0x0201, 3, 10, ops.ST_REFUSED, 3, 3980))[0])
    assert (rsp.status, rsp.reason, rsp.value, rsp.ok) == (ops.ST_REFUSED, 3, 3980, False)


def test_a_malformed_failure_tail_is_refused_not_guessed():
    body = ops.RSP_HEAD.pack(0x0001, ops.ST_BAD_ARGS) + b"\x00"
    rec = st.Decoder().feed(st.encode_record(ops.KIND_RSP, 1, 0, body))[0]
    try:
        ops.decode_response(rec)
    except ValueError:
        return
    raise AssertionError("a 1-byte failure tail decoded")


def test_a_response_longer_than_the_table_knows_is_refused_for_fixed_ops():
    body = ops.RSP_HEAD.pack(0x0001, 0) + struct.pack("<II", 1, 2)
    rec = st.Decoder().feed(st.encode_record(ops.KIND_RSP, 1, 0, body))[0]
    try:
        ops.decode_response(rec)
    except ValueError:
        return
    raise AssertionError("a result with trailing bytes decoded")


def test_negative_current_and_limit_stay_signed():
    rsp = ops.decode_response(st.Decoder().feed(
        ops.encode_response("power.limits", 1, 0, 5500, 4000, 3_000_000, -3_000_000, 3, 2))[0])
    assert rsp.fields["uc_ua"] == -3_000_000
    power = ops.decode_response(st.Decoder().feed(
        [r for n, _, r in ops.golden_cases() if n == "power.read"][0])[0])
    assert power.fields["target_a_ua"] == -12_000


def test_capabilities_lists_every_op_in_the_table():
    rsp = ops.decode_response(st.Decoder().feed(
        [r for n, _, r in ops.golden_cases() if n == "sys.capabilities"][0])[0])
    assert rsp.tail == [op.code for op in ops.OPS]
    assert rsp.fields["total"] == rsp.fields["n"] == len(ops.OPS)
    assert len(ops.OPS) <= ops.CAPS_PAGE


def test_every_op_fits_the_firmware_buffers():
    for op in ops.OPS:
        assert ops.REQ_HEAD.size + op.args_s.size <= ops.MAX_REQ_BODY, op.name
        assert ops.RSP_HEAD.size + op.result_s.size <= ops.MAX_RSP_BODY, op.name
    cap = ops.RSP_HEAD.size + ops.BY_NAME["sys.capabilities"].result_s.size \
        + 2 * ops.CAPS_PAGE
    assert cap <= ops.MAX_RSP_BODY
    sel = ops.RSP_HEAD.size + ops.BY_NAME["selftest.run"].result_s.size \
        + 2 * len(ops.SELFTEST_ITEMS) * 2
    assert sel <= ops.MAX_RSP_BODY


def test_the_watchdog_is_one_and_a_quarter_keepalive_periods():
    assert ops.WATCHDOG_MS == ops.KEEPALIVE_MS * 5 // 4
    assert ops.WATCHDOG_MS == 2500
    assert ops.LEAVE == b"\x03" and ops.ENTER == b"ops\r"


def test_a_host_frame_never_starts_with_an_escape_byte():
    """ETX and `?` are only valid between frames; a REQ must not begin with them."""
    for _, req, _ in ops.golden_cases():
        assert req[0] == st.END and req[1] not in (st.CTRL_LEAVE, st.CTRL_CATALOGUE)


def test_the_doc_names_every_op_with_its_code():
    text = DOC.read_text()
    for op in ops.OPS:
        assert f"`0x{op.code:04x}` | `{op.name}`" in text, op.name


def test_the_doc_covers_every_shell_command_family():
    rows = re.findall(r'^\s*\("([^"]+)", "', SHELL.read_text(), re.M)
    assert len(rows) > 50
    text = DOC.read_text()
    missing = set()
    for row in rows:
        word = row.split(",")[0].split(" ")[0]
        if f"`{word}" not in text and f"| {word}" not in text:
            missing.add(word)
    assert not missing, f"shell commands the device-interface doc does not mention: {sorted(missing)}"


def test_the_op_table_in_the_doc_matches_the_generator_names_and_args():
    text = DOC.read_text()
    for op in ops.OPS:
        for arg in op.arg_names:
            assert arg in text, (op.name, arg)


def test_opcodes_match_the_firmware_constants():
    src = (ROOT / "firmware" / "cynthion-soc" / "src" / "ops" / "proto.rs")
    if not src.exists():
        return
    consts = {m.group(1): int(m.group(2).replace("_", ""), 16)
              for m in re.finditer(r"pub const OP_(\w+): u16 = 0x([0-9a-fA-F_]+);",
                                   src.read_text())}
    table = {op.name.upper().replace(".", "_"): op.code for op in ops.OPS}
    assert consts == table
