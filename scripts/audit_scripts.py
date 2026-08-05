#!/usr/bin/env python3
#
# Audit scripts/: read every one, and work out what still calls it.
# SPDX-License-Identifier: BSD-3-Clause

"""
What each script in `scripts/` is, and whether anything still reaches it.

    ./dev.py audit                 # the table, to stdout and tmp/logs/
    ./dev.py audit -- --markdown   # the same as a markdown table
    ./dev.py audit -- --logging    # how the scripts LOG, via audit_logging.py

## Why this is a tool and not a judgement

`scripts/` holds 87 files. Classifying them by name prefix is worthless -- a
prefix says what a file was called, not whether anything calls it -- and reading
87 files by hand produces an opinion that is stale the day after it is written.

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

## The four classifications, and why none of them is "delete"

  * **live** -- reachable from `dev.py`, transitively. Load-bearing.
  * **called** -- imported or spawned by other code, but not from `dev.py`.
  * **documented** -- named only by prose. A spent probe lands here, and so
    does a top-level tool a human runs from a README: the two are
    indistinguishable to this program and the difference is the whole judgement.
  * **orphan** -- nothing anywhere mentions it.

The workspace rule is that non-regenerable content is retired to `debris/` and
regenerable content is deleted; this tool cannot tell which a probe script is,
because that depends on what it cost to write. It marks candidates and stops.

## What counts as a call, and what stopped counting

`called` used to be `if name in text`, which counted a comment explaining what a
script had measured. Prose about a tool outlives the tool, so that kept spent
tools alive by definition -- and worse, it manufactured `live`: `install.py` was
reachable from `./dev.py` because `machine_setup.py` passes `"install"` to `dnf`.
56 KiB of installer nothing had run in months, wearing a load-bearing badge.

So for Python the evidence is the AST: imports, and string literals that are not
docstrings. A bare stem counts only if it has an underscore, which every script
named that way does and no argv verb does. Comments never reach the AST at all.

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

sys.path.insert(0, str(SCRIPTS))

from devlog import emit  # noqa: E402

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


def docstring_nodes(tree):
    """Every string node that is a docstring, so prose is not read as code.

    A module, class or function docstring is an `ast.Constant` like any other
    string literal, and it is exactly where one script explains what another one
    measured. Collected by identity so the string-literal pass below can skip it.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


# Line-comment syntax per language, for the non-Python callers. JSON has none.
LINE_COMMENT = {".rs": ("//",), ".toml": ("#",), ".yml": ("#",),
                ".yaml": ("#",), ".cfg": ("#",), ".sh": ("#",)}


def without_comments(text, suffix):
    """The same exclusion Python gets from its AST, for the languages without one.

    Rust doc comments are where this repo records which probe produced a number,
    so `firmware/cynthion-soc/src/ulpi.rs` "reached" `phy_probe.py` by saying
    what it had confirmed. `Cargo.toml` does it too. Crude -- it will cut a URL
    in half -- which costs nothing, because the only thing being looked for is a
    script filename and no invocation follows a comment marker on its own line.
    """
    markers = LINE_COMMENT.get(suffix)
    if not markers:
        return text
    out = []
    for line in text.splitlines():
        for marker in markers:
            index = line.find(marker)
            if index != -1:
                line = line[:index]
        out.append(line)
    return "\n".join(out)


def references_from(path, names):
    """Which other scripts this file INVOKES -- not which it mentions.

    This used to be `if name in text`, and that is the bug #157 records: a
    comment explaining what a script had measured counted as a call, so `called`
    was an upper bound nobody could act on. It was worse than the issue said --
    `install.py` was classified LIVE off a docstring example in
    `logging_utils.py` and a "mirrors install.py" comment in `check.py`. Nothing
    has run it in months.

    So for Python the evidence is now the AST only:

      * `import x` / `from x import y`, resolved against the script names;
      * a string LITERAL naming the script -- a subprocess argv, or a bare
        module name as in `soc_sims.py`'s `SIMS = ["soc_bus_sim", ...]`.

    Comments never reach the AST, and docstrings are skipped explicitly. For a
    non-Python caller (a `.toml` or a `.json` job file) there is no AST, so the
    substring stands -- those formats have no comment syntax to be fooled by.
    """
    found = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    others = [n for n in names if n != path.name]
    stems = {n: (n[:-3] if n.endswith(".py") else n) for n in others}

    if path.suffix != ".py":
        stripped = without_comments(text, path.suffix)
        for name in others:
            if name in stripped:
                found.add(name)
        return found

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    skip = docstring_nodes(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = f"{alias.name.split('.')[0]}.py"
                if module in names:
                    found.add(module)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = f"{node.module.split('.')[0]}.py"
            if module in names:
                found.add(module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in skip:
            for name in others:
                if name in node.value:
                    found.add(name)
                elif node.value == stems[name] and bare_stem_is_evidence(stems[name]):
                    found.add(name)
    return found


def bare_stem_is_evidence(stem):
    """Whether a literal equal to `stem` alone may be read as naming a script.

    `soc_sims.py` holds `SIMS = ["soc_bus_sim", ...]` and builds each path from
    it, so the bare stem has to count or fifteen live simulations read as
    orphans. But `machine_setup.py` holds `["sudo", manager, "install", "-y"]`,
    and that made `install.py` -- which nothing has run in months -- reachable
    from `./dev.py`, which is how a 56 KiB dead installer kept a `live` badge.

    An underscore is the whole difference: every script named this way has one,
    and no argv verb does.
    """
    return "_" in stem


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
    parser.add_argument("--logging", action="store_true",
                        help="audit how the scripts LOG instead (audit_logging.py)")
    args, rest = parser.parse_known_args()

    # `audit_logging.py` was the one orphan in this table, and it audits the
    # same directory for a different property. Reached through here it is
    # discoverable from `./dev.py audit --logging` without a second door.
    if args.logging:
        import audit_logging
        sys.argv = ["audit_logging.py", *rest]   # in-process: no child to capture
        return audit_logging.main()
    if rest:
        parser.error(f"unrecognised arguments: {' '.join(rest)}")

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

    # Who names each script, from the whole tree, in the two ways that differ.
    #
    # `invoked_by` is the same AST evidence `reaches` uses, so `called` means
    # code really runs this. `named_by` stays a plain substring scan, because
    # `documented` is a claim about PROSE and prose is exactly what a substring
    # finds. Deciding `called` from the substring scan is what let `install.py`
    # look like code was calling it when the four citations were all Markdown.
    named_by = {name: set() for name in names}
    invoked_by = {name: set() for name in names}
    for path in repo_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        where = str(path.relative_to(ROOT))
        for name in names:
            if name in text and path.name != name:
                named_by[name].add(where)
        if path.suffix in CODE_SUFFIXES:
            for name in references_from(path, names):
                if path.name != name:
                    invoked_by[name].add(where)

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
        elif invoked_by[name]:
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
            "cited_by": sorted(invoked_by[name] if kind == "called"
                               else named_by[name]),
            "summary": summary_of(path),
        })

    # DANGLING DEPENDENCIES: live code importing something already archived.
    #
    # The check above cannot see these. `names` is built from what is IN scripts/,
    # so once a file moves to debris/ it stops being a name anything is searched
    # for -- and a live script that imports it looks perfectly reachable while
    # being broken. That is not hypothetical: this sweep archived eight scripts
    # that live code still needed, and each one surfaced as a crash at the moment
    # someone ran the tool that needed it, not when it was moved.
    # Same evidence as `references_from`, and for the same reason: matching the
    # filename in the text flagged `soc_board_sim.py` for a comment citing the
    # register values `phy_probe.py` had read. A false alarm here blocks a
    # retirement that is correct, which is the opposite of what this is for.
    archived = {q.name for q in (ROOT / "debris" / "scripts").glob("*.py")}
    dangling = {}
    for path in scripts:
        for name in references_from(path, archived):
            dangling.setdefault(name, []).append(path.name)

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

    if dangling:
        lines.append("")
        lines.append("DANGLING: live scripts referencing archived ones --")
        for name, users in sorted(dangling.items()):
            lines.append(f"  debris/scripts/{name} <- {', '.join(sorted(users))}")
        lines.append("  recover these, or the tools above them are broken.")

    lines.append("")
    lines.append(f"{len(rows)} scripts: {counts['live']} live, "
                 f"{counts['called']} called, {counts['documented']} documented, "
                 f"{counts['orphan']} orphan")
    lines.append(f"live = reachable from ./{ENTRY}; called = imported or spawned "
                 f"by other code; documented = named only by prose; "
                 f"orphan = nothing mentions it")
    lines.append("documented is NOT the retirement list -- a top-level tool a "
                 "human runs from a README lands here too. It is the list of "
                 "things nothing can reach without reading prose first, so each "
                 "one is either a promotion into ./dev.py or a retirement.")

    for line in lines:
        emit(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
