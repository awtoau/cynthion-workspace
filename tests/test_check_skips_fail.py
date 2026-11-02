"""check.py: a skipped check must not exit 0 unless --allow-skips (#392)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK_PY = REPO / "scripts" / "check.py"


@pytest.fixture(scope="module")
def check_module():
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("check_skips_under_test", CHECK_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _r(m, name, ok=True, skipped=False):
    return m.Result(name, ok=ok, seconds=0.0, skipped=skipped)


def test_skip_fails_by_default(check_module):
    results = [_r(check_module, "python"), _r(check_module, "apollo", skipped=True)]
    assert check_module.exit_code(results, allow_skips=False) == 1


def test_skip_passes_with_allow_skips(check_module):
    results = [_r(check_module, "python"), _r(check_module, "apollo", skipped=True)]
    assert check_module.exit_code(results, allow_skips=True) == 0


def test_failure_fails_even_with_allow_skips(check_module):
    results = [_r(check_module, "python", ok=False)]
    assert check_module.exit_code(results, allow_skips=True) == 1


def test_all_ran_passes(check_module):
    assert check_module.exit_code([_r(check_module, "python")], allow_skips=False) == 0
