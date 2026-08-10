#!/usr/bin/env python3
#
# CK parks LOW when the clock gate closes -- as a checked property, not a
# property held by construction.
# SPDX-License-Identifier: BSD-3-Clause

"""Does CK park Low, and does anything notice if it stops?

Two vendor documents carry two DIFFERENT rules for stopping the clock, and
**neither carries both** ([#340](https://github.com/awtoau/cynthion-workspace/issues/340)):

  * datasheet 10.2.2 -- *"recommended to stop the clock when it is in Low state"*
  * app note 7.2.2 -- *"recommended not to stop the clock during register access"*

The app note REPLACES the sentence rather than adding to it, so an audit from
either document alone is working from half the requirement.

**Park-low is satisfied today by construction and nothing tests it.** Both PHYs
gear CK out of an ODDR whose data inputs are `clk_en` and constant zeros, so
`clk_en = 0` emits no pulse and leaves CK Low. That is a property one refactor
from being lost -- an `i_D0=1`, or a CK taken from a free-running divider, would
park it High and every existing check would still pass.

How the CK gearing is FOUND rather than named: every ODDR in the PHY is probed
with `clk_en` driven all-ones, and the one whose data inputs respond is the
clock's. CS# gears through an identical primitive and does not respond, so the
search cannot pick it up by accident, and a CK path that stopped following
`clk_en` at all leaves nothing to find -- which fails.

Then, on that instance:

  1. `clk_en` all ones -> the data inputs are NOT all zero (the gate is wired at
     all; without this a dangling net would pass check 2 trivially).
  2. `clk_en` zero -> every data input is zero, so CK parks **Low**.

`--mutate` is the negative control: it parks the found instance's first phase
High and REQUIRES the run to fail. A check that cannot fail proves nothing.

Usage:

    scripts/hyperram_ck_park_check.py
    scripts/hyperram_ck_park_check.py --mutate    # must exit non-zero

Log: `tmp/logs/hyperram_ck_park_check.log`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from amaranth import Cat, Const, Module, Signal                 # noqa: E402
from amaranth.hdl import Fragment, IOPort                        # noqa: E402
from amaranth.hdl._ir import Instance                            # noqa: E402
from amaranth.lib.io import DifferentialPort, SingleEndedPort    # noqa: E402
from amaranth.sim import Simulator                               # noqa: E402
from luna.gateware.interface.psram import HyperRAMPHY           # noqa: E402

from peripherals.hyperram_dqs_phy import HyperRAMDQSPHY         # noqa: E402

LOGFILE = ROOT / "tmp" / "logs" / "hyperram_ck_park_check.log"

# The output-gearing primitives either PHY can drive CK from. `ODDRX1F` is 2:1
# (luna's non-DQS PHY), `ODDRX2F` 4:1 (the DQS PHY at 2 CK per `sync` cycle).
ODDR_TYPES = ("ODDRX1F", "ODDRX2F", "ODDRX2DQA", "ODDRX2DQSB")

# The gearing data ports, in phase order. The LAST one a primitive has is the
# one that decides the parked level, which is why the order matters.
DATA_PORTS = ("D0", "D1", "D2", "D3")

log = logging.getLogger("hyperram-ck-park")


class FakePad:
    """One pad, with only the attributes a PHY touches on it."""

    def __init__(self, name, width=1, *, i=False, oe=False, io=False, p=False):
        self.o = Signal(width, name=f"{name}_o")
        if i:
            self.i = Signal(width, name=f"{name}_i")
        if oe:
            self.oe = Signal(name=f"{name}_oe")
        if io:
            self.io = Signal(width, name=f"{name}_io")
        if p:
            self.p = Signal(width, name=f"{name}_p")
            self.n = Signal(width, name=f"{name}_n")


class Bus:
    """`platform.request("ram")` reduced to the pads a PHY drives."""


def non_dqs_phy():
    bus = Bus()
    bus.cs = FakePad("cs")
    bus.clk = FakePad("clk")
    bus.rwds = FakePad("rwds", i=True, oe=True)
    bus.dq = FakePad("dq", 8, i=True, oe=True)
    return HyperRAMPHY(bus=bus)


def dqs_phy():
    """The DQS PHY drives RAW PADS, so its bus is `lib.io` ports and not signals.

    Same shape `platform.request("ram", 0, dir="-")` hands it: `clk` differential,
    the rest single-ended.
    """
    bus = Bus()
    bus.cs = SingleEndedPort(IOPort(1, name="cs"))
    bus.clk = DifferentialPort(p=IOPort(1, name="clk_p"),
                               n=IOPort(1, name="clk_n"))
    bus.reset = SingleEndedPort(IOPort(1, name="reset"))
    bus.rwds = SingleEndedPort(IOPort(1, name="rwds"))
    bus.dq = SingleEndedPort(IOPort(8, name="dq"))
    return HyperRAMDQSPHY(bus=bus)


PHYS = (("non-DQS (luna's HyperRAMPHY, 2:1)", non_dqs_phy),
        ("DQS (HyperRAMDQSPHY, 4:1)", dqs_phy))


def oddr_instances(fragment):
    """Every output-gearing primitive in the design, at any depth."""
    found = []
    if isinstance(fragment, Instance) and fragment.type in ODDR_TYPES:
        found.append(fragment)
    for sub, *_ in fragment.subfragments:
        found += oddr_instances(sub)
    return found


def data_ports(instance):
    """The gearing data inputs this primitive has, in phase order."""
    return [(name, instance.ports[name][0])
            for name in DATA_PORTS if name in instance.ports]


def sample(ports, clk_en, *, mutate=False):
    """The gearing data inputs, evaluated with `clk_en` driven to `clk_en`.

    Only the port EXPRESSIONS go into the probe module, so anything the PHY
    computes elsewhere is absent and reads as zero -- which is why the all-ones
    pass has to be run as well, and why a dangling CK cannot pass.
    """
    values = [value for _, value in ports]
    if mutate:
        # The negative control: phase 0 parked High. Everything downstream of the
        # search is unchanged, so a failure here is the CHECK working.
        values = [Const(1, 1)] + values[1:]

    m = Module()
    probe = Signal(len(values))
    m.d.comb += probe.eq(Cat(*(v.any() for v in values)))
    result = {}

    async def testbench(ctx):
        ctx.set(clk_en.signal, clk_en.value)
        await ctx.delay(0)
        result["probe"] = ctx.get(probe)

    # No clock: the probe is combinational, and adding one would need a `sync`
    # domain this module does not have.
    sim = Simulator(m)
    sim.add_testbench(testbench)
    sim.run()
    return result["probe"]


class Driven:
    def __init__(self, signal, value):
        self.signal, self.value = signal, value


def check_phy(name, build, *, mutate) -> list[str]:
    """The CK gearing of one PHY, found and then judged. Returns failures."""
    failures = []
    phy = build()
    fragment = Fragment.get(phy, None)
    instances = oddr_instances(fragment)
    log.info("  %s: %d output-gearing primitive(s)", name, len(instances))
    if not instances:
        return [f"{name}: no output gearing found at all -- this check is "
                f"looking at the wrong thing"]

    clk_en = phy.phy.clk_en
    ones = (1 << len(clk_en)) - 1

    # FIND the clock's gearing: the one whose data inputs move with `clk_en`.
    live = []
    for instance in instances:
        ports = data_ports(instance)
        if not ports:
            continue
        if sample(ports, Driven(clk_en, ones)) != 0:
            live.append((instance, ports))

    if len(live) != 1:
        return [f"{name}: {len(live)} of {len(instances)} gearing primitives "
                f"follow `clk_en`; CK must be driven by exactly one, and a CK "
                f"that no longer follows the gate is the fault being looked for"]

    instance, ports = live[0]
    phases = ", ".join(n for n, _ in ports)
    log.info("    CK gears through %s (%s), %d-bit clk_en",
             instance.type, phases, len(clk_en))

    gated = sample(ports, Driven(clk_en, 0), mutate=mutate)
    open_ = sample(ports, Driven(clk_en, ones), mutate=mutate)

    if open_ == 0:
        failures.append(f"{name}: the gate open drives no phase -- CK never "
                        f"toggles, so 'parks Low' would be true and useless")
    else:
        log.info("    PASS  the gate open drives phases 0b%s",
                 format(open_, f"0{len(ports)}b"))

    if gated != 0:
        failures.append(f"{name}: `clk_en = 0` leaves phases 0b"
                        f"{format(gated, f'0{len(ports)}b')} driven, so CK parks "
                        f"HIGH -- datasheet 10.2.2 wants it stopped Low")
    else:
        log.info("    PASS  `clk_en = 0` drives every phase Low, so CK parks Low")
    return failures


def setup_logging(verbose):
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"),
                  logging.StreamHandler(sys.stdout)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mutate", action="store_true",
                    help="park CK's first phase High and REQUIRE a failure")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    if args.mutate:
        log.info("NEGATIVE CONTROL: CK phase 0 parked High. A pass here means "
                 "this check cannot see a CK that stops in the wrong state.")

    failures = []
    for name, build in PHYS:
        failures += check_phy(name, build, mutate=args.mutate)

    if args.mutate:
        if failures:
            log.info("PASS (negative control) -- %d failure(s) seen:", len(failures))
            for f in failures:
                log.info("  %s", f)
            return 0
        log.error("FAIL (negative control) -- CK parked High went unnoticed")
        return 1

    if failures:
        for f in failures:
            log.error("FAIL %s", f)
        return 1
    log.info("PASS -- CK parks Low on both PHYs when the gate closes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
