#!/usr/bin/env python3
#
# Does `SocClocks`' fast domain reach a real edge clock, or general fabric? #314.
# SPDX-License-Identifier: BSD-3-Clause

"""
Build the smallest design that gives `SocClocks(with_fast=True)` an ECLK consumer,
and read the answer out of the bitstream.

    ./scripts/soc_eclk_check.py                       # build and report
    ./scripts/soc_eclk_check.py --no-build            # report the last build

## Why a harness rather than the SoC

`top.py` builds `fast` only when `FLASH_PHY_FAST` or `HYPERRAM_DQS` is on, and
both are off in the shipping build -- so the shipping bitstream contains no edge
clock and cannot answer the question either way. `HyperRAMDQSPHY` is the only
consumer that takes `ClockSignal("fast")` as an ECLK, so this pairs it with
`SocClocks` and nothing else. Turning either flag on makes the shipping SoC this
design's clocking, which is what makes the answer here the SoC's answer.

## What is being tested

The bank ECLK input mux accepts CLKOP and CLKOS from either LEFT PLL, four clock
pads and two fabric taps. CLKOS2 and CLKOS3 reach the primary clock network only,
so an ECLK asked to come from CLKOS2 can arrive only through the fabric tap --
which nextpnr takes, announcing it as `log_info` rather than a warning. Nothing
in the build output, the timing report or the metrics row says the edge clock is
unconstrained. #314.

## The evidence, and where it is

PASS is two statements about the artifacts, not about the log:

  * `top.config`, in a `.tile *:ECLK_L` block: `arc: *ECLKI* G_JLLCPLL0CLKOS` --
    the PLL output driving the bank ECLK mux directly.
  * `top.tim`: no "general routing will be used".

Output goes to `tmp/eclk-probe/`.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

from amaranth import Cat, Elaboratable, Module, Signal

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp" / "eclk-probe"

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

import fast_build_env  # noqa: E402,F401  (sets AMARANTH_nextpnr_opts on import)
from devlog import emit  # noqa: E402

FABRIC_ECLK = "general routing will be used"
ECLK_ARC_RE = re.compile(r"^arc: (\S*ECLKI\S*) (\S+)$", re.M)


class EclkProbe(Elaboratable):
    """`SocClocks` at the shipping frequency, with a DQS PHY hung off `fast`.

    The counter is not decoration: with constant inputs yosys prunes the gearing
    primitives and there is no ECLK left to route, which would report PASS for a
    design that no longer asks the question.
    """

    def __init__(self, sync_mhz=60.0):
        self.sync_mhz = sync_mhz

    def elaborate(self, platform):
        from clocks import SocClocks
        from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY

        m = Module()
        m.submodules.car = car = SocClocks(sync_mhz=self.sync_mhz, with_fast=True)
        bus = platform.request("ram", 0, dir="-")
        m.submodules.phy = phy = HyperRAMDQSPHY(bus=bus)

        counter = Signal(32)
        m.d.sync += counter.eq(counter + 1)
        m.d.comb += [
            phy.phy.clk_en.eq(counter[:2]),
            phy.phy.dq.o.eq(counter),
            phy.phy.dq.e.eq(counter[4]),
            phy.phy.rwds.o.eq(counter[:4]),
            phy.phy.rwds.e.eq(counter[5]),
            phy.phy.cs.eq(counter[6]),
            phy.phy.reset.eq(0),
        ]
        # A sink outside the PHY, so the read path is not dead logic either.
        leds = Cat(platform.request("led", index).o for index in range(6))
        m.d.comb += leds.eq(phy.phy.dq.i ^ Cat(phy.phy.rwds.i, phy.dll_locked,
                                               phy.dll_ready, car.locked))
        return m


def report(build_dir):
    """What the artifacts say about the edge clock. Returns True on PASS."""
    config = (build_dir / "top.config")
    tim = (build_dir / "top.tim")
    if not config.exists():
        emit(f"no bitstream at {config}; run without --no-build")
        return False

    text = config.read_text()
    sources = []
    for match in re.finditer(r"^\.tile (\S+:ECLK_[LR])\n((?:.+\n)*)", text, re.M):
        for arc in ECLK_ARC_RE.finditer(match.group(2)):
            sources.append((match.group(1), arc.group(1), arc.group(2)))

    fabric = tim.read_text().count(FABRIC_ECLK) if tim.exists() else -1
    emit(f"\n{build_dir.relative_to(ROOT)}:")
    if not sources:
        emit("  no ECLK mux is configured -- this design has no edge clock")
    for tile, sink, source in sources:
        emit(f"  {tile:20s} {sink} <- {source}")
    emit(f"  '{FABRIC_ECLK}' in top.tim: {fabric}")
    for line in (tim.read_text().splitlines() if tim.exists() else []):
        if "Max frequency for clock" in line:
            emit("  " + line.split("Info:")[-1].strip())

    dedicated = [s for _, _, s in sources if "CPLL0CLK" in s]
    ok = bool(dedicated) and fabric == 0
    emit(f"  => {'PASS' if ok else 'FAIL'}: "
         f"{len(dedicated)} dedicated PLL arc(s), {fabric} fabric fallback(s)")
    return ok


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sync-mhz", type=float, default=60.0,
                        help="the shipping SoC's CPU clock")
    parser.add_argument("--label", default="current",
                        help="subdirectory of tmp/eclk-probe for this build")
    parser.add_argument("--no-build", action="store_true",
                        help="only re-read the artifacts of the last build")
    args = parser.parse_args()

    build_dir = OUT / args.label
    if not args.no_build:
        from board.cynthion_r1_4 import CynthionPlatformRev1D4
        shutil.rmtree(build_dir, ignore_errors=True)
        emit(f"building the ECLK probe at sync = {args.sync_mhz:g} MHz "
             f"-> {build_dir.relative_to(ROOT)}")
        CynthionPlatformRev1D4().build(EclkProbe(sync_mhz=args.sync_mhz),
                                       do_program=False,
                                       build_dir=str(build_dir))
    return 0 if report(build_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
