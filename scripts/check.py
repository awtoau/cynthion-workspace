#!/usr/bin/env python3
"""
Local check runner for the Cynthion workspace.

The only place checks run: natively on this machine, against the real
toolchain and the real free-threaded 3.15t interpreter, with no Docker,
no cloud runners and no GitHub Actions.

    ./scripts/check.py                 # every check
    ./scripts/check.py rust python     # only the named checks
    ./scripts/check.py --list          # what is available
    ./scripts/check.py --fast          # skip any check marked slow (none are)
    ./scripts/check.py --parallel      # run independent checks concurrently

Exit status is 0 only if every selected check passed, so this works
directly as a pre-commit / pre-push hook:

    ln -s ../../scripts/check.py .git/hooks/pre-push

Output goes to the console and, one line per check, to tmp/logs/check.log;
each check's full stdout+stderr lands in tmp/logs/check-<name>.log for
post-mortem. A run first deletes any check-<name>.log whose check no longer
exists, so a retired check's last output cannot be mistaken for a current
result.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from logging_utils import setup_logging

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "tmp"
LOGS = TMP / "logs"
REPOS = ROOT / "repos"

# Mirrors install.py: the workspace targets free-threaded CPython. Resolved
# rather than hard-coded so a machine mid-upgrade still works.
PYTHON_CANDIDATES = ["python3.15t", "python3.14t", "python3.15", "python3.14"]


def resolve_python() -> str:
    override = os.getenv("CYN_PYTHON")
    if override:
        return override
    for candidate in PYTHON_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return "python3"


PYTHON = resolve_python()

RESET, BOLD, GREEN, RED, YELLOW, CYAN = (
    "\033[0m", "\033[1m", "\033[92m", "\033[91m", "\033[93m", "\033[96m",
)


@dataclass
class Step:
    """One command in a check."""
    cmd: List[str]
    cwd: Path
    # Steps that only report information; a non-zero exit does not fail the run.
    informational: bool = False


@dataclass
class Check:
    name: str
    description: str
    steps: List[Step]
    slow: bool = False
    # Reason this check cannot run here, if any (evaluated at collection time).
    skip_reason: Optional[Callable[[], Optional[str]]] = None


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    skipped: bool = False
    detail: str = ""
    failed_cmd: str = ""
    tail: List[str] = field(default_factory=list)


def _need(tool: str) -> Callable[[], Optional[str]]:
    def probe() -> Optional[str]:
        if shutil.which(tool) is None:
            return f"{tool} not on PATH"
        return None
    return probe


def build_checks() -> List[Check]:
    """The checks, mirroring what the retired GitHub workflows ran."""
    cyn_fw = REPOS / "cynthion" / "firmware"
    apollo_fw = REPOS / "apollo" / "firmware"
    tinyusb = REPOS / "apollo" / "lib" / "tinyusb"

    return [
        Check(
            name="rust",
            description="cargo check + clippy (moondancer, riscv32imac)",
            skip_reason=_need("cargo"),
            steps=[
                Step(["cargo", "check", "--release",
                      "--target", "riscv32imac-unknown-none-elf"], cyn_fw),
                Step(["make", "clippy"], cyn_fw),
            ],
        ),
        Check(
            name="apollo",
            description="Apollo SAMD11 firmware build + size report",
            skip_reason=_need("arm-none-eabi-gcc"),
            steps=[
                # tinyusb does not vendor the microchip MCU headers (sam.h);
                # they are fetched on demand. Cheap no-op once present.
                Step([PYTHON, "tools/get_deps.py", "samd11"], tinyusb),
                Step(["make", "APOLLO_BOARD=cynthion"], apollo_fw),
                Step(["arm-none-eabi-size", "_build/cynthion_d11/firmware.elf"],
                     apollo_fw, informational=True),
                # Not informational: this one fails. The size output above
                # cannot, so a change taking flash from 89% to 99% passed the
                # loop and was caught only if someone read the log -- on a part
                # with 14 KB of usable flash and 4 KB of RAM.
                #
                # The stack figure is the measurement from stack_probe.c: 344
                # bytes at the deepest path exercised, against a 1024-byte
                # reservation. Passed in rather than re-measured because
                # measuring it needs the board, and this check has to work
                # without hardware attached.
                Step([PYTHON, "scripts/apollo_budget_check.py",
                      "--stack-measured", "344"], ROOT),
            ],
        ),
        Check(
            name="python",
            description=f"import check + pytest on {PYTHON}",
            steps=[
                # `facedancer` was asserted here and imported by nothing in this
                # repo -- the assertion was the whole reason the submodule was
                # installed. See #169.
                Step([PYTHON, "-c", "import cynthion, apollo_fpga"], ROOT),
                # `tests/` is OURS and was not collected here until now. Both
                # files in it passed and neither had ever run in the gate, so
                # when 37a6095 retired `hyperram_readclksel_sweep.py` to
                # debris/ its test kept importing a module that was no longer
                # there -- a collection error nothing was positioned to see.
                # An uncollected test directory is worse than no tests: it
                # reads as coverage and asserts nothing.
                Step([PYTHON, "-m", "pytest",
                      "repos/cynthion/cynthion/python/tests/",
                      "tests/",
                      "-q", "--tb=short"], ROOT),
            ],
        ),
        Check(
            name="freethreading",
            description="interpreter is a free-threaded build and stays that way",
            steps=[
                # Guards the workspace's core assumption: if some import flips
                # the GIL back on, the parallel build path silently serialises.
                Step([PYTHON, "-c",
                      "import sys; "
                      "import cynthion, apollo_fpga; "
                      "assert not sys._is_gil_enabled(), "
                      "'GIL is enabled - not a free-threaded build, or an "
                      "import re-enabled it'; "
                      "print('free-threaded OK:', sys.version.split()[0])"],
                     ROOT),
            ],
        ),
        # There is no `flutter` check. The dashboard was retired to
        # debris/code/app-flutter-dashboard/ because the check could not fail:
        # `analyze` and `test` were both informational, so the only step that
        # could go red was `pub get`. It reported green for months over 6
        # analyzer warnings and a failing smoke test -- the same
        # `continue-on-error` failure the hosted workflows had, carried into the
        # local runner. See docs/github_actions.md.
        Check(
            name="amaranthsoc",
            description="amaranth_soc is the real package, not luna_soc's vendored copy",
            steps=[
                # The dependency nothing declared. luna_soc appends its own
                # vendored amaranth_soc/amaranth_stdio to sys.path when the real
                # ones are missing -- no error, no version string, different
                # gateware out of a tree last re-synced 2025-01-07. Declaring
                # them in machine_setup.py fixed it; this is what notices if the
                # declaration is ever dropped, since a declaration is invisible
                # and a red check is not. See #190.
                #
                # Imports only; no board, no toolchain, well under a second.
                Step([PYTHON, "scripts/amaranth_soc_check.py"], ROOT),
            ],
        ),
        Check(
            name="socmap",
            description="the committed SVD still matches the SoC's memory map",
            steps=[
                # The one check that can see drift between the gateware and the
                # firmware. `--check` regenerates into tmp/ and compares; it also
                # compares the map against the *_BASE constants in
                # vexii_hello_soc.py and the literals in target.rs, so a
                # peripheral that moved without the crate being regenerated fails
                # here instead of on the board.
                #
                # Elaboration only -- no synthesis, no board, a few seconds.
                Step([PYTHON, "scripts/soc_generate_pac.py", "--check"], ROOT),
            ],
        ),
        Check(
            name="irqlog",
            description="no interrupt handler can reach a console",
            steps=[
                # Printing from a handler spins on a UART FIFO inside an
                # interrupt; on a level-sensitive shared source that is a hang
                # that presents as a dead CPU. Ownership stops most of it --
                # `src/irq.rs` holds a `UartRx`, which has no transmit method, so
                # a `write!` there does not compile -- but Rust's privacy cannot
                # stop a sibling module naming `Uart`, so the rest is a grep.
                #
                # Source only; no board, no toolchain, well under a second.
                Step([PYTHON, "scripts/soc_irq_log_check.py"], ROOT),
            ],
        ),
        Check(
            name="paths",
            description="no tracked file names one machine's filesystem",
            steps=[
                # This repo is public. A home directory or a local mount point
                # in a committed file says which account and which disk the
                # work was done on, and is wrong for everyone who clones it.
                # It has also already broken once here, when the checkout
                # moved and every absolute path in the docs went stale.
                #
                # Tracked files only, text only; no board, no toolchain,
                # well under a second.
                Step([PYTHON, "scripts/private_path_check.py"], ROOT),
            ],
        ),
        # There is no `gateware` check. It elaborated
        # `cynthion.gateware.analyzer.top` -- the USB analyzer bitstream inside
        # repos/cynthion, which this repo does not build -- and it did so for
        # `CynthionPlatformRev0D2`, while 43 references across scripts/ and
        # gateware/ target r1.4 and the board is r1.4.0. Upstream code, wrong
        # board revision, and 17-50 s of a 17 s gate: ~98% of the wall time.
        #
        # The gateware this repo DOES build is checked by `socmap`, which
        # elaborates gateware/soc/top.py in 0.7 s. See #169.
    ]


def run_check(check: Check, logger, verbose: bool) -> Result:
    """Run one check, capturing output to tmp/logs/check-<name>.log."""
    skip = check.skip_reason() if check.skip_reason else None
    if skip:
        logger.info("%s: skipped (%s)", check.name, skip)
        return Result(check.name, ok=True, seconds=0.0, skipped=True, detail=skip)

    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"check-{check.name}.log"
    started = time.monotonic()

    with open(log_path, "w") as log_file:
        for step in check.steps:
            printable = " ".join(str(c) for c in step.cmd)
            log_file.write(f"\n$ cd {step.cwd}\n$ {printable}\n")
            log_file.flush()

            if not step.cwd.exists():
                msg = f"working directory missing: {step.cwd}"
                log_file.write(msg + "\n")
                logger.error("%s: %s", check.name, msg)
                return Result(check.name, ok=False,
                              seconds=time.monotonic() - started,
                              detail=msg, failed_cmd=printable)

            proc = subprocess.run(
                step.cmd, cwd=step.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            log_file.write(proc.stdout or "")
            log_file.flush()
            if verbose and proc.stdout:
                print(proc.stdout, end="")

            if proc.returncode != 0 and not step.informational:
                tail = (proc.stdout or "").strip().splitlines()[-12:]
                logger.error("%s: FAILED exit %d in %.1fs (%s), output in %s",
                             check.name, proc.returncode,
                             time.monotonic() - started, printable,
                             log_path.name)
                return Result(
                    check.name, ok=False,
                    seconds=time.monotonic() - started,
                    detail=f"exit {proc.returncode}",
                    failed_cmd=printable, tail=tail,
                )

    seconds = time.monotonic() - started
    logger.info("%s: passed in %.1fs", check.name, seconds)
    return Result(check.name, ok=True, seconds=seconds)


def prune_orphan_logs(names: List[str]) -> List[str]:
    """Delete tmp/logs/check-<name>.log for checks that no longer exist.

    A retired check leaves its last log behind, and nothing distinguishes that
    file from the result of a check that ran a moment ago -- same directory,
    same naming, and a mtime that only says when the check was last alive.
    `check-gateware.log` outlived the `gateware` check by weeks that way, still
    reading as current output for a check that had been deleted in #169.

    Keyed off the full registered set rather than the checks selected for this
    run, so `check.py rust` does not treat every other check as retired.
    """
    if not LOGS.is_dir():
        return []
    live = {f"check-{n}.log" for n in names}
    orphans = sorted(p for p in LOGS.glob("check-*.log") if p.name not in live)
    for path in orphans:
        path.unlink()
    return [p.name for p in orphans]


def main() -> int:
    checks = build_checks()
    names = [c.name for c in checks]

    parser = argparse.ArgumentParser(
        description="Run the workspace checks locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help=f"checks to run (default: all). one of: {', '.join(names)}")
    parser.add_argument("--list", action="store_true", help="list checks and exit")
    parser.add_argument("--fast", action="store_true", help="skip slow checks")
    parser.add_argument("--parallel", action="store_true",
                        help="run checks concurrently (free-threaded build makes this real)")
    parser.add_argument("--jobs", type=int, default=4, help="workers for --parallel")
    parser.add_argument("--verbose", action="store_true", help="stream command output")
    args = parser.parse_args()

    if args.list:
        print(f"\n{BOLD}Available checks{RESET}\n")
        for c in checks:
            skip = c.skip_reason() if c.skip_reason else None
            state = f"{YELLOW}unavailable: {skip}{RESET}" if skip else f"{GREEN}ready{RESET}"
            slow = f" {YELLOW}(slow){RESET}" if c.slow else ""
            print(f"  {BOLD}{c.name:<14}{RESET} {c.description}{slow}")
            print(f"  {'':<14} {state}\n")
        return 0

    if args.checks:
        unknown = [n for n in args.checks if n not in names]
        if unknown:
            print(f"{RED}unknown check(s): {', '.join(unknown)}{RESET}", file=sys.stderr)
            print(f"available: {', '.join(names)}", file=sys.stderr)
            return 2
        selected = [c for c in checks if c.name in args.checks]
    else:
        selected = [c for c in checks if not (args.fast and c.slow)]

    TMP.mkdir(parents=True, exist_ok=True)
    # file_only: this script prints its own coloured report, so the logger's
    # console half would say everything twice. The file half is the point --
    # an appended record of which checks ran when, next to the per-check logs
    # holding their output.
    logger = setup_logging("check.py", log_dir=LOGS, filename="check.log",
                           file_only=True)

    logger.info("run: %d checks selected (%s), interpreter %s",
                len(selected), ", ".join(c.name for c in selected), PYTHON)

    # Announced rather than silent: a deletion nobody is told about is
    # indistinguishable from a log that was never written.
    for orphan in prune_orphan_logs(names):
        print(f"{YELLOW}⌫ removed {orphan} — no such check{RESET}")

    print(f"\n{BOLD}{CYAN}{'=' * 68}{RESET}")
    print(f"{BOLD}{CYAN}Local checks — {len(selected)} selected, interpreter {PYTHON}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 68}{RESET}\n")

    started = time.monotonic()
    results: List[Result] = []

    if args.parallel:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_check, c, logger, args.verbose): c
                       for c in selected}
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                _report(r)
    else:
        for check in selected:
            print(f"{CYAN}▶ {check.name}{RESET} — {check.description}")
            r = run_check(check, logger, args.verbose)
            results.append(r)
            _report(r)

    elapsed = time.monotonic() - started
    results.sort(key=lambda r: r.name)

    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    passed = [r for r in results if r.ok and not r.skipped]

    print(f"\n{BOLD}{CYAN}{'=' * 68}{RESET}")
    print(f"{BOLD}Summary{RESET}  "
          f"{GREEN}{len(passed)} passed{RESET}  "
          f"{RED}{len(failed)} failed{RESET}  "
          f"{YELLOW}{len(skipped)} skipped{RESET}  "
          f"in {elapsed:.1f}s")
    print(f"{BOLD}{CYAN}{'=' * 68}{RESET}")

    logger.info("summary: %d passed, %d failed, %d skipped in %.1fs%s",
                len(passed), len(failed), len(skipped), elapsed,
                "" if not failed else
                " -- failed: " + ", ".join(r.name for r in failed))

    for r in failed:
        print(f"\n{RED}{BOLD}✗ {r.name}{RESET} ({r.detail})")
        print(f"  command: {r.failed_cmd}")
        print(f"  log:     tmp/logs/check-{r.name}.log")
        for line in r.tail:
            print(f"  │ {line}")

    if skipped:
        print()
        for r in skipped:
            print(f"{YELLOW}⊘ {r.name} skipped — {r.detail}{RESET}")

    print()
    return 1 if failed else 0


def _report(r: Result) -> None:
    if r.skipped:
        print(f"  {YELLOW}⊘ {r.name} skipped — {r.detail}{RESET}")
    elif r.ok:
        print(f"  {GREEN}✓ {r.name}{RESET} ({r.seconds:.1f}s)")
    else:
        print(f"  {RED}✗ {r.name}{RESET} ({r.detail}, {r.seconds:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
