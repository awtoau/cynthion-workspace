"""run_bounded's history is shared across worktrees (#435)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "tools"))
import subprocess_timeout_from_history as sth  # noqa: E402

IDENT = ("-c", "user.name=t", "-c", "user.email=t@t")


def _git(cwd, *args):
    subprocess.run(("git", *IDENT, *args), cwd=cwd, check=True,
                   capture_output=True, timeout=30)


@pytest.fixture
def repo_and_worktree(tmp_path):
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "scripts" / "x.py").write_text("")
    _git(main, "init", "-q")
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "i")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    return main, wt


def test_worktree_resolves_to_main_tree(repo_and_worktree):
    main, wt = repo_and_worktree
    assert sth.main_tree(wt).resolve() == main.resolve()


def test_non_git_root_falls_back_to_itself(tmp_path):
    assert sth.main_tree(tmp_path) == tmp_path


def test_history_path_is_the_main_trees():
    assert sth.HISTORY == sth.main_tree() / "tmp" / "build_times.json"
