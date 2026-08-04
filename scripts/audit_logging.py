#!/usr/bin/env python3
#
# How does each script log, and where does its output actually go? See #175.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reports every logging arrangement in `scripts/`, so the inconsistency is a list
rather than an impression.

    ./scripts/audit_logging.py
    ./scripts/audit_logging.py --unconverted    # only what still needs work

## What it is looking for

Four things, because they fail in different ways:

  * **its own log file.** `LOG = ROOT / "tmp" / "logs" / "<name>.log"` means a
    run is split across as many files as it invoked scripts, and reading a
    failure requires knowing which one to open.
  * **a private emit().** Most scripts here define a local
    `emit(handle, text)` that writes to stdout and to that private file. Same
    shape, fifty-odd copies, no shared format and no origin tag.
  * **uncaptured children.** `subprocess.call` / `subprocess.run` without
    `capture_output` or a pipe hands the child the parent's streams: its output
    reaches the terminal and NO file. This is how a traceback goes missing.
  * **already converted.** Imports `devlog`.

## Why this is a script and not a grep

The distinction that matters is between a `subprocess` call that captures and
one that does not, and that is an argument-shape question rather than a text
question -- `run(cmd, capture_output=True)` is fine and `run(cmd)` is not, and
they differ by a keyword. This walks the AST instead of guessing from a line.

The same mistake in the earlier `audit_scripts.py` -- classifying by name rather
than by reading -- is recorded in that file; this one reads.
"""

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))

from devlog import emit  # noqa: E402

# Calls that hand the child our streams unless told otherwise.
_SPAWNERS = {"call", "run", "Popen", "check_call", "check_output"}

# Keywords that mean the caller took the output rather than letting it through.
_CAPTURING = {"capture_output", "stdout", "stderr"}


class Finding:
    def __init__(self, path):
        self.path = path
        self.own_log = None
        self.private_emit = False
        self.uses_devlog = False
        self.uncaptured = []        # line numbers

    @property
    def converted(self):
        return self.uses_devlog and not self.own_log and not self.uncaptured


def inspect(path):
    """Read one script and say how it logs."""
    finding = Finding(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return finding

    for node in ast.walk(tree):
        # `import devlog` / `from devlog import ...`
        if isinstance(node, ast.Import):
            if any(alias.name == "devlog" for alias in node.names):
                finding.uses_devlog = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "devlog":
                finding.uses_devlog = True

        # `LOG = ROOT / "tmp" / "logs" / "<name>.log"`
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("LOG", "LOGFILE"):
                    text = ast.unparse(node.value)
                    # `endswith`, not `"dev.log" not in text`: that missed
                    # `install_udev.log`, whose own name ends in "dev.log".
                    if "logs" in text and not text.endswith("'dev.log'"):
                        finding.own_log = text

        # A local `emit` that takes a file handle is the private copy.
        elif isinstance(node, ast.FunctionDef) and node.name == "emit":
            if len(node.args.args) >= 2:
                finding.private_emit = True

        # subprocess.<spawner>(...) with nothing catching the output.
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in _SPAWNERS and isinstance(func, ast.Attribute):
                owner = getattr(func.value, "id", "")
                if owner == "subprocess":
                    kwargs = {kw.arg for kw in node.keywords}
                    if not (kwargs & _CAPTURING):
                        finding.uncaptured.append(node.lineno)
    return finding


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--unconverted", action="store_true",
                        help="list only scripts that still need converting")
    args = parser.parse_args()

    findings = [inspect(p) for p in sorted(SCRIPTS.glob("*.py"))
                if p.name not in ("devlog.py", "audit_logging.py")]

    own = [f for f in findings if f.own_log]
    private = [f for f in findings if f.private_emit]
    leaky = [f for f in findings if f.uncaptured]
    done = [f for f in findings if f.converted]

    emit(f"scripts examined: {len(findings)}")
    emit()
    emit(f"  own log file        {len(own):3}   a run is split across this many files")
    emit(f"  private emit()      {len(private):3}   same shape, no shared format or origin")
    emit(f"  uncaptured child    {len(leaky):3}   output reaches the terminal and no file")
    emit(f"  already converted   {len(done):3}   imports devlog")
    emit()

    if args.unconverted:
        for finding in findings:
            if finding.converted:
                continue
            marks = []
            if finding.own_log:
                marks.append("own-log")
            if finding.private_emit:
                marks.append("private-emit")
            if finding.uncaptured:
                marks.append(f"uncaptured:{len(finding.uncaptured)}")
            if marks:
                emit(f"  {finding.path.name:<34} {' '.join(marks)}")
        return 0

    emit("worst offenders -- children spawned with no capture at all:")
    for finding in sorted(leaky, key=lambda f: -len(f.uncaptured))[:10]:
        lines = ", ".join(str(n) for n in finding.uncaptured[:6])
        more = "" if len(finding.uncaptured) <= 6 else f" (+{len(finding.uncaptured) - 6})"
        emit(f"  {finding.path.name:<34} {len(finding.uncaptured):2} at {lines}{more}")
    emit()
    emit(f"log: {ROOT / 'tmp' / 'logs' / 'dev.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
