#!/usr/bin/env python3
#
# One log file for everything `dev.py` and `scripts/*.py` do.
# SPDX-License-Identifier: BSD-3-Clause

"""
Tier A logging for this repo's tooling: stderr for a human, `tmp/logs/dev.log`
for everything.

## Why there is one file

There were fifty-six. `dev.py` wrote `tmp/logs/dev.log`, and each of the
fifty-five scripts it invokes opened `tmp/logs/<its own name>.log`. Neither half
held a whole run: `dev.log` recorded

    17:16:18  INFO  [dev.py] run: python3 scripts/soc_run.py --no-build
    17:18:01  ERROR [dev.py] rc=1

and the traceback that said *why* went to the terminal and to nowhere else,
because `subprocess.call` inherits the parent's streams. Reading a failure meant
knowing which of the sibling logs to open, and reading a failure that only
happened once meant reproducing it first.

So: one path, one format, and child output captured into it rather than
inherited past it.

## The format

    17:23:03.400521+1000  INFO   [soc_run.configure] flash written
    17:23:04.117903+1000  CHILD  [nextpnr-ecp5] Info: Max frequency ...

Local time with offset, never UTC and never a hardcoded `+10:00` -- Selwyn
observes DST, so a fixed offset is an hour wrong every summer, and a human reads
these live.

`[module.function]` is derived from the caller's stack frame, not passed in.
Passing it in means it is wrong the moment code is moved, and a label that lies
about where a line came from is worse than no label.

Lines from a child process are tagged `CHILD` with the program that produced
them, so `soc_run.py`'s own output and `nextpnr`'s are distinguishable in the
one stream.

## Colour on the terminal, never in the file

ANSI in a log file ruins every later `grep` -- a coloured `ERROR` does not match
`grep ERROR`, which is exactly the failure this module exists to prevent. Colour
is written to stderr only, and only when stderr is a TTY and `NO_COLOR` /
`CLICOLOR=0` do not forbid it.

## Daemons do not use this

`docs/coding/python.md` § Logging: daemons are Tier B and use `logging` with a
`SysLogHandler`. This is Tier A -- dev tooling, gates and harnesses -- and the
two are deliberately different.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "tmp" / "logs"
LOG = LOGS / "dev.log"

_COLOR = {
    "FATAL": "\033[1;91m", "ERROR": "\033[31m", "WARN": "\033[33m",
    "INFO": "\033[97m", "DEBUG": "\033[94m", "ALERT": "\033[92m",
    "CHILD": "\033[90m",
}
_RESET = "\033[0m"

_USE_COLOR = (
    sys.stderr.isatty()
    and not os.environ.get("NO_COLOR")
    and os.environ.get("CLICOLOR") != "0"
)

# Matches CSI sequences, which is all these tools emit. Stripped on the way into
# the file only.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S.%f%z")


def _origin(depth: int) -> str:
    """`module.function` of the caller, from the stack rather than an argument.

    `__name__` is `__main__` for anything run directly, which is every script
    here, so the file stem is used instead -- `soc_run.main` rather than
    `__main__.main`. Module-level calls report just the module.
    """
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "?"
    module = Path(frame.f_code.co_filename).stem
    function = frame.f_code.co_name
    return module if function == "<module>" else f"{module}.{function}"


def _write(line: str, level: str) -> None:
    if _USE_COLOR:
        sys.stderr.write(f"{_COLOR.get(level, '')}{line}{_RESET}\n")
    else:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(_ANSI.sub("", line) + "\n")
    except OSError:
        pass                # never let logging kill the run


def log(msg: str, level: str = "INFO", *, depth: int = 2) -> None:
    """One line to stderr and to `dev.log`, tagged with where it came from.

    `depth` is for wrappers that call this on someone else's behalf; leave it
    alone otherwise.
    """
    _write(f"{_stamp()}  {level:<6} [{_origin(depth)}] {msg}", level)


def emit(msg: str = "") -> None:
    """Human-facing output that is also recorded.

    Scripts here print progress and results to stdout for a person watching, and
    those lines are exactly what is wanted in the log when nobody was. This
    writes the plain text to stdout AND a tagged copy to `dev.log`, so a report
    reads normally on screen and is still attributable afterwards.
    """
    print(msg, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{_stamp()}  OUT    [{_origin(2)}] "
                         f"{_ANSI.sub('', msg)}\n")
    except OSError:
        pass


# Grace between the child closing its output and the child exiting.
#
# EOF on the pipe means it has finished writing, so what remains is process
# teardown -- sub-millisecond in every run in `tmp/logs/dev.log`. 5 s is a floor
# generous enough that a slow flush cannot trip it, and it exists so a child that
# closes stdout and then wedges is killed and named rather than waited on for
# ever (#295).
EXIT_GRACE_S = 5.0


def spawn(cmd: list[str], cwd: Path | str | None = None, *,
          quiet: bool = False, bound_seconds: float | None = None) -> int:
    """Run `cmd`, streaming its output to the terminal AND into `dev.log`.

    The whole point: `subprocess.call` hands the child the parent's streams, so
    its output reaches the screen and no file. Everything a build, a simulation
    or a board session prints is then lost the moment the terminal scrolls.

    Streamed rather than captured-and-written-at-the-end, so a long synthesis
    still prints as it goes and a run interrupted half way still leaves what it
    had reached.

    `quiet` suppresses the terminal copy but still records -- for children whose
    output is parsed rather than read.

    `bound_seconds` kills the child at that limit and logs the operation, the
    limit and the elapsed. **Unbounded is the default and is correct here**: this
    also runs the console watchers and board sessions, which outlive
    reconfigures by design (`riscv_console.py`, `tio_user.py`), and a dispatcher
    cannot know what its argv is for. The bound belongs at the call site that
    knows the operation -- see `soc_run.py`'s QEMU gate. What is NOT optional is
    the exit grace below: EOF then a wedge is a hang either way.
    """
    log("run: " + " ".join(str(part) for part in cmd), depth=3)
    program = Path(str(cmd[0])).name
    if program in ("python3", "python") and len(cmd) > 1:
        program = Path(str(cmd[1])).name

    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=cwd or ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    watchdog = None
    if bound_seconds is not None:
        def overrun():
            log(f"TIMEOUT {program}: killed at {bound_seconds:.0f}s, "
                f"elapsed {time.monotonic() - started:.0f}s", "ERROR", depth=3)
            proc.kill()
        watchdog = threading.Timer(bound_seconds, overrun)
        watchdog.daemon = True
        watchdog.start()
    try:
        handle = None
        try:
            LOGS.mkdir(parents=True, exist_ok=True)
            handle = LOG.open("a", encoding="utf-8")
        except OSError:
            pass
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not quiet:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            if handle is not None:
                handle.write(f"{_stamp()}  CHILD  [{program}] "
                             f"{_ANSI.sub('', line)}\n")
                handle.flush()
        if handle is not None:
            handle.close()
    finally:
        proc.stdout.close()
        if watchdog is not None:
            watchdog.cancel()
    try:
        rc = proc.wait(timeout=EXIT_GRACE_S)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT {program}: output ended but it did not exit within "
            f"{EXIT_GRACE_S:.0f}s (elapsed {time.monotonic() - started:.0f}s); "
            f"killed", "ERROR", depth=3)
        proc.kill()
        rc = proc.wait()
    # A bounded run that succeeded is evidence about how long this takes, so the
    # next one's bound comes from measurement rather than from the floor.
    if bound_seconds is not None and rc == 0:
        try:
            from subprocess_timeout_from_history import record
            record(f"spawn:{program}", time.monotonic() - started)
        except (ImportError, OSError):
            pass
    log(f"rc={rc}: {program}", "INFO" if rc == 0 else "ERROR", depth=3)
    return rc
