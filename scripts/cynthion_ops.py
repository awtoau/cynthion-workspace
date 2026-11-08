#!/usr/bin/env python3
#
# Host client for the SoC's binary op protocol: framing, ids, timeouts.
# SPDX-License-Identifier: BSD-3-Clause

"""
Request/response client for [`docs/device-interface.md`](../docs/device-interface.md)
(#303). Not the simulated shell: this is the layer it will sit on.

    ops = OpsClient(transport)          # any object with write() and read(timeout)
    ops.enter()                         # text `ops`, wait for SESSION
    rsp = ops.call("power.read")        # Response; raises OpError unless status ok
    ops.leave()                         # ETX, wait for the board to confirm

What it owns:

- request ids (incrementing u32), matched on the echo, so a late reply to an
  earlier request is counted and dropped, never mistaken for the current one;
- the per-op timeout, `soc_ops.limit_ms`: 1.25 x the board's worst case, 50 ms
  floor. Expiry raises `OpTimeout` naming op, limit and elapsed. Nothing retries;
- the keepalive: `hold(seconds)` pings every `KEEPALIVE_MS`, which is what keeps
  the board's 2500 ms watchdog from ending the session;
- pushed records (TEXT, DROP, ERROR) land in `events`.

Units are the board's. This file converts nothing (#270).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import soc_ops as ops  # noqa: E402
import soc_stream as st  # noqa: E402

# Waiting for SESSION after `ops\r`: the board answers after the editor prints its
# prompt, one console turn (well under 10 ms) plus a USB CDC round trip. 250 ms is
# 5x the 50 ms op floor; expiry means the board is not in text mode on this console.
ENTER_LIMIT_MS = 250
# ETX to the ERROR(left) frame: one console turn plus a round trip, the op floor.
LEAVE_LIMIT_MS = ops.LIMIT_FLOOR_MS


class OpTimeout(TimeoutError):
    """No response inside the op's limit."""


class OpsClient:
    def __init__(self, transport, *, clock=time.monotonic):
        self.transport = transport
        self.clock = clock
        self.decoder = st.Decoder()
        self.events: list[st.Record] = []
        self.session: st.Record | None = None
        self.stale = 0              # responses whose id nobody was waiting for
        self._next_id = 1
        self._last_tx = clock()
        self._queue: list[st.Record] = []   # decoded, not yet looked at

    # -- plumbing --------------------------------------------------------

    def _write(self, data: bytes) -> None:
        self.transport.write(data)
        self._last_tx = self.clock()

    def _wait(self, limit_ms: float, want) -> st.Record | None:
        """Read until `want(record)` holds or `limit_ms` passes. Others go to events."""
        deadline = self.clock() + limit_ms / 1000
        while True:
            while self._queue:
                record = self._queue.pop(0)
                if want(record):
                    return record
                self._file(record)
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            self._queue.extend(self.decoder.feed(self.transport.read(remaining)))

    def _file(self, record: st.Record) -> None:
        if record.kind == st.RSP:
            self.stale += 1
        else:
            self.events.append(record)

    # -- session ---------------------------------------------------------

    def enter(self) -> st.Record:
        """`ops\\r` in text mode; returns the SESSION record."""
        self._write(ops.ENTER)
        record = self._wait(ENTER_LIMIT_MS, lambda r: r.kind == st.SESSION)
        if record is None:
            raise OpTimeout(f"enter: no SESSION frame in {ENTER_LIMIT_MS} ms "
                            f"(is the console in text mode?)")
        self.session = record
        return record

    def leave(self) -> None:
        """ETX; waits for the board's ERROR(left, host)."""
        self._write(ops.LEAVE)
        record = self._wait(LEAVE_LIMIT_MS, lambda r: r.kind == st.ERROR)
        if record is None:
            raise OpTimeout(f"leave: no confirmation in {LEAVE_LIMIT_MS} ms")
        code, value = st.ERROR_S.unpack(record.body)
        if (code, value) != (ops.ERR_LEFT, ops.LEAVE_HOST):
            raise OpTimeout(f"leave: expected left/host, got code {code} value {value}")

    # -- ops -------------------------------------------------------------

    def call(self, op, *args: int, raise_on_error: bool = True) -> ops.Response:
        op = ops._op(op)
        req_id = self._next_id
        self._next_id = (self._next_id + 1) & 0xFFFFFFFF
        limit = ops.limit_ms(op)
        start = self.clock()
        self._write(ops.encode_request(op, req_id, *args))
        record = self._wait(limit, lambda r: r.kind == st.RSP and r.seq == req_id)
        if record is None:
            raise OpTimeout(f"{op.name}: no response in {limit} ms "
                            f"(waited {(self.clock() - start) * 1000:.0f} ms, id {req_id})")
        rsp = ops.decode_response(record)
        if raise_on_error and not rsp.ok:
            raise ops.OpError(op.name, rsp.status, rsp.reason, rsp.value)
        return rsp

    def hold(self, seconds: float) -> None:
        """Keep the session alive for `seconds` with a ping every KEEPALIVE_MS."""
        end = self.clock() + seconds
        while self.clock() < end:
            if (self.clock() - self._last_tx) * 1000 >= ops.KEEPALIVE_MS:
                self.call("sys.ping", 0)
            self._queue.extend(self.decoder.feed(self.transport.read(0.01)))
            while self._queue:
                self._file(self._queue.pop(0))

    def capabilities(self) -> tuple[ops.Response, list[int]]:
        """Supported opcodes, every page."""
        listed: list[int] = []
        while True:
            rsp = self.call("sys.capabilities", len(listed))
            listed += rsp.tail
            if len(listed) >= rsp.fields["total"] or not rsp.tail:
                return rsp, listed


class LinkTransport:
    """`soc_shell.Link` as a transport."""

    def __init__(self, link):
        self.link = link

    def write(self, data: bytes) -> None:
        self.link.write(data)

    def read(self, timeout: float) -> bytes:
        return self.link.read_available() or b""
