#!/usr/bin/env python3
#
# Fixed-latency override for luna's DQS HyperRAM controller. See #186.
# SPDX-License-Identifier: BSD-3-Clause

"""
Lets the DQS controller's fixed latency be set per build.

Upstream's `HIGH_LATENCY_CLOCKS` is a class constant of 5. At 4:1 gearing one
`sync` cycle is 2 CK, so the data phase can only ever start on an even CK, and
every capture setting lands the 32-bit group at least one word late.

**Measured, on all sixteen tap/phase combinations at CK 120: the achievable
skews are +1, +2, +3 and +4 words, and zero is not among them.** The taps only
ever push capture LATER, so the correction has to come from the other end --
start the data phase earlier and let a tap put back the overshoot. That is what
`vexii_bootram.HYPERRAM_LATENCY_CLOCKS = 4` does.

`scripts/hyperram_sel_sweep.py` re-runs that sweep against the window's 64-byte
line check; at latency 4 it still reports no setting reaching 16/16, which is why
#186 is open and why a BITSLIP -- the one knob neither the taps nor the latency
provide -- is the next thing tried. LiteDRAM's ECP5 PHY has one in exactly the
position luna's does not (`BitSlip(4)` between `IDDRX2DQA` and the data path).

This is a subclass rather than a vendored copy so the 180-line FSM stays
upstream's and keeps whatever fixes it gets. `elaborate` reads the constant
through `self`, so an instance attribute is enough.

## This file used to export a write mask, and no longer does

It was `hyperram_mask.py`, and it re-drove `phy.rwds.o` -- which
`HyperRAMDQSInterface` ties to 0 -- so that a 16-bit staging port could store one
word into a 32-bit group without a read-modify-write.

That mask never worked on silicon: a staged `a5c3` read back as `c3c3`, the
inhibited half replaced by a copy of the half that landed. It is gone with the
16-bit ports it existed for. Every owner of the arbiter now presents a whole
32-bit pair, which is the granule the DQS PHY actually has, and which is what
LiteX's `litehyperbus` and OpenHBMC both use -- neither has a narrow side-port,
and both express byte granularity as byte enables on the wide bus instead.
"""

from luna.gateware.interface.psram import HyperRAMDQSInterface


class LatencyHyperRAMDQSInterface(HyperRAMDQSInterface):
    """`HyperRAMDQSInterface` with a per-instance fixed latency.

    Identical in every other respect, including the port names the rest of the
    design connects to. Passing no `high_latency_clocks` leaves upstream's
    constant untouched.
    """

    def __init__(self, *, phy, high_latency_clocks=None):
        super().__init__(phy=phy)
        if high_latency_clocks is not None:
            self.HIGH_LATENCY_CLOCKS = high_latency_clocks
