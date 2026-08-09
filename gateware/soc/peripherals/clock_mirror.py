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

__all__ = ["ClockMirror"]

# One PMOD pin per source, in order. Pin 0 is PMOD A pin 1.
MAX_SOURCES = 8


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
