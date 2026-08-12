#!/usr/bin/env python3
#
# The board has ONE owner: this process. Submit a job; never open the tty. #430
# SPDX-License-Identifier: BSD-3-Clause

"""
One process owns the board -- the tty, Apollo JTAG, and what is configured.

    ./scripts/board.py run --variant <slug> "bist smoke"     # the client
    ./scripts/board_arbiter.py --serve                       # the server

## THE RULE

**Never open the console tty yourself. Never run `apollo configure` yourself.**
Every board operation goes through a job. The board is shared, stateful and
ruinous when two clients touch it at once: an agent reflashed while another was
measuring, and the second's rows are of an unknown build (#430).

Going around it is detectable and is reported: a foreign process holding the tty
or a USB node is named, by pid and command line, and the job is refused.

## One verb

Submit a job, get a transcript with its provenance. `run` is the whole sequence
-- resolve bitstream -> configure if what is loaded is not what was asked for ->
CONFIRM -> run the commands -> transcript. The primitives underneath are the
same endpoint with a different `kind`:

    kind=run        the whole sequence (default)
    kind=configure  configure and confirm; no commands
    kind=confirm    confirm only; touches no JTAG unless the console is silent
    kind=shell      commands on whatever is loaded, with provenance saying what

There is no acquire/release. Verbs are operations, never leases -- an agent
never gets handed the tty (playwrong's shape, #430 comment 3).

## What it does NOT do

- **Not building.** `./dev.py` is the entry point and `soc_build_fanout.py`
  keeps the builds. A job whose variant has no bitstream is refused, with the
  build command to run.
- **Not flashing firmware.** `soc_run.py` owns the flash write; adding a second
  front end for it is how the retired `cyn` died (`debris/CYN_ARCHITECTURE.md`).
- **Not command dispatch.** A job's commands are SoC *shell* commands, not host
  programs. The arbiter runs exactly one host program: `apollo configure`, via
  `soc_confirm.configure_and_confirm`.

## Why a transcript cannot look like a pass when nothing ran

`passed` requires ALL of: the pre-configure gate open, a `confirm` verdict of
`ok`, the board's own `info` reporting an image commit, and every command
returning the shell's prompt. A job with no commands, a board that is absent,
blank, wedged, or held by someone else, cannot reach it.
`tests/test_board_arbiter.py` drives each of those to `failed`.

## Idle jobs are NOT measurements

`priority=idle` runs when nothing else is queued and is **preempted** by a real
submission -- abandoned mid-sweep. Its output goes to a separate directory with
a separate schema, and `hyperram_matrix_diff.py` REFUSES to load one. They earn
their place by reporting events -- a wedge, a moved tally, a cell that changed,
a die temperature that drifted -- never rows.

## Where things are

Shared across git worktrees, because agents work in them and there is one board:
the port is the mutex, and state lives in the MAIN working tree.

    tmp/board-arbiter/jobs/<id>.json   transcripts
    tmp/board-arbiter/idle/<id>.json   idle observations, separate schema
    tmp/logs/board-arbiter.log         the server's own log
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

# 9000 is `tio_user.py`'s console fan-out, which this reads THROUGH rather than
# competing with. This is the job endpoint.
PORT = 9100

SCHEMA_JOB = "board-job"
SCHEMA_IDLE = "board-idle-observation"

# How long a shell command gets to answer when the job states no budget.
#
# Waits for: the shell's `>` prompt. Expected worst case: the FIRST reply after
# an attach is enumeration plus the banner flush, ~1.5 s (`soc_confirm`); every
# later command measured 0.04-0.07 s on this build. 1.3x the 1.5 s, the same
# bound `soc_shell.REPLY_S` and `soc_confirm.CONSOLE_REPLY_S` already carry.
# On expiry: the command is recorded `timeout` with the limit and the elapsed,
# the job FAILS, and the board is marked unknown so the next job reconfigures.
# A sweep takes seconds and MUST state its own budget -- `bist all 1` is 4.4 s.
DEFAULT_COMMAND_S = 2.0

# How often the command reader looks for bytes and for a preemption.
#
# Waits for: nothing -- it is the granularity of the read loop. Expected: the
# prompt arrives in milliseconds, so a coarser tick would make preemption feel
# like a hang and a finer one would spin. On expiry: the loop checks its
# deadline and the preempt flag, then reads again.
READ_TICK_S = 0.05

# How long a client waits for a server it just started to accept a connection.
#
# Waits for: the listening socket. Expected: this module's imports plus a bind,
# ~0.4 s measured cold. 5 s is 12x that because the first import of pyusb and
# pyserial on a cold page cache is the outlier. On expiry: the client says the
# port never came up, names tmp/logs/board-arbiter.log, and exits non-zero.
SERVER_START_S = 5.0

# The longest one long-poll may block a client's GET.
#
# Waits for: a job's completion event. Expected: jobs are bounded by their own
# command budgets and may legitimately run for minutes, so this is not about the
# job -- it is how often a waiting client re-learns the server is alive. On
# expiry: the current status is returned and the client asks again.
MAX_LONG_POLL_S = 30.0

# Processes allowed to hold the console tty. `tio_user.py --serve` owns it BY
# DESIGN and fans it out on 9000, which is how this reads it.
ALLOWED_HOLDER = "tio_user.py"

DIE_C = re.compile(r"die\s+(-?\d+)\s*C")


def shared_root() -> Path:
    """The MAIN working tree, so every worktree arbitrates the same board.

    A worktree's `tmp/` is its own, and a lock or a queue under it arbitrates
    nothing. `--git-common-dir` names the shared `.git`, whose parent is the main
    tree -- the same resolution `soc_confirm.apollo_cli` uses for `repos/`.
    """
    try:
        common = subprocess.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
            cwd=ROOT, capture_output=True, text=True, check=True)
        main = Path(common.stdout.strip()).parent
        if (main / "scripts").is_dir():
            return main
    except (subprocess.CalledProcessError, OSError):
        pass
    return ROOT


SHARED = shared_root()
STATE = SHARED / "tmp" / "board-arbiter"
JOBS_DIR = STATE / "jobs"
IDLE_DIR = STATE / "idle"
LOG = SHARED / "tmp" / "logs" / "board-arbiter.log"


def now() -> str:
    """ISO 8601 with an offset, to the millisecond.

    Milliseconds because two jobs' start and finish stamps are what shows they
    were serialised rather than interleaved, and a second is longer than a job.
    """
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds")


def say(line: str = "") -> None:
    """One line to stdout and to the log. The server's stdout IS the log file."""
    print(f"{datetime.now().strftime('%H:%M:%S')} {line}", flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{now()} {line}\n")
    except OSError:
        pass


def git(root: Path, *args: str):
    try:
        return subprocess.run(("git", *args), cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None


# ---------------------------------------------------------------------------
# What a job asks for
# ---------------------------------------------------------------------------

KINDS = ("run", "configure", "confirm", "shell")
PRIORITIES = ("normal", "idle")
TERMINAL = ("passed", "failed", "refused", "preempted")


class Refused(RuntimeError):
    """The request cannot be run at all. Never a transcript, always a reason."""


@dataclass
class Job:
    id: str
    kind: str = "run"
    priority: str = "normal"
    label: str = ""
    variant: str | None = None
    bitstream: str | None = None
    commands: list = field(default_factory=list)
    budget_s: float = DEFAULT_COMMAND_S
    repeat: int = 1
    client: dict = field(default_factory=dict)
    submitted: str = field(default_factory=now)
    status: str = "queued"
    record: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)


def build_dirs() -> dict:
    """Every built variant this machine can configure: slug -> top.bit.

    Both trees, because an agent in a worktree builds into its own `tmp/` while
    the main tree holds everything built before the worktree existed.
    """
    found = {}
    for root in (SHARED, ROOT):
        base = root / "tmp" / "awto_soc" / "build"
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/top.bit")):
            found.setdefault(path.parent.name, path)
    return found


def resolve_bitstream(job: Job) -> Path:
    """The `.bit` this job runs against. Refuses rather than guessing (#367)."""
    if job.bitstream:
        path = Path(job.bitstream)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        if not path.exists():
            raise Refused(f"no bitstream at {path}")
        return path
    if not job.variant:
        raise Refused("a run or configure job needs `variant` or `bitstream`")
    built = build_dirs()
    if job.variant not in built:
        raise Refused(
            f"no bitstream built for variant {job.variant!r}. Built: "
            f"{', '.join(sorted(built)) or 'none'}. Build it with "
            f"`./dev.py run --build-only` under that variant's environment, or "
            f"`scripts/soc_build_fanout.py`; this does not build.")
    return built[job.variant]


@contextmanager
def build_lock(bitstream: Path):
    """Hold the variant's build lock while its bytes are read and configured.

    `soc_run.py` rewrites `top.bit` in place under `<build>/.build.lock` (#351),
    and did so three times during one afternoon's soak here. Without this the
    arbiter can hash one build and hand the ECP5 another's bytes.
    """
    path = bitstream.parent / ".build.lock"
    handle = open(path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as busy:
            handle.seek(0)
            raise Refused(
                f"a build is writing {bitstream.parent.name} right now "
                f"({path.name} is held). Its bytes are not a bitstream yet -- "
                f"resubmit when the build finishes.") from busy
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def identify(path: Path) -> dict:
    data = path.read_bytes()
    try:
        where = str(path.relative_to(SHARED))
    except ValueError:
        where = path.name
    return {"path": where, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)}


# ---------------------------------------------------------------------------
# The hardware. Only the worker thread touches this.
# ---------------------------------------------------------------------------


class Board:
    """The tty, Apollo JTAG, and what is configured.

    `loaded` is what THIS process configured and confirmed. It is dropped to
    None on every failure, every preemption and every restart, so "unknown"
    always costs a reconfigure rather than an assumption (#367).
    """

    def __init__(self):
        self.loaded = None          # (variant, sha256) or None == unknown
        self.polluted = False       # a preempted sweep may still be printing

    # -- guards ------------------------------------------------------------

    @staticmethod
    def foreign_holders():
        """Processes holding the board's nodes that are not the console service.

        `tio_user.py --serve` holds the tty by design. Anything else is someone
        going around the arbiter, and is named rather than silently interleaved.
        """
        import board_holders

        return [(pid, cmd) for pid, cmd in
                board_holders.processes_holding(board_holders.board_nodes())
                if ALLOWED_HOLDER not in cmd]

    def guard(self):
        import soc_confirm

        holders = self.foreign_holders()
        if holders:
            self.loaded = None
            raise Refused(
                "the board is held by " + "; ".join(
                    f"pid {pid}: {cmd}" for pid, cmd in holders)
                + " -- that process is going around the arbiter. Stop it, or "
                  "run it as `./scripts/tio_user.py` which serves on 9000.")
        blocked = soc_confirm.precheck()
        if blocked is not None:
            self.loaded = None
            raise Refused(f"[{blocked.name}] {blocked.headline}")

    # -- the sequence ------------------------------------------------------

    def ensure_image(self, job: Job, want: tuple, bitstream: Path) -> bool:
        """Configure only if what is loaded is not what the job asked for.

        A ladder of ten jobs on one variant configures once; a variant change,
        a preemption or a cold start configures. Returns whether it configured.
        """
        import soc_confirm

        if self.loaded == want and not self.polluted:
            return False
        say(f"  configure {bitstream.name} ({want[0]})")
        rc = soc_confirm.configure_and_confirm(bitstream, expect="shell")
        if rc != 0:
            self.loaded = None
            raise Refused(
                f"configure_and_confirm refused or failed for {bitstream.name}"
                " -- see tmp/logs/dev.log for the named verdict (#360)")
        self.loaded, self.polluted = want, False
        return True

    @staticmethod
    def confirm() -> dict:
        """The unskippable step. `apollo configure` exiting 0 is not a design."""
        import soc_confirm

        verdict = soc_confirm.confirm(expect="shell")
        return {"verdict": verdict.name, "headline": verdict.headline,
                "retry": verdict.retry}

    @staticmethod
    def open_link():
        import soc_shell

        link = soc_shell.Link.open(None)
        link.settle(READ_TICK_S)
        link.write(b"\r")
        link.read_until_prompt(DEFAULT_COMMAND_S)
        return link

    @staticmethod
    def ask(link, command: str, budget_s: float, stop: threading.Event | None):
        """One command, one reply, abandonable.

        `Link.read_until_prompt` cannot be interrupted, and an idle sweep holds
        it for minutes -- so the loop is here, with the same prompt rule.
        """
        import soc_shell

        started = time.monotonic()
        deadline = started + budget_s
        link.settle(READ_TICK_S)
        link.write(command.encode() + b"\r")
        reply = b""
        while time.monotonic() < deadline:
            if stop is not None and stop.is_set():
                return reply, "preempted", time.monotonic() - started
            chunk = link.read_available()
            if chunk:
                reply += chunk
                if reply.rstrip().endswith(soc_shell.PROMPT):
                    return reply, "ok", time.monotonic() - started
        return reply, "timeout", time.monotonic() - started

    def provenance(self, link) -> dict:
        """What the BOARD says it is running. Not the host checkout (#430).

        `info` is read by the arbiter itself rather than being a job command,
        so no transcript can exist without it.
        """
        import hyperram_matrix_diff as matrix

        # ASKED TWICE. The banner is flushed on the first byte received after a
        # boot, so it can arrive mid-reply and carry the prompt this read stops
        # at -- `info`'s own output then lands after the read finished. Seen on
        # a real job; the same reason `soc_confirm.CONSOLE_ASKS` is 2.
        for _attempt in range(2):
            reply, status, _ = self.ask(link, "info", DEFAULT_COMMAND_S, None)
            text = reply.decode("ascii", "replace")
            image = matrix.IMAGE.search(text)
            if status == "ok" and image:
                break
        gateware = matrix.GATEWARE.search(text)
        die = DIE_C.search(text)
        if status != "ok" or not image:
            raise Refused(
                "`info` did not report an image commit, so nothing this job did "
                f"can be attributed to a build (status {status}, "
                f"{len(text)} bytes, asked twice)")
        return {
            "image": image.group(1), "image_dirty": image.group(2) == "dirty",
            "gateware": gateware.group(1) if gateware else None,
            "gateware_dirty": (gateware.group(2) == "dirty"
                               if gateware else None),
            "die_c": int(die.group(1)) if die else None,
        }


# ---------------------------------------------------------------------------
# Idle observations: EVENTS, never rows
# ---------------------------------------------------------------------------
#
# An idle run is not a measurement and must be incapable of being quoted as one.
# Separate directory, separate schema, and `hyperram_matrix_diff.load` refuses
# to read one -- `tests/test_idle_is_not_a_measurement.py` proves the refusal.
# What it earns its board time with is events: a wedge with the causing state
# still loaded, a tally that moved between identical runs, a cell that changed
# verdict, a die temperature that drifted.

# How much of a reply an idle observation keeps.
#
# `bist all 1` prints 343 KB; a soak of hundreds of runs would be gigabytes of
# rows nobody may quote. The tail carries the summary line and the last cells,
# which is what an event is derived from; the full byte count is recorded.
IDLE_REPLY_TAIL = 2000

# How far the die may drift before it is an event, in Celsius.
#
# tCSM halves above 85 C (#341) and nothing has ever driven this part into that
# regime. 5 C is the smallest step the `info` reading (whole degrees) can show
# as a trend rather than as sampling noise.
DIE_DRIFT_C = 5


def _identity(record: dict) -> tuple:
    """What a comparison has to hold constant: the bitstream and the image."""
    provenance = record.get("provenance") or {}
    return ((provenance.get("bitstream") or {}).get("sha256"),
            (provenance.get("board") or {}).get("image"))


def same_build(record: dict, previous: dict) -> bool:
    return _identity(record) == _identity(previous)


def build_change(record: dict, previous: dict) -> str:
    """The most useful event a soak produces: the board stopped being the same.

    Observed live -- another session rebuilt this variant three times during one
    afternoon's soak, and a cell diff across it would have read as marginality.
    """
    was_bit, was_image = _identity(previous)
    now_bit, now_image = _identity(record)
    return (f"BUILD CHANGED under the soak since {previous['id']}: bitstream "
            f"{(was_bit or '?')[:12]} -> {(now_bit or '?')[:12]}, image "
            f"{was_image} -> {now_image}. Nothing is compared across it -- a "
            f"moved cell here is the build, not the part.")


def idle_observe(record: dict, previous: dict | None) -> dict:
    """Fill an idle record's `tally`, `verdict_classes` and `events`.

    Keys are deliberately NOT a matrix run's (`summary`, `failures`, `pins`):
    a run recorded here cannot be fed to the diff even if the refusal in
    `hyperram_matrix_diff.load` were removed.
    """
    import bist_rows

    events, tally, classes, flapped = [], {}, {}, {}
    for step in record.get("transcript", []):
        text = step.get("reply", "")
        key = f"{step['command']}#{step['pass']}"
        if step["status"] == "timeout":
            events.append(
                f"WEDGE: {step['command']!r} gave no prompt in "
                f"{record['request']['budget_s']:.1f} s after "
                f"{step['elapsed_s']:.1f} s. The state that caused it is still "
                "on the board until the next job configures.")
        summary = bist_rows.SUMMARY.search(text)
        if summary:
            tally[key] = {name: int(value) for name, value
                          in summary.groupdict().items()}
        for row in bist_rows.rows(text):
            cell = "{lat},{mode},{drive},{clk},{sel}".format(**row)
            verdict = row["verdict"]
            seen = ("fail" if verdict.startswith("fail")
                    else "pass" if verdict.startswith("PASS")
                    else "no result")
            # A cell that disagrees with ITSELF across this job's own passes is
            # the strongest marginality evidence there is: same configuration,
            # same board, seconds apart.
            if cell in classes and classes[cell] != seen:
                flapped.setdefault(cell, set()).add(classes[cell])
                flapped[cell].add(seen)
            classes[cell] = seen
        step["reply_bytes"] = len(text)
        step["reply"] = text[-IDLE_REPLY_TAIL:]

    if record.get("status") == "refused":
        events.append(f"REFUSED: {record.get('error')}")

    board = (record.get("provenance") or {}).get("board") or {}
    record["tally"] = tally
    record["verdict_classes"] = classes
    if flapped:
        events.append(
            f"{len(flapped)} cell(s) disagreed with themselves WITHIN this run: "
            + ", ".join(f"{cell} {'/'.join(sorted(seen))}"
                        for cell, seen in sorted(flapped.items())[:8])
            + (" ..." if len(flapped) > 8 else ""))

    if previous and not same_build(record, previous):
        events.append(build_change(record, previous))
        previous = None                 # nothing below may compare across it

    if previous:
        for key, counts in tally.items():
            was = (previous.get("tally") or {}).get(key)
            if was and was != counts:
                events.append(f"TALLY MOVED for {key}: {was} -> {counts}")
        # ONLY cells both runs reached. An idle sweep is abandoned mid-command,
        # so its cell set is a prefix -- a union would report every cell the
        # shorter run never got to as having changed verdict.
        before = previous.get("verdict_classes") or {}
        moved = sorted(cell for cell in set(before) & set(classes)
                       if before[cell] != classes[cell])
        if moved:
            events.append(
                f"{len(moved)} cell(s) changed verdict against "
                f"{previous['id']}: "
                + ", ".join(f"{cell} {before.get(cell)}->{classes.get(cell)}"
                            for cell in moved[:8])
                + (" ..." if len(moved) > 8 else ""))
        was_die = ((previous.get("provenance") or {}).get("board") or {}).get("die_c")
        if was_die is not None and board.get("die_c") is not None \
                and abs(board["die_c"] - was_die) >= DIE_DRIFT_C:
            events.append(f"DIE TEMPERATURE moved {was_die} C -> "
                          f"{board['die_c']} C since {previous['id']} (#341)")

    record["events"] = events
    return record


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


class Arbiter:
    """One worker, two queues, and a preempt flag between them."""

    def __init__(self):
        self.lock = threading.Condition()
        self.normal: deque = deque()
        self.idle: deque = deque()
        self.by_id: dict = {}
        self.running: Job | None = None
        self.preempt = threading.Event()
        self.board = Board()
        self.stopping = False
        self.started = now()
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        IDLE_DIR.mkdir(parents=True, exist_ok=True)

    # -- submission --------------------------------------------------------

    def submit(self, job: Job) -> Job:
        with self.lock:
            self.by_id[job.id] = job
            if job.priority == "idle":
                self.idle.append(job)
            else:
                self.normal.append(job)
                # ABANDON the idle job, mid-sweep. Nothing depends on it
                # finishing, and a real submission must not wait for one.
                if self.running is not None and self.running.priority == "idle":
                    self.preempt.set()
            self.lock.notify_all()
        say(f"submitted {job.id} {job.kind}/{job.priority} "
            f"{job.variant or job.bitstream or ''} {job.commands}")
        return job

    def take(self) -> Job | None:
        with self.lock:
            while not self.stopping and not self.normal and not self.idle:
                self.lock.wait()
            if self.stopping:
                return None
            job = self.normal.popleft() if self.normal else self.idle.popleft()
            self.running = job
            self.preempt.clear()
            return job

    def status(self) -> dict:
        with self.lock:
            return {
                "server": {"pid": os.getpid(), "port": PORT,
                           "root": str(ROOT), "shared": str(SHARED),
                           "started": self.started},
                "board": {"loaded": list(self.board.loaded)
                                    if self.board.loaded else None,
                          "polluted": self.board.polluted},
                "running": self.running.id if self.running else None,
                "queued": [j.id for j in self.normal],
                "queued_idle": [j.id for j in self.idle],
            }

    # -- the worker --------------------------------------------------------

    def work(self):
        while True:
            job = self.take()
            if job is None:
                return
            try:
                self.execute(job)
            except Exception as failure:            # never kill the worker
                say(f"  {job.id} worker error: {failure!r}")
                job.status = "failed"
                job.record = {"schema": SCHEMA_JOB, "id": job.id,
                              "status": "failed", "error": repr(failure)}
                self.write(job)
            finally:
                with self.lock:
                    self.running = None
                job.done.set()

    def execute(self, job: Job):
        started = now()
        job.status = "running"
        say(f"running {job.id} ({job.kind}/{job.priority})")
        record = {
            "schema": SCHEMA_IDLE if job.priority == "idle" else SCHEMA_JOB,
            "id": job.id, "kind": job.kind, "priority": job.priority,
            "label": job.label, "client": job.client,
            "submitted": job.submitted, "started": started,
            "request": {"variant": job.variant, "bitstream": job.bitstream,
                        "commands": list(job.commands),
                        "budget_s": job.budget_s, "repeat": job.repeat},
        }
        transcript = []
        record["transcript"] = transcript
        try:
            record["provenance"] = self.prepare(job)
            link = self.board.open_link()
            try:
                record["provenance"]["board"] = self.board.provenance(link)
                record["preempted"] = self.drive(job, link, transcript)
            finally:
                link.close()
            record["status"] = self.verdict(job, record)
        except Refused as reason:
            record["status"] = "refused"
            record["error"] = str(reason)
            say(f"  {job.id} REFUSED: {reason}")
        record["finished"] = now()
        job.status = record["status"]
        job.record = record
        if job.priority == "idle":
            self.observe(record)
        self.write(job)
        say(f"  {job.id} {record['status']}")

    def prepare(self, job: Job) -> dict:
        """Everything before a command runs: guard, image, confirm."""
        self.board.guard()
        provenance = {
            "host": {"root": str(ROOT), "commit": git(ROOT, "rev-parse", "HEAD"),
                     "dirty": bool(git(ROOT, "status", "--porcelain"))},
            "variant": job.variant, "bitstream": None,
            "configured_by_this_job": False,
        }
        if job.kind in ("run", "configure"):
            bitstream = resolve_bitstream(job)
            with build_lock(bitstream):
                identity = identify(bitstream)
                want = (job.variant or identity["sha256"][:12],
                        identity["sha256"])
                provenance["bitstream"] = identity
                provenance["configured_by_this_job"] = self.board.ensure_image(
                    job, want, bitstream)
        elif self.board.polluted:
            raise Refused(
                "a preempted idle sweep may still be printing on the console, "
                "so a `shell` job cannot be attributed. Submit a `run` job "
                "(which configures) or `configure` first.")
        provenance["confirm"] = self.board.confirm()
        if provenance["confirm"]["verdict"] != "ok":
            self.board.loaded = None
            raise Refused(f"[{provenance['confirm']['verdict']}] "
                          f"{provenance['confirm']['headline']}")
        return provenance

    def drive(self, job: Job, link, transcript: list) -> bool:
        """Every command, in order. True if a real submission cut it short.

        An idle job abandoned before its first command counts: the configure it
        did is not a failure, and calling it one would hide the preemption.
        """
        stop = self.preempt if job.priority == "idle" else None
        for pass_index in range(max(1, job.repeat)):
            for command in job.commands:
                if stop is not None and stop.is_set():
                    return True
                reply, status, elapsed = self.board.ask(
                    link, command, job.budget_s, stop)
                transcript.append({
                    "command": command, "pass": pass_index, "status": status,
                    "elapsed_s": round(elapsed, 3),
                    "reply": reply.decode("ascii", "replace"),
                })
                if status == "timeout":
                    # No prompt is a wedge until proven otherwise: the board is
                    # unknown from here, so the next job reconfigures.
                    self.board.loaded = None
                    say(f"  {job.id} NO PROMPT for {command!r} in "
                        f"{job.budget_s:.1f} s (elapsed {elapsed:.1f} s, "
                        f"{len(reply)} bytes)")
                    return
                if status == "preempted":
                    self.board.loaded, self.board.polluted = None, True
                    say(f"  {job.id} preempted during {command!r} "
                        f"after {elapsed:.1f} s")
                    return

    @staticmethod
    def verdict(job: Job, record: dict) -> str:
        """`passed` needs every leg. Nothing else may reach it."""
        if record.get("preempted") or any(step["status"] == "preempted"
                                          for step in record["transcript"]):
            return "preempted"
        if record["provenance"].get("confirm", {}).get("verdict") != "ok":
            return "failed"
        if not record["provenance"].get("board", {}).get("image"):
            return "failed"
        if job.kind in ("run", "shell") and not record["transcript"]:
            return "failed"
        if any(step["status"] != "ok" for step in record["transcript"]):
            return "failed"
        return "passed"

    # -- records -----------------------------------------------------------

    def write(self, job: Job):
        directory = IDLE_DIR if job.priority == "idle" else JOBS_DIR
        path = directory / f"{job.id}.json"
        path.write_text(json.dumps(job.record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        job.record["path"] = str(path.relative_to(SHARED))

    def observe(self, record: dict):
        """Turn an idle transcript into EVENTS. Never into rows.

        Rows are what a measurement is made of, and an idle run is not one --
        see the module docstring and `hyperram_matrix_diff.load`.
        """
        idle_observe(record, previous_idle(record))
        for line in record["events"]:
            say(f"  idle event: {line}")


def previous_idle(record: dict) -> dict | None:
    """The last idle observation of the same commands, for comparison."""
    for path in sorted(IDLE_DIR.glob("*.json"), reverse=True):
        if path.stem == record["id"]:
            continue
        try:
            other = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if other.get("request", {}).get("commands") == \
                record.get("request", {}).get("commands"):
            return other
    return None


# ---------------------------------------------------------------------------
# HTTP: one contract, so a shell client and (later) an MCP tool share it
# ---------------------------------------------------------------------------


def make_job(payload: dict) -> Job:
    kind = payload.get("kind", "run")
    priority = payload.get("priority", "normal")
    if kind not in KINDS:
        raise Refused(f"kind must be one of {KINDS}, not {kind!r}")
    if priority not in PRIORITIES:
        raise Refused(f"priority must be one of {PRIORITIES}")
    commands = payload.get("commands") or []
    if not all(isinstance(c, str) and c.strip() for c in commands):
        raise Refused("commands must be non-empty strings")
    if kind in ("run", "shell") and not commands:
        raise Refused(f"a {kind} job with no commands would report a pass "
                      "having run nothing")
    if kind in ("confirm", "shell") and payload.get("variant"):
        raise Refused(f"a {kind} job does not configure, so naming a variant "
                      "would claim provenance it cannot check. Use kind=run.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Job(
        id=f"{stamp}-{os.urandom(2).hex()}", kind=kind, priority=priority,
        label=str(payload.get("label", ""))[:80],
        variant=payload.get("variant"), bitstream=payload.get("bitstream"),
        commands=commands,
        budget_s=float(payload.get("budget_s") or DEFAULT_COMMAND_S),
        repeat=int(payload.get("repeat") or 1),
        client=payload.get("client") or {})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    arbiter: Arbiter = None

    def log_message(self, *_args):
        pass                                    # the server logs its own lines

    def reply(self, code: int, body: dict):
        data = json.dumps(body, indent=2, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/status":
            return self.reply(200, self.arbiter.status())
        if path.startswith("/jobs/"):
            job = self.arbiter.by_id.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self.reply(404, {"error": "no such job"})
            # Long poll: the client blocks on the job's own event rather than
            # asking on a timer. The cap is how often it re-learns we are alive.
            wait_s = dict(part.split("=", 1) for part in query.split("&")
                          if "=" in part).get("wait")
            if wait_s:
                job.done.wait(min(float(wait_s), MAX_LONG_POLL_S))
            return self.reply(200, {"id": job.id, "status": job.status,
                                    "record": job.record})
        return self.reply(404, {"error": "GET /status or /jobs/<id>"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            return self.reply(400, {"error": f"bad JSON: {error}"})
        if self.path == "/shutdown":
            self.reply(200, {"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            with self.arbiter.lock:
                self.arbiter.stopping = True
                self.arbiter.lock.notify_all()
            return None
        if self.path != "/jobs":
            return self.reply(404, {"error": "POST /jobs or /shutdown"})
        try:
            job = make_job(payload)
        except (Refused, TypeError, ValueError) as reason:
            return self.reply(400, {"error": str(reason)})
        self.arbiter.submit(job)
        return self.reply(202, {"id": job.id, "status": job.status})


def serve() -> int:
    arbiter = Arbiter()
    Handler.arbiter = arbiter
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as error:
        if reachable():
            say(f"another arbiter already owns {PORT}; nothing to do")
            return 0
        say(f"cannot bind {PORT}: {error}")
        return 1
    threading.Thread(target=arbiter.work, daemon=True).start()
    say(f"board arbiter on 127.0.0.1:{PORT}  root {ROOT}  shared {SHARED}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    say("board arbiter stopped")
    return 0


def reachable(timeout_s: float = 0.5) -> bool:
    """Is a server listening? Loopback connect, sub-millisecond when it is."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout_s):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true",
                        help="run the server (clients start it themselves)")
    args = parser.parse_args()
    if not args.serve:
        parser.error("nothing to do without --serve; the client is "
                     "scripts/board.py, which starts this itself")
    return serve()


if __name__ == "__main__":
    sys.exit(main())
