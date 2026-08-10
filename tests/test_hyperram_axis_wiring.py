#!/usr/bin/env python3
#
# The `sel` axis is wired on one build and not the other. See #343.
# SPDX-License-Identifier: BSD-3-Clause

"""Does every axis the firmware sweeps reach the gateware?

`bist all` advertises 4096 cells across five axes. `readclksel` is a DQSBUFM
input, so on a non-DQS build it reaches nothing and 4096 cells are 512
configurations run 8 times -- and a flat `sel` column then reads as a measured
"no effect" rather than as a disconnected wire, which is the same class of fault
as #334 and #335.

The check is structural because nothing else can be: `READCLKSEL` goes to the
FPGA, not to the part, so it has no device read-back and no board test can reach
it. `scripts/hyperram_axis_wiring.py` elaborates both builds and reports which
parameters anything reads; this pins its answers so the wiring cannot change
without a test saying so.

Both directions are asserted. A check that only demanded "dead on non-DQS" would
pass just as well if the instrument reported everything dead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

hyperram_axis_wiring = pytest.importorskip(
    "hyperram_axis_wiring", reason="needs amaranth and the Cynthion platform")

FIRMWARE = ROOT / "firmware" / "cynthion-soc" / "src" / "bist.rs"


@pytest.fixture(scope="module")
def builds():
    """`{dqs: {parameter: read_by}}` for both PHYs. One elaboration each."""
    return {dqs: hyperram_axis_wiring.elaborate(dqs, 80.0)
            for dqs in (True, False)}


def _sites(build, name):
    captured, read = build
    for _address, signal in captured:
        if signal.name == name:
            return sorted(read.get(signal, ()))
    raise AssertionError(f"no `{name}` parameter register in this build")


def test_readclksel_reaches_the_phy_on_a_dqs_build(builds):
    # The control. If this fails the instrument is broken, not the build.
    assert _sites(builds[True], "readclksel"), \
        "`sel` must reach the DQS PHY, or nothing below distinguishes anything"


def test_readclksel_reaches_nothing_without_dqs(builds):
    assert _sites(builds[False], "readclksel") == [], \
        "`sel` is wired on the non-DQS path now -- update #343 and the firmware's " \
        "distinct-configuration arithmetic, which still divides by SEL_VALUES"


@pytest.mark.parametrize("name", ["device_cr0", "device_cr1"])
def test_the_register_writes_reach_the_part_on_both_builds(builds, name):
    # `drive`, `lat`, `mode` and `clk` travel to the part as CR0/CR1 writes.
    # Whether the part HONOURS them is `hyperram_axis_liveness.py`'s question.
    for dqs, build in builds.items():
        assert _sites(build, name), f"`{name}` is read by nothing (dqs={dqs})"


def test_the_firmware_divides_by_the_same_sel_count():
    """`Bist::distinct` divides 4096 by this; the matrix multiplies by it."""
    text = FIRMWARE.read_text()
    assert "const SEL_VALUES: u32 = 8;" in text
    values = dict((name, count) for name, count, _reg
                  in hyperram_axis_wiring.MATRIX)
    assert values["sel"] == 8
