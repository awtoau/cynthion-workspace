# SPDX-License-Identifier: BSD-3-Clause
"""`pnr_noise.parse_report` reads the post-route verdict, including a FAILing clock.

nextpnr prints the pre-route estimate as `Info:` and the post-route line as
`Warning:` when the clock fails. A parser matching `Info:` only keeps the
estimate for exactly the clocks that fail (docs/timing-variability.md).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "soc"))

import pnr_noise  # noqa: E402

LOG = """\
Info: Device utilisation:
Info: 	          TRELLIS_FF:   10283/  24288    42%
Info: 	        TRELLIS_COMB:   20520/  24288    84%
Info: Max frequency for clock '$glbnet$clk': 51.08 MHz (FAIL at 60.00 MHz)
Info: Max frequency for clock '$glbnet$hr_clk': 90.20 MHz (PASS at 80.00 MHz)
Info: Routing..
Info:     iter=1 wires=349160 overused=24015 overuse=28445 archfail=NA
Info:     iter=2 wires=371536 overused=0 overuse=0 archfail=0
Warning: Max frequency for clock '$glbnet$clk': 52.87 MHz (FAIL at 60.00 MHz)
Info: Max frequency for clock '$glbnet$hr_clk': 90.42 MHz (PASS at 80.00 MHz)
Info: Program finished normally.
"""


def test_post_route_warning_line_wins():
    util, fmax, verdict = pnr_noise.parse_report(LOG)
    assert util["TRELLIS_COMB"] == 20520
    assert fmax["$glbnet$clk"] == 52.87, "pre-route Info estimate must not win"
    assert verdict == {"$glbnet$clk": "FAIL", "$glbnet$hr_clk": "PASS"}


def test_passing_clock_still_parsed():
    _, fmax, verdict = pnr_noise.parse_report(LOG)
    assert fmax["$glbnet$hr_clk"] == 90.42
    assert verdict["$glbnet$hr_clk"] == "PASS"


def test_a_log_with_no_verdict_gives_empty_dicts():
    util, fmax, verdict = pnr_noise.parse_report("Info: nothing here\n")
    assert (util, fmax, verdict) == ({}, {}, {})
