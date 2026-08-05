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

import json
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


def limit_for(family):
    """The timeout this family should get, and why."""
    history = _load()
    recorded = history.get(family, {})
    slowest = recorded.get("slowest")

    if slowest is None:
        return FIRST_RUN_LIMIT, "no history yet"

    bound = max(MINIMUM_LIMIT, slowest * (1 + MARGIN))
    return bound, f"slowest was {slowest:.0f}s, +{int(MARGIN*100)}%"


def run_bounded(command, *, family, cwd=None, env=None, capture=True):
    """Run `command`, killed if it overruns what this family has needed before.

    Returns the CompletedProcess, or None if it was killed. A killed run is
    deliberately not recorded: a hang is not evidence about how long a
    successful build takes.
    """
    bound, reason = limit_for(family)
    started = time.perf_counter()

    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, timeout=bound,
            capture_output=capture, text=True)
    except subprocess.TimeoutExpired:
        print(f"  {family}: killed after {bound:.0f}s ({reason})")
        return None

    elapsed = time.perf_counter() - started

    # Only successful runs update the history. A failing build can be much
    # faster than a working one -- synthesis errors out early -- and recording
    # it would tighten the bound until real builds start being killed.
    if result.returncode == 0:
        history = _load()
        recorded = history.setdefault(family, {})
        previous = recorded.get("slowest", 0.0)
        recorded["slowest"] = max(previous, elapsed)
        recorded["last"] = elapsed
        recorded["runs"] = recorded.get("runs", 0) + 1
        _save(history)

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
