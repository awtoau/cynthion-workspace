# The scrub must be able to reject. #427.
# SPDX-License-Identifier: BSD-3-Clause

"""`gh_post.py` refuses before anything reaches the network.

A scrub that has never rejected anything is indistinguishable from one that
cannot: the check runs, reports clean, and the leak goes out under it. So every
category it claims to catch is pinned here with an example, and the two it must
NOT catch -- prose naming a credential, and a URL carrying a path-shaped
component -- are pinned with it.

The private-path examples are assembled from pieces on purpose: a literal one in
this file would fail `private_path_check.py`, which is the same check `gh_post`
imports.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gh_post  # noqa: E402
import private_path_check as ppc  # noqa: E402

HOME = "/" + "home/someone"
MNT = "/" + "mnt/2tb/git"


def refusal(fields, repo=gh_post.PREAPPROVED):
    with pytest.raises(gh_post.Refusal) as caught:
        gh_post.gate(repo, fields)
    return str(caught.value)


def test_the_path_patterns_are_the_tracked_file_check_s_own():
    """One set of patterns, not a second that drifts from it."""
    assert gh_post.ppc.PRIVATE is ppc.PRIVATE
    assert gh_post.ppc.URL is ppc.URL


def test_a_private_path_is_refused():
    why = refusal({"body": f"Repro at {HOME}/notes.md\n"})
    assert "private path" in why and "body:1" in why


def test_a_private_path_in_the_title_is_refused():
    """Fields other than the body are published too."""
    assert "private path" in refusal({"title": f"build fails under {MNT}"})


def test_a_url_is_not_a_private_path():
    gh_post.gate(gh_post.PREAPPROVED,
                 {"body": "See https://example.com/mnt/2tb/datasheet.pdf\n"})


def test_a_token_is_refused():
    why = refusal({"body": "GH_TOKEN=ghp_0123456789abcdefghij0123456789abcd\n"})
    assert "GitHub token" in why


def test_a_private_key_block_is_refused():
    assert "private key" in refusal({"body": "-----BEGIN RSA PRIVATE KEY-----\n"})


def test_an_assignment_with_a_value_is_refused():
    assert "credential assignment" in refusal(
        {"body": "the token: hunter2secretvalue is set\n"})


@pytest.mark.parametrize("body", [
    "We discussed the token and the password rules.",
    "token=<yours>, api_key=$GH_KEY, secret=REDACTED.",
    "password: xxxx",
])
def test_prose_and_placeholders_are_allowed(body):
    """Refusing the bare word trains everyone to work around the gate."""
    gh_post.gate(gh_post.PREAPPROVED, {"body": body + "\n"})


def test_the_reverse_engineering_work_is_refused():
    assert "reverse-engineering" in refusal({"body": "the V07 capture shows it\n"})


def test_every_problem_is_reported_not_just_the_first():
    why = refusal({"body": f"{HOME}/a\n{HOME}/b\n"})
    assert "2 problem(s)" in why and "nothing was filed" in why


def test_only_the_preapproved_repo_is_accepted():
    why = refusal({"body": "clean\n"}, repo="greatscottgadgets/cynthion")
    assert "not pre-approved" in why
    # The refusal must name the approval, or the next agent re-derives it.
    assert "second approval" in why and "upstream" in why


def test_a_clean_body_on_the_preapproved_repo_passes():
    gh_post.gate(gh_post.PREAPPROVED,
                 {"body": "The fix is in `scripts/gh_post.py`.\n"})


def test_a_refused_body_reaches_no_gh_call(tmp_path):
    """End to end: the exit status is 1 and no command was composed."""
    body = tmp_path / "dirty.md"
    body.write_text(f"Repro at {HOME}/notes.md\n")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gh_post.py"), "comment", "427",
         "--body-file", str(body), "--dry-run"],
        capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 1
    assert "REFUSED" in done.stdout
    assert "would run" not in done.stdout


def test_a_missing_body_file_is_refused(tmp_path):
    with pytest.raises(gh_post.Refusal):
        gh_post.read_body(tmp_path / "absent.md")
    empty = tmp_path / "empty.md"
    empty.write_text("\n")
    with pytest.raises(gh_post.Refusal):
        gh_post.read_body(empty)
