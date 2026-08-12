#!/usr/bin/env python3
#
# The CPU matrix's arms, and the rig that reads its results.
# SPDX-License-Identifier: BSD-3-Clause

"""Can an arm silently be the arm next to it, and can a rung silently not take?

Two failures this guards, both of which have happened:

  * an arm that changes nothing. `--performance-counters` dropped generates a
    core byte-identical to `--performance-counters 0`, because `--with-rdtime`
    adds zicntr and `withPerformanceCounters` is `zihpm || zicntr`. A matrix
    reports the pair as two arms and their difference as noise.
  * a rung that never took. `riscv_clock_ladder.py` rewrote `SYNC_MHZ = \\d+` in
    `top.py`; the assignment became a ternary and then an environment read, so
    the rewrite hit the wrong arm and then nothing at all, and every rung built
    at the default (#439).

The parsers are tested against captured nextpnr output rather than a live build.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

import riscv_clock_ladder  # noqa: E402
import soc_cpu_arms  # noqa: E402
import soc_occupancy_timing  # noqa: E402
from soc import variant  # noqa: E402

# One clock's critical path as nextpnr prints it, trimmed to three hops.
NEXTPNR_LOG = """\
Info: Critical path report for clock '$glbnet$clk' (posedge -> posedge):
Info:       type curr  total name
Info:   clk-to-q  0.40  0.40 Source cpu.TrapPlugin_logic_harts_0_trap_pending_state_code_TRELLIS_FF_Q.Q
Info:    routing  0.87  1.27 Net cpu.TrapPlugin_logic_harts_0_trap_pending_state_code[1] (4,33) -> (12,35)
Info:                          Sink cpu.PerformanceCounterPlugin_logic_ignoreNextCommit_LUT4_Z.B
Info:      logic  0.18  1.45 Source cpu.PerformanceCounterPlugin_logic_ignoreNextCommit_LUT4_Z.F
Info:    routing  1.21  2.66 Net cpu.TrapPlugin_logic_harts_0_trap_fsm_stateNext[0] (12,35) -> (20,38)
Info:                          Sink cpu.TrapPlugin_logic_harts_0_trap_pending_arbiter_down_payload_exception.D
Info:      logic  0.18  2.84 Source cpu.TrapPlugin_logic_harts_0_trap_fsm_buffer_i_valid.F
Info: Max frequency for clock '$glbnet$clk': 62.36 MHz (PASS at 60.00 MHz)
"""


def test_every_arm_changes_something_about_the_generated_core():
    """No two arms may produce the same generator flags AND geometry."""
    seen = {}
    for name in soc_cpu_arms.ARMS:
        arm = soc_cpu_arms.ARMS[name]
        key = (tuple(soc_cpu_arms.flags_for(name)),
               arm.get("sets", "default"), arm.get("ways", "default"),
               tuple(arm.get("drop_ports", ())))
        assert key not in seen, (
            f"{name} and {seen.get(key)} are the same configuration under two "
            f"names; `soc_cpu_arms.py digest` hashes the generated core")
        seen[key] = name


def test_dropping_the_counter_option_alone_does_not_remove_the_plugin():
    """rdtime and the performance counters are one switch (Param.scala:590)."""
    flags = soc_cpu_arms.flags_for("pc-none")
    assert "--performance-counters" not in flags
    # The one that matters: leaving --with-rdtime in place keeps zicntr, and
    # zicntr alone instantiates PerformanceCounterPlugin.
    assert "--with-rdtime" not in flags


def test_base_is_the_shipping_configuration_untouched():
    sys.path.insert(0, str(ROOT / "gateware" / "soc"))
    from cpu.cpu import GENERATE_FLAGS

    assert soc_cpu_arms.flags_for("base") == list(GENERATE_FLAGS)


def test_split_geometry_arms_leave_the_generator_substitution_off():
    """`cache_sets` is one number and cannot say two things."""
    for name, arm in soc_cpu_arms.ARMS.items():
        if any(flag.startswith(("--fetch-l1-", "--lsu-l1-"))
               for flag in arm.get("flags", {})):
            assert arm.get("sets", "unset") is None and arm.get("ways", "unset") is None, (
                f"{name} sets a per-cache flag but leaves top.py's CACHE_SETS "
                f"substitution on, which would overwrite it")


def test_critical_path_is_attributed_to_the_plugin_that_owns_it():
    path = soc_occupancy_timing.critical_path(NEXTPNR_LOG, "$glbnet$clk")
    assert path["owner"] == "TrapPlugin"
    assert path["hops"] == 2
    # clk-to-q counts as logic; the period is logic + routing.
    assert path["logic_ns"] == 0.76
    assert path["routing_ns"] == 2.08
    assert path["bbox"] == [4, 33, 20, 38]
    assert "PerformanceCounterPlugin" in path["plugins"]


def test_critical_path_of_a_clock_that_is_not_in_the_log_is_none():
    assert soc_occupancy_timing.critical_path(NEXTPNR_LOG, "$glbnet$usb") is None


def test_a_ladder_rung_is_a_build_directory_of_its_own():
    """#439: the rung has to reach the build, and be visible in the artifacts."""
    assert (riscv_clock_ladder.rung_build_dir(72)
            != riscv_clock_ladder.rung_build_dir(60))
    assert riscv_clock_ladder.rung_env(72)["CYNTHION_SYNC_MHZ"] == "72"
    assert variant.value("CYNTHION_SYNC_MHZ",
                         riscv_clock_ladder.rung_env(72)) == "72"


def test_the_ladder_reads_the_constraint_nextpnr_enforced():
    """The check whose absence let every rung build at 60 and say otherwise."""
    found = riscv_clock_ladder.CONSTRAINT.search(NEXTPNR_LOG)
    assert found and float(found.group(2)) == 60.00
    assert float(found.group(1)) == 62.36
