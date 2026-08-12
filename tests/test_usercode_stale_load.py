#!/usr/bin/env python3
#
# A stale load must be caught, and the checker must be seen to fail. #447, #450.
# SPDX-License-Identifier: BSD-3-Clause

"""Does the host notice it is talking to the wrong bitstream, or the wrong part?

The identity is the ECP5's USERCODE, which JTAG reads and the fabric cannot, so
the check belongs to the host that did the loading. What is asserted here:

  * two builds produce two records, and a lookup resolves each to its own build
  * the part answering with build A's USERCODE while build B was loaded is
    `stale-bitstream`
  * a part whose IDCODE is not the one the build was packed for is `wrong-part`
  * a record declaring a clock the PLL cannot reach is `declared-clock`
  * the JTAG read path decodes what the programmer hands it

Every one has its control in the same test: the same evidence with the identity
AGREEING must not produce the verdict. A checker that fires on everything is
worth as little as one that fires on nothing, and this repo has found twelve
instruments that could not fail.

UNTESTED HERE, for want of a board: that `_read_usercode()` returns what ecppack
stamped. The bytes-to-int decode is exercised with an injected programmer; the
JTAG transaction itself is not.
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

# As read off this board: an LFE5U-12F answering prjtrellis' 12F IDCODE.
ECP5_IDCODE = 0x2111_1043
OTHER_IDCODE = 0x4111_1043

STATUS_RUNNING = 0x0020_0100


def record(usercode, **overrides):
    """A build record with everything the checks read, and nothing else."""
    row = {
        "usercode": usercode,
        "usercode_hex": f"{usercode:#010x}",
        "commit": f"{usercode:040x}",
        "dirty": False,
        "variant": "bist0-ck160-dqs1-merge0-sync60",
        "source_digest": "0" * 64,
        "gateware_digest": "0" * 16,
        "build_dir": "tmp/awto_soc/build/bist0-ck160-dqs1-merge0-sync60",
        "device": "LFE5U-12F",
        "speed": "8",
        "idcode": ECP5_IDCODE,
        "sync_hz": 60_000_000,
        "usb_hz": 60_000_000,
        "cache_sets": 64,
        "cache_ways": 2,
        "isa": {"m": True, "a": True, "c": True, "rdtime": True},
        "built_at": "2026-08-12T13:00:00+10:00",
    }
    row.update(overrides)
    return row


def verdict(**evidence):
    return soc_confirm.diagnose(soc_confirm.Evidence(**evidence))


# A board that is answering everything correctly apart from its identity. Every
# test below starts here, so a failing verdict can only come from the identity.
HEALTHY = dict(apollo=True, console_usb=True, console_node="/dev/ttyACM4",
               reply=b"\r\n> ", idcode=ECP5_IDCODE, status=STATUS_RUNNING)


# ---------------------------------------------------------------------------
# The stale load
# ---------------------------------------------------------------------------


def test_the_control_first_a_matching_usercode_passes():
    """Without this, every assertion below is satisfied by a checker that
    always fails."""
    assert verdict(**HEALTHY, usercode=0xAAAA_1111,
                   expected=record(0xAAAA_1111)).name == "ok"


def test_a_stale_bitstream_is_caught_even_though_the_shell_answers():
    """Build A configured, build B's identity expected.

    The shell answers -- a wrong design answers just as well -- so this is the
    case that passed before the check existed.
    """
    result = verdict(**HEALTHY, usercode=0xAAAA_1111,
                     expected=record(0xBBBB_2222))
    assert result.name == "stale-bitstream"
    assert "aaaa1111" in " ".join(result.lines)
    assert "bbbb2222" in " ".join(result.lines)


def test_no_record_leaves_the_question_undecided_rather_than_passed():
    """A build directory with no record must not read as agreement."""
    assert verdict(**HEALTHY, usercode=0xAAAA_1111, expected=None).name == "ok"
    assert soc_confirm.from_identity(
        soc_confirm.Evidence(**HEALTHY, usercode=0xAAAA_1111)) is None


def test_two_variants_two_records_and_the_lookup_tells_them_apart(tmp_path):
    """The mapping file: build two, configure one, check against the other."""
    index = tmp_path / usercode_map.INDEX
    first = record(0xAAAA_1111, variant="bist0-sync60",
                   build_dir="tmp/awto_soc/build/bist0-sync60")
    second = record(0xBBBB_2222, variant="bist1-sync72",
                    build_dir="tmp/awto_soc/build/bist1-sync72")
    for row, where in ((first, tmp_path / "a"), (second, tmp_path / "b")):
        usercode_map.write(row, where, root=tmp_path)

    table = json.loads(index.read_text())
    assert set(table) == {"0xaaaa1111", "0xbbbb2222"}
    assert usercode_map.lookup(0xAAAA_1111, root=tmp_path)["variant"] == "bist0-sync60"
    assert usercode_map.lookup(0xBBBB_2222, root=tmp_path)["variant"] == "bist1-sync72"
    # A code from neither build resolves to nothing rather than to the last row.
    assert usercode_map.lookup(0xCCCC_3333, root=tmp_path) is None

    # And the record travels with its own bitstream, which is what survives a
    # bitcache restore into another directory.
    assert usercode_map.read_build_record(tmp_path / "a")["usercode"] == 0xAAAA_1111
    assert usercode_map.read_build_record(tmp_path / "b")["usercode"] == 0xBBBB_2222
    assert usercode_map.read_build_record(tmp_path / "c") is None


# ---------------------------------------------------------------------------
# The part itself
# ---------------------------------------------------------------------------


def test_a_different_die_is_its_own_verdict_and_outranks_the_bitstream():
    """A wrong part makes every number from the board about another device."""
    result = verdict(**{**HEALTHY, "idcode": OTHER_IDCODE},
                     usercode=0xAAAA_1111, expected=record(0xAAAA_1111))
    assert result.name == "wrong-part"
    assert f"{OTHER_IDCODE:08x}" in " ".join(result.lines)

    # It is decided BEFORE staleness: a bitstream packed for another die is not
    # repaired by rebuilding at this commit.
    both_wrong = verdict(**{**HEALTHY, "idcode": OTHER_IDCODE},
                         usercode=0xAAAA_1111, expected=record(0xBBBB_2222))
    assert both_wrong.name == "wrong-part"


def test_an_unread_idcode_does_not_manufacture_a_wrong_part():
    """`ffffffff` is JTAG contention, which the existing verdict already names."""
    for unread in (None, 0, 0xFFFF_FFFF):
        result = verdict(**{**HEALTHY, "idcode": unread},
                         usercode=0xAAAA_1111, expected=record(0xAAAA_1111))
        assert result.name != "wrong-part"


def test_the_expected_idcode_comes_from_prjtrellis_not_from_this_repo():
    """The platform's device string, resolved through the packer's own table."""
    table = usercode_map.trellis_devices()
    if table is None:
        pytest.skip("no prjtrellis database installed")
    assert usercode_map.idcode_for("LFE5U-12F") == ECP5_IDCODE
    assert usercode_map.idcode_for("LFE5U-25F") == OTHER_IDCODE
    assert usercode_map.idcode_for("LFE5U-NOT-A-PART") is None


# ---------------------------------------------------------------------------
# What the build declared, against what the part can do
# ---------------------------------------------------------------------------


def test_a_clock_the_pll_cannot_reach_is_reported():
    """The control is the shipping build, which must produce nothing."""
    assert usercode_map.clock_problems(record(1)) == []

    above_ceiling = usercode_map.clock_problems(record(1, sync_hz=200_000_000))
    assert above_ceiling and any("above" in line for line in above_ceiling)

    # 61 MHz: inside the ceiling, and no CLKI/CLKFB pair from 60 MHz lands on it
    # with the fPFD floor in the way.
    unreachable = usercode_map.clock_problems(record(1, sync_hz=61_000_000))
    assert unreachable and any("no EHXPLLL" in line for line in unreachable)

    wrong_usb = usercode_map.clock_problems(record(1, usb_hz=48_000_000))
    assert wrong_usb and any("ULPI" in line for line in wrong_usb)


def test_a_declared_clock_the_part_cannot_deliver_is_its_own_verdict():
    result = verdict(**HEALTHY, usercode=0xAAAA_1111,
                     expected=record(0xAAAA_1111, sync_hz=200_000_000))
    assert result.name == "declared-clock"
    # And the control: the same board with a legal declaration answers `ok`.
    assert verdict(**HEALTHY, usercode=0xAAAA_1111,
                   expected=record(0xAAAA_1111)).name == "ok"


# ---------------------------------------------------------------------------
# The read path
# ---------------------------------------------------------------------------


class FakeChain:
    def __init__(self, idcodes):
        self._idcodes = idcodes

    def enumerate(self, return_idcodes=False):
        return self._idcodes


class FakeProgrammer:
    def __init__(self, usercode):
        self._usercode = usercode

    def _read_usercode(self):
        return self._usercode


class FakeDebugger:
    """Apollo's shape, with the two calls `read_identity` makes."""

    def __init__(self, idcode, usercode):
        self._chain = FakeChain([idcode])
        self._usercode = usercode
        self.closed = False

    @property
    def jtag(self):
        debugger = self

        class Context:
            def __enter__(self):
                return debugger._chain

            def __exit__(self, *exc):
                return False

        return Context()

    def create_jtag_programmer(self, chain):
        assert chain is self._chain
        return FakeProgrammer(self._usercode)

    def close(self):
        self.closed = True


def test_the_read_path_returns_what_the_programmer_reports():
    from soc_usercode import read_identity

    assert read_identity(FakeDebugger(ECP5_IDCODE, 0xDEAD_BEEF)) == (
        ECP5_IDCODE, 0xDEAD_BEEF)


def test_a_part_that_will_not_answer_reports_no_usercode():
    """`ffffffff` must not be decoded as a build identity."""
    from soc_usercode import read_identity

    assert read_identity(FakeDebugger(0xFFFF_FFFF, 0x1234)) == (0xFFFF_FFFF, None)
    assert read_identity(FakeDebugger(0, 0x1234)) == (0, None)
