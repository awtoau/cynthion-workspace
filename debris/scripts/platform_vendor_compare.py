#!/usr/bin/env python3
#
# Compare a design elaborated against the upstream cynthion platform with the
# same design elaborated against the locally vendored one.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves the vendored r1.4 platform is pin-for-pin identical to the upstream one.

Vendoring a board definition is a copy, and a copy is exactly the kind of change
that looks correct in review and is wrong on the bench. A pin map that differs
from the board is worse than no change: the build succeeds, the bitstream loads,
and the wrong ball drives the wrong net.

So this does not diff the source. It runs the real place-and-route both ways and
compares what nextpnr actually decided:

  * device utilisation (LUTs, FFs, BRAM, DSP, IO, PLL) -- catches a resource
    that silently changed shape
  * the full pin assignment, signal name to ball -- catches a transposed or
    dropped pin, which utilisation alone would not

Both runs use the same design source and the same toolchain; only the platform
class differs. Identical output on both counts is the evidence.

    ./scripts/platform_vendor_compare.py                 # default designs
    ./scripts/platform_vendor_compare.py --design blinky

Builds land in tmp/platform_vendor/<design>/<side>/ -- never in tmp/vexii_hello,
which another investigation owns.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = ROOT / "tmp" / "platform_vendor"
LOGS = ROOT / "tmp" / "logs"

UPSTREAM = "from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4 as Plat"
VENDORED = "from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4 as Plat"


# Designs exercised. Each is a self-contained elaboratable defined as source
# text so the identical string is fed to both sides -- no chance of the two
# runs drifting apart through an import.
DESIGNS = {
    # Touches LEDs, the button and the 60MHz clock: the smallest design that
    # still proves clock, input and output pins all landed.
    "blinky": """
from amaranth import *

class Design(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        leds = [platform.request("led", i) for i in range(6)]
        btn  = platform.request("button_user")
        ctr  = Signal(28)
        m.d.sync += ctr.eq(ctr + 1)
        for i, led in enumerate(leds):
            m.d.comb += led.o.eq(ctr[20 + i] ^ btn.i)
        return m
""",
    # Wide, multi-bank design: both ULPI PHYs, HyperRAM, QSPI flash, the
    # sideband pins and both Type-C controllers. This is the one that would
    # catch a mistake in the bulk of the resource list, where the pin count is
    # large and a transposition is easy to miss by eye.
    "wide": """
from amaranth import *

class Design(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        ctr = Signal(24)
        m.d.sync += ctr.eq(ctr + 1)

        ulpi_c = platform.request("control_phy")
        ulpi_t = platform.request("target_phy")
        ram    = platform.request("ram")
        qspi   = platform.request("qspi_flash")
        uart   = platform.request("uart")
        adv    = platform.request("int")
        tc     = platform.request("target_type_c")

        # Drive every output from the counter and fold every input back into
        # it, so nothing is optimised away as unconnected.
        m.d.comb += [
            ulpi_c.stp.o.eq(ctr[0]),
            ulpi_c.rst.o.eq(ctr[1]),
            ulpi_c.clk.o.eq(ClockSignal("sync")),
            ulpi_c.data.o.eq(ctr[:8]),
            ulpi_c.data.oe.eq(ctr[9]),
            ulpi_t.stp.o.eq(ctr[2]),
            ulpi_t.rst.o.eq(ctr[3]),
            ulpi_t.clk.o.eq(ClockSignal("sync")),
            ulpi_t.data.o.eq(ctr[4:12]),
            ulpi_t.data.oe.eq(ctr[13]),
            ram.clk.o.eq(ctr[0]),
            ram.cs.o.eq(ctr[1]),
            ram.reset.o.eq(ctr[2]),
            ram.dq.o.eq(ctr[:8]),
            ram.dq.oe.eq(ctr[3]),
            ram.rwds.o.eq(ctr[4]),
            ram.rwds.oe.eq(ctr[5]),
            qspi.cs.o.eq(ctr[6]),
            qspi.dq.o.eq(ctr[:4]),
            qspi.dq.oe.eq(ctr[7]),
            uart.tx.o.eq(ctr[8]),
            uart.tx.oe.eq(1),
            adv.o.eq(ctr[9]),
            adv.oe.eq(ctr[10]),
            tc.scl.o.eq(ctr[11]),
            tc.sda.o.eq(ctr[12]),
            tc.sda.oe.eq(ctr[13]),
            tc.sbu1.o.eq(ctr[14]),
            tc.sbu1.oe.eq(ctr[15]),
            tc.sbu2.o.eq(ctr[16]),
            tc.sbu2.oe.eq(ctr[17]),
        ]
        acc = Signal(8)
        m.d.sync += acc.eq(acc ^ ulpi_c.data.i ^ ulpi_t.data.i ^ ram.dq.i
                           ^ Cat(qspi.dq.i, uart.rx.i, adv.i, tc.int.i, tc.fault.i))
        led = platform.request("led", 0)
        m.d.comb += led.o.eq(acc.any())
        return m
""",
}


def build_script(design_src, platform_import, outdir):
    """Source for one elaboration run."""
    return f"""
import sys
sys.path.insert(0, {str(ROOT / "ecp5-test")!r})
{platform_import}
{design_src}
Plat().build(Design(), do_program=False, build_dir={str(outdir)!r})
"""


def run_side(design, side, platform_import, logger):
    outdir = BUILD_ROOT / design / side
    outdir.mkdir(parents=True, exist_ok=True)
    script = outdir / "build.py"
    script.write_text(build_script(DESIGNS[design], platform_import, outdir))

    logger.info("elaborating %s (%s)", design, side)
    # The OSS CAD Suite environment has to be sourced for yosys/nextpnr; it is
    # not on PATH by default.
    cmd = (f'source "$HOME/opt/oss-cad-suite/environment" && '
           f'{sys.executable} {script}')
    proc = subprocess.run(["bash", "-c", cmd], cwd=ROOT,
                          capture_output=True, text=True)
    (outdir / "build.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        logger.error("%s/%s FAILED (rc=%d); see %s",
                     design, side, proc.returncode, outdir / "build.log")
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        for line in tail:
            logger.error("  | %s", line)
        return None
    return outdir


# nextpnr prints utilisation as e.g. "Info: \t   TRELLIS_IO:   105/  197  53%"
UTIL_RE = re.compile(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+\d+%", re.M)


def utilisation(outdir):
    """Parse the resource counts nextpnr reported.

    nextpnr writes this to its own stderr, which amaranth routes into the
    timing report rather than back to us, so the report is the source -- not
    build.log, which only carries what the build script itself printed.
    """
    tim = outdir / "top.tim"
    if not tim.exists():
        return {}
    log = tim.read_text()
    # Only the "Device utilisation" block, not the per-stage chatter.
    start = log.find("Device utilisation")
    if start < 0:
        return {}
    block = log[start:start + 2000]
    return {name: int(used) for name, used, _ in UTIL_RE.findall(block)}


def pin_assignment(outdir):
    """Signal-name to ball, from the constraint file handed to nextpnr."""
    lpf = list(outdir.glob("*.lpf"))
    if not lpf:
        return {}
    pins = {}
    for line in lpf[0].read_text().splitlines():
        # LOCATE COMP "name" SITE "ball";
        m = re.match(r'\s*LOCATE\s+COMP\s+"([^"]+)"\s+SITE\s+"([^"]+)"', line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def compare(design, logger):
    up = run_side(design, "upstream", UPSTREAM, logger)
    vd = run_side(design, "vendored", VENDORED, logger)
    if up is None or vd is None:
        return False

    ok = True

    u_util, v_util = utilisation(up), utilisation(vd)
    if not u_util:
        logger.error("%s: could not parse utilisation from the upstream build",
                     design)
        ok = False
    elif u_util == v_util:
        logger.info("%s: utilisation identical -- %s", design,
                    ", ".join(f"{k}={v}" for k, v in sorted(u_util.items())))
    else:
        ok = False
        logger.error("%s: UTILISATION DIFFERS", design)
        for key in sorted(set(u_util) | set(v_util)):
            a, b = u_util.get(key), v_util.get(key)
            if a != b:
                logger.error("  %-20s upstream=%s vendored=%s", key, a, b)

    u_pins, v_pins = pin_assignment(up), pin_assignment(vd)
    if not u_pins:
        logger.error("%s: no pin assignments parsed from the upstream .lpf",
                     design)
        ok = False
    elif u_pins == v_pins:
        logger.info("%s: pin assignment identical -- %d pins located",
                    design, len(u_pins))
    else:
        ok = False
        logger.error("%s: PIN ASSIGNMENT DIFFERS", design)
        for key in sorted(set(u_pins) | set(v_pins)):
            a, b = u_pins.get(key), v_pins.get(key)
            if a != b:
                logger.error("  %-40s upstream=%s vendored=%s", key, a, b)

    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", action="append", choices=sorted(DESIGNS),
                    help="design to compare (repeatable; default: all)")
    args = ap.parse_args()
    designs = args.design or sorted(DESIGNS)

    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("platform_vendor_compare")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOGS / "platform_vendor_compare.log", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)

    results = {d: compare(d, logger) for d in designs}
    logger.info("---")
    for design, ok in results.items():
        logger.info("%-10s %s", design, "IDENTICAL" if ok else "DIFFERS")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
