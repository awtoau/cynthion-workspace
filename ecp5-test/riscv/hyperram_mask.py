#!/usr/bin/env python3
#
# Per-byte write masking for luna's DQS HyperRAM controller. See #92.
# SPDX-License-Identifier: BSD-3-Clause

"""
Exposes the write mask that `HyperRAMDQSInterface` ties off to zero.

## Why this is needed at all

The DQS controller's data path is 32 bits wide, but two of the three ports on
`vexii_bootram.BootRAM` present 16 -- the firmware staging CSR and the JTAG
stager, both of which move a HyperRAM word at a time. With no mask, storing 16
bits means writing 32 and destroying the neighbouring word.

The usual answers are read-modify-write (two transactions per staged word) or
buffering pairs of words (wrong the moment a caller writes one word at a fixed
address, which `hyperram::write_header` does). Neither is necessary: HyperBus
already carries a per-byte write mask on RWDS, and the ECP5 PHY already gears it
out.

    ODDRX2F  i_D0=phy.rwds.o[3]  i_D1=rwds.o[2]  i_D2=rwds.o[1]  i_D3=rwds.o[0]

`HyperRAMDQSInterface` drives that port with a constant:

    with m.State("WRITE_DATA"):
        m.d.sync += [ ..., self.phy.rwds.o.eq(0) ]

`0` means "write every byte", which is right for the Wishbone window and wrong
for everything narrower. So the capability is present in the hardware and in the
PHY, and only the controller declines to offer it.

## How the override works

Amaranth resolves conflicting drivers in a domain by statement order -- the last
one wins. `super().elaborate()` adds the FSM's `rwds.o.eq(0)` inside `WRITE_DATA`;
this class then adds an unconditional assignment *after* it, which therefore
takes priority in every state.

Driving it unconditionally is correct rather than merely convenient. `rwds.o`
only reaches the pin while `rwds.e` is asserted, which the parent does in
`WRITE_DATA` alone, so outside a write the value is not observable. Inside one,
the mask is registered on the same edge as `phy.dq.o`, so it stays aligned with
the data it applies to -- which a state-scoped override would also have to
arrange, with more code.

This is a subclass rather than a vendored copy so that the 180-line FSM stays
upstream's and keeps whatever fixes it gets.

## Bit order

`write_mask` is in the same order as `phy.rwds.o`: bit 3 masks the first byte on
the wire, bit 0 the last. **1 inhibits the byte**, per the HyperBus data-mask
convention. `vexii_bootram` builds it from Wishbone `sel`-style lanes and states
the mapping there; this module only forwards it.
"""

from amaranth import Signal
from luna.gateware.interface.psram import HyperRAMDQSInterface


class MaskedHyperRAMDQSInterface(HyperRAMDQSInterface):
    """`HyperRAMDQSInterface` with the RWDS write mask brought out.

    Identical in every other respect, including the port names the rest of the
    design connects to. `write_mask` defaults to 0, so an instance nobody drives
    behaves exactly like the base class.
    """

    def __init__(self, *, phy, high_latency_clocks=None):
        super().__init__(phy=phy)
        # 1 inhibits that byte. Same order as `phy.rwds.o`: bit 3 is first on
        # the wire.
        self.write_mask = Signal(4)

        # Upstream's fixed latency is FIVE `sync` cycles, and at 4:1 gearing one
        # of those is 2 CK -- so the data phase can only start on even CK, and
        # every capture setting lands the 32-bit group at least one word late.
        #
        # Measured, on all sixteen tap/phase combinations at CK 120: the
        # achievable skews are +1, +2, +3 and +4 words and zero is not among
        # them. The taps only ever push capture LATER, so the fix has to come
        # from the other end -- start the data phase two CK earlier and let a
        # tap put back the one word that overshoots.
        #
        # `elaborate` reads this through `self`, so an instance attribute is
        # enough and the FSM stays upstream's.
        if high_latency_clocks is not None:
            self.HIGH_LATENCY_CLOCKS = high_latency_clocks

    def elaborate(self, platform):
        m = super().elaborate(platform)
        # Added after the parent's statements, so it supersedes the `rwds.o.eq(0)`
        # in WRITE_DATA. See the module docstring for why unconditional is right.
        m.d.sync += self.phy.rwds.o.eq(self.write_mask)
        return m
