#!/usr/bin/env python3
#
# `usb` from the oscillator so the CPU clock stops being hostage to it.
# SPDX-License-Identifier: BSD-3-Clause

"""SoC clock domains, with the USB PHY on the oscillator rather than a PLL.

## The problem this removes

`VariableClockDomainGenerator` solves `sync` and `usb` from ONE PLL. Both divide
one VCO, so making `usb` land on exactly 60.000 MHz constrains `sync` too --
across 60..130 MHz only **60, 100 and 120** satisfy both. That is the origin of
"the CPU can only run at three frequencies", and it is a property of the
topology, not of the part.

The 0.5% tolerance it enforces on `usb` is not paranoia. A 90 MHz `sync` build
put `usb` at 63.000 MHz, placed and configured cleanly, and never appeared on the
USB bus -- while a *higher* 100 MHz build with `usb` at exactly 60.000 enumerated
at once. A source-synchronous parallel interface has no mechanism to absorb a
frequency error, and the failure looks exactly like a design that missed timing.

## Why it does not have to exist

**The board's primary clock is a discrete 60 MHz oscillator on A8, and the FPGA
SOURCES the ULPI clock** -- `clk_dir='o'` on all three PHY resources in
`cynthion_r1_4.py`. So `usb` can be that oscillator passed straight through:
exactly 60.000 MHz by construction, with less jitter than a PLL copy, and no
tolerance left to check.

`sync` is then free, within the PLL's own specification. From a 60 MHz reference
**13 integer frequencies between 63 and 130 MHz are reachable in spec**:

    70  72  75  80  84  90  96  100  105  108  110  120  130

That is 13 rather than 68 because of `PFD_MIN_MHZ` below, and the difference is
the point: the other 55 are reachable only by dividing the reference below the
phase detector's minimum, where the datasheet stops guaranteeing jitter. A
solver that offered them would be offering configurations that build and then
misbehave as noise.

13 is four times what sharing a VCO with the USB PHY allows, and the constraint
is the PLL's published limit rather than an accident of that sharing.

60 MHz appears in this file exactly once, as the USB PHY's requirement. It is
not the CPU's number and never was.

## What this does NOT do

It does not give the HyperRAM its own clock. `fast` here is still a second output
of the SAME PLL as `sync`: the flash PHY runs its logic in that domain, the DQS
PHY takes it as an EDGE clock, and the DQS 4:1 gearing requires it to be exactly
twice its fabric domain.

`fast` is CLKOS and not CLKOS2, which is not interchangeable -- see `elaborate`.

`hyperram_clocks.HyperRAMDomains` is the second PLL, and composes with this one:
both take the oscillator as their reference, so neither is fed from the other's
output. Instantiating it is what makes CK sweepable without touching the CPU, and
it is not wired into `top.py` yet (#230).
"""

import os

from amaranth import (ClockDomain, ClockSignal, Elaboratable, Instance, Module,
                      ResetSignal, Signal)

__all__ = ["SocClocks", "solve_pll", "reachable"]

# EHXPLLL limits. The VCO window is the binding one; the divider ranges are wide
# enough that they rarely are.
VCO_MIN_MHZ = 400.0
VCO_MAX_MHZ = 800.0
DIV_MAX = 128

# Phase-detector minimum, datasheet FPGA-DS-02012 Table 3.23. `CLKI / CLKI_DIV`
# must stay at or above this.
#
# NOT optional, and it is easy to violate without noticing: the solver will
# happily divide a 60 MHz reference by 60 to reach an awkward target, giving a
# 1 MHz phase detector. That configuration builds, and the datasheet's note 3
# says the jitter figures are simply not guaranteed there -- so it fails, if it
# fails, as noise rather than as a refusal.
#
# Measured before this check existed: 55 of the 68 integer frequencies from 63
# to 130 MHz were reachable only by breaking it.
PFD_MIN_MHZ = 10.0

# The ULPI PHY's requirement, and the only reason this number appears here.
USB_PHY_MHZ = 60.0

# What the FABRIC has been observed to close at, as distinct from what the PLL
# can produce. The two are different questions and only one of them was being
# asked.
#
# `solve_pll` will happily return dividers for 130 MHz. The design's own recorded
# timing says the fabric will not follow: an 8 ns bus path, and the BTB closing
# at 57.55 MHz unrelaxed. So a build could be requested that is guaranteed to
# fail place-and-route, and the only signal was a synthesis run 90-120 s later
# with a message about a net rather than about the request.
#
# 90 MHz is deliberately generous against the ~65-79 MHz this design has actually
# measured across placements -- the point is to refuse the absurd, not to
# second-guess a build that might close.
#
# `CYNTHION_SYNC_CEILING_MHZ` raises it, because the ceiling is a measurement of
# one design at one moment and asking what the DIE does past what nextpnr
# predicts is a legitimate experiment (#470). Not in `VARIANT_ENV`: it permits a
# build, it does not change what is elaborated at a given `sync`.
#
# Same class as the `fPFD` floor above: the solver knows something the caller
# does not, and staying silent about it costs a build.
SYNC_CEILING_MHZ = float(os.environ.get("CYNTHION_SYNC_CEILING_MHZ") or 90.0)

# How long RESETB is held low, in `usb` cycles. The USB334x datasheet Rev 1.2
# section 5.6.2: cycling RESETB resets the ULPI registers to their defaults and
# every internal state machine, "by bringing the pin low for a minimum of 1
# microsecond and then high". 128 cycles is 2.133 us at 60.000 MHz -- twice the
# minimum, and this clock is the oscillator rather than a PLL output, so the
# figure is exact rather than rounded.
PHY_PAD_RESET_CYCLES = 128

# How long after RESETB is released before the PHY may be spoken to, in `usb`
# cycles. TPREP is 1.0..1.2 ms with a 60 MHz REFCLK and LPM disabled (Table 4.3),
# measured from REFCLK and RESETB both valid to the PHY de-asserting DIR. Until
# then the bus is not the link's to drive. 72000 cycles is exactly 1.200 ms at
# 60.000 MHz, which is the specified maximum and not a guess about the typical.
#
# Both constants are used twice: here for the power-on sequence, and in
# `peripherals/ulpi_window.py` for the CSR-driven one. One definition, because a
# datasheet number copied is a datasheet number that will diverge.
PHY_PREP_CYCLES = 72_000


def solve_pll(target_mhz, input_mhz=60.0, *, ratio=None, tolerance=1e-6):
    """Dividers for `target_mhz` on CLKOP, and `ratio * target` on CLKOS.

    `FEEDBK_PATH="CLKOP"` means the feedback is taken AFTER the output divider,
    so CLKOP is `input / CLKI_DIV * CLKFB_DIV` and the VCO is that times
    CLKOP_DIV. Both outputs divide the one VCO, which is why `ratio` is a
    property of the pair and not a free choice per output.

    CLKOS rather than CLKOS2: only CLKOP and CLKOS reach an edge clock, and
    `fast` is one. See `SocClocks.elaborate` and #314.

    Returns `(vco, clki_div, clkfb_div, clkop_div, clkos_div)` or None.
    `clkos_div` is None when no ratio was asked for.
    """
    for clki_div in range(1, DIV_MAX + 1):
        # The phase detector sees the divided reference, and it has a floor.
        if input_mhz / clki_div < PFD_MIN_MHZ:
            break
        for clkfb_div in range(1, DIV_MAX + 1):
            if abs(input_mhz / clki_div * clkfb_div - target_mhz) > tolerance:
                continue
            for clkop_div in range(1, DIV_MAX + 1):
                vco = target_mhz * clkop_div
                if not (VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ):
                    continue
                if ratio is None:
                    return (vco, clki_div, clkfb_div, clkop_div, None)
                # The second output is the same VCO divided again, so the ratio
                # is only reachable when it divides CLKOP_DIV exactly. SKIPPED,
                # not rounded: the search moves to a larger CLKOP_DIV, and if
                # none fits the VCO window `SocClocks` refuses with the reachable
                # list. A rounded `fast` corrupts DDR data rather than failing.
                if clkop_div % ratio:
                    continue
                return (vco, clki_div, clkfb_div, clkop_div, clkop_div // ratio)
    return None


def reachable(low, high, input_mhz=60.0, *, ratio=None):
    """Every frequency in `low..high` this generator can actually produce."""
    return sorted({round(input_mhz / ki * kfb, 6)
                   for ki in range(1, DIV_MAX + 1)
                   for kfb in range(1, DIV_MAX + 1)
                   if low <= input_mhz / ki * kfb <= high
                   and solve_pll(input_mhz / ki * kfb, input_mhz,
                                 ratio=ratio) is not None})


class SocClocks(Elaboratable):
    """`usb` straight off the oscillator, `sync` (and optionally `fast`) on PLL0.

    Parameters
    ----------
    sync_mhz : float
        The CPU clock. Free -- not tied to the PHY's 60 MHz.
    with_fast : bool
        Build the `fast` edge-clock domain. Needed by the flash PHY and by the
        HyperRAM DQS PHY; nothing else reads it.
    fast_ratio : int
        `fast = fast_ratio * sync`, and it must be an integer because both come
        from one VCO. The DQS PHY's 4:1 gearing requires exactly 2.
    input_mhz : float
        The board oscillator. 60 MHz on this board, and `usb` IS it.
    """

    def __init__(self, *, sync_mhz, with_fast=False, fast_ratio=2,
                 input_mhz=USB_PHY_MHZ, ceiling_mhz=SYNC_CEILING_MHZ):
        # Refused HERE, before a solve that would succeed and a synthesis that
        # would not. The PLL reaching a frequency says nothing about the fabric
        # closing at it, and until this existed the two were conflated: an
        # out-of-reach request produced a place-and-route failure two minutes
        # later, reported as a message about a net.
        if ceiling_mhz is not None and sync_mhz > ceiling_mhz:
            raise ValueError(
                f"sync = {sync_mhz:g} MHz is above the {ceiling_mhz:g} MHz this "
                f"fabric has been observed to close at. The PLL can produce it; "
                f"place-and-route is not expected to, and the failure would "
                f"arrive ~2 minutes from now as a message about a net rather "
                f"than about this number. Measured on this design: 64.5-79.4 MHz "
                f"across placements. Pass `ceiling_mhz=` to try anyway, or "
                f"`ceiling_mhz=None` to disable the check.")

        self.sync_mhz = sync_mhz
        self.with_fast = with_fast
        self.fast_ratio = fast_ratio
        self.input_mhz = input_mhz

        if abs(input_mhz - USB_PHY_MHZ) > 1e-9:
            raise ValueError(
                f"`usb` is the {input_mhz:g} MHz oscillator passed through, and "
                f"the ULPI PHY needs exactly {USB_PHY_MHZ:g} MHz. A board with a "
                f"different oscillator needs a PLL output for `usb`, and this "
                f"class is the wrong one for it.")

        solved = solve_pll(sync_mhz, input_mhz,
                           ratio=fast_ratio if with_fast else None)
        if solved is None:
            nearby = [f for f in reachable(sync_mhz - 10, sync_mhz + 10,
                                           input_mhz,
                                           ratio=fast_ratio if with_fast else None)]
            raise ValueError(
                f"no PLL configuration gives sync = {sync_mhz:g} MHz"
                + (f" with fast = {fast_ratio}x it" if with_fast else "")
                + f"; the VCO must land in {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz. "
                + (f"Reachable nearby: {[round(f, 3) for f in nearby]}"
                   if nearby else "Nothing nearby is reachable either."))

        (self.vco_mhz, self.clki_div, self.clkfb_div,
         self.clkop_div, self.clkos_div) = solved

        # The solver's own invariant, restated where the division is USED. A
        # `fast` at half of what the gearing expects builds cleanly and returns
        # wrong data, so this must never be reached by a later edit to `solve_pll`.
        if with_fast and self.clkop_div % fast_ratio:
            raise ValueError(
                f"CLKOP_DIV = {self.clkop_div} is not divisible by "
                f"fast_ratio = {fast_ratio}; CLKOS divides the same VCO and "
                f"cannot reach {fast_ratio}x sync from it.")

        # What the hardware will ACTUALLY produce, recomputed from the dividers
        # rather than echoed from the request. The firmware reports these and the
        # console baud divisor is derived from `actual_sync_mhz`, so a generator
        # that returned the requested value would make a rounded clock present as
        # a mangled console rather than as a wrong number.
        #
        # `solve_pll` only accepts an exact match, so today these equal what was
        # asked for. They are computed anyway: the day that solver learns to
        # approximate, everything downstream is already reading the truth.
        self.actual_sync_mhz = input_mhz / self.clki_div * self.clkfb_div
        self.actual_fast_mhz = (self.vco_mhz / self.clkos_div
                                if self.clkos_div else None)

        # `usb` IS the oscillator. Not a PLL output, so there is nothing to
        # round: it is the input frequency, exactly, by construction.
        self.actual_usb_mhz = input_mhz

        # The PLL's LOCK, brought out so something can SEE it. It has existed in
        # every generator here and never left the module: a PLL that failed to
        # lock could only be inferred from its consequences, which is how an
        # unconnected CLKFB cost a two-rebuild bisect.
        self.locked = Signal()

        # The ULPI PHYs' reset pad, active high here and inverted at the pin.
        # Brought out so `top.py` can OR it with the CSR-driven reset without
        # either of them owning the pad alone. See `elaborate` for the two
        # durations and where they come from.
        self.phy_reset = Signal()
        # Asserted once the power-on sequence has finished and the PHYs have had
        # their preparation time. `usb`'s reset is its inverse.
        self.phy_ready = Signal()

    def elaborate(self, platform):
        m = Module()

        m.domains.usb = ClockDomain()
        m.domains.sync = ClockDomain()
        if self.with_fast:
            m.domains.fast = ClockDomain()

        # The domain the power-on reset itself lives in. It is `usb`'s clock with
        # no reset, and it has to be: a counter that releases `usb`'s reset
        # cannot be held by that same reset, or it never counts and the release
        # never comes.
        m.domains.usb_por = ClockDomain(reset_less=True)

        osc = Signal()
        if platform is not None:
            m.d.comb += osc.eq(platform.request("clk_60MHz", 0).i)

        clk_sync = Signal()
        clk_fast = Signal()
        locked = Signal()

        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8",
            a_MFG_ENABLE_FILTEROPAMP="1", a_MFG_GMCREF_SEL="2",
            # PINNED, and only when there is an edge clock to lose. X2/Y49 is the
            # one PLL bel on the left ECLK mux, so a placer that swaps this
            # design's PLLs silently drops `fast` back onto fabric with no error.
            # Two PLLs both wanting an ECLK cannot coexist: nextpnr then refuses
            # the bel outright, which is the loud failure and the intended one.
            **({"a_BEL": "X2/Y49/EHXPLL_LL"} if self.with_fast else {}),
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
            # CLKOS, NOT CLKOS2 -- the edge-clock network cannot see CLKOS2.
            # The bank ECLK mux takes CLKOP/CLKOS from either left PLL, four
            # clock pads and two fabric taps; CLKOS2/CLKOS3 are primary-network
            # only, so an ECLK from CLKOS2 arrives through the fabric tap with
            # skew nobody bounded, announced as `log_info` and not a warning.
            # `HyperRAMDQSPHY` reads `fast` as its ECLK. #314
            **({"p_CLKOS_ENABLE": "ENABLED",
                "p_CLKOS_DIV": self.clkos_div,
                "p_CLKOS_CPHASE": self.clkos_div - 1,
                "p_CLKOS_FPHASE": 0} if self.with_fast else {}),
            i_CLKI=osc,
            # THE FEEDBACK. `FEEDBK_PATH="CLKOP"` means the loop is closed
            # through the CLKOP output, so CLKFB must be driven from it. Leaving
            # it unconnected opens the loop: the PLL never locks, `sync` is held
            # in reset for ever by the line below, and the design configures
            # cleanly and does nothing. It cost a bisect to find, because a
            # bitstream that builds and loads but never runs looks like a
            # timing failure or a bad frequency rather than a missing wire.
            i_CLKFB=clk_sync,
            i_RST=0, i_STDBY=0, i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk_sync,
            **({"o_CLKOS": clk_fast} if self.with_fast else {}),
            o_LOCK=locked,
        )
        m.d.comb += self.locked.eq(locked)

        # ---- the ULPI PHYs' power-on reset ----------------------------------
        #
        # `usb` does not wait for the PLL. It is the oscillator, running before
        # the PLL is asked for anything, and holding it until an unrelated PLL
        # locks would reinvent the dependency this topology removes. But it must
        # not be tied to 0 either: `usb`'s reset is the ONLY reset the three
        # USB3343s have. Two of the three pads are driven from it -- `top.py`
        # for TARGET, and LUNA's `UTMITranslator` for AUX -- and all three
        # declare `rst_invert=True`, so a constant 0 leaves them permanently
        # de-asserted. Cold power-up survives that on the PHY's own internal POR;
        # warm reconfiguration does not, because reflashing does not power-cycle
        # the part, and a PHY carrying a stuck bus turn from the previous
        # bitstream keeps it.
        #
        # The pad pulses low for `PHY_PAD_RESET_CYCLES`, then the FPGA side stays
        # in reset for the whole of TPREP: everything in `usb` starts on a PHY
        # that has finished coming up, rather than racing it. On expiry the
        # domain runs for the rest of the session -- this is a power-on reset,
        # not a watchdog, and the way back afterwards is the CSR in
        # `peripherals/ulpi_window.py`.
        settled = PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES
        por = Signal(range(settled + 1))
        with m.If(por != settled):
            m.d.usb_por += por.eq(por + 1)
        m.d.comb += [
            self.phy_reset.eq(por < PHY_PAD_RESET_CYCLES),
            self.phy_ready.eq(por == settled),
        ]

        m.d.comb += [
            ClockSignal("usb_por").eq(osc),
            ClockSignal("usb").eq(osc),
            ClockSignal("sync").eq(clk_sync),

            # `sync` waits for lock; a domain clocked by an unlocked PLL sees a
            # frequency that drifts as it settles.
            ResetSignal("sync").eq(~locked),

            ResetSignal("usb").eq(~self.phy_ready),
        ]
        if self.with_fast:
            m.d.comb += [ClockSignal("fast").eq(clk_fast),
                         ResetSignal("fast").eq(~locked)]

        # TELL THE PLACER. Without a constraint nextpnr times a domain against a
        # default and prints a PASS about a frequency nobody chose -- which has
        # already happened once in this tree, on a domain whose PLL was dead.
        if platform is not None:
            platform.add_clock_constraint(clk_sync, self.sync_mhz * 1e6)
            if self.with_fast:
                platform.add_clock_constraint(
                    clk_fast, self.fast_ratio * self.sync_mhz * 1e6)

        return m
