#!/usr/bin/env python3
#
# Build the SoC with a candidate trim removed, and diff it. #451.
# SPDX-License-Identifier: BSD-3-Clause

"""Build the real SoC with and without a named trim, and report the delta.

`scripts/soc_module_area.py` reads what each module cost in a build that already
happened, which is exact for AREA and says nothing about what removing it does
to the rest of the design. This builds both sides.

**It edits no tracked file.** Each trim is a subclass installed on its module
before `top` is imported, in a subprocess so the second build does not reuse the
first one's imports. Every port and every CSR register survives, so the memory
map, the generated PAC and the firmware's addresses are unchanged and the only
difference is the logic -- which UNDERSTATES a real deletion by the CSR bridge
and the decoder window it would also remove.

    ./scripts/soc_trim_delta.py --trim spi-controller --runs 3
    ./scripts/soc_trim_delta.py --list

## Reading the numbers

Cell counts are a property of the netlist and repeat exactly for a given
elaboration. **Fmax does not.** `--no-parallel-refine` is forced so placement is
deterministic, but #441 means elaboration itself is not, so every Fmax here is
reported as a spread over `--runs` builds and a single number from it is not a
result. #440: a build killed by its own bound leaves a parseable `top.tim`, so
`Program finished normally.` is required.

Output is mirrored to ./tmp/logs/soc_trim_delta.log.
"""

import argparse
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_trim_delta.log"
OUT = ROOT / "tmp" / "trim-delta"

UTIL_RE = re.compile(r"^Info:\s+(\S+):\s+(\d+)/\s*(\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'(\S+)':\s+([\d.]+) MHz")
FINISHED = "Program finished normally."
CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "TRELLIS_RAMW")

# Each trim is Python spliced into the build subprocess before `top` is
# imported. One statement of setup per entry, and the entry's key is what
# `--trim` names.
TRIMS = {
    # SPI0: the arbitrary-command path to the flash. The shipping Rust firmware
    # never reads or writes it -- `scripts/soc_dead_peripherals.py` reports it
    # reachable only from the generated C firmware.
    "spi-controller": """
import peripherals.flash as flash
from amaranth import Module
from amaranth.lib import wiring

class _Controller(flash.HoldableSPIController):
    # The CSR window still answers -- its decoder, its `hold` register and the
    # WishboneCSRBridge in front of it all stay, so the memory map and the PAC
    # are unchanged. Only `inner`, upstream's SPIController, goes. A real
    # deletion recovers this PLUS that window.
    def elaborate(self, platform):
        m = Module()
        m.submodules.hold_bridge = self._hold_bridge
        m.submodules.decoder     = self._decoder
        m.d.comb += [
            self._decoder.bus.addr.eq(self.bus.addr),
            self._decoder.bus.r_stb.eq(self.bus.r_stb),
            self._decoder.bus.w_stb.eq(self.bus.w_stb),
            self._decoder.bus.w_data.eq(self.bus.w_data),
            self.bus.r_data.eq(self._decoder.bus.r_data),
        ]
        return m

# The crossbar is left alone. With port 0 never asserting `valid`, synthesis
# folds most of its arbitration anyway -- it measures 139 COMB unflattened and
# 2 flat -- so stubbing it would add nothing this can attribute.
flash.HoldableSPIController = _Controller
""",

    # The `claimed` switch in BusFault: a second copy of the decoder's window
    # comparison, over the same 30-bit address, to answer ERR one cycle sooner
    # than the timeout would. The timeout catches strictly more.
    "bus-fault-claimed": """
import bus.fault as fault

_real = fault.BusFault.elaborate

class _Total:
    # ONE window matching every address, so `claimed` folds to a constant 1 and
    # the Switch disappears. Not "no windows" -- that folds `claimed` to 0, which
    # answers ERR to every access and takes the rest of the design with it.
    class bus:
        class memory_map:
            @staticmethod
            def window_patterns():
                return [(None, None, ("-" * 32, None))]

class _NoClaimed(fault.BusFault):
    def elaborate(self, platform):
        keep, self._decoder = self._decoder, _Total
        try:
            return _real(self, platform)
        finally:
            self._decoder = keep

fault.BusFault = _NoClaimed
""",

    # THE POSITIVE CONTROL. `HyperRAMProbe` is twelve counters and a large CSR
    # block; if stubbing it does not move the total either, the harness is not
    # measuring removals and no other row in this table means anything.
    "hyperram-probe": """
import peripherals.hyperram_probe as hyperram_probe
from amaranth import Module
from amaranth.lib import wiring

class _Hyper(hyperram_probe.HyperRAMProbe):
    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)
        return m

hyperram_probe.HyperRAMProbe = _Hyper
""",
}


def build(setup, build_dir):
    """Build the SoC in a subprocess with `setup` spliced in before the import."""
    script = f"""
import os, sys
from pathlib import Path
ROOT = Path({str(ROOT)!r})
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

import fast_build_env
# Placement must be deterministic for a delta to mean anything; the Fmax spread
# this leaves is elaboration's, not the placer's (#441, #306).
os.environ["AMARANTH_nextpnr_opts"] = "--threads 31 --router router2"


{setup}

import top as soc_top
from board.cynthion_r1_4 import CynthionPlatformRev1D4

CynthionPlatformRev1D4().build(
    soc_top.AwtoSoc(firmware=[0] * (soc_top.RAM_SIZE // 4)),
    do_program=False, build_dir={str(build_dir)!r})
"""
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(proc.stdout[-4000:] + "\n" + proc.stderr[-4000:])


def read(build_dir):
    """(cells, fmax) from a FINISHED nextpnr run (#440)."""
    text = (build_dir / "top.tim").read_text()
    if FINISHED not in text:
        raise SystemExit(f"{build_dir}/top.tim did not finish -- not a result (#440)")
    cells, fmax = {}, {}
    for line in text.splitlines():
        match = UTIL_RE.match(line.strip())
        if match and match.group(1) in CELLS:
            cells[match.group(1)] = int(match.group(2))
        match = FMAX_RE.search(line)
        if match:
            fmax.setdefault(match.group(1), []).append(float(match.group(2)))
    # nextpnr prints the table twice, before and after routing; the last is the
    # one that describes the bitstream.
    return cells, {clock: values[-1] for clock, values in fmax.items()}


def spread(values):
    if not values:
        return "--"
    if len(values) == 1:
        return f"{values[0]:.2f} (1 build)"
    return (f"{min(values):.2f}-{max(values):.2f}, median "
            f"{statistics.median(values):.2f} ({len(values)} builds)")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trim", help="which trim to measure")
    parser.add_argument("--runs", type=int, default=2,
                        help="builds per side (default 2)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list or not args.trim:
        for name in TRIMS:
            print(f"  {name}")
        return 0
    if args.trim not in TRIMS:
        raise SystemExit(f"no such trim: {args.trim}; --list shows them")

    out = []

    def emit(line=""):
        print(line, flush=True)
        out.append(line)

    emit(f"trim: {args.trim}, {args.runs} builds a side, "
         f"--parallel-refine OFF")
    results = {}
    for label, setup in (("baseline", ""), (args.trim, TRIMS[args.trim])):
        cells_runs, fmax_runs = [], {}
        for run in range(args.runs):
            # One baseline set, shared by every trim: it is the same design.
            build_dir = OUT / (f"baseline-{run}" if not setup
                               else f"{args.trim}-{run}")
            # A finished build is reused. Cell counts are a property of the
            # netlist, and re-running it would only re-roll the elaboration
            # noise of #441 -- which is what `--runs` is sampling on purpose.
            if (build_dir / "top.tim").exists() and \
                    FINISHED in (build_dir / "top.tim").read_text():
                emit(f"  reusing {label} run {run}")
            else:
                emit(f"  building {label} run {run} ...")
                build(setup, build_dir)
            cells, fmax = read(build_dir)
            cells_runs.append(cells)
            for clock, value in fmax.items():
                fmax_runs.setdefault(clock, []).append(value)
        results[label] = (cells_runs, fmax_runs)

    emit()
    emit(f"  {'cell':<16} {'baseline':>20} {'trimmed':>20} {'delta':>10}")
    emit("  " + "-" * 70)
    base_runs = results["baseline"][0]
    trim_runs = results[args.trim][0]
    for cell in CELLS:
        base = sorted({run.get(cell, 0) for run in base_runs})
        trim = sorted({run.get(cell, 0) for run in trim_runs})
        delta = (statistics.median(trim) - statistics.median(base))
        emit(f"  {cell:<16} {str(base):>20} {str(trim):>20} {delta:>+10.0f}")
    emit()
    emit("  a cell count that is not identical across runs of ONE side is #441:")
    emit("  elaboration is not reproducible, so the netlist is not either.")
    emit()
    emit("  Fmax, MHz")
    for clock in sorted(set(results["baseline"][1]) | set(results[args.trim][1])):
        emit(f"    {clock:<34}")
        emit(f"      baseline {spread(results['baseline'][1].get(clock, []))}")
        emit(f"      trimmed  {spread(results[args.trim][1].get(clock, []))}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
