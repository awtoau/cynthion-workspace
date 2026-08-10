#!/usr/bin/env python3
#
# What the engine says ABOUT ITSELF has to be able to be wrong. See #359, #366.
# SPDX-License-Identifier: BSD-3-Clause

"""Every bit the engine reports about itself must be driven by the engine.

A status bit that is a literal is indistinguishable from one that is working and
happens to be set, which is the fault class this rig keeps producing: `busy` was
`busy.eq(1)` (#359) and the CR0/CR1 readback was printed as fact whatever the
capture phase did to it (#366).

Structural, because nothing else can be: `HyperRAMCeiling` instantiates ECP5 hard
blocks (DQSBUFM, EHXPLLL, DTR), so the elaborated design cannot be run in
Amaranth's simulator and no board test can reach a signal that never leaves the
FPGA. What CAN be settled here is whether the report's driver is the engine's own
state or a constant -- and that is the whole of both defects.

The end-to-end behaviour lives in `scripts/hyperram_dqs_model_sim.py --stage
config`, which runs the real controller against the device model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

hyperram_axis_wiring = pytest.importorskip(
    "hyperram_axis_wiring", reason="needs amaranth and the Cynthion platform")


@pytest.fixture(scope="module")
def build():
    """The DQS build, prepared once. `sel` reaches the PHY only here (#343)."""
    return hyperram_axis_wiring.prepare(dqs=True, sync_mhz=60.0)


def _drivers(fragment, name):
    """Every RHS signal of an assignment to a signal called `name`, top level.

    Top level only: the driver of a harness input is in the parent, and the
    harness's own reads of it would score a literal as driven.
    """
    found = []
    for statements in fragment.statements.values():
        for statement in statements:
            targets = {signal.name for signal in statement._lhs_signals()}
            if name in targets:
                found.append(statement._rhs_signals()
                             - statement._lhs_signals())
    assert found, f"nothing assigns to `{name}` in the top fragment"
    return found


def _result(results, address):
    for claimed, read in results:
        if claimed == address:
            return read
    raise AssertionError(f"no result register at address {address}")


def test_busy_is_not_a_literal(build):
    """#359: `busy.eq(1)` can never say not-busy, so no caller can wait on it."""
    fragment, _params, _results = build
    for rhs in _drivers(fragment, "busy"):
        assert rhs, "`harness.busy` is driven by a constant -- it can never " \
                    "say not-busy, so `bist status` reads busy=1 for ever (#359)"


def test_busy_is_driven_by_the_engine_state(build):
    """Not merely non-constant: the state the FSM already exports (#318)."""
    from hyperram.hyperram_ceiling_top import REG_FSM_STATE

    fragment, _params, results = build
    state = _result(results, REG_FSM_STATE)._rhs_signals()
    assert state, "REG_FSM_STATE reads no signal, so this test proves nothing"
    for rhs in _drivers(fragment, "busy"):
        assert rhs & state, \
            "`busy` is not a function of the engine FSM state; it should be " \
            "'not parked', and the state is already exported (#359, #318)"
