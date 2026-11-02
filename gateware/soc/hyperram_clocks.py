#!/usr/bin/env python3
#
# A second PLL, so the HyperRAM clock is not the CPU clock. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""
`hr`, from the die's second PLL, independent of `sync`.

## Why the HyperRAM cannot keep using the CPU's clock

Device CK is `hr`, so the CPU clock and CK are separate knobs. Tying them
together would make every ladder rung move the CPU with it:

  * **The RISC-V tops out around 75 MHz.** The interesting half of the HyperRAM
    ladder is past the clock the CPU can be built at.
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

## The dividers are configuration-time; the SELECTION is not

`qspi_gateware.py` records that the ECP5's PLL output dividers "are programmed
during configuration and are not writable afterwards", and that a hand-built
`EHXPLLL` did not behave as documented -- `CLKOS_DIV=2` measured 480 MHz where
240 was asked for.

So the FREQUENCIES are per-bitstream. Which one is live is not: `DCSC` picks
between two PLL outputs of the same VCO at run time, glitchlessly, so CK
joins the runtime cross product instead of gating it. Two rungs per build,
`MAX_RUNGS`; the limit is silicon and is argued where it is enforced.
"""

from amaranth import ClockDomain, ClockSignal, Elaboratable, Instance, Module, Signal
from amaranth.lib.cdc import FFSynchronizer

__all__ = ["HyperRAMDomains", "reachable_ck",
           "solve_dcsc_rungs", "solve_hr_pll_rungs", "MAX_RUNGS"]

# Rungs one bitstream can carry, and it is a HARD limit of the silicon.
#
# `DCSC` is a 2:1 mux -- `DCSC(CLK1, CLK0, SEL1, SEL0, MODESEL, DCSOUT)` in
# `share/yosys/ecp5/cells_bb.v` -- so one of them selects between two clocks,
# not four. There are exactly two DCS bels on the die (`DCS0`, `DCS1`) and they
# do not cascade: the sources of `G_DCS0CLK0` (`tiledata/CMUX_UL_0/bits.db`) are
# the primary-clock feed wires and four `G_*CPCLKCIB0` fabric taps, and
# `G_DCSOUT_DCS*` is not among them. Chaining one into the other therefore costs
# a fabric hop in the clock path, on the domain whose edge placement is the
# thing being measured. Two rungs, no cascade.
MAX_RUNGS = 2

# EHXPLLL limits, from the ECP5 datasheet. Same window the SoC's own generator
# uses; duplicated here rather than imported because that one lives in the
# apollo submodule and this must not depend on it.
VCO_MIN_MHZ, VCO_MAX_MHZ, MAX_DIV = 400.0, 800.0, 128

# Phase-detector minimum, FPGA-DS-02012 Table 3.23. `CLKI / CLKI_DIV` must stay
# at or above it, and `clocks.py` has enforced it on the SoC's PLL all along.
#
# This file did not: `range(1, 8)` admitted CLKI_DIV 7, an 8.57 MHz phase
# detector off the 60 MHz reference. Every CK of the form 60*k/7 was reachable
# only that way -- 85.714286 among them, which is the rung #331/#332 measured
# latency codes at. The datasheet's note 3 does not say such a build fails; it
# says the jitter figures stop being guaranteed, so it fails as noise on the
# edge being measured rather than as a refusal. See #428.
PFD_MIN_MHZ = 10.0


# CLKI_DIV values the floor leaves, for a given reference. One definition, used
# by all four solvers -- they disagreed, and `solve_dcsc_rungs` was the only one
# that was right.
def _clki_range(input_mhz):
    return range(1, int(input_mhz // PFD_MIN_MHZ) + 1)


def solve_hr_pll_rungs(ck_list, input_mhz=60.0, tolerance=0.001):
    """One VCO and one output divider per CK in `ck_list`.

    CLKOP carries rung 0 AND the feedback, so two equations hold at once:
    `ck_list[0] = input * CLKFB_DIV / CLKI_DIV` fixes the loop, and
    `VCO = ck_list[0] * CLKOP_DIV` fixes the VCO. Every other rung is then a
    plain integer divisor of that same VCO -- which is the whole reason several
    CK values fit in one bitstream.

    Returns (vco, clki_div, clkfb_div, [div per rung]) or None.
    """
    first = ck_list[0]
    for clki_div in _clki_range(input_mhz):
        for clkfb_div in range(1, 81):
            if abs(input_mhz * clkfb_div / clki_div - first) > tolerance:
                continue
            for clkop_div in range(1, MAX_DIV + 1):
                vco = first * clkop_div
                if not VCO_MIN_MHZ <= vco <= VCO_MAX_MHZ:
                    continue
                divs = [clkop_div]
                for ck in ck_list[1:]:
                    div = round(vco / ck)
                    if not 1 <= div <= MAX_DIV or abs(vco / div - ck) > tolerance:
                        break
                    divs.append(div)
                else:
                    return (vco, clki_div, clkfb_div, divs)
    return None


def reachable_ck(low, high, input_mhz=60.0):
    """Every device CK this PLL can produce in [low, high], ascending."""
    out = []
    for clki in _clki_range(input_mhz):
        for clkfb in range(1, 81):
            ck = input_mhz * clkfb / clki
            if low - 1e-9 <= ck <= high + 1e-9 and solve_hr_pll_rungs(
                    [ck], input_mhz):
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
    # ECP5's 400-800 MHz VCO range. `_clki_range` is the 10 MHz phase-detector
    # floor -- the same limit that makes the single-output axis coarse.
    reachable = set()
    for clki in _clki_range(input_mhz):
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
    """`hr`, from the die's second PLL. `hr` IS device CK."""

    def __init__(self, *, ck_mhz, input_mhz=60.0):
        # A float or a sequence; a single rung behaves exactly like the scalar.
        self.ck_rungs = ([float(ck_mhz)] if isinstance(ck_mhz, (int, float))
                         else [float(ck) for ck in ck_mhz])
        if len(set(self.ck_rungs)) != len(self.ck_rungs):
            raise ValueError(f"duplicate CK rungs: {self.ck_rungs}")
        if len(self.ck_rungs) > MAX_RUNGS:
            raise ValueError(
                f"{len(self.ck_rungs)} rungs asked for, {MAX_RUNGS} available: "
                f"DCSC is a 2:1 mux and the two DCS bels do not cascade")

        # Rung 0. `ck_mhz` stays a scalar so every caller that reads it -- the
        # BIST engine's timing constants among them -- is unchanged.
        self.ck_mhz = self.ck_rungs[0]
        # Each rung is one output divider of the shared VCO; the four EHXPLLL
        # outputs are what makes several fit.
        self.hr_mhz = self.ck_mhz
        solved = solve_hr_pll_rungs(self.ck_rungs, input_mhz)
        if solved is None:
            raise ValueError(self._unreachable(input_mhz))
        (self.vco_mhz, self.clki_div, self.clkfb_div, self.rung_divs) = solved
        self.clkop_div = self.rung_divs[0]
        self.clkos_div = self.rung_divs[1] if len(self.rung_divs) > 1 else None

        # The FASTEST rung, because the placer times one net that carries either
        # rate. Constraining the domain at rung 0 would leave the other rung
        # timed against a frequency nobody built for -- the same class of PASS
        # that `add_clock_constraint` below exists to stop.
        self.hr_mhz_max = max(self.ck_rungs)
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

        # WHICH RUNG IS LIVE. A level, not a pulse: `DCSC` does the handoff and
        # is glitchless with both clocks running, which they always are here --
        # both are outputs of the same locked VCO.
        #
        # Present even with one rung, so a caller does not have to know how many
        # there are; it is then ignored rather than absent.
        self.sel = Signal(range(max(len(self.ck_rungs), 2)))

    def _unreachable(self, input_mhz):
        """Why the ladder rung asked for does not exist, and what does nearby."""
        rungs = ", ".join(f"{ck:g}" for ck in self.ck_rungs)
        near = reachable_ck(min(self.ck_rungs) - 20, max(self.ck_rungs) + 20,
                            input_mhz)
        return (f"no PLL configuration gives HyperRAM CK {rungs} MHz"
                + (" off one shared VCO" if len(self.ck_rungs) > 1 else "")
                + f"; VCO must land in {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz. "
                f"Reachable nearby: {near}")

    def elaborate(self, platform):
        m = Module()

        m.domains.hr = ClockDomain()

        # One net per rung, straight off the PLL, plus the net the domain
        # actually runs on. With one rung they are the same wire.
        clk_rung = [Signal(name=f"clk_hr_rung{index}")
                    for index in range(len(self.rung_divs))]
        clk_hr = clk_rung[0] if len(clk_rung) == 1 else Signal(name="clk_hr")
        locked_raw = Signal()

        clkos_div = self.clkos_div

        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8", a_MFG_ENABLE_FILTEROPAMP="1",
            a_MFG_GMCREF_SEL="2",
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
                "p_CLKOS_DIV": clkos_div,
                "p_CLKOS_CPHASE": clkos_div - 1,
                "p_CLKOS_FPHASE": 0} if clkos_div else {}),
            i_CLKI=self.clki,
            # THE FEEDBACK. `FEEDBK_PATH="CLKOP"` closes the loop through the
            # CLKOP output, so leaving CLKFB undriven opens it: the PLL never
            # locks and anything gated on `locked` is held for ever. The design
            # still configures and still builds, which is why this cost a
            # bisect the first time -- see `clocks.py`.
            i_CLKFB=clk_rung[0],
            i_RST=0, i_STDBY=0, i_PHASESEL0=0, i_PHASESEL1=0,
            i_PHASEDIR=1, i_PHASESTEP=1, i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk_rung[0],
            **({"o_CLKOS": clk_rung[1]} if len(clk_rung) > 1 else {}),
            o_LOCK=locked_raw,
        )

        if len(clk_rung) > 1:
            # THE RUNG SELECTOR, and the reason CK stops being a rebuild.
            #
            # `DCSC` is the ECP5's glitchless clock mux: it completes the cycle
            # in flight before handing over, so a `sel` write cannot produce a
            # runt on a running domain. SEL0/SEL1 are one-hot from one bit, so
            # the illegal both-high state is unreachable and the both-low state
            # -- which stops the clock -- cannot be typed by accident.
            #
            # `MODESEL=0` with `DCSMODE="POS"`: both inputs are outputs of the
            # same locked VCO and are always running, which is the case that
            # mode is for.
            #
            # DCSOUT reaches the PRIMARY clock network only (`G_DCSOUT_DCS*` ->
            # `G_*PCLK*`), never the edge-clock mux.
            m.submodules.ck_mux = Instance(
                "DCSC",
                p_DCSMODE="POS",
                i_CLK0=clk_rung[0],
                i_CLK1=clk_rung[1],
                i_SEL0=~self.sel[0],
                i_SEL1=self.sel[0],
                i_MODESEL=0,
                o_DCSOUT=clk_hr,
            )

        m.d.comb += ClockSignal("hr").eq(clk_hr)
        # TELL THE PLACER WHAT THESE RUN AT.
        #
        # Without this nextpnr times them against a default and prints a PASS
        # that is about a frequency nobody chose -- a 12 MHz constraint on a
        # domain intended to run at 50, reported as passing. Every number the
        # rig then produces is taken on a clock whose timing was never checked,
        # which is precisely the class of result this whole variant exists to
        # stop producing.
        #
        # `hr_mhz_max`, not `hr_mhz`: with more than one rung the domain is one
        # net carrying either rate, and timing it at rung 0 leaves the faster
        # rung unchecked.
        if platform is not None:
            platform.add_clock_constraint(clk_hr, self.hr_mhz_max * 1e6)

        # LOCK is asynchronous to everything. Synchronised into `hr` because
        # that is the domain the engine gates on.
        m.submodules += FFSynchronizer(locked_raw, self.locked, o_domain="hr")

        return m
