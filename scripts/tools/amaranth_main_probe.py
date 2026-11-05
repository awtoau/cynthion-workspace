#!/usr/bin/env python3
# Run the workspace on amaranth main + the patched luna / luna_soc, in scratch directories.
# SPDX-License-Identifier: BSD-3-Clause

"""
    ./scripts/tools/amaranth_main_probe.py [--rebuild]

- Installs into `tmp/amaranth_main_probe/` (never the system environment):
  amaranth main at AMARANTH_PIN, luna at LUNA_PIN + `patches/luna-amaranth-0.6.patch`,
  luna_soc at LUNA_SOC_PIN + `patches/luna-soc-amaranth-0.6.patch`.
- Runs the workspace tests and `scripts/check.py repro` with those first on PYTHONPATH.
- Log: ./tmp/logs/amaranth_main_probe.log. Scope and state: #222, #83.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "tmp" / "logs" / "amaranth_main_probe.log"
BASE = ROOT / "tmp" / "amaranth_main_probe"
AMARANTH_PIN = "90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7"
LUNA_PIN = "352cdbfc284465b346e66b851eaf3ab68635060a"
LUNA_SOC_PIN = "368174f26eb1749cbc621e3b609e32b9124e1a7a"
# Shared source stores: $GIT_MIRROR, else <repo>/../../git_mirror.
LUNA_STORE = Path(os.environ.get("GIT_MIRROR") or ROOT.parents[1] / "git_mirror") / "greatscottgadgets" / "luna"
LUNA_SOC_URL = "https://github.com/awtoau/awto-luna-soc.git"
# Stall limit: pip and git print nothing for long only when hung; tests and the repro check print
# per test / per step. 300 s covers a cold pip install of amaranth from git.
STALL_S = 300


def run(command, cwd=None, env=None):
    logging.info("$ %s", " ".join(map(str, command)))
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=STALL_S * 3)
    with LOG.open("a") as handle:
        handle.write(done.stdout + done.stderr)
    if done.returncode != 0:
        logging.error("rc=%d: %s", done.returncode, (done.stdout + done.stderr).strip().splitlines()[-1:])
    return done.returncode


def prepare(rebuild):
    if rebuild:
        shutil.rmtree(BASE, ignore_errors=True)
    site = BASE / "site"
    if (site / "amaranth").exists():
        return site
    site.mkdir(parents=True)
    run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(site),
         f"amaranth @ git+https://github.com/amaranth-lang/amaranth.git@{AMARANTH_PIN}"])

    luna = BASE / "luna-src"
    # A stale registration (a deleted tmp/ dir) makes `worktree add` fail; prune it first.
    run(["git", "-C", str(LUNA_STORE), "worktree", "prune"])
    if run(["git", "-C", str(LUNA_STORE), "worktree", "add", "--detach", str(luna), LUNA_PIN]) != 0:
        raise SystemExit("could not create the luna worktree: see " + str(LOG))
    run(["git", "-C", str(luna), "apply", str(ROOT / "patches" / "luna-amaranth-0.6.patch")])
    run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(site), str(luna)])

    luna_soc = BASE / "luna-soc-src"
    run(["git", "clone", "-q", LUNA_SOC_URL, str(luna_soc)])
    run(["git", "-C", str(luna_soc), "checkout", "-q", LUNA_SOC_PIN])
    run(["git", "-C", str(luna_soc), "apply", str(ROOT / "patches" / "luna-soc-amaranth-0.6.patch")])
    run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(site), str(luna_soc)])
    return site


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    site = prepare("--rebuild" in sys.argv)
    env = {**os.environ, "PYTHONPATH": str(site)}
    seen = subprocess.run([sys.executable, "-W", "ignore", "-c",
                           "import amaranth, luna, luna_soc; print(amaranth.__version__)"],
                          env=env, capture_output=True, text=True).stdout.strip()
    logging.info("amaranth %s from %s", seen, site)
    tests = run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "tests"], cwd=ROOT, env=env)
    repro = run([sys.executable, "scripts/check.py", "repro"], cwd=ROOT, env=env)
    logging.info("tests rc=%d, repro rc=%d", tests, repro)
    return 0 if tests == 0 and repro == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
