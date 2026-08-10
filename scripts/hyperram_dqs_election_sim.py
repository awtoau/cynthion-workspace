#!/usr/bin/env python3
#
# Can RWDS reach a `fix` transaction's timing on the DQS path? #338.
# SPDX-License-Identifier: BSD-3-Clause

"""Measure the latency the DQS controller ELECTS, against what RWDS did.

    ./scripts/hyperram_dqs_election_sim.py
    ./scripts/hyperram_dqs_election_sim.py --defect   # the control; must FAIL

## The question

Every marginal cell in `results/hyperram/` is `var` and none is `fix` (#338).
Marginality needs a per-transaction degree of freedom. This measures whether
`fix` has one at all.

## What is measured

`hyperram_dqs_controller.py` elects the count at the end of the CA:

    with m.If(extra_latency | self.phy.rwds.i.any() | self.fixed_latency):

`extra_latency` has already ORed `rwds.i.any()` from `SHIFT_COMMAND0`, so the
election is an OR over every RWDS bit sampled across the CA -- and
`| self.fixed_latency` makes that OR a constant when `fixed_latency` is 1.

So: drive `phy.rwds.i` to a chosen pattern over the CA, count the cycles the FSM
spends in `HANDLE_LATENCY`, and turn that into CK. Pure pysim, no device model
and no PHY shim, for the reason `hyperram_dqs_latency_sim.py` gives -- the shim's
pin latencies are unresolved (#186) and would make an end-to-end run
inconclusive. The election arithmetic is what is in question here.

## The checks

1. **Under `fix` the elected wait does not depend on RWDS, at any code.** This is
   the asymmetry the issue asks about, stated as something that can fail.
2. **Under `var` it does depend on RWDS**, at every code where the two branches
   differ -- otherwise the axis is inert and check 1 is vacuous.
3. **Under `var` with RWDS low the wait is a CONSTANT that does not track L.**
   `LOW_LATENCY_CLOCKS` is a class constant with no input on this controller, and
   `hyperram_ceiling_top.py:629` declines to drive one.
4. **One high bit anywhere in the sampled window elects the long count.** `.any()`
   over a 4-bit gearbox word, sampled in two states, is 8 chances to see a 1.

## The control

`--defect` rewrites `| self.fixed_latency` out of the election, imports the
mutant and runs the same checks. Check 1 MUST fail there; if it passes, it is not
measuring the gate and this file is worthless. The run exits non-zero unless the
defect is caught.

Log: `tmp/logs/hyperram-dqs-election-sim.log`.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

from amaranth.sim import Simulator  # noqa: E402

from hyperram.hyperram_ceiling_top import (  # noqa: E402
    CR0_POWER_ON_LATENCY_CODE, LATENCY_CLOCKS_BY_CODE)

CONTROLLER = ROOT / "gateware" / "soc" / "peripherals" / "hyperram_dqs_controller.py"
LOG = ROOT / "tmp" / "logs" / "hyperram-dqs-election-sim.log"

# One controller cycle in CK, 4:1 gearing. Same constant, same reason, as
# `hyperram_dqs_latency_sim.py`.
CK_PER_CYCLE = 2

# The election, and the mutation that removes the gate `--defect` exists to prove
# is load-bearing. Matched as source text so the check breaks loudly if the line
# is reworded rather than passing against a line it no longer describes.
ELECTION = "with m.If(extra_latency | self.phy.rwds.i.any()\n" \
           "                              | self.fixed_latency):"
ELECTION_DEFECT = "with m.If(extra_latency | self.phy.rwds.i.any()):"

# Cycles to reach and leave HANDLE_LATENCY. IDLE -> CS_SETUP -> SHIFT_COMMAND0 ->
# SHIFT_COMMAND1 -> HANDLE_LATENCY is 4, then at most `max_latency_clocks + 1`.
# With max 14 that is 19; 4x margin. On expiry the row reports the count it
# reached and the check fails on the mismatch rather than the run hanging.
SETTLE_CYCLES = 80

log = logging.getLogger("hyperram-dqs-election-sim")


def setup_logging(verbose=False):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG)])


def load_controller(defect=False):
    """Import `HyperRAMDQSController`, optionally with the gate removed.

    The mutant goes into a package of its own because the module's one relative
    import (`from . import hyperram_controller`) needs a parent -- and a package
    keeps the mutant off `peripherals`, so the good controller in the same run is
    the file as committed.
    """
    if not defect:
        from peripherals.hyperram_dqs_controller import HyperRAMDQSController
        return HyperRAMDQSController

    source = CONTROLLER.read_text()
    if ELECTION not in source:
        raise SystemExit(
            f"the election line this control mutates is not in {CONTROLLER.name} "
            "any more -- the defect cannot be injected, so nothing below is a "
            "control. Re-read the election and update ELECTION.")

    package = ROOT / "tmp" / "hyperram_election_defect" / "peripherals_defect"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "hyperram_controller.py").write_text(
        (CONTROLLER.parent / "hyperram_controller.py").read_text())
    (package / "hyperram_dqs_controller.py").write_text(
        source.replace(ELECTION, ELECTION_DEFECT))

    sys.path.insert(0, str(package.parent))
    spec = importlib.util.find_spec("peripherals_defect.hyperram_dqs_controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.HyperRAMDQSController


def latency_ck(code):
    """The L the DQS build drives for this code (`hyperram_ceiling_top.py:184`)."""
    return LATENCY_CLOCKS_BY_CODE.get(
        code, LATENCY_CLOCKS_BY_CODE[CR0_POWER_ON_LATENCY_CODE])


def elected_ck(controller_cls, latency_clocks, fixed, rwds, log_row=False):
    """CK the controller waits, given what RWDS did over the CA.

    `rwds` is the value driven onto `phy.rwds.i` for the whole command phase, so
    a single set bit is the `.any()` case the election is sensitive to.
    """
    from luna.gateware.interface.psram import HyperBusDQSPHY

    phy = HyperBusDQSPHY()
    dut = controller_cls(phy=phy, sync_mhz=50.0, max_latency_clocks=14)
    handle_latency = controller_cls.STATES.index("HANDLE_LATENCY")
    cycles = []

    async def stimulus(ctx):
        ctx.set(dut.latency_clocks, latency_clocks)
        ctx.set(dut.fixed_latency, fixed)
        ctx.set(dut.perform_write, 0)
        ctx.set(dut.register_space, 0)
        ctx.set(dut.address, 0x100)
        ctx.set(dut.final_word, 1)
        ctx.set(dut.phy.rwds.i, rwds)
        ctx.set(dut.start_transfer, 1)
        await ctx.tick()
        ctx.set(dut.start_transfer, 0)
        entered = False
        for _ in range(SETTLE_CYCLES):
            if ctx.get(dut.state) == handle_latency:
                entered = True
                cycles.append(1)
            elif entered:
                break
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(stimulus)
    sim.run()
    if not cycles:
        raise SystemExit(
            f"HANDLE_LATENCY never entered in {SETTLE_CYCLES} cycles "
            f"(latency_clocks={latency_clocks}, fixed={fixed}, rwds={rwds:#x}) "
            "-- the stimulus no longer starts a read")
    return len(cycles) * CK_PER_CYCLE


def rwds_width(controller_cls):
    from luna.gateware.interface.psram import HyperBusDQSPHY
    return len(HyperBusDQSPHY().rwds.i)


def run_checks(controller_cls, label):
    """The four checks. Returns the list of failures."""
    failures = []
    width = rwds_width(controller_cls)
    all_high = (1 << width) - 1
    short_ck = (controller_cls.LOW_LATENCY_CLOCKS + 1) * CK_PER_CYCLE

    log.info("\n=== %s ===", label)
    log.info("  rwds.i is %d bits; LOW_LATENCY_CLOCKS = %d, so the short wait is "
             "%d CK", width, controller_cls.LOW_LATENCY_CLOCKS, short_ck)
    log.info("\n  %-5s %-3s %s", "code", "L", "elected CK   fix/low fix/high "
             "var/low var/high")

    var_moved, short_waits = 0, set()
    for code in sorted(LATENCY_CLOCKS_BY_CODE) + [7]:
        L = latency_ck(code)
        n = L - 1                       # `hyperram_ceiling_top.py:625`
        got = {(fixed, rwds): elected_ck(controller_cls, n, fixed, rwds)
               for fixed in (1, 0) for rwds in (0, all_high)}
        log.info("  %-5s %-3s %14d %8d %8d %9d", code, L,
                 got[(1, 0)], got[(1, all_high)], got[(0, 0)], got[(0, all_high)])

        # 1. RWDS must not reach a `fix` transaction.
        if got[(1, 0)] != got[(1, all_high)]:
            failures.append(
                f"code {code}: under `fix`, RWDS CHANGED the wait "
                f"{got[(1, 0)]} -> {got[(1, all_high)]} CK")
        # 2. ...and must reach a `var` one, or check 1 is vacuous.
        if got[(0, 0)] != got[(0, all_high)]:
            var_moved += 1
        short_waits.add(got[(0, 0)])

    if not var_moved:
        failures.append(
            "under `var`, RWDS changed the wait at NO code -- the stimulus is "
            "not reaching the election, so check 1 measured nothing")
    else:
        log.info("\n  RWDS moved the `var` wait at %d of %d codes",
                 var_moved, len(LATENCY_CLOCKS_BY_CODE) + 1)

    # 3. The short branch is a constant that tracks no latency code.
    if len(short_waits) == 1:
        log.info("  `var` with RWDS low waits %d CK at EVERY code -- the short "
                 "branch is a constant and does not track L", short_waits.pop())
    else:
        failures.append(f"the short branch varied: {sorted(short_waits)} CK")

    # 4. `.any()`: one bit anywhere in the window is the whole answer.
    n = latency_ck(0) - 1
    lone = [bit for bit in range(width)
            if elected_ck(controller_cls, n, 0, 1 << bit) != short_ck]
    log.info("  one high bit elects the long count from %d of %d rwds.i bits: %s",
             len(lone), width, lone)
    if len(lone) != width:
        failures.append(
            f"only {len(lone)} of {width} rwds.i bits elect the long count, so "
            "`.any()` is not the sensitivity it looks like")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--defect", action="store_true",
                        help="remove `| self.fixed_latency` from the election "
                             "and REQUIRE check 1 to fail")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    good = run_checks(load_controller(False), "the controller as committed")
    for line in good:
        log.info("  FAIL %s", line)

    defect = run_checks(load_controller(True),
                        "CONTROL: `| self.fixed_latency` removed")
    caught = [line for line in defect if "under `fix`" in line]
    log.info("\n  the defect produced %d failure(s), %d of them on `fix`",
             len(defect), len(caught))

    if good:
        log.info("\nFAIL -- the committed controller failed %d check(s)", len(good))
        return 1
    if not caught:
        log.info("\nFAIL -- removing the gate changed NOTHING, so check 1 cannot "
                 "fail and proves nothing about `fix`")
        return 1
    log.info("\nPASS -- RWDS cannot reach a `fix` transaction's timing, and the "
             "control shows the check fails when the gate is removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
