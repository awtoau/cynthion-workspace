#!/usr/bin/env python3
#
# The BIST rig's addresses are written down in three languages. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Do the gateware, the firmware and the transport agree on where things are?

The BIST peripheral is addressed by a hand-written constant in the firmware
rather than by the generated PAC, and that is deliberate: the PAC is generated
from whichever variant `HYPERRAM_BIST` selects, and only one of the two can be
committed. Committing the measurement variant's would leave the shipping image
checking itself against a map it does not have.

The cost of that choice is a constant that can drift, and drift here does not
fail to build -- it reads a peripheral that is not there, or reads the parameter
window and finds zeros where results should be. Zero errors out of zero words is
exactly what a *passing* cell looks like to anything that does not check, which
is the failure this project has already made more than once.

So the agreement is asserted here instead:

    HYPERRAM_BIST_BASE      top.py            <-> bist.rs   BASE
    RESULT_WINDOW           bist_csr.py       <-> bist.rs   RESULT_WINDOW
    BURST_WORDS             ceiling engine    <-> bist.rs   BURST_WORDS

The gateware constants themselves are checked against the elaborated memory map
by `scripts/soc_generate_pac.py --check`, so the chain from Amaranth's own map to
the pointer the CPU dereferences is closed by the two together.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOP = ROOT / "gateware" / "soc" / "top.py"
TRANSPORT = ROOT / "gateware" / "soc" / "peripherals" / "bist_csr.py"
PERIPHERAL = ROOT / "gateware" / "soc" / "peripherals" / "hyperram_bist.py"
ENGINE = ROOT / "gateware" / "probes" / "hyperram" / "hyperram_ceiling_top.py"
FIRMWARE = ROOT / "firmware" / "cynthion-soc" / "src" / "bist.rs"


def _int(text: str) -> int:
    """Parse a Python or Rust integer literal, underscores and all."""
    return int(text.replace("_", ""), 0)


def _grab(path: Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(), re.MULTILINE)
    assert match, f"{path.name}: nothing matched {pattern!r} -- has it been renamed?"
    return _int(match.group(1))


def test_peripheral_base_agrees():
    """`bist.rs` must point at where `top.py` puts the peripheral."""
    gateware = _grab(TOP, r"^HYPERRAM_BIST_BASE\s*=\s*(0x[0-9a-fA-F_]+)")
    firmware = _grab(FIRMWARE, r"^pub const BASE:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
    assert firmware == gateware, (
        f"the firmware addresses the BIST peripheral at 0x{firmware:08x} but the "
        f"gateware puts it at 0x{gateware:08x}; every register access would go "
        f"somewhere else entirely")


def test_result_window_agrees():
    """Results live one window up, and both sides must use the same offset.

    A mismatch reads the parameter window instead: the CPU's own last write comes
    back as though the engine had produced it, and a cell that never ran reports
    zero errors.
    """
    gateware = _grab(TRANSPORT, r"^\s*RESULT_WINDOW\s*=\s*(0x[0-9a-fA-F_]+)")
    firmware = _grab(FIRMWARE, r"const RESULT_WINDOW:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
    assert firmware == gateware, (
        f"the firmware reads results at +0x{firmware:x} but the gateware drives "
        f"them at +0x{gateware:x}; reads would return the CPU's own parameters")


def test_result_window_clears_the_parameter_window():
    """The two windows must not overlap, whatever `addr_width` the rig uses.

    `HyperRAMBist` defaults to an 8-bit engine address space, which is 64
    registers of 4 bytes = 0x100. The result window starts exactly there. If the
    engine ever grows past that, the windows collide silently -- a parameter and
    a result would share an address.
    """
    window = _grab(TRANSPORT, r"^\s*RESULT_WINDOW\s*=\s*(0x[0-9a-fA-F_]+)")
    default = _grab(PERIPHERAL, r"addr_width=(\d+)")
    assert (1 << default) <= window, (
        f"addr_width={default} needs 0x{1 << default:x} bytes of parameter space "
        f"but the result window starts at 0x{window:x}")


def test_burst_words_agrees():
    """The firmware's poll bound is derived from the engine's burst length.

    `Bist::poll_limit` works out how long a pass may legitimately take from
    `BURST_WORDS` words each way. If the engine's burst grows and the firmware's
    copy does not, the bound becomes too short and every cell reports a timeout
    -- which reads as a broken part rather than a stale constant.
    """
    gateware = _grab(ENGINE, r"^BURST_WORDS\s*=\s*(\d+)")
    firmware = _grab(FIRMWARE, r"^const BURST_WORDS:\s*u32\s*=\s*(\d+)")
    assert firmware == gateware, (
        f"the engine bursts {gateware} words but the firmware's poll bound is "
        f"derived from {firmware}; too short and every cell times out, too long "
        f"and a wedge is indistinguishable from a slow pass")


def test_register_numbers_are_the_engines_own():
    """The two rigs must address the same window.

    The JTAG applet and the CPU rig share a numbering deliberately -- that is
    what lets a number from one be compared with a number from the other. A
    renumbering on either side would not fail to build; it would silently read a
    different register and report it under the old name.
    """
    engine = ENGINE.read_text()
    firmware = FIRMWARE.read_text()
    for name in ("ID", "STATUS", "ERRORS", "WORDS", "CONTROL", "READCLKSEL",
                 "DEVICE_CR0", "DEVICE_CR1", "PASS_LIMIT"):
        want = _grab(ENGINE, rf"^REG_{name}\s*=\s*(\d+)") if f"REG_{name}" in engine \
            else None
        assert want is not None, f"the engine no longer defines REG_{name}"
        got = _grab(FIRMWARE, rf"pub const {name}:\s*usize\s*=\s*(\d+)")
        assert got == want, (
            f"the engine's REG_{name} is {want} but the firmware reads {got}; "
            f"the two rigs would be addressing different registers under the "
            f"same name")
    assert "APPLET_ID" in firmware
