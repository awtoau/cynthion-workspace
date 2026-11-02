"""A doc link into a gitignored or untracked file must not resolve (#376)."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_doc_links  # noqa: E402


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.csv\n")
    (tmp_path / "bom.csv").write_text("ignored\n")
    (tmp_path / "scratch.txt").write_text("untracked\n")
    (tmp_path / "kept.md").write_text("tracked\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore", "kept.md"],
                   check=True)


def test_gitignored_file_is_not_a_valid_link_target(tmp_path):
    _repo(tmp_path)
    assert not check_doc_links.is_tracked(tmp_path / "bom.csv")


def test_untracked_file_is_not_a_valid_link_target(tmp_path):
    _repo(tmp_path)
    assert not check_doc_links.is_tracked(tmp_path / "scratch.txt")


def test_tracked_file_is_a_valid_link_target(tmp_path):
    _repo(tmp_path)
    assert check_doc_links.is_tracked(tmp_path / "kept.md")


def test_resolves_rejects_gitignored_link(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(check_doc_links, "ROOT", tmp_path.resolve())
    assert not check_doc_links.resolves(tmp_path / "bom.csv")
    assert check_doc_links.resolves(tmp_path / "kept.md")
