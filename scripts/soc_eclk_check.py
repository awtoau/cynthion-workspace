#!/usr/bin/env python3
#
# Does an edge clock reach its bank on dedicated routing, or on general fabric? #314.
# SPDX-License-Identifier: BSD-3-Clause

"""
Read the answer out of a build's own artifacts, and fail the build if it is wrong.

    ./scripts/soc_eclk_check.py --audit tmp/awto_soc/build/<slug>   # any build
    ./scripts/soc_eclk_check.py                                     # build the probe
    ./scripts/soc_eclk_check.py --control                           # must FAIL

## The fault, and why nothing reported it

`HyperRAMDQSPHY` takes its ECLK from `fast`/`hr_fast`. The bank ECLK input mux
accepts CLKOP and CLKOS from a left PLL, four clock pads and two fabric taps --
ten sources. CLKOS2/CLKOS3 reach the primary clock network only, so an ECLK asked
for from CLKOS2 arrives only through the fabric tap: nextpnr says
`no route found, general routing will be used` as `log_info`, and the build
passes. Skew on the clock the capture phase is measured against is then
unconstrained and moves with placement.

`ECLKBRIDGECS` is not the fix. Its bel pins are hardwired to two input-mux
OUTPUTS, so it sits downstream of the mux that rejects the source.
`ECLKBRIDGECS: 0/2` is the success criterion, not `1/2`.

## What is checked, all of it from artifacts

  1. `top.config`, `ECLK_[LR]` tile: the `*ECLKI*` arc's source is a PLL global
     `G_J{LL,UL}CPLL0CLK{OP,OS}`, and no arc comes from a `*QECLKCIB*` tap.
  2. `top.tim`: zero `no route found, general routing will be used`.
  3. `top.tim`: `Promoted '<net>' to bank <n> ECLK<i>` -- the promotion happened.
  4. `top.tim`: a derived frequency constraint on the promoted `$eclk<n>_<i>` net,
     so the edge clock is timed rather than merely routed.

A design with no edge clock passes vacuously and says so -- the shipping SoC
builds `fast` only when `FLASH_PHY_FAST` or `HYPERRAM_DQS` is on.

**nextpnr reports no Fmax for an ECLK domain.** Its consumers are hard IOLOGIC
and DQSBUFM bels with no timed arcs, so the derived constraint above is the whole
of the tool's timing statement about it. Structure is the only evidence available
here; that is why this exists.

## The control

`--control` builds the same probe with the fix removed -- CLKOS2 and no `a_BEL`,
which is the pre-fix source at 861439e -- by mutating the elaborated PLL instance
rather than by keeping a broken copy of the generator. It must FAIL, and a run
where it passes means this check has stopped looking.

Output goes to `tmp/eclk-probe/<label>/`.
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

FABRIC_ECLK = "general routing will be used"
HAS_ECLK = "trying dedicated routing for edge clock source"
ECLK_ARC_RE = re.compile(r"^arc: (\S*ECLKI\S*) (\S+)$", re.M)
DEDICATED_RE = re.compile(r"^G_J(?:LL|UL)CPLL0CLK(?:OP|OS)$")
FABRIC_TAP_RE = re.compile(r"QECLKCIB")
PROMOTED_RE = re.compile(r"Promoted '(\S+)' to bank (\d+) ECLK(\d+)")
DERIVED_RE = re.compile(r"Derived frequency constraint of ([\d.]+) MHz for net "
                        r"(\S+\$eclk\d+_\d+)")
FMAX_RE = re.compile(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz "
                     r"\((PASS|FAIL) at ([\d.]+) MHz\)")
UTIL_RE = re.compile(r"^Info:\s+(ECLKSYNCB|ECLKBRIDGECS|TRELLIS_ECLKBUF|DQSBUFM):"
                     r"\s+(\d+)/\s*(\d+)", re.M)


def _last_run(text):
    """Only the last nextpnr run. `top.tim` accumulates across invocations."""
    from build_metrics import last_run
    return last_run(text)


def audit(build_dir):
    """What the artifacts say about the edge clock.

    Returns `(ok, lines)`. `ok` is False only for a design that HAS an edge clock
    and got it wrong; a design without one cannot fail.
    """
    build_dir = Path(build_dir)
    config, tim = build_dir / "top.config", build_dir / "top.tim"
    lines, checks = [f"{build_dir}:"], []

    if not config.exists() or not tim.exists():
        return False, lines + [f"  no top.config/top.tim -- nothing was built here"]

    log = _last_run(tim.read_text(errors="replace"))
    text = config.read_text(errors="replace")

    arcs = []
    for tile in re.finditer(r"^\.tile (\S+:ECLK_[LR])\n((?:.+\n)*)", text, re.M):
        for arc in ECLK_ARC_RE.finditer(tile.group(2)):
            arcs.append((tile.group(1), arc.group(1), arc.group(2)))

    if HAS_ECLK not in log and not arcs:
        return True, lines + ["  no edge clock in this design -- nothing to check"]

    for tile, sink, source in arcs:
        lines.append(f"  {tile:20s} {sink} <- {source}")
    dedicated = [s for _t, _k, s in arcs if DEDICATED_RE.match(s)]
    tapped = [s for _t, _k, s in arcs if FABRIC_TAP_RE.search(s)]
    checks.append((bool(dedicated) and not tapped,
                   f"{len(dedicated)} PLL arc(s) into the bank ECLK mux, "
                   f"{len(tapped)} fabric tap(s)"))

    fabric = log.count(FABRIC_ECLK)
    checks.append((fabric == 0, f"'{FABRIC_ECLK}': {fabric}"))

    promoted = PROMOTED_RE.findall(log)
    for net, bank, index in promoted:
        lines.append(f"  promoted {net} -> bank {bank} ECLK{index}")
    checks.append((bool(promoted), f"{len(promoted)} edge clock(s) promoted to a bank"))

    derived = DERIVED_RE.findall(log)
    for mhz, net in derived:
        lines.append(f"  timed    {net} at {mhz} MHz")
    checks.append((bool(derived), f"{len(derived)} derived ECLK constraint(s)"))

    for bel, used, avail in UTIL_RE.findall(log):
        lines.append(f"  {bel:16s} {used}/{avail}")
    # Last wins: nextpnr prints the table twice, after placement and after
    # routing, and only the second is this build's.
    fmax = {row[0]: row for row in FMAX_RE.findall(log)}
    for clock, mhz, verdict, target in fmax.values():
        lines.append(f"  {clock:28s} {mhz:>7} MHz  {verdict} at {target} MHz")
    lines.append("  no Fmax row for the ECLK: its consumers are hard IOLOGIC bels")

    ok = all(passed for passed, _ in checks)
    for passed, what in checks:
        lines.append(f"  {'ok  ' if passed else 'FAIL'} {what}")
    lines.append(f"  => {'PASS' if ok else 'FAIL'}")
    return ok, lines


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


def break_the_fix(fragment):
    """Put the edge clock back on CLKOS2 and unpin the PLL -- the design at 861439e.

    Mutates the elaborated instance rather than keeping a broken copy of the
    generator, so the control cannot drift away from what it is a control for.
    """
    found = []

    def walk(node):
        for sub, _name, _src in node.subfragments:
            if getattr(sub, "type", None) == "EHXPLLL":
                found.append(sub)
            walk(sub)

    walk(fragment)
    for pll in found:
        if "CLKOS" not in pll.ports:
            continue
        pll.ports["CLKOS2"] = pll.ports.pop("CLKOS")
        for name in ("ENABLE", "DIV", "CPHASE", "FPHASE"):
            if f"CLKOS_{name}" in pll.parameters:
                pll.parameters[f"CLKOS2_{name}"] = pll.parameters.pop(f"CLKOS_{name}")
        pll.attrs.pop("BEL", None)
    if not found:
        raise RuntimeError("no EHXPLLL to break -- the control would test nothing")
    return fragment


def build_probe(build_dir, sync_mhz, control):
    from amaranth.hdl import Fragment

    import fast_build_env  # noqa: F401  (sets AMARANTH_nextpnr_opts on import)
    from board.cynthion_r1_4 import CynthionPlatformRev1D4

    platform = CynthionPlatformRev1D4()
    fragment = Fragment.get(EclkProbe(sync_mhz=sync_mhz), platform)
    if control:
        break_the_fix(fragment)
    shutil.rmtree(build_dir, ignore_errors=True)
    platform.build(fragment, do_program=False, build_dir=str(build_dir))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit", metavar="DIR",
                        help="read an existing build's artifacts and stop")
    parser.add_argument("--sync-mhz", type=float, default=60.0,
                        help="the shipping SoC's CPU clock")
    parser.add_argument("--control", action="store_true",
                        help="build the pre-fix design -- it MUST fail")
    parser.add_argument("--label", default="",
                        help="subdirectory of tmp/eclk-probe for this build")
    parser.add_argument("--no-build", action="store_true",
                        help="only re-read the artifacts of the last build")
    args = parser.parse_args()

    from devlog import emit

    if args.audit:
        ok, lines = audit(args.audit)
        for line in lines:
            emit(line)
        return 0 if ok else 1

    label = args.label or ("control" if args.control else "current")
    build_dir = OUT / label
    if not args.no_build:
        emit(f"building the ECLK probe at sync = {args.sync_mhz:g} MHz"
             f"{' WITH THE FIX REMOVED' if args.control else ''} "
             f"-> {build_dir.relative_to(ROOT)}")
        build_probe(build_dir, args.sync_mhz, args.control)

    ok, lines = audit(build_dir)
    for line in lines:
        emit(line)
    if args.control:
        # The control's PASS is the interesting failure: it means the check has
        # stopped looking, not that the pre-fix design became correct.
        emit(f"control: {'STILL PASSES -- this check is broken' if ok else 'fails, as it must'}")
        return 1 if ok else 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
