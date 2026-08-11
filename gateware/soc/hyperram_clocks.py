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

## The dividers are configuration-time; the SELECTION is not

`qspi_gateware.py` records that the ECP5's PLL output dividers "are programmed
during configuration and are not writable afterwards", and that a hand-built
`EHXPLLL` did not behave as documented -- `CLKOS_DIV=2` measured 480 MHz where
240 was asked for.

So the FREQUENCIES are per-bitstream. Which one is live is not: `DCSC` picks
between two PLL outputs of the same VCO at run time, glitchlessly, so on the
non-DQS path CK joins the runtime cross product instead of gating it. Two rungs
per build, `MAX_RUNGS`, and the DQS path is fixed at one -- both limits are
silicon and are argued where they are enforced.

## Dynamic phase shift (#228)

`phase=True` gives `DPHASE_SOURCE="ENABLED"` and drives the five phase ports from
fabric instead of tying them off. Both move together: the ports were driven with
the attribute DISABLED before, so the shifter was unreachable in every build.
`peripherals/pll_phase.py` is the CSR the CPU writes; the silicon rules are here.

Default is `phase=False`, which is what every build had -- the attribute's effect
on a PLL whose phase ports are held at 0 is documented nowhere local, so a build
that does not want the lever does not get the config bit either.

  * **One step is 1/8 of a VCO period**, whatever the output divider. A full
    360 degrees of an output is `8 * CLKOx_DIV` steps. (Diamond 3.14's
    `EHXPLLL.v`: `vco_ph_del = ((FPHASE + step) * t_out) / (8 * DIV)`.)
  * **PHASESEL is not natural binary**: CLKOP=0b11, CLKOS=0b00, CLKOS2=0b01,
    CLKOS3=0b10 (Diamond `PLL_Options_XO2.htm`; same in the sim model).
  * **CLKOP MUST NOT be shifted here.** `FEEDBK_PATH="CLKOP"`, so a shift on
    CLKOP is inside the loop: the PLL corrects it and every OTHER output moves
    instead. The sim model prints a warning for exactly this. The port is still
    an input -- refusing it in gateware would cost a rebuild to find out what it
    does -- and `pll_phase.py` names it as the trap.
  * The shifter sits AFTER the loop, per-output (ECP5 datasheet Fig 2.5, p.18),
    so shifting CLKOS/CLKOS2/CLKOS3 leaves the others alone.
  * Pulse widths, ECP5 datasheet Table 3.23 p.71: PHASESTEP at least 4 VCO
    cycles in each state, PHASESEL/PHASEDIR stable 5 ns before it. nextpnr
    returns `TMG_IGNORE` for every EHXPLLL port (`ecp5/arch.cc`), so NOTHING in
    the flow checks those -- they are met by construction in `pll_phase.py`.

## The probe clock

`phase=True` adds CLKOS2 at rung 0's frequency and a `hr_probe` domain on
it, for no purpose but to be shifted and watched.

A phase lever with no observation is the class of instrument this project has
been burned by (#294). `probe_level` is a `hr`-domain toggle sampled by
`hr_probe` and XORed back against itself: two outputs of one VCO at one
frequency, so the sample point sits at a FIXED place in the toggle and the level
is a constant -- until the phase moves it across an edge, when it flips. One flip
per `4 * CLKOP_DIV` steps, which measures the step size rather than assuming it.
"""

from amaranth import ClockDomain, ClockSignal, Elaboratable, Instance, Module, Signal
from amaranth.lib.cdc import FFSynchronizer

__all__ = ["HyperRAMDomains", "solve_hr_pll", "reachable_ck",
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


def solve_hr_pll_rungs(ck_list, input_mhz=60.0, tolerance=0.001):
    """One VCO and one output divider per CK in `ck_list`. Non-DQS only.

    CLKOP carries rung 0 AND the feedback, so two equations hold at once:
    `ck_list[0] = input * CLKFB_DIV / CLKI_DIV` fixes the loop, and
    `VCO = ck_list[0] * CLKOP_DIV` fixes the VCO. Every other rung is then a
    plain integer divisor of that same VCO -- which is the whole reason several
    CK values fit in one bitstream.

    DQS is excluded by arithmetic, not by preference: there `hr_fast` must be
    exactly `2 * hr` and must reach the bank edge-clock mux, which takes CLKOP
    and CLKOS only. Both are then spoken for by one rung. See `HyperRAMDomains`.

    Returns (vco, clki_div, clkfb_div, [div per rung]) or None.
    """
    first = ck_list[0]
    for clki_div in range(1, 8):
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

    def __init__(self, *, ck_mhz, dqs=True, input_mhz=60.0, phase=False):
        # A float or a sequence. One rung is the old behaviour exactly.
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
        self.dqs = dqs

        if dqs:
            # ONE RUNG, AND IT IS THE SILICON, NOT A SIMPLIFICATION.
            #
            # `hr_fast` is the DQS PHY's edge clock and must be `2 * hr`
            # exactly. The bank ECLK input mux takes CLKOP and CLKOS from a left
            # PLL, four clock pads and two fabric taps -- and nothing else; see
            # `elaborate` and #314. A DCSC output is not on that list at all
            # (`G_DCSOUT_DCS*` reaches the primary clock network only), so the
            # edge clock cannot be selected at run time. Fixing `hr_fast` fixes
            # `hr` with it, because the ratio is not free.
            #
            # `ECLKBRIDGECS` is the one runtime mux on the edge-clock path, but
            # its `W2_CLKI0/1` sources are `G_BANK{6,7}ECLK*` -- already-formed
            # bank edge clocks, not the input muxes -- and switching it would
            # invalidate the `DDRDLLA` delay code that sets the DQSBUFM read
            # window, which is the thing this rig exists to measure.
            if len(self.ck_rungs) > 1:
                raise ValueError(
                    f"{len(self.ck_rungs)} rungs on the DQS path: hr_fast is an "
                    f"edge clock, the ECLK input mux takes only CLKOP/CLKOS, and "
                    f"hr = hr_fast / 2 leaves nothing to select. Use dqs=False.")
            self.hr_mhz = self.ck_mhz / 2
            solved = solve_hr_pll(self.hr_mhz, input_mhz, with_fast=True)
            if solved is None:
                raise ValueError(self._unreachable(input_mhz))
            (self.vco_mhz, self.clki_div, self.clkfb_div,
             self.clkop_div, self.clkos_div) = solved
            self.rung_divs = [self.clkop_div]
        else:
            # `hr` IS CK here, so each rung is one output divider of the shared
            # VCO and the four EHXPLLL outputs are what makes several fit.
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
        self.hr_mhz_max = max(self.ck_rungs) / 2 if dqs else max(self.ck_rungs)
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

        # DYNAMIC PHASE SHIFT, driven by `peripherals/pll_phase.py` (#228).
        #
        # Levels, not pulses: the PLL acts on PHASESTEP's FALLING edge and needs
        # 4 VCO cycles in each state, so the shaping is the peripheral's and this
        # only carries what it produced.
        self.phase_sel = Signal(2)
        self.phase_dir = Signal()
        self.phase_step = Signal()

        # The probe clock's sample of `hr`, async to everything. See the module
        # docstring; zero and meaningless without `phase`.
        self.phase = phase
        self.probe_level = Signal()

    def _unreachable(self, input_mhz):
        """Why the ladder rung asked for does not exist, and what does nearby."""
        rungs = ", ".join(f"{ck:g}" for ck in self.ck_rungs)
        near = reachable_ck(min(self.ck_rungs) - 20, max(self.ck_rungs) + 20,
                            self.dqs, input_mhz)
        return (f"no PLL configuration gives HyperRAM CK {rungs} MHz"
                + (" with hr_fast at twice it" if self.dqs else
                   " off one shared VCO" if len(self.ck_rungs) > 1 else "")
                + f"; VCO must land in {VCO_MIN_MHZ:g}..{VCO_MAX_MHZ:g} MHz. "
                f"Reachable nearby: {near}")

    def elaborate(self, platform):
        m = Module()

        m.domains.hr = ClockDomain()
        if self.dqs:
            m.domains.hr_fast = ClockDomain()
        if self.phase:
            m.domains.hr_probe = ClockDomain()

        # One net per rung, straight off the PLL, plus the net the domain
        # actually runs on. With one rung they are the same wire.
        clk_rung = [Signal(name=f"clk_hr_rung{index}")
                    for index in range(len(self.rung_divs))]
        clk_hr = clk_rung[0] if len(clk_rung) == 1 else Signal(name="clk_hr")
        clk_hr_fast = Signal()
        clk_probe = Signal()
        locked_raw = Signal()

        # CLKOS is rung 1 on the non-DQS path and `hr_fast` on the DQS one.
        # Never both: the DQS path has one rung, for the reason in `__init__`.
        clkos_div = self.clkos_div if self.dqs else (
            self.rung_divs[1] if len(self.rung_divs) > 1 else None)

        m.submodules.pll = Instance(
            "EHXPLLL",
            a_ICP_CURRENT="12", a_LPF_RESISTOR="8", a_MFG_ENABLE_FILTEROPAMP="1",
            a_MFG_GMCREF_SEL="2",
            **({"a_BEL": "X2/Y49/EHXPLL_LL"} if self.dqs else {}),
            p_PLLRST_ENA="DISABLED", p_INTFB_WAKE="DISABLED",
            # WITHOUT THIS THE FIVE PHASE PORTS DO NOTHING. The sim model gates
            # all four dynamic inputs to 0 internally when it is DISABLED, and
            # the flow carries the enum straight through (nextpnr
            # `ecp5/bitstream.cc` -> `PLL1_*/bits.db` F2B8). Every generator in
            # this tree drove the ports and left this DISABLED, which is why
            # #228 could be "logged, not investigated" for so long.
            #
            # Tracks `phase`, so the attribute and the ports always agree and a
            # build that does not ask for the lever gets the config bit it had.
            p_STDBY_ENABLE="DISABLED",
            p_DPHASE_SOURCE="ENABLED" if self.phase else "DISABLED",
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
            # The probe clock: rung 0's frequency, and CPHASE/FPHASE set to the
            # zero-degree point (`CPHASE = DIV - 1`), so at step 0 it is nominally
            # CLKOP. CLKOS2 rather than CLKOS3 for no reason but that CLKOS3's
            # CPHASE word lives in the partner tile.
            **({"p_CLKOS2_ENABLE": "ENABLED",
                "p_CLKOS2_DIV": self.clkop_div,
                "p_CLKOS2_CPHASE": self.clkop_div - 1,
                "p_CLKOS2_FPHASE": 0} if self.phase else {}),
            i_CLKI=self.clki,
            # THE FEEDBACK. `FEEDBK_PATH="CLKOP"` closes the loop through the
            # CLKOP output, so leaving CLKFB undriven opens it: the PLL never
            # locks and anything gated on `locked` is held for ever. The design
            # still configures and still builds, which is why this cost a
            # bisect the first time -- see `clocks.py`.
            i_CLKFB=clk_rung[0],
            i_RST=0, i_STDBY=0,
            i_PHASESEL0=self.phase_sel[0], i_PHASESEL1=self.phase_sel[1],
            i_PHASEDIR=self.phase_dir, i_PHASESTEP=self.phase_step,
            # Held high, as Lattice's own BW_ALIGN reference design and every
            # `ecppll` output do. It reloads the COARSE (CPHASE) phase on its
            # falling edge; CPHASE here is already the zero-degree setting, so a
            # falling edge would re-apply what is applied. Not exposed: nothing
            # sweeps it, and a lever whose only effect is to repeat the current
            # state is a footgun with no axis behind it.
            i_PHASELOADREG=1,
            i_PLLWAKESYNC=0, i_ENCLKOP=0,
            o_CLKOP=clk_rung[0],
            **({"o_CLKOS": clk_hr_fast} if self.dqs else
               {"o_CLKOS": clk_rung[1]} if len(clk_rung) > 1 else {}),
            **({"o_CLKOS2": clk_probe} if self.phase else {}),
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
            # `G_*PCLK*`), never the edge-clock mux. That is what confines this
            # to the non-DQS path -- see `__init__`.
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
            # No ECLKBRIDGECS. Its bel pins are hardwired to the outputs of two
            # input muxes -- `.fixed_conn W2_JCLK0_ECLKBRIDGECS1 W2_JECLKI1` and
            # `W2_JCLK1_ECLKBRIDGECS1 S1W2_JECLKI1` in the same `bits.db` -- so
            # it sits DOWNSTREAM of the list above and cannot rescue a source
            # that list rejects. The lower-left PLL feeds bank 7 directly; there
            # is no die to cross. `ECLKBRIDGECS: 0/2` is the success criterion.
            m.d.comb += ClockSignal("hr_fast").eq(clk_hr_fast)

        if self.phase:
            m.d.comb += ClockSignal("hr_probe").eq(clk_probe)

            # THE PHASE DETECTOR, and it is two flip-flops.
            #
            # `pattern` is `hr` divided by two, sampled on the probe clock and
            # brought back. `back ^ pattern` is then a STATIC level: same
            # frequency off one VCO, so the sample lands at a fixed point of the
            # pattern every cycle, and it only changes when a phase step walks
            # that point across an edge.
            #
            # The XOR is what makes it static. Reporting the sample itself was
            # the first version and it does not work: `pattern` alternates every
            # cycle, so the sample alternates too whatever the phase, and what
            # reached the CPU was an aliasing artefact of the counting clock
            # rather than a phase. It moved with phase, which is the dangerous
            # kind of wrong.
            #
            # No delay line to match latency: `pattern` toggles every cycle, so
            # delaying it by the two cycles the round trip costs returns the same
            # value it started at.
            #
            # Sampling a clock as DATA was the other obvious version and is also
            # wrong: a PLL output used as data has to leave the clock network,
            # and nothing in the flow promises it can. Three ordinary FFs on two
            # ordinary clocks ask the same question with no special routing.
            pattern = Signal()
            sampled = Signal()
            back = Signal()
            m.d.hr += pattern.eq(~pattern)
            m.d.hr_probe += sampled.eq(pattern)
            m.d.hr += [
                back.eq(sampled),
                self.probe_level.eq(back ^ pattern),
            ]

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
            if self.dqs:
                platform.add_clock_constraint(clk_hr_fast, 2 * self.hr_mhz * 1e6)
            if self.phase:
                platform.add_clock_constraint(clk_probe, self.hr_mhz * 1e6)

        # LOCK is asynchronous to everything. Synchronised into `hr` because
        # that is the domain the engine gates on.
        m.submodules += FFSynchronizer(locked_raw, self.locked, o_domain="hr")

        return m
