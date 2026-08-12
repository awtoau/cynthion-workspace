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

# Extra decoder windows, added at elaboration. `{count}` of them at addresses
# nothing decodes today, each fed 32 live bits so the read-data gather cannot
# fold -- that gather is 98% of the decoder's cells (`soc_decoder_cost.py`).
# A trim that ADDS is how the marginal window is measured: removing one prunes
# the peripheral behind it as well, and the two cannot then be separated.
_EXTRA_WINDOWS = '''
from amaranth import Cat, Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In
from amaranth_soc import csr, wishbone
from amaranth_soc.csr.wishbone import WishboneCSRBridge
from amaranth_soc.memory import MemoryMap

_COUNT = {count}
_BRIDGED = {bridged}

class _Held(wiring.Component):
    def __init__(self):
        super().__init__({{}})

class _Bare(wiring.Component):
    """ACK always, and 32 live bits back."""
    def __init__(self):
        super().__init__({{
            "bus": In(wishbone.Signature(addr_width=2, data_width=32,
                                         granularity=8)),
            "data": In(32),
        }})
        memory_map = MemoryMap(addr_width=4, data_width=8)
        memory_map.add_resource(_Held(), name=("held",), size=16)
        self.bus.memory_map = memory_map

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [self.bus.ack.eq(self.bus.cyc & self.bus.stb),
                     self.bus.dat_r.eq(self.data)]
        return m

class _Bridged(wiring.Component):
    """One live 32-bit register behind a csr.Bridge and a WishboneCSRBridge."""
    def __init__(self):
        builder = csr.Builder(addr_width=3, data_width=8)
        self._reg = csr.Register({{"value": csr.Field(csr.action.R, 32)}},
                                 access="r")
        builder.add("value", self._reg)
        self._csr = csr.Bridge(builder.as_memory_map())
        self._wb = WishboneCSRBridge(self._csr.bus, data_width=32)
        super().__init__({{"data": In(32)}})
        self.bus = self._wb.wb_bus

    def elaborate(self, platform):
        m = Module()
        m.submodules.csr = self._csr
        m.submodules.wb = self._wb
        m.d.comb += self._reg.f.value.r_data.eq(self.data)
        return m

_real_elaborate = wishbone.Decoder.elaborate

class _Extra(wishbone.Decoder):
    def elaborate(self, platform):
        subs = []
        for index in range(_COUNT):
            sub = _Bridged() if _BRIDGED else _Bare()
            self.add(sub.bus, addr=0xf0001000 + index * 0x100,
                     name=f"extra{{index}}")
            subs.append(sub)
        m = Module()
        m.submodules.decoder = _real_elaborate(self, platform)
        for index, sub in enumerate(subs):
            m.submodules[f"extra{{index}}"] = sub
            # 32 bits that move: the decoder's own address, plus two strobes.
            m.d.comb += sub.data.eq(Cat(self.bus.adr, self.bus.we, self.bus.stb))
        return m

wishbone.Decoder = _Extra
'''

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

    # The whole SPI0 stack, window included: the controller, its hold register,
    # its WishboneCSRBridge and the decoder window in front of them. #442's
    # deletion, as opposed to `spi-controller` above which keeps the window.
    "window-spi0": """
import peripherals.flash as flash
from amaranth import Module
from amaranth_soc import wishbone

class _Gone(flash.HoldableSPIController):
    def elaborate(self, platform):
        return Module()

flash.HoldableSPIController = _Gone

_real_add = wishbone.Decoder.add

def _add(self, sub_bus, *, name=None, addr=None, sparse=False):
    # The bridge is left constructed and unconnected; with its `wb_bus` read by
    # nothing, synthesis prunes it, which is what a deletion would do.
    if name == "spi0":
        return None
    return _real_add(self, sub_bus, name=name, addr=addr, sparse=sparse)

wishbone.Decoder.add = _add
""",

    # WHAT ONE DECODER WINDOW COSTS, measured by ADDING rather than removing:
    # a removal prunes the peripheral behind it too and the two cannot be
    # separated. Three bare subordinates, each answering ACK and 32 live bits,
    # at addresses nothing else uses. Divide by three.
    "plus3-windows": _EXTRA_WINDOWS.format(count=3, bridged=False),
    "plus1-window": _EXTRA_WINDOWS.format(count=1, bridged=False),

    # The same, each behind a `WishboneCSRBridge` and an 8-byte CSR block --
    # the shape of the `console` window. The difference from `plus3-windows` is
    # what the bridge and its multiplexer cost (#443).
    "plus3-bridged-windows": _EXTRA_WINDOWS.format(count=3, bridged=True),

    # THE NULL CONTROL. Nothing is removed and nothing is added: one 32-bit
    # constant takes a different value. Whatever this moves is what the MAPPER
    # moves when the netlist changes at all, and no trim delta smaller than it
    # is attributable to the trim. #454.
    "null-constant": """
import top as _top
_top.I2C_SCL_HZ = 999_000
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
