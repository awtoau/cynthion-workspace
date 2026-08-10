#!/usr/bin/env python3
#
# Does each BIST parameter register reach anything in the design? See #343.
# SPDX-License-Identifier: BSD-3-Clause

"""Which `bist` axes are WIRED, decided from the elaborated design, not the board.

    ./scripts/hyperram_axis_wiring.py

## Why this exists

`hyperram_axis_liveness.py` asks the PART whether an axis moved, and can only ask
about fields the part reads back -- CR0 and CR1. `READCLKSEL` is not one: it goes
to the FPGA's own capture logic and nothing reads it back, so no board test can
tell "the phase does nothing" from "the phase is not connected".

The design itself can. Every host-writable parameter is a `Signal` handed to
`BISTHarness.add_register`; if nothing OUTSIDE the register file reads it, it
reaches nothing, and every row that varied it is a repeat of its neighbour.

## What it does

* elaborates `HyperRAMCeiling` for both PHYs against the real platform
* records every `add_register` parameter as it goes
* walks the elaborated fragment tree, skipping the `harness` subtree that owns
  the register file, and collects every signal READ there
* a parameter absent from that set is DEAD on that build

Its own control is the pair: `readclksel` must come back live on the DQS build.
An instrument that reports "dead" on both builds is broken, not informative.

Transcript -> `tmp/logs/hyperram-axis-wiring.log`.
"""

from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _sub in ("gateware", "gateware/probes", "gateware/soc", "scripts"):
    sys.path.insert(0, str(ROOT / _sub))

from amaranth.hdl._ast import SignalDict, SignalSet  # noqa: E402
from amaranth.hdl._ir import (Fragment, Instance, IOBufferInstance,  # noqa: E402
                              UnusedElaboratable)
from amaranth.hdl._mem import MemoryInstance  # noqa: E402

# `platform.request` builds a `PinBuffer` per pin and this script never
# elaborates them; the warning is about the platform, not the design.
warnings.filterwarnings("ignore", category=UnusedElaboratable)

LOG = ROOT / "tmp" / "logs" / "hyperram-axis-wiring.log"

# The register file lives under this submodule and reads every parameter for
# host read-back, which would score all of them live.
REGISTER_FILE = "harness"

# address -> (column in `bist all`, what it is meant to change)
AXES = {
    "readclksel": ("sel", "DQSBUFM capture phase"),
    "device_cr0": ("drive / lat / mode", "CR0 write to the part"),
    "device_cr1": ("clk", "CR1[6] write to the part"),
}

# `bist all`'s nesting, outermost first. Kept next to the axis table so the
# arithmetic below cannot drift from what the firmware prints.
MATRIX = [("lat", 16, "device_cr0"), ("mode", 2, "device_cr0"),
          ("drive", 8, "device_cr0"), ("clk", 2, "device_cr1"),
          ("sel", 8, "readclksel")]


def reads_outside(fragment, skip, path="top", found=None):
    """Signal -> the submodules that READ it, less the named subfragments.

    WHERE it is read is the point: an axis read only by the config FSM moves the
    part, one read by `phy` moves the FPGA, and the two are different claims.

    `Assign._rhs_signals()` includes the assignment's own target, so the target
    is subtracted per statement; an index expression on the target survives.
    """
    if found is None:
        found = SignalDict()
    here = SignalSet()
    for stmts in fragment.statements.values():
        for stmt in stmts:
            here |= stmt._rhs_signals() - stmt._lhs_signals()
    if isinstance(fragment, Instance):
        for value, kind in fragment.ports.values():
            if kind == "i":
                here |= value._rhs_signals()
    elif isinstance(fragment, IOBufferInstance):
        for value in (fragment.o, fragment.oe):
            if value is not None:
                here |= value._rhs_signals()
    elif isinstance(fragment, MemoryInstance):
        for port in fragment._read_ports:
            here |= port._addr._rhs_signals() | port._en._rhs_signals()
        for port in fragment._write_ports:
            here |= (port._addr._rhs_signals() | port._en._rhs_signals()
                     | port._data._rhs_signals())
    for signal in here:
        found.setdefault(signal, set()).add(path)
    for sub, name, _src in fragment.subfragments:
        if name in skip:
            continue
        reads_outside(sub, skip, f"{path}.{name}" if name else path, found)
    return found


def prepare(dqs, sync_mhz):
    """One build's prepared fragment, its parameters and its results.

    Results are spied too: a test that wants "what drives the register the
    firmware reads" needs the value expression, not only the address.
    """
    import bist as harness_module
    from board import CynthionPlatformRev1D4
    from hyperram.hyperram_ceiling_top import HyperRAMCeiling

    captured, results = [], []
    original = harness_module.BISTHarness.add_register
    original_ro = harness_module.BISTHarness.add_read_only_register

    def spy(self, address, *, value_signal):
        captured.append((address, value_signal))
        return original(self, address, value_signal=value_signal)

    def spy_ro(self, address, *, read):
        results.append((address, read))
        return original_ro(self, address, read=read)

    harness_module.BISTHarness.add_register = spy
    harness_module.BISTHarness.add_read_only_register = spy_ro
    try:
        design = HyperRAMCeiling(sync_mhz=sync_mhz, dqs=dqs)
        # `prepare` lowers `ClockSignal`/`ResetSignal` to the domain's own
        # signals; without it the walk below meets one and cannot resolve it.
        prepared = Fragment.get(design, CynthionPlatformRev1D4()).prepare()
    finally:
        harness_module.BISTHarness.add_register = original
        harness_module.BISTHarness.add_read_only_register = original_ro
    return prepared.fragment, captured, results


def elaborate(dqs, sync_mhz):
    """Elaborate one build, returning its parameters and the signals read."""
    fragment, captured, _results = prepare(dqs, sync_mhz)
    return captured, reads_outside(fragment, {REGISTER_FILE})


def report(log, dqs, sync_mhz):
    captured, read = elaborate(dqs, sync_mhz)
    live = {}
    log.info("")
    log.info("%s PHY, sync %g MHz", "DQS" if dqs else "non-DQS", sync_mhz)
    for _address, signal in sorted(captured, key=lambda pair: pair[1].name):
        if signal.name not in AXES:
            continue
        column, _what = AXES[signal.name]
        sites = sorted(read.get(signal, ()))
        live[signal.name] = bool(sites)
        log.info("  %-12s %-20s %s", signal.name, column,
                 "read by " + ", ".join(sites) if sites
                 else "DEAD -- read by nothing")

    cells = 1
    distinct = 1
    for name, values, register in MATRIX:
        cells *= values
        distinct *= values if live.get(register) else 1
    log.info("  %d advertised cells = %d distinct configurations x %d repeats",
             cells, distinct, cells // distinct)
    return live


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"),
                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("axis-wiring")

    log.info("Which `bist` parameters reach the design. See #343.")
    dqs_live = report(log, dqs=True, sync_mhz=60.0)
    sdr_live = report(log, dqs=False, sync_mhz=80.0)

    log.info("")
    if not dqs_live.get("readclksel"):
        log.error("CONTROL FAILED: readclksel is dead on the DQS build too, so "
                  "this instrument is broken rather than the non-DQS build.")
        return 2
    dead = sorted(name for name, ok in sdr_live.items() if not ok)
    if dead:
        log.info("non-DQS dead axes: %s", ", ".join(dead))
    log.info("transcript -> %s", LOG.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
