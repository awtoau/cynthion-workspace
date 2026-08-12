#!/usr/bin/env python3
#
# Talk to the board through its arbiter. Never open the tty yourself. #430
# SPDX-License-Identifier: BSD-3-Clause

"""
The client for the board arbiter. Starts the server itself if it is not up.

    ./scripts/board.py run --variant bist1-ck120-dqs1-mirror0-mirrordiv4 \\
                           "bist smoke" "bist latency 8"
    ./scripts/board.py shell "info"          # no configure; whatever is loaded
    ./scripts/board.py confirm               # is a design running, and which
    ./scripts/board.py configure --variant <slug>
    ./scripts/board.py idle --variant <slug> --repeat 20 --budget 40 "bist all 8"
    ./scripts/board.py status
    ./scripts/board.py stop

## THE RULE

**Never open the console tty yourself. Never run `apollo configure` yourself.**
The board is one shared, stateful resource: the tty, Apollo JTAG and what is
configured. Two clients touching it at once is how a measurement ends up
attributed to another agent's build (#430). Anything holding the tty that is not
`tio_user.py` is named in the refusal.

## What comes back

A transcript with its provenance: the bitstream sha256 that was configured, the
commit the BOARD reports (not the host checkout), the `confirm` verdict, and one
entry per command with the reply and how long it took. Exit status is 0 only for
`passed`; `failed`, `refused` and `preempted` are all non-zero.

`--json` prints the record. Records also land in `tmp/board-arbiter/`.

## Auto-start

The server is started by the first client that finds the port down, from THIS
checkout. `status` reports which root the running server came from -- one board,
one server, whichever worktree got there first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import board_arbiter as arbiter  # noqa: E402

BASE = f"http://127.0.0.1:{arbiter.PORT}"

# How long one long-poll may block, and how long the client's socket allows.
#
# Waits for: the job's completion event, server-side -- no polling loop and no
# sleep. Expected: a job runs for as long as its command budgets allow, so the
# cap is not about the job; it is how often a waiting client re-learns that the
# server is alive. 1.25x for the socket, so the read times out only when the
# server genuinely stopped answering. On expiry: the client asks again.
LONG_POLL_S = 30.0
SOCKET_S = LONG_POLL_S * 1.25


def request(method: str, path: str, payload: dict | None = None,
            timeout_s: float = SOCKET_S):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def ensure_server(quiet: bool = False) -> None:
    """Start the arbiter if the port is down, and wait for it to listen."""
    if arbiter.reachable():
        return
    log = arbiter.LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "board_arbiter.py"), "--serve"],
        cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + arbiter.SERVER_START_S
    while time.monotonic() < deadline:
        if arbiter.reachable():
            if not quiet:
                print(f"started the board arbiter on {BASE}", file=sys.stderr)
            return
        # A refused connect returns instantly; without a pause this spins a core
        # for the whole start-up window.
        time.sleep(0.05)
    raise SystemExit(
        f"the arbiter did not accept a connection within "
        f"{arbiter.SERVER_START_S:.0f} s of being started "
        f"(elapsed {arbiter.SERVER_START_S:.0f} s). See "
        f"{log.relative_to(arbiter.SHARED)}")


def wait(job_id: str, quiet: bool) -> dict:
    """Block on the job's completion, server-side. No overall deadline.

    A client can legitimately queue behind a job that is minutes long, and every
    job is bounded by its own command budgets. What ends the wait early is the
    SERVER going away, which is an error.
    """
    said = None
    while True:
        try:
            _code, body = request("GET",
                                  f"/jobs/{job_id}?wait={LONG_POLL_S:.0f}")
        except (OSError, urllib.error.URLError) as error:
            raise SystemExit(f"the arbiter stopped answering: {error}. See "
                             f"{arbiter.LOG.relative_to(arbiter.SHARED)}")
        if body.get("status") in arbiter.TERMINAL:
            return body["record"] or body
        if not quiet and body.get("status") != said:
            said = body.get("status")
            _c, status = request("GET", "/status")
            ahead = len(status.get("queued", []))
            print(f"  {job_id}: {said}"
                  + (f", {ahead} job(s) ahead" if said == "queued" else ""),
                  file=sys.stderr)


def show(record: dict) -> None:
    provenance = record.get("provenance") or {}
    board = provenance.get("board") or {}
    bitstream = provenance.get("bitstream") or {}
    print(f"=== {record['id']} {record['kind']}/{record['priority']} "
          f"{record['status'].upper()} ===")
    if record.get("error"):
        print(f"  {record['error']}")
    if bitstream:
        print(f"  bitstream {bitstream['path']}  sha256 "
              f"{bitstream['sha256'][:12]}  "
              f"{'configured by this job' if provenance.get('configured_by_this_job') else 'already loaded'}")
    if board:
        print(f"  board image {board.get('image')}"
              f"{' DIRTY' if board.get('image_dirty') else ''}"
              f"  gateware {board.get('gateware')}"
              f"  die {board.get('die_c')} C")
    if provenance.get("confirm"):
        print(f"  confirm [{provenance['confirm']['verdict']}] "
              f"{provenance['confirm']['headline']}")
    for step in record.get("transcript") or []:
        print(f"--- {step['command']} ({step['status']}, "
              f"{step['elapsed_s']:.2f} s) ---")
        print(step["reply"].strip() or "(nothing)")
    for line in record.get("events") or []:
        print(f"  idle event: {line}")
    if record.get("path"):
        print(f"  -> {record['path']}")


def submit(args, kind: str, priority: str = "normal") -> int:
    ensure_server(args.json)
    payload = {
        "kind": kind, "priority": priority,
        "commands": list(getattr(args, "commands", []) or []),
        "variant": getattr(args, "variant", None),
        "bitstream": getattr(args, "bitstream", None),
        "budget_s": getattr(args, "budget", None),
        "repeat": getattr(args, "repeat", 1),
        "label": getattr(args, "label", "") or "",
        "client": {"pid": os.getpid(), "root": str(ROOT),
                   "user": os.environ.get("USER", "?"),
                   "argv": " ".join(sys.argv[1:])[:200]},
    }
    try:
        code, body = request("POST", "/jobs", payload)
    except (OSError, urllib.error.URLError):
        # The port answered a moment ago and is gone: a server shutting down as
        # this client arrived. Start one and submit again, once.
        ensure_server(args.json)
        code, body = request("POST", "/jobs", payload)
    if code != 202:
        print(f"REFUSED: {body.get('error')}", file=sys.stderr)
        return 2
    record = wait(body["id"], args.json)
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        show(record)
    return 0 if record.get("status") == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true",
                        help="print the record instead of the transcript")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def board_job(name, help_text, *, commands=True, variant=True):
        sub = subparsers.add_parser(name, help=help_text)
        if commands:
            sub.add_argument("commands", nargs="*",
                             help="SoC shell commands, in order")
            sub.add_argument("--budget", type=float,
                             default=arbiter.DEFAULT_COMMAND_S,
                             help="seconds per command; a sweep needs its own "
                                  f"(default {arbiter.DEFAULT_COMMAND_S})")
        if variant:
            sub.add_argument("--variant", help="the built variant slug")
            sub.add_argument("--bitstream", help="a .bit to use instead")
        sub.add_argument("--label", default="", help="what this job is for")
        return sub

    board_job("run", "configure if needed, confirm, then run the commands")
    board_job("shell", "run commands on whatever is loaded", variant=False)
    board_job("configure", "configure and confirm; run nothing", commands=False)
    board_job("confirm", "is a design running, and which failure if not",
              commands=False, variant=False)
    idle = board_job("idle", "run at idle priority; preempted by a real job")
    idle.add_argument("--repeat", type=int, default=1,
                      help="run the commands this many times, or until "
                           "preempted")
    subparsers.add_parser("status", help="the queue and what is loaded")
    subparsers.add_parser("stop", help="shut the arbiter down")

    args = parser.parse_args()

    if args.command == "status":
        ensure_server(args.json)
        _code, body = request("GET", "/status")
        print(json.dumps(body, indent=2, sort_keys=True))
        return 0
    if args.command == "stop":
        if not arbiter.reachable():
            print("no arbiter is running")
            return 0
        request("POST", "/shutdown", {})
        return 0
    if args.command == "idle":
        return submit(args, "run", priority="idle")
    return submit(args, args.command)


if __name__ == "__main__":
    sys.exit(main())
