"""A clock constraint nextpnr ignored must fail the build, not scroll past as a warning."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import build_log_audit  # noqa: E402

# nextpnr's own wording, as logged by a yosys 0.70 build that merged the names away.
DROPPED = (
    "Warning: net 'car.clk_sync' does not exist in design, ignoring clock constraint\n"
    "Warning: net 'hyperram.pll.clk_hr' does not exist in design, ignoring clock constraint\n"
    "Info: Max frequency for clock '$glbnet$car.osc': 102.77 MHz (PASS at 60.00 MHz)\n"
)
CLEAN = "Info: Max frequency for clock '$glbnet$clk': 90.76 MHz (PASS at 60.00 MHz)\n"


def test_every_dropped_net_is_named():
    assert build_log_audit.dropped_clock_constraints(DROPPED) == [
        "car.clk_sync", "hyperram.pll.clk_hr"]


def test_a_clean_log_has_none():
    assert build_log_audit.dropped_clock_constraints(CLEAN) == []


def test_another_warning_is_not_a_dropped_constraint():
    other = "Warning: Max frequency for clock '$glbnet$x': 1.0 MHz (FAIL at 60.00 MHz)\n"
    assert build_log_audit.dropped_clock_constraints(other) == []


def test_soc_run_refuses_a_build_that_dropped_one():
    text = (REPO / "scripts" / "soc_run.py").read_text()
    assert "dropped_clock_constraints" in text
