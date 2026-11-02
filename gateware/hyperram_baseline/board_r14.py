# SPDX-License-Identifier: BSD-3-Clause
"""Minimal Cynthion r1.4 platform: the 60 MHz oscillator and the HyperRAM, nothing else.

- Board facts only, transcribed from GSG's public platform file
  `cynthion/python/src/gateware/platform/cynthion_r1_4.py`
  (github.com/greatscottgadgets/cynthion; resources `clk_60MHz` and `ram`).
- Bank voltage 3.3 V (LVCMOS33), as that file declares.
"""

from amaranth.build import Attrs, Clock, DiffPairs, Pins, PinsN, Resource, Subsignal
from amaranth.vendor import LatticeECP5Platform


class CynthionR14Baseline(LatticeECP5Platform):
    device = "LFE5U-12F"
    package = "BG256"
    speed = "8"
    default_clk = "clk_60MHz"

    resources = [
        Resource("clk_60MHz", 0, Pins("A8", dir="i"), Clock(60e6), Attrs(IO_TYPE="LVCMOS33")),
        Resource("ram", 0,
                 Subsignal("clk", DiffPairs("C3", "D3", dir="o"), Attrs(IO_TYPE="LVCMOS33D")),
                 Subsignal("dq", Pins("F2 B1 C2 E1 E3 E2 F3 G4", dir="io")),
                 Subsignal("rwds", Pins("D1", dir="io")),
                 Subsignal("cs", PinsN("B2", dir="o")),
                 Subsignal("reset", PinsN("C1", dir="o")),
                 Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")),
    ]
    connectors = []
