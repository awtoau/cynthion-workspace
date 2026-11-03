"""Every script that launches an Amaranth/yosys build pins yosys to one thread (#356)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

BUILDERS = [
    "scripts/soc_run.py",
    "scripts/sideband/sideband_build.py",
    "scripts/fabric/fabric_build.py",
]


def test_pin_value_is_one():
    import fast_build_env
    assert fast_build_env.YOSYS_MAX_THREADS == "1"


@pytest.mark.parametrize("path", BUILDERS)
def test_builder_takes_the_pin(path):
    text = (REPO / path).read_text()
    assert "from fast_build_env import" in text and "YOSYS_MAX_THREADS" in text, path
