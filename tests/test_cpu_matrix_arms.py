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
Info:    routing  1.07  3.91 Net cpu.TrapPlugin_logic_harts_0_trap_fsm_stateNext[1] (20,38) -> (20,38)
Info:                          Sink cpu.FetchL1Plugin_logic_plru_mem_spinal_port1_TRELLIS_FF_Q.M
Info:      setup  0.00  3.91 Source cpu.FetchL1Plugin_logic_plru_mem_spinal_port1_TRELLIS_FF_Q.M
Info: 0.76 ns logic, 3.15 ns routing

Info: Critical path report for cross-domain path '<async>' -> '<async>':
Info:   clk-to-q  0.40  0.40 Source stager.fifo.produce_w_bin_TRELLIS_FF_Q_4.Q
Info:    routing  9.99  10.39 Net stager.fifo.produce_w_bin[5] (15,3) -> (16,4)
Info: 0.40 ns logic, 9.99 ns routing

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


def test_critical_path_names_both_ends_and_stops_at_its_own_section():
    """The cross-domain reports follow immediately; a parser that runs into them
    reports an 86 ns path on a 14 ns design."""
    path = soc_occupancy_timing.critical_path(NEXTPNR_LOG, "$glbnet$clk")
    assert (path["from"], path["to"]) == ("TrapPlugin", "FetchL1Plugin")
    assert path["busiest"] == "TrapPlugin"
    assert path["hops"] == 2
    # nextpnr's own summary line, not an accumulation of the steps.
    assert (path["logic_ns"], path["routing_ns"]) == (0.76, 3.15)
    assert path["bbox"] == [4, 33, 20, 38]
    assert "PerformanceCounterPlugin" in path["plugins"]


def test_critical_path_of_a_clock_that_is_not_in_the_log_is_none():
    assert soc_occupancy_timing.critical_path(NEXTPNR_LOG, "$glbnet$usb") is None


def test_constrain_clones_the_netlist_and_moves_only_the_constraint(tmp_path,
                                                                    monkeypatch):
    """The one comparison that changes the constraint and nothing else."""
    monkeypatch.setattr(soc_occupancy_timing, "OUT", tmp_path)
    synth = tmp_path / "base" / "synth"
    synth.mkdir(parents=True)
    (synth / "top.json").write_text('{"modules": {}}')
    (synth / "build_top.sh").write_text("nextpnr-ecp5 --lpf top.lpf\n")
    (synth / "top.lpf").write_text(
        'FREQUENCY NET "car.clk_sync" 60000000.0 HZ;\n'
        'FREQUENCY NET "user_jtag.tck" 20000000.0 HZ;\n')

    soc_occupancy_timing.constrain("base", 80)
    clone = tmp_path / "base-at80" / "synth"
    assert (clone / "top.json").read_text() == (synth / "top.json").read_text()
    lpf = (clone / "top.lpf").read_text()
    assert 'FREQUENCY NET "car.clk_sync" 80000000.0 HZ;' in lpf
    assert 'FREQUENCY NET "user_jtag.tck" 20000000.0 HZ;' in lpf


def test_constrain_refuses_an_lpf_it_cannot_find_the_clock_in(tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(soc_occupancy_timing, "OUT", tmp_path)
    synth = tmp_path / "base" / "synth"
    synth.mkdir(parents=True)
    (synth / "top.json").write_text("{}")
    (synth / "build_top.sh").write_text("\n")
    (synth / "top.lpf").write_text('FREQUENCY NET "renamed" 60000000.0 HZ;\n')
    try:
        soc_occupancy_timing.constrain("base", 80)
    except SystemExit as error:
        assert "expected 1" in str(error)
    else:
        raise AssertionError("a silently unconstrained clone was produced")


def test_a_sample_that_does_not_converge_is_bounded_and_recorded(tmp_path,
                                                                 monkeypatch):
    """A router that will not converge must not hold a sweep open forever."""
    monkeypatch.setattr(soc_occupancy_timing, "OUT", tmp_path)
    monkeypatch.setattr(soc_occupancy_timing, "SAMPLE_LIMIT_S", 1)
    # A stand-in for a router that never finishes, killed by the bound. It has
    # to tolerate the `--seed N` the harness appends, so not `sleep`.
    monkeypatch.setattr(
        soc_occupancy_timing, "nextpnr_command",
        lambda arm: [sys.executable, "-c", "import time; time.sleep(30)"])
    (tmp_path / "stuck" / "synth").mkdir(parents=True)
    row = soc_occupancy_timing.sample("stuck", 7)
    assert row["ok"] is False and "not converging" in row["why"]
    assert row["seconds"] >= 1


def test_a_ladder_rung_is_a_build_directory_of_its_own():
    """#439: the rung has to reach the build, and be visible in the artifacts."""
    dirs = {mhz: variant.build_dir(ROOT, {"CYNTHION_SYNC_MHZ": f"{mhz:g}"})
            for mhz in (50, 60, 72)}
    assert len(set(dirs.values())) == 3, dirs
    assert variant.value("CYNTHION_SYNC_MHZ", {"CYNTHION_SYNC_MHZ": "72"}) == "72"


def test_the_ladder_reads_the_constraint_nextpnr_enforced():
    """The check whose absence let every rung build at 60 and say otherwise.

    `timing()` is shared with `soc_sync_ladder.py` and is what both compare the
    rung against; a log without `Program finished normally.` yields nothing at
    all, so a killed run cannot be read as a rung (#440).
    """
    got = riscv_clock_ladder.timing(NEXTPNR_LOG + "\nInfo: Program finished normally.\n")
    assert got["clk"]["target"] == 60.00 and got["clk"]["mhz"] == 62.36
    assert riscv_clock_ladder.timing(NEXTPNR_LOG) == {}
