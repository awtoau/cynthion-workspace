#!/usr/bin/env python3
#
# Cell counts for the data path (yosys synth_ecp5, module alone, no place and route).
# SPDX-License-Identifier: BSD-3-Clause

"""Cells only: LUT4 / CCU2C / FF / DP16KD / LUTRAM for the stage 1 data path.

    python3 scripts/soc/datapath_area.py
    python3 scripts/soc/datapath_area.py --only usbport

Configurations (each alone behind plain ports, `synth_ecp5`, `stat`):
- `datapath`: `DataPath` (parser, rings, sequencer, builder, CSRs).
- `usbport` / `usbport_eng`: `UsbPort` without / with the engine port (the delta is the port).
- `serial` / `device`: LUNA `USBSerialDevice` / `DataPathDevice` on a bare UTMI bus.
Out of context: an upper bound on what the SoC gains. No timing is reported.
Output: terminal and `tmp/logs/dev.log`; yosys files in `tmp/datapath-area/`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("scripts", "gateware", "gateware/soc", "gateware/probes"):
    sys.path.insert(0, str(ROOT / sub))

import _paths  # noqa: F401,E402
from devlog import emit  # noqa: E402

BUILD = ROOT / "tmp" / "datapath-area"
CELLS = ("LUT4", "CCU2C", "TRELLIS_FF", "DP16KD", "TRELLIS_DPR16X4", "PFUMX", "L6MUX21")

# yosys on the largest config (UsbPort + SIE) takes ~1 min cold; 3 x (shared-box rule).
YOSYS_LIMIT_S = 180


def make(config):
    from amaranth.lib import wiring
    if config == "datapath":
        from datapath.core import DataPath
        return DataPath(), None
    if config in ("usbport", "usbport_eng"):
        from peripherals.usb_host import UsbPort
        return UsbPort(engine_port=config == "usbport_eng"), None
    from luna.gateware.interface.utmi import UTMIInterface
    utmi = UTMIInterface()
    kw = dict(bus=utmi, idVendor=0x1d50, idProduct=0x6180, max_packet_size=512)
    if config == "serial":
        from luna.gateware.usb.devices.acm import USBSerialDevice
        dev = USBSerialDevice(**kw)
        streams = (dev.rx, dev.tx)
    else:
        from datapath.device import DataPathDevice
        dev = DataPathDevice(**kw)
        streams = (dev.rx, dev.tx, dev.data_rx, dev.data_tx, dev.ctl_rx, dev.ctl_tx)
    def leaves(rec):
        for value in rec.fields.values():
            yield from (leaves(value) if hasattr(value, "fields") else (value,))
    ports = [dev.connect] + [s for st in (*streams, utmi) for s in leaves(st)]
    return dev, ports


def ports_of(component):
    return [value for _, _, value in component.signature.flatten(component)]


def build_il(config, path):
    from amaranth import Elaboratable, Module
    from amaranth.back import rtlil
    part, ports = make(config)
    ports = ports if ports is not None else ports_of(part)

    class Top(Elaboratable):
        def elaborate(self, platform):
            m = Module()
            m.submodules.part = part
            return m

    path.write_text(rtlil.convert(Top(), name="top", ports=ports))


def run(config):
    BUILD.mkdir(parents=True, exist_ok=True)
    il = BUILD / f"{config}.il"
    build_il(config, il)
    script = BUILD / f"{config}.ys"
    stat = BUILD / f"{config}-stat.txt"
    script.write_text(f"read_rtlil {il.name}\nsynth_ecp5 -top top\ntee -o {stat.name} stat\n")
    log = BUILD / f"{config}-yosys.log"
    with log.open("w") as out:
        try:
            proc = subprocess.run(["yosys", "-q", script.name], cwd=BUILD, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=YOSYS_LIMIT_S)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"{config}: yosys over {YOSYS_LIMIT_S}s; see {log}")
    if proc.returncode != 0:
        raise SystemExit(f"{config}: yosys failed; see {log}")
    counts = {}
    for line in stat.read_text().splitlines():
        found = re.match(r"^\s+(\d+)\s+(\S+)\s*$", line)
        if found and found.group(2) in CELLS:
            counts[found.group(2)] = int(found.group(1))
    return counts


CONFIGS = ("datapath", "usbport", "usbport_eng", "serial", "device")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated configs")
    args = parser.parse_args()
    chosen = [c for c in CONFIGS if not args.only or c in args.only.split(",")]
    emit("data path cells (yosys synth_ecp5, module alone; no timing)")
    emit(f"  {'config':<14} {'LUT4':>6} {'CCU2C':>6} {'FF':>6} {'DP16KD':>7} {'DPR16X4':>8}")
    rows = {}
    for config in chosen:
        c = rows[config] = run(config)
        emit(f"  {config:<14} {c.get('LUT4', 0):>6} {c.get('CCU2C', 0):>6} "
             f"{c.get('TRELLIS_FF', 0):>6} {c.get('DP16KD', 0):>7} {c.get('TRELLIS_DPR16X4', 0):>8}")
    for a, b in (("usbport", "usbport_eng"), ("serial", "device")):
        if a in rows and b in rows:
            d = {k: rows[b].get(k, 0) - rows[a].get(k, 0) for k in CELLS}
            emit(f"  delta {b} - {a}: LUT4 {d['LUT4']:+} CCU2C {d['CCU2C']:+} "
                 f"FF {d['TRELLIS_FF']:+} DP16KD {d['DP16KD']:+} DPR16X4 {d['TRELLIS_DPR16X4']:+}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
