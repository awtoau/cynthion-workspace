#!/usr/bin/env python3
# The flash pins state DRIVE/SLEWRATE/PULLMODE, like the HyperRAM pins. #459, #593.
# SPDX-License-Identifier: BSD-3-Clause

"""Are the flash pins' electricals stated, and are they the #590 baseline's?

  * the emitted LPF carries DRIVE, SLEWRATE and PULLMODE on every flash pin
  * the values are 16 mA FAST, PULLMODE NONE (the baseline's climb ran on 16 FAST)
  * with those attributes removed the flash lines are `IO_TYPE=LVCMOS33` and nothing else
  * no build-variant axis for them (#593 removed CYNTHION_FLASH_DRIVE/_SLEWRATE)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import Elaboratable, Module  # noqa: E402

from board.cynthion_r1_4 import CynthionPlatformRev1D4  # noqa: E402
from soc import variant  # noqa: E402

# Balls of the flash pins (CS# N8, DQ T8 T7 M7 N7); SCK is USRMCLK, no pad.
FLASH_BALLS = {"T8", "T7", "M7", "N7", "N8"}
STATED = re.compile(r"\s+(DRIVE|SLEWRATE|PULLMODE)=\w+")


class _Flash(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        pins = platform.request("qspi_flash", 0)
        m.d.comb += [pins.dq.o.eq(1), pins.dq.oe.eq(1), pins.cs.o.eq(1)]
        return m


def _lpf() -> list[str]:
    plan = CynthionPlatformRev1D4().prepare(_Flash())
    return plan.files["top.lpf"].splitlines()


def _flash_iobufs(lines):
    ports = {m.group(1) for line in lines
             if (m := re.match(r'LOCATE COMP "(\S+)" SITE "(\w+)";', line))
             and m.group(2) in FLASH_BALLS}
    assert len(ports) == 5, ports
    return [line for line in lines
            if line.startswith("IOBUF PORT") and line.split('"')[1] in ports]


def test_every_flash_pin_states_its_electricals():
    iobufs = _flash_iobufs(_lpf())
    assert len(iobufs) == 5
    for line in iobufs:
        for attr in ("DRIVE", "SLEWRATE", "PULLMODE"):
            assert f" {attr}=" in line, line


def test_values_are_the_baseline_s():
    for line in _flash_iobufs(_lpf()):
        assert "DRIVE=16" in line and "SLEWRATE=FAST" in line \
            and "PULLMODE=NONE" in line, line


def test_lpf_differs_only_by_the_stated_attributes():
    for line in _flash_iobufs(_lpf()):
        assert STATED.sub("", line).endswith("IO_TYPE=LVCMOS33;"), line
        assert len(STATED.findall(line)) == 3, line


def test_no_variant_axis():
    names = {name for name, _d, _t, _k in variant.VARIANT_ENV}
    assert not {"CYNTHION_FLASH_DRIVE", "CYNTHION_FLASH_SLEWRATE"} & names
