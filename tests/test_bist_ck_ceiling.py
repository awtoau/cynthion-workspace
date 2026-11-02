#!/usr/bin/env python3
#
# A CK past the fabric is refused at elaboration, not by nextpnr 200 s later. #410.
# SPDX-License-Identifier: BSD-3-Clause

"""
`top.py` refuses a CK its fabric cannot close at, and its default is not one.

#410 is a default that could not be built: everything unset asked for CK 100, and
the fabric stops at 84. The failure arrived as a raw nextpnr ERROR about
`hr_clk` after a full synthesis, with nothing naming CK as the cause.

Two things have to hold and neither is checked by anything else:

  * `variant.CK_DEFAULT_MHZ` is at or under the ceiling
  * asking for more is refused at import, with the rungs that would work
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

import variant  # noqa: E402

TOP = ROOT / "gateware" / "soc" / "top.py"

# Importing top.py is the only way to reach a module-level refusal, and it pulls
# in the whole SoC -- so it happens in a subprocess, once per case.
#
#   waits for   `python3 top.py --help`, which is import plus argparse
#   expected    ~5 s cold, measured on this tree; nothing is elaborated
#   multiplier  4x, because a cold filesystem cache dominates and this is a
#               correctness gate rather than a benchmark
#   on expiry   the test fails naming the variant and the limit
IMPORT_SECONDS = 20


def run_top(**env):
    return subprocess.run(
        [sys.executable, str(TOP), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=IMPORT_SECONDS,
        env={**os.environ, **env})


def test_the_default_is_under_its_own_ceiling():
    """The #410 regression, as one comparison."""
    ceiling = float(TOP.read_text().split("HYPERRAM_CK_CEILING_MHZ = ")[1]
                    .splitlines()[0])
    assert float(variant.CK_DEFAULT_MHZ) <= ceiling


def test_the_default_variant_elaborates():
    result = run_top()
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_ck_past_the_fabric_is_refused_by_name():
    result = run_top(CYNTHION_HYPERRAM_CK_MHZ="100")
    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "past the fabric ceiling" in message
    # The refusal has to carry what WOULD work, or it is only a faster failure.
    assert "84" in message and "#410" in message


def test_the_ceiling_can_be_raised_on_purpose():
    """It is one measurement of one design; raising it is visible in the command."""
    result = run_top(CYNTHION_HYPERRAM_CK_MHZ="100",
                     CYNTHION_HYPERRAM_CK_CEILING_MHZ="120")
    assert result.returncode == 0, result.stdout + result.stderr
