#!/usr/bin/env python3
#
# One SoC: the staging path and the BIST engine in the same bitstream. #432.
# SPDX-License-Identifier: BSD-3-Clause

"""There is no BIST variant to fork to, and nothing selects one.

The fork existed because `BootRAM` and the engine both called
`platform.request("ram")` and Amaranth allows one requester. One PHY, one
controller and a mode mux removed the reason, so what is checked here is that
the reason cannot come back:

  * `top.py` elaborates both halves, unconditionally
  * `ram` is requested exactly once, by `hyperram_share.py`
  * no variant variable turns either half off
  * `CYNTHION_SYNC_MHZ` still overrides one value for both

Asked of `top.py` in a subprocess -- 0.1 s an import -- because the module reads
the variant at import time and a second import in one process would see the
first one's environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from soc import variant  # noqa: E402

SOC = ROOT / "gateware" / "soc"
NAME = "CYNTHION_SYNC_MHZ"


def _ask(env, expression):
    clean = {k: v for k, v in os.environ.items()
             if k not in {n for n, _d, _t, _k in variant.VARIANT_ENV}}
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'gateware');"
         "sys.path.insert(0, 'gateware/soc'); import top;"
         f"print({expression})"],
        cwd=ROOT, capture_output=True, text=True, env={**clean, **env})
    assert result.returncode == 0, result.stderr[-800:]
    return result.stdout.strip()


def test_the_clock_is_one_value_for_one_design():
    # 50 is the measured rung -- `soc_sync_ladder.py`, every seed. Read from
    # the table rather than typed, so this test is about there being ONE
    # value, not about which.
    assert _ask({}, "top.SYNC_MHZ") == str(float(variant.value(NAME, {})))
    assert _ask({NAME: "45"}, "top.SYNC_MHZ") == "45.0"


def test_no_variant_variable_selects_a_half():
    """A flag naming one half is a fork, whatever it is called."""
    tags = {tag for _n, _d, tag, _k in variant.VARIANT_ENV}
    assert "bist" not in tags and "merge" not in tags, tags


def test_the_pins_are_requested_once_and_by_the_shared_module():
    # Parsed, not grepped: `hyperram_dqs_phy.py` documents in a docstring the
    # resource its caller must hand it.
    import ast

    requesters = sorted({
        path.name for path in SOC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "request"
        and node.args and getattr(node.args[0], "value", None) == "ram"})
    assert requesters == ["hyperram_share.py"], requesters


def test_neither_half_can_be_switched_off_again():
    """The names that selected one, gone from the tree rather than defaulted."""
    gone = ("WITH_BIST", "WITH_BOOTRAM", "HYPERRAM_MERGED",
            "CYNTHION_HYPERRAM_BIST")
    found = []
    for directory in ("gateware", "scripts", "tests", "firmware"):
        for path in (ROOT / directory).rglob("*.p*"):
            if path.suffix not in (".py", ".pyi") or path.name == Path(__file__).name:
                continue
            text = path.read_text()
            found += [f"{path.name}: {name}" for name in gone if name in text]
    assert found == [], found


def test_the_engine_cannot_ask_for_the_pins():
    """No port, no engine: the fork's cause is a required argument now."""
    sys.path.insert(0, str(SOC))
    from peripherals.hyperram_bist import HyperRAMBist

    try:
        HyperRAMBist(ck_mhz=80.0)
    except TypeError:
        return
    raise AssertionError("the BIST engine built without a port to drive")
