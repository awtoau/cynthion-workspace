#!/usr/bin/env python3
# AOA repeatability, closed loop: every step is judged by the Cynthion AND by what the app reports.
# SPDX-License-Identifier: BSD-3-Clause
"""
    ./scripts/soc/aoa_repeat.py [--cycles 10] [--sizes 1 64 512 4096] [--phone IP]

Stops at the first failing cycle (--keep-going to run on): fix the error, rerun.
Per cycle: role off/host, reset, aoa, wait for the app to report the accessory open, set echo
mode, echo each size. The app's event log is followed the whole time and
`ctl get` is read after each phase: a cycle fails on any app error after the accessory is open,
on `accessory_open` false, or when the app's received byte count differs from what was sent.
Log: tmp/logs/aoa_repeat.log (shell output, app events, app state per phase).
Needs `vbus control` already done and the bench app (control port 6560) installed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "tmp" / "logs" / "aoa_repeat.log"
# The awto-dji bench worktree with `dev.py ctl`; override with AWTO_DJI_CTL.
CTL_REPO = Path(os.environ.get("AWTO_DJI_CTL", ROOT.parent / "awto-dji-wt-ctl"))

# One shell command is ~4 s on this console (prompt wait); a cycle is ~8 commands.
SHELL_LIMIT_S = 60
CTL_LIMIT_S = 20
# Accessory open follows re-enumeration by ~1-2 s on the Pixel; 8 x 0.5 s = 4 s covers it.
OPEN_TRIES, OPEN_GAP_S = 8, 0.5
# Log-follow poll: an app event line lands within one poll; 0.2 s is far below a USB step.
POLL_S = 0.2
DEFAULT_APP_LOG = ROOT / "tmp" / "logs" / "dji_listen_echo2.log"
APP_ERROR = re.compile(r"\bapp E\b|stream error|EIO|Exception")


class AppEvents:
    """The app's event lines (its log on the PC listener, port 9910), followed from now on.

    The control port pushes only results; stream errors, detaches and state changes reach the
    listener, which `dev.py listen --port 9910 --tail > <path>` writes to a file.
    """

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise SystemExit(f"app log {self.path} missing: start `./dev.py listen --port 9910 "
                             f"--tail > {self.path}` in the awto-dji bench worktree and connect the app")
        self.lines = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        with self.path.open() as handle:
            handle.seek(0, 2)
            while not self.stop.is_set():
                line = handle.readline()
                if line:
                    with self.lock:
                        self.lines.append((time.monotonic(), line.rstrip()))
                else:
                    self.stop.wait(POLL_S)

    def mark(self):
        with self.lock:
            return len(self.lines)

    def since(self, mark):
        with self.lock:
            return [text for _, text in self.lines[mark:]]

    def close(self):
        self.stop.set()


def say(line, log):
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def shell(commands):
    run = subprocess.run([sys.executable, str(ROOT / "scripts/soc_shell.py"), *commands],
                         cwd=ROOT, capture_output=True, text=True, timeout=SHELL_LIMIT_S)
    return run.stdout


def ctl(phone, *args):
    run = subprocess.run(["./dev.py", "ctl", "--target", phone, *args], cwd=CTL_REPO,
                         capture_output=True, text=True, timeout=CTL_LIMIT_S)
    return run.stdout


def reply(text):
    """The control port's reply object from `dev.py ctl` output, or {}."""
    for line in text.splitlines():
        if line.startswith('{"t": "reply"'):
            return json.loads(line)
    return {}


def app_state(phone, log, label):
    got = reply(ctl(phone, "get"))
    result = got.get("result") or {}
    state = {"accessory_open": got.get("accessory_open"), "mode": result.get("mode"),
             "bytes": result.get("bytes"), "frames": result.get("frames"),
             "pass": result.get("pass")}
    log.write(f"app {label}: {json.dumps(state)}\n")
    return state


def echo_ok(text, size):
    block = text.split(f"usb target_a echo 1 {size} 1", 1)[-1]
    return bool(re.search(rf"echo out {size} in {size} bytes", block)) \
        and "stopped:" not in block.split("aux>")[1] and "mismatch" not in block


def run_cycle(cycle, args, events, log):
    """-> (ok, why). `why` names the first thing that failed, with the app's side of it."""
    start = events.mark()
    out = shell(["usb target_a role off", "usb target_a role host", "usb target_a reset",
                 "usb target_a aoa"])
    log.write(out)
    detach = [t for t in events.since(start) if APP_ERROR.search(t)]
    if "accessory 18d1:2d0" not in out:
        return False, f"aoa did not enumerate; app saw: {detach or 'nothing'}"

    for _ in range(OPEN_TRIES):
        mode = reply(ctl(args.phone, "mode", "echo", f"test={cycle}"))
        log.write(json.dumps(mode) + "\n")
        if mode.get("ok") and mode.get("accessory_open"):
            break
        time.sleep(OPEN_GAP_S)
    else:
        return False, f"app never reported the accessory open (last reply {mode})"

    quiet = events.mark()
    text = shell([f"usb target_a echo 1 {size} 1" for size in args.sizes])
    log.write(text)
    results = {size: echo_ok(text, size) for size in args.sizes}
    state = app_state(args.phone, log, "after echo")
    errors = [t for t in events.since(quiet) if APP_ERROR.search(t)]
    expected = sum(args.sizes)
    if errors:
        return False, f"app error during echo: {errors[0][:160]}"
    if not all(results.values()):
        return False, f"Cynthion echo failed {results}; app received {state['bytes']} of {expected} B"
    if state["bytes"] != expected:
        return False, f"app received {state['bytes']} B, Cynthion sent {expected} B"
    if not state["accessory_open"]:
        return False, "app reports the accessory closed after echo"
    return True, f"app received {state['bytes']} B in {state['frames']} frames"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 64, 512, 4096])
    parser.add_argument("--phone", default="192.168.25.125")
    parser.add_argument("--app-log", default=str(DEFAULT_APP_LOG))
    parser.add_argument("--keep-going", action="store_true",
                        help="do not stop at the first failing cycle (default: stop, fix, rerun)")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    passed = 0
    events = AppEvents(args.app_log)
    try:
        with LOG.open("w") as log:
            for cycle in range(1, args.cycles + 1):
                started = time.monotonic()
                mark = events.mark()
                ok, why = run_cycle(cycle, args, events, log)
                passed += ok
                log.write("app events this cycle:\n" +
                          "\n".join("  " + t for t in events.since(mark)) + "\n")
                say(f"cycle {cycle}: {'PASS' if ok else 'FAIL'}  {why}  "
                    f"{time.monotonic() - started:.0f} s", log)
                if not ok and not args.keep_going:
                    say(f"stopped at the first failure; details in {LOG.relative_to(ROOT)}", log)
                    break
            say(f"{passed}/{cycle} cycles passed", log)
    finally:
        events.close()
    return 0 if passed == args.cycles else 1


if __name__ == "__main__":
    sys.exit(main())
