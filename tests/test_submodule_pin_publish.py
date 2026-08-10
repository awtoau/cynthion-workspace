#!/usr/bin/env python3
#
# Publishing a pin is irreversible, so its refusals are tested. See #373.
# SPDX-License-Identifier: BSD-3-Clause

"""The refusal paths of `submodule_pin_publish.py`, against real repositories.

A push to a public fork cannot be taken back, so the two things that stop one --
a non-fast-forward, and a private path in the outgoing diff -- are tested rather
than trusted. Real git repositories in `tmp_path`, no network and no submodule of
this repo involved: the checks have to hold for the NEXT pin, not only for
`repos/apollo`.

The positive control is here too: a clean fast-forward must actually push, or
the refusals above prove nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import submodule_pin_publish as publisher  # noqa: E402


def run(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def make_pair(tmp_path, content="clean = 1\n"):
    """A bare origin and a clone with one unpushed commit. Returns (sub, detail)."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    run(["init", "--bare", "-b", "main", str(origin)], tmp_path)
    run(["clone", str(origin), str(work)], tmp_path)
    for name, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        run(["config", name, value], work)
    (work / "first.txt").write_text("first\n")
    run(["add", "-A"], work)
    run(["commit", "-m", "first"], work)
    run(["push", "-u", "origin", "main"], work)

    (work / "second.py").write_text(content)
    run(["add", "-A"], work)
    run(["commit", "-m", "second"], work)
    pin = run(["rev-parse", "HEAD"], work).stdout.strip()

    sub = SimpleNamespace(label="repos/fake", mod=work / ".git",
                          url=str(origin), pin=pin)
    return sub, publisher.plan(sub)


def test_the_plan_names_the_unpushed_commits_and_the_branch(tmp_path):
    sub, detail = make_pair(tmp_path)
    assert detail["branch"] == "main"
    assert detail["fast_forward"] is True
    assert len(detail["commits"]) == 1 and "second" in detail["commits"][0]


def test_a_clean_fast_forward_publishes(tmp_path):
    """The control. Without it the refusals below could be refusing everything."""
    sub, detail = make_pair(tmp_path)
    assert publisher.publish(sub, detail) == 0
    # Verified against the remote, which is the claim being made -- not against
    # the push's exit code (#360 is the same mistake one layer down).
    assert publisher.worktree_setup.on_any_remote(sub.mod, sub.pin)


def test_a_private_path_in_the_outgoing_diff_refuses(tmp_path):
    """These forks are public; a `/mnt/2tb/...` in a comment is a leak."""
    sub, detail = make_pair(tmp_path, content="# see /mnt/2tb/git/private/thing\n")
    assert publisher.scrub(sub, detail)
    assert publisher.publish(sub, detail) == 1
    assert not publisher.worktree_setup.on_any_remote(sub.mod, sub.pin)


def test_a_credential_shaped_string_refuses(tmp_path):
    sub, detail = make_pair(tmp_path, content='API_KEY = "sk-not-a-real-one"\n')
    assert publisher.publish(sub, detail) == 1


def test_a_non_fast_forward_refuses(tmp_path):
    """Publishing must never rewrite what someone else already has."""
    sub, detail = make_pair(tmp_path)
    # Move the remote on underneath, so the local branch is no longer a
    # fast-forward of it.
    other = tmp_path / "other"
    run(["clone", str(detail["url"]), str(other)], tmp_path)
    for name, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        run(["config", name, value], other)
    (other / "theirs.txt").write_text("theirs\n")
    run(["add", "-A"], other)
    run(["commit", "-m", "theirs"], other)
    run(["push", "origin", "main"], other)
    run(["fetch", "origin"], sub.mod.parent)

    detail = publisher.plan(sub)
    assert detail["fast_forward"] is False
    assert publisher.publish(sub, detail) == 1
