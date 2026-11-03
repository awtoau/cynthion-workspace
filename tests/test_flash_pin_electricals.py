#!/usr/bin/env python3
# The flash pins state DRIVE/SLEWRATE/PULLMODE: the #590 baseline's 16 mA FAST. #459, #593.
# SPDX-License-Identifier: BSD-3-Clause

"""Every flash pin carries the stated electricals, one IOBUF per line (per-line OE)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import Elaboratable, Module  # noqa: E402

from board.cynthion_r1_4 import CynthionPlatformRev1D4  # noqa: E402

# Balls of the flash pins (CS# N8, IO0-3 T8 T7 M7 N7); SCK is USRMCLK, no pad.
FLASH_BALLS = {"T8", "T7", "M7", "N7", "N8"}


class _Flash(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        pins = platform.request("qspi_flash", 0)
        for n in range(4):
            io = getattr(pins, f"io{n}")
            m.d.comb += [io.o.eq(1), io.oe.eq(n & 1)]
        m.d.comb += pins.cs.o.eq(1)
        return m


def _flash_iobufs():
    lines = CynthionPlatformRev1D4().prepare(_Flash()).files["top.lpf"].splitlines()
    ports = {m.group(1) for line in lines
             if (m := re.match(r'LOCATE COMP "(\S+)" SITE "(\w+)";', line))
             and m.group(2) in FLASH_BALLS}
    assert len(ports) == 5, ports
    return [line for line in lines
            if line.startswith("IOBUF PORT") and line.split('"')[1] in ports]


def test_every_flash_pin_states_the_baseline_electricals():
    iobufs = _flash_iobufs()
    assert len(iobufs) == 5
    for line in iobufs:
        assert "IO_TYPE=LVCMOS33" in line and "DRIVE=16" in line \
            and "SLEWRATE=FAST" in line and "PULLMODE=NONE" in line, line
