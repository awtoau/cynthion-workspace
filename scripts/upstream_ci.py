#!/usr/bin/env python3
"""
Run an upstream GreatScottGadgets repo's own CI checks in a clean, disposable clone.

Upstream CI is the gate a submitted patch has to pass, so the cheapest way to
de-risk a submission is to run that same gate locally, first, on a tree that has
none of our changes in it. This script does that: it clones the upstream repo
into ./tmp/, checks out whatever ref (or PR head, or local patch) you name, and
runs the exact commands the upstream workflow files run.

The clone is thrown away and re-made on every run, so nothing here can touch
repos/* or the workspace submodules.

Usage:
    ./scripts/upstream_ci.py luna                     # upstream main
    ./scripts/upstream_ci.py luna --pr 301            # someone else's PR head
    ./scripts/upstream_ci.py apollo --job host-tests  # one job only
    ./scripts/upstream_ci.py apollo --pick 0e9bfb1    # our commit on clean upstream
    ./scripts/upstream_ci.py apollo --patch tmp/x.patch   # a patch file on clean upstream
    ./scripts/upstream_ci.py luna --list              # show jobs, run nothing
    ./scripts/upstream_ci.py apollo --reuse           # skip re-clone, rerun jobs

Exit code is 0 only if every selected job passes.

See docs/upstream-ci-workflow.md for how this fits the submission process, and
docs/upstream-patch-process.md for the rules a patch must satisfy before it goes
anywhere near a fork.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from logging_utils import setup_logging  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent.parent
TMP = WORKSPACE / "tmp"
LOG_DIR = TMP / "logs"

PY = sys.executable

# The Apollo firmware matrix, copied from .github/workflows/firmware.yml.
# (board name, APOLLO_BOARD, BOARD_REVISION_MAJOR, BOARD_REVISION_MINOR)
APOLLO_BOARDS = [
    ("cynthion",           "cynthion", None, None),
    ("samd11_xplained",    "samd11_xplained", None, None),
    ("qtpy",               "qtpy", None, None),
    ("cynthion-r0.2",      "cynthion", "0", "2"),
    ("cynthion-r0.4",      "cynthion", "0", "4"),
    ("raspberry_pi_pico",  "raspberry_pi_pico", None, None),
]


def apollo_firmware_jobs():
    """One job per board in upstream's firmware-build matrix."""
    jobs = {}
    for name, board, major, minor in APOLLO_BOARDS:
        env = {"APOLLO_BOARD": board}
        if major is not None:
            env |= {"BOARD_REVISION_MAJOR": major, "BOARD_REVISION_MINOR": minor}
        jobs[f"firmware-{name}"] = {
            "cmd": ["make", "get-deps", "all"],
            "cwd": "firmware",
            "env": env,
            "needs": ["arm-none-eabi-gcc", "make"],
            "workflow": ".github/workflows/firmware.yml (firmware-build)",
        }
    return jobs


# What each upstream repo's CI actually runs. `workflow` records where the
# command came from, so drift in upstream's config is visible when this stops
# matching.
REPOS = {
    "luna": {
        "url": "https://github.com/greatscottgadgets/luna.git",
        "jobs": {
            "sim-tests": {
                "cmd": [PY, "-m", "unittest", "discover", "-t", ".", "-s", "tests", "-v"],
                "needs_import": ["amaranth"],
                "workflow": ".github/workflows/simulate.yml (simulations)",
            },
        },
        "note": "Also has a Jenkins hardware-in-the-loop job we cannot run or read.",
    },
    "apollo": {
        "url": "https://github.com/greatscottgadgets/apollo.git",
        "submodules": True,
        "jobs": {
            "host-tests": {
                "cmd": [PY, "-m", "unittest", "discover", "-p", "jtag_svf.py", "-v"],
                "workflow": ".github/workflows/host.yml (python)",
            },
            **apollo_firmware_jobs(),
        },
    },
    "cynthion": {
        "url": "https://github.com/greatscottgadgets/cynthion.git",
        "jobs": {
            "unit-tests": {
                "cmd": [PY, "-m", "unittest", "discover", "-s", "cynthion/python/tests"],
                "needs_import": ["amaranth", "cynthion"],
                "workflow": ".github/workflows/python.yml (build-and-test)",
            },
            "analyzer-elaborate": {
                "cmd": [PY, "-m", "cynthion.gateware.analyzer.analyzer"],
                "needs_import": ["amaranth", "cynthion"],
                "workflow": ".github/workflows/python.yml (build-and-test)",
            },
        },
    },
    # No "facedancer": this workspace carries no such submodule (#169), so there
    # is no repos/facedancer to cherry-pick from.
    "luna-soc": {
        "url": "https://github.com/greatscottgadgets/luna-soc.git",
        "jobs": {},
        "note": "No CI at all upstream: no .github/workflows. A PR gets no automated signal.",
    },
}


def run(logger, cmd, cwd=None, check=True):
    """Run a short command, logging it first."""
    logger.info(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        for line in (proc.stderr or proc.stdout).splitlines()[-20:]:
            logger.error(f"  {line}")
        if check:
            raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def check_prereqs(logger, jobs):
    """Fail fast, before spending time on a clone, on anything a job needs."""
    problems = []
    if shutil.which("git") is None:
        problems.append("git not on PATH")

    for name, job in jobs.items():
        for tool in job.get("needs", []):
            if shutil.which(tool) is None:
                problems.append(f"{name}: {tool} not on PATH")
        for mod in job.get("needs_import", []):
            probe = subprocess.run(
                [PY, "-c", f"import {mod}"], capture_output=True, text=True)
            if probe.returncode != 0:
                problems.append(f"{name}: cannot import {mod} under {PY}")

    if problems:
        for p in sorted(set(problems)):
            logger.error(p)
        raise SystemExit("prerequisites missing")


def make_clone(logger, repo, spec, ref, pr, patch, pick, reuse):
    """Create (or reuse) the disposable clone and check out the wanted commit."""
    clone = TMP / f"upstream-{repo}"

    if reuse:
        if not (clone / ".git").is_dir():
            raise SystemExit(f"--reuse given but no clone at {clone}")
        logger.info(f"reusing existing clone at {clone}")
    else:
        if clone.exists():
            logger.info(f"removing previous clone {clone}")
            shutil.rmtree(clone)
        clone.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "clone", "--branch", ref, spec["url"], str(clone)]
        if pick is None and patch is None:
            # Shallow is fine when we only build the commit as-is. A cherry-pick
            # or a 3-way apply needs real history and blobs, so skip --depth
            # there: `git apply -3` on a shallow clone fails with "repository
            # lacks the necessary blob".
            cmd[2:2] = ["--depth", "1"]
        if spec.get("submodules"):
            cmd.insert(2, "--recurse-submodules")
        run(logger, cmd)

        if pr is not None:
            # PR heads live in the read-only refs/pull namespace. This is the
            # contributor's commit, not a merge with main -- same as what
            # GitHub Actions tests on a `pull_request` event's head.
            depth = ["--depth", "1"] if pick is None and patch is None else []
            run(logger, ["git", "fetch"] + depth + ["origin",
                         f"refs/pull/{pr}/head:pr-{pr}"], cwd=clone)
            run(logger, ["git", "checkout", f"pr-{pr}"], cwd=clone)

        if pick is not None:
            # Cherry-pick straight from our local working repo. This is the
            # operation the real submission performs -- our commit replayed onto
            # clean upstream -- so a conflict here is exactly the conflict a
            # maintainer would hit, surfaced before anyone is asked to look.
            local = WORKSPACE / "repos" / repo
            if not (local / ".git").exists():
                raise SystemExit(f"--pick needs a local clone at {local}")
            run(logger, ["git", "remote", "add", "local", str(local)], cwd=clone)
            run(logger, ["git", "fetch", "local"], cwd=clone)
            for sha in pick:
                # -x records the origin sha in the message, so the replayed
                # commit stays traceable back to our tree.
                run(logger, ["git", "cherry-pick", "-x", sha], cwd=clone)
                logger.info(f"cherry-picked {sha}")

        if patch is not None:
            patch = Path(patch).resolve()
            if not patch.is_file():
                raise SystemExit(f"no such patch file: {patch}")
            run(logger, ["git", "apply", "-3", "--stat", str(patch)], cwd=clone)
            run(logger, ["git", "apply", "-3", str(patch)], cwd=clone)
            logger.info(f"applied patch {patch.name}")

    desc = run(logger, ["git", "log", "-1", "--format=%H %ad %an %s",
                        "--date=iso-strict"], cwd=clone)
    logger.info(f"HEAD: {desc.stdout.strip()}")
    return clone, desc.stdout.strip()


def run_job(logger, name, job, clone, stamp):
    """Stream one job's output to its own log; return (ok, summary)."""
    cwd = clone / job.get("cwd", ".")
    env = os.environ | job.get("env", {})
    raw_log = LOG_DIR / f"upstream-ci-{name}-{stamp}.log"

    logger.info(f"--- {name}: $ {' '.join(job['cmd'])}  (cwd={cwd.relative_to(clone)})")
    if job.get("env"):
        logger.info(f"    env: {job['env']}")

    with open(raw_log, "w") as fh:
        proc = subprocess.Popen(job["cmd"], cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:
            fh.write(line)
            stripped = line.rstrip()
            # unittest -v marks each outcome at the end of the test's line.
            if re.search(r"\.\.\. (FAIL|ERROR)$", stripped):
                logger.error(f"    {stripped}")
        rc = proc.wait()

    text = raw_log.read_text()
    summary = ""
    ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", text, re.M)
    if ran:
        summary = f"{ran.group(1)} tests in {ran.group(2)}s"
    # Apollo's firmware Makefile prints the size table at the end of a build.
    size = re.search(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[0-9a-f]+\s", text, re.M)
    if size and not ran:
        summary = f"text={size.group(1)} data={size.group(2)} bss={size.group(3)}"

    ok = rc == 0
    (logger.info if ok else logger.error)(
        f"    {'PASS' if ok else 'FAIL'}  {summary}  -> {raw_log.name}")
    if not ok:
        for line in text.splitlines()[-15:]:
            logger.error(f"    | {line}")
    return ok, summary


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", choices=sorted(REPOS), help="upstream repo to check")
    ap.add_argument("--ref", default="main", help="branch/tag to clone (default: main)")
    ap.add_argument("--pr", type=int, help="check out this upstream PR's head commit")
    ap.add_argument("--pick", action="append", metavar="SHA",
                    help="cherry-pick this commit from repos/<repo> onto clean "
                         "upstream (repeatable, applied in the order given)")
    ap.add_argument("--patch", help="apply this patch file to the clean clone first")
    ap.add_argument("--job", action="append",
                    help="run only this job (repeatable); default is all")
    ap.add_argument("--list", action="store_true", help="list jobs and exit")
    ap.add_argument("--reuse", action="store_true",
                    help="reuse the existing clone instead of re-cloning")
    args = ap.parse_args()

    spec = REPOS[args.repo]
    logger = setup_logging("upstream-ci", log_dir=LOG_DIR)

    if args.list:
        logger.info(f"{args.repo}: {spec['url']}")
        if spec.get("note"):
            logger.warning(spec["note"])
        for name, job in spec["jobs"].items():
            logger.info(f"  {name:24s} {job['workflow']}")
            logger.info(f"  {'':24s} $ {' '.join(job['cmd'])}")
        if not spec["jobs"]:
            logger.warning("  no runnable jobs")
        return 0

    jobs = spec["jobs"]
    if args.job:
        unknown = set(args.job) - set(jobs)
        if unknown:
            raise SystemExit(f"unknown job(s): {', '.join(sorted(unknown))}")
        jobs = {k: v for k, v in jobs.items() if k in args.job}
    if not jobs:
        logger.error(f"{args.repo} has no runnable CI jobs")
        if spec.get("note"):
            logger.error(spec["note"])
        return 1

    started = datetime.now().astimezone()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    logger.info(f"started {started.isoformat(timespec='seconds')}")
    logger.info(f"python {sys.version.split()[0]} at {PY}")
    if spec.get("note"):
        logger.warning(spec["note"])

    check_prereqs(logger, jobs)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    clone, head = make_clone(logger, args.repo, spec, args.ref, args.pr,
                             args.patch, args.pick, args.reuse)

    results = {}
    for name, job in jobs.items():
        results[name] = run_job(logger, name, job, clone, stamp)

    target = f"PR #{args.pr}" if args.pr else args.ref
    if args.pick:
        target += " + " + "+".join(s[:8] for s in args.pick)
    if args.patch:
        target += f" + {Path(args.patch).name}"
    failed = [n for n, (ok, _) in results.items() if not ok]

    logger.info("=" * 64)
    logger.info(f"repo:    {args.repo}")
    logger.info(f"target:  {target}")
    logger.info(f"commit:  {head.split()[0][:12]}")
    for name, (ok, summary) in results.items():
        logger.info(f"  {'PASS' if ok else 'FAIL'}  {name:24s} {summary}")
    if failed:
        logger.error(f"{len(failed)} of {len(results)} job(s) failed: {', '.join(failed)}")
    else:
        logger.info(f"all {len(results)} job(s) passed")
    logger.info(f"logs:    {LOG_DIR}/upstream-ci-*-{stamp}.log")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
