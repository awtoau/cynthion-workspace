"""adv_health: the FPGA_ADV health bytes [ok, crc_fail, timeout, watchdog] decoded as the firmware defines them."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import _paths  # noqa: E402,F401  (scripts/ subdirectories on sys.path)
import adv_health  # noqa: E402


def test_watchdog_bit_zero_is_link_dead_and_bit_one_is_cpu_dead():
    assert adv_health.decode(bytes([9, 0, 0, 0]))["verdict"] == "healthy"
    assert adv_health.decode(bytes([0, 0, 3, 1]))["verdict"] == "link dead"
    assert adv_health.decode(bytes([9, 0, 0, 2]))["verdict"] == "cpu dead"
    assert adv_health.decode(bytes([0, 0, 3, 3]))["verdict"] == "link dead, cpu dead"


def test_counts_are_reported_by_name():
    got = adv_health.decode(bytes([200, 4, 7, 0]))
    assert (got["ok"], got["crc_fail"], got["timeout"]) == (200, 4, 7)


def test_a_short_reply_is_refused():
    import pytest
    with pytest.raises(ValueError):
        adv_health.decode(bytes([1, 2, 3]))
