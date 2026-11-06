#!/usr/bin/env python3
#
# What one Wishbone-to-CSR bridge per peripheral costs against one shared. #619.
# SPDX-License-Identifier: BSD-3-Clause

"""Per-peripheral `WishboneCSRBridge` against one `csr.Decoder` + one `CsrBridge`.

Alone, not the whole SoC: real peripherals (intc, two 16550s, CLINT) synthesised
with `yosys synth_ecp5`, an upper bound in the sense
`scripts/soc/soc_peripheral_area.py` means.

  * `bare`   -- the peripheral's CSR bus left as ports
  * `own`    -- each peripheral behind its own upstream `WishboneCSRBridge`,
                joined by a `wishbone.Decoder` (what top.py does today)
  * `shared` -- every peripheral on one `csr.Decoder` behind one `CsrBridge`
                (what top.py does after #619), including its ERR for holes

    ./scripts/soc/soc_csr_bridge_cost.py

Output is mirrored to ./tmp/logs/soc_csr_bridge_cost.log.
"""

import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BUILD = ROOT / "tmp" / "csr-bridge-cost"
LOG = ROOT / "tmp" / "logs" / "soc_csr_bridge_cost.log"

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware"))

from amaranth import Module, Signal                       # noqa: E402
from amaranth.back import verilog                         # noqa: E402
from amaranth.hdl import UnusedElaboratable               # noqa: E402
from amaranth.lib import wiring                           # noqa: E402
from amaranth.lib.wiring import In, Out, flipped          # noqa: E402
from amaranth_soc import csr, wishbone                    # noqa: E402
from amaranth_soc.csr.wishbone import WishboneCSRBridge   # noqa: E402

from bus.csr_bridge import CsrBridge                      # noqa: E402
from cpu.clint import Clint                               # noqa: E402
from cpu.intc import Interrupts                           # noqa: E402
from peripherals.uart16550 import Uart16550               # noqa: E402

warnings.filterwarnings("ignore", category=UnusedElaboratable)

FEATURES = {"cti", "bte", "err"}


def peripherals():
    """(name, instance, byte offset): the sizes and spread of the real map."""
    return [("intc", Interrupts({n: "level" for n in range(20)}), 0xc00),
            ("console", Uart16550(), 0x000),
            ("apollo", Uart16550(), 0x500),
            ("clint", Clint(), 0x10000)]


class Bare(wiring.Component):
    def __init__(self, periph):
        self._periph = periph
        super().__init__({"bus": In(csr.Signature(
            addr_width=periph.bus.addr_width, data_width=8))})
        self.bus.memory_map = periph.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.p = self._periph
        wiring.connect(m, flipped(self.bus), self._periph.bus)
        return m


class Own(wiring.Component):
    def __init__(self, items):
        self._items = items
        super().__init__({"wb": In(wishbone.Signature(
            addr_width=30, data_width=32, granularity=8, features=FEATURES))})

    def elaborate(self, platform):
        m = Module()
        dec = wishbone.Decoder(addr_width=30, data_width=32, granularity=8,
                               features=FEATURES)
        m.submodules.dec = dec
        for name, periph, base in self._items:
            m.submodules[name] = periph
            bridge = WishboneCSRBridge(periph.bus, data_width=32)
            m.submodules[name + "_br"] = bridge
            dec.add(bridge.wb_bus, addr=0xf0000000 + base, name=name)
        wiring.connect(m, flipped(self.wb), dec.bus)
        return m


class Shared(wiring.Component):
    def __init__(self, items):
        self._items = items
        super().__init__({"wb": In(wishbone.Signature(
            addr_width=30, data_width=32, granularity=8, features=FEATURES))})

    def elaborate(self, platform):
        m = Module()
        csr_dec = csr.Decoder(addr_width=18, data_width=8)
        m.submodules.csr_dec = csr_dec
        for name, periph, base in self._items:
            m.submodules[name] = periph
            csr_dec.add(periph.bus, addr=base, name=name)
        bridge = CsrBridge(csr_dec.bus)
        m.submodules.bridge = bridge
        dec = wishbone.Decoder(addr_width=30, data_width=32, granularity=8,
                               features=FEATURES)
        m.submodules.dec = dec
        dec.add(bridge.wb_bus, addr=0xf0000000)
        wiring.connect(m, flipped(self.wb), dec.bus)
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
    comb = sum(cells.get(k, 0) for k in ("LUT4", "PFUMX", "L6MUX21", "CCU2C"))
    return comb, cells.get("TRELLIS_FF", 0)


def main():
    out = []

    def emit(line=""):
        print(line)
        out.append(line)

    emit("  alone, not the whole SoC: synth_ecp5, upper bound")
    emit()
    emit(f"  {'case':<28} {'COMB':>6} {'FF':>5}")
    emit("  " + "-" * 41)
    bare = {}
    for name, periph, _ in peripherals():
        bare[name] = synth(f"bare_{name}", Bare(periph))
        emit(f"  {'bare ' + name:<28} {bare[name][0]:>6} {bare[name][1]:>5}")
    emit()
    for name, periph, base in peripherals():
        comb, ff = synth(f"own1_{name}", Own([(name, periph, base)]))
        emit(f"  {'+ own bridge, ' + name:<28} {comb:>6} {ff:>5}   "
             f"delta {comb - bare[name][0]:+d} COMB {ff - bare[name][1]:+d} FF")
    emit()
    own = synth("own_all", Own(peripherals()))
    shared = synth("shared_all", Shared(peripherals()))
    total = [sum(v[i] for v in bare.values()) for i in (0, 1)]
    emit(f"  {'bare, summed':<28} {total[0]:>6} {total[1]:>5}")
    emit(f"  {'own bridges x4 + wb decoder':<28} {own[0]:>6} {own[1]:>5}")
    emit(f"  {'one csr.Decoder + CsrBridge':<28} {shared[0]:>6} {shared[1]:>5}")
    emit(f"  {'saved':<28} {own[0] - shared[0]:>6} {own[1] - shared[1]:>5}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
