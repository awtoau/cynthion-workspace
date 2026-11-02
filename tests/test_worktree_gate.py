#!/usr/bin/env python3
#
# worktree-check in the gate, vendor model advisory; setup always prunes. See #375.
# SPDX-License-Identifier: BSD-3-Clause

"""Contract tests for the worktree half of the gate (#375)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev  # noqa: E402
import worktree_setup as ws  # noqa: E402


def test_worktree_gate_is_first_in_the_gate():
    assert dev.GATE[0] == "worktree-gate"
    assert "worktree-gate" in dev.COMMANDS


def test_missing_vendor_model_fails_check_but_only_warns_in_the_gate(monkeypatch):
    monkeypatch.setattr(ws, "submodules", lambda *a, **k: [])
    monkeypatch.setattr(ws, "resolve_shared", lambda *a, **k: None)
    assert ws.check() == 1
    assert ws.check(gate=True) == 0


def test_setup_has_no_opt_in_prune():
    import inspect
    assert "prune" not in inspect.signature(ws.setup).parameters
    assert "prune" not in inspect.signature(ws.setup_one).parameters
