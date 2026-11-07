#!/usr/bin/env python3
#
# The SoC device interface, host side: op table, request/response codec.
# SPDX-License-Identifier: BSD-3-Clause

"""
Op table and codec for the binary request/response protocol in
[`docs/device-interface.md`](../../docs/device-interface.md) (#303).

    ./scripts/soc/soc_ops.py --vectors     # golden frames as hex
    ./scripts/soc/soc_ops.py --table       # the op table as markdown
    ./scripts/soc/soc_ops.py --golden      # tests/data/ops_golden.txt contents

Frames are the record-stream frames of `soc_stream.py` (SLIP, CRC-32, 12-byte
header); this module adds two kinds and the bodies they carry.

Units and scales live in the DRIVER on the board (#270). Every value here is
already a physical unit (mV, uA, ms) or an enum; this file converts nothing.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import soc_stream as st  # noqa: E402

# --------------------------------------------------------------------------
# frame kinds, status codes, error codes -- frozen by docs/device-interface.md
# --------------------------------------------------------------------------

PROTO_VERSION = 1               # op-protocol version, reported by sys.capabilities

KIND_REQ = st.REQ               # host -> board, header.seq = request id
KIND_RSP = st.RSP               # board -> host, header.seq = the request's id

REQ_HEAD = struct.Struct("<H")          # op
RSP_HEAD = struct.Struct("<HB")         # op, status
FAIL_TAIL = struct.Struct("<BI")        # reason, value -- every non-OK response

# The firmware's buffers. A request body over MAX_REQ_BODY is refused as
# OVERFLOW; capabilities reports both so a host never has to guess.
MAX_REQ_BODY = 64
MAX_RSP_BODY = 128
MAX_OP_BODY = MAX_REQ_BODY - REQ_HEAD.size

ST_OK = 0
ST_UNKNOWN_OP = 1
ST_BAD_ARGS = 2
ST_REFUSED = 3
ST_UNAVAILABLE = 4
ST_IO = 5
STATUS_NAMES = {ST_OK: "ok", ST_UNKNOWN_OP: "unknown_op", ST_BAD_ARGS: "bad_args",
                ST_REFUSED: "refused", ST_UNAVAILABLE: "unavailable", ST_IO: "io"}

# ERROR records (kind 0x04, `<II code, value`) for frames that never became requests.
ERR_CRC = 1
ERR_ESCAPE = 2
ERR_RUNT = 3
ERR_LENGTH = 4
ERR_VERSION = 5
ERR_KIND = 6
ERR_OVERFLOW = 7
ERR_SHORT_REQ = 8
ERR_LEFT = 9                    # value = reason the session ended (LEAVE_*)
ERROR_NAMES = {ERR_CRC: "crc", ERR_ESCAPE: "escape", ERR_RUNT: "runt",
               ERR_LENGTH: "length", ERR_VERSION: "version", ERR_KIND: "kind",
               ERR_OVERFLOW: "overflow", ERR_SHORT_REQ: "short_request",
               ERR_LEFT: "left"}

LEAVE_HOST = 1                  # ETX
LEAVE_WATCHDOG = 2

# Binary-mode entry and exit.
ENTER = b"ops\r"                # text command; the board answers with a SESSION frame
LEAVE = bytes((st.CTRL_LEAVE,))  # one raw byte, only between frames

# Silence limit. Derived: host keepalive period x 1.25, see the doc.
KEEPALIVE_MS = 2000
WATCHDOG_MS = 2500

# Features bits in sys.capabilities.
FEAT_BOARD = 1 << 0             # I2C board peripherals present (not QEMU)
FEAT_HYPERRAM = 1 << 1

# Stable wire codes. The board maps these to its own enums; the order of
# firmware tables is irrelevant to the wire.
LEDS = ["red", "orange", "yellow", "green", "blue", "violet"]
LED_OFF, LED_ON, LED_FABRIC = 0, 1, 2
CHANNELS = ["target_a", "target_c", "aux", "control"]       # power.rs PORTS order
SOURCES = ["control", "aux", "target_c"]
VBUS_OFF, VBUS_ON, VBUS_SOURCE, VBUS_OPEN_ALL = 0, 1, 2, 3
SWITCH = {0: "none", 1: "open", 2: "closed"}
ORIENTATION = {0: "unknown", 1: "unattached", 2: "cc1", 3: "cc2", 4: "both"}
CC_UNMEASURED = 0xFF

VBUS_REASONS = {1: "too_high", 2: "too_low", 3: "uv", 4: "ov", 5: "unreadable",
                6: "not_sampling", 7: "not_output", 8: "other_source_closed",
                9: "cc_failed"}
# status UNAVAILABLE, any op
UNAVAILABLE_REASONS = {1: "no_board", 2: "no_sample", 3: "read_failed"}
VBUS_NONE = 0xFF                # vbus.set `source` when nothing was closed

SELFTEST_ITEMS = ["alu", "muldiv", "comp", "atomic", "ram", "clock", "uart",
                  "gateware", "flash", "phy", "rail"]
SELFTEST_OUTCOME = {0: "pass", 1: "fail", 2: "skip"}


# --------------------------------------------------------------------------
# the op table
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Op:
    code: int
    name: str
    args: str                   # struct format, "<" prefixed; "" = none
    arg_names: tuple
    result: str
    result_names: tuple
    shell: str                  # the shell command this replaces
    idempotent: bool
    worst_ms: str               # worst-case board time, with its source
    doc: str
    tail: str = ""              # repeated element format after `result`, to end of body
    tail_name: str = ""

    @property
    def args_s(self) -> struct.Struct:
        return struct.Struct(self.args or "<")

    @property
    def result_s(self) -> struct.Struct:
        return struct.Struct(self.result or "<")


def _per(names: list[str], fields: list[str]) -> tuple:
    return tuple(f"{n}_{f}" for n in names for f in fields)


OPS: list[Op] = [
    Op(0x0001, "sys.ping", "<I", ("nonce",), "<I", ("nonce",),
       "(none)", True, "<1 ms: no hardware",
       "Echo. Also the keepalive that holds the watchdog."),
    Op(0x0002, "sys.capabilities", "<H", ("first",),
       "<BHHIHB", ("proto", "max_req_body", "max_rsp_body", "features", "total", "n"),
       "(none)", True, "<1 ms: no hardware",
       "Protocol version, buffer limits, feature bits, and the supported opcodes "
       "from index `first` (a page of at most 48).",
       tail="H", tail_name="ops"),
    Op(0x0003, "sys.info", "", (), "<IIIBB",
       ("fw_git", "time_hz", "uptime_ms", "console", "consoles"),
       "info", True, "<1 ms: no hardware",
       "Build identity (GIT_WORD, bit 31 = dirty), clock, uptime, this console."),
    Op(0x0100, "power.read", "", (),
       "<IB" + "IiIB" * 4, ("age_ms", "channels")
       + _per(CHANNELS, ["mv", "ua", "floor_ua", "flags"]),
       "pac1954 status; usb <port> status (rail line)", True,
       "~3 ms: REFRESH + 1.25 ms wait + 16-byte read (power.rs ON_DEMAND_WAIT_US)",
       "Four channels in `CHANNELS` order. flags bit 0 = drawing (above the floor). "
       "age_ms 0xFFFFFFFF = older than the age limit."),
    Op(0x0101, "power.limits", "<B", ("channel",), "<iiiiBB",
       ("ov_mv", "uv_mv", "oc_ua", "uc_ua", "armed", "fired"),
       "pac1954 limit / alert (read)", True, "<1 ms: cached, no bus",
       "Cached alert limits for one channel. armed/fired bit 0 ov, 1 uv, 2 oc, 3 uc."),
    Op(0x0200, "vbus.status", "", (), "<BBB", ("raw", "closed", "powered"),
       "vbus status; usb <port> power", True, "<1 ms: one register read",
       "raw register. closed bit 0 control, 1 aux, 2 target_c. "
       "powered bit n = power.read channel n driven."),
    Op(0x0201, "vbus.set", "<BBH", ("port", "action", "current_ma"),
       "<BIB", ("source", "mv", "flags"),
       "vbus <source>|off|charge; usb <port> power on|off", True,
       "~5 ms: gate read (as power.read) + register write; target_c adds FUSB302B writes",
       "action 0 off, 1 on, 2 close source, 3 open all. port = CHANNELS index. "
       "current_ma 0/1500/3000 = Rp advertised on target_c, else 0. "
       "flags bit 0 fell back to aux, bit 1 already on. Refusal: reason in VBUS_REASONS."),
    Op(0x0300, "led.get", "", (), "<BBB", ("lit", "fabric", "button"),
       "led; info button", True, "<1 ms: GPIO reads",
       "lit/fabric: bit n = LEDS[n]; fabric = released to the gateware."),
    Op(0x0301, "led.set", "<BB", ("led", "state"), "<BB", ("lit", "fabric"),
       "led <colour> on|off|fabric", True, "<1 ms: GPIO writes",
       "led = LEDS index; state 0 off, 1 on, 2 fabric. Result is led.get's."),
    Op(0x0400, "typec.status", "", (),
       "<BB" + "BBBBBBBI" * 2, ("configured", "lines")
       + _per(["target", "aux"], ["ok", "device_id", "vbus", "cc1", "cc2",
                                   "source_cc", "orientation", "serviced"]),
       "fusb302b [status]", True,
       "~3 ms: 8 register reads x 2 ports at the board's I2C rate",
       "Live read per port (target, aux). cc1/cc2 0xFF = not measured. "
       "ok 0 = read failed (other fields zero)."),
    Op(0x0401, "pd.status", "", (), "<BBBBIIIIBHI",
       ("armed", "cc", "running", "ready", "sent", "received", "errors",
        "hardresets", "contract_pos", "contract_ma", "session_bytes"),
       "pd target_c status", True, "<1 ms: no bus",
       "Policy-engine state on TARGET-C. contract_pos 0 = no contract."),
    Op(0x0500, "usb.port", "<B", ("port",),
       "<BIiBBBBBBBB", ("sample", "mv", "ua", "drawing", "switch", "cable",
                        "vbus", "cc1", "cc2", "source_cc", "orientation"),
       "usb <port> status", True, "~3 ms: as power.read when the poll is off",
       "One port, completely. sample 0 = no sample (mv/ua 0). "
       "switch in SWITCH; cable 0 unknown/none, 1 cached state valid."),
    Op(0x0600, "hr.status", "", (), "<IIIBBBBBBBHHBBB",
       ("id", "ck_khz_req", "ck_khz_meas", "ready", "locked", "calibrated", "busy",
        "hr_busy", "last_op", "last_code", "errors", "state", "cal_tap", "cal_width",
        "cal_valid"),
       "hr status", True, "<1 ms: CSR reads",
       "UNAVAILABLE on a target without the peripheral."),
    Op(0x0700, "selftest.run", "", (), "<BBBB", ("passed", "failed", "skipped", "n"),
       "selftest", True,
       "unmeasured on the board; provisional 250 ms client limit (RAM walk is log2-sparse)",
       "Runs every check. Tail: n pairs (item index in SELFTEST_ITEMS, outcome).",
       tail="BB", tail_name="items"),
]

BY_CODE = {op.code: op for op in OPS}
BY_NAME = {op.name: op for op in OPS}
assert len(BY_CODE) == len(OPS) == len(BY_NAME), "duplicate opcode or name"
for _op in OPS:
    assert len(_op.arg_names) == len(_op.args_s.unpack(bytes(_op.args_s.size))), _op.name
    _n = len(_op.result_s.unpack(bytes(_op.result_s.size)))
    assert len(_op.result_names) == _n, (_op.name, len(_op.result_names), _n)

CAPS_PAGE = 48


# --------------------------------------------------------------------------
# codec
# --------------------------------------------------------------------------

class OpError(Exception):
    """A response with status != OK, or one that cannot be understood."""

    def __init__(self, op: str, status: int, reason: int = 0, value: int = 0):
        self.op, self.status, self.reason, self.value = op, status, reason, value
        super().__init__(f"{op}: {STATUS_NAMES.get(status, status)} "
                         f"reason={reason} value={value}")


def encode_request(op: Op | int | str, req_id: int, *args: int) -> bytes:
    op = _op(op)
    body = REQ_HEAD.pack(op.code) + op.args_s.pack(*args)
    return st.encode_record(KIND_REQ, req_id, 0, body)


def encode_response(op: Op | int | str, req_id: int, at_ms: int, *result: int,
                    tail: list | None = None) -> bytes:
    op = _op(op)
    body = RSP_HEAD.pack(op.code, ST_OK) + op.result_s.pack(*result)
    for item in tail or []:
        body += struct.pack("<" + op.tail, *(item if isinstance(item, tuple) else (item,)))
    return st.encode_record(KIND_RSP, req_id, at_ms, body)


def encode_failure(op_code: int, req_id: int, at_ms: int, status: int,
                   reason: int = 0, value: int = 0) -> bytes:
    body = RSP_HEAD.pack(op_code, status) + FAIL_TAIL.pack(reason, value)
    return st.encode_record(KIND_RSP, req_id, at_ms, body)


def _op(op) -> Op:
    if isinstance(op, Op):
        return op
    return BY_NAME[op] if isinstance(op, str) else BY_CODE[op]


@dataclass
class Request:
    req_id: int
    op_code: int
    args: dict
    body: bytes


@dataclass
class Response:
    req_id: int
    op_code: int
    status: int
    at_ms: int
    fields: dict = field(default_factory=dict)
    tail: list = field(default_factory=list)
    reason: int = 0
    value: int = 0

    @property
    def ok(self) -> bool:
        return self.status == ST_OK


def decode_request(record: st.Record) -> Request:
    if record.kind != KIND_REQ or len(record.body) < REQ_HEAD.size:
        raise ValueError("not a request")
    code, = REQ_HEAD.unpack_from(record.body)
    rest = record.body[REQ_HEAD.size:]
    op = BY_CODE.get(code)
    args = {}
    if op and len(rest) == op.args_s.size:
        args = dict(zip(op.arg_names, op.args_s.unpack(rest)))
    return Request(record.seq, code, args, rest)


def decode_response(record: st.Record) -> Response:
    if record.kind != KIND_RSP or len(record.body) < RSP_HEAD.size:
        raise ValueError("not a response")
    code, status = RSP_HEAD.unpack_from(record.body)
    rest = record.body[RSP_HEAD.size:]
    rsp = Response(record.seq, code, status, record.at_ms)
    if status != ST_OK:
        if len(rest) != FAIL_TAIL.size:
            raise ValueError(f"failure tail is {len(rest)} bytes, want {FAIL_TAIL.size}")
        rsp.reason, rsp.value = FAIL_TAIL.unpack(rest)
        return rsp
    op = BY_CODE.get(code)
    if op is None:
        raise ValueError(f"response for unknown opcode 0x{code:04x}")
    fixed = op.result_s.size
    if len(rest) < fixed:
        raise ValueError(f"{op.name}: result is {len(rest)} bytes, want {fixed}")
    rsp.fields = dict(zip(op.result_names, op.result_s.unpack_from(rest)))
    if op.tail:
        item = struct.Struct("<" + op.tail)
        extra = rest[fixed:]
        if len(extra) % item.size:
            raise ValueError(f"{op.name}: tail {len(extra)} bytes, element {item.size}")
        rsp.tail = [item.unpack_from(extra, i) for i in range(0, len(extra), item.size)]
        if len(item.unpack(bytes(item.size))) == 1:
            rsp.tail = [t[0] for t in rsp.tail]
    elif len(rest) != fixed:
        raise ValueError(f"{op.name}: result is {len(rest)} bytes, want {fixed}")
    return rsp


# --------------------------------------------------------------------------
# golden vectors: request, the fake board's response, frozen in tests
# --------------------------------------------------------------------------

# The values the Rust test driver (firmware/cynthion-soc-tests/tests/ops.rs)
# returns. Two copies on purpose: a drift fails the test on one side.
AT = 5000


def _golden_cases() -> list[tuple[str, bytes, bytes]]:
    c: list[tuple[str, bytes, bytes]] = []

    def add(name, req_id, args, result, tail=None):
        op = BY_NAME[name]
        c.append((name, encode_request(op, req_id, *args),
                  encode_response(op, req_id, AT, *result, tail=tail)))

    add("sys.ping", 1, (0xA5A5C0DB,), (0xA5A5C0DB,))
    add("sys.capabilities", 2, (0,),
        (PROTO_VERSION, MAX_REQ_BODY, MAX_RSP_BODY, FEAT_BOARD | FEAT_HYPERRAM,
         len(OPS), len(OPS)), tail=[op.code for op in OPS])
    add("sys.info", 3, (), (0x8000_1A2B, 60_000_000, 123_456, 0, 2))
    add("power.read", 4, (),
        (50, 4,
         4994, -12_000, 20_000, 0,
         5001, 466_000, 20_000, 1,
         0, 0, 20_000, 0,
         5000, 90_000, 20_000, 1))
    add("power.limits", 5, (2,), (5500, 4000, 3000_000, -3000_000, 0b0011, 0b0010))
    add("vbus.status", 6, (), (0x84, 0b100, 0b0010))
    add("vbus.set", 7, (1, VBUS_ON, 1500), (0, 5001, 0))
    add("led.get", 8, (), (0b001001, 0b100000, 1))
    add("led.set", 9, (3, LED_ON), (0b001001, 0b100000))
    add("typec.status", 10, (),
        (1, 0x3, 1, 0x91, 1, 1, 0, 1, 2, 4,
         1, 0x91, 0, CC_UNMEASURED, CC_UNMEASURED, 0, 0, 0))
    add("pd.status", 11, (), (1, 1, 1, 1, 12, 11, 0, 0, 1, 900, 1536))
    add("usb.port", 12, (1,), (1, 5001, 466_000, 1, 1, 1, 1, 1, 0, 0, 2))
    add("hr.status", 13, (),
        (0x1234_5678, 180_000, 179_900, 1, 1, 1, 0, 0, 4, 0, 0, 0, 10, 32, 1))
    add("selftest.run", 14, (), (10, 0, 1, 11),
        tail=[(i, o) for i, o in [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0),
                                   (6, 0), (7, 0), (8, 0), (9, 0), (10, 2)]])
    # failures
    op = BY_NAME["vbus.set"]
    c.append(("vbus.set refused uv", encode_request(op, 15, 3, VBUS_SOURCE, 0),
              encode_failure(op.code, 15, AT, ST_REFUSED, 3, 3980)))
    c.append(("unknown op", st.encode_record(KIND_REQ, 16, 0, REQ_HEAD.pack(0x7FFF)),
              encode_failure(0x7FFF, 16, AT, ST_UNKNOWN_OP)))
    c.append(("bad args", st.encode_record(KIND_REQ, 17, 0,
                                           REQ_HEAD.pack(0x0001) + b"\x00"),
              encode_failure(0x0001, 17, AT, ST_BAD_ARGS)))
    return c


def golden_cases():
    return _golden_cases()


def golden_text() -> str:
    """The contents of firmware/cynthion-soc-tests/tests/ops_golden.txt."""
    lines = ["# generated by scripts/soc/soc_ops.py --golden; the Rust test replays it",
             "# name | request frame | expected response frame (hex)"]
    for name, req, rsp in golden_cases():
        lines.append(f"{name} | {req.hex()} | {rsp.hex()}")
    return "\n".join(lines) + "\n"


def table_markdown() -> str:
    out = ["| code | op | args | result | shell | idem | worst case |",
           "|---|---|---|---|---|---|---|"]
    for op in OPS:
        args = ", ".join(op.arg_names) or "-"
        res = ", ".join(op.result_names[:3]) + (", ..." if len(op.result_names) > 3 else "")
        out.append(f"| `0x{op.code:04x}` | `{op.name}` | {args} | {res or '-'} | "
                   f"{op.shell} | {'yes' if op.idempotent else 'no'} | {op.worst_ms} |")
    return "\n".join(out)


def main() -> int:
    if "--golden" in sys.argv:
        sys.stdout.write(golden_text())
    elif "--table" in sys.argv:
        print(table_markdown())
    else:
        for name, req, rsp in golden_cases():
            print(f"{name:<22} REQ {req.hex()}\n{'':<22} RSP {rsp.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
