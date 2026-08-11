#!/usr/bin/env python3
#
# Does the host still parse what the board prints? #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Hold `scripts/bist_rows.py` against a transcript of the firmware's output.

**A regex that silently matches nothing looks exactly like a board that reported
nothing.** Three of the seven scripts that parsed BIST rows had already drifted
out of step with the firmware unnoticed -- `hyperram_matrix.py`'s pattern
predated the timestamp column entirely -- and every one of them presented as a
dead rig rather than as a broken parser. `hyperram_ck_sweep.py` was fixed for
exactly this once (82268d7); this is what stops the next one.

The fixtures below are the output of `firmware/cynthion-soc/src/bist.rs`, column
for column. They are checked two ways:

  * every pattern in `bist_rows` must match them -- a firmware format change
    fails here;
  * no script may carry its own row pattern -- a copy that drifts is the whole
    failure mode, so a new one fails here too.

The column widths themselves are held against the firmware by
`firmware/cynthion-soc-tests/tests/columns.rs`, which renders a row with
`FIELD_WIDTHS` and lands it under `HEADING`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bist_rows  # noqa: E402

# `bist latency 16` as the board prints it. Each row is followed by the `sel`
# census that says which capture phases were walked for it (#421), the first-bad
# evidence line under the failing one, and the summary every sweep ends with.
LATENCY = """\
  256 cells: CR0[7:4] latency code x fix/var x 8 capture phase(s), at drive 3
  one row per code at its best phase; `sel` under it says what else was tried
  CK 80 MHz  rung 0 of 2  PLL locked
  sync 60 MHz counted  PLL locked
  time   ms since boot            lat    CR0[7:4] latency code
  mode   CR0[3] fixed/variable    drive  CR0[14:12] output drive
  clk    CR1[6] differential/single-ended    sel  READCLKSEL capture phase
  errors/words  the real pass    control  the negative control's errors
      time  lat  mode  drive  clk  sel    errors     words   control  verdict
000101.787    0  fix       3  dif    2         0      2048      2048  PASS
      sel  8 walked  pass 1,2,3  pick 2 -- the widest window's centre
000101.812    0  var       3  dif    5      2048      2048      2048  fail
      sel  8 walked  none passed  pick 5 -- the fewest errors
      first bad: index 0x0  got 0x00000000  want 0xffbf0000
000101.840   15  var       7  dif    6         8      1024      1024  NO RESULT -- control did not fire
      sel  8 walked  none passed  pick 6 -- the fewest errors
  1 pass, 1 fail, 1 no result of 3
"""

# `bist cell 3 se 0 2 fix`, which prints one row with the status decode under it.
CELL = """\
  CK 120 MHz  rung 0 of 1  PLL locked
  sync 60 MHz counted  PLL locked
      time  lat  mode  drive  clk  sel    errors     words   control  verdict
000197.956    2  fix       3   se    0      8192      8192      8192  fail
      real   : status 0x4078f7 busy=1 done=1 error=1 negative=0
      control: status 0x4078f7 busy=1 done=1 error=1 negative=0
      first bad: index 0x0  got 0x0000003f  want 0xffbf0000
"""

# `bist all` on a non-DQS build: only non-clean rows print, so a clean run has
# NO rows and that is not a parse failure.
ALL_CLEAN = """\
  4096 cells: latency x fix/var x drive x clock x phase
  512 DISTINCT configurations x 8 repeats: `sel` reaches nothing without the DQS PHY (#343)
  printing only what is NOT a clean pass
  CK 90 MHz  rung 1 of 2  PLL locked
  sync 60 MHz counted  PLL locked
      time  lat  mode  drive  clk  sel    errors     words   control  verdict
  4096 pass, 0 fail, 0 no result of 4096
  512 DISTINCT configurations x 8 repeats: `sel` reaches nothing without the DQS PHY (#343)
"""


PURE = ROOT / "firmware" / "cynthion-soc" / "src" / "bist" / "pure.rs"


def firmware_columns():
    """`FIELD_WIDTHS` and `HEADING` out of the firmware, not copied here.

    A fixture typed by hand drifts from the format string it is meant to stand
    for, and then this file agrees with itself while the board prints something
    else.
    """
    text = PURE.read_text()
    widths = [int(n) for n in re.search(
        r"FIELD_WIDTHS: \[usize; \d+\] = \[([^\]]+)\]", text).group(1).split(",")]
    heading = re.search(r'pub const HEADING: &str =\s*"([^"]*)"', text).group(1)
    return widths, heading


def test_the_fixtures_are_the_firmwares_own_columns():
    """The transcripts above, held against `pure.rs`. A width changed in the
    firmware fails here rather than on a board."""
    widths, heading = firmware_columns()
    assert heading in LATENCY, "the fixture heading is not the firmware's"

    ends, at = [], 0
    for width in widths:
        at += width
        ends.append(at)
        at += 2

    # By its stamp, not its line number: the fixture grows evidence lines.
    row, = [line for line in LATENCY.splitlines() if line.startswith("000101.812")]
    for i, (end, want) in enumerate(zip(ends, [
            "000101.812", "0", "var", "3", "dif", "5", "2048", "2048", "2048"])):
        assert row[end - widths[i]:end].strip() == want, f"field {i}: {row!r}"
        assert heading[end - widths[i]:end].strip(), f"heading field {i} is blank"
    assert row[ends[-1] + 2:] == "fail"


def test_a_row_yields_every_axis_and_every_count():
    row, = bist_rows.rows(
        "000038.640   15  var       7  dif    6         8      1024      1024  fail")
    assert (row["lat"], row["mode"], row["drive"]) == (15, "var", 7)
    assert (row["clk"], row["sel"]) == ("dif", 6)
    assert (row["errors"], row["words"], row["control"]) == (8, 1024, 1024)
    assert row["verdict"] == "fail"
    assert row["time"] == "000038.640"
    assert row["fixed"] is False


def test_the_verdict_keeps_its_explanation():
    """`fail` and `fail (SHORT ...)` are different results and a truncated
    verdict makes them one."""
    rows = bist_rows.rows(LATENCY)
    assert [r["verdict"] for r in rows] == [
        "PASS", "fail", "NO RESULT -- control did not fire"]


def test_a_sweep_transcript_parses_rows_summary_and_evidence():
    rows = bist_rows.require_rows(LATENCY, "bist latency")
    assert len(rows) == 3
    assert bist_rows.require_summary(LATENCY, "bist latency") == {
        "passed": 1, "failed": 1, "no_result": 1, "total": 3}
    bad = bist_rows.FIRST_BAD.search(LATENCY)
    assert bad and (bad["index"], bad["got"], bad["want"]) == (
        "0x0", "0x00000000", "0xffbf0000")


def test_the_speeds_are_on_every_table():
    """A table whose frequency is not on it cannot be compared with another."""
    for text, ck in ((LATENCY, "80"), (CELL, "120"), (ALL_CLEAN, "90")):
        assert bist_rows.CK.search(text)["mhz"] == ck
        assert bist_rows.SYNC.search(text)["mhz"] == "60"


def test_a_single_cell_row_parses_the_same_as_a_sweep_row():
    """One row shape for every verb, so one pattern reads them all."""
    row, = bist_rows.require_rows(CELL, "bist cell")
    assert (row["lat"], row["mode"], row["drive"], row["clk"], row["sel"]) == (
        2, "fix", 3, "se", 0)
    assert row["words"] == 8192


def test_the_census_says_which_phases_were_walked_for_each_row():
    """A latency row is a statement about its capture phase unless this line
    says what else was tried (#421)."""
    first, second, third = bist_rows.censuses(LATENCY)
    assert (first["walked"], first["passed"], first["pick"]) == (8, [1, 2, 3], 2)
    # NOTHING PASSED carries no pass list: a pick out of a dead sweep must not
    # read as a working phase.
    assert (second["passed"], second["pick"]) == ([], 5)
    assert third["passed"] == []
    # The last one is what `require_census` hands a caller asking for a phase.
    assert bist_rows.require_census(LATENCY, "bist latency")["pick"] == 6


def test_a_census_line_is_not_read_as_a_result_row():
    """Both are indented lines under a heading; only one has a stamp."""
    census = "      sel  8 walked  pass 1,2,3  pick 2 -- the widest window's centre"
    assert bist_rows.ROW.search(census) is None
    assert len(bist_rows.rows(LATENCY)) == 3


def test_a_sweep_with_no_census_exits_rather_than_defaulting_to_phase_zero():
    with pytest.raises(SystemExit) as raised:
        bist_rows.require_census(CELL, "bist eye")
    assert "NO `sel` CENSUS" in str(raised.value)


def test_the_heading_is_recognised_apart_from_the_rows():
    """"the parser is wrong" and "the sweep did not run" must not look alike."""
    assert bist_rows.HEADING.search(LATENCY)
    assert not bist_rows.HEADING.search("no table here")
    assert not bist_rows.ROW.search(bist_rows.HEADING.search(LATENCY).group(0))


def test_a_clean_all_run_has_no_rows_but_still_has_a_summary():
    assert bist_rows.rows(ALL_CLEAN) == []
    assert bist_rows.require_summary(ALL_CLEAN, "bist all")["total"] == 4096
    repeats = bist_rows.REPEATS.search(ALL_CLEAN)
    assert (repeats["distinct"], repeats["repeats"]) == ("512", "8")


def test_an_unparsable_reply_exits_loudly_rather_than_returning_empty():
    """The bug this module exists to prevent: an empty result set read as a
    board that reported nothing."""
    with pytest.raises(SystemExit) as raised:
        bist_rows.require_rows("bist: this bitstream has no engine\n", "bist all")
    assert "NO ROWS PARSED" in str(raised.value)

    # And a heading with no rows must blame the PARSE, not the board.
    heading = bist_rows.HEADING.search(LATENCY).group(0) + "\n"
    with pytest.raises(SystemExit) as raised:
        bist_rows.require_rows(heading, "bist latency")
    assert "the PARSE is wrong" in str(raised.value)


def test_a_sweep_that_stopped_early_is_not_saved_as_a_complete_one():
    with pytest.raises(SystemExit) as raised:
        bist_rows.require_summary(CELL, "bist all")
    assert "NO SUMMARY LINE" in str(raised.value)


# A row pattern living anywhere but `bist_rows`. The two alternations are only
# ever written in a regex: the axes appear elsewhere as `("dif", "se")` tuples
# and as `dif`/`fix` command words, neither of which carries the pipe.
PRIVATE_ROW = ("dif|se", "fix|var", "PASS|fail")


def test_no_script_carries_its_own_copy_of_the_row_pattern():
    """Seven copies is how three of them went stale without anyone noticing."""
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name == "bist_rows.py":
            continue
        text = path.read_text()
        if any(pattern in text for pattern in PRIVATE_ROW):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} carry their own BIST row pattern. Import `bist_rows` "
        "instead -- a private copy drifts and then matches nothing, which is "
        "indistinguishable from a board that reported nothing.")
