#!/usr/bin/env python3
#
# The BIST PLL's phase detector stays above its floor. #428.
# SPDX-License-Identifier: BSD-3-Clause

"""
Every CK `hyperram_clocks.py` offers must be reachable in spec.

`CLKI / CLKI_DIV` is the phase-detector frequency and FPGA-DS-02012 Table 3.23
puts its minimum at 10 MHz. `clocks.py` has enforced that on the SoC's own PLL
since it was written; this file's solvers did not, and admitted CLKI_DIV 7 --
8.57 MHz off the 60 MHz reference.

The rungs that buys are exactly the ones of the form `60 * k / 7`: 85.714286 MHz
non-DQS, which is where #331/#332 took their latency codes, and 102.857143 MHz
DQS, which is one end of the gap #313 measures. Out of spec does not mean it
fails to build -- it means the datasheet stops guaranteeing jitter, on the clock
whose edge placement is the thing being measured.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from hyperram_clocks import (  # noqa: E402
    PFD_MIN_MHZ, reachable_ck, solve_dcsc_rungs, solve_hr_pll,
    solve_hr_pll_rungs)

INPUT_MHZ = 60.0


def test_the_floor_matches_the_datasheet():
    """One number, and `clocks.py` holds the same one."""
    from clocks import PFD_MIN_MHZ as soc_floor

    assert PFD_MIN_MHZ == soc_floor == 10.0


@pytest.mark.parametrize("ck", [85.714286, 102.857143, 111.428571])
def test_a_sevenths_rung_is_refused(ck):
    """`60 * k / 7` needs CLKI_DIV 7, so it is not a rung at all."""
    assert solve_hr_pll(ck, INPUT_MHZ, with_fast=False) is None
    assert ck not in reachable_ck(ck - 1, ck + 1, dqs=False, input_mhz=INPUT_MHZ)


@pytest.mark.parametrize("dqs", [False, True])
def test_every_reachable_ck_keeps_the_phase_detector_in_spec(dqs):
    """The whole published ladder, checked against the divider it needs."""
    for ck in reachable_ck(40, 300, dqs=dqs, input_mhz=INPUT_MHZ):
        hr = ck / 2 if dqs else ck
        solved = solve_hr_pll(hr, INPUT_MHZ, with_fast=dqs)
        assert solved is not None, f"CK {ck} is listed but does not solve"
        clki_div = solved[1]
        assert INPUT_MHZ / clki_div >= PFD_MIN_MHZ, (
            f"CK {ck} needs CLKI_DIV {clki_div}, a "
            f"{INPUT_MHZ / clki_div:.2f} MHz phase detector")


def test_the_two_rung_solver_obeys_it_too():
    """`solve_hr_pll_rungs` fixes the loop from rung 0, so it has the same floor."""
    assert solve_hr_pll_rungs([85.714286], INPUT_MHZ) is None
    assert solve_hr_pll_rungs([80.0, 90.0], INPUT_MHZ) is not None
    _vco, clki_div, _clkfb, _divs = solve_hr_pll_rungs([80.0, 90.0], INPUT_MHZ)
    assert INPUT_MHZ / clki_div >= PFD_MIN_MHZ


def test_the_dcsc_planner_agrees_with_the_others():
    """It capped CLKI_DIV at 6 by hand and was the only one that was right."""
    for vco, rungs in solve_dcsc_rungs(60, 120, input_mhz=INPUT_MHZ):
        assert vco > 0 and rungs
