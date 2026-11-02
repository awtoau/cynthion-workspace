#
# The block header: one definition for the gateware, the simulations and the PC tool.
# SPDX-License-Identifier: BSD-3-Clause

"""8-byte block header (`docs/data-path.md#block-header`). Little endian.

    [0]   MAGIC 0xDB
    [1]   FLAGS  [3:0] endpoint  [4] dir (0 OUT to phone, 1 IN from phone)
                 [6:5] type (USB bmAttributes: 0 ctrl 1 iso 2 bulk 3 int)  [7] zlp
    [2]   TAG    PC's choice, echoed in every result block
    [3]   AUX    [3:0] codec (0 none)  [4] more (result)  [5] short (result)
    [4]   STATUS 0 in a request; result code in a result
    [5:8] LENGTH payload bytes, 24 bit (stage 1 accepts <= MAX_LEN)

Pure Python: imported by gateware, sims and `scripts/datapath_pc.py` alike.
"""

from dataclasses import dataclass

HEADER_BYTES = 8
MAGIC = 0xDB
MAX_LEN = 1 << 16           # stage 1 block payload limit
LEN_BITS = 17               # holds MAX_LEN

DIR_OUT, DIR_IN = 0, 1
TYPE_CONTROL, TYPE_ISO, TYPE_BULK, TYPE_INTERRUPT = 0, 1, 2, 3

AUX_MORE, AUX_SHORT = 1 << 4, 1 << 5

# Result STATUS codes (byte 4). The RV writes them; the gateware supplies the evidence.
ST_OK, ST_STALL, ST_NAK_LIMIT, ST_ERROR, ST_BAD_HEADER, ST_REJECTED, ST_OVERRUN = range(7)
STATUS_NAMES = {ST_OK: "ok", ST_STALL: "stall", ST_NAK_LIMIT: "nak_limit", ST_ERROR: "error",
                ST_BAD_HEADER: "bad_header", ST_REJECTED: "rejected", ST_OVERRUN: "overrun"}


@dataclass
class Header:
    ep: int = 1
    dir: int = DIR_OUT
    type: int = TYPE_BULK
    zlp: bool = False
    tag: int = 0
    codec: int = 0
    more: bool = False
    short: bool = False
    status: int = 0
    length: int = 0

    @property
    def flags(self):
        return (self.ep & 0xF) | (self.dir << 4) | ((self.type & 3) << 5) | (int(self.zlp) << 7)

    @property
    def aux(self):
        return (self.codec & 0xF) | (AUX_MORE if self.more else 0) | (AUX_SHORT if self.short else 0)

    def pack(self):
        n = self.length
        return bytes([MAGIC, self.flags, self.tag & 0xFF, self.aux, self.status & 0xFF,
                      n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF])

    @classmethod
    def unpack(cls, raw):
        raw = bytes(raw)
        if len(raw) < HEADER_BYTES or raw[0] != MAGIC:
            raise ValueError(f"not a block header: {raw[:HEADER_BYTES].hex()}")
        f, a = raw[1], raw[3]
        return cls(ep=f & 0xF, dir=(f >> 4) & 1, type=(f >> 5) & 3, zlp=bool(f >> 7),
                   tag=raw[2], codec=a & 0xF, more=bool(a & AUX_MORE),
                   short=bool(a & AUX_SHORT), status=raw[4],
                   length=raw[5] | raw[6] << 8 | raw[7] << 16)


def out_block(payload, *, ep=1, tag=0, zlp=False, type=TYPE_BULK):
    """A PC -> phone block: header + payload."""
    payload = bytes(payload)
    return Header(ep=ep, dir=DIR_OUT, type=type, zlp=zlp, tag=tag, length=len(payload)).pack() + payload


def in_request(length, *, ep=1, tag=0, type=TYPE_BULK):
    """A phone -> PC request: header only; `length` is the most the PC wants back."""
    return Header(ep=ep, dir=DIR_IN, type=type, tag=tag, length=length).pack()
