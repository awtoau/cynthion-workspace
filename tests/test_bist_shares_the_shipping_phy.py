#!/usr/bin/env python3
#
# The BIST rig's numbers transfer only while it drives the shipping PHY. #425.
# SPDX-License-Identifier: BSD-3-Clause

"""
The measurement variant and the shipping SoC instantiate the SAME device path.

#425 asks whether a number taken on the BIST bitstream says anything about the
design that ships. The answer rests on one fact: from the pad back to the
controller's word interface, `HyperRAMCeiling` and `BootRAM` are the same
modules, and the latency table has one definition.

  | path    | PHY                | controller             |
  |---------|--------------------|------------------------|
  | non-DQS | luna `HyperRAMPHY` | `HyperRAMController`   |
  | DQS     | `HyperRAMDQSPHY`   | `HyperRAMDQSController`|

Fork either of them and the transfer argument is void without anything saying
so -- the rig would keep producing rows, about a device path the product does
not have. That is what this test is for.

It does NOT claim the two designs are otherwise alike. What the BIST variant
cannot say anything about is stated in #425: the shipping SoC's own CK, which is
`sync` on the non-DQS path and `2 x fast` on the DQS one, and is therefore
bounded by the CPU's closure rather than by the part.

Static, over the sources, because the alternative is elaborating two full SoCs
against a platform to compare two class identities.
"""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

SHIPPING = ROOT / "gateware" / "soc" / "bootram.py"
RIG = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_ceiling_top.py"

# Every symbol on the device path, and nothing that is not on it.
DEVICE_PATH = ("HyperRAMPHY", "HyperRAMController",
               "HyperRAMDQSPHY", "HyperRAMDQSController")


def imported_from(path):
    """`{name: module}` for every `from X import name`, nested ones included."""
    found = {}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found[alias.asname or alias.name] = node.module
    return found


@pytest.fixture(scope="module")
def imports():
    return imported_from(SHIPPING), imported_from(RIG)


@pytest.mark.parametrize("name", DEVICE_PATH)
def test_both_designs_import_the_same_device_path(name, imports):
    shipping, rig = imports
    assert name in shipping, f"{SHIPPING.name} no longer uses {name}"
    assert name in rig, f"{RIG.name} no longer uses {name}"
    assert shipping[name] == rig[name], (
        f"{name} comes from {shipping[name]} in the shipping SoC and "
        f"{rig[name]} in the BIST rig -- the paths have forked, so no number "
        f"the rig produces is about the product (#425)")


def test_the_latency_table_has_one_definition(imports):
    """The rig takes `HYPERRAM_LATENCY_CLOCKS` from the shipping module."""
    _shipping, rig = imports
    assert rig.get("HYPERRAM_LATENCY_CLOCKS") == "bootram"

    import bootram

    assert isinstance(bootram.HYPERRAM_LATENCY_CLOCKS, int)


def test_the_rig_owns_the_pins_exclusively():
    """Both request `ram`, and Amaranth allows one requester.

    This is why the BIST variant cannot boot and why it is not the shipping
    design -- recorded here so the exclusivity is a checked property rather than
    a paragraph in a docstring.
    """
    for path in (SHIPPING, RIG):
        assert 'request("ram"' in path.read_text(), \
            f"{path.name} no longer requests the HyperRAM pins itself"
