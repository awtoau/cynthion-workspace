#!/usr/bin/env python3
#
# An edge clock sourced from CLKOS2 falls back to fabric SILENTLY. See #314.
# SPDX-License-Identifier: BSD-3-Clause

"""Does every PLL output that becomes an edge clock exist in the bank ECLK mux?

The fault this catches has no other alarm. `HyperRAMDQSPHY` takes its `ECLK` from
a `fast`/`hr_fast` domain; if that domain is driven from CLKOS2 the bank ECLK
input mux cannot select it, nextpnr routes it through the fabric tap instead, and
says so as `log_info` -- not a warning, not an error. Nothing in the build output,
the metrics row or the timing report names it, and the design still runs. It just
runs on a clock whose skew nobody bounded, which on a capture-phase rig is the
measurement itself.

Three generators can produce an edge clock and all three are checked:

    gateware/soc/clocks.py                       `fast`      (shipping SoC)
    gateware/soc/hyperram_clocks.py              `hr_fast`   (SoC BIST variant)
    gateware/probes/hyperram/hyperram_ceiling_top.py  `fast`  (standalone applet)

The legal source list is READ FROM prjtrellis, not written down here, so the
assertion tracks the silicon rather than a comment. `test_trellis_agrees_with_the
_literal` is what makes the fallback constant trustworthy when the DB is absent.
"""

from __future__ import annotations

import re
import shutil
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# EHXPLLL outputs the bank ECLK input mux accepts. Derived from prjtrellis by
# `test_trellis_agrees_with_the_literal`; written out only so the generator check
# still runs on a checkout without the DB.
ECLK_REACHABLE_OUTPUTS = {"CLKOP", "CLKOS"}

# The one left-hand PLL bel on the 25F die. Bank 7 holds every HyperRAM DQ/RWDS
# pad, and its ECLK mux takes PLL sources from the left PLLs only.
LEFT_PLL_BEL = "X2/Y49/EHXPLL_LL"


def _trellis_eclk_db() -> Path | None:
    """`tiledata/ECLK_L/bits.db`, from the toolchain that will do the build."""
    import os

    roots = []
    for var in ("TRELLIS_DB", "TRELLIS_INSTALL_PREFIX"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    # The suite the builds actually use. `hyperram_ceiling.py` prepends the same
    # path to PATH, so this finds the DB whether or not the caller did.
    for tool in ("nextpnr-ecp5", "ecppack", "yosys"):
        found = shutil.which(tool)
        if found:
            roots.append(Path(found).resolve().parent.parent / "share" / "trellis")
    roots.append(Path.home() / "opt" / "oss-cad-suite" / "share" / "trellis")
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


def _pll_of(design, platform):
    """The one EHXPLLL in an elaborated design."""
    from amaranth.hdl import Fragment

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fragment = Fragment.get(design, platform)

    found = []

    def walk(node):
        for sub, _name, _src in node.subfragments:
            if getattr(sub, "type", None) == "EHXPLLL":
                found.append(sub)
            walk(sub)

    walk(fragment)
    assert len(found) == 1, f"expected one EHXPLLL, found {len(found)}"
    return found[0]


def _outputs(pll) -> set[str]:
    """Which EHXPLLL clock outputs this instance actually drives."""
    return {name for name, (_value, direction) in pll.ports.items()
            if direction == "o" and name.startswith("CLKO")}


def _platform():
    sys.path[:0] = [str(ROOT / "gateware"), str(ROOT / "gateware" / "probes"),
                    str(ROOT / "gateware" / "soc"), str(ROOT / "repos" / "apollo")]
    from board import CynthionPlatformRev1D4
    return CynthionPlatformRev1D4()


def _generators():
    """Every generator that can produce an edge clock, with one configured."""
    _platform()                                    # puts `gateware/**` on the path
    from clocks import SocClocks
    from hyperram_clocks import HyperRAMDomains
    from hyperram.hyperram_ceiling_top import HyperRAMClocks

    return [
        ("soc/clocks.py `fast`", SocClocks(sync_mhz=60.0, with_fast=True)),
        ("soc/hyperram_clocks.py `hr_fast`", HyperRAMDomains(ck_mhz=120.0, dqs=True)),
        ("probes ceiling `fast`", HyperRAMClocks(sync_mhz=100.0, with_fast=True)),
    ]


@pytest.mark.parametrize("name,design", _generators(), ids=lambda arg: arg
                         if isinstance(arg, str) else "")
def test_edge_clock_comes_from_a_reachable_output(name, design):
    """No CLKOS2/CLKOS3 on a design whose second output IS an edge clock."""
    pll = _pll_of(design, _platform())
    outputs = _outputs(pll)
    assert outputs <= ECLK_REACHABLE_OUTPUTS, (
        f"{name}: drives {sorted(outputs - ECLK_REACHABLE_OUTPUTS)}, which the bank "
        f"ECLK input mux does not accept -- nextpnr will route the edge clock "
        f"through the fabric tap and report it as log_info only (#314)")


@pytest.mark.parametrize("name,design", _generators(), ids=lambda arg: arg
                         if isinstance(arg, str) else "")
def test_edge_clock_pll_is_pinned_to_the_left(name, design):
    """Only PLL0_LL reaches bank 7, and an unpinned placer swap is silent."""
    pll = _pll_of(design, _platform())
    assert pll.attrs.get("BEL") == LEFT_PLL_BEL, (
        f"{name}: BEL is {pll.attrs.get('BEL')!r}, not {LEFT_PLL_BEL!r}. The "
        f"HyperRAM pads are bank 7 and the 25F die has no upper-left PLL, so a "
        f"placer that moves this PLL right drops the edge clock onto fabric (#314)")


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
