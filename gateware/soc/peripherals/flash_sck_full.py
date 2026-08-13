"""SCK at the domain rate: one bit time per cycle, and a gated clock at the pad.

`luna_soc`'s PHY is `SCK = domain / (2 * (1 + divisor))` for two structural
reasons, and both have to go:

  * `SPIClockGenerator` toggles `clk` as a register, so one SCK period is two
    domain cycles;
  * `SPIPHYController` then drives the pad from `m.d.sync += pads.sck.eq(...)`,
    a second register.

Apollo's controller reaches `SCK = domain / (1 + divisor)` and has been measured
byte-exact on this board at **144 MHz SCK, 71.70 MB/s** (#100). This is the same
rate by the same route, on the PHY the SoC already has.

**Why a gated clock and not an `ODDRX1F`.** `USRMCLK` cannot have a DDR output:
nextpnr refuses it, Diamond's mapper refuses it (`DDR component cannot be packed
in any I/O logic block`), and prjtrellis shows the CCLK site has no
`DATAMUX_ODDR`/`IOLDO` mux. What the routing database DOES allow is a global
clock reaching `USRMCLKI` **through a LUT or FF** -- which is a gated clock, and
is what this uses. See `docs/chips/w25q32-config-flash.md`.

**What it costs.** One SCK period is now one domain cycle, so:

  * the read round trip -- SCK out, flash `tV`, pad in -- gets 16.7 ns rather
    than 33.3;
  * `en` must outlast the data, because sampling trails the launch by two bit
    times rather than one. Extra SCK edges inside one CS# are harmless on a
    read: the flash clocks out bytes nobody keeps.

Failure above the ceiling on this part is corrupt data rather than an error, so
`flash bench`'s content check is what judges it.
"""

from amaranth import ClockSignal, Elaboratable, Module, Signal


class FullRateClockGenerator(Elaboratable):
    """Drop-in for `SPIClockGenerator`, one bit time per domain cycle.

    Same port names so `SPIPHYController` needs no other change. `clk` is the
    pad's gate rather than a level to register: the caller must drive the pad
    combinationally, not through `m.d.sync`.
    """

    def __init__(self, divisor=0, domain="sync"):
        if divisor != 0:
            raise ValueError("full-rate SCK is the domain rate; a divisor would "
                             "halve it again, which is what this removes")
        self._domain = domain
        self.div     = divisor
        self.en      = Signal()
        self.clk     = Signal()
        self.sample  = Signal()
        self.update  = Signal()

    def elaborate(self, platform):
        m = Module()
        posedge_reg  = Signal()
        posedge_reg2 = Signal()

        m.d.comb += [
            self.clk    .eq(self.en),
            self.update .eq(self.en),
            self.sample .eq(posedge_reg2),
        ]
        m.d.sync += [
            posedge_reg .eq(self.en),
            posedge_reg2.eq(posedge_reg),
        ]

        if self._domain != "sync":
            from amaranth.hdl import DomainRenamer
            m = DomainRenamer({"sync": self._domain})(m)
        return m


def gated_sck(m, pads, clkgen, domain="sync"):
    """Drive SCK as the domain clock, gated -- not as a registered level.

    The LUT this becomes is the only path a global clock has to `USRMCLKI`.
    """
    m.d.comb += pads.sck.eq(ClockSignal(domain) & clkgen.clk)
