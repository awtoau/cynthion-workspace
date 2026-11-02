#!/usr/bin/env python3
#
# One worktree must not consume another's stash. See #369.
# SPDX-License-Identifier: BSD-3-Clause

"""`stash_guard.decide` against a real repo with two worktrees sharing one stash stack.

Control: the exact accident from #369 (bare pop after the other worktree stashed)
is shown to be possible in git itself, then denied by the guard.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stash_guard  # noqa: E402


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
        capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def two(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    git(a, "init", "-q", "-b", "main")
    (a / "f").write_text("0\n")
    git(a, "add", "f")
    git(a, "commit", "-qm", "init")
    b = tmp_path / "b"
    git(a, "worktree", "add", "-q", "-b", "agent-b", str(b))
    (a / "f").write_text("a\n")
    git(a, "stash", "push", "-qm", "mine-a")
    (b / "f").write_text("b\n")
    git(b, "stash", "push", "-qm", "mine-b")  # now stash@{0} is b's
    return str(a), str(b)


def test_bare_pop_is_the_accident_and_is_denied(two):
    a, b = two
    assert git(a, "stash", "list", "--format=%gs").splitlines()[0].startswith("On agent-b")
    assert "no ref" in stash_guard.decide("git stash pop", a)
    assert "no ref" in stash_guard.decide("git stash pop", b)  # top is not b's either way
    assert stash_guard.decide("git stash", a) is None  # bare = push, allowed


def test_pop_of_another_worktrees_entry_is_denied(two):
    a, b = two
    assert "belongs to 'agent-b'" in stash_guard.decide("git stash pop stash@{0}", a)
    assert "belongs to 'main'" in stash_guard.decide("git stash apply stash@{1}", b)
    assert stash_guard.decide("git stash drop stash@{0}", a)


def test_own_entry_allowed_by_ref_or_sha(two):
    a, b = two
    assert stash_guard.decide("git stash pop stash@{1}", a) is None
    assert stash_guard.decide("git stash pop stash@{0}", b) is None
    sha = git(a, "rev-parse", "stash@{1}")
    assert stash_guard.decide(f"git stash apply {sha}", a) is None
    assert stash_guard.decide(f"git stash apply {sha}", b)


def test_clear_and_chained_and_dash_C(two):
    a, b = two
    assert "clear" in stash_guard.decide("git stash clear", a)
    assert stash_guard.decide("cd x && git stash pop", a)
    assert stash_guard.decide(f"git -C {b} stash pop stash@{{1}}", a)
    assert stash_guard.decide(f"git -C {b} stash pop stash@{{0}}", a) is None


def test_harmless_commands_pass(two):
    a, _ = two
    for cmd in ("git stash list", "git stash push -m x", "git stash show stash@{0}",
                "git status", "echo stash pop"):
        assert stash_guard.decide(cmd, a) is None, cmd


def test_hook_entrypoint_exit_codes(two):
    a, _ = two
    def run(command):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "stash_guard.py")],
            input=json.dumps({"tool_input": {"command": command}, "cwd": a}),
            capture_output=True, text=True).returncode
    assert run("git stash pop") == 2
    assert run("git stash pop stash@{1}") == 0


def test_hook_is_registered_in_project_settings():
    hooks = json.loads((ROOT / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
    cmds = [h["command"] for e in hooks if e["matcher"] == "Bash" for h in e["hooks"]]
    assert any("scripts/stash_guard.py" in c for c in cmds)
