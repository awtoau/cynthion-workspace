# SPDX-License-Identifier: BSD-3-Clause
"""8-bit byte stream <-> 32-bit word stream, in one clock domain (the 60 MHz ULPI side).

- Word = 35 bits: data[0:32] (byte i in bits 8i..8i+7), n[32:34] = bytes - 1, last[34].
- 35 bits fit one ECP5 EBR at 512 x 36 (FPGA-TN-02204 Table 4.8).
- Both directions sustain one byte per cycle with no bubbles.
"""

from amaranth import Cat, Elaboratable, Module, Mux, Signal

WORD_BITS = 35


def word(data, n, last):
    return Cat(data, n, last)


class Packer(Elaboratable):
    """Bytes in (valid/ready/last) -> words out (valid/ready). `swap` is a sim-only mutation."""

    def __init__(self, *, domain="usb", swap=False):
        self.domain, self.swap = domain, swap
        self.i_data, self.i_last = Signal(8), Signal()
        self.i_valid, self.i_ready = Signal(), Signal()
        self.o_word, self.o_valid, self.o_ready = Signal(WORD_BITS), Signal(), Signal()

    def elaborate(self, platform):
        m = Module()
        d = m.d[self.domain]
        acc = Signal(24)
        n = Signal(2)
        m.d.comb += self.i_ready.eq(~self.o_valid | self.o_ready)
        with m.If(self.o_valid & self.o_ready):
            d += self.o_valid.eq(0)
        bytes_ = [Mux(n == i, self.i_data, acc[8 * i:8 * i + 8] if i < 3 else 0)
                  for i in range(4)]
        data = Signal(32)
        m.d.comb += data.eq(Cat(*(bytes_[::-1] if self.swap else bytes_)))
        with m.If(self.i_valid & self.i_ready):
            done = (n == 3) | self.i_last
            with m.If(done):
                d += [self.o_word.eq(word(data, n, self.i_last)), self.o_valid.eq(1), n.eq(0)]
            with m.Else():
                d += [acc.eq(data[:24]), n.eq(n + 1)]
        return m


class Unpacker(Elaboratable):
    """Words in (valid/ready) -> bytes out (valid/ready/last); next word loads on the last byte."""

    def __init__(self, *, domain="usb"):
        self.domain = domain
        self.i_word, self.i_valid, self.i_ready = Signal(WORD_BITS), Signal(), Signal()
        self.o_data, self.o_last = Signal(8), Signal()
        self.o_valid, self.o_ready = Signal(), Signal()

    def elaborate(self, platform):
        m = Module()
        d = m.d[self.domain]
        held = Signal(WORD_BITS)
        idx = Signal(2)
        n, last = held[32:34], held[34]
        end = idx == n
        m.d.comb += [self.o_data.eq(held.word_select(idx, 8)),
                     self.o_last.eq(last & end),
                     self.i_ready.eq(~self.o_valid | (self.o_ready & end))]
        with m.If(self.o_valid & self.o_ready):
            d += idx.eq(idx + 1)
            with m.If(end):
                d += self.o_valid.eq(0)
        with m.If(self.i_valid & self.i_ready):
            d += [held.eq(self.i_word), idx.eq(0), self.o_valid.eq(1)]
        return m
