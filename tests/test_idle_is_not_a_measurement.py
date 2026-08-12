#!/usr/bin/env python3
#
# Idle output must be INCAPABLE of being quoted as a measurement. #430
# SPDX-License-Identifier: BSD-3-Clause

"""The refusal, proved.

Three independent barriers, because a caveat beside a number is read as the
number and this corpus has been deleted twice:

1. a separate directory -- `tmp/board-arbiter/idle/`, never `results/hyperram/`
2. a separate schema -- `board-idle-observation`, and `load` refuses it
3. different keys -- no `failures`, no `summary`, so the diff could not consume
   one even with the refusal removed
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

import board_arbiter as arb  # noqa: E402
import hyperram_matrix_diff as matrix  # noqa: E402


SWEEP = """
      time  lat  mode  drive  clk  sel    errors     words   control  verdict
000000.424    2  fix       3  dif    0      8192      8192      8192  fail
000000.427    2  fix       3  dif    2         0      8192      8192  PASS
  1 pass, 1 fail, 0 no result of 2
"""


def observation(status="passed", reply=SWEEP, command="bist all 8", die=50):
    return {
        "schema": arb.SCHEMA_IDLE, "id": "20260812-000000-aaaa",
        "priority": "idle", "status": status,
        "request": {"commands": [command], "budget_s": 40.0},
        "provenance": {"board": {"image": "deadbee", "die_c": die}},
        "transcript": [{"command": command, "pass": 0, "status": "ok",
                        "elapsed_s": 1.0, "reply": reply}],
    }


def test_the_diff_refuses_an_idle_observation_by_schema(tmp_path):
    path = tmp_path / "20260812-000000-aaaa.json"
    path.write_text(json.dumps(arb.idle_observe(observation(), None)))
    with pytest.raises(SystemExit) as refusal:
        matrix.load(path)
    assert "IDLE OBSERVATION" in str(refusal.value)


def test_the_diff_refuses_anything_under_the_idle_directory(tmp_path):
    """Even stripped of its schema: the directory is the second barrier."""
    directory = tmp_path / "board-arbiter" / "idle"
    directory.mkdir(parents=True)
    run = observation()
    del run["schema"]
    (directory / "run.json").write_text(json.dumps(run))
    with pytest.raises(SystemExit):
        matrix.load(directory / "run.json")


def test_a_recorded_run_still_loads(tmp_path):
    """The refusal must not swallow the artifact it exists to protect."""
    path = tmp_path / "20260812-000000-baseline.json"
    path.write_text(json.dumps({"schema": "hyperram-matrix-run",
                                "failures": {}, "summary": {}}))
    assert matrix.load(path)["schema"] == "hyperram-matrix-run"


def test_an_idle_observation_carries_no_measurement_keys():
    run = arb.idle_observe(observation(), None)
    for forbidden in ("failures", "summary", "no_results", "pins", "ck_mhz",
                      "axis_fail_counts"):
        assert forbidden not in run, f"{forbidden} makes this quotable as a row"


def test_the_idle_directory_is_not_the_results_directory():
    assert arb.IDLE_DIR != matrix.RESULTS
    assert "results" not in arb.IDLE_DIR.as_posix()


# --- what an idle run is FOR: events ---------------------------------------


def test_a_wedge_is_an_event():
    run = observation(status="failed", reply="")
    run["transcript"][0]["status"] = "timeout"
    run["transcript"][0]["elapsed_s"] = 40.0
    events = arb.idle_observe(run, None)["events"]
    assert any(line.startswith("WEDGE:") for line in events)


def test_a_moved_tally_is_an_event():
    first = arb.idle_observe(observation(), None)
    moved = SWEEP.replace("1 pass, 1 fail", "0 pass, 2 fail")
    second = arb.idle_observe(observation(reply=moved), first)
    assert any("TALLY MOVED" in line for line in second["events"])


def test_a_cell_that_changed_verdict_is_an_event():
    first = arb.idle_observe(observation(), None)
    flipped = SWEEP.replace("      0      8192      8192  PASS",
                            "     16      8192      8192  fail")
    second = arb.idle_observe(observation(reply=flipped), first)
    assert any("changed verdict" in line for line in second["events"])


def test_die_drift_is_an_event():
    first = arb.idle_observe(observation(die=50), None)
    second = arb.idle_observe(
        observation(die=50 + arb.DIE_DRIFT_C), first)
    assert any("DIE TEMPERATURE" in line for line in second["events"])


def test_identical_idle_runs_report_nothing():
    """No event is the normal case, and it must not manufacture one."""
    first = arb.idle_observe(observation(), None)
    second = arb.idle_observe(observation(), first)
    assert second["events"] == []


def test_a_long_reply_is_trimmed_but_its_size_is_kept():
    run = arb.idle_observe(observation(reply="x" * 50_000), None)
    step = run["transcript"][0]
    assert step["reply_bytes"] == 50_000
    assert len(step["reply"]) == arb.IDLE_REPLY_TAIL
