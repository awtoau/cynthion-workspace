#!/usr/bin/env python3
#
# Can RWDS reach a `fix` transaction's timing on the DQS path? #338.
# SPDX-License-Identifier: BSD-3-Clause

"""Measure the latency the DQS controller ELECTS, against what RWDS did.

    ./scripts/hyperram_dqs_election_sim.py
    ./scripts/hyperram_dqs_election_sim.py --controls gate,short,window

## The question

Every marginal cell in `results/hyperram/` is `var` and none is `fix` (#338).
Marginality needs a per-transaction degree of freedom. This measures whether
`fix` has one at all, what the short branch waits, and WHICH fabric cycles the
election is sensitive to.

## What is measured

Drive `phy.rwds.i`, count the cycles the FSM spends in `HANDLE_LATENCY`, turn
that into CK. Pure pysim, no device model and no PHY shim, for the reason
`hyperram_dqs_latency_sim.py` gives -- the shim's pin latencies are unresolved
(#186) and would make an end-to-end run inconclusive. The election arithmetic is
what is in question here; `hyperram_dqs_model_sim.py --stage config` judges the
same two defects end to end against the device model.

## The checks

1. **Under `fix` the elected wait does not depend on RWDS, at any code.** This is
   the asymmetry #381 asks about, stated as something that can fail.
2. **Under `var` it does depend on RWDS**, at every code where the two branches
   differ -- otherwise the axis is inert and check 1 is vacuous.
3. **Under `var` with RWDS low the wait TRACKS L.** #380: `LOW_LATENCY_CLOCKS`
   was a class constant with no input, so the short branch waited 8 CK at every
   code and no code was right. Judged against `low_latency_clocks()`, the
   controller's own rounding, so there is one definition of it.
4. **One high bit anywhere in the sampled window elects the long count.** `.any()`
   over a 4-bit gearbox word is four chances to see a 1.
5. **The election is sensitive to exactly ONE fabric cycle, and it is the one the
   controller declares.** Measured by driving RWDS high in a single cycle at a
   time. #381: sampled in `SHIFT_COMMAND0`/`SHIFT_COMMAND1` it reads the pin
   cycles BEFORE the CA -- a bus that is deselected at any round trip of one
   cycle or more.

## The controls

`--controls` rewrites a line of the controller out and re-runs every check
against the mutant, requiring the named check to fail:

    gate    the `fixed_latency` gate off the sample     -> check 1 must fail
    deaf    the sample tied low                         -> check 2 must fail
    short   the short branch back to the class constant -> check 3 must fail
    anybit  the RWDS word read one bit deep             -> check 4 must fail
    window  the sample back into the CA states          -> check 5 must fail

If a mutation changes nothing, the check it belongs to cannot fail and proves
nothing; the run exits non-zero.

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

# The mutations, as SOURCE TEXT, so a control breaks loudly when the line it
# describes is reworded rather than passing against a line it no longer names.
# `check` is the check number the mutant is REQUIRED to fail; `edits` lists the
# rewrites to try in order, so one control spans the fix that changed the line.
ELECTION_BEFORE = ("with m.If(extra_latency | self.phy.rwds.i.any()\n"
                   "                              | self.fixed_latency):")
RWDS_ASKS = ("rwds_asks.eq(sample_now & ~self.fixed_latency\n"
             "                         & self.phy.rwds.i.any()),")
MUTATIONS = {
    "gate": {
        "check": 1,
        "why": "the `fixed_latency` gate off the sample -- RWDS can reach `fix`",
        "edits": [(RWDS_ASKS,
                   "rwds_asks.eq(sample_now & self.phy.rwds.i.any()),"),
                  (ELECTION_BEFORE,
                   "with m.If(extra_latency | self.phy.rwds.i.any()):")],
    },
    "deaf": {
        "check": 2,
        "why": "the sample tied low -- the election hears nothing",
        "edits": [(RWDS_ASKS, "rwds_asks.eq(0),"),
                  ("with m.If(extra_latency | self.phy.rwds.i.any()\n"
                   "                              | self.fixed_latency):",
                   "with m.If(extra_latency | self.phy.rwds.i.any() | 1):")],
    },
    "short": {
        "check": 3,
        "why": "the short branch back to the class constant (#380)",
        "edits": [("latency_clocks_remaining.eq(self.low_latency_clocks)",
                   "latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS)")],
    },
    "anybit": {
        "check": 4,
        "why": "the RWDS word read one bit deep instead of `.any()`",
        "edits": [("self.phy.rwds.i.any()", "self.phy.rwds.i[0]")],
    },
    "window": {
        "check": 5,
        "why": "the RWDS sample back into the CA states (#381)",
        "edits": [("sample_now.eq(xact_age == self._rwds_sample_cycle),",
                   "sample_now.eq((xact_age == 2) | (xact_age == 3)),")],
    },
}

# Cycles to reach and leave HANDLE_LATENCY. IDLE -> CS_SETUP -> SHIFT_COMMAND0 ->
# SHIFT_COMMAND1 -> HANDLE_LATENCY is 4, then at most `max_latency_clocks + 1`.
# With max 14 that is 19; 4x margin. On expiry the row reports the count it
# reached and the check fails on the mismatch rather than the run hanging.
SETTLE_CYCLES = 80

# Fabric cycles searched for the election's sensitivity window. The CA ends at
# cycle 3 and the shortest short count exits HANDLE_LATENCY at cycle 4, so
# everything that can matter is inside the first eight; 2x that bound.
WINDOW_CYCLES = 16

log = logging.getLogger("hyperram-dqs-election-sim")


def setup_logging(verbose=False):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG)])


def load_controller(mutation=None):
    """Import `HyperRAMDQSController`, optionally with one line rewritten.

    The mutant goes into a package of its own because the module's one relative
    import (`from . import hyperram_controller`) needs a parent -- and a package
    keeps the mutant off `peripherals`, so the good controller in the same run is
    the file as committed.
    """
    if mutation is None:
        from peripherals.hyperram_dqs_controller import HyperRAMDQSController
        return HyperRAMDQSController

    source = CONTROLLER.read_text()
    mutant = next((source.replace(old, new)
                   for old, new in MUTATIONS[mutation]["edits"]
                   if old in source), None)
    if mutant is None:
        return None

    package = (ROOT / "tmp" / "hyperram_election_defect" /
               f"peripherals_{mutation}")
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "hyperram_controller.py").write_text(
        (CONTROLLER.parent / "hyperram_controller.py").read_text())
    (package / "hyperram_dqs_controller.py").write_text(mutant)

    sys.path.insert(0, str(package.parent))
    name = f"peripherals_{mutation}.hyperram_dqs_controller"
    spec = importlib.util.find_spec(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.HyperRAMDQSController


def latency_ck(code):
    """The L the DQS build drives for this code (`hyperram_ceiling_top.py:184`)."""
    return LATENCY_CLOCKS_BY_CODE.get(
        code, LATENCY_CLOCKS_BY_CODE[CR0_POWER_ON_LATENCY_CODE])


def elected_ck(controller_cls, latency_clocks, fixed, rwds, low=None,
               only_cycle=None):
    """CK the controller waits, given what RWDS did.

    `rwds` is driven onto `phy.rwds.i` for the whole transaction, so a single set
    bit is the `.any()` case the election is sensitive to. `only_cycle` drives it
    in that ONE fabric cycle and zero everywhere else, which is what turns this
    into a sensitivity measurement rather than a level test. Cycle 0 is the one
    `start_transfer` is seen in.
    """
    from luna.gateware.interface.psram import HyperBusDQSPHY

    phy = HyperBusDQSPHY()
    dut = controller_cls(phy=phy, sync_mhz=50.0, max_latency_clocks=14)
    handle_latency = controller_cls.STATES.index("HANDLE_LATENCY")
    cycles = []

    def rwds_at(cycle):
        if only_cycle is None:
            return rwds
        return rwds if cycle == only_cycle else 0

    async def stimulus(ctx):
        ctx.set(dut.latency_clocks, latency_clocks)
        ctx.set(dut.fixed_latency, fixed)
        if low is not None and hasattr(dut, "low_latency_clocks"):
            ctx.set(dut.low_latency_clocks, low)
        ctx.set(dut.perform_write, 0)
        ctx.set(dut.register_space, 0)
        ctx.set(dut.address, 0x100)
        ctx.set(dut.final_word, 1)
        ctx.set(dut.phy.rwds.i, rwds_at(0))
        ctx.set(dut.start_transfer, 1)
        await ctx.tick()
        ctx.set(dut.start_transfer, 0)
        entered = False
        for step in range(SETTLE_CYCLES):
            ctx.set(dut.phy.rwds.i, rwds_at(1 + step))
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


def sensitivity_window(controller_cls, latency_clocks, all_high):
    """Fabric cycles at which a LONE RWDS high changes the elected wait.

    One cycle high and the rest low, one cycle at a time, so the answer is the
    controller's real sensitivity rather than what its states are called.
    """
    base = elected_ck(controller_cls, latency_clocks, 0, 0)
    return {c for c in range(WINDOW_CYCLES)
            if elected_ck(controller_cls, latency_clocks, 0, all_high,
                          only_cycle=c) != base}


def rwds_width(controller_cls):
    from luna.gateware.interface.psram import HyperBusDQSPHY
    return len(HyperBusDQSPHY().rwds.i)


def short_clocks(controller_cls, L):
    """`low_latency_clocks` for this L: the controller's own rounding, or its
    class constant on a controller that has no input (#380)."""
    module = sys.modules[controller_cls.__module__]
    if hasattr(module, "low_latency_clocks"):
        return module.low_latency_clocks(L)
    return controller_cls.LOW_LATENCY_CLOCKS


def want_short_ck(L):
    """The short wait this rig REQUIRES, in CK, derived from L and nothing else.

    A device that declines the extra latency serves after L CK. `HANDLE_LATENCY`
    waits `2 x n + 2`, so only even counts exist and an odd L cannot be met: the
    requirement is the largest expressible wait that does not EXCEED L, because
    a wait past the first word loses it and is #381's `128 - L` shape.
    """
    return 2 * (L // 2)


def declared_sample_cycle(controller_cls):
    """The fabric cycle the controller says it samples RWDS in, or None."""
    module = sys.modules[controller_cls.__module__]
    fn = getattr(module, "rwds_sample_cycle", None)
    return fn() if fn else None


def run_checks(controller_cls, label):
    """The five checks. Returns [(check number, what failed), ...]."""
    failures = []
    width = rwds_width(controller_cls)
    all_high = (1 << width) - 1

    log.info("\n=== %s ===", label)
    log.info("  rwds.i is %d bits", width)
    log.info("\n  %-5s %-3s %s", "code", "L", "elected CK   fix/low fix/high "
             "var/low var/high   want short")

    var_moved = 0
    for code in sorted(LATENCY_CLOCKS_BY_CODE) + [7]:
        L = latency_ck(code)
        n = L - 1                       # `hyperram_ceiling_top.py:625`
        low = short_clocks(controller_cls, L)
        want_short = want_short_ck(L)
        got = {(fixed, rwds): elected_ck(controller_cls, n, fixed, rwds, low=low)
               for fixed in (1, 0) for rwds in (0, all_high)}
        log.info("  %-5s %-3s %14d %8d %8d %9d %11d", code, L,
                 got[(1, 0)], got[(1, all_high)], got[(0, 0)], got[(0, all_high)],
                 want_short)

        # 1. RWDS must not reach a `fix` transaction.
        if got[(1, 0)] != got[(1, all_high)]:
            failures.append((1, f"code {code}: under `fix`, RWDS CHANGED the "
                                f"wait {got[(1, 0)]} -> {got[(1, all_high)]} CK"))
        # 2. ...and must reach a `var` one, or check 1 is vacuous.
        if got[(0, 0)] != got[(0, all_high)]:
            var_moved += 1
        # 3. The short branch tracks L. (#380)
        if got[(0, 0)] != want_short:
            failures.append((3, f"code {code} (L={L}): the short branch waited "
                                f"{got[(0, 0)]} CK, not the {want_short} CK "
                                f"`low_latency_clocks={low}` asks for"))

    if not var_moved:
        failures.append((2, "under `var`, RWDS changed the wait at NO code -- "
                            "the stimulus is not reaching the election, so "
                            "check 1 measured nothing"))
    else:
        log.info("\n  RWDS moved the `var` wait at %d of %d codes",
                 var_moved, len(LATENCY_CLOCKS_BY_CODE) + 1)

    # 4. `.any()`: one bit anywhere in the window is the whole answer.
    L = latency_ck(0)
    n, low = L - 1, short_clocks(controller_cls, L)
    short_ck = elected_ck(controller_cls, n, 0, 0, low=low)
    lone = [bit for bit in range(width)
            if elected_ck(controller_cls, n, 0, 1 << bit, low=low) != short_ck]
    log.info("  one high bit elects the long count from %d of %d rwds.i bits: %s",
             len(lone), width, lone)
    if len(lone) != width:
        failures.append((4, f"only {len(lone)} of {width} rwds.i bits elect the "
                            "long count, so `.any()` is not the sensitivity it "
                            "looks like"))

    # 5. ONE fabric cycle, and the one the controller declares. (#381)
    declared = declared_sample_cycle(controller_cls)
    window = sensitivity_window(controller_cls, n, all_high)
    log.info("  the election is sensitive to fabric cycle(s) %s; the controller "
             "declares %s", sorted(window), declared)
    if declared is None:
        failures.append((5, "the controller declares no `rwds_sample_cycle`, so "
                            "the sample is wherever its states happen to sit -- "
                            "which is the CA, before the device is selected "
                            "(#381)"))
    elif window != {declared}:
        failures.append((5, f"the election is sensitive to {sorted(window)}, "
                            f"not to the declared cycle {declared} alone"))
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", default=",".join(MUTATIONS),
                        help="comma-separated mutations to run as controls "
                             f"(default all: {', '.join(MUTATIONS)})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    good = run_checks(load_controller(), "the controller as committed")
    for number, line in good:
        log.info("  FAIL check %d: %s", number, line)

    # A check earns its place only if it can fail. It is demonstrated either by
    # failing on the committed controller -- the defect is still live -- or by a
    # mutation that makes it fail.
    demonstrated = {number for number, _ in good}
    uninjectable = []
    for name in [n.strip() for n in args.controls.split(",") if n.strip()]:
        mutant = load_controller(name)
        if mutant is None:
            uninjectable.append(name)
            log.info("\n=== CONTROL %s: NOT INJECTABLE -- no edit of %s matches "
                     "%s ===", name, MUTATIONS[name]["why"], CONTROLLER.name)
            continue
        wanted = MUTATIONS[name]["check"]
        caught = [line for number, line in
                  run_checks(mutant, f"CONTROL {name}: {MUTATIONS[name]['why']}")
                  if number == wanted]
        log.info("  the mutation produced %d failure(s) on check %d",
                 len(caught), wanted)
        if caught:
            demonstrated.add(wanted)

    missing = sorted({1, 2, 3, 4, 5} - demonstrated)
    log.info("\n  checks shown able to fail: %s", sorted(demonstrated))
    if uninjectable:
        log.info("  controls not injectable at this commit: %s",
                 ", ".join(uninjectable))

    if missing:
        log.info("\nFAIL -- check(s) %s neither failed on the committed "
                 "controller nor on any control, so they cannot fail at all",
                 missing)
        return 1
    if good:
        log.info("\nFAIL -- the committed controller failed %d check(s)", len(good))
        return 1
    log.info("\nPASS -- every check passed on the controller as committed, and "
             "every one was shown failing on the defect it exists for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
