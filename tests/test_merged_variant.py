#!/usr/bin/env python3
#
# The merged elaboration is BOTH halves of the fork, and one clock knob. #432.
# SPDX-License-Identifier: BSD-3-Clause

"""Does `CYNTHION_HYPERRAM_MERGED` elaborate BootRAM *and* the BIST engine?

The closure probe's whole value is that nothing is missing from it: a merged
build that quietly dropped `BootRAM` would report the BIST variant's Fmax under
a new name, and that number already exists.

Asked of `top.py` in a subprocess -- 0.1 s an import -- because the module reads
the variant at import time and a second import in one process would see the
first one's environment.

`CYNTHION_SYNC_MHZ` is checked here too: it is what lets one elaboration be
built at two clocks, and its default is per variant (#439).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))

from soc import variant  # noqa: E402

MERGED = {"CYNTHION_HYPERRAM_MERGED": "1"}
BIST = {"CYNTHION_HYPERRAM_BIST": "1"}


def _ask(env):
    """(WITH_BOOTRAM, WITH_BIST, SYNC_MHZ) as top.py resolves them."""
    clean = {k: v for k, v in os.environ.items()
             if k not in {n for n, _d, _t, _k in variant.VARIANT_ENV}}
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'gateware');"
         "sys.path.insert(0, 'gateware/soc'); import top;"
         "print(top.WITH_BOOTRAM, top.WITH_BIST, top.SYNC_MHZ)"],
        cwd=ROOT, capture_output=True, text=True, env={**clean, **env})
    assert result.returncode == 0, result.stderr[-800:]
    bootram, bist, sync = result.stdout.split()
    return bootram == "True", bist == "True", float(sync)


def test_the_shipping_soc_has_bootram_and_no_engine():
    assert _ask({}) == (True, False, 60.0)


def test_the_bist_rig_has_the_engine_and_no_bootram():
    assert _ask(BIST) == (False, True, 50.0)


def test_the_merged_probe_has_both():
    # The point of #432 stage 1: strictly more logic than either half.
    assert _ask(MERGED) == (True, True, 60.0)


def test_the_clock_is_a_variant_variable_rather_than_an_edit():
    assert _ask({**MERGED, "CYNTHION_SYNC_MHZ": "50"}) == (True, True, 50.0)


def test_each_of_those_is_its_own_build_directory():
    envs = [{}, BIST, MERGED, {**MERGED, "CYNTHION_SYNC_MHZ": "50"}]
    assert len({variant.build_dir(ROOT, env) for env in envs}) == len(envs)
