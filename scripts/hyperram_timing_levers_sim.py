#!/usr/bin/env python3
#
# Are the controller's timing parameters levers, and are they set to the right
# numbers for the part that is fitted?
# SPDX-License-Identifier: BSD-3-Clause

"""`HyperRAMController`'s timing constants, swept in Amaranth's own simulator.

#341: a parameter that any sweep varies must be an INPUT, and a parameter that
could be wrong must be swept. #331 and #338 were both "no effect" results that
were really "never connected" -- the sweep moved the part and the controller held
a constant.

What is checked, and what each one would have caught:

  1. **tCSHI is a runtime input** and the CS#-high gap follows it. A constant
     cannot be swept, so its value is a claim nothing tests.
  2. **The gap is right for the FITTED part.** `T_CSHI_NS = 10.0` is Winbond's
     T100 figure; the board carries a `6I` = T166, which wants 6 ns. Too long is
     safe and costs a recovery cycle on every transaction, so this asks for the
     gap to cover tCSHI with at most the rounding plus the one structural cycle.
  3. **The tCSM watchdog bound is a runtime input** and expiry follows it.
  4. **The latency-code table is DERIVED.** `ceil(tACC / tCK)` = 4/5/6/7/7 at
     T100..T250 is exactly the datasheet's minimum code per frequency, so a
     controller that computes it is right at frequencies nobody tabulated.
  5. **tRWR is implemented.** 36 ns at T166, equal to tACC at every grade, and
     covered today only because LC7 happens to spend 7 CK. If the code drops the
     cover goes with it, so the controller has to say when the configured
     latency is below the floor.

All of it runs in `amaranth.sim` against the controller alone -- no device model,
no Icarus, no Diamond. Every case ends in RECOVERY or in the watchdog, both of
which are the controller's own arithmetic.

Usage:

    scripts/hyperram_timing_levers_sim.py
    scripts/hyperram_timing_levers_sim.py --sync-mhz 166

Log: `tmp/logs/hyperram_timing_levers_sim.log`.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from amaranth.hdl import Fragment                            # noqa: E402,F401
from amaranth.sim import Simulator                           # noqa: E402
from luna.gateware.interface.psram import HyperBusPHY        # noqa: E402

from peripherals import hyperram_controller as hc            # noqa: E402
from peripherals.hyperram_controller import HyperRAMController  # noqa: E402

LOGFILE = ROOT / "tmp" / "logs" / "hyperram_timing_levers_sim.log"

# The clock the SoC runs the non-DQS path at, so the tCSHI arithmetic checked
# here is the arithmetic the board uses. `soc_hyperram_sim.NON_DQS_SYNC_MHZ`.
DEFAULT_SYNC_MHZ = 192.0

# The grade the board carries: a `6I`, packaged, 3.0 V, 166 MHz. Restated here
# rather than imported, because a requirement taken from the thing under test
# agrees with it by construction. `Config-AC.v`, docs/chips/hyperram/config-ac.md.
FITTED_TCSHI_NS = 6.0
FITTED_TACC_NS = 36.0

# `ceil(tACC / tCK)` per grade, from the vendor's own AC file: the claim is that
# this IS the datasheet's minimum latency code column, not a separate lookup.
# (tCK ns, tACC ns, the datasheet's minimum code at that frequency.)
GRADE_LATENCY_TABLE = (
    ("T100", 10.0, 40.0, 4),
    ("T133", 7.5, 37.5, 5),
    ("T166", 6.0, 36.0, 6),
    ("T200", 5.0, 35.0, 7),
    ("T250", 4.0, 28.0, 7),
)

# Recovery counts to sweep. 1 is the floor the constructor clamps to and 8 is
# four times the largest any clock this board runs produces, so a lever that is
# wired but ignored shows up as a flat line rather than as a near miss.
RECOVERY_SWEEP = (1, 2, 3, 5, 8)

# Watchdog bounds to sweep. Small enough that expiry is reached in tens of
# cycles rather than the ~700 the tCSM default takes at 192 MHz, and spread so a
# stuck constant cannot match more than one of them.
WATCHDOG_SWEEP = (24, 40, 61)

log = logging.getLogger("hyperram-timing-levers")


class Checks:
    """Pass/fail with the observed value attached, so a failure names itself."""

    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def check(self, what: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            log.info("  PASS  %s%s", what, f"   ({detail})" if detail else "")
        else:
            self.failed.append(f"{what}{f' -- {detail}' if detail else ''}")
            log.error("  FAIL  %s%s", what, f"   ({detail})" if detail else "")
        return ok


class Harness:
    """The controller alone, its `HyperBusPHY` record left for the testbench.

    `phy_round_trip_cycles=0` -- there is no PHY here, and nothing in this file
    looks at RWDS, so the sample instant is irrelevant to every case.
    """

    def __init__(self, *, sync_mhz: float, **kwargs):
        self.phy = HyperBusPHY()
        self.ctl = HyperRAMController(phy=self.phy, sync_mhz=sync_mhz,
                                      phy_round_trip_cycles=0, **kwargs)


def probe(sync_mhz: float, **kwargs):
    """A controller built only to be asked what inputs it has.

    Elaborated and thrown away: Amaranth warns about an `Elaboratable` that is
    built and never used, and the warning is right in general.
    """
    ctl = Harness(sync_mhz=sync_mhz, **kwargs).ctl
    Fragment.get(ctl, None)
    return ctl


def lever(ctl, name: str):
    """The runtime input `name`, or None if this build bakes it at elaboration.

    A missing lever is the #341 defect itself, so it is reported as a failed
    check rather than raised: a sweep that cannot move the controller is the
    thing being looked for.
    """
    value = getattr(ctl, name, None)
    return value if hasattr(value, "shape") else None


# Cycle bounds. Both are derived from the FSM's own state count, not from a round
# number, and expiry logs the state the controller was in and fails the run --
# a testbench that falls out of its loop and carries on is how #316 stayed green.
def recovery_bound(recovery_cycles: int) -> int:
    """Two register writes, each IDLE + CS_SETUP + 3 command + WRITE_DATA +
    (n+1) RECOVERY = 7+n cycles, plus one settling cycle. 1.25x that."""
    return int(1.25 * (2 * (7 + recovery_cycles) + 1))


def watchdog_bound(burst_cycles: int, latency_clocks: int) -> int:
    """Command and latency (4 + L - 1) then the watchdog's own count, then
    RECOVERY. 1.25x the sum."""
    return int(1.25 * (4 + latency_clocks + burst_cycles + 8))


async def _drive_levers(ctx, ctl, levers: dict):
    for name, value in levers.items():
        signal = lever(ctl, name)
        if signal is not None:
            ctx.set(signal, value)


def measure_recovery_gap(sync_mhz: float, *, levers: dict, ctor: dict) -> int | None:
    """Sync cycles CS# stays High between two back-to-back register writes.

    Register writes are used because they need no device: SHIFT_COMMAND2 goes
    straight to WRITE_DATA and WRITE_DATA to RECOVERY. Returns None if the
    second transaction never started inside the bound.
    """
    dut = Harness(sync_mhz=sync_mhz, **ctor)
    limit = recovery_bound(max([1, *levers.values()]))
    result: dict = {}

    async def testbench(ctx):
        await _drive_levers(ctx, dut.ctl, levers)
        ctx.set(dut.ctl.register_space, 1)
        ctx.set(dut.ctl.perform_write, 1)
        ctx.set(dut.ctl.address, 0)
        ctx.set(dut.ctl.write_data, 0x8f2f)
        ctx.set(dut.ctl.start_transfer, 1)
        ctx.set(dut.ctl.final_word, 1)

        seen_high, gap, falls = False, 0, 0
        prev_cs = 0
        for _ in range(limit):
            cs = ctx.get(dut.phy.cs)
            if cs and not prev_cs:
                falls += 1                      # CS# asserted: a transaction began
                if falls == 2:
                    result["gap"] = gap
                    return
            if falls == 1:
                if not cs:
                    seen_high, gap = True, gap + 1
                elif seen_high:
                    pass
            prev_cs = cs
            await ctx.tick()
        result["state"] = ctx.get(dut.ctl.state)

    sim = Simulator(dut.ctl)
    sim.add_clock(1e-6 / sync_mhz)
    sim.add_testbench(testbench)
    sim.run()
    if "gap" not in result:
        log.error("no second transaction inside %d cycles (state %s) -- the "
                  "recovery measurement did not run", limit, result.get("state"))
    return result.get("gap")


def measure_watchdog(sync_mhz: float, *, levers: dict, ctor: dict) -> int | None:
    """Cycles from CS# falling to `timed_out`, on a read the device never answers.

    RWDS is left at 0 for the whole run, so READ_DATA's only exit is the tCSM
    watchdog -- which is the bound being measured.
    """
    dut = Harness(sync_mhz=sync_mhz, **ctor)
    latency = max(HyperRAMController.HIGH_LATENCY_CLOCKS,
                  ctor.get("high_latency_clocks", 0))
    limit = watchdog_bound(max(levers.get("burst_cycles", 0),
                               levers.get("burst_beats", 0)), latency)
    result: dict = {}

    async def testbench(ctx):
        await _drive_levers(ctx, dut.ctl, levers)
        ctx.set(dut.ctl.register_space, 0)
        ctx.set(dut.ctl.perform_write, 0)
        ctx.set(dut.ctl.address, 0x1000)
        ctx.set(dut.ctl.start_transfer, 1)

        age, started = 0, False
        for _ in range(limit):
            if ctx.get(dut.phy.cs):
                started = True
            if started:
                age += 1
                if ctx.get(dut.ctl.timed_out):
                    result["age"] = age
                    return
            await ctx.tick()
        result["state"] = ctx.get(dut.ctl.state)

    sim = Simulator(dut.ctl)
    sim.add_clock(1e-6 / sync_mhz)
    sim.add_testbench(testbench)
    sim.run()
    if "age" not in result:
        log.error("the watchdog did not expire inside %d cycles (state %s)",
                  limit, result.get("state"))
    return result.get("age")


def read_flag(sync_mhz: float, *, levers: dict, ctor: dict, flag: str):
    """Settle the configuration inputs and read a combinational status flag."""
    dut = Harness(sync_mhz=sync_mhz, **ctor)
    signal = lever(dut.ctl, flag)
    if signal is None:
        return None
    result: dict = {}

    async def testbench(ctx):
        await _drive_levers(ctx, dut.ctl, levers)
        await ctx.delay(0)
        result["value"] = ctx.get(signal)

    sim = Simulator(dut.ctl)
    sim.add_clock(1e-6 / sync_mhz)
    sim.add_testbench(testbench)
    sim.run()
    return result.get("value")


def section_tcshi_lever(checks: Checks, sync_mhz: float) -> None:
    """1. tCSHI is a lever, and the gap follows it one for one."""
    log.info("\n1. tCSHI as a runtime input, sync %.0f MHz\n", sync_mhz)

    ctl = probe(sync_mhz)
    if not checks.check("the controller has a `recovery_cycles` input",
                        lever(ctl, "recovery_cycles") is not None,
                        "tCSHI is baked at elaboration, so it cannot be swept "
                        "and its value is a claim nothing tests"):
        return

    ctor = {"max_recovery_cycles": max(RECOVERY_SWEEP)}
    gaps = {}
    for n in RECOVERY_SWEEP:
        gaps[n] = measure_recovery_gap(sync_mhz, levers={"recovery_cycles": n},
                                       ctor=ctor)
    log.info("        gap in cycles per recovery_cycles: %s", gaps)

    # RECOVERY runs n+1 cycles (n down to 0) but drives CS# High from its SECOND,
    # and IDLE holds it High for one more, so the observed gap is n+1.
    checks.check("the CS#-high gap follows the input, n+1 cycles at every n",
                 all(gaps[n] == n + 1 for n in RECOVERY_SWEEP),
                 str(gaps))
    checks.check("...and the sweep moves it at all",
                 len(set(gaps.values())) == len(RECOVERY_SWEEP), str(gaps))


def section_tcshi_value(checks: Checks) -> None:
    """2. The reset gap is right for the part that is fitted."""
    log.info("\n2. tCSHI against the FITTED grade, %g ns at T166\n",
             FITTED_TCSHI_NS)

    for sync_mhz in (166.0, 192.0):
        gap = measure_recovery_gap(sync_mhz, levers={}, ctor={})
        if gap is None:
            checks.check(f"a gap was measured at sync {sync_mhz:g}", False)
            continue
        gap_ns = gap * 1000.0 / sync_mhz
        required = max(1, math.ceil(FITTED_TCSHI_NS * sync_mhz / 1000.0))
        log.info("        sync %.0f MHz: %d cycles = %.2f ns, tCSHI needs %.1f ns "
                 "= %d cycle(s)", sync_mhz, gap, gap_ns, FITTED_TCSHI_NS, required)

        checks.check(f"sync {sync_mhz:g}: the gap covers tCSHI",
                     gap_ns >= FITTED_TCSHI_NS,
                     f"{gap_ns:.2f} ns against {FITTED_TCSHI_NS:g} ns")
        # The rounding, plus the one cycle RECOVERY's exit structurally adds. More
        # than that is a recovery cycle spent on every transaction for nothing.
        checks.check(f"sync {sync_mhz:g}: ...without spending a cycle on nothing",
                     gap <= required + 1,
                     f"{gap} cycles where {required + 1} covers {FITTED_TCSHI_NS:g} "
                     f"ns; 10 ns is Winbond's T100 figure and this part is T166")


def section_watchdog_lever(checks: Checks, sync_mhz: float) -> None:
    """3. The tCSM watchdog bound is a lever."""
    log.info("\n3. The tCSM watchdog bound as a runtime input\n")

    ctl = probe(sync_mhz)
    if not checks.check("the controller has a `burst_cycles` input",
                        lever(ctl, "burst_cycles") is not None,
                        "the tCSM bound is baked, so tCSM behaviour is assumed "
                        "rather than tested"):
        return

    ages = {}
    for n in WATCHDOG_SWEEP:
        ages[n] = measure_watchdog(sync_mhz, levers={"burst_cycles": n}, ctor={})
    log.info("        cycles to `timed_out` per burst_cycles: %s", ages)

    checks.check("a shorter bound expires sooner, monotonically",
                 all(a is not None for a in ages.values())
                 and sorted(ages.values()) == [ages[n] for n in WATCHDOG_SWEEP],
                 str(ages))
    # The bound counts from CS# falling, and `timed_out` is registered one cycle
    # after `burst_remaining` reaches 0, so expiry lands at n+2 measured from the
    # cycle CS# was first seen Low.
    checks.check("...at the cycle the bound names, not at a constant",
                 all(ages[n] == n + 2 for n in WATCHDOG_SWEEP), str(ages))


def section_latency_table(checks: Checks) -> None:
    """4. The minimum latency code is DERIVED from tACC, not looked up."""
    log.info("\n4. ceil(tACC / tCK) against the datasheet's code column\n")

    derive = getattr(hc, "min_latency_code", None)
    if not checks.check("the controller module derives a minimum latency code",
                        callable(derive),
                        "the code table is a lookup, so a frequency nobody "
                        "tabulated has no answer"):
        return

    rows = []
    for grade, tck_ns, tacc_ns, want in GRADE_LATENCY_TABLE:
        got = derive(1000.0 / tck_ns, t_acc_ns=tacc_ns)
        rows.append(f"{grade}:{got}")
        checks.check(f"{grade}: ceil({tacc_ns:g}/{tck_ns:g}) is the datasheet's "
                     f"minimum code {want}", got == want, f"got {got}")
    log.info("        %s", "  ".join(rows))

    # The frequency the board runs the non-DQS path at, which no grade column
    # names. ceil(36 / 5.208) = 7, which is why LC7 is the only legal code there.
    checks.check("192 MHz, which no column tabulates, gives LC7",
                 derive(192.0, t_acc_ns=FITTED_TACC_NS) == 7,
                 str(derive(192.0, t_acc_ns=FITTED_TACC_NS)))


def section_trwr(checks: Checks, sync_mhz: float) -> None:
    """5. tRWR is implemented, not covered by an accident of the latency code."""
    log.info("\n5. tRWR, %g ns at T166 and equal to tACC at every grade\n",
             FITTED_TACC_NS)

    floor = max(1, math.ceil(FITTED_TACC_NS * sync_mhz / 1000.0))
    ctl = probe(sync_mhz)
    if not checks.check("the controller reports a latency below the tRWR floor",
                        lever(ctl, "latency_below_trwr") is not None,
                        "tRWR is implemented nowhere; LC7 covers it by accident, "
                        "and the cover goes if the code drops"):
        return
    if not checks.check("...against a floor it computes itself",
                        lever(ctl, "min_latency_clocks") is not None):
        return

    shipped = read_flag(sync_mhz, flag="latency_below_trwr", ctor={},
                        levers={"fixed_latency": 1,
                                "latency_clocks": 14})
    checks.check("the shipped LC7 fixed-latency configuration is clear",
                 shipped == 0, f"flag {shipped}, floor {floor} CK")

    # Variable latency at LC3: the SHORT branch waits 3 CK where tRWR wants
    # `floor`. This is the configuration the flag exists for -- fixed latency
    # doubles the code and hides it.
    low = read_flag(sync_mhz, flag="latency_below_trwr", ctor={},
                    levers={"fixed_latency": 0, "latency_clocks": 6,
                            "low_latency_clocks": 3})
    checks.check(f"variable latency at LC3 is flagged: 3 CK under {floor}",
                 low == 1, f"flag {low}")

    legal = read_flag(sync_mhz, flag="latency_below_trwr", ctor={},
                      levers={"fixed_latency": 0, "latency_clocks": 14,
                              "low_latency_clocks": floor})
    checks.check(f"...and variable latency at the floor itself is not",
                 legal == 0, f"flag {legal}")


def setup_logging(verbose: bool) -> None:
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOGFILE, mode="w"),
                  logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync-mhz", type=float, default=DEFAULT_SYNC_MHZ,
                    help="sync, which is also CK on the non-DQS path "
                         f"(default: {DEFAULT_SYNC_MHZ:g})")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    checks = Checks()

    section_tcshi_lever(checks, args.sync_mhz)
    section_tcshi_value(checks)
    section_watchdog_lever(checks, args.sync_mhz)
    section_latency_table(checks)
    section_trwr(checks, args.sync_mhz)

    log.info("")
    if checks.failed:
        for f in checks.failed:
            log.error("FAILED: %s", f)
        log.error("%d checks passed, %d FAILED", checks.passed, len(checks.failed))
        return 1
    log.info("all %d checks passed", checks.passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
