#!/usr/bin/env python3
#
# What the device engine costs in ECP5 cells, by yosys synth_ecp5 (no place and route, no board).
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Cell counts of `usbcore.device.Ep0Device` and of the L1 codecs it is built on. #612, #619.

    python3 scripts/soc/usb_device_area.py

- Yosys `synth_ecp5` on the module alone: LUT4, carry (CCU2C), flip-flops, LUTRAM (TRELLIS_DPR16X4),
  block RAM (DP16KD). Inputs and outputs are the module's ports, so nothing is optimised away
  unless it is unreachable from them.
- Not in-situ: the SoC is routing-bound (docs/soc-size-review.md); this is the engine's own size.
- Log: tmp/logs/usb_device_area.log. Netlist and yosys output: tmp/usb-device-area/.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from amaranth import ClockDomain, Module, Value
from amaranth.back import rtlil
from devlog import emit

OUT = ROOT / "tmp" / "usb-device-area"
YOSYS_LIMIT_S = 120      # waits for: synth_ecp5 of ~1k cells; measured ~3 s, 40x for a loaded box. Expiry: fail.
CELLS = ("LUT4", "CCU2C", "TRELLIS_FF", "TRELLIS_DPR16X4", "DP16KD", "L6MUX21", "PFUMX")


def yosys():
    found = shutil.which("yosys")
    if found:
        return found
    cand = Path.home() / "opt" / "oss-cad-suite" / "bin" / "yosys"
    if cand.exists():
        return str(cand)
    raise SystemExit("yosys not found (PATH or $HOME/opt/oss-cad-suite/bin)")


def synth(name, top_cls, **kw):
    m = Module()
    usb = ClockDomain("usb")
    m.domains.usb = usb
    dut = top_cls(**kw)
    m.submodules.dut = dut
    ports = [Value.cast(value) for _path, _flow, value in dut.signature.flatten(dut)]
    OUT.mkdir(parents=True, exist_ok=True)
    il = OUT / f"{name}.il"
    il.write_text(rtlil.convert(m, name=name, ports=[usb.clk, usb.rst] + ports))
    log = OUT / f"{name}.yosys.log"
    cmd = [yosys(), "-q", "-l", str(log), "-p",
           f"read_rtlil {il}; synth_ecp5 -top {name} -noflatten; flatten; opt_clean; stat"]
    subprocess.run(cmd, check=True, timeout=YOSYS_LIMIT_S, capture_output=True)
    text = log.read_text()
    last = text.rsplit("Printing statistics", 1)[-1]
    counts = {c: int(n) for n, c in re.findall(r"^\s+(\d+)\s+(\w+)\s*$", last, re.MULTILINE) if c in CELLS}
    return counts


def main():
    from usbcore.data import DataGenerator, DataReceiver
    from usbcore.device import Ep0Device
    from usbcore.handshake import HandshakeDetector, HandshakeGenerator
    from usbcore.token import TokenDetector
    emit("usb device engine, yosys synth_ecp5 cell counts")
    rows = []
    for name, cls in (("token_detector", TokenDetector), ("data_receiver", DataReceiver),
                      ("data_generator", DataGenerator), ("hs_detector", HandshakeDetector),
                      ("hs_generator", HandshakeGenerator), ("ep0_device", Ep0Device)):
        counts = synth(name, cls)
        rows.append((name, counts))
        emit("  " + f"{name:16}" + " ".join(f"{c}={counts.get(c, 0):<5}" for c in CELLS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
