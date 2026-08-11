#!/usr/bin/env python3
#
# The one place the host side knows what a `bist` row looks like. #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Parse `bist`'s rows, its summary and its evidence lines.

Import it; there is nothing to run.

    import bist_rows
    cells  = bist_rows.require_rows(text, "bist latency")
    totals = bist_rows.require_summary(text, "bist all")

## Why one module and not one regex per script

Seven scripts each carried their own copy of the row pattern and three had
already drifted out of step with the firmware unnoticed -- `hyperram_matrix.py`'s
predated the timestamp column entirely. **A regex that silently matches nothing
looks exactly like a board that reported nothing**, so the drift presents as a
dead rig rather than as a broken parser.

The row is `firmware/cynthion-soc/src/bist/pure.rs`'s `HEADING`/`FIELD_WIDTHS`,
one `writeln!` per cell in `bist.rs`'s `report`:

        time  lat  mode  drive  clk  sel    errors     words   control  verdict
    000038.640   15  var       7  dif    6         8      1024      1024  fail

`tests/test_bist_row_parsers.py` holds every pattern here against a transcript
of the firmware's real output, so a format change fails there rather than on a
board a month later.
"""

from __future__ import annotations

import re

# One row per cell. `verdict` is the rest of the line because it is the one
# unpadded field and carries its own explanation -- `fail (SHORT ...)`,
# `NO RESULT -- control did not fire`.
ROW = re.compile(
    r"^\s*(?P<time>\d+\.\d+)"
    r"\s+(?P<lat>\d+)\s+(?P<mode>fix|var)\s+(?P<drive>\d+)"
    r"\s+(?P<clk>dif|se)\s+(?P<sel>\d+)"
    r"\s+(?P<errors>\d+)\s+(?P<words>\d+)\s+(?P<control>\d+)"
    r"\s+(?P<verdict>\S.*?)\s*$", re.M)

# The heading, so "the parser is wrong" can be told from "the sweep printed
# nothing". Without it both look like an empty result set.
HEADING = re.compile(r"^\s+time\s+lat\s+mode\s+drive\s+clk\s+sel\s+errors\s+"
                     r"words\s+control\s+verdict\s*$", re.M)

# Every sweep ends with this, `latency` and `smoke` included. The three counts
# are separate BY CONTRACT: NO RESULT is not a pass and not a failure.
SUMMARY = re.compile(r"(?P<passed>\d+) pass,\s*(?P<failed>\d+) fail,\s*"
                     r"(?P<no_result>\d+) no result of\s*(?P<total>\d+)")

# The `sel` census under a row that had a choice of capture phase (#421):
# `      sel  8 walked  pass 1,2,3  pick 2 -- the widest window's centre`.
# `passed` is ABSENT when nothing passed, and a caller must then not read `pick`
# as a phase that works -- `require_census` is what enforces that.
CENSUS = re.compile(r"^\s*sel\s+(?P<walked>\d+) walked\s+"
                    r"(?:pass (?P<passed>[\d,]+)|none passed)\s+"
                    r"pick (?P<pick>\d+)", re.M)

# Printed only when the cell count overstates what was varied (#343).
REPEATS = re.compile(r"(?P<distinct>\d+)\s+DISTINCT configurations "
                     r"x (?P<repeats>\d+) repeats")

FIRST_BAD = re.compile(r"first bad:\s*index\s*(?P<index>0x[0-9a-f]+)\s*"
                       r"got\s*(?P<got>0x[0-9a-f]+)\s*"
                       r"want\s*(?P<want>0x[0-9a-f]+)")

# `bist ck`'s rung listing, and the CK/sync lines every sweep now prints above
# its table. The frequency is ragged on purpose -- reachable rungs include
# 85.714 MHz and a sweep exists to tell those apart (#333).
RUNG = re.compile(r"^\s*rung\s+(?P<rung>\d+)\s+(?P<mhz>[\d.]+)\s+MHz"
                  r"(?P<live>\s+<- live)?", re.M)
CK = re.compile(r"^\s+CK\s+(?P<mhz>[\d.]+)\s+MHz", re.M)
SYNC = re.compile(r"^\s+sync\s+(?P<mhz>[\d.]+)\s+MHz", re.M)

# `bist status`, as the PART reports its registers.
CR0_BACK = re.compile(r"CR0\s+part reports\s+(0x[0-9a-fA-F]+)")
CR1_BACK = re.compile(r"CR1\s+part reports\s+(0x[0-9a-fA-F]+)")

INTS = ("lat", "drive", "sel", "errors", "words", "control")


def cell(match):
    """One row's named groups, with the numeric ones as ints."""
    got = match.groupdict()
    got.update({name: int(got[name]) for name in INTS})
    got["fixed"] = got["mode"] == "fix"
    return got


def rows(text):
    """Every result row, as dicts.

    An EMPTY list is legitimate for `bist all` alone, which prints nothing for a
    clean pass. Every other caller wants `require_rows`.
    """
    return [cell(match) for match in ROW.finditer(text)]


def _blame(text):
    """Whether the board printed a table at all, which is what separates a
    broken parser from a sweep that did not run."""
    if HEADING.search(text):
        return ("the heading printed and no row matched it, so the PARSE is "
                "wrong, not the board")
    return "no heading either, so the sweep did not run"


def require_rows(text, what):
    """Every row, or a loud exit. Never an empty list read as a clean result."""
    found = rows(text)
    if not found:
        raise SystemExit(
            f"NO ROWS PARSED from `{what}` -- {_blame(text)}. The row format is "
            "firmware/cynthion-soc/src/bist/pure.rs; tests/test_bist_row_"
            f"parsers.py is the check that should have caught this.\n{text[-800:]}")
    return found


def censuses(text):
    """Every `sel` census, in the order printed. `passed` is a list of ints."""
    found = []
    for match in CENSUS.finditer(text):
        got = match.groupdict()
        found.append({
            "walked": int(got["walked"]),
            "pick": int(got["pick"]),
            "passed": [int(n) for n in got["passed"].split(",")]
                      if got["passed"] else [],
        })
    return found


def require_census(text, what):
    """The LAST census, or a loud exit.

    A caller asking for one wants a phase to run at, so a sweep that printed
    none must not be read as "phase 0 is fine" -- which is the defect in #421.
    """
    found = censuses(text)
    if not found:
        raise SystemExit(
            f"NO `sel` CENSUS from `{what}` -- {_blame(text)}. It is printed by "
            "firmware/cynthion-soc/src/bist.rs under every row that had a "
            f"choice of capture phase.\n{text[-800:]}")
    return found[-1]


def summary(text):
    """The sweep's tally, or `None`."""
    found = SUMMARY.search(text)
    if not found:
        return None
    return {name: int(value) for name, value in found.groupdict().items()}


def require_summary(text, what):
    """The tally, or a loud exit. A sweep without one did not finish, and a
    partial matrix must not be recorded as a complete one."""
    found = summary(text)
    if not found:
        raise SystemExit(
            f"NO SUMMARY LINE from `{what}` -- the sweep did not finish, or its "
            "tally moved away from `N pass, N fail, N no result of N`. Tail "
            f"was:\n{text[-800:]}")
    return found
