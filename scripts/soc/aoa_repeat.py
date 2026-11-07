#!/usr/bin/env python3
# AOA repeatability, closed loop: every step is judged by the Cynthion AND by what the app reports.
# SPDX-License-Identifier: BSD-3-Clause
"""
    ./scripts/soc/aoa_repeat.py [--cycles 10] [--sizes 1 64 512 4096] [--phone IP]

Stops at the first failing cycle (--keep-going to run on): fix the error, rerun.
Per cycle: role off/host, reset, aoa, wait for the app's accessory generation to rise (a new attach, open),
set echo mode, echo each size. The app's event log is followed the whole time and
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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
PORT = "target_a"
LOG = ROOT / "tmp" / "logs" / "aoa_repeat.log"
# The awto-dji bench worktree with `dev.py ctl`; override with AWTO_DJI_CTL.
CTL_REPO = Path(os.environ.get("AWTO_DJI_CTL", ROOT.parent / "awto-dji-wt-ctl"))

# One shell command is ~4 s on this console (prompt wait); a cycle is ~8 commands.
SHELL_LIMIT_S = 60
CTL_LIMIT_S = 20
# The app logs attach + open ~1-2 s after the phone re-enumerates; bounded by the re-attach limit.
OFF_SETTLE_S = 2.5
ATTACH_WAIT_S = 8.0   # phone re-attach bound, same as ATTACH_US in the firmware
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


# Per-command reply bound: aoa = 8 s re-attach wait (ATTACH_US) x 1.5; echo up to 4096 B is under 1 s; rest 3 s.
# On expiry the arbiter job fails and `shell` returns what it has (the cycle then fails on the missing text).
def budget_for(command):
    return 12.0 if command.endswith(" aoa") else 5.0 if " echo " in command else 3.0


def shell(commands):
    """Each command as its own arbiter job (the tty stays the arbiter's); text = echo + reply per command."""
    from soc_shell import ArbiterLink
    out = ""
    for command in commands:
        link = ArbiterLink()
        link.write(command.replace("usb target_a", f"usb {PORT}").encode())
        out += command + "\n" + link.read_until_prompt(budget_for(command)).decode("ascii", "replace") + "\n"
    return out


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
        and "stopped:" not in block and "mismatch" not in block


def wait_for_new_accessory(phone, gen_before):
    """True once the app reports a higher accessory generation, open, than before the `aoa`.

    A bare `accessory_open` can be left over from the session before; the generation (one per
    attach, from the app) proves it is the new accessory, whatever order its log lines land in.
    """
    deadline = time.monotonic() + ATTACH_WAIT_S
    while time.monotonic() < deadline:
        got = reply(ctl(phone, "get"))
        if got.get("accessory_open") and (got.get("accessory_gen") or 0) > gen_before:
            return True
        time.sleep(POLL_S)
    return False


def attach(args, events, log):
    """Fresh AOA attach: role off, settle, host, reset, aoa, wait for the app's new generation.

    -> (ok, why). Every test starts here so no data left in the phone's buffers by an earlier
    run can be read as part of the next one.
    """
    out = shell(["usb target_a role off"])
    # Android tears its gadget down ~1 s after the bus loss and re-presents it ~1.7 s after; a reset
    # or aoa inside that window is undone by it. 2.5 s = 1.5x the measured re-present time.
    time.sleep(OFF_SETTLE_S)
    out += shell(["usb target_a role host", "usb target_a reset"])
    log.write(out)
    if "reset done" not in out:
        return False, f"reset did not complete: {out.strip().splitlines()[-3:]}"
    gen_before = reply(ctl(args.phone, "get")).get("accessory_gen") or 0
    mark = events.mark()
    out = shell(["usb target_a aoa"])
    log.write(out)
    if "accessory 18d1:2d0" not in out:
        return False, f"aoa did not enumerate; app saw: {events.since(mark) or 'nothing'}"
    if not wait_for_new_accessory(args.phone, gen_before):
        return False, (f"app accessory_gen never rose above {gen_before} with the accessory open; "
                       f"saw: {events.since(mark)[-4:]}")
    return True, ""


def run_cycle(cycle, args, events, log):
    """-> (ok, why). `why` names the first thing that failed, with the app's side of it."""
    ok, why = attach(args, events, log)
    if not ok:
        return ok, why
    mode = reply(ctl(args.phone, "mode", "echo", f"test={cycle}"))
    log.write(json.dumps(mode) + "\n")
    if not (mode.get("ok") and mode.get("accessory_open")):
        return False, f"app refused echo mode or reports the accessory closed: {mode}"

    quiet = events.mark()
    sent = 0
    for size in args.sizes:
        text = shell([f"usb target_a echo 1 {size} 1"])
        log.write(text)
        sent += size
        state = app_state(args.phone, log, f"after echo {size}")
        errors = [t for t in events.since(quiet) if APP_ERROR.search(t)]
        if errors:
            return False, f"app error during echo {size}: {errors[0][:160]}"
        if not echo_ok(text, size):
            return False, f"Cynthion echo {size} failed; app received {state['bytes']} of {sent} B"
        if state["bytes"] != sent:
            return False, f"after echo {size} app received {state['bytes']} B, Cynthion sent {sent} B"
    expected = sent
    if not state["accessory_open"]:
        return False, "app reports the accessory closed after echo"
    return True, f"app received {state['bytes']} B in {state['frames']} frames"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 64, 512, 4096])
    parser.add_argument("--port", default="target_a", choices=("target_a", "target_c"),
                        help="shell port name; target_a and target_c share one data bus")
    parser.add_argument("--phone", default="192.168.25.125")
    parser.add_argument("--app-log", default=str(DEFAULT_APP_LOG))
    parser.add_argument("--keep-going", action="store_true",
                        help="do not stop at the first failing cycle (default: stop, fix, rerun)")
    args = parser.parse_args()
    global PORT
    PORT = args.port

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
