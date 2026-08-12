#!/usr/bin/env python3
#
# One confirmation step, used by every path that configures. See #360.
# SPDX-License-Identifier: BSD-3-Clause

"""No script may invoke `apollo configure` without confirming a design came up.

The gate was per-script, added each time a script got bitten -- `alive()` in
`hyperram_pin_axis.py` was the only one, and every other path still trusted the
exit code. `apollo configure` returns 0 with a blank FPGA (#360), so this fails
when a new caller reaches the CLI directly.

Structural, because nothing else can be: a board holds one state at a time, so
no run can demonstrate that all the OTHER call sites are gated.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# The one path. Everything else asks it.
OWNER = "soc_confirm.py"

# `"configure"` as an argv element -- the CLI verb, not the English word.
INVOCATION = re.compile(r'["\']configure["\']\s*[,\]]')

# Not callers of the CLI: each names the verb as one of its OWN choices -- a
# `--mode` in one, a job kind in the arbiter and its client, which reach the
# board only through `soc_confirm.configure_and_confirm`.
EXEMPT = {"bitstream_load_time_probe.py", "board_arbiter.py", "board.py"}


def callers():
    """Scripts whose source hands `configure` to the Apollo CLI."""
    found = {}
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name in EXEMPT:
            continue
        text = path.read_text()
        if INVOCATION.search(text) and "apollo" in text.lower():
            found[path.name] = text
    return found


def test_only_soc_confirm_invokes_the_apollo_cli_directly():
    direct = sorted(name for name in callers() if name != OWNER)
    assert not direct, (
        f"these reach `apollo configure` without confirming a design came up: "
        f"{direct}. Use `soc_confirm.configure_and_confirm(bitstream, "
        f"expect=...)` -- a zero exit is not a running design (#360)")


def test_soc_confirm_itself_still_invokes_it():
    """The control: if the pattern stops matching, the test above is vacuous."""
    assert OWNER in callers(), (
        "the invocation pattern no longer matches soc_confirm.py, so the check "
        "above would pass no matter what any other script did")


def test_every_configure_caller_states_which_confirmation_it_expects():
    """`expect=` is not optional in practice: the default is the strictest.

    A caller that prompts the console when its own next act is to capture the
    banner eats the transcript. Each call site therefore names its level, and
    this asserts the argument is present wherever the helper is called with a
    bitstream.
    """
    calls = 0
    for path in sorted(SCRIPTS.glob("*.py")):
        for line in re.findall(r"configure_and_confirm\((?!bitstream,\s*\*)[^)]*\)",
                               path.read_text()):
            calls += 1
            assert "expect=" in line, f"{path.name}: {line} does not say what it expects"
    assert calls >= 8, f"only {calls} call sites found; the sweep is not working"
