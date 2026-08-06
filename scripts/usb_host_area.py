#!/usr/bin/env python3
#
# What the vendored host engine costs in fabric, by synthesis rather than guess.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measure the LUT, FF, BRAM and fmax cost of `gateware/probes/usb_host/guh`.

    ./scripts/usb_host_area.py              # baseline, then the engine
    ./scripts/usb_host_area.py --configs sie

Synthesis and place-and-route only. **No hardware is touched**; nothing is
programmed. Output goes to the terminal and to `tmp/logs/dev.log`, the toolchain's
own output to `tmp/usb-host-area/<config>/`.

## Why a scaffold, and why a baseline

An unconnected core synthesises to nothing. Each configuration is therefore
wrapped in a top level whose only job is to keep the core reachable: every
`USBSIEInterface` input is driven from a free-running LFSR, so nothing is
constant-folded and no state is unreachable, and every output is XOR-reduced onto
one LED pin, so nothing is dangling. The baseline is that scaffold with no engine
in it, and it is subtracted.

Clocking is `VariableClockDomainGenerator(sync_mhz=60)` -- the SoC's own generator
(`gateware/soc/vexii_hello_soc.py`), not LUNA's -- so the `sync`/`usb` split and
the 60 MHz constraint match the design this would go into.

## What this number is, and what it is not

It is the engine on the ULPI pins at ~10% occupancy. It is **not** the in-situ
figure: `docs/usb-host-options.md` section 12.3 records that this design is
routing-bound and that placement is stochastic to within about 9 MHz, so the cost
inside a 50%-full SoC has to be measured inside a 50%-full SoC. That build needs
`target_phy` to change owner (section 13), which is the next increment and not
this one. Until then the honest statement is that this design's own timing is
untouched, because no gateware was added to it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, log  # noqa: E402

BUILD_ROOT = ROOT / "tmp" / "usb-host-area"
RESULTS = BUILD_ROOT / "area-results.json"

# The part, for the percentages.
TOTAL_LUT, TOTAL_BRAM = 24288, 56

# A high-speed bulk packet is 512 bytes and the TX FIFO has to hold a whole one,
# so 512 is the depth any real integration would use. GUH's default of 64 would
# flatter the result.
FIFO_DEPTH = 512


def build_top(config):
    """One configuration's top level. Imported lazily so --help stays cheap."""
    from amaranth import Cat, Module, Signal
    from amaranth.hdl import Elaboratable
    from apollo_fpga.gateware.variable_clock import VariableClockDomainGenerator

    class Top(Elaboratable):
        def elaborate(self, platform):
            m = Module()
            m.submodules.car = VariableClockDomainGenerator(sync_mhz=60)

            led = platform.request("led", 0)
            observed = Signal()

            if config == "baseline":
                counter = Signal(24)
                m.d.usb += counter.eq(counter + 1)
                m.d.comb += [observed.eq(counter[-1]), led.o.eq(observed)]
                return m

            from usb_host.guh.sie import USBSIE

            core = USBSIE(bus=platform.request("target_phy"),
                          fifo_depth=FIFO_DEPTH)
            m.submodules.core = core
            ctrl = core.ctrl

            lfsr = Signal(32, init=1)
            m.d.usb += lfsr.eq(
                Cat(lfsr[1:], lfsr[0] ^ lfsr[10] ^ lfsr[30] ^ lfsr[31]))
            m.d.comb += [
                ctrl.bus_reset.eq(lfsr[0]),
                ctrl.xfer.start.eq(lfsr[1]),
                ctrl.xfer.type.eq(lfsr[2:4]),
                ctrl.xfer.dev_addr.eq(lfsr[4:11]),
                ctrl.xfer.ep_addr.eq(lfsr[11:15]),
                ctrl.xfer.data_pid.eq(lfsr[15]),
                ctrl.txs.valid.eq(lfsr[16]),
                ctrl.txs.payload.eq(lfsr[17:25]),
                ctrl.rxs.ready.eq(lfsr[25]),
            ]

            sink = Signal(32)
            m.d.usb += sink.eq(sink ^ Cat(
                ctrl.status.as_value(), ctrl.txs.ready,
                ctrl.rxs.valid, ctrl.rxs.payload))
            m.d.comb += [observed.eq(sink.xor()), led.o.eq(observed)]
            return m

    return Top()


_UTIL = re.compile(
    r"^\s*Info:\s+(?P<cell>[A-Z0-9_]+):\s+(?P<used>\d+)\s*/\s*(?P<total>\d+)",
    re.MULTILINE)
_FMAX = re.compile(
    r"Max frequency for clock\s+'(?P<clock>[^']+)':\s+(?P<fmax>[\d.]+)\s+MHz"
    r"\s+\((?P<verdict>PASS|FAIL) at (?P<target>[\d.]+) MHz\)")

CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "TRELLIS_RAMW", "DP16KD")


def measure(config):
    from board import CynthionPlatformRev1D4

    emit(f"  building {config} ...")
    build_dir = BUILD_ROOT / config
    build_dir.mkdir(parents=True, exist_ok=True)
    try:
        CynthionPlatformRev1D4().build(
            build_top(config), do_program=False, build_dir=str(build_dir))
    except Exception as exc:  # noqa: BLE001 -- report and keep the other configs
        log(f"{config} build failed: {exc}", "ERROR")
        return {"config": config, "ok": False, "error": str(exc)}

    report = build_dir / "top.tim"
    text = report.read_text(errors="replace") if report.is_file() else ""
    util = {m.group("cell"): int(m.group("used"))
            for m in _UTIL.finditer(text) if m.group("cell") in CELLS}
    timing = {m.group("clock"): {"fmax_mhz": float(m.group("fmax")),
                                 "target_mhz": float(m.group("target")),
                                 "verdict": m.group("verdict")}
              for m in _FMAX.finditer(text)}
    return {"config": config, "ok": bool(util),
            "utilisation": util, "timing": timing}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", nargs="*", default=["baseline", "sie"])
    args = parser.parse_args()

    emit("USB host engine area on Cynthion r1.4 "
         f"(LFE5U-12F: {TOTAL_LUT} LUT / {TOTAL_BRAM} BRAM)")
    results = [measure(config) for config in args.configs]

    base = next((r for r in results if r["config"] == "baseline" and r["ok"]), None)
    emit("")
    for result in results:
        if not result["ok"]:
            emit(f"  {result['config']:<10} FAILED: {result.get('error', '?')}")
            continue
        util = result["utilisation"]
        line = (f"  {result['config']:<10} "
                f"{util.get('TRELLIS_COMB', 0):6d} LUT  "
                f"{util.get('TRELLIS_FF', 0):5d} FF  "
                f"{util.get('DP16KD', 0):3d} BRAM  "
                f"{util.get('TRELLIS_RAMW', 0):4d} LUTRAM")
        if base and result is not base:
            delta = {cell: util.get(cell, 0) - base["utilisation"].get(cell, 0)
                     for cell in CELLS}
            line += (f"   (delta {delta['TRELLIS_COMB']:+d} LUT, "
                     f"{delta['TRELLIS_FF']:+d} FF, "
                     f"{delta['DP16KD']:+d} BRAM, "
                     f"{delta['TRELLIS_RAMW']:+d} LUTRAM)")
        emit(line)
        for clock, timing in result["timing"].items():
            emit(f"    {clock:<32} {timing['fmax_mhz']:7.2f} MHz vs "
                 f"{timing['target_mhz']:.2f} -- {timing['verdict']}")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2))
    emit(f"  results: {RESULTS.relative_to(ROOT)}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
