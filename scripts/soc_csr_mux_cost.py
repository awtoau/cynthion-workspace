#!/usr/bin/env python3
#
# What a csr.Bridge read multiplexer costs, per register. #443.
# SPDX-License-Identifier: BSD-3-Clause

"""What an `amaranth_soc.csr.Bridge` costs as registers are added to it.

Every CSR peripheral on this SoC is a `csr.Builder` of registers behind one
`csr.Bridge`, and `scripts/soc_module_area.py` finds that bridge is 76-88% of
the area of every register-only peripheral in the shipping netlist -- more than
the logic it fronts. This is the controlled measurement of why.

One block of eight 32-bit read-only registers is synthesised repeatedly, varying
only how many of them are driven by a CONSTANT and how many by a live input.
Nothing else changes: same block size, same address width, same bridge.

    ./scripts/soc_csr_mux_cost.py

Out of context, so an upper bound in the sense
`scripts/soc_peripheral_area.py`'s docstring means. What is being measured here
is a SLOPE -- the marginal cost of one more live register -- and the slope is
the part that survives into a real design.

Output is mirrored to ./tmp/logs/soc_csr_mux_cost.log.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tmp" / "csr-mux-cost"
LOG = ROOT / "tmp" / "logs" / "soc_csr_mux_cost.log"

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import Const, Module                       # noqa: E402
from amaranth.back import verilog                        # noqa: E402
from amaranth.lib import wiring                          # noqa: E402
from amaranth.lib.wiring import In                       # noqa: E402
from amaranth_soc import csr                             # noqa: E402

# Eight 32-bit registers is about the size of a peripheral's block in this SoC.
BLOCK = 8

# (constants, live). The first row is the control: a block whose every register
# is a constant, which must come out near zero -- if it does not, what follows
# is not measuring what it says.
CASES = ((8, 0), (7, 1), (6, 2), (4, 4), (0, 2), (0, 4), (0, 8))


class Block(wiring.Component):
    """`constants` + `live` read-only 32-bit registers behind one bridge."""

    def __init__(self, constants, live):
        builder = csr.Builder(addr_width=5, data_width=8)
        self._const, self._live = [], []
        for index in range(constants):
            register = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                    access="r")
            builder.add(f"c{index}", register)
            self._const.append(register)
        for index in range(live):
            register = csr.Register({"value": csr.Field(csr.action.R, 32)},
                                    access="r")
            builder.add(f"l{index}", register)
            self._live.append(register)
        self._bridge = csr.Bridge(builder.as_memory_map())
        super().__init__({
            "bus": In(csr.Signature(addr_width=5, data_width=8)),
            **{f"in{index}": In(32) for index in range(live)},
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)
        for index, register in enumerate(self._const):
            m.d.comb += register.f.value.r_data.eq(Const(0xa5a50000 | index, 32))
        for index, register in enumerate(self._live):
            m.d.comb += register.f.value.r_data.eq(getattr(self, f"in{index}"))
        return m


def synth(name, dut):
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / f"{name}.v"
    source.write_text(verilog.convert(dut, name=name))
    proc = subprocess.run(
        ["yosys", "-p", f"read_verilog {source}; synth_ecp5 -top {name}; stat"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip()[-1500:])
    cells = {}
    tail = proc.stdout.rsplit(f"=== {name} ===", 1)[-1]
    for line in tail.splitlines():
        match = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if match:
            cells[match.group(2)] = int(match.group(1))
    return cells


def main():
    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    emit(f"  one csr.Bridge, {BLOCK} registers of 32 bits, read-only")
    emit()
    emit(f"  {'constant':>9} {'live':>5} {'COMB':>7} {'LUT4':>7} {'MUX':>6} "
         f"{'FF':>5}")
    emit("  " + "-" * 46)
    for constants, live in CASES:
        cells = synth(f"blk{constants}_{live}", Block(constants, live))
        comb = (cells.get("LUT4", 0) + cells.get("PFUMX", 0)
                + cells.get("L6MUX21", 0) + cells.get("CCU2C", 0))
        emit(f"  {constants:>9} {live:>5} {comb:>7} {cells.get('LUT4', 0):>7} "
             f"{cells.get('PFUMX', 0) + cells.get('L6MUX21', 0):>6} "
             f"{cells.get('TRELLIS_FF', 0):>5}")

    emit()
    emit("The first row is the control: all constants, so the multiplexer folds")
    emit("and what is left is the address decode alone. Every row below it is the")
    emit("same block with the same number of registers.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
