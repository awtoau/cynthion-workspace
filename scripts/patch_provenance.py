#!/usr/bin/env python3
"""Classify every file in patches/ against the submodule histories.

For each patch we establish, by content rather than by title, which of these it
is:

  landed      - the patch's content is already present in the target tree's
                history; we name the commit.
  superseded  - a different commit achieves the same thing differently.
  unlanded    - real work that was never applied anywhere.
  obsolete    - the patch targets code that no longer exists.

Method (in order of reliability, most reliable first):

  1. ``git patch-id`` on the patch vs ``git patch-id`` on every commit in the
     target repo.  An exact patch-id match is proof the same diff landed,
     regardless of the commit message.  This is the strongest signal available
     and it is title-blind.
  2. ``git apply --check --reverse`` in the target tree.  If the patch applies
     cleanly *in reverse* against the current worktree, its content is present
     in the current tree.
  3. ``git apply --check`` forward.  If it applies forward, the content is
     absent and the patch is still applicable, i.e. genuinely unlanded.
  4. ``git log -S<distinctive line>`` for the added lines the patch introduces.
     Finds content that landed under an unrelated commit message, or reworded.
  5. Existence of the touched paths.  If a patch's target files are all gone,
     it is obsolete.

No timeouts or sleeps are used anywhere: every git invocation is a bounded,
terminating local operation, so there is nothing to wait for.

Writes a machine-readable JSON summary plus a human log to tmp/logs/.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCH_ROOT = REPO_ROOT / "patches"
LOG_DIR = REPO_ROOT / "tmp" / "logs"

AEST = timezone(timedelta(hours=10))

# Which tree each patch directory is written against.  Determined by inspecting
# the a/ and b/ paths inside the patches themselves: patches/cynthion/*.patch
# touch cynthion/python/..., firmware/moondancer/..., awto.md -> repos/cynthion.
TARGETS = {
    "apollo": REPO_ROOT / "repos" / "apollo",
    "cynthion": REPO_ROOT / "repos" / "cynthion",
    "moondancer": REPO_ROOT / "repos" / "cynthion",  # moondancer lives in cynthion
}


def run(args: list[str], cwd: Path, stdin: bytes | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


@dataclass
class Finding:
    patch: str
    target_repo: str
    subject: str = ""
    touched: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    patch_id: str = ""
    patch_id_match: str = ""       # commit whose patch-id is identical
    applies_forward: bool = False
    applies_reverse: bool = False
    log_S_hits: dict = field(default_factory=dict)
    verdict: str = "unknown"
    evidence: list[str] = field(default_factory=list)


def parse_patch(path: Path) -> tuple[str, list[str]]:
    """Return (subject, touched paths) for a patch/diff file."""
    subject = ""
    touched: list[str] = []
    text = path.read_text(errors="replace")
    m = re.search(r"^Subject: (?:\[PATCH[^\]]*\] )?(.*)$", text, re.M)
    if m:
        subject = m.group(1).strip()
    for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)$", text, re.M):
        touched.append(m.group(2))
    return subject, touched


def distinctive_added_lines(path: Path, limit: int = 6) -> list[str]:
    """Pick added lines long and unusual enough to be worth a git log -S.

    Skips blank/short lines, pure punctuation, and generic boilerplate so the
    -S search actually discriminates.
    """
    out: list[str] = []
    for raw in path.read_text(errors="replace").splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:].strip()
        if len(line) < 25:
            continue
        if line.startswith(("#", "//", "*", "-", "=")):
            continue
        if set(line) <= set("+-*/=_ \t{}()[];"):
            continue
        out.append(line)
        if len(out) >= limit * 4:
            break
    # Prefer the longest, they discriminate best.
    out.sort(key=len, reverse=True)
    return out[:limit]


def compute_patch_id(patch: Path, repo: Path) -> str:
    rc, out = run(["git", "patch-id", "--stable"], repo, stdin=patch.read_bytes())
    if rc != 0 or not out.strip():
        return ""
    return out.split()[0]


def build_commit_patch_id_index(repo: Path, max_commits: int = 400) -> dict[str, str]:
    """Map patch-id -> commit sha for the newest max_commits commits.

    max_commits is a work bound, not a time bound: the local patches are all
    dated 2026, and the repo's 2026 work is well inside the newest 400 commits,
    so indexing more would only cost CPU.  On overflow (no match found) the
    caller falls back to git log -S, which searches all history.
    """
    index: dict[str, str] = {}
    rc, out = run(
        ["git", "log", "--format=%H", "-n", str(max_commits), "--all"], repo
    )
    if rc != 0:
        return index
    shas = out.split()
    for sha in shas:
        rc, diff = run(
            ["git", "show", "--format=", "--patch", "--no-color", sha], repo
        )
        if rc != 0:
            continue
        p = subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=str(repo),
            input=diff.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        tok = p.stdout.decode().split()
        if tok:
            index.setdefault(tok[0], sha)
    return index


def classify(patch: Path, repo_name: str, repo: Path, pid_index: dict[str, str]) -> Finding:
    f = Finding(
        patch=str(patch.relative_to(REPO_ROOT)),
        target_repo=str(repo.relative_to(REPO_ROOT)),
    )
    f.subject, f.touched = parse_patch(patch)

    # (5) do the target paths still exist?
    for p in f.touched:
        if not (repo / p).exists():
            f.missing_paths.append(p)

    # (1) patch-id equality - title blind, strongest signal
    f.patch_id = compute_patch_id(patch, repo)
    if f.patch_id and f.patch_id in pid_index:
        f.patch_id_match = pid_index[f.patch_id]
        rc, subj = run(
            ["git", "log", "-1", "--format=%h %ad %s", "--date=short", f.patch_id_match],
            repo,
        )
        f.evidence.append(f"patch-id {f.patch_id[:12]} identical to commit {subj.strip()}")

    # (2)/(3) applicability against the current worktree
    rc, out = run(["git", "apply", "--check", str(patch)], repo)
    f.applies_forward = rc == 0
    if rc == 0:
        f.evidence.append("git apply --check: applies forward cleanly (content absent)")
    else:
        first = out.strip().splitlines()[:2]
        f.evidence.append("git apply --check failed: " + " | ".join(first))

    rc, out = run(["git", "apply", "--check", "--reverse", str(patch)], repo)
    f.applies_reverse = rc == 0
    if rc == 0:
        f.evidence.append(
            "git apply --check --reverse: applies in reverse (content present in worktree)"
        )

    # (4) content search over all history for the distinctive added lines
    for line in distinctive_added_lines(patch):
        rc, out = run(
            ["git", "log", "--all", "--format=%h %s", "-S", line, "--"], repo
        )
        hits = [h for h in out.strip().splitlines() if h] if rc == 0 else []
        f.log_S_hits[line[:90]] = hits[:4]

    any_log_hit = any(v for v in f.log_S_hits.values())

    # Verdict
    if f.patch_id_match:
        f.verdict = "landed"
    elif f.missing_paths and len(f.missing_paths) == len(f.touched):
        f.verdict = "obsolete"
    elif f.applies_reverse and not f.applies_forward:
        f.verdict = "landed"
        f.evidence.append("verdict from clean reverse-apply against current tree")
    elif any_log_hit and not f.applies_forward:
        f.verdict = "landed-or-superseded"
        f.evidence.append("added lines found in history but diff does not match exactly")
    elif f.applies_forward:
        f.verdict = "unlanded"
    else:
        f.verdict = "needs-manual-review"
    return f


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(AEST).strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / "patch_provenance.log"
    json_path = REPO_ROOT / "tmp" / f"patch_provenance-{stamp}.json"

    lines: list[str] = []

    def emit(msg: str) -> None:
        print(msg)
        lines.append(msg)

    emit(f"patch provenance scan {datetime.now(AEST).isoformat(timespec='seconds')}")

    pid_indexes: dict[str, dict[str, str]] = {}
    for name, repo in TARGETS.items():
        key = str(repo)
        if key not in pid_indexes:
            emit(f"indexing patch-ids for {repo.relative_to(REPO_ROOT)} ...")
            pid_indexes[key] = build_commit_patch_id_index(repo)
            emit(f"  {len(pid_indexes[key])} commit patch-ids indexed")

    findings: list[Finding] = []
    for name in sorted(TARGETS):
        d = PATCH_ROOT / name
        if not d.is_dir():
            continue
        repo = TARGETS[name]
        for patch in sorted(d.iterdir()):
            if patch.suffix not in {".patch", ".diff"}:
                continue
            f = classify(patch, name, repo, pid_indexes[str(repo)])
            findings.append(f)
            emit("")
            emit(f"== {f.patch}")
            emit(f"   subject : {f.subject or '(bare diff, no subject)'}")
            emit(f"   target  : {f.target_repo}")
            emit(f"   touched : {', '.join(f.touched) if f.touched else '(none parsed)'}")
            if f.missing_paths:
                emit(f"   MISSING : {', '.join(f.missing_paths)}")
            emit(f"   VERDICT : {f.verdict}")
            for e in f.evidence:
                emit(f"     - {e}")
            for line, hits in f.log_S_hits.items():
                if hits:
                    emit(f"     -S {line!r}")
                    for h in hits:
                        emit(f"        {h}")

    emit("")
    emit("=== summary ===")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    for verdict, n in sorted(counts.items()):
        emit(f"  {verdict:22s} {n}")
    emit(f"  {'TOTAL':22s} {len(findings)}")

    log_path.write_text("\n".join(lines) + "\n")
    json_path.write_text(json.dumps([asdict(f) for f in findings], indent=2) + "\n")
    print(f"\nlog  -> {log_path.relative_to(REPO_ROOT)}")
    print(f"json -> {json_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
