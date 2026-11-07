#!/usr/bin/env python3
# Each console reply belongs to the command that was sent (#632).
# SPDX-License-Identifier: BSD-3-Clause

"""Proof that `Board.ask` attributes every reply to its own command. No hardware.

The shell's line editor redraws on Enter: erase, prompt, echo, THEN the command
runs and prints. A command slower than `SETTLE_S` (30 ms) to its first byte left
the redraw prompt as the last thing read, `ask` returned it, and the real output
arrived under the NEXT command (#632: every slot 0.00-0.01 s).

    ./scripts/board/board_ask_test.py        # logs to tmp/logs/board_ask_test.log
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _paths  # noqa: F401
import board_arbiter as arbiter

LOG = ROOT / "tmp" / "logs" / "board_ask_test.log"

# Commands and how long the fake shell takes to print the first output byte.
# 0.12 s is four times SETTLE_S: the slowest first-byte gap `pd target_c send` has.
COMMANDS = [("alpha", 0.0), ("bravo", 0.12), ("charlie", 0.0), ("delta", 0.12)]


class FakeShell:
    """Byte-accurate editor behaviour: redraw prompt + echo now, output after a delay."""

    def __init__(self):
        self.pending: list[tuple[float, bytes]] = []

    def write(self, data: bytes):
        command = data.strip().decode()
        delay = dict(COMMANDS)[command]
        now = time.monotonic()
        # The redraw reaches the host in two reads: the prompt, then the echo.
        self.pending.append((now, b"\r\n>"))
        self.pending.append((now + 0.002, b" " + command.encode()))
        self.pending.append((now + max(delay, 0.004), b"\r\nout-" + command.encode() + b"\r\n> "))

    def read_available(self) -> bytes:
        now = time.monotonic()
        ready = [chunk for at, chunk in self.pending if at <= now]
        self.pending = [(at, chunk) for at, chunk in self.pending if at > now]
        if not ready:
            time.sleep(0.001)
        return b"".join(ready)

    def settle(self, _seconds):
        return b""

    def _settle(self, deadline):
        # Same rule as soc_shell.Link._settle: bytes within SETTLE_S, else nothing.
        end = time.monotonic() + 0.030
        while time.monotonic() < min(end, deadline):
            chunk = self.read_available()
            if chunk:
                return chunk
        return b""


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    link = FakeShell()
    failures = []
    lines = []
    for command, _ in COMMANDS:
        reply, status, elapsed = arbiter.Board.ask(link, command, 2.0, None)
        text = reply.decode()
        mine = f"out-{command}" in text
        others = [c for c, _ in COMMANDS if c != command and f"out-{c}" in text]
        ok = status == "ok" and mine and not others
        lines.append(f"{'PASS' if ok else 'FAIL'}  {command}: {status} {elapsed:.3f}s {text!r}")
        if not ok:
            failures.append(command)
    LOG.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
