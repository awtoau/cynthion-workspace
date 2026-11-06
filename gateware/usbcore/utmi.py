#
# UTMI+ interfaces as amaranth.lib.wiring signatures, and the transmit multiplexer.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
L0 link-side view of a UTMI+ transceiver (UTMI+ 1.05; ULPI PHYs reach it through `ulpi.py`).

- `UTMITxSignature`: one transmitter. Source drives `data`, `valid`; sink returns `ready`
  (byte accepted at this edge). Dropping `valid` ends the packet (EOP).
- `UTMIRxSignature`: one receive stream. `active` spans the packet; `valid` strobes a byte.
- `UTMISignature`: the full bus, link (SIE) side: Out = to PHY, In = from PHY. Flat UTMI names.
- `UTMITxMux`: merges transmitters; at most one `valid` at a time (one-hot), `ready` fans out.
"""

import functools
import operator

from amaranth import Module, Mux
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["UTMITxSignature", "UTMIRxSignature", "UTMISignature", "UTMITxMux",
           "UTMI_TO_PHY", "UTMI_FROM_PHY"]


class UTMITxSignature(wiring.Signature):
    def __init__(self):
        super().__init__({"data": Out(8), "valid": Out(1), "ready": In(1)})


class UTMIRxSignature(wiring.Signature):
    def __init__(self):
        super().__init__({"data": Out(8), "valid": Out(1), "active": Out(1)})


# (name, width): driven by the link.
UTMI_TO_PHY = (
    ("tx_data", 8), ("tx_valid", 1),
    ("xcvr_select", 2), ("term_select", 1), ("op_mode", 2), ("suspend", 1),
    ("id_pullup", 1), ("dm_pulldown", 1), ("dp_pulldown", 1),
    ("chrg_vbus", 1), ("dischrg_vbus", 1), ("use_external_vbus_indicator", 1),
)

# (name, width): driven by the PHY.
UTMI_FROM_PHY = (
    ("tx_ready", 1), ("rx_data", 8), ("rx_active", 1), ("rx_valid", 1),
    ("line_state", 2), ("vbus_valid", 1), ("session_valid", 1), ("session_end", 1),
    ("rx_error", 1), ("host_disconnect", 1), ("id_digital", 1),
)


class UTMISignature(wiring.Signature):
    """Link-side UTMI+; `UTMISignature().flip()` is the PHY (or translator) side."""

    def __init__(self):
        members = {name: Out(width) for name, width in UTMI_TO_PHY}
        members.update({name: In(width) for name, width in UTMI_FROM_PHY})
        super().__init__(members)


class UTMITxMux(wiring.Component):
    """AND-OR mux of transmitters onto `output`. Inputs are added before elaboration."""

    output: Out(UTMITxSignature())

    def __init__(self):
        self._inputs = []
        super().__init__()

    def add_input(self, tx):
        self._inputs.append(tx)

    def elaborate(self, platform):
        m = Module()
        if self._inputs:
            m.d.comb += [
                self.output.valid.eq(functools.reduce(operator.or_, (i.valid for i in self._inputs))),
                self.output.data.eq(functools.reduce(
                    operator.or_, (Mux(i.valid, i.data, 0) for i in self._inputs))),
            ]
        m.d.comb += [i.ready.eq(self.output.ready) for i in self._inputs]
        return m
