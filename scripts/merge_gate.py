#!/usr/bin/env python3
#
# Local merge gate: SoC sims, pytest, doc links, private paths.
# SPDX-License-Identifier: BSD-3-Clause

"""
The gate that stands in for CI (there is none on GitHub).

    ./scripts/merge_gate.py              # run the gate
    ./scripts/merge_gate.py --install    # hooks: pre-merge-commit, pre-push (this checkout only)
    CYNTHION_SKIP_GATE=1 git merge ...   # skip it

Why these four: `scripts/check.py` does not run `soc_sims.py`, and a failing
sim sat on main until every sim was run by hand.

Runs only in the main checkout. Agent worktrees lack submodules, so the doc
links and apollo-dependent tests fail there for reasons unrelated to the change.

Limits (host processes, house rule): a step is killed after STALL_S with no
output; the whole gate after TOTAL_S. Both log which step, the limit, the elapsed
time and the last line.
"""

import argparse
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "tmp" / "logs" / "merge-gate.log"

# House rule: 60 s stall guard per step; total = 3x the measured worst-case gate (#640).
STALL_S = 60
# soc_sims.py prints a liveness line every 30 s (#640), so no step needs a longer stall.
STALL_BY_STEP: dict[str, int] = {}
TOTAL_S = 3 * 480

# pytest: two known psycopg failures in test_build_metrics_dialect.py are
# allowed, any other failure or collection error is not.
KNOWN_FAILURES = {
    "tests/test_build_metrics_dialect.py::test_no_silent_fallback_to_another_store",
    "tests/test_build_metrics_dialect.py::test_drivers_keep_the_gil_off",
}

STEPS = [
    ("sims", [sys.executable, "scripts/soc_sims.py"]),
    ("pytest", [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]),
    ("doclinks", [sys.executable, "scripts/check_doc_links.py"]),
    ("paths", [sys.executable, "scripts/private_path_check.py"]),
]


def say(line, log):
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def run_step(name, cmd, started, log):
    """-> (returncode, output lines). Kills on stall or total expiry."""
    # A hook inherits GIT_DIR / GIT_INDEX_FILE; every git call in a step would hit this repo's index.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    lines, last = [], time.monotonic()
    while proc.poll() is None:
        if not selector.select(timeout=1.0):
            now = time.monotonic()
            stall = STALL_BY_STEP.get(name, STALL_S)
            for limit, why, since in ((stall, "stall", last), (TOTAL_S, "total", started)):
                if now - since > limit:
                    proc.kill()
                    say(f"gate: {name} KILLED ({why} limit {limit} s, elapsed "
                        f"{now - since:.0f} s, last line: {lines[-1] if lines else '-'!r})", log)
                    return 124, lines
            continue
        line = proc.stdout.readline()
        if line:
            lines.append(line.rstrip())
            last = time.monotonic()
            log.write(line)
    lines.extend(l.rstrip() for l in proc.stdout.read().splitlines())
    return proc.returncode, lines


def judge(name, code, lines):
    if name != "pytest" or code == 0:
        return code == 0
    failed = {l.split()[1] for l in lines if l.startswith("FAILED ")}
    errors = [l for l in lines if l.startswith("ERROR ")]
    names = {f.split(" - ")[0] for f in failed}
    return not errors and bool(names) and names <= KNOWN_FAILURES


def in_main_checkout():
    git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    return Path(git_dir).resolve() == (ROOT / common).resolve()


def install():
    hooks = ROOT / ".git" / "hooks"
    for hook in ("pre-merge-commit", "pre-push"):
        target = hooks / hook
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(Path("../../scripts/merge_gate.py"))
        print(f"installed {target.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install", action="store_true")
    args, _ = parser.parse_known_args()
    if args.install:
        install()
        return 0
    if os.environ.get("CYNTHION_SKIP_GATE"):
        print("gate: skipped (CYNTHION_SKIP_GATE)")
        return 0
    if not in_main_checkout():
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with LOG.open("w") as log:
        for name, cmd in STEPS:
            say(f"gate: {name} ...", log)
            code, lines = run_step(name, cmd, started, log)
            if not judge(name, code, lines):
                say(f"gate: {name} FAILED (rc {code}); details in {LOG.relative_to(ROOT)}", log)
                for line in lines[-15:]:
                    print("   " + line)
                return 1
            say(f"gate: {name} ok", log)
        say(f"gate: passed in {time.monotonic() - started:.0f} s", log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
