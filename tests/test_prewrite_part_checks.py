#!/usr/bin/env python3
#
# Nothing is written to a board before its IDCODE and flash JEDEC are checked.
# SPDX-License-Identifier: BSD-3-Clause

"""apollo#15 (flash_program checks no part IDs) and cynthion#185 (device string
vs the packed bitstream), as they apply to this fork.

  * `soc_confirm.prewrite_verdict` / `part_gate` refuse a wrong die, a wrong
    flash, an unreadable ID and a `.bit` that disagrees with its build record.
  * `usercode_map.bitstream_idcode` reads what `ecppack` really packed.

Every refusal has its control: the same inputs with the IDs agreeing proceed.
Nothing here touches a board; the IDs are injected. UNVERIFIED on hardware: that
`read_flash_jedec` returns 0xef4016 on the real W25Q32 (separate-session reads
follow `flash_backup.py`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

import soc_confirm  # noqa: E402
import usercode_map  # noqa: E402

IDCODE_12F = 0x2111_1043
IDCODE_25F = 0x4111_1043
W25Q32 = 0xEF4016


def make_bit(path, idcode, part="LFE5U-12F-8CABGA256"):
    """The head of an ecppack `.bit`: comment, preamble, VERIFY_ID."""
    head = (b"\xff\x00Part: " + part.encode() + b"\x00\xff\xff\xff\xbd\xb3"
            b"\xff\xff\xff\xff;\x00\x00\x00\xe2\x00\x00\x00"
            + idcode.to_bytes(4, "big") + b"\x22\x00\x00\x00" + b"\x00" * 64)
    path.write_bytes(head)
    return path


def build(tmp_path, packed=IDCODE_12F, record_idcode=IDCODE_12F):
    bit = make_bit(tmp_path / "top.bit", packed)
    (tmp_path / usercode_map.RECORD_NAME).write_text(json.dumps(
        {"device": "LFE5U-12F", "idcode": record_idcode, "usercode": 1}))
    return bit


def gate(bit, board=IDCODE_12F, jedec=W25Q32, flash=False):
    return soc_confirm.part_gate(bit, flash=flash, read_idcode=lambda: board,
                                 read_jedec=lambda: jedec)


def test_agreeing_ids_proceed(tmp_path):
    assert gate(build(tmp_path), flash=True) is None


def test_bitstream_idcode_is_read_from_the_file(tmp_path):
    assert usercode_map.bitstream_idcode(make_bit(tmp_path / "a.bit", IDCODE_12F)) == IDCODE_12F
    assert usercode_map.bitstream_idcode(make_bit(tmp_path / "b.bit", IDCODE_25F)) == IDCODE_25F
    assert usercode_map.bitstream_part(tmp_path / "a.bit") == "LFE5U-12F-8CABGA256"


def test_wrong_die_is_refused_before_a_write(tmp_path):
    verdict = gate(build(tmp_path), board=IDCODE_25F)
    assert verdict is not None and verdict.name == "wrong-part"


def test_unreadable_idcode_is_refused_not_assumed(tmp_path):
    for unread in (None, 0, 0xFFFFFFFF):
        verdict = gate(build(tmp_path), board=unread)
        assert verdict is not None and verdict.name == "part-unverified"


def test_wrong_flash_is_refused_only_when_flash_is_written(tmp_path):
    bit = build(tmp_path)
    assert gate(bit, jedec=0xEF7016, flash=True).name == "wrong-flash"
    assert gate(bit, jedec=None, flash=True).name == "wrong-flash"
    # Control: a configure does not touch the flash, so its JEDEC is not asked.
    assert gate(bit, jedec=0xEF7016, flash=False) is None


def test_bitstream_packed_for_another_die_than_its_record_is_refused(tmp_path):
    """cynthion#185: the device string and the packed `.bit` must agree."""
    bit = build(tmp_path, packed=IDCODE_25F, record_idcode=IDCODE_12F)
    problems = usercode_map.bitstream_problems(
        bit, usercode_map.read_build_record(tmp_path))
    assert problems and "packed for IDCODE 41111043" in problems[0]
    verdict = gate(bit, board=IDCODE_25F)
    assert verdict is not None and verdict.name == "bitstream-mismatch"


def test_matching_bitstream_and_record_have_no_problems(tmp_path):
    bit = build(tmp_path)
    assert usercode_map.bitstream_problems(
        bit, usercode_map.read_build_record(tmp_path)) == []


def test_part_comment_must_match_the_record_device(tmp_path):
    bit = make_bit(tmp_path / "top.bit", IDCODE_12F, part="LFE5U-25F-8CABGA256")
    problems = usercode_map.bitstream_problems(
        bit, {"device": "LFE5U-12F", "idcode": IDCODE_12F})
    assert any("Part: LFE5U-25F" in line for line in problems)


def test_no_record_falls_back_to_the_bitstream_own_idcode(tmp_path):
    bit = make_bit(tmp_path / "top.bit", IDCODE_12F)
    assert gate(bit) is None
    assert gate(bit, board=IDCODE_25F).name == "wrong-part"


def test_the_real_build_record_matches_prjtrellis():
    """The die on this board answers 0x21111043 = prjtrellis' LFE5U-12F."""
    if usercode_map.trellis_devices() is None:
        pytest.skip("prjtrellis database not installed")
    assert usercode_map.idcode_for("LFE5U-12F") == IDCODE_12F
    assert usercode_map.idcode_for("LFE5U-25F") == IDCODE_25F


def test_the_flash_write_path_calls_the_gate_before_the_programmer():
    text = (ROOT / "scripts" / "soc_run.py").read_text()
    body = text[text.index("def write_rodata_flash"):text.index("def configure_and_read")]
    assert body.index("part_gate(BITSTREAM, flash=True)") < body.index('"flash-program"')
