# SPDX-License-Identifier: BSD-3-Clause
"""`usb` and `sync` for the LUNA USB probes: both the 60 MHz oscillator, one power-on reset.

- Same topology as the SoC's `usb` (`gateware/soc/clocks.py`): oscillator passed through, no PLL.
- `sync` is the same net, so the JTAG register block and the endpoints share one clock.
- Reset held for the USB334x pad pulse plus TPREP (constants from `soc/clocks.py`);
  LUNA's `UTMITranslator` drives the AUX PHY reset pad from `ResetSignal("usb")`.
- The parent defines `domains` (amaranth 0.6 has no upward domain propagation, #609).
"""

import sys
from pathlib import Path

from amaranth import ClockDomain, ClockSignal, Elaboratable, Module, ResetSignal, Signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from soc.clocks import PHY_PAD_RESET_CYCLES, PHY_PREP_CYCLES  # noqa: E402

__all__ = ["OscDomains"]


class OscDomains(Elaboratable):
    def __init__(self):
        self.domains = [ClockDomain("usb"), ClockDomain("sync")]

    def elaborate(self, platform):
        m = Module()
        m.domains.por = ClockDomain(reset_less=True, local=True)
        osc = platform.request(platform.default_clk, 0).i
        settled = PHY_PAD_RESET_CYCLES + PHY_PREP_CYCLES
        count = Signal(range(settled + 1))
        with m.If(count != settled):
            m.d.por += count.eq(count + 1)
        held = count != settled
        m.d.comb += [
            ClockSignal("por").eq(osc),
            ClockSignal("usb").eq(osc),
            ClockSignal("sync").eq(osc),
            ResetSignal("usb").eq(held),
            ResetSignal("sync").eq(held),
        ]
        return m
