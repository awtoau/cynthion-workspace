#!/usr/bin/env python3
"""Apply a triage manifest to GitHub issues: one comment, and optionally a close.

Why a tool rather than a shell loop: a triage pass touches ~60 issues, each with a
multi-line body. `gh issue comment` per issue by hand is untraceable and half-applied
runs cannot be resumed. This records every action to a log and refuses to repeat one.

Manifest is Markdown, not JSON — the bodies ARE Markdown and escaping them twice is
how a table ends up posted with literal `\n` in it. One section per issue:

    ### 341 comment
    ...body...

    ### 306 close: the netlist race is fixed, and each cause now has its own issue

    ### 199 label: p1 needs-hardware-test

`action` is `comment`, `close` or `label`. For the first two, everything after the colon
is the reason line, bolded at the top of the body, so a closing comment always says why.
For `label` it is the space-separated labels to add, and the body is a note to the log
rather than a comment — labels do not need explaining twice.

    scripts/gh_triage_apply.py <manifest.md> [--repo owner/name] [--dry-run]

Log: tmp/logs/gh_triage_apply.log (append). Applied numbers are read back from it, so a
re-run after a failure skips what already landed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "gh_triage_apply.log"
DEFAULT_REPO = "awtoau/cynthion-workspace"

# `gh` talks to github.com over the network. 1.25x is not derivable from an
# interface's arithmetic here, so this is a plain ceiling: a comment post that has
# not completed in 60 s is a network fault, not a slow post, and the run should say
# so rather than hang a 60-issue pass on one call.
GH_TIMEOUT_S = 60


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(f"{now()} {line}\n")
    print(line, flush=True)


def already_applied() -> set[tuple[int, str]]:
    """Keyed on (issue, action): a labelling pass must not be skipped by an earlier comment."""
    if not LOG.exists():
        return set()
    done = set()
    for line in LOG.read_text().splitlines():
        if " APPLIED " in line:
            fields = line.split(" APPLIED ")[1].split()
            done.add((int(fields[0]), fields[1]))
    return done


HEADING = re.compile(r"^###\s+(\d+)\s+(comment|close|label)\s*(?::\s*(.*))?$")


def parse_manifest(text: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            current = {
                "number": int(match.group(1)),
                "action": match.group(2),
                "reason": (match.group(3) or "").strip(),
                "lines": [],
            }
            entries.append(current)
            continue
        if current is not None:
            current["lines"].append(line)
    for entry in entries:
        entry["body"] = "\n".join(entry.pop("lines")).strip()
        if entry["action"] == "label":
            if not entry["reason"]:
                raise ValueError(f"issue {entry['number']} names no label")
        elif not entry["body"]:
            raise ValueError(f"issue {entry['number']} has an empty body")
    return entries


def run_gh(args: list[str], body: str | None) -> None:
    result = subprocess.run(
        ["gh", *args],
        input=body,
        text=True,
        capture_output=True,
        timeout=GH_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed rc={result.returncode}: {result.stderr.strip()}")


def apply_one(entry: dict, repo: str, dry_run: bool) -> None:
    number = int(entry["number"])
    action = entry["action"]

    if action == "label":
        labels = entry["reason"].split()
        if dry_run:
            log(f"DRYRUN {number} label {' '.join(labels)}")
            return
        args = ["issue", "edit", str(number), "--repo", repo]
        for label in labels:
            args += ["--add-label", label]
        run_gh(args, None)
        log(f"APPLIED {number} label {' '.join(labels)}")
        return

    body = entry["body"].rstrip() + "\n"
    if entry.get("reason"):
        body = f"**{entry['reason']}**\n\n{body}"

    if dry_run:
        log(f"DRYRUN {number} {action} ({len(body)} chars)")
        return

    run_gh(["issue", "comment", str(number), "--repo", repo, "--body-file", "-"], body)
    if action == "close":
        run_gh(["issue", "close", str(number), "--repo", repo], None)
    log(f"APPLIED {number} {action}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = parse_manifest(args.manifest.read_text())
    done = already_applied()
    log(f"START {args.manifest.name} {len(entries)} entries, {len(done)} already applied")

    failures = 0
    for entry in entries:
        if (int(entry["number"]), entry["action"]) in done:
            log(f"SKIP {entry['number']} {entry['action']} already applied")
            continue
        try:
            apply_one(entry, args.repo, args.dry_run)
        except Exception as error:  # one bad issue must not abandon the rest
            failures += 1
            log(f"FAILED {entry['number']} {error}")

    log(f"DONE {len(entries)} entries, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
