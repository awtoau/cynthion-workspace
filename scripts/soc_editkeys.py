#!/usr/bin/env python3
#
# Drive the board's line editor key by key and check what the line becomes.
# SPDX-License-Identifier: BSD-3-Clause

"""
Type a key sequence at the board and assert the resulting command line.

    ./scripts/soc_editkeys.py            # every case
    ./scripts/soc_editkeys.py --verbose  # and the raw bytes echoed back

Results are mirrored to ./tmp/logs/soc_editkeys.log.

## Why this is not in soc_test.py

`soc_test.py` sends whole lines and reads replies. It cannot see an editing key
at all: backspace, Delete, Home and Ctrl-W never reach it, because a line that
arrives at the dispatcher has already been edited. That blind spot is how the
`0x7f` defect survived -- pressing Backspace on a terminal that sends DEL
INSERTED a DEL character, and every line-level test passed while it did.

## How a key is checked without reading the terminal's screen

The board echoes ANSI as it edits, so the screen state is not directly
readable. Instead each case types its keys, then presses Enter and reads the
error the dispatcher prints:

    unknown command; try `help`

...is useless, but the shell echoes the line it received first. So the
assertion is on what the board says it was given, which is the thing under
test -- not on a reconstruction of the escape codes.

Cases that would run a real command are spelled with a nonsense prefix so
nothing on the board is touched.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
import soc_shell  # noqa: E402

LOG = ROOT / "tmp" / "logs" / "soc_editkeys.log"

ESC = b"\x1b"
LEFT, RIGHT = ESC + b"[D", ESC + b"[C"
HOME, END = ESC + b"[H", ESC + b"[F"
DEL = ESC + b"[3~"
BS, DEL7 = b"\x08", b"\x7f"
CTRL_A, CTRL_E, CTRL_K, CTRL_U, CTRL_W = b"\x01\x05\x0b\x15\x17"[0:1], b"\x05", b"\x0b", b"\x15", b"\x17"

# (name, keys typed, what the line should be when Enter is pressed)
#
# `zz` prefixes keep every case an unknown command, so the board parses the line
# and echoes it back without doing anything.
CASES = [
    ("backspace 0x08", b"zzabc" + BS, "zzab"),
    ("backspace 0x7f (DEL byte)", b"zzabc" + DEL7, "zzab"),
    ("left then insert", b"zzac" + LEFT + b"b", "zzabc"),
    ("Delete removes under cursor", b"zzabc" + LEFT + LEFT + DEL, "zzac"),
    ("Delete at end of line does nothing", b"zzab" + DEL, "zzab"),
    ("Home then insert", b"zabc" + HOME + b"z", "zzabc"),
    ("End after Home", b"zzabc" + HOME + END + b"d", "zzabcd"),
    ("Ctrl-A is Home", b"zabc" + CTRL_A + b"z", "zzabc"),
    ("Ctrl-E is End", b"zzabc" + CTRL_A + CTRL_E + b"d", "zzabcd"),
    ("Ctrl-K kills to end", b"zzabcdef" + LEFT + LEFT + LEFT + CTRL_K, "zzabc"),
    ("Ctrl-U kills to start", b"zzabc" + CTRL_U + b"zz", "zz"),
    ("Ctrl-W kills a word", b"zzab cdef" + CTRL_W, "zzab "),
    ("Ctrl-W over trailing spaces", b"zzab cd   " + CTRL_W, "zzab "),
]


def run(link, keys, budget_s):
    """Type `keys`, press Enter, and return everything the board sent back."""
    mark_before = link.read_until_prompt(budget_s=0.2)  # drain
    del mark_before
    link.write(keys + b"\r")
    return link.read_until_prompt(budget_s=budget_s)


def line_from(reply, keys):
    """The command line the board echoed, stripped of ANSI and the prompt."""
    text = reply.decode("utf-8", "replace")
    # The echo is everything between the prompt and the newline that ends it.
    # ANSI is removed rather than interpreted: the board emits cursor moves as
    # it edits, and what matters is the characters it settled on.
    out, i = [], 0
    while i < len(text):
        if text[i] == "\x1b":
            while i < len(text) and text[i] not in "@ABCDFHKPmn~":
                i += 1
            i += 1
            continue
        out.append(text[i])
        i += 1
    flat = "".join(out)
    for piece in flat.splitlines():
        at = piece.rfind("aux> ")
        if at >= 0 and piece[at + 5:].startswith("zz"):
            return piece[at + 5:].rstrip("\r\n")
    return ""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        link = soc_shell.Link.open(args.port)
    except RuntimeError as error:
        emit(f"could not reach the console: {error}")
        return 1
    emit(f"console: {link.how}")

    # 0.25 s: the slowest command measured is 47 ms and none of these run one.
    # See scripts/soc_command_budget.py for where that figure comes from.
    budget_s = 0.25

    link.write(b"\r")
    link.read_until_prompt(budget_s=1.0)

    passed = failed = 0
    for name, keys, want in CASES:
        reply = run(link, keys, budget_s)
        got = line_from(reply, keys)
        ok = got == want
        passed, failed = (passed + ok, failed + (not ok))
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            emit(f"        typed {keys!r}")
            emit(f"        want  {want!r}")
            emit(f"        got   {got!r}")
        if args.verbose:
            emit(f"        raw   {reply[-90:]!r}")
        link.write(b"\x15\r")           # Ctrl-U, clean line for the next case
        link.read_until_prompt(budget_s=budget_s)

    link.close()
    emit("")
    emit(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
