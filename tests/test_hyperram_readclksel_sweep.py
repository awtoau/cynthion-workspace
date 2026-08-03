#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

"""Synthetic pass/fail proofs for READCLKSEL window analysis."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from hyperram_readclksel_sweep import analysis_lines, analyse_window  # noqa: E402


def table(*passing):
    selected = set(passing)
    return [(setting, setting in selected) for setting in range(8)]


class WindowAnalysisTests(unittest.TestCase):

    def test_no_passing_setting_has_no_window_or_centre(self):
        result = analyse_window(table())
        self.assertEqual(result.kind, "no_pass")
        self.assertEqual(result.ranges, ())
        self.assertIsNone(result.width)
        self.assertIsNone(result.centre)
        self.assertIn("NO PASSING SETTING", "\n".join(analysis_lines(result)))

    def test_every_setting_passing_invalidates_capture_test(self):
        result = analyse_window(table(*range(8)))
        self.assertEqual(result.kind, "all_pass")
        self.assertIsNone(result.width)
        self.assertIsNone(result.centre)
        self.assertTrue(result.width_is_lower_bound)
        report = "\n".join(analysis_lines(result))
        self.assertIn("INVALID CAPTURE TEST", report)
        self.assertIn("EVERY SETTING PASSED", report)

    def test_width_one_is_legal_and_alarming(self):
        result = analyse_window(table(3))
        self.assertEqual(result.kind, "single_window")
        self.assertEqual(result.ranges, ((3, 3),))
        self.assertEqual(result.width, 1)
        self.assertEqual(result.centre, 3)
        self.assertTrue(result.alarming)
        self.assertIn("ALARM: WIDTH 1", "\n".join(analysis_lines(result)))

    def test_disjoint_ranges_do_not_get_averaged_across_failure(self):
        result = analyse_window(table(1, 2, 5, 6))
        self.assertEqual(result.kind, "disjoint")
        self.assertEqual(result.ranges, ((1, 2), (5, 6)))
        self.assertEqual(result.widths, (2, 2))
        self.assertIsNone(result.width)
        self.assertIsNone(result.centre)
        self.assertIn("DISJOINT PASSING RANGES",
                      "\n".join(analysis_lines(result)))

    def test_window_at_end_has_lower_bound_width_and_centre(self):
        result = analyse_window(table(5, 6, 7))
        self.assertEqual(result.kind, "single_window")
        self.assertEqual(result.ranges, ((5, 7),))
        self.assertEqual(result.width, 3)
        self.assertEqual(result.centre, 6)
        self.assertTrue(result.clipped_end)
        self.assertTrue(result.width_is_lower_bound)
        self.assertEqual(result.centre_bound, "lower")
        report = "\n".join(analysis_lines(result))
        self.assertIn("true width may extend above", report)
        self.assertIn("LOWER BOUND", report)

    def test_even_width_exposes_both_central_settings(self):
        result = analyse_window(table(2, 3, 4, 5))
        self.assertEqual(result.centre_candidates, (3, 4))
        self.assertEqual(result.centre, 3)


if __name__ == "__main__":
    unittest.main()
