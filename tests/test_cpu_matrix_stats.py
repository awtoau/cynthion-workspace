#!/usr/bin/env python3
#
# The statistics `soc_occupancy_timing.py` reports. #470.
# SPDX-License-Identifier: BSD-3-Clause

"""Is the p a p, and does the interval bracket the mean?

`_betainc()` was a step function -- 0 below the beta reflection point and 1
above it -- so every p this harness has printed, including #467's, was `0.0` or
`1.0` and carried no more information than the sign of the difference. It went
unnoticed because a broken p in the direction you expect reads as a strong
result.

Checked against the published two-sided t table, which is the only reference
that does not come from the same arithmetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import soc_occupancy_timing  # noqa: E402


def test_the_p_value_is_a_p_value_and_not_a_step_function():
    for dof, t, want in ((9, 2.262, 0.05), (29, 2.045, 0.05),
                         (9, 1.833, 0.10), (9, 3.250, 0.01)):
        got = soc_occupancy_timing._betainc(dof / 2, 0.5, dof / (dof + t * t))
        assert abs(got - want) < 0.001, (dof, t, got)

    # Monotone and distinct in t, which the step function was not.
    ps = [soc_occupancy_timing._betainc(4.5, 0.5, 9 / (9 + t * t))
          for t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)]
    assert ps == sorted(ps, reverse=True), ps
    assert len(set(ps)) == len(ps), ps


def test_welch_separates_what_is_separated_and_not_what_is_not():
    apart = soc_occupancy_timing.welch([60.0, 61, 62, 60.5, 61.5],
                                       [70.0, 71, 72, 70.5, 71.5])
    together = soc_occupancy_timing.welch([60.0, 61, 62, 60.5, 61.5],
                                          [60.1, 61.1, 62.1, 60.6, 61.6])
    assert apart[2] < 0.001 and together[2] > 0.5


def test_t_critical_matches_the_published_table():
    assert abs(soc_occupancy_timing.t_critical(9) - 2.262) < 0.005
    assert abs(soc_occupancy_timing.t_critical(29) - 2.045) < 0.005
    assert abs(soc_occupancy_timing.t_critical(1_000_000) - 1.960) < 0.005


def test_the_paired_interval_brackets_the_mean():
    ref = {seed: 70.0 for seed in range(1, 11)}
    arm = {seed: 70.0 + (seed % 3) for seed in range(1, 11)}
    got = soc_occupancy_timing.paired(ref, arm)
    assert got["lo"] < got["mean"] < got["hi"]
    assert got["n"] == 10

    # An arm that is identical to the reference has a zero-width interval on
    # zero, not a missing one.
    same = soc_occupancy_timing.paired(ref, dict(ref))
    assert same["mean"] == same["lo"] == same["hi"] == 0.0
