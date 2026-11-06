#!/usr/bin/env python3
#
# An edge clock sourced from CLKOS2 falls back to fabric SILENTLY. See #314.
# SPDX-License-Identifier: BSD-3-Clause

"""Does every PLL output that becomes an edge clock exist in the bank ECLK mux?

The fault this catches has no other alarm. If an edge-clock domain is driven
from CLKOS2 the bank ECLK input mux cannot select it, nextpnr routes it through
the fabric tap instead, and says so as `log_info` -- not a warning, not an error.
Nothing in the build output, the metrics row or the timing report names it, and
the design still runs, on a clock whose skew nobody bounded.

- No generator in the tree builds an edge clock since #593 removed `fast`; the
  source list and the artifact audit (`soc_eclk_check.py`) stay for the next one.
- The legal source list is READ FROM prjtrellis; `test_trellis_agrees_with_the
  _literal` keeps the fallback constant honest when the DB is absent.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# EHXPLLL outputs the bank ECLK input mux accepts, as prjtrellis says
# (`test_trellis_agrees_with_the_literal`).
ECLK_REACHABLE_OUTPUTS = {"CLKOP", "CLKOS"}


def _trellis_eclk_db() -> Path | None:
    """`tiledata/ECLK_L/bits.db`, from the toolchain that will do the build."""
    import os

    roots = []
    for var in ("TRELLIS_DB", "TRELLIS_INSTALL_PREFIX"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    # The suite the builds actually use; the build scripts prepend the same
    # path to PATH, so this finds the DB whether or not the caller did.
    for tool in ("nextpnr-ecp5", "ecppack", "yosys"):
        found = shutil.which(tool)
        if found:
            roots.append(Path(found).resolve().parent.parent / "share" / "trellis")
    roots.append(Path.home() / "opt" / "oss-cad-suite-20261104" / "oss-cad-suite" / "share" / "trellis")
    for root in roots:
        for candidate in (root / "database" / "ECP5" / "tiledata" / "ECLK_L" / "bits.db",
                          root / "ECP5" / "tiledata" / "ECLK_L" / "bits.db"):
            if candidate.exists():
                return candidate
    return None


def _mux_sources(db: Path, mux: str) -> list[str]:
    """The source wire names of one `.mux` block."""
    text = db.read_bytes().decode("utf-8", errors="replace")
    block = re.search(rf"^\.mux {re.escape(mux)}\n((?:\S.*\n)*)", text, re.MULTILINE)
    assert block, f"{db}: no `.mux {mux}` -- has the tile been renamed?"
    return [line.split()[0] for line in block.group(1).splitlines() if line.strip()]


def test_trellis_agrees_with_the_literal():
    """The DB decides which PLL outputs the ECLK mux takes; the literal follows."""
    db = _trellis_eclk_db()
    if db is None:
        pytest.skip("no prjtrellis ECLK_L/bits.db found via TRELLIS_DB, "
                    "TRELLIS_INSTALL_PREFIX or nextpnr-ecp5 on PATH")
    # All four input muxes in the tile, so a source legal on only one of them
    # cannot pass by being read from the other.
    for mux in ("W2_ECLKI0", "W2_JECLKI1", "S1W2_ECLKI0", "S1W2_JECLKI1"):
        sources = _mux_sources(db, mux)
        pll = {match.group(1) for match in
               (re.match(r"G_J[UL]LCPLL0(CLK\w+)$", src) for src in sources) if match}
        assert pll == ECLK_REACHABLE_OUTPUTS, (
            f"{mux}: prjtrellis says the PLL sources are {sorted(pll)}, this file "
            f"says {sorted(ECLK_REACHABLE_OUTPUTS)}")


# ---------------------------------------------------------------------------
# The artifact audit. The tests above read the SOURCE; these read what the tools
# produced, which is the only thing that says where the clock actually went.
#
# Both fixtures are verbatim from the matched pair built at 4d4c539 --
# `soc_eclk_check.py` with and without `--control` -- and differ in one arc and
# one log line.
# ---------------------------------------------------------------------------
CONFIG_DEDICATED = """\
.tile CIB_R25C2:ECLK_L
arc: S1W2_ECLKI0 G_JLLCPLL0CLKOS
arc: W2_JECLK0 W2_JNEIGHBORECLK0
"""

CONFIG_FABRIC_TAP = CONFIG_DEDICATED.replace("G_JLLCPLL0CLKOS", "G_JLLQECLKCIB0")

TIM_DEDICATED = """\
Info: Logic utilisation before packing:
Info: Promoted 'fast_clk' to bank 7 ECLK0.
Info:     Derived frequency constraint of 120.0 MHz for net fast_clk$eclk7_0
Info: \t        ECLKBRIDGECS:       0/      2     0%
Info:     trying dedicated routing for edge clock source fast_clk
Info: Max frequency for clock '$glbnet$clk': 309.89 MHz (PASS at 60.00 MHz)
"""

TIM_FABRIC = TIM_DEDICATED.replace(
    "Info:     trying dedicated routing for edge clock source fast_clk\n",
    "Info:     trying dedicated routing for edge clock source fast_clk\n"
    "Info:         no route found, general routing will be used.\n")


def _build_dir(tmp_path, config, tim):
    (tmp_path / "top.config").write_text(config)
    (tmp_path / "top.tim").write_text(tim)
    return tmp_path


def _audit(build_dir):
    sys.path.insert(0, str(ROOT / "scripts"))
    import _paths  # noqa: F401  (scripts/ subdirectories on sys.path)
    from soc_eclk_check import audit
    return audit(build_dir)


def test_audit_passes_the_dedicated_path(tmp_path):
    ok, lines = _audit(_build_dir(tmp_path, CONFIG_DEDICATED, TIM_DEDICATED))
    assert ok, "\n".join(lines)


def test_audit_fails_the_fabric_tap(tmp_path):
    """The pre-fix design. Both halves of it, each on its own."""
    ok, _ = _audit(_build_dir(tmp_path, CONFIG_FABRIC_TAP, TIM_FABRIC))
    assert not ok, "the control passed -- this check has stopped looking (#314)"

    ok, _ = _audit(_build_dir(tmp_path, CONFIG_FABRIC_TAP, TIM_DEDICATED))
    assert not ok, "a fabric-tap arc passed when nextpnr happened not to log it"

    ok, _ = _audit(_build_dir(tmp_path, CONFIG_DEDICATED, TIM_FABRIC))
    assert not ok, "'general routing will be used' passed on a dedicated arc"


def test_audit_is_vacuous_without_an_edge_clock(tmp_path):
    """The shipping SoC builds no `fast`, so it must not fail this."""
    ok, lines = _audit(_build_dir(tmp_path, ".tile CIB_R25C2:CIB_EBR\n",
                                 "Info: Logic utilisation before packing:\n"))
    assert ok and "no edge clock" in "\n".join(lines)


def test_audit_reads_only_the_last_nextpnr_run(tmp_path):
    """`top.tim` accumulates; a fixed build must not inherit the old failure."""
    ok, _ = _audit(_build_dir(tmp_path, CONFIG_DEDICATED,
                              TIM_FABRIC + TIM_DEDICATED))
    assert ok, "an earlier run's fabric fallback was read as this build's"
