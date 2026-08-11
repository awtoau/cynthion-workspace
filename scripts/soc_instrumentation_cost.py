#!/usr/bin/env python3
#
# What the SoC's three instrumentation peripherals cost, in context.
# SPDX-License-Identifier: BSD-3-Clause

"""Build the real SoC twice -- with and without its instrumentation -- and diff
the utilisation.

`scripts/soc_peripheral_area.py` synthesises each peripheral alone, which gives
an UPPER BOUND. This gives the DELTA: the same top, the same placement seed, the
same everything except that `FlashILA`, `FlashPinProbe` and `HyperRAMProbe`
elaborate to nothing.

**It edits no tracked file.** The three classes are replaced on their modules
before `top` is imported, by subclasses that keep every port and
every CSR register -- so the memory map, the generated PAC and the firmware's
addresses are all unchanged -- and whose `elaborate` returns an empty module.
What that removes is the counters, the edge detectors, the capture memory and
the sample multiplexers; what it keeps is the CSR bridge each one needs to
answer at its address at all. So the number below is the cost of the LOGIC,
which is the part a deletion would actually recover, and it understates a real
deletion by the bridges.

    ./scripts/soc_instrumentation_cost.py

**One build is one placement.** This design has closed between 71.45 and 76.99
MHz across identical builds, so treat the Fmax column as noise unless
`scripts/pnr_noise.py` says otherwise. Cell counts are deterministic for a given
netlist and do not have that problem.

Output is mirrored to ./tmp/logs/soc_instrumentation_cost.log.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_instrumentation_cost.log"

UTIL_RE = re.compile(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'(\S+)':\s+([\d.]+) MHz")

# The cells worth reporting. The rest of the utilisation table is I/O and hard
# blocks that instrumentation does not move.
CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "TRELLIS_RAMW")


def read_utilisation(build_dir):
    """(cells, fmax) from a finished nextpnr run's `top.tim`."""
    text = (build_dir / "top.tim").read_text()
    cells, fmax = {}, {}
    for line in text.splitlines():
        match = UTIL_RE.match(line.strip())
        if match and match.group(1) in CELLS:
            cells[match.group(1)] = int(match.group(2))
        match = FMAX_RE.search(line)
        if match:
            fmax[match.group(1)] = float(match.group(2))
    return cells, fmax


def build(stub, build_dir):
    """Build the SoC in a subprocess, optionally with instrumentation stubbed.

    A subprocess because the monkey-patch has to happen before
    `top` is imported, and a module imported once in this process
    would be reused for the second build.
    """
    script = f"""
import sys
from pathlib import Path
ROOT = Path({str(ROOT)!r})
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))

STUB = {stub!r}

# PIN `GatewareId`'s CONSTANTS, and this is not a nicety.
#
# `GatewareId` bakes the build time and the git hash in as 32-bit constants, and
# synthesis folds them: two builds a minute apart have different `built` words,
# constant-fold differently, and land on different LUT counts. Measured at 153
# TRELLIS_COMB between two builds of an otherwise identical design -- which is a
# fifth of the number this script exists to report. Fixing both makes the two
# netlists differ only in what is being measured.
import peripherals.gateware_id as gateware_id
from datetime import datetime, timezone
_real = gateware_id.GatewareId

class _Fixed(_real):
    def __init__(self, **kwargs):
        kwargs.setdefault("built",
                          datetime(2000, 1, 1, tzinfo=timezone.utc))
        kwargs.setdefault("git", 0)
        super().__init__(**kwargs)

gateware_id.GatewareId = _Fixed
if STUB:
    # Subclass rather than replace, so every port, every CSR register and the
    # whole memory map survive and only the logic goes. `elaborate` still
    # instantiates the CSR bridge -- a peripheral that did not answer at its
    # address would change the decoder, not just the module.
    import peripherals.flash, hyperram_probe
    from amaranth import Module
    from amaranth.lib import wiring

    def _empty(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)
        return m

    class _ILA(vexii_flash.FlashILA):
        elaborate = _empty

    class _Probe(vexii_flash.FlashPinProbe):
        elaborate = _empty

    class _Hyper(hyperram_probe.HyperRAMProbe):
        elaborate = _empty

    vexii_flash.FlashILA = _ILA
    vexii_flash.FlashPinProbe = _Probe
    hyperram_probe.HyperRAMProbe = _Hyper

import top as soc_top
from board.cynthion_r1_4 import CynthionPlatformRev1D4

# The block RAM initialiser does not affect the fabric this measures, and the
# shipping build puts no firmware there anyway.
CynthionPlatformRev1D4().build(
    soc_top.AwtoSoc(firmware=[0] * (soc_top.RAM_SIZE // 4)),
    do_program=False, build_dir={str(build_dir)!r})
"""
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(proc.stdout[-3000:] + "\n" + proc.stderr[-3000:])


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true",
                        help="reuse existing build directories if present")
    args = parser.parse_args()

    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    results = {}
    for label, stub in (("with instrumentation", False),
                        ("stubbed", True)):
        build_dir = ROOT / "tmp" / "instr-cost" / ("stub" if stub else "full")
        if not (args.keep and (build_dir / "top.tim").exists()):
            emit(f"building: {label} ...")
            build(stub, build_dir)
        results[label] = read_utilisation(build_dir)

    full_cells, full_fmax = results["with instrumentation"]
    stub_cells, stub_fmax = results["stubbed"]

    emit()
    emit(f"  {'cell':<16} {'with':>8} {'stubbed':>8} {'delta':>8} {'share':>7}")
    emit("  " + "-" * 52)
    for cell in CELLS:
        have, gone = full_cells.get(cell, 0), stub_cells.get(cell, 0)
        delta = have - gone
        share = (100.0 * delta / have) if have else 0.0
        emit(f"  {cell:<16} {have:>8} {gone:>8} {delta:>8} {share:>6.1f}%")

    emit()
    emit("  Fmax, and ONE PLACEMENT EACH -- see the docstring before quoting it")
    for clock in sorted(set(full_fmax) | set(stub_fmax)):
        emit(f"    {clock:<32} {full_fmax.get(clock, 0):>7.2f} -> "
             f"{stub_fmax.get(clock, 0):>7.2f} MHz")

    emit()
    emit("The delta is the LOGIC only: each stub keeps its CSR bridge, so a real")
    emit("deletion recovers this plus the bridge and its decoder window.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
