#!/usr/bin/env python3
#
# Clocks out to PMOD pins, so a scope can check them. See #294.
# SPDX-License-Identifier: BSD-3-Clause

"""Divided copies of the internal clocks on PMOD A, for an external instrument.

## Why

Every clock number this project has produced so far is the design describing
itself -- `clock_monitor` counts `sync` against `usb`, and both are inside the
same die that would be wrong together. A scope on a pin is the first source of
evidence that does not share a failure mode with the thing under test.

It matters more once CK is runtime-selectable (#228): "which rung is live" then
has to be answerable from outside, not from the register that requested it.

## Divisor

**/4, fixed at build.** Two limits set it:

  * LVCMOS33 output is rated 150 MHz (ECP5 datasheet Table 3.21). `hr_fast` is
    the device CK on the DQS path and reaches 200 MHz, so the raw net is out of
    spec on the pad.
  * A PMOD header with flying leads is not a transmission line at any of these
    rates. A number that is provably `f/4` beats a rounded-off edge at `f`.

/4 puts every source in 15..50 MHz for the CK range this board is swept over
(sync 60, usb 60, hr <=200, hr_fast <=200). Powers of two only -- the divider is
a counter bit, so an odd divisor would need a duty-cycle correction that no
reader of the pin wants to have to know about.

## Fabric counter, not CLKDIVF

`CLKDIVF` is the hard divider, but it takes ECLK in and ECLK out: reaching it
means an `ECLKSYNCB` on the same edge-clock spine the DQS PHY is using, which is
the resource #314 was about. A toggle counter is two LUT4s and two FFs per
source and touches nothing.

The cost is that the source domain gains a fabric register. On `hr_fast` that
means the net must reach the primary clock network as well as the edge clock,
which is why this is OFF by default.

It does not cost the edge clock: with the mirror on, the DQS build still routes
`S1W2_ECLKI0` from `G_JLLCPLL0CLKOS` -- the dedicated PLL tap, not the fabric
one -- and no clock falls back to general routing. Check that in `top.config`
rather than the log if this changes; nextpnr announces the fallback as
`log_info` and no summary carries it (#314).
"""

from amaranth import Elaboratable, Module, Signal
from amaranth.lib import io
from amaranth.lib.wiring import In, Component, connect, flipped
from amaranth_soc import csr

__all__ = ["ClockMirror", "ClockMirrorMap", "SOURCE_CODES"]

# One PMOD pin per source, in order. Pin 0 is PMOD A pin 1.
MAX_SOURCES = 8

# What the firmware is told is on a pad. A number rather than a name because a
# nibble per pad fits one register; `firmware/cynthion-soc/src/bist.rs` holds the
# other half of the table and `tests/test_clock_mirror.py` holds them equal.
#
# 0 is "nothing", so a build with no mirror -- whose window does not exist and
# reads as zeroes -- reports every pad idle, which is what is true.
SOURCE_CODES = {"sync": 1, "usb": 2, "hr": 3, "hr_fast": 4, "hr_probe": 5}


class ClockMirror(Elaboratable):
    """`divisor`-divided copies of `domains` on the pins of one PMOD buffer.

    `domains` is a list of domain names; each gets the next PMOD pin, in order,
    so pin N is `domains[N]` and the mapping is positional rather than named.
    A caller that reorders the list moves the pins, which is the intent -- the
    firmware's `pmod` command reports the same list.

    `pads` is a `dir="-"` PMOD resource; the buffer is built here so that a
    build with the mirror off never requests the resource at all and the pins
    stay available to whatever is plugged into them.
    """

    def __init__(self, *, pads, domains, divisor=4):
        if divisor < 2 or divisor & (divisor - 1):
            raise ValueError(f"divisor must be a power of two >= 2, not {divisor}")
        if len(domains) > MAX_SOURCES:
            raise ValueError(f"{len(domains)} sources for {MAX_SOURCES} pins")

        self.pads = pads
        self.domains = list(domains)
        self.divisor = divisor
        self.stages = divisor.bit_length() - 1

    def elaborate(self, platform):
        m = Module()

        m.submodules.buffer = buffer = io.Buffer("io", self.pads)
        out = Signal(len(buffer.o))

        for index, domain in enumerate(self.domains):
            # A free-running counter; bit `stages-1` is the input divided by
            # `divisor`. Registered, so the pin never sees a decode glitch.
            count = Signal(self.stages, name=f"mirror_{domain}_count")
            m.d[domain] += count.eq(count + 1)
            m.d.comb += out[index].eq(count[-1])

        m.d.comb += [
            buffer.o.eq(out),
            # Whole-buffer enable: `io.Buffer` has one `oe` for all eight pins,
            # so the unused ones are driven low rather than left floating. A
            # floating pin next to a clock is a pin someone will scope by
            # mistake.
            buffer.oe.eq(1),
        ]

        return m


class ClockMirrorMap(Component):
    """Which source is on which pad, read from the bitstream rather than assumed.

        +0  pads   RO  nibble per pad, pad 0 in bits 3:0; see SOURCE_CODES
        +4  info   RO  bits 7:0 divisor, bits 15:8 pad count

    Constants, and that is the point: the firmware's `pmod`/`bist mirror` answer
    then comes from the design that drives the pins, not from a table in the
    firmware that is right until someone reorders the domain list. `divisor` is
    here for the same reason -- a reader with a scope needs it to turn 20 MHz on
    a pad back into 80 MHz on the die.
    """

    def __init__(self, *, domains, divisor):
        unknown = [name for name in domains if name not in SOURCE_CODES]
        if unknown:
            raise ValueError(f"no SOURCE_CODES entry for {unknown}; the firmware "
                             f"cannot name a pad it has no code for")
        self._pads = sum(SOURCE_CODES[name] << (4 * index)
                         for index, name in enumerate(domains))
        self._info = (len(domains) << 8) | (divisor & 0xFF)

        self._pads_reg = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                      access="r")
        self._info_reg = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                      access="r")
        builder = csr.Builder(addr_width=3, data_width=8)
        builder.add("pads", self._pads_reg, offset=0x00)
        builder.add("info", self._info_reg, offset=0x04)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({"bus": In(csr.Signature(addr_width=3, data_width=8))})
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)
        m.d.comb += [
            self._pads_reg.f.value.r_data.eq(self._pads),
            self._info_reg.f.value.r_data.eq(self._info),
        ]
        return m
