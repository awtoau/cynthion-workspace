#!/usr/bin/env python3
#
# QEMU gate for the binary op protocol: enter, run ops, malformed input, leave.
# SPDX-License-Identifier: BSD-3-Clause

"""
Called by `scripts/soc_test.py`, which owns the QEMU session class and the image.
Its own emulator, never the main suite's: binary frames carry bare LF bytes and
the main suite asserts none reach the wire.

What it proves, against the shipping source (QEMU build, no board):

- enter with `ops`, get SESSION, run every op in the table;
- ops that need hardware say `unavailable` rather than inventing data;
- malformed, overlong, truncated, bad-CRC, bad-escape, wrong-version, wrong-kind
  and unknown-opcode input each produce the documented ERROR/RSP and leave the
  session usable;
- ETX returns to the text shell, and so does a stuck host (the watchdog).

Not covered here: async text diverted into TEXT records (QEMU has nothing that
logs unprompted), and anything that needs a board.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import cynthion_ops as co  # noqa: E402
import soc_ops as ops  # noqa: E402
import soc_stream as st  # noqa: E402

# The board leaves binary mode WATCHDOG_MS after the last byte. QEMU adds its own
# scheduling on top of the guest's 1 ms tick; 1.5 x covers a loaded host without
# letting a watchdog that never fires wait out a minute.
WATCHDOG_BUDGET_S = ops.WATCHDOG_MS * 1.5 / 1000
# Silence short of the watchdog must NOT end the session: 80% of it.
QUIET_S = ops.WATCHDOG_MS * 0.8 / 1000


class SessionTransport:
    """`soc_test.Session` as a transport: new bytes since the last read."""

    def __init__(self, session):
        self.session = session
        self.at = len(session.buf)

    def write(self, data: bytes) -> None:
        self.session.send(data)

    def read(self, timeout: float) -> bytes:
        s = self.session
        with s.cond:
            if len(s.buf) == self.at and not s.closed:
                s.cond.wait(timeout)
            data = bytes(s.buf[self.at:])
            self.at = len(s.buf)
        return data


def quiet(session, seconds: float) -> None:
    """Block for `seconds` of silence. The needle cannot occur, so this returns
    at the deadline, or early only if the board speaks (and then the caller's
    next check fails on what arrived)."""
    session.expect(b"\xff" * 64, seconds, len(session.buf))


def build(kind, verflags, seq, body) -> bytes:
    """A frame with a valid CRC, from raw fields (for the malformed cases)."""
    payload = struct.pack("<BBHII", kind, verflags, len(body), seq, 0) + body
    payload += struct.pack("<I", st.crc32(payload))
    return bytes((st.END,)) + st.slip_escape(payload) + bytes((st.END,))


def run(check, Session, elf, boot_s, prompt, expect_line, show) -> None:
    session = Session(elf)
    try:
        if session.expect(b"Cynthion RISC-V SoC", boot_s) is None:
            check("ops gate: the firmware reached the console", False,
                  show(session.snapshot()))
            return
        _functional(check, session, prompt, show)
    finally:
        session.close()


def _errors(client, code):
    return [e for e in client.events
            if e.kind == st.ERROR and st.ERROR_S.unpack(e.body)[0] == code]


def _functional(check, session, prompt, show) -> None:
    client = co.OpsClient(SessionTransport(session))

    # --- enter ---------------------------------------------------------
    try:
        record = client.enter()
    except co.OpTimeout as error:
        check("ops: `ops` answers with a SESSION frame", False,
              f"{error}\nreceived: {show(session.snapshot())}")
        return
    check("ops: `ops` answers with a SESSION frame, RESYNC set",
          record.resync and record.fields.get("format_version") == 1,
          str(record))
    check("ops: the text prompt before it was discarded, not parsed",
          client.decoder.counters.discarded_bytes > 0 and client.decoder.counters.clean(),
          "; ".join(client.decoder.summary()))

    # --- every op ------------------------------------------------------
    def call(name, *args):
        return client.call(name, *args, raise_on_error=False)

    rsp = call("sys.ping", 0xC0DB_C0DB)
    check("ops: ping echoes a nonce full of delimiter bytes",
          rsp.ok and rsp.fields["nonce"] == 0xC0DB_C0DB, str(rsp))

    caps, listed = client.capabilities()
    check("ops: capabilities lists exactly the table's opcodes",
          listed == [op.code for op in ops.OPS]
          and caps.fields["proto"] == ops.PROTO_VERSION
          and (caps.fields["max_req_body"], caps.fields["max_rsp_body"])
          == (ops.MAX_REQ_BODY, ops.MAX_RSP_BODY), str(caps.fields))
    check("ops: QEMU reports no board and no HyperRAM",
          caps.fields["features"] == 0, f"features {caps.fields['features']:#x}")

    info = call("sys.info")
    check("ops: info is this build on console 0",
          info.ok and info.fields["console"] == 0 and info.fields["consoles"] >= 1
          and info.fields["time_hz"] > 0 and info.fields["uptime_ms"] > 0, str(info.fields))

    for name, args in [("power.read", ()), ("led.get", ()), ("led.set", (3, ops.LED_ON)),
                       ("typec.status", ()), ("hr.status", ()),
                       ("vbus.set", (1, ops.VBUS_ON, 0)),
                       ("vbus.set", (3, ops.VBUS_SOURCE, 0))]:
        rsp = call(name, *args)
        check(f"ops: {name} {args} is unavailable (no board), with reason no_board",
              rsp.status == ops.ST_UNAVAILABLE and rsp.reason == 1, str(rsp))

    rsp = call("power.limits", 1)
    check("ops: power.limits reads the cache with nothing armed",
          rsp.ok and rsp.fields["armed"] == 0 and rsp.fields["fired"] == 0, str(rsp.fields))

    rsp = call("vbus.status")
    check("ops: vbus.status: every switch open",
          rsp.ok and rsp.fields["closed"] == 0 and rsp.fields["powered"] == 0, str(rsp.fields))
    rsp = call("vbus.set", 0, ops.VBUS_OPEN_ALL, 0)
    check("ops: vbus.set open-all succeeds and names no source",
          rsp.ok and rsp.fields["source"] == ops.VBUS_NONE, str(rsp.fields))
    rsp = call("vbus.set", 3, ops.VBUS_OFF, 0)
    check("ops: vbus.set off on an input port is refused as not_output",
          rsp.status == ops.ST_REFUSED and rsp.reason == 7, str(rsp))

    rsp = call("pd.status")
    check("ops: pd.status: not armed, no contract",
          rsp.ok and rsp.fields["armed"] == 0 and rsp.fields["contract_pos"] == 0,
          str(rsp.fields))

    rsp = call("usb.port", 0)
    check("ops: usb.port target_a: no sample, no switch, no cable",
          rsp.ok and rsp.fields["sample"] == 0 and rsp.fields["switch"] == 0
          and rsp.fields["cable"] == 0, str(rsp.fields))
    rsp = call("usb.port", 3)
    check("ops: usb.port control: switch open",
          rsp.ok and rsp.fields["switch"] == 1, str(rsp.fields))

    rsp = call("selftest.run")
    f = rsp.fields
    check("ops: selftest.run ran, nothing failed, items add up",
          rsp.ok and f["failed"] == 0 and f["passed"] > 0
          and f["n"] == len(rsp.tail) == f["passed"] + f["failed"] + f["skipped"]
          and all(i < len(ops.SELFTEST_ITEMS) and o in ops.SELFTEST_OUTCOME
                  for i, o in rsp.tail), str(f))

    # --- bad requests --------------------------------------------------
    body_short = st.encode_record(ops.KIND_REQ, 901, 0, ops.REQ_HEAD.pack(0x0001) + b"\x00")
    client._write(body_short)
    r = client._wait(200, lambda r: r.kind == st.RSP and r.seq == 901)
    check("ops: wrong argument length -> bad_args",
          r is not None and ops.decode_response(r).status == ops.ST_BAD_ARGS, str(r))
    client._write(st.encode_record(ops.KIND_REQ, 902, 0, ops.REQ_HEAD.pack(0x7FFF)))
    r = client._wait(200, lambda r: r.kind == st.RSP and r.seq == 902)
    check("ops: unknown opcode -> unknown_op with the code echoed",
          r is not None and ops.decode_response(r).status == ops.ST_UNKNOWN_OP
          and ops.decode_response(r).op_code == 0x7FFF, str(r))
    rsp = call("power.limits", 4)
    check("ops: power.limits channel 4 -> bad_args", rsp.status == ops.ST_BAD_ARGS, str(rsp))
    rsp = call("vbus.set", 1, ops.VBUS_ON, 1234)
    check("ops: vbus.set with a current that is no Rp level -> bad_args",
          rsp.status == ops.ST_BAD_ARGS, str(rsp))

    # --- malformed frames: one ERROR each, no response, session still alive
    good = ops.encode_request("sys.ping", 950, 1)
    corrupt = bytearray(good)
    corrupt[6] ^= 0x10
    cases = {
        "bad CRC": (bytes(corrupt), ops.ERR_CRC),
        "runt": (bytes((st.END, 0x08, 0x10, st.END)), ops.ERR_RUNT),
        "bad escape": (bytes((st.END, 0x08, st.ESC, 0x00, 0x01, st.END)), ops.ERR_ESCAPE),
        "length lie": (bytes(good[:3]) + b"\xff" + bytes(good[4:]), None),
        "future version": (build(ops.KIND_REQ, 2 << 4, 951, ops.REQ_HEAD.pack(1) + b"\0" * 4),
                           ops.ERR_VERSION),
        "wrong kind": (build(ops.KIND_RSP, st.VERSION << 4, 952, ops.REQ_HEAD.pack(1) + b"\0" * 4),
                       ops.ERR_KIND),
        "no opcode": (build(ops.KIND_REQ, st.VERSION << 4, 953, b"\x01"), ops.ERR_SHORT_REQ),
        "overlong": (build(ops.KIND_REQ, st.VERSION << 4, 954, b"\x00" * (ops.MAX_REQ_BODY + 1)),
                     ops.ERR_OVERFLOW),
        "truncated": (bytes(good[:len(good) // 2]), None),
    }
    for label, (frame, code) in cases.items():
        before, stale = len(client.events), client.stale
        client._write(frame + bytes((st.END,)))
        # 150 ms: one console turn per byte plus the CDC round trip, 3x the op floor
        client._wait(150, lambda r: False)
        errors = [e for e in client.events[before:] if e.kind == st.ERROR]
        ok = bool(errors) if code is None else any(
            st.ERROR_S.unpack(e.body)[0] == code for e in errors)
        check(f"ops: {label} frame -> ERROR record, no response",
              ok and client.stale == stale,
              f"errors {[st.ERROR_S.unpack(e.body) for e in errors]}, stale {client.stale}")
        rsp = call("sys.ping", 7)
        check(f"ops: session alive after a {label} frame",
              rsp.ok and rsp.fields["nonce"] == 7, str(rsp))

    # --- the watchdog does not fire early, and does fire ---------------
    quiet(session, QUIET_S)
    rsp = call("sys.ping", 8)
    check(f"ops: {QUIET_S:.1f} s of silence (80% of the watchdog) does not end the session",
          rsp.ok and rsp.fields["nonce"] == 8, str(rsp))

    # a stuck host: half a frame, then nothing
    client._write(good[:len(good) // 2])
    mark = len(session.snapshot())
    started = time.monotonic()
    found = session.expect(b"ops: watchdog, back to text", WATCHDOG_BUDGET_S, mark)
    waited = time.monotonic() - started
    check(f"ops: a stuck host mid-frame is cut loose within {WATCHDOG_BUDGET_S:.2f} s "
          f"(watchdog {ops.WATCHDOG_MS} ms)",
          found is not None and waited >= ops.WATCHDOG_MS / 1000 * 0.9,
          f"waited {waited:.2f} s; received {show(session.snapshot()[mark:])}")
    client._queue.extend(client.decoder.feed(SessionTransport.read(client.transport, 0.05)))
    left = [e for e in (client._queue + client.events) if e.kind == st.ERROR
            and st.ERROR_S.unpack(e.body) == (ops.ERR_LEFT, ops.LEAVE_WATCHDOG)]
    check("ops: the watchdog announced itself with ERROR(left, watchdog)", bool(left),
          f"{len(client._queue)} queued, {len(client.events)} events")

    # --- back in text: the shell is usable -----------------------------
    mark = len(session.snapshot())
    session.send(b"\r")
    check("ops: after the watchdog the text prompt is back",
          session.expect(prompt, 3.0, mark) is not None, show(session.snapshot()[mark:]))
    mark = len(session.snapshot())
    session.send(b"time\r")
    check("ops: and `time` answers", session.expect(b"uptime", 3.0, mark) is not None,
          show(session.snapshot()[mark:]))

    # --- re-enter, leave by ETX ----------------------------------------
    client2 = co.OpsClient(SessionTransport(session))
    try:
        client2.enter()
        rsp = client2.call("sys.ping", 9)
        client2.leave()
        check("ops: re-entry works, and ETX is confirmed by ERROR(left, host)",
              rsp.ok, str(rsp))
    except (co.OpTimeout, ops.OpError) as error:
        check("ops: re-entry works, and ETX is confirmed by ERROR(left, host)", False, str(error))
    mark = len(session.snapshot())
    session.send(b"\r")
    check("ops: after ETX the text prompt is back",
          session.expect(prompt, 3.0, mark) is not None, show(session.snapshot()[mark:]))
