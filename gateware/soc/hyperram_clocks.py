#!/usr/bin/env python3
#
# A second PLL, so the HyperRAM clock is not the CPU clock. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""
`hr` and `hr_fast`, from the die's second PLL, independent of `sync`.

## Why the HyperRAM cannot keep using the CPU's clock

`HyperRAMDQSPHY` gears off `fast` at twice `sync`, so device CK is `2 * SYNC_MHZ`
and every ladder rung moves the CPU with it. Two things make that untenable:

  * **The RISC-V tops out around 75 MHz.** CK 140 needs `sync` 70 and CK 180
    needs 90, so the interesting half of the HyperRAM ladder is past the clock
    the CPU can be built at. The part's ceiling and the core's ceiling are
    different questions and one of them was silently answering the other.
  * **A swept CPU clock is a swept measurement.** Every rung changes the console
    divisor, the CLINT tick, the flash SCK divisor and the cache refill timing
    at the same time as CK. Nothing measured across rungs is a controlled
    comparison.

The 25F die has PLL sites `PLL0_LL`, `PLL1_LR` and `PLL0_LR`, and the SoC uses
one. This takes a second, leaving `sync`, `usb` and `fast` exactly as they were.

## What crosses, and what does not

Nothing streams between the domains. The BIST engine lives entirely in `hr`; the
CPU writes static parameters before a pass and reads counters after one. The
crossing is a `go` pulse in and a `done` pulse out, both through
`FFSynchronizer`, with the parameter and counter registers held still on either
side of them by construction rather than by a FIFO.

That is deliberate. `stream_buffer.py` records a `SyncFIFOBuffered` between two
domains that "worked perfectly while both were 60, then produced a stream with
correct counter values and dropped characters" once they differed -- a
continuous crossing is where that lives, and there is no continuous crossing
here.

## The dividers are configuration-time

`qspi_gateware.py` records that the ECP5's PLL output dividers "are programmed
during configuration and are not writable afterwards", and that a hand-built
`EHXPLLL` did not behave as documented -- `CLKOS_DIV=2` measured 480 MHz where
240 was asked for. So CK is per-bitstream: one build per rung, and every
runtime axis swept inside it.
"""

from amaranth import ClockDomain, ClockSignal, Elaboratable, Instance, Module, Signal
from amaranth.lib.cdc import FFSynchronizer

__all__ = ["HyperRAMDomains", "solve_hr_pll", "reachable_ck",
           "solve_dcsc_rungs"]

# EHXPLLL limits, from the ECP5 datasheet. Same window the SoC's own generator
# uses; duplicated here rather than imported because that one lives in the
# apollo submodule and this must not depend on it.
VCO_MIN_MHZ, VCO_MAX_MHZ, MAX_DIV = 400.0, 800.0, 128


def solve_hr_pll(hr_mhz, input_mhz=60.0, with_fast=True, tolerance=0.001):
    """Dividers giving `hr`, and `hr_fast` at twice it when the DQS PHY needs it.

    Feedback is taken from CLKOP, so CLKFB_DIV counts *output* periods:
    VCO = input * CLKFB_DIV * CLKOP_DIV / CLKI_DIV, and therefore
    hr = input * CLKFB_DIV / CLKI_DIV independently of CLKOP_DIV. Treating
    CLKFB_DIV as a plain VCO multiplier is the mistake `variable_clock.py`
    records as having produced clocks at twice the requested rate.

    Unlike the SoC's generator this solves for no `usb`, because this PLL drives
    nothing that enumerates. That is the whole reason it is a second PLL.

    Returns (vco, clki_div, clkfb_div, clkop_div, clkos_div) or None.

    `hr_fast` is CLKOS, not CLKOS2: only CLKOP and CLKOS reach the ECP5's
    edge-clock input mux. See `elaborate`.
    """
    for clki_div in range(1, 8):
        for clkfb_div in range(1, 81):
            if abs(input_mhz * clkfb_div / clki_div - hr_mhz) > tolerance:
                continue
            for clkop_div in range(1, MAX_DIV + 1):
                vco = hr_mhz * clkop_div
                if not VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ:
                    continue
                # `hr_fast` divides the same VCO, so it exists only where
                # CLKOP_DIV is even. Rejected here rather than checked after:
                # a silently-wrong fast clock corrupts data rather than
                # failing to build.
                if with_fast and clkop_div % 2:
                    continue
                clkos = clkop_div // 2 if with_fast else None
                return (vco, clki_div, clkfb_div, clkop_div, clkos)
    return None


def reachable_ck(low, high, dqs=True, input_mhz=60.0):
    """Every device CK this PLL can produce in [low, high], ascending.

    CK is `2 * hr` on the DQS PHY and `hr` on the non-DQS one, so the rungs
    differ between them and quoting one ladder for both is how a comparison
    stops being a comparison.
    """
    out = []
    for clki in range(1, 8):
        for clkfb in range(1, 81):
            hr = input_mhz * clkfb / clki
            ck = 2 * hr if dqs else hr
            if low - 1e-9 <= ck <= high + 1e-9 and solve_hr_pll(
                    hr, input_mhz, with_fast=dqs):
                out.append(round(ck, 6))
    return sorted(set(out))


def solve_dcsc_rungs(low, high, outputs=4, input_mhz=60.0, step=5.0):
    """VCOs and divisors whose outputs cover `low`..`high` at `step` spacing.

    **This is what makes CK a runtime axis.** `EHXPLLL` has four outputs --
    CLKOP, CLKOS, CLKOS2, CLKOS3 -- each with an independent divider off the
    SHARED VCO, and `DCSC` selects between clock sources glitchlessly at run
    time. So one bitstream carries up to `outputs` CK values and the CPU picks
    one with a register write.

    Not a fabric divider: every rung is a real PLL output, so there is no added
    jitter and the DDR gearing rides it normally.

    The dividers are `p_` parameters -- bitstream config bits -- so the
    FREQUENCIES are fixed per build; only the SELECTION is runtime. That is the
    whole trick, and it turns ~31 bitstreams for a 5 MHz grid into a handful.

    Returns a list of (vco_mhz, [ck_mhz, ...]), greedy-smallest-first.
    """
    # Reachable VCOs: 60 * clkfb/clki, times an output divider, inside the
    # ECP5's 400-800 MHz VCO range. `clki` stops at 6 for the 10 MHz phase
    # detector floor -- the same limit that makes the single-output axis coarse.
    reachable = set()
    for clki in range(1, 7):
        for clkfb in range(1, 81):
            base = input_mhz * clkfb / clki
            for div in range(1, MAX_DIV + 1):
                vco = base * div
                if 400 <= vco <= 800:
                    reachable.add(round(vco, 3))

    def rungs_of(vco):
        """The outputs of this VCO that land in range, best `outputs` of them."""
        return sorted({round(vco / d, 1) for d in range(1, MAX_DIV + 1)
                       if low <= vco / d <= high})

    targets = [low + step * i for i in range(int((high - low) / step) + 1)]
    chosen, covered = [], set()
    while len(covered) < len(targets):
        def gain_of(vco):
            # Only `outputs` of them can be built, so score the best subset.
            hits = [t for t in targets
                    if any(abs(t - r) <= step / 2 for r in rungs_of(vco))]
            return len(set(hits[:outputs]) - covered)
        best = max(reachable, key=gain_of)
        if not gain_of(best):
            break                      # nothing left reaches an uncovered target
        keep = [r for r in rungs_of(best)][:outputs]
        chosen.append((best, keep))
        covered |= {t for t in targets
                    if any(abs(t - r) <= step / 2 for r in keep)}
    return chosen


class HyperRAMDomains(Elaboratable):
    """`hr`, and `hr_fast` at twice it, from the die's second PLL.

    `ck_mhz` is the DEVICE clock, not the fabric one: the DQS PHY gears 4:1 off
    `hr_fast` and emits two CK per `hr` cycle, so `hr = ck_mhz / 2` there and
    `hr = ck_mhz` on the non-DQS path. Taking the device number as the argument
    means a caller cannot get the factor of two wrong, which is the mistake
    every ladder in this tree has made at least once.
    """

    def __init__(self, *, ck_mhz, dqs=True, input_mhz=60.0):
        self.ck_mhz = ck_mhz
        self.dqs = dqs
        self.hr_mhz = ck_mhz / 2 if dqs else ck_mhz

        solved = solve_hr_pll(self.hr_mhz, input_mhz, with_fast=dqs)
        if solved is None:
            raise ValueError(
                f"no PLL configuration gives a HyperRAM CK of {ck_mhz:g} MHz "
                f"(fabric {self.hr_mhz:g} MHz)"
                + (" with hr_fast at twice it" if dqs else "")
                + f"; VCO must land in {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz. "
                f"Reachable nearby: {reachable_ck(ck_mhz - 20, ck_mhz + 20, dqs)}")

        (self.vco_mhz, self.clki_div, self.clkfb_div,
         self.clkop_div, self.clkos_div) = solved
        self.input_mhz = input_mhz

        # Held low until the PLL locks, so the engine cannot start a transaction
        # on a clock that is still moving.
        self.locked = Signal()

        # The reference, driven by the caller. `ThreeDomainClocks` wires it to
        # the board oscillator; a design whose own generator has already
        # requested `clk_60MHz` must pass an equivalent 60 MHz net, because
        # Amaranth allows one requester per resource.
        #
        # Public, and checked at elaboration. It was private and undriven once:
        # the PLL then had a constant 0 for a reference, never locked, and the
        # build still succeeded -- nextpnr timed the dead domain against a
        # default constraint and reported PASS.
        self.clki = Signal()

    def elaborate(self, platform):
        m = Module()

        m.domains.hr = ClockDomain()
        if self.dqs:
            m.domains.hr_fast = ClockDomain()

        clk_hr = Signal()
        clk_hr_fast = Signal()
        locked_raw = Signal()

        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8", a_MFG_ENABLE_FILTEROPAMP="1",
            a_MFG_GMCREF_SEL="2",
            **({"a_BEL": "X2/Y49/EHXPLL_LL"} if self.dqs else {}),
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            p_STDBY_ENABLE="DISABLED", p_DPHASE_SOURCE="DISABLED",
            p_OUTDIVIDER_MUXA="DIVA", p_OUTDIVIDER_MUXB="DIVB",
            p_OUTDIVIDER_MUXC="DIVC", p_OUTDIVIDER_MUXD="DIVD",
            p_CLKI_DIV=self.clki_div,
            p_CLKFB_DIV=self.clkfb_div,
            p_FEEDBK_PATH="CLKOP",
            p_CLKOP_ENABLE="ENABLED",
            p_CLKOP_DIV=self.clkop_div,
            p_CLKOP_CPHASE=self.clkop_div - 1,
            p_CLKOP_FPHASE=0,
            **({"p_CLKOS_ENABLE": "ENABLED",
                "p_CLKOS_DIV": self.clkos_div,
                "p_CLKOS_CPHASE": self.clkos_div - 1,
                "p_CLKOS_FPHASE": 0} if self.dqs else {}),
            i_CLKI=self.clki,
            # THE FEEDBACK. `FEEDBK_PATH="CLKOP"` closes the loop through the
            # CLKOP output, so leaving CLKFB undriven opens it: the PLL never
            # locks and anything gated on `locked` is held for ever. The design
            # still configures and still builds, which is why this cost a
            # bisect the first time -- see `clocks.py`.
            i_CLKFB=clk_hr,
            i_RST=0, i_STDBY=0, i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk_hr,
            **({"o_CLKOS": clk_hr_fast} if self.dqs else {}),
            o_LOCK=locked_raw,
        )

        m.d.comb += ClockSignal("hr").eq(clk_hr)
        if self.dqs:
            # CLKOS, NOT CLKOS2 -- the edge clock network cannot see CLKOS2.
            #
            # `hr_fast` is the ECLK for the DQS PHY's ODDRX2F gearing and
            # DQSBUFM capture, so it must reach the bank's edge-clock spine on
            # dedicated routing, not fabric. The bank ECLK input mux
            # (`W2_ECLKI0` / `W2_JECLKI1` in the ECP5 `ECLK_L` tile) takes only
            # `G_J{LL,UL}CPLL0CLK{OP,OS}`, the four `G_JPCLKT*` clock pins, and
            # the two `*QECLKCIB*` fabric taps. CLKOS2/CLKOS3 exist only on the
            # primary clock network, so an ECLK sourced from CLKOS2 can only
            # arrive through the fabric tap -- which nextpnr refuses on the
            # dedicated pass and then falls back to general routing with
            # unconstrained skew. See #314.
            #
            # No ECLKBRIDGECS: its CLK0/CLK1 are hardwired to those same two
            # input muxes, so it bridges banks, not distance, and cannot rescue
            # a source the mux does not accept. The lower-left PLL feeds bank 7
            # directly -- there is no die to cross.
            m.d.comb += ClockSignal("hr_fast").eq(clk_hr_fast)

        # TELL THE PLACER WHAT THESE RUN AT.
        #
        # Without this nextpnr times them against a default and prints a PASS
        # that is about a frequency nobody chose -- a 12 MHz constraint on a
        # domain intended to run at 50, reported as passing. Every number the
        # rig then produces is taken on a clock whose timing was never checked,
        # which is precisely the class of result this whole variant exists to
        # stop producing.
        if platform is not None:
            platform.add_clock_constraint(clk_hr, self.hr_mhz * 1e6)
            if self.dqs:
                platform.add_clock_constraint(clk_hr_fast, 2 * self.hr_mhz * 1e6)

        # LOCK is asynchronous to everything. Synchronised into `hr` because
        # that is the domain the engine gates on.
        m.submodules += FFSynchronizer(locked_raw, self.locked, o_domain="hr")

        return m
