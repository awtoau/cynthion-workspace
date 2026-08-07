#!/usr/bin/env python3
"""Turn off VSCode preview tabs in every Insiders profile.

Preview mode (`workbench.editor.enablePreview`, default true) makes a single
click load the file into ONE reused tab, so each new file — or each Source
Control diff — evicts the last. Profiles each carry their own settings.json,
which shadows the user-level one, so the setting has to be written per profile
or it silently does nothing in whichever profile a workspace is bound to.

Settings files are JSONC (comments allowed), so this edits them textually
rather than round-tripping through json.dumps, which would strip the comments.

Usage:
    scripts/vscode_no_preview_tabs.py [--dry-run]

Backups land in tmp/vscode-profile-backup/, the log in tmp/logs/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "tmp" / "logs"
BACKUP_DIR = ROOT / "tmp" / "vscode-profile-backup"

USER_DIR = Path.home() / ".config" / "Code - Insiders" / "User"

KEYS = {
    "workbench.editor.enablePreview": False,
    "workbench.editor.enablePreviewFromQuickOpen": False,
    "workbench.editor.enablePreviewFromCodeNavigation": False,
}

COMMENT = (
    "A single click loaded the file into one reused \"preview\" tab (italic\n"
    "title), so each new file — and each Source Control diff — evicted the\n"
    "previous one. Off = every click gets its own real tab."
)


class Log:
    """Console and file, per the workspace logging rule."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        print(msg)
        self.fh.write(msg + "\n")
        self.fh.flush()


def strip_jsonc(text: str) -> tuple[str, list[bool]]:
    """Drop // and /* */ comments outside strings.

    Returns the stripped text plus a per-character mask, True where the
    character sits inside a string literal — the trailing-comma pass needs it
    so a comma inside a string value is never mistaken for syntax.
    """
    out: list[str] = []
    mask: list[bool] = []
    i, n = 0, len(text)
    in_str = False

    def emit(ch: str, inside: bool) -> None:
        out.append(ch)
        mask.append(inside)

    while i < n:
        c = text[i]
        if in_str:
            emit(c, True)
            if c == "\\" and i + 1 < n:
                emit(text[i + 1], True)
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            emit(c, False)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        emit(c, False)
        i += 1
    return "".join(out), mask


def parse_jsonc(text: str) -> dict:
    """Parse a VSCode settings file: comments and trailing commas are legal there."""
    stripped, mask = strip_jsonc(text)
    chars = list(stripped)
    for i, c in enumerate(chars):
        if c != "," or mask[i]:
            continue
        j = i + 1
        while j < len(chars) and chars[j].isspace():
            j += 1
        if j < len(chars) and chars[j] in "}]" and not mask[j]:
            chars[i] = " "
    return json.loads("".join(chars))


def detect_indent(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(" ") and line.lstrip().startswith('"'):
            return " " * (len(line) - len(line.lstrip()))
    return "  "


def add_keys(text: str) -> str | None:
    """Insert the keys before the final closing brace. None if already present."""
    if '"workbench.editor.enablePreview"' in text:
        return None

    close = text.rfind("}")
    if close == -1:
        raise ValueError("no closing brace")

    head = text[:close].rstrip()
    tail = text[close:]
    ind = detect_indent(text)

    sep = "" if head.endswith("{") else ","
    block = [f"{ind}// {ln}" for ln in COMMENT.splitlines()]
    block += [f'{ind}"{k}": {json.dumps(v)},' for k, v in KEYS.items()]
    block[-1] = block[-1].rstrip(",")

    return head + sep + "\n" + "\n".join(block) + "\n" + tail


def targets() -> list[Path]:
    found = [USER_DIR / "settings.json"]
    profiles = USER_DIR / "profiles"
    if profiles.is_dir():
        found += sorted(p / "settings.json" for p in profiles.iterdir() if p.is_dir())
    return [p for p in found if p.is_file()]


def profile_names() -> dict[str, str]:
    storage = USER_DIR / "globalStorage" / "storage.json"
    if not storage.is_file():
        return {}
    data = json.loads(storage.read_text(encoding="utf-8"))
    return {p["location"]: p.get("name", "?") for p in data.get("userDataProfiles", [])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = ap.parse_args()

    log = Log(LOG_DIR / "vscode_no_preview_tabs.log")
    names = profile_names()

    if not USER_DIR.is_dir():
        log(f"FAIL: {USER_DIR} does not exist")
        return 1

    changed = skipped = failed = 0
    for path in targets():
        loc = path.parent.name
        label = "<default>" if path.parent == USER_DIR else names.get(loc, loc)

        text = path.read_text(encoding="utf-8")
        try:
            parse_jsonc(text)
        except Exception as exc:  # fail fast, do not write over a file we can't read
            log(f"SKIP {label:20} unparseable before edit: {exc}")
            failed += 1
            continue

        updated = add_keys(text)
        if updated is None:
            log(f"  ok {label:20} already set")
            skipped += 1
            continue

        try:
            parse_jsonc(updated)
        except Exception as exc:
            log(f"FAIL {label:20} edit produced invalid JSON: {exc}")
            failed += 1
            continue

        if args.dry_run:
            log(f" dry {label:20} would add {len(KEYS)} keys")
            changed += 1
            continue

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, BACKUP_DIR / f"{loc}.settings.json")
        path.write_text(updated, encoding="utf-8")
        log(f" set {label:20} {path}")
        changed += 1

    log(f"\nchanged={changed} already-set={skipped} failed={failed}")
    log(f"backups: {BACKUP_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
