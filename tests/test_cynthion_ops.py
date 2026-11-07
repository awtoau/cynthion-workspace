#!/usr/bin/env python3
#
# The op client against a scripted board, on a simulated clock. See #303.
# SPDX-License-Identifier: BSD-3-Clause

"""Nothing here sleeps: the clock is a number the fake transport advances."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
import cynthion_ops as co  # noqa: E402
import pytest  # noqa: E402
import soc_ops as ops  # noqa: E402
import soc_stream as st  # noqa: E402


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Board:
    """A transport whose far end answers requests from a script.

    `answer(request) -> list of frames` (empty list: the board says nothing).
    """

    def __init__(self, clock: Clock, answer=None):
        self.clock = clock
        self.answer = answer or self.echo
        self.rx = st.Decoder()
        self.pending = b""
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)
        if data == ops.ENTER:
            self.pending += st.encode_record(
                st.SESSION, 0, 1, st.session_body(kinds=0, firmware_git=1, gateware_git=0,
                                                  time_hz=60_000_000, session_id=9),
                flags=st.FLAG_RESYNC)
        elif data == ops.LEAVE:
            self.pending += st.encode_record(st.ERROR, 1, 2, st.ERROR_S.pack(
                ops.ERR_LEFT, ops.LEAVE_HOST))
        for record in self.rx.feed(data):
            if record.kind == ops.KIND_REQ:
                self.pending += b"".join(self.answer(ops.decode_request(record)))

    def read(self, timeout: float) -> bytes:
        if self.pending:
            data, self.pending = self.pending, b""
            return data
        self.clock.now += timeout
        return b""

    @staticmethod
    def echo(request: ops.Request) -> list[bytes]:
        op = ops.BY_CODE[request.op_code]
        if op.name == "sys.ping":
            return [ops.encode_response(op, request.req_id, 7, request.args["nonce"])]
        if op.name == "sys.capabilities":
            first = request.args["first"]
            codes = [o.code for o in ops.OPS][first:first + 5]
            return [ops.encode_response(op, request.req_id, 7, 1, 64, 128, 3, len(ops.OPS),
                                        len(codes), tail=codes)]
        return [ops.encode_failure(request.op_code, request.req_id, 7, ops.ST_REFUSED, 3, 3980)]


def client(answer=None):
    clock = Clock()
    clock.now = 100.0
    board = Board(clock, answer)
    return co.OpsClient(board, clock=clock), board, clock


def test_enter_waits_for_session_and_keeps_it():
    c, board, _ = client()
    record = c.enter()
    assert record.name == "SESSION" and c.session is record
    assert board.sent[0] == b"ops\r"


def test_enter_times_out_naming_what_it_waited_for():
    c, board, clock = client()
    board.write = lambda data: None
    with pytest.raises(co.OpTimeout, match="no SESSION frame in 250 ms"):
        c.enter()
    assert clock.now - 100.0 >= 0.25 - 1e-9


def test_a_call_returns_the_decoded_response():
    c, _, _ = client()
    rsp = c.call("sys.ping", 0xDEADBEEF)
    assert rsp.fields == {"nonce": 0xDEADBEEF} and rsp.req_id == 1


def test_request_ids_increment_and_responses_are_matched_on_them():
    c, board, _ = client()
    assert [c.call("sys.ping", n).req_id for n in (1, 2, 3)] == [1, 2, 3]


def test_a_failure_status_raises_with_reason_and_value():
    c, _, _ = client()
    with pytest.raises(ops.OpError) as error:
        c.call("vbus.set", 3, ops.VBUS_SOURCE, 0)
    assert (error.value.status, error.value.reason, error.value.value) == \
        (ops.ST_REFUSED, 3, 3980)
    # and can be asked not to
    assert c.call("vbus.set", 3, ops.VBUS_SOURCE, 0, raise_on_error=False).reason == 3


def test_a_silent_board_times_out_at_the_op_limit_not_before_and_not_long_after():
    c, _, clock = client(answer=lambda request: [])
    with pytest.raises(co.OpTimeout, match=r"vbus\.set: no response in 50 ms"):
        c.call("vbus.set", 1, ops.VBUS_OFF, 0)
    waited = clock.now - 100.0
    assert 0.050 - 1e-9 <= waited <= 0.050 + 1e-6, waited


def test_selftest_gets_its_own_longer_limit():
    assert ops.limit_ms("selftest.run") == 250
    assert ops.limit_ms("power.read") == ops.LIMIT_FLOOR_MS == 50
    assert all(ops.limit_ms(op) >= ops.LIMIT_FLOOR_MS for op in ops.OPS)
    # 1.25 x the worst case, rounded up
    assert ops.limit_ms("selftest.run") == -(-ops.WORST_MS["selftest.run"] * 5 // 4)


def test_a_late_reply_to_an_earlier_request_is_dropped_not_mistaken():
    late: list[bytes] = []

    def answer(request):
        if request.args.get("nonce") == 1:
            late.extend(Board.echo(request))      # held back past the timeout
            return []
        return Board.echo(request) + late

    c, board, _ = client(answer)
    with pytest.raises(co.OpTimeout):
        c.call("sys.ping", 1)
    rsp = c.call("sys.ping", 2)
    assert rsp.fields["nonce"] == 2 and rsp.req_id == 2
    assert c.call("sys.ping", 3).fields["nonce"] == 3
    assert c.stale == 1


def test_pushed_records_are_kept_and_do_not_disturb_the_wait():
    def answer(request):
        text = st.encode_record(st.TEXT, 5, 1, b"hello")
        return [text] + Board.echo(request)

    c, _, _ = client(answer)
    assert c.call("sys.ping", 4).fields["nonce"] == 4
    assert [e.fields["text"] for e in c.events] == ["hello"]


def test_leave_requires_the_board_to_confirm():
    c, board, _ = client()
    c.leave()
    assert board.sent[-1] == b"\x03"
    c2, board2, _ = client()
    board2.write = lambda data: None
    with pytest.raises(co.OpTimeout, match="leave: no confirmation"):
        c2.leave()


def test_hold_pings_inside_the_watchdog_window():
    c, board, clock = client()
    c.hold(10.0)
    pings = [d for d in board.sent if d != b""]
    times = len(pings)
    # 10 s at one ping per 2 s
    assert 4 <= times <= 6, times
    assert ops.KEEPALIVE_MS < ops.WATCHDOG_MS


def test_a_ping_cadence_gap_never_exceeds_the_watchdog():
    """Cannot-fail proof: the cadence is what protects the session, so measure it."""
    sent_at: list[float] = []

    class Spy(Board):
        def write(self, data):
            sent_at.append(self.clock.now)
            super().write(data)

    clock = Clock()
    board = Spy(clock)
    c = co.OpsClient(board, clock=clock)
    c.hold(20.0)
    gaps = [b - a for a, b in zip(sent_at, sent_at[1:])]
    assert gaps and max(gaps) <= ops.WATCHDOG_MS / 1000
    assert max(gaps) >= ops.KEEPALIVE_MS / 1000 - 0.02


def test_capabilities_walks_every_page():
    c, _, _ = client()
    rsp, listed = c.capabilities()
    assert listed == [o.code for o in ops.OPS]
