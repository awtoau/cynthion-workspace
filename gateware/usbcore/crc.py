#
# USB CRC5 (tokens) and CRC16 (data), as parallel XOR networks.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
USB 2.0 8.3.5: both CRCs are reflected (LSB first), preset to all ones, sent complemented.

- CRC5: x^5 + x^2 + 1 over the 11 token bits; `crc5(value11)` is combinational.
- CRC16: x^16 + x^15 + x^2 + 1 over data bytes; `crc16_next(state, byte)` is one byte step.
- `CRC16`: a running register (`clear`, `update`, `data`); `crc` is the value to send.
- `CRC16_RESIDUAL`: register value after a packet's payload AND its CRC bytes: a good packet
  ends there, so receivers need no copy of the last two bytes.
- The XOR networks are derived here by stepping the serial LFSR symbolically, not tabulated.
"""

import functools
import operator

from amaranth import Cat, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["crc5", "crc16_next", "CRC16", "CRC5_POLY_REFLECTED", "CRC16_POLY_REFLECTED",
           "CRC16_RESIDUAL", "crc5_reference", "crc16_reference"]

CRC5_POLY_REFLECTED  = 0x14     # 0x05 bit-reversed over 5 bits
CRC16_POLY_REFLECTED = 0xA001   # 0x8005 bit-reversed over 16 bits

_ONE = "1"                      # symbolic constant term


def _step(state, bit, poly):
    """One serial LFSR step on symbolic bits (frozensets of terms, XOR = symmetric difference)."""
    fb = state[0] ^ bit
    nxt = list(state[1:]) + [frozenset()]
    return [nxt[i] ^ fb if (poly >> i) & 1 else nxt[i] for i in range(len(state))]


def _xor(terms, lookup):
    bits = [lookup(t) for t in sorted(terms, key=str) if t != _ONE]
    value = functools.reduce(operator.xor, bits) if bits else Const(0, 1)
    return ~value if _ONE in terms else value


@functools.cache
def _crc16_equations():
    state = [frozenset({("s", i)}) for i in range(16)]
    for j in range(8):
        state = _step(state, frozenset({("d", j)}), CRC16_POLY_REFLECTED)
    return tuple(state)


@functools.cache
def _crc5_equations():
    state = [frozenset({_ONE}) for _ in range(5)]            # preset 0x1F
    for j in range(11):
        state = _step(state, frozenset({("d", j)}), CRC5_POLY_REFLECTED)
    return tuple(s ^ frozenset({_ONE}) for s in state)       # sent complemented


def crc16_next(state, byte):
    """16-bit register after shifting in `byte` (bit 0 first)."""
    def lookup(t):
        return state[t[1]] if t[0] == "s" else byte[t[1]]
    return Cat(*(_xor(eq, lookup) for eq in _crc16_equations()))


def crc5(value):
    """5-bit CRC to send for an 11-bit token field (address in [6:0], endpoint in [10:7])."""
    return Cat(*(_xor(eq, lambda t: value[t[1]]) for eq in _crc5_equations()))


def crc5_reference(value):
    """Bit-serial software CRC5 of an 11-bit integer."""
    reg = 0x1F
    for j in range(11):
        fb = (reg ^ (value >> j)) & 1
        reg = (reg >> 1) ^ (CRC5_POLY_REFLECTED if fb else 0)
    return ~reg & 0x1F


def crc16_reference(data, *, register=False):
    """Bit-serial software CRC16 of a byte sequence; `register` returns the raw register."""
    reg = 0xFFFF
    for byte in data:
        for j in range(8):
            fb = (reg ^ (byte >> j)) & 1
            reg = (reg >> 1) ^ (CRC16_POLY_REFLECTED if fb else 0)
    return reg if register else ~reg & 0xFFFF


def _residual():
    crc = crc16_reference(b"")
    return crc16_reference(bytes([crc & 0xFF, crc >> 8]), register=True)


CRC16_RESIDUAL = _residual()


class CRC16(wiring.Component):
    """Running CRC16. `clear` wins over `update`; `crc` reflects bytes up to the last edge."""

    clear:  In(1)
    update: In(1)
    data:   In(8)
    crc:    Out(16)
    good:   Out(1)     # register == CRC16_RESIDUAL: payload plus its CRC checked out

    def __init__(self, domain="usb"):
        self._domain = domain
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        reg = Signal(16, init=0xFFFF)
        with m.If(self.clear):
            m.d[self._domain] += reg.eq(0xFFFF)
        with m.Elif(self.update):
            m.d[self._domain] += reg.eq(crc16_next(reg, self.data))
        m.d.comb += [
            self.crc.eq(~reg),
            self.good.eq(reg == CRC16_RESIDUAL),
        ]
        return m
