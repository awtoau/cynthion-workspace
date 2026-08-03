#!/usr/bin/env python3
#
# Audit scripts/: read every one, and work out what still calls it.
# SPDX-License-Identifier: BSD-3-Clause

"""
What each script in `scripts/` is, and whether anything still reaches it.

    ./dev.py audit                 # the table, to stdout and tmp/logs/
    ./dev.py audit -- --markdown   # the same as a markdown table

## Why this is a tool and not a judgement

`scripts/` holds 167 files. Classifying them by name prefix is worthless -- a
prefix says what a file was called, not whether anything calls it -- and reading
167 files by hand produces an opinion that is stale the day after it is written.

So this reads them. For each file it recovers:

  * **what it says it is** -- the first line of its module docstring, or its
    `argparse` description, which is the author's own summary;
  * **what it reaches** -- every other script it imports or invokes, found by
    walking the AST for imports and for string literals that name a script;
  * **what reaches it** -- every mention of its filename anywhere in the tree
    that is not itself and not a generated artifact;
  * **when it was last touched**, from git.

From that, reachability is computed rather than guessed: `dev.py` is the root,
anything it names is live, anything those name is live, transitively.

## The three classifications, and why "orphan" is not "delete"

  * **live** -- reachable from `dev.py`. These are load-bearing.
  * **cited** -- not reachable, but named by a doc, a test, or a comment. Some
    of these are reference material whose value is the finding, not the run.
  * **orphan** -- nothing anywhere mentions it.

An orphan is a candidate for `debris/scripts/` or deletion, and which one it is
still needs a human. The workspace rule is that non-regenerable content is
retired to `debris/` and regenerable content is deleted; this tool cannot tell
which a probe script is, because that depends on what it cost to write. It marks
the candidates and stops there.

## The false-negative this cannot fix

A script invoked only by a shell command in a comment, a note, or someone's
memory reads as an orphan here. That is the correct output -- it is unreachable
by any means the repo records -- but it is why the classification is evidence for
a decision rather than the decision.
"""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOG = ROOT / "tmp" / "logs" / "audit_scripts.log"

# The root of reachability. Everything live is live because this names it, or
# because something this names does.
ENTRY = "dev.py"

# Directories whose contents are not evidence of anything: build output, vendored
# upstream trees, scratch, and the archive itself.
#
# `worktrees` is the one that mattered. `.claude/worktrees/` held eleven agent
# worktrees, each a full copy of the tree, so every script was "cited by" its own
# copies and 88 files looked referenced when almost none were. A citation from a
# copy of the repo is not a citation. `.claude` itself stays searchable, because
# commands and skills there do name scripts for real.
IGNORE_DIRS = {"tmp", "build", "repos", "target", ".git", "__pycache__",
               "debris", "node_modules", ".pytest_cache", "worktrees"}

# Files worth searching for a mention. Source, docs and config -- not binaries.
TEXT_SUFFIXES = {".py", ".md", ".rs", ".toml", ".json", ".sh", ".yml", ".yaml",
                 ".x", ".txt", ".cfg"}

# A mention from one of these is a caller: something could execute this script
# without a human reading prose first. A mention from anything else -- which in
# practice means a `.md` -- records that the script once RAN, not that anything
# runs it. That is the distinction between `called` and `documented`, and it is
# the one that matters: a one-off measurement is cited forever by the doc holding
# its result, so counting doc mentions as references keeps dead tools alive.
CODE_SUFFIXES = {".py", ".rs", ".sh", ".toml", ".json", ".yml", ".yaml", ".cfg"}


def summary_of(path):
    """The author's own one-line summary of the file.

    Module docstring first, then an `argparse` description, then the first
    comment line. Three fallbacks because the repo uses all three styles and a
    file with no summary at all is itself a finding.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<unreadable>"

    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text))
        except SyntaxError:
            doc = None
        if doc:
            return first_sentence(doc)
        match = re.search(r'description\s*=\s*["\']([^"\']{10,})', text)
        if match:
            return first_sentence(match.group(1))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!") \
                and "SPDX" not in stripped and len(stripped) > 12:
            return first_sentence(stripped.lstrip("# "))
    return ""


def first_sentence(text):
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line[:96]
    return ""


def references_from(path, names):
    """Which other scripts this file names, by import or by string.

    Both forms are used here: `soc_run.py` invokes `soc_test.py` as a
    subprocess with a path built from a string, while `check.py` imports
    helpers. A grep for the bare filename catches the first; the AST catches the
    second, including `from x import y` where the string never appears.
    """
    found = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found

    for name in names:
        if name == path.name:
            continue
        if name in text:
            found.add(name)
            continue
        # A script named WITHOUT its suffix, as a quoted module name.
        #
        # `soc_sims.py` holds `SIMS = ["soc_bus_sim", "soc_clint_sim", ...]` and
        # imports each one, so a search for "soc_clint_sim.py" finds nothing and
        # three simulations that run on every `./dev.py sim` were reported as
        # orphans. Quoted-exact rather than bare, because stems like `check` and
        # `install` are ordinary words and an unquoted match would make almost
        # everything look reachable.
        stem = name[:-3] if name.endswith(".py") else name
        if f'"{stem}"' in text or f"'{stem}'" in text:
            found.add(name)

    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return found
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if f"{module}.py" in names:
                        found.add(f"{module}.py")
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
                if f"{module}.py" in names:
                    found.add(f"{module}.py")
    return found


def repo_files():
    """Every text file that could mention a script."""
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in IGNORE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def last_touched(path):
    """`YYYY-MM-DD` of the last commit touching this file, or `untracked`."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(path.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() or "untracked"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markdown", action="store_true",
                        help="emit a markdown table, for pasting into an issue")
    parser.add_argument("--only",
                        choices=["live", "called", "documented", "orphan"],
                        help="report one classification")
    args = parser.parse_args()

    scripts = sorted(p for p in SCRIPTS.iterdir()
                     if p.is_file() and p.suffix in (".py", ".sh"))
    names = {p.name for p in scripts}
    # dev.py at the root is the entry point and is not itself in scripts/ twice;
    # the shim there and the real one under scripts/ share a name, which is
    # deliberate and would otherwise look like a self-reference.
    names.add(ENTRY)

    # Who each script names.
    reaches = {p.name: references_from(p, names) for p in scripts}
    # The root shim and `scripts/dev.py` share a filename, deliberately -- one is
    # three lines that import the other. So their references must be UNIONED into
    # the single entry, not assigned: assigning lost every step the real dev.py
    # names and reported one live script out of 171.
    root_shim = ROOT / ENTRY
    if root_shim.exists():
        reaches[ENTRY] = reaches.get(ENTRY, set()) | references_from(root_shim, names)

    # Who names each script, from the whole tree.
    named_by = {name: set() for name in names}
    for path in repo_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        where = str(path.relative_to(ROOT))
        for name in names:
            if name in text and path.name != name:
                named_by[name].add(where)

    # Reachability from the entry point, transitively. Computed, not assumed:
    # `dev.py` names five scripts and those name a dozen more, and the whole
    # point is that nobody has that closure in their head.
    live = set()
    frontier = [ENTRY]
    while frontier:
        current = frontier.pop()
        for name in reaches.get(current, ()):
            if name not in live:
                live.add(name)
                frontier.append(name)

    rows = []
    for path in scripts:
        name = path.name
        if name in live:
            kind = "live"
        elif any(Path(w).suffix in CODE_SUFFIXES for w in named_by[name]):
            kind = "called"
        elif named_by[name]:
            kind = "documented"
        else:
            kind = "orphan"
        rows.append({
            "name": name,
            "kind": kind,
            "size": path.stat().st_size,
            "touched": last_touched(path),
            "cited_by": sorted(named_by[name]),
            "summary": summary_of(path),
        })

    if args.only:
        rows = [r for r in rows if r["kind"] == args.only]

    lines = []
    counts = {"live": 0, "called": 0, "documented": 0, "orphan": 0}
    for row in rows:
        counts[row["kind"]] += 1

    if args.markdown:
        lines.append("| script | state | last commit | KiB | what it says it is |")
        lines.append("|---|---|---|---:|---|")
        for row in sorted(rows, key=lambda r: (r["kind"], r["name"])):
            lines.append(f"| `{row['name']}` | {row['kind']} | {row['touched']} "
                         f"| {row['size'] // 1024} | {row['summary']} |")
    else:
        for row in sorted(rows, key=lambda r: (r["kind"], r["name"])):
            lines.append(f"{row['kind']:6} {row['touched']}  {row['size'] // 1024:4} KiB  "
                         f"{row['name']}")
            if row["summary"]:
                lines.append(f"                                    {row['summary']}")
            if row["kind"] in ("called", "documented"):
                lines.append(f"                                    cited by: "
                             f"{', '.join(row['cited_by'][:4])}")

    lines.append("")
    lines.append(f"{len(rows)} scripts: {counts['live']} live, "
                 f"{counts['called']} called, {counts['documented']} documented, "
                 f"{counts['orphan']} orphan")
    lines.append(f"live = reachable from ./{ENTRY}; called = named by other code; "
                 f"documented = named only by prose; orphan = nothing mentions it")
    lines.append("documented and orphan are the retirement candidates: nothing "
                 "can run them without a human reading a doc first.")

    text = "\n".join(lines)
    print(text)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(text + "\n")
    print(f"\nlog: {LOG.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
