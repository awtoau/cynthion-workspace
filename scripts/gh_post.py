#!/usr/bin/env python3
#
# One command to file on the tracker, with the scrub in the path.
# SPDX-License-Identifier: BSD-3-Clause

"""File an issue, a comment or a close on the tracker. The scrub is not optional.

    ./scripts/gh_post.py issue   --title "..." --body-file tmp/x.md --label p0
    ./scripts/gh_post.py comment 498 --body-file tmp/x.md
    ./scripts/gh_post.py close   498 --body-file tmp/x.md --reason completed
    ./scripts/gh_post.py comment 498 --body-file tmp/x.md --dry-run

Prints the URL of what it created. Records to `tmp/logs/dev.log` like everything
else here.

## Why this exists

Filing anything used to be three round trips re-derived per agent: read a memory
file to learn whether this repo is pre-approved, hand-roll a `grep` scrub that
was only as good as that agent's regex, then `gh issue comment`. The scrub is the
part that matters and it was the part being reinvented. Here it runs on every
field, before any network call, and a hit files nothing.

## What it refuses

* **Private paths** -- absolute paths under the per-machine roots. The patterns
  are imported from `private_path_check.py`, which already enforces this on every
  tracked file as the `paths` check, so there is one set to keep right rather
  than two that drift.
* **Credentials** -- known token shapes, and `name = value` where the name is
  password/secret/token/api-key and the value is not a placeholder. A bare
  mention of the word "token" is prose, and is allowed; refusing it would train
  everyone to work around this.
* **The reverse-engineering work** that stays off a public tracker.

## Which repo

`awtoau/cynthion-workspace` is pre-approved and is the default: file directly, no
confirmation. **Every other repo is refused**, and the refusal names the approval
that would be needed. This script cannot grant it -- that is the point. See
`docs/README.md` and the workspace's agent rules for the tiering itself.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import private_path_check as ppc  # noqa: E402
from devlog import emit, log  # noqa: E402

# The one repo where filing needs no confirmation, and the default target.
PREAPPROVED = "awtoau/cynthion-workspace"

# Token shapes that are a credential wherever they appear.
TOKEN_SHAPES = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"), "API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]

# `name = value` / `name: value`. The name alone is prose; the pair is a leak.
ASSIGNMENT = re.compile(
    r"(?i)\b(pass(?:word|wd)|secret|token|api[-_ ]?key|access[-_ ]?key)"
    r"\s*[:=]\s*[\"']?([^\s\"']+)")

# Values that carry nothing: an env var, an angle-bracket slot, a mask, a word.
PLACEHOLDER = re.compile(
    r"(?i)^(<.*>|\$\{?\w+\}?|\{\{?\w+\}?\}|[*x.\-_]+|none|null|redacted|"
    r"changeme|your[-_]?\w*)$")
MIN_SECRET = 6                          # shorter than this is a label, not a key

# The RE work is not named on a public tracker. Keep the abstract subject.
SENSITIVE = [
    (re.compile(r"(?i)\b(hantek|v07)\b"),
     "names the reverse-engineering work; keep it generic"),
]


class Refusal(Exception):
    """Something did not pass the gate. Nothing has been filed."""


def scrub_text(where: str, text: str) -> list[str]:
    """Every reason `text` must not be published, as `where:line: why` lines."""
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        # URLs blanked first, exactly as the tracked-file check does it: a
        # datasheet link carrying a path-shaped component is a citation.
        scanned = ppc.URL.sub(lambda m: " " * len(m.group(0)), line)

        for hit in ppc.PRIVATE.finditer(scanned):
            problems.append(f"{where}:{number}: private path -- {hit.group(0)}\n"
                            f"      {line.strip()[:120]}")
        for pattern, why in TOKEN_SHAPES + SENSITIVE:
            for hit in pattern.finditer(scanned):
                problems.append(f"{where}:{number}: {why} -- {hit.group(0)[:24]}\n"
                                f"      {line.strip()[:120]}")
        for hit in ASSIGNMENT.finditer(scanned):
            # Markdown and prose punctuation stripped, or `token=<yours>,`
            # reads as a seven-character secret.
            value = hit.group(2).strip("`.,;:")
            if len(value) < MIN_SECRET or PLACEHOLDER.match(value):
                continue
            problems.append(f"{where}:{number}: credential assignment -- "
                            f"{hit.group(1)}\n      {line.strip()[:120]}")
    return problems


def read_body(path: Path) -> str:
    if not path.is_file():
        raise Refusal(f"body file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise Refusal(f"body file is empty: {path}")
    return text


def check_repo(repo: str) -> None:
    """Pre-approved repo, or a refusal naming the approval that is missing."""
    if repo == PREAPPROVED:
        return
    raise Refusal(
        f"{repo} is not pre-approved, and this script cannot grant the approval.\n"
        f"  only {PREAPPROVED} may be filed on without asking.\n"
        "\n"
        "  a public repo we own : confirm the action and the target, THEN get a\n"
        "                        second approval on the final text\n"
        "  someone else's repo  : both of those, PLUS explicit confirmation it\n"
        "                        is going upstream and not to a fork\n"
        "\n"
        "  Ask for those, then run `gh` directly with what was approved.")


def gate(repo: str, fields: dict[str, str]) -> None:
    """The whole gate: the repo tier, then every field. Raises or returns."""
    check_repo(repo)
    problems = [p for where, text in fields.items()
                for p in scrub_text(where, text)]
    if problems:
        raise Refusal(
            "this tracker is public.\n\n"
            + "\n".join(f"  {p}" for p in problems)
            + f"\n\n  {len(problems)} problem(s); nothing was filed.")


def run_gh(argv: list[str], *, dry_run: bool) -> int:
    if dry_run:
        emit("dry run, would run: gh " + " ".join(argv))
        return 0
    if not shutil.which("gh"):
        raise Refusal("gh is not on PATH")
    log("gh " + " ".join(argv))
    done = subprocess.run(["gh", *argv], capture_output=True, text=True)
    if done.returncode:
        emit(done.stderr.strip())
        log(f"gh failed rc={done.returncode}", "ERROR")
        return done.returncode
    if done.stdout.strip():
        emit(done.stdout.strip())
    return 0


def label_args(labels: list[str]) -> list[str]:
    return [arg for name in labels for arg in ("--label", name)]


def cmd_issue(args) -> int:
    body = read_body(Path(args.body_file))
    gate(args.repo, {"title": args.title, str(args.body_file): body,
                     "labels": " ".join(args.label)})
    return run_gh(["issue", "create", "--repo", args.repo,
                   "--title", args.title, "--body-file", str(args.body_file),
                   *label_args(args.label)], dry_run=args.dry_run)


def cmd_comment(args) -> int:
    body = read_body(Path(args.body_file))
    gate(args.repo, {str(args.body_file): body, "labels": " ".join(args.label)})
    rc = run_gh(["issue", "comment", str(args.number), "--repo", args.repo,
                 "--body-file", str(args.body_file)], dry_run=args.dry_run)
    return rc or add_labels(args)


def cmd_close(args) -> int:
    fields = {"labels": " ".join(args.label)}
    body = read_body(Path(args.body_file)) if args.body_file else None
    if body is not None:
        fields[str(args.body_file)] = body
    gate(args.repo, fields)
    rc = add_labels(args)
    if body is not None:
        rc = rc or run_gh(["issue", "comment", str(args.number),
                           "--repo", args.repo, "--body-file",
                           str(args.body_file)], dry_run=args.dry_run)
    rc = rc or run_gh(["issue", "close", str(args.number), "--repo", args.repo,
                       "--reason", args.reason], dry_run=args.dry_run)
    if rc or args.dry_run:
        return rc
    # `gh` exits 0 on a close that GitHub's secondary rate limit silently
    # dropped, so the exit code is not evidence. Ask for the state back.
    if not closed(args.number, args.repo):
        emit(f"REFUSED: #{args.number} is still OPEN after `gh issue close` "
             f"exited 0 -- most likely GitHub's secondary rate limit. Re-run.")
        return 1
    emit(f"https://github.com/{args.repo}/issues/{args.number}")
    return rc


def closed(number: int, repo: str) -> bool:
    """The issue's state, read back rather than inferred from an exit code."""
    out = subprocess.run(["gh", "issue", "view", str(number), "--repo", repo,
                          "--json", "state", "--jq", ".state"],
                         capture_output=True, text=True)
    return out.stdout.strip().upper() == "CLOSED"


def add_labels(args) -> int:
    """`--label` on an existing issue. p0 is the reason this is here."""
    if not args.label:
        return 0
    return run_gh(["issue", "edit", str(args.number), "--repo", args.repo,
                   *[a for name in args.label
                     for a in ("--add-label", name)]], dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    # Shared options live on the subcommands only. Declared on both, argparse
    # lets the subparser's default overwrite what the parent parsed -- which
    # would silently redirect `--repo` back to the pre-approved one.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=PREAPPROVED,
                        help=f"target repo (default {PREAPPROVED})")
    common.add_argument("--label", action="append", default=[],
                        help="label to set; repeatable")
    common.add_argument("--dry-run", action="store_true",
                        help="scrub and print the gh command; file nothing")

    subparsers = parser.add_subparsers(dest="what", required=True)

    new = subparsers.add_parser("issue", parents=[common],
                                help="open an issue")
    new.add_argument("--title", required=True)
    new.add_argument("--body-file", required=True)
    new.set_defaults(fn=cmd_issue)

    say = subparsers.add_parser("comment", parents=[common],
                                help="comment on an issue")
    say.add_argument("number", type=int)
    say.add_argument("--body-file", required=True)
    say.set_defaults(fn=cmd_comment)

    end = subparsers.add_parser("close", parents=[common],
                                help="comment, then close an issue")
    end.add_argument("number", type=int)
    end.add_argument("--body-file")
    end.add_argument("--reason", default="completed",
                     choices=["completed", "not planned"])
    end.set_defaults(fn=cmd_close)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Refusal as why:
        emit(f"REFUSED: {why}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
