#!/usr/bin/env python3
#
# Does the latency axis reach the CONTROLLER on the DQS path, and in the right units? #354.
# SPDX-License-Identifier: BSD-3-Clause

"""Two checks, and the second is the one that would have caught #354.

    ./scripts/hyperram_dqs_latency_sim.py
    ./scripts/hyperram_dqs_latency_sim.py --against HEAD~1   # the control

## 1. The units, measured on the controller itself

`HANDLE_LATENCY` is counted in `sync` cycles and one of those is **2 CK** at 4:1
gearing. The claim in `hyperram_ceiling_top.py` line 194 -- that the DQS count is
`L - 1` where the non-DQS one is `2 x L` -- rests entirely on that, so it is
measured rather than restated: drive `latency_clocks = n`, count the cycles the
FSM actually spends in `HANDLE_LATENCY`, and require `n + 1`.

Pure pysim on `HyperRAMDQSController`. No device model, no PHY shim, no swept
offsets -- the shim's SCLK-to-pin latencies are unresolved (#186) and would make
an end-to-end alignment run inconclusive. This measures the arithmetic, which is
what the fix turns on, and nothing else.

## 2. The wiring, checked structurally

The defect was not a wrong value. It was that `psram.latency_clocks` and
`psram.fixed_latency` were **assigned in one branch of `if self.dqs:` and not the
other**, so the DQS build held reset values while `Bist::configure` reprogrammed
the part on every cell. A value check cannot see that; a check that both branches
drive both inputs can.

`--against <rev>` runs the same check over the file at another commit, which is
how the control is done here: at the commit before the fix it must FAIL.

Log: `tmp/logs/hyperram_dqs_latency_sim.log`.
"""

from __future__ import annotations

import argparse
import ast
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "probes"))

from amaranth.sim import Simulator  # noqa: E402

from hyperram.hyperram_ceiling_top import (  # noqa: E402
    CR0_POWER_ON_LATENCY_CODE, LATENCY_CLOCKS_BY_CODE)
from peripherals.hyperram_dqs_controller import HyperRAMDQSController  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "hyperram_dqs_latency_sim.log"
CEILING = Path("gateware/probes/hyperram/hyperram_ceiling_top.py")

# `HyperRAMDQSController.STATES` index of HANDLE_LATENCY. Read from the class so
# a reordering moves it here too -- the FSM encoding is checked against that
# tuple at elaboration, so it is the authority.
HANDLE_LATENCY = HyperRAMDQSController.STATES.index("HANDLE_LATENCY")

# One controller cycle in CK, 4:1 gearing: four bytes per cycle, two bytes per CK.
CK_PER_CYCLE = 2

# Cycles to let one transaction get as far as the latency phase.
# Waits for: IDLE -> CS_SETUP -> SHIFT_COMMAND0 -> SHIFT_COMMAND1 ->
#   HANDLE_LATENCY, then `n + 1` cycles of it. n <= 14 here, so under 20.
# Multiplier: 4x. On expiry the row reports the count it reached and the check
# fails on the mismatch rather than the run hanging.
SETTLE_CYCLES = 80


def measure_latency_cycles(n, log):
    """`sync` cycles the FSM spends in HANDLE_LATENCY when told `latency_clocks = n`."""
    from luna.gateware.interface.psram import HyperBusDQSPHY

    phy = HyperBusDQSPHY()
    # `sync_mhz` only sets the tCSHI and tCSM counts; 50 matches the testbenches.
    dut = HyperRAMDQSController(phy=phy, sync_mhz=50.0, max_latency_clocks=14)
    seen = []

    async def stimulus(ctx):
        ctx.set(dut.latency_clocks, n)
        ctx.set(dut.fixed_latency, 1)
        ctx.set(dut.perform_write, 0)
        ctx.set(dut.register_space, 0)
        ctx.set(dut.address, 0x100)
        ctx.set(dut.final_word, 1)
        ctx.set(dut.start_transfer, 1)
        await ctx.tick()
        ctx.set(dut.start_transfer, 0)
        entered = False
        for _ in range(SETTLE_CYCLES):
            state = ctx.get(dut.state)
            if state == HANDLE_LATENCY:
                entered = True
                seen.append(1)
            elif entered:
                break
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(stimulus)
    sim.run()
    return len(seen)


def check_units(log):
    """`HANDLE_LATENCY` must run `n + 1` cycles, so the wait is `2 (n + 1)` CK."""
    bad = []
    log.info("--- 1. the units, measured on HyperRAMDQSController ---")
    log.info("  n   cycles in HANDLE_LATENCY   CK waited   n + 1?")
    for n in range(0, 8):
        cycles = measure_latency_cycles(n, log)
        ok = cycles == n + 1
        bad += [] if ok else [f"latency_clocks={n} spent {cycles} cycles in "
                              f"HANDLE_LATENCY, not {n + 1}"]
        log.info("%3d   %20d   %9d   %s", n, cycles, cycles * CK_PER_CYCLE,
                 "yes" if ok else "NO")

    log.info("")
    log.info("--- and what that makes the right count per CR0[7:4] ---")
    log.info("  code  device L  fixed wait  latency_clocks  wait it gives")
    for code, clocks in sorted(LATENCY_CLOCKS_BY_CODE.items()):
        n = clocks - 1
        gives = CK_PER_CYCLE * (n + 1)
        want = 2 * clocks
        ok = gives == want
        bad += [] if ok else [f"code {code}: L - 1 = {n} waits {gives} CK, but "
                              f"fixed latency is {want} CK"]
        log.info("  %4d  %8d  %10d  %14d  %s CK  %s",
                 code, clocks, want, n, gives, "" if ok else "<-- WRONG")
    log.info("  so the DQS count is `L - 1`, and the shipped constant %d is that "
             "for the power-on L of %d -- right all along, never driven",
             LATENCY_CLOCKS_BY_CODE[CR0_POWER_ON_LATENCY_CODE] - 1,
             LATENCY_CLOCKS_BY_CODE[CR0_POWER_ON_LATENCY_CODE])
    return bad


# The two controller inputs that decide when a read is sampled. Both must be
# driven on BOTH paths; one branch driving them is the whole of #354.
REQUIRED = ("latency_clocks", "fixed_latency")


def driven_in(branch):
    """Which of `REQUIRED` are assigned to `psram.<name>` anywhere in `branch`."""
    found = set()
    for stmt in branch:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Attribute):
                continue
            if (node.attr in REQUIRED and isinstance(node.value, ast.Name)
                    and node.value.id == "psram"):
                found.add(node.attr)
    return found


def check_wiring(source, log, label):
    """Both branches of `if self.dqs:` must drive both latency inputs."""
    bad = []
    log.info("--- 2. the wiring in %s ---", label)
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.If)
                and ast.unparse(node.test) == "self.dqs"):
            continue
        for branch, name in ((node.body, "DQS"), (node.orelse, "non-DQS")):
            got = driven_in(branch)
            missing = sorted(set(REQUIRED) - got)
            log.info("  %-8s drives %s%s", name,
                     ", ".join(sorted(got)) or "NEITHER",
                     f"   MISSING {', '.join(missing)}" if missing else "")
            for item in missing:
                bad.append(f"{label}: the {name} branch never drives "
                           f"psram.{item}, so it holds its reset value while "
                           f"the part is reprogrammed on every cell")
        return bad
    bad.append(f"{label}: no `if self.dqs:` found -- the check cannot see the "
               f"thing it is about, which is not a pass")
    return bad


def source_at(rev, log):
    out = subprocess.run(["git", "show", f"{rev}:{CEILING}"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"cannot read {CEILING} at {rev}: {out.stderr.strip()}")
    return out.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--against", metavar="REV",
                    help="also run the wiring check over the file at REV, and "
                         "require it to FAIL -- the control")
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, mode="w")])
    log = logging.getLogger()

    failures = check_units(log)
    log.info("")
    failures += check_wiring((ROOT / CEILING).read_text(), log, "the working tree")

    if args.against:
        log.info("")
        caught = check_wiring(source_at(args.against, log), log, args.against)
        if caught:
            log.info("  control: %d finding(s) at %s, so this check can see the "
                     "defect it certifies absent here", len(caught), args.against)
            for line in caught:
                log.info("    would fail: %s", line)
        else:
            failures.append(f"the CONTROL passed: {CEILING.name} at "
                            f"{args.against} satisfied the wiring check, so the "
                            f"check is not watching what it claims to")

    log.info("")
    for line in failures:
        log.error("FAIL %s", line)
    if not failures:
        log.info("the latency axis reaches the controller on both paths, and "
                 "the DQS count is `L - 1` by measurement")
    log.info("log -> %s", LOG.relative_to(ROOT))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
