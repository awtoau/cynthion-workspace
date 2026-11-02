#!/usr/bin/env python3
# The flash pins state DRIVE/SLEWRATE/PULLMODE, like the HyperRAM pins. #459.
# SPDX-License-Identifier: BSD-3-Clause

"""Are the flash pins' electricals stated, defaulted to what nextpnr applied, and sweepable?

  * the emitted LPF carries DRIVE, SLEWRATE and PULLMODE on every flash pin
  * with those attributes removed the flash lines are the old `IO_TYPE=LVCMOS33`
    and nothing else changed, so a default build differs only by stated defaults
  * the sweep axes move the flash lines and nothing else
  * the axes are in the variant table, hence in the cache key and the slug
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


def _lpf(**kwargs) -> list[str]:
    plan = CynthionPlatformRev1D4(**kwargs).prepare(_Flash())
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


def test_defaults_are_what_nextpnr_applied_before():
    # DRIVE 8, SLEWRATE SLOW, PULLMODE NONE: read from a built top.config, #459.
    for line in _flash_iobufs(_lpf()):
        assert "DRIVE=8" in line and "SLEWRATE=SLOW" in line \
            and "PULLMODE=NONE" in line, line


def test_default_lpf_differs_only_by_the_stated_attributes():
    for line in _flash_iobufs(_lpf()):
        assert STATED.sub("", line).endswith("IO_TYPE=LVCMOS33;"), line
        assert len(STATED.findall(line)) == 3, line


def test_the_axes_move_the_flash_lines_and_nothing_else():
    base, swept = _lpf(), _lpf(flash_drive="16", flash_slewrate="FAST")
    for line in _flash_iobufs(swept):
        assert "DRIVE=16" in line and "SLEWRATE=FAST" in line, line
    flash = lambda lines: set(_flash_iobufs(lines))
    assert [l for l in base if l not in flash(base)] == \
           [l for l in swept if l not in flash(swept)]


def test_the_axes_are_in_the_variant_table_with_the_platform_defaults():
    from board import cynthion_r1_4 as board
    table = {name: default for name, default, _t, _k in variant.VARIANT_ENV}
    assert table["CYNTHION_FLASH_DRIVE"] == board.FLASH_DRIVE
    assert table["CYNTHION_FLASH_SLEWRATE"] == board.FLASH_SLEWRATE
    base = variant.build_dir(ROOT, {})
    for env in ({"CYNTHION_FLASH_DRIVE": "16"}, {"CYNTHION_FLASH_SLEWRATE": "FAST"}):
        assert variant.build_dir(ROOT, env) != base
