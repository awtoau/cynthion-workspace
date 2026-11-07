#!/usr/bin/env python3
#
# Each console reply must belong to the command that was typed (#632).
# SPDX-License-Identifier: BSD-3-Clause

"""The line editor redraws on Enter (ESC[2K, prompt, typed text), runs the command, then prints
output and a new prompt. A reply is complete only after the output, not at the redraw prompt.

The fake link replays that byte stream with the redraw and the output in separate reads
separated by empty reads (the gap that made replies land one command late).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "board"))
sys.path.insert(0, str(ROOT / "gateware"))

import board_arbiter  # noqa: E402
import soc_shell  # noqa: E402

OUTPUT = {"info": "image abc\r\nfabric ok\r\n", "power": "off  c0\r\n", "quiet": ""}


def stream_for(command: str, tick: int) -> list[bytes]:
    """Reads the editor produces for one command; the redraw ends in a bare prompt."""
    redraw = f"{command}\x1b[2K000000.{tick:03d} aux>".encode()
    rest = f" {command}\r\n{OUTPUT[command]}000000.{tick + 1:03d} aux>".encode()
    return [redraw, b"", b"", b"", rest]


class FakeLink:
    def __init__(self):
        self.reads: list[bytes] = []
        self.tick = 0

    def settle(self, _s):
        pass

    def write(self, data: bytes):
        command = data.decode().strip()
        self.tick += 10
        self.reads += stream_for(command, self.tick)

    def read_available(self):
        return self.reads.pop(0) if self.reads else b""


def ask(link, command):
    reply, status, _ = board_arbiter.Board.ask(link, command, 1.0, None)
    return reply, status


def test_each_reply_belongs_to_its_command():
    link = FakeLink()
    for command in ("info", "power", "quiet", "info"):
        reply, status = ask(link, command)
        text = reply.decode()
        assert status == "ok"
        assert OUTPUT[command] in text, (command, text)
        for other, out in OUTPUT.items():
            if other != command and out:
                assert out not in text, (command, "got", other)


def test_redraw_prompt_alone_is_not_complete():
    assert not soc_shell.reply_complete(b"info\x1b[2K000000.791 aux>")
    assert not soc_shell.reply_complete(b"info\x1b[2K000000.791 aux> info\r\n")
    assert soc_shell.reply_complete(b"info\x1b[2K000000.791 aux> info\r\nx\r\n000000.8 aux>")


def test_bare_prompt_without_editor_redraw_still_completes():
    assert soc_shell.reply_complete(b"\r\n> ")
