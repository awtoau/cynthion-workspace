#!/usr/bin/env python3
#
# Every JTAG session ends its start-up in Test-Logic-Reset. apollo#29.
# SPDX-License-Identifier: BSD-3-Clause

"""`jtag_init()` only *assumes* the TAP is in Test-Logic-Reset (apollo#29), so
the host must force it. `JTAGChain.__enter__` is the only sender of
`REQUEST_JTAG_START` in this workspace; this pins that it is followed, before
anything else, by `REQUEST_JTAG_GO_TO_STATE(RESET)`, which the firmware turns
into five TMS=1 clocks unconditionally (`jtag_tap.c`, `jtag_go_to_state`).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

from apollo_fpga import jtag  # noqa: E402


class FakeDebugger:
    def __init__(self):
        self.calls = []

    def in_request(self, request, length=0, **_):
        self.calls.append(("in", request))
        return bytes(16)

    def out_request(self, request, value=0, **_):
        self.calls.append(("out", request, value))


def test_enter_resets_the_tap_straight_after_start():
    dbg = FakeDebugger()
    jtag.JTAGChain(dbg).__enter__()
    outs = [c for c in dbg.calls if c[0] == "out"]
    assert outs[0][1] == jtag.REQUEST_JTAG_START
    assert outs[1][1:] == (jtag.REQUEST_JTAG_GO_TO_STATE,
                           jtag.JTAGChain.STATE_NUMBERS["RESET"])


def test_nothing_else_in_the_workspace_sends_jtag_start():
    """A second sender would skip the reset; the check can fail if one appears."""
    senders = []
    for base in (ROOT / "scripts", ROOT / "repos" / "apollo" / "apollo_fpga"):
        for path in base.rglob("*.py"):
            text = path.read_text()
            if "REQUEST_JTAG_START" in text and "out_request" in text:
                senders.append(path.name)
    assert senders == ["jtag.py"]
