#!/usr/bin/env python3
#
# Shared helper: run a build, bounded by what previous builds actually took.
# SPDX-License-Identifier: BSD-3-Clause

"""
Runs a subprocess with a timeout derived from measured history rather than
guessed.

The problem this solves is that an unbounded build hangs forever when
place-and-route wedges, and a generously fixed timeout costs that full wait
every time something goes wrong. Neither tells you quickly that a build is
stuck.

So durations are recorded per command family, and each run is allowed the
slowest previously observed time plus a margin. A build that overruns that is
not slow, it is stuck, and killing it early is the whole point.

The first run of anything has no history, so it gets a generous ceiling and is
recorded. From then on the bound tightens to what the machine has demonstrated
it can do.

    from subprocess_timeout_from_history import run_bounded
    result = run_bounded(["make"], family="firmware", cwd=ROOT)
"""

import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "tmp" / "build_times.json"

# Fraction added to the slowest observed run. 25% absorbs ordinary variation --
# a loaded machine, a cold cache -- without tolerating a genuine hang.
MARGIN = 0.25

# Ceiling for a family with no recorded history, in seconds. Generous because
# nothing is known yet; it is replaced by measurement after one successful run.
FIRST_RUN_LIMIT = 900.0

# Floor, so a family whose builds are fast does not end up with a bound so
# tight that normal jitter trips it.
MINIMUM_LIMIT = 30.0


def _load():
    if HISTORY.exists():
        try:
            return json.loads(HISTORY.read_text())
        except json.JSONDecodeError:
            # A corrupt history is not worth failing over: discard and rebuild.
            return {}
    return {}


def _save(history):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(history, indent=2, sort_keys=True))


def limit_for(family, floor=None):
    """The timeout this family should get, and why.

    `floor` raises the minimum for a caller that knows something history cannot:
    a build run beside seven others is slower than every run recorded alone, and
    a bound from those recordings would kill it while it was working (#295, #351).
    """
    floor = MINIMUM_LIMIT if floor is None else max(MINIMUM_LIMIT, floor)
    history = _load()
    recorded = history.get(family, {})
    slowest = recorded.get("slowest")

    if slowest is None:
        return max(FIRST_RUN_LIMIT, floor), "no history yet"

    bound = max(floor, slowest * (1 + MARGIN))
    return bound, f"slowest was {slowest:.0f}s, +{int(MARGIN*100)}%"


def record(family, elapsed):
    """Remember how long a SUCCESSFUL run of `family` took.

    Public because streamed runs (`devlog.spawn`) cannot go through
    `run_bounded` -- they need the child's output line by line -- but their
    bounds should tighten from measurement just the same.
    """
    history = _load()
    recorded = history.setdefault(family, {})
    recorded["slowest"] = max(recorded.get("slowest", 0.0), elapsed)
    recorded["last"] = elapsed
    recorded["runs"] = recorded.get("runs", 0) + 1
    _save(history)


def run_bounded(command, *, family, cwd=None, env=None, capture=True,
                merge_stderr=False, floor=None):
    """Run `command`, killed if it overruns what this family has needed before.

    Returns the CompletedProcess, or None if it was killed. A killed run is
    deliberately not recorded: a hang is not evidence about how long a
    successful build takes.

    `merge_stderr` puts the child's stderr in `stdout`, for callers that log one
    interleaved stream rather than two.
    """
    bound, reason = limit_for(family, floor)
    started = time.perf_counter()
    if merge_stderr:
        streams = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    elif capture:
        streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    else:
        streams = {}

    # OWN SESSION, so the whole tree can be killed and not just the shell.
    # `subprocess.run`'s timeout kills the direct child only: an orphaned
    # nextpnr outlived its build by 1831 s and slowed every build after it
    # (#440).
    proc = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                            start_new_session=True, **streams)
    try:
        stdout, stderr = proc.communicate(timeout=bound)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        # WHAT overran, the LIMIT and where it came from, and the ELAPSED. A
        # bound that expires without naming itself turns a hang into a mystery,
        # which is the failure this module exists to prevent (#295).
        print(f"  TIMEOUT {family}: killed at {bound:.0f}s ({reason}), "
              f"elapsed {time.perf_counter() - started:.0f}s")
        print(f"    {' '.join(str(part) for part in command)[:160]}")
        return None

    result = subprocess.CompletedProcess(command, proc.returncode,
                                         stdout, stderr)
    elapsed = time.perf_counter() - started

    # Only successful runs update the history. A failing build can be much
    # faster than a working one -- synthesis errors out early -- and recording
    # it would tighten the bound until real builds start being killed.
    if result.returncode == 0:
        record(family, elapsed)

    return result


def report():
    """Print what has been learned, for inspection."""
    history = _load()
    if not history:
        print("no build history recorded yet")
        return

    print(f"  {'family':<24}{'slowest':>10}{'last':>10}{'runs':>6}{'bound':>9}")
    for family, recorded in sorted(history.items()):
        bound, reason = limit_for(family)
        floored = "floor" if "30" in f"{bound:.0f}" and recorded['slowest'] * 1.25 < MINIMUM_LIMIT else ""
        print(f"  {family:<24}{recorded['slowest']:>9.2f}s"
              f"{recorded.get('last', 0):>9.2f}s"
              f"{recorded.get('runs', 0):>6}{bound:>8.0f}s  {floored}")


if __name__ == "__main__":
    report()
