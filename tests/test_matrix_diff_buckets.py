# The diff must see a PASS -> NO RESULT move. #422.
# SPDX-License-Identifier: BSD-3-Clause

"""Three buckets in the artifact, because the firmware scores three.

`bist.rs` records a cell that passes without its negative control having fired as
**NO RESULT, not a pass** -- a comparator that never fires and a part that never
errs produce identical output.

The recorded artifact had two buckets. A NO RESULT row was printed, counted in
the summary, and then dropped: it entered neither `failures` nor the axis spread.
So a cell moving `PASS` -> `NO RESULT` between two identical runs was invisible
to the diff **that exists to find marginal cells** -- and NO RESULT is exactly
where a marginal cell lands, because the control firing is the thing that goes
intermittent.

The old `len(cells) != failed` warning could not catch it either: the counts
agreed precisely BECAUSE NO RESULTs were excluded from both sides.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hyperram_matrix_diff as md  # noqa: E402

CELL = "0,var,3,dif,2"


def run(failures=None, no_results=None):
    return {
        "ck_mhz": 100, "passes_per_cell": 8, "recorded": "t", "commit": "c",
        "dirty": False, "failures": failures or {}, "no_results": no_results or {},
    }


def test_pass_to_no_result_is_a_move(capsys):
    """The defect, stated as a test.

    Under the old two-bucket diff both runs had an empty failure set, so this
    reported IDENTICAL and returned 0.
    """
    a = run()
    b = run(no_results={CELL: [8, 1024, 1024, "NO RESULT -- control did not fire"]})
    assert md.diff_runs(a, b) == 1
    assert "PASS" in capsys.readouterr().out


def test_fail_to_no_result_is_a_move():
    """Also invisible before: the key was in both failure sets... it was not.

    It left `failures` and entered nothing, so it read as fail -> PASS -- a
    HEALED cell, which is the opposite of what happened.
    """
    a = run(failures={CELL: [8, 1024, 1024, "fail"]})
    b = run(no_results={CELL: [8, 1024, 1024, "NO RESULT -- control did not fire"]})
    assert md.diff_runs(a, b) == 1


def test_identical_runs_do_not_move():
    entry = {CELL: [8, 1024, 1024, "NO RESULT -- control did not fire"]}
    assert md.diff_runs(run(no_results=entry), run(no_results=entry)) == 0


def test_a_real_failure_still_moves():
    a = run()
    b = run(failures={CELL: [8, 1024, 1024, "fail"]})
    assert md.diff_runs(a, b) == 1


def test_the_spread_counts_no_results():
    """An axis judged inert from failures alone is judged on half the evidence.

    A run whose NO RESULTs are structured and whose failures are flat reported
    every axis dead.
    """
    spread = md.axis_spread({CELL: [], "1,fix,3,dif,2": []})
    assert spread["sel"]["2"] == 2
    assert spread["mode"] == {"var": 1, "fix": 1}
