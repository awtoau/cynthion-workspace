#!/usr/bin/env python3
#
# The audit's control: things known to run must come back reachable.
# SPDX-License-Identifier: BSD-3-Clause

"""`audit_scripts.py` must classify what demonstrably runs as `live`.

The audit decides which scripts are retirement candidates, so a bug in it is
acted on by moving files. That has already happened once: #157 records
`hyperram_readclksel_sweep.py` being archived while the issue asking for that
sweep was open, and its test failing at collection unseen ever since.

The control is the two lists that cannot be wrong, because running them IS the
gate: the fifteen names in `soc_sims.SIMS`, and every script `dev.py` names in
its STEPS/COMMANDS tables. Nothing in either can be unreachable. If a change to
the reference resolver drops one, this fails here rather than at the moment
someone runs the tool that needed it.
"""

import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_scripts  # noqa: E402


@lru_cache(maxsize=1)
def reachable():
    """`live`, computed the way `audit_scripts.main` computes it.

    Cached: it parses every script in `scripts/`, and the parametrised test
    below asks thirty times.
    """
    scripts = sorted(p for p in SCRIPTS.iterdir()
                     if p.is_file() and p.suffix in (".py", ".sh"))
    names = {p.name for p in scripts} | {audit_scripts.ENTRY}
    reaches = {p.name: audit_scripts.references_from(p, names) for p in scripts}
    shim = ROOT / audit_scripts.ENTRY
    if shim.exists():
        reaches[audit_scripts.ENTRY] = (
            reaches.get(audit_scripts.ENTRY, set())
            | audit_scripts.references_from(shim, names))

    live, frontier = set(), [audit_scripts.ENTRY]
    while frontier:
        for name in reaches.get(frontier.pop(), ()):
            if name not in live:
                live.add(name)
                frontier.append(name)
    return live


def known_to_run():
    sims = re.search(r"SIMS = \[(.*?)\]", (SCRIPTS / "soc_sims.py").read_text(),
                     re.S).group(1)
    names = {f"{n}.py" for n in re.findall(r'"([^"]+)"', sims)}
    return names | set(re.findall(r'script\("([^"]+)"\)',
                                  (SCRIPTS / "dev.py").read_text()))


@pytest.mark.parametrize("name", sorted(known_to_run()))
def test_script_known_to_run_is_reachable(name):
    assert (SCRIPTS / name).exists(), f"{name} is named but absent"
    assert name in reachable(), (
        f"{name} runs in the gate but the audit calls it unreachable; "
        "it would be reported as a retirement candidate")


@pytest.fixture
def resolver(tmp_path):
    """`references_from` over a file written for the occasion."""
    def resolve(source, names):
        path = tmp_path / "caller.py"
        path.write_text(source)
        return audit_scripts.references_from(path, set(names))
    return resolve


def test_prose_is_not_a_call(resolver):
    """The bug #157 asked to fix, pinned so it cannot come back.

    `references_from` used to be `if name in text`, so a comment or a docstring
    explaining what a script had measured was evidence that something ran it.
    That is how a spent probe stays `called` forever.
    """
    found = resolver('"""Confirmed by soc_run.py, see docs."""\n'
                     '# soc_test.py measured this at 22 s\n'
                     'x = 1\n', {"soc_run.py", "soc_test.py"})
    assert found == set(), f"prose counted as a call: {found}"


def test_an_import_and_an_argv_are_calls(resolver):
    """The two things that ARE evidence, so the fix cannot overshoot."""
    assert resolver("import soc_shell\n", {"soc_shell.py"}) == {"soc_shell.py"}
    assert resolver('run(["python3", "scripts/soc_test.py"])\n',
                    {"soc_test.py"}) == {"soc_test.py"}
    # `soc_sims.py` names its simulations as bare stems and builds the path.
    assert resolver('SIMS = ["soc_bus_sim"]\n',
                    {"soc_bus_sim.py"}) == {"soc_bus_sim.py"}


def test_an_argv_verb_is_not_a_script(resolver):
    """`machine_setup.py` passes "install" to dnf; that is not `install.py`.

    This is what made a 56 KiB installer nothing had run look reachable from
    `./dev.py`.
    """
    assert resolver('run(["dnf", "install", "-y", pkg])\n',
                    {"install.py"}) == set()
    assert not audit_scripts.bare_stem_is_evidence("install")
    assert audit_scripts.bare_stem_is_evidence("soc_bus_sim")
